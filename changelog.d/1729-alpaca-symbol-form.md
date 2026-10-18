- #1729: the order path sends every symbol in Alpaca's form (GEF-B -> GEF.B) and maps positions back; an asset Alpaca does not know, or a ticker with no broker form, is skipped untradable instead of halting the book.
### Fixed
- Paper order path: class-share tickers go to Alpaca in its form (GEF.B, BRK.B) for assets, orders, risk and reconciliation; an unknown asset (404 40410000) or a ticker with no broker form is skipped as untradable, not a halt (#1729).
