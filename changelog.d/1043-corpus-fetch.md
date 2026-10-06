- T120 corpus fetch: tradepartner.corpus.departure_fetch builds corpus.jsonl and counts.json under edgar.cache_dir/corpus/departure-reason/ from form.idx, the Form 25 text, the submissions JSON and one 8-K; CLI wiring is T124.
### Added
- `tradepartner.corpus.departure_fetch`: the departure-reason corpus fetch (Form 25 population with its count identity, EX-99.25 notice, markers, the one 8-K with its items), cached under `edgar.cache_dir/corpus/departure-reason/` (T120, #1043).
