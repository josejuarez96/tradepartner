- #1116 paper equity reads refuse, never skip: one marks.equity_at for run, resume, drawdown, outcomes, report; drawdown faults on an unreadable mark; back-filled names get an assets read; a split in a carried gap adjusts the close.
### Fixed
- Paper equity reads: the report and outcomes read a held session's cash from its position rows, an unreadable mark faults the drawdown check (run halts, resume refuses), a name held only on a back-filled session is flagged from the broker, and a split inside a carried gap adjusts the carried close (#1116).
