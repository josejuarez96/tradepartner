- Ingest catch-up-bound message and the stale_data runbook rows now say a plain ingest must follow --backfill before paper's staleness gate clears (#2181)
### Fixed
- The ingest catch-up-bound message now names the plain `ingest --source alpaca` run that must follow `--backfill` for paper to stop halting stale_data; the scheduling runbook row says the same (#2181).
