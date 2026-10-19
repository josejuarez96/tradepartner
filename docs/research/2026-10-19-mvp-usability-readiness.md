# Research Report: MVP usability readiness, the owner's workflow and the delivery timeline

**Brief:** #1914 (owner-approved brief, 2026-10-19, with orchestrator corrections)  ·  **Date:** 2026-10-19  ·  **Status:** COMPLETE  ·  **Agent/model:** team mvpreport, claude-opus-5-5  ·  **Repo at:** `7f0d3aa2` (origin/main)

**Question.** What can the owner use now? What still stops him using it comfortably on his own? When can he realistically expect the intended web interface? What should the team prioritise to get there? The bar is "usable", not "feature-complete".

**Not acceptance criteria.** A profitable strategy is not one: a reproducible, correctly evaluated rejection is a working Research Lab outcome. [ADR 0024](../decisions/0024-lift-long-only-and-top-1000.md)'s wider scope (shorts, intraday, wider universe) is not MVP scope.

**Method.** This report reconciles existing records and adds nothing new. I read the decisions, plans, specs, runbooks and code on `origin/main`, the branch `origin/spike/design-ui`, and the issues and PRs. Store facts come from the evidence folder the orchestrator gathered, cited as **(orch. query, 2026-10-19)**. That folder was a read-only query of the owner's store at `9d776b2d`, schema 33. Anything else that would need the store is marked NOT VERIFIED. I ran nothing against the store, the broker or the holdout.

**Status labels used below:**
- **IMPL**: implemented.
- **TEST**: tested on fixtures.
- **STORE**: store-validated, meaning an owner-run read or run with recorded rows.
- **OPS**: validated in operation, meaning the scheduler on the owner's Mac did it on a real session.
- **BLOCK**: blocked.
- **DEF**: deferred.

## Answer
**Verdict:** MIXED  ·  **Confidence:** medium (current state); low (dates)

**What works now.** The owner can run the data, backtest and sweep loop today from the CLI and the Streamlit dashboard without an agent, and much of it has run on his store. The paper machine runs unattended every session, and it has already caught four faults correctly (three kill-switch halts and one stale-data refusal).

**What does not work yet.**
- Paper has placed **zero orders and recorded zero fills on any book, ever** (orch. query, 2026-10-19). The order path has never run against the broker.
- The machine-readiness gate ([ADR 0017](../decisions/0017-fast-paper-and-machine-readiness-gate.md) part E) has not started. No `shakedown_span` row exists, and the gate waits on owner decisions.
- Experiments that are not plain backtests still need an agent-written script.
- Faults past the first halt still need an agent to diagnose them.

**The web app.** It does not exist as code beyond the owner's front-end prototype. Its ADR is still *Proposed*, and every gate that ADR sets before the build is open.

**Earliest dates:**

| Milestone | Earliest date | Planning range |
|---|---|---|
| A: backend operational MVP | after the close of **Tue 2026-11-03** (span row on 10-20 after the first rebalance, T161 ticked); Mon 11-02 only if the row is written tonight, 10-19 | 2026-11-03 to 2026-12-01 |
| B: owner-facing app MVP | no point date: not before the third owner review sitting (about **2026-11-06** if weekly from this week), plus an agent build span this report cannot bind to sessions | December 2026 |
| C: research-to-paper lifecycle | **2026-10-20** for the daily book (first fills); **2026-12-01** for a new candidate (B10) | daily book 10-20 to 11-03; B10 2026-12-01 to 2027-01-04 |

Every date is bound to trading sessions and owner decisions, not to agent build speed.

---

## A. Current state (verified 2026-10-19)

### A1. What merged today and what is in flight
These were checked on `origin/main` against the plan checkboxes.

