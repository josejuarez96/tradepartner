- #844 resolver: an unreadable cover-page ticker ('New York Stock Exchange') never ends a span, and VAL*/BAX (NYSE) read as their symbol; VMC regains ~500 days after fill-holes.
### Fixed
- Price resolver: a junk cover-page ticker no longer ends a company's ticker span, and footnote-marked tickers (`VAL*`, `BAX (NYSE)`) are read as their symbol (#844).
