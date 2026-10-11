- T166d (#1517): the #1382 stamps driver's request floor/ceiling and during-run cap, rewritten-CIK probes (3b), late API stamps (4b) and in-window earlier SGML corrections under T166f's pre-check (--window-earlier-ok).
### Changed
- scripts/repair_stamps.py: the dry run reports requests_floor, requests_ceiling and rewritten_if_all_mixed (requests_known goes) and the cap holds during the run; rewritten CIKs are probed, not re-read; --late-api-from/--late-api-to re-read late API stamps; --window-earlier-ok admits in-window earlier SGML corrections under T166f's pre-check (#1497, T166d).
