# Q1 (repurchasers' long leg): rungs 1 and 2, written before any read

**Date:** 2026-10-19  ·  **Issue:** #1862  ·  **Kind:** pre-analysis (desk note). **No data was read from the store, no backtest ran, nothing is registered, and no Chen-Zimmermann file was opened.** Every number below is arithmetic on a stated assumption or a figure from the literature, graded at the end.  ·  **Parent:** Q1 of the [strategy-discovery report](2026-10-19-strategy-discovery.md#q1-the-first-falsifiable-question-after-b10-sketched-not-run)  ·  **Ledger row:** Q1 in the [ladder ledger](hypothesis-backlog.md#ladder-ledger) (looks so far: 0)

## The idea

In the [ADR 0006](../decisions/0006-universe-and-cadence.md) top-1,000 universe, hold an equal-weight long book of the lowest-net-share-issuance quintile (the largest net repurchasers over 12 months), about 60 names, rebalanced monthly.
- **Signal:** log change in split-adjusted shares outstanding between the latest fact known at t and the latest known at t - 12 months (`known_at` = acceptance).
- **Origin:** extension (P: Pontiff-Woodgate 2008, Daniel-Titman 2006; X: our acceptance-timed, split-adjusted shares fact).
- **Mechanism:** managers retire shares when they judge them cheap, and the market underreacts. Its non-return prediction (rung 2-O, only if rung 2 cannot decide): repurchasers' next-four-quarter operating cash flow over assets is no worse than issuers'.

Rungs use [ADR 0023](../decisions/0023-break-first-fast-books-paper-exam.md)'s definitions: rung 1 is cost break-even (needed edge = 2 x one-sided turnover x measured cost per side, against the published long-leg proxy); rung 2 is the long-only leg's sign and size in published or open data, post-2010. Per rule L4 of the ledger, the public read for rung 2 ends 2019-12.

## Rung 1: cost arithmetic

Break-even per side = long-leg gross per month / (2 x one-sided monthly turnover).

| Assumed long-leg gross (per month) | Turnover 8% | Turnover 12% | Turnover 20% |
|---|---|---|---|
| 0.10 % (10 bp, the rung-2 floor) | 62 bp | 42 bp | 25 bp |
| 0.05 % | 31 bp | 21 bp | 12 bp |
| 0.02 % | 12 bp | 8 bp | 5 bp |

