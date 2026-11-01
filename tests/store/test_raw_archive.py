"""The content-addressed raw archive helper (plan T346, #2751; ADR 0028 rules 1-2,
Upload, Disk, Integrity; data-foundation spec amendment #2714 items 1, 3 and 4).

Every test writes only under its own `tmp_path`, fakes the disk through
`diskfloor.free_bytes` and fakes iCloud through `FakeICloud` (the one injectable
seam over `osascript` and `brctl`), so the suite runs on any OS and never touches
iCloud. `MacICloud`'s command shapes are checked with a fake runner; the one test
that drives the real commands is opt-in (`RUN_ICLOUD_TESTS=1` plus a scratch
iCloud folder in `TRADEPARTNER_ICLOUD_TEST_DIR`).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from tradepartner import diskfloor
from tradepartner.config import Settings
from tradepartner.store import raw_archive
from tradepartner.store.raw_archive import (
    ItemStatus,
    MacICloud,
    RawArchiveICloudError,
    RawArchiveIntegrityError,
    RawArchiveMissingError,
    RawArchiveOfflineError,
    archive_body,
    archive_manifest,
    body_path,
    download_inputs,
    evict_uploaded,
    is_archived,
    manifest_path,
    read_body,
    read_manifest,
    transfer_wait_s,
)
from tradepartner.store.raw_archive_config import ARCHIVE_ENV_NAME, RawArchiveFloorError

GB = 1_000_000_000
PLENTY = 500 * GB


@pytest.fixture(autouse=True)
def _no_archive_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shell's own archive settings never reach these tests."""
    for key in list(os.environ):
        if key.upper() == ARCHIVE_ENV_NAME or key.upper().startswith("RAW_ARCHIVE__"):
            monkeypatch.delenv(key)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "archive"
    path.mkdir()
    return path


def _settings(root: Path, **archive: float) -> Settings:
    return Settings(_env_file=None, tradepartner_raw_archive=str(root), raw_archive=archive)


class Disk:
    """A fake `diskfloor.free_bytes`: a queue of free-space readings, the last one
    repeated."""

    def __init__(self, *readings: int) -> None:
        self.readings = list(readings)
        self.calls = 0

    def __call__(self, _path: Path) -> int:
        self.calls += 1
        if len(self.readings) > 1:
            return self.readings.pop(0)
        return self.readings[0]


def _disk(monkeypatch: pytest.MonkeyPatch, *readings: int) -> Disk:
    disk = Disk(*readings)
    monkeypatch.setattr(diskfloor, "free_bytes", disk)
    return disk


@pytest.fixture(autouse=True)
def _plenty_of_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    _disk(monkeypatch, PLENTY)


