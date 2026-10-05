- T76b: statement_facts_as_of(T) joins through securities_as_of(T) on cik; no-look-ahead checks for the join and two broken-adapter known_at proxies land (#885).
### Added
- statement_facts_as_of(conn, T, security_ids=None) (store/asof.py, #660, T76b): statement_facts rows known by T, joined through securities_as_of(T) on cik, one row per (security_id, fact_name, period_end, period_days); no-look-ahead checks cover the join and two broken-adapter known_at proxies (period_end's close, midnight of filed).
