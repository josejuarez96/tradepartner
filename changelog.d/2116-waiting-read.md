- T256: `ops.waiting_on_owner(conn, settings, now)` lists what each open book waits on the owner for (kill switch, lagging order, missed run, shakedown note, first report, unreadable rows), each with its command (PR #2119).
### Added
- The "waiting on me" read: `execution.waiting` (pure) and `ops.waiting_on_owner`, every open book's items in a fixed order with the command that clears each (T256, #2116).