class Clock:
    """A fake monotonic clock that `sleep` advances; it also runs a hook per
    sleep so a fake iCloud can change state as time passes."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []
        self.on_sleep: list[Callable[[], None]] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        for hook in self.on_sleep:
            hook()


class FakeICloud:
    """The iCloud seam, faked. Each path has a status; `upload_after` and
    `download_after` say after how many status polls of that path an upload or a
    requested download completes (`None`: never). `evict` makes a path dataless
    unless `evict_sticks` is false; `download` only requests."""

    def __init__(self) -> None:
        self.states: dict[Path, ItemStatus] = {}
        self.upload_after: dict[Path, int | None] = {}
        self.download_after: dict[Path, int | None] = {}
        self.polls: dict[Path, int] = {}
        self.requested: set[Path] = set()
        self.calls: list[tuple[str, Path]] = []
        self.evict_sticks = True
        self.default_upload_after: int | None = 0

    def state(self, path: Path) -> ItemStatus:
        return self.states.get(path, ItemStatus(uploaded=False, current=True, dataless=False))

    def make_dataless(self, path: Path) -> None:
        self.states[path] = ItemStatus(uploaded=True, current=False, dataless=True)

    def status(self, paths: Sequence[Path]) -> Mapping[Path, ItemStatus]:
        out = {}
        for path in paths:
            self.calls.append(("status", path))
            polls = self.polls.get(path, 0)
            self.polls[path] = polls + 1
            state = self.state(path)
            after = self.upload_after.get(path, self.default_upload_after)
            if not state.uploaded and after is not None and polls >= after:
                state = ItemStatus(uploaded=True, current=state.current, dataless=state.dataless)
            down = self.download_after.get(path, 0)
            if path in self.requested and state.dataless and down is not None and polls >= down:
                state = ItemStatus(uploaded=True, current=True, dataless=False)
                self.requested.discard(path)
            self.states[path] = state
            out[path] = state
        return out

    def evict(self, path: Path) -> None:
        self.calls.append(("evict", path))
        assert self.state(path).uploaded, "evicted before a confirmed upload"
        if self.evict_sticks:
            self.make_dataless(path)

    def download(self, path: Path) -> None:
        self.calls.append(("download", path))
        self.requested.add(path)
        self.polls[path] = 0

    def named(self, kind: str) -> list[Path]:
        return [p for k, p in self.calls if k == kind]


# --- archive-path audit: no archived body or manifest is ever removed or read ------

_EVENTS: list[tuple[str, str]] = []
_ARMED = [False]


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if not _ARMED[0] or not args:
        return
    if event in {"open", "os.remove", "os.rename", "os.rmdir", "shutil.rmtree", "os.truncate"}:
        _EVENTS.append((event, os.fspath(args[0]) if isinstance(args[0], str | Path) else ""))
        if event == "os.rename" and len(args) > 1 and isinstance(args[1], str | Path):
            _EVENTS.append(("os.rename.to", os.fspath(args[1])))


sys.addaudithook(_audit)


class Audit:
    def __enter__(self) -> list[tuple[str, str]]:
        _EVENTS.clear()
        _ARMED[0] = True
        return _EVENTS

    def __exit__(self, *_exc: object) -> None:
        _ARMED[0] = False


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _archive_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


# --- write: identical and changed bodies --------------------------------------------


def test_a_body_is_stored_once_under_its_sha256(root: Path) -> None:
    settings = _settings(root)
    body = b"<ownershipDocument>one</ownershipDocument>"
    first = archive_body(settings, body)
    assert first.sha256 == _sha(body)
    assert first.created is True
    assert first.size == len(body)
    path = body_path(root, first.sha256)
    assert path.read_bytes() == body
    assert path.relative_to(root).parts == ("raw", first.sha256[:2], first.sha256)
    again = archive_body(settings, body)
    assert again.sha256 == first.sha256
    assert again.created is False
    assert _archive_files(root) == [path]
    assert is_archived(settings, first.sha256)


def test_a_changed_body_gets_a_second_file_and_keeps_the_first(root: Path) -> None:
    settings = _settings(root)
    one = archive_body(settings, b"form.20261030.idx v1")
    two = archive_body(settings, b"form.20261030.idx v2")
    assert one.sha256 != two.sha256
    assert two.created is True
    assert body_path(root, one.sha256).read_bytes() == b"form.20261030.idx v1"
    assert body_path(root, two.sha256).read_bytes() == b"form.20261030.idx v2"
    assert len(_archive_files(root)) == 2


def test_a_held_name_with_a_different_size_is_refused_not_replaced(root: Path) -> None:
    """A final name exists only after a complete write, so a size mismatch is
    corruption: refused loudly, and the held file is left exactly as it is."""
    settings = _settings(root)
    body = b"twelve bytes"
    sha = archive_body(settings, body).sha256
    body_path(root, sha).write_bytes(b"short")
    with pytest.raises(RawArchiveIntegrityError, match=sha):
        archive_body(settings, body)
    assert body_path(root, sha).read_bytes() == b"short"


def test_the_archive_root_must_be_configured(tmp_path: Path) -> None:
    from tradepartner.store.raw_archive_config import RawArchiveUnavailableError

    settings = Settings(_env_file=None)
    with pytest.raises(RawArchiveUnavailableError, match=ARCHIVE_ENV_NAME):
        archive_body(settings, b"x")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("bad", ["", "ABC", "../" + "0" * 61, "g" * 64, "0" * 63, "0" * 65])
def test_a_malformed_hash_is_refused_before_any_path_is_built(root: Path, bad: str) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        body_path(root, bad)
    with pytest.raises(ValueError, match="SHA-256"):
        manifest_path(root, bad)


# --- interrupted write ---------------------------------------------------------------


def test_an_interrupted_write_leaves_no_body_and_no_temp_file(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(root)
    body = b"half written"

    def boom(_fd: int) -> None:
        raise OSError("disk went away")

    monkeypatch.setattr(raw_archive.os, "fsync", boom)
    with pytest.raises(OSError, match="disk went away"):
        archive_body(settings, body)
    monkeypatch.undo()
    _disk(monkeypatch, PLENTY)
    assert _archive_files(root) == []
    assert not is_archived(settings, _sha(body))
    # A rerun writes the body whole.
    assert archive_body(settings, body).created is True
    assert body_path(root, _sha(body)).read_bytes() == body


def test_a_temp_file_left_by_a_crash_is_not_a_held_body(root: Path) -> None:
    """A process killed mid-write leaves its temp file; that never counts as the
    body, so the next write stores it whole."""
    settings = _settings(root)
    body = b"crashed once"
    sha = _sha(body)
    shard = body_path(root, sha).parent
    shard.mkdir(parents=True)
    stray = shard / f"{sha}.deadbeef{raw_archive.TMP_SUFFIX}"
    stray.write_bytes(body[:3])
    assert not is_archived(settings, sha)
    assert archive_body(settings, body).created is True
    assert body_path(root, sha).read_bytes() == body


def test_temp_files_are_kept_out_of_icloud_sync() -> None:
    """iCloud Drive does not sync a name ending in `.nosync`, so a half-written
    body is never uploaded."""
    assert raw_archive.TMP_SUFFIX.endswith(".nosync")


# --- the 10 GB floor -------------------------------------------------------------------


def test_a_write_below_the_floor_is_refused_and_writes_nothing(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(root)
    body = b"x" * 100
    _disk(monkeypatch, 10 * GB + 99)
    with pytest.raises(RawArchiveFloorError):
        archive_body(settings, body)
    assert _archive_files(root) == []
    _disk(monkeypatch, 10 * GB + 100)
    assert archive_body(settings, body).created is True


def test_an_identical_body_writes_nothing_so_needs_no_floor(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(root)
    archive_body(settings, b"held")
    _disk(monkeypatch, 0)
    assert archive_body(settings, b"held").created is False


def test_a_manifest_write_below_the_floor_is_refused(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(root)
    _disk(monkeypatch, GB)
    with pytest.raises(RawArchiveFloorError):
        archive_manifest(settings, b'{"run": 1}')
    assert _archive_files(root) == []


# --- dedup of an evicted, dataless or `.icloud` placeholder ------------------------------


def test_a_dataless_body_dedups_by_name_without_being_read(root: Path) -> None:
    """Reading a dataless file downloads it silently (T354), so dedup goes by the
    content-addressed name and its size, never by opening the file."""
    settings = _settings(root)
    body = b"evicted body"
    sha = archive_body(settings, body).sha256
    path = body_path(root, sha)
    with Audit() as events:
        again = archive_body(settings, body)
        held = is_archived(settings, sha)
    assert again.created is False
    assert held
    assert ("open", str(path)) not in events
    assert _archive_files(root) == [path]


def test_an_icloud_placeholder_dedups_without_being_read(root: Path) -> None:
    """Older macOS replaces an evicted `name` with `.name.icloud`; T354 saw none on
    macOS 27, but a placeholder must still count as held."""
    settings = _settings(root)
    body = b"evicted the old way"
    sha = _sha(body)
    shard = body_path(root, sha).parent
    shard.mkdir(parents=True)
    placeholder = shard / f".{sha}.icloud"
    placeholder.write_bytes(b"bplist00 placeholder")
    with Audit() as events:
        again = archive_body(settings, body)
        held = is_archived(settings, sha)
    assert again.created is False
    assert held
    assert not body_path(root, sha).exists()
    assert not any(path == str(placeholder) for event, path in events if event == "open")
    assert _archive_files(root) == [placeholder]


def test_a_placeholder_for_another_hash_is_not_a_match(root: Path) -> None:
    settings = _settings(root)
    body = b"new body"
    sha = _sha(body)
    other = "0" * 64
    shard = body_path(root, sha).parent
    shard.mkdir(parents=True)
    (shard / f".{other}.icloud").write_bytes(b"bplist00")
    assert archive_body(settings, body).created is True


# --- manifests ---------------------------------------------------------------------------


def test_a_manifest_is_content_addressed_and_read_back_hash_checked(root: Path) -> None:
    settings = _settings(root)
    text = json.dumps({"writer_run_id": "r1", "entries": []}).encode()
    written = archive_manifest(settings, text)
    assert manifest_path(root, written.sha256).read_bytes() == text
    assert manifest_path(root, written.sha256).relative_to(root).parts[0] == "manifests"
    assert read_manifest(settings, written.sha256) == text
    manifest_path(root, written.sha256).write_bytes(b"tampered")
    with pytest.raises(RawArchiveIntegrityError):
        read_manifest(settings, written.sha256)


def test_reading_an_absent_manifest_is_refused(root: Path) -> None:
    with pytest.raises(RawArchiveMissingError):
        read_manifest(_settings(root), "a" * 64)


# --- the bounded wait ----------------------------------------------------------------------


def test_the_wait_scales_with_size_above_its_floor(root: Path) -> None:
    settings = _settings(root)
    assert transfer_wait_s(settings, 20_000_000, upload=True) == 300.0
    assert transfer_wait_s(settings, 1_500_000_000, upload=True) == pytest.approx(300.0)
    assert transfer_wait_s(settings, 2_000_000_000, upload=True) == pytest.approx(400.0)
    custom = _settings(root, upload_wait_min_s=10, download_wait_min_s=20, transfer_wait_s_per_mb=1)
    assert transfer_wait_s(custom, 5_000_000, upload=True) == 10.0
    assert transfer_wait_s(custom, 5_000_000, upload=False) == 20.0
    assert transfer_wait_s(custom, 50_000_000, upload=False) == 50.0


# --- eviction: only after a confirmed upload ----------------------------------------------


def _pass(
    settings: Settings, hashes: Sequence[str], icloud: FakeICloud, clock: Clock
) -> raw_archive.EvictionReport:
    return evict_uploaded(settings, hashes, icloud, clock=clock, sleep=clock.sleep)


def test_a_body_is_evicted_only_after_its_upload_is_confirmed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(root)
    sha = archive_body(settings, b"upload me").sha256
    path = body_path(root, sha)
    icloud, clock = FakeICloud(), Clock()
    icloud.upload_after[path] = 3
    _disk(monkeypatch, 20 * GB, 20 * GB)
    report = _pass(settings, [sha], icloud, clock)
    assert report.evicted == (sha,)
    assert report.pending_upload == ()
    kinds = [kind for kind, _ in icloud.calls]
    assert kinds.index("evict") > 3
    assert kinds.count("evict") == 1
    assert clock.sleeps == [5.0, 5.0, 5.0]
    assert report.upload_lag_s == {sha: 15.0}
    # Never unlinked: the evicted body keeps its name.
    assert path.exists()


def test_an_unconfirmed_upload_times_out_stays_local_and_is_reported(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(root, upload_wait_min_s=30, poll_interval_s=10)
    body = b"never uploads"
    sha = archive_body(settings, body).sha256
    icloud, clock = FakeICloud(), Clock()
    icloud.default_upload_after = None
    report = _pass(settings, [sha], icloud, clock)
    assert report.pending_upload == (sha,)
    assert report.evicted == ()
    assert report.local_bytes == len(body)
    assert icloud.named("evict") == []
    assert clock.now == pytest.approx(30.0)
    assert body_path(root, sha).read_bytes() == body


def test_an_eviction_that_does_not_leave_a_dataless_file_is_reported_failed(
    root: Path,
) -> None:
    settings = _settings(root)
    body = b"stubborn"
    sha = archive_body(settings, body).sha256
    icloud, clock = FakeICloud(), Clock()
    icloud.evict_sticks = False
    report = _pass(settings, [sha], icloud, clock)
    assert report.evict_failed == (sha,)
    assert report.evicted == ()
    assert report.local_bytes == len(body)


def test_a_local_copy_whose_hash_no_longer_matches_is_never_evicted(root: Path) -> None:
    settings = _settings(root)
    sha = archive_body(settings, b"original").sha256
    body_path(root, sha).write_bytes(b"corrupt!")
    icloud, clock = FakeICloud(), Clock()
    report = _pass(settings, [sha], icloud, clock)
    assert report.mismatched == (sha,)
    assert icloud.named("evict") == []


def test_an_already_dataless_body_is_neither_read_nor_evicted_again(root: Path) -> None:
    settings = _settings(root)
    sha = archive_body(settings, b"gone already").sha256
    path = body_path(root, sha)
    icloud, clock = FakeICloud(), Clock()
    icloud.make_dataless(path)
    with Audit() as events:
        report = _pass(settings, [sha], icloud, clock)
    assert report.already_evicted == (sha,)
    assert icloud.named("evict") == []
    assert ("open", str(path)) not in events


def test_freed_space_is_measured_never_assumed(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """T354: an evicted file shows `blocks=0` at once but APFS returns the space
    minutes later, so the report gives the measured change, here none."""
    settings = _settings(root)
    sha = archive_body(settings, b"y" * 1000).sha256
    disk = _disk(monkeypatch, 20 * GB, 20 * GB - 196_000)
    report = _pass(settings, [sha], FakeICloud(), Clock())
    assert report.evicted == (sha,)
    assert report.free_before == 20 * GB
    assert report.free_after == 20 * GB - 196_000
    assert report.freed_bytes == -196_000
    assert disk.calls == 2


def test_a_manifest_is_never_evicted(root: Path) -> None:
    """Manifests stay local: the eviction pass takes body hashes only, and a
    manifest's hash names no body."""
    settings = _settings(root)
    sha = archive_manifest(settings, b'{"run": "r1"}').sha256
    icloud, clock = FakeICloud(), Clock()
    with pytest.raises(RawArchiveMissingError, match=sha):
        _pass(settings, [sha], icloud, clock)
    assert icloud.named("evict") == []
    assert manifest_path(root, sha).exists()


