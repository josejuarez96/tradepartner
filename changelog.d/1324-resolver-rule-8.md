- T141b (#1324): resolver rule 8 in code; a span ends after its last own bar when another issuer takes the ticker; #974 lead clipped; --fill-holes refetches the window. Inert until T141's repair.
### Added
- Resolver rule 8 (#1314 item 2, T141b): `ListingResolver` ends a span on the session after its last traded Alpaca bar when another company's span of the ticker follows after a stopped line, clips the new issuer's first-span lead to the hand-over, and `ingest --fill-holes` lists and refetches the hand-over window for the old holder (`rule8_window`).
