# Charter: TradePartner

**Status:** ACCEPTED 2026-09-24 (issue #7). Changes happen only through an ADR. Items marked ⬜ are owner facts that gate a later phase, as noted.
**Source:** [initial research handoff](research/2026-09-24-initial-research-handoff.md), §2 and §12.

## Purpose
A personal, local system for researching, testing and paper-trading (then small-live) US equity strategies, with every decision logged. The main aim is to learn how to make disciplined, evidence-based decisions. Beating the market is a separate, harder, optional goal.

## Objective & success criteria ([ADR 0005](decisions/0005-objective-benchmark-stop-criteria.md))
- **Primary objective, ranked:** (1) disciplined, evidence-based decisions, shown by process metrics; (2) calibration, once anything emits probabilities; (3) beating a benchmark, optional and never a phase-exit criterion.
- **Benchmarks:** SPY and MTUM, total return, computed inside the system with the same data and cost model. Measured against, never targeted.
- **Success looks like:** the phase exit criteria in [roadmap.md](roadmap.md), plus at Phase 4 exit: the machine-readiness shakedown passes ([ADR 0017](decisions/0017-fast-paper-and-machine-readiness-gate.md), 2026-10-09; before it, "paper tracks backtest within the configured tolerance", which is now reported per paper book for as long as it runs and required for a strategy to go live, not for the phase to exit); every order has a complete journal chain; every override has a reason; every backtest run is in the trial registry.
- **Stop / failure criteria:** no phase exit within 6 months of the last tag forces an explicit continue-or-stop decision at the retro; exceeding the budget halts paid ingestion; any reported result found to have look-ahead, or any untracked run, halts the phase. Live capital is never topped up without amending this charter.

## Scope
- **In:** US equities, long-only, low frequency (daily or slower), no leverage, no options trading. The owner may revisit this through an ADR.
- **Direction** ([ADR 0015](decisions/0015-expansion-seams.md) part A): the narrowness above is the current state, not the goal — the long-term direction also includes swing trading and day trading of US equities, and possibly options, futures and long/short spreads, each entering scope only through its own later ADR.
- **Out:** the utility sector (owner compliance decision; the entire SIC 4900–4999 division, a guarded setting changeable only by amending this charter). Anything using material non-public information.
- **Deferred beyond the MVP** (ADR required to enter scope): social and Google Trends data, news-text signals, and the LLM analyst layer. See [roadmap.md](roadmap.md), "MVP scope".
- **Interface:** the owner interacts through a CLI and a local, read-only dashboard that reads the system's own database. The only write action is a logged override with a reason. See [roadmap.md](roadmap.md), "User experience".
- **Universe** ([ADR 0006](decisions/0006-universe-and-cadence.md)): US common stocks on NYSE, Nasdaq and NYSE American; ranked by market cap with liquidity and price floors, rebuilt point-in-time at each rebalance. The numbers (proposed defaults: top 1000, $5M median dollar volume, $5 price) are config, frozen per hypothesis at pre-registration.
- **Cadence** ([ADR 0012](decisions/0012-cadence-as-a-hypothesis-parameter.md), superseding ADR 0006's cadence): a frozen hypothesis parameter, `month_end` by default (the last session of the month, orders for the next session at the frozen fill convention, one-period hold); paper trading accepts `month_end`, `week_end` and `daily`, several books at once ([ADR 0017](decisions/0017-fast-paper-and-machine-readiness-gate.md), 2026-10-09; before it, "the MVP's paper and live cadence is monthly"); the live cadence is the Phase 6 ADR's.

## Constraints
- Runs locally, for personal use only.
- Paper trading first. Live capital is about $100 at most until the charter is amended. ✅ Account type, *decided 2026-10-04 ([#813](https://github.com/josejuarez96/tradepartner/issues/813#issuecomment-5982649128))*: a **taxable cash account**; falling back to a taxable margin account with margin never used only if the live API needs margin, in which case the Phase 6 live ADR must keep [ADR 0010](decisions/0010-phase-4-risk-rules.md) point 1 (buys sized within `account().cash`, never `buying_power`) and its item 6 reserve of our own open buys. That ADR also records the two broker facts this choice rests on (whether the live API needs margin; whether `account().cash` on a cash account includes unsettled sell proceeds) and the account actually opened. Retirement account rejected (#813).
- ⬜ **Compliance:** employer personal-trading policy reviewed. Required before Phase 6; blocks nothing earlier. *Procedure decided 2026-10-04 ([#813](https://github.com/josejuarez96/tradepartner/issues/813#issuecomment-5982649128), option (a)):* the owner obtains the policy, records on #813 what it requires (pre-clearance, restricted list, holding period, duplicate statements), and ticks this line with the date, before plan task T75b.
- **Budget:** set by [ADR 0009](decisions/0009-price-vendor.md) at $0/month for spend **by the running system** (data, APIs, LLM calls; development tooling excluded) from Phase 3 until a later ADR changes it (data-vendor spend had been deferred to the start of Phase 3 per [ADR 0003](decisions/0003-data-adapters-local-first.md)). Research labeling spend by jobs the owner runs by hand is capped by `research.spend_ceiling_usd_month` and `research.spend_ceiling_usd_total` under [ADR 0013](decisions/0013-research-measurement-boundary.md) (both `0.0` in code, set only in the owner's `.env`; $40 in all, owner decision of 2026-10-06); the running system's $0 is unchanged.
- **Time:** no fixed weekly hours. Build and review hours are recorded in each phase retro, and the 6-month stop criterion above applies. The owner reviews and merges every PR (~≤400 lines each).

## Principles (from the research)
1. Any edge comes from strategy, data or discipline. It does not come from using an LLM.
2. Hypothesis before data. Count every trial. Keep a locked holdout.
3. Code computes numbers. LLMs never place orders.
4. Point-in-time data, including delisted names. *Amended 2026-09-26 by [ADR 0009](decisions/0009-price-vendor.md): until a later ADR names a paid vendor, delisted names from 2016-01-04 as served by Alpaca SIP daily bars and stamped by Form 25 filings, observed on a sample of eight (2019 to 2023), with the survivorship gap measured per rebalance as a lower bound and gated on holdout runs.*
5. Rules are set before money is at risk. Overrides are logged.
6. Around 100 trades is mostly noise. Tune on process (calibration, costs, bugs), not on P&L.
