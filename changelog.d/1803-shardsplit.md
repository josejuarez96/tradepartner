- T216 done: the four truncation-heavy as-of tests run as 9 probe chunks each (tests only), so xdist spreads them; slowest test 343 s to 43 s.
### Changed
- Split the four truncation-heavy as-of tests (health, gap, universe, adjusted prices) into 9 parametrised probe chunks; probe set pinned by count and hash.
