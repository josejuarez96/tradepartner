- Scheduled writers (paper run/kill, ingest) wait 300 s for the store (store.writer_lock_retry_seconds); a locked paper kill says NOT engaged ([#2178](https://github.com/josejuarez96/tradepartner/issues/2178)).
### Fixed
- Scheduled writers out-wait a long store reader, and a locked paper kill says plainly it is NOT engaged (#2178).
