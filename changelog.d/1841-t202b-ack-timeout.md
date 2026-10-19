- T202b built (#1843): an acknowledgement timeout leaves one order pending_unresolved (schema 30), settled by the next run's step-3 read-back; awaiting review
### Added
- An unacknowledged order past paper.accept_wait_max_seconds (default 300 s) is left pending_unresolved with a pending_unresolved alert and the run goes on; a second timeout in the run still halts; the next run's start, paper resume and paper reconcile settle it by a read-back (store schema version 30).
