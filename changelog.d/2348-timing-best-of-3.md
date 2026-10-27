- The six wall-clock read-budget tests take the best of 3 timed reads, so a busy CI runner no longer fails them (#2348)
### Fixed
- Read-budget timing tests assert the fastest of three timed reads against the same bound, ending CI flakes on busy runners (#2348)
