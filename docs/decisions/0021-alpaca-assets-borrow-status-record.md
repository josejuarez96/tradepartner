# 0021. The borrow-status record: a daily point-in-time snapshot of Alpaca's assets list, data only

**Status:** Proposed  ·  **Date:** 2026-10-11  ·  **Issue:** #1506 (data-foundation plan tasks T171 to T171d)

## Context

The [beyond-long-only report](../research/2026-10-10-beyond-long-only.md) (2026-10-10) graded a short leg in our universe NOT SUPPORTED as a source of net edge, and feasibility of an easy-to-borrow (ETB) short leg on Alpaca paper SUPPORTED (its section 7, rows 7 and B13). Its one data gap is the point-in-time record: Alpaca's `shortable` and `easy_to_borrow` flags are current-only, so any backtest that applies today's flags to history has look-ahead, and no history of them exists anywhere free. [ADR 0015](0015-expansion-seams.md)'s shorting row lists "a research report on borrow cost and availability" as a precondition for ever opening shorting; the report supplies most of it, and this record is the missing part.

History starts only when recording starts. The owner therefore started a scratch forward recorder outside the repo on 2026-10-11 08:09Z (`~/tradepartner-probes/borrow-snapshot/`): one `GET /v2/assets` a day on the paper endpoint, saved as one gzipped JSON with the UTC fetch time (33,636 assets, 5,373 of them shortable). The build imports those files, so no day is lost while the store table is built.

What the record is for: a feasibility gate and a cost input for research on short or hedged books (B12's borrow sensitivity, an ETB-only gate), and nothing on the order path. Shorting stays closed: the charter's long-only scope changes only through its own ADR (ADR 0015 part A), and the deterministic risk rules would need an ETB-only gate first (report, section 4).

The research-program rule ([research-program.md](../ways-of-working/research-program.md), "When a claim would change the tool itself"): new data to ingest goes through an ADR. [ADR 0009](0009-price-vendor.md) settled Alpaca as the price vendor and the USD 0 budget, and its point 5 the terms under which Alpaca data is stored (personal, non-commercial, private); it did not consider the trading API's assets endpoint as a stored fact. The data-foundation spec reads that endpoint today only as `snapshot_static` ticker and exchange evidence, one symbol at a time. This record is a new fact table from an already-decided source.

## Options considered

1. **A daily snapshot of Alpaca's full assets list into a store table, `known_at` = the fetch time** (this ADR). Pro: the only free source that says what *our* broker would let us short on a given day, which is the question a paper short book would face; one request a day; the recorder already runs, so the history starts 2026-10-11; terms are ADR 0009's. Con: no history before 2026-10-11, so a borrow-aware backtest of any in-sample window needs a modelled fee or flag for the years before it; the flags are Alpaca's, not the market's (a name HTB at Alpaca may be ETB elsewhere); the endpoint is current-only, so a lapse in the daily job leaves a hole that nothing can fill.
2. **FINRA's short-interest files, stamped at publication.** Pro: a market-wide, twice-monthly, point-in-time series back to June 2021 for exchange-listed names. Con: short interest is a proxy for borrow availability, not a measure of it; twice a month, not daily; a second external source with its own terms and parser, which the research-program rule says needs its own ADR. Not chosen now; it enters by its own ADR when a registered research run needs it (the report's B13 names it as the optional second half).
3. **IBKR's public borrow file.** Pro: fee rates, not just flags; a daily file. Con: another broker's book, under terms the free-data research never read, with no account of ours behind it; the fee a different broker charges is not the fee Alpaca would. Not chosen.
4. **Nothing: keep the current-only flags on the broker interface.** Pro: no table. Con: every borrow-aware backtest stays look-ahead by construction, and the ADR 0015 precondition is never met. Not chosen.

## Decision

We will take option 1. The data-foundation spec's amendment of 2026-10-11 (#1506) holds the table and the rules; this record fixes what is costly to change once rows are stored:

1. **Source and request.** One `GET /v2/assets` per trading day on Alpaca's paper endpoint (the assets list is account-agnostic reference data; the paper and live endpoints serve the same list), through the existing trading client and the data key pair the store already uses, so no new secret. `us_equity` assets only; other classes are counted, not stored.
2. **The stamp.** `known_at` is the clock reading after the response arrived (for an imported file, its recorded `fetch_finished_utc`), never the request start, never a date, never back-dated; `provenance = snapshot`. Nothing about an asset is knowable before the fetch that showed it.
3. **Rows record change.** The first snapshot writes one row per asset; a later snapshot writes a row for an asset only when a stored field changed, the asset is new, or it is missing from the list (one row with `present = FALSE`). Every snapshot writes its own `ingestion_runs` row, so an unchanged day is recorded as observed. "Latest as of T" per asset is the status at T.
4. **Data only.** No consumer of the table is part of this decision. The order path, the risk rules, the backtest engine and the paper books do not read it. A backtest or a paper book that reads it enters by a strategy-lab or backtest spec amendment, and shorting itself by a charter ADR (ADR 0015 part A).
5. **Terms and budget.** ADR 0009 point 5 applies: the rows are the owner's private copy under Alpaca's personal, non-commercial terms, never redistributed; the export and backups are private as they are for bars. The budget stays USD 0 (ADR 0009 point 1).
6. **History starts 2026-10-11 08:09Z.** The recorder's files are imported as the first snapshots; no row is ever synthesised for a day before the first recording. A hypothesis that needs borrow status before that date models it and says so in its file.

## Consequences

- Good: the ADR 0015 precondition becomes meetable with data of our own; the only look-ahead a borrow-aware study could carry (today's flag on yesterday's book) is impossible at the schema; one request a day and a few hundred changed rows a day, so the table stays small.
- Bad / accepted risks: a borrow-aware backtest of any window before 2026-10-11 still needs a modelled borrow input, and the report says that is where the short leg's return is most uncertain; a missed daily run is a permanent hole, visible only as a stale snapshot on the health page; Alpaca's flags are one broker's view.
- Reversibility: cheap. The table is additive, read by nothing; dropping it changes no trial, no fingerprint and no paper book.
- Revisit if: the owner opens shorting (then the consumer, its gate and FINRA's series are decided together); Alpaca changes or withdraws the endpoint's fields (`borrow_status`, `easy_to_borrow`); a registered research run needs a market-wide series rather than Alpaca's book.
