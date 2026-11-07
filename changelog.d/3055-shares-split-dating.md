- #3055: rule 7 split dating behind universe.shares_split_dating (off; flip #3070) and a shares cap-jump review list in health; class mislinks left to T198's rederive-1682.
### Added
- Rule 7 reads a post-split share count dated before its split's ex-date as already split, under `universe.shares_split_dating` (default off, frozen off), and `health` lists shares facts whose implied cap jumps against their neighbours (`health.shares_cap_jump_ratio`) (#3055).
