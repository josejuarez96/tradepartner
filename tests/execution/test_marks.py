"""Marks and lapses, pure (Phase 4 plan T63b; spec req 7 step 5, req 5).

Every expected number is computed by hand in the test's comments.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import duckdb
import polars as pl
import pytest

from tradepartner.calendar import next_session, previous_session, session_close, session_open
from tradepartner.execution import ledger as ledger_module
from tradepartner.execution.ids import client_order_id
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.marks import (
    Mark,
    Missed,
    UnreadableMarkError,
    equity_at,
    lapses,
    marks_for,
    missed_run,
)
from tradepartner.store import schema
from tradepartner.store.db import insert_row
from tradepartner.store.journal import (
    FillRow,
    KillSwitchRow,
    OrderedFill,
    OrderRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    RebalanceEventRow,
)

WINDOW = 1
A = "SEC_A"
B = "SEC_B"
TOLERANCE = 1e-6
#: An empty `actions_as_of` frame: no split among the marked sessions.
NO_ACTIONS = pl.DataFrame(
    schema={
        "security_id": pl.Utf8,
        "action_type": pl.Utf8,
        "ex_date": pl.Date,
        "ratio_or_amount": pl.Float64,
    }
)


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


def _kill_switch_row(event_id: int, at: datetime, state: str) -> KillSwitchRow:
    return KillSwitchRow(
        event_id=event_id,
        window_id=WINDOW,
        at=at,
        state=state,
        source="owner",
        **_stamp(at),
    )


def _run(run_id: int, session: date, *, window_id: int = WINDOW) -> PaperRunRow:
    return PaperRunRow(
        run_id=run_id,
        window_id=window_id,
        session=session,
        started_at=_at(session),
        invoked_by="tty",
        code_version="x",
        **_stamp(_at(session)),
    )


def _null_session_run(run_id: int, started_at: datetime, *, window_id: int = WINDOW) -> PaperRunRow:
    """A run invoked on a non-session day: `session` and `kind` are both
    `NULL` in the schema."""
    return PaperRunRow(
        run_id=run_id,
        window_id=window_id,
        session=None,
        started_at=started_at,
        invoked_by="tty",
        code_version="x",
        **_stamp(started_at),
    )


def _result(run_id: int, at: datetime, status: str) -> PaperRunResultRow:
    return PaperRunResultRow(
        run_id=run_id,
        finished_at=at,
        status=status,
        clock_fault=False,
        **_stamp(at),
    )


def _bar(
    conn: duckdb.DuckDBPyConnection,
    security_id: str,
    session: date,
    close: float,
    known_at: datetime | None = None,
) -> None:
    known_at = known_at or session_close(session)
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
        marks = marks_for(conn, window, ledger, [d1, d2], {A: True}, actions=NO_ACTIONS)
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
    marks = marks_for(conn, _window(), ledger, [d1], {}, actions=NO_ACTIONS)
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

    marks = marks_for(conn, _window(), ledger, [s_minus_1], {A: True}, actions=actions_as_of)
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
    marks = marks_for(conn, _window(), ledger, [d1], {A: False}, actions=NO_ACTIONS)
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
        marks_for(conn, _window(), ledger, [d1], {}, actions=NO_ACTIONS)
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
        marks_for(conn, _window(), ledger, [d2], {A: True}, actions=NO_ACTIONS)
    conn.close()


# A delisted name's bars stop (WNDX: last bar 2019-06-24, Form 25 known only
# after close(2019-06-28)); the sessions after its last bar get no bar at all.
D0 = date(2026, 10, 5)
D1 = next_session(D0)
D2 = next_session(D1)
D3 = next_session(D2)


@pytest.mark.parametrize(
    "flags",
    [
        pytest.param({A: False}, id="untradable"),
        pytest.param({}, id="no-flag"),  # no current ticker, or not in the assets read
    ],
)
def test_an_untradable_name_past_its_last_bar_is_marked_at_its_last_close(
    tmp_path, caplog: pytest.LogCaptureFixture, flags: dict[str, bool]
) -> None:
    """#678 / T86 (spec req 7, "marked at its last close"): a held name whose
    flag is false or absent and whose bars stopped carries its last known
    close forward from the session after its last bar, with a `marks`
    warning naming the stale close's date. 4 shares x 10.0 = 40.0."""
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    _bar(conn, A, D0, 10.0)  # its last bar; D1 and D2 get none
    ledger = _ledger_for({D1: {A: 4.0}, D2: {A: 4.0}})
    with caplog.at_level("WARNING", logger="tradepartner.execution.marks"):
        marks = marks_for(conn, _window(), ledger, [D1, D2], flags, actions=NO_ACTIONS)
    conn.close()

    assert [(m.session, m.mark_price, m.value) for m in marks] == [
        (D1, 10.0, 40.0),
        (D2, 10.0, 40.0),
    ]
    assert all(m.tradable is flags.get(A) for m in marks)
    warned = [r.getMessage() for r in caplog.records]
    assert len(warned) == 2
    assert all(A in w and D0.isoformat() in w for w in warned)


