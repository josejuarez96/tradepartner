"""Marks and lapses, pure (Phase 4 plan T63b; spec req 7 step 5, req 5).

Every expected number is computed by hand in the test's comments.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import polars as pl
import pytest

from tradepartner.calendar import next_session, session_close
from tradepartner.execution import ledger as ledger_module
from tradepartner.execution.ids import client_order_id
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.marks import Mark, Missed, lapses, marks_for, missed_run
from tradepartner.execution.switch import SwitchState
from tradepartner.store import schema
from tradepartner.store.db import insert_row
from tradepartner.store.journal import (
    FillRow,
    OrderedFill,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    RebalanceEventRow,
)

WINDOW = 1
A = "SEC_A"
B = "SEC_B"
TOLERANCE = 1e-6


def _stamp(at: datetime) -> dict[str, datetime]:
    return {"known_at": at, "ingested_at": at}


def _window(window_id: int = WINDOW, first_rebalance_session: date | None = None) -> PaperWindowRow:
    session = first_rebalance_session or date(2026, 9, 30)
    at = datetime(session.year, session.month, session.day, 13, tzinfo=UTC)
    return PaperWindowRow(
        window_id=window_id,
        hypothesis_id=1,
        first_rebalance_session=session,
        account_id="PA1",
        starting_cash=1000.0,
        starting_equity=1000.0,
        code_version="abc",
        started_at=at,
        frozen_json="{}",
        frozen_sha256="0" * 64,
        **_stamp(at),
    )


def _switch(engaged: bool) -> SwitchState:
    return SwitchState(engaged=engaged, run_in_progress=False, causes=())


def _bar(conn: duckdb.DuckDBPyConnection, security_id: str, session: date, close: float) -> None:
    known_at = session_close(session)
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": security_id,
            "session": session,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "test",
            "provenance": "bar",
        },
    )


@pytest.fixture
def store(tmp_path) -> Iterator[duckdb.DuckDBPyConnection]:
    """A file-backed store, written with fixture bars then reopened read-only,
    so `marks_for`'s acceptance criterion ("every read through a read-only
    connection") is tested against a connection that cannot write."""
    path = tmp_path / "store.duckdb"
    write_conn = duckdb.connect(str(path))
    schema.init_schema(write_conn)
    yield write_conn
    write_conn.close()


def _reopen_read_only(tmp_path) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(tmp_path / "store.duckdb"), read_only=True)


def _ledger_for(
    positions: dict[date, dict[str, float]],
    cash: float = 500.0,
):
    """A `marks.LedgerFor` stub: `positions[through]` is the window's holding
    as of that session, handed straight back as a `Ledger` (these tests pin
    `marks_for`'s own behaviour, not `ledger.from_journal`'s, which T51
    already covers)."""

    def _ledger(through: date) -> Ledger:
        return Ledger(positions=dict(positions.get(through, {})), cash=cash, through=through)

    return _ledger


# --- marks_for ----------------------------------------------------------------------


def test_back_fills_two_sessions_without_a_run(tmp_path) -> None:
    d1, d2 = date(2026, 10, 5), date(2026, 10, 6)
    write_conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(write_conn)
    _bar(write_conn, A, d1, 10.0)
    _bar(write_conn, A, d2, 11.0)
    write_conn.close()
    conn = _reopen_read_only(tmp_path)
    try:
        window = _window()
        ledger = _ledger_for({d1: {A: 50.0}, d2: {A: 50.0}})
        marks = marks_for(conn, window, ledger, [d1, d2], {A: True})
    finally:
        conn.close()

    assert [m.session for m in marks] == [d1, d2]
    assert marks[0] == Mark(
        session=d1,
        security_id=A,
        quantity=50.0,
        mark_price=10.0,
        value=500.0,
        cash=500.0,
        tradable=True,
    )
    assert marks[1] == Mark(
        session=d2,
        security_id=A,
        quantity=50.0,
        mark_price=11.0,
        value=550.0,
        cash=500.0,
        tradable=True,
    )


def test_flat_session_gets_one_cash_only_row(tmp_path) -> None:
    d1 = date(2026, 10, 5)
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    ledger = _ledger_for({})  # nothing held on d1
    marks = marks_for(conn, _window(), ledger, [d1], {})
    conn.close()

    assert marks == [
        Mark(
            session=d1,
            security_id=None,
            quantity=0.0,
            mark_price=None,
            value=None,
            cash=500.0,
            tradable=None,
        )
    ]


