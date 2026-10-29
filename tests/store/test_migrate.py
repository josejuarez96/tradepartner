"""The explicit, backed-up store migration entry point."""

import ast
from contextlib import AbstractContextManager
from pathlib import Path

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store import backup, migrate, schema
from tradepartner.store.db import open_for_write, open_read_only

# Total calls, read-only calls, and disposition of every writable call.
# A new call must be classified here; PR 2 changes selected fail-closed
# entry points to migrate-adopting after its CLI gates are wired.
INIT_SCHEMA_CALLERS = {
    "backfill.py": (2, 0, "fail-closed"),
    "backtest/lab.py": (1, 0, "fail-closed"),
    "backtest/run.py": (1, 0, "fail-closed"),
    "cli.py": (21, 6, "fail-closed"),
    "dashboard/backtest_page.py": (1, 1, "read-only"),
    "dashboard/research_page.py": (1, 1, "read-only"),
    "dashboard/trials_page.py": (1, 1, "read-only"),
    "execution/window.py": (1, 0, "fail-closed"),
    "ingest.py": (4, 0, "fail-closed"),
    "retract.py": (1, 0, "fail-closed"),
    "store/migrate.py": (1, 0, "migrate-adopting"),
}


def test_every_init_schema_caller_is_classified() -> None:
    root = Path(__file__).resolve().parents[2] / "src/tradepartner"
    observed: dict[str, int] = {}
    for path in root.rglob("*.py"):
        if path.name == "schema.py":
            continue
        tree = ast.parse(path.read_text())
        calls = sum(
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == "init_schema")
                or (isinstance(node.func, ast.Attribute) and node.func.attr == "init_schema")
            )
            for node in ast.walk(tree)
        )
        if calls:
            observed[str(path.relative_to(root))] = calls
    assert observed == {path: total for path, (total, _, _) in INIT_SCHEMA_CALLERS.items()}
    assert all(
        0 <= read_only <= total and (policy == "read-only") == (read_only == total)
        for total, read_only, policy in INIT_SCHEMA_CALLERS.values()
    )


@pytest.fixture
def store_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    return Settings(store={"path": str(tmp_path / "store.duckdb")})


def _seed_version(path: str, version: int) -> None:
    conn = duckdb.connect(path)
    schema.init_schema(conn)
    conn.execute("UPDATE schema_version SET version = ?", [version])
    conn.close()


def test_missing_store_initialises_without_backup(
    store_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schema, "_ALLOW_MIGRATION", False)
    monkeypatch.setattr(backup, "check_backup_disk_space", lambda _: pytest.fail("disk check"))
    monkeypatch.setattr(backup, "backup_store", lambda *_args, **_kwargs: pytest.fail("backup"))
    migrate.migrate(store_settings)
    conn = duckdb.connect(store_settings.store.path, read_only=True)
    assert schema.stored_version(conn) == schema.CURRENT_SCHEMA_VERSION
    conn.close()


