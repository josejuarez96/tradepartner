- T80 (#926): schema version 12 adds the five research-registry tables and trial_results.n_research; additive migration at the owner store's next writing job; read-only opens of version 11 keep working.
### Added
- Schema version 12: the research-registry tables (registrations, datasets, runs, results, decisions) with known_at and enumeration CHECKs, nullable trial_results.n_research, and ResearchNotInitialised for research reads on an unmigrated store (#926, T80).
