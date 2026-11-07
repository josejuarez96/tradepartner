# Research Report: equity issuance desk read

**Brief:** #2669  ·  **Date:** 2026-10-30  ·  **Status:** COMPLETE  ·  **Agent/model:** team codexissue, Codex; local read-only DuckDB probe

## Answer

**Verdict:** `CompEquIss` **FAIL**; `ShareIss1Y` **PASS** on the intended size-screened desk line; `NetEquityFinance` **FAIL** as a check  ·  **Confidence:** low for transfer to TradePartner; medium for the arithmetic on these supplied CZ series. Share issuance's size-screened high quintile clears the long-leg and L/S lines in 2009+, 2016+ and 2020+. Composite issuance's size-screened long leg falls to +0.003 %/month (t 0.02) in 2016+ and +0.027 (t 0.12) in 2020+. A PASS here only keeps the idea for feasibility and a registered test; it is no finding of tradable net alpha.

## Frozen kill line — written 2026-10-30 23:35:22 EDT, before opening either return file

For each `CompEquIss` and `ShareIss1Y` in each Chen–Zimmermann file and window, PASS requires the favorable long decile's excess over the equal mean of ten deciles to be at least **+0.10 %/month** with **t ≥ 1.0**, and the long–short spread to exceed the cost line. A strict shortfall on any condition is FAIL; missing or non-comparable data is INSUFFICIENT. For `ShareIss1Y`, the favorable low-issuance side may be decile 01, so direction and the supplied `LS = 10 − 01` sign must be shown explicitly rather than silently inverted. This adopts the I1 desk-read shape in the [backlog](hypothesis-backlog.md#i1-investment-cluster-asset-growth-twin-of-q1-desk-read-first); the windows requested here are 2009+, 2016+, and 2020+.

