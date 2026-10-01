- Phase 4 T60 (#390): execution/wrapper.py: allowlist constants and classify, the shared-clock pre-check, the req 4 halt path (a repeated skew ClockError in the halt read leaves the halt standing, #375), replay
### Added
- execution/wrapper.py: the risk-gated wrapper's core (allowlist, clock pre-check, halt path, replay) (T60, #390, #375)
