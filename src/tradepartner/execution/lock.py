"""The paper run lock, one per book (Phase 4 spec reqs 5, 7 and 14; plan T59;
ADR 0017 B.3, plan T155).

One process at a time may act on a book's paper account through the journal:
`paper run`, `resume`, `reconcile`, `start`, `stop`, `abandon` and `settle` each
take `run_lock(settings, book_id)` for their whole duration. It is an exclusive
`fcntl.flock` on `<store.path>.paper.<book>.lock`, taken without waiting: a
second holder of the same book's lock gets `LockHeld` at once (the second
`paper run` exits `locked`, spec acceptance "Kill mid-run"), never a queue
behind the first. Another book's lock is another file, so books never wait on
each other (each book is its own account, ADR 0017 B.4). `book_id` defaults to
`paper.book_id` (the spec's `--book` default) and must match the book token
grammar (`store.journal.check_book_id`), so it can never name a path outside
the store's directory.

The operating system releases a `flock` when its file descriptor closes, so a
crash, a `kill -9` or a reboot leaves nothing to clean up and no stale lock to
break by hand. The lock file itself stays; its content names the last holder's
pid, for the `LockHeld` message only.

`is_held(settings, book_id)` is for readers that never take the lock (the operations
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
from contextlib import ExitStack, contextmanager
from pathlib import Path

from tradepartner.config import MAIN_BOOK_ID, SANDBOX_BOOK_ID, Settings
from tradepartner.store.journal import check_book_id

#: Owner read and write only: the file names a pid, nothing else.
_LOCK_FILE_MODE = 0o600


class LockHeld(RuntimeError):
    """Another process holds the paper run lock."""


def _temp_roots() -> set[Path]:
    """The fixed directories a throwaway store copy may live under: `/tmp`, resolved
    (`/private/tmp` on macOS). Not `tempfile.gettempdir()`: `TMPDIR` must not widen it."""
    return {Path("/tmp").resolve()}


def sandbox_marker(settings: Settings) -> Path:
    """`<store.path>.sandbox`: the file that marks a store as a sandbox COPY. The runbook
    creates it beside the copy; the live store never has one."""
    return Path(f"{settings.store.path}.sandbox")


def _resolved_sandbox_marker(settings: Settings) -> Path:
    """The sandbox marker beside the fully resolved store path (symlinks followed)."""
    return Path(f"{Path(settings.store.path).resolve()}.sandbox")


def sandbox_store_refusal(settings: Settings, book_id: str) -> str | None:
    """Why `book_id` may not run on `settings.store.path`, or `None` (owner decision
    2026-10-28, docs/runbooks/sandbox.md). Both directions, so the copy (which holds the
    owner's real books and, with the real keys in `.env`, could trade them) and the live
    store never mix: book `sandbox` needs a store that resolves under a fixed temp root
    AND carries the marker; every other book is refused on a marked store. Pure reads."""
    store = Path(settings.store.path).resolve()
    # The marker beside the RESOLVED store decides: a file symlink to a marked copy is
    # the sandbox; a symlink (even one with a marker beside it) to the live store is not.
    marked = _resolved_sandbox_marker(settings).is_file()
    if book_id != SANDBOX_BOOK_ID:
        if marked:
            return (
                f"book {book_id!r} is refused on a sandbox copy of the store (marker "
                f"{_resolved_sandbox_marker(settings).name} present): only book "
                f"{SANDBOX_BOOK_ID!r} "
                "runs there (docs/runbooks/sandbox.md)"
            )
        return None
    if marked and any(root in store.parents for root in _temp_roots()):
        return None
    return (
        f"book {SANDBOX_BOOK_ID!r} runs only on a temp copy of the store, never the live "
        f"one: store.path {settings.store.path!r} must be under /tmp and have the marker "
        "file <store.path>.sandbox (docs/runbooks/sandbox.md)"
    )


def resolve_book(settings: Settings, book_id: str | None) -> str:
    """The command's book: `book_id`, or `paper.book_id` when none is given (the
    spec's `--book` default); `ValueError` outside the book token grammar, or for book
    `sandbox` on a store that is not a temp copy (`sandbox_store_refusal`)."""
    book = settings.paper.book_id if book_id is None else book_id
    check_book_id(book)
    refusal = sandbox_store_refusal(settings, book)
    if refusal is not None:
        raise ValueError(refusal)
    return book


def _resolved_store(settings: Settings) -> Path:
    """The fully resolved store path (symlinks followed), so a symlink and its target
    take the same lock (#2557). A path with no symlink in it keeps its lock names."""
    return Path(settings.store.path).resolve()


def lock_path(settings: Settings, book_id: str | None = None) -> Path:
    """`<store.path>.paper.<book>.lock`, beside the store it guards; `book_id`
    defaults to `paper.book_id`. `ValueError` outside the book token grammar."""
    return Path(f"{_resolved_store(settings)}.paper.{resolve_book(settings, book_id)}.lock")


def legacy_lock_path(settings: Settings) -> Path:
    """`<store.path>.paper.lock`, the one lock before books (T59). Book `main`
    takes it too (after its own), so a process still on the old code and one on
    the new exclude each other across the upgrade while H1's window is open."""
    return Path(f"{_resolved_store(settings)}.paper.lock")


def _holder(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    return text or "another process"


@contextmanager
def run_lock(settings: Settings, book_id: str | None = None) -> Iterator[None]:
    """Hold the book's exclusive run lock for the `with` block, or raise
    `LockHeld` at once when another holder of that book's lock exists (in this
    process or another). Released when the block exits, normally or by an
    exception. `book_id` defaults to `paper.book_id`. Book `main` also holds
    `legacy_lock_path` for the whole block, refused the same way."""
    book = resolve_book(settings, book_id)
    with ExitStack() as stack:
        stack.enter_context(_flock(lock_path(settings, book)))
        if book == MAIN_BOOK_ID:
            stack.enter_context(_flock(legacy_lock_path(settings)))
        yield


@contextmanager
def _flock(path: Path) -> Iterator[None]:
    """The exclusive non-blocking `flock` on `path` for the block (`LockHeld`)."""
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


def is_held(settings: Settings, book_id: str | None = None) -> bool:
    """True while some process holds the book's run lock (`book_id` defaults to
    `paper.book_id`). Never creates the lock file: before the book's first run
    there is no file and nothing holds it. A process that holds the lock gets
    True here for its own lock; it must not ask. For book `main`, a holder of
    `legacy_lock_path` (old code) counts as held too."""
    book = resolve_book(settings, book_id)
    if _held(lock_path(settings, book)):
        return True
    return book == MAIN_BOOK_ID and _held(legacy_lock_path(settings))


def _held(path: Path) -> bool:
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
