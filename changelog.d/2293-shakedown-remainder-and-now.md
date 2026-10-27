- #2293, #2302: paper shakedown uses the frozen trading minimum for E.3 remainder checks and excludes rows known after `now`.
### Fixed
- E.3 checks both order kinds for no remainder and reports each offending order once; shakedown's timestamped reads stop at `now` (#2293, #2302).
