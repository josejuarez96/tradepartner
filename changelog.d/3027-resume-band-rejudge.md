- paper resume now refuses a band halt it used to miss: a run's own halt reason (SystemFaultError: fill_price_outside_band) and a halt-read bad fill whose engaged write failed (#3027, #3028)
### Fixed
- `paper resume` refuses a run's band halt whose reason starts with its fault type, and rejudges the fills of every run the release would clear against the run session's band, so a halt read whose band engagement write failed no longer lets a plain resume release (#3027, #3028).
