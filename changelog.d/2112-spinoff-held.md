- T261 (#2112): a held spin-off no longer halts on a .WI child (parser resolves the root; reconcile allows a .WI child until regular-way) or on its ex-date fill band (one-sided); spin-off cash in lieu is #2122 (hold)
### Fixed
- Paper reconcile: a spin-off child named `X.WI` is stored under `X` and allowed while the broker holds it when-issued, and a fill across the parent's spin-off ex-date is judged on one side of the price band (#2112).
