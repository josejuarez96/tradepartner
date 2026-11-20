"""The process exit codes of the `tradepartner` command, pinned in one place (#3651).

The launchd jobs, the runbooks and the dashboard read these numbers, so a member's
value never changes and no two meanings share an enum. Two enums, not one:
`paper lots-reconcile` numbers its outcomes differently from every other command
(its 3 is "a difference", not a write failure), and one `IntEnum` would turn those
into silent aliases.

Imports only `enum`, so any module may import it.
"""

from __future__ import annotations

import enum


@enum.unique
class ExitCode(enum.IntEnum):
    """What every command but `paper lots-reconcile` exits with."""

    OK = 0
    FAILED = 1
    USAGE = 2
    WRITE_FAILED = 3
    REFUSED = 4
    LOCKED = 5
    NO_WINDOW = 6
    INTERRUPTED = 130


@enum.unique
class LotsReconcileExit(enum.IntEnum):
    """`paper lots-reconcile`'s own codes from 3 up (0, 1 and 2 are `ExitCode`'s)."""

    DIFFERENCE = 3
    UNKNOWN_EXPORT_FORMAT = 4
    NO_DISPOSALS = 5
    LOCKED = 6
    NO_WINDOW = 7
    NO_STORE = 8
    SCHEMA_VERSION = 9
