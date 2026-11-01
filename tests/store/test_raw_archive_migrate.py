"""The existing-cache migration runner, move and backup classes (plan T361a, #2765,
and T361b, #2775; spec amendment #2743 items 2, 3, 4 and 7). Everything runs under `tmp_path`,
with the disk and iCloud faked (`FakeICloud`), so no real archive, cache or store is touched."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator, Mapping, Sequence
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
    RestoreRefusedError,
    apply_migration,
    restore_files,
)
from tradepartner.store.raw_archive_plan import CacheFile, current_versions

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

    def run(
        self, run_id: str = "r1", at: datetime = NOW, **kw: object
    ) -> raw_archive_migrate.MigrationResult:
        return apply_migration(
            self.settings,
            self.cache,
            self.icloud,
            run_id=run_id,
            now=lambda: at,
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
    assert by_path["stamps/v1/0001.json"]["state"] == "evicted"
    assert by_path["stamps/v1/0001.json"]["sha256"] == _sha(b"stamp")
    assert by_path["stamps/v1/0001.json"]["class"] == "backup"
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


def test_a_torn_journal_line_does_not_hide_later_records(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = rig.cache / "_archived" / "journal.jsonl"
    journal.parent.mkdir()
    start = {"op": "start", "run": "r1", "at": NOW.isoformat()}
    journal.write_text(json.dumps(start) + '\n{"op": "inte')

    def stop(name: str) -> None:
        if name == "evict":
            raise Boom

    monkeypatch.setattr(raw_archive_migrate, "_step", stop)
    with pytest.raises(Boom):
        rig.run()
    monkeypatch.setattr(raw_archive_migrate, "_step", lambda _n: None)
    result = rig.run()
    assert len(rig.index()) == 2
    assert result.count("evicted") == 2


# ---- backup class (T361b) ----


def _manifest(rig: Rig, digest: str) -> dict[str, dict[str, object]]:
    doc = json.loads((rig.root / "manifests" / digest).read_text())
    return {f["path"]: f for f in doc["files"]}


def test_backups_are_copied_by_hash_confirmed_evicted_and_never_moved(rig: Rig) -> None:
    rig.put("stamps/v1.restamp-x/m.json", b"manifest")
    rig.put("stamps/v1.rel.bak", b"bak")
    before = {k: v for k, v in rig.tree().items() if k.startswith("stamps")}
    result = rig.run()
    assert rig.tree().items() >= before.items()  # every backup source is untouched
    assert result.backup_count("evicted") == 3
    for data in (b"stamp", b"manifest", b"bak"):
        assert body_path(rig.root, _sha(data)).exists()
    assert body_path(rig.root, _sha(b"stamp")) in rig.icloud.evicted
    by_path = _manifest(rig, result.manifest_sha256)
    assert {p: f["sha256"] for p, f in by_path.items() if f["class"] == "backup"} == {
        "stamps/v1/0001.json": _sha(b"stamp"),
        "stamps/v1.restamp-x/m.json": _sha(b"manifest"),
        "stamps/v1.rel.bak": _sha(b"bak"),
    }
    assert "stamps/v1/0001.json" not in {r["path"] for r in rig.index()}  # not tombstoned


def test_identical_backup_bytes_are_stored_once(rig: Rig) -> None:
    rig.put("stamps/v1/0002.json", b"stamp")
    result = rig.run()
    assert result.backup_count("evicted") == 2
    bodies = [p for p in rig.root.rglob("*") if p.is_file() and p.name == _sha(b"stamp")]
    assert len(bodies) == 1


def test_a_second_apply_after_a_stamps_rewrite_copies_only_the_changed_files(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.put("stamps/v1/0002.json", b"other")
    first = rig.run()
    rig.put("stamps/v1/0001.json", b"stamp-rewritten")
    written: list[int] = []
    real = raw_archive_migrate.archive_body
    monkeypatch.setattr(
        raw_archive_migrate, "archive_body", lambda s, b: (written.append(len(b)), real(s, b))[1]
    )
    second = rig.run()
    assert written == [len(b"stamp-rewritten")]
    assert second.manifest_sha256 != first.manifest_sha256
    snap = {p: f["sha256"] for p, f in _manifest(rig, second.manifest_sha256).items()}
    assert snap["stamps/v1/0001.json"] == _sha(b"stamp-rewritten")
    assert snap["stamps/v1/0002.json"] == _sha(b"other")  # the full snapshot
    old = _manifest(rig, first.manifest_sha256)
    assert old["stamps/v1/0001.json"]["sha256"] == _sha(b"stamp")


def test_below_the_floor_a_backup_stops_with_no_partial_file_and_resumes(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(diskfloor, "free_bytes", lambda _p: 10 * GB + 3)  # room for 3 bytes
    with pytest.raises(RawArchiveFloorError):
        rig.run()
    assert not body_path(rig.root, _sha(b"stamp")).exists()
    assert not [p for p in rig.root.rglob("*") if p.name.endswith(".tmp.nosync")]
    assert (rig.cache / "stamps/v1/0001.json").read_bytes() == b"stamp"
    monkeypatch.setattr(diskfloor, "free_bytes", lambda _p: 500 * GB)
    result = rig.run()
    assert result.backup_count("evicted") == 1
    assert body_path(rig.root, _sha(b"stamp")).exists()


def test_a_backup_source_rewritten_during_its_copy_is_retried_once_then_reported(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = rig.cache / "stamps/v1/0001.json"
    count = [0]

    def churn(name: str) -> None:
        if name == "backup_copy":
            count[0] += 1
            path.write_bytes(b"churn-%d" % count[0])

    monkeypatch.setattr(raw_archive_migrate, "_step", churn)
    result = rig.run()
    assert count[0] == 2  # one try and one retry
    assert result.backup_states == {"stamps/v1/0001.json": "changed"}

    flaky = [True]

    def once(name: str) -> None:
        if name == "backup_copy" and flaky[0]:
            flaky[0] = False
            path.write_bytes(b"once")

    monkeypatch.setattr(raw_archive_migrate, "_step", once)
    rig.put("stamps/v1/0001.json", b"first")
    again = rig.run()
    assert again.backup_states == {"stamps/v1/0001.json": "evicted"}
    assert _sha(b"once") in {p.name for p in rig.root.rglob("*")}


def test_an_unconfirmed_backup_upload_stays_pending_and_counted(rig: Rig) -> None:
    rig.icloud.never_upload = True
    result = rig.run()
    assert result.backup_states == {"stamps/v1/0001.json": "pending_upload"}
    assert result.backup_count("pending_upload") == 1
    assert body_path(rig.root, _sha(b"stamp")).read_bytes() == b"stamp"
    assert _manifest(rig, result.manifest_sha256)["stamps/v1/0001.json"]["state"] == (
        "pending_upload"
    )


@pytest.mark.parametrize(
    "step", ["backup_intent", "backup_copy", "backup_evict", "manifest", "evict"]
)
def test_an_interrupted_backup_run_resumes_to_the_uninterrupted_end_state(
    tmp_path: Path, step: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    rigs = (Rig(tmp_path / "whole"), Rig(tmp_path / "cut"))
    for r in rigs:
        r.put(f"{DEAD}/a.json", b"dead-a")
        r.put("stamps/v1/0001.json", b"stamp")
        r.put("stamps/v1/0002.json", b"stamp-2")
    whole, cut = rigs
    expected = whole.run()
    fired: list[str] = []

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
    assert result.backup_states == expected.backup_states
    assert _end_state(cut)[0] == _end_state(whole)[0]
    assert _end_state(cut)[2] == _end_state(whole)[2]
    assert len(cut.index()) == 1


def test_a_vanished_backup_source_is_reported_not_fatal(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    (rig.cache / "stamps/v1/0001.json").unlink()
    rig.put("stamps/v1/0002.json", b"two")
    # the planner lists what exists; simulate the file vanishing after planning
    real = raw_archive_migrate._listing

    def listing(cache: Path) -> Iterator[CacheFile]:
        yield from real(cache)
        os.unlink(cache / "stamps/v1/0002.json")

    monkeypatch.setattr(raw_archive_migrate, "_listing", listing)
    result = rig.run()
    assert result.backup_states["stamps/v1/0002.json"] == "changed"


@pytest.mark.parametrize("step", ["backup_intent", "backup_copy"])
def test_a_backup_only_run_cut_before_its_settle_still_writes_the_manifest(
    rig: Rig, step: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fired: list[str] = []

    def stop(name: str) -> None:
        if name == step and not fired:
            fired.append(name)
            raise Boom

    monkeypatch.setattr(raw_archive_migrate, "_step", stop)
    clean = tmp_rig = Rig(rig.cache.parent / "clean")
    clean.put("stamps/v1/0001.json", b"stamp")
    for p in [rig.cache / DEAD, rig.cache / "corpus", rig.cache / "bulk"]:
        import shutil

        shutil.rmtree(p)
    with pytest.raises(Boom):
        rig.run()
    monkeypatch.setattr(raw_archive_migrate, "_step", lambda _n: None)
    result = rig.run()
    assert result.manifest_sha256 == tmp_rig.run().manifest_sha256 != ""
    assert result.backup_states == {"stamps/v1/0001.json": "evicted"}


# --- restore (T361c) ---------------------------------------------------------


def _restore(rig: Rig, path: str, **kw: object) -> raw_archive_migrate.RestoreResult:
    return restore_files(
        rig.settings,
        rig.cache,
        rig.icloud,
        path=path,
        now=lambda: NOW,
        **kw,  # type: ignore[arg-type]
    )


class RestoreICloud(FakeICloud):
    """Evicted bodies come back local on `download`, as brctl does."""

    def download(self, path: Path) -> None:
        self.evicted.discard(path)


def test_restored_bytes_path_and_mtime_equal_the_original_and_the_body_stays(rig: Rig) -> None:
    rig.icloud = RestoreICloud()
    corpus = rig.cache / "corpus/departure-reason/raw/b.json"
    corpus_mtime = corpus.stat().st_mtime_ns
    rig.run()
    result = _restore(rig, "corpus/departure-reason")
    assert result.restored == {"corpus/departure-reason/raw/b.json": _sha(b"corpus-b")}
    assert corpus.read_bytes() == b"corpus-b"
    assert corpus.stat().st_mtime_ns == corpus_mtime
    assert body_path(rig.root, _sha(b"corpus-b")).exists()  # the archived body stays
    assert not (rig.cache / DEAD / "a.json").exists()  # out of scope, not restored
    last = rig.index()[-1]
    assert last["op"] == "restored"
    assert last["path"] == "corpus/departure-reason/raw/b.json"
    assert not [p for p in rig.cache.rglob(".restore-*")]


def test_restore_refuses_an_offline_body_with_nothing_written(rig: Rig) -> None:
    rig.run()  # FakeICloud.download raises: the bodies are evicted and cannot come back
    before = rig.tree()
    index = rig.index()
    with pytest.raises(AssertionError):
        _restore(rig, DEAD)
    assert rig.tree() == before
    assert rig.index() == index
    assert not (rig.cache / DEAD / "a.json").exists()


def test_restore_refuses_a_mismatched_body_with_nothing_written(rig: Rig) -> None:
    rig.run()
    body = body_path(rig.root, _sha(b"dead-a"))
    rig.icloud.evicted.clear()
    body.write_bytes(b"tampered")
    index = rig.index()
    with pytest.raises(raw_archive.RawArchiveIntegrityError):
        _restore(rig, DEAD)
    assert not (rig.cache / DEAD / "a.json").exists()
    assert rig.index() == index


def test_restore_never_overwrites_a_local_backup_file_and_names_each(rig: Rig) -> None:
    rig.put("stamps/v1/0002.json", b"other")
    rig.run()
    before = rig.tree()
    with pytest.raises(RestoreRefusedError) as exc:
        _restore(rig, "stamps/v1")
    assert "stamps/v1/0001.json" in str(exc.value)
    assert "stamps/v1/0002.json" in str(exc.value)
    assert rig.tree() == before


def test_restore_run_from_removed_files_returns_either_snapshot(rig: Rig) -> None:
    rig.icloud = RestoreICloud()
    rig.put("stamps/v1/0002.json", b"other")
    rig.run(run_id="r1")
    rig.put("stamps/v1/0001.json", b"stamp-rewritten")
    rig.run(run_id="r2", at=datetime(2026, 11, 2, tzinfo=UTC))
    snap = {"stamps/v1/0001.json", "stamps/v1/0002.json"}
    for run, expect in (("r1", b"stamp"), ("r2", b"stamp-rewritten"), (None, b"stamp-rewritten")):
        for rel in snap:
            (rig.cache / rel).unlink()
        result = _restore(rig, "stamps/v1", run=run)
        assert set(result.restored) == snap
        assert (rig.cache / "stamps/v1/0001.json").read_bytes() == expect
        assert (rig.cache / "stamps/v1/0002.json").read_bytes() == b"other"
    assert body_path(rig.root, _sha(b"stamp")).exists()  # both snapshots' bodies stay


def test_restore_refuses_when_nothing_matches_or_the_run_is_unknown(rig: Rig) -> None:
    rig.run()
    with pytest.raises(RestoreRefusedError):
        _restore(rig, "nope/")
    with pytest.raises(RestoreRefusedError):
        _restore(rig, "stamps/v1", run="zzz")


def test_restore_stops_below_the_floor_with_nothing_written(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.icloud = RestoreICloud()
    rig.run()
    monkeypatch.setattr(diskfloor, "free_bytes", lambda _p: 1)
    with pytest.raises(RawArchiveFloorError):
        _restore(rig, DEAD)
    assert not (rig.cache / DEAD / "a.json").exists()
