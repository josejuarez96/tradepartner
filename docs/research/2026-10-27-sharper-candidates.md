# Research Report: Five sharper, mechanism-driven candidates for a small, timestamp-rich, daily-bar system, and what an equal-weight benchmark changes

**Brief:** orchestrator brief of 2026-10-27 (owner context: every tested idea was a broad academic factor, long-only, top 1,000, monthly, and all lost to SPY); no issue number  ·  **Date:** 2026-10-27  ·  **Status:** COMPLETE for the brief's questions, at the evidence depth noted per candidate (several key magnitudes UNVERIFIED, listed below)  ·  **Agent/model:** research subagent, claude-opus-5-5; 21 web searches, 12 fetches, read-only repo reads (config and schema for store coverage; no database opened, `.env` not read)

**Budget note.** The brief set no budget. I set myself about 20 searches and stopped there. Several magnitudes are vendor-reported or seen only in abstracts. They are marked UNVERIFIED, and none is used as a kill or pass number without that label.

## Answer
**Verdict:** MIXED  ·  **Confidence:** medium for the ranking and the RSP arithmetic; low for any candidate's net return.

1. **None of the five is SUPPORTED** under handoff §6.1. The best grades are MIXED: spin-offs, and activist 13D post-filing drift. Filing delay is INSUFFICIENT, because the sign is published but there is no net, post-2010 tradable test. Closed-end fund discounts are INSUFFICIENT. The after-hours / Friday 8-K timing trade is NOT SUPPORTED, because a Tier 1 test on 8-Ks from 2005 to 2013 finds no under-reaction.
2. **The five fit our edges better than the factors did.** Each is an event with a named counterparty and a holding period of weeks to months. Each lives in small names, where a $100 to $100k book has a capacity advantage. Three of the five key on SEC acceptance times.
3. **First by information value per unit of cost: C1, filing delay ("the filing that did not arrive").** It uses the one data edge we own, needs no new data for its first read, has a pre-registered kill line, and has a long-only form (early filers) that a cash account can hold. Close second: **C2, spin-offs**, whose rung-2 read is an open ETF series (CSD) and costs about an hour. The owner decides the order. This report only grades the evidence.
4. **Equal-weight benchmark: over our in-sample window (2020-09 to 2023-12), RSP beat SPY.** RSP returned about +50.1% (about 12.96%/yr) and SPY about +43.5% (about 11.43%/yr), so RSP led by about **1.5 pp/yr**. The mega-cap drag is a **2023-only** story: in 2023, RSP returned 13.87% against SPY's 26.29%. Over the whole window it ran the other way (2020-Q4 and 2022 favoured equal weight). So "equal weight against cap-weighted SPY" **does not explain** the in-sample losses of trials 1-24. Against RSP they read about 1.5 pp/yr worse, not better. The drag is real for windows after 2023 (paper and forward books).

## Store coverage, as the code says (read-only; the database was not opened)
- **Filing index forms** (`config.py` `issuer_forms`, about line 604): 10-K, 10-Q, 8-K, 20-F, 40-F, S-1, F-1, **10-12B**, 25, 25-NSE. **Not held:** NT 10-K / NT 10-Q (Rule 12b-25 notices), SC 13D / SC 13G (and the XML "SCHEDULE 13D" form used since 2024-12), and 424B prospectuses. Adding a form is a config-list change plus a re-ingest of submissions (cost S). Whether the subject company's submissions payload lists third-party 13D filings is **UNVERIFIED**; EDGAR's company pages list them.
- `filing_events` (schema line 1227) is defined for 8-K items (T164). Per the brief, `items` is not yet populated.
- Corporate actions hold splits and dividends for 4,820 securities (ADR 0024). Paper credits spin-offs (`spinoff_receipt`); the backtest does not adjust for them (ADR 0023 context).
- Measured cost (from the [paper-fill report](2026-10-25-paper-fill-costs.md)): do not calibrate costs from paper fills. Use **about 15 bp per side for names with ADV above $200M, 25-30 bp for $50M-200M, and 50 bp or exclude below $50M** for open fills. All five candidates live mostly in the $50M-200M band or below, so rung 1 below uses **30 bp per side (60 bp round trip)** as the base and 50 bp as the stress case.

## The five candidates

