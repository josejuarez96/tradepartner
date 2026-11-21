# A data-driven search on Sharadar: the protocol, pinned before any run

**Spike, never merged** (git-workflow: `spike/` branches are throwaway). Findings go to `docs/research/` in their own PR.
Written 2026-11-21 by a cloud session that has **not seen any Sharadar data**. The script was tested on synthetic data only.

## Why this is different from the 37 ideas so far
Every earlier idea was a published predictor checked on about 40 months. This asks the data what predicts, with enough
history (1998 on) to afford the protection that makes a search honest: three windows, the last two each read once.

## Windows (by the month-end a position is formed on)
| Window | Months | Use |
|---|---|---|
| Explore | 1999-01 to 2014-10 | Anything. Features are ranked, models are compared, one model is frozen. |
| Confirm | 2015-01 to 2019-12 | Read **once**, with the frozen model. The script writes the result and refuses a second read. |
| Exam | 2020-01 to 2023-11 | Read **once**, only if Confirm passed. |
Two months are left out before each boundary so no position formed in one window is held into the next.
Nothing after 2023-12 is loaded. (The project's own dead months for a new family are 2024 on.)

## Universe, each month-end
- A security with a daily bar on the market's last session of the month, unadjusted close at least $5.
- Liquidity band from the median of close x volume over the 20 sessions ending that day:
  **primary $10M to $200M** (too small for funds, tradable at our size: about 12 to 17 bp a side quoted mid-session,
  `docs/research/2026-11-02-cost-by-liquidity-band.md`); **secondary $200M and up**. Under $10M is not used.
- Common stock only, when the TICKERS table is present (its category field); delisted names are kept.
- A month with fewer than 100 names in a band is skipped for that band.

## What is predicted
The return from the close of the first session after the month-end to the close 21 sessions later (adjusted closes),
less the equal-weight mean of the same band that month. So a result is always against matched peers, never against SPY.
A name whose bars stop inside the hold exits at its last close; the report also shows the result with a further
-30% charged on every such exit.

## Features (all known at the month-end)
- **Prices and volume:** 1-month, 6-month and 12-month-less-1 returns; 60-session volatility; largest one-day return in
  21 sessions; distance from the 252-session high; the Amihud price-impact measure; change in dollar volume.
- **Fundamentals (SF1, as-reported trailing-twelve-month rows):** gross profit to assets, operating cash flow to
  assets, return on equity, accruals, asset growth, share-count growth, revenue growth, change in gross margin, debt
  growth, earnings yield, free-cash-flow yield, book to price. A row is used only from **five calendar days after its
  `datekey`**, a deliberately conservative lag.
- **Insiders (SF2):** officers' and directors' open-market purchases and sales filed in the last 90 days: distinct
  buyers, distinct sellers, net value over market value.
- **Institutions (SF3A):** quarter-on-quarter change in the number of holders and in units held, used from 50 days
  after the quarter end.
A feature whose table or columns are missing is skipped and named in the report. Features are ranked within each
month and band; a missing value takes the middle rank.

## Models (Explore only)
Two candidates, compared by walk-forward inside Explore (train on months before year Y less two months, score year Y):
1. **Ridge** on the ranks.
2. **Gradient-boosted trees**, shallow (depth 3, 200 trees, learning rate 0.05, at least 200 names a leaf), which can
   find combinations and conditions the ridge cannot.
The one with the higher walk-forward t on the primary band's net result is refit on all of Explore and frozen
(its file's SHA-256 is recorded). No other hyperparameter search is run.

## The book that is scored
Each month, the top tenth of the band by model score, equal weight, held one month. One-sided turnover is measured
from the names held; cost is 2 x turnover x cost per side.

## Pass lines, pinned
- **Confirm passes** only if, in the **primary band**, the net result at **50 bp a side** is at least **+0.20 %/month
  with t at least 2.0** over the Confirm months. The report also prints 25 bp and 12 bp, and the secondary band.
- **Exam passes** on the same line over the Exam months.
- A pass is a reason to register a family and take a forward paper exam under ADR 0016. It is not a reason to trade.

## What counts as a look
Explore is free. Confirm and Exam are one look each, enforced by the result files. Changing a feature, a window,
the universe or a pass line after Confirm has been read makes a new search: delete nothing, start a new work
directory, and count it.

## Licence
The Sharadar data stays on the owner's machine. The script prints summary statistics only and writes no vendor row
anywhere. Nothing about the data's quality or coverage is to be committed (licence section 8, ADR 0022).

## How to run
```bash
uv run python spikes/sharadar_discovery/discover.py --data <dir with the Sharadar files> --work <empty dir> describe
uv run python spikes/sharadar_discovery/discover.py --data <dir> --work <dir> explore
uv run python spikes/sharadar_discovery/discover.py --data <dir> --work <dir> confirm
uv run python spikes/sharadar_discovery/discover.py --data <dir> --work <dir> exam
```
Run `describe` first and send back its output (file names, column names, row counts, date ranges; no data values).
The column names in the script are from memory of Sharadar's documentation and need checking against it.
