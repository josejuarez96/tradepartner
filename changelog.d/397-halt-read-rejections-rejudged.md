- #397 `paper resume` judges every run its release would clear (`switch.faulted_runs`) with `rejection_breaches`, so a halt read's rejections refuse the release; the collect docstring now says the next run does not re-judge them (owner answer (a))
### Fixed
- Execution: `paper resume` refuses on a rejection-cap breach of a halted run whose halt read journaled the rejections (#397)