### C1. Filing delay: "the filing that did not arrive" (long early filers; short or avoid late filers)
- **Mechanism.** A firm that misses its own usual 10-Q or 10-K date, or files an NT notice under Rule 12b-25, is usually signalling a problem: accounting trouble, auditor friction or a bad quarter. The market reacts to the notice, then keeps drifting. **The other side:** holders who are not watching the calendar, and small-cap longs who cannot be shorted against because borrowing is scarce. Short sellers do trade this ahead of the notice (Dai, Li, Zaiats and Zhao 2021). Our edge is point-in-time: on any day, "past its own usual date with no 10-Q accepted" is a fact the store can state to the second. A vendor earnings calendar is not needed for this absence signal.
- **Evidence.**
  - Bartov and Konchitchki, *Accounting Horizons* 2017 (Tier 1): 2,115 first-time late filers over nine years. Five-day return around the notice **−2.93% (NT 10-Q), −1.96% (NT 10-K)**. Abnormal returns "continue to decline" over the following year, less so for accounting-reason delays. The drift magnitude was not visible to me (UNVERIFIED). Firms that promise the grace period and then miss it fall by **about 4.12% against 2.02%** over five days (Columbia Blue Sky summary and Stern summary, Tier 2).
  - Johnson and So, *JFQA* 2018 (Tier 1): firms that schedule earnings later than expected report worse news, and equity prices do not reflect it until the announcement. The **260 bp spread** in the month after the revision (advancers minus delayers) is a **vendor figure** (Wall Street Horizon, Tier 3; UNVERIFIED in the paper).
  - Dai et al., *Journal of Corporate Finance* 2021 (Tier 1, abstract only): short interest rises before late filings and predicts the later underperformance.
