- #1085: the LLM boundary text scan reads .gz files decompressed and refuses compressed formats it cannot read, so a gzipped fixture cannot hide the vendor host. The wider network list and req 13 wording landed with #1121/T123.
### Added
- LLM boundary test (c) reads `.gz` files decompressed and fails closed on other compressed formats under src/, scripts/ and tests/ (#1085).
