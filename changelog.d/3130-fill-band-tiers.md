- paper: the fill-price band has two tiers; a real gap past 25% is a fill_price_gap note, only a fill outside [1/3, 3]x the reference halts, so gaps no longer end windows (#3130)
### Changed
- The fill-price band halts only outside paper.max_fill_price_ratio (new, default 3.0: [1/3, 3] x the close(S-1) reference); a fill past paper.max_fill_price_deviation (0.25) inside it is a fill_price_gap note on the run, not a halt, and paper resume no longer refuses for it (#3130)
