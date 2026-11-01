"""Point-in-time delisting reason revisions (T326)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest
from lookahead.harness import TruncatedStore

from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.db import insert_row
from tradepartner.store.delisting_reasons import delisting_reasons_as_of
from tradepartner.store.delistings import delistings_with_reasons_as_of, listing_ends_as_of
from tradepartner.universe import universe_as_of

FILED = datetime(2020, 1, 2, 15, tzinfo=UTC)
EVIDENCE = datetime(2020, 2, 3, 15, tzinfo=UTC)
INGESTED = datetime(2020, 3, 1, tzinfo=UTC)


@pytest.fixture
def synthetic() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    insert_row(
        conn,
        "delistings",
        {
            "security_id": "S",
            "form": "25",
            "class_title": "Common Stock",
            "exchange": "NASDAQ",
            "filed_at": FILED,
            "effective_on": FILED.date() + timedelta(days=10),
            "known_at": FILED,
            "ingested_at": INGESTED,
            "source": "edgar",
            "provenance": "filing",
        },
    )
    yield conn
    conn.close()


def _reason(known_at: datetime = EVIDENCE, reason: str = "bankruptcy") -> dict[str, object]:
    return {
        "security_id": "S",
        "delisting_form": "25",
        "delisting_class_title": "Common Stock",
        "delisting_exchange": "NASDAQ",
        "delisting_filed_at": FILED,
        "reason": reason,
        "reason_evidence_form": "8-K",
        "reason_evidence_accession": "0001-20-000001",
        "reason_source": "eightk_item",
        "reason_known_at": known_at,
        "known_at": known_at,
        "ingested_at": INGESTED,
        "source": "edgar",
        "provenance": "filing",
    }


def test_late_evidence_and_revisions_are_as_of(synthetic: duckdb.DuckDBPyConnection) -> None:
    insert_row(synthetic, "delisting_reasons", _reason())
    assert delisting_reasons_as_of(synthetic, EVIDENCE - timedelta(microseconds=1)).is_empty()
    assert delisting_reasons_as_of(synthetic, EVIDENCE)["reason"].to_list() == ["bankruptcy"]
    insert_row(synthetic, "delisting_reasons", _reason(EVIDENCE + timedelta(days=1), "unknown"))
    assert delisting_reasons_as_of(synthetic, EVIDENCE)["reason"].to_list() == ["bankruptcy"]
    assert delisting_reasons_as_of(synthetic, INGESTED)["reason"].to_list() == ["unknown"]


def test_reason_cannot_reveal_future_form_25(synthetic: duckdb.DuckDBPyConnection) -> None:
    early_evidence = FILED - timedelta(days=20)
    insert_row(synthetic, "delisting_reasons", _reason(early_evidence))
    assert delisting_reasons_as_of(synthetic, FILED - timedelta(microseconds=1)).is_empty()
    assert delisting_reasons_as_of(synthetic, FILED)["reason"].to_list() == ["bankruptcy"]
    assert delistings_with_reasons_as_of(synthetic, FILED - timedelta(microseconds=1)).is_empty()
    assert delistings_with_reasons_as_of(synthetic, FILED)["reason"].to_list() == ["bankruptcy"]


def test_truncation_invariance(synthetic: duckdb.DuckDBPyConnection) -> None:
    insert_row(synthetic, "delisting_reasons", _reason())
    truncated = TruncatedStore(synthetic, tables=("delistings", "delisting_reasons"))
    try:
        for t in (EVIDENCE - timedelta(microseconds=1), EVIDENCE, INGESTED):
            assert delisting_reasons_as_of(synthetic, t).equals(
                delisting_reasons_as_of(truncated.at(t), t)
            )
    finally:
        truncated.close()


def test_reason_constraints(synthetic: duckdb.DuckDBPyConnection) -> None:
    for field, value in (("reason", "guessed"), ("reason_source", "guessed")):
        with pytest.raises(duckdb.ConstraintException):
            insert_row(synthetic, "delisting_reasons", {**_reason(), field: value})
    with pytest.raises(duckdb.ConstraintException):
        insert_row(synthetic, "delisting_reasons", {**_reason(), "reason_known_at": FILED})
    with pytest.raises(duckdb.ConstraintException):
        insert_row(synthetic, "delisting_reasons", {**_reason(), "reason_evidence_accession": None})
    insert_row(
        synthetic,
        "delisting_reasons",
        {
            **_reason(),
            "reason": "unknown",
            "reason_source": "unknown",
            "reason_evidence_accession": None,
        },
    )


def test_universe_and_listing_ends_ignore_reasons(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    settings = Settings(_env_file=None)
    t = datetime(2021, 1, 4, 21, tzinfo=UTC)
    before_universe = universe_as_of(fixture_store, t, settings)
    before_ends = listing_ends_as_of(fixture_store, t, settings)
    episode = fixture_store.execute(
        "SELECT security_id, form, class_title, exchange, filed_at FROM delistings LIMIT 1"
    ).fetchone()
    assert episode is not None
    row = _reason()
    for key, value in zip(
        (
            "security_id",
            "delisting_form",
            "delisting_class_title",
            "delisting_exchange",
            "delisting_filed_at",
        ),
        episode,
        strict=True,
    ):
        row[key] = value
    insert_row(fixture_store, "delisting_reasons", row)
    after_universe = universe_as_of(fixture_store, t, settings)
    assert after_universe.members.equals(before_universe.members)
    assert after_universe.exclusions.equals(before_universe.exclusions)
    assert after_universe.shares_fallbacks.equals(before_universe.shares_fallbacks)
    assert after_universe.rules_enabled == before_universe.rules_enabled
    assert listing_ends_as_of(fixture_store, t, settings).equals(before_ends)


def test_v42_store_adds_only_reason_table(tmp_path: Path) -> None:
    path = tmp_path / "v42.duckdb"
    with duckdb.connect(str(path)) as conn:
        schema.init_schema(conn)
        conn.execute(
            "INSERT INTO delistings (security_id, form, class_title, exchange, filed_at, "
            "effective_on, known_at, ingested_at, source, provenance) "
            "VALUES ('S', '25', 'Common Stock', 'NASDAQ', ?, DATE '2020-01-12', "
            "?, ?, 'edgar', 'filing')",
            [FILED, FILED, INGESTED],
        )
        original = conn.execute("SELECT * FROM delistings").fetchall()
        conn.execute("DROP TABLE delisting_reasons")
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version VALUES (?, ?)",
            [schema._PRE_DELISTING_REASONS_VERSION, INGESTED],
        )
        schema.init_schema(conn)
        assert schema.stored_version(conn) == schema.CURRENT_SCHEMA_VERSION
        assert "delisting_reasons" in {r[0] for r in conn.execute("SHOW TABLES").fetchall()}
        assert conn.execute("SELECT * FROM delistings").fetchall() == original


def test_version_43_builds_on_the_snapshot_sightings_version() -> None:
    """Version 42 is #2665's `snapshot_sightings`; this PR's migration is 42 -> 43."""
    assert "snapshot_sightings" in schema.TABLE_NAMES
    assert schema._PRE_DELISTING_REASONS_VERSION == schema._PRE_SNAPSHOT_SIGHTINGS_VERSION + 1
    assert schema._PRE_DELISTING_REASONS_VERSION + 1 == schema.CURRENT_SCHEMA_VERSION
