- #1221: sweep_report reads variant state from lab_queries.variant_states (one classifier with the runner's plan), with a parity test.
### Fixed
- The sweep report and `lab status` classify variants with `store.lab_queries.variant_states`, the runner's own rule, instead of a diverging copy (#1221).
