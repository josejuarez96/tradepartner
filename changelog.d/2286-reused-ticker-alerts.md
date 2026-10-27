- #2286 and #2289: a reused ticker restricts only its former name, and a halted run delivers its owed ticker-change skipped alert.
### Fixed
- Paper order runs keep a delisted name's exit open when a later issuer reused its ticker, including after a rename, without halting other names (#2286).
- A halted paper run sends its owed ticker-change lines in the skipped alert (#2289).
