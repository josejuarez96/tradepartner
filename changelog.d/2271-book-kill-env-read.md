- Book page kill refuses in plain words with the desk command when the live `.env` cannot be read (#2271).
### Fixed
- An `OSError` reading the live `.env` now gives `KillRefused` before `paper kill` starts, instead of a raw dashboard traceback.