**Turnover assumption:** one-sided turnover **100% per year** for each leg, or 1/12 per month, an estimate for an annual-ish shares signal from I1's cost calculation (not measured). With two legs and entry plus exit, gross monthly drag = 2 legs × 2 sides × (1/12) × per-side cost. Short borrow = 30 bp/year ÷ 12 = 2.5 bp/month. Apply the requested ×2 margin to the sum. At +24.2 bp/side (all names), the line is 2 × [(4 × 24.2 ÷ 12) + 2.5] = **21.13 bp/month = 0.2113 %/month**. At the requested +15.6 bp/side liquidity sensitivity, it is 2 × [(4 × 15.6 ÷ 12) + 2.5] = **15.4 bp/month = 0.154 %/month**. The 15.6 figure is the *buy-side* over-$200M figure in the [fill study](2026-10-30-paper-fill-cost-arrival.md), not that study's ADR-matched per-side cost. The [ADR 0023 amendment](../decisions/0023-break-first-fast-books-paper-exam.md#amendment-2026-10-30-2667-one-rung-1-cost-rule) requires arrival-relative, order-equal buy/sell mean, registered liquidity band and rebalance days only. The study's over-$200M rebalance-day figure under that rule is **8.8 bp/side**: 2 × [(4 × 8.8 ÷ 12) + 2.5] = **10.87 bp/month = 0.1087 %/month**. Its against-open counterpart is 24.2 bp/side; opening drift is excluded by the rule but disclosed. All-name rebalance days alone were 17.0 bp/side; +24.2 is the requested all-order stress proxy, not an ADR-matched measurement. A live candidate with no own fills therefore has a provisional cost line.

## Evidence

The read-only script is `/Users/josejuarez/tradepartner-probes/issuance/compute.py`; its output is `out.txt` in that directory. DuckDB read the two local parquets without changing them. `ret` is percent per month; t is the ordinary sample mean divided by its standard error (no HAC correction). The latest month in both is **2025-12**. `LS` equals high minus `01` to floating-point precision for every included month. In the value-weighted file high is `10` and the benchmark is the equal mean of ten deciles. In the size-screened file `CompEquIss` and `ShareIss1Y` have **five** portfolios, so high is `05` and the benchmark is the equal mean of five; `NetEquityFinance` has ten. There is no ten-decile size-screened read for the first two signals.

Each cell below is **mean %/month (t)**. Long means high minus the equal portfolio mean; short means equal portfolio mean minus `01`. Positive-month share and worst rolling 12-month compounded L/S return are after the t columns. The latter is a rolling one-year loss, used here as the requested 12-month drawdown measure.

| File / signal | Window (months) | L/S | Long | Short | Positive months | Worst 12m |
|---|---|---:|---:|---:|---:|---:|
| VW CompEquIss | 2009–25 (204) | 0.724 (2.44) | 0.556 (2.45) | 0.168 (1.15) | 57.4% | −29.4% |
| VW CompEquIss | 2016–25 (120) | 0.773 (1.69) | 0.582 (1.65) | 0.191 (0.86) | 53.3% | −29.4% |
| VW CompEquIss | 2020–25 (72) | 0.822 (1.20) | 0.830 (1.54) | −0.007 (−0.02) | 54.2% | −29.4% |
| Screen CompEquIss | 2009–25 (204) | 0.325 (1.80) | 0.183 (1.59) | 0.142 (1.23) | 55.4% | −21.1% |
| Screen CompEquIss | 2016–25 (120) | 0.043 (0.18) | 0.003 (0.02) | 0.040 (0.24) | 52.5% | −21.1% |
| Screen CompEquIss | 2020–25 (72) | −0.065 (−0.18) | 0.027 (0.12) | −0.092 (−0.38) | 51.4% | −21.1% |
| VW ShareIss1Y | 2009–25 (204) | 0.671 (2.48) | 0.162 (1.44) | 0.509 (2.49) | 62.7% | −34.0% |
| VW ShareIss1Y | 2016–25 (120) | 0.799 (1.96) | 0.248 (1.51) | 0.551 (1.76) | 65.8% | −34.0% |
| VW ShareIss1Y | 2020–25 (72) | 0.662 (1.02) | 0.202 (0.84) | 0.459 (0.92) | 58.3% | −34.0% |
| Screen ShareIss1Y | 2009–25 (204) | 0.350 (1.68) | 0.134 (1.61) | 0.216 (1.58) | 53.4% | −20.1% |
| Screen ShareIss1Y | 2016–25 (120) | 0.380 (1.20) | 0.155 (1.34) | 0.225 (1.05) | 51.7% | −20.1% |
| Screen ShareIss1Y | 2020–25 (72) | 0.559 (1.20) | 0.260 (1.54) | 0.300 (0.94) | 54.2% | −20.1% |
| VW NetEquityFinance | 2009–25 (204) | 0.147 (0.42) | 0.156 (1.06) | −0.009 (−0.03) | 53.4% | −35.4% |
| VW NetEquityFinance | 2016–25 (120) | 0.204 (0.38) | 0.282 (1.40) | −0.078 (−0.19) | 54.2% | −35.4% |
| VW NetEquityFinance | 2020–25 (72) | −0.012 (−0.01) | 0.222 (0.75) | −0.234 (−0.36) | 54.2% | −35.4% |
| Screen NetEquityFinance | 2009–25 (204) | 0.315 (1.21) | 0.083 (0.82) | 0.231 (1.10) | 53.9% | −26.9% |
| Screen NetEquityFinance | 2016–25 (120) | 0.256 (0.64) | 0.085 (0.61) | 0.171 (0.52) | 51.7% | −26.9% |
| Screen NetEquityFinance | 2020–25 (72) | 0.272 (0.47) | 0.058 (0.30) | 0.214 (0.44) | 54.2% | −26.9% |

**Verdict application.** The intended decision series is size-screened, as in Q1 and I1's declared desk reads. `ShareIss1Y` clears +0.10 and t 1.0 on its high quintile even in 2020+, and its +0.350/+0.380/+0.559 L/S means clear both the requested 0.154% liquidity proxy and the ADR-matched 0.1087% line. `CompEquIss` clears on the full size-screened span but fails both later spans and the 2020+ cost line. The VW 2020+ `ShareIss1Y` long t 0.84 fails that cut's own frozen line; it is a recorded disconfirmation, not the intended size-screened decision series. The NetEquityFinance check fails the long-leg t line on the full size-screened span and the 2020+ L/S cost line in VW.

**Data feasibility.** The local `SignalDoc.csv` definitions are: `CompEquIss`, five-year growth of market equity minus five-year stock return; `ShareIss1Y`, growth in split-adjusted shares from t−18 to t−6 months; `NetEquityFinance`, (common-stock sales `sstk` minus repurchases `prstkc`) divided by average total assets in years t and t−1, excluding absolute ratios over one. For `ShareIss1Y`, at each formation `t`, read the latest `shares_outstanding` cover/dei fact whose acceptance-based `known_at ≤ t−6m`, and the latest corresponding fact at `t−18m`, through `facts_as_of` and the as-of security mapping; adjust both counts with split events known by `t` and effective by `t`, then form their growth. The six-month lag is part of the CZ definition and must not be replaced by current shares. For `CompEquIss`, read shares through `facts_as_of(t)` and raw daily close through `prices_as_of(t)` to form market equity, repeat at t−5y, and compute the split-adjusted stock price return over that span from the 2016+ bars and splits, using only actions effective and known at each read; subtract the return from market-equity growth. A five-year signal cannot be formed before five years of held daily bars, so the earliest 2016+ history makes its practical start about 2021; contemporaneous cover shares can be noisy and class-mapped differently from CRSP shares. `NetEquityFinance` cannot be replicated: the store's stated facts do not include `sstk` and `prstkc`; cover shares and split events cannot distinguish issued shares from repurchases, compensation, conversions, or class changes. Each candidate also needs `universe_as_of(t)` and a 20-session median raw close × volume from daily bars with `known_at ≤ t` for a daily or weekly book's $200M floor; a monthly book must state its registered universe explicitly. Store delister coverage is thin before 2020 per I1, and the CZ size screen is NYSE 20th percentile market equity, **not** that $200M dollar-volume floor.

## Disconfirmation

- McLean–Pontiff's publication-decay result (QI-2) gives a roughly half-sized prior; Chen–Welch 2026's luck-adjusted non-microcap post-2005 prior of about **0.07%/month** (Tier 2, from the [frontier report](2026-10-28-strategy-frontier-verified.md)) is below even the ADR-matched 0.1087% margin line. The observed CZ means are selected outcomes, not an unbiased prior or a TradePartner net return.
- Composite issuance's size-screened spread falls from +0.325 overall to +0.043 in 2016+ and −0.065 in 2020+, despite VW staying above +0.7. Share issuance has a VW 2020+ long t under 1; its screened 2016+ long is only +0.155, barely over the requested 0.154 cost proxy if compared as a standalone leg (the frozen spread test uses the two-leg spread).
- The L/S books show one-year rolling losses of 20–34% on the candidate series. Monthly positive shares near 52–54% in the screened series leave substantial path and capacity risk.

## Caveats & gaps

- These are CZ gross portfolio returns, not an execution simulation; no financing, borrow availability, actual turnover, real $200M liquidity cut, or point-in-time TradePartner universe is present. The +24.2 and +15.6 bp cost inputs are requested stress proxies; the only ADR-matched liquid rebalance figure available is +8.8 bp, from three paper sessions and order rather than issuer weights.
- The t statistics ignore serial correlation and selection across searched predictors. The five-portfolio screen and ten-decile VW sort are different constructions. The 2024–25 returns are after ADR 0016's development boundary and overlap its shared historical exam; they cannot be treated as a fresh TradePartner holdout after this desk read.
- The CZ `LS` ordering is verified as high minus low. The `Sign = −1` in SignalDoc is direction metadata; operationally `ShareIss1Y` here means favoring the high portfolio as supplied. Reconcile its economic labeling with the original signal sign and sorting convention before registration; the Q1 low-issuance long-only proposal is not established by this high-side read.

## UNVERIFIED items

- Store coverage counts, exact per-name shares/split histories, delister inclusion, actual one-sided turnover, borrow locates and live cost for the proposed book; no store return was run.
- Whether the local CZ high portfolio is indeed low issuance after sign inversion, and whether its five-year/one-year constructions can be matched exactly by the cover shares and available split records.

## Sources

- Local Chen–Zimmermann parquets and `SignalDoc.csv` paths stated in #2669; read with the external script and saved output above, no network access.
- [I1 backlog and ladder ledger](hypothesis-backlog.md#i1-investment-cluster-asset-growth-twin-of-q1-desk-read-first); [Q1 pre-analysis](2026-10-19-q1-repurchasers-rung12.md); [ADR 0023 cost amendment](../decisions/0023-break-first-fast-books-paper-exam.md#amendment-2026-10-30-2667-one-rung-1-cost-rule); [paper fill cost study](2026-10-30-paper-fill-cost-arrival.md).
- [Research-program registration defaults](../ways-of-working/research-program.md#registration-defaults); [ADR 0016 point 8](../decisions/0016-development-boundary-and-forward-exams.md); [data-foundation as-of API](../specs/data-foundation.md); [frontier report](2026-10-28-strategy-frontier-verified.md).