def test_the_evict_guard_refuses_any_path_outside_the_body_tree(root: Path) -> None:
    settings = _settings(root)
    sha = archive_manifest(settings, b'{"run": "r2"}').sha256
    icloud = FakeICloud()
    with pytest.raises(RawArchiveIntegrityError, match="manifest"):
        raw_archive._evict(root, manifest_path(root, sha), icloud)
    assert icloud.named("evict") == []


# --- never unlink ----------------------------------------------------------------------------


def test_no_operation_removes_renames_over_or_truncates_an_archived_file(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting an iCloud file deletes its cloud copy (ADR 0028 Upload), so no
    path here ever removes, replaces or truncates a final archive name."""
    settings = _settings(root)
    with Audit() as events:
        one = archive_body(settings, b"keep me")
        archive_body(settings, b"keep me")
        two = archive_body(settings, b"keep me too")
        man = archive_manifest(settings, b'{"m": 1}')
        archive_manifest(settings, b'{"m": 1}')
        icloud, clock = FakeICloud(), Clock()
        _pass(settings, [one.sha256, two.sha256], icloud, clock)
        clock.on_sleep.append(lambda: None)
        download_inputs(settings, [one.sha256, two.sha256], icloud, clock=clock, sleep=clock.sleep)
        read_body(settings, one.sha256, icloud)
        read_manifest(settings, man.sha256)
    finals = {
        str(body_path(root, one.sha256)),
        str(body_path(root, two.sha256)),
        str(manifest_path(root, man.sha256)),
    }
    for event, path in events:
        if event in {"os.remove", "os.rmdir", "shutil.rmtree", "os.truncate", "os.rename"}:
            assert path.endswith(raw_archive.TMP_SUFFIX), (event, path)
            assert path not in finals
    renamed_to = [path for event, path in events if event == "os.rename.to"]
    # Each new final name is created exactly once, by a rename from its temp file.
    assert sorted(renamed_to) == sorted(finals)
    for path in finals:
        assert Path(path).exists()


def test_the_module_source_has_no_delete_call_on_a_final_name() -> None:
    """A static belt to the audit above: the only removal in the module is of its
    own temp file."""
    source = Path(raw_archive.__file__).read_text(encoding="utf-8")
    for call in ("rmtree", "os.remove(", "os.replace(", ".rmdir(", "os.unlink("):
        assert call not in source, call
    assert source.count(".unlink(") == 1
    assert "tmp.unlink(missing_ok=True)" in source


# --- download, offline, hash mismatch ---------------------------------------------------------


def _download(
    settings: Settings, hashes: Sequence[str], icloud: FakeICloud, clock: Clock
) -> tuple[raw_archive.ArchivedBody, ...]:
    return download_inputs(settings, hashes, icloud, clock=clock, sleep=clock.sleep)


def test_every_named_input_is_requested_and_verified_before_any_read(root: Path) -> None:
    settings = _settings(root)
    a = archive_body(settings, b"input a").sha256
    b = archive_body(settings, b"input b").sha256
    icloud, clock = FakeICloud(), Clock()
    icloud.make_dataless(body_path(root, a))
    icloud.make_dataless(body_path(root, b))
    icloud.download_after[body_path(root, a)] = 1
    icloud.download_after[body_path(root, b)] = 2
    at_first_wait: list[int] = []
    clock.on_sleep.append(lambda: at_first_wait.append(len(icloud.named("download"))))
    got = _download(settings, [a, b], icloud, clock)
    assert [x.sha256 for x in got] == [a, b]
    assert sorted(icloud.named("download")) == sorted([body_path(root, a), body_path(root, b)])
    # Every request goes out before the first wait.
    assert at_first_wait[0] == 2
    assert read_body(settings, a, icloud) == b"input a"


def test_a_local_input_is_not_requested_again(root: Path) -> None:
    settings = _settings(root)
    a = archive_body(settings, b"already local").sha256
    icloud, clock = FakeICloud(), Clock()
    got = _download(settings, [a], icloud, clock)
    assert [x.sha256 for x in got] == [a]
    assert icloud.named("download") == []


def test_a_missing_input_refuses_before_any_download(root: Path) -> None:
    settings = _settings(root)
    a = archive_body(settings, b"present").sha256
    absent = "b" * 64
    icloud, clock = FakeICloud(), Clock()
    icloud.make_dataless(body_path(root, a))
    with pytest.raises(RawArchiveMissingError, match=absent):
        _download(settings, [a, absent], icloud, clock)
    assert icloud.named("download") == []


def test_an_offline_input_refuses_after_the_bounded_wait(root: Path) -> None:
    settings = _settings(root, download_wait_min_s=20, poll_interval_s=5)
    a = archive_body(settings, b"offline").sha256
    b = archive_body(settings, b"online").sha256
    icloud, clock = FakeICloud(), Clock()
    for sha in (a, b):
        icloud.make_dataless(body_path(root, sha))
    icloud.download_after[body_path(root, a)] = None
    with pytest.raises(RawArchiveOfflineError, match=a) as info:
        _download(settings, [a, b], icloud, clock)
    assert b not in str(info.value)
    assert clock.now == pytest.approx(20.0)
    assert str(root) not in str(info.value)


def test_a_hash_mismatch_after_download_refuses(root: Path) -> None:
    settings = _settings(root)
    a = archive_body(settings, b"good").sha256
    b = archive_body(settings, b"also good").sha256
    body_path(root, b).write_bytes(b"also bad!")
    icloud, clock = FakeICloud(), Clock()
    with pytest.raises(RawArchiveIntegrityError, match=b):
        _download(settings, [a, b], icloud, clock)


def test_a_download_below_the_floor_refuses_before_any_request(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The floor counts the bytes the downloads will bring back."""
    settings = _settings(root)
    a = archive_body(settings, b"z" * 5000).sha256
    icloud, clock = FakeICloud(), Clock()
    icloud.make_dataless(body_path(root, a))
    _disk(monkeypatch, 10 * GB + 4999)
    with pytest.raises(RawArchiveFloorError):
        _download(settings, [a], icloud, clock)
    assert icloud.named("download") == []


def test_a_placeholder_input_is_downloaded_through_its_placeholder(root: Path) -> None:
    settings = _settings(root)
    body = b"placeholder body"
    sha = _sha(body)
    shard = body_path(root, sha).parent
    shard.mkdir(parents=True)
    placeholder = shard / f".{sha}.icloud"
    placeholder.write_bytes(b"bplist00")
    icloud, clock = FakeICloud(), Clock()

    def materialise() -> None:
        if placeholder.exists() and icloud.named("download"):
            body_path(root, sha).write_bytes(body)
            placeholder.unlink()

    clock.on_sleep.append(materialise)
    got = _download(settings, [sha], icloud, clock)
    assert icloud.named("download") == [placeholder]
    assert got[0].sha256 == sha


def test_reading_a_dataless_body_is_refused_rather_than_silently_downloaded(
    root: Path,
) -> None:
    settings = _settings(root)
    sha = archive_body(settings, b"cold").sha256
    path = body_path(root, sha)
    icloud = FakeICloud()
    icloud.make_dataless(path)
    with Audit() as events, pytest.raises(RawArchiveOfflineError, match="download_inputs"):
        read_body(settings, sha, icloud)
    assert ("open", str(path)) not in events


def test_every_body_read_is_hash_checked(root: Path) -> None:
    settings = _settings(root)
    sha = archive_body(settings, b"checked").sha256
    body_path(root, sha).write_bytes(b"swapped")
    with pytest.raises(RawArchiveIntegrityError, match=sha):
        read_body(settings, sha, FakeICloud())


def test_reading_an_absent_body_is_refused(root: Path) -> None:
    with pytest.raises(RawArchiveMissingError):
        read_body(_settings(root), "c" * 64, FakeICloud())


# --- MacICloud: the real commands, checked with a fake runner -----------------------------------


class Runner:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, "")


