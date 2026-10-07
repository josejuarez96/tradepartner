- T103 lab registry in review: store/lab_registry.py writes the lab tables and serves the registration-time reads; every call raises LabNotInitialised on a store without the lab tables.
### Added
- Strategy-lab registry module (`store/lab_registry.py`): lab-table writes and registration-time reads (fingerprints, family rules, sweeps, sweep runs, SR* high-water mark, sweep readiness, pre-lab, promotions, grandfathered rows); lab modules and `execution` kept import-isolated (T103).
