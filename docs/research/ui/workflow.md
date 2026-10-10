# How the owner works: a strategy's life, the daily routine, and the app that follows from them

**Status:** proposal for the owner · **Date:** 2026-10-10 · **Branch:** `spike/design-ui`
**Why:** the owner, after seeing a desktop backtester: "at a glance I can see it's a strategy simulator: where the parameters go, where I import a strategy, run it, log it and read the results. We're not thinking about the process flow at all." This note maps the process from the repo's own rules ([research-program.md](../../ways-of-working/research-program.md), [ADR 0016](../../decisions/0016-development-boundary-and-forward-exams.md), [ADR 0017](../../decisions/0017-fast-paper-and-machine-readiness-gate.md), the specs and `cli.py`), then derives the app's structure from it. Charts come last, and only where a decision needs one. Read with [engine-first.md](engine-first.md) (what the engine records).

## 1. One strategy's life

Every strategy moves through the same stages. Each row is one stage: what the owner does, where it lives today, what the engine records, and what he needs to see to decide.

| Stage | The owner | Lives today in | The engine records | To decide, he needs to see |
|---|---|---|---|---|
| **1. Evidence** | Reads what published research says | `docs/research/claims.toml` (282 graded claims) | nothing (files) | The claims an idea rests on and their grades |
| **2. Idea** | Ranks the backlog; parks or retires ideas | `docs/research/hypothesis-backlog.md` (B1 to B10) | nothing (files) | Prior, evidence, data and engine work needed, cost (S/M/L), kill criterion, value of the information |
| **3. Specify** | An agent drafts the file; he merges it, then registers | `docs/hypotheses/<slug>.md` or `docs/sweeps/<slug>.md`; `tradepartner hypothesis register`, `sweep register` | `hypotheses` (params, `params_sha256`, holdout window, `registered_at`); lab tables for sweeps | The parameters, exactly as frozen: universe, signal, top fraction, cadence, costs, holdout, the promote and retire lines |
| **4. Develop** | Runs it, usually overnight | `tradepartner backtest <slug>`, `sweep run` | One **trial** per run (`trials`, `trial_results`, `trial_metrics`, `trial_equity`, `trial_rebalances`, `trial_weights`), N rises | The result and its cost in tries: excess over the S&P, luck check, cost sensitivity, the rebalances |
| **5. Judge** | Promotes the winner or retires the sweep, against lines set before the run | `sweep report`, `sweep promote` / `retire`, `lab status` | `owner_decisions` (`promotion`, `sweep_retired`) | The variant chosen by the pre-registered statistic, the floor it had to clear, the family's N and luck bar |
| **6. Exam** | Spends the holdout once, on one finalist, with a reason; or lets the paper book judge it | `backtest --spend-holdout --holdout-reason`; `decision ...` | A `holdout` trial; `owner_decisions` (`holdout_spend`) | That the exam is still unspent, what it would cost to spend it, then the one result |
| **7. Paper** | Starts a book; checks it each morning; kills or resumes it with a reason; reads the tracking report | `paper start`, then the scheduled `paper run` (launchd); `paper status`, `kill`, `resume`, `report`, `check`, `shakedown` | `paper_windows`, `signals`, `decisions`, `orders`, `order_events`, `fills`, `positions_daily`, `reconciliations`, `alerts`, `kill_switch`, `paper_reports` | Did it run, is it reconciled, what it holds and why, how far it is from its backtest, how far into its forward exam |
| **8. Live** | One more book on the live endpoint, under its own ADR (Phase 6) | not built | the same journal | The same as paper, plus the live limits |
| **9. Record** | The result becomes a `TP-` claim, good or bad; the backlog item moves on | `claims.toml`, the backlog | nothing (files) | What was learned, in a sentence, beside the evidence it changes |

Two rules shape the whole flow, and the app must carry them, not work around them:
- **Every run counts.** A run is a trial and raises the family's N, which raises the luck bar every later result must clear. There is no free rerun.
- **Parameters are frozen once registered.** A changed parameter is a new sweep variant, registered and counted, never an edit.

## 2. The routine

| When | What happens | What the owner wants from the app |
|---|---|---|
| Every evening, 18:30 ET (launchd) | `tradepartner ingest` brings the store up to the session | Nothing, unless it failed or data is stale |
| Every session, before the open (launchd) | `paper run` for each book: switch check, stale-data check, collect fills, reconcile, mark, plan, trade | Nothing, unless a run failed, halted, skipped, or reconciliation mismatched |
| Each morning, a minute or two, often on the phone | He checks | Anything wrong or waiting on me? How are the books doing against what was expected? |
| When a run finishes (overnight backtest, sweep) | He reads the result | What did it find, could it be luck, what does it cost in tries, what is the decision now |
| When a decision is due | Promote or retire, spend an exam, start a book, kill or resume, set the boundary | The decision, its consequence, the evidence, and the one action (with a reason) |
| Weekly, or when a verdict lands | Reviews the backlog | Ideas by stage, what is blocked on what, what to run next |