def test_unversioned_store_initialises_without_backup(
    store_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = duckdb.connect(store_settings.store.path)
    conn.execute("CREATE TABLE unrelated (id INTEGER)")
    conn.close()
    monkeypatch.setattr(schema, "_ALLOW_MIGRATION", False)
    monkeypatch.setattr(backup, "check_backup_disk_space", lambda _: pytest.fail("disk check"))
    monkeypatch.setattr(backup, "backup_store", lambda *_args, **_kwargs: pytest.fail("backup"))
    migrate.migrate(store_settings)
    conn = duckdb.connect(store_settings.store.path, read_only=True)
    assert schema.stored_version(conn) == schema.CURRENT_SCHEMA_VERSION
    conn.close()


def test_store_created_after_missing_file_check_cannot_migrate_without_backup(
    store_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(schema, "_ALLOW_MIGRATION", False)
    monkeypatch.setattr(backup, "backup_store", lambda *_args, **_kwargs: pytest.fail("backup"))

    def another_writer_creates_store(
        settings: Settings,
    ) -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        _seed_version(settings.store.path, schema.CURRENT_SCHEMA_VERSION - 1)
        return open_for_write(settings)

    monkeypatch.setattr(migrate, "open_for_write", another_writer_creates_store)
    with pytest.raises(schema.SchemaMigrationRequiredError, match="backup first"):
        migrate.migrate(store_settings)
    conn = duckdb.connect(store_settings.store.path, read_only=True)
    assert schema.stored_version(conn) == schema.CURRENT_SCHEMA_VERSION - 1
    conn.close()


def test_equal_version_is_no_op(store_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_version(store_settings.store.path, schema.CURRENT_SCHEMA_VERSION)
    monkeypatch.setattr(backup, "backup_store", lambda *_args, **_kwargs: pytest.fail("backup"))
    migrate.migrate(store_settings)


def test_migration_probe_uses_writer_lock_wait(
    store_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_version(store_settings.store.path, schema.CURRENT_SCHEMA_VERSION)
    retries: list[bool] = []

    def read(
        settings: Settings, *, retry: bool = False
    ) -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        retries.append(retry)
        return open_read_only(settings, retry=retry)

    monkeypatch.setattr(migrate, "open_read_only", read)
    migrate.migrate(store_settings)
    assert retries == [True]


def test_newer_version_refuses(store_settings: Settings) -> None:
    _seed_version(store_settings.store.path, schema.CURRENT_SCHEMA_VERSION)
    conn = duckdb.connect(store_settings.store.path)
    conn.execute(
        "INSERT INTO schema_version VALUES (?, current_timestamp)",
        [schema.CURRENT_SCHEMA_VERSION + 1],
    )
    conn.close()
    with pytest.raises(schema.SchemaVersionError):
        migrate.migrate(store_settings)


def test_older_store_is_copied_then_migrated(
    store_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_version(store_settings.store.path, schema.CURRENT_SCHEMA_VERSION - 1)
    monkeypatch.setattr(schema, "_ALLOW_MIGRATION", False)
    migrate.migrate(store_settings)
    copies = list(Path(store_settings.store.path).parent.glob("*.bak-*.manifest.json"))
    assert len(copies) == 1
    assert '"reason": "pre_migration"' in copies[0].read_text()
    conn = duckdb.connect(store_settings.store.path, read_only=True)
    assert schema.stored_version(conn) == schema.CURRENT_SCHEMA_VERSION
    conn.close()


def test_writer_that_migrated_after_backup_makes_this_a_no_op(
    store_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_version(store_settings.store.path, schema.CURRENT_SCHEMA_VERSION - 1)
    original_backup = backup.backup_store

    def backup_then_other_writer(settings: Settings, *, reason: str) -> backup.BackupResult:
        assert reason == backup.PRE_MIGRATION
        result = original_backup(settings, reason=backup.PRE_MIGRATION)
        conn = duckdb.connect(settings.store.path)
        conn.execute("UPDATE schema_version SET version = ?", [schema.CURRENT_SCHEMA_VERSION])
        conn.close()
        return result

    monkeypatch.setattr(backup, "backup_store", backup_then_other_writer)
    monkeypatch.setattr(schema, "init_schema", lambda _: pytest.fail("redundant migration"))
    migrate.migrate(store_settings)


@pytest.mark.parametrize("failure", ["disk", "backup"])
def test_failed_preflight_preserves_store(
    store_settings: Settings, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    _seed_version(store_settings.store.path, schema.CURRENT_SCHEMA_VERSION - 1)
    monkeypatch.setattr(schema, "_ALLOW_MIGRATION", False)

    def refuse(_: Settings) -> None:
        raise backup.BackupError(failure)

    if failure == "disk":
        monkeypatch.setattr(backup, "check_backup_disk_space", refuse)
    else:
        monkeypatch.setattr(
            backup, "backup_store", lambda *_args, **_kwargs: refuse(store_settings)
        )
    with pytest.raises(backup.BackupError):
        migrate.migrate(store_settings)
    conn = duckdb.connect(store_settings.store.path, read_only=True)
    assert schema.stored_version(conn) == schema.CURRENT_SCHEMA_VERSION - 1
    conn.close()
