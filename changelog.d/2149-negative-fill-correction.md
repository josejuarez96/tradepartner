- Alpaca's negative-quantity fill correction is folded into its order's earlier fill instead of halting the run (#2149)
### Fixed
- adapters: a negative-quantity partial_fill row from Alpaca paper (a correction 2 ms after a fractional fill) is folded into the same order's earlier fill at the same price; it halted book daily on 2026-10-21 (#2149)
