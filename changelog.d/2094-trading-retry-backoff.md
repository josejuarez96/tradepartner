- Trading raw requests use capped exponential backoff for retryable failures (#2094).
### Fixed
- Trading retries now wait 1, 2 and 4 seconds by default before reattempting, while duplicate order read-back remains intact (#2094).
