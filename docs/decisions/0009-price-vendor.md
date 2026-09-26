# 0009. Price vendor: none for Phase 3; Alpaca SIP bars plus EDGAR, with the survivorship gap measured and gated

**Status:** Proposed  ·  **Date:** 2026-09-26  ·  **Issue:** #240 (plan task T29)

## Context

ADR 0003 deferred the price-vendor choice to the start of Phase 3 and set the terms (rule 8): the ADR records the data budget; it may say "no vendor", but then it amends charter principle 4 ("point-in-time data, including delisted names") in the same document, and Phase 4 may not start until the owner has signed off on the survivorship gap in the trial registry.

What is known now that was not known then:

- **Alpaca's free plan serves consolidated SIP daily bars from 2016-01-04**, and for a probed sample of eight delisted names (five acquired, three failed) the bars run to each name's last regular session ([#104](../research/2026-09-25-alpaca-delisted-bars.md)). A zero-volume placeholder bar can follow the last real session, and a renamed ticker's history is served under both symbols; the security master handles both.
- **Alpaca's corporate actions are complete for 2016 to 2025** against ground truth for every split, reverse split, spin-off and dividend probed ([#101](../research/2026-09-25-alpaca-open-and-depth.md)); no announcement dates are carried, so the first-seen proxy of #83 stamps them.
- **Missing bars are rare**: at most 0.03% of listed names on any of twenty sessions, median 0.01% ([#106](../research/2026-09-25-alpaca-missing-bars.md)).
- **EDGAR is the point-in-time source for delistings** (Form 25 and 25-NSE, stamped at acceptance) and for the security master, so "which names existed on date T" does not depend on the price vendor at all.
- **The survivorship gap is measured, not assumed.** `survivorship_gap` (Phase 2 T15) counts, per rebalance, the names listed at some session in the window that have no bar at T or were delisted in the window with a stale tail, and the backtest refuses a run whose count share crosses the frozen threshold (`gap.count_share_threshold`, default 0.05) unless the owner overrides with a reason that is written to the trial ([Phase 3 spec](../specs/backtest.md), gap gate).
- **The first hypothesis fits the history.** H1 rebalances first on 2017-01-31 with a 12-month formation window, so it needs bars from January 2016 ([H1](../hypotheses/h1-momentum-12-1.md)). Its holdout ends 2026-09-30.
- **No paid vendor is verified.** The G8 report ([2026-09-25-price-vendors.md](../research/2026-09-25-price-vendors.md)) graded every vendor's delisted coverage "claimed" on the vendor's word; none records when a corporate action became known; every adjusted series is restated backwards; and Norgate, Tiingo, Sharadar and Massive all require deleting local data when the subscription ends, with Sharadar's direct licence the only one that explicitly lets derived works (results, statistics, trade logs) survive. Alpaca's terms allow private storage for personal, non-commercial use and say nothing about deletion ([free-data terms](../research/2026-09-25-free-data-terms.md)).

The store keeps raw bars and corporate actions with `known_at`, and adjusts at read time (ADR 0003 rule 1), so a vendor's restated adjusted series would never be stored as truth anyway; what a vendor could add is history before 2016 and delisted names Alpaca lacks.

## Options considered

1. **No paid vendor for Phase 3: Alpaca SIP daily bars from 2016 plus EDGAR, gap measured and gated** (this ADR). Pro: zero cost; delisted names observed present on the sample; corporate actions complete for the period; no deletion clause hangs over the store, the Parquet export or tagged backtest artefacts; the gap gate makes any bias visible per rebalance instead of assumed away. Con: history starts 2016-01-04, so no hypothesis can form a signal before that; the delisted coverage is verified on eight names only, not on the population; the free plan's SIP history access is Alpaca's to withdraw.
2. **Sharadar SEP, direct, Prices plan (USD 39/mo, history from 1998).** Pro: delisted names claimed since 1998; the only licence read that explicitly keeps derived works; bulk download; a `closeunadj` field, so raw storage holds. Con: USD 468/yr for history no registered hypothesis needs yet; delisted coverage still unverified; local data must be deleted within thirty days of cancellation, so the store would have to be rebuilt from Alpaca if the subscription lapses; adds a second price source to reconcile.
3. **Norgate Platinum (USD 630/yr, delisted names to 1990).** Pro: the deepest delisted coverage claimed, "essentially complete" from late 1992. Con: the updater is Windows-only (a VM on the owner's Mac, unresolved); deletion on expiry; adjustment method undocumented; the most expensive option that an individual can buy.
4. **Massive (formerly Polygon), 20+ year plan (USD 199/mo).** Pro: full history claimed for delisted tickers, flat files. Con: USD 2,388/yr; Tier 3 reports dispute the delisted coverage; no dividend adjustment; storage during the subscription not addressed in the terms.
5. **EODHD (USD 199/yr) or Tiingo (USD 300/yr).** Con: EODHD's names delisted before 2018 have prices only and its post-termination clause is unresolved; Tiingo's delisted support excludes recycled tickers by its own account and its free tier forbids persistent storage. Neither is better than option 1 for the period H1 covers.

## Decision

We will run Phase 3 with **no paid price vendor**. The `PriceSource` for every registered hypothesis, including H1's real-store run (T45b), is **Alpaca's free plan, SIP daily bars** (`alpaca.historical_feed = sip`), with EDGAR as the source of delistings and the security master, exactly as Phase 2 built it.

1. **Data budget: USD 0 per month for Phase 3.** The budget is re-opened only by a trigger below, and a vendor then enters through a new ADR and a plan amendment adding its adapter, never by editing this one.
2. **Delisted-coverage grade for Alpaca: verified on a sample, not on the population.** G8's "Absent (not documented)" is superseded by #104's observation. The population check is the standing one G8 asked for (follow-up 2): the data-health page's "delisted names present" metric (Phase 2 T18, T21) reports, for every Form 25 delisting in the store, whether bars exist up to the final session, and the owner reads it before T45b.
3. **The gap is the guard.** Every trial records `survivorship_gap` per rebalance; a holdout run that crosses `gap.count_share_threshold` is refused unless overridden with a reason (Phase 3 spec, gap gate). Per ADR 0003 rule 8, **Phase 4 does not start until the owner has signed off, in the trial registry's owner-decision row for H1's in-sample run, that the recorded gap is acceptable.**
4. **History starts 2016-01-04.** A hypothesis whose formation window needs an earlier session cannot be registered under this ADR; registering one is a trigger.
5. **Terms and derived artefacts.** Alpaca data is stored privately for the owner's own account under Alpaca's personal, non-commercial terms; nothing is redistributed. The Parquet export and tagged backtest artefacts (ADR 0003) carry no deletion obligation. If a vendor is later added, its deletion clause applies to its rows and to any export containing them, and the ADR that adds it says what happens to artefacts at cancellation.
6. **Charter principle 4 is amended**, per ADR 0003 rule 8, to say what "including delisted names" means under this ADR: delisted names from 2016-01-04 as served by Alpaca SIP bars and stamped by Form 25 filings, verified on a sample, with the survivorship gap measured per rebalance and gated. The text is in `docs/charter.md` with a reference to this ADR.

**Triggers that re-open the budget (any one):**
- H1's in-sample run, or any later registered hypothesis, records a gap count share above `gap.count_share_threshold` at any month-end, or a size share the owner judges material, and the owner does not accept it;
- the data-health delisted-coverage metric shows Form 25 names without bars to their final session beyond the sample's 8 of 8;
- a hypothesis the owner wants to register needs bars before 2016-01-04;
- Alpaca withdraws SIP history from the free plan or changes its storage terms.

If a trigger fires, the candidate order from G8's evidence is Sharadar SEP direct (derived works survive cancellation), then EODHD for post-2018 work only, then Norgate if pre-1998 history is needed and the Windows updater is acceptable. Massive is not a candidate at its price.

## Consequences

- Good: Phase 3 starts now with no spend and no licence deadline over the store; every bias the free source carries is measured per rebalance and refused above a threshold instead of assumed; a vendor remains a one-adapter change (ADR 0003).
- Bad / accepted risks: no signal can form before 2016-01-04, which rules out long-history hypotheses and limits the in-sample window to about seven years before the 2024 holdout; delisted coverage rests on eight probed names until the data-health metric runs on the full store; Alpaca can change its free plan, in which case the store keeps what it holds and a vendor decision follows.
- Reversibility: cheap. Adding a vendor is a new ADR plus one adapter task; the store's raw layout is vendor-agnostic and nothing recorded under Alpaca has to be discarded.
- Revisit if: any trigger above fires, or at the Phase 3 close-out (T45b) if the recorded gap or the data-health metric is worse than the sample suggested.
