- Engine: a negative position value now raises ValueError instead of being silently dropped (ADR 0015 TE5); zero and dust unchanged.
### Fixed
- The backtest engine raises on a negative position value instead of silently dropping it (ADR 0015 TE5, #1255).
