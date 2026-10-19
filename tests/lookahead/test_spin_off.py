"""The spin-off factor in the adjusted total-return read (backtest spec, amendment
2026-10-19 #1894, items 2 to 4; plan T211b).

Hand fixture: parent `P` closes 100.0 on 2024-01-02..04 (rising by 1 a day would blur the
arithmetic, so the closes are flat), 100.0 on the ex-date 2024-01-05 and after; child `C`
(first bar on the ex-date E) closes 20.0 on E. The row is stored in the T64 convention: child
in `security_id`, ratio r = 0.5 in `ratio_or_amount`, parent in `source_action_id`. So
D = r x C = 10.0 and the factor is 1 - 10.0 / 100.0 = 0.9 on every parent bar before E.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import duckdb
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.asof import (
    adjusted_prices_as_of,
    marking_changed_ids_as_of,
    unvalued_spin_offs_as_of,
)
from tradepartner.store.db import configure_connection, insert_row

SESSIONS = (
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
    date(2024, 1, 9),
)
E = SESSIONS[3]
T = session_close(SESSIONS[5])
SETTINGS = Settings(_env_file=None)
RATIO, CHILD_CLOSE, PARENT_CLOSE = 0.5, 20.0, 100.0
D = RATIO * CHILD_CLOSE
FACTOR = 1.0 - D / PARENT_CLOSE


def _bar(
    sid: str, session: date, close: float, *, known_at: datetime | None = None
) -> dict[str, object]:
    stamp = known_at or session_close(session)
    return {
        "security_id": sid,
        "session": session,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
        "known_at": stamp,
        "ingested_at": stamp,
        "source": "alpaca",
        "provenance": "bar",
    }


def _spinoff(
    *,
    ratio: float = RATIO,
    parent: str = "P",
    child: str = "C",
    ex_date: date = E,
    known_at: datetime | None = None,
    action_type: str = "spinoff",
) -> dict[str, object]:
    stamp = known_at or session_close(ex_date)
    return {
        "security_id": child if action_type == "spinoff" else parent,
        "action_type": action_type,
        "ex_date": ex_date,
        "ratio_or_amount": ratio,
        "announced_at": None,
        "source_action_id": parent if action_type == "spinoff" else "",
        "cancelled": False,
        "known_at": stamp,
        "ingested_at": stamp,
        "source": "alpaca",
        "provenance": "action",
    }


def _store(*, child_close: float | None = CHILD_CLOSE) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    for session in SESSIONS:
        insert_row(conn, "prices_daily", _bar("P", session, PARENT_CLOSE))
    if child_close is not None:
        for session in SESSIONS[3:]:
            insert_row(conn, "prices_daily", _bar("C", session, child_close))
    insert_row(conn, "corporate_actions", _spinoff())
    return conn


@pytest.fixture
def store() -> duckdb.DuckDBPyConnection:
    conn = _store()
    yield conn  # type: ignore[misc]
    conn.close()


def _read(
    conn: duckdb.DuckDBPyConnection,
    t: datetime = T,
    ids: list[str] | None = None,
    *,
    spin_offs: bool = True,
) -> pl.DataFrame:
    return adjusted_prices_as_of(
        conn, t, ids, include_dividends=True, include_spin_offs=spin_offs, settings=SETTINGS
    )


def _close(frame: pl.DataFrame, sid: str, session: date) -> float:
    row = frame.filter((pl.col("security_id") == sid) & (pl.col("session") == session))
    assert row.height == 1, (sid, session)
    return float(row["close"].item())


def test_a_valued_spin_off_moves_the_parent_only_before_the_ex_date_by_the_factor(
    store: duckdb.DuckDBPyConnection,
) -> None:
    with_flag, without = _read(store), _read(store, spin_offs=False)
    for session in SESSIONS[:3]:
        assert _close(with_flag, "P", session) == pytest.approx(PARENT_CLOSE * FACTOR, abs=1e-12)
        assert _close(without, "P", session) == PARENT_CLOSE
    # The ex-date, later sessions and the child are untouched.
    untouched = pl.col("security_id").eq("C") | pl.col("session").ge(E)
    assert_frame_equal(with_flag.filter(untouched), without.filter(untouched))


def test_a_valued_spin_off_equals_a_parent_dividend_of_r_times_c_on_the_ex_date(
    store: duckdb.DuckDBPyConnection,
) -> None:
    got = _read(store, ids=["P"])
    store.execute("DELETE FROM corporate_actions")
    insert_row(
        store,
        "corporate_actions",
        _spinoff(parent="P", ratio=D, action_type="dividend") | {"security_id": "P"},
    )
    want = adjusted_prices_as_of(store, T, ["P"], include_dividends=True, settings=SETTINGS)
    assert_frame_equal(
        got.drop("provenance"), want.drop("provenance"), check_exact=False, abs_tol=1e-12
    )
    assert not got.equals(
        adjusted_prices_as_of(store, T, ["P"], include_dividends=False, settings=SETTINGS)
    )


def test_the_flag_without_dividends_raises(store: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="include_dividends"):
        adjusted_prices_as_of(store, T, include_spin_offs=True, settings=SETTINGS)


def test_a_read_for_the_parent_alone_values_it_from_the_childs_bar(
    store: duckdb.DuckDBPyConnection,
) -> None:
    alone = _read(store, ids=["P"])
    assert _close(alone, "P", SESSIONS[0]) == pytest.approx(PARENT_CLOSE * FACTOR, abs=1e-12)
    both = _read(store)
    assert_frame_equal(alone, both.filter(pl.col("security_id") == "P"))
    # A read for the child alone selects no spin-off row: the row is the parent's.
    child_alone = _read(store, ids=["C"])
    assert_frame_equal(child_alone, _read(store, ids=["C"], spin_offs=False))
    assert unvalued_spin_offs_as_of(store, T, ["C"], settings=SETTINGS).is_empty()


def test_an_empty_id_list_selects_nothing(store: duckdb.DuckDBPyConnection) -> None:
    assert _read(store, ids=[]).is_empty()


def test_two_rows_of_one_parent_on_one_ex_date_are_two_factors(
    store: duckdb.DuckDBPyConnection,
) -> None:
    for session in SESSIONS[3:]:
        insert_row(store, "prices_daily", _bar("C2", session, 10.0))
    insert_row(store, "corporate_actions", _spinoff(child="C2", ratio=1.0))
    got = _close(_read(store, ids=["P"]), "P", SESSIONS[0])
    assert got == pytest.approx(PARENT_CLOSE * FACTOR * (1.0 - 10.0 / 100.0), abs=1e-12)


def _no_child_close() -> duckdb.DuckDBPyConnection:
    return _store(child_close=None)


def _no_prior_bar() -> duckdb.DuckDBPyConnection:
    conn = _store()
    conn.execute("DELETE FROM prices_daily WHERE security_id = 'P' AND session < ?", [E])
    return conn


def _stale_prior_bar() -> duckdb.DuckDBPyConnection:
    conn = _store()
    conn.execute("DELETE FROM prices_daily WHERE security_id = 'P' AND session < ?", [E])
    insert_row(conn, "prices_daily", _bar("P", date(2023, 12, 18), PARENT_CLOSE))
    return conn


def _implausible_amount() -> duckdb.DuckDBPyConnection:
    conn = _store()
    conn.execute("UPDATE corporate_actions SET ratio_or_amount = 5.0")  # D = 100 = P
    return conn


def _outside_calendar_range() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    insert_row(
        conn,
        "prices_daily",
        _bar(
            "P", date(1989, 12, 29), PARENT_CLOSE, known_at=datetime(1989, 12, 29, 21, tzinfo=UTC)
        ),
    )
    for session in (date(1990, 1, 3), date(1990, 1, 4)):
        insert_row(conn, "prices_daily", _bar("P", session, PARENT_CLOSE))
        insert_row(conn, "prices_daily", _bar("C", session, CHILD_CLOSE))
    insert_row(conn, "corporate_actions", _spinoff(ex_date=date(1990, 1, 3)))
    return conn


UNVALUED = [
    ("no_child_close", _no_child_close),
    ("no_prior_bar", _no_prior_bar),
    ("stale_prior_bar", _stale_prior_bar),
    ("implausible_amount", _implausible_amount),
    ("outside_calendar_range", _outside_calendar_range),
]


@pytest.mark.parametrize(("reason", "build"), UNVALUED, ids=[u[0] for u in UNVALUED])
def test_each_unvalued_reason_leaves_the_row_deleted_frame_and_is_listed(
    reason: str, build: object
) -> None:
    conn = build()  # type: ignore[operator]
    try:
        t = session_close(date(1990, 1, 4)) if reason == "outside_calendar_range" else T
        got = _read(conn, t)
        ex_date = conn.execute(
            "SELECT ex_date FROM corporate_actions WHERE action_type = 'spinoff'"
        ).fetchone()[0]
        conn.execute("DELETE FROM corporate_actions")
        assert_frame_equal(got, _read(conn, t, spin_offs=False))
        # The listing is a function of the stored row; put it back.
        insert_row(conn, "corporate_actions", _spinoff(ex_date=ex_date))
        if reason == "implausible_amount":
            conn.execute("UPDATE corporate_actions SET ratio_or_amount = 5.0")
        listed = unvalued_spin_offs_as_of(conn, t, settings=SETTINGS)
        assert listed.columns == ["parent", "child", "ex_date", "reason"]
        assert listed.rows() == [("P", "C", ex_date, reason)]
        assert unvalued_spin_offs_as_of(conn, t, ["P"], settings=SETTINGS).rows() == listed.rows()
    finally:
        conn.close()


def test_a_valued_spin_off_is_not_listed(store: duckdb.DuckDBPyConnection) -> None:
    assert unvalued_spin_offs_as_of(store, T, settings=SETTINGS).is_empty()


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_a_non_positive_or_non_finite_ratio_raises(
    store: duckdb.DuckDBPyConnection, bad: float
) -> None:
    store.execute("UPDATE corporate_actions SET ratio_or_amount = ?", [bad])
    with pytest.raises(ValueError, match="non-positive or non-finite"):
        _read(store)
    with pytest.raises(ValueError, match="non-positive or non-finite"):
        unvalued_spin_offs_as_of(store, T, settings=SETTINGS)
    # The default read does not look at spin-off rows at all.
    _read(store, spin_offs=False)


@pytest.mark.parametrize("bad", [0.0, -3.0, float("nan")])
def test_a_non_positive_or_non_finite_child_close_raises(
    store: duckdb.DuckDBPyConnection, bad: float
) -> None:
    store.execute(
        "UPDATE prices_daily SET close = ? WHERE security_id = 'C' AND session = ?", [bad, E]
    )
    with pytest.raises(ValueError, match="non-positive or non-finite"):
        _read(store)


def test_a_cancelled_or_future_dated_row_is_not_applied(store: duckdb.DuckDBPyConnection) -> None:
    base = _read(store, spin_offs=False)
    # Ex-date after t: not yet effective, whatever its known_at.
    assert_frame_equal(
        _read(store, session_close(SESSIONS[2])),
        _read(store, session_close(SESSIONS[2]), spin_offs=False),
    )
    # A later revision that cancels the row retires the earlier one.
    cancel = _spinoff(known_at=T - timedelta(hours=1)) | {"cancelled": True}
    insert_row(store, "corporate_actions", cancel)
    assert_frame_equal(_read(store), base)


# --- No look-ahead -----------------------------------------------------------------


def _frames_agree_before(conn: duckdb.DuckDBPyConnection, plant: callable) -> None:  # type: ignore[valid-type]
    """`t` reads the same with and without the planted fact, which is known after `t`."""
    t = session_close(SESSIONS[4])
    before = _read(conn, t)
    before_list = unvalued_spin_offs_as_of(conn, t, settings=SETTINGS)
    plant(conn)
    assert_frame_equal(_read(conn, t), before)
    assert_frame_equal(unvalued_spin_offs_as_of(conn, t, settings=SETTINGS), before_list)


def test_a_row_known_after_t_leaks_nothing() -> None:
    conn = _store()
    conn.execute("DELETE FROM corporate_actions")
    late = session_close(SESSIONS[5])
    t = session_close(SESSIONS[4])
    base = _read(conn, t)
    insert_row(conn, "corporate_actions", _spinoff(known_at=late))
    assert_frame_equal(_read(conn, t), base)
    assert unvalued_spin_offs_as_of(conn, t, settings=SETTINGS).is_empty()
    # At the instant it becomes known it applies.
    assert _close(_read(conn, late), "P", SESSIONS[0]) == pytest.approx(
        PARENT_CLOSE * FACTOR, abs=1e-12
    )
    conn.close()


def test_a_child_bar_known_after_t_leaks_nothing_and_the_row_is_unvalued_until_then() -> None:
    conn = _store()
    late = session_close(SESSIONS[5])
    conn.execute(
        "UPDATE prices_daily SET known_at = ?, ingested_at = ? "
        "WHERE security_id = 'C' AND session = ?",
        [late, late, E],
    )
    t = session_close(SESSIONS[4])
    row_deleted = _read(conn, t, spin_offs=False)
    assert_frame_equal(_read(conn, t), row_deleted)
    assert unvalued_spin_offs_as_of(conn, t, settings=SETTINGS).rows() == [
        ("P", "C", E, "no_child_close")
    ]
    assert _close(_read(conn, late), "P", SESSIONS[0]) == pytest.approx(
        PARENT_CLOSE * FACTOR, abs=1e-12
    )
    conn.close()


def test_a_revision_of_the_row_or_the_childs_bar_known_after_t_leaks_nothing() -> None:
    late = session_close(SESSIONS[5])

    def revise_row(conn: duckdb.DuckDBPyConnection) -> None:
        insert_row(conn, "corporate_actions", _spinoff(ratio=1.0, known_at=late))

    def revise_child(conn: duckdb.DuckDBPyConnection) -> None:
        insert_row(conn, "prices_daily", _bar("C", E, 30.0, known_at=late))

    def revise_parent_prior(conn: duckdb.DuckDBPyConnection) -> None:
        insert_row(conn, "prices_daily", _bar("P", SESSIONS[2], 50.0, known_at=late))

    for plant in (revise_row, revise_child, revise_parent_prior):
        conn = _store()
        _frames_agree_before(conn, plant)
        conn.close()
    # And each revision does change the read once known.
    conn = _store()
    base = _read(conn, late)
    revise_child(conn)
    assert not _read(conn, late).equals(base)
    conn.close()


def test_the_probe_names_the_parent_when_the_row_becomes_known_or_effective() -> None:
    conn = _store()
    conn.execute("DELETE FROM corporate_actions")
    ids = ["P", "C", "Z"]
    prev, now = session_close(SESSIONS[1]), session_close(SESSIONS[2])
    assert marking_changed_ids_as_of(conn, prev, now, ids) == ()
    # The row becomes known in (prev, now]: the parent is named, though the row's
    # security_id is the child.
    insert_row(conn, "corporate_actions", _spinoff(known_at=now))
    assert "P" in marking_changed_ids_as_of(conn, prev, now, ["P", "Z"])
    # The row is known; its ex-date arrives in (prev, now].
    conn.execute(
        "UPDATE corporate_actions SET known_at = ?, ingested_at = ?", [prev - timedelta(days=1)] * 2
    )
    assert "P" in marking_changed_ids_as_of(conn, session_close(SESSIONS[2]), T, ["P"])
    conn.close()


def test_the_probe_names_the_parent_when_the_childs_bar_on_e_becomes_known() -> None:
    conn = _store()
    late = session_close(SESSIONS[5])
    prev = session_close(SESSIONS[4])
    conn.execute(
        "UPDATE prices_daily SET known_at = ?, ingested_at = ? "
        "WHERE security_id = 'C' AND session = ?",
        [late, late, E],
    )
    assert marking_changed_ids_as_of(conn, prev, late, ["P"]) == ("P",)
    assert (
        marking_changed_ids_as_of(
            conn, session_close(SESSIONS[0]), session_close(SESSIONS[1]), ["P"]
        )
        == ()
    )
    conn.close()


def test_the_utc_instant_just_before_the_row_is_known_excludes_it() -> None:
    conn = _store()
    conn.execute(
        "UPDATE corporate_actions SET known_at = ?, ingested_at = ?",
        [datetime(2024, 1, 9, 21, tzinfo=UTC)] * 2,
    )
    t = datetime(2024, 1, 9, 21, tzinfo=UTC)
    assert_frame_equal(
        _read(conn, t - timedelta(microseconds=1)),
        _read(conn, t - timedelta(microseconds=1), spin_offs=False),
    )
    assert not _read(conn, t).equals(_read(conn, t, spin_offs=False))
    conn.close()