def test_a_gap_before_the_names_last_bar_still_raises(tmp_path) -> None:
    """A missing bar followed by a later bar is an ingest gap, not a delisting:
    even untradable, the name is never marked at a stale close there."""
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    _bar(conn, A, D0, 10.0)
    _bar(conn, A, D2, 12.0)  # D1 has no bar, but D2 does
    ledger = _ledger_for({D1: {A: 1.0}, D2: {A: 1.0}})
    with pytest.raises(ValueError, match="no price"):
        marks_for(conn, _window(), ledger, [D1, D2], {A: False}, actions=NO_ACTIONS)
    conn.close()


def test_a_gap_on_a_name_sold_before_the_last_session_still_raises(tmp_path) -> None:
    """A back-fill: the name is held on D1 only (sold before D2), so the run's
    `assets` read gives it no flag, and its D1 bar is missing while D2's is
    there. That is an ingest gap, not a delisting: it raises, though the name
    is not held on the session that has the later bar."""
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    _bar(conn, A, D0, 10.0)
    _bar(conn, A, D2, 12.0)
    ledger = _ledger_for({D1: {A: 1.0}})  # flat on D2
    with pytest.raises(ValueError, match="no price"):
        marks_for(conn, _window(), ledger, [D1, D2], {}, actions=NO_ACTIONS)
    conn.close()


def test_a_name_with_no_bar_at_all_still_raises_when_untradable(tmp_path) -> None:
    """No last close to carry: the untradable rule never invents a price."""
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    ledger = _ledger_for({D1: {A: 1.0}})
    with pytest.raises(ValueError, match="no price"):
        marks_for(conn, _window(), ledger, [D1], {A: False}, actions=NO_ACTIONS)
    conn.close()


def test_the_carried_close_reads_no_bar_after_the_session(tmp_path) -> None:
    """No look-ahead: the D1 mark carries the D0 close as known at close(D1).
    A revision of D0 known only after close(D1) prices D2's mark, not D1's,
    and a bar dated after the last marked session is never read."""
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    _bar(conn, A, D0, 10.0)
    _bar(conn, A, D0, 11.0, known_at=session_close(D1) + timedelta(minutes=1))
    _bar(conn, A, D3, 99.0)  # after every marked session: never read
    ledger = _ledger_for({D1: {A: 1.0}, D2: {A: 1.0}})
    marks = marks_for(conn, _window(), ledger, [D1, D2], {A: False}, actions=NO_ACTIONS)
    conn.close()

    assert [(m.session, m.mark_price) for m in marks] == [(D1, 10.0), (D2, 11.0)]


