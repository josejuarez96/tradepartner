# Spec: Event data (collectors and event studies)

**Status:** Draft  ·  **Issue:** #306  ·  **Related:** [charter](../charter.md) (Scope, "Deferred beyond the MVP"), [roadmap](../roadmap.md) ("a cheap timestamped collector may be started early, as a side job"), [ADR 0003](../decisions/0003-data-adapters-local-first.md) (adapters, local-first), [ADR 0005](../decisions/0005-objective-benchmark-stop-criteria.md) (integrity criterion), [ADR 0008](../decisions/0008-llm-role.md) (no LLM, point 3), [ADR 0009](../decisions/0009-price-vendor.md) (budget USD 0, survivorship gap), [data-foundation spec](data-foundation.md) (req 1 store and lock, req 6 adapters, req 9 ingest), [backtest spec](backtest.md) (req 8 trial count, req 9 registry, req 11 holdout), [paper-trading spec](paper-trading.md), [strategy-lab spec](strategy-lab.md) (quiet intervals), research [G2 insider purchases](../research/2026-09-26-g2-insider-purchases.md), [G5 search volume](../research/2026-09-26-g5-search-volume.md), [free-data terms](../research/2026-09-25-free-data-terms.md)

## Problem & why now

The system knows prices and a few filing facts. It does not know **what happened** to a company or to the market on a given day: an earnings release, an insider purchase, a bankruptcy notice, an executive order. Every later direction the owner has named needs that record, stamped with when it became knowable:

- new systematic signals (insider purchases: G2 found no post-2010 monthly-horizon test and said only our own backtest can close that gap);
- the Phase 5 LLM memo, whose input packet ADR 0008 requires to be fixed and timestamped;
- context and alerts for held names in Phase 4 ("why is this down 20%?", "a held company filed a bankruptcy 8-K");
- a later picks journal and override review.

It also needs one measuring tool that answers **"what happened to prices after events like this?"** (an event study). That tool is useful beyond events: the cost of the gap between signal and fill (the T70 timing question), how delisted names behaved before they left (the survivorship gap ADR 0009 measures only as a lower bound), and whether owner overrides helped.

**Why now, while the MVP is still in build:** some sources can only be collected going forward. EDGAR is point-in-time by construction (every filing has an SEC acceptance timestamp), so it can be backfilled honestly at any time. The Federal Register can be backfilled with a conservative stamp. News cannot: an archive fetched later is not what was knowable then (handoff §5, D3). Every month news is not collected is a month of evaluation history that can never be recovered. This spec is a **side track**. It does not block, and must not slow, the Phase 2–4 critical path: the collectors are built first by one team (req 13), and the study engine, which touches the trial registry, waits until Phase 3 has closed (T45b).

## Users & usage

- **The scheduler** runs `tradepartner collect` on its own interval (`collect.interval_minutes`) on every day, trading or not. It fetches new items from each enabled source, stores them raw, parses them and exits. It never opens the main store read-write, and never touches the broker or the paper account.
- **The owner** glances at the collection card on the data-health page (coverage and last success per source), runs `tradepartner collect --backfill --source S --since DATE` once per source, and runs event studies (`tradepartner study run <slug>`) on the real store when they want an answer. Only the owner runs anything against the real store (CLAUDE.md rule 9).
- **Build agents** run collectors against recorded fixtures and studies on the fixture store only, as `backtest-runner` does.
- **Later phases** (Phase 4 ops page, strategy-lab hypotheses, Phase 5 memo) read the event tables through an as-of API. None of them does so until its own spec says so (req 11).
- **Tests**, on every PR, run parsers on recorded payloads and studies on the fixture universe with the network disabled.

## Definitions

