task: T306 (PR #2562)
- T306 (#2539): schema 40 adds sec_filing_events and sec_form4_transactions with acceptance-time reads behind events.sec_forms_read_enabled (off); excluded from trial vintage.
### Added
- Schema version 40: SEC ownership tables (sec_filing_events, sec_form4_transactions) and gated point-in-time reads sec_filing_events_as_of / sec_form4_transactions_as_of (T306, #2539).
