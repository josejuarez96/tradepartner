- 515/416: halt write-failure exit code distinct from a crash's; alerts.channels requires a non-store channel (#543).
### Fixed
- The halt path's engaged-row write failure now exits a distinct code (WRITE_FAILED_EXIT_CODE=2) from an ordinary crash (CRASH_EXIT_CODE=1), and `alerts.channels` must include a non-store channel so `deliver_without_store` can never deliver nowhere (#515, #416).
