- paper status, status --all and the Operations page show 'EDGAR data N sessions old' after ingest.edgar_max_age_sessions (default 3) sessions without an ok EDGAR run; display only, paper keeps trading (#2197)
### Added
- Paper status and the Operations page warn when the EDGAR master has not refreshed for ingest.edgar_max_age_sessions sessions (default 3, 1 to 20); paper keeps trading (#2197)
