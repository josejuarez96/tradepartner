- T168 (#1516): the departure corpus takes every acceptance (Form 25, 8-K, marker) from the SGML header, not the late submissions JSON; corpus fetch prints its request count, stops above --max-requests; fetch_version 2.
### Changed
- corpus fetch departure-reason: every acceptance comes from the filing's SGML header (the submissions JSON was served hours late since 2026-10); the run prints the requests still to make and stops above --max-requests; corpus.jsonl and counts.json carry fetch_version 2 (T168, #1516).