def test_split_on_session_marks_pre_split_quantity_and_raw_close(tmp_path) -> None:
    """Spec acceptance: "a 2:1 split with ex_date = S on a held name marks at
    close(S-1) with the pre-split quantity and raw close (no jump)." Here S-1
    is the session being marked, S (the next session) is the split's ex_date,
    strictly after it. The `ledger` callable is bound to the real
    `ledger.from_journal` (T51) over one 100-share buy and a `split`
    `actions_as_of` row dated S, so this test exercises `marks_for`'s actual
    integration with the ledger's own "ex_date <= through" rule, not a stub:
    `from_journal(through=S-1)` excludes a split whose ex_date is S, so the
    position stays at the pre-split 100 shares and the raw, pre-split close
    recorded for S-1 is untouched -- no jump."""
    d0 = date(2026, 10, 1)
    s_minus_1 = date(2026, 10, 5)
    s = next_session(s_minus_1)  # the split's ex_date, after the marked session
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    _bar(conn, A, s_minus_1, 20.0)  # the raw, pre-split close

    order = OrderRow(
        client_order_id=client_order_id("tp", d0, A, "buy", 1),
        decision_id=1,
        run_id=1,
        session=d0,
        attempt=1,
        phase="buys",
        security_id=A,
        symbol=A,
        side="buy",
        notional=None,
        quantity=100.0,
        sells_in_flight_at_submit=False,
        **_stamp(_at(d0)),
    )
    fill = OrderedFill(
        fill=FillRow(
            fill_id=1,
            client_order_id=order.client_order_id,
            filled_at=_at(d0),
            quantity=100.0,
            price=18.0,
            price_implied=False,
            broker_fill_id="f1",
            source="broker_feed",
            **_stamp(_at(d0)),
        ),
        side="buy",
        security_id=A,
        symbol=A,
        run_id=1,
        window_id=WINDOW,
    )
    actions_as_of = pl.DataFrame(
        {
            "security_id": [A],
            "action_type": ["split"],
            "ex_date": [s],
            "ratio_or_amount": [2.0],
        }
    )

    def ledger(through: date) -> Ledger:
        return ledger_module.from_journal(
            [fill],
            [order],
            [],
            actions_as_of,
            None,
            starting_cash=2000.0,
            through=through,
            window_id=WINDOW,
            quantity_tolerance=TOLERANCE,
        )

    marks = marks_for(conn, _window(), ledger, [s_minus_1], {A: True})
    conn.close()

    # Cash: started at 2000.0, minus 100 * 18.0 for the buy = 200.0.
    assert marks == [
        Mark(
            session=s_minus_1,
            security_id=A,
            quantity=100.0,
            mark_price=20.0,
            value=2000.0,
            cash=200.0,
            tradable=True,
        )
    ]


def test_tradable_carried_from_flags(tmp_path) -> None:
    d1 = date(2026, 10, 5)
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    _bar(conn, A, d1, 10.0)
    _bar(conn, B, d1, 5.0)
    ledger = _ledger_for({d1: {A: 1.0, B: 1.0}})
    marks = marks_for(conn, _window(), ledger, [d1], {A: False})
    conn.close()

    by_name = {m.security_id: m for m in marks}
    assert by_name[A].tradable is False
    assert by_name[B].tradable is None  # no flag given: carried through as unknown


def test_missing_price_raises(tmp_path) -> None:
    d1 = date(2026, 10, 5)
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    ledger = _ledger_for({d1: {A: 1.0}})
    with pytest.raises(ValueError, match="no price"):
        marks_for(conn, _window(), ledger, [d1], {})
    conn.close()


def test_a_stale_earlier_bar_never_stands_in_for_a_missing_one(tmp_path) -> None:
    """`prices_as_of` returns the latest-known revision of *every* session's
    bar on or before the one given, one row per (security_id, session), not
    only the session asked about. A d1-only `closes` dict built without
    filtering on `session` would therefore silently price a d2 mark off d1's
    stale close instead of raising "no price" for the missing d2 bar -- a
    fake return (and, with a split in between, a fake drawdown). This pins
    that `marks_for` filters to the session itself."""
    d1 = date(2026, 10, 5)
    d2 = next_session(d1)
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    _bar(conn, A, d1, 10.0)  # d2 gets no bar at all
    ledger = _ledger_for({d2: {A: 1.0}})
    with pytest.raises(ValueError, match="no price"):
        marks_for(conn, _window(), ledger, [d2], {A: True})
    conn.close()


