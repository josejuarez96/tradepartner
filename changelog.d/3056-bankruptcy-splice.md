- #3056 (PR #3064): post-bankruptcy new equity no longer spliced onto the old id (GPOR/WW shapes); health lists spliced series; repair is master rederive + repair-resolution in the sitting
### Added
- `health` lists `spliced_series`: a close jump above `health.splice_max_price_ratio` across a bar gap on one id (#3056).
### Fixed
- Post-bankruptcy equity listed on another exchange after a 25-NSE is its own security, and an old line that stopped before its Form 25 no longer takes its successor's first prints (#3056).
