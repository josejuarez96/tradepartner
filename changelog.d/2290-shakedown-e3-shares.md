- #2290: paper shakedown E.3 judges a notional decision's share-count orders (trims, whole-share buys, short opens, partial covers) in shares, not dollars at the fill price; ADR 0017 E.3 corrected.
### Fixed
- `paper shakedown` E.3 no longer fails every trim that fills above its reference price: orders by share count are judged in shares against what they were sized to (#2290).