**Merged:**
- T210 and T210b (cost terms and the measured cost level: [backtest plan](../plans/backtest.md):84-85, PRs #1822 and #1870), plus T210c (:86, #1858).
- T217, T217b and T217c (:98-100, PRs #1827, #1874 and #1857), and T209b (:83, #1840).
- T211's beta-adjusted alpha (:87, PR #1796).
- The FRED DTB3 fix and backfill (#1887).
- ADR 0024 (#1865), the short-build plan (#1873, #1878), and the amendments #1905 (T229) and #1899 (T211b-T211e).
- T226 ([paper plan](../plans/paper-trading.md):158, #1888) and T228 (:160, #1881, schema 34).

**Not yet on the owner's store:** schema 34. The owner's store is at schema 33 (orch. query, 2026-10-19).

**In flight, as drafts:** T231 (#1900), T232 (#1907) and T211b (#1912).

**T218 ran but is not closed.** It was registered and run today (registration 1, run 1, `ok`). Its kill rule fired: net spread −6.82 pp a year, CI −24.9 to +11.3 (orch. query, 2026-10-19; #1876). Its plan line is still unticked ([backtest plan](../plans/backtest.md):101), and its report `docs/research/2026-10-20-long-short-spike.md` does not exist yet.

### A2. Capability classification

**Data**

| Capability | Status | Evidence |
|---|---|---|
| Daily ingest (Alpaca, EDGAR, FRED) on launchd at 18:30 | OPS | `ingestion_runs`: 1,430 alpaca, 39 edgar, 6 fred (orch. query, 2026-10-19); [scheduling runbook](../runbooks/scheduling.md):104-183 |
| Data-health report and integrity check | OPS | `tradepartner health --check`; Streamlit Data health page (`src/tradepartner/dashboard/health_page.py`) |

**Research**

| Capability | Status | Evidence |
|---|---|---|
| Hypothesis registry, backtest, holdout refusal, survivorship-gap gate | OPS | 17 trials, holdout trial 4 spent by decision 1 (orch. query, 2026-10-19) |
| Sweeps (register, run, report, promote or retire) | OPS | sweep 1, trials 11-16, retired by decision 5 (orch. query, 2026-10-19) |
| Research registry (`experiment` and `dataset`) | STORE (once) | T218: registration 1, run 1 (orch. query, 2026-10-19) |
| Kill ladder rungs 4-5 and placebo, cost terms, measured-cost flag, beta-alpha | TEST | backtest plan :84-100; no store trial uses them yet |
| Measured cost from real fills (`--measured-cost-bps` input, rung 1) | BLOCK | there are no fills to measure |
| Signed engine, short costs and borrow (T219-T224), paper shorts (T225-T235) | DEF for MVP (approved, in build) | ADR 0024; paper plan :157-167 |

**Paper trading**

| Capability | Status | Evidence |
|---|---|---|
| Scheduled paper run, reconciliation and marks (two books) | OPS | runs 5-18; reconciliations 1-19 (orch. query, 2026-10-19) |
| Order submit and fill collection on Alpaca paper | TEST only, never run on the owner's account | `orders` = 0 and `fills` = 0 (orch. query, 2026-10-19) |
| Fault halts (reconciliation, stale data, broker error) and kill-switch resume | OPS | `kill_switch` events 1-4; alerts 1-8 (orch. query, 2026-10-19); the fixes are listed in A3 below |
| Shakedown gate (`decision shakedown-span`, `paper shakedown`) | IMPL + TEST, BLOCK on the owner | no `shakedown_span` row in `owner_decisions` 1-15 (orch. query, 2026-10-19) |

**Alerts**

| Capability | Status | Evidence |
|---|---|---|
| macOS alerts | OPS (on the Mac only) | `alert_deliveries` channel `macos`, ok (orch. query, 2026-10-19) |
| Email alerts | IMPL, not configured | `src/tradepartner/execution/alerts.py`:233-335; default channels `("store","macos")`, `src/tradepartner/config.py`:1912 |
| Push or phone alerts | not built | none in `alerts.py` |

**Interface**

| Capability | Status | Evidence |
|---|---|---|
| Streamlit dashboard (Health, Backtest, Trials, Research, Operations, Override) | IMPL + TEST, used | `src/tradepartner/dashboard/app.py`:192; `tests/dashboard/` |
| Web app (ADR 0018) | not built (prototype only) | section C |
| Live account (Phase 6) | BLOCK on the shakedown (T162 → T163) | paper plan :141-142 |

### A3. The paper record in one paragraph
The record below is from the orch. query, 2026-10-19.

**Windows.** Windows were restarted four times:
- 1 and 2 were abandoned for the T181 data release on 10-13.
- 3 and 4 were abandoned by the owner on 10-18 to freeze `paper.max_stale_mark_sessions`.

**Runs.**
- No run is recorded for 10-15 (the `missed_run` alerts 6 and 8 both name 10-15). No window was open for 10-14's run: windows 1 and 2 were abandoned on 10-13, and windows 3 and 4 opened at 20:50 UTC on 10-14. Why 10-15 was missed is not recorded; the Mac sleeping is an inference (#1759).
- Every run since is a `mark`, a `no_session`, or one of the fault runs below (three halts, two stale refusals).
- The daily book (window 5) shows its next rebalance as **Tue 2026-10-20**, run at 08:05 ET (`paper status --all`).
- `main` (window 6, H1) first rebalances on 2026-10-30, which puts its first fill session on Mon 2026-11-02.

**Faults and their fixes.** Each fault the machine caught was fixed by agent PRs, not by the owner alone:
- The 10-12 cash mismatch: #1584 and #1596.
- The 10-13 unexplained activity: #1595 and #1640.
- The 10-16 Alpaca 404 on `GEF-B`: #1729, #1730 and #1784.
- The 10-16 stale data: #1771 and #1769.

---

## B. Owner workflow walkthrough

"Owner intervention" means a step only the owner may take, or one he must take because no automation exists. "Usable today?" means usable without an agent at his elbow.

### Stage 1: Health check
- **Commands and pages:**
  - `uv run tradepartner health --check`
  - `uv run tradepartner paper status --all`
  - the Streamlit **Data health** page (`uv run tradepartner dashboard`, `src/tradepartner/cli.py`:2437)
- **Tests:** `tests/dashboard/test_health_page.py`
- **Owner intervention:** read the output. Wake the Mac for the 18:30 ingest and the 08:05 run (open issue #1759).
- **Doc gaps:**
  - No single "today" summary across ingest, paper runs and reconciliation (the parity table in C3).
  - The runbook's wake section covers the ingest only ([scheduling](../runbooks/scheduling.md):199; #1759).
- **Usable today?** **Yes.**

### Stage 2: Hypothesis
- **Commands and pages:**
  - a hypothesis file drafted in a PR (class B)
  - `tradepartner hypothesis register <file>`, or `sweep register <file>`
  - the Streamlit **Research** page, which shows the research registry only
- **Tests:** the registry tests; the `backtest-runner` agent can check a file on a fixture store (`.claude/agents/backtest-runner.md`:8-47)
- **Owner intervention:** merging the file is the owner's word. Agents draft the files by design ([strategy-lab spec](../specs/strategy-lab.md):17).
- **Doc gaps:** no owner-facing "write and register a hypothesis" runbook. `docs/runbooks/` holds only `benchmark-backfill.md`, `data-releases.md` and `scheduling.md`.
- **Usable today?** **Yes, with an agent drafting** (as designed).

### Stage 3: Authorised backtest or experiment
- **Commands and pages:**
  - `tradepartner backtest <slug>`, with the holdout only through `--spend-holdout --holdout-reason`
  - `sweep run`
  - `experiment register` → `dataset register` → `experiment open` → a bespoke script → `experiments`, which is how T218 ran (#1876 owner-commands comment)
- **Tests:** backtest, sweep and registry suites; T218 was rehearsed on a fixture store (#1876)
- **Owner intervention:**
  - The owner runs every real trial.
  - The momentum holdout is already spent (trial 4).
  - No development-boundary row exists (decisions 1-15), so ADR 0016's window rule applies.
- **Doc gaps:**
  - There is **no `experiment run` command**. Each experiment's analysis is its own spec ([research-registry spec](../specs/research-registry.md):109), and T218's script lives outside the repo on a never-merged spike ([backtest plan](../plans/backtest.md):101).
  - No runbook for backtest, sweep or experiment.
- **Usable today?**
  - **Backtest and sweep: yes.**
  - **Experiment: no**, it needs an agent's script.

### Stage 4: Evaluate
- **Commands and pages:**
  - `tradepartner trials`, `sweep report`, `sweep promote|retire`
  - `decision gap-signoff`, `lab status`, `experiments`
  - the Streamlit **Backtest** and **Trial registry** pages
- **Tests:** `tests/dashboard/test_backtest_page.py` and `tests/dashboard/test_trials_page.py`
- **Owner intervention:**
  - An experiment's kill rule is read by hand from its JSON. The verdict is `n/a` ([long-short-spike](../experiments/long-short-spike.md):21).
  - Kill-ladder rungs 1-3 are counted in a manual ledger ([hypothesis backlog](hypothesis-backlog.md):155-199).
- **Doc gaps:**
  - No "read a result" guide.
  - Unfinished trials 5-10 have no closing command.
- **Usable today?**
  - **Backtests and sweeps: yes.**
  - **Experiments: partly.**

### Stage 5: Monitor paper
- **Commands and pages:**
  - `paper status --all`, `paper check --book <t>`, `paper report --book <t>`
  - the Streamlit **Operations** page (`src/tradepartner/dashboard/ops_page.py`; spec req 12)
- **Tests:** `tests/dashboard/test_ops_page.py`
- **Owner intervention:**
  - Read alerts on the Mac. Only `store` and `macos` are configured (config.py:1912).
  - Keep the Mac awake (#1759).
- **Doc gaps:**
  - The alert table ([scheduling](../runbooks/scheduling.md):416-435) lacks the code's `stale_mark`, `skipped`, `lot_ledger` and `pending_unresolved` kinds (`alerts.py`:109-127).
  - E.6's note lists `email`, which is not configured (scheduling :534).
- **Usable today?**
  - **On the Mac: yes.**
  - **Away from it: no.**
  - **With fills: never exercised.**

### Stage 6: Resolve faults
- **Commands and pages:**
  - `paper kill --book <t>|--all --reason`
  - `paper resume --book <t> --reason [--accept-broker-fills|--accept-rejections]`
  - `paper settle`
  - `decision shakedown-note`
  - ([scheduling](../runbooks/scheduling.md):437-495)
  - the Streamlit **Override** page, the only write
- **Tests:** T201 fault drills on the fake broker (#1794); halts in operation (A3)
- **Owner intervention:**
  - The owner resumes.
  - Diagnosing the four real faults so far (three halts, one stale-data refusal) took agent PRs (A3).
- **Doc gaps:**
  - No entry for a broker 404 on a delisted name (#1480).
  - No "waiting on me" list.
  - Kill and resume are CLI only.
- **Usable today?** **Partly.** Mechanical resume yes; diagnosis no.

**Summary.** On the Mac, the owner can use the CLI and Streamlit loop today for health, backtests, sweeps, evaluation and monitoring. Three things stop comfortable independent use:
1. The order path has never run.
2. Fault diagnosis and experiment scripts still need an agent.
3. Alerts do not reach him off the Mac.

---

## C. UI workstream reconciliation

### C1. What is decided
[ADR 0018](../decisions/0018-end-user-web-app.md) is the decision on the web app.

**Status (:3):** "**Proposed** (merged by the owner, PR #1411, 2026-10-10; amended the same day by #1412)".

**It decides:**
- **Architecture (:28):** one local, read-only Python process. It serves a FastAPI read API on loopback (§1, :30-57) under `src/tradepartner/webapp/`, launched by `tradepartner web`.
- **Front end (§3, :72-79):** React + TypeScript + Vite in `webapp/`, built with pnpm.
- **Writes (§2, :59):** kill and resume, run as subprocesses of `paper kill` and `paper resume`.
- **Streamlit (§5, :92-99):** each page retires only after its replacement reaches parity.

**The 10-10 amendment (#1413, :142-231) adds:**
- reading the lab in the app;
- a Run button and registering from the app, which brings the writes to seven;
- version 1 on loopback only, with phone access left undecided.

**Gates it sets before the build:**
- **Before any webapp task is claimed:** a throwaway `spike/webapp-probe` must report on #1410 (§7, :110-116).
- **Before any webapp task is claimed:** the charter, roadmap and design-standard amendments must merge (:123).
- **Next:** `docs/specs/webapp.md` and `docs/plans/webapp.md` (:140).

**None of these exist.**
- There is no probe branch, no spec and no plan.
- [charter](../charter.md):20 still says "read-only dashboard … the only write action is a logged override".
- [roadmap](../roadmap.md):51-55 still says "No separate API".
- The [design standard](../design/dashboard.md):84-90 still records ADR 0011's Streamlit decision and has no phone-width rule.
- `pyproject.toml` has `streamlit` but no fastapi or uvicorn.

### C2. What the owner built on `origin/spike/design-ui`
- **History:** last commit `06194031`, 2026-10-10, by the owner. The branch is 35 commits ahead of main and 228 behind (136 files, +136k lines).
- **It is a real front end with mock data, not only design docs:**
  - **Location and tooling:** `web/` (not the ADR's `webapp/`), built with npm (not pnpm).
  - **Stack:** React 19, Vite 7, TypeScript 5.9, Tailwind 4, shadcn/Radix and lightweight-charts.
  - **Screens:** Today, Strategies, Strategy, StrategyActions, Book, TrialBench, Trial and OverviewStates (`web/src/screens/`).
  - **Data:** typed sample JSON (`web/sample/*.json`, `web/src/lib/types.ts`).
- **What it lacks:** API code, Python and tests. Every write is a `PrototypeWrite` mock.
- **Design docs:**
  - `docs/research/ui/HANDOFF.md` sets out four routines:
    1. a morning check on the phone;
    2. reading a trial at the desk;
    3. deciding;
    4. a weekly review.
  - It also records the owner's review rules (:26-34), a source map of engine readers still missing (:57-72), and open questions 1-5 (:117-122).
  - `interaction.md` §8 (:190-195) records the 10-10 decisions: register in the app, an N→N+1 SR* confirm on Run, agent drafts as PRs, and reading the lab now.

**Background (not re-derived):**
- The owner rejected an agent-made canvas as developer-centric (10-09).
- His 10-12 "store first" direction held UI work.
- ADR 0023 (break-first, 10-18) then set the working direction.

No repo document records the hold. "Store first" appears in no file under `docs/`.

### C3. Streamlit as the interim: parity with the owner's journeys

| Journey | Streamlit today | Gap to the HANDOFF routines |
|---|---|---|
| Health | Data health page | No cross-system "Today" line; not phone-shaped |
| Hypothesis | Research page (registry only) | No register action (CLI) |
| Backtest | Backtest page, read-only ("runs are CLI only") | No Run; no N+1 / SR* confirm (needs a size-S engine function, interaction.md §8 Q2) |
| Evaluate | Backtest + Trial registry (DSR, holdout spends, decisions) | No verdict layout; no sweep report page |
| Monitor paper | Operations page (KPIs, fills, chains, alerts, reconciliation) | No per-period tracking vs the backtest (`paper_report_periods` not built) |
| Resolve faults | Operations alerts + Override (write) | Kill and resume are CLI only; no "waiting on me" list |

### C4. Reusable, and the minimum first version
**Reusable:**
- **From the branch:** the screens, components, tokens and `types.ts`, plus the HANDOFF source map as the API's resource list.
- **From main:** the pure readers the Streamlit pages already use, namely:
  - `execution.ops.page_data` and `book_summaries` (`src/tradepartner/execution/ops.py`:778, :947);
  - `health.health_report`;
  - the `load_*_view` functions in the backtest and trials pages;
  - `dashboard/header.py`'s freshness reads.

**Minimum first version** (ADR 0018 :231, open question A3 order):
1. A read API for `/health`, `/books`, `/books/{book}` and `/alerts`, with the Today, Books and Operations screens.
2. Kill and resume as subprocess endpoints.
3. The lab reading screens.
4. Run and Register, which come after version 1.

**Engine readers named by the ADR that are absent from `src/`:** `paper_report_periods`, the "waiting on me" and "alerts since" readers, `trial_detail`, `latest_registrations` and `next_trial_cost`.

**Decisions the owner must take first:**
- Reconcile `web/` + npm with the ADR's `webapp/` + pnpm: change one or the other.
- ADR 0018's *Proposed* status. Its acceptance is class B and his call; this report only recommends.

---

## D. Critical path

**Classes:**
- **MVP**: an MVP blocker.
- **OPS**: an operational-safety blocker.
- **RES**: a research blocker.
- **UI**: a UI-MVP blocker.
- **PAR**: approved parallel work.
- **POST**: post-MVP.
- **DEF**: deferred.

Every row maps to an existing task or issue unless it is marked *proposed*.

### Paper and operations (Milestone A)

| Item | Class | Task / issue | Who |
|---|---|---|---|
| Daily book's first-ever rebalance, Tue 10-20 08:05 ET, places and fills orders | MVP | T161 ticks on it (paper plan :140) | scheduler; owner ticks |
| Decide: hold the parked pre-shakedown drill week, or skip it | MVP | parked 2026-10-09 (orchestrator log #836, comment 2026-10-10); in no plan | owner |
| Decide the drill host: daily book (#1416 allows it) or `main` | MVP | T162 (paper plan :141) | owner |
| Open the span: `decision shakedown-span --sessions 10 --order-sessions 5 --reason …` | MVP | T162 | owner |
| `paper shakedown` exit 0 over every book | MVP | T162 (ADR 0017 :77-89) | owner |
| One Mac wake covering 18:30 and 08:05, or no sleep on power | OPS | #1759, T161 | owner + docs |
| Runbook entry and code path for a broker 404 on a delisted name | OPS | #1480 | agent |
| Drill alert kind; small-account non-fractionable skip | OPS | #1848 | agent |
| Alert table missing four kinds; E.6 lists unconfigured email | OPS | *proposed docs issue* (no task covers scheduling.md :416-435 vs alerts.py :109-127) | agent |
| Off-Mac alerts (configure email: `.env.example`:32-38), **before the span opens or after it passes**: E.6 needs an `ok` delivery in the span on every non-store channel (ADR 0017 :88) | OPS (comfort) | none needed; owner config | owner |
| Pull origin/main and migrate the store to schema 34 (#1881) | OPS | T228 follow-through | owner |

### Research Lab (Milestone C)

| Item | Class | Task / issue | Who |
|---|---|---|---|
| T218 report and tick | RES (closes a loop) | T218 (backtest plan :101), #1872 | agent |
| T198 share-class sitting (target 10-24..27), then T214 | RES | data-foundation plan :292, :295 | owner |
| B10 sweep at the first sitting after ten daily-book fill sessions | RES | T165e (backtest plan :79, :128) | owner (agent drafts the file) |
| Owner runbook: run a backtest, sweep or experiment and read its verdict | RES (independence) | *proposed docs issue*: T115 (strategy-lab plan :69) covered agent and scheduling docs only | agent |
| A merged, reusable experiment runner (vs per-experiment spike scripts) | POST | by design per research-registry spec :109; revisit only if experiments become routine | n/a |
| Close unfinished trials 5-10 | POST | none; cosmetic (backtest spec :97 lists them as `unfinished`) | n/a |
| Risk-free default flip (`risk_free_source` still `"constant"`, config.py:1679) | RES (minor) | T170d (data-foundation plan :230) | owner |

### Web app (Milestone B)

| Item | Class | Task / issue | Who |
|---|---|---|---|
| ADR 0018 status decision | UI | ADR 0018 :3; class B | owner |
| Charter, roadmap and design-standard amendments | UI | ADR 0018 :123 | agent drafts, owner merges |
| `spike/webapp-probe`, its report on #1410 | UI | ADR 0018 §7 | agent |
| `docs/specs/webapp.md` + `docs/plans/webapp.md` | UI | ADR 0018 :140 | agent drafts, owner merges |
| Missing readers (`paper_report_periods`, waiting-on-me, …) | UI | HANDOFF :57-72; to be plan tasks | agent |

### Approved parallel, post-MVP and deferred

| Item | Class | Task / issue | Who |
|---|---|---|---|
| Short build: T211b-T211e, T219-T224, T225-T235 | PAR, POST for MVP | ADR 0024; backtest plan :89-107; paper plan :157-167 | agents |
| T163 live-book ADR, then the cash account | POST (Phase 6) | paper plan :142; gate on T162 | owner |
| T204 concurrent runs; T23; intraday; options (ADR 0024 :27 keeps options out) | DEF | paper plan; ADR 0024 | n/a |

**The critical path to Milestone A is calendar-bound, not build-bound.** It runs:
1. the 10-20 rebalance works;
2. the owner decides on the drill week and the drill host;
3. the owner writes the span row;
4. ten clean sessions pass;
5. `paper shakedown` exits 0.

Agent work on the path is limited to the OPS rows, all of them small. **Candidates for owner reprioritisation, not cancellation:** the short build's agent capacity (PAR) is the largest pool. It could pause, or run at lower priority, while the OPS and UI documents are written.

---

## E. Forecasts

### Session arithmetic
**The rule.** The span runs from the XNYS session after the span row's New York date (ADR 0017 :79; `src/tradepartner/execution/shakedown.py`:370).

**The calendar.** XNYS sessions from 10-20, computed with `exchange_calendars`:
- 10-20 … 10-23
- 10-26 … 10-30
- 11-02 … 11-06
- 11-09 …
- 11-27 is a half day (11-26 is closed)
- 11-30, 12-01

**The scenarios:**

| Span row written | Drill | 10th session = earliest pass | Note |
|---|---|---|---|
| Mon 10-19 (NY date) | daily book, any span session | **Mon 11-02** | M = 5 fills met by 10-26 if the daily book fills every session |
| Tue 10-20 | daily book | Tue 11-03 | |
| after a 5-session drill week, about Mon 10-26 | daily book | Mon 11-09 | |
| any date, drill on `main` but not on H1's first fill session | `main` on its next fill session, Tue 12-01 (signal at the 11-30 month-end) | Tue 12-01 | |

**A conflict in T162.** Its 2026-10-18 amendment schedules the drill on `main` on 2026-11-02 (paper plan :141). The same line forbids the drill on H1's first fill session, "which is T71's evidence run and must trade". Every `main` window (1, 3, 6) has had `first_rebalance_session` 2026-10-30, so 11-02 *is* H1's first fill session. The owner has to choose.

### Milestone A: backend operational MVP
Definition: Phase 4's exit, `paper shakedown` exit 0 over every book ([roadmap](../roadmap.md):12), with a runbook entry for each fault class seen so far.

- **Current readiness:**
  - The code is complete: T200, T201 and T202 have merged, which T162's amendment required before the span row.
  - The operation is not: there are zero orders and the span is not open.
- **Critical path:** the 10-20 rebalance, then the owner's drill decisions and the span row, then ten sessions.
- **Already merged:** everything T162 depends on except T161's tick.
- **Owner-run dependencies:**
  - the drill-week decision;
  - the drill host;
  - the span row;
  - the drill itself (`paper kill` before a run, then `paper resume` the same day);
  - a `shakedown_note` for any stale or missed run;
  - the wake.
- **Elapsed-session dependency:** 10 sessions, with fills on 5.
- **In parallel:** the OPS docs rows; the T218 report.
- **Earliest date:** after the close of **Tue 2026-11-03**, following the report's own order: watch the 10-20 rebalance, tick T161 (T162 depends on it, paper plan :141), write the span row on 10-20, and the span runs 10-21 to 11-03. The aggressive case is Mon 11-02, which needs the row written tonight (10-19 New York date), before any order has ever been placed, and T161 ticked first.
- **Planning range:** **2026-11-03 to 2026-12-01**.
- **Risks:**
  - The first-ever orders on 10-20 may surface a new fault. A mismatch or halt restarts the span, and each restart costs 10 or more sessions (two weeks).
  - Missed runs: 10-15 had none, cause unrecorded (#1759 suspects sleep).
  - The drill-host conflict.
  - Schema 34 is not yet on the store.
- **Confidence:** low for the earliest date; medium for the range.

### Milestone B: owner-facing app MVP
Definition: ADR 0018's version 1. That is the read API plus Today, Books and Operations, and kill and resume, on loopback. Streamlit remains for everything else.

- **Current readiness:**
  - A front-end prototype on mock data exists.
  - There is no API, spec, plan or probe, and the ADR is still Proposed.
- **Critical path:**
  1. The owner decides on resuming the work and on ADR 0018's status.
  2. Amendments, then the probe, then the spec and plan, each needing his review.
  3. The build: the readers, the API, then porting the screens.
  4. His screen review.
- **Already merged:** the Streamlit readers the API would wrap.
- **Owner-run dependencies:** about three or four review sittings.
  1. The ADR status and the amendments.
  2. The spec and plan.
  3. One or two screen reviews against his HANDOFF rules.
- **Elapsed-session dependency:** none. The data is better once real fills exist, which follows Milestone A.
- **In parallel:** everything in Milestone A.
- **Earliest date:** no evidence-backed point date. The basis is three owner sittings in sequence (each needs the previous one's documents merged), assumed weekly from the week of 10-19:
  1. sitting 1 about 10-23: ADR status and the amendments;
  2. sitting 2 about 10-30: the probe report, the spec and the plan;
  3. sitting 3 about 11-06: the first screen review.

  The build sits between sittings 2 and 3 and beyond: six pure readers and one journal table (ADR 0018 :121), the API, porting three screens, and kill and resume. Its length depends on agent throughput, which the brief excludes as a basis. So version 1 is **not before about 2026-11-06**, and later by the build span.
- **Planning range:** **December 2026**. Thanksgiving week and the short build compete for attention.
- **Risks:**
  - The owner's own design bar: he rejected one canvas.
  - The two toolchain mismatches (folder, package manager).
  - Phone access is undecided.
- **Confidence:** low.

### Milestone C: research-to-paper lifecycle
Definition: a hypothesis that came through the Lab has a paper window with real fills, and `paper report` / `paper check` compare it with its tracking trial.

- **Current readiness:** the path has run once, up to the window. Sweep 1 was retired; the daily book is an operations book from decision 6, window 5, with trial 17. It has never had fills.
- **Critical path, tier 1 (the daily book):** fills from 10-20, then `paper report --book daily`.
- **Critical path, tier 2 (a new candidate, B10):**
  1. T198's sitting (10-24..27).
  2. T165e at the first sitting after ten daily-book fill sessions, about 11-02 or later.
  3. Promotion, then `paper start` (month-end cadence).
  4. The first fills on Tue 12-01.
- **Already merged:** the hypothesis, sweep and promotion path (sweep 1; decision 6); `paper start`, `paper report` and `paper check` (paper spec reqs 10, 14 and 15); T217/b/c's ladder (backtest plan :98-100); T165 to T165d (B10's screen, backtest plan :128).
- **Owner-run dependencies:** T198; the T165e sweep; `paper start`.
- **In parallel:** Milestone A (the same fill sessions count for both); the T218 report; the Q1 repurchasers' rung 1-2 read (strategy discovery).
- **Elapsed-session dependencies:** 10 daily-book fill sessions; the 11-30 month-end.
- **Earliest dates:** tier 1 **Tue 2026-10-20** (first fills; `paper report --book daily` can compare from then; when `paper check` first passes depends on `paper.min_rebalances` for a `daily` book, NOT VERIFIED). Tier 2 **Tue 2026-12-01**.
- **Planning range:** tier 1 10-20 to 11-03; a fault on the first orders is fixed while the book keeps marking. Tier 2 12-01 to **2027-01-04**: if the sweep or promotion misses the November month-end, the next signal is 12-31 and the next fills are on 2027-01-04. If B10 dies, tier 2 ends at a recorded rejection.
- **Risks:**
  - B10's prior is near zero after costs ([daily data readiness](2026-10-18-daily-data-readiness.md):9-11). If its sweep kills it, the lifecycle ends correctly at a recorded rejection. That is a working Lab, but there is no new paper book.
  - The ladder ledger names only B10 and the Q1 repurchasers as live candidates ([strategy discovery](2026-10-19-strategy-discovery.md):11-15).
- **Confidence:** medium for tier 1, low for tier 2.

---

## F. Decision memo

1. **What the owner can use now.** On the Mac, from the CLI and the Streamlit dashboard:
   - data health;
   - registering hypotheses and sweeps;
   - authorised backtests and sweeps;
   - reading trials, sweep reports and DSR;
   - gap sign-offs;
   - monitoring both paper books, and kill and resume.

   Each of these has run on his store (A2).
2. **What blocks independent use.**
   - The order path has never run: zero orders and zero fills on any book.
   - Fault diagnosis still needs an agent. All four real faults (three halts, one stale-data refusal) needed PRs, and #1480's 404 case has no runbook entry.
   - Experiments other than backtests need an agent-written script, and their kill rule is read by hand.
   - There is no owner runbook for running and reading research.
   - Alerts reach only the Mac. Email is built but not configured.
3. **What must finish before the paper shakedown can pass.**
   - The daily book's first rebalance (Tue 10-20) must place and fill orders.
   - Three owner decisions:
     1. the parked drill week: hold it or skip it;
     2. the drill host: the daily book (#1416), or `main` on a fill session other than H1's first, which resolves T162's 11-02 conflict;
     3. the `decision shakedown-span --sessions 10 --order-sessions 5` row.
   - Then ten sessions with no mismatch or halt, fills on five, the drill inside the span, a note on any stale or missed alert, `health` passing, and `paper shakedown` exit 0.
   - Advisable alongside: the one-wake setting (#1759), the schema 34 pull, and #1480.
4. **Is the Lab usable for routine experiments?**
   - **Backtests and sweeps: yes.** They are operationally validated, and a rejection is recorded correctly (sweep 1, T218).
   - **Registered experiments: not yet routine.** They need a bespoke script and a hand-read verdict.
   - **What limits research is not capability or data.** It is the lack of promising hypotheses: the live queue is B10 (near-zero prior) and the Q1 repurchasers.
5. **Resume the UI now, or at which milestone?**
   - **Resume the document gate now.** That covers the ADR 0018 status decision, the charter, roadmap and design-standard amendments, the probe, the spec and the plan. It costs owner review time, not sessions. Milestone A is waiting on the calendar anyway.
   - **Start the build when the spec and plan merge.**
   - **Ship kill and resume in the app only after Milestone A passes,** so that no new write path enters a running span.
   - Streamlit stays the interim for every journey.
6. **The next two weeks' priorities, in order:**
   1. Watch the 10-20 rebalance and fix any fault at once.
   2. The owner's three shakedown decisions, then the span row.
   3. The OPS documents (#1759, #1480, #1848, the alert-table fix).
   4. The T218 report and tick.
   5. T198's sitting (10-24..27).
   6. An owner research runbook (proposed).
   7. The ADR 0018 document gate.
   8. T165e once ten daily-book fill sessions exist.
7. **Parallel or deferred.**
   - **Parallel:** the ADR 0024 short build (T211b-T211e, T219-T235) continues as approved, but it is not MVP. It is the main candidate if the owner wants agent capacity moved to items 3, 6 and 7.
   - **Deferred:** T163 (live) until the pass; T204; intraday; options, which ADR 0024 still excludes.
8. **The earliest evidence-backed windows.**

   | Milestone | Earliest | Planning range | Depends on |
   |---|---|---|---|
   | A: shakedown pass | after the close of Tue 2026-11-03 (Mon 11-02 only if the row is written tonight) | 11-03 to 12-01 | the 10-20 rebalance, T161 ticked, the span row on 10-20, a daily-book drill, no restart |
   | B: app version 1 | not before about 2026-11-06 (third weekly sitting) plus the build | December 2026 | three owner sittings in sequence, then the agent build |
   | C, tier 1: daily book | Tue 2026-10-20 | 10-20 to 11-03 | the first orders filling |
   | C, tier 2: B10 | Tue 2026-12-01 | 12-01 to 2027-01-04 | T198, T165e, B10 surviving its sweep |

---

## Evidence
| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| No orders or fills ever | orch. query, 2026-10-19 (`orders`, `fills`) | STORE | "count_star() 0" | n/a | n/a |
| No span opened | orch. query, 2026-10-19 (`owner_decisions` 1-15) | STORE | kinds: holdout_spend, data_release, gap_signoff, sweep_retired, operations_book | n/a | n/a |
| Gate thresholds | ADR 0017 :79 | DEC | "N = 10 and M = 5" | n/a | n/a |
| Drill-host conflict | paper plan :141 | DEC | "not H1's first fill session" vs "the drill on `main` on 2026-11-02" | n/a | n/a |
| ADR 0018 not accepted | ADR 0018 :3 | DEC | "**Proposed**" | n/a | n/a |
| T218 killed | #1876; orch. query | STORE | "net spread -6.82 pp/yr" | in-sample | yes |
| B10 waits on daily fills | backtest plan :128 | DEC | "at the first sitting after ten daily-book fill sessions" | n/a | n/a |

## Disconfirmation
**Searches run:**
- Looked for any `shakedown_span` row, for orders and fills, and for a merged webapp spec or plan.
- Looked for a repo record of the drill-week parking and of "store first".
- Looked for an owner runbook for research.

**What I found against my reading:**
- The parking is in a #836 comment, not in a plan. So the owner may consider the drill week already dropped, and then the earliest date stands.
- T162's 10-18 amendment suggests the owner already accepted a drill on `main` on 11-02. If so, the conflict is resolved in favour of 11-02 and Milestone A's dates stand.

## Caveats & gaps
- The dates assume the daily book trades every session. `rebalance_band` defaults to 0 (config.py:1356) and the file says "every session" ([momentum-daily-shakedown](../hypotheses/momentum-daily-shakedown.md):42, :98). They also assume no restart.
- Milestone B's date rests on owner review latency, which this report cannot measure.

## UNVERIFIED items
- `paper.min_rebalances` for a `daily` book, and when `paper check` first passes.
- That window 5's 10-19 run was a `mark` because rebalance orders go out at the next open (ADR 0023 D3). This is inferred from runs 17 and 18 and `paper status`.
- The 2,696 DTB3 rows: from the brief, not in a repo doc.

## Follow-up questions (not answered here)
- Should the two proposed docs issues be filed: the alert table, and the owner research runbook?
- Does the owner want version 1 of the app phone-reachable (amendment D, open)?

## Sources
- [ADR 0017](../decisions/0017-fast-paper-and-machine-readiness-gate.md), [ADR 0018](../decisions/0018-end-user-web-app.md), [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md), [ADR 0024](../decisions/0024-lift-long-only-and-top-1000.md).
- [Roadmap](../roadmap.md), [charter](../charter.md).
- [Paper plan](../plans/paper-trading.md), [backtest plan](../plans/backtest.md).
- [Strategy-lab spec](../specs/strategy-lab.md), [research-registry spec](../specs/research-registry.md).
- [Scheduling runbook](../runbooks/scheduling.md), [design standard](../design/dashboard.md).
- [Daily data readiness](2026-10-18-daily-data-readiness.md), [strategy discovery](2026-10-19-strategy-discovery.md), [hypothesis backlog](hypothesis-backlog.md).
- `origin/spike/design-ui` (`06194031`): `docs/research/ui/HANDOFF.md`, `docs/research/ui/interaction.md`.
- Issues and PRs: #836, #1352, #1410-#1413, #1416, #1480, #1759, #1848, #1876, #1881, #1887.
- The evidence folder: the orchestrator's read-only query, 2026-10-19.
