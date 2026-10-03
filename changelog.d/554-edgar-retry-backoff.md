- EDGAR HTTP client: capped exponential backoff on 503/429/transport errors, a 10-min wait-then-fail on 403, and a corrupt-zip re-download (#554).
### Fixed
- EDGAR adapter: capped exponential backoff (honouring Retry-After) on 503, 429, connection drops and timeouts; a single 10-minute wait before one retry on a 403 rate-limit block, then fail; a corrupt or truncated bulk/FSN zip is re-downloaded once (#554).
