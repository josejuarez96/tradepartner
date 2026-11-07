- #3205 EDGAR: every send (retries included) checks SEC's 403 block first; while one request waits it out, other workers send nothing and an in-flight success no longer clears it early.
### Fixed
- EDGAR: while one request waits out SEC's rate-limit block, every other send is refused as a 403 without being sent, and a success landing during the wait no longer clears the block (#3205).
