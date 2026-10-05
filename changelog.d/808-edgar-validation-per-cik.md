- #578 part 3 (#808): per-CIK submissions and companyfacts API payloads that fail to parse are recorded for the validation gate; per-document failures keep the one failed_filings.json policy, listed in the check's message.
### Added
- EDGAR input validation (#578 part 3, owner option (a)): per-CIK submissions and companyfacts API parse failures are recorded for the pre-write gate (absent for the pass, nothing cached); check_failures' message now lists the run's unaccepted per-document failures (bounded by edgar.max_validation_listed, redacted).
