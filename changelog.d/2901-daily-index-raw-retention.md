task: T347 (PR #2904)
- T347: daily index files are kept by hash in the raw archive before parse; each edgar_index run archives a file-date manifest and its message names provenance_sha256 (#2714).
### Added
- Daily index raw retention: each daily form.idx body is archived by SHA-256 before parse, with one provenance manifest per edgar_index writer run (T347, #2714).