def test_a_split_inside_the_carried_gap_adjusts_the_carried_close(tmp_path) -> None:
    """#892 item 4 / #1116: an untradable name past its last bar (D0, close
    10.0) with a 2:1 split whose ex-date (D2) falls inside the carried gap.
    The ledger's quantity doubles on D2 (4 -> 8 shares), so the carried raw
    close is divided by the ratio there: D1 4 x 10.0 = 40.0, D2 8 x 5.0 =
    40.0. Carrying the raw 10.0 would mark D2 at 80.0, a fake gain of the
    split ratio. A split before the last bar (D0) or after the marked
    session is not counted."""
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    _bar(conn, A, D0, 10.0)
    ledger = _ledger_for({D1: {A: 4.0}, D2: {A: 8.0}})
    actions = pl.DataFrame(
        {
            "security_id": [A, A, A, B],
            "action_type": ["split", "split", "split", "split"],
            "ex_date": [D0, D2, D3, D2],
            "ratio_or_amount": [3.0, 2.0, 5.0, 7.0],
        }
    )
    marks = marks_for(conn, _window(), ledger, [D1, D2], {A: False}, actions=actions)
    conn.close()

    assert [(m.session, m.quantity, m.mark_price, m.value) for m in marks] == [
        (D1, 4.0, 10.0, 40.0),
        (D2, 8.0, 5.0, 40.0),
    ]


@pytest.mark.parametrize("ratio", [0.0, -2.0, float("nan"), float("inf")])
def test_a_bad_split_ratio_inside_the_carried_gap_raises(tmp_path, ratio: float) -> None:
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    _bar(conn, A, D0, 10.0)
    ledger = _ledger_for({D1: {A: 4.0}})
    actions = pl.DataFrame(
        {
            "security_id": [A],
            "action_type": ["split"],
            "ex_date": [D1],
            "ratio_or_amount": [ratio],
        }
    )
    with pytest.raises(ValueError, match="ratio"):
        marks_for(conn, _window(), ledger, [D1], {A: False}, actions=actions)
    conn.close()


# --- equity_at ----------------------------------------------------------------------


def _row(
    session: date,
    security_id: str | None,
    *,
    cash: float | None,
    value: float | None = None,
) -> PositionDailyRow:
    at = session_close(session)
    return PositionDailyRow(
        run_id=1,
        session=session,
        security_id=security_id,
        quantity=0.0 if security_id is None else 1.0,
        mark_price=value,
        value=value,
        cash=cash,
        known_at=at,
        ingested_at=at,
    )


def test_equity_at_reads_a_held_sessions_cash_from_its_position_rows() -> None:
    """#665 / #649: once anything is held `marks_for` writes no cash-only
    row, only position rows each carrying the ledger's cash. 500 + 40 + 60 =
    600; another session's rows are never read."""
    rows = [
        _row(D1, A, cash=500.0, value=40.0),
        _row(D1, B, cash=500.0, value=60.0),
        _row(D2, A, cash=1.0, value=1.0),
    ]
    assert equity_at(rows, D1) == 600.0


def test_equity_at_reads_a_flat_sessions_cash_only_row() -> None:
    assert equity_at([_row(D1, None, cash=750.0)], D1) == 750.0


def test_equity_at_reads_the_marks_for_rows_it_is_given(tmp_path) -> None:
    """Round trip with the writer's own row shape: two held names on D1."""
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    schema.init_schema(conn)
    _bar(conn, A, D1, 10.0)
    _bar(conn, B, D1, 5.0)
    marks = marks_for(
        conn, _window(), _ledger_for({D1: {A: 2.0, B: 4.0}}), [D1], {}, actions=NO_ACTIONS
    )
    conn.close()
    rows = [
        PositionDailyRow(
            run_id=1,
            session=m.session,
            security_id=m.security_id,
            quantity=m.quantity,
            mark_price=m.mark_price,
            value=m.value,
            cash=m.cash,
            known_at=session_close(D1),
            ingested_at=session_close(D1),
        )
        for m in marks
    ]
    assert all(r.security_id is not None for r in rows)  # no cash-only row
    assert equity_at(rows, D1) == 500.0 + 20.0 + 20.0


