"""Explicit store migration after a verified, marked backup."""

from __future__ import annotations

from pathlib import Path

from tradepartner.config import Settings
from tradepartner.store import backup, schema
from tradepartner.store.db import open_for_write, open_read_only


def migrate(settings: Settings) -> None:
    """Bring an older store to the code version, preserving a verified copy first.

    An absent or unversioned store is first-time initialization. Read-only
    inspection releases its connection before taking the backup or write lock.
    """
    store = Path(settings.store.path)
    if not store.is_file():
        version = None
    else:
        with open_read_only(settings, retry=True) as conn:
            version = schema.stored_version(conn)

    if version == schema.CURRENT_SCHEMA_VERSION:
        return
    if version is not None and version > schema.CURRENT_SCHEMA_VERSION:
        raise schema.SchemaVersionError(
            f"store schema_version is {version}, this code expects {schema.CURRENT_SCHEMA_VERSION}"
        )
    if version is not None:
        backup.check_backup_disk_space(settings)
        result = backup.backup_store(settings, reason=backup.PRE_MIGRATION)
        backup.sweep_pre_migration_backups(settings, keep=result.copy_path)

    with open_for_write(settings) as conn:
        current = schema.stored_version(conn)
        if current == schema.CURRENT_SCHEMA_VERSION:
            return
        if current is not None and current > schema.CURRENT_SCHEMA_VERSION:
            raise schema.SchemaVersionError(
                f"store schema_version is {current}, this code expects "
                f"{schema.CURRENT_SCHEMA_VERSION}"
            )
        if version is None and current is not None:
            raise schema.SchemaMigrationRequiredError(
                f"store schema_version appeared at {current} after the initial read; "
                "run `tradepartner ingest` again so it can take a backup first"
            )
        previous = schema._ALLOW_MIGRATION
        schema._ALLOW_MIGRATION = True
        try:
            schema.init_schema(conn)
        finally:
            schema._ALLOW_MIGRATION = previous
