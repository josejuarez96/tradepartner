- #793 resolver registrant check: a subsidiary or unproven claim never takes a live holder's ticker (AEP, MAA, MPW, REG, SLG back to the parent; SR, MGEE unassigned and counted)
### Fixed
- Alpaca bars of AEP, MAA, MPW, REG and SLG no longer land on a subsidiary or operating partnership whose cover page cites the parent's ticker; an unproven claim on a still-filing holder's ticker is unassigned and counted until the holder is delisted or quiet for `alpaca.registrant_quiet_days` (new, default 180; spec rule 6, #793).
