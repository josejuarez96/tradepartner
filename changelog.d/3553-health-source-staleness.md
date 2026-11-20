- health --check now fails on stale EDGAR, FRED and SEC forms by last ok run in sessions; rates staleness measured at report time (#3553)
### Fixed
- health --check fails with edgar_stale, fred_stale and sec_forms_stale when a source's last ok run is past its limit; DTB3 stale_sessions is measured at report time (#3553)
