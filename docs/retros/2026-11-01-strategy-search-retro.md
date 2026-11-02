# Retro: the strategy-search limit (6 of 6)

**Date:** 2026-11-01 · **Issue:** #2835 · **Rule:** the owner's search limit of 2026-10-30 ([#836](https://github.com/josejuarez96/tradepartner/issues/836#issuecomment-6537997869), #2667): 6 candidates or 2026-11-27, then a retro where the owner chooses **continue, change course or stop**. The sixth candidate was read on 2026-11-01, so the retro is due now. · **Decision:** the owner's, recorded on #2835.

## 1. The six results

Each kill line was frozen before any return was read. All six failed.

| # | Candidate | Read | Result | Desk read |
|---|---|---|---|---|
| 1 | Asset growth (I1) | CZ files, size-screened long leg, 2010-2019 | +0.0076 %/month against a 0.10 floor | [2026-10-30](../research/2026-10-30-i1-asset-growth-desk-read.md) |
| 2 | Equity issuance (`CompEquIss`, `ShareIss1Y`) | CZ files, 2 files × windows | Best cell +0.202 %/month long, t 0.84 | [2026-10-30](../research/2026-10-30-equity-issuance-desk-read.md) |
| 3 | B7, Form 4 insider purchases | Registered event-study feasibility | 47 events against a 400 floor | [recount](../research/2026-11-form4-insider-feasibility-recount.md) |
| 4 | Cash-based operating profitability (`CBOperProf`) | CZ files, 6 cells | Weak short leg in all six | [2026-10-31](../research/2026-10-31-cash-op-profitability-desk-read.md) |
| 5 | Long-only cash profitability vs SPY | Store, 40 months | −0.088 %/month net vs SPY, t −0.24 | [2026-10-31](../research/2026-10-31-cash-profitability-long-only-desk-read.md) |
| 6 | Net external financing (`XFIN`) | CZ files, 6 cells, 2009+/2016+/2020+ | Means positive, every cell fails a t ≥ 1 or 0.10 floor | [2026-11-01](../research/2026-11-01-net-external-financing-desk-read.md) |

Before the limit, the same story: momentum variants lost to SPY (T114), B3 gross profitability lost to SPY, B9 earnings-reaction drift died on cost (break-even 1.75 to 3.5 bp per side), C1 and C1b filing delay were killed on matched peers.

## 2. What this rules out, and what it does not

**Rules out (with the evidence we used):**
- **Slow, published fundamental anomalies in liquid US stocks as a stand-alone edge after 2009.** Five of the six were this kind. The Chen-Zimmermann files give 17 years (2009 to 2025) of post-publication returns, and the spreads that are positive are not distinguishable from zero. This matches the 2026-10-30 report: the median post-2005 large and mid-cap anomaly earns about 0.07 %/month before costs.
- **Fast signals at our current cost.** Paper fills cost +24.2 bp per side against arrival ([fill-cost note](../research/2026-10-30-paper-fill-cost-arrival.md)), which kills anything that turns the book over monthly.

**Does not rule out:**
- **A cheaper execution path.** The same note: liquid names cost +10.6 bp per side if buys go out at the open, against +17.9 bp as the run executes today (buys wait for sells to fill). Cost is something we control, and it decides which signals are viable at all.
- **Edges that come from our own data rather than a published file.** Exact SEC acceptance times for every filing (fully available after T324), point-in-time fundamentals, and a fast nightly. Published anomalies are crowded; timing-based event signals are less so, but untested here.
- **Weak signals in combination.** Every candidate was read alone. A composite of several weak, low-correlated slow signals was never tested.

**On buying older prices.** It would not have changed these six reads: the desk reads already used 17 years of published data. Older prices matter for re-running a survivor in our own store, not for finding one.

## 3. The options

| Option | What it means | Cost | What it can teach |
|---|---|---|---|
| **A. Continue** | Another batch of 6 published-anomaly desk reads | About a day each | Low: the prior is the same as the six that died |
| **B. Change course** | (1) Cut execution cost first: send buys at the open, or use opening and closing auction orders, measured on the paper books against today's +24.2 bp. (2) Then a new limit of candidates built on our own data: an earnings-announcement event family on 8-K Item 2.02 acceptance times (after T324), and one composite of weak slow signals | Cost work: a paper-spec change and a few sessions of fills. Candidates: after T324 and the fundamentals backfill | Whether a lower cost makes any signal viable; whether timing or combination does what single published signals do not |
| **C. Stop the strategy search** | Keep the machine running as a paper system; no new candidates until a new idea with its own evidence appears | None | Nothing new; the charter's six-month continue-or-stop review would decide the project |

## 4. Recommendation

**B, change course, cost first.** The one thing the evidence says we can move is cost: it decides the viable set, it is measured on our own books, and it needs no new data. Set a new limit before any read (for example, 4 candidates or 2026-12-15), with the event family and one composite as its first two. Keep the fundamentals work going (T363 to T368), because the composite and the event family both read it. Do not buy older prices yet.

If the owner chooses B, the next builds are: a paper-spec amendment for buy timing (class B), the T323 → T323b → T324 chain already in flight, and the new limit recorded in the hypothesis backlog.
