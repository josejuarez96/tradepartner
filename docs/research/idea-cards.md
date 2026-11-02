# Idea cards

**Status:** living (class A)  ·  **Spec:** [idea pipeline](../specs/idea-pipeline.md) (#2896), requirements 1 to 7 and "Data / interfaces"  ·  **Ledger:** the [ladder ledger](hypothesis-backlog.md#ladder-ledger) (one row per card that answered question 3; one batch row per mining pass)

Every idea enters as a card from a named source, written before any return is read for it. This file holds the tracking table, the capability tally, the vocabulary, the card shape, and then the cards, one section per source pass, newest first. A card's verdict is the filter's, not a selection: selection happens after the independent review (spec requirement 7, steps 2 and 4).

## Tracking table (pilot, 2026-11)

Counts only, no minimum on any row. Source C's column is from its pass ([flow-calendar note](2026-11-02-flow-calendar-source-c.md), #2907); the orchestrator keeps the sum.

| Measure | Source B (#2908) | Source C (#2907) |
|---|---|---|
| Ideas reviewed (field × event pairs, or flows) | 33 | 17 flows |
| Cards written | 5 | 6 |
| One-line rejects | 28 | 11 |
| `rejected` cards, by question | 2 (question 3) | 2 (question 3: FC-3, FC-4) |
| `deferred` cards, by reason | 0 | 3 (power: FC-1, FC-2, FC-5) |
| `blocked` cards, by capability | 3 (data field: later statement vintages; data field: deal filings and terms; data field: 8-K items) | 1 (FC-6: side short (paper); also cost measurement (borrow, smaller IPOs) and data field: lock-up terms) |
| `admit` cards | 0 | 0 |
| Admitted (`admit` + `blocked`) killable without a return read | 3 of 3 | 1 of 1 |
| Forwarded to the strict tier | 0 (selection follows the review) | 0 (selection follows the review) |
| Killed by a fast look | n/a (no fast look runs in the pilot before selection) | n/a |
| Strict-tier verdicts, `TP-` claims written | 0, 0 | 0, 0 |
| Agent-days spent | about 1 | about 1 |

## Capability tally

Admitted `blocked` cards per missing capability, by distinct mechanism. "Past a capability-free gate" counts cards whose mechanism has a fast look on open data, an `economic` read, or a graded claim at MIXED or better.

- **data field: `statement_facts` later vintages** (parse-only): 1 admitted card (IC-B2), 1 distinct mechanism, 0 past a capability-free gate · build: M, about 2 to 3 agent-days (keep every vintage the ingest already parses and counts as `statement_restated`; a schema change and a look-ahead test) · abandoning: one mechanism (stealth revisions) that no other held field can test. IC-B2's trade also needs **side: short (paper)**.
- **data field: deal filings and offer terms** (filing-index forms not held; offer terms not held): 1 admitted card (IC-B3), 1 distinct mechanism, 0 past a capability-free gate · build: the filing-index seed is already planned (T318 to T324; T324 is an owner step, S); offer terms are a new text parse, M to L, not planned · abandoning: the one risk premium in the batch (merger arbitrage), whose payoff is cash-plus rather than an anomaly. Also needs **cost measurement ($50 to 200M)** to reach power.
- **data field: 8-K items (`filing_events`)** (code merged, 0 rows): 1 admitted card (IC-B4), 1 distinct mechanism, 0 past a capability-free gate · build: T164e's real-store run, owner, S (the same run candidate 1 waits on) · abandoning: the non-earnings negative-item timing card; the run is shared with candidate 1, so its marginal cost here is near zero. IC-B4 also needs **side: short (paper)** for the trade itself (ADR 0024 allows it; no short book exists).
- **side: short (paper)** (self-imposed: ADR 0024 allows it, no short paper book exists): needed by 3 admitted cards (IC-B2, IC-B4, FC-6), 3 distinct mechanisms, 0 past a capability-free gate; IC-B2 and IC-B4 each wait on a data field first; FC-6 (lock-up expiry) needs it for its trade and also waits on the two lines below · build: a short paper book, size not estimated here · abandoning: the trade side of both cards; long-only could only avoid the names.
- **cost measurement (short borrow on recent IPOs)**: 1 admitted card (FC-6), 1 distinct mechanism, 0 past a capability-free gate · build: M (paper charges no borrow; a borrow-rate source is needed) · abandoning: the lock-up expiry short, the one source C card that passes the power screen.
- **data field: lock-up terms** (prospectus text, parse-only): 1 admitted card (FC-6), 1 distinct mechanism, 0 past a capability-free gate · build: M (a 424B4 text parse) · abandoning: as above; a day-180 default without the parse would misdate early releases.
- **cost measurement ($50 to 200M)**: no card blocked on it alone. IC-B1 fails break-even at the lowest measured cost, so measuring the band would not admit it; IC-B3 needs the band for power. Source C: FC-6's smaller IPOs are below $200M (not blocked on it alone); FC-1 and FC-5 need it too but are `deferred: power`, so they are not counted.

## Vocabulary

The owner's list of 2026-11-02 (spec, "Data / interfaces"); extend by editing this header.

| Capability | Values | Status today |
|---|---|---|
| bars | daily · minute | daily held; minute self-imposed (Alpaca serves them; the 2026-11-02 replay pulled them) |
| quotes | none · SIP NBBO | self-imposed (pulled ad hoc for the cost notes, not stored) |
| order types | market DAY · limit · OPG/CLS auction · stop | market held; limit and stop self-imposed (the adapter implements market only); OPG/CLS self-imposed and paper fills unproven (every OPG order expired in Probe 3; the T374 probe's result is pending, and T375 builds only if it fills) |
| side | long · short (paper) · short (live) | long held; short on paper allowed by ADR 0024, no book yet; short live `charter (ADR)` (live stays long-only on the cash account until the live account decision) |
| liquidity band | ≥ $200M · $50–200M · < $50M | ≥ $200M is the registration default for daily and weekly books; lower bands self-imposed |
| cost measurement | per band, under the 2026-10-30 rung-1 rule | ≥ $200M measured (+8.8 bp arrival, +24.2 bp open; two rebalance days); lower bands unmeasured (self-imposed: no book trades them) |
| instruments | US equities · options · crypto · futures | equities held; options `charter (ADR)`; crypto `charter (ADR)`; futures external (the broker has none) and `charter (ADR)` |
| history | 2016+ · pre-2016 | 2016+ held; pre-2016 external, purchasable (ADR 0022) |
| data field | <named field> | held · parse-only · not held |
| live size | ≤ $100 | `charter (ADR)` (the cap holds until the charter is amended) |

## Card shape

```
### <id>  <one-line mechanism>
source: B|C|A|D|E (<batch row date>)   ·   written: <date>   ·   by: owner | agent | workflow <id>
1 persists because:  inefficiency <who keeps losing, why> | premium <what loss we are paid to carry, when it arrives> | constraint <on whom>
2 capture:           capacity at $100-$100k <yes/no, why>; hold <n> sessions fits patience <yes/no>; execution needed <held | q6>
3 cost:              turnover <x>/month, cost/side <arrival bp> / <open bp> at <band> (or unmeasured); plausible gross <bp> (<source>); margin <× break-even>
4 data:              timestamp <field>; events/yr <n>; delisted coverage <ok | thin>; history <2016+ | pre-2016>
5 tried before:      <ledger id, exposure ids, related published mechanisms, windows already seen, or none>; what is new
6 capabilities:      <list from the vocabulary, each held | self-imposed | charter (ADR) | external>; held suffice for a first test: yes | no
7 kill decidable:    n_eff <n> at <effect>; <yes | deferred: power>
verdict:             admit | blocked: <capability> | deferred: <reason> | rejected: <question>   ·   killable without a return read: yes | no
frozen:              <kill-line comment URL and created_at = the freeze timestamp>
looks:               k = <n> = campaign <c> (all cards) + prior <p> (since 2026-10-30, where recorded); batch denominator <d>; lower bound
fast look:           declared <series, construction, window, kill line; comment URL>  →  <pass | kill, one number, date, exposure id>
review:              <reviewer's findings, one line>   ·   ledger: <row id>   ·   strict tier: <issue # or ->
```

Ids: `IC-B<n>` for source B cards, `FC-<n>` for source C (the flow calendar, written before this header merged); ledger rows `L-B<n>` for source B and `FC-<n>` for source C, so the two passes never renumber each other.

## Source C: the flow calendar (pass of 2026-11-02, #2907)

The rule, the documents and the eleven one-line rejects are in the [source C note](2026-11-02-flow-calendar-source-c.md) (sections 1, 2 and 4). Its batch row is in the note's section 6 and in the ledger's Batches table. **Cost arithmetic used on every card:** the rung-1 rule's only measured band, over $200M, is **+8.8 bp per side against arrival** and **+24.2 bp against the open** (paper, two rebalance days, [fill-cost note](2026-10-30-paper-fill-cost-arrival.md)). One event round trip therefore breaks even at 2 × 8.8 = **17.6 bp** (48.4 bp against the open). D9's margin of 2× break-even needs **35.2 bp**. The publication haircut (QI-2, about 58% after publication; UNGRADED and scoped to long-short characteristic portfolios, applied here to event windows because the spec's question 3 directs it) is applied once, and not to a figure measured only after publication. **Power:** n = (sd / effect)², with every sd stated as an assumption on the card and none taken from held returns. Events on one calendar date are counted as one cluster: the effective count per date is m / (1 + (m − 1)ρ), at most 1/ρ. **Window:** event counts use the development window, 2020-08-31 to 2023-12-29 (every family's `in_sample_start` to the boundary). The store's history starts in 2016, but before 2020 it holds mostly survivors (census: 36 delisters on file for 2016-07 to 2018), so earlier dates are not counted. **Reads:** no store row, no price, no return (note §1).

### FC-1  Stocks promoted from the Russell 2000 to the Russell 1000 are sold by R2000 trackers at the implementation close and recover over the next 20 sessions
source: C (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotc)
1 persists because:  inefficiency: R2000-benchmarked funds must sell the promoted names at the implementation close (4th Friday of June; 2nd Friday of December from 2026), whatever the price, and R1000 funds buy only a small weight of each; the band (±2.5% cumulative cap around #1,000) is public, so the selling is foreseeable but not avoidable by the trackers (Russell methodology v7.2, §6.10, App. F)
2 capture:           capacity at $100-$100k yes (the promoted names are mid caps around the #1,000 breakpoint; our orders are tiny against a volume spike of about 12×); hold 20 sessions fits patience yes; execution needed: held (market DAY buy at the Monday open after implementation, sell 20 sessions later); a closing-auction buy at the implementation close itself would catch more and is q6 (CLS, self-imposed, paper fills unproven)
3 cost:              turnover: one round trip per name per review (1 to 2 reviews a year, about 0.1 to 0.2/month as a book share); cost/side +8.8 / +24.2 bp for names over $200M; **unmeasured ($50–200M)** for the many promoted names under $200M median dollar volume; plausible gross **+2.0% over 20 sessions** (Dimensional 2014-2023, value-weighted, against the R2000; **UNVERIFIED: a practitioner web article, ungraded, not in claims.toml**; a post-publication window, so not haircut again; +1.0% if a second haircut is applied for the 2025 note); margin 11× break-even at arrival, 4× at the open (+2.0%); 5.7× and 2.1× at +1.0%. In the $50–200M band, the 2026-10-28 audit's figures against the open (buys +51 bp, sells +6 bp, not under the rung-1 rule) average about +28.5 bp per side, a 57 bp round trip, which would leave about 1.75× at +1.0% (0.98× if every side cost the buy figure), so rung 1 clears at ≥ $200M arrival and is undecided in $50–200M
4 data:              timestamp: rank-day close (last business day of April or October) for the predicted list; implementation date from App. F. Russell membership history **not held**: it can be rebuilt approximately from `prices_daily` close × `facts` `shares_outstanding` (ingested from `EntityCommonStockSharesOutstanding`; its `known_at`) on rank day, `listings.exchange`, and the band, which needs last year's membership (a recursive rebuild with an error near the breakpoint), or bought; events/yr: unknown, countable without returns from the rebuild (one date a year to 2025, two from December 2026); delisted coverage: ok for 2020+, thin before (census: 36 delisters on file for 2016-07 to 2018), and missing names before 2020 would also shift the rebuilt ranks around #1,000; history 2016+, but only **3 June dates (2021, 2022, 2023)** fall in the development window
5 tried before:      no ledger item. Relatives: QI-19 (index addition, NOT SUPPORTED, parked: S&P additions, not Russell migrations, and the announcement leg, not the post-implementation reversal); C2 (forced selling of spin-off children, deferred: power); HO-8 (fire sales, MIXED). Published: Madhavan 2003, Chang-Hong-Liskovich 2015, Greenwood-Sammon 2025 (shrinking index effects), Dimensional 2025. Not in the CZ catalogue. **Window seen:** the Dimensional result (2014-2023) covers 2020-08 to 2023-12, the months a strict-tier store test would use, so such a test is not on unseen data. New: the long-only reversal leg after a forced sale, on a date and list fixed by rule
6 capabilities:      bars daily held; order types market DAY held (CLS self-imposed, unproven); side long held; liquidity band ≥ $200M held, $50–200M self-imposed; cost measurement $50–200M self-imposed (unmeasured); data field Russell membership **not held** (rebuildable from held fields); history 2016+ held; held suffice for a first test: **yes** (the rung 2-O volume check below uses held bars and facts)
7 kill decidable:    sd of a mid cap's 20-session excess return assumed 12% (about 43% a year); n = (12/2)² = 36 at +2.0%, 144 at +1.0%. Clustering: with ρ assumed 0.1 against size-matched peers, at most about 10 effective events per date; on the 3 June dates of the development window that is at most about 30, below 36; **deferred: power** at +2.0% and at +1.0%. It re-enters with a membership history that includes delisters (bought, or pre-2016 history under ADR 0022), which adds dates
verdict:             **deferred: power** (at most about 30 effective events against 36, on 3 dates); if it re-enters, it is also blocked on cost measurement ($50–200M) and a data field (Russell membership, rebuildable)   ·   killable without a return read: **yes** (the power screen fails first). If it re-enters, a rung 2-O check: on the implementation dates it would then hold, our rebuilt promoted names should show the published volume spike (about 12× the volume of 20 sessions earlier) and size-matched stayers should not. No spike means the rebuild is wrong, and the card dies before any return is read
frozen:              -
looks:               k = campaign 0 from source C (no card read a return; source B's count is added at selection) + prior p (counted at selection from the ledger Items table, `trials`, `research_runs` and the desk reads since 2026-10-30); batch denominator 17 (source C); lower bound
fast look:           none (pilot: no fast look before the selection)
review:              -   ·   ledger: FC-1   ·   strict tier: -

### FC-2  Nasdaq-100 trackers must buy the names that the December reconstitution rule selects, and the list can be predicted from the November reference date
source: C (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotc)
1 persists because:  inefficiency: index funds tracking the Nasdaq-100 must buy additions and sell deletions at the effective-date close (after the 3rd Friday of December), whatever the price; the selection is mechanical (top 75 by full market cap, then the 100/125 buffer) on data known at the reference date, the last trading day of November (Nasdaq-100 methodology 2026, steps 1 to 4 and schedule)
2 capture:           capacity at $100-$100k yes (large caps); hold about 15 sessions (from the open after the reference date to the open after the effective date) fits patience yes; execution needed: held (market DAY at both ends)
3 cost:              turnover: one round trip per added name a year; cost/side +8.8 / +24.2 bp at ≥ $200M (the additions are large caps); plausible gross ≤ **+1.0%** over the window (assumption: Greenwood-Sammon find the Nasdaq-100 effect shrank with the others, to under 1% around the announcement in the 2010s; no verified figure for the pre-announcement window); margin ≤ 5.7× break-even at arrival, ≤ 2.1× at the open
4 data:              timestamp: reference-date close; `listings.exchange` (point-in-time) held; ICB industry not held (`classifications.sic` as a proxy for "Financials"); full market cap from `facts` shares × close, but ADR depositary-share counts not held; events/yr about 3 to 6 additions and as many deletions (a count over 2020 to 2023 is needed, without returns; none was made in this pass); delisted coverage: n/a (large caps); history 2016+, with 4 December dates (2020 to 2023) in the development window, each year's rule version pinned first (the 2021 redline shows the reference dates changed)
5 tried before:      no ledger item. Relatives: QI-19 (index addition, NOT SUPPORTED, parked); Greenwood-Sammon (Nasdaq-100 included); Biktimirov & Xu 2019 (first-time additions permanent, repeat additions and deletions temporary). Not in the CZ catalogue. No window seen. New: a rule-predicted list bought before the announcement, not the announcement reaction
6 capabilities:      bars daily held; order types market DAY held; side long held; liquidity band ≥ $200M held; cost measurement ≥ $200M held; data field ICB classification not held (SIC proxy held), ADR share counts not held; history 2016+ held; held suffice for a first test: yes (a count of the predicted adds against the published adds, with no returns)
7 kill decidable:    sd of a large cap's 15-session excess return assumed 8%; n = (8/1)² = 64 at +1.0%. Raw additions 2020 to 2023: about 20 on 4 dates (assumed about 5 a date); with ρ assumed 0.1, about 3.5 effective a date, about 14 in all; **deferred: power**
verdict:             **deferred: power** (about 14 effective events against 64 needed at a plausible ≤ +1.0%)   ·   killable without a return read: yes (the power screen fails before any read)
frozen:              -
looks:               k = campaign 0 from source C + prior p (counted at selection); batch denominator 17; lower bound
fast look:           none
review:              -   ·   ledger: FC-2   ·   strict tier: -

### FC-3  Balanced, pension and target-date funds sell equities at month end after stocks beat bonds, and the market dips the next day
source: C (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotc)
1 persists because:  constraint: mandates fix a stock/bond mix and rebalance on a calendar or a threshold, whatever the price; the trade is foreseeable from the month-to-date stock-versus-bond return (Harvey, Mazzoleni & Melone, NBER w33554, 2025)
2 capture:           capacity at $100-$100k yes (SPY); hold: out of SPY for about 1 to 2 sessions in months when the signal says "sell", in SPY otherwise; fits patience yes; execution needed: held (market DAY sell at the open of the last session or two, buy back at the open after); a long-only book can use the flow only by stepping aside, so the gain is the avoided dip minus the T-bill it earns instead
3 cost:              turnover: one round trip in signal months (at most 12 a year); cost/side +8.8 / +24.2 bp (SPY is in the ≥ $200M band; the rule's figure was measured on single stocks, and SPY's own cost is unmeasured and probably lower); plausible gross about 17 bp per event (the paper's next-day figure) × 0.42 after the QI-2 haircut ≈ **7 bp**; margin **0.4× break-even** (17.6 bp)
4 data:              timestamp: month-to-date closes; SPY bars held (the benchmark); a bond return series (an aggregate or Treasury ETF's bars) is UNVERIFIED in the store; the T-bill rate held (`rates`); events/yr 12 (calendar), fewer above a threshold; delisted coverage: n/a; history 2016+ (40 months in the development window)
5 tried before:      no ledger item. Relatives: B2 (trend-filter overlay, parked: a higher Sharpe at a lower return is not accepted, owner #659), which applies if stepping aside lowers the return; FC-4 (same days, different holders). Published in 2025: no post-publication window. Window seen: the paper's sample end was not read; if it runs past 2020-08, those months are seen. New: a mandate-driven sale with a conditional direction
6 capabilities:      bars daily held; order types market DAY held; side long held; instruments US equities (an ETF) held; cost measurement for an ETF not measured separately (the ≥ $200M band figure applies under the rule); data field bond index bars UNVERIFIED; history 2016+ held; held suffice for a first test: no (a bond series is needed, and the only first test is a return read)
7 kill decidable:    daily market sd about 1.1%; n = (110/7)² ≈ 250 against 40 months (2020-09 to 2023-12); deferred: power on the store window (a pre-2016 open-data read would have the count)
verdict:             **rejected: question 3** (plausible 7 bp against a 17.6 bp round trip at the rule's figure; re-read only if an ETF cost is ever measured under the rung-1 rule)   ·   killable without a return read: yes (cost arithmetic)
frozen:              -
looks:               k = campaign 0 from source C + prior p (counted at selection); batch denominator 17; lower bound
fast look:           none
review:              -   ·   ledger: FC-3   ·   strict tier: -

### FC-4  Institutions that must hold cash on the last business day sell equities a settlement cycle earlier, and prices recover before month end
source: C (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotc)
1 persists because:  constraint: pension benefits, salaries and fund redemptions are paid on the last business day T; cash from a sale arrives after the settlement lag (SEC Rule 15c6-1: T+3 from 1995, T+2 from 2017-09-05, T+1 from 2024-05-28), so the holders sell by T−4, then T−3, now T−2, whatever the price (Etula et al., RFS 2020, §1)
2 capture:           capacity at $100-$100k yes (SPY); hold: out of SPY over the selling window (5 sessions ending at the settlement deadline), in SPY otherwise; fits patience yes; execution needed: held (market DAY); the long-only form steps aside, as in FC-3
3 cost:              turnover: one round trip a month; cost/side +8.8 / +24.2 bp (≥ $200M band; SPY's own cost unmeasured); plausible gross: the selling window's excess return was −31% cumulative over 2003 to 2013, about −28 bp a month (log), × 0.42 after the QI-2 haircut ≈ **12 bp** avoided a month; margin **0.7× break-even** (17.6 bp)
4 data:              timestamp: the trading calendar and the settlement rule in force each month (rule dates above); SPY bars held; events/yr 12; delisted coverage: n/a; history 2016+ (40 months in the development window, all under T+2; the T+2 change of 2017 is before it and the T+1 change of 2024 after it)
5 tried before:      no ledger item. Relatives: FC-3 (same days); the turn-of-the-month effect (Ogden 1990; McConnell & Xu 2008) is the buying half of the same cycle; B2's objective rule as in FC-3. Published 2020; no post-publication read here. No window seen. New: the settlement rule dates the flow, and the rule changed twice, which predicts that the window moves
6 capabilities:      bars daily held; order types market DAY held; side long held; instruments US equities (an ETF) held; cost measurement for an ETF not measured separately; history 2016+ held; held suffice for a first test: yes (SPY bars, a return read)
7 kill decidable:    5-session market sd about 2.5%; n = (250/12)² ≈ 430 against 40 months; deferred: power on the store window
verdict:             **rejected: question 3** (plausible 12 bp against 17.6 bp; re-read only if an ETF cost is measured under the rung-1 rule)   ·   killable without a return read: yes (cost arithmetic)
frozen:              -
looks:               k = campaign 0 from source C + prior p (counted at selection); batch denominator 17; lower bound
fast look:           none
review:              -   ·   ledger: FC-4   ·   strict tier: -

### FC-5  Taxable holders sell prior-year losers before the last December session and cannot buy them back for 30 days; the losers recover in January
source: C (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotc)
1 persists because:  constraint: a capital loss counts in the tax year of the trade date (IRC §1211/§1212; IRS Pub. 550), and the wash-sale rule (IRC §1091) bars buying back within 30 days, so the sale is fixed by the calendar, and the buyback is pushed past late January
2 capture:           capacity at $100-$100k yes; hold about 20 sessions (last days of December to late January) fits patience yes; execution needed: held (market DAY)
3 cost:              turnover: one round trip a year; cost/side **unmeasured (< $50M and $50–200M)**: the losers that tax selling moves most are small caps, below the measured band; +8.8 / +24.2 bp for any loser over $200M; plausible gross: no verified post-2000 US figure found in this pass; assumed ≤ **+1.0%** after the haircut (the classic small-cap January premium was several percent and is said to have weakened, UNVERIFIED); margin ≤ 5.7× break-even at ≥ $200M arrival, unknown below
4 data:              timestamp: the trading calendar; the prior-year loss measured from held closes (a signal input, read to rank names, not a return outcome); events/yr: one date, hundreds of names; delisted coverage: thin before 2020 (census), and losers delist more often; history 2016+ (3 Januaries, 2021 to 2023, in the development window)
5 tried before:      no ledger item. Relatives in the CZ catalogue: `MomSeason`, `MomSeasonShort` and the other Heston-Sadka seasonality signals (calendar seasonality, not tax selling), and the momentum family's January losses. No window seen here. New: the date rule written from the tax code
6 capabilities:      bars daily held; order types market DAY held; side long held; liquidity band < $200M self-imposed; cost measurement < $200M self-imposed (unmeasured); history 2016+ held; held suffice for a first test: no (the band that carries the effect has no cost figure)
7 kill decidable:    one date a year with a strongly shared small-cap January move: with ρ assumed 0.15, at most about 7 effective events a date, about 20 over 3 Januaries; sd of a small cap's 20-session excess return assumed 15%; n = (15/1)² = 225 at +1.0%; **deferred: power**
verdict:             **deferred: power** (and the cost below $200M is unmeasured)   ·   killable without a return read: yes (the power screen fails first)
frozen:              -
looks:               k = campaign 0 from source C + prior p (counted at selection); batch denominator 17; lower bound
fast look:           none
review:              -   ·   ledger: FC-5   ·   strict tier: -

### FC-6  Pre-IPO holders become free to sell when the lock-up expires, about 180 days after the prospectus, and the price falls around that date
source: C (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotc)
1 persists because:  inefficiency: the supply arrives on a contractual date printed in the prospectus; VC funds with fixed lives and employees with concentrated wealth sell whatever the price, and short sellers who could front-run it face expensive, scarce borrow in recent IPOs (Field & Hanka, JF 2001: −1.5% over three days, volume +40%)
2 capture:           capacity at $100-$100k yes for most deals; hold about 3 to 5 sessions fits patience yes; execution needed: a **short** (q6). The long-only form is only an avoid list, which earns nothing on its own
3 cost:              turnover: one round trip per expiry; cost/side +8.8 / +24.2 bp for names over $200M, **unmeasured** for smaller IPOs; borrow fee **unmeasured** (paper charges no borrow; recent IPOs are often hard to borrow); plausible gross −1.5% × 0.42 after the haircut ≈ **−0.6%** to the short over three days; margin 3.4× break-even before borrow, unknown after
4 data:              timestamp: the 424B4 acceptance gives the prospectus date (`filing_index` holds all forms of issuer CIKs since T318, but its 424B depth back to 2020 is UNVERIFIED: no census count cited); the lock-up length and early-release clauses are in the prospectus text, **parse-only** (not stored); the first bar gives the listing date; events/yr about 100 to 300 operating-company IPOs (excluding SPACs; 2020-2021 heavy), assumed, not counted; delisted coverage: ok for 2020+ (census), and young firms delist often; history 2016+, counted from 2020-08
5 tried before:      no ledger item. Relatives in the CZ catalogue: `IndIPO`, `AgeIPO` (IPO long-run underperformance, a slower and different effect); B11 and the beyond-long-only notes (LS claims) on shorting cost. **Window seen:** the 2024 thesis result (2003-2022) covers 2020-08 to 2022-12, so a strict-tier store test on those months is not on unseen data. New: the contractual supply date
6 capabilities:      side short (paper) self-imposed (ADR 0024 allows it; no book exists); short live `charter (ADR)`; bars daily held; order types market DAY held; cost measurement for the short side and borrow self-imposed (unmeasured); data field lock-up terms parse-only; history 2016+ held; held suffice for a first test: **yes** (the rung 2-O volume check below uses held bars and the filing index)
7 kill decidable:    sd of a recent IPO's 3-session excess return assumed 8%; n = (8/0.6)² ≈ 180; raw expiries in 2020-08 to 2023-12 assumed about 500 (the 2020-2021 IPO wave), on many different dates (low clustering); **yes**
verdict:             **blocked: side short (paper)**; cost measurement (borrow, small IPOs); data field (lock-up terms, parse-only)   ·   killable without a return read: **yes**. A rung 2-O check: on day 180 after each held 424B4 acceptance, volume should rise by about the published 40% against the prior 20 sessions; no rise means the day-180 rule (or early releases) does not date the flow, and the card dies before any return is read
frozen:              -
looks:               k = campaign 0 from source C + prior p (counted at selection); batch denominator 17; lower bound
fast look:           none
review:              -   ·   ledger: FC-6   ·   strict tier: -

## Source B: own data (pass of 2026-11-02, #2908)

**Body mined.** The store census of the [data-edge census note](2026-11-02-data-edge-census.md): every data table of the 2026-11-02 store copy (schema 41), plus the fields the code parses but the store does not keep. **Rule** (the spec's, written before this pass): one card per field × event pair that names who had to do something after the fact became known, could we act before they did (our `known_at` no later than the public's first knowledge), and why the return might persist; a one-line reject for the rest. Read: counts, dates and close × volume levels for liquidity bands only; no price change or return of any kind. Field census to 2026-09-30, event counts to 2023-12-29.

**Batch row.** 2026-11-02 · agent (team pilotb) · 33 field × event pairs reviewed · 5 cards · 0 admitted to rung 1 (verdict `admit`); 3 `blocked`, 2 `rejected` · ledger rows L-B1 to L-B5. The batch is over 20 ideas, so the ledger's Upgrade path's first trigger is met as well as the second the spec names; the spec's requirement 5 handles both (no catalogue-wide look; the selected card's rung-2 read, if any, follows the path as written).

### IC-B1  Officer and director open-market purchases in mid-liquidity names predict a 21-session drift
```
source: B (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotb)
1 persists because:  inefficiency: outside sellers and liquidity providers trade against insiders' private information; constraint: below $200M of daily dollar volume the funds that could arbitrage it are capacity-bound (NE-3: informative mostly in smaller firms)
2 capture:           capacity at $100-$100k yes ($100k is under 0.2% of a $50M day); hold 21 sessions fits patience yes; execution needed held (market DAY at the first open after acceptance; G2-2: over 70% are accepted after the close)
3 cost:              turnover 100%/month per position; cost/side unmeasured ($50-200M) (context only: paper half-spread at arrival +46 bp in this band, two sessions, not under the rung-1 rule); the >= $200M figure +8.8 arrival / +24.2 open is the floor; plausible gross about 0.13%/month for all officer/director purchases (the card's trade: CMP opportunistic VW 0.72 and routine 0.09, G2-B, weighted about 1:2, x 0.42 QI-2; the opportunistic-only 0.30% applies to just 4.5% to 13% of events); break-even 17.6 bp at 8.8; margin 0.74x, under break-even at any cost >= 6.5 bp/side
4 data:              timestamp sec_form4_transactions.accepted_at = known_at (100% of rows); events/yr 495-846 officer/director purchase issuer-days in the band (5,160 in 2016-2023, 972 issuers; 2,839 in 2020-2023); delisted coverage ok 2019+ (B7 recount, all names: treated 100%, neutral 93.6%), thin before; history 2016+ (a CMP classifier needs three prior years, G2-4)
5 tried before:      B7 (ledger row B7, retired 2026-10-31, TP-9: the $200M stratum had at most 47 opportunistic events); G2-A NOT SUPPORTED (liquid, days), G2-B MIXED (monthly, non-microcap), G2-5 and G2-6 (the reaction is front-loaded, 70-80% gone before disclosure); no store return was read for B7, so no window is seen; new: the band (B7's frozen design required >= $200M) and all officer/director purchases, since CMP's cut classifies only 4.5% to 13% of events
6 capabilities:      data field Form 4 held; bars daily held; order types market DAY held; side long held; liquidity band $50-200M self-imposed; cost measurement ($50-200M) self-imposed; held suffice for a first test: no (cost unmeasured), but the verdict does not wait on it
7 kill decidable:    n_eff about 5,900 at 0.13% (sd about 10% per 21-session event, UNVERIFIED) against 2,839 raw issuer-days in 2020-2023, fewer effective after issuer clustering: deferred: power; the opportunistic-only version (0.30%, n about 1,100) has only about 130 to 370 events: deferred: power too
verdict:             rejected: question 3   ·   killable without a return read: yes (it fails on the card: under break-even even at the cheapest measured band, 8.8 bp, which the $50-200M band cannot plausibly undercut; D9 would call an unmeasured band `blocked: cost measurement`, and the reviewer should confirm the floor argument)
frozen:              - (not selected)
looks:               k = counted at selection = campaign 0 (no source-B card read a return) + prior p (since 2026-10-30; the spec's requirement 5 lists where); batch denominator 33; lower bound
fast look:           - (none before selection, spec requirement 7 step 4)
review:              pending   ·   ledger: L-B1   ·   strict tier: -
```

### IC-B2  Stealth revisions: a later filing reports a prior period's fact at a different value, and the market is slow to notice
```
source: B (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotb)
1 persists because:  inefficiency: holders who read headline numbers or restated databases (which overwrite the first vintage) never see the revision as an event; managers choose a quiet comparative-column revision over an 8-K Item 4.02 to keep attention low (Files, Swanson and Tse 2009, UNVERIFIED); downward revisions precede later material restatements (Choudhary, Merkley and Schipper 2021, UNVERIFIED)
2 capture:           capacity at $100-$100k yes (>= $200M names); hold 60 sessions (to the next filing) fits patience yes; execution needed q6 (the trade is a short of downward revisions; long-only can only avoid them)
3 cost:              turnover about 33%/month per position; cost/side +8.8 arrival / +24.2 open at >= $200M; short round trip 2 x 8.8 + 7.1 borrow (30 bp/yr over 60/252) = 24.7 bp arrival, 55.5 bp open; plausible gross 50 bp per event (UNVERIFIED; no graded claim; the smaller announcement reactions of stealth restatements imply slower incorporation); margin 2.0x arrival, 0.9x open (provisional)
4 data:              timestamp the revising filing's acceptance (the same stamp source as the first vintage); events/yr not countable (field parse-only: the ingest's `statement_restated` counter saw 253,797 differing later values against 2,134,064 first-vintage keys at the 2026-10-06 rebuild, all years and names, including recasts for discontinued operations, segment changes and the 2018-2019 ASC 606 and 842 transitions, which are not errors); delisted coverage ok 2019+, thin before; history 2016+ (bars; facts from 2011)
5 tried before:      none here (no ledger row, no exposure); nearest: C1 and C1b (filing timing, retired), B3, CBOperProf (levels of the same facts, retired); QI-28 (keep original and restated values separate) is the data principle behind it; no catalogue signal reads revisions; no window seen; new: the revision event itself, which the store has never kept
6 capabilities:      data field statement_facts later vintages parse-only; side short (paper) self-imposed (ADR 0024 allows; no book); liquidity band >= $200M held; held suffice for a first test: no
7 kill decidable:    n_eff about 256 at 0.5% (sd about 8% per 60-session event, UNVERIFIED); the count waits on the field; yes if the >= $200M band holds about 256 effective downward revisions in 2019-2023, else deferred: power
verdict:             blocked: data field (statement_facts later vintages)   ·   killable without a return read: yes (the count; then a rung 2-O `economic` run: do downward revisions precede a lower next first-vintage value or an amended filing)
frozen:              - (not selected)
looks:               k = counted at selection = campaign 0 + prior p (since 2026-10-30); batch denominator 33; lower bound
fast look:           - (none before selection)
review:              pending   ·   ledger: L-B2   ·   strict tier: -
```

### IC-B3  Cash-deal merger arbitrage: buy the target after the announcement, collect the spread at completion
```
source: B (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotb)
1 persists because:  premium: we carry deal-break risk, a loss that arrives when deals fail and clusters in market drawdowns (Mitchell and Pulvino 2001: returns like selling index puts, UNVERIFIED); constraint: target holders who want out sell to arbitrage capital, which is limited (Baker and Savasoglu 2002, UNVERIFIED)
2 capture:           capacity at $100-$100k yes in targets >= $50M of daily dollar volume; hold about 80 sessions (announcement to completion, UNVERIFIED median) fits patience yes; execution needed held (one market DAY buy; completion converts to cash with no sell order); the payoff is cash-plus with crash beta, not an SPY-beating book (ADR 0005 makes beating SPY optional)
3 cost:              turnover about 25%/month (one buy per deal of about four months; a break costs one sell); cost/side +8.8 arrival / +24.2 open at >= $200M, unmeasured ($50-200M); plausible gross 56 bp per deal over T-bills (about 4%/yr alpha, Mitchell and Pulvino 1963-1998, x 0.42 QI-2, over four months; spreads shrank after 2002, Jetley and Ji 2010; UNVERIFIED); break-even 8.8 to 17.6 bp per deal; margin 3.2x at arrival on a full round trip (the break path; the completion path costs one side, 6.4x), 1.2x at the open on a round trip
4 data:              timestamp the announcement's acceptance (8-K Item 1.01, PREM14A/DEFM14A, SC TO-T, 425): not held (the all-forms filing index is not seeded, T318 to T324; filing_events has 0 rows); offer terms not held (the `mergers` table holds 35 completions known before 2026-10-01, from 2025-08); events/yr: upper bound from Form 25 common delistings by band at filing, 2019-2023: >= $200M 45, $50-200M 128 (all reasons, completed only; failed deals absent); delisted coverage thin before 2019, and every completed target is a delister, so 2016-2018 is unusable; history 2016+
5 tried before:      none (no ledger row, no exposure); nearby: C2 spin-offs (deferred: power), the parked index-addition trade (QI-19, a different flow); not a catalogue anomaly; the source C pass may hold a sibling card on deal-related index deletions (the reviewer merges by mechanism); new: a risk premium, not an anomaly
6 capabilities:      data field deal filings not held; data field offer terms not held; liquidity band $50-200M self-imposed; cost measurement ($50-200M) self-imposed; side long held (stock deals' short-acquirer leg left out); held suffice for a first test: no
7 kill decidable:    n_eff about 160 at 0.56% (the effect from the literature above; the sd, about 7%, from a separate illustrative mixture, about 90% close for +2 to 3% and about 10% break for -20%, whose own mean is lower; UNVERIFIED); >= $200M alone at most 45, deferred: power; with $50-200M at most 173, marginal yes
verdict:             blocked: data field (deal filings and offer terms)   ·   killable without a return read: yes (the count of cash deals in band, 2019-2023, once the index is seeded: under about 160 is deferred: power)
frozen:              - (not selected)
looks:               k = counted at selection = campaign 0 + prior p (since 2026-10-30); batch denominator 33; lower bound
fast look:           - (none before selection)
review:              pending   ·   ledger: L-B3   ·   strict tier: -
```

### IC-B4  Negative non-earnings 8-K items accepted when attention is low (Friday after 16:00, or before a holiday) are incorporated slowly
```
source: B (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotb)
1 persists because:  inefficiency: inattentive holders react late (DellaVigna and Pollet 2009, earnings: 15% lower immediate and 70% higher delayed response, UNVERIFIED); managers keep timing bad news to low attention because it works for them (Niessner 2015, UNVERIFIED); part is selection of worse firms (Michaely, Rubin and Vedrashko 2016, UNVERIFIED)
2 capture:           capacity at $100-$100k yes (>= $200M names); hold 20 sessions fits patience yes; execution needed q6 (a short at the first open after; long-only can only avoid)
3 cost:              turnover 100% per 20 sessions per position; cost/side +8.8 arrival / +24.2 open at >= $200M; short round trip 2 x 8.8 + 2.4 borrow (30 bp/yr over 20/252) = 20.0 bp arrival, 50.8 bp open; plausible gross 45 bp per event beyond the same items on Monday to Thursday (the delayed share of a -2% reaction, x 0.42; UNVERIFIED); margin 2.25x arrival, 0.9x open (provisional)
4 data:              timestamp filing_events.accepted_at (items 4.02, 5.02, 2.06, 1.02, 3.01): 0 rows (code merged, PR #1459; T164e's real-store run not made); TP-1 shows the stamps matter (half of 2.02s accepted 16:00-17:30 and dated that session); events/yr not countable; delisted coverage ok 2019+; history 2016+
5 tried before:      candidate 1 (earnings-event family, #2892, Item 2.02, B9 again) reads the same table; this card leaves 2.02 out; C1 (late periodic filings) is a different timing mechanism; no ledger row; DellaVigna and Pollet is published, for earnings only; no window seen; new: non-earnings negative items and the attention split
6 capabilities:      data field 8-K items (filing_events) not held (code merged, no rows); side short (paper) self-imposed; liquidity band >= $200M held; held suffice for a first test: no
7 kill decidable:    n_eff about 180 at 0.45% (sd about 6% per 20-session event in >= $200M names, UNVERIFIED); the count waits on the rows; with about 300 names in band it is likely tens a year, so the count decides between yes and deferred: power
verdict:             blocked: data field (8-K items, filing_events)   ·   killable without a return read: yes (the count; then a rung 2-O run: next-session abnormal volume after Friday-evening negative items against Monday to Thursday ones, volume levels only)
frozen:              - (not selected)
looks:               k = counted at selection = campaign 0 + prior p (since 2026-10-30); batch denominator 33; lower bound
fast look:           - (none before selection)
review:              pending   ·   ledger: L-B4   ·   strict tier: -
```

### IC-B5  Open-market purchases by 10% owners who are not officers or directors predict a 21-session drift
```
source: B (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotb)
1 persists because:  inefficiency: a blockholder accumulating may know more or plan to monitor, and sellers trade against it; constraint: the blockholder's own capital
2 capture:           capacity at $100-$100k yes; hold 21 sessions fits patience yes; execution needed held
3 cost:              turnover 100%/month per position; cost/side +8.8 arrival / +24.2 open at >= $200M; plausible gross at most 10 bp/month (unaffiliated large holders' trades are the least informative insider class, Seyhun 1986, UNVERIFIED; no register claim); break-even 17.6 bp; margin under 1x
4 data:              timestamp sec_form4_transactions.accepted_at (100%); events/yr 5-35 issuer-days at >= $200M (148 in 2016-2023, 35 issuers), 496 at $50-200M (92 issuers); delisted coverage ok 2019+; history 2016+
5 tried before:      B7 (officers and directors, retired); C3 13D activism (re-scoped and left open in the 2026-10-28 batch; 13D rows not held); new: the role
6 capabilities:      all held at >= $200M (data field Form 4 held, daily bars, market DAY, long); held suffice for a first test: yes
7 kill decidable:    n_eff about 10,000 at 0.10% (sd about 10%, UNVERIFIED) against 35 issuers: deferred: power
verdict:             rejected: question 3   ·   killable without a return read: yes (it fails on the card)
frozen:              -
looks:               k = counted at selection = campaign 0 + prior p (since 2026-10-30); batch denominator 33; lower bound
fast look:           -
review:              pending   ·   ledger: L-B5   ·   strict tier: -
```

### Source B one-line rejects (28)

Each names the field × event pair and the reason: a filter question, or the census rule's own test, (i) no one must act after the fact, (ii) our `known_at` is no earlier than the public's.

1. `statement_facts` × fundamental anomalies re-formed at each filing's acceptance (event-time rebalancing): rejected: question 3, the gross is bounded by the post-2009 catalogue spreads already read (I1, CBOperProf, XFIN, the composite) while turnover rises; both killed kinds at once.
2. `statement_facts` × accruals revealed at the 10-Q acceptance (Levi 2008): rejected: question 3, the accrual anomaly's demise after 2005 (Green, Hand and Soliman 2011, UNVERIFIED); `net_income` not held until #2826's T365; TP-1 puts the 10-Q a median one day after the 2.02.
3. `statement_facts.known_at` × early or late periodic filing: rejected: question 5, C1 and C1b with nothing new.
4. `statement_facts` × revenue or earnings surprise at acceptance: rejected: question 5, PEAD is parked (HO-4 NOT SUPPORTED outside microcaps).
5. `statement_facts` × the 10-Q as the first release for issuers with no 2.02 8-K: rejected: question 2, they are microcaps (4.9% of filings in the top 1,000 have no 2.02, TP-1).
6. `statement_facts` × derived gross profit or tag changes: rejected: question 1, a construction detail, not an event anyone acts on.
7. `facts.shares_outstanding` × cover-page share-count changes (buybacks, ATM issuance): rejected: question 5, the issuance line was killed three times (Q1, equity issuance, XFIN) with nothing new.
8. Form 4 code P × officers and directors at >= $200M: rejected: question 7, B7's 47 events; retired.
9. Form 4 code S × insider sales and sale clusters: rejected: question 1, sales carry little information (NE-3).
10. Form 4 code G × gifts timed at price peaks: rejected: question 1, the SEC's 2022 amendment made gifts reportable within two business days from 2023-02-27, closing the backdating window.
11. Form 4 codes F, A, M × tax withholding, grants, exercises: rejected: question 1, not discretionary.
12. Form 4 × late filing (accepted more than two business days after the trade): merged into IC-B1 as a possible cut; at >= $200M a subset of 47 events, rejected: question 7.
13. Form 4 × after-hours acceptance (G2-7): rejected: question 1, an entry-timing detail of IC-B1, not a mechanism.
14. Form 4 × our acceptance timing against free real-time feeds (EDGAR feeds, insider aggregators): rule (ii), no earlier knowledge.
15. Form 4 × the 10b5-1 checkbox: deferred: history (the field exists from 2023-04, eight months before the boundary) and not held.
16. Form 144 × notices of proposed insider sales: deferred: history (electronic filing from 2023-04) and not held.
17. Corporate actions × dividend initiations and omissions: rule (ii), `announced_at` is empty and `known_at` is a median one day before the ex-date, after the declaration; also a catalogue anomaly.
18. Corporate actions × forward and reverse splits: rule (ii) as 17; reverse splits also question 2 (sub-$5 names, hard-to-borrow shorts).
19. Corporate actions × spin-offs: deferred: power, C2 (76 rows from 2020-11).
20. Corporate actions × dividend ex-day pricing: passed to source C (a tax and dividend rule flow).
21. Form 25 × forced sales by holders that may hold only listed names: passed to source C; also question 4 (no bars after delisting).
22. Classifications SIC 6770 × SPAC trust-value redemptions: passed to source C (the redemption rule); trust value per share not held.
23. `symbol_changes` × name changes: rejected: question 1, no persistence reason named after 2001.
24. `listings` × exchange transfers: rejected: question 1.
25. 8-K Item 2.02 × earnings reaction: not carded, candidate 1 (#2892) holds it.
26. 8-K Item 3.01 × listing-deficiency notices: rejected: question 2, sub-$1 bid names and hard-to-borrow shorts; the negative-item timing version is in IC-B4.
27. SC 13D × activist stakes: deferred: data (adapter code merged, 0 rows); C3 is open in the 2026-10-28 batch and is not re-carded here.
28. Filing index × SEC comment letters (CORRESP, UPLOAD) and NT 10-K/10-Q: rejected: question 1 for comment letters (the informed trading precedes release, Dechow, Lawrence and Ryans 2016, UNVERIFIED) and question 5 for NT forms (C1's mechanism).
