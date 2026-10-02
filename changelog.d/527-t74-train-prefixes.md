- T74: merge-train script, its tests and the rulesets directory require safety-reviewer; the merge subcommand is under ask.
### Added
- SAFETY_PREFIXES gains scripts/merge_train.py, tests/test_merge_train.py and .github/rulesets/; .claude/settings.json asks before uv run python scripts/merge_train.py merge.
