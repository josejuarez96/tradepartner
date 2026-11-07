- #3078: reviewed list of FDIC-filer banks (FRC, SBNY) and read-only `store bank-filers` census; keying them waits on an ADR 0022 amendment (options in the PR)
### Added
- `tradepartner store bank-filers [--assets FILE]`: a read-only census of exchange-listed banks that file with their regulator instead of EDGAR (First Republic, Signature), from a reviewed list with source URLs, plus bank-level leads from an Alpaca assets file (#3078)
