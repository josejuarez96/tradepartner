- T157b: `paper shakedown` (ADR 0017 E): execution/shakedown.py reads the span from the newest shakedown_span row (restarted by a mismatch or halt) and prints E.1-E.7 over every book, read-only; exit 0 only when all pass.
### Added
- `tradepartner paper shakedown`: the machine-readiness gate's seven lines (ADR 0017 part E) over every book, thresholds from the `shakedown_span` decision, read-only (T157b, #1391).
