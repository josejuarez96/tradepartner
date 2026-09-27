"""The paper run lock (Phase 4 spec reqs 5, 7 and 14; plan T59).

One process at a time may act on the paper account through the journal: `paper
run`, `resume`, `reconcile`, `stop` and `abandon` each take `run_lock(settings)`
for their whole duration. It is an exclusive `fcntl.flock` on
`<store.path>.paper.lock`, taken without waiting: a second holder gets
`LockHeld` at once (the second `paper run` exits `locked`, spec acceptance
"Kill mid-run"), never a queue behind the first.

The operating system releases a `flock` when its file descriptor closes, so a
crash, a `kill -9` or a reboot leaves nothing to clean up and no stale lock to
break by hand. The lock file itself stays; its content names the last holder's
pid, for the `LockHeld` message only.

`is_held(settings)` is for readers that never take the lock (the operations
page, `paper status`): the kill-switch derivation shows "run in progress" rather
than "engaged" for an unfinished run while the lock is held
(`execution.switch.derive`). It tests the lock with a non-blocking shared
`flock` that it drops at once. A holder starting in that same instant is refused
as if a run held it; the caller of `run_lock` treats that like any other
`locked` exit.

The store and its lock file live on a local disk (the owner's Mac). On a
network filesystem `flock` may be emulated per process and would not refuse a
second holder in the same process.

This is a separate lock from DuckDB's own file lock on the store, which is held
only per write chunk (`store.db.open_for_write`) and released between chunks.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from tradepartner.config import Settings

#: Owner read and write only: the file names a pid, nothing else.
_LOCK_FILE_MODE = 0o600


class LockHeld(RuntimeError):
    """Another process holds the paper run lock."""


def lock_path(settings: Settings) -> Path:
    """`<store.path>.paper.lock`, beside the store it guards."""
    return Path(f"{settings.store.path}.paper.lock")


def _holder(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return text or "another process"


@contextmanager
def run_lock(settings: Settings) -> Iterator[None]:
    """Hold the exclusive run lock for the `with` block, or raise `LockHeld` at
    once when another holder exists (in this process or another). Released when
    the block exits, normally or by an exception."""
    path = lock_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, _LOCK_FILE_MODE)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockHeld(f"the paper run lock {path} is held by {_holder(path)}") from None
        try:
            os.ftruncate(fd, 0)
            os.write(fd, f"pid {os.getpid()}\n".encode())
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def is_held(settings: Settings) -> bool:
    """True while some process holds the run lock. Never creates the lock file:
    before the first run there is no file and nothing holds it. A process that
    holds the lock gets True here for its own lock; it must not ask."""
    path = lock_path(settings)
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)
