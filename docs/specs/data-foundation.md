# Spec: Data foundation (Phase 2)

**Status:** Draft  ·  **Issue:** #8  ·  **Related:** [ADR 0003](../decisions/0003-data-adapters-local-first.md), [ADR 0004](../decisions/0004-tooling-adopt-avoid.md), [ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md), [ADR 0006](../decisions/0006-universe-and-cadence.md), [roadmap Phase 2](../roadmap.md)

## Problem & why now

Nothing in Phases 3–6 can be trusted unless the data underneath answers one question correctly: *what was knowable at time T?* This spec builds that layer: a point-in-time store, a security master, a trading calendar, the adapters that feed them from free sources, a daily job, and the page that shows whether the data is healthy. It is the first code in the project.

## Users & usage

- **The owner**, once a day, glances at the data-health page (is the store current, are there gaps, how big is the survivorship gap). Once, at the start, the owner runs a recording script with their own keys to produce the scrubbed fixtures the tests use.
- **The scheduler**, once per trading day after the close, runs `tradepartner ingest`.
- **Phase 3 code** (backtester, signals) reads through the as-of API and never touches adapters directly.
- **Tests**, on every PR, run the no-look-ahead suite against the fixture universe with no network and no secrets.

## Definitions

- **T** is always a tz-aware UTC timestamp. A rebalance *date* is mapped to T = that session's close via the calendar. Never a bare date.
- **`known_at`** is when a fact became knowable to a market participant. **`ingested_at`** is when we stored it. `known_at ≤ ingested_at` always.
- **Revision**: a later value for the same key is a **new row** with its own `known_at`; rows are never updated in place. "Latest as of T" means the row with the greatest `known_at ≤ T`.
- **Provenance** on every row: `filing` (from a dated SEC filing; `known_at` = acceptance timestamp), `bar` (per the timing rule), `action` (per the corporate-action rule), or `snapshot` (from a current-only endpoint; `known_at` = fetch time, so it is only valid for T ≥ fetch time).

## Requirements

