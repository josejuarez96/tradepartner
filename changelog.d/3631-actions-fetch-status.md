- #3631: the name-change and merger fetches tolerate only transient errors (timeout, connection, 429, 5xx); a 401/403/422 or a local OSError now fails the alpaca chunk.
### Fixed
- A permanent error on the daily name-change or merger request (a refused key, a malformed request, a local OSError) was counted as 'unanswered' and the run stayed ok; it now fails the alpaca chunk, and last_actions_report is a typed PriceSource attribute read without getattr (#3631).
