- #735 ListingResolver no longer aborts the Alpaca backfill: placeholder tickers and non-equity classes hold no ticker, same-day ticker pairs, later same-company classes and same-start ties go unassigned and counted on the run row.
### Fixed
- ListingResolver: only equity classes hold tickers; placeholder tickers, a security's same-day ticker pair, a later class of one company and same-start ties are left unassigned and counted on the alpaca run row instead of aborting the backfill (#735).
