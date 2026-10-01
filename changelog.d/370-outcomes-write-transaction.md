- #370 write_outcomes_and_lots runs in one transaction (a crash mid-write leaves the previous lot set current); an unsaved changed ledger no longer feeds realised_pnl (#371); lots refuse non-finite fills and a naive filled_at (#372, #373)
### Fixed
- Outcomes/lot write is one transaction; realised_pnl waits for a saved ledger; lots refuse non-finite or naive-time fills (#370, #371, #372, #373)
