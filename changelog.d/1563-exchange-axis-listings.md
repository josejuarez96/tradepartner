- T178 (#1563): both cover-page parsers read listings tagged by dei:EntityListingsExchangeAxis behind edgar.exchange_axis_listings (default off; T181 flips it with the version bumps); mismatched MIC members are counted.
### Added
- Cover-page listings under the exchange axis (IBM, AbbVie, Abbott, 3M, P&G shapes) in both the per-document and FSN parsers, behind `edgar.exchange_axis_listings` (default off), with an `exchange_axis_mismatch` count on the EDGAR run message (#1563, #1541).
