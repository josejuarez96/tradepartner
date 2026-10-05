- Health: no_bars_after_delisting no longer flags a span the resolver's own rule 7 keeps running under the same ticker (#829).
### Fixed
- health: `no_bars_after_delisting` accepts a delisting whose next EQUITY listing of the security carries the same ticker (resolver rule 7, #819), including through a same-day filer-typo pair; a different ticker, or a non-equity row of the same ticker, still fails (#829).
