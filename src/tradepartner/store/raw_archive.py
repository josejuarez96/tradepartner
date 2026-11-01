"""The content-addressed raw archive (plan T346, #2751; ADR 0028 rules 1-2 and its
Storage rules; data-foundation spec amendment #2714 items 1, 3 and 4).

This is the one module that opens archive paths. Ingest and re-derive call it;
no backtest, universe, signal or research reader may (the source scan in
`tests/lookahead/test_raw_archive_boundary.py`). That scan is the real guard:
reading an evicted iCloud file silently downloads it (T354), so the file system
does not stop a reader.

**Layout** under the root that `raw_archive_config.require_archive_root` checks:
a raw body lives at `raw/<first two hex>/<sha256>`, a provenance manifest at
`manifests/<sha256>`. The name is the content's SHA-256, so it is the hash index:
an identical body is found by name and size (`os.lstat`), never by reading it, so
an evicted (dataless) body or an older macOS `.<sha256>.icloud` placeholder dedups
without a download. A sync conflict copy ("name 2") never matches a name.

**Write** (`archive_body`, `archive_manifest`). A new body is checked against the
`raw_archive.min_free_disk_gb` floor (free bytes minus its size), written to a
temp file ending in `.nosync` (iCloud does not sync it), fsynced, and renamed to
its final name only if that name is still free. A final name therefore exists
only for a complete body, and no write ever replaces one. An interrupted write
leaves at most its temp file, which never counts as held.

**Evict** (`evict_uploaded`), a deferred pass over named body hashes: poll the
upload status, bounded by `raw_archive.upload_wait_min_s` scaled by size
(`transfer_wait_s`); once a body's upload is confirmed, re-hash its local copy,
call `brctl evict`, and require a dataless file afterwards. A body still
unconfirmed at the deadline stays local, counted (`local_bytes`) and reported
pending. Manifests are never evicted: the pass takes body hashes only, and
`_evict` refuses any path outside the body tree. Free space is read before and
after the pass and the measured change reported; an eviction is never counted as
freed space (APFS returns it minutes later, T354).

**Download** (`download_inputs`), before a re-derive parses anything: every named
hash must be held; the floor is checked against the bytes the downloads bring
back; `brctl download` is requested for every non-local body, then each is waited
for (bounded by `raw_archive.download_wait_min_s`) and re-hashed. Any missing,
offline or mismatched body refuses the whole set before a single parse. There is
no fallback to a fresh fetch.

**Read** (`read_body`, `read_manifest`) re-checks the hash on every read and
refuses a dataless body instead of downloading it behind the floor's back.

Nothing here unlinks, replaces or truncates an archived file: deleting an iCloud
file deletes its cloud copy (ADR 0028 Upload). The only removal is of this
module's own temp file.

**macOS calls** sit behind `ICloudItems`: `MacICloud` reads the upload and
downloading status with one `osascript -l JavaScript` call per batch (Foundation
URL resource keys, T354; no pyobjc), the dataless flag from `os.lstat`, and runs
`brctl evict` / `brctl download`, always with an argument list and no shell. No
message names an archive path, which carries the macOS account name; messages
name hashes and commands instead.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from tradepartner import diskfloor
from tradepartner.config import Settings
from tradepartner.store.raw_archive_config import check_archive_write, require_archive_root

#: Raw bodies live under this directory of the archive root.
BODY_DIR = "raw"
#: Provenance manifests live under this one; they stay local and are never evicted.
MANIFEST_DIR = "manifests"
#: A temp file's suffix. iCloud Drive does not sync a name ending in `.nosync`, so
#: a half-written body is never uploaded.
TMP_SUFFIX = ".tmp.nosync"
#: Older macOS replaces an evicted `name` with `.name.icloud`.
PLACEHOLDER_SUFFIX = ".icloud"
#: `SF_DATALESS` in `st_flags` (`<sys/stat.h>`): the file's data is in iCloud only.
SF_DATALESS = 0x40000000
#: Bytes per MB in the size-scaled waits.
MB = 1_000_000
_CHUNK = 1 << 20
_SHA256 = re.compile(r"[0-9a-f]{64}")

_CURRENT = "NSURLUbiquitousItemDownloadingStatusCurrent"
#: JXA run handler: one JSON line `[path, uploaded, downloading status]` per
#: argument. Fresh `NSURL` objects per call, so no cached resource value is read.
_STATUS_SCRIPT = """
ObjC.import('Foundation');
function run(argv) {
  var out = [];
  for (var i = 0; i < argv.length; i++) {
    var u = $.NSURL.fileURLWithPath(argv[i]);
    var up = Ref(); var dl = Ref(); var e = Ref();
    u.getResourceValueForKeyError(up, 'NSURLUbiquitousItemIsUploadedKey', e);
    u.getResourceValueForKeyError(dl, 'NSURLUbiquitousItemDownloadingStatusKey', e);
    out.push(JSON.stringify([argv[i], ObjC.unwrap(up[0]) === true,
      ObjC.unwrap(dl[0]) === undefined ? null : ObjC.unwrap(dl[0])]));
  }
  return out.join('\\n');
}
"""


class RawArchiveIntegrityError(RuntimeError):
    """An archived file's bytes do not match its hash, or a path is not what its
    caller may act on."""


class RawArchiveMissingError(RuntimeError):
    """A named hash is not held in the archive."""


class RawArchiveOfflineError(RuntimeError):
    """A held body is not local (evicted) where its bytes are needed."""


class RawArchiveICloudError(RuntimeError):
    """An `osascript` or `brctl` call failed or timed out."""


@dataclass(frozen=True)
class ItemStatus:
    """One file's iCloud state. `current` is true for a local copy (and for a file
    outside iCloud); `dataless` is the `SF_DATALESS` flag."""

    uploaded: bool
    current: bool
    dataless: bool


class ICloudItems(Protocol):
    """The macOS calls, injectable so tests run with a fake on any OS."""

    def status(self, paths: Sequence[Path]) -> Mapping[Path, ItemStatus]:
        """The state of every path, in one call."""
        ...

    def evict(self, path: Path) -> None:
        """Ask iCloud to evict the local copy (`brctl evict`)."""
        ...

    def download(self, path: Path) -> None:
        """Ask iCloud to bring the file back (`brctl download`); does not wait."""
        ...


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _st_flags(path: Path) -> int:
    """The file's BSD flags (0 where the platform has none)."""
    return int(getattr(os.lstat(path), "st_flags", 0))


