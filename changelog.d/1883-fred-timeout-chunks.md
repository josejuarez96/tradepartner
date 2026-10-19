- FRED backfill: real-time windows under the 2000-vintage cap, timeout, 5xx retry, FRED error text (#1883)
### Fixed
- FRED backfill splits the vintage range into windows (rates.fred_vintage_window_days), sets a request timeout, retries timeouts and 5xx, and reports FRED's error message (#1883)
