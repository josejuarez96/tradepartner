- Operations and Health pages render stored reconciliation events, run messages, waiting details and delisting-reason lines as plain text, never markdown (#3656).
### Fixed
- Dashboard: the Operations page's reconciliation events, last-run messages and waiting-item details, and the Health page's delisting-reason lines and latest ingest message, render through `st.text`, so a link or image in stored broker or vendor text shows as literal characters (#3656).
