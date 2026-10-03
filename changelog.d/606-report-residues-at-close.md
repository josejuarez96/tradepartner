- Fixed #606: report's residue term now always prices at the close through a separate accessor, never the frozen execution.fill_price bar.
### Fixed
- Residues in the tracking report are always priced at the close, like the marks, regardless of the window's frozen execution.fill_price (#606).