## 3. What this means for the app

**The organising object is the strategy, not the chart.** The desktop backtester reads at a glance because its layout is its process: settings on the left, Run, then the results in tabs (plot, trades log, stats). TradePartner's process is longer, so the app should read the same way at two scales:

1. **Today** (home). The routine's first question: is the machine healthy (last ingest, last paper run per book, reconciliation, alerts), what is waiting on me (decisions due, runs to start, questions to answer), how each book is doing against its expected range. Quiet when nothing is wrong. Most mornings this is the only screen opened, often on the phone.

2. **Strategies** (the pipeline). Every idea in one list, grouped by stage: Idea, Specified, In development, Judged, Exam, On paper, Live, Retired. One row per strategy: its stage, what it is waiting on, its latest numbers if it has any. This replaces today's Research screen's mix of a results map, a needs-you list and stage groups.

3. **A strategy's page**, laid out like the backtester because the process is the same shape:
   - **Left: the specification**, the frozen parameters as registered (universe, signal, top fraction, cadence, costs, holdout window, the promote and retire lines, the kill criterion), with its fingerprint and registration date. Read-only, because the file is frozen; the panel says how a change would happen (a new variant, counted).
   - **Right: tabs that follow the stages it has reached.** *Evidence* (its claims), *Runs* (the trial log: every run, its N at the time, its status, including refusals), *Result* (one run: the equity against the S&P, the rebalances log, the statistics with the luck check; the step-through lives here), *Exam*, *Paper* (its book), *Decisions* (every owner decision on it, with reasons).
   - **The action is the engine's command.** Under ADR 0011 and ADR 0018 the app writes only the override and kill/resume, so the "Run" control shows the exact command to run, with its consequence stated ("adds trial 3 to the momentum family; the luck bar rises"). This is deliberate, not a stopgap: it is the app being the code, and it teaches the engine. A later ADR can let the app run it.
   - **No parameter form for tweak-and-rerun.** The backtester's editable parameters are exactly what this system exists to stop: each tweak is a hidden trial. Here a parameter change is a new registered variant, and the page shows its cost in tries before it happens.

4. **Books**, the operations side: one book's runs, holdings and why, orders and their journal chain, tracking against its backtest, the forward exam's progress, kill and resume.

5. **Data**, rarely: the health report, freshness, gaps.

## 4. Charts earn their place

| Decision | The one picture that helps | Already in the engine? |
|---|---|---|
| Is a book on track? | Its value against the S&P with the backtest's expected range | Yes: equity, benchmark, `tracking_error_spy` |
| Did the backtest find anything? | Excess over the S&P across the window, with the development and exam windows marked | Yes: `trial_equity` |
| Could it be luck? | The luck check on its zoned scale | Yes: `trial_results.dsr` |
| Does it survive costs? | The result at each cost level | Yes: `trial_metrics` per `cost_per_side_bps` |
| Which variant does the sweep promote? | Variants against the pre-registered floor, beside N | Yes: lab tables |
| Why does it hold this stock? | The rank and the cut at the last rebalance | Paper: yes (`signals`); backtest: **no**, scores are not persisted |

Everything else (month grids, histograms, turnover bars) is a detail opened on demand, not a panel on the first screen.

## 5. What happens to the spike's screens

| Spike screen | Becomes |
|---|---|
| Overview | **Today**, with the machine's health added and the statistics grid cut to what the morning check needs |
| Research | **Strategies**, the pipeline by stage; the results map becomes a view inside the momentum family's page |
| Trial page and workstation | The **Result** tab of a strategy's page, under its specification panel; the panels that are not a decision's picture (section 4) move behind "details" |
| Book | **Books**, unchanged in substance |

## 6. Questions for the owner

1. **The morning check:** desk or phone first, and what makes you open the app on a day when nothing is wrong?
2. **Starting runs:** do you want to start backtests and sweeps from the app (an ADR change), or is "show me the exact command" right for now?
3. **Who writes a hypothesis file:** do you want to draft one in the app (a form that produces the file for review), or keep it agent-drafted and reviewed in a PR?
4. **The pipeline:** do the stages in section 1 match how you think of an idea's progress, or would you name or group them differently?
