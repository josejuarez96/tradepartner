- Wash-sale matches normalise the disposal and the replacement lot to one share unit, so a split or stock merger never mixes units (#1980).
### Fixed
- Wash-sale matching: a loss sold before a 2:1 split or a stock merger at 0.5 was matched and prorated in mixed share units, so matched_quantity and disallowed_amount were off by the ratio; the disposal, the shares its sale closed and every lot are now scaled to today's units at match time (#1980).
