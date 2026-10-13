- Stock dividends now enter the split adjustment path as share ratios; SCCO repair rows are documented for the owner release.
### Fixed
- Store Alpaca stock_dividends as split-like share-ratio actions with point-in-time stamps and stable source IDs, count them on the run row, and keep exactly matched ratio-as-cash rows out of cash dividends (#1618).