@dataclass(frozen=True)
class MacICloud:
    """`ICloudItems` on macOS: `osascript` for status, `brctl` for evict and
    download, each call capped at `timeout_s` and run without a shell."""

    timeout_s: float
    runner: Runner = field(default=subprocess.run)

    @classmethod
    def from_settings(cls, settings: Settings) -> MacICloud:
        """The real calls, capped at `raw_archive.command_timeout_s`."""
        return cls(timeout_s=settings.raw_archive.command_timeout_s)

    def _run(self, name: str, argv: list[str]) -> str:
        try:
            done = self.runner(argv, capture_output=True, text=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired:
            raise RawArchiveICloudError(f"{name} timed out after {self.timeout_s:g} s") from None
        except OSError as exc:
            raise RawArchiveICloudError(f"{name} could not run ({type(exc).__name__})") from None
        if done.returncode != 0:
            raise RawArchiveICloudError(f"{name} failed with exit code {done.returncode}")
        return str(done.stdout)

    def status(self, paths: Sequence[Path]) -> Mapping[Path, ItemStatus]:
        """One JXA call for all `paths`; the dataless flag from `os.lstat`."""
        if not paths:
            return {}
        argv = ["osascript", "-l", "JavaScript", "-e", _STATUS_SCRIPT, *map(str, paths)]
        out = self._run("osascript status", argv)
        by_text: dict[str, tuple[bool, object]] = {}
        for line in out.splitlines():
            if line.strip():
                text, uploaded, downloading = json.loads(line)
                by_text[str(text)] = (bool(uploaded), downloading)
        result = {}
        for path in paths:
            if str(path) not in by_text:
                raise RawArchiveICloudError(f"osascript status returned no row for {path.name}")
            uploaded, downloading = by_text[str(path)]
            result[path] = ItemStatus(
                uploaded=uploaded,
                current=downloading is None or downloading == _CURRENT,
                dataless=bool(_st_flags(path) & SF_DATALESS),
            )
        return result

    def evict(self, path: Path) -> None:
        """`brctl evict <path>`."""
        self._run("brctl evict", ["brctl", "evict", str(path)])

    def download(self, path: Path) -> None:
        """`brctl download <path>`."""
        self._run("brctl download", ["brctl", "download", str(path)])


@dataclass(frozen=True)
class Archived:
    """One archive write: the content's SHA-256, whether this call created the
    file (`False`: the hash was already held), and its size in bytes."""

    sha256: str
    created: bool
    size: int


@dataclass(frozen=True)
class ArchivedBody:
    """A body verified local and matching its hash, ready for a re-derive."""

    sha256: str
    path: Path
    size: int


@dataclass(frozen=True)
class EvictionReport:
    """One eviction pass. Every hash lands in exactly one tuple. `local_bytes` is
    what stays on the laptop (pending, failed or mismatched) and so still counts
    against the floor; `freed_bytes` is the measured change in free space, never
    the sum of evicted sizes; `upload_lag_s` is the observed time to a confirmed
    upload, from the start of the pass."""

    evicted: tuple[str, ...]
    already_evicted: tuple[str, ...]
    pending_upload: tuple[str, ...]
    evict_failed: tuple[str, ...]
    mismatched: tuple[str, ...]
    local_bytes: int
    free_before: int
    free_after: int
    upload_lag_s: Mapping[str, float]

    @property
    def freed_bytes(self) -> int:
        """Measured: free space after the pass minus before (may be zero or less)."""
        return self.free_after - self.free_before


def _check_sha(sha256: str) -> str:
    if not _SHA256.fullmatch(sha256):
        raise ValueError("expected a lowercase hex SHA-256 of 64 characters")
    return sha256


def body_path(root: Path, sha256: str) -> Path:
    """Where the raw body with this hash lives under `root`."""
    _check_sha(sha256)
    return root / BODY_DIR / sha256[:2] / sha256


def manifest_path(root: Path, sha256: str) -> Path:
    """Where the provenance manifest with this hash lives under `root`."""
    _check_sha(sha256)
    return root / MANIFEST_DIR / sha256


def _placeholder(path: Path) -> Path:
    return path.with_name(f".{path.name}{PLACEHOLDER_SUFFIX}")


def _held_size(path: Path) -> int | None:
    """The held file's logical size by `lstat` (a dataless file keeps it), `-1` for
    a `.icloud` placeholder (size unknown), `None` if not held. Never reads it."""
    try:
        return os.lstat(path).st_size
    except FileNotFoundError:
        pass
    return -1 if os.path.lexists(_placeholder(path)) else None


def _write(settings: Settings, root: Path, path: Path, body: bytes) -> Archived:
    sha = path.name
    held = _held_size(path)
    if held is None:
        check_archive_write(settings, root, len(body))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{sha}.{uuid.uuid4().hex}{TMP_SUFFIX}")
        try:
            with tmp.open("xb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            held = _held_size(path)
            if held is None:
                tmp.rename(path)
                _fsync_dir(path.parent)
                return Archived(sha256=sha, created=True, size=len(body))
        finally:
            tmp.unlink(missing_ok=True)
    if held not in (-1, len(body)):
        raise RawArchiveIntegrityError(
            f"archived {sha} holds {held} bytes, not {len(body)}: the archive is damaged; "
            "it is left as it is"
        )
    return Archived(sha256=sha, created=False, size=len(body))


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def archive_body(settings: Settings, body: bytes) -> Archived:
    """Keep `body` under its SHA-256 before it is parsed. An identical body is
    found by name and writes nothing; a new one is floor-checked and written
    atomically. Raises `RawArchiveUnavailableError`, `RawArchiveFloorError` or
    `RawArchiveIntegrityError`; the caller fails its source run on any of them."""
    root = require_archive_root(settings)
    return _write(settings, root, body_path(root, hashlib.sha256(body).hexdigest()), body)


def archive_manifest(settings: Settings, body: bytes) -> Archived:
    """Keep a writer run's provenance manifest under its SHA-256. Manifests stay
    local: no pass here evicts one."""
    root = require_archive_root(settings)
    return _write(settings, root, manifest_path(root, hashlib.sha256(body).hexdigest()), body)


def is_archived(settings: Settings, sha256: str) -> bool:
    """Whether a body with this hash is held, local or evicted, by name only."""
    root = require_archive_root(settings)
    return _held_size(body_path(root, sha256)) is not None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _verified(path: Path, sha256: str, kind: str) -> bytes:
    if _held_size(path) is None:
        raise RawArchiveMissingError(f"{kind} {sha256} is not in the raw archive")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise RawArchiveIntegrityError(f"archived {kind} {sha256} does not match its hash")
    return data


def read_manifest(settings: Settings, sha256: str) -> bytes:
    """A manifest's bytes, hash-checked."""
    root = require_archive_root(settings)
    return _verified(manifest_path(root, sha256), sha256, "manifest")


def read_body(settings: Settings, sha256: str, icloud: ICloudItems) -> bytes:
    """A raw body's bytes, hash-checked. A body that is not local is refused, not
    read: reading would download it silently, past the floor check that
    `download_inputs` makes."""
    root = require_archive_root(settings)
    path = body_path(root, sha256)
    if _held_size(path) is None:
        raise RawArchiveMissingError(f"body {sha256} is not in the raw archive")
    if not os.path.exists(path) or not _is_local(icloud.status([path])[path]):
        raise RawArchiveOfflineError(
            f"body {sha256} is not local; run download_inputs for it before reading"
        )
    return _verified(path, sha256, "body")


def _is_local(state: ItemStatus) -> bool:
    return state.current and not state.dataless


def transfer_wait_s(settings: Settings, size_bytes: int, *, upload: bool) -> float:
    """The bounded wait for `size_bytes` to upload (or download): the configured
    minimum, or `raw_archive.transfer_wait_s_per_mb` per MB if that is longer."""
    cfg = settings.raw_archive
    floor = cfg.upload_wait_min_s if upload else cfg.download_wait_min_s
    return max(floor, cfg.transfer_wait_s_per_mb * size_bytes / MB)


def _held_bodies(root: Path, hashes: Sequence[str]) -> dict[str, Path]:
    """Each named hash's body path, refusing (before any action) any hash that
    names no held body."""
    paths = {sha: body_path(root, sha) for sha in dict.fromkeys(hashes)}
    missing = [sha for sha, path in paths.items() if _held_size(path) is None]
    if missing:
        raise RawArchiveMissingError(
            f"{len(missing)} named bodies are not in the raw archive: {', '.join(missing)}"
        )
    return paths


def _evict(root: Path, path: Path, icloud: ICloudItems) -> None:
    """The one place `brctl evict` is called: only for a body under the body tree."""
    if path.parent.parent != root / BODY_DIR:
        raise RawArchiveIntegrityError("refused: only a raw body is evicted, never a manifest")
    icloud.evict(path)


def evict_uploaded(
    settings: Settings,
    hashes: Sequence[str],
    icloud: ICloudItems,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> EvictionReport:
    """Evict each named body once its upload is confirmed, within the bounded
    wait; see the module docstring. Never unlinks; never evicts a manifest."""
    root = require_archive_root(settings)
    paths = _held_bodies(root, hashes)
    free_before = diskfloor.free_bytes(root)
    start = clock()
    sizes = {sha: max(_held_size(path) or 0, 0) for sha, path in paths.items()}
    evicted: list[str] = []
    already: list[str] = []
    failed: list[str] = []
    mismatched: list[str] = []
    lags: dict[str, float] = {}
    waiting = {sha: path for sha, path in paths.items() if os.path.exists(path)}
    already.extend(sha for sha in paths if sha not in waiting)
    deadline = start + transfer_wait_s(settings, sum(sizes[s] for s in waiting), upload=True)
    poll = settings.raw_archive.poll_interval_s
    first = True
    while waiting:
        if not first:
            now = clock()
            if now >= deadline:
                break
            sleep(min(poll, deadline - now))
        first = False
        states = icloud.status(list(waiting.values()))
        for sha, path in list(waiting.items()):
            state = states[path]
            if state.dataless:
                already.append(sha)
            elif state.uploaded:
                lags[sha] = clock() - start
                if _hash_file(path) != sha:
                    mismatched.append(sha)
                else:
                    _evict(root, path, icloud)
                    after = icloud.status([path])[path]
                    (evicted if after.dataless and not after.current else failed).append(sha)
            else:
                continue
            del waiting[sha]
    pending = list(waiting)
    order = {sha: i for i, sha in enumerate(paths)}
    local = sum(sizes[sha] for sha in (*pending, *failed, *mismatched))
    return EvictionReport(
        evicted=tuple(sorted(evicted, key=order.__getitem__)),
        already_evicted=tuple(sorted(already, key=order.__getitem__)),
        pending_upload=tuple(sorted(pending, key=order.__getitem__)),
        evict_failed=tuple(sorted(failed, key=order.__getitem__)),
        mismatched=tuple(sorted(mismatched, key=order.__getitem__)),
        local_bytes=local,
        free_before=free_before,
        free_after=diskfloor.free_bytes(root),
        upload_lag_s=lags,
    )


def download_inputs(
    settings: Settings,
    hashes: Sequence[str],
    icloud: ICloudItems,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[ArchivedBody, ...]:
    """Make every named body local and verify its hash, before a re-derive parses
    anything; see the module docstring. Raises `RawArchiveMissingError`,
    `RawArchiveFloorError`, `RawArchiveOfflineError`, `RawArchiveIntegrityError`
    or `RawArchiveICloudError`, each before any body is returned."""
    root = require_archive_root(settings)
    paths = _held_bodies(root, hashes)
    present = [path for path in paths.values() if os.path.exists(path)]
    states = icloud.status(present)
    offline = {
        sha: path
        for sha, path in paths.items()
        if not os.path.exists(path) or not _is_local(states[path])
    }
    sizes = {sha: max(_held_size(path) or 0, 0) for sha, path in offline.items()}
    check_archive_write(settings, root, sum(sizes.values()))
    for path in offline.values():
        icloud.download(path if os.path.exists(path) else _placeholder(path))
    start = clock()
    deadline = start + transfer_wait_s(settings, sum(sizes.values()), upload=False)
    poll = settings.raw_archive.poll_interval_s
    while offline:
        now = clock()
        if now >= deadline:
            break
        sleep(min(poll, deadline - now))
        landed = [path for path in offline.values() if os.path.exists(path)]
        states = icloud.status(landed)
        for sha, path in list(offline.items()):
            if path in states and _is_local(states[path]):
                del offline[sha]
    if offline:
        raise RawArchiveOfflineError(
            f"{len(offline)} named bodies did not download within the bounded wait: "
            f"{', '.join(offline)}; nothing was parsed"
        )
    out = []
    for sha, path in paths.items():
        if _hash_file(path) != sha:
            raise RawArchiveIntegrityError(
                f"archived body {sha} does not match its hash after download; nothing was parsed"
            )
        out.append(ArchivedBody(sha256=sha, path=path, size=os.lstat(path).st_size))
    return tuple(out)
