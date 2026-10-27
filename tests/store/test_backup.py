"""`tradepartner store backup`: a verified, named copy that never overwrites
or deletes. Temp files only; no real store is touched."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.config import Settings
from tradepartner.store import backup, registry, schema
from tradepartner.store.backup import BackupError, backup_store

NOW = datetime(2026, 10, 20, 14, 5, 59, tzinfo=UTC)


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    path = tmp_path / "t.duckdb"
    monkeypatch.setenv("STORE__PATH", str(path))
    conn = duckdb.connect(str(path))
    conn.execute("CREATE TABLE schema_version (version INTEGER)")
    conn.execute("INSERT INTO schema_version VALUES (33), (34)")
    conn.close()
    return path


def test_copy_is_named_for_utc_minute_verified_and_manifested(store: Path) -> None:
    result = backup_store(Settings(), now=NOW, git_commit="abc123")
    copy = Path(f"{store}.bak-20261020-1405")
    assert result.copy_path == copy
    assert copy.read_bytes() == store.read_bytes()
    manifest = json.loads(Path(f"{copy}.manifest.json").read_text())
    assert manifest["git_commit"] == "abc123"
    assert manifest["schema_version"] == 34
    assert manifest["code_dirty"] is None
    assert manifest["store_bytes"] == store.stat().st_size
    assert manifest["wal_bytes"] is None
    assert not list(store.parent.glob("*.partial"))


def test_the_wal_is_copied_too(store: Path) -> None:
    wal = Path(f"{store}.wal")
    wal.write_bytes(b"log bytes")
    result = backup_store(Settings(), now=NOW, git_commit="c")
    assert result.wal_copy_path == Path(f"{result.copy_path}.wal")
    assert result.wal_copy_path.read_bytes() == b"log bytes"
    assert wal.read_bytes() == b"log bytes"


def test_release_open_reads_rows_from_the_backups_wal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crash leaves a real WAL; the release check must see its latest row."""
    path = tmp_path / "release.duckdb"
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(path))
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    conn.close()
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import duckdb, os, sys; "
            "c = duckdb.connect(sys.argv[1]); "
            "c.execute(\"INSERT INTO rates VALUES ('x', DATE '2026-10-20', 1.0, "
            "DATE '2026-10-20', TIMESTAMPTZ '2026-10-20 12:00:00+00', "
            "TIMESTAMPTZ '2026-10-20 13:00:00+00', 'test', 'release')\"); "
            "os._exit(0)",
            str(path),
        ],
        check=True,
    )
    assert Path(f"{path}.wal").is_file()
    result = backup_store(Settings(), now=NOW, git_commit="c")
    assert result.wal_copy_path == Path(f"{result.copy_path}.wal")
    assert result.wal_copy_path.is_file()
    live = duckdb.connect(str(path))
    copied = registry.open_backup(result.copy_path, path)
    try:
        registry.open_data_release(
            live, copied, name="wal-release", backup_path=str(result.copy_path), reason="test"
        )
    finally:
        copied.close()
        live.close()


def test_refuses_when_another_connection_holds_the_store(store: Path) -> None:
    import subprocess
    import sys

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import duckdb,sys,time;c=duckdb.connect(sys.argv[1]);"
            "print('x',flush=True);time.sleep(30)",
            str(store),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        holder.stdout.readline()
        with pytest.raises(BackupError, match="in use"):
            backup_store(Settings(), now=NOW, git_commit="c")
    finally:
        holder.kill()
        holder.wait()
    assert not list(store.parent.glob("*.bak-*"))
    assert not list(store.parent.glob("*.partial"))


def test_never_overwrites_an_existing_copy(store: Path) -> None:
    existing = Path(f"{store}.bak-20261020-1405")
    existing.write_bytes(b"older")
    with pytest.raises(BackupError, match="already exists"):
        backup_store(Settings(), now=NOW, git_commit="c")
    assert existing.read_bytes() == b"older"


