"""The Lab's child process: argv, spawn and log (plan T290, #2320; amendment
2026-10-27, #2304; ADR 0018 section B).

The Lab runs the desk's own commands as children of the same interpreter, as
`book_actions` does for a kill: `sys.executable` running the `tradepartner`
console script installed beside it (the CLI module is never named or imported
here), list form, no shell. Three argv shapes exist, every option in
`--name=value` form and the positional after `--`:

* `sweep run [--note=<n>] -- <slug>`
* `backtest --start=<d> --end=<d> [--note=<n>] -- <slug>`
* `sweep register -- <absolute path>`

Never `--spend-holdout`, `--holdout-reason`, `--holdout-repeat`,
`--override-gap`, `--gap-reason`, `--rerun` or `--measured-cost-bps`: those are
desk actions, and no builder here can emit one.

**Checks before anything starts** (each a `LabRefused`, nothing spawned): the
slug against `[a-z0-9][a-z0-9-]*` and found by the page's own read
(`lab_registry.sweep_by_slug` for a sweep, `registry.get_hypothesis` for a
hypothesis); the note typed non-blank, at most `ui.max_reason_chars`, with no
control characters; and the page reading the live store with no backup
manifest beside it (`book_actions.live_settings` / `live_store_path`, reused,
not edited; `book_actions` imports nothing from the lab).

**The spawn never waits.** `Popen` with `start_new_session=True` (a dashboard
restart never kills a trial), stdin `DEVNULL`, stdout and stderr to one new log
file per run, `<store.path>.lab-run.log`, opened `O_WRONLY | O_CREAT | O_TRUNC |
O_NOFOLLOW` and set to `0600` with `fchmod`, never a pipe (a page that does not
read a pipe stalls the child once its buffer fills, and a pipe dies with the
dashboard). The raw log is never shown: `scrubbed_tail` is the only reader, and
it removes every secret of the page's and the live settings. The child gets the
page's store as an absolute `STORE__PATH`, no `TRADEPARTNER_ENV_FILE`, and the
project root as its working directory. Waiting on the child and the slot are
`lab_run`'s (T298), which takes the handle returned here.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb

from tradepartner.config import Settings, secret_values
from tradepartner.dashboard import book_actions
from tradepartner.store import lab_registry, lab_schema, registry

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_STORE_VARS = ("STORE__PATH", "TRADEPARTNER_ENV_FILE", "STORE")
#: The default size of the log tail the page shows.
TAIL_BYTES = 4000
LOG_SUFFIX = ".lab-run.log"
_MASK = b"\x00"  # same-length stand-in while byte offsets matter


class LabKind(StrEnum):
    """What a spawned Run is."""

    SWEEP_RUN = "sweep_run"
    BACKTEST = "backtest"


class LabRefused(ValueError):
    """Refused before the command started; nothing ran."""


@dataclass(frozen=True)
class Spawned:
    """A started child: `child` is the `Popen` handle (never waited on here),
    `log_path` the file its text goes to."""

    child: Any
    pid: int
    kind: LabKind
    slug: str
    argv: tuple[str, ...]
    log_path: Path


def _entry_point() -> Path:
    """The `tradepartner` console script (`[project.scripts]`) beside `sys.executable`."""
    return Path(sys.executable).parent / "tradepartner"


def check_slug(slug: str) -> str:
    """`slug` if it fits `[a-z0-9][a-z0-9-]*`, else `LabRefused`."""
    if not isinstance(slug, str) or _SLUG.fullmatch(slug) is None:
        raise LabRefused(f"{slug!r} is not a valid name: lowercase letters, digits and dashes.")
    return slug


def note_refusal(note: str | None, settings: Settings) -> str | None:
    """Why `note` cannot be sent, or None. None (no note) is fine; a note that is
    present must not be blank, over `ui.max_reason_chars`, or hold a control character."""
    if note is None:
        return None
    if not note.strip():
        return "A blank note is refused: type one or leave it out."
    cap = settings.ui.max_reason_chars
    if len(note) > cap:
        return f"The note is {len(note)} characters; the limit is {cap}."
    if any(unicodedata.category(c) in {"Cc", "Cf"} for c in note):
        return "The note holds a control or invisible formatting character."
    return None


def _note_option(note: str | None, settings: Settings) -> list[str]:
    refusal = note_refusal(note, settings)
    if refusal is not None:
        raise LabRefused(refusal)
    return [] if note is None else [f"--note={note}"]


def _base() -> list[str]:
    return [sys.executable, str(_entry_point())]


def sweep_run_argv(slug: str, note: str | None, settings: Settings) -> list[str]:
    """`sweep run [--note=<n>] -- <slug>`."""
    return [*_base(), "sweep", "run", *_note_option(note, settings), "--", check_slug(slug)]


def backtest_argv(
    slug: str, start: date, end: date, note: str | None, settings: Settings
) -> list[str]:
    """`backtest --start=<d> --end=<d> [--note=<n>] -- <slug>`."""
    if start > end:
        raise LabRefused(f"The window starts ({start}) after it ends ({end}).")
    return [
        *_base(),
        "backtest",
        f"--start={start.isoformat()}",
        f"--end={end.isoformat()}",
        *_note_option(note, settings),
        "--",
        check_slug(slug),
    ]


def register_argv(path: Path) -> list[str]:
    """`sweep register -- <absolute path>`. A relative path or one with a NUL is refused."""
    text = str(path)
    if not path.is_absolute() or "\x00" in text:
        raise LabRefused("The sweep file must be named by an absolute path.")
    return [*_base(), "sweep", "register", "--", text]


def store_refusal(settings: Settings, live: Settings) -> str | None:
    """None when the page reads the live store with no backup manifest beside it."""
    page = Path(settings.store.path).resolve()
    try:
        same = os.path.samefile(page, book_actions.live_store_path(live))
    except (ValueError, OSError):
        same = False
    if not same:
        return "This page is reading a store that is not the live store, so nothing runs."
    if page.with_name(f"{page.name}.manifest.json").exists():
        return (
            "This page is reading a store with a backup manifest beside it, so it cannot run here."
        )
    return None


def log_path(settings: Settings) -> Path:
    """`<store.path>.lab-run.log`, absolute."""
    store = Path(settings.store.path).resolve()
    return store.with_name(f"{store.name}{LOG_SUFFIX}")


def open_log(path: Path) -> int:
    """Open `path` `O_WRONLY | O_CREAT | O_NOFOLLOW`, check it is a regular file with
    one link, then truncate it and set it `0600` (also when a wider file was there).
    A symlink, a non-regular file or a hard-linked file in its place is refused,
    before anything is truncated."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        raise LabRefused(f"The run log {path.name} cannot be opened ({exc.strerror}).") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise LabRefused(f"The run log {path.name} is not a plain single-link file.")
        os.ftruncate(fd, 0)
        os.fchmod(fd, 0o600)
    except BaseException:
        os.close(fd)
        raise
    return fd


