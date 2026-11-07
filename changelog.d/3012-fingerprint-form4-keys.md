- #3012: no Form 4 key moves the EDGAR build fingerprint any more (price-below and zero-price codes excluded beside the rule 5 keys); default hash re-pinned. T324's --rebuild-index seed must run after this lands.
### Fixed
- The EDGAR build fingerprint no longer hashes `events.form4_xml_price_below` or `events.form4_zero_price_xml_codes`: only the Form 4 writer reads them, as with the other Form 4 keys (#3012).
