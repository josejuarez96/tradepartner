- Unspent-cash check after collect (#1982): a broker-cancelled or expired buy's idle cash now alerts; the distinct write-off reason is blocked on a spec/schema amendment.
### Fixed
- The unspent_cash alert reads the broker's cash after the run's collection, not the buys phase's cash at sizing, so a broker-cancelled or expired buy no longer hides idle cash (#1982).