- **Turnover (UNVERIFIED, an estimate).** The signal moves only when a new 10-Q or 10-K shares fact arrives, so most of a quintile's membership is stable between quarters. The 8-12 %/month range is an analogy to B3's measured 10.4 %/month (trial 3), a slow quarterly fact-driven tilt. A 12-month change measured on noisy cover-page share counts could churn more, hence the 20 % column. Rung 1 gets its real number only from the store's signal, which this note does not read.
- **Measured cost.** About 6 bp per side from Probe 3's median open-fill slippage (n = 4, small) plus a 5-10 bp half-spread (HO-14, UNGRADED), so roughly 11-16 bp, taken here as 15 bp.
- **Kill line, as the discovery report declared it:** break-even <= 3 x measured cost per side, so about 45 bp. At a 10 bp long leg the 8 % column clears (62 bp) but the 12 % column (42 bp) falls just under the 3x line, so even the floor-level long leg is borderline at the upper turnover guess. At 5 bp gross every column fails the 3x line, and only the 8-12 % columns pass a bare 15 bp line.
- **Reading.** Rung 1 is not where Q1 lives or dies: it is decided by the long leg's size (rung 2's question) and by the turnover. The only way rung 1 alone kills Q1 is turnover above about 30 %/month, which a 12-month share-count signal should not produce unless the fact feed is noisy (the failure the report's D1 residue names).
- **Status: pending rung 1 numbers.** It needs the store's measured turnover and B10's two-week fill read, both outside this note.

## Rung 2: public evidence on the long leg

Two literatures bear on this idea and are not the same thing.
1. **Repurchase announcements** (event studies).
2. **Net share issuance as a cross-sectional sort** (what Q1 actually trades; Chen-Zimmermann `ShareIss1Y` is this sort).

### For a positive long leg

| Source | Finding | Relevance | Grade |
|---|---|---|---|
| Ikenberry, Lakonishok, Vermaelen, JFE 1995 | Open-market repurchase announcements 1980-1990 are followed by large positive abnormal returns over four years (about 12 % on average from memory, far larger for high book-to-market firms and near zero for glamour firms) | Original event study; announcements, not realised net shrinkage; pre-1991 | M, figures from memory, not re-fetched |
| Peyer and Vermaelen, RFS 2009 | The anomaly had not disappeared in 1991-2001 (3,481 announcements); excess returns as large as before, concentrated in value and small stocks and where managers cited undervaluation | Replicates out of the original sample, still pre-2002 and small-stock-heavy | M; secondary summaries only for the magnitudes |
| Pontiff and Woodgate, JF 2008 | One-year change in shares outstanding predicts cross-sectional returns, not explained by size, book-to-market or momentum | The signal Q1 uses; sample to 2003 | M, magnitudes not re-checked |
| Daniel and Titman, JF 2006 | Composite (5-year) issuance predicts returns beyond book-to-market | Close cousin; the 5-year variant is declared as not read | M |
| Fama and French, JF 2008 | Net issues are among the anomalies that hold in big stocks as well as small, 1963-2005 | Supports a long-leg effect in large caps; pre-2010 | M |

### Against, and post-publication

| Source | Finding | Relevance | Grade |
|---|---|---|---|
| Fu and Huang, Management Science 2016 | Long-run abnormal returns after both buybacks and seasoned offerings disappear for events in 2003-2012; linked to institutional ownership, lower trading costs and better disclosure | The direct post-2002 disconfirmation, for announcements | M (one study; announcements, not the issuance sort) |
| Mitchell and Stafford, JB 2000 | Long-run event-time abnormal returns after buybacks (and offerings) shrink or vanish with calendar-time factor methods and a joint-dependence correction | Method challenge to the original anomaly; from memory | M, not re-fetched |
| McLean and Pontiff, JF 2016 | Anomaly returns 26 % lower out of sample and 58 % lower after publication (net issuance is among the 97) | Generic decay applies to Pontiff-Woodgate (2008) | M (QI-2, UNGRADED in our register) |
| Stambaugh, Yu and Yuan, JFE 2012 | Anomaly profits, net issuance included, concentrate in the short leg and after high sentiment | The premium sits in the issuers, not the repurchasers (LS-20 I) | M |
| Hou, Xue and Zhang, RFS 2020 | 65 % of 452 anomalies fail |t| >= 1.96 once microcaps are controlled | Generic; the net-issuance result was not isolated here | M, generic only |
| Muravyev, Pearson and Pollet (LS-6, SUPPORTED) | Anomaly profits are carried by the short leg and vanish after borrow fees | Relevant only to a long-short follow-up | S |

### What the literature settles, and what it does not

- **It settles the direction of the prior.** Evidence for repurchasers outperforming is strongest before 2002 and in value and small stocks. Post-2003 event evidence (Fu-Huang) and the post-publication decay (McLean-Pontiff) both point to a smaller effect now. The premium that survives (Stambaugh-Yu-Yuan, LS-20) sits mostly in the issuers, which a long-only book cannot use.
- **It does not settle the long leg's size for our window.** No source reached reports the size-screened, large-cap, long-only leg of the lowest-issuance quintile for 2010-2019 net of nothing but gross. That number is what the rung-2 read measures, and it is the only thing that can decide Q1. This is the same gap that parked IRRX, except that a Chen-Zimmermann series (`ShareIss1Y`) very probably exists here. Its exact file name and size-screen variant are UNVERIFIED until the read.
- **Prior (a guess, not a measurement).** A long leg below the 0.10 %/month line is more likely than not; about 60-70 % kill, as the discovery report stated. Nothing here changes that.
- **Disconfirming reading kept in view.** Jensen, Kelly and Pedersen (JF 2023) argue published factors replicate better than the multiple-testing haircuts imply, and Fama-French 2008 puts net issues among the large-cap survivors. If the read passes, those are the studies that would support it.

### Rung 2: the declared read (to run later, by the owner or a research window)

- **One series:** Chen-Zimmermann `ShareIss1Y`, size-screened file (excluding names below the NYSE 20th size percentile, B9's precedent), long-leg (lowest-issuance) portfolio, gross.
- **Window:** 2010-01 to 2019-12 (L4).
- **Kill if** the long leg is below 0.10 %/month **or** t is below 1.0 (the B9/IRRX line).
- **Not read:** the value-weighted file, `ShareIss5Y`, or any later window. Reading any of them adds a ledger row and is disclosed as a forking path. If the declared file turns out not to exist under that name, the substitute is chosen and written down before any number is opened.
- **Post-2019 decay** is not seen until rung 4 (the cost L4 accepts).

## Kill condition

Q1 is killed at rung 1 or 2 if any of these holds:
1. Break-even per side at the store's measured turnover is at or below 3 x measured cost per side (about 45 bp);
2. the declared long leg (2010-2019) is below 0.10 %/month or t is below 1.0.

If rung 2 passes, Q1 goes to rung 3 (about 60 names, fractional orders, 6-10 trades a month, expected to pass) and then a root family `issuance` at rung 4, which can kill but cannot confirm in 40 months. If it dies, with B3 it retires slow fundamental long-only tilts in this universe, and if B10 also dies the ADR 0023 revisit reads that the long-only constraint, not the idea supply, binds. The owner has since lifted that constraint (2026-10-19, ADR 0024 in progress), so a failed long leg would send Q1's short leg (issuers) to the pool, still through the ladder.

## Verdict

**Pending rung 1 numbers.** The literature alone does not settle Q1: it lowers the prior (post-2002 decay, short-leg concentration) but gives no long-leg figure for the size-screened 2010-2019 window, and the cost margin depends on a turnover that only the store can measure.

## Uncertainty kept

- Figures marked "from memory" (Ikenberry et al., Mitchell-Stafford, Pontiff-Woodgate, Daniel-Titman, Fama-French) were not re-fetched for this note; only Peyer-Vermaelen and Fu-Huang were checked against abstracts and secondary summaries. Verify before any is relied on in a claim.
- The 8-12 % turnover is an analogy, not a measurement; the measured cost rests on four fills.
- Announcement studies (programs, intent) differ from realised net share shrinkage (what the sort uses); evidence for one transfers only partly to the other.

## Sources

- Ikenberry, Lakonishok, Vermaelen, "Market underreaction to open market share repurchases", JFE 1995.
- Peyer, Vermaelen, "The Nature and Persistence of Buyback Anomalies", RFS 2009 ([RePEc](https://ideas.repec.org/a/oup/rfinst/v22y2009i4p1693-1745.html)).
- Fu, Huang, "The Persistence of Long-Run Abnormal Returns Following Stock Repurchases and Offerings", Management Science 2016 (DOI 10.1287/mnsc.2015.2150; [SMU working paper](https://ink.library.smu.edu.sg/lkcsb_research/3268)).
- Mitchell, Stafford, "Managerial decisions and long-term stock price performance", Journal of Business 2000.
- Pontiff, Woodgate, JF 2008; Daniel, Titman, JF 2006; Fama, French, "Dissecting anomalies", JF 2008.
- McLean, Pontiff, JF 2016; Stambaugh, Yu, Yuan, JFE 2012; Hou, Xue, Zhang, "Replicating Anomalies", RFS 2020 ([RePEc](https://ideas.repec.org/a/oup/rfinst/v33y2020i5p2019-2133..html)); Jensen, Kelly, Pedersen, JF 2023.
- In-repo: [discovery report Q1](2026-10-19-strategy-discovery.md), [B9/B3 note](2026-10-18-b9-kill-b3-retirement.md), [IRRX retry](2026-10-18-irrx-rung2-retry.md), claims QI-2, QI-15, HO-14, LS-6, LS-20.
