"""The existing-cache migration runner, move class (plan T361, #2765; spec
amendment #2743 items 2, 4 and 7). Everything runs under `tmp_path`, with the disk
and iCloud faked (`FakeICloud`), so no real archive, cache or store is touched."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradepartner import diskfloor
from tradepartner.config import Settings
from tradepartner.store import raw_archive, raw_archive_migrate
from tradepartner.store.raw_archive import ItemStatus, body_path
from tradepartner.store.raw_archive_config import ARCHIVE_ENV_NAME, RawArchiveFloorError
from tradepartner.store.raw_archive_migrate import (
    MigrationRefusedError,
    MigrationStoppedError,
    apply_migration,
)
from tradepartner.store.raw_archive_plan import current_versions

GB = 1_000_000_000
NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
DEAD = f"fsn/v{current_versions()['fsn'] + 7}"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in list(os.environ):
        if key.upper() == ARCHIVE_ENV_NAME or key.upper().startswith("RAW_ARCHIVE__"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(diskfloor, "free_bytes", lambda _p: 500 * GB)


class FakeICloud:
    def __init__(self) -> None:
        self.never_upload = False
        self.evicted: set[Path] = set()

    def status(self, paths: Sequence[Path]) -> Mapping[Path, ItemStatus]:
        return {
            p: ItemStatus(uploaded=True, current=False, dataless=True)
            if p in self.evicted
            else ItemStatus(uploaded=not self.never_upload, current=True, dataless=False)
            for p in paths
        }

    def evict(self, path: Path) -> None:
        self.evicted.add(path)

    def download(self, path: Path) -> None:
        raise AssertionError("a migration never downloads")


class Rig:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "archive"
        self.root.mkdir(parents=True)
        self.cache = tmp_path / "cache"
        self.cache.mkdir()
        self.icloud = FakeICloud()
        self.settings = Settings(
            _env_file=None,
            tradepartner_raw_archive=str(self.root),
            raw_archive={"upload_wait_min_s": 0.01, "poll_interval_s": 0.01},
        )

    def put(self, rel: str, data: bytes, mtime: int = 1_700_000_000) -> Path:
        path = self.cache / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        os.utime(path, ns=(mtime * 10**9, mtime * 10**9))
        return path

    def run(self, **kw: object) -> raw_archive_migrate.MigrationResult:
        return apply_migration(
            self.settings,
            self.cache,
            self.icloud,
            run_id="r1",
            now=lambda: NOW,
            **kw,  # type: ignore[arg-type]
        )

    def index(self) -> list[dict[str, object]]:
        path = self.cache / "_archived" / "index.jsonl"
        return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []

    def tree(self) -> dict[str, bytes]:
        return {
            p.relative_to(self.cache).as_posix(): p.read_bytes()
            for p in sorted(self.cache.rglob("*"))
            if p.is_file() and "_archived" not in p.parts
        }


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    r = Rig(tmp_path)
    r.put(f"{DEAD}/a.json", b"dead-a")
    r.put("corpus/departure-reason/raw/b.json", b"corpus-b", mtime=1_600_000_000)
    r.put("stamps/v1/0001.json", b"stamp")
    r.put("bulk/companyfacts.zip", b"bulk")
    return r


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_move_class_files_move_by_hash_and_the_rest_stays(rig: Rig) -> None:
    kept = {k: v for k, v in rig.tree().items() if k.startswith(("stamps", "bulk"))}
    mtime = os.stat(rig.cache / DEAD / "a.json").st_mtime_ns
    result = rig.run()

    assert not (rig.cache / DEAD / "a.json").exists()
    assert body_path(rig.root, _sha(b"dead-a")).read_bytes() == b"dead-a"
    assert os.stat(body_path(rig.root, _sha(b"dead-a"))).st_mtime_ns == mtime
    assert {k: v for k, v in rig.tree().items()} == kept
    assert result.states == {
        f"{DEAD}/a.json": "evicted",
        "corpus/departure-reason/raw/b.json": "evicted",
    }
    index = {str(r["path"]): r for r in rig.index()}
    assert index[f"{DEAD}/a.json"]["sha256"] == _sha(b"dead-a")
    assert index[f"{DEAD}/a.json"]["mtime_ns"] == mtime
    assert index[f"{DEAD}/a.json"]["manifest"] == result.manifest_sha256
    manifest = json.loads((rig.root / "manifests" / result.manifest_sha256).read_text())
    by_path = {f["path"]: f for f in manifest["files"]}
    assert by_path[f"{DEAD}/a.json"]["state"] == "evicted"
    assert by_path["stamps/v1/0001.json"]["state"] == "backup_pending"
    assert manifest["started_at"] == NOW.isoformat()
    assert (rig.cache / "_archived" / "journal-r1.jsonl").exists()
    assert not (rig.cache / "_archived" / "journal.jsonl").exists()


def test_a_cross_filesystem_root_refuses_before_any_change(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(raw_archive_migrate, "same_filesystem", lambda *_a: False)
    before = rig.tree()
    with pytest.raises(MigrationRefusedError):
        rig.run()
    assert rig.tree() == before
    assert not (rig.cache / "_archived").exists()
    assert list(rig.root.rglob("*")) == []


def test_below_the_floor_the_moves_run_and_a_rerun_finishes_the_manifest(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diskfloor, "free_bytes", lambda _p: GB)  # floor is 10 GB
    with pytest.raises(RawArchiveFloorError):
        rig.run()
    assert body_path(rig.root, _sha(b"dead-a")).exists()
    assert not (rig.cache / DEAD / "a.json").exists()
    monkeypatch.setattr(diskfloor, "free_bytes", lambda _p: 500 * GB)
    result = rig.run()
    assert result.count("evicted") == 2
    assert len(rig.index()) == 2


def _end_state(rig: Rig) -> tuple[object, ...]:
    paths = sorted(str(p.relative_to(rig.root)) for p in rig.root.rglob("*") if p.is_file())
    return (paths, sorted((str(r["path"]), r["sha256"]) for r in rig.index()), rig.tree())


class Boom(Exception):
    pass


@pytest.mark.parametrize("step", ["intent", "rename", "evict", "manifest"])
def test_an_interrupted_run_resumes_to_the_uninterrupted_end_state(
    tmp_path: Path, step: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    whole = Rig(tmp_path / "whole")
    cut = Rig(tmp_path / "cut")
    for r in (whole, cut):
        r.put(f"{DEAD}/a.json", b"dead-a")
        r.put("corpus/departure-reason/raw/b.json", b"corpus-b")
    expected = whole.run()

    fired = []

    def stop(name: str) -> None:
        if name == step and not fired:
            fired.append(name)
            raise Boom

    monkeypatch.setattr(raw_archive_migrate, "_step", stop)
    with pytest.raises(Boom):
        cut.run()
    monkeypatch.setattr(raw_archive_migrate, "_step", lambda _n: None)
    result = cut.run()

    assert result.manifest_sha256 == expected.manifest_sha256
    assert result.states == expected.states
    assert len(cut.index()) == 2  # nothing tombstoned twice
    got, want = _end_state(cut), _end_state(whole)
    assert got[0] == want[0] and got[1] == want[1]  # nothing lost, nothing archived twice


def test_an_intent_with_neither_source_nor_body_stops_the_run(rig: Rig) -> None:
    journal = rig.cache / "_archived" / "journal.jsonl"
    journal.parent.mkdir()
    ghost = {"op": "intent", "path": "corpus/departure-reason/raw/x", "bytes": 1, "mtime_ns": 1}
    ghost["sha256"] = _sha(b"x")
    journal.write_text(
        json.dumps({"op": "start", "run": "r1", "at": NOW.isoformat()})
        + "\n"
        + json.dumps(ghost)
        + "\n"
    )
    with pytest.raises(MigrationStoppedError):
        rig.run()


def test_a_source_changed_after_its_intent_is_kept_and_reported(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stop(name: str) -> None:
        if name == "intent":
            raise Boom

    monkeypatch.setattr(raw_archive_migrate, "_step", stop)
    with pytest.raises(Boom):
        rig.run()
    monkeypatch.setattr(raw_archive_migrate, "_step", lambda _n: None)
    first = next(p for p in sorted(rig.tree()) if p.startswith(("corpus", "fsn")))
    rig.put(first, b"rewritten")
    result = rig.run()
    assert result.states[first] == "changed"
    assert (rig.cache / first).read_bytes() == b"rewritten"
    assert first not in {r["path"] for r in rig.index()}


def test_an_unconfirmed_upload_stays_pending_local_and_counted(rig: Rig) -> None:
    rig.icloud.never_upload = True
    result = rig.run()
    assert result.count("pending_upload") == 2
    assert not rig.icloud.evicted
    assert body_path(rig.root, _sha(b"dead-a")).read_bytes() == b"dead-a"
    assert len(rig.index()) == 2  # still findable
    manifest = json.loads((rig.root / "manifests" / result.manifest_sha256).read_text())
    assert [f["state"] for f in manifest["files"] if f["class"] == "move"] == ["pending_upload"] * 2


def test_a_duplicate_is_unlinked_only_after_the_local_body_re_hashes(rig: Rig) -> None:
    rig.put("corpus/departure-reason/raw/b.json", b"dead-a")  # same bytes as the dead file
    result = rig.run()
    states = sorted(result.states.values())
    assert states == ["duplicate", "evicted"] or states == ["evicted", "evicted"]
    assert not (rig.cache / "corpus/departure-reason/raw/b.json").exists()
    assert body_path(rig.root, _sha(b"dead-a")).exists()


def test_a_duplicate_stays_local_when_the_archived_body_is_evicted_or_damaged(
    rig: Rig,
) -> None:
    held = body_path(rig.root, _sha(b"dead-a"))
    held.parent.mkdir(parents=True)
    held.write_bytes(b"dead-a")
    rig.icloud.evicted.add(held)  # evicted: not local
    damaged = body_path(rig.root, _sha(b"corpus-b"))
    damaged.parent.mkdir(parents=True, exist_ok=True)
    damaged.write_bytes(b"corpus-X")  # local but does not re-hash (same size)
    result = rig.run()
    assert result.states == {
        f"{DEAD}/a.json": "duplicate_kept",
        "corpus/departure-reason/raw/b.json": "duplicate_kept",
    }
    assert (rig.cache / DEAD / "a.json").read_bytes() == b"dead-a"
    assert rig.index() == []  # nothing left the cache, so nothing is tombstoned


def test_no_archive_file_is_unlinked_and_no_reader_is_called(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("corpus/departure-reason/raw/c.json", b"dead-a")
    removed: list[str] = []
    real_unlink: Callable[..., None] = os.unlink

    def spy(path: str | Path, *a: object, **k: object) -> None:
        removed.append(os.fspath(path))
        real_unlink(path, *a, **k)

    monkeypatch.setattr(os, "unlink", spy)
    for name in ("read_body", "download_inputs", "read_manifest"):
        monkeypatch.setattr(
            raw_archive, name, lambda *_a, **_k: pytest.fail("the archive was read")
        )
    rig.run()
    assert removed and not any(
        p.startswith(str(rig.root)) and not p.endswith(".tmp.nosync") for p in removed
    )


def test_a_rerun_with_nothing_to_move_changes_nothing(rig: Rig) -> None:
    rig.run()
    before = (_end_state(rig), sorted(os.listdir(rig.cache / "_archived")))
    again = rig.run()
    assert again.states == {} and again.manifest_sha256 == ""
    assert (_end_state(rig), sorted(os.listdir(rig.cache / "_archived"))) == before