def test_a_copy_that_does_not_match_is_refused_and_leaves_nothing(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def bad_copy(src: Path, dst: Path) -> None:
        Path(dst).write_bytes(b"corrupt")

    monkeypatch.setattr(backup.shutil, "copyfile", bad_copy)
    with pytest.raises(BackupError, match="does not match"):
        backup_store(Settings(), now=NOW, git_commit="c")
    assert not list(store.parent.glob("*.bak-*"))
    assert not list(store.parent.glob("*.partial"))
    assert store.exists()


def test_a_missing_store_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "nowhere.duckdb"))
    with pytest.raises(BackupError, match="no store"):
        backup_store(Settings(), now=NOW, git_commit="c")


def test_naive_time_is_rejected(store: Path) -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        backup_store(
            Settings(),
            now=datetime(2026, 10, 20, 14, 5),  # noqa: DTZ001 - the point of the test
            git_commit="c",
        )


def test_the_command_prints_the_copy_and_exits_zero(store: Path) -> None:
    app = cli.make_app(clock=lambda: NOW)
    out = CliRunner().invoke(app, ["store", "backup"])
    assert out.exit_code == 0, out.output
    assert "byte for byte" in out.output
    assert Path(f"{store}.bak-20261020-1405").exists()
    again = CliRunner().invoke(app, ["store", "backup"])
    assert again.exit_code == 1
    assert "already exists" in again.output


def test_the_command_records_this_checkouts_commit_and_dirty_flag(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli.registry, "code_version", lambda: ("deadbeef", True))
    out = CliRunner().invoke(cli.make_app(clock=lambda: NOW), ["store", "backup"])
    assert out.exit_code == 0, out.output
    manifest = json.loads(Path(f"{store}.bak-20261020-1405.manifest.json").read_text())
    assert manifest["git_commit"] == "deadbeef"
    assert manifest["code_dirty"] is True


