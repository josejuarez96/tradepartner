# Spec: Data foundation (Phase 2)

**Status:** Draft  ·  **Issue:** #8  ·  **Related:** [ADR 0003](../decisions/0003-data-adapters-local-first.md), [ADR 0004](../decisions/0004-tooling-adopt-avoid.md), [ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md), [ADR 0006](../decisions/0006-universe-and-cadence.md), [roadmap Phase 2](../roadmap.md)

## Problem & why now

Nothing in Phases 3–6 can be trusted unless the data underneath answers one question correctly: *what was knowable at time T?* This spec builds that layer: a point-in-time store, a security master, a trading calendar, the adapters that feed them from free sources, a daily job, and the page that shows whether the data is healthy. It is the first code in the project.

## Users & usage

- **The owner**, once a day, glances at the data-health page. Once, at the start, the owner runs a recording script with their own keys to produce the scrubbed fixtures the tests use, and records the source-terms facts.
- **The scheduler**, once per trading day after the close, runs `tradepartner ingest`.
- **Phase 3 code** reads through the as-of API and never touches adapters directly.
- **Tests**, on every PR, run against the fixture universe with the network disabled.

## Definitions

- **T** is always a tz-aware UTC timestamp. A rebalance *date* maps to T = that session's close via the calendar. Never a bare date.
- **`known_at`** is when a fact became knowable to a market participant. **`ingested_at`** is when we stored it. `known_at ≤ ingested_at` always.
- **Revision**: a later value for the same key is a **new row** with its own `known_at`; rows are never updated in place. "Latest as of T" is the row with the greatest `known_at ≤ T`.
- **Provenance** on every row: `filing` (`known_at` = SEC acceptance timestamp), `bar` (session close), `action` (rule below), `snapshot` (current-only endpoint; `known_at` = fetch time), or `snapshot_static` (a snapshot attribute the config explicitly allows to apply before its fetch time, e.g. a company name or a pre-2019 exchange; counted and shown on the health page). A `snapshot_static` value is stored only in a row whose `known_at` is that snapshot's fetch time; only the row's `valid_from` may predate it, and as-of reads still see the row only once `known_at <= T` (#35).
- **Raw payload**: the bytes a source returns (JSON, SGML header text, or a downloaded filing file). Fetch functions return raw payloads; parsers turn raw payloads into records. Only parsers are unit-tested; fixtures are recorded raw payloads.

## Requirements

1. **Store.** A single DuckDB file, path from config. Every fact table has `known_at`, `ingested_at`, `source`, `provenance`. Prices raw OHLCV; corporate actions separate; adjusted series computed at read time as of T. Ingest holds the file read-write only while committing a chunk and releases it between chunks; every other process uses **short-lived `read_only=True`** connections and shows a "store busy" state when locked.
2. **Calendar.** Wrapper over `exchange_calendars` XNYS: is-session, next/previous session, open and close timestamps, last session of month, last *completed* session as of a timestamp, sessions in a calendar-month window, half-day detection. No weekday logic. The calendar-month window is exact: for `end_session` and `months`, it is every session `s` with `end_session − relativedelta(months=months) < s ≤ end_session`.
3. **Security master.** A `securities` row is created from the **earliest issuer filing per CIK** in EDGAR's filing index (`known_at` = that filing's acceptance; issuer form types from `master.issuer_forms`, so reporting owners filing Forms 3/4/5 and 13D/G never create rows). The index is scanned over **full history regardless of `--since`**, so `securities.known_at` is never too late. Companies therefore exist at historical T and delisted names exist at all. Ticker ranges, exchange, SIC, type and delisting come from filings where filings supply them and from `snapshot_static` attributes where they do not (pre-~2019 ticker and exchange); those count only from their fetch time, see open question 1. Keyed by `security_id`; adapters never resolve by bare ticker. Benchmarks (SPY, MTUM) are **seeded from config**, not derived from EDGAR (MTUM shares its trust's CIK with other funds).
4. **Delistings.** Forms **25 and 25-NSE** are both ingested as filing rows (`known_at` = acceptance) that name a class title and exchange. **Transfer status and listing end are never stored; they are derived at read time** from rows with `known_at ≤ T`: a listing's end is its last bar session known at T after the filing; the filing is a **transfer** at T only if a new listing row for the same security exists with `known_at ≤ T` and `valid_from` within `master.transfer_window_sessions` of the filing date. Between the filing and the new listing becoming known, the security is treated as delisted at T (conservative), and becomes a transfer once the new listing is known. `effective_on` is stored for reference only (filing date + 10 days per Rule 12d2-2 unless stated).
5. **Corporate actions.** `PriceSource.corporate_actions()` supplies splits and dividends. **First-seen** row: `known_at` = source announcement time if present, else the close of the session before ex-date. **Any later row that differs** for the same action: `known_at = ingested_at`. Never back-dated on revision. An action's identity is (security, source action id) when the source gives a stable id, else (security, type, ex-date) (#108). So a **re-dated** action (same id, new ex-date) is a revision: the old ex-date applies until its `known_at`, the new one after, never both. A **cancelled** revision (`cancelled = TRUE`, stamped at `ingested_at`) withdraws the event from its `known_at` on. It also retires the old key of a re-date from a source with no id, which ingest writes as a cancel for the old key plus a first-seen row for the new one. As-of reads take the latest revision per identity first, then apply the ex-date and cancel filters.
6. **`PriceSource`** (`bars`, `corporate_actions`) with **fixture** and **Alpaca** adapters. **`FilingSource`** (filing index, company facts, filing headers, cover pages, Form 25/25-NSE, companies snapshot) with **fixture** and **EDGAR** adapters. Real adapters = thin raw-fetch client (`httpx` for EDGAR JSON/SGML/files, `alpaca-py` for Alpaca) + pure parsers; `edgartools` (pinned, cache dir from config) is used only to parse downloaded cover-page iXBRL.
7. **`Broker` interface** and in-memory **fake broker** (submit, cancel, positions, fills; duplicate client order IDs rejected). No risk logic (Phase 4).
8. **As-of read API.** `prices_as_of`, `adjusted_prices_as_of(T, include_dividends=False)` (splits only by default; dividends for Phase 3 total return), `facts_as_of`, `securities_as_of`, `listings_as_of`, `universe_as_of`, `survivorship_gap`. All take tz-aware T and return only rows with `known_at ≤ T`, latest revision as of T. **Level rules** (ADR 0006 rules 4 and 8) use the **raw close at T** and adjust shares only for splits with **ex-date ≤ T**, regardless of when the split became known.
9. **Ingest.** `tradepartner ingest [--source alpaca|edgar|all] [--backfill --since DATE] [--dry-run]`. Per-source atomic chunks (one calendar month per source); a failure commits nothing from that chunk, earlier chunks stand; `--backfill` resumes from the last committed chunk. Idempotent for a completed session. Halts non-zero on stale or failed source, with only the `ingestion_runs` row written for that source. Retries on lock for `store.lock_retry_seconds`, then exits non-zero.
10. **Staleness** is source-level: expected latest session = calendar's last completed session at run time minus `ingest.settle_delay_minutes`; stale if the reference symbol has no bar for it, or more than `ingest.max_missing_share` of listed names are missing it. On an IEX-only feed (set by the owner in T3), `max_missing_share` and rule 6 thresholds are what the owner recorded, never silently relaxed.
11. **Health as code.** `tradepartner health [--check]` uses a pure `health.py` shared with the page: last successful ingest per source; coverage; gaps; survivorship gap and its three side categories; unclassifiable count; `snapshot_static` reliance count at the latest T; **delisted names count and list**; liquidity-rule and fill-price settings; integrity rules (non-null `known_at`; `known_at ≤ ingested_at`; bars only on sessions; no duplicate `(security_id, session, known_at)`; non-overlapping listings per security; no bars after a listing's end for delistings; guarded SIC default = charter). `--check` exits non-zero on any failure.
12. **Page.** Streamlit, rendering `health.py` output, read-only, with the busy state.
13. **Fixture universe** as CSV, seeded generator, content-equal regeneration. Required cases: delisting with truncated history (> N sessions before the Form 25 filing) and one within N; a **clean merger delisting** (last bar the session before filing; expected *not* missing); a **25-NSE**; a Form 25 on a **non-common class** (common survives); an **exchange transfer**, plus a probe T **between the Form 25 and the new listing's `known_at`** (expected: delisted at that T, transfer later); a **same-company ticker change**; a **ticker reused** by a different company; a **dual-class** company; a split; a split between a shares filing and T; a **split known before T with ex-date after T**; a **revised dividend**; a **split re-dated by source id** and a **cancelled dividend** (#108); a restated shares fact; a stale shares fact; an unclassifiable name; a `snapshot_static`-only pre-2019 listing; a holiday; a half day; SPY and MTUM as benchmarks with dividends. Generated CSVs are reviewed through the generator and excluded from the 400-line budget.
14. **Config** (pydantic settings). Every ADR 0006 threshold is a key with its default; `universe.exclude_sic_ranges` is guarded. `universe.liquidity_rule_enabled` defaulted to `false` and `execution.fill_price` to `close` until the owner's T3 research set them; T3 (#84, 2026-09-25) set `liquidity_rule_enabled=true` (SIP history gives consolidated volume) and kept `fill_price=close` ([research](../research/2026-09-25-free-data-terms.md)). Secrets only from `.env`.
15. **`data-validator` agent**: read-only, runs `health --check` and the suite on the owner's real store, reports; computes nothing itself.

## Acceptance criteria (testable)

Timing and store
- [ ] Every fact table row: tz-aware UTC `known_at`, `ingested_at`, `known_at ≤ ingested_at`, non-null `source`, `provenance` in the allowed set (schema test; `health --check` rule).
- [ ] A bar for session S has `known_at` = XNYS close of S (normal day and half day).
- [ ] A bar re-fetched with a different close becomes a second row with `known_at = ingested_at`; `prices_as_of` returns each in its interval.
- [ ] First-seen corporate action without an announcement time: `known_at` = close before ex-date; a **revised** dividend becomes a new row with `known_at = ingested_at`, and `adjusted_prices_as_of(T, include_dividends=True)` uses the old amount before that and the new after.
- [ ] Backfilled 2018 split in 2026: `adjusted_prices_as_of(2019-01-31 close)` adjusts 2017 prices.
- [ ] Split known before T with ex-date after T: `universe_as_of(T)` cap uses unadjusted shares and raw close; rule 4 uses the raw close.
- [ ] Restated shares fact: `facts_as_of(T)` returns the earlier value between the two `known_at`, the later after; facts carry `as_of_date` and `class_member`.

Look-ahead
- [ ] **Truncation invariance** for every as-of function including `universe_as_of` and `survivorship_gap`: f(T) on the full fixture store equals f(T) on a store truncated to `known_at ≤ T`, for probe timestamps placed **just before and just after every distinct `known_at`** in the fixture.
- [ ] The broken adapter's violations (early `known_at`; pre-adjusted prices; ticker-based resolution; `known_at > ingested_at`; back-dated revision) are each caught by a named check.
- [ ] A bare date passed as T raises.
- [ ] An autouse test fixture makes `socket.connect` raise; every test passes with it on.

Security master and universe
- [ ] Reused ticker: prices by `security_id` return only that company's rows. Same-company ticker change: one `security_id`, two listing ranges.
- [ ] Earliest-filing rule: a fixture company whose only filings are historical has a `securities` row with `known_at` = its first filing's acceptance; `securities_as_of` at a T before any filing returns nothing for it.
- [ ] `snapshot_static` attributes apply before fetch time only for the columns config allows; `health` reports the reliance count. `snapshot_static` values are never written into a row with an earlier `known_at`; as-of reads and the truncation harness give such rows no exemption, so before its `known_at` the row is invisible (#35).
- [ ] Delistings: 25 and 25-NSE both end the named listing; a Form 25 on a preferred class leaves the common listed; a transfer within the window keeps the security in `universe_as_of` on the new exchange once the new listing is known, and shows it delisted at a T before that; the delisted fixture name is absent from `universe_as_of` after its last session and present in `securities_as_of` with dates.
- [ ] `universe_as_of(T)`: rules 1–8 in ADR order; dual-class cap summed over classes, both listed classes admitted; split-adjusted cap; stale shares excluded; output records enabled rules and the fill-price setting.
- [ ] `universe.liquidity_rule_enabled=false` (when set; the default is `true` since T3): rule 5 skipped and recorded; `health` shows it.
- [ ] Each `universe.*` key overridden one at a time changes membership as expected; AST check: `universe.py` and `gap.py` contain no numeric literals other than 0, 1, -1.
- [ ] `survivorship_gap(T)` matches hand-computed count and size share per the formula; clean merger delisting is *not* missing; the three side categories are reported separately.

Adapters and fixtures
- [ ] EDGAR parsers on recorded payloads: filing-derived records have `known_at` = acceptance and `provenance = filing`; snapshot records `provenance = snapshot` with recorded fetch time; SGML header yields SIC; cover page yields title, symbol, exchange; 25 and 25-NSE yield class title and exchange.
- [ ] Alpaca parsers on recorded payloads: raw closes, `known_at` per timing rule, actions with the proxy rule, resolution through the master.
- [ ] Scrub test: no fixture contains a key-shaped string, an email pattern, or a `User-Agent`-shaped string (pattern-based; no `.env` needed).
- [ ] No `.env`: package imports and all tests pass.

Ingest
- [ ] Idempotent re-run for a completed session: no fact rows change; `rows_added = 0`.
- [ ] Staleness on holiday, weekend, pre-close, half day: expected latest session is the last completed one (four tests).
- [ ] Stale reference symbol: non-zero exit, `status = stale`, no fact rows changed for that source.
- [ ] Mid-chunk failure: that chunk not committed, earlier chunks committed, `--backfill` resumes from `chunk_cursor`.
- [ ] Lock: with a short-lived reader that closes within `lock_retry_seconds`, ingest completes; with a writer that never closes, ingest exits non-zero after the retry window. The page shows "store busy" while a chunk commits.

Health and page
- [ ] `health --check` passes on the fixture store; fails naming the rule on an injected duplicate bar, overlapping listing, bar after a delisting end, or changed SIC default.
- [ ] Headless render shows every `health.py` metric, including delisted names and `snapshot_static` reliance.

Real-store evidence (close-out PR, not unit tests)
- [ ] `universe_as_of` at a month-end **inside the backfill window** on the owner's real store is non-empty and includes at least one name later delisted.
- [ ] Five consecutive scheduled `ingestion_runs` rows with `status = ok`.

## Out of scope

Signals, backtester, trial registry, cost model (Phase 3). Risk-gated broker wrapper, Alpaca paper adapter, alerts (Phase 4). Paid vendor (Phase 3 ADR). Intraday data. Fundamentals beyond shares outstanding and SIC. Pre-2019 ticker history beyond `snapshot_static`. Parquet export command is Phase 2 (`tradepartner export`) but automated export at tags is Phase 3 tooling.

## Data / interfaces

**Tables** (all with `known_at`, `ingested_at`, `source`, `provenance`):
- `securities(security_id, cik, name, benchmark)`
- `listings(security_id, ticker, exchange, class_title, valid_from)` — `valid_to` derived at read time
- `classifications(security_id, sic, security_type, rule)`
- `delistings(security_id, form, class_title, exchange, filed_at, effective_on)` — end session and transfer status derived at read time
- `prices_daily(security_id, session, open, high, low, close, volume)` raw
- `corporate_actions(security_id, action_type, ex_date, ratio_or_amount)`
- `facts(security_id, fact_name, as_of_date, class_member, value, filing_accession)`
- `ingestion_runs(run_id, started_at, finished_at, status, source, mode, rows_added, chunk_cursor, message)`

**Master column sources and `known_at`**

| Column | Source | `known_at` | Provenance |
|---|---|---|---|
| securities row, cik | earliest **issuer-form** filing per CIK in the full-history EDGAR filing index | first acceptance | filing |
| name | companies snapshot | fetch time, allowed static | snapshot_static |
| ticker, exchange, class title (≥ ~2019) | cover-page `dei:TradingSymbol`, `dei:SecurityExchangeName`, `dei:Security12bTitle` | acceptance | filing |
| ticker, exchange (earlier) | Alpaca assets snapshot + companies snapshot | fetch time, allowed static | snapshot_static |
| SIC | filing SGML header | acceptance | filing |
| security type | rules per ADR 0006 "Verify" (SIC 6770; 20-F/40-F; F-6; N-CSR/N-PORT/485BPOS/N-2; title/suffix; asset class) | acceptance of the filing used, else fetch time | filing / snapshot_static |
| shares outstanding | XBRL `EntityCommonStockSharesOutstanding` with cover `as_of_date` and class dimension | acceptance | filing |
| delisting / transfer | Forms 25 and 25-NSE, per class and exchange | acceptance | filing |
| splits, dividends | `PriceSource.corporate_actions` | first seen: announcement time else close before ex-date; revisions: `ingested_at` | action |
| bars | `PriceSource.bars` | session close; revisions: `ingested_at` | bar |

**Survivorship gap** at T: let W = the sessions in (previous rebalance session, T]. L = common, non-benchmark listings active at **any** session in W (known at T). M = names in L still listed at T with no bar at T, plus names in L delisted (not transferred) inside W whose last bar is more than `gap.missing_tail_sessions` before the **last session before the Form 25 filing**. Count share = |M|/|L|. Size share = Σ(shares × last known raw close) over M / Σ over L; names with no close ever contribute zero and are counted. Side categories, reported separately and not in M: `unclassifiable`, `truncated_history` (rule 6), `stale_shares` (rule 7).

**Interfaces** (`src/tradepartner/adapters/`): `PriceSource.bars(ids, start, end)`, `.corporate_actions(ids, start, end)`; `FilingSource.filing_index(since)`, `.companies_snapshot()`, `.facts(cik, names)`, `.filing_headers(cik, forms)`, `.cover_pages(cik)`, `.delistings(since)`; raw clients `alpaca_raw.*`, `edgar_raw.*` return raw payloads; `Broker.submit/cancel/positions/fills`.

**Config keys**: `store.path`, `store.lock_retry_seconds=60`, `ingest.settle_delay_minutes=60`, `ingest.reference_symbol=SPY`, `ingest.max_missing_share=0.05`, `edgar.cache_dir`, `master.transfer_window_sessions=5`, `master.issuer_forms=[10-K,10-Q,8-K,20-F,40-F,S-1,F-1,10-12B,25,25-NSE]`, `master.static_columns=[name,ticker,exchange]`, `benchmarks=[SPY,MTUM]`, `execution.fill_price=close`, `alpaca.historical_feed=sip` (T3), `universe.security_types=[common]`, `universe.exchanges=[NYSE,NASDAQ,NYSE_AMERICAN]`, `universe.exclude_sic_ranges=[[4900,4999]]` (guarded), `universe.min_price=5`, `universe.liquidity_rule_enabled=true` (T3; was `false`, `universe.min_median_dollar_volume=5_000_000`, `universe.liquidity_window=20`, `universe.min_history_months=12`, `universe.max_shares_age_days=400`, `universe.top_n_by_cap=1000`, `gap.missing_tail_sessions=5`, `gap.count_share_threshold=0.05`.

**Env vars**: `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `SEC_EDGAR_USER_AGENT`.

**CLI**: `tradepartner ingest`, `health [--check]`, `dashboard`, `export` (Parquet of every table), and `python -m tradepartner.cli_record` (owner-run recorder, before the CLI exists).

## Risks & domain checks

- **Look-ahead:** adapters stamp `known_at`; the as-of API filters; truncation invariance with probes around every `known_at` proves agreement; the broken adapter proves each check has teeth; revisions are never back-dated.
- **Survivorship:** securities exist from their first filing, so delisted names are in the denominator; the gap is bounded and shown; it gates the holdout in Phase 3.
- **Adjusted-price rewrites:** raw storage plus read-time adjustment; revised actions get `ingested_at`.
- **Level-rule timing:** raw close at T and ex-date ≤ T, so a known-but-future split cannot distort cap or the price floor.
- **Ticker reuse and class confusion:** master keyed by `security_id`; delistings matched by class title and exchange.
- **Secrets:** pydantic settings; scrub test; fixtures pattern-checked.
- **Terms and opens:** the owner's T3 research resolves Alpaca terms, auction vs IEX open, fractional-at-open behavior, actions depth, history depth, assets-endpoint reuse, EDGAR rate limit and User-Agent, backfill runtime; it sets `liquidity_rule_enabled`, `execution.fill_price`, `max_missing_share`.
- **Single writer:** chunked commits release the lock; readers are short-lived; page has a busy state.
- **Costs:** none. **Order path:** fake broker only. **LLM inputs:** none.

## Open questions

1. **Pre-2019 ticker ranges** remain `snapshot_static`. Because such rows count only from their fetch time (#35), a name listed before 2019 is absent from `universe_as_of` until a filing supplies its ticker and exchange, so coverage early in the backfill window is thin and skewed by when each company first filed a tagged cover page. A research brief on 8-K Item 5.03/3.03 parsing is the candidate if Phase 3 needs better; not here.
2. **Backfill window**: if T3's estimate exceeds one overnight run, `--since` defaults to the last 3 years and the plan says so. `--since` limits bars, actions and facts only; the filing index and Forms 25/25-NSE are always full history.
3. **`edgartools` cover-page parsing**: if it proves unstable, fall back to parsing the iXBRL `dei` tags with the standard library and pin the approach in T11.
