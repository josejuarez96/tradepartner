# Idea cards

**Status:** living (class A) · **Rules:** [idea-pipeline spec](../specs/idea-pipeline.md) (#2896): the card (Data / interfaces), the filter (requirement 3), the pilot (requirement 7) · **Ledger:** each card that answers question 3 has a row in the [ladder ledger](hypothesis-backlog.md#ladder-ledger), whose `link` cell names the card id

An idea exists in the pipeline only as a card on this page, written before any return is read for it. Cards are grouped by source pass, newest pass first. Within a pass, each card is one section. This header holds the tracking table, the capability tally and the vocabulary; the orchestrator keeps the totals current by hand.

## Tracking table

One row per source pass, counts only. Zero is an acceptable value on any row.

| Pass | Ideas reviewed | Cards | `rejected` (by question) | `deferred` | `blocked` (by capability) | `admit` | Killable without a return read (of admitted) | Forwarded to strict tier | Agent-days |
|---|---|---|---|---|---|---|---|---|---|
| C, flow calendar, 2026-11-02 ([note](2026-11-02-flow-calendar-source-c.md)) | 17 | 6 | 13: q1 ×1, q3 ×6 (2 cards, 4 one-line), q4 ×2, q5 ×3, q6 ×1 | 2 (power: FC-2, FC-5) | 2 (FC-1: cost measurement < $200M, data field; FC-6: side short, cost measurement, data field) | 0 | 2 of 2 | 0 (selection follows the review) | about 1 |

Strict-tier verdicts and `TP-` claims: none yet.

## Capability tally

Admitted `blocked` cards, per missing capability, counted **by distinct mechanism**. Line shape (spec): `<capability>: <n> admitted cards, <m> distinct mechanisms, <p> past a capability-free gate · build: <size, agent-days> · abandoning: <what is lost>`.

- **cost measurement ($50–200M band):** 1 admitted card (FC-1), 1 mechanism, 0 past a capability-free gate · build: S on paper (a book or probe that trades the band on rebalance days, two weeks of fills under the rung-1 rule) · abandoning: the Russell migration reversal, the one card with a post-publication gross figure several times break-even.
- **cost measurement (short borrow on recent IPOs):** 1 (FC-6), 1, 0 · build: M (paper has no borrow fees; needs a borrow-rate source) · abandoning: the lock-up expiry short.
- **side: short (paper):** 1 (FC-6), 1, 0 · build: M or more, not estimated in this pass (a short book on paper under ADR 0024) · abandoning: as above.
- **data field: Russell membership history:** 1 (FC-1), 1, 0 · build: S to M (rebuild from held rank-day prices and share counts; or buy the history) · abandoning: as FC-1 above.
- **data field: lock-up terms (prospectus text):** 1 (FC-6), 1, 0 · build: M (424B4 text parse) · abandoning: as FC-6 above.

## Capability vocabulary

The owner's list of 2026-11-02 (spec, Data / interfaces); extend by editing here.

| Capability | Values | Status today |
|---|---|---|
| bars | daily · minute | daily held; minute self-imposed (Alpaca serves them; the 2026-11-02 replay pulled them) |
| quotes | none · SIP NBBO | self-imposed (pulled ad hoc for the cost notes, not stored) |
| order types | market DAY · limit · OPG/CLS auction · stop | market held; limit and stop self-imposed (the adapter implements market only); OPG/CLS self-imposed and **paper fills unproven** (every OPG order expired in Probe 3; the T374 probe's result is pending, and T375 builds only if it fills) |
| side | long · short (paper) · short (live) | long held; short on paper allowed by ADR 0024, no book yet; short live `charter (ADR)` (live stays long-only on the cash account until the live account decision) |
| liquidity band | ≥ $200M · $50–200M · < $50M | ≥ $200M is the registration default for daily and weekly books; lower bands self-imposed |
| cost measurement | per band, under the 2026-10-30 rung-1 rule | ≥ $200M measured (+8.8 bp arrival, +24.2 bp open; two rebalance days); lower bands unmeasured (self-imposed: no book trades them) |
| instruments | US equities · options · crypto · futures | equities held; options `charter (ADR)` ("no options trading"); crypto `charter (ADR)` (scope is US equities; the broker offers it); futures external (the broker has none) and `charter (ADR)` (the charter's direction line brings them in only by a later ADR) |
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

## Source C: the flow calendar (pass of 2026-11-02, #2907)

The rule, the documents and the eleven one-line rejects are in the [source C note](2026-11-02-flow-calendar-source-c.md) (sections 1, 2 and 4). Its batch row is in the note's section 6 and in the ledger's Batches table. **Cost arithmetic used on every card:** the rung-1 rule's only measured band, over $200M, is **+8.8 bp per side against arrival** and **+24.2 bp against the open** (paper, two rebalance days, [fill-cost note](2026-10-30-paper-fill-cost-arrival.md)). One event round trip therefore breaks even at 2 × 8.8 = **17.6 bp** (48.4 bp against the open). D9's margin of 2× break-even needs **35.2 bp**. The publication haircut (QI-2, about 58% after publication) is applied once, and not to a figure measured only after publication. **Power:** n = (sd / effect)², with every sd stated as an assumption on the card and none taken from held returns. Events on one calendar date are counted as one cluster: the effective count per date is m / (1 + (m − 1)ρ), at most 1/ρ. **Reads:** no store row, no price, no return (note §1).

### FC-1  Stocks promoted from the Russell 2000 to the Russell 1000 are sold by R2000 trackers at the implementation close and recover over the next 20 sessions
source: C (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotc)
1 persists because:  inefficiency: R2000-benchmarked funds must sell the promoted names at the implementation close (4th Friday of June; 2nd Friday of December from 2026), whatever the price, and R1000 funds buy only a small weight of each; the band (±2.5% cumulative cap around #1,000) is public, so the selling is foreseeable but not avoidable by the trackers (Russell methodology v7.2, §6.10, App. F)
2 capture:           capacity at $100-$100k yes (the promoted names are mid caps around the #1,000 breakpoint; our orders are tiny against a volume spike of about 12×); hold 20 sessions fits patience yes; execution needed: held (market DAY buy at the Monday open after implementation, sell 20 sessions later); a closing-auction buy at the implementation close itself would catch more and is q6 (CLS, self-imposed, paper fills unproven)
3 cost:              turnover: one round trip per name per review (1 to 2 reviews a year, about 0.1 to 0.2/month as a book share); cost/side +8.8 / +24.2 bp for names over $200M; **unmeasured ($50–200M)** for the many promoted names under $200M median dollar volume; plausible gross **+2.0% over 20 sessions** (Dimensional 2014-2023, value-weighted, against the R2000; a post-publication window, so not haircut again; +1.0% if a second haircut is applied for the 2025 note); margin 11× break-even at arrival, 4× at the open (+2.0%); 5.7× and 2.1× at +1.0%. In the $50–200M band, the 2026-10-28 audit's buys against the open (+51 bp, not under the rung-1 rule) would leave about 1.75× at +1.0%, so the verdict turns on the unmeasured band
4 data:              timestamp: rank-day close (last business day of April or October) for the predicted list; implementation date from App. F. Russell membership history **not held**: it can be rebuilt approximately from `prices_daily` close × `facts` `EntityCommonStockSharesOutstanding` (its `known_at`) on rank day, `listings.exchange`, and the band, which needs last year's membership (a recursive rebuild with an error near the breakpoint), or bought; events/yr: unknown, countable without returns from the rebuild (one date a year to 2025, two from December 2026); delisted coverage: ok for 2020+, thin before (census: 36 delisters on file for 2016-07 to 2018); history 2016+ (8 June dates, 2016 to 2023, inside the development boundary)
5 tried before:      no ledger item. Relatives: QI-19 (index addition, NOT SUPPORTED, parked: S&P additions, not Russell migrations, and the announcement leg, not the post-implementation reversal); C2 (forced selling of spin-off children, deferred: power); HO-8 (fire sales, MIXED). Published: Madhavan 2003, Chang-Hong-Liskovich 2015, Greenwood-Sammon 2025 (shrinking index effects), Dimensional 2025. Not in the CZ catalogue. No window seen here. New: the long-only reversal leg after a forced sale, on a date and list fixed by rule
6 capabilities:      bars daily held; order types market DAY held (CLS self-imposed, unproven); side long held; liquidity band ≥ $200M held, $50–200M self-imposed; cost measurement $50–200M self-imposed (unmeasured); data field Russell membership **not held** (rebuildable from held fields); history 2016+ held; held suffice for a first test: **yes** (the rung 2-O volume check below uses held bars and facts)
7 kill decidable:    sd of a mid cap's 20-session excess return assumed 12% (about 43% a year); n = (12/2)² = 36 at +2.0%, 144 at +1.0%. Clustering: 8 dates with ρ assumed 0.1 against size-matched peers give at most about 10 effective events per date, about 60 to 80 in all; **yes at +2.0%** (marginal), deferred: power at +1.0%
verdict:             **blocked: cost measurement ($50–200M)**; data field (Russell membership, rebuildable)   ·   killable without a return read: **yes**. A rung 2-O check: on the 8 implementation dates, our rebuilt promoted names should show the published volume spike (about 12× the volume of 20 sessions earlier) and size-matched stayers should not. No spike means the rebuild is wrong, and the card dies before any return is read
frozen:              -
looks:               k = campaign 0 from source C (no card read a return; source B's count is added at selection) + prior p (counted at selection from the ledger Items table, `trials`, `research_runs` and the desk reads since 2026-10-30); batch denominator 17 (source C); lower bound
fast look:           none (pilot: no fast look before the selection)
review:              -   ·   ledger: FC-1   ·   strict tier: -

### FC-2  Nasdaq-100 trackers must buy the names that the December reconstitution rule selects, and the list can be predicted from the November reference date
source: C (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotc)
1 persists because:  inefficiency: index funds tracking the Nasdaq-100 must buy additions and sell deletions at the effective-date close (after the 3rd Friday of December), whatever the price; the selection is mechanical (top 75 by full market cap, then the 100/125 buffer) on data known at the reference date, the last trading day of November (Nasdaq-100 methodology 2026, steps 1 to 4 and schedule)
2 capture:           capacity at $100-$100k yes (large caps); hold about 15 sessions (from the open after the reference date to the open after the effective date) fits patience yes; execution needed: held (market DAY at both ends)
3 cost:              turnover: one round trip per added name a year; cost/side +8.8 / +24.2 bp at ≥ $200M (the additions are large caps); plausible gross ≤ **+1.0%** over the window (assumption: Greenwood-Sammon find the Nasdaq-100 effect shrank with the others, to under 1% around the announcement in the 2010s; no verified figure for the pre-announcement window); margin ≤ 5.7× break-even at arrival, ≤ 2.1× at the open
4 data:              timestamp: reference-date close; `listings.exchange` (point-in-time) held; ICB industry not held (`classifications.sic` as a proxy for "Financials"); full market cap from `facts` shares × close, but ADR depositary-share counts not held; events/yr about 3 to 6 additions (3 in 2024, 6 in 2025) and as many deletions; delisted coverage: n/a (large caps); history 2016+ (8 December dates), each year's rule version pinned first (the 2021 redline shows the reference dates changed)
5 tried before:      no ledger item. Relatives: QI-19 (index addition, NOT SUPPORTED, parked); Greenwood-Sammon (Nasdaq-100 included); Biktimirov & Xu 2019 (first-time additions permanent, repeat additions and deletions temporary). Not in the CZ catalogue. No window seen. New: a rule-predicted list bought before the announcement, not the announcement reaction
6 capabilities:      bars daily held; order types market DAY held; side long held; liquidity band ≥ $200M held; cost measurement ≥ $200M held; data field ICB classification not held (SIC proxy held), ADR share counts not held; history 2016+ held; held suffice for a first test: yes (a count of the predicted adds against the published adds, with no returns)
7 kill decidable:    sd of a large cap's 15-session excess return assumed 8%; n = (8/1)² = 64 at +1.0%. Raw additions 2016 to 2023: about 40 on 8 dates; with ρ assumed 0.1 and about 5 names a date, about 3.5 effective a date, about 28 in all; **deferred: power**
verdict:             **deferred: power** (about 28 effective events against 64 needed at a plausible ≤ +1.0%)   ·   killable without a return read: yes (the power screen fails before any read)
frozen:              -
looks:               k = campaign 0 from source C + prior p (counted at selection); batch denominator 17; lower bound
fast look:           none
review:              -   ·   ledger: FC-2   ·   strict tier: -

### FC-3  Balanced, pension and target-date funds sell equities at month end after stocks beat bonds, and the market dips the next day
source: C (2026-11-02)   ·   written: 2026-11-02   ·   by: agent (pilotc)
1 persists because:  constraint: mandates fix a stock/bond mix and rebalance on a calendar or a threshold, whatever the price; the trade is foreseeable from the month-to-date stock-versus-bond return (Harvey, Mazzoleni & Melone, NBER w33554, 2025)
2 capture:           capacity at $100-$100k yes (SPY); hold: out of SPY for about 1 to 2 sessions in months when the signal says "sell", in SPY otherwise; fits patience yes; execution needed: held (market DAY sell at the open of the last session or two, buy back at the open after); a long-only book can use the flow only by stepping aside, so the gain is the avoided dip minus the T-bill it earns instead
3 cost:              turnover: one round trip in signal months (at most 12 a year); cost/side +8.8 / +24.2 bp (SPY is in the ≥ $200M band; the rule's figure was measured on single stocks, and SPY's own cost is unmeasured and probably lower); plausible gross about 17 bp per event (the paper's next-day figure) × 0.42 after the QI-2 haircut ≈ **7 bp**; margin **0.4× break-even** (17.6 bp)
4 data:              timestamp: month-to-date closes; SPY bars held (the benchmark); a bond return series (an aggregate or Treasury ETF's bars) is UNVERIFIED in the store; the T-bill rate held (`rates`); events/yr 12 (calendar), fewer above a threshold; delisted coverage: n/a; history 2016+ (96 months to 2023)
5 tried before:      no ledger item. Relatives: B2 (trend-filter overlay, parked: a higher Sharpe at a lower return is not accepted, owner #659), which applies if stepping aside lowers the return; FC-4 (same days, different holders). Published in 2025: no post-publication window. No window seen. New: a mandate-driven sale with a conditional direction
6 capabilities:      bars daily held; order types market DAY held; side long held; instruments US equities (an ETF) held; cost measurement for an ETF not measured separately (the ≥ $200M band figure applies under the rule); data field bond index bars UNVERIFIED; history 2016+ held; held suffice for a first test: no (a bond series is needed, and the only first test is a return read)
7 kill decidable:    daily market sd about 1.1%; n = (110/7)² ≈ 250 against 96 months (2016 to 2023); deferred: power on the store window (a pre-2016 open-data read would have the count)
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
4 data:              timestamp: the trading calendar and the settlement rule in force each month (rule dates above); SPY bars held; events/yr 12; delisted coverage: n/a; history 2016+ (96 months to 2023, across the T+2 change; the T+1 change of 2024 falls after the development boundary)
5 tried before:      no ledger item. Relatives: FC-3 (same days); the turn-of-the-month effect (Ogden 1990; McConnell & Xu 2008) is the buying half of the same cycle; B2's objective rule as in FC-3. Published 2020; no post-publication read here. No window seen. New: the settlement rule dates the flow, and the rule changed twice, which predicts that the window moves
6 capabilities:      bars daily held; order types market DAY held; side long held; instruments US equities (an ETF) held; cost measurement for an ETF not measured separately; history 2016+ held; held suffice for a first test: yes (SPY bars, a return read)
7 kill decidable:    5-session market sd about 2.5%; n = (250/12)² ≈ 430 against 96 months; deferred: power on the store window
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
4 data:              timestamp: the trading calendar; the prior-year loss measured from held closes (a signal input, read to rank names, not a return outcome); events/yr: one date, hundreds of names; delisted coverage: thin before 2020 (census), and losers delist more often; history 2016+ (8 Januaries to 2023, the January 2024 outcome falling past the boundary)
5 tried before:      no ledger item. Relatives in the CZ catalogue: `MomSeason`, `MomSeasonShort` and the other Heston-Sadka seasonality signals (calendar seasonality, not tax selling), and the momentum family's January losses. No window seen here. New: the date rule written from the tax code
6 capabilities:      bars daily held; order types market DAY held; side long held; liquidity band < $200M self-imposed; cost measurement < $200M self-imposed (unmeasured); history 2016+ held; held suffice for a first test: no (the band that carries the effect has no cost figure)
7 kill decidable:    one date a year with a strongly shared small-cap January move: with ρ assumed 0.15, at most about 7 effective events a date, about 50 over 8 years; sd of a small cap's 20-session excess return assumed 15%; n = (15/1)² = 225 at +1.0%; **deferred: power**
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
4 data:              timestamp: the 424B4 acceptance (`filing_index`, held) gives the prospectus date; the lock-up length and early-release clauses are in the prospectus text, **parse-only** (not stored); the first bar gives the listing date; events/yr about 100 to 300 operating-company IPOs (excluding SPACs; 2020-2021 heavy); delisted coverage: thin before 2020 (census), and young firms delist often; history 2016+
5 tried before:      no ledger item. Relatives in the CZ catalogue: `IndIPO`, `AgeIPO` (IPO long-run underperformance, a slower and different effect); B11 and the beyond-long-only notes (LS claims) on shorting cost. No window seen here. New: the contractual supply date
6 capabilities:      side short (paper) self-imposed (ADR 0024 allows it; no book exists); short live `charter (ADR)`; bars daily held; order types market DAY held; cost measurement for the short side and borrow self-imposed (unmeasured); data field lock-up terms parse-only; history 2016+ held; held suffice for a first test: **yes** (the rung 2-O volume check below uses held bars and the filing index)
7 kill decidable:    sd of a recent IPO's 3-session excess return assumed 8%; n = (8/0.6)² ≈ 180; raw events 2016 to 2023 about 1,000 on many different dates (low clustering); **yes**
verdict:             **blocked: side short (paper)**; cost measurement (borrow, small IPOs); data field (lock-up terms, parse-only)   ·   killable without a return read: **yes**. A rung 2-O check: on day 180 after each held 424B4 acceptance, volume should rise by about the published 40% against the prior 20 sessions; no rise means the day-180 rule (or early releases) does not date the flow, and the card dies before any return is read
frozen:              -
looks:               k = campaign 0 from source C + prior p (counted at selection); batch denominator 17; lower bound
fast look:           none
review:              -   ·   ledger: FC-6   ·   strict tier: -