def scrub(text: str, *settings: Settings) -> str:
    """`text` with every secret value of `settings` replaced and control characters blanked."""
    values = {v for s in settings for v in secret_values(s)}
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    return _CONTROL.sub(" ", _ANSI.sub(" ", text)).strip()


def scrubbed_tail(path: Path, settings: Settings, *, max_bytes: int = TAIL_BYTES) -> str:
    """The last `max_bytes` of the run log, scrubbed over the page's settings and the
    live ones. A secret cut by the window's start is removed too: the window reads
    past `max_bytes` by the longest secret, scrubs, then trims. If the live config
    cannot be read the tail is not shown at all."""
    try:
        live = book_actions.live_settings()
    except (ValueError, OSError):
        return "(the log is not shown: the live config could not be read)"
    secrets = [v for s in (settings, live) for v in secret_values(s)]
    margin = max((len(v.encode()) for v in secrets), default=0)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return ""
        size = os.fstat(fd).st_size
        want = max_bytes + margin
        os.lseek(fd, max(0, size - want), os.SEEK_SET)
        raw = os.read(fd, want)
    finally:
        os.close(fd)
    masked = bytearray(raw)
    for value in sorted({v.encode() for v in secrets}, key=len, reverse=True):
        masked = bytearray(masked.replace(value, _MASK * len(value)))
    window = bytes(masked[-max_bytes:])
    text = window.decode(errors="replace")
    text = re.sub(f"{_MASK.decode()}+", "[redacted]", text)
    text = _CONTROL.sub(" ", _ANSI.sub(" ", text)).strip()
    return text


def _child_env(settings: Settings) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k.upper() not in _STORE_VARS}
    env["STORE__PATH"] = str(Path(settings.store.path).resolve())
    return env


def _known(conn: duckdb.DuckDBPyConnection, kind: LabKind, slug: str) -> bool:
    if kind is LabKind.SWEEP_RUN:
        return lab_registry.sweep_by_slug(conn, slug) is not None
    try:
        registry.get_hypothesis(conn, slug)
    except registry.UnknownHypothesis:
        return False
    return True


def spawn(
    settings: Settings,
    conn: duckdb.DuckDBPyConnection,
    kind: LabKind,
    slug: str,
    *,
    note: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> Spawned:
    """Start one `sweep run` or `backtest` child and return at once (module
    docstring). `conn` is the caller's short read-only connection, used only to
    find `slug`. `LabRefused` when any check fails, before anything starts."""
    if kind is LabKind.SWEEP_RUN:
        argv = sweep_run_argv(slug, note, settings)
    else:
        if start is None or end is None:
            raise LabRefused("A backtest needs a first and a last session.")
        argv = backtest_argv(slug, start, end, note, settings)
    try:
        live = book_actions.live_settings()
    except (ValueError, OSError) as exc:
        raise LabRefused("The live store's config could not be read, so nothing ran.") from exc
    refusal = store_refusal(settings, live)
    if refusal is not None:
        raise LabRefused(refusal)
    try:
        found = _known(conn, kind, slug)
    except (registry.RegistryError, lab_schema.LabNotInitialised, duckdb.Error) as exc:
        raise LabRefused(f"{slug!r} could not be looked up in the registry.") from exc
    if not found:
        raise LabRefused(f"{slug!r} is not registered, so it cannot run.")
    if not _entry_point().is_file():
        raise LabRefused("The tradepartner command is not installed beside this interpreter.")
    path = log_path(settings)
    fd = open_log(path)
    try:
        child = subprocess.Popen(
            argv,
            env=_child_env(settings),
            cwd=book_actions.PROJECT_ROOT,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=fd,
            stderr=fd,
            start_new_session=True,
        )
    finally:
        os.close(fd)
    return Spawned(child, child.pid, kind, slug, tuple(argv), path)
