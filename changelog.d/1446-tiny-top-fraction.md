- #1446: a tiny valid top_fraction (e.g. 1e-7) now selects one name instead of dividing by zero; registered 0.05/0.10/0.20 counts unchanged.
### Fixed
- A tiny `strategy.top_fraction` (e.g. 1e-7) selects at least one name instead of raising ZeroDivisionError (#1446).
