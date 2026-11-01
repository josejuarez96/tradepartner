- Paper tests: a stale-ingest refusal recovers once S-1 is truly covered (refusals stay stale, nothing sent), and a failed book A shows failed in status/ops/waiting while book B's next run is ok (#2602, PR #2606).
### Added
- Tests for recovery after a stale-ingest refusal and for status/ops/waiting monitoring after one book's run fails (#2602).
