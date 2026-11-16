"""`store/fence.py` and the `security_fence` table (#3501, plan task T401; data-foundation
spec amendment 2026-11-16, items 1, 2 and 4, acceptance (b), (e) and (f)).

Fixture stores only; nothing here touches the owner's store.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tradepartner.store import fence, schema
from tradepartner.store.db import insert_row

T1 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
T2 = T1 + timedelta(days=1)
T3 = T1 + timedelta(days=2)
D1 = date(2020, 1, 2)
D2 = date(2020, 1, 10)


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(":memory:")
    schema.init_schema(c)
    return c


def _put(
    conn: duckdb.DuckDBPyConnection,
    sid: str,
    at: datetime,
    *,
    action: str = "fence",
    start: date = D1,
    end: date = D2,
    rule: str = "manual",
    batch: int = 1,
    ingested: datetime | None = None,
) -> None:
    insert_row(
        conn,
        "security_fence",
        {
            "security_id": sid,
            "from_session": start,
            "to_session": end,
            "rule": rule,
            "reason": "r",
            "issue": 3475,
            "action": action,
            "batch": batch,
            "known_at": at,
            "ingested_at": ingested or at,
        },
    )


def test_a_row_known_after_the_read_time_is_invisible(conn: duckdb.DuckDBPyConnection) -> None:
    """No look-ahead, and the truncation test: rows written after a read time never
    appear in that read."""
    _put(conn, "A", T2)
    assert fence.fence_as_of(conn, T1) == []
    assert fence.fence_vintage(conn, T1) is None
    assert [r.security_id for r in fence.fence_as_of(conn, T2)] == ["A"]
    assert [r.security_id for r in fence.fence_as_of(conn, T3)] == ["A"]


def test_a_row_ingested_after_the_read_time_is_invisible(conn: duckdb.DuckDBPyConnection) -> None:
    _put(conn, "A", T1, ingested=T3)
    assert fence.fence_as_of(conn, T2) == []
    assert fence.fence_vintage(conn, T2) is None
    assert fence.fence_vintage(conn, T3) == T1


def test_a_lift_removes_the_fence_only_from_its_own_known_at(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    _put(conn, "A", T1, batch=1)
    _put(conn, "A", T3, action="lift", batch=2)
    assert [r.security_id for r in fence.fence_as_of(conn, T2)] == ["A"]
    assert fence.fence_as_of(conn, T3) == []
    # Append-only: the fence row is still there.
    (count,) = conn.execute("SELECT count(*) FROM security_fence").fetchone()  # type: ignore[misc]
    assert count == 2


def test_a_refence_after_a_lift_is_active_again(conn: duckdb.DuckDBPyConnection) -> None:
    _put(conn, "A", T1, batch=1)
    _put(conn, "A", T2, action="lift", batch=2)
    _put(conn, "A", T3, batch=3, end=date(2020, 1, 20))
    (row,) = fence.fence_as_of(conn, T3 + timedelta(days=1))
    assert row.to_session == date(2020, 1, 20)


def test_the_key_is_id_from_session_and_rule(conn: duckdb.DuckDBPyConnection) -> None:
    """A lift names the same key; a fence of another rule or from-session is another key."""
    _put(conn, "A", T1, batch=1)
    _put(conn, "A", T1, batch=1, rule="other")
    _put(conn, "A", T1, batch=1, start=date(2020, 2, 3), end=date(2020, 2, 4))
    _put(conn, "A", T2, action="lift", batch=2)
    rows = fence.fence_as_of(conn, T3)
    assert sorted((r.rule, r.from_session) for r in rows) == [
        ("manual", date(2020, 2, 3)),
        ("other", D1),
    ]


def test_the_vintage_counts_every_row_active_or_not(conn: duckdb.DuckDBPyConnection) -> None:
    """Acceptance (f): A fenced at t1, B at t2, A lifted at t3. The active rows at t3
    are {B}, but a reproduction as of t2 gives {A, B}; the vintage at t3 is t3, so a
    run that recorded it reads the fence as of t3, and one that recorded t2 reads
    {A, B}."""
    _put(conn, "A", T1, batch=1)
    _put(conn, "B", T2, batch=2)
    _put(conn, "A", T3, action="lift", batch=3)
    assert {r.security_id for r in fence.fence_as_of(conn, T3)} == {"B"}
    assert fence.fence_vintage(conn, T3) == T3
    assert fence.fence_vintage(conn, T2) == T2
    assert {r.security_id for r in fence.fence_as_of(conn, T2)} == {"A", "B"}
    assert fence.fence_vintage(conn, T3 + timedelta(days=9)) == T3


def test_the_vintage_is_none_only_with_no_row_at_all(conn: duckdb.DuckDBPyConnection) -> None:
    assert fence.fence_vintage(conn, T3) is None
    _put(conn, "A", T1, batch=1)
    _put(conn, "A", T2, action="lift", batch=2)
    assert fence.fence_as_of(conn, T3) == []
    assert fence.fence_vintage(conn, T3) == T2  # no active row, still not None


def test_a_naive_read_time_is_refused(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError):
        fence.fence_as_of(conn, datetime(2026, 11, 1, 12, 0))  # noqa: DTZ001


def test_fenced_ids_hold_the_closed_window(conn: duckdb.DuckDBPyConnection) -> None:
    _put(conn, "A", T1)
    rows = fence.fence_as_of(conn, T2)
    assert fence.fenced_ids(rows, D1) == {"A"}
    assert fence.fenced_ids(rows, D2) == {"A"}
    assert fence.fenced_ids(rows, D1 - timedelta(days=1)) == frozenset()
    assert fence.fenced_ids(rows, D2 + timedelta(days=1)) == frozenset()


def _row(sid: str, rule: str = "r1") -> fence.FenceRow:
    return fence.FenceRow(sid, D1, D2, rule, "r", 1, "fence", 1, T1, T1)


def test_split_findings_accepts_only_the_same_rule_id_and_whole_span() -> None:
    """Acceptance (c)'s matching half: inside the window accepted; one session after
    `to_session`, a span running past it, another rule, another id each fail."""
    rows = [_row("A")]
    inside = fence.Finding("r1", "A", D1, D1)
    whole = fence.Finding("r1", "A", D1, D2)
    after = fence.Finding("r1", "A", D2 + timedelta(days=1), D2 + timedelta(days=1))
    past = fence.Finding("r1", "A", D2 - timedelta(days=1), D2 + timedelta(days=1))
    before = fence.Finding("r1", "A", D1 - timedelta(days=1), D1)
    rule = fence.Finding("r2", "A", D1, D1)
    other = fence.Finding("r1", "B", D1, D1)
    accepted, failing = fence.split_findings(
        rows, [inside, whole, after, past, before, rule, other]
    )
    assert accepted == [inside, whole]
    assert failing == [after, past, before, rule, other]


def test_split_findings_ignores_a_lift_row() -> None:
    lifted = fence.FenceRow("A", D1, D2, "r1", "r", 1, "lift", 2, T2, T2)
    finding = fence.Finding("r1", "A", D1, D1)
    assert fence.split_findings([lifted], [finding]) == ([], [finding])


def test_fence_share_counts_the_numerator_on_the_session_and_the_pairs() -> None:
    rows = [_row("A"), _row("B"), _row("Z")]  # Z is not in the universe
    bars = {
        "A": {D1, D2, date(2020, 2, 3)},
        "B": {D1},
        "C": {D1, D2},
    }
    share = fence.fence_share(
        rows,
        D1,
        universe_ids={"A", "B", "C", "D"},
        delisted_ids={"B", "C"},
        bar_sessions=bars,
    )
    assert (share.fenced_securities, share.universe_securities) == (2, 4)
    assert (share.fenced_pairs, share.universe_pairs) == (3, 6)
    assert (share.fenced_delisted, share.universe_delisted) == (1, 2)
    assert share.security_share == 0.5
    assert share.pair_share == 0.5
    assert share.delisted_universe_share == 0.5
    assert "2 of 4 securities" in share.line() and "3 of 6" in share.line()


def test_a_name_fenced_only_in_the_past_counts_in_pairs_not_in_the_numerator() -> None:
    share = fence.fence_share(
        [_row("A")],
        D2 + timedelta(days=30),
        universe_ids={"A"},
        delisted_ids=set(),
        bar_sessions={"A": {D1, D2}},
    )
    assert share.fenced_securities == 0 and share.fenced_pairs == 2


def test_the_share_of_an_empty_universe_is_zero() -> None:
    share = fence.fence_share([], D1, universe_ids=set(), delisted_ids=set(), bar_sessions={})
    assert share.security_share == 0.0 and share.pair_share == 0.0


def test_no_rule_is_accepted_by_fence_yet() -> None:
    assert not fence.ACCEPTING_RULES


# --- acceptance (e): the older schema -------------------------------------------------


def test_a_version_49_store_opens_read_only_and_reads_an_empty_fence(tmp_path: Path) -> None:
    path = tmp_path / "old.duckdb"
    with duckdb.connect(str(path)) as c:
        schema.init_schema(c)
        c.execute("DROP TABLE security_fence")
        c.execute("DELETE FROM schema_version")
        c.execute("INSERT INTO schema_version VALUES (49, ?)", [T1])
    with open_read_only_path(path) as ro:
        schema.init_schema(ro)  # accepted read-only: no migration, no error
        assert fence.fence_as_of(ro, T3) == []
        assert fence.fence_vintage(ro, T3) is None


def open_read_only_path(path: Path) -> duckdb.DuckDBPyConnection:
    """A read-only connection configured as the store's readers are."""
    from tradepartner.store.db import configure_connection

    c = duckdb.connect(str(path), read_only=True)
    configure_connection(c)
    return c


