- T273: the Home view model (status line, Needs you, book cards, Data status, Research strip), two ui.* keys and the strategy one-liners in docs/ui/book_blurbs.toml (#2251)
### Added
- The Home page's view model (`execution/home_view.py`): one read pass returning the status line, the items that need the owner, a card per book, data status and the research strip; `ui.next_run_time_et` and `ui.holdings_rows` config keys; `docs/ui/book_blurbs.toml` with one plain sentence per registered hypothesis (#2251).
