- #3471: a symbol change refused because a sibling class lists the new ticker now ends the old ticker on its id (GLBL, EMPW base ids no longer take a reuser's bars)
### Fixed
- A symbol change refused because another class of the issuer lists the new ticker now ends the old ticker on its security at the process date, so a later reuser's bars (AlTi's GLBL, Holley's EMPW) no longer land on the bare id (#3471).
