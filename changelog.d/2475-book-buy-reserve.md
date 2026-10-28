- Book A's open buys no longer reserve book B's cash; fill collection no longer takes its since from another book (#2475)
### Fixed
- A failing book no longer throttles or shifts the fill reads of the other books: the open-buy reserve and fill collection read their own book only; resume cursors stay account-wide (#2490) (#2475)
