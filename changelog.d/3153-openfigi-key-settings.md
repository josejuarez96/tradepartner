- OPENFIGI_API_KEY is read through Settings (shell or .env) and redacted by secret_values; ingest passes it to map_tickers, so a .env key gets the keyed limits (#3153).
### Fixed
- openfigi: the `.env` `OPENFIGI_API_KEY` is now used (read through `Settings`, redacted like every secret); before, only a shell export reached the client (#3153).
