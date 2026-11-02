"""Point-in-time spin-off basis allocation regressions (T254)."""

from datetime import UTC, date, datetime, timedelta

import duckdb

from tradepartner.store import asof, schema


def test_late_allocation_and_revision_are_point_in_time() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    stamp = datetime(2020, 1, 8, 17, tzinfo=UTC)
    later = stamp + timedelta(days=4)
    for fraction, at in ((0.25, stamp), (0.35, later)):
        conn.execute(
            "INSERT INTO spinoff_allocations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ["P", "C", date(2020, 1, 2), fraction, at, at, "owner", "owner"],
        )
    assert asof.spinoff_allocations_as_of(conn, stamp - timedelta(seconds=1)).is_empty()
    assert asof.spinoff_allocations_as_of(conn, stamp)["basis_allocation"].to_list() == [0.25]
    assert asof.spinoff_allocations_as_of(conn, later)["basis_allocation"].to_list() == [0.35]


def test_fixture_form_8937_arrives_after_spinoff(fixture_store: duckdb.DuckDBPyConnection) -> None:
    from tradepartner.calendar import session_close

    at_spinoff = session_close(date(2018, 12, 13))
    row = fixture_store.execute(
        "SELECT known_at FROM spinoff_allocations WHERE parent_security_id = 'SEC_TICKCHANGE'"
    ).fetchone()
    assert row is not None
    stamp = row[0]
    assert stamp > at_spinoff
    assert asof.spinoff_allocations_as_of(fixture_store, at_spinoff).is_empty()
    assert asof.spinoff_allocations_as_of(fixture_store, stamp)["basis_allocation"].to_list() == [
        0.25
    ]


def test_truncation_invariance_at_allocation_stamp(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    from lookahead.harness import TruncatedStore

    stamp = fixture_store.execute("SELECT known_at FROM spinoff_allocations").fetchone()[0]
    truncated = TruncatedStore(fixture_store)
    try:
        for t in (stamp - timedelta(microseconds=1), stamp):
            assert asof.spinoff_allocations_as_of(fixture_store, t).equals(
                asof.spinoff_allocations_as_of(truncated.at(t), t)
            )
    finally:
        truncated.close()
