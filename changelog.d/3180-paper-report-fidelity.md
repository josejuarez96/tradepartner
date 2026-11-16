- paper report prints a report-only cumulative fidelity line: cumulative paper vs trial return, gap in bp, consistent/divergent/too few periods from live paper.fidelity_* keys; excluded periods listed (#3180)
### Added
- `paper report` prints a report-only cumulative economic-fidelity line (paper vs tracking-trial cumulative return, gap in bp, verdict from the run-time `paper.fidelity_max_gap_bps` / `paper.fidelity_min_periods` keys) and lists excluded periods; per-period verdicts unchanged (#3180).
