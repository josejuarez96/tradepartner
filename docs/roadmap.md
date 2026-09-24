# Roadmap

**Status:** Proposed. Phases 2+ will be revised once the charter is decided.
Each phase ends with its exit criteria met, a retro, and a `v0.<N>.0` tag.

| Phase | Goal | Exit criteria (draft) |
|---|---|---|
| **0: Foundations** | Repo, tooling, and ways of working | Ways of working merged. Charter draft exists |
| **1: Charter & decisions** | Close the open decisions that block building (handoff §12) | Charter accepted. ADRs for objective/benchmark, universe and cadence, data adapters ([0003](decisions/0003-data-adapters-local-first.md)), tooling ([0004](decisions/0004-tooling-adopt-avoid.md)), and LLM role (even if the answer is "none yet"). Remediation research G1–G8 either done, deferred to the phase that needs it, or explicitly dropped. **The paid price vendor and budget decisions are deferred to Phase 3** |
| **2: Data foundation** | Point-in-time local store, security master, market calendar, and the `PriceSource` / `FilingSource` / `Broker` adapters with their local defaults (fixtures, Alpaca free, EDGAR, fake broker) | Every fact has `known_at`. The no-look-ahead suite passes on the fixture universe. The survivorship gap is measured and shown. `data-validator` checks pass. A daily job runs unattended. **UX:** data-health page (coverage, gaps, last update, delisted names present) |
| **3: Backtest & trial registry** | Backtester with realistic costs, a locked holdout, and a trial registry in the database | The first registered hypothesis is tested end-to-end. Deflated Sharpe is reported. Our engine matches `bt` on the fixture universe within the configured tolerance. **Price vendor ADR merged at Phase 3 start** (it may record "no vendor", which also amends charter principle 4). The holdout is not spent on a source above the survivorship-gap threshold (ADR 0003 rule 6). `quant-auditor` passes. **UX:** backtest page (equity curve vs SPY and MTUM, drawdowns, costs) and a trial-registry view |
| **4: Paper trading & journal** | Scheduled paper trading with risk rules, kill switch, reconciliation, and a full decision journal. *Entry gate:* owner has signed off on the survivorship gap in the trial registry (ADR 0003 rule 8) | N weeks of unattended paper runs. The journal captures signal → decision → order → fill → outcome. **UX:** operations page (today's signals and reasons, positions, orders, fills, kill-switch state), the override screen, and alerts for job failure, stale data and kill switch |
| **5: LLM analyst layer** *(only if an ADR says so)* | LLM memo on a fixed input packet, with the authority the ADR defines | Post-cutoff-only evaluation plan. Calibration tracked. `safety-reviewer` passes. **UX:** memo shown next to the signal it comments on |
| **6: Small live** | ~$100 live account under the same rules | Pre-set stop criteria written down. Compliance check (employer policy) done. **UX:** review page (calibration, loss attribution, override log, paper vs backtest drift) |

## MVP scope (what Phases 2–4 build)

The MVP is a plain quant system: **long-only, monthly-rebalanced 12-1 momentum** on a liquid US universe, with a realistic cost model, tracked against SPY and MTUM buy-and-hold inside the same system. *The strategy, cadence and benchmarks are provisional until the charter's ⬜ items are accepted.* That is the first registered hypothesis (Phase 3) and the first paper strategy (Phase 4).

**Deferred beyond the MVP** (each needs an ADR to enter scope):
- Social and Google Trends data. Historical archives are not point-in-time (see the handoff, §5 and D3), so they cannot be backtested honestly. A cheap timestamped collector may be started early, as a side job, to build history for a later phase.
- News-text signals. Backtestable in principle, but they fight the speed problem. Revisit after the MVP runs.
- The LLM analyst layer (Phase 5). No evidence yet that it adds predictive value. Its first job, if any, is process discipline (e.g. a pre-mortem on owner overrides), not prediction.

## User experience

The owner is the only user, and the system runs locally. The interface is a **cross-cutting slice of every phase**, not a phase of its own: each phase exits with its page in place (the **UX** items above).

- **Two surfaces:** a CLI for jobs (`ingest`, `backtest <hypothesis>`, `paper run`), which is what the scheduler calls; and a local dashboard (Streamlit or Marimo; chosen in the Phase 2 plan per ADR 0004) that **reads the same database the system writes to**. No separate API, no duplicated logic.
- **The dashboard is read-only, with one exception:** the override screen. An override requires a reason and is logged (development-process rule 8).
- **Alerts** (job failure, stale data, kill switch) arrive in Phase 4 and matter more than charts, because the system runs unattended.
- **Design consequence for Phase 2:** the storage schema is designed to be read by a human as well as by code.
