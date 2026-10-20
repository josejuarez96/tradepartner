"""`tradepartner store backup`: a verified, named copy that never overwrites
or deletes. Temp files only; no real store is touched."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.config import Settings
from tradepartner.store import backup
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
    assert result.wal_copy_path == Path(f"{store}.wal.bak-20261020-1405")
    assert result.wal_copy_path.read_bytes() == b"log bytes"
    assert wal.read_bytes() == b"log bytes"


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
