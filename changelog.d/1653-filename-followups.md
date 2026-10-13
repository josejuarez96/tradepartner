- T184f/e/g follow-ups (#1653, #1662): collision labels kept, probe bounded, health and Form 25 counts exact, master-retract spares filing_name rows with the switch off
### Fixed
- Filename-listing follow-ups: both colliding tickers of one Alpaca symbol are listed in filename_collisions; the collision probe fetches the sessions the rule needs (alpaca.collision_probe_margin_sessions), not years of bars; health only_listing and filename_form25_unmatched count what they say; master-retract no longer proposes filing_name rows while edgar.filename_listings is off
