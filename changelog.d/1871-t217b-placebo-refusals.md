- T217b done: the ladder's trial-row note tag and the refused_placebo refusals (holdout/tracking run, sweep promote, --operations-book-of); delayed files untouched.
### Added
- Trial rows carry the shock tag (placebo=<mode> seed=<n>, signal_delay=<d>) in trials.note from run and sweep runs; at the defaults the note is unchanged. A holdout or tracking run of a placebo file, sweep promote and --operations-book-of refuse it as refused_placebo before any write; delayed files are not refused.
