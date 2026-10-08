"""The wrapper core: the allowlist, the shared clock and its pre-check, the halt
path and the replay (Phase 4 plan T60; spec req 4; ADR 0007 point 7's wrapper
criteria; ADR 0010 points 2 and 4; #375, T58's handoff that the halt read
treats a repeated skew `ClockError` as "halt stands")."""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

import duckdb
import polars as pl
import pytest

from tradepartner import calendar
from tradepartner.adapters.broker import (
    Broker,
    DuplicateClientOrderIdError,
    Order,
    OrderNotOpenError,
    OrderRequest,
    Side,
    UnknownOrderError,
)
from tradepartner.adapters.fake_broker import (
    Expire,
    FakeBroker,
    FakeTransportError,
    FillAt,
    HoldCancel,
    PartialFill,
    Reject,
)
from tradepartner.cli import USAGE_ERROR
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import (
    ClockError,
    LimitBreachError,
    ReconciliationError,
    RejectionCapError,
    SkipCapError,
    StaleDataError,
    SystemFaultError,
)
from tradepartner.execution import alerts, switch, wrapper
from tradepartner.execution.alerts import Alerter
from tradepartner.execution.collect import WriteOffContext, collect
from tradepartner.execution.plan import State, decision_state
from tradepartner.execution.wrapper import (
    ALLOWLISTS,
    CANCEL_ALLOWLIST,
    CRASH_EXIT_CODE,
    SUBMIT_ALLOWLIST,
    WRITE_FAILED_EXIT_CODE,
    RiskGatedBroker,
    Verdict,
    classify,
)
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import (
    DecisionRow,
    OrderEventRow,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    append,
    decisions_for,
    order_events_for,
)
from tradepartner.store.schema import HALT_REASON, LONG

from .test_wrapper_phases import T_I, A, Env, _execute, _gate, _missed, _submits

pytest_plugins = ("execution.test_wrapper_phases",)

FROZEN = RiskConfig()
SESSION = date(2026, 10, 1)
NO_ACTIONS = pl.DataFrame(
    schema={
        "security_id": pl.Utf8,
        "action_type": pl.Utf8,
        "ex_date": pl.Date,
        "ratio_or_amount": pl.Float64,
    }
)


class FixedClock(Protocol):
    """The `fixed_clock` fixture (conftest.py)."""

    now: datetime

    def __call__(self) -> datetime: ...

    def advance(self, **delta: float) -> datetime: ...


class LocalFault(Exception):
    """A type nobody classified: it must halt like any other."""


# --- journal helpers --------------------------------------------------------------


def _append(settings: Settings, *rows: object) -> list[int | None]:
    with open_for_write(settings) as conn:
        return [append(conn, row) for row in rows]  # type: ignore[arg-type]


def _run(settings: Settings, window: PaperWindowRow, at: datetime) -> PaperRunRow:
    row = PaperRunRow(
        window_id=window.window_id,  # type: ignore[arg-type]
        session=SESSION,
        kind="rebalance",
        started_at=at,
        invoked_by="scheduler",
        code_version="test",
        known_at=at,
        ingested_at=at,
    )
    (run_id,) = _append(settings, row)
    return replace(row, run_id=run_id)


def _order(
    settings: Settings,
    fake: FakeBroker,
    clock: FixedClock,
    run: PaperRunRow,
    coid: str,
    *,
    side: str = "buy",
    symbol: str = "AAA",
    acknowledge: bool = True,
    submit: bool = True,
) -> OrderRow:
    """A decision, its order row and `pending` event, the fake's submit, and
    (unless `acknowledge=False`) the `accepted` event: what the wrapper leaves."""
    at = clock()
    notional = 1000.0
    (decision_id,) = _append(
        settings,
        DecisionRow(
            run_id=run.run_id,  # type: ignore[arg-type]
            rebalance_session=date(2026, 9, 30),
            security_id=f"SEC-{symbol}",
            side=side,
            planned_notional=notional,
            target_notional=notional if side == "buy" else None,
            whole_share=False,
            decision="trade",
            known_at=at,
            ingested_at=at,
        ),
    )
    assert decision_id is not None
    row = OrderRow(
        client_order_id=coid,
        decision_id=decision_id,
        run_id=run.run_id,  # type: ignore[arg-type]
        session=SESSION,
        attempt=1,
        phase=side,
        security_id=f"SEC-{symbol}",
        symbol=symbol,
        side=side,
        notional=notional,
        sells_in_flight_at_submit=False,
        known_at=at,
        ingested_at=at,
    )
    events: list[object] = [
        row,
        OrderEventRow(client_order_id=coid, status="pending", known_at=at, ingested_at=at),
    ]
    if submit:
        order = fake.submit(
            OrderRequest(client_order_id=coid, symbol=symbol, side=Side(side), notional=notional)
        )
        if acknowledge:
            events.append(
                OrderEventRow(
                    client_order_id=coid,
                    event_at=order.submitted_at,
                    status="accepted",
                    broker_order_id=order.broker_order_id,
                    known_at=at,
                    ingested_at=at,
                )
            )
    _append(settings, *events)
    return row


def _events(settings: Settings, coid: str) -> list[tuple[str, str | None]]:
    """`coid`'s events as (status, reason), in write order."""
    with open_for_write(settings) as conn:
        rows = conn.execute(
            "SELECT status, reason FROM order_events WHERE client_order_id = ? ORDER BY rowid",
            [coid],
        ).fetchall()
    return [(status, reason) for status, reason in rows]


def _query(settings: Settings, sql: str, params: list[object] | None = None) -> list[Any]:
    with open_for_write(settings) as conn:
        return conn.execute(sql, params or []).fetchall()