@pytest.mark.parametrize(
    ("rows", "match"),
    [
        pytest.param([], "no mark rows", id="no-rows"),
        pytest.param(
            [_row(D1, A, cash=500.0, value=40.0), _row(D1, B, cash=None, value=60.0)],
            "no cash",
            id="a-none-cash-next-to-valued-rows",
        ),
        pytest.param([_row(D1, None, cash=None)], "no cash", id="flat-none-cash"),
        pytest.param(
            [_row(D1, A, cash=500.0, value=40.0), _row(D1, B, cash=400.0, value=60.0)],
            "disagree",
            id="two-cash-values",
        ),
        pytest.param([_row(D1, A, cash=500.0, value=None)], "has value None", id="no-value"),
        pytest.param([_row(D1, None, cash=float("nan"))], "cash", id="nan-cash"),
        pytest.param([_row(D1, None, cash=float("inf"))], "cash", id="inf-cash"),
        pytest.param(
            [_row(D1, A, cash=500.0, value=float("nan"))], "has value nan", id="nan-value"
        ),
        pytest.param(
            [
                _row(D1, A, cash=500.0, value=float("inf")),
                _row(D1, B, cash=500.0, value=float("-inf")),
            ],
            "has value inf",
            id="inf-and-minus-inf",  # math.fsum would raise its own ValueError
        ),
        pytest.param(
            [_row(D1, A, cash=1e308, value=1e308)], "equity marked", id="overflowing-total"
        ),
    ],
)
def test_equity_at_refuses_a_session_it_cannot_read(
    rows: list[PositionDailyRow], match: str
) -> None:
    """A row that cannot state the equity is refused, never skipped (#665,
    #713 (ii)(a), #1116)."""
    with pytest.raises(UnreadableMarkError, match=match):
        equity_at(rows, D1)


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
    assert lapses(window, [], [], [], [], before, {"paper.max_catch_up_sessions": 1}) == []


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
    assert lapses(window, [], [], [], [], boundary, frozen) == []

    # The first session past the boundary: lapsed.
    past = next_session(boundary)
    assert lapses(window, [], [], [], [], past, frozen) == [
        Missed(rebalance_session=t0, reason="catch_up_lapsed")
    ]


def test_lapses_reason_is_kill_switch_when_the_switch_is_engaged_at_the_checked_session() -> None:
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 1
    boundary = next_session(f0)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    # Engaged on the boundary session itself (inside the catch-up period) and
    # still engaged at `past`.
    rows = [_kill_switch_row(1, session_close(boundary), "engaged")]

    assert lapses(window, [], [], rows, [], past, frozen) == [
        Missed(rebalance_session=t0, reason="kill_switch")
    ]


def test_lapses_reason_is_kill_switch_even_when_released_before_the_lapse() -> None:
    """#366 Q19(b): a switch released one session before the lapse must still
    report `kill_switch`, because it was engaged during the catch-up period
    even though it is clear again by `past`, the session checked."""
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 2
    boundary = f0
    for _ in range(max_catch_up):
        boundary = next_session(boundary)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    # Engaged during f0 (the first session of the catch-up period), released
    # on the boundary session -- clear again well before `past`.
    rows = [
        _kill_switch_row(1, session_close(f0), "engaged"),
        _kill_switch_row(2, session_close(boundary), "released"),
    ]

    assert lapses(window, [], [], rows, [], past, frozen) == [
        Missed(rebalance_session=t0, reason="kill_switch")
    ]


def test_lapses_reason_is_catch_up_lapsed_when_engaged_only_after_the_period() -> None:
    """An engagement that starts only once the catch-up window has already
    run out (on `past` itself, not on any session from F_i through the
    boundary) did not cause the miss, so the reason stays `catch_up_lapsed`."""
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 1
    boundary = next_session(f0)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    rows = [_kill_switch_row(1, session_close(past), "engaged")]

    assert lapses(window, [], [], rows, [], past, frozen) == [
        Missed(rebalance_session=t0, reason="catch_up_lapsed")
    ]


def test_lapses_reason_is_kill_switch_for_an_engage_and_release_within_one_session() -> None:
    """Safety/quant review on #532 (reviewed at 3ad42dc): sampling the switch
    only at each session's own close missed an engagement opened and
    released within a single session of the catch-up period -- e.g. the
    owner engages the switch before the boundary session's open (blocking
    that session's run) and resumes before its close. The reason must still
    be `kill_switch`."""
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 0
    boundary = f0  # max_catch_up_sessions = 0: the boundary is F_i itself
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    # Engaged 30 minutes before the boundary session's open, released two
    # hours later -- both well before that session's close, and well before
    # `past`.
    engaged_at = session_open(boundary) - timedelta(minutes=30)
    released_at = session_open(boundary) + timedelta(hours=2)
    rows = [
        _kill_switch_row(1, engaged_at, "engaged"),
        _kill_switch_row(2, released_at, "released"),
    ]

    assert lapses(window, [], [], rows, [], past, frozen) == [
        Missed(rebalance_session=t0, reason="kill_switch")
    ]