1. **Store.** A single DuckDB file whose path comes from config. Every fact table has `known_at`, `ingested_at`, `source`, `provenance`. Prices are stored raw OHLCV; corporate actions separately; adjusted series are computed at read time as of T (ADR 0003 rule 1). Ingest opens the file read-write; every other process opens **short-lived `read_only=True`** connections.
2. **Calendar.** A wrapper over `exchange_calendars` XNYS exposing: is-session, next/previous session, session open and close timestamps, last session of month, last *completed* session as of a timestamp, sessions in a calendar-month window, and half-day detection. No "weekday" logic anywhere.
3. **Security master.** Keyed by our `security_id`, mapped to CIK and to dated ticker ranges, with exchange, security type, SIC, delisting date and reason, and a `benchmark` flag. Each column's source and `known_at` rule is fixed in "Data / interfaces". Answers "what existed at T" (ADR 0003 rule 3).
4. **`PriceSource` interface**: `bars()` and `corporate_actions()` (splits, dividends). Two adapters: **fixture** and **Alpaca** (free plan). Each adapter separates *fetch* (thin, untested beyond a smoke test) from *parse* (pure, tested on recorded raw JSON).
5. **`FilingSource` interface**: company facts (shares outstanding), filing headers (SIC), cover-page tags (title, symbol, exchange, from ~2019), Form 25 delistings, and the current company-tickers snapshot. Two adapters: **fixture** and **EDGAR** via `edgartools` (pinned; cache directory from config; identity from config). Same fetch/parse split.
6. **`Broker` interface** and an **in-memory fake broker** (submit, cancel, positions, fills; duplicate client order IDs rejected). No risk logic here (Phase 4).
7. **As-of read API.** `prices_as_of(T)`, `adjusted_prices_as_of(T)`, `facts_as_of(T)`, `securities_as_of(T)`, `listings_as_of(T)`, `universe_as_of(T)`, `survivorship_gap(T)`. Every function takes a tz-aware T and returns only rows with `known_at ≤ T`, choosing the latest revision as of T.
8. **Ingest job.** `tradepartner ingest [--source alpaca|edgar|all] [--backfill --since DATE] [--dry-run]`. Per-source **atomic** transactions (a failure in one source commits nothing from that source; the other source's commit stands). Idempotent: re-running for the same completed session adds no fact rows. Backfill commits in chunks (per month per source) and is resumable from the last committed chunk. Halts with non-zero exit on **stale** or failed source, leaving the store unchanged except for the `ingestion_runs` row. Retries on DuckDB lock for a configured time, then exits non-zero.
9. **Staleness** is source-level: the expected latest session is the calendar's last completed session at run time minus a configured settle delay; a source is stale if its reference symbol (config, e.g. SPY) has no bar for that session, or more than a configured share of listed names are missing it.
10. **Health checks as code.** `tradepartner health [--check]` computes, in a pure `health.py` shared with the dashboard: last successful ingest per source; coverage by session; gaps; survivorship gap; unclassifiable count; liquidity-rule status; and integrity checks (non-null `known_at`, `known_at ≤ ingested_at`, bars only on sessions, no duplicate `(security_id, session, known_at)`, non-overlapping listings, guarded SIC default matches the charter). `--check` exits non-zero on any integrity failure.
11. **Data-health page.** A Streamlit page rendering `health.py` output. Read-only, short-lived connections.
12. **Fixture universe** (ADR 0003 rule 4). Checked in as **CSV** (reviewable in diffs), generated by a seeded script; the test asserts content equality on regeneration, not bytes. Required cases: a delisting with truncated history (> N sessions before Form 25), a delisting within N sessions, a split, a split between a shares filing and a rebalance T, a ticker reused by a different company, a restated shares fact (two `known_at` for one period), a stale shares fact (> 400 days), an unclassifiable name, a holiday, a half day, SPY and MTUM as `benchmark` rows with dividends.
13. **Benchmarks.** SPY and MTUM bars and dividends are ingested and flagged `benchmark`; they are excluded from the universe by type but available to Phase 3 for total return.
14. **Config.** Pydantic settings. Every threshold from ADR 0006 is a config key with the ADR's default; `universe.exclude_sic_ranges` is **guarded** (test pins it to the charter). Secrets only from `.env`; `.env.example` lists them; nothing needs `.env` unless a real adapter is called.
15. **`data-validator` agent.** A read-only `.claude/agents/data-validator.md` that runs `tradepartner health --check` and the no-look-ahead suite against a real store and reports. It computes nothing itself (charter principle 3).

## Acceptance criteria (testable)

Timing and store
- [ ] Given any fact table, when queried, then every row has tz-aware UTC `known_at` and `ingested_at`, `known_at ≤ ingested_at`, and non-null `source` and `provenance` (schema test; also a `health --check` rule).
- [ ] Given a bar for session S, when stored, then `known_at` = XNYS close of S from the calendar (normal day and half day).
- [ ] Given a bar re-fetched after the settle delay with a different close, when ingested, then a second row exists with `known_at = ingested_at`, and `prices_as_of(T)` returns the first for T before the revision and the second after.
- [ ] Given a corporate action with no announcement time in the source, when stored, then `known_at` = close of the session before its ex-date (the conservative proxy) and never `ingested_at`; given a backfill of a 2018 split run in 2026, when `adjusted_prices_as_of(2019-01-31 close)` runs, then 2017 prices are adjusted.
- [ ] Given a restated shares fact, when `facts_as_of(T)` is called for T between the two `known_at` values, then the earlier value is returned; after the second, the later one.

Look-ahead
- [ ] **Truncation invariance.** For every as-of function including `universe_as_of` and `survivorship_gap`, and for every T in a set of probe timestamps, f(T) on the full fixture store equals f(T) on a store physically truncated to `known_at ≤ T`.
- [ ] Given the deliberately broken adapter, when the suite runs, then each of its violations is caught by a named check: early `known_at` on bars → the timing check; pre-adjusted prices → the raw-close check against the fixture; ticker-based resolution → the ticker-reuse check; `known_at` after `ingested_at` → the schema check.
- [ ] Given a rebalance *date*, when any as-of function is called with it, then it raises (T must be a tz-aware timestamp).

Security master and universe
- [ ] Given the reused ticker in the fixture, when prices are requested by `security_id`, then only that company's rows are returned.
- [ ] Given the delisted name, when `universe_as_of(T)` runs after its Form 25 effective date, then it is absent; when `securities_as_of(T)` runs, then it is present with a delisting date.
- [ ] Given a rebalance T, when `universe_as_of(T)` runs, then every included name satisfies ADR 0006 rules 1–8 in order; the split-between-filing-and-T case yields the split-adjusted cap; the stale-shares case is excluded; the output records which rules were enabled.
- [ ] Given `universe.liquidity_rule_enabled = false`, when `universe_as_of(T)` runs, then rule 5 is skipped, the output says so, and `health` shows "liquidity rule disabled".
- [ ] Given each `universe.*` key overridden one at a time, when `universe_as_of(T)` runs, then the membership changes as expected (one test per key). Given `universe.py` and `gap.py`, when an AST check runs, then they contain no numeric literals other than 0, 1 and -1.
- [ ] Given the fixture, when `survivorship_gap(T)` runs, then count share and size share equal the hand-computed values, per the formula in "Data / interfaces", with the three exclusion categories reported separately.

Adapters and fixtures
- [ ] Given recorded EDGAR JSON, when parsed, then filing-derived records have `known_at` = acceptance timestamp and `provenance = filing`; snapshot records have `provenance = snapshot` and `known_at` = the recorded fetch time.
- [ ] Given recorded Alpaca JSON, when parsed, then bars have `known_at` per the timing rule, closes are unadjusted, and resolution goes through the master by `security_id`.
- [ ] Given the committed fixtures, when the scrub test runs, then no file contains an API key, secret, email address or the owner's User-Agent string.
- [ ] Given no `.env`, when the package is imported and all fixture-based tests run, then nothing fails.

Ingest
- [ ] Given a store ingested for the last completed session, when `ingest` runs again, then no fact rows change and `ingestion_runs` records `rows_added = 0`.
- [ ] Given runs on a holiday, a weekend, before the close, and on a half day, when staleness is evaluated, then "expected latest session" is the last completed session in each case (four tests).
- [ ] Given a stale reference symbol, when `ingest` runs, then exit is non-zero, `ingestion_runs.status = stale`, and no fact rows changed.
- [ ] Given a source that raises mid-chunk, when `ingest` runs, then that source's chunk is not committed, earlier chunks are, and `--backfill` resumes from the last committed chunk.
- [ ] Given a reader holding a read-only connection, when `ingest` runs, then it completes; given a writer holding the file, when `ingest` runs, then it retries for the configured time and exits non-zero.

Health and dashboard
- [ ] Given the fixture store, when `health --check` runs, then it passes; given a store with an injected duplicate bar or overlapping listing, then it exits non-zero naming the rule.
- [ ] Given `universe.exclude_sic_ranges` changed from the charter value, when `health --check` runs, then it fails.
- [ ] Given the fixture store, when the page renders headlessly, then it shows last ingest, coverage, gaps, survivorship gap, unclassifiable count and liquidity-rule status.

Unattended operation (evidence, not a unit test)
- [ ] Given the launchd job installed per the runbook, when five consecutive sessions pass, then `ingestion_runs` has five scheduled rows with `status = ok`, shown in the close-out PR.

## Out of scope

Signals, backtester, trial registry, cost model (Phase 3). Risk-gated broker wrapper, Alpaca paper adapter, alerts (Phase 4). Paid price vendor (Phase 3 ADR). Intraday data. Fundamentals beyond shares outstanding and SIC. Options, social or news data. Pre-2019 ticker-range history beyond snapshot provenance (ADR 0006 states the limit). Dashboard pages beyond data health.

## Data / interfaces

**Tables** (all with `known_at`, `ingested_at`, `source`, `provenance`):
- `securities(security_id, cik, name, security_type, benchmark)`
- `listings(security_id, ticker, exchange, valid_from, valid_to)`
- `classifications(security_id, sic, security_type, rule)` — revisions over time
- `delistings(security_id, form25_filed_at, effective_on, reason)`
- `prices_daily(security_id, session, open, high, low, close, volume)` raw
- `corporate_actions(security_id, action_type, ex_date, ratio_or_amount)`
- `facts(security_id, fact_name, period_end, value, filing_accession)`
- `ingestion_runs(run_id, started_at, finished_at, status, source, mode, rows_added, chunk_cursor, message)`

**Master column sources and `known_at` rules**

| Column | Source | `known_at` | Provenance |
|---|---|---|---|
| cik, name | EDGAR company-tickers snapshot | fetch time | snapshot |
| ticker range, exchange (≥ ~2019) | cover-page `dei:TradingSymbol`, `dei:SecurityExchangeName` | filing acceptance | filing |
| ticker range, exchange (earlier) | Alpaca assets snapshot + company-tickers snapshot | fetch time | snapshot |
| SIC | filing SGML header | filing acceptance | filing |
| security type | rules in ADR 0006 "Verify" (SIC 6770, 20-F/40-F, N-CSR/N-PORT/485BPOS/N-2, title/suffix, price-source asset class) | acceptance of the filing the rule used, else fetch time | filing or snapshot |
| shares outstanding | XBRL `EntityCommonStockSharesOutstanding` | filing acceptance | filing |
| delisting | Form 25 | filing acceptance; `effective_on` = filing date + 10 days per Rule 12d2-2 unless the form states otherwise | filing |
| splits, dividends | `PriceSource.corporate_actions` | source announcement time if present, else close of the session before ex-date | action |
| bars | `PriceSource.bars` | session close | bar |

**Survivorship gap** at T (ADR 0003 rule 5): let L = names in `listings_as_of(T)` with `security_type = common` and not `benchmark`. Missing-price set M = names in L with no bar at T, or (for names with a Form 25 at or before T) whose last bar is more than `gap.missing_tail_sessions` before `effective_on`. Count share = |M| / |L|. Size share = Σ(shares × last known close) over M / Σ over L, where names with no close at all contribute zero to the numerator and are also reported as a count. Reported separately, not in M: `unclassifiable` (no type rule matched), `truncated_history` (rule 6 failures), `stale_shares` (rule 7 failures).

**Interfaces** (`src/tradepartner/adapters/`): `PriceSource.bars(security_ids, start, end) -> Iterable[Bar]`, `.corporate_actions(security_ids, start, end) -> Iterable[Action]`; `FilingSource.companies_snapshot()`, `.facts(cik, fact_names)`, `.filing_headers(cik, forms)`, `.cover_pages(cik)`, `.delistings(since)`; `Broker.submit(order)`, `.cancel(order_id)`, `.positions()`, `.fills(since)`.

**Config keys**: `store.path`, `store.lock_retry_seconds=60`, `ingest.settle_delay_minutes=60`, `ingest.reference_symbol=SPY`, `ingest.max_missing_share=0.05`, `edgar.cache_dir`, `universe.security_types=[common]`, `universe.exchanges=[NYSE,NASDAQ,NYSE_AMERICAN]`, `universe.exclude_sic_ranges=[[4900,4949],[4960,4999]]` (guarded), `universe.min_price=5`, `universe.liquidity_rule_enabled=true`, `universe.min_median_dollar_volume=5_000_000`, `universe.liquidity_window=20`, `universe.min_history_months=12`, `universe.max_shares_age_days=400`, `universe.top_n_by_cap=1000`, `gap.missing_tail_sessions=5`, `gap.count_share_threshold=0.05`.

**Env vars** (`.env.example`): `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `SEC_EDGAR_USER_AGENT`.

**CLI:** `tradepartner ingest`, `tradepartner health [--check]`, `tradepartner dashboard`, `tradepartner record-fixtures` (owner-run; writes scrubbed JSON to `tests/fixtures/{alpaca,edgar}/`).

## Risks & domain checks

- **Look-ahead:** adapters stamp `known_at`; the as-of API filters; the truncation-invariance test proves the two agree for every function. The broken adapter proves each named check has teeth.
- **Survivorship:** free prices lack delisted names' history. The gap is bounded, shown, and gates the holdout in Phase 3. It does not block Phase 2.
- **Adjusted-price rewrites:** raw storage plus read-time adjustment.
- **Backfilled corporate actions:** the conservative proxy (close before ex-date) prevents `known_at = ingest time` from erasing history.
- **Ticker reuse:** master keyed by `security_id` and CIK; adapters never resolve by bare ticker.
- **Secrets:** pydantic settings from `.env`; never logged; fixtures scrubbed and tested.
- **Terms and opens:** verified in T2 before any real adapter: Alpaca free-plan terms, official auction open vs IEX print, fractional-order behavior at the open, corporate-actions depth, history depth, ticker-reuse behavior of the assets endpoint; EDGAR User-Agent and ~10 req/s. Outcomes set `universe.liquidity_rule_enabled` and the ADR 0006 open-price fallback, both recorded in the T2 research file.
- **Single writer:** dashboard and CLI reads are short-lived read-only; ingest retries on lock.
- **Costs:** none. **Order path:** fake broker only. **LLM inputs:** none.

## Open questions

1. **Pre-2019 ticker ranges** are snapshot provenance. If Phase 3 needs earlier reproducibility, a research brief on 8-K Item 5.03/3.03 ticker-change parsing is the candidate; not in this spec.
2. **Form 25 effective date**: the +10-day rule is the default; T7 checks whether `edgartools` exposes the stated effective date.
3. **Backfill runtime**: estimated in T2 from rate limits; if a full 10-year backfill exceeds one overnight run, the plan adds a `--since` default of the last 3 years and documents it.
