# Roadmap

**Status:** Proposed. Phases 2+ will be revised once the charter is decided.
Each phase ends with its exit criteria met, a retro, and a `v0.<N>.0` tag.

| Phase | Goal | Exit criteria (draft) |
|---|---|---|
| **0: Foundations** | Repo, tooling, and ways of working | This PR merged. Charter draft exists |
| **1: Charter & decisions** | Close the open decisions that block building (handoff §12) | Charter accepted. ADRs for objective/benchmark, universe, cadence, data vendor, storage, backtest engine, and LLM role (even if the answer is "none yet"). Remediation research G1–G8 either done or explicitly dropped |
| **2: Data foundation** | Point-in-time local store + ingestion for the chosen sources + market calendar | Every fact has `known_at`. Delisted names are covered, or the gap is documented. `data-validator` checks pass. A daily job runs unattended |
| **3: Backtest & trial registry** | Backtester with realistic costs, a locked holdout, and a trial registry in the database | The first registered hypothesis is tested end-to-end. Deflated Sharpe is reported. `quant-auditor` passes |
| **4: Paper trading & journal** | Scheduled paper trading with risk rules, kill switch, reconciliation, and a full decision journal | N weeks of unattended paper runs. The journal captures signal → decision → order → fill → outcome |
| **5: LLM analyst layer** *(only if an ADR says so)* | LLM memo on a fixed input packet, with the authority the ADR defines | Post-cutoff-only evaluation plan. Calibration tracked. `safety-reviewer` passes |
| **6: Small live** | ~$100 live account under the same rules | Pre-set stop criteria written down. Compliance check (employer policy) done |
