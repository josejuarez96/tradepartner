# Engine first: what TradePartner does, how other platforms show it, and what the UI should become

**Status:** proposal for the owner · **Date:** 2026-10-10 · **Branch:** `spike/design-ui`
**Why:** the owner asked to step back before more screens: "a lab meets a trading engine, scientific aesthetic but with capabilities; a user should eventually understand how the backtest engine works." This note maps the real engine (from `src/` and `docs/`), summarises how QuantConnect and others present theirs, and proposes a structure built from the engine instead of from a generic finance app.

Sources: a read of `src/tradepartner/` and the docs (charter, roadmap, STATUS, research-program, ADRs 0005/0008/0010/0011/0016/0017, specs for backtest, strategy lab, paper trading, `design/dashboard.md`), and a web survey. The web survey could not open vendor pages (the proxy blocked them); claims marked *(unverified)* come from search summaries or memory and need a spot check.

## 1. The engine, as one machine

The same pipeline runs in two places: **the lab** (a backtest, over history) and **a book** (paper trading, once per session). The UI should show one machine in both modes, so that understanding one teaches the other.

| Stage | What happens | What is recorded today |
|---|---|---|
| 1. Data as of T | Prices, corporate actions, filings, delistings; every fact has `known_at`; reads return the latest revision known at T | `prices_daily`, `facts`, `delistings` … with `known_at`, `provenance`; `health` report |
| 2. Universe | ADR 0006 rules in order (security type, exchange, … `top_n_by_cap`); each excluded name counted once under the first rule it fails; survivorship gap | backtest: `trial_rebalances.n_universe`, `gap_*_share`, counts; paper: `signals.reason = excluded_*` |
| 3. Signal and rank | e.g. momentum = close(skip)/close(form) − 1; ranked | **backtest: computed, not stored** (`Plan.scores` lives in memory only); paper: `signals(score, rank, reason)` |
| 4. Targets | top fraction, equal weight, long-only, rest in cash | backtest: `trial_weights.target_weight`; paper: plan |
| 5. Risk gate | frozen limits per window (position weight .05, order notional, orders per run, drawdown .30 …) | paper: `decisions` (`trade`, `skip_*`, `dust`, `forced_exit`), halts |
| 6. Orders and fills | sells first, then buys; costs `per_side_bps`; paper orders have a full event chain | backtest: `fill_price`, `shares`, `cost_paid`; paper: `orders` → `order_events` → `fills` → `outcomes` |
| 7. Portfolio and marks | daily values, cash, exits (delisted, stale) | `trial_equity` per cost level; `positions_daily`, `lots` |
| 8. Judgement | metrics, luck check (deflated Sharpe with the family's N), red flag, exam rules | `trial_metrics`, `trial_results(n_trials, sr_star, dsr …)`, `owner_decisions` |

Around the machine sit the **registry** (every run is a trial with its frozen settings, `params_sha256`, `code_version`, `data_cutoff`, `store_max_ingested_at`), the **lab** (sweeps with pre-registered `promote_at_least` / `retire_below`), the **exam rules** (development boundary, holdout spent once, forward exam on paper) and, for books, the **daily run** (switch check → stale-data check → collect fills → reconcile → mark → plan → trade) with the kill switch and reconciliation.

This is much richer than what the spike shows. The spike shows outcomes (a value, a chart, a list); the engine records *process*. A lab UI should show the process.

## 2. How other platforms present their engines

| Platform | What it shows | Borrow | Avoid |
|---|---|---|---|
| QuantConnect | Results page that updates while the backtest runs: a banner of running numbers, an equity chart that grows, then about 40 statistics and tabs (orders, logs, a snapshot of the code) *(tabs unverified)*; optimisation charts that change form with the number of parameters; a debugger with breakpoints | The engine visibly running (simulated clock, growing chart, counters); the code and settings snapshot stored with the run | A wall of 40 equal-weight statistics; every optimisation run overlaid with no legend |
| Quantopian (historic) | Full backtest page with sections; tear sheets (returns, positions, round trips, factor risk); contest rules shown as pass/fail with values; a "cone" comparing live returns against the backtest's range *(unverified)* | Checks as pass/fail with the measured value; live-vs-backtest cone (we already have the expected-range band) | Tear sheets as pages of charts with no reading order |
| Composer | The strategy as a visual tree of plain-language blocks; backtest with historical allocations by date | The rule as a diagram that is also the explanation; "what it held on this date" | Defaults (slippage, window) hidden off the main view |
| TradingView / TrendSpider | Strategy tester dock (overview, performance, list of trades, properties); trades drawn on the chart; bar replay | A Properties panel listing every assumption next to the result; replay one step at a time; list ↔ chart linking | Arrows on a chart without context (TrendSpider's own help says so) |
| W&B / MLflow | Runs table, compare runs, parallel coordinates for sweeps, lineage (commit, data, config) | Every backtest as a run with lineage; a diff between two runs | Sweep plots that invite picking the best cell |
| StrategyQuant / Build Alpha / Numerai | Walk-forward grid, Monte Carlo fans, noise tests *(Build Alpha unverified)*; Numerai leads with consistency across periods | Consistency per period (per year) as a headline; out-of-sample zones shaded on the curve | 3D surfaces |

**The gap nobody fills:** no platform found puts the number of tries and the deflated Sharpe next to the winning result. TradePartner already computes both (`n_trials`, `sr_star`, `dsr`). Making that honesty visible is the product's clearest point of difference.

## 3. Proposed structure: an instrument with a lab notebook

Two kinds of screen, matching the engine's two modes, plus the bench that ties them together.

1. **Bench (home).** Is the machine healthy, is anything waiting on the owner, how are the books doing against what was expected. Quiet when nothing is wrong. (Today's Overview, kept.)
2. **Lab.** Ideas → pre-registration → trials → judgement.
   - **Run ledger** (MLflow-style): every trial as one row with its family, window, N at the time, luck check, status (`ok`, `refused_*`). Rows open the trial page.
   - **Trial page**, the lab's centre:
     - *Run identity:* hypothesis, frozen settings hash, code version, data cutoff, run date.
     - *Honesty strip:* versions tried in the family (N), the luck bar SR*, the luck check, exam status (locked / spent / forward), red flag if raised.
     - *Hero:* equity against the S&P with the zones shaded on the timeline (development window, dead months, holdout), event dots on rebalances.
     - *Consistency:* one bar per year, excess over the S&P.
     - *Assumptions panel:* universe rule, cost per side and the cost sensitivity levels (0/15/30/60/100 bp, all already computed), fill price, survivorship gap.
     - *Checks:* pass/fail with values (gap within limit, red flag, enough periods).
   - **Rebalance step-through: the machine view.** Pick a rebalance (or press play) and watch one pass through the pipeline, stage by stage: universe (N in, exclusions by rule), signal (distribution with the cut line), selected names, targets, trades and costs, portfolio after. This is the screen that teaches how the engine works.
   - **Sweeps:** variants as a small multiple or heatmap (by number of parameters), always beside N and the moving luck bar, with the pre-registered promote/retire lines drawn on.
3. **Books.** The same machine, run daily.
   - **Run timeline:** each session's run as its steps (switch check, stale data, fills, reconcile, mark, plan, trade) with a status for each; a failed step says what happened and what holds.
   - **Order chain:** decision → order → broker events → fill → outcome, with the risk check that applied.
   - **Holdings and why** (built), **tracking against the backtest** (`paper report`), **kill switch and window stop kept apart** (see §5).
4. **Data.** Health of the store: freshness, gaps, retractions, the `known_at` story for a fact. Rarely visited; one screen.

## 4. Aesthetic: scientific instrument, not trading app

- **Figures, not widgets.** Each chart is a numbered figure with a caption that says what it shows and how it was computed (the method note the Overview already has). Axes with ticks and units.
- **Uncertainty is always drawn.** Bands, intervals, the luck bar. A number a researcher would question carries its ⓘ.
- **The pipeline as a schematic.** A small, labelled diagram of the eight stages, reused as navigation in the step-through and in a book's run timeline; the current stage lit.
- **Identity in mono, prose in sans.** Run IDs, hashes, code versions, dates in IBM Plex Mono; explanation in Plex Sans. (Keeps direction E's tokens.)
- **Analog where it earns it:** candles and order lines on a holding's chart, tick-marked scales, a replay scrubber. Not as decoration.
- **Watch the tells** ([ai-tells.md](ai-tells.md)): broadsheet hairlines, mono for every label and numbered "01/02/03" sections are the second-order clichés this style drifts toward. Figure numbers are earned only where figures are referenced in text.

## 5. What the docs constrain (and where the spike is out of line)

- **ADR 0011 (accepted): Streamlit, no separate API, localhost only**, for the Phase 2–3 dashboard; those pages exist (health, backtest, trials, research, operations, override, per book). The roadmap left the next step open on purpose: "Whether Streamlit with this standard is enough, or a React front end behind a thin read-only API is warranted, is a Phase 4 ADR" (`roadmap.md`). That ADR has not been written. The owner's direction (2026-10-10) is a real app, not Streamlit, so the ADR is the missing piece: React front end, read-only local API, which writes it may make, and how the phone reaches it.
- **The UI is read-only except one write:** the logged override (`exclude_name`, `keep_name`, `engage_kill_switch`) with a reason. Stop/resume from the UI needs a spec change.
- **"Stop" is two things.** The spike's "Stop book" (holdings kept, next run skipped) is the **kill switch** (`paper kill`; released only by `paper resume` after a clean reconciliation). `paper stop` **closes the window and sells**. The UI must name them differently.
- **No contest between books** (ADR 0017 open question): rank books against their own expected range, not against each other.
- **No LLM cards or chat** (ADR 0008, `design/dashboard.md`).
- **Phone access** conflicts with localhost-only until a network decision exists.
- **Not in any store:** "lessons" (nearest is a `TP-` claim), the "waiting on you" queue, the backlog (files). The Research screen's data has no source yet.

## 6. What it would take

| Need | Exists? | Work |
|---|---|---|
| Trial page (identity, honesty strip, zones, cost sensitivity) | Yes: registry, `trial_metrics`, `trial_equity` per cost level | UI only |
| Expected-range band per book | Yes: `tracking_error_spy` is a trial metric | UI only; replaces the 6%/8%/5% placeholders |
| Per-year consistency bars | Yes: from `trial_equity` | UI only |
| Rebalance step-through (universe, trades, costs) | Mostly: `trial_rebalances`, `trial_weights` | UI only |
| …including the signal and rank at each rebalance | **No**: `Plan.scores` is not persisted | Engine change: persist scores and ranks per rebalance (new table, plan task) |
| Book run timeline, order chains | Yes: runs, `decisions`, `orders`, `order_events`, `fills`, `outcomes` | UI only |
| A data source the React app can read | **No** API | The Phase 4 front-end ADR picks one: a read-only local API over `ops.page_data` and registry reads, or `tradepartner export` Parquet files |

## 7. Decisions for the owner

1. **The Phase 4 front-end ADR.** Direction set by the owner: a real app (React), not Streamlit. To decide in the ADR: a read-only local API over the existing readers versus reading `tradepartner export` files; which writes the app may make (today only the logged override); whether the Streamlit pages stay as the developer's view; and how the phone reaches a localhost-only system.
2. **Which machine view first:** the lab's trial page with the rebalance step-through, or a book's run timeline with order chains?
3. **Engine change:** open a plan task to persist backtest signal scores and ranks, so the step-through can show why each name was chosen?
4. **Stop/resume from the UI:** keep it as the one override (`engage_kill_switch`) and leave release to `paper resume`, or specify a UI release?
