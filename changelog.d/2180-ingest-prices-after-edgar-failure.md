- Ingest runs every source even when EDGAR fails, so an SEC outage no longer halts both books stale_data; exits 1 if any source failed; runbook says what to do (#2180, #2181)
### Fixed
- tradepartner ingest no longer skips the Alpaca price source after an EDGAR failure; the exit code and heartbeat still report the failure (#2180)
