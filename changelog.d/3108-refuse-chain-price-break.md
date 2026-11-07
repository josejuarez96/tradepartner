- Chain-aware reads also refuse a successor chain whose hand-over close breaks 1:1 (outside master.chain_max_price_ratio after same-span splits/spin-offs); Audit A s10b without a reason row now -47.6% (#3108)
### Fixed
- Backtest: a successor chain whose hand-over close ratio (after any split or spin-off between the two bars known at the read) falls outside `master.chain_max_price_ratio` either way is refused like a failed predecessor's, so a mis-derived chain with no delisting reason no longer splices a 1.0 equity into a 25 one (#3108).
