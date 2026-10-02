"""Schema version 8 (#472): `resume_invocations.accept_rejections` and the
`resume_acceptances` table, for `paper resume --accept-rejections`.

The migration from version 7 rebuilds `resume_invocations` with every row kept in
insertion order and given `accept_rejections = FALSE` (no earlier resume could be
given the flag), and creates `resume_acceptances`; the result has a fresh store's
shape. A read-only connection still opens a version-7 store.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.store import journal as store_journal
from tradepartner.store import schema

_NOW = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)

#: `resume_invocations` as versions 5 to 7 created it.
_V7_RESUME_INVOCATIONS_DDL = schema._CREATE_RESUME_INVOCATIONS.replace(
    "    accept_rejections BOOLEAN NOT NULL,\n", ""
)
_REBUILT = {"resume_invocations", "resume_acceptances"}


@pytest.fixture
def journal() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _version_7_store(path: Path, resume_ids: tuple[int, ...] = ()) -> Path:
    """A store shaped as version 7 left it, holding one `resume_invocations` row
    per entry of `resume_ids`, in that insertion order."""
    with duckdb.connect(str(path)) as conn:
        schema.init_schema(conn)
        conn.execute("DROP TABLE resume_acceptances")
        conn.execute("DROP TABLE resume_invocations")
        conn.execute(_V7_RESUME_INVOCATIONS_DDL)
        conn.execute("UPDATE schema_version SET version = 7")
        for index, resume_id in enumerate(resume_ids):
            at = _NOW + timedelta(minutes=index)
            conn.execute(
                'INSERT INTO resume_invocations (resume_id, "at", reason, '
                "accept_broker_fills, known_at, ingested_at) VALUES (?, ?, ?, ?, ?, ?)",
                [resume_id, at, f"reason {resume_id}", index % 2 == 0, at, at + timedelta(1)],
            )
    return path


def _versions(conn: duckdb.DuckDBPyConnection) -> list[int]:
    return [
        row[0] for row in conn.execute("SELECT version FROM schema_version ORDER BY 1").fetchall()
    ]


def _shape(conn: duckdb.DuckDBPyConnection, table: str) -> tuple[Any, ...]:
    """Columns and constraints of `table`, for comparing two stores."""
    columns = conn.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_name = ? ORDER BY ordinal_position",
        [table],
    ).fetchall()
    constraints = conn.execute(
        "SELECT constraint_type, constraint_text FROM duckdb_constraints() "
        "WHERE database_name = current_database() AND table_name = ? ORDER BY ALL",
        [table],
    ).fetchall()
    return (columns, constraints)


def test_a_fresh_store_has_the_flag_column_and_the_acceptance_table(
    journal: duckdb.DuckDBPyConnection,
) -> None:
    columns, _ = _shape(journal, "resume_invocations")
    assert ("accept_rejections", "BOOLEAN", "NO") in columns
    columns, _ = _shape(journal, "resume_acceptances")
    assert [c[0] for c in columns] == ["resume_id", "accepted_json", "known_at", "ingested_at"]
    assert all(c[2] == "NO" for c in columns)


def test_the_flag_column_refuses_null(journal: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(duckdb.ConstraintException):
        journal.execute(
            'INSERT INTO resume_invocations (resume_id, "at", reason, accept_broker_fills, '
            "accept_rejections, known_at, ingested_at) VALUES (1, ?, 'r', FALSE, NULL, ?, ?)",
            [_NOW, _NOW, _NOW],
        )


def test_one_acceptance_row_per_resume(journal: duckdb.DuckDBPyConnection) -> None:
    insert = (
        "INSERT INTO resume_acceptances (resume_id, accepted_json, known_at, ingested_at) "
        "VALUES (1, '[]', ?, ?)"
    )
    journal.execute(insert, [_NOW, _NOW])
    with pytest.raises(duckdb.ConstraintException):
        journal.execute(insert, [_NOW, _NOW])


def test_write_open_of_a_version_7_store_keeps_every_resume_row_without_the_flag(
    tmp_path: Path, journal: duckdb.DuckDBPyConnection
) -> None:
    ids = (3, 1, 2)
    path = _version_7_store(tmp_path / "v7.duckdb", ids)
    reader = (
        'SELECT resume_id, "at", reason, accept_broker_fills, known_at, ingested_at '
        "FROM resume_invocations"
    )
    with duckdb.connect(str(path)) as conn:
        before = conn.execute(f"{reader} ORDER BY rowid").fetchall()
        others_before = {
            table: _shape(conn, table)
            for table in schema.JOURNAL_TABLE_NAMES
            if table not in _REBUILT
        }
        schema.init_schema(conn)
        after = conn.execute(f"{reader} ORDER BY rowid").fetchall()
        flags = conn.execute("SELECT accept_rejections FROM resume_invocations").fetchall()
        acceptances = conn.execute("SELECT COUNT(*) FROM resume_acceptances").fetchone()
        shapes = {table: _shape(conn, table) for table in schema.JOURNAL_TABLE_NAMES}
        versions = _versions(conn)
    assert after == before
    assert [row[0] for row in after] == list(ids)
    assert flags == [(False,)] * len(ids)
    assert acceptances == (0,)
    assert versions == [7, 8]
    assert shapes == {table: _shape(journal, table) for table in schema.JOURNAL_TABLE_NAMES}
    assert {t: s for t, s in shapes.items() if t not in _REBUILT} == others_before


def test_a_migrated_store_reopens_without_another_version_row(tmp_path: Path) -> None:
    path = _version_7_store(tmp_path / "v7.duckdb", (1,))
    for _ in range(2):
        with duckdb.connect(str(path)) as conn:
            schema.init_schema(conn)
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [7, 8]


def test_read_only_open_of_a_version_7_store_passes(tmp_path: Path) -> None:
    """Every read but the resume tables works on a store no write has touched since
    this version was pulled."""
    path = _version_7_store(tmp_path / "v7.duckdb", (1,))
    with duckdb.connect(str(path), read_only=True) as conn:
        schema.init_schema(conn)
        assert _versions(conn) == [7]
        store_journal.require_journal(conn)
        assert store_journal.open_window(conn) is None
        assert store_journal.kill_switch_events_for(conn, 1) == []
        with pytest.raises(duckdb.Error):
            store_journal.resume_invocations(conn)  # no accept_rejections column yet
        with pytest.raises(duckdb.Error):
            store_journal.resume_acceptances(conn)  # no table yet
