- #3635: a halt-path or failed-path note keeps the failing step's scrubbed message and file:line, bounded by paper.max_fault_note_chars (300).
### Changed
- A paper halt's or failed run's notes ("alert not written", "cancel of ... failed", "result row not written" and the rest) now read `(Type: scrubbed message at file.py:line)` instead of the error's type alone; the message is cut to `paper.max_fault_note_chars` (default 300) (#3635).
