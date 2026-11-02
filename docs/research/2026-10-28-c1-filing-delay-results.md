# C1 filing-delay results: the idea is retired

**Date:** 2026-10-28 · **Status:** COMPLETE · **Kind:** results record (no new evidence; summarizes three registered runs)

## C1 run 2 (docs/experiments/c1-filing-delay.md)

Killed. SPY was the benchmark, which the follow-up replaced with matched peers. The early-filer long was -79 bp gross against SPY at 60 sessions (t -2.1) and the late-filer short survived at +98 bp net (t 1.37), but all three terciles lagged SPY and the causal read reversed. The result was size and market beta, not filing delay.

## C1b run 3 (docs/experiments/c1b-filing-delay-matched.md), 14,589 events

Long early-minus-matched 60-session spread +5 bp gross, t_min -1.46, killed. Short late-minus-matched net -58 bp after costs, t_min -2.78, killed. Both lines killed on all four readings (raw, beta-adjusted, size-tight, size-tight beta-adjusted). Artifact: data/research/c1b-filing-delay-matched/c1b-filing-delay-matched-run3.json (local, not in git).

## C1c run 4 (docs/experiments/c1c-filing-delay-delisted.md)

C1b's design rerun on a vendor side dataset that includes delisted names (ADR 0022 amendment). Both lines killed both with and without delisted names; C1b's verdict stands. No vendor-derived figures here (licence); the full output is local in ~/tradepartner-probes/sharadar/.

## Conclusion

The filing-delay idea is retired.
