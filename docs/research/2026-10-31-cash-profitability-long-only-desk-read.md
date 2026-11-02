# Research Report: candidate 5 cash operating profitability desk read

**Brief:** TradePartner #2689, candidate 5 · **Date:** 2026-10-31 · **Status:** COMPLETE · **Audit:** [quant-auditor PASS](https://github.com/josejuarez96/tradepartner/issues/2689#issuecomment-6548223852) · **Source:** [frozen kill line](https://github.com/josejuarez96/tradepartner/issues/2689#issuecomment-6548103186), probe `~/tradepartner-probes/cbl/probe.py`, output `~/tradepartner-probes/cbl/out.txt`

## Answer

**Verdict: FAIL.** The frozen combine rule requires positive mean monthly net excess over SPY, an ordinary SPY-excess t of at least 1.0, and positive mean monthly net excess over the equal-weighted point-in-time universe. The 40 observed return months have **−0.0881% per month versus SPY (t = −0.238)** and **−0.0334% per month versus EW**. All three conditions fail. The 2× cost result is report-only and does not affect the verdict. This is a coverage-only research read outside ADR 0016 point 8's fixed 2024-01-01 through 2026-09-30 historical exam interval; it is not a registered exam or unseen holdout.

## Evidence

The probe used the 2026-10-31 04:41 store with `duckdb.connect(..., read_only=True)`. It scored 41 signal months from August 2020 through December 2023; the December 2023 signal has no subsequent return observation, leaving **40 monthly returns**. The pre-return floor was met in every signal month: 396–419 scored names and 80–84 selected top-quintile names. The book uses next-session open execution, equal weights, and 24.2 bp per side on traded notional; the EW comparator pays the same base cost. The ordinary t printed by the probe is the sample-variance t without HAC.

| Book and comparator | Mean monthly net excess | Ordinary t | Return months | Positive share | Worst rolling 12m | Observed turnover | Names held |
|---|---:|---:|---:|---:|---:|---:|---:|
| Base, vs SPY | −0.0881% | −0.238 | 40 | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| Base, vs EW universe | −0.0334% | UNVERIFIED | 40 | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| Report-only 2× cost, vs SPY | −0.1803% | −0.489 | 40 | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |
| Report-only 2× cost, vs base-cost EW universe | −0.1256% | UNVERIFIED | 40 | UNVERIFIED | UNVERIFIED | UNVERIFIED | UNVERIFIED |

The 2× stress charges the candidate book 48.4 bp per side while the script keeps the EW comparator at 24.2 bp per side. The 80–84 figure is **selected names**, not an observed count of executable holdings. The probe does not print the EW t, positive shares, rolling 12-month results, traded notional or turnover, or executable held-name counts. No additional return cut was computed to fill those cells.

**Literal rule application:** Base SPY excess is below zero; its t is below 1.0; base EW excess is below zero. Therefore FAIL. The cost stress also has negative excess means, but it is a sensitivity only.

## Disconfirmation

- The observed negative net excess against both comparators disconfirms the candidate's frozen promotion claim over this window. The ordinary SPY t is also below the threshold.
- B3's different gross-profitability book was already retired on the same August 2020–December 2023 interval after −3.28 percentage points per year net excess CAGR versus SPY and a run-time DSR below 0.5. That is family context, not an independent test of this cash operating-profitability formula or this cost and execution rule. See the [B3 retirement record](2026-10-18-b9-kill-b3-retirement.md).
- The 2× cost sensitivity worsens SPY excess to −0.1803% per month and EW excess to −0.1256% per month. It cannot reverse the base verdict.

## Caveats

- **Zero fills:** 16,604 of 16,727 scored name-months (**99.3%**) used at least one zero-filled component. Component shares were R&D 64.8%, receivables 25.0%, inventory 15.1%, prepaids 83.5%, deferred revenue 93.1%, payables 17.7%, and accruals 52.1%. This owner-approved construction for candidate 5 differs from the prior local XBRL guidance to preserve missingness; equivalence to the Chen–Zimmermann signal remains unverified.
- **Delistings:** The engine uses a last traded close and leaves proceeds in cash. The crash fix described below settles a previously close-marked holding at that stored value when the following price query has no usable last close. It does not apply a failure-return shock, and the outcome's sensitivity to that convention is unmeasured.
- **Window overlap:** The signal window is B3's registered August 2020 start through December 2023, and overlaps B3's historical trial. B3's known result on these months is −3.28 percentage points per year net excess CAGR versus SPY (7.78% versus 11.06%) and 10.4% monthly turnover for its different book. This read is neither independent replication of B3 nor an unseen store holdout.
- **Inference:** The printed t uses an ordinary sample standard error, with no HAC, serial-correlation, or selection adjustment. The 24.2 bp per-side cost is a provisional proxy, not observed execution cost for this book. Actual turnover and executable holdings are not printed.

## Exploratory-cut ledger

**No extra return cuts.** The frozen kill line records earlier coverage-only dry runs, including the original 2017-07 start diagnostic and the pinned 2020-08..2023-12 coverage run; those read no candidate return or price change. In this step, every launch is listed below. The initial script hash matched the frozen SHA-256 `916339eea57d431558532680662516285cdf16b6e3c0c52edad69086089ba770` before any launch.

| Launch | Result and exposure |
|---|---|
| 1, exact prescribed command | `uv` failed before Python started because `/Users/josejuarez/.cache/uv` was inaccessible. No probe or return read. Its error was written to `out.txt`, later overwritten. |
| 2, same command with `UV_CACHE_DIR=/private/tmp/cbl-uv-cache` | Printed coverage, entered the return loop, then crashed before summary output: `RuntimeError: no last close for held 0001235010`. This **counts as return exposure** even though no return statistic printed. Its output was later overwritten. |
| 3, same command after one crash-only edit | Completed and printed the base verdict and 2× stress in `out.txt`. This **counts as a second return exposure**. No alternate window, signal, threshold, comparator, or cost was tried. |

**Crash-only edit:** In `execute_month`, the final `else` for a held name without a usable previous or later close now adds its already prior-close-marked `value` to cash instead of raising. This implements the frozen last-close cash treatment for the missing-bar case and changes no pin, formula, window, threshold, or selection. The edited probe `~/tradepartner-probes/cbl/probe.py` SHA-256 is `bb32fdb0587cfecc35e9cafd41926ab9d760c19271364fdebe55463b357fb08b`. There were no other edits to the probe and no further runs.

## UNVERIFIED

- The EW-excess ordinary t, monthly positive shares, worst rolling 12-month returns, actual turnover, and executable held-name counts: `probe.py` does not print the underlying monthly series or these summaries. The table marks every unavailable cell accordingly; calculating them would require an unapproved additional cut or probe change.
- The size and direction of the last-close crash fix's effect on the printed return statistics, and a failure-return shock for delisted holdings.
- Equivalence of this first-vintage XBRL formula and the Chen–Zimmermann supplied cash operating-profitability signal; whether quality or beta adjustment changes the result; realized fill costs.
