# 0006. Universe and cadence

**Status:** Accepted  ·  **Date:** 2026-09-24  ·  **Issue:** #7

## Context

The [charter](../charter.md) left the universe (market-cap and liquidity floor) and the cadence (rebalance frequency and holding period) open. The MVP ([roadmap](../roadmap.md)) is long-only 12-1 momentum. The [handoff](../research/2026-09-24-initial-research-handoff.md) constrains both:

- Anomaly returns are concentrated in microcaps and are eaten by costs there (H3, Novy-Marx & Velikov in H1). An investable universe excludes them.
- Momentum evidence is for monthly formation with a one-month skip (H1). Monthly turnover also keeps the cost model simple and the trial count small.
- The owner excluded the utility sector (§2), a compliance decision (§10), not a tunable parameter.
- Alpaca fractional orders are DAY-only market/limit with NBBO fills (D2), so daily-or-slower cadence is the only one that fits the broker.

Every numeric threshold below is a **config value with a proposed default**, per CLAUDE.md. Two guards apply: (a) the universe config is **frozen in each hypothesis's pre-registration**; changing it after a hypothesis's first run creates a new hypothesis, never a variant (handoff §4, §10: no tuning the universe on P&L); (b) `exclude_sic_ranges` is a **guarded setting** that changes only through a charter amendment, because it is a compliance rule.

## Options considered

**Universe**
1. **S&P 500 constituents.** Pro: liquid, familiar. Con: point-in-time constituent history is paid data; index membership itself is a selection effect.
2. **All listed US equities.** Pro: no selection. Con: dominated by microcaps where the strategy is not investable and costs are unmodelable.
3. **Top-N by market cap with liquidity and price floors, rebuilt point-in-time at each rebalance** (this ADR). Pro: built from our own security master; no paid index data; excludes the uninvestable tail. Con: needs shares outstanding for market cap, which comes from EDGAR XBRL with a filing lag, and needs a security-type classification that EDGAR does not supply directly (see "Verify" below).

**Cadence**
1. **Daily rebalance.** Con: turnover and costs dominate a long-only momentum signal; more trials per year.
2. **Quarterly.** Con: weaker match to the momentum literature; slower feedback for a learning project.
3. **Monthly, one-month hold** (this ADR). Matches the evidence and the paper-trading feedback loop.

## Decision

### Universe, rebuilt as of each rebalance date T using only facts with `known_at ≤ T`

Filters apply **in this order**; the top-N cut is last, so the result has exactly N names when enough qualify.

| # | Rule | Proposed default (config key) | Point-in-time source |
|---|---|---|---|
| 1 | Security type: common stock (any listed class); exclude funds and ETFs, SPACs, ADRs and other foreign-issuer receipts, preferreds, warrants, units | `universe.security_types=[common]` | security master classification (see Verify) |
| 2 | Exchange: NYSE, Nasdaq, NYSE American | `universe.exchanges` | security master (see Verify) |
| 3 | Sector exclusion: utilities | `universe.exclude_sic_ranges=[[4900,4999]]` **guarded** | SIC from each filing's header, with the filing's `known_at` |
| 4 | Price: close at T ≥ $5 | `universe.min_price=5` | `PriceSource` |
| 5 | Liquidity: 20-session median **consolidated** dollar volume ≥ $5M | `universe.min_median_dollar_volume=5_000_000`, `universe.liquidity_window=20` | `PriceSource`; **not usable on an IEX-only feed** (blocked until ADR 0003's Alpaca check resolves) |
| 6 | History: every session in the 12 calendar months before T (per the XNYS calendar) has a bar | `universe.min_history_months=12` | `PriceSource` + calendar |
| 7 | Shares data: a shares-outstanding fact with `known_at ≤ T` and age ≤ 400 days | `universe.max_shares_age_days=400` | EDGAR XBRL |
| 8 | Size: top N companies by market cap; cap = shares outstanding **adjusted by corporate actions with `known_at ≤ T`** × close at T, summed over all classes; every listed class of a qualifying company is admitted | `universe.top_n_by_cap=1000` | EDGAR XBRL × `PriceSource` × `corporate_actions` |

Names dropped by rules 1, 6 or 7 for **missing data** (unclassifiable type, truncated price history, no or stale shares fact) are counted in the survivorship-gap report (ADR 0003 rule 5) as separate categories, so every exclusion is visible.

The owner confirmed on 2026-09-24 that the exclusion covers the **entire** SIC 4900–4999 division (electric, gas, water, sanitary services and related), with no carve-outs: nothing utilities-adjacent is in scope.

### Cadence

- Rebalance date T is the **last trading session of each calendar month** on the XNYS calendar.
- Signals and universe use closes through T (ADR 0003 rule 2). Orders are placed **for the open of session T+1** as DAY orders, and the backtester and the `bt` oracle fill at the **official opening price of T+1** (ADR 0004). If a real opening price is not available from the price source, the fallback, decided in the Phase 2 plan, is T+1 close for both backtest and paper, never a mix.
- Holding period is one month: positions are held until the next rebalance. No intra-month trades except forced exits (delisting, kill switch, risk rule).
- **Missed rebalance** (machine off, source stale, broker down): trade at the next available session, logged as a missed rebalance in the journal. The backtester does not model this; paper-vs-backtest drift from missed rebalances is reported separately.
- Position count and weighting (equal-weight top decile, etc.) belong to the strategy spec in Phase 3, not this ADR.

**Benchmarks** (ADR 0005) use the same calendar and the same T+1-open execution assumption.

### Verify before the Phase 2 plan (security master classification)

EDGAR has no point-in-time "security type" or "exchange" field. The Phase 2 plan must name a rule and source per column and report an **unclassifiable** count on the health page. Candidate sources:
- **SPACs:** SIC 6770 (blank checks).
- **Foreign issuers / ADRs:** filers of 20-F or 40-F; depositary receipts registered on Form F-6.
- **Funds and ETFs:** investment-company form types (N-CSR, N-PORT, 485BPOS, N-2). Commodity and grantor-trust ETFs file 10-Ks under ordinary SICs and need a name or price-source metadata rule.
- **Class, title and exchange, from ~2019:** cover-page iXBRL tags `dei:Security12bTitle`, `dei:TradingSymbol`, `dei:SecurityExchangeName`.
- **Before 2019:** ticker-suffix conventions plus the price source's asset metadata, as a documented fallback.
- **Preferreds, warrants, units:** share the common's CIK; classified by title and suffix rules.

Until this is verified, the claim that the universe is reproducible at any historical T holds only from the date the classification sources cover.

## Consequences

- Good: the universe is built from our own store with no paid index data; the cadence matches the evidence and the broker's order types; every number is a config key; compliance and pre-registration guards stop the universe from becoming a tuning knob.
- Bad / accepted risks: market cap from XBRL lags the true value by up to a quarter and is missing for some names (visible via the gap report). Foreign filers are excluded, narrowing the universe versus MTUM's. The $5 floor and top-1000 cut are conventions, not evidence-based; they are logged as config in every trial. Rebalance dates cluster with month-end flows, which may worsen fills; the cost model should be checked against paper fills in Phase 4. Pre-2019 security-type classification will be heuristic.
- Reversibility: cheap for thresholds (config, subject to the pre-registration guard); moderate for the universe definition, because every registered trial depends on it. Changing the definition after trials exist means new trials, not edited ones.
- Revisit if: the survivorship gap or the unclassifiable count concentrates near the cap cut; paper fills diverge from the cost model at month end; the strategy spec needs a different formation window; or the Phase 3 strategy spec finds that ~$100 across the chosen position count falls below Alpaca's fractional minimum per order.
