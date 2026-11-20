- Config refuses env names that misspell a nested setting (#3589); owner runs 'uv run tradepartner paper status --all' after pulling
### Added
- Settings refuses single-underscore spellings of nested keys and unknown sections in env and dotenv, naming keys only
