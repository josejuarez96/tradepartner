- #1356 fixed: Alpaca daily-bars requests on SIP cap their end at now minus alpaca.sip_delay_minutes (15), so the same-day scheduled ingest no longer hits the free plan's recent-SIP refusal.
### Fixed
- Scheduled ingest no longer fails with 'subscription does not permit querying recent SIP data': a same-day SIP bars request ends at now minus `alpaca.sip_delay_minutes` (default 15) instead of 23:59:59 UTC (#1356).
