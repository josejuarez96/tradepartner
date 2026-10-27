"""The Lab's one Run slot (plan T298, #2318; amendment 2026-10-27 #2304, note 3, 4, 6).

One run at a time, found by every browser tab and by a restarted dashboard: the
handle is a slot file, `<store.path>.lab-run.json`, not `st.session_state`.

* `take` creates the file with `O_CREAT | O_EXCL` (a second take, a double click
  included, is `Busy`) and is `Busy` while its `in_flight` argument (the registry's
  runs in flight, read by the caller) names anything. A file with no PID yet
  (between the create and the `record` after `Popen`) is `Busy` for
  `grace_seconds` and never taken over inside it. A file whose PID is gone, or alive
  with another `ps -o lstart= -p <pid>` start time (PID reuse), is stale: it is
  renamed aside first, then the slot is created with `O_EXCL`, so two tabs racing
  for it get one winner.
* `record` writes the PID, its start time, kind, slug, baseline, start and log path
  (temp file, then `os.replace`). `watch` starts a daemon thread that waits on the
  child and writes its exit code the same way. A restart loses only that code,
  which never decides a verdict.
* `status` reads only the slot file, the PID and the log, takes no connection, and
  returns the log's tail through the `scrub` it is given.
* `release` renames the file to `<store.path>.lab-run.last.json` once the child has
  gone; `read_last` reads the baseline and exit code back after it. The next
  `take` clears it.

This module takes the child handle (anything with `pid` and `wait()`) and the scrub
function as arguments; it imports neither the child module nor the store.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import stat
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

_FILE_MODE = 0o600


class Child(Protocol):
    """The part of a `Popen` the slot uses."""

    pid: int

    def wait(self) -> int:
        """Block until the child exits and return its exit code."""
        ...


@dataclass(frozen=True)
class SlotRecord:
    """What the slot file holds once the child has started."""

    pid: int
    pid_start: str | None
    kind: str
    slug: str
    baseline: int | None
    started_at: str
    log_path: str
    exit_code: int | None = None


@dataclass(frozen=True)
class Taken:
    """The slot is ours; the caller spawns, then calls `record` (or `abandon`)."""

    slot_path: Path
    token: str = ""


@dataclass(frozen=True)
class Busy:
    """The slot is not free; `reasons` names each cause."""

    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SlotStatus:
    """A read of the slot: no store connection, the log tail already scrubbed."""

    state: Literal["idle", "starting", "running", "ended", "stale"]
    record: SlotRecord | None
    log_tail: str


StartTimeOf = Callable[[int], str | None]


class PsUnavailable(Exception):
    """`ps` failed or timed out: the PID's state is unknown, not gone."""


class SlotLost(Exception):
    """The slot `take` created is no longer the file at the slot path."""


PS_TIMEOUT_SECONDS = 10.0  # a bound on a local `ps`, not a trading threshold (#2357)


def process_start_time(pid: int, *, timeout_seconds: float = PS_TIMEOUT_SECONDS) -> str | None:
    """`ps -o lstart= -p <pid>` (list form, no shell); None when the PID is gone.

    Raises `PsUnavailable` when `ps` itself fails or times out (unknown, not gone).
    """
    try:
        out = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PsUnavailable(str(exc)) from exc
    text = out.stdout.strip()
    if out.returncode == 0 and text:
        return text
    if out.returncode == 1 and not text and not out.stderr.strip():
        return None  # ps's answer for a PID that does not exist
    raise PsUnavailable(f"ps exited {out.returncode}: {out.stderr.strip()[:200]}")


@contextlib.contextmanager
def _locked(slot_path: Path) -> Iterator[None]:
    """Hold an exclusive `flock` on `<slot>.lock` around every ownership check and
    change (create, takeover, record, exit code, release, abandon), across tabs and
    processes. The lock file is never removed."""
    lock = slot_path.with_name(f"{slot_path.name}.lock")
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, _FILE_MODE)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the lock


def last_path(slot_path: Path) -> Path:
    """`<store.path>.lab-run.last.json` for `<store.path>.lab-run.json`."""
    name = slot_path.name
    stem = name[: -len(".json")] if name.endswith(".json") else name
    return slot_path.with_name(f"{stem}.last.json")


def _parse(text: str) -> SlotRecord | None:
    try:
        d = json.loads(text)
        if not isinstance(d, dict) or not isinstance(d.get("pid"), int):
            return None
        baseline = d.get("baseline")
        code = d.get("exit_code")
        return SlotRecord(
            pid=d["pid"],
            pid_start=d.get("pid_start"),
            kind=str(d["kind"]),
            slug=str(d["slug"]),
            baseline=baseline if isinstance(baseline, int) else None,
            started_at=str(d["started_at"]),
            log_path=str(d["log_path"]),
            exit_code=code if isinstance(code, int) else None,
        )
    except (ValueError, KeyError, TypeError):
        return None


