# Research Report: Small-account structural edges (odd-lot tenders, SPACs, CEFs, small-deal merger arbitrage, rights offerings, reverse-split round-ups)

**Brief:** #3670  ·  **Date:** 2026-11-20  ·  **Status:** INCOMPLETE. Every candidate is graded, but CVR-bearing deals were not searched and Schwab's voluntary-action fee was not found (see Caveats). Every web page, Alpaca's docs included, was read only through search-engine summaries, because the session's fetch tool could not reach docs.alpaca.markets or oddlotarbitrage.com. So every figure below is **UNVERIFIED** at the page level unless marked otherwise.  ·  **Agent/model:** team smalledge; desk research only, 27 web searches, no store read, no return computed, no order

## Answer
**Verdict:** INSUFFICIENT for every candidate as a post-2015 net edge, and MIXED for small-deal merger arbitrage. No candidate reaches SUPPORTED.  ·  **Confidence:** medium on the broker findings and the "dead or closing" calls; low on every dollar figure (practitioner tallies and my own arithmetic)

1. **Only one candidate fits the system you have.** That is small-deal cash merger arbitrage. When a cash merger closes, the payout is mandatory, so the broker turns the shares into cash with no election and no fee. Every other candidate needs a *voluntary* corporate-action election: tendering shares, redeeming a SPAC, or exercising rights.
2. **Alpaca makes voluntary actions uneconomic at this size.** According to search summaries of Alpaca's documentation, Alpaca charges **$100 per client per voluntary corporate action**. A third-party broker built on Alpaca says the same. Alpaca's own FAQ adds that the cost "usually discourages customers from acting". The typical odd-lot tender earns about $120 to $495 per event, so the fee takes most of it. Fidelity's commissions page lists **$0** for voluntary reorganizations, and Interactive Brokers lists "All Other (Mandatory, Voluntary…)" corporate actions as free. Schwab's figure was not found.
3. **Alpaca paper trading cannot simulate any of these trades.** According to Alpaca staff on its forum, the paper environment does not process corporate actions at all, not even splits or dividends. A paper book therefore never receives a tender payout, a SPAC redemption or a merger cash-out. As built, the repo's forward exam (ADR 0016) cannot judge any of these candidates. It also scores monthly excess returns over SPY, and a book that sits mostly in cash cannot pass that.
4. **The best small-capacity lead is odd-lot priority in issuer tenders and split-off exchange offers.** It is genuinely too small for institutions: at most 99 shares per account. Practitioner tallies (Tier 3) put it at about **1 to 5 profitable deals a year**, worth about **$285 each on average** ($120 to $495). That gives roughly **$300 to $1,400 a year gross** at a $10,000 account, before taxes and fees, and close to $0 at $1,000, since most deals need $2,000 to $10,000 of stock. No academic study of the returns exists. The protection is real in the primary filings: in 3M's 2022 split-off, odd-lot tenders were accepted in full while everyone else was prorated to 7.35%. But it fails in documented ways: issuers dropped the priority before expiry (Frontera, Blue Bird), a broker prorated a 99-share holder (a J&J exchange-offer anecdote), and brokers set early internal deadlines. In practice it is **a manual process at Fidelity or IBKR, a few times a year, not a system**.
5. **The reverse-split round-up is the purest small-account trade, and it is closing.** The industry is moving against it. SIFMA (the brokers' trade body) asked NYSE and Nasdaq in March 2025 to end round-ups. In December 2025 it published a white paper proposing cash-in-lieu as the default, calling round-up arbitrage abuse. Upexi's 2024 round-up drew a market-manipulation complaint. **At Alpaca it probably never worked:** since August 2021 Alpaca has paid out fractional shares in splits, so a client is likely credited a fraction rather than a rounded-up share (UNVERIFIED for round-ups specifically).
6. **SPAC trust-value trades, CEF discount trades and rights offerings are not small-capacity edges.** SPAC IPOs and redemptions are dominated by institutional arbitrage funds, and the payoff is close to the T-bill rate. A redemption also needs a voluntary election. CEF discount reversion is a cross-sectional anomaly with institutional activists on the same side: Saba, Karpus and Bulldog, per ICI 2025. The published long-short is gross and needs a short leg. US rights offerings are rare and mostly come from closed-end funds. The best study of those finds fund premiums *fall* during the offering (Khorana, Wahal and Zenner 2002), which is evidence against participating.
7. **Merger arbitrage is real but small, and it is a risk premium, not free money.** The longest live record found is The Merger Fund: **3.90% a year over the 10 years to 2026-03-31**, with a 3-year beta of 0.02. That is after fund fees and roughly 2 percentage points a year over T-bills. Academic evidence that small deals pay more is pre-1997 (Baker and Savasoglu 2002). The repo already carries this as idea card IC-B3, blocked on the cost of names under $50M and on deal-terms data. By my arithmetic, expected excess over T-bills at $10,000 is about **$50 to $200 a year after costs**, before tax, with losses clustered in crashes.

**One line:** none of the six is a workable *system* edge on Alpaca. Odd-lot tenders and split-off odd-lot carve-outs are the only small-capacity trade still alive in 2026, and they are a manual, few-times-a-year process at a $0-fee broker worth a few hundred dollars a year at $10,000. Merger arbitrage is the only candidate Alpaca and the repo can run, and its post-2015 evidence is a thin T-bill-plus premium.

## Per-candidate table

| Candidate | Who is on the other side; why institutions cannot take it | Best post-2015 net evidence (tier) | Grade | Dead or closing? Since when | Events/yr (US) × $ per event at our size | Expected net $/yr at $1k / $10k (my arithmetic, UNVERIFIED inputs) | Alpaca feasible? | Paper / forward exam |
|---|---|---|---|---|---|---|---|---|
| (a) Odd-lot priority in issuer tenders, Dutch auctions, split-off exchange offers | Issuers buy odd lots first to cut holder-servicing costs (Rule 13e-4 permits it). Capacity is 99 shares per beneficial owner, worthless to a fund | Practitioner tallies only (Tier 3): 1 to 5 deals/yr, about $285 avg ($120 to $495); a 2025 Millrose blogger reports net $890.54. Mechanism confirmed in primary results filings (Tier 1): 3M 2022, Pfizer 2013, BMS 2009 | INSUFFICIENT (no return study) | Alive. Failure modes: priority dropped (Frontera, Blue Bird), broker prorates odd lots (J&J anecdote), early broker cutoffs | 1 to 5 × about $120 to $495 (needs about $2k to $10k per event, for weeks) | $1k: about $0 to $150 (few deals fit). $10k: about $300 to $1,400 gross at a $0-fee broker; about $0 to $900 at Alpaca after $100 a deal | **No in practice**: $100 per election | Paper cannot simulate it. Judge per event, not by monthly SPY excess |
| (b) SPAC below trust before redemption or liquidation | SPAC arbitrage funds hold most IPO units; a retail holder is their competitor, not their counterparty | Gahng, Ritter and Zhang, RFS 2023 (Tier 1): high IPO-investor returns, mostly pre-2021, "recent deals are likely to disappoint". No post-2022 net study found | INSUFFICIENT | Excess over T-bills largely gone after 2022; pre-deal yields reported at about 4.5 to 5.0% (Tier 3), roughly the T-bill rate | Dozens of trusts; the capacity is not small | About $0 excess at either size; negative after a $100 redemption fee | No in practice (redemption is a voluntary election) | Paper cannot redeem |
| (c) CEF discount reversion; CEF tenders, liquidations, open-endings | Discount buyers include Saba, Karpus, Bulldog (Tier 2: ICI 2025). Institutions take it, so it is not small-capacity | Wu, Piccotti and Patro, JFR 2017 (Tier 1, via a CXO summary, UNVERIFIED): gross long-short 14.9%/yr; net about 1%/yr over benchmark at a 0.67% spread (CXO's estimate). Needs a short leg | MIXED (gross, older sample, crowded, short leg) | Crowded by activists; a Supreme Court ruling against Saba on §47(b) private actions (Tier 3 report, date not verified) narrows one activist tool | About 420 US CEFs; tenders are prorated | Not estimated: no post-2015 net long-only figure found | Discount buying yes; tender participation $100 an election | Paper books the buy, not the tender |
| (d) Small-deal cash merger arbitrage | Target holders who want out sell to limited arbitrage capital; deals under about $1bn fall outside many institutional universes (an arXiv 2026 paper excludes them) | The Merger Fund (Tier 2 live record): 3.90%/yr over 10 yrs to 2026-03, beta 0.02. Verdad (Tier 2): about 40 qualifying deals/yr, 89% close, +2.0% when they close, −2.8% when they break (UNVERIFIED sample). Size premium only pre-1997 (Baker and Savasoglu, JFE 2002, Tier 1) | MIXED | Spreads shrank more than 400 bp from 2002 (Jetley and Ji, FAJ, Tier 1; sample ends 2007) | About 40 cash deals/yr; $10k spread over 5 to 10 deals | $1k: about $5 to $20. $10k: about $50 to $200 excess over T-bills after 25 to 60 bp entry cost | **Yes**: the cash-out is mandatory; a two-step tender is followed by a squeeze-out at the same price | Paper can buy; the cash-out must be booked by hand (paper skips corporate actions) |
| (e) Rights offerings, oversubscription privilege | US rights offerings are rare, mostly CEFs and small distressed issuers; the sponsor's broker-dealer is often the soliciting party | Khorana, Wahal and Zenner, JFQA 2002 (Tier 1, 1988 to 1998): the CEF premium turns into a discount during the offering | INSUFFICIENT (no post-2015 study; old evidence points against) | No sign it was ever a retail edge | A handful a year (not counted) | About $0 | No in practice ($100 an election; rights subscription is a voluntary action) | Paper cannot exercise |
| (f) Reverse-split round-up, cash-in-lieu | The issuer pays for the round-up shares. Capacity is one share per account; institutions cannot scale it | None (Tier 3 only). SIFMA (Tier 2, industry): reverse splits up 191% from 2023 to 2024 | INSUFFICIENT | **Closing**: SIFMA letter March 2025, white paper Dec 2025 (cash-in-lieu default); Upexi manipulation complaint; Nasdaq notice-rule changes 2023 to 2024 | Hundreds of reverse splits a year; tens to low hundreds of $ per event per account (UNVERIFIED) | At Alpaca about $0 (fractions credited, not rounded up, UNVERIFIED) | Probably no (fractional shares paid out since Aug 2021) | Paper ignores splits |
| (g) Going-private reverse-split cash-outs | Controllers cash out small holders, sometimes without a premium | None found | INSUFFICIENT | n/a | Rare | Not estimated | Mandatory cash; feasible | n/a |
| (g) CVR-bearing deals | Not searched | — | not graded | — | — | — | — | — |

**Ranking** (positions in each column; ties broken by the next column):

| Rank | (i) Strength of post-2015 net evidence | (ii) Expected net $/yr at $10,000 | (iii) How soon a first forward observation |
|---|---|---|---|
| 1 | (d) merger arb, MIXED (live fund record) | (a) odd-lot tenders, about $300 to $1,400 at a $0-fee broker | (d) merger arb: open deals exist today |
| 2 | (c) CEF discount, MIXED (gross, older) | (d) merger arb, about $50 to $200 | (a) odd-lot: the next qualifying tender, likely within 1 to 6 months |
| 3 | (a) odd-lot, INSUFFICIENT but primary-source mechanism | (f) round-up, unknown and about $0 at Alpaca | (c) CEF: discount data needed first |
| 4 | (b) SPAC, INSUFFICIENT | (c) CEF, not estimated | (f) round-up: weekly events, but not at Alpaca |
| 5 | (f) round-up, (e) rights, (g) | (b), (e), about $0 | (b), (e): need a voluntary election |

## Evidence

### Broker feasibility (the question that can kill an idea outright)
| Claim | Source | Tier | Key figure | Read how |
|---|---|---|---|---|
| Alpaca charges for voluntary corporate actions | Alpaca docs, "Voluntary Corporate Actions" | 1 (vendor doc) | "The cost is $100 per client per voluntary corporate action, which is stated in the email" | Search summary only; fetch blocked. **UNVERIFIED** |
| The same fee, seen by a downstream broker | HeyGoTrade help, "Voluntary corporate action" | 3 | "USD 100 service fee … per voluntary action" for rights and warrant elections via Alpaca | Search summary. **UNVERIFIED** |
| Alpaca expects the fee to deter elections | Alpaca docs FAQ | 1 | Voluntary notices cannot be blocked; "the cost attached to them usually discourages customers from acting" | Search summary. **UNVERIFIED** |
| Alpaca processes tenders, Dutch auctions, exchange offers, rights and warrant elections | Alpaca docs; changelog 2026-05-05 | 1 | Contra/placeholder security, then DTC allocation "usually … a week or two"; new activity types for tender offers, exchange offers, rights subscription and warrant exercise elections (2026-05-05) | Search summary. **UNVERIFIED** |
| Alpaca may reject an election | Alpaca docs | 1 | "Alpaca can reject a customer's decision to act if there is no cash in the account" | Search summary. **UNVERIFIED** |
| Alpaca pays out fractional shares in splits | Alpaca blog, "Updated terms for payments in fractional shares" | 1 (broker) | Will "begin paying out fractional shares in the event of a stock split, effective August 2nd, 2021" | Search summary. **UNVERIFIED**; a second Alpaca post on removing cash-in-lieu was seen but not dated |
| Alpaca paper does not process corporate actions | Alpaca forum, staff reply | 3 (forum, staff) | The paper environment "doesn't implement corporate actions including stock splits"; also "stock splits or dividends" | Search summary. **UNVERIFIED** |
| Fidelity voluntary reorganizations are $0 | Fidelity "Commissions, Margin Rates, and Fees" | 1 (broker) | $0 per transaction for voluntary corporate reorganizations; an older undated BrokerageLink schedule shows $38 | Search summary. **UNVERIFIED** |
| IBKR voluntary corporate actions are free | IBKR "Other Fees" | 1 (broker) | "All Other (Mandatory, Voluntary, With Options, etc)" free; dissenter/appraisal requests USD 500 | Search summary. **UNVERIFIED** |
| IBKR supports Dutch-auction elections at several prices | IBKR KB "Voluntary Corporate Action tool" | 1 (broker) | Election rows per bid price; non-electing shares get the default | Search summary |
| Schwab's fee | Schwab Pricing Guide (April 2026) | 1 | **Not found**; old TD Ameritrade schedule: voluntary tender $0, mandatory reorg $38 | Search summary. **UNVERIFIED** |

### (a) Odd-lot priority
| Claim | Source | Tier | Key figure | OOS / post-pub? | Net? |
|---|---|---|---|---|---|
| Odd-lot tenders are fully accepted while others are prorated | 3M 8-K, 2022 split-off final results | 1 | Odd lots accepted in full; final proration factor 7.346065% for others | n/a (mechanism) | n/a |
| Same | Pfizer SC TO-I/A 2013; BMS SC TO-I/A 2009 | 1 | Proration 24.1520516% (Pfizer) and 53.8954842% (BMS) for non-odd-lot shares | n/a | n/a |
| Custodial odd lots can lose the preference | Halliburton exchange-offer filing, via a search summary | 1 | "if you hold an odd-lot through a custodial account… you are not entitled to this preferential treatment" | n/a | n/a. **UNVERIFIED** context |
| 1 to 5 deals a year, a few hundred dollars each | FindStox odd-lot page | 3 | "usually only 1 to 5 deals per year"; case table averages about $285 ($120 to $495) | live tally | gross |
| A 2025 split-off result | My Money Blog, Lennar/Millrose | 3 | "net profit was $890.54"; another writer saw the cap reached, so the bonus was "only … about 2-3% vs the anticipated 6-7%" | live | blogger's net |
| An academic return claim | KorroAi "oddly" PAPER.md (GitHub) | 3 | Attributes a 3 to 5% small-holder edge to Larcker and Lys (1987). That paper is about risk-arbitrage information costs, not odd lots, so the attribution looks wrong | — | **Not evidence; UNVERIFIED** |

### (b) SPACs
| Claim | Source | Tier | Key figure | Post-pub? | Net? |
|---|---|---|---|---|---|
| SPAC IPO investors earned high returns, but recent deals likely disappoint | Gahng, Ritter and Zhang, "SPACs", RFS 36(9) 2023, 3463–3501 | 1 | Working paper 15.9%/yr; an NBER talk 12.0%/yr (versions differ) | Sample mostly pre-2021 | Gross. **UNVERIFIED** (abstract and secondary) |
| Redemptions stay very high | Houlihan Capital Q1 2025 SPAC report | 2 | Median redemption 91.7% in Q1 2025, against a 3-yr median of 95.9% | — | — |
| Most pre-deal SPACs trade near current redemption value | AUM Advisors, De-SPAC Scorecard H1 2026 | 3 | Against current redemption value "just 3 of 47" trade more than 5% above trust | — | — |
| Pre-deal yields about the T-bill rate | Advisory piece (search summary) | 3 | 4.5 to 5.0% annualized, unsourced | — | **UNVERIFIED** |

### (c) Closed-end funds
| Claim | Source | Tier | Key figure | Post-pub? | Net? |
|---|---|---|---|---|---|
| Discount-sorted long-short earns a gross premium | Wu, Piccotti and Patro, J. Financial Research, June 2017, via CXO Advisory | 1 via 2 | Q5−Q1 gross 14.9%/yr, Sharpe 1.5; model alpha 17.4% gross; CXO's estimate about 1%/yr net over the benchmark at a 0.67% spread | in-sample | partial. **UNVERIFIED** |
| CEF activism is concentrated in three firms | ICI, "Closed-End Fund Activism" (2025) | 2 (industry body, an interested party) | "almost the exclusive domain of three activist firms—Saba …, Karpus …, and Bulldog" | — | — |
| Discounts are wider than their long-run average | Bloomberg, via Yahoo Finance | 2 | Average discount about 6.5% against a 25-yr average of 4.5%; more than 420 US CEFs | — | — |

### (d) Merger arbitrage
| Claim | Source | Tier | Key figure | Post-pub? | Net? |
|---|---|---|---|---|---|
| Live merger-arbitrage fund return | The Merger Fund (MERFX), Zacks and PortfoliosLab | 2 | 10-yr 3.90% total return to 2026-03-31; 3-yr beta 0.02 | live, 2016–2026 | net of fund fees, before tax |
| Spreads shrank | Jetley and Ji, "The Shrinking Merger Arbitrage Spread", FAJ | 1 | Spread "declined by more than 400 bps since 2002"; sample 1990–2007 | partly | gross |
| Returns rise with target size and completion risk | Baker and Savasoglu, JFE 64 (2002) | 1 | 0.6–0.9%/month abnormal, 1,901 positions, 1981–1996 | pre-2015 only | gross |
| Recent deal statistics | Verdad, "Merger Arbitrage" | 2 | About 40 deals/yr; 89% close; +2.0% on success, −2.8% on breaks | unclear sample | **UNVERIFIED** (the break figure looks small against typical 20 to 40% break losses) |
| Payoff is like selling puts | Mitchell and Pulvino (2001), via Alpha Architect | 1 via 2 | 50 bp/month excess, beta 0 in normal markets, 0.50 in down markets | pre-2001 | partial. **UNVERIFIED** |

### (e) Rights offerings
| Claim | Source | Tier | Key figure |
|---|---|---|---|
| CEF rights offerings turn a premium into a discount, worse with conflicted advisers | Khorana, Wahal and Zenner, JFQA 37(2) 2002, 177–200 | 1 | 120 offerings, 1988–1998. Abstract only: **UNVERIFIED** |
| Oversubscription is a request, not an allocation | Dividend Vision guide | 3 | — |

### (f) Reverse-split round-ups
| Claim | Source | Tier | Key figure |
|---|---|---|---|
| Reverse splits surged; round-ups attract arbitrage; exchanges asked to end them | SIFMA letter to NYSE and Nasdaq, 2025-03-25 | 2 (industry body, an interested party) | "191% increase in reverse stock splits among exchange-listed issuers" from 2023 to 2024 |
| Cash-in-lieu proposed as the default "to eliminate arbitrage opportunities" | SIFMA white paper, 2025-12-03 | 2 | Upexi 2024 1-for-20: 202,183 round-up shares; a manipulation complaint |
| Round-up requests flow through DTC by participant | American Rebel split notice, 2025 (Barchart) | 1 (issuer release) | DTC collects requests by participant; about 4 business days later it asks for the total |
| Robinhood passes issuer round-ups | Robinhood corporate actions tracker | 1 (broker) | "long positions receive 1 additional share" when the action rounds |

No exchange rule banning round-ups was found as of the search date. Nasdaq's reverse-split notice and disclosure rules (SR-NASDAQ-2023-025, SR-NASDAQ-2024-010) were seen as titles only.

### Point-in-time data (question 4)
- **What carries the terms on EDGAR.** SC TO-I and SC TO-I/A carry issuer tenders and Dutch auctions, and the odd-lot clause sits in the Offer to Purchase, exhibit (a)(1)(A). The final proration is in the last SC TO-I/A or an 8-K item 8.01. SC TO-T is for third-party tenders, DEFM14A for merger proxies, SC 13E3 for going-private deals, and 8-K items 1.01 and 2.01 for mergers. 424B prospectuses carry rights offerings. For reverse splits, 8-K item 5.03 or 3.03 and DEF 14A carry ratio and fractional treatment, and the treatment is often only in a press release. Form 25 marks delistings.
- **The catch.** Every one of these is free with an acceptance timestamp. But the field that matters (odd-lot clause, price range, ratio, fractional treatment) is free text, not structured data.
- **CEF NAVs.** N-PORT gives monthly holdings and NAV, filed with a lag and made public only quarterly, so it is not a daily point-in-time NAV. Daily NAVs come from sponsors (UNVERIFIED as a free point-in-time history).
- **What the store holds today** (data-edge census, 2026-11-02). The all-forms `filing_index` is not seeded in that copy, `filing_events` (8-K items) has 0 rows, and deal terms are not held. Form 25 rows exist from 2006 (thin before 2019). Corporate actions come from the broker feed with no declaration date. So none of these candidates can be screened from the store today. Once seeded, the index would give the event clock, and the terms would still need a parser or a hand read.

### How to judge few events honestly (question 6)
- **Why ADR 0016's exam does not fit.** Its forward return exam (amended 2026-11-06 and 2026-11-07) reads monthly net excess returns over the frozen benchmark: SPY unless the finalist's file names another. A book passes at a look only if the mean and the alpha t-statistics both reach 4.762 / 3.367 / 2.750 at 12 / 24 / 36 months. It also needs ADR 0023's minimum fills, has a futility stop at month 6, and allows 4 exams in total.
- **Why that rules these candidates out.** A book that is mostly cash, with 1 to 5 odd-lot events a year or 5 to 10 merger positions:
  - lags SPY in a rising market, so leg (i) fails unless the file names T-bills as the benchmark;
  - cannot produce tender or cash-out fills on Alpaca paper (finding 3), so the exam would be void;
  - spends one of the four exam slots on a few-hundred-dollar question.
- **A form that could judge them** (a description, not a recommendation):
  - Pre-register each event's predicted dollar outcome from the filing alone, at the filing's acceptance time. Use the tender price, the odd-lot clause and the worst-case proration.
  - Record the realised outcome from the broker statement.
  - Judge on two counts: a calibration count (how often the clause held, and how often the broker delivered) and the summed dollars against the summed predictions.
  - For merger arbitrage, a pre-registered list of every qualifying cash deal from the seeded index can be marked to market forward with zero discretion. That is the IC-B3 card's route.
- **How long it takes.** With 1 to 5 odd-lot events a year, a failure rate of about 10% (one bad event in ten) needs years to estimate. Whether to adopt such a standard is the owner's decision, not this report's.

## Disconfirmation
- **Searches run** (27 in total), aimed at evidence against:
  - "odd lot tender offer arbitrage 2024 2025 broker rejected odd lot priority dead";
  - SPAC returns after 2022 and yield compression;
  - CEF activism crowding and Saba;
  - post-2010 merger-arbitrage spread decline and small-deal studies (two searches);
  - rights-offering price effects;
  - reverse-split round-up changes 2023–2025, exchange rule filings, and "still working in 2026";
  - Alpaca's voluntary and mandatory corporate actions, fractional shares and paper limits;
  - IBKR, Fidelity and Schwab fee schedules.
- **What was found against:**
  - (a) Issuers withdrew the odd-lot preference before expiry (Frontera; Blue Bird "a couple weeks before the deal was scheduled to close"). A street-name 99-share holder in a J&J offer reported proration to about 23% (one blog comment, anecdote). Millrose's cap made the "bonus" 2–3% instead of 6–7%. Brokers set internal cutoffs a day or more before expiry (Fidelity, Atmus 2024). Custodial odd lots may not qualify (Halliburton filing).
  - (b) Gahng, Ritter and Zhang's own warning that recent deals will disappoint. Redemption rates above 90% since 2022. Few pre-deal SPACs above current trust value.
  - (c) An activist oligopoly on the same side of the trade. The gross result needs a short leg, and a CXO estimate cuts the net to about 1%/yr. An earlier University of Georgia study (title only) concludes mechanical CEF strategies do not earn excess returns.
  - (d) Spreads down more than 400 bp from 2002. The live fund record is about T-bills plus 2 points. The size premium has no post-2015 test.
  - (e) Old evidence that CEF rights offerings lower the price.
  - (f) SIFMA's campaign (March and December 2025), the Upexi complaint, and broker statements about bans. Alpaca's fractional payout.
- **What was not found:** any peer-reviewed post-2015 return study of odd-lot tenders, SPAC arbitrage, round-ups or rights offerings; any exchange rule actually banning round-ups.

## Caveats & gaps
- **Fetch blocked.** WebFetch could not resolve docs.alpaca.markets or oddlotarbitrage.com in this session, so I stopped trying other hosts, as instructed. Every quote here comes from a search engine's summary of the page. The Alpaca $100 fee and the paper-trading limit should be confirmed by reading the pages, or by asking Alpaca support, before anything relies on them. docs.alpaca.markets would have to be allowed in the session's network settings for a re-read.
- **Not searched:** CVR-bearing deals (budget).
- **Not found:** Schwab's voluntary-action fee. Exact Alpaca handling of an issuer round-up, and whether Alpaca charges $100 for a SPAC redemption, are also unknown.
- **The dollar figures are my own arithmetic** on Tier 3 inputs:
  - (a) is 1 to 5 events × $120–$495, less $100 per event at Alpaca.
  - (d) is 3 to 4%/yr gross excess on $10,000, less 4 turns × 25–60 bp on entry (no exit sell).
  - Both are before tax. Gains are mostly short-term and taxed as ordinary income.
- **Event counts are not from EDGAR.** No SC TO-I count was drawn, and the store's index is not seeded. A count of SC TO-I filings with "odd lot" in exhibit (a)(1)(A) for 2016–2026 would replace the Tier 3 "1 to 5" figure.
- **Practitioner tallies are survivor-biased.** Blogs post the wins.

## UNVERIFIED items
- Every Alpaca, Fidelity, IBKR and Robinhood statement above: read through search summaries only.
- Gahng, Ritter and Zhang return figures (versions differ, 15.9% against 12.0%).
- Wu, Piccotti and Patro 2017 figures: CXO secondary.
- Khorana, Wahal and Zenner 2002: abstract only.
- Mitchell and Pulvino: via Alpha Architect.
- Verdad's −2.8% break loss.
- FindStox's 1 to 5 deals and $285 average.
- The Frontera, Blue Bird and J&J failure cases.
- The Halliburton custodial-account clause.
- The 4.5 to 5.0% SPAC yield.
- The Supreme Court §47(b) ruling as reported by mutualfunds.com.
- The Nasdaq rule numbers.
- The KorroAi attribution to Larcker and Lys, which looks wrong.

## Follow-up questions (not answered here)
- Does Alpaca charge the $100 fee on SPAC redemptions and on odd-lot tenders specifically, and does it pass issuer round-ups to fractional-capable accounts? (One support ticket would answer this.)
- A count from EDGAR: SC TO-I and SC TO-I/A filings 2016–2026 whose Offer to Purchase includes odd-lot priority, and in how many the stock traded below the bottom of the range at some point during the offer.
- CVR-bearing cash deals: is the CVR typically left unpriced by arbitrage funds, given it is non-transferable?
- Schwab's current voluntary-reorganization fee.
- The tax treatment of round-up shares received (basis and income), and of odd-lot tender proceeds held under a year.
- Whether a T-bill benchmark and a per-event ledger would be acceptable under a future ADR 0016 amendment. That is the owner's decision.

## Sources
All accessed 2026-11-20, through web-search result summaries (no page fetched).
- Alpaca, Voluntary Corporate Actions: https://docs.alpaca.markets/us/docs/voluntary-corporate-actions (T1)
- Alpaca, Mandatory Corporate Actions: https://docs.alpaca.markets/us/docs/mandatory-corporate-actions (T1)
- Alpaca changelog, reverse split new_symbol, 2026-06-23: https://docs.alpaca.markets/us/v1.1/changelog/2026-06-23-reverse-split-dad1b8e (T1)
- Alpaca changelog, activity types, 2026-05-05: https://docs.alpaca.markets/us/v1.4.2/changelog/2026-05-05-activity-types-697a81d (T1)
- Alpaca blog, Updated Terms for Payments in Fractional Shares: https://alpaca.markets/blog/updated-terms-for-payments-in-fractional-shares/ (T1)
- Alpaca forum, Paper account NVDA stock split issue: https://forum.alpaca.markets/t/paper-account-nvda-stock-split-issue/14412 (T3)
- Alpaca forum, Corporate Actions Handeling: https://forum.alpaca.markets/t/corporate-actions-handeling/19325 (T3)
- Alpaca forum, Reverse stock split problem: https://forum.alpaca.markets/t/reverse-stock-split-problem/3510 (T3)
- HeyGoTrade, Voluntary corporate action: https://help.heygotrade.com/en/articles/12840000-voluntary-corporate-action (T3)
- Fidelity, Commissions, Margin Rates, and Fees: https://www.fidelity.com/trading/commissions-margin-rate (T1)
- Fidelity, Corporate actions FAQ: https://fidelity.com/customer-service/corporate-actions-learn-more-faqs (T1)
- IBKR, Other Fees: https://www.interactivebrokers.com/en/pricing/other-fees.php (T1)
- IBKR KB, Voluntary Corporate Action tool: https://ibkb.interactivebrokers.com/article/1070 (T1)
- Schwab, Pricing Guide for Individual Investors: https://www.schwab.com/legal/Schwab-pricing-guide-for-individual-investors (T1; fee not found)
- TD Ameritrade fee schedule (historical): https://welcome.tdameritrade.com/content/dam/tda/retail/marketing/en/pdf/TDA4075.pdf (T1, historical)
- Robinhood, Corporate actions tracker: https://robinhood.com/us/en/support/articles/corporate-actions-tracker/ (T1 broker)
- 3M 8-K exhibit 99.1, 2022 split-off results: https://www.sec.gov/Archives/edgar/data/66740/000114036122032322/ny20004937x36_ex99-1.htm (T1)
- Pfizer SC TO-I/A 2013: https://www.sec.gov/Archives/edgar/data/0000078003/000119312513274637/d556202dsctoia.htm (T1)
- Bristol-Myers Squibb SC TO-I/A 2009: https://www.sec.gov/Archives/edgar/data/0000014272/000119312509259341/dsctoia.htm (T1)
- SEC, Odd-Lot Tender Offers by Issuers (1996 release): https://www.sec.gov/rules/1996/12/odd-lot-tender-offers-issuers (T1, title only)
- FindStox, Odd Lot Arbitrage: https://www.findstox.com/oddlots (T3)
- Eagle Point Capital, XBiotech odd-lot: https://www.eaglepointcap.com/blog/odd-lot-arbitrage-opportunity-in-xbiotech-shares (T3)
- Eagle Point Capital, J&J odd-lot comments: https://eaglepointcapital.substack.com/p/johnson-and-johnson-odd-lot-arbitrage/comments (T3)
- My Money Blog, Lennar/Millrose: https://www.mymoneyblog.com/lennar-millrose-properties-odd-lot-tender.html (T3)
- My Money Blog, Cummins/Atmus: https://www.mymoneyblog.com/cummins-atmus-filtration-odd-lot-tender-exchange-offer.html (T3)
- Special Situation Investments, Odd-lot arbitrage with $720 upside: https://www.specialsituationinvestments.com/2025/11/odd-lot-arbitrage-with-720-upside/ (T3)
- KorroAi "oddly" PAPER.md: https://github.com/KorroAi/oddly/blob/main/PAPER.md (T3, not evidence)
- Gahng, Ritter and Zhang, "SPACs", RFS 2023: https://ideas.repec.org/a/oup/rfinst/v36y2023i9p3463-3501..html (T1)
- Ritter, SPACs working paper: https://site.warrington.ufl.edu/ritter/files/SPACs.pdf (T1)
- Houlihan Capital Q1 2025 SPAC report: https://www.houlihancapital.com/wp-content/uploads/2025/05/Q1-2025-SPAC-Report.pdf (T2)
- AUM Advisors, De-SPAC Scorecard H1 2026: https://aumadvisors.com/scorecard (T3)
- Columbia Blue Sky Blog, SPACs create value but agency costs loom (2025): https://clsbluesky.law.columbia.edu/2025/08/04/spacs-create-value-but-agency-costs-loom/ (T2)
- ICI, Closed-End Fund Activism (2025): https://www.ici.org/files/2025/cef-activism.pdf (T2)
- CXO Advisory on Wu, Piccotti and Patro (2017): https://www.cxoadvisory.com/mutual-hedge-funds/enhanced-exploitation-of-closed-end-fund-discounts/ (T2 summary of T1)
- vLex, "Exploiting closed-end fund discounts" (JFR 2017): https://law-journals-books.vlex.com/vid/exploiting-closedend-fund-discounts-855659869 (T1 listing)
- Yahoo Finance / Bloomberg, Saba ETF piles into equity CEFs: https://finance.yahoo.com/news/saba-etf-piles-equity-closed-211915810.html (T2)
- mutualfunds.com, Supreme Court and CEF activism: https://www.mutualfunds.com/closed-end-funds-channel/the-supreme-court-just-changed-cef-activism/ (T3)
- Jetley and Ji, Shrinking Merger Arbitrage Spread: https://www.analysisgroup.com/globalassets/uploadedfiles/content/insights/ag_features/merger_arbitrage_spread_-_still_shrinking/jetley_and_ji_shrinking_merger_arbitrage_spread.pdf (T1)
- Baker and Savasoglu, Limited arbitrage in M&A, JFE 2002: https://www.sciencedirect.com/science/article/abs/pii/S0304405X02000727 (T1)
- Verdad, Merger Arbitrage: https://verdadcap.com/archive/merger-arbitrage (T2)
- Alpha Architect, Facts, Fiction, and Merger Arbitrage: https://alphaarchitect.com/facts-fiction-merger-arbitrage/ (T2)
- Zacks, MERFX performance: https://www.zacks.com/funds/mutual-fund/quote/MERFX/performance (T2)
- PortfoliosLab, MERFX vs MERIX: https://portfolioslab.com/tools/stock-comparison/MERFX/MERIX (T3)
- arXiv 2607.09921, Global Merger-Arbitrage Forecasting with Language Models: https://arxiv.org/pdf/2607.09921 (T1, scope note only)
- Khorana, Wahal and Zenner, JFQA 2002: https://ideas.repec.org/a/cup/jfinqa/v37y2002i02p177-200_00.html (T1)
- SIFMA letter, Reverse Stock Splits and Fractional Share Round-Ups (2025-03-25): https://www.sifma.org/wp-content/uploads/2025/03/SIFMA-Reverse-Split-and-Fractional-Shares-Letter-to-Exchanges-03.25.25.pdf (T2)
- SIFMA white paper (2025-12-03): https://www.sifma.org/wp-content/uploads/2025/12/SIFMA-White-Paper_Reverse-Stock-Splits-and-Fractional-Share-Management-A-Framework-for-Market-Integrity_12.03.pdf (T2)
- Computershare, Upexi reverse split case study: https://www.computershare.com/us/insights/transfer-agent-services/upexi-reverse-stock-split-case-study (T2)
- Barchart, American Rebel reverse split with round-lot protection (2025): https://www.barchart.com/story/news/34980500/american-rebel-announces-1-for-20-reverse-stock-split-with-round-lot-shareholder-protection-to-be-effective-on-october-3-2025 (T1 issuer release)
- SEC, SR-NASDAQ-2023-025 comments: https://www.sec.gov/comments/sr-nasdaq-2023-025/srnasdaq2023025.htm (T1, title only)
- SEC, Going private (investor.gov glossary): https://www.investor.gov/introduction-investing/investing-basics/glossary/going-private (T1)
- Repo: [data-edge census](2026-11-02-data-edge-census.md), [idea cards, IC-B3](idea-cards.md), [ADR 0016](../decisions/0016-development-boundary-and-forward-exams.md), [cost by liquidity band](2026-11-02-cost-by-liquidity-band.md), claims QI-2 and G2-S10 in [claims.toml](claims.toml)

## Proposed claims
For the orchestrator to add to `docs/research/claims.toml` (not edited here).

```toml
[[claim]]
id = "SE-1"
statement = "Alpaca charges $100 per client per voluntary corporate action (tender, exchange offer, rights or warrant election), which exceeds the typical $120-$495 odd-lot tender gain at our size."
kind = "fact"
grade = "n/a"
scope = "Alpaca US brokerage, docs as summarised by search on 2026-11-20; page not fetched"
source = "docs/research/2026-11-20-small-account-structural-edges.md"
section = "Evidence: Broker feasibility"
note = "UNVERIFIED at page level; confirm with Alpaca support. Fidelity lists $0 and IBKR lists voluntary actions as free."

[[claim]]
id = "SE-2"
statement = "Alpaca paper accounts do not process corporate actions (splits, dividends, tenders, cash-outs), so no event-driven structural trade can be scored on paper as the forward exam is built."
kind = "fact"
grade = "n/a"
scope = "Alpaca paper environment; staff forum replies"
source = "docs/research/2026-11-20-small-account-structural-edges.md"
section = "Answer, finding 3"
bears_on = ["adr:0016"]
note = "Tier 3 (staff forum posts), UNVERIFIED."

[[claim]]
id = "SE-3"
statement = "Odd-lot priority in issuer tenders and split-off exchange offers has no post-2015 academic return evidence; practitioner tallies give 1-5 profitable US deals a year averaging about $285 each."
kind = "empirical"
grade = "INSUFFICIENT"
scope = "US issuer tenders, Dutch auctions and split-offs; 99-share positions; gross, before tax"
source = "docs/research/2026-11-20-small-account-structural-edges.md"
section = "Evidence: (a) Odd-lot priority"
note = "Mechanism confirmed in primary filings (3M 2022, Pfizer 2013, BMS 2009). Failure modes: priority withdrawn, broker mishandling, early cutoffs."

[[claim]]
id = "SE-4"
statement = "Small-deal cash merger arbitrage is MIXED post-2015: the longest live record found (The Merger Fund) returned 3.90% a year over the ten years to 2026-03 with beta 0.02, about T-bills plus 2 points, and the size premium is documented only for 1981-1996."
kind = "empirical"
grade = "MIXED"
scope = "US cash deals; live fund net of fund fees, before tax"
source = "docs/research/2026-11-20-small-account-structural-edges.md"
section = "Evidence: (d) Merger arbitrage"
bears_on = ["card:IC-B3"]

[[claim]]
id = "SE-5"
statement = "Reverse-split round-up arbitrage is closing: SIFMA asked NYSE and Nasdaq in March 2025 to end round-ups and in December 2025 proposed cash-in-lieu as the default; at Alpaca, which pays out fractional shares in splits since August 2021, a round-up probably never reaches the client."
kind = "empirical"
grade = "INSUFFICIENT"
scope = "US listed reverse splits 2023-2026"
source = "docs/research/2026-11-20-small-account-structural-edges.md"
section = "Evidence: (f) Reverse-split round-ups"
note = "No exchange rule banning round-ups was found; the Alpaca round-up handling is UNVERIFIED."

[[claim]]
id = "SE-6"
statement = "SPAC trust-value trades, CEF discount reversion and rights offerings are not small-capacity edges: institutions (SPAC arbitrage funds; Saba, Karpus, Bulldog) take the same side, and CEF rights offerings historically lowered fund premiums."
kind = "empirical"
grade = "INSUFFICIENT"
scope = "US; SPAC post-2022, CEF post-2015, CEF rights 1988-1998"
source = "docs/research/2026-11-20-small-account-structural-edges.md"
section = "Per-candidate table"
```
