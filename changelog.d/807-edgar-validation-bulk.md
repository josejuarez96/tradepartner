- #578 part 2 (#807): form.idx quarters, submissions.zip/companyfacts.zip members and whole FSN periods that fail to parse are recorded for the validation gate and absent for the pass; the gate also lists them if the pass later raises.
### Added
- EDGAR input validation (#578 part 2): parse failures in form.idx quarters, submissions.zip and companyfacts.zip members and FSN periods are recorded for the pre-write gate instead of raised (each absent for the rest of the pass, no per-CIK API fallback); a pass that raises after a recorded failure still fails with the full list, naming that error.
