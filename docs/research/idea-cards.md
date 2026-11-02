# Idea cards

**Status:** living (class A)  ·  **Spec:** [idea pipeline](../specs/idea-pipeline.md) (#2896), requirements 1 to 7 and "Data / interfaces"  ·  **Ledger:** the [ladder ledger](hypothesis-backlog.md#ladder-ledger) (one row per card that answered question 3; one batch row per mining pass)

Every idea enters as a card from a named source, written before any return is read for it. This file holds the tracking table, the capability tally, the vocabulary, the card shape, and then the cards, one section per source pass, newest first. A card's verdict is the filter's, not a selection: selection happens after the independent review (spec requirement 7, steps 2 and 4).

## Tracking table (pilot, 2026-11)

Counts only, no minimum on any row. Source C's column is filled by its own pass (#2907); the orchestrator keeps the sum.

| Measure | Source B (#2908) | Source C (#2907) |
|---|---|---|
| Ideas reviewed (field × event pairs, or flows) | 33 | pending |
| Cards written | 5 | pending |
| One-line rejects | 28 | pending |
| `rejected` cards, by question | 2 (question 3) | pending |
| `deferred` cards, by reason | 0 | pending |
| `blocked` cards, by capability | 3 (data field: later statement vintages; data field: deal filings and terms; data field: 8-K items) | pending |
| `admit` cards | 0 | pending |
| Admitted (`admit` + `blocked`) killable without a return read | 3 of 3 | pending |
| Forwarded to the strict tier | 0 (selection follows the review) | pending |
| Killed by a fast look | n/a (no fast look runs in the pilot before selection) | pending |
| Strict-tier verdicts, `TP-` claims written | 0, 0 | pending |
| Agent-days spent | about 1 | pending |

## Capability tally

Admitted `blocked` cards per missing capability, by distinct mechanism. "Past a capability-free gate" counts cards whose mechanism has a fast look on open data, an `economic` read, or a graded claim at MIXED or better.

- **data field: `statement_facts` later vintages** (parse-only): 1 admitted card (IC-B2), 1 distinct mechanism, 0 past a capability-free gate · build: M, about 2 to 3 agent-days (keep every vintage the ingest already parses and counts as `statement_restated`; a schema change and a look-ahead test) · abandoning: one mechanism (stealth revisions) that no other held field can test. IC-B2's trade also needs **side: short (paper)**.
- **data field: deal filings and offer terms** (filing-index forms not held; offer terms not held): 1 admitted card (IC-B3), 1 distinct mechanism, 0 past a capability-free gate · build: the filing-index seed is already planned (T318 to T324; T324 is an owner step, S); offer terms are a new text parse, M to L, not planned · abandoning: the one risk premium in the batch (merger arbitrage), whose payoff is cash-plus rather than an anomaly. Also needs **cost measurement ($50 to 200M)** to reach power.
- **data field: 8-K items (`filing_events`)** (code merged, 0 rows): 1 admitted card (IC-B4), 1 distinct mechanism, 0 past a capability-free gate · build: T164e's real-store run, owner, S (the same run candidate 1 waits on) · abandoning: the non-earnings negative-item timing card; the run is shared with candidate 1, so its marginal cost here is near zero. IC-B4 also needs **side: short (paper)** for the trade itself (ADR 0024 allows it; no short book exists).
- **side: short (paper)** (self-imposed: ADR 0024 allows it, no short paper book exists): needed by 2 admitted cards (IC-B2, IC-B4), 2 distinct mechanisms, 0 past a capability-free gate; neither is blocked on it alone (each waits on a data field first) · build: a short paper book, size not estimated here · abandoning: the trade side of both cards; long-only could only avoid the names.
- **cost measurement ($50 to 200M)**: no card blocked on it alone. IC-B1 fails break-even at the lowest measured cost, so measuring the band would not admit it; IC-B3 needs the band for power.

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

Ids: `IC-B<n>` for source B cards, `IC-C<n>` for source C; ledger rows `L-B<n>` and `L-C<n>`, so the two passes never renumber each other.

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
