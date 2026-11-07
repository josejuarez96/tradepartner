- paper resume: two books resuming at once no longer refuse each other; switch.release checks the latest resume of the window's own book (rows without a book still count for every book) (#3030)
### Fixed
- `switch.release` requires the latest resume of the window's book, not of the whole store, and refuses another book's resume, so two books' overlapping `paper resume`s both release (#3030).
