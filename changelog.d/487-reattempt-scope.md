- T60d merged (#487): execution/reattempts.py, the pure re-attempt scope and end-of-phase write-offs the wrapper driver (T60b) calls.
### Added
- execution.reattempts: attempt_scope (open decisions for their remainder, in-flight ones left to collection, the last-phase rule) and write_offs (derived write-offs plus the buys a completed last phase deferred), pure over decisions and their plan.decision_state (T60d, #487).
