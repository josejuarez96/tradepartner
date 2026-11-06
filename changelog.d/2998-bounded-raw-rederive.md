task: T352 (PR #3000)
- T352: `archive rederive` re-derives named Form 4 and daily index rows from archived raw bodies inside an open release, keeping known_at (PR #3000, class B)
### Added
- `tradepartner archive rederive`: a bounded re-derive from the raw archive. Each row maps to one writer manifest entry, every input is hash-checked before parse, the disk floor is checked before and during, parser mistakes are corrected in place inside an open data release with known_at kept, and rows an open paper window read are refused (dry run by default; `--release` and `--expect` to write; T352, #2998)
