- #1445 fixed: the open-buy reserve counts each fill of a quantity buy in post-split shares (splits after that fill's day), so fills on both sides of a split no longer under-reserve.
### Fixed
- Open-buy reserve: a quantity buy with fills on both sides of a split now reserves its true post-split unfilled shares (#1445).