def _halt_trace(settings: Settings, run: PaperRunRow) -> list[str]:
    """Every row the halt path writes for `run`, ordered by `known_at`, which a
    ticking clock (or `utc_now()` after a `ClockError`) makes strict: the
    `engaged` row, the order events after each order's `pending` and
    `accepted`, the alert and the result row."""
    rows = _query(
        settings,
        "SELECT 'engaged', known_at FROM kill_switch WHERE run_id = ? "
        "UNION ALL SELECT status, known_at FROM order_events "
        "WHERE status NOT IN ('pending', 'accepted') "
        "UNION ALL SELECT 'alert:' || kind, known_at FROM alerts WHERE run_id = ? "
        "UNION ALL SELECT 'result:' || status, known_at FROM paper_run_results "
        "WHERE run_id = ? ORDER BY 2",
        [run.run_id, run.run_id, run.run_id],
    )
    return [name for name, _ in rows]


class TickingClock:
    """A clock one second later at every reading, so write order shows in
    `known_at`; shared by the wrapper and the fake, as in production."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def alerter_conn(journal_settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(journal_settings.store.path)
    try:
        yield conn
    finally:
        conn.close()


def _wrapper(
    settings: Settings,
    fake: FakeBroker,
    clock: Callable[[], datetime],
    conn: duckdb.DuckDBPyConnection,
    *,
    alerter: Alerter | None = None,
) -> RiskGatedBroker:
    return RiskGatedBroker(
        fake,
        clock,
        FROZEN,
        settings,
        lambda: open_for_write(settings),
        calendar,
        alerter or Alerter(settings, conn, clock),
    )


def _halt(gate: RiskGatedBroker, fault: Exception, run: PaperRunRow, **kwargs: Any) -> None:
    """Raise `fault` and take the halt path from its `except`, as the run does."""
    try:
        raise fault
    except Exception as exc:
        gate.halt(exc, run, **kwargs)


# --- the allowlist ------------------------------------------------------------------


def test_the_allowlist_constants_are_pinned() -> None:
    """ADR 0007 point 1, ADR 0010 point 4: submit empty, cancel exactly
    OrderNotOpenError, every other method empty; one constant per method."""
    assert SUBMIT_ALLOWLIST == ()
    assert (OrderNotOpenError,) == CANCEL_ALLOWLIST
    abstract = {
        name
        for name, member in inspect.getmembers(Broker)
        if getattr(member, "__isabstractmethod__", False)
    }
    assert set(ALLOWLISTS) == abstract
    for method, allowed in ALLOWLISTS.items():
        assert allowed == (CANCEL_ALLOWLIST if method == "cancel" else ())
    for name in (
        "GET_ORDER_ALLOWLIST",
        "OPEN_ORDERS_ALLOWLIST",
        "FILLS_ALLOWLIST",
        "POSITIONS_ALLOWLIST",
        "ACCOUNT_ALLOWLIST",
        "ASSETS_ALLOWLIST",
    ):
        assert getattr(wrapper, name) == ()
    with pytest.raises(TypeError):
        ALLOWLISTS["submit"] = (ValueError,)  # type: ignore[index]


class _AllowedButSystemFault(SystemFaultError, OrderNotOpenError):
    """A system fault that is also on cancel's allowlist: the fault wins."""


@pytest.mark.parametrize(
    ("method", "exc", "verdict"),
    [
        ("cancel", OrderNotOpenError("done"), Verdict.ALLOWED),
        ("cancel", UnknownOrderError("x"), Verdict.HALT),
        ("cancel", _AllowedButSystemFault("x"), Verdict.HALT),
        ("submit", OrderNotOpenError("x"), Verdict.HALT),
        ("submit", ValueError("x"), Verdict.HALT),
        ("submit", ClockError("x"), Verdict.HALT),
        ("submit", FakeTransportError("x"), Verdict.HALT),
        ("submit", DuplicateClientOrderIdError("x"), Verdict.HALT),
        ("get_order", LocalFault("x"), Verdict.HALT),
        ("fills", StaleDataError("x"), Verdict.HALT),
        ("account", KeyboardInterrupt(), Verdict.PROPAGATE),
        ("submit", SystemExit(1), Verdict.PROPAGATE),
    ],
)
def test_classify(method: str, exc: BaseException, verdict: Verdict) -> None:
    assert classify(method, exc) is verdict


def test_classify_refuses_a_method_the_broker_lacks() -> None:
    with pytest.raises(ValueError, match="close_position"):
        classify("close_position", ValueError())


# --- the shared clock and the pre-check ------------------------------------------------


