- #737: Alpaca bars/actions requests send master tickers only in Alpaca form (trim, upper, CRD-A->CRD.A); other tickers are not sent and are named on the run row, so one bad ticker cannot fail a backfill chunk.
### Fixed
- Alpaca prices: master tickers that are not Alpaca symbols (`BAX (NYSE)`, `C/28`, `F&G`) are no longer sent to the bars or corporate-actions request, where one could fail the whole chunk; safe spellings are normalised (`NKTX `, `Caap`, `CRD-A` -> `CRD.A`) and rows resolve back through every master spelling (#737).
