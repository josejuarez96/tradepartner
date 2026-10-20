- Daily name-change request tolerates a transport error (prices land, warning counted, asked again next run); health.last_ingests skips actions-backfill months (#1676)
### Fixed
- A network error on the daily run's name-change request no longer fails the day's price chunk; it is counted in the run message and the window asks again next run (#1676)
- health.last_ingests shows the latest daily alpaca run, not an actions-backfill month (#1676)
