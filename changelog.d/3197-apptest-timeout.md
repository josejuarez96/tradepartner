- Dashboard AppTest runs share one 60 s timeout (was streamlit's 3 s default on some runs), with a guard test, so loaded CI shards stop failing correct pages (#3197, #3209).
### Fixed
- Dashboard tests no longer time out at streamlit AppTest's 3 s default on loaded CI shards: every AppTest uses a shared 60 s timeout (#3197).
