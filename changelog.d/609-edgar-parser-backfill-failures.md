- EDGAR parser fixes for the 2026-10-02 backfill failures (#609): 264 of 295 clear; F2 (22) and D1 (1) stay failures for the owner; FSN_VERSION 2 re-downloads all FSN periods (~21.5 GB) and COVER_VERSION 2 re-fetches cached covers
### Fixed
- EDGAR parsers: FSN keeps the latest share ddate per class and skips NULL counts, FSN titles differing only by a dropped space are one title, and cover pages skip and count incomplete listings, skip nil facts and parse a cover with no listing as empty (#609)
