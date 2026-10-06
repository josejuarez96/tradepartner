- T81 (#993): research registry API in store/research.py (register, datasets, open/attach/close runs, results, family_run_count), RunHandle and the one reader load_dataset; req 13 isolation test.
### Added
- Research-registry API (`store/research.py`): experiment and dataset registration, run handles with window, split, holdout, budget and confirmatory gates, results with code-computed verdicts, `family_run_count`; `research.load_dataset` as the one export reader; `family_holdout_spends` lists research spends.