def test_lapses_reason_is_kill_switch_for_a_crashed_run_with_no_kill_switch_row() -> None:
    """Safety/quant review on #532: a run that crashed (or hit a halt-path
    write failure) inside the catch-up period engages the switch by
    `execution.switch.derive`'s own rule even with no `kill_switch` row at
    all (spec req 5: "no `released` row after that run's `started_at`").
    `lapses` must still name `kill_switch`, not `catch_up_lapsed`, for a
    rebalance whose catch-up window ran out while such a run sat uncleared."""
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 1
    boundary = next_session(f0)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    # A run on the boundary session halted; no kill_switch row exists at all
    # (the halt-path write failed, or the process died before it could try).
    crashed = _run(1, boundary)
    halted = _result(1, _at(boundary), "halted")

    assert lapses(window, [crashed], [], [], [halted], past, frozen) == [
        Missed(rebalance_session=t0, reason="kill_switch")
    ]

    # An unfinished run (no result row at all, e.g. a hard crash) counts too.
    unfinished = _run(2, boundary)
    assert lapses(window, [unfinished], [], [], [], past, frozen) == [
        Missed(rebalance_session=t0, reason="kill_switch")
    ]


def test_lapses_reason_is_kill_switch_for_a_crash_from_before_the_period_uncleared() -> None:
    """Second-pass finding on #532 (quant-auditor and safety-reviewer, both
    FAIL at 54366cf): a run that crashed *before* F_i and was never cleared
    by a release still has the switch engaged throughout the catch-up
    period by `execution.switch.derive`'s own rule (it keeps engaging until
    a `released` row comes after both its `started_at` and its
    `finished_at`). Every run in the period (F_i and the boundary session)
    then ends `skipped_kill_switch`, exactly as spec req 5 requires while
    engaged, with no `kill_switch` row of its own. The reason must still be
    `kill_switch`, not `catch_up_lapsed`."""
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 1
    boundary = next_session(f0)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    before_f0 = previous_session(f0)
    crashed = _run(1, before_f0)
    halted = _result(1, _at(before_f0), "halted")
    # The runs inside the period itself: skipped by the still-engaged switch,
    # no kill_switch row of their own, no result in FAULTED_RUN_STATUSES.
    run_f0 = _run(2, f0)
    result_f0 = _result(2, _at(f0), "skipped_kill_switch")
    run_boundary = _run(3, boundary)
    result_boundary = _result(3, _at(boundary), "skipped_kill_switch")

    assert lapses(
        window,
        [crashed, run_f0, run_boundary],
        [],
        [],
        [halted, result_f0, result_boundary],
        past,
        frozen,
    ) == [Missed(rebalance_session=t0, reason="kill_switch")]

    # A release before F_i clears the crash: the period then has nothing
    # engaging it (its own runs finish `ok`), so the reason reverts to
    # `catch_up_lapsed`.
    released = _kill_switch_row(1, session_close(before_f0), "released")
    run_f0b = _run(4, f0)
    ok_f0 = _result(4, _at(f0), "ok")
    run_boundary_b = _run(5, boundary)
    ok_boundary = _result(5, _at(boundary), "ok")
    assert lapses(
        window,
        [crashed, run_f0b, run_boundary_b],
        [],
        [released],
        [halted, ok_f0, ok_boundary],
        past,
        frozen,
    ) == [Missed(rebalance_session=t0, reason="catch_up_lapsed")]


