- Fill cursors are per book: a paper resume records its book (schema 47, resume_invocations.book_id), so another book's resume no longer moves this book's fills(since) (#2490).
### Fixed
- A `paper resume` fill cursor is its own book's: `resume_invocations` gains a nullable `book_id` (schema 47) and a book's collection reads only its own run and resume cursors, dropping a pre-47 resume cursor of unknown book (#2490).
