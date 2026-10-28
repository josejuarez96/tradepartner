task: T310 (PR #2524)
- T310 done: golden tests pin the config surface for the R1 split (PR #2524)
### Added
- tests/test_config_golden.py and tests/fixtures/config_golden/: goldens for the Settings dump, schema, frozen keys, family hashes, env names, importable names and the .env.example load; CONFIG_GOLDEN_UPDATE=1 rewrites them