# --- missed_run -----------------------------------------------------------------


def test_missed_run_true_when_s_minus_1_has_no_run() -> None:
    d1 = date(2026, 10, 5)
    d2 = next_session(d1)
    d3 = next_session(d2)
    runs = [
        PaperRunRow(
            run_id=1,
            window_id=WINDOW,
            session=d1,
            started_at=_at(d1),
            invoked_by="tty",
            code_version="x",
            **_stamp(_at(d1)),
        )
    ]
    assert missed_run(runs, d2) is False  # S-1 = d1, has a row
    assert missed_run(runs, d3) is True  # S-1 = d2, no row


def _at(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 13, tzinfo=UTC)


# --- lapses ---------------------------------------------------------------------


def test_lapses_before_the_first_rebalance_session_is_empty() -> None:
    """A mark-only session before the window's T_0 (e.g. the run right after
    `paper start`) has no rebalance due yet; `rebalance_sessions(start, end)`
    raises for `start > end`, so this must be handled before calling it."""
    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    before = date(2026, 9, 29)
    assert lapses(window, [], [], _switch(False), before, {"paper.max_catch_up_sessions": 1}) == []


def test_lapses_computed_at_boundary_session_and_not_one_earlier() -> None:
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)  # a rebalance session (last session of September)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 2
    boundary = f0
    for _ in range(max_catch_up):
        boundary = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}

    # Still inside the catch-up window: no lapse yet.
    assert lapses(window, [], [], _switch(False), boundary, frozen) == []

    # The first session past the boundary: lapsed.
    past = next_session(boundary)
    assert lapses(window, [], [], _switch(False), past, frozen) == [
        Missed(rebalance_session=t0, reason="catch_up_lapsed")
    ]


def test_lapses_reason_is_kill_switch_when_the_switch_is_engaged() -> None:
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 1
    boundary = next_session(f0)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}

    assert lapses(window, [], [], _switch(True), past, frozen) == [
        Missed(rebalance_session=t0, reason="kill_switch")
    ]


def test_lapses_skips_a_rebalance_already_journaled() -> None:
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 1
    boundary = next_session(f0)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    run = PaperRunRow(
        run_id=1,
        window_id=WINDOW,
        session=t0,
        started_at=_at(t0),
        invoked_by="tty",
        code_version="x",
        **_stamp(_at(t0)),
    )
    executed = RebalanceEventRow(
        rebalance_session=t0, run_id=1, status="executed", **_stamp(_at(t0))
    )

    assert lapses(window, [run], [executed], _switch(False), past, frozen) == []


def test_lapses_raises_for_an_event_whose_run_is_not_given() -> None:
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    past = next_session(next_session(f0))
    event = RebalanceEventRow(rebalance_session=t0, run_id=99, status="executed", **_stamp(_at(t0)))

    with pytest.raises(ValueError, match="not among the runs given"):
        lapses(window, [], [event], _switch(False), past, {"paper.max_catch_up_sessions": 1})


# --- read-only and no-write checks ------------------------------------------------


def test_marks_for_works_through_a_read_only_connection(tmp_path) -> None:
    d1 = date(2026, 10, 5)
    write_conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(write_conn)
    _bar(write_conn, A, d1, 10.0)
    write_conn.close()

    conn = duckdb.connect(str(tmp_path / "store.duckdb"), read_only=True)
    try:
        ledger = _ledger_for({d1: {A: 1.0}})
        marks = marks_for(conn, _window(), ledger, [d1], {})
    finally:
        conn.close()
    assert marks[0].mark_price == 10.0


def test_module_has_no_write_statements() -> None:
    import tradepartner.execution.marks as marks

    text = inspect.getsource(marks)
    for keyword in ("INSERT", "UPDATE", "DELETE"):
        assert not re.search(rf"\b{keyword}\b", text, re.IGNORECASE), keyword