def test_lapses_ignores_kill_switch_rows_of_another_window() -> None:
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 1
    boundary = next_session(f0)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    other_window_row = KillSwitchRow(
        event_id=1,
        window_id=WINDOW + 1,
        at=session_close(boundary),
        state="engaged",
        source="owner",
        **_stamp(session_close(boundary)),
    )

    assert lapses(window, [], [], [other_window_row], [], past, frozen) == [
        Missed(rebalance_session=t0, reason="catch_up_lapsed")
    ]


def test_lapses_reason_is_catch_up_lapsed_when_a_release_has_no_engage_before_it() -> None:
    """A stray `released` row with nothing engaged before it reads as not
    engaged, never as a bug that defaults to `kill_switch`."""
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 1
    boundary = next_session(f0)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    rows = [_kill_switch_row(1, session_close(f0), "released")]

    assert lapses(window, [], [], rows, [], past, frozen) == [
        Missed(rebalance_session=t0, reason="catch_up_lapsed")
    ]


def test_lapses_reason_is_kill_switch_when_engaged_before_f0_and_never_released() -> None:
    """An engagement from before T_i's fill session that is still open when
    the catch-up period starts carries forward and counts, even with no
    `kill_switch` row inside the period itself."""
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 1
    boundary = next_session(f0)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    before_f0 = previous_session(f0)
    rows = [_kill_switch_row(1, session_close(before_f0), "engaged")]

    assert lapses(window, [], [], rows, [], past, frozen) == [
        Missed(rebalance_session=t0, reason="kill_switch")
    ]


def test_lapses_does_not_raise_on_a_null_session_run_before_the_period() -> None:
    """quant-auditor finding on #532 at 8da3e8d: `paper_runs.session` is
    nullable (a run invoked on a non-session day ends `no_session` with no
    `session` or `kind`). A routine weekend/holiday invocation before the
    catch-up period must not raise and must not engage it -- its result is
    `no_session`, never in `FAULTED_RUN_STATUSES`."""
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 1
    boundary = next_session(f0)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    before_f0 = previous_session(f0)
    weekend_run = _null_session_run(1, session_close(before_f0) - timedelta(hours=1))
    weekend_result = _result(1, session_close(before_f0) - timedelta(hours=1), "no_session")

    assert lapses(window, [weekend_run], [], [], [weekend_result], past, frozen) == [
        Missed(rebalance_session=t0, reason="catch_up_lapsed")
    ]


def test_lapses_reason_is_kill_switch_for_a_null_session_unfinished_run() -> None:
    """quant-auditor finding on #532 at 8da3e8d: a null-session run that
    crashed (no result row at all) and was never released still engages
    the switch by `execution.switch.derive`'s own rule, whatever its
    `session`; placed by `started_at` against the period boundaries, it
    must carry forward into the period the same way a dated run does."""
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    max_catch_up = 1
    boundary = next_session(f0)
    past = next_session(boundary)
    frozen = {"paper.max_catch_up_sessions": max_catch_up}
    before_f0 = previous_session(f0)
    crashed = _null_session_run(1, session_close(before_f0) - timedelta(hours=1))

    assert lapses(window, [crashed], [], [], [], past, frozen) == [
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

    assert lapses(window, [run], [executed], [], [], past, frozen) == []


def test_lapses_raises_for_an_event_whose_run_is_not_given() -> None:
    from tradepartner.backtest.schedule import fill_session

    t0 = date(2026, 9, 30)
    window = _window(first_rebalance_session=t0)
    f0 = fill_session(t0)
    past = next_session(next_session(f0))
    event = RebalanceEventRow(rebalance_session=t0, run_id=99, status="executed", **_stamp(_at(t0)))

    with pytest.raises(ValueError, match="not among the runs given"):
        lapses(window, [], [event], [], [], past, {"paper.max_catch_up_sessions": 1})


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
        marks = marks_for(conn, _window(), ledger, [d1], {}, actions=NO_ACTIONS)
    finally:
        conn.close()
    assert marks[0].mark_price == 10.0


def test_module_has_no_write_statements() -> None:
    import tradepartner.execution.marks as marks

    text = inspect.getsource(marks)
    for keyword in ("INSERT", "UPDATE", "DELETE"):
        assert not re.search(rf"\b{keyword}\b", text, re.IGNORECASE), keyword
