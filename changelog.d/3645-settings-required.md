### Changed
- store/asof.py: price_jumps_as_of, sec_filing_events_as_of, sec_form4_transactions_as_of and unvalued_spin_offs_as_of require settings= (no get_settings() fallback); tests/test_settings_boundary.py ratchets get_settings() calls below the CLI against tests/fixtures/get_settings_baseline.txt (#3645).