def test_a_writable_version_49_store_migrates_and_keeps_every_row(tmp_path: Path) -> None:
    path = tmp_path / "old.duckdb"
    with duckdb.connect(str(path)) as c:
        schema.init_schema(c)
        c.execute("DROP TABLE security_fence")
        c.execute("DELETE FROM schema_version")
        c.execute("INSERT INTO schema_version VALUES (49, ?)", [T1])
        c.execute(
            "INSERT INTO owner_decisions VALUES (1, ?, 'gap_signoff', NULL, NULL, '{}', 'why')",
            [T1],
        )
        before = c.execute("SELECT * FROM owner_decisions").fetchall()
    with duckdb.connect(str(path)) as c:
        schema.init_schema(c)
        assert c.execute("SELECT * FROM owner_decisions").fetchall() == before
        c.execute(
            "INSERT INTO owner_decisions VALUES (2, ?, 'fence_write', NULL, NULL, '{}', 'r')", [T1]
        )
        assert fence.fence_as_of(c, T3) == []


def test_the_fence_table_rejects_a_bad_row(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(duckdb.ConstraintException):
        _put(conn, "A", T1, action="delete")
    with pytest.raises(duckdb.ConstraintException):
        _put(conn, "A", T1, start=D2, end=D1)


def test_the_version_is_50_and_the_fence_is_not_a_fact_table() -> None:
    assert schema.CURRENT_SCHEMA_VERSION == 50
    assert "security_fence" not in schema.TABLE_NAMES
    assert "security_fence" in schema.MASTER_CHECK_TABLE_NAMES
