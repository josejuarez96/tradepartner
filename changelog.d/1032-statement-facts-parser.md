- T77 (#660): parse_statement_facts (period from start/end, submissions form, tag precedence, conflicts returned, comparatives, unit and malformed counts) and the edgar.statement_* config keys, switch off.
### Added
- EDGAR statement-facts parser `parse_statement_facts` and config keys `edgar.statement_facts_enabled` (default false), `edgar.statement_tags`, `edgar.statement_forms`, `edgar.statement_units` (#660, T77).
