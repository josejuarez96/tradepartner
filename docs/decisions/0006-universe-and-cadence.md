# 0006. Universe and cadence

**Status:** Accepted; Cadence section superseded by [ADR 0012](0012-cadence-as-a-hypothesis-parameter.md) (2026-10-05); the universe section, guards (a) and (b) and Verify stay in force; the amendment of 2026-10-09 (T70, #294) records paper's T+1 execution under ADR 0012 sentence 2  ·  **Date:** 2026-09-24  ·  **Issue:** #7

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

*Amendment 2026-10-04 (#787):* in rule 6 "a bar" means a traded bar (volume above zero). Rule 6 also fails a name with an unexplained one-day price jump in the window that the owner has not accepted (reason `price_jump`, not missing data). See the [data-foundation spec](../specs/data-foundation.md), "price-quality gate".

*Amendment 2026-10-04 (#845):* in rule 7 the shares fact is the latest one known at T that is **in line** with the security's last accepted earlier fact. In line means a ratio within `universe.max_shares_ratio` (100) either way, after the splits known at T between the two dates; the owner can also accept a fact through `universe.accepted_shares_facts`. An out-of-line fact (a filer scale error) is rejected, and the last accepted fact is used instead, under the same age limit. See the [data-foundation spec](../specs/data-foundation.md), "shares plausibility".

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

## Amendment 2026-10-09: Phase 4 paper execution (T70, #294)

**Status: Accepted by the owner's merge of PR #297 (#247 Q4 and Q14; drafted 2026-09-27, completed 2026-10-09 once Probe 3 reported).** This is the execution half of the [ADR 0005 amendment](0005-objective-benchmark-stop-criteria.md#amendment-2026-10-09-phase-4-residual-tracking-t70-294); both halves merge before `paper start` (T71).

**Where this sits under ADR 0012.** This ADR's Cadence section, including its "T+1 close for both backtest and paper, never a mix" bullet, was superseded by [ADR 0012](0012-cadence-as-a-hypothesis-parameter.md) on 2026-10-05, whose sentence 2 now governs: the backtest fills on session T+1 at the frozen `execution.fill_price`, "how paper executes on T+1 is the paper spec's, and the difference between the two is measured by its tracking comparison, not assumed away". ADR 0012 asked T70, landing after it, to restate its bullet against that sentence rather than amend superseded text. This amendment therefore supersedes nothing; it records, for the ADR 0005 check and for the owner's decision on #247 Q4, what the paper spec's rule is and the evidence it rests on.

**How paper executes on T+1 (spec reqs 3 and 7).** H1's registered `execution.fill_price = close` stays frozen: its backtest and benchmarks keep the close convention. Paper executes market DAY orders in two phases: sells, by quantity, submitted before the open inside the submit window; then buys, by notional, submitted after every sell is terminal or `paper.sell_wait_seconds` has elapsed since the open, sized from the account's then-current cash. On Alpaca's paper simulator a pre-open fractional order fills at the official opening print and a pre-open whole-share order, and every order submitted during hours (so every buy), at the NBBO after the open; none fills at the backtest's close. The ADR 0005 residual check accounts for the difference while always showing the raw series. Signals still use only information known at the preceding rebalance cutoff. Nothing here changes H1's registration, the cadence (ADR 0012) or the universe.

**What Probe 3 found about the paper simulator** ([report](../research/2026-10-09-probe3-preopen-fills.md), #182, PR #1349; the protocol is in [#86's report](../research/2026-09-25-alpaca-open-and-depth.md)):
- a fractional (notional) buy queued before the open appears to be released at a fixed pre-open time and fills at the official opening print, within about 2 s of 09:30 ET (4 of 4);
- a whole-share buy queued before the open fills at the NBBO ask 17 to 118 s after the open (median 53 s), −9 to +35 bps from the official open (4 of 4);
- an OPG order expires unfilled on paper (4 of 4), so OPG is not a usable order type for the paper stage and the two-phase design above stands;
- the eight whole-share and OPG orders were acknowledged 5 to 14 ms after creation (the four fractional ones came back `accepted` at once); the T48b recording ([alpaca-paper-facts](../research/2026-10-08-alpaca-paper-facts.md)) shows during-hours market orders filled at the first poll, which is the case the buys phase meets.

Every gap above is measured against the **official open**. The fill-timing term of the residual (req 10) is measured against the frozen `execution.fill_price` bar price, the **close** of the fill session for H1, so it also carries each name's open-to-close move on that session; Probe 3 did not measure that term, only the execution side of it.

**The six timing keys (#247 Q14), checked against that evidence.** All six are run-time keys (not frozen into the window), so a value the first paper window proves wrong is changed in config with a dated note in the spec, not by a new amendment. The measured latencies sit inside every placeholder with a wide margin, and the two that could have been tightened (`sell_wait_seconds`, `poll_interval_seconds`) bind only in the case the probe did not cover, where a tighter value has no measured gain and a worse failure mode. So **all six keep their values, now as measured values rather than placeholders.** Per key:

| Key | Value | Why it stays |
|---|---|---|
| `paper.submit_window_before_open_minutes` | 90 | Probe 3 submitted 25 to 29 minutes before the open and the orders were accepted and held until 09:30; it did not test an earlier submission, so nothing measured moves this edge. The scheduling runbook's 08:05 ET job and `lab.paper_run_lead_minutes` rest on it. |
| `paper.submit_window_after_open_minutes` | 30 | Unmeasured: the probe submitted before the open only. Interaction with the sell wait: the wait is counted from the open (`wrapper._await_sells`), so a run whose sells phase starts after open + `sell_wait_seconds` (after 09:45 here) sweeps its sells once and sizes buys straight away; that is acceptable only because T48b saw during-hours market orders fill at the first poll. A tighter edge would turn a late wake into a `pending` rebalance for no measured gain. |
| `paper.sell_wait_seconds` | 900 | The slowest of the four whole-share buy fills was 118.5 s after the open and the slowest fractional 2.0 s; **no sell was probed**, so the buy-side delay is only a proxy. The wait ends as soon as every sell is terminal, so on a session like the two probed, 900 and a tighter value behave identically; the key binds only on a sell still open minutes after the open, exactly the unprobed case, and there a longer wait funds more buys. The failure mode of too short a wait is not unspent cash: a buy submitted while a sell of its rebalance is in flight stays open under the spec's remainder rule, the rebalance stays `pending` and is retried as a catch-up next session (more orders, more drift, and a lapse to `missed` after `paper.max_catch_up_sessions`). 900 is kept. The check on the first fill sessions: a sell still open at open + 900 s on a normal session is a reason to look at the sell, not to move this key. |
| `paper.poll_interval_seconds` | 15 | Fills landed from 1 s (fraction) and 17 s (whole share) after the open. A sweep of the open sells costs one paced `get_order` each (`alpaca.trading_requests_per_minute`, 0.4 s per read), so at H1's order counts the sweep, not this interval, sets how soon the buys follow the last sell fill (about 40 s for 100 open sells); a 5 s poll would save at most 10 s. Pre-open, `_await_sells` polls from submission to open + `sell_wait_seconds`; the pacer is saturated from about 38 open sells at 15 s, harmlessly, since the pacer bounds the rate. 15 stays, at most `paper.accept_wait_seconds` (req 3(f)). |
| `paper.accept_wait_seconds` | 30 | Acknowledgement stamps 5 to 14 ms after creation on the whole-share and OPG orders, and T48b's fills at the first poll 3 to 4 ms after submit. The deadline binds only on a fault, where 30 s is already a thousand times the measured latency; shortening it buys nothing and risks a false halt on a slow broker response. |
| `paper.fill_read_overlap_seconds` | 60 | Not measured by the probe. The overlap guards the skew between the journal's `known_at` and the broker's fill stamps, which `risk.max_broker_clock_skew_seconds = 60` already bounds and the run pre-checks. Probe 3 did show that Alpaca restamps a queued fractional order's `submitted_at` to its pre-open release time (09:23 ET on both sessions), which confirms that the cursor rests on the journal's own `known_at`, never on the broker's `submitted_at`. |

**How two sessions limit this.** The evidence is four buy fills per order type on two sessions in two liquid large-cap names, on paper; the owner chose two sessions rather than the protocol's five (#182, 2026-10-08). It bounds nothing: the worst case observed is the largest of four, not a tail, and no sell, no mixed whole-plus-fraction order and no thin name was probed. What it does show is that none of the six values is contradicted by the simulator's measured behaviour, and that the two values a tighter sample might have justified lowering only matter in the cases the probe left open. The first paper window's own `orders` and `fills` rows, across many names and both sides, are the larger sample; a value they prove wrong is changed as a run-time key with a dated note in the spec.

**Option (c) of #247 Q4** (register an `open` fill convention with a slippage allowance and track that) was not revisited. Probe 3's fills landing at or near the official open is evidence an `open` convention could use, but it would need the slippage key the #86 report leaves to the execution ADR and a new registration beside H1, and the owner chose (b). It stays with the Phase 4 execution ADR.