- **Disconfirmation.** Searched: "late filers … drift trading strategy … short sale constraints"; "earnings announcement date delay … replication decay". Found: RavenPack's study (Tier 3, vendor) says the date signal "decays relatively quickly", and the delayer side decays faster than the advancer side. McLean-Pontiff's 58% post-publication haircut applies (QI-2). There is **no net, post-2010, tradable test** of either leg. The late-filer universe skews small (no size figure found), so the short leg meets LS-6: anomaly short legs net about zero after borrow.
- **Grade:** **INSUFFICIENT** for a net tradable strategy. The direction has two independent Tier 1 sources (MIXED-quality support); the magnitude does not.
- **Data needed vs held.** The absence signal (each firm's own same-quarter filing lag from 10-Q/10-K acceptance times) is **held**, at zero cost. NT notices: add `NT 10-K` and `NT 10-Q` to `issuer_forms` and re-ingest (S). Earnings-date scheduling, Johnson-So's actual signal, needs a paid calendar or the 8-K 2.02 history (T164).
- **Kill ladder from published numbers.**
  - **Rung 1:** the hold is 20-60 sessions, so one-sided turnover is roughly 50-100% a month. That costs about 3.6-7.2 pp/yr at 30 bp per side, or about 0.6% per event round trip. Long leg (early filers): if it is half the vendor's 260 bp spread, after a 58% haircut it is about **55 bp per event**. That is **below the 60 bp round trip, so it fails at 30 bp and passes only near 15 bp** (liquid names). The NT short leg: the −2% to −3% five-day move is mostly at the notice, before our next-open entry, and the after-entry drift is UNVERIFIED. **Rung 1 is undecidable from published numbers.** That is why the first test is our own read.
  - **Rung 2:** no Chen-Zimmermann series exists for filing lag (none found). Rung 2 must be a stored-data read (rung 2-O, then an own-store return read, ledger row L1).
  - **Rung 3:** the long leg holds at $100 (fractional, if the names are fractionable: UNVERIFIED for small caps). The short leg is paper only (ADR 0024), with no fractional shorts (LS-16) and likely hard-to-borrow names.
- **Holding / turnover:** 20-60 sessions; about 3-8 events a day in filing season; weekly cadence is enough.
- **Cheapest first test.** One read-only query, declared before it runs (ledger row first). For 10-Q/10-K acceptances 2017-01..2019-12 (before every in-sample start, rule L4), compute each firm's lag against its same quarter one year earlier. Then measure the 20- and 60-session market-adjusted return from the next open, by lag tercile, at the 15/30 bp lines. **Kill:** the early-filer tercile earns less than 60 bp per event after next-open entry, or t < 1.0. Cost: about one agent-day, no new data, no engine.

### C2. Spin-off children (long), bought after the forced selling
- **Mechanism.** When a parent spins off a small child, index funds and mandate-bound holders of the parent receive shares they may not hold: the wrong index, too small, the wrong sector. They sell without regard to price in the first weeks. Analysts have not started coverage. **The other side** is that forced seller. The niche is capacity: small children are too small for the funds that would arbitrage them. Our timestamp edge is modest. The Form 10-12B registration is in the store with its acceptance time, so the event list is point-in-time.
- **Evidence.**
  - Cusatis, Miles and Woolridge, *JFE* 1993 (Tier 1, 1965-1988): parents and children both earned abnormal returns after spin-offs. The figures are not re-verified here.
  - McConnell and Ovtchinnikov 2004 (Tier 1): children outperformed two benchmarks over the first 22 months (the horizon is from the search summary; UNVERIFIED).
  - McConnell, Sibley and Xu, *J. Portfolio Management* 2015, 42(1) (Tier 1, abstract): 2001-2013, a buy-all-children portfolio beat the spin-off ETF, and the ETF beat the value-weighted market. This is the only post-2000 US Tier 1 source found. The magnitudes are UNVERIFIED.
- **Disconfirmation.** Searched: "spin-off … 2000-2020 no outperformance after controlling size value momentum"; "CSD performance vs S&P 500 … anomaly disappeared". Found: a 2000-2020 Nordic study (84 spin-offs, thesis, Tier 3) with **no significant** child returns and negative parent returns. Other European theses find positive returns linked to small size, not value. The CSD ETF reportedly delivered **about half the S&P 500's total return after its May 2016 index change** (Seeking Alpha, Tier 3). That conflicts with an undated snapshot showing a 10-year return of 15.26% against 13.88% (PortfoliosLab, Tier 3). No US net, post-2013 academic test was found.
- **Grade:** **MIXED** (Tier 1 support to 2013, conflicting and Tier 3 evidence afterwards, no net test).
- **Data needed vs held.** 10-12B acceptance times and the child's first bar are **held**. Pairing child and parent needs a small hand-built map or the Form 10 text (S). Spin-off actions in the backtest are not adjusted, which matters only for the parent leg. Since 2016 the event count is roughly 30-60 a year (UNVERIFIED), so own-store power is low: about 300 events, overlapping holds.
- **Kill ladder.**
  - **Rung 1:** about a 12-month hold, so turnover is about one round trip a year. At 30-50 bp per side that needs **0.6-1.0 pp/yr**. Any surviving premium clears it. **Passes trivially.**
  - **Rung 2:** an open, investable long-leg proxy exists: **CSD** (Invesco S&P Spin-Off ETF, since 2006). Read its total return against SPY and RSP for 2007-2019 (rule L4). **Kill:** CSD's excess over RSP below 0 or t < 1.0. Caveat: CSD holds only larger spin-offs (index rule, UNVERIFIED), so a kill here leaves the small-child niche untested. A pass is an upper bound on what a fund can capture.
  - **Rung 3:** at $100, 3-5 children at about $20-30 each. Whether fresh spin-offs are fractionable at Alpaca is UNVERIFIED.
- **Holding / turnover:** 6-24 months, entered 1-4 weeks after the first regular-way session; about 2-5 entries a month.
- **Cheapest first test:** the CSD read above (an ETF bar series, roughly an hour). Then a children-only event study on the store from 10-12B acceptances 2016-2019.

### C3. Activist 13D post-filing drift (long)
- **Mechanism.** An activist crossing 5% and filing a Schedule 13D forces change: board seats, buybacks, sales. Most of the price move happens before and at the filing. The question is whether holders underreact to the slow value created afterwards. **The other side:** sellers to the activist, and holders who sell into the jump. The niche: targets are small and mid caps, and a fund cannot buy a meaningful stake after the filing. **Rule change:** since 2024-02-05 the initial 13D is due within **5 business days** instead of 10 calendar days. Amendments are due within 2 business days, and the schedule has been XML since 2024-12-18 (SEC final rule, October 2023, via AIMA and Mintz summaries, Tier 2; the Federal Register text was not read). Less of the run-up can happen before the filing now, so the post-filing share may have changed. Pre-2024 evidence may not transfer.
- **Evidence.**
  - Brav, Jiang, Partnoy and Thomas, *JF* 2008 (Tier 1): filing-window abnormal return about **7%** over [−20, +20]. **No reversal** in the following year.
  - The same authors in *FAJ* 2008 (Tier 1): the filing-window return **fell from 15.9% (2001) to 3.4% (2006)**.
  - Brav, Jiang and Kim, *Annual Review of Financial Economics* 2015 (Tier 1): a review; text not read.
  - A working paper (author unconfirmed, Tier 3 by verification): hedge-fund targets earn an **additional 11.4%** in the year after the filing (UNVERIFIED).
  - A 1995-2016 study (reported in *Institutional Investor*, Tier 2): significant but smaller excess returns over 36 and 60 months, with a selection caveat ("hedge funds tend to target underperforming firms").
- **Disconfirmation.** Searched: "13D … post-filing drift after 2010 decline"; "hedge fund activism 13D … 2010-2020 sample". Found: a 2023 *IJBAF* study (Tier 1, abstract): **post-filing abnormal returns are significantly lower than the pre-filing returns**, for 13D and 13G targets alike. An international study: equal-weighted activism **without** outcomes earns **−9.8%/yr**, with outcomes +1.1% (abstract). A critique: the announcement effect is common to any 13D, not specific to activists. A later sample (2008-2014, author unverified) reports that the 2001-2006 decline reversed. **No net, post-2010 tradable test** was found.
- **Grade:** **MIXED.** The jump is solid but untradable for us, because it happens before our next-open entry. Post-filing drift conflicts across studies, and the 2024 rule break limits what older evidence says.
- **Data needed vs held.** **Not held.** Add SC 13D, SC 13D/A and SCHEDULE 13D to the forms list and re-ingest (S), if the subject's payload carries them (UNVERIFIED). Separating activists from other 13D filers needs a filer list or the structured purpose field, which only exists since 2024-12 (S-M). Bars are held.
- **Kill ladder.**
  - **Rung 1:** a 6-12-month hold needs about 0.6-1.0% per event at 30-50 bp per side. If any post-filing drift of a few percent a year exists, **it passes.**
  - **Rung 2:** no open series. It needs the 13D ingest, then an own-store read for 2016-2019.
  - **Rung 3:** about $100 across 3-5 targets. Small caps raise fractionability and whole-share risk (UNVERIFIED).
- **Holding / turnover:** 3-12 months; event count roughly 10-25 activist filings a month (UNVERIFIED).
- **Cheapest first test:** add the 13D forms, then count events since 2016 with acceptance times (coverage, no returns). The return read comes only if the count supports power.

### C4. Closed-end fund discount mean reversion (long the widest discounts; optional short the richest premiums)
- **Mechanism.** A closed-end fund trades at a discount or premium to its net asset value. Discounts widen in retail selling (tax-loss season, panics) and revert over months. The other side is retail CEF holders. **This is the purest capacity niche of the five:** most CEFs are $100M-1B, with thin volume. Institutions arbitrage them only through specialists (activists such as Saba). It does not use our timestamp edge.
- **Evidence.**
  - Pontiff, *JFE* 1995 (Tier 1, cited from memory, not re-read: discount mean reversion predicts CEF returns; UNVERIFIED here).
  - A Rutgers study (publication and date not confirmed, Tier 1 by type if published): most funds' premiums mean-revert, with an average **half-life of 7.7 months**, faster for bond funds. Strategy returns of **17.3-18.2%/yr** are **not clearly net of costs**.
  - Matisse Capital (Tier 3, marketing): discount predicts return. Not evidence.
- **Disconfirmation.** Searched: "closed-end fund discount mean reversion trading strategy returns net of costs recent evidence 2010s". Found nothing post-2010 that is net and out of sample.
- **Grade:** **INSUFFICIENT.**
- **Data needed vs held.** **Not held, and the costliest of the five.** It needs daily NAV history. NAV tickers (the `X…X` symbols) are not in Alpaca SIP equity bars (UNVERIFIED). CEFConnect pages are not point-in-time. CEFs may also fall outside the store's universe and listings logic (they file N-CSR, not 10-K). The cost is M-L, plus a universe decision.
- **Kill ladder.**
  - **Rung 1:** the half-life of about 7.7 months implies 3-12-month holds. CEF half-spreads are wide (UNVERIFIED, often 20-100 bp), so at 50 bp per side **break-even is about 1-2 pp/yr**, small against claimed double-digit returns. Undecidable without net data.
  - **Rung 2:** no open series.
  - **Rung 3:** strong. A $100 book can hold 3-5 CEFs, but fractionable status is UNVERIFIED.
- **Holding / turnover:** 3-12 months; weekly cadence.
- **Cheapest first test:** before any data build, a desk check of whether a free, dated daily NAV source exists for 2016-2019. If none does, park the idea.

### C5. After-hours and Friday 8-K timing: buried bad news (short or avoid)
- **Mechanism.** Managers file bad news after the close or on Fridays, when attention is low. If the market under-reacts, prices drift. **Our edge is direct:** the acceptance second tells us after-hours and Friday from the store, with no vendor needed.
- **Evidence.**
  - DellaVigna and Pollet, *JF* 2009 (Tier 1, earnings, 1984-2006): on Fridays, **60%** of the total response is delayed against **40%** on other days, and announcement volume is **10% lower**.
  - Rawson, Twedt and Watkins (Tier 1 by venue, via a Columbia Blue Sky summary, Tier 2): price formation after negative 8-K news is slower when the firm issues a concurrent unrelated press release.
- **Disconfirmation (the binding finding).** Searched: "Niessner strategic disclosure timing 8-K Friday after-hours … returns"; "DellaVigna Pollet … replication later period". Found: **Segal and Segal, *Review of Accounting Studies* 2016 (Tier 1)**, non-earnings 8-Ks 2005-2013. Managers do release negative news after hours and on the last day of the week, but there is **"no evidence that strategic reporting of negative news leads to investor under-reaction."** A later thesis (Tier 3) attributes the Friday effect to firm heterogeneity. The earnings-reaction drift this would amplify is B9's, **killed** at rungs 1-2 (long leg +0.07%/month, t 0.94).
- **Grade:** **NOT SUPPORTED** for timing alone on post-2005 8-Ks. MIXED at best for earnings-only Friday releases, and that leg needs T164's items.
- **Data:** 8-K acceptance times are held. Item codes are needed to separate earnings (T164).
- **Kill ladder.**
  - **Rung 1:** B9's measured break-even was **1.75-3.5 bp per side**. A Friday subset scales the delayed share by at most 60/40 = 1.5×, so break-even is **about 2.6-5.3 bp per side, against our 15-30 bp. It fails rung 1 by arithmetic.**
- **Holding / turnover:** 20 sessions; high.
- **Cheapest first test:** none needed. It dies at the desk. It is listed so the ledger records it, and because it shows that **the timestamp alone is not an edge.** The timestamp pays only when it times an *absence* (C1) or a slow-moving event (C2, C3).

## Ranking by information value per unit of cost

| Rank | Candidate | Grade | First-test cost | Data cost to rung 4 | Rung 1 (published) | Live-compatible leg (cash, $100) | Why this rank |
|---|---|---|---|---|---|---|---|
| 1 | C1 filing delay | INSUFFICIENT | ~1 agent-day, held data | 0 (absence signal); S for NT forms | undecidable; passes only near 15 bp | long early filers | Uses our unique edge, needs no data, and a kill or pass regrades two claims |
| 2 | C2 spin-offs | MIXED | ~1 hour (CSD series) | S (child-parent map) | passes trivially | long children | Cheapest decisive read; best grade; low power on our own store |
| 3 | C3 activist 13D | MIXED | S (form ingest + count) | S-M (activist classification) | passes if drift exists | long targets | Good mechanism; the 2024 rule break and missing data cost more |
| 4 | C5 Friday / after-hours 8-K | NOT SUPPORTED | 0 (desk arithmetic, done here) | — | **fails** (2.6-5.3 bp vs 15-30) | avoid list | Killed on paper; recorded so the ledger counts it |
| 5 | C4 CEF discounts | INSUFFICIENT | desk check for a NAV source | M-L + universe decision | undecidable | long discounts | Best capacity niche, but the costliest data and the weakest modern evidence |

**What I would test first, and why:** C1, the early-filer and late-filer read on 2017-2019 acceptance times. It is the only candidate whose first read needs neither new data nor an engine, and whose signal nobody without exact acceptance history can replicate cheaply. It also has a long leg that a cash account can hold. Its information value is high either way: a fail tells the owner that the timestamp edge has no standalone return value at our costs. **C2's CSD read is so cheap that it can run the same day.** This is an evidence ranking. The order is the owner's call (backlog rules).

**Considered and dropped before ranking:** dividend initiations and omissions (Boehme and Sorescu, *JF* 2002, Tier 1: the drift is insignificant value-weighted and "not robust across subsamples"; Chen, Chou and Lee, *JEF* 2014, confirm it is non-robust). SEO and at-the-market issuance (US evidence is old; Lyandres, Sun and Zhang cut SEO underperformance by 37-46% with an investment factor; it overlaps Q1 and LS-20).

## Equal weight against cap weight: the RSP finding

**Monthly and annual total returns, dividends reinvested** (chartrow.com, Tier 3 as a data aggregator; the 2023 figures cross-check against Invesco's index figures, Tier 2):

| Period | RSP | SPY | RSP − SPY |
|---|---|---|---|
| 2020-09..2020-12 (Sep −2.5, Oct −0.6, Nov +14.3, Dec +4.2 / Sep −3.7, Oct −2.5, Nov +10.9, Dec +3.7) | +15.4% | +8.0% | +7.4 pp |
| 2021 | +29.4% | +28.7% | +0.7 pp |
| 2022 | −11.6% | −18.2% | +6.6 pp |
| 2023 | +13.7% (index 13.87%) | +26.2% (index 26.29%) | **−12.5 pp** |
| **2020-09..2023-12 (40 months, our in-sample window after 2020-08-31)** | **+50.1%, 12.96%/yr** | **+43.5%, 11.43%/yr** | **+1.5 pp/yr** |

Compounded from the rounded monthly and annual figures, so expect about ±0.3 pp of rounding error. Including August 2020 (RSP +4.3%, SPY +7.0%) leaves RSP still ahead: +56.6% against +53.5%.

**What it changes in reading past trials.**
- In-sample (2020-08-31..2023-12-29), an equal-weighted large-cap book had about a **1.5 pp/yr tailwind** over SPY, not a headwind. Trials 1-24's losses against SPY are therefore **not** explained by equal weight against cap weight. Read against RSP, each in-sample trial's excess is about 1.5 pp/yr **worse**: for example, B10's −14.6%/yr screened becomes about −16, and the −7.7%/yr control about −9. That assumes those figures are in-sample annualized excesses, and subtracting annualized gaps is approximate.
- The mega-cap drag is real **in 2023 and after**. Invesco reports the S&P 500 at **86%** total return for 2023-2025 against about **43%** for equal weight (Tier 2 summary, UNVERIFIED in a primary source). Any paper, forward or holdout window from 2024 onward will look much worse against SPY than against RSP.
- Our universe is the top 1,000, not the S&P 500. Ranks 501-1,000 are mid-caps, so RSP is a close but imperfect null. The exact null for an equal-weighted top-N book is **the store's own equal-weighted universe return**, which costs nothing to compute and removes both the weighting and the universe effect.
- **Reading for the owner (not a decision):** reporting excess over RSP and over the own-universe equal-weight return beside SPY separates "the signal" from "the weighting". It would not have rescued any past trial in-sample. ADR 0005 names SPY as the objective, so adding a reporting line is a diagnostic. Changing the objective would need an ADR 0005 amendment.

## Evidence
| Claim | Source | Tier | Key figure (quoted) | OOS / post-pub? | Net of costs? |
|---|---|---|---|---|---|
| NT notices are followed by negative returns and drift | Bartov & Konchitchki, *Accounting Horizons* 2017; Columbia Blue Sky and Stern summaries | 1 (2 for the summaries) | −2.93% (NT 10-Q), −1.96% (NT 10-K), 5-day; 2,115 firms; decline "continues" for months | no | no |
| Short sellers anticipate late filings | Dai, Li, Zaiats & Zhao, *J. Corp. Finance* 2021 | 1 (abstract) | short interest rises before; predicts underperformance | n/a | no |
| Later-than-expected earnings dates predict bad news; equity prices are slow | Johnson & So, *JFQA* 2018 | 1 | 260 bp/month spread (vendor figure, UNVERIFIED) | no | no |
| Date signal decays fast, delayers fastest | RavenPack study | 3 | qualitative | partly | no |
| Spin-off children outperform (2001-2013) | McConnell, Sibley & Xu, *JPM* 2015 | 1 (abstract) | children > CSD > VW market | no | no |
| Spin-off premium absent (Nordic 2000-2020) / CSD lagged after 2016 | theses; Seeking Alpha | 3 | no significant child returns; "about half" the S&P total return | — | — |
| 13D filing window positive, no reversal | Brav, Jiang, Partnoy & Thomas, *JF* 2008 | 1 | about 7%, [−20, +20] | no | no |
| 13D window return declined 2001-2006 | Brav et al., *FAJ* 2008 | 1 | 15.9% → 3.4% | partly | no |
| Post-filing 13D returns lower than pre-filing | *IJBAF* 2023 | 1 (abstract) | "significantly lower" | yes | no |
| 13D due in 5 business days from 2024-02-05; XML from 2024-12-18 | SEC final rule, via AIMA and Mintz | 2 (primary not read) | as stated | — | — |
| Friday announcements: larger delayed response | DellaVigna & Pollet, *JF* 2009 | 1 | 60% vs 40% delayed; volume 10% lower | no (1984-2006) | no |
| No under-reaction to strategically timed 8-K bad news | Segal & Segal, *RAST* 2016 | 1 | "no evidence … under-reaction" (2005-2013) | yes | n/a |
| Dividend-initiation drift is not robust | Boehme & Sorescu, *JF* 2002 | 1 | insignificant value-weighted | yes | no |
| CEF premiums mean-revert | Rutgers study (venue unconfirmed) | 1? | half-life 7.7 months; 17.3-18.2%/yr, not clearly net | no | unclear |
| RSP vs SPY, 2020-09..2023-12 | chartrow monthly and annual; Invesco for 2023 | 3 / 2 | RSP +50.1%, SPY +43.5% (computed) | n/a | ETF net of fees |

## Disconfirmation
- **Searches run:**
  - "late filers NT 10-Q post-announcement drift trading strategy small firms short sale constraints"
  - "earnings announcement date delay … replication decay"
  - "spin-off long-run abnormal returns recent sample 2000-2020 no outperformance …"
  - "CSD performance vs S&P 500 … anomaly disappeared"
  - "13D … post-filing drift after 2010 decline"
  - "hedge fund activism 13D … 2010-2020 … post-filing drift"
  - "Niessner … 8-K Friday after-hours …"
  - "DellaVigna Pollet … replication later period weakened"
  - "Boehme Sorescu …"
  - "SEO / ATM long-run underperformance post-2010"
  - "CEF discount mean reversion … net of costs 2010s"
- **What was found against:**
  - C5 is contradicted by Segal and Segal (Tier 1) and by B9's own kill.
  - C1: vendor evidence says the date signal decays fast, and the late-filer short leg sits where LS-6 says short legs die net of borrow.
  - C2: post-2013 Tier 3 evidence conflicts, and a Nordic 2000-2020 study finds no child premium.
  - C3: post-filing returns are lower than pre-filing, the no-outcome tail is negative, and the 2024 rule is a regime break.
  - C4: no modern net test.
  - RSP: the "mega-cap drag explains our losses" premise is contradicted for the in-sample window as a whole.

## Caveats & gaps
- Most magnitudes were read from abstracts or summaries. No candidate has a post-2010, net, US test. QI-2's 58% haircut is the right prior for every published number above.
- Rung-1 lines assume the paper-fill report's ADV-banded open-fill costs (30 bp base). If live costs on these small names are worse, C1 dies first.
- Own-store power is low for the event ideas: C2 has about 300 events since 2016, and C3 needs an ingest even to count.
- Store coverage is read from config and schema, not the database. Whether third-party 13D filings come in the subject's submissions payload is unchecked.
- Process: I could not run `team.py`, git or gh (no shell in this run). The report was written outside the repo for the caller to place on a branch.

## UNVERIFIED items
- The magnitude of Bartov-Konchitchki's post-notice drift, and the size distribution of late filers.
- Johnson-So's 260 bp (vendor figure).
- McConnell-Ovtchinnikov's 22-month horizon and magnitudes; McConnell-Sibley-Xu magnitudes; CSD's index rules and dated performance.
- The 11.4% post-13D figure and its authorship; activist event counts per month; whether 13Ds appear in subject submissions payloads.
- Rutgers CEF study venue and date; CEF spreads; NAV data availability; Pontiff 1995 specifics.
- Alpaca fractionable coverage for small caps, fresh spin-offs and CEFs.
- Invesco's 2023-2025 86% vs 43% figure.
- Spin-off counts per year since 2016.

## Proposed ladder-ledger entries (owner or orchestrator to add; rule L2)
- **Batch:** 2026-10-27 · agent · 5 proposed (+2 dropped before ranking: dividend initiations, SEO/ATM) · admitted to rung 1 by desk arithmetic: 5 (C5 failed rung 1 at the desk) · this report.
- **L-rows** for C1-C5 when any rung-1-to-3 number is first computed. C5's row is already computed here: rung 1 failed, at 2.6-5.3 bp per side break-even.

## Follow-up questions (not answered here)
- Should the backtest report add excess over RSP and over the own-universe equal-weight return beside SPY (a reporting change; ADR 0005 unchanged)?
- Does Alpaca fractional trading cover the small caps these events land in? This is a rung-3 gate for C1-C4 at $100.
- Is a wider universe (below the default liquidity floors) justified at admission for C1-C3 (ADR 0024 item 2)?
- For C4: are CEFs in scope for the store and universe at all?

## Sources
- Bartov & Konchitchki, "SEC Filings, Regulatory Deadlines, and Capital Market Consequences", *Accounting Horizons* 2017 — https://faculty.haas.berkeley.edu/yaniv/files/Papers_Publications/SEC-Filings-Regulatory-Deadlines-Capital-Market-Consequences_Bartov-Konchitchki_AH.pdf (PDF text not extractable; figures via the next two)
- Columbia Blue Sky summary — http://clsbluesky.law.columbia.edu/2017/11/27/how-missing-sec-filing-deadlines-affects-a-companys-stock-value/
- NYU Stern summary — https://www.stern.nyu.edu/experience-stern/faculty-research/late-financial-filings-come-at-a-cost.md
- Dai, Li, Zaiats & Zhao, *J. Corporate Finance* 69 (2021) — https://ideas.repec.org/a/eee/corfin/v69y2021ics092911992100167x.html
- Johnson & So, *JFQA* 53(6) 2018 — https://ideas.repec.org/a/cup/jfinqa/v53y2018i06p2431-2464_00.html ; vendor summary https://wallstreethorizon.com/news/So
- RavenPack earnings-date paper — https://cdn-static.bizzabo.com/bizzabo.users.files/145245/251725/6341843/RavenPack%20Earnings%20Date%20Paper.pdf ; FactSet summary https://insight.factset.com/five-research-findings-on-earnings-date-timing-that-affect-trading
- McConnell, Sibley & Xu, *JPM* 2015 — https://business.purdue.edu/faculty/mcconnell/publications/The%20Stock%20Price...2013%20jpm.2015.42.1.143.pdf
- Nordic and European spin-off theses — https://gupea.ub.gu.se/handle/2077/68912 ; https://oda.oslomet.no/oda-xmlui/handle/11250/2823590
- CSD commentary — https://seekingalpha.com/article/4784447-csd-us-spin-off-etf-not-meeting-expectations ; https://portfolioslab.com/symbol/CSD
- Brav, Jiang, Partnoy & Thomas, *JF* 2008 — https://ideas.repec.org/a/bla/jfinan/v63y2008i4p1729-1775.html ; *FAJ* 2008 — https://ideas.repec.org/a/taf/ufajxx/v64y2008i6p45-61.html ; Brav, Jiang & Kim, *ARFE* 2015 — https://econpapers.repec.org/RePEc:anr:refeco:v:7:y:2015:p:579-595
- 2023 *IJBAF* activism study — https://ideas.repec.org/a/ids/injbaf/v13y2023i3p277-320.html ; "Hedge Fund Activists Might Just Be Good Stock Pickers" — https://www.institutionalinvestor.com/article/b1mm061kfjg0yk/Hedge-Fund-Activists-Might-Just-Be-Good-Stock-Pickers
- SEC 13D/G amendments — https://www.aima.org/compass/practical-guides/spotlight-on-compliance/schedule-13d-and-13g-reporting.html ; https://www.mintz.com/insights-center/viewpoints/2901/2023-10-24-sec-adopts-amendments-section-13-reporting-requirements
- DellaVigna & Pollet — https://www.nber.org/papers/w11683.pdf
- Segal & Segal, *RAST* 2016 — https://springerlink.bibliotecabuap.elogim.com/10.1007/s11142-016-9366-y ; Rawson/Twedt/Watkins summary — https://clsbluesky.law.columbia.edu/2022/11/11/how-companies-distract-investors-when-disclosing-bad-news/
- Boehme & Sorescu, *JF* 2002 — https://econpapers.repec.org/RePEc:bla:jfinan:v:57:y:2002:i:2:p:871-900 ; Chen, Chou & Lee, *JEF* 2014 — https://ideas.repec.org/a/spr/jecfin/v38y2014i4p643-657.html
- SEO critique (Lyandres, Sun & Zhang), via search summary — https://www.nber.org/system/files/working_papers/w11459/revisions/w11459.rev0.pdf
- CEF mean reversion — https://law-journals-books.vlex.com/vid/exploiting-closedend-fund-discounts-855659869 ; https://hedgefundalpha.com/strategies/closed-end-fund-discounts/
- RSP / SPY returns — https://chartrow.com/quote/rsp/monthly-returns ; https://chartrow.com/quote/spy/monthly-returns ; https://chartrow.com/quote/rsp/returns ; https://chartrow.com/quote/spy/returns ; Invesco 2023 index returns — https://invesco.com/apac/en/institutional/insights/etf/why-consider-an-sp-500-equal-weight-strategy.html
- Repo: [paper-fill costs](2026-10-25-paper-fill-costs.md), [hypothesis backlog](hypothesis-backlog.md), [beyond long-only](2026-10-10-beyond-long-only.md), [strategy discovery](2026-10-19-strategy-discovery.md), ADR 0023, ADR 0024, `src/tradepartner/config.py` (`issuer_forms`), `src/tradepartner/store/schema.py` (`filing_events`)
