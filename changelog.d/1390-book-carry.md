- T155 (#1390): every window command, resume and reconcile take the book (default paper.book_id); run lock per book; start reads only the book's previous window and refuses another book's account; H1's main unchanged.
### Added
- Books on the order path: every window command, `paper resume` and `paper reconcile` take the book, the run lock is per book (`<store.path>.paper.<book>.lock`), and `start` refuses another book's account (`account_in_use`) and never excuses positions by another book's residues (ADR 0017 B.3 to B.5; T155).
