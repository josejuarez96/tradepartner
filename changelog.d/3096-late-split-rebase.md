- #3096: a split known after its ex-date (or vendor re-dated) is re-based at the first step that knows it (n_late_splits), or the trial is refused when a fill already used the unrepaired value; Audit A s03 now -0.10% (was +424%).
### Fixed
- Backtest: a split first known after its ex-date, or re-dated or cancelled by the vendor, no longer books the raw price jump as return: the first step that knows it re-bases the held position (shares x ratio on the ex-date) and counts `n_late_splits`; when a fill or exit already traded on the unrepaired value the trial is refused (#3096).
