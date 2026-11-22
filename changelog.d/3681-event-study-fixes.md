- Event study: market cap now carries shares over known splits (rule 8), and the fallback long kill uses the real net figure whatever the line is named (#3681, PR #3700).
### Fixed
- Event-study runner: market cap applies splits known at the read time between the shares fact and the price date; the fallback long kill is computed from net_bp, not always killed (#3681).
