- T153 done: one Alpaca paper key pair per book; `main` keeps ALPACA_PAPER_API_KEY/SECRET, others read ALPACA_PAPER_BOOKS__<TOKEN>__API_KEY/SECRET; build_broker and AlpacaBroker take the book.
### Added
- Per-book Alpaca paper key pairs (ALPACA_PAPER_BOOKS__<TOKEN>__API_KEY/_API_SECRET; book `main` unchanged), and `build_broker(settings, clock, book_id)`; a book with no pair is refused `no_credentials` (ADR 0017 B.1, B.2; T153).
