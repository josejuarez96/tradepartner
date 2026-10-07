- #1084: signals.gross_profitability raises ValueError when t is not a session close (mid-session, after close, weekend, holiday); half-day closes pass.
### Changed
- gross_profitability refuses a t that is not a session close, so freshness is never measured from the previous session (#1084).
