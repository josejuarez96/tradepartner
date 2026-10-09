# Roadmap

**Status:** Accepted with the charter (2026-09-24). Phases 2+ are revised at each phase retro.
Each phase ends with its exit criteria met, a retro, and a `v0.<N>.0` tag.

| Phase | Goal | Exit criteria (draft) |
|---|---|---|
| **0: Foundations** | Repo, tooling, and ways of working | Ways of working merged. Charter draft exists |
| **1: Charter & decisions** | Close the open decisions that block building (handoff §12) | Charter accepted. ADRs for objective/benchmark ([0005](decisions/0005-objective-benchmark-stop-criteria.md)), universe and cadence ([0006](decisions/0006-universe-and-cadence.md)), data adapters ([0003](decisions/0003-data-adapters-local-first.md)), tooling ([0004](decisions/0004-tooling-adopt-avoid.md)), and LLM role (even if the answer is "none yet"). Remediation research G1–G8 either done, deferred to the phase that needs it, or explicitly dropped. **The paid price vendor and budget decisions are deferred to Phase 3** |
| **2: Data foundation** | Point-in-time local store, security master, market calendar, and the `PriceSource` / `FilingSource` / `Broker` adapters with their local defaults (fixtures, Alpaca free, EDGAR, fake broker) | Every fact has `known_at`. The no-look-ahead suite passes on the fixture universe. The survivorship gap is measured and shown. `data-validator` checks pass. A daily job runs unattended. **UX:** data-health page (coverage, gaps, last update, delisted names present) |
| **3: Backtest & trial registry** | Backtester with realistic costs, a locked holdout, and a trial registry in the database | The first registered hypothesis is tested end-to-end. Deflated Sharpe is reported. Our engine matches `bt` on the fixture universe within the configured tolerance. **Price vendor ADR merged at Phase 3 start** (it may record "no vendor", which also amends charter principle 4). The holdout is not spent on a source above the survivorship-gap threshold (ADR 0003 rule 6). `quant-auditor` passes. **UX:** backtest page (equity curve vs SPY and MTUM, drawdowns, costs) and a trial-registry view |
| **4: Paper trading & journal** | Scheduled paper trading with risk rules, kill switch, reconciliation, and a full decision journal. *Entry gate:* owner has signed off on the survivorship gap in the trial registry (ADR 0003 rule 8). Side track: the strategy lab ([spec](specs/strategy-lab.md)) runs beside the paper window on the owner's machine; it has no exit criterion of its own and is reviewed at the Phase 4 retro | *Amended by [ADR 0017](decisions/0017-fast-paper-and-machine-readiness-gate.md) (2026-10-09):* `paper shakedown` passes on the real store over every book (a scheduler-run session on every day of the shakedown span, real fills on at least the recorded number of them, zero reconciliation mismatches, no duplicate or mis-sized order, a kill-switch drill, complete journal chains, delivered alerts), not six monthly rebalances of one strategy; the tracking check (ADR 0005 criterion 1) is reported per book, not an exit criterion; several books (one Alpaca paper account each) at `month_end`, `week_end` or `daily` run at once. The journal captures signal → decision → order → fill → outcome. **UX:** operations page (today's signals and reasons, positions, orders, fills, kill-switch state), the override screen, and alerts for job failure, stale data and kill switch |
| **5: LLM analyst layer** *(only if an ADR says so)* | LLM memo on a fixed input packet, with the authority the ADR defines | Post-cutoff-only evaluation plan. Calibration tracked. `safety-reviewer` passes. **UX:** memo shown next to the signal it comments on |
| **6: Small live** | ~$100 live account under the same rules, as one more book on the live endpoint under its own ADR. *Entry gate ([ADR 0017](decisions/0017-fast-paper-and-machine-readiness-gate.md), 2026-10-09):* Phase 4's `paper shakedown` passed; the machine, not a strategy's months of tracking, is what is gated. Strategy evidence keeps accruing on paper and live in parallel | Pre-set stop criteria written down (#878). Compliance check (employer policy) done. Taxable cash account (#813). `safety-reviewer` passes on the live path. **UX:** review page (calibration, loss attribution, override log, paper vs backtest drift) |

## MVP scope (what Phases 2–4 build)

The MVP is a plain quant system: **long-only, monthly-rebalanced 12-1 momentum** on a liquid US universe, with a realistic cost model, tracked against SPY and MTUM buy-and-hold inside the same system. That is the first registered hypothesis (Phase 3) and the first paper strategy (Phase 4).

**Deferred beyond the MVP** (each needs an ADR to enter scope):
- Social and Google Trends data. Historical archives are not point-in-time (see the handoff, §5 and D3), so they cannot be backtested honestly. A cheap timestamped collector may be started early, as a side job, to build history for a later phase.
- News-text signals. Backtestable in principle, but they fight the speed problem. Revisit after the MVP runs.
- The LLM analyst layer (Phase 5). No evidence yet that it adds predictive value. Its first job, if any, is process discipline (e.g. a pre-mortem on owner overrides), not prediction.

## Calendar-bound phases and what fills them

Added 2026-09-30 (#388). Phase 4 exits after at least six monthly rebalances of unattended paper runs, so once `paper start` runs, about six calendar months pass that no build throughput can shorten; Phase 6's pre-set stop criteria work the same way. *([ADR 0017](decisions/0017-fast-paper-and-machine-readiness-gate.md), 2026-10-09, replaces the six rebalances with the machine-readiness shakedown, weeks rather than months on a daily book; the items below stay in their order.)* Windows that would otherwise idle take the items below, in this order, each through its own spec, plan or ADR PR under the plan-shape rules in [development-process.md](ways-of-working/development-process.md), and none touching Phase 4 code:

1. **The Phase 5 spec and plan** under [ADR 0008](decisions/0008-llm-role.md) (an advisory memo at most, never a number and never an order): the fixed input packet, the post-cutoff-only evaluation plan and the calibration record. Drafting can start once the Phase 4 journal schema is final, since the packet reads the journal.
2. **Phase 6 preparation** as docs tasks the owner completes: the written stop criteria, the account-type decision and the employer compliance check.
3. **The `data-validator` and `journal-analyst` agents** ([agents.md](ways-of-working/agents.md) candidates): the first is Phase 2's T23, whose agent half does not need the owner's scheduling evidence and can be split from it by a Phase 2 plan amendment; the second needs a real journal.
4. **The strategy-lab engine**, specified and its plan drafted: the [strategy-lab spec](specs/strategy-lab.md) (Accepted 2026-10-05, #931; #281 answered) and its [plan](plans/strategy-lab.md) (#933, T91 to T115) run as the Phase 4 side track above. **The event-data engine** once the owner answers #307, and the timestamped social collector side job, which needs its own ADR before it enters scope.
5. **Research labeling under [ADR 0013](decisions/0013-research-measurement-boundary.md)**: the [labeling spec](specs/research-labeling.md) and its [plan](plans/research-labeling.md), then the corpus build and the first pilot (task A, the security-master departure reason) **after T45b's real-store run has completed**, beside Phase 4 and touching no Phase 4 code. No phase's exit criteria depend on it, and it does not shorten or lengthen Phase 4's shakedown span (ADR 0017; before it, the six paper rebalances). Its spend is the charter's research labeling cap, not the running system's budget.

The deferred capabilities of [ADR 0015](decisions/0015-expansion-seams.md) part C, each entering scope only through its own later ADR when its trigger arrives, and none built speculatively (not idle-window work):

- **The journal clock** — trigger: the intraday ADR, since more than one run per session is what needs it.
- **Shorting and margin** — trigger: a registered long/short hypothesis the owner wants to run, and a research report on borrow cost and availability.
- **Price-triggered stop exits** — trigger: a swing hypothesis whose rule needs one.
- **Intraday bars** — trigger: a day- or swing-trading hypothesis at a sub-daily interval, and a data source with honest bar-close timestamps and corporate-action handling.
- **Other calendars** — trigger: the first non-XNYS instrument.
- **Options and futures data** — trigger: an options or futures hypothesis, after a research report on point-in-time coverage.
- **Roll** — trigger: the first futures hypothesis.
- **Tax logic for shorts and derivatives** — trigger: live trading on such an instrument (Phase 6 or later).
- **Multi-book aggregation** — trigger: a second paper book the owner wants on the same account, or a portfolio-level report across books.

## User experience

The owner is the only user, and the system runs locally. The interface is a **cross-cutting slice of every phase**, not a phase of its own: each phase exits with its page in place (the **UX** items above).

- **Two surfaces:** a CLI for jobs (`ingest`, `backtest <hypothesis>`, `paper run`), which is what the scheduler calls; and a local dashboard (Streamlit or Marimo; chosen in the Phase 2 plan per ADR 0004) that **reads the same database the system writes to**. No separate API, no duplicated logic.
- **The dashboard is read-only, with one exception:** the override screen. An override requires a reason and is logged (development-process rule 8).
- **Alerts** (job failure, stale data, kill switch) arrive in Phase 4 and matter more than charts, because the system runs unattended.
- **Design consequence for Phase 2:** the storage schema is designed to be read by a human as well as by code.
- **Design standard** ([design/dashboard.md](design/dashboard.md), #165): every page shares one skeleton (header with "as of" and "last updated", KPI tiles, one hero chart, supporting cards, a table), one token set in light and dark, a validated categorical palette used in fixed order, reserved status colours, one y-axis per chart, and nothing that is not a number the owner needs (no chat, no AI cards, no promotions). Applied from T21 and T44 onward; T44 retrofits T43's page. Whether Streamlit with this standard is enough, or a React front end behind a thin read-only API is warranted, is a Phase 4 ADR.
