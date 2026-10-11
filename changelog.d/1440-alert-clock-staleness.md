- #1440 (owner calls from #649): run alerts stamped by the gate clock (raw-clock fallback noted), run_failed with no run id before the run row, staleness and clock floor read only the price source's daily ingest.
### Fixed
- Paper run: alerts are stamped by the risk gate's checked clock (a failed reading falls back to the raw clock with a note), a clock or store fault before the run row now sends a run_failed alert, and only Alpaca's daily ingest runs count for staleness and the clock floor (#1440).