def test_migration_copy_records_reason_and_code_version(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(backup.registry, "code_version", lambda: ("migration-head", True))
    result = backup_store(Settings(), reason=backup.PRE_MIGRATION, now=NOW)
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["reason"] == "pre_migration"
    assert manifest["git_commit"] == "migration-head"
    assert manifest["code_dirty"] is True
    assert result.git_commit == "migration-head"
    with pytest.raises(BackupError, match="already exists"):
        backup_store(Settings(), reason=backup.PRE_MIGRATION, now=NOW)


def test_retention_removes_only_old_marked_copies_and_their_wals(
    store: Path,
) -> None:
    settings = Settings(store={"path": str(store), "pre_migration_backups_to_keep": 1})
    for minute, reason in [("1401", None), ("1402", "pre_migration"), ("1403", "pre_migration")]:
        copy = Path(f"{store}.bak-20261020-{minute}")
        copy.write_bytes(b"copy")
        Path(f"{copy}.wal").write_bytes(b"wal")
        Path(f"{copy}.manifest.json").write_text(
            json.dumps({"reason": reason, "store_file": store.name})
        )
    partial = Path(f"{store}.bak-20261020-1400.partial")
    partial.write_bytes(b"unfinished")
    backup.sweep_pre_migration_backups(settings, keep=Path(f"{store}.bak-20261020-1403"))
    assert not Path(f"{store}.bak-20261020-1402").exists()
    assert not Path(f"{store}.bak-20261020-1402.wal").exists()
    assert not Path(f"{store}.bak-20261020-1402.manifest.json").exists()
    for minute in ("1401", "1403"):
        assert Path(f"{store}.bak-20261020-{minute}").exists()
        assert Path(f"{store}.bak-20261020-{minute}.wal").exists()
        assert Path(f"{store}.bak-20261020-{minute}.manifest.json").exists()
    assert partial.read_bytes() == b"unfinished"


def test_owner_backup_survives_migration_sweep(store: Path) -> None:
    settings = Settings(store={"path": str(store), "pre_migration_backups_to_keep": 1})
    owner = backup_store(settings, now=NOW, git_commit="owner")
    for minute in (6, 7):
        backup_store(
            settings,
            now=NOW.replace(minute=minute),
            git_commit="migration",
            reason=backup.PRE_MIGRATION,
        )
    backup.sweep_pre_migration_backups(settings, keep=Path(f"{store}.bak-20261020-1407"))
    assert owner.copy_path.is_file()
    assert owner.manifest_path.is_file()


def test_disk_check_counts_store_wal_and_margin_before_copy(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Path(f"{store}.wal").write_bytes(b"wal")
    settings = Settings(store={"path": str(store), "backup_disk_margin_bytes": 7})
    needed = store.stat().st_size + 3 + 7
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _: SimpleNamespace(free=needed - 1))
    with pytest.raises(BackupError, match="free disk"):
        backup_store(settings, now=NOW, git_commit="c", reason=backup.PRE_MIGRATION)
    assert not list(store.parent.glob("*.bak-*"))
    monkeypatch.setattr(backup.shutil, "disk_usage", lambda _: SimpleNamespace(free=needed))
    backup.check_backup_disk_space(settings)


def test_orphan_manifest_does_not_displace_a_real_copy(store: Path) -> None:
    settings = Settings(store={"path": str(store), "pre_migration_backups_to_keep": 2})
    copies = [
        backup_store(settings, now=NOW.replace(minute=minute), reason=backup.PRE_MIGRATION)
        for minute in (5, 6, 7)
    ]
    copies[1].copy_path.unlink()
    backup.sweep_pre_migration_backups(settings, keep=copies[2].copy_path)
    assert copies[0].copy_path.is_file()
    assert copies[1].manifest_path.is_file()
    assert copies[2].copy_path.is_file()


def test_keep_survives_future_stamped_copy(store: Path) -> None:
    settings = Settings(store={"path": str(store), "pre_migration_backups_to_keep": 1})
    future = backup_store(settings, now=NOW.replace(year=2027), reason=backup.PRE_MIGRATION)
    current = backup_store(settings, now=NOW, reason=backup.PRE_MIGRATION)
    backup.sweep_pre_migration_backups(settings, keep=current.copy_path)
    assert current.copy_path.is_file()
    assert current.manifest_path.is_file()
    assert not future.copy_path.exists()
    assert not future.manifest_path.exists()


def test_sweep_skips_partial_wrong_name_bad_json_wrong_store_and_symlink(
    store: Path, tmp_path: Path
) -> None:
    settings = Settings(store={"path": str(store), "pre_migration_backups_to_keep": 1})
    keep = backup_store(settings, now=NOW, reason=backup.PRE_MIGRATION)
    record = {"reason": backup.PRE_MIGRATION, "store_file": store.name}
    partial = Path(f"{store}.bak-20261020-1406.manifest.json.partial")
    wrong_name = Path(f"{store}.bak-not-a-stamp.manifest.json")
    bad_json = Path(f"{store}.bak-20261020-1407.manifest.json")
    wrong_store = Path(f"{store}.bak-20261020-1408.manifest.json")
    symlink_copy = Path(f"{store}.bak-20261020-1409")
    symlink_manifest = Path(f"{symlink_copy}.manifest.json")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-copy"
    outside.write_bytes(b"outside")
    partial.write_text(json.dumps(record))
    wrong_name.write_text(json.dumps(record))
    bad_json.write_text("{bad")
    Path(str(bad_json).removesuffix(".manifest.json")).write_bytes(b"bad-json-copy")
    wrong_store.write_text(json.dumps({**record, "store_file": "other.duckdb"}))
    Path(str(wrong_store).removesuffix(".manifest.json")).write_bytes(b"other-store-copy")
    symlink_copy.symlink_to(outside)
    symlink_manifest.write_text(json.dumps(record))
    backup.sweep_pre_migration_backups(settings, keep=keep.copy_path)
    for path in (partial, wrong_name, bad_json, wrong_store, symlink_copy, symlink_manifest):
        assert path.exists()
    assert outside.read_bytes() == b"outside"
    outside.unlink()
