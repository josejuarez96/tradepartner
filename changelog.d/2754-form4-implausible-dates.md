### Changed
- Form 4 transaction dates stay as filed; as-of readers exclude only impossible dates (before `events.form4_min_transaction_date`, default 2000-01-01, or after the filing's acceptance day plus `events.form4_after_acceptance_days`, default 0), expose the excluded count, and health warns on them; late filings stay readable (#2754).