def test_mac_status_is_one_batched_jxa_call_with_paths_as_arguments(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b b"
    a.write_bytes(b"1")
    b.write_bytes(b"2")
    out = "\n".join(
        [
            json.dumps([str(a), True, "NSURLUbiquitousItemDownloadingStatusCurrent"]),
            json.dumps([str(b), False, None]),
        ]
    )
    runner = Runner(out)
    icloud = MacICloud(timeout_s=7.0, runner=runner)
    got = icloud.status([a, b])
    assert len(runner.calls) == 1
    argv, kwargs = runner.calls[0]
    assert argv[:3] == ["osascript", "-l", "JavaScript"]
    assert argv[-2:] == [str(a), str(b)]
    assert "NSURLUbiquitousItemIsUploadedKey" in argv[4]
    assert "NSURLUbiquitousItemDownloadingStatusKey" in argv[4]
    assert kwargs.get("shell", False) is False
    assert kwargs["timeout"] == 7.0
    assert got[a] == ItemStatus(uploaded=True, current=True, dataless=False)
    # A file outside iCloud has no downloading status: it is local, never uploaded.
    assert got[b] == ItemStatus(uploaded=False, current=True, dataless=False)


def test_mac_status_reads_not_downloaded_and_the_dataless_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = tmp_path / "a"
    a.write_bytes(b"1")
    out = json.dumps([str(a), True, "NSURLUbiquitousItemDownloadingStatusNotDownloaded"])
    monkeypatch.setattr(raw_archive, "_st_flags", lambda _p: raw_archive.SF_DATALESS | 0x60)
    got = MacICloud(timeout_s=1.0, runner=Runner(out)).status([a])
    assert got[a] == ItemStatus(uploaded=True, current=False, dataless=True)


def test_mac_status_refuses_output_that_does_not_name_every_path(tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.write_bytes(b"1")
    with pytest.raises(RawArchiveICloudError, match="status"):
        MacICloud(timeout_s=1.0, runner=Runner("")).status([a])


def test_mac_evict_and_download_run_brctl_without_a_shell(tmp_path: Path) -> None:
    a = tmp_path / "a"
    runner = Runner()
    icloud = MacICloud(timeout_s=3.0, runner=runner)
    icloud.evict(a)
    icloud.download(a)
    assert [argv for argv, _ in runner.calls] == [
        ["brctl", "evict", str(a)],
        ["brctl", "download", str(a)],
    ]
    assert all(kw.get("shell", False) is False and kw["timeout"] == 3.0 for _, kw in runner.calls)


def test_a_failed_command_names_the_command_never_the_path(tmp_path: Path) -> None:
    secret = tmp_path / "Mobile Documents" / "someone@example.com"
    runner = Runner(returncode=1)
    with pytest.raises(RawArchiveICloudError) as info:
        MacICloud(timeout_s=1.0, runner=runner).evict(secret)
    message = str(info.value)
    assert "brctl evict" in message
    assert str(secret) not in message
    assert "@" not in message


def test_a_timed_out_command_is_an_icloud_error(tmp_path: Path) -> None:
    def slow(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    with pytest.raises(RawArchiveICloudError, match="timed out") as info:
        MacICloud(timeout_s=1.0, runner=slow).download(tmp_path / "x")
    assert str(tmp_path) not in str(info.value)


def test_mac_icloud_is_built_from_config(root: Path) -> None:
    icloud = MacICloud.from_settings(_settings(root, command_timeout_s=9))
    assert icloud.timeout_s == 9.0


# --- the real commands (opt-in) ------------------------------------------------------------------


@pytest.mark.network
@pytest.mark.skipif(
    sys.platform != "darwin"
    or os.environ.get("RUN_ICLOUD_TESTS") != "1"
    or not os.environ.get("TRADEPARTNER_ICLOUD_TEST_DIR"),
    reason="real iCloud test; set RUN_ICLOUD_TESTS=1 and TRADEPARTNER_ICLOUD_TEST_DIR "
    "to an empty scratch iCloud Drive folder to run",
)
def test_real_icloud_upload_evict_download_round_trip() -> None:  # pragma: no cover
    """Writes one small random body into the owner's scratch iCloud folder, waits
    for its upload, evicts it, downloads it back and re-hashes it. The body is
    left in place (never unlink); the owner removes the scratch folder."""
    scratch = Path(os.environ["TRADEPARTNER_ICLOUD_TEST_DIR"])
    settings = Settings(_env_file=None, tradepartner_raw_archive=str(scratch))
    icloud = MacICloud.from_settings(settings)
    body = os.urandom(64 * 1024)
    sha = archive_body(settings, body).sha256
    report = evict_uploaded(settings, [sha], icloud)
    assert report.evicted == (sha,), report
    assert icloud.status([body_path(scratch, sha)])[body_path(scratch, sha)].dataless
    got = download_inputs(settings, [sha], icloud)
    assert got[0].sha256 == sha
    assert read_body(settings, sha, icloud) == body
