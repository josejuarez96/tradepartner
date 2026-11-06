- #2610: write_result stores a NaN or infinite secondary/exploratory value as JSON null, so the run stays ok and spends no extra budget slot
### Fixed
- A non-finite value in a research result's secondary or exploratory metrics is stored as JSON null instead of failing write_result (#2610)
