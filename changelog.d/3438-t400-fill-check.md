task: T400 (PR #3442)
- T400: a broker fill is checked against its order's symbol, side and size at collect and reconcile; a mismatch or overfill halts the book; paper resume --accept-fill-mismatch (PR #3442)
### Added
- A broker fill that does not match its order's symbol, side or size now halts the book (fail closed), reconcile names an overfilled order, and paper resume --accept-fill-mismatch accepts only the fill ids the halt names (T400, #3333).
