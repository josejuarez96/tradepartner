- paper resume clamps its own known_at stamps (settle, resume_acceptances, synthetic fills, journal cut) so they never go backwards, with a logged warning when it clamps.
### Changed
- paper resume: clock-skew clamp for its own known_at stamps, with warning logging; new tests for the clamp and the fail-closed journal-cut case.
