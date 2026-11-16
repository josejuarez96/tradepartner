- refetch-actions refuses a new split/dividend repeating a live event on another id of the same CIK (#3304, PR #3315)
### Fixed
- refetch-actions' collision check now also refuses a repeat of a live event on another security id of the same CIK (dividends and splits), unless reviewed distinct or allowed (#3304)
