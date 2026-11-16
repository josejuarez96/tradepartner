- #3198: true-up reversal refuses (replay) when recorded fills would no longer replay; Home says restricted only for breaks that restrict a name (#3189 exec-3, exec-4)
### Fixed
- `paper true-up --reverse` refuses, writing nothing, when the reversal would leave a later sale unable to replay in the lot ledger; Home no longer says trading is restricted for a break that restricts no name (`unknown_symbol`).