- **Event**: one stored record of something that happened, from one source, with `known_at` (when it became knowable to a market participant, tz-aware UTC), `ingested_at`, `source` and `provenance`, as in the data-foundation spec. An event names a **company by CIK** (or none, for a market-wide event such as an executive order), never a `security_id`. The security it concerns is resolved **at read time** as of `known_at` through the security master, so a later ticker change or class split cannot rewrite history.
- **Fan-out**: a company event resolves to **every** security of that CIK listed as of `known_at`. A study takes **one observation per event**, on the security the study's universe rule ranks first among them (for the ADR 0006 universe, the class the universe holds; when it holds both, the larger by the universe's own liquidity measure at close(E−1)), and reports how many events had more than one listed class. A Form 4 row names its security class from the filing, so it resolves to that class alone.
- **Event session** E of an event: the first XNYS session whose close is strictly after `known_at`. An 8-K accepted at 16:30 ET on a session belongs to the next session; the same rule handles half-days and holidays through `tradepartner.calendar`.
- **Entry session**: the session a strategy could first hold the name under the backtest's fill convention: read at close(E), fill at `calendar.next_session(E)` at `execution.fill_price`. The study reports the **reaction** (close(E−1) to close(E)) separately from the **tradeable drift** (from the entry fill onward), because the system trades daily bars with next-session fills and can never capture the reaction.
- **Two clocks.** The **selection clock** is each event's own `known_at`: nothing known after it may decide whether the event is in the study (req 9 (a)). The **measurement clock** is the run's `data_cutoff` (the trial's stored cutoff, backtest req 9): every price, action and benchmark used to measure returns is read as of that one instant, so a bar revision known later than one window's end but before the cutoff is seen by every window alike. The two clocks are recorded on the trial row.
- **Window**: a pair of session offsets relative to E, from config (`study.windows`). Offsets count XNYS sessions from `tradepartner.calendar`, never weekdays.
- **Abnormal return** over a window: the name's total return minus SPY's total return over the same sessions (market-adjusted). Both use the as-of adjusted prices at the measurement clock. A market-model or factor adjustment is out of scope for v1.
- **Study**: a file under `docs/studies/<slug>.md` that fixes, before any run, the event selection rule, the windows, the benchmark, the cost levels, the family it informs and what result is expected. It is registered before it runs, like a hypothesis, and each run is a trial (req 8).

## Requirements

**Collection**

1. **Separate events file.** Event tables live in their own DuckDB file (`events.path`), not in `store.path`. Collectors are its only writers, and collectors never open the main store read-write (a boundary test enforces both, req 11). Rationale: DuckDB allows one writer, and `ingest` and `paper run` already compete for the main store. Readers (studies, pages) open both files with the short-lived `read_only=True` rule of the data-foundation spec req 1, attaching the events file. **The one exception is the registry:** `study register` and `study run` write `studies` and `trials` rows in the main store exactly as `hypothesis register` and `backtest` do, taking the same writer for the same moments, and the strategy-lab's quiet intervals apply to `study run` as they do to a sweep.
2. **Sources, v1.** Each source is an adapter behind one `EventSource` interface (thin raw fetch + pure parser, as ADR 0003 and data-foundation req 6), with a fixture twin:
   - **(a) EDGAR 8-K and 8-K/A**: accession, CIK, form, item numbers, acceptance timestamp, and the primary document cached raw on disk (text not parsed in v1). `known_at` = acceptance timestamp (`provenance = filing`). The master ingest already reads 8-K accessions and acceptances from the submissions index (`edgar.header_forms`), so the collector **reuses that index and the existing submissions cache**; its only new fetches are the item list and the primary document per accession. It never re-fetches the index.
   - **(b) EDGAR Forms 4 and 4/A**: one row per reported non-derivative transaction: reporter CIK, reporter role (director, officer and title, 10% owner), transaction code, transaction date, shares, price, shares owned after, the security title, and whether the filing is late. `known_at` = acceptance timestamp. G2's point-in-time notes apply: never the transaction date, never the deemed filing date.
   - **(c) Federal Register presidential documents and rules**: document number, type (executive order, proclamation, memorandum, rule, proposed rule), agencies, title, abstract, signing and publication dates, raw JSON cached. `known_at`: forward collection = first fetch time (`provenance = snapshot`); backfill = **the close of the first XNYS session on or after the publication date** (the late rule), until the research brief in req 13 shows the public-inspection time is reliable enough to replace it. A backfilled row is never stamped earlier than the item was publicly available.
   - **(d) News metadata**: **only if the owner decides so on open question 1.** Headline, source, URL, source-stated publication time, and entity tags if the source provides them; never full article text. `known_at` = our fetch time (`snapshot`), always, and the row stores `stamp_resolution_minutes` = the `collect.interval_minutes` in force at the fetch, so a study can show how much of "priced in" is the interval. The source's publication time is stored as evidence and is never used as a stamp.
3. **Backfill.** `collect --backfill --since DATE --source S` for (a), (b) and (c) only, in atomic chunks, resumable and idempotent (the ingest rules of data-foundation req 9). Form 4 history starts at `collect.form4_backfill_start`, default **2013-01-01**, three years before price history, so G2's opportunistic-insider classifier (prior three years) can be built for 2016 onward. Nothing for (d) is ever backfilled.
4. **Append-only, revisions as new rows.** Amendments (8-K/A, 4/A) are new rows linked to the original accession. Nothing is updated in place. As-of reads return rows with `known_at ≤ T`, latest revision as of T.
5. **Raw kept, within a budget.** Every parsed row points at a raw payload on disk (content hash, fetch time), so a parser fix can re-parse history without re-fetching. Raw bytes are never sent anywhere else. The raw cache has a size budget, `collect.raw_size_budget_gb`; a collector run that would exceed it halts non-zero with only its `collection_runs` row written, and the data-health card shows the fill. The first collector task measures one quarter of 8-K documents and Form 4 XML and sets the default from that measurement (req 13).
6. **Pace and terms.** EDGAR requests use the existing raw client and User-Agent. The 10 requests per second limit (free-data terms 7a) is a **static split between processes**, because the limiter is in-memory and `ingest` and `collect` may run at once: `edgar.requests_per_second` (ingest, default lowered to 6) plus `collect.edgar_requests_per_second` (default 3) must not exceed 10, checked by a `Settings` validator that refuses to start otherwise. The Federal Register is paced by `collect.federal_register_min_interval_seconds`. Each new source's terms (storage, redistribution, rate) are recorded in the free-data terms report **before its adapter merges**; the Federal Register line is the first task of the plan (req 13).
7. **Health and failure.** Each `collect` run writes a `collection_runs` row per source (started, finished, status, items fetched, parse failures, last `known_at` seen, raw bytes added). Per-item parse failures are quarantined and counted, as T11h does for EDGAR (#263), never dropped silently. The data-health page gets a collection card: per source, last success, items per day over the last 30 days, gaps (days with zero items where the source normally has some), quarantined count and raw-cache fill against the budget. A collector failure never blocks `ingest`, `paper run` or a backtest.

**Event studies**

8. **Registered, counted, holdout-safe.** The registry changes are one additive migration to **store schema version 6** (the journal took 5), with `REGISTRY_TABLE_NAMES` updated and the fact tables untouched:
   - a `studies` table beside `hypotheses` (slug, file hash, canonical parameters and their hash, family, the frozen `holdout.start`, `holdout.end` and `in_sample_start` copied from `Settings` at registration, registered_at, code version). Identical re-registration returns the existing row, as backtest req 3.
   - `trials` gains a nullable `study_id`; `hypothesis_id` becomes nullable; a CHECK requires exactly one of the two; the `kind` CHECK gains `event_study`. A study trial records both clocks (Definitions). Every other trial column keeps its meaning.
   - `HypothesisFamily` gains `diagnostic` in code (a reviewed change, as the docstring requires). Unlike `oracle`, `diagnostic` is **allowed on the real store** and its trials are **never counted in any N**; the registry refuses a `diagnostic` hypothesis, so only studies use it.
   - **Counting** (resolves open question 3 of the first draft): a study run in a strategy family **counts toward that family's N**. Backtest spec req 8 is amended in the same PR to read "N = the number of `ok`, non-synthetic trials of kind `in_sample` or `event_study` in the same family at run time"; V is unchanged, since a study has no monthly Sharpe and contributes no (parameter hash, window) pair. The strategy-lab's declared-count reporting shows the cost.
   - **Holdout**: a study freezes the holdout at registration (above). A run whose widest window, at any selected event, touches the frozen holdout is refused (`refused_holdout`) and leaves a `refused` result row. There is no `--spend-holdout` for studies: a study never spends a holdout.
   - A study run without a trial handle raises, as the engine does (backtest req 9).
9. **The engine.** `study.run(definition, provider, handle)` is a pure function over a read-only provider:
   - **(a) Selection** sees only rows with `known_at ≤` the event's own `known_at`, plus the event row itself (the selection clock). Nothing about what happened after the event can select it.
   - **(b) Universe** at each event is the ADR 0006 universe as of close(E−1) unless the study says otherwise, resolved per the fan-out rule. Delisted names are included: a name whose listing ends inside a window is sold at its last close, as the engine does (T37c), and counted.
   - **(c) Output per event**: raw and abnormal return per window, the reaction and the tradeable drift separately, gross and at each `costs` level for one round trip, every price read at the measurement clock.
   - **(d) Aggregate per window**: event count, **distinct event dates**, mean, median, share positive, and a t-statistic whose standard error is **cluster-robust with one-way clustering by event date** (Liang–Zeger sandwich, the Petersen 2009 form, small-sample factor G/(G−1) with G the number of dates), because events on one day are not independent. The cumulative average abnormal return path is reported session by session from the first offset in `study.windows` to the last, with a band of ± z × the clustered standard error of the cumulative mean at each offset, z the normal quantile for `study.band_confidence` (default 0.95, so 1.96).
   - **(e)** Refuses to report a window with fewer than `study.min_events` events or `study.min_distinct_dates` dates, showing the counts instead.
10. **First studies**, shipped with the engine as registered study files:
    - **(a)** 8-K item 2.02 (results of operations), reaction and drift, momentum-universe names, family `momentum`, from 2016-01-01 to the last session before the study's frozen `holdout.start`.
    - **(b)** Form 4 open-market purchases (code P) by officers and directors, drift at 5, 20 and 60 sessions, family `momentum`. This is the evidence G2 asked for, run as a study, not yet a hypothesis.
    - **(c) Diagnostic:** prices of Form 25 names over the 60 sessions before `effective_on`, read from the store's delistings, a direct look at what the survivorship gap hides.
    - **(d) Diagnostic, once paper fills exist:** the move from signal close to fill price, per fill, as the T70 timing evidence. It reads fills **only through `store.journal.fills_for`** (the T49c accessor), so T50's fills boundary test stays true, and it waits on T49c.
11. **Boundaries**, each a case in a tree-wide AST test like `tests/execution/test_boundaries.py`:
    - Nothing under `backtest/`, `execution/` or the risk wrapper imports the events package or names an event table in SQL until a spec names that reader.
    - No module in the collect or events packages opens `settings.store.path` with anything but `read_only=True`; the collect package opens the main store not at all.
    - The events package references the journal's tables only through `store.journal` accessors, never in SQL.
    - No event is a signal until a registered hypothesis uses it through the strategy-lab or backtest spec.
    - Per the charter, news-derived signals need an ADR before any hypothesis may use them.
    - Per ADR 0008, nothing here calls an LLM or stores an LLM-produced value.
12. **Study page.** A dashboard page (design standard) with a study picker, the cumulative abnormal return path with its band, the per-window table with counts, distinct dates and costs, the multi-class event count, and the event list with links to each filing. It reads only. Where the source is the Federal Register, the page states that the stamp is publication, not signing, so the drift measured starts at publication.
13. **Order of work** (resolves open question 5 of the first draft). Two halves, two plans or one plan in two chains. **Collectors first**, by one team, in parallel with Phase 4: the Federal Register terms line and the one-quarter size measurement, then (a), (b), (c) with the health card, then (d) if question 1 says so. **The engine second**, after T45b closes Phase 3, because req 8 migrates the registry and amends the backtest spec, and neither should move while H1's real-store run is being reviewed. Study 10 (d) additionally waits on T49c.

## Acceptance criteria (testable)

Collection
- [ ] Given recorded 8-K, Form 4 and Federal Register payloads, when parsed, then every row has a tz-aware UTC `known_at` equal to the acceptance timestamp (EDGAR) or the stamping rule (Federal Register), and `known_at ≤ ingested_at`.
- [ ] Given a Form 4/A amending a Form 4, when read as of a time between the two acceptances, then only the original is returned; as of after, the amendment.
- [ ] Given a backfilled Federal Register item published on a Saturday, its `known_at` is the close of the following Monday's session (or the next session across a holiday).
- [ ] The boundary test fails if any module under `collect/` opens `settings.store.path`, or any module under `events/` opens it other than `read_only=True`, or names a journal table in SQL. Given a writer holding the main store's lock for the whole run, `collect` still completes and writes its `collection_runs` rows.
- [ ] Given a payload that fails to parse, then it is quarantined, counted in `collection_runs`, and the rest of the chunk commits.
- [ ] Given a backfill interrupted mid-chunk and rerun, then no row is duplicated and none is missing.
- [ ] Given an 8-K collection run on a fixture whose submissions cache already holds the accession, the collector fetches only the item list and the primary document, never the index (asserted on the recorded transport).
- [ ] Given `edgar.requests_per_second=8` and `collect.edgar_requests_per_second=3`, `Settings` refuses to load; 6 and 3 load.
- [ ] Given a raw cache one document below `collect.raw_size_budget_gb`, the next run halts non-zero after its `collection_runs` row and writes no parsed row.
- [ ] Given news collection (if enabled), then no news row has `known_at` earlier than its fetch time, whatever the source's publication time says, and each row carries `stamp_resolution_minutes`.
- [ ] The collection card shows a gap on a fixture day with zero items for a source that has items on the surrounding days, and the raw-cache fill.

Registry
- [ ] A version-5 store migrates to 6 additively: every journal and registry row survives, `studies` exists, `trials` accepts a row with `study_id` and no `hypothesis_id`, refuses a row with both or neither, and accepts `kind='event_study'`; the fact tables' DDL is byte-identical.
- [ ] Registering a study twice from the same file returns the same row; a changed window list yields a new row; the frozen holdout on the row equals `Settings.holdout` at registration and does not move when `HOLDOUT__START` changes later.
- [ ] N for family `momentum` counts an `ok` `event_study` trial; V is unchanged by it; a `diagnostic` trial changes no family's N; `register_hypothesis(family="diagnostic")` is refused; a `diagnostic` study runs on a store at `settings.store.path`.
- [ ] A study run without a trial handle raises. A study whose widest window at any selected event touches the frozen holdout is refused and leaves a `refused` result row, with no provider price read.

Engine
- [ ] Look-ahead: a study run on the fixture store truncated at each event's `known_at` selects the same events as the untruncated run, and its selection function is never passed a row with `known_at` after the event's.
- [ ] Measurement clock: a bar revision known after one window's end but before `data_cutoff` changes every window's return that spans the bar, identically; a revision known after `data_cutoff` changes none.
- [ ] Given a CIK with two listed classes on a fixture, one 8-K yields one observation on the class the universe rule ranks first, and the multi-class count is 1.
- [ ] Given two events on one session and one on another, the aggregate reports 3 events and 2 distinct dates, and the t-statistic uses date-clustered errors, checked against a hand computation with G/(G−1) applied.
- [ ] The band at each offset equals the cumulative mean ± 1.96 × the clustered standard error for `study.band_confidence=0.95`, checked against a hand computation.
- [ ] Given a name that delists inside a window, it is exited at its last close and counted, not dropped.
- [ ] Given an 8-K accepted at 16:30 ET, its event session is the next XNYS session, including across a holiday and a half-day.
- [ ] Study 10 (d) on a fixture journal reads fills only through `fills_for` (the boundary test), and its per-fill move equals a hand computation from the fixture's signal close and fill price.

## Out of scope

- Any LLM use: reading filings, classifying events, extracting exposures (Phase 5, ADR 0008).
- Any signal, hypothesis or order that uses an event. That goes through strategy-lab or backtest specs, and for news through an ADR.
- Social media and search-volume collection (charter deferral; G5 graded search attention NOT SUPPORTED in large caps).
- Full news article text, 10-K and 10-Q text and text diffs ("lazy prices"), company exposure maps (tariff → affected names). Each is a follow-up spec once v1 runs.
- Intraday prices, market-model or factor-adjusted abnormal returns, paid data of any kind.
- Alerts from collected events. They need the Phase 4 alert machinery and its own requirement in the paper-trading spec.

## Data / interfaces

- **Files:** `events.path` (DuckDB, gitignored), raw cache under `collect.raw_dir`.
- **Event tables** (all with `known_at`, `ingested_at`, `source`, `provenance`): `filings_8k` (+ item rows), `insider_transactions`, `federal_register_documents`, `news_items` (only if enabled), `collection_runs`, `collection_quarantine`. Schema versioned separately from the main store (`events_schema_version`, starting at 1).
- **Registry (main store, schema version 6):** `studies`; `trials.study_id` (nullable), `trials.hypothesis_id` nullable with the exactly-one CHECK, kind `event_study`; family `diagnostic` in `HypothesisFamily`; `REGISTRY_TABLE_NAMES` extended. Additive migration from 5, following the registry's own precedent; read-only connections accept a version-5 store.
- **CLI:** `tradepartner collect [--source edgar_8k|edgar_form4|federal_register|news|all] [--backfill --since DATE] [--dry-run]`; `tradepartner study register <file>`; `tradepartner study run <slug>`.
- **As-of API:** `events_as_of(T, kinds, cik=None)`, `insider_transactions_as_of(T, cik=None)`, resolving CIK to `security_id` as of each row's `known_at` per the fan-out rule.
- **Config keys:** `events.path`, `collect.interval_minutes`, `collect.sources`, `collect.raw_dir`, `collect.raw_size_budget_gb`, `collect.form4_backfill_start` (default 2013-01-01), `collect.edgar_requests_per_second` (default 3; `edgar.requests_per_second` default lowered to 6; validator: sum ≤ 10), `collect.federal_register_min_interval_seconds`, `study.windows` (default `[[-5,-1],[0,0],[1,5],[6,20],[21,60]]`), `study.min_events`, `study.min_distinct_dates`, `study.benchmark` (default SPY), `study.band_confidence` (default 0.95).
- **Env vars:** none for EDGAR (existing User-Agent) and the Federal Register (no key). A news source may add one to `.env.example`.
- **Scheduling:** one more launchd job. It writes only the events file, so it needs no quiet interval against ingest or paper runs; EDGAR pacing is the static split of req 6.

## Amendments to other documents (separate PRs after this spec merges, the #281 precedent)

- **Roadmap:** one sentence under "MVP scope": "Event data (collectors and event studies, [spec](specs/event-data.md)) is a side track beside Phases 2–4: collectors run as a scheduled side job from the moment they merge; the study engine lands after Phase 3 closes."
- **Backtest spec req 8:** the N sentence per req 8 above, dated, with this spec as the reason.
- **Config:** `HypothesisFamily` gains `diagnostic` with its docstring rule (real store allowed, never counted, studies only).
- **Free-data terms report:** a Federal Register row (API terms, rate, redistribution) before adapter (c) merges; a news-source row before (d) merges.

## Risks & domain checks

- **Look-ahead through stamps.** The biggest risk is a stamp earlier than true public availability. The rules are: acceptance time for EDGAR; fetch time for anything forward-collected; the late rule for Federal Register backfill. Late stamps bias studies toward "priced in", which is the safe direction. Executive orders are usually public at signing, days before Federal Register publication, so Federal Register-stamped studies measure the drift after publication, not the news itself; the study page says so (req 12).
- **Look-ahead through selection and measurement.** "Surprise" or "important" filters must use only data known at the event (selection clock). Returns are read at one instant (measurement clock) so no window sees a revision another does not. Req 9 (a), the Definitions and the two truncation criteria enforce this.
- **Survivorship.** Events for companies that later delisted must resolve to their securities. CIK-first storage and read-time resolution handle that. Study 10 (c) measures the gap directly.
- **Data mining.** A cheap study tool invites trying 200 event types and keeping the best. Req 8 registers and counts every strategy-family run toward that family's N, so the deflated-Sharpe hurdle for the eventual hypothesis rises with every study. Diagnostic studies cannot inform a hypothesis, which is why they are exempt.
- **Statistics.** Events cluster (earnings season, macro days). Date-clustered errors and the distinct-dates floor (req 9 (d) and (e)) stop one busy day from looking like 50 independent results.
- **Store contention.** Solved by the separate file (req 1) for collection. Study runs take the registry writer like a backtest, so they are subject to the same quiet intervals. The cost is a second file to back up.
- **Terms and rate.** EDGAR is covered by the existing research and the static split. The Federal Register (US government, public API) and any news source need a terms line before merge (req 6).
- **Disk growth.** Measured before it is assumed: the first collector task records one quarter and sets `collect.raw_size_budget_gb`; the collector halts at the budget rather than filling the disk.
- **Order path / secrets / LLM.** None touched. `safety-reviewer` is not needed unless a news source adds a key. `quant-auditor` runs on the study engine and the registry migration.

## Open questions

1. **News collection: now, or not yet?** The charter defers news-text *signals* and the roadmap allows an early timestamped collector. Do you want (d) in v1, and from which free source? GDELT is the obvious free candidate, with terms and stamp semantics to verify. Recommendation: yes, metadata only, after a short `researcher` brief on GDELT's terms and update cadence; a week's brief against history that cannot be recovered later.
2. **Federal Register public-inspection stamp.** The late rule (req 2 (c)) applies until a `researcher` brief shows whether the public-inspection availability time is reliable enough to replace it. The brief is a task in the collectors plan; no decision is needed to start.

Resolved in this draft from the first draft's questions, for the owner to confirm by merging: strategy-family study runs count toward N (req 8); Form 4 backfill starts 2013-01-01 (req 3); collectors first, engine after T45b (req 13).