def test_the_wrapper_and_the_adapter_hold_one_clock(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    assert scripted_fake.clock is fixed_clock
    assert gate.clock_precheck(SESSION, None) == fixed_clock()
    other = _wrapper(journal_settings, scripted_fake, lambda: fixed_clock(), alerter_conn)
    with pytest.raises(ClockError, match="one clock"):
        other.clock_precheck(SESSION, None)


def _precheck_at(
    settings: Settings, conn: duckdb.DuckDBPyConnection, now: object, last_ok: datetime | None
) -> datetime:
    clock = lambda: now  # noqa: E731
    fake = FakeBroker(clock=clock, price_of=lambda _s: 100.0, auto_fill=False)  # type: ignore[arg-type]
    gate = _wrapper(settings, fake, clock, conn)  # type: ignore[arg-type]
    return gate.clock_precheck(SESSION, last_ok)


def test_the_precheck_bounds(
    journal_settings: Settings, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """Not earlier than the last ok ingest's finish; not later than close(S)
    plus `risk.clock_max_sessions_late` sessions (1: close of S+1); both
    bounds inclusive."""
    assert FROZEN.clock_max_sessions_late == 1
    bound = calendar.session_close(calendar.next_session(SESSION))
    ingest = datetime(2026, 10, 1, 1, 0, tzinfo=UTC)
    check = lambda now, last: _precheck_at(journal_settings, alerter_conn, now, last)  # noqa: E731
    assert check(ingest, ingest) == ingest
    assert check(bound, ingest) == bound
    with pytest.raises(ClockError, match="last ok ingestion"):
        check(ingest - timedelta(microseconds=1), ingest)
    with pytest.raises(ClockError, match="later than"):
        check(bound + timedelta(microseconds=1), ingest)


@pytest.mark.parametrize(
    "reading",
    [datetime(2026, 10, 1, 14, 0), "2026-10-01T14:00:00Z", None],  # noqa: DTZ001
    ids=["naive", "string", "none"],
)
def test_the_precheck_refuses_a_malformed_reading(
    journal_settings: Settings, alerter_conn: duckdb.DuckDBPyConnection, reading: object
) -> None:
    with pytest.raises(ClockError):
        _precheck_at(journal_settings, alerter_conn, reading, None)


def test_a_raising_clock_is_a_clock_error(
    journal_settings: Settings, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    def broken() -> datetime:
        raise OSError("no time")

    fake = FakeBroker(clock=broken, price_of=lambda _s: 100.0, auto_fill=False)
    gate = _wrapper(journal_settings, fake, broken, alerter_conn)
    with pytest.raises(ClockError, match="OSError"):
        gate.clock_precheck(SESSION, None)


def test_every_later_reading_is_monotonic(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    first = gate.clock_precheck(SESSION, None)
    assert gate.read_clock() == first  # equal is not backwards
    fixed_clock.advance(seconds=5)
    assert gate.read_clock() == first + timedelta(seconds=5)
    fixed_clock.advance(seconds=-1)
    with pytest.raises(ClockError, match="went back"):
        gate.read_clock()


# --- the halt path ---------------------------------------------------------------------


@pytest.fixture
def ticking() -> TickingClock:
    """Starts well before the wall clock, so after a `ClockError` the halt's
    `utc_now()` stamps never make a fill look ahead (the skew check)."""
    return TickingClock(datetime(2025, 6, 2, 14, 0, tzinfo=UTC))


@pytest.fixture
def ticking_fake(ticking: TickingClock) -> FakeBroker:
    return FakeBroker(clock=ticking, price_of=lambda _s: 100.0, auto_fill=False, account_id="PA1")


@pytest.mark.parametrize(
    "fault",
    [
        ClockError("clock went wrong"),
        ValueError("bad request"),
        FakeTransportError("connection reset"),
        LocalFault("nobody classified this"),
    ],
    ids=["clock", "value", "transport", "local-type"],
)
def test_each_fault_halts_in_the_req_4_order(
    journal_settings: Settings,
    ticking_fake: FakeBroker,
    ticking: TickingClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
    fault: Exception,
) -> None:
    run = _run(journal_settings, open_window, ticking())
    _order(journal_settings, ticking_fake, ticking, run, "tp-open")
    _order(journal_settings, ticking_fake, ticking, run, "tp-done")
    ticking_fake.apply("tp-done", FillAt(price=50.0))
    gate = _wrapper(journal_settings, ticking_fake, ticking, alerter_conn)

    with pytest.raises(type(fault)) as raised:
        _halt(gate, fault, run)
    assert raised.value is fault

    clock_fault = isinstance(fault, ClockError)
    [(source, fault_type, reason)] = _query(
        journal_settings,
        "SELECT source, fault_type, reason FROM kill_switch WHERE run_id = ?",
        [run.run_id],
    )
    assert (source, fault_type) == ("fault", type(fault).__name__)
    assert str(fault) in reason
    assert _events(journal_settings, "tp-open")[2:] == [
        ("cancel_requested", HALT_REASON),
        ("cancelled", None),
    ]
    assert _events(journal_settings, "tp-done")[2:] == [
        ("cancel_requested", HALT_REASON),
        ("cancel_noop", HALT_REASON),
        ("filled", None),
    ]
    [(status, result_type, message, flagged)] = _query(
        journal_settings,
        "SELECT status, fault_type, message, clock_fault FROM paper_run_results WHERE run_id = ?",
        [run.run_id],
    )
    assert (status, result_type, flagged) == ("halted", type(fault).__name__, clock_fault)
    assert str(fault) in message
    assert _halt_trace(journal_settings, run) == [
        "engaged",
        "cancel_requested",  # tp-open, before its cancel call
        "cancel_requested",  # tp-done, before its cancel call
        "cancel_noop",  # tp-done had already filled
        "cancelled",  # the reads: tp-open's terminal event
        "filled",  # tp-done's
        "alert:halted",
        "result:halted",
    ]


def test_the_alert_row_exists_before_the_fault_propagates(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    run = _run(journal_settings, open_window, fixed_clock())
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    try:
        _halt(gate, LimitBreachError("per-order notional"), run)
    except LimitBreachError:
        seen = _query(
            journal_settings, "SELECT kind, message FROM alerts WHERE run_id = ?", [run.run_id]
        )
        results = _query(
            journal_settings, "SELECT status FROM paper_run_results WHERE run_id = ?", [run.run_id]
        )
    assert [kind for kind, _ in seen] == ["halted"]
    assert "per-order notional" in seen[0][1]
    assert results == [("halted",)]


@pytest.mark.parametrize(
    "fault, kind",
    [
        (ReconciliationError("local and broker state disagree"), "reconciliation"),
        (RejectionCapError("too many rejections"), "rejection_cap"),
        (SkipCapError("too many skips"), "skip_cap"),
        (StaleDataError("prices end at S-2"), "stale_data"),
        (LimitBreachError("per-order notional"), "halted"),
        (ValueError("bad request"), "halted"),
    ],
    ids=["reconciliation", "rejection_cap", "skip_cap", "stale_data", "limit_breach", "value"],
)
def test_halt_writes_one_alert_with_the_fault_specific_kind(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
    fault: Exception,
    kind: str,
) -> None:
    """Owner decision 2026-10-03, #644, Option A: `halt` writes exactly one
    alert per halt, with the fault-specific kind where `_FAULT_ALERT_KINDS`
    maps one, and `fault_type` still named in its message. `StaleDataError`
    and any other fault keep their existing, unmapped kinds."""
    run = _run(journal_settings, open_window, fixed_clock())
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(type(fault)):
        _halt(gate, fault, run)
    seen = _query(
        journal_settings, "SELECT kind, message FROM alerts WHERE run_id = ?", [run.run_id]
    )
    assert [row[0] for row in seen] == [kind]
    assert type(fault).__name__ in seen[0][1]


def test_the_halt_cancels_only_acknowledged_orders_and_never_a_pending_one(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    run = _run(journal_settings, open_window, fixed_clock())
    other = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-acked")
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-pending", acknowledge=False)
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-unsent", submit=False)
    _order(journal_settings, scripted_fake, fixed_clock, other, "tp-other-run", symbol="BBB")
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)

    with pytest.raises(ValueError):
        _halt(gate, ValueError("x"), run)

    touched = {c.args[0] for c in scripted_fake.calls if c.method in ("cancel", "get_order")}
    assert touched == {"tp-acked"}
    for untouched in ("tp-pending", "tp-unsent", "tp-other-run"):
        assert all(not s.startswith("cancel") for s, _ in _events(journal_settings, untouched))
    assert _events(journal_settings, "tp-acked")[2:] == [
        ("cancel_requested", HALT_REASON),
        ("cancelled", None),
    ]


def test_a_failed_cancel_and_a_failed_read_each_journal_cancel_failed(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-a")

    def refuse(_coid: str) -> None:
        raise FakeTransportError("cancel lost")

    def unreadable(_coid: str) -> None:
        raise FakeTransportError("read lost")

    monkeypatch.setattr(scripted_fake, "cancel", refuse)
    monkeypatch.setattr(scripted_fake, "get_order", unreadable)
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(ValueError):
        _halt(gate, ValueError("x"), run)

    assert _events(journal_settings, "tp-a")[2:] == [
        ("cancel_requested", HALT_REASON),
        ("cancel_failed", HALT_REASON),
        ("cancel_failed", HALT_REASON),
    ]
    [(message,)] = _query(
        journal_settings, "SELECT message FROM paper_run_results WHERE run_id = ?", [run.run_id]
    )
    assert "cancel of tp-a failed" in message
    assert "halt read of tp-a failed (FakeTransportError: read lost)" in message  # #730


def test_a_rejection_verdict_the_halt_read_finds_is_named_in_the_alert(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    """T58's handoff: a halt-path caller never drops a rejection verdict."""
    run = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-a")
    scripted_fake.apply("tp-a", Reject())
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(ValueError):
        _halt(gate, ValueError("x"), run)
    assert _events(journal_settings, "tp-a")[2:] == [
        ("cancel_requested", HALT_REASON),
        ("cancel_noop", HALT_REASON),
        ("rejected", None),
    ]
    [(alert,)] = _query(
        journal_settings, "SELECT message FROM alerts WHERE run_id = ?", [run.run_id]
    )
    assert f"run {run.run_id}" in alert and "rejected" in alert


def test_a_held_cancel_that_fills_stays_in_flight_until_a_later_collection(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    run = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-held")
    scripted_fake.apply("tp-held", HoldCancel(fill=PartialFill(4.0, 100.0)))
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(ValueError):
        _halt(gate, ValueError("x"), run)

    assert _events(journal_settings, "tp-held")[2:] == [("cancel_requested", HALT_REASON)]
    assert _query(journal_settings, "SELECT quantity FROM fills") == [(4.0,)]

    scripted_fake.complete_cancel("tp-held")
    fixed_clock.advance(minutes=5)
    with open_for_write(journal_settings) as conn:
        [order] = [o for o in _orders(conn) if o.client_order_id == "tp-held"]
    later = _run(journal_settings, open_window, fixed_clock())
    collect(
        scripted_fake,
        lambda: open_for_write(journal_settings),
        [order],
        fixed_clock,
        "run",
        later.run_id,  # type: ignore[arg-type]
        FROZEN,
        journal_settings,
    )
    assert _events(journal_settings, "tp-held")[-1] == ("cancelled", None)


def _orders(conn: duckdb.DuckDBPyConnection) -> list[OrderRow]:
    from tradepartner.store.journal import orders_for

    return orders_for(conn, window_id=None)


def test_a_terminal_buy_read_by_the_halt_gets_its_written_off_row(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    """A buy that went terminal short of its target with no sell in flight (its
    terminal event journaled by an earlier collection that wrote no write-off)
    gets its `written_off` row from the halt's read, which back-fills it."""
    run = _run(journal_settings, open_window, fixed_clock())
    short = _order(journal_settings, scripted_fake, fixed_clock, run, "tp-short")
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-open", symbol="BBB")
    scripted_fake.apply("tp-short", PartialFill(2.0, 100.0))
    scripted_fake.apply("tp-short", Expire())
    collect(
        scripted_fake,
        lambda: open_for_write(journal_settings),
        [short],
        fixed_clock,
        "run",
        run.run_id,  # type: ignore[arg-type]
        FROZEN,
        journal_settings,
    )
    assert _events(journal_settings, "tp-short")[-1] == ("expired", None)
    context = WriteOffContext(
        window_id=open_window.window_id,  # type: ignore[arg-type]
        actions_as_of=NO_ACTIONS,
        price_of=lambda _security_id: 100.0,
        session=SESSION,
    )
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(ValueError):
        _halt(gate, ValueError("x"), run, write_offs=context)

    with open_for_write(journal_settings) as conn:
        rows = decisions_for(conn, open_window.window_id)  # type: ignore[arg-type]
        decisions = {d.decision.security_id: d for d in rows}
    assert [e.status for e in decisions["SEC-AAA"].events] == ["written_off"]
    assert decisions["SEC-BBB"].events == ()


def test_a_halted_buy_stays_open_for_decision_state(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    """The reasons the halt writes are the ones `plan.decision_state` reads: a buy
    cancelled by the halt short of its target, submitted with no sell in flight,
    stays open instead of being written off as unfunded."""
    run = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-buy")
    scripted_fake.apply("tp-buy", PartialFill(2.0, 100.0))
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(ValueError):
        _halt(gate, ValueError("x"), run)

    with open_for_write(journal_settings) as conn:
        [decision] = decisions_for(conn, open_window.window_id)  # type: ignore[arg-type]
        orders = _orders(conn)
        events = order_events_for(conn, window_id=None)
        from tradepartner.store.journal import fills_for

        fills = fills_for(conn, client_order_ids=["tp-buy"])
    assert _events(journal_settings, "tp-buy")[-1] == ("cancelled", None)
    state = decision_state(
        decision.decision,
        decision.events,
        orders,
        events,
        fills,
        NO_ACTIONS,
        lambda _security_id: 100.0,
        FROZEN,
        session=SESSION,
    )
    assert state.state is State.OPEN
    assert not state.written_off


def test_a_clock_that_keeps_raising_still_gets_the_engaged_row_first(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    run = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-a")
    readings = 0

    def broken() -> datetime:
        nonlocal readings
        readings += 1
        raise OSError("clock gone")

    alerter = Alerter(journal_settings, alerter_conn, broken)
    gate = _wrapper(journal_settings, scripted_fake, broken, alerter_conn, alerter=alerter)
    before = datetime.now(UTC)
    with pytest.raises(ClockError):
        _halt(gate, ClockError("clock gone"), run)
    after = datetime.now(UTC)

    assert readings == 0  # never read again after the ClockError
    stamps = _query(
        journal_settings,
        "SELECT known_at FROM kill_switch WHERE run_id = ? "
        "UNION ALL SELECT known_at FROM order_events WHERE status LIKE 'cancel%' "
        "UNION ALL SELECT known_at FROM alerts WHERE run_id = ? "
        "UNION ALL SELECT known_at FROM paper_run_results WHERE run_id = ?",
        [run.run_id, run.run_id, run.run_id],
    )
    assert len(stamps) == 5  # engaged, cancel_requested, cancelled, alert, result
    assert all(before <= stamp <= after for (stamp,) in stamps)
    assert _query(
        journal_settings,
        "SELECT clock_fault FROM paper_run_results WHERE run_id = ?",
        [run.run_id],
    ) == [(True,)]
    [(engaged_at,)] = _query(
        journal_settings, "SELECT known_at FROM kill_switch WHERE run_id = ?", [run.run_id]
    )
    assert engaged_at == min(stamp for (stamp,) in stamps)


def test_a_clock_that_breaks_during_another_halt_switches_to_utc_now(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    run = _run(journal_settings, open_window, fixed_clock())
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    gate.read_clock()
    fixed_clock.advance(seconds=-30)  # the next reading goes back
    with pytest.raises(ValueError):
        _halt(gate, ValueError("x"), run)
    assert _query(
        journal_settings, "SELECT clock_fault FROM paper_run_results WHERE run_id = ?", [run.run_id]
    ) == [(True,)]
    assert _query(
        journal_settings, "SELECT COUNT(*) FROM kill_switch WHERE run_id = ?", [run.run_id]
    ) == [(1,)]


class SpyAlerter(Alerter):
    """Records the non-store deliveries."""

    sent: list[tuple[str, str]]

    def deliver_without_store(self, kind: str, message: str) -> list[Any]:
        self.sent.append((kind, message))
        return []


def test_the_write_failure_path_alerts_outside_the_store_and_exits_non_zero(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-a")
    monkeypatch.setattr(
        switch, "engage", lambda *a, **k: switch.WriteFailed("StoreLockedError: locked")
    )
    spy = SpyAlerter(journal_settings, alerter_conn, fixed_clock)
    spy.sent = []
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn, alerter=spy)

    with pytest.raises(SystemExit) as exited:
        _halt(gate, ValueError("boom"), run)

    assert exited.value.code == WRITE_FAILED_EXIT_CODE != 0
    # #515 (ii): distinct from a crash's exit code and from the CLI's usage-error
    # code, so launchd/the runbook can tell a failed halt write apart from either.
    assert WRITE_FAILED_EXIT_CODE not in (CRASH_EXIT_CODE, USAGE_ERROR)
    assert isinstance(exited.value.__cause__, ValueError)
    [(kind, message)] = spy.sent
    assert kind == "kill_switch_write_failed"
    assert "boom" in message and "StoreLockedError" in message
    assert not [c for c in scripted_fake.calls if c.method == "cancel"]
    assert _query(journal_settings, "SELECT COUNT(*) FROM alerts") == [(0,)]
    assert _query(journal_settings, "SELECT COUNT(*) FROM paper_run_results") == [(0,)]


def test_stale_data_halts_and_alerts_without_engaging_the_switch(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    run = _run(journal_settings, open_window, fixed_clock())
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(StaleDataError):
        _halt(gate, StaleDataError("prices end at S-2"), run)
    assert _query(journal_settings, "SELECT COUNT(*) FROM kill_switch") == [(0,)]
    assert _query(journal_settings, "SELECT kind FROM alerts WHERE run_id = ?", [run.run_id]) == [
        ("stale_data",)
    ]
    assert _query(
        journal_settings,
        "SELECT status, fault_type FROM paper_run_results WHERE run_id = ?",
        [run.run_id],
    ) == [("stale", "StaleDataError")]


def test_a_repeated_skew_clock_error_in_the_halt_read_leaves_the_halt_standing(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#375 (T58's handoff, #340 finding 8): a broker fill stamped beyond
    `risk.max_broker_clock_skew_seconds` makes every halt read raise
    `ClockError` again. The halt stands: the switch stays engaged, the original
    fault is re-raised, and the alert and result row name the skew.

    After the first skew the halt stamps by `store.db.utc_now()` (by design), so
    the wall clock is pinned to the test's start: the fill stays ahead of it
    whatever the real date (#462: unpinned, this test failed from the moment the
    real time passed the fixture's `CLOCK_START`)."""
    pinned = fixed_clock.now
    monkeypatch.setattr(wrapper, "utc_now", lambda: pinned)
    monkeypatch.setattr(alerts, "utc_now", lambda: pinned)
    run = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-a")
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-b", symbol="BBB")
    start = fixed_clock.now
    fixed_clock.now = start + timedelta(seconds=FROZEN.max_broker_clock_skew_seconds + 60)
    scripted_fake.apply("tp-a", PartialFill(1.0, 100.0))  # filled_at in the clock's future
    fixed_clock.now = start
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)

    with pytest.raises(LocalFault):
        _halt(gate, LocalFault("first fault"), run)

    assert _query(
        journal_settings, "SELECT state FROM kill_switch WHERE run_id = ?", [run.run_id]
    ) == [("engaged",)]
    [(status, fault_type, message, clock_fault)] = _query(
        journal_settings,
        "SELECT status, fault_type, message, clock_fault FROM paper_run_results WHERE run_id = ?",
        [run.run_id],
    )
    assert (status, fault_type, clock_fault) == ("halted", "LocalFault", True)
    assert "first fault" in message and "ClockError" in message
    assert message.count("halt read stands on ClockError") == 1  # named once, not per read
    [(alert,)] = _query(
        journal_settings, "SELECT message FROM alerts WHERE run_id = ?", [run.run_id]
    )
    assert "ClockError" in alert
    assert _query(journal_settings, "SELECT COUNT(*) FROM fills") == [(0,)]
    assert _query(
        journal_settings, "SELECT known_at FROM paper_run_results WHERE run_id = ?", [run.run_id]
    ) == [(pinned,)]  # stamped by the (pinned) utc_now() after the skew
    for coid in ("tp-a", "tp-b"):
        assert ("cancel_requested", HALT_REASON) in _events(journal_settings, coid)


# --- the replay ------------------------------------------------------------------------


def _request(coid: str = "tp-r", notional: float = 1000.0, symbol: str = "AAA") -> OrderRequest:
    return OrderRequest(client_order_id=coid, symbol=symbol, side=Side.BUY, notional=notional)


def test_an_identical_duplicate_is_adopted_as_a_replay(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    run = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-r", acknowledge=False)
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(DuplicateClientOrderIdError) as duplicate:
        scripted_fake.submit(_request())
    order = gate.replay(_request(), duplicate.value)
    assert order.client_order_id == "tp-r"
    [(broker_order_id,)] = _query(
        journal_settings,
        "SELECT broker_order_id FROM order_events "
        "WHERE client_order_id = 'tp-r' AND status = 'replay'",
    )
    assert broker_order_id == order.broker_order_id is not None
    with open_for_write(journal_settings) as conn:
        from tradepartner.store.journal import pending_orders

        assert pending_orders(conn, window_id=None) == []  # a replay acknowledges


@pytest.mark.parametrize(
    "request_",
    [_request(notional=999.0), _request(symbol="BBB")],
    ids=["notional", "symbol"],
)
def test_a_differing_duplicate_halts(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    alerter_conn: duckdb.DuckDBPyConnection,
    request_: OrderRequest,
) -> None:
    scripted_fake.submit(_request())
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(SystemFaultError, match="differs"):
        gate.replay(request_, DuplicateClientOrderIdError("tp-r"))
    assert classify("submit", SystemFaultError()) is Verdict.HALT
    assert _query(journal_settings, "SELECT COUNT(*) FROM order_events") == [(0,)]


def test_a_failed_fetch_on_a_duplicate_halts(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(SystemFaultError, match="get_order failed") as raised:
        gate.replay(_request(), DuplicateClientOrderIdError("tp-r"))
    assert isinstance(raised.value.__cause__, UnknownOrderError)


# --- store faults on the halt path (safety review on #437) ---------------------------


class FlakyJournal:
    """A `Connect` whose calls numbered in `failing` raise, the others open the
    store; `calls` counts every call."""

    def __init__(self, settings: Settings, failing: set[int]) -> None:
        self._settings = settings
        self._failing = failing
        self.calls = 0

    def __call__(self) -> Any:
        self.calls += 1
        if self.calls in self._failing:
            raise OSError("store unavailable")
        return open_for_write(self._settings)


def test_a_store_fault_after_the_engaged_row_never_cuts_the_halt_short(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Call 1 reads the open orders, call 2 is tp-a's `cancel_requested` (it
    fails, so tp-a is never cancelled: no call without its row), and tp-b goes
    on through its cancel and read. The alert and result row still land, and
    the original fault is re-raised carrying the failure as a note."""
    run = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-a")
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-b", symbol="BBB")
    gate = RiskGatedBroker(
        scripted_fake,
        fixed_clock,
        FROZEN,
        journal_settings,
        FlakyJournal(journal_settings, failing={2}),
        calendar,
        Alerter(journal_settings, alerter_conn, fixed_clock),
    )
    fault = ValueError("original")
    with pytest.raises(ValueError) as raised:
        _halt(gate, fault, run)

    assert raised.value is fault
    assert any("cancel of tp-a not attempted" in note for note in fault.__notes__)
    cancels = [c.args[0] for c in scripted_fake.calls if c.method == "cancel"]
    assert cancels == ["tp-b"]
    assert _events(journal_settings, "tp-a")[2:] == []
    assert _events(journal_settings, "tp-b")[2:] == [
        ("cancel_requested", HALT_REASON),
        ("cancelled", None),
    ]
    [(alert,)] = _query(
        journal_settings, "SELECT message FROM alerts WHERE run_id = ?", [run.run_id]
    )
    assert "cancel of tp-a not attempted" in alert
    assert _query(
        journal_settings, "SELECT status FROM paper_run_results WHERE run_id = ?", [run.run_id]
    ) == [("halted",)]


def test_a_store_that_fails_after_the_engaged_row_still_re_raises_the_original_fault(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    run = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, scripted_fake, fixed_clock, run, "tp-a")
    gate = RiskGatedBroker(
        scripted_fake,
        fixed_clock,
        FROZEN,
        journal_settings,
        FlakyJournal(journal_settings, failing=set(range(1, 100))),
        calendar,
        Alerter(journal_settings, alerter_conn, fixed_clock),
    )
    fault = LocalFault("original")
    with pytest.raises(LocalFault) as raised:
        _halt(gate, fault, run)
    assert raised.value is fault
    [note] = fault.__notes__
    assert "open orders not read" in note and "result row not written" in note
    assert _query(
        journal_settings, "SELECT state FROM kill_switch WHERE run_id = ?", [run.run_id]
    ) == [("engaged",)]
    assert not [c for c in scripted_fake.calls if c.method == "cancel"]


class BrokenAlerter(Alerter):
    def write(self, *args: Any, **kwargs: Any) -> int:
        raise OSError("alerts table locked")


def test_a_failed_alert_write_is_named_in_the_result_row_and_on_the_fault(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    run = _run(journal_settings, open_window, fixed_clock())
    alerter = BrokenAlerter(journal_settings, alerter_conn, fixed_clock)
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn, alerter=alerter)
    fault = ValueError("original")
    with pytest.raises(ValueError):
        _halt(gate, fault, run)
    [(message,)] = _query(
        journal_settings, "SELECT message FROM paper_run_results WHERE run_id = ?", [run.run_id]
    )
    assert "alert not written (OSError)" in message
    assert any("alert not written" in note for note in fault.__notes__)


def test_the_precheck_refuses_a_naive_last_ok_ingest(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(ValueError, match="last_ok_ingest"):
        gate.clock_precheck(SESSION, datetime(2026, 10, 1, 1, 0))  # noqa: DTZ001


def test_a_clock_error_from_the_replay_fetch_keeps_its_type(
    journal_settings: Settings,
    scripted_fake: FakeBroker,
    fixed_clock: FixedClock,
    alerter_conn: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def clock_broken(_coid: str) -> None:
        raise ClockError("adapter clock failed")

    monkeypatch.setattr(scripted_fake, "get_order", clock_broken)
    gate = _wrapper(journal_settings, scripted_fake, fixed_clock, alerter_conn)
    with pytest.raises(ClockError, match="adapter clock"):
        gate.replay(_request(), DuplicateClientOrderIdError("tp-r"))


# --- the position side (ADR 0015 seam 2, plan T134) ----------------------------------------


def test_a_short_decision_halts_the_batch_before_any_submit(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """A `decisions` row with `position_side = 'short'` (which nothing in `src/`
    writes: the row is the journal's, however it got there) halts the batch
    with `LimitBreachError` naming `refused_position_side` before any submit,
    through the batch's `missed` row; the run's halt path then writes the
    `kill_switch` row. The batch's long buy beside it is not submitted either:
    the batch halts whole, as for every structural rule."""
    at = env.run.started_at
    rows = []
    for security_id, side in ((A, "short"), ("SEC_DUAL_B", LONG)):
        row = DecisionRow(
            run_id=env.run.run_id,  # type: ignore[arg-type]
            rebalance_session=T_I,
            security_id=security_id,
            target_weight=0.04,
            side="buy",
            planned_notional=3000.0,
            target_notional=3000.0,
            whole_share=False,
            decision="trade",
            position_side=side,
            known_at=at,
            ingested_at=at,
        )
        (decision_id,) = _append(env.settings, row)
        rows.append(replace(row, decision_id=decision_id))
    short_id = rows[0].decision_id
    gate = _gate(env, alerter_conn)

    with pytest.raises(LimitBreachError, match="refused_position_side") as raised:
        _execute(gate, env, rows)
    message = str(raised.value)
    # Both the journaled decision and the order `to_risk` built from it are named.
    assert f"order of decision {short_id} " in message
    assert f"refused_position_side: decision {short_id} " in message
    assert "SEC_DUAL_B" not in message
    assert _submits(env.fake) == []
    orders_sql = "SELECT COUNT(*) FROM orders WHERE run_id = ?"
    assert _query(env.settings, orders_sql, [env.run.run_id]) == [(0,)]
    assert _missed(env.settings) == [("missed", "limit_breach")]

    with pytest.raises(LimitBreachError):
        _halt(gate, raised.value, env.run)
    [(source, fault_type, reason)] = _query(
        env.settings,
        "SELECT source, fault_type, reason FROM kill_switch WHERE run_id = ?",
        [env.run.run_id],
    )
    assert (source, fault_type) == ("fault", "LimitBreachError")
    assert "refused_position_side" in reason
    assert _submits(env.fake) == []


# --- the order shape (ADR 0015 seam 3, plan T135b) ------------------------------------------


class ShapedBroker(FakeBroker):
    """A `Broker` stub that returns, from the method named `shaped`, the fake's
    order as a limit order, which `FakeBroker` itself never does; its book
    stays the fake's market order."""

    def __init__(self, *, shaped: str | None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.shaped = shaped

    def _shape(self, method: str, order: Order) -> Order:
        if method != self.shaped:
            return order
        return replace(order, order_type="limit", limit_price=99.5)

    def submit(self, request: OrderRequest) -> Order:
        return self._shape("submit", super().submit(request))

    def get_order(self, client_order_id: str) -> Order:
        return self._shape("get_order", super().get_order(client_order_id))


def _shaped_env(env: Env, shaped: str | None) -> ShapedBroker:
    env.fake = ShapedBroker(
        shaped=shaped,
        clock=env.clock,
        price_of=lambda symbol: env.prices[symbol],
        auto_fill=False,
        account_id="PA1",
    )
    return env.fake


def _two_buys(env: Env) -> list[DecisionRow]:
    at = env.run.started_at
    rows = []
    for security_id in (A, "SEC_DUAL_B"):
        row = DecisionRow(
            run_id=env.run.run_id,  # type: ignore[arg-type]
            rebalance_session=T_I,
            security_id=security_id,
            target_weight=0.04,
            side="buy",
            planned_notional=3000.0,
            target_notional=3000.0,
            whole_share=False,
            decision="trade",
            known_at=at,
            ingested_at=at,
        )
        (decision_id,) = _append(env.settings, row)
        rows.append(replace(row, decision_id=decision_id))
    return rows


@pytest.mark.parametrize("shaped", ["submit", "get_order"])
def test_a_limit_order_from_the_broker_halts_after_its_event_is_journaled(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, shaped: str
) -> None:
    """The first of two buys comes back from `submit` (or its acknowledgement's
    `get_order`) as a limit order: its `accepted` event is journaled first,
    `raw_json` included, then the batch halts with `SystemFaultError` naming
    `refused_order_shape`, a fault after a submit (no `missed` row, not
    `LimitBreachError`), and the second buy is never submitted. The run's
    halt path writes the `kill_switch` row with that reason and cancels the
    limit order."""
    fake = _shaped_env(env, shaped)
    gate = _gate(env, alerter_conn)

    with pytest.raises(SystemFaultError, match="refused_order_shape") as raised:
        _execute(gate, env, _two_buys(env))
    assert type(raised.value) is SystemFaultError
    assert f"{shaped} returned order " in str(raised.value)
    assert "order_type 'limit', limit_price 99.5" in str(raised.value)
    (first,) = _submits(fake)
    coid = first.client_order_id
    [(status, broker_order_id, raw)] = _query(
        env.settings,
        "SELECT status, broker_order_id, raw_json FROM order_events "
        "WHERE client_order_id = ? AND status != 'pending'",
        [coid],
    )
    assert (status, broker_order_id) == ("accepted", fake.get_order(coid).broker_order_id)
    assert json.loads(raw)["order_type"] == ("limit" if shaped == "get_order" else "market")
    assert _missed(env.settings) == []
    pending = "SELECT client_order_id FROM orders WHERE run_id = ? AND client_order_id != ?"
    [(second,)] = _query(env.settings, pending, [env.run.run_id, coid])
    assert _events(env.settings, second) == [("pending", None)]

    with pytest.raises(SystemFaultError):
        _halt(gate, raised.value, env.run)
    [(source, fault_type, reason)] = _query(
        env.settings,
        "SELECT source, fault_type, reason FROM kill_switch WHERE run_id = ?",
        [env.run.run_id],
    )
    assert (source, fault_type) == ("fault", "SystemFaultError")
    assert "refused_order_shape" in reason
    assert _events(env.settings, coid)[2:] == [
        ("cancel_requested", HALT_REASON),
        ("cancelled", None),
    ]
    assert _events(env.settings, second) == [("pending", None)]  # resume settles it
    assert [r.client_order_id for r in _submits(fake)] == [coid]


def test_the_market_day_order_passes_the_shape_check_unchanged(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """The same stub returning the fake's own market day orders: both buys go
    out and the batch ends `ok`."""
    fake = _shaped_env(env, None)
    outcome = _execute(_gate(env, alerter_conn), env, _two_buys(env))
    assert outcome.status == "ok"
    assert len(_submits(fake)) == 2
    assert _query(env.settings, "SELECT COUNT(*) FROM kill_switch") == [(0,)]


def test_a_replayed_limit_order_halts_after_its_replay_event(
    journal_settings: Settings,
    fixed_clock: FixedClock,
    open_window: PaperWindowRow,
    alerter_conn: duckdb.DuckDBPyConnection,
) -> None:
    """A duplicate id whose fetched order matches the request field for field
    but is a limit order: the `replay` event is journaled, then
    `refused_order_shape`."""
    fake = ShapedBroker(
        shaped="get_order",
        clock=fixed_clock,
        price_of=lambda _s: 100.0,
        auto_fill=False,
        account_id="PA1",
    )
    run = _run(journal_settings, open_window, fixed_clock())
    _order(journal_settings, fake, fixed_clock, run, "tp-r", acknowledge=False)
    gate = _wrapper(journal_settings, fake, fixed_clock, alerter_conn)
    with pytest.raises(SystemFaultError, match="refused_order_shape"):
        gate.replay(_request(), DuplicateClientOrderIdError("tp-r"))
    assert _events(journal_settings, "tp-r") == [("pending", None), ("replay", None)]
