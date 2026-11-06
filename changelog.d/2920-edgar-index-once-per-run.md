- #2920 (PR #2922): EDGAR ingest reads the filing index once per run, not once per issuer CIK; the T323a nightly hang (10-20 h) is fixed on both the walked and stored index paths.
### Fixed
- The EDGAR ingest no longer hangs for hours: per-CIK filing-index reads are grouped once per run on the walked index and read in one scan per binding on the stored index (#2920).
