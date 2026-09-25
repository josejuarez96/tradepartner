# Hypothesis: <title>

<!--
Copy to docs/hypotheses/<slug>.md. Merging the file does not register it: the owner runs
`tradepartner hypothesis register docs/hypotheses/<slug>.md` after merge (spec req 10,
docs/specs/backtest.md). The registry hashes the whole file, so any later edit, prose
included, makes a new hypothesis. Settle the text before registering.
-->

**Family:** <from `hypotheses.families`>  ·  **Author:**  ·  **Date:** YYYY-MM-DD

## Economic rationale
<!-- Why the effect should exist and persist, with citations to primary sources
(research reports in docs/research/ and the papers they grade). -->

## Parameters

The block below is the only part the registry parses. Rules (`backtest/hypothesis.py`):
- `slug` equals this file's name without `.md`; `family` is one of `hypotheses.families`.
- It must name `in_sample_start`, `holdout.start`, `holdout.end` (TOML dates,
  `in_sample_start` before `holdout.start`) and **every** `strategy.*` and `costs.*` key.
  The holdout never comes from live settings.
- It may pin any other frozen key: `universe.*`, `execution.fill_price`, `backtest.*`,
  `adjust.*`, `master.*`, `gap.*`, `metrics.*`, `benchmarks`, `alpaca.historical_feed`.
  Frozen keys it leaves out take the live config values at registration and are
  printed with the rest. Any other key is refused.
- Keep other fenced blocks out of the parameter block; its first bare ``` line closes it.
- Re-registering an older version of the file is refused: runs use the latest registration.

```toml hypothesis
slug = "<slug>"
family = "<family>"
title = "<title>"
in_sample_start = YYYY-MM-DD

[holdout]
start = YYYY-MM-DD
end = YYYY-MM-DD

[strategy]
formation_months = 12
skip_months = 1
top_fraction = 0.10
weighting = "equal"
signal_total_return = true

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]
```

Owner answers that set these values (spec open questions), one line each:
-

## Expected magnitudes and red flags
<!-- Net excess return prior and range, single-year gaps, worst quarter, tracking error,
from the cited sources. What result would suggest a bug or look-ahead rather than an edge
(`metrics.red_flag_excess_cagr_pp`). -->

## Power arithmetic
<!-- In-sample and holdout months, tracking error, and the t-statistic a plausible excess
would reach (ADR 0005). Say plainly if the test cannot reach significance. -->

## Prior-evidence disclosure
<!-- Every result for the holdout period already seen before registration: papers, fund
fact sheets, live windows. "None" only if true. -->

## Retirement condition
<!-- The result that retires this hypothesis, stated before any run. -->