def _read(path: Path) -> SlotRecord | None:
    try:
        return _parse(path.read_text())
    except OSError:
        return None


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.tmp.{uuid.uuid4().hex}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, _FILE_MODE)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _alive(rec: SlotRecord, start_time_of: StartTimeOf) -> bool:
    """Same PID with the start time recorded; another start time is PID reuse."""
    if rec.pid_start is None:  # `ps` failed at record time: signal 0 is all we have
        try:
            os.kill(rec.pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    now_start = start_time_of(rec.pid)
    return now_start is not None and now_start == rec.pid_start


def _running(rec: SlotRecord, start_time_of: StartTimeOf) -> bool:
    """Live child; an unknown PID state (`ps` failed) counts as running (fail closed)."""
    if rec.exit_code is not None:
        return False
    try:
        return _alive(rec, start_time_of)
    except PsUnavailable:
        return True


def _create(slot_path: Path) -> Taken | None:
    try:
        fd = os.open(slot_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, _FILE_MODE)
    except FileExistsError:
        return None
    token = uuid.uuid4().hex
    try:
        os.write(fd, json.dumps({"token": token}).encode())
    finally:
        os.close(fd)
    with contextlib.suppress(OSError):
        last_path(slot_path).unlink()
    return Taken(slot_path, token)


def _in_grace(path: Path, now: datetime, grace_seconds: float) -> bool:
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except OSError:
        return False
    return (now - mtime).total_seconds() < grace_seconds


def take(
    slot_path: Path,
    *,
    in_flight: Sequence[str],
    now: datetime,
    grace_seconds: float,
    start_time_of: StartTimeOf = process_start_time,
) -> Taken | Busy:
    """Take the one slot, or say why it is busy. Never retries."""
    with _locked(slot_path):
        return _take(
            slot_path,
            in_flight=in_flight,
            now=now,
            grace_seconds=grace_seconds,
            start_time_of=start_time_of,
        )


def _take(
    slot_path: Path,
    *,
    in_flight: Sequence[str],
    now: datetime,
    grace_seconds: float,
    start_time_of: StartTimeOf,
) -> Taken | Busy:
    if now.tzinfo is None:
        raise ValueError("now must be tz-aware")
    if in_flight:
        return Busy(tuple(f"a run is in flight: {item}" for item in in_flight))
    if (made := _create(slot_path)) is not None:
        return made
    try:
        judged = slot_path.read_text()
    except FileNotFoundError:
        made = _create(slot_path)
        return made if made is not None else Busy(("another tab took the slot",))
    rec = _read(slot_path)
    if rec is None:
        if _in_grace(slot_path, now, grace_seconds):
            return Busy(("the slot was just taken and its run is starting",))
    elif _running(rec, start_time_of):
        return Busy((f"{rec.kind} run of {rec.slug} is running (pid {rec.pid})",))
    # Stale: rename aside first, then create with O_EXCL.
    aside = slot_path.with_name(f"{slot_path.name}.stale.{uuid.uuid4().hex}")
    try:
        os.rename(slot_path, aside)
    except FileNotFoundError:
        return Busy(("another tab took over the stale slot",))
    try:
        moved = aside.read_text()
    except OSError:
        moved = None
    if moved != judged:  # content, not inode: inode numbers are reused at once
        # We renamed a file another tab had just made: put it back.
        with contextlib.suppress(OSError):
            os.link(aside, slot_path)
        aside.unlink(missing_ok=True)
        return Busy(("another tab took over the stale slot",))
    aside.unlink(missing_ok=True)
    made = _create(slot_path)
    return made if made is not None else Busy(("another tab took over the stale slot",))


def abandon(slot_path: Path, taken: Taken) -> bool:
    """Remove the reservation `taken` made when its spawn failed before any child
    existed. Removes nothing unless the file still carries `taken`'s token."""
    with _locked(slot_path):
        try:
            current = json.loads(slot_path.read_text())
        except (OSError, ValueError):
            return False
        if not isinstance(current, dict) or current.get("token") != taken.token:
            return False
        with contextlib.suppress(OSError):
            slot_path.unlink()
        return True


def record(
    slot_path: Path,
    child: Child,
    *,
    kind: str,
    slug: str,
    baseline: int | None,
    started_at: datetime,
    log_path: Path,
    start_time_of: StartTimeOf = process_start_time,
    taken: Taken | None = None,
) -> SlotRecord:
    """Write the child's record into the slot (temp file, then `os.replace`).

    Given the `Taken` that `take` returned, raises `SlotLost` when the file at the
    slot path is no longer the one it created (another tab took the slot over
    after the grace); the caller must not treat the run as the slot's.
    """
    if started_at.tzinfo is None:
        raise ValueError("started_at must be tz-aware")
    try:
        pid_start = start_time_of(child.pid)
    except PsUnavailable:
        pid_start = None
    rec = SlotRecord(
        pid=child.pid,
        pid_start=pid_start,
        kind=kind,
        slug=slug,
        baseline=baseline,
        started_at=started_at.astimezone(UTC).isoformat(),
        log_path=str(log_path),
    )
    with _locked(slot_path):
        if taken is not None:
            try:
                current = json.loads(slot_path.read_text())
            except (OSError, ValueError) as exc:
                raise SlotLost(str(slot_path)) from exc
            if not isinstance(current, dict) or current.get("token") != taken.token:
                raise SlotLost(str(slot_path))
        _write_atomic(slot_path, _payload(rec))
    return rec


def _payload(rec: SlotRecord) -> dict[str, Any]:
    d: dict[str, Any] = {
        "pid": rec.pid,
        "pid_start": rec.pid_start,
        "kind": rec.kind,
        "slug": rec.slug,
        "baseline": rec.baseline,
        "started_at": rec.started_at,
        "log_path": rec.log_path,
    }
    if rec.exit_code is not None:
        d["exit_code"] = rec.exit_code
    return d


def _store_exit_code(slot_path: Path, pid: int, code: int) -> None:
    """Add the exit code to the record that still names `pid` (slot, else last)."""
    with _locked(slot_path):
        for path in (slot_path, last_path(slot_path)):
            rec = _read(path)
            if rec is not None and rec.pid == pid:
                done = SlotRecord(**{**rec.__dict__, "exit_code": code})
                with contextlib.suppress(OSError):
                    _write_atomic(path, _payload(done))
                return


def watch(slot_path: Path, child: Child) -> threading.Thread:
    """Start a daemon thread that waits on `child` and records its exit code."""

    def wait_and_record() -> None:
        try:
            code = child.wait()
        except Exception:
            return
        _store_exit_code(slot_path, child.pid, int(code))

    thread = threading.Thread(target=wait_and_record, name="lab-run-waiter", daemon=True)
    thread.start()
    return thread


def _tail(log_path: str, tail_bytes: int) -> str:
    try:
        fd = os.open(log_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return ""
    try:
        with os.fdopen(fd, "rb") as fh:
            info = os.fstat(fh.fileno())
            if not stat.S_ISREG(info.st_mode):
                return ""
            size = info.st_size
            offset = max(0, size - tail_bytes)
            fh.seek(offset)
            raw = fh.read(tail_bytes).decode("utf-8", errors="replace")
    except OSError:
        return ""
    if offset > 0:  # the cut may sit inside a secret: scrub whole lines only
        _, nl, rest = raw.partition("\n")
        return rest if nl else ""
    return raw


def status(
    slot_path: Path,
    *,
    scrub: Callable[[str], str],
    now: datetime,
    grace_seconds: float,
    start_time_of: StartTimeOf = process_start_time,
    tail_bytes: int,
) -> SlotStatus:
    """Read the slot file, the PID and the log only; no connection, tail scrubbed."""
    if not slot_path.exists():
        return SlotStatus("idle", None, "")
    rec = _read(slot_path)
    if rec is None:
        state: Literal["starting", "stale"] = (
            "starting" if _in_grace(slot_path, now, grace_seconds) else "stale"
        )
        return SlotStatus(state, None, "")
    tail = scrub(_tail(rec.log_path, tail_bytes))[-tail_bytes:]
    return SlotStatus("running" if _running(rec, start_time_of) else "ended", rec, tail)


def release(slot_path: Path, *, start_time_of: StartTimeOf = process_start_time) -> bool:
    """Rename the slot to `.last.json` once its child has gone; False while it runs."""
    with _locked(slot_path):
        rec = _read(slot_path)
        if rec is None:  # absent, or a starting reservation: not a finished run
            return False
        if _running(rec, start_time_of):
            return False
        try:
            os.replace(slot_path, last_path(slot_path))
        except FileNotFoundError:
            return False
        return True


def read_last(slot_path: Path) -> SlotRecord | None:
    """The released run's record (baseline, exit code), kept until the next take."""
    return _read(last_path(slot_path))
