"""Tests for the `store.journal` readers (Phase 4 plan T49c).

Each reader on a seeded store; each raises `JournalNotInitialised` on a version-4
store; a per-window read is unchanged when another window's rows are added; the
latest-event reader follows `known_at`, not insertion order; the module text still
has no `UPDATE` or `DELETE` (pinned in `test_journal.py`, re-checked here).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import version_4_store

from tradepartner.store import journal, schema
from tradepartner.store.journal import (
    AdjustmentRow,
    AlertRow,
    DecisionEventRow,
    DecisionRow,
    FillCursorRow,
    FillRow,
    JournalIntegrityError,
    JournalNotInitialised,
    KillSwitchRow,
    OrderEventRow,
    OrderRow,
    OutcomeRow,
    OverrideRow,
    PaperPlanRow,
    PaperReportRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PaperWindowStopRow,
    PositionDailyRow,
    RebalanceEventRow,
    ReconciliationRow,
    ResumeAcceptanceRow,
    ResumeInvocationRow,
    SignalRow,
    append,
)

_NOW = datetime(2026, 10, 1, 21, 0, tzinfo=UTC)
_SESSION = date(2026, 10, 1)
_NEXT = date(2026, 10, 2)


def _at(minutes: int) -> datetime:
    return _NOW + timedelta(minutes=minutes)


def _stamp(minutes: int = 0) -> dict[str, datetime]:
    return {"known_at": _at(minutes), "ingested_at": _at(minutes)}


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    schema.init_schema(c)
    try:
        yield c
    finally:
        c.close()


def _window(conn: duckdb.DuckDBPyConnection, minutes: int = 0) -> int:
    window_id = append(
        conn,
        PaperWindowRow(
            hypothesis_id=1,
            first_rebalance_session=_SESSION,
            account_id="PA1",
            starting_cash=1000.0,
            starting_equity=1000.0,
            code_version="abc",
            started_at=_at(minutes),
            frozen_json="{}",
            frozen_sha256="0" * 64,
            **_stamp(minutes),
        ),
    )
    assert window_id is not None
    return window_id


def _stop(conn: duckdb.DuckDBPyConnection, window_id: int, state: str, minutes: int) -> None:
    append(
        conn,
        PaperWindowStopRow(window_id=window_id, at=_at(minutes), state=state, **_stamp(minutes)),
    )


def _run(
    conn: duckdb.DuckDBPyConnection,
    window_id: int,
    session: date = _SESSION,
    kind: str = "rebalance",
    minutes: int = 0,
) -> int:
    run_id = append(
        conn,
        PaperRunRow(
            window_id=window_id,
            session=session,
            kind=kind,
            started_at=_at(minutes),
            invoked_by="scheduler",
            code_version="abc",
            **_stamp(minutes),
        ),
    )
    assert run_id is not None
    return run_id


def _decision(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    security_id: str = "SEC_A",
    rebalance_session: date | None = _SESSION,
    override_id: int | None = None,
) -> int:
    decision_id = append(
        conn,
        DecisionRow(
            run_id=run_id,
            rebalance_session=rebalance_session,
            security_id=security_id,
            side="buy",
            planned_notional=100.0,
            whole_share=False,
            decision="override" if override_id is not None else "trade",
            override_id=override_id,
            **_stamp(),
        ),
    )
    assert decision_id is not None
    return decision_id


def _order(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    decision_id: int,
    coid: str,
    security_id: str = "SEC_A",
    side: str = "buy",
    session: date = _SESSION,
) -> str:
    append(
        conn,
        OrderRow(
            client_order_id=coid,
            decision_id=decision_id,
            run_id=run_id,
            session=session,
            attempt=1,
            phase=side,
            security_id=security_id,
            symbol=security_id,
            side=side,
            notional=100.0,
            sells_in_flight_at_submit=False,
            **_stamp(),
        ),
    )
    return coid


def _event(
    conn: duckdb.DuckDBPyConnection, coid: str, status: str, minutes: int, reason: str | None = None
) -> None:
    append(
        conn,
        OrderEventRow(client_order_id=coid, status=status, reason=reason, **_stamp(minutes)),
    )


def _seed(conn: duckdb.DuckDBPyConnection, tag: str) -> tuple[int, int]:
    """One window holding one row of every table a window reader covers; `tag`
    keeps its security ids and order ids apart from another window's."""
    window_id = _window(conn)
    run_id = _run(conn, window_id)
    append(
        conn,
        PaperRunResultRow(
            run_id=run_id, finished_at=_at(5), status="ok", clock_fault=False, **_stamp(5)
        ),
    )
    sec = f"SEC_{tag}"
    decision_id = _decision(conn, run_id, security_id=sec)
    append(
        conn,
        DecisionEventRow(decision_id=decision_id, run_id=run_id, status="skipped", **_stamp(1)),
    )
    coid = _order(conn, run_id, decision_id, f"tp-{tag}-1", security_id=sec)
    _event(conn, coid, "pending", 1)
    _order(conn, run_id, decision_id, f"tp-{tag}-2", security_id=sec)
    _event(conn, f"tp-{tag}-2", "accepted", 1)
    append(
        conn,
        OutcomeRow(
            client_order_id=coid, through_session=_SESSION, kind="not_executed", **_stamp(2)
        ),
    )
    append(
        conn,
        PositionDailyRow(
            run_id=run_id, session=_SESSION, security_id=sec, quantity=1.0, **_stamp(3)
        ),
    )
    append(
        conn,
        AdjustmentRow(
            window_id=window_id, run_id=run_id, session=_SESSION, kind="dividend_cash", **_stamp(3)
        ),
    )
    append(
        conn,
        ReconciliationRow(window_id=window_id, run_id=run_id, at=_at(3), status="ok", **_stamp(3)),
    )
    append(
        conn,
        KillSwitchRow(window_id=window_id, at=_at(3), state="engaged", source="owner", **_stamp(3)),
    )
    append(
        conn,
        RebalanceEventRow(
            rebalance_session=_SESSION, run_id=run_id, status="executed", **_stamp(4)
        ),
    )
    append(
        conn,
        PaperPlanRow(
            run_id=run_id,
            plan_trial_id=7,
            rebalance_session=_SESSION,
            n_universe=10,
            n_targets=5,
            n_orders_below_min_at_live_capital=0,
            **_stamp(),
        ),
    )
    append(
        conn,
        SignalRow(
            run_id=run_id,
            rebalance_session=_SESSION,
            security_id=sec,
            reason="selected",
            **_stamp(),
        ),
    )
    append(
        conn,
        OverrideRow(
            window_id=window_id,
            made_at=_at(0),
            security_id=sec,
            kind="exclude_name",
            reason="r",
            **_stamp(),
        ),
    )
    append(
        conn,
        OverrideRow(
            window_id=window_id, made_at=_at(0), kind="engage_kill_switch", reason="r", **_stamp()
        ),
    )
    append(
        conn,
        PaperReportRow(
            window_id=window_id, trial_id=9, through_session=_SESSION, run_at=_at(6), **_stamp(6)
        ),
    )
    _stop(conn, window_id, "requested", 7)
    return window_id, run_id


#: Every reader scoped to a window, called on window `w`.
WINDOW_READERS: dict[str, Callable[[duckdb.DuckDBPyConnection, int], Any]] = {
    "window_stops_for": lambda c, w: journal.window_stops_for(c, w),
    "runs_for": lambda c, w: journal.runs_for(c, w),
    "orders_for": lambda c, w: journal.orders_for(c, window_id=w),
    "order_events_for": lambda c, w: journal.order_events_for(c, window_id=w),
    "latest_order_events": lambda c, w: journal.latest_order_events(c, window_id=w),
    "non_terminal_orders": lambda c, w: journal.non_terminal_orders(c, window_id=w),
    "pending_orders": lambda c, w: journal.pending_orders(c, window_id=w),
    "decisions_for": lambda c, w: journal.decisions_for(c, w),
    "kill_switch_events_for": lambda c, w: journal.kill_switch_events_for(c, w),
    "positions_daily_for": lambda c, w: journal.positions_daily_for(c, w),
    "last_marked_session": lambda c, w: journal.last_marked_session(c, w),
    "adjustments_for": lambda c, w: journal.adjustments_for(c, w),
    "reconciliations_for": lambda c, w: journal.reconciliations_for(c, w),
    "last_ok_reconciliation": lambda c, w: journal.last_ok_reconciliation(c, w),
    "rebalance_events_for": lambda c, w: journal.rebalance_events_for(c, w),
    "plans_for": lambda c, w: journal.plans_for(c, w),
    "overrides_for": lambda c, w: journal.overrides_for(c, w),
    "unconsumed_kill_switch_overrides": lambda c, w: journal.unconsumed_kill_switch_overrides(c, w),
    "paper_reports_for": lambda c, w: journal.paper_reports_for(c, w),
    "outcomes_for": lambda c, w: journal.outcomes_for(c, w),
}

#: Readers with no window: store-wide or keyed by something else.
OTHER_READERS: dict[str, Callable[[duckdb.DuckDBPyConnection], Any]] = {
    "open_window": journal.open_window,
    "latest_window": journal.latest_window,
    "orders_on_session": lambda c: journal.orders_on_session(c, _SESSION, "SEC_A", "buy"),
    "fill_cursors": journal.fill_cursors,
    "latest_collected_through": journal.latest_collected_through,
    "signals_for": lambda c: journal.signals_for(c, 1),
    "resume_invocations": journal.resume_invocations,
    "alerts_for": lambda c: journal.alerts_for(c, kind="no_window", session=_SESSION),
    "non_terminal_orders(None)": lambda c: journal.non_terminal_orders(c, window_id=None),
    "pending_orders(None)": lambda c: journal.pending_orders(c, window_id=None),
}


# --- not initialised, window isolation, module text -----------------------------------


@pytest.mark.parametrize(
    "reader",
    [*(lambda c, r=r: r(c, 1) for r in WINDOW_READERS.values()), *OTHER_READERS.values()],
    ids=[*WINDOW_READERS, *OTHER_READERS],
)
def test_every_reader_raises_journal_not_initialised_on_a_version_4_store(
    tmp_path: Path, reader: Callable[[duckdb.DuckDBPyConnection], Any]
) -> None:
    path = version_4_store(tmp_path / "store_v4.duckdb")
    conn = duckdb.connect(str(path), read_only=True)
    try:
        with pytest.raises(JournalNotInitialised):
            reader(conn)
    finally:
        conn.close()


@pytest.mark.parametrize("name", list(WINDOW_READERS))
def test_a_window_read_excludes_another_windows_rows(
    conn: duckdb.DuckDBPyConnection, name: str
) -> None:
    reader = WINDOW_READERS[name]
    first, _ = _seed(conn, "ONE")
    alone = reader(conn, first)
    second, _ = _seed(conn, "TWO")
    assert reader(conn, first) == alone
    assert alone  # the seed gives every reader something to return
    if name != "last_marked_session":  # both windows mark the same session
        assert reader(conn, second) != alone


def test_the_module_still_never_updates_or_deletes() -> None:
    text = Path(journal.__file__).read_text()
    code = re.sub(r'"""[\s\S]*?"""', "", text)
    assert not re.search(r"\b(UPDATE|DELETE|TRUNCATE|DROP|ALTER)\b", code)


def test_the_kill_switch_override_kind_is_the_shared_schema_constant() -> None:
    """#691: `OverrideWithUse.consumed` and `unconsumed_kill_switch_overrides` compare
    against `schema.ENGAGE_KILL_SWITCH_KIND`, never a quoted copy of it, so a renamed
    kind cannot leave the journal readers matching a stale string."""
    code = re.sub(r'"""[\s\S]*?"""', "", Path(journal.__file__).read_text())
    assert f'"{schema.ENGAGE_KILL_SWITCH_KIND}"' not in code
    assert code.count("== ENGAGE_KILL_SWITCH_KIND") == 2


# --- windows ------------------------------------------------------------------------------


def test_open_window_follows_the_stop_rows(conn: duckdb.DuckDBPyConnection) -> None:
    assert journal.open_window(conn) is None
    assert journal.latest_window(conn) is None
    first = _window(conn)
    assert journal.open_window(conn) == journal.latest_window(conn)
    assert (window := journal.open_window(conn)) is not None and window.window_id == first
    _stop(conn, first, "requested", 1)
    assert (window := journal.open_window(conn)) is not None and window.window_id == first
    _stop(conn, first, "closed", 2)
    assert journal.open_window(conn) is None
    assert (latest := journal.latest_window(conn)) is not None and latest.window_id == first
    second = _window(conn, 3)
    assert (window := journal.open_window(conn)) is not None and window.window_id == second
    _stop(conn, second, "abandoned", 4)
    assert journal.open_window(conn) is None
    assert (latest := journal.latest_window(conn)) is not None and latest.window_id == second
    assert [s.state for s in journal.window_stops_for(conn, first)] == ["requested", "closed"]


def test_two_open_windows_fail_closed(conn: duckdb.DuckDBPyConnection) -> None:
    _window(conn)
    _window(conn)
    with pytest.raises(JournalIntegrityError, match="open"):
        journal.open_window(conn)


# --- runs ----------------------------------------------------------------------------------


def test_runs_carry_their_result_or_none_when_unfinished(conn: duckdb.DuckDBPyConnection) -> None:
    window_id = _window(conn)
    finished = _run(conn, window_id)
    append(
        conn,
        PaperRunResultRow(
            run_id=finished, finished_at=_at(5), status="halted", clock_fault=False, **_stamp(5)
        ),
    )
    unfinished = _run(conn, window_id, _NEXT, minutes=10)
    runs = journal.runs_for(conn, window_id)
    assert [r.run.run_id for r in runs] == [finished, unfinished]
    assert runs[0].result is not None and runs[0].result.status == "halted"
    assert runs[1].result is None


# --- orders ----------------------------------------------------------------------------------


def test_latest_event_follows_known_at_not_insertion_order(conn: duckdb.DuckDBPyConnection) -> None:
    window_id = _window(conn)
    run_id = _run(conn, window_id)
    coid = _order(conn, run_id, _decision(conn, run_id), "tp-1")
    _event(conn, coid, "filled", 20)
    _event(conn, coid, "pending", 1)
    _event(conn, coid, "accepted", 5)
    latest = journal.latest_order_events(conn, window_id=window_id)
    assert latest[coid].status == "filled"
    assert [e.status for e in journal.order_events_for(conn, window_id=window_id)] == [
        "pending",
        "accepted",
        "filled",
    ]


def test_pending_and_non_terminal_orders(conn: duckdb.DuckDBPyConnection) -> None:
    window_id = _window(conn)
    run_id = _run(conn, window_id)
    decision_id = _decision(conn, run_id)
    statuses: dict[str, list[tuple[str, str | None]]] = {
        "none": [],
        "pending": [("pending", None)],
        "pending_cancel": [
            ("pending", None),
            ("cancel_requested", "halt"),
            ("cancel_failed", "halt"),
        ],
        "accepted": [("pending", None), ("accepted", None)],
        "replay": [("pending", None), ("replay", None)],
        "filled": [("pending", None), ("accepted", None), ("filled", None)],
        "cancelled_not_received": [("pending", None), ("cancelled", "not_received")],
    }
    for coid, events in statuses.items():
        _order(conn, run_id, decision_id, coid)
        for minute, (status, reason) in enumerate(events):
            _event(conn, coid, status, minute, reason)
    # A terminal event journaled with an earlier known_at than a later cancel row
    # still ends the order: terminal is absorbing.
    _order(conn, run_id, decision_id, "expired_then_noop")
    _event(conn, "expired_then_noop", "accepted", 0)
    _event(conn, "expired_then_noop", "cancel_noop", 9)
    _event(conn, "expired_then_noop", "expired", 3)
    # A fill collected before any acknowledging event (a submit that timed out
    # after the broker took the order) acknowledges it: not pending, still open.
    _order(conn, run_id, decision_id, "pending_with_fill")
    _event(conn, "pending_with_fill", "pending", 0)
    append(
        conn,
        FillRow(
            client_order_id="pending_with_fill",
            filled_at=_at(1),
            quantity=0.5,
            price=10.0,
            price_implied=False,
            broker_fill_id="bf-1",
            source="broker_feed",
            **_stamp(2),
        ),
    )

    pending = {o.client_order_id for o in journal.pending_orders(conn, window_id=window_id)}
    open_ = {o.client_order_id for o in journal.non_terminal_orders(conn, window_id=window_id)}
    assert pending == {"none", "pending", "pending_cancel"}
    assert open_ == {"none", "pending", "pending_cancel", "accepted", "replay", "pending_with_fill"}
    assert pending == {o.client_order_id for o in journal.pending_orders(conn, window_id=None)}


def test_orders_on_a_session_span_decisions_and_windows(conn: duckdb.DuckDBPyConnection) -> None:
    """The attempt count behind `client_order_id` is store-wide: ids are unique
    across windows, so a window filter here could re-issue an id."""
    first = _window(conn)
    run_one = _run(conn, first)
    _order(conn, run_one, _decision(conn, run_one), "a1")
    _order(conn, run_one, _decision(conn, run_one), "a2")
    _order(conn, run_one, _decision(conn, run_one), "sell", side="sell")
    _order(conn, run_one, _decision(conn, run_one, "SEC_B"), "other_name", security_id="SEC_B")
    _order(conn, run_one, _decision(conn, run_one), "other_day", session=_NEXT)
    _stop(conn, first, "closed", 1)
    second = _window(conn, 2)
    run_two = _run(conn, second)
    _order(conn, run_two, _decision(conn, run_two), "a3")
    found = journal.orders_on_session(conn, _SESSION, "SEC_A", "buy")
    assert [o.client_order_id for o in found] == ["a1", "a2", "a3"]


# --- decisions -----------------------------------------------------------------------------


def test_decisions_carry_their_events_and_filter_by_rebalance(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    window_id = _window(conn)
    run_id = _run(conn, window_id)
    planned = _decision(conn, run_id)
    exit_ = _decision(conn, run_id, "SEC_B", rebalance_session=None)
    append(
        conn,
        DecisionEventRow(decision_id=planned, run_id=run_id, status="written_off", **_stamp(9)),
    )
    append(
        conn, DecisionEventRow(decision_id=planned, run_id=run_id, status="skipped", **_stamp(2))
    )
    every = journal.decisions_for(conn, window_id)
    assert [d.decision.decision_id for d in every] == [planned, exit_]
    assert [e.status for e in every[0].events] == ["skipped", "written_off"]
    assert every[1].events == ()
    only = journal.decisions_for(conn, window_id, rebalance_session=_SESSION)
    assert [d.decision.decision_id for d in only] == [planned]


# --- cursors, kill switch, marks, adjustments, reconciliations -------------------------------


def test_fill_cursors_and_the_latest_collected_through(conn: duckdb.DuckDBPyConnection) -> None:
    assert journal.latest_collected_through(conn) is None
    append(
        conn, FillCursorRow(writer_kind="run", writer_id=1, collected_through=_at(30), **_stamp(31))
    )
    append(
        conn,
        FillCursorRow(writer_kind="resume", writer_id=1, collected_through=_at(10), **_stamp(40)),
    )
    assert journal.latest_collected_through(conn) == _at(30)
    assert [c.writer_kind for c in journal.fill_cursors(conn)] == ["run", "resume"]


def test_kill_switch_rows_in_known_at_order(conn: duckdb.DuckDBPyConnection) -> None:
    window_id = _window(conn)
    append(
        conn,
        KillSwitchRow(
            window_id=window_id, at=_at(5), state="released", source="owner", **_stamp(5)
        ),
    )
    append(
        conn,
        KillSwitchRow(window_id=window_id, at=_at(1), state="engaged", source="fault", **_stamp(1)),
    )
    assert [k.state for k in journal.kill_switch_events_for(conn, window_id)] == [
        "engaged",
        "released",
    ]


def test_marks_since_the_last(conn: duckdb.DuckDBPyConnection) -> None:
    window_id = _window(conn)
    run_id = _run(conn, window_id)
    assert journal.last_marked_session(conn, window_id) is None
    append(
        conn, PositionDailyRow(run_id=run_id, session=_NEXT, quantity=0.0, cash=1.0, **_stamp(2))
    )
    append(
        conn,
        PositionDailyRow(
            run_id=run_id, session=_SESSION, security_id="SEC_A", quantity=1.0, **_stamp(1)
        ),
    )
    assert journal.last_marked_session(conn, window_id) == _NEXT
    assert [m.session for m in journal.positions_daily_for(conn, window_id)] == [_SESSION, _NEXT]
    assert [m.session for m in journal.positions_daily_for(conn, window_id, after=_SESSION)] == [
        _NEXT
    ]


def test_adjustments_through_a_session(conn: duckdb.DuckDBPyConnection) -> None:
    window_id = _window(conn)
    for session in (_SESSION, _NEXT):
        append(
            conn,
            AdjustmentRow(
                window_id=window_id, session=session, kind="dividend_cash", cash=1.0, **_stamp()
            ),
        )
    assert len(journal.adjustments_for(conn, window_id)) == 2
    assert [
        a.session for a in journal.adjustments_for(conn, window_id, through_session=_SESSION)
    ] == [_SESSION]


def test_last_ok_reconciliation(conn: duckdb.DuckDBPyConnection) -> None:
    window_id = _window(conn)
    assert journal.last_ok_reconciliation(conn, window_id) is None
    for minutes, status in ((1, "ok"), (2, "ok"), (3, "mismatch")):
        append(
            conn,
            ReconciliationRow(
                window_id=window_id,
                at=_at(minutes),
                status=status,
                broker_cash=float(minutes),
                **_stamp(minutes),
            ),
        )
    last = journal.last_ok_reconciliation(conn, window_id)
    assert last is not None and last.broker_cash == 2.0
    assert [r.status for r in journal.reconciliations_for(conn, window_id)] == [
        "ok",
        "ok",
        "mismatch",
    ]


# --- rebalances, plans, signals ----------------------------------------------------------------


def test_rebalance_events_plans_and_signals_per_run(conn: duckdb.DuckDBPyConnection) -> None:
    window_id, run_id = _seed(conn, "ONE")
    other_run = _run(conn, window_id, _NEXT)
    append(
        conn,
        SignalRow(
            run_id=other_run,
            rebalance_session=_NEXT,
            security_id="SEC_X",
            reason="below_cut",
            **_stamp(),
        ),
    )
    assert [e.status for e in journal.rebalance_events_for(conn, window_id)] == ["executed"]
    assert [p.plan_trial_id for p in journal.plans_for(conn, window_id)] == [7]
    assert [s.security_id for s in journal.signals_for(conn, run_id)] == ["SEC_ONE"]
    assert [s.security_id for s in journal.signals_for(conn, other_run)] == ["SEC_X"]


# --- overrides -------------------------------------------------------------------------------


def test_overrides_with_their_consumption(conn: duckdb.DuckDBPyConnection) -> None:
    window_id = _window(conn)
    run_id = _run(conn, window_id)

    def override(kind: str) -> int:
        override_id = append(
            conn,
            OverrideRow(
                window_id=window_id,
                made_at=_at(0),
                security_id="SEC_A",
                kind=kind,
                reason="why",
                **_stamp(),
            ),
        )
        assert override_id is not None
        return override_id

    excluded, unused, engaged, pending_kill = (
        override("exclude_name"),
        override("keep_name"),
        override("engage_kill_switch"),
        override("engage_kill_switch"),
    )
    decision_id = _decision(conn, run_id, override_id=excluded)
    event_id = append(
        conn,
        KillSwitchRow(
            window_id=window_id,
            at=_at(1),
            state="engaged",
            source="owner",
            override_id=engaged,
            **_stamp(1),
        ),
    )
    # A `released` row citing an override consumes nothing (req 5: only an
    # `engaged` row citing it does).
    append(
        conn,
        KillSwitchRow(
            window_id=window_id,
            at=_at(2),
            state="released",
            source="owner",
            override_id=pending_kill,
            **_stamp(2),
        ),
    )
    found = {o.override.override_id: o for o in journal.overrides_for(conn, window_id)}
    assert found[excluded].decision_ids == (decision_id,) and found[excluded].consumed
    assert not found[unused].consumed
    assert found[engaged].kill_switch_event_ids == (event_id,) and found[engaged].consumed
    assert not found[pending_kill].consumed
    assert [o.override_id for o in journal.unconsumed_kill_switch_overrides(conn, window_id)] == [
        pending_kill
    ]


# --- reports, resumes, alerts, outcomes ---------------------------------------------------------


def test_reports_resumes_alerts_and_outcomes(conn: duckdb.DuckDBPyConnection) -> None:
    window_id, _ = _seed(conn, "ONE")
    assert [r.trial_id for r in journal.paper_reports_for(conn, window_id)] == [9]
    assert [o.kind for o in journal.outcomes_for(conn, window_id)] == ["not_executed"]

    assert journal.resume_invocations(conn) == []
    append(
        conn,
        ResumeInvocationRow(
            at=_at(1),
            reason="checked",
            accept_broker_fills=False,
            accept_rejections=False,
            **_stamp(1),
        ),
    )
    assert [r.reason for r in journal.resume_invocations(conn)] == ["checked"]

    assert journal.resume_acceptances(conn) == []
    append(conn, ResumeAcceptanceRow(resume_id=1, accepted_json="[]", **_stamp(2)))
    assert [(r.resume_id, r.accepted_json) for r in journal.resume_acceptances(conn)] == [(1, "[]")]

    assert journal.alerts_for(conn, kind="no_window", session=_NEXT) == []
    append(conn, AlertRow(session=_NEXT, kind="no_window", message="m", at=_at(1), **_stamp(1)))
    append(conn, AlertRow(session=_NEXT, kind="locked", message="m", at=_at(1), **_stamp(1)))
    append(conn, AlertRow(session=_SESSION, kind="no_window", message="m", at=_at(1), **_stamp(1)))
    assert [
        (a.kind, a.session) for a in journal.alerts_for(conn, kind="no_window", session=_NEXT)
    ] == [("no_window", _NEXT)]


def test_the_private_select_refuses_fills(conn: duckdb.DuckDBPyConnection) -> None:
    """`fills` is read only through `fills_for`, which hides superseded rows."""
    with pytest.raises(TypeError, match="fills_for"):
        journal._select(conn, FillRow)


def test_status_constants_are_schema_values() -> None:
    order_statuses = set(schema.JOURNAL_ENUMS["order_events", "status"])
    assert set(journal.TERMINAL_ORDER_STATUSES) <= order_statuses
    assert set(journal.ACKNOWLEDGED_ORDER_STATUSES) <= order_statuses
    assert set(journal.CLOSING_STOP_STATES) <= set(
        schema.JOURNAL_ENUMS["paper_window_stops", "state"]
    )


def test_order_readers_fail_closed_on_an_order_with_no_run(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    window_id = _window(conn)
    _order(conn, 99, 1, "orphan")
    for read in (
        journal.orders_for,
        journal.order_events_for,
        journal.non_terminal_orders,
        journal.pending_orders,
    ):
        with pytest.raises(JournalIntegrityError, match="orphan"):
            read(conn, window_id=window_id)


def test_an_override_is_consumed_only_by_what_its_kind_consumes(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """A decision citing an `engage_kill_switch` override, or another window's
    `engaged` row citing it, does not consume it."""
    window_id = _window(conn)
    run_id = _run(conn, window_id)
    kill = append(
        conn,
        OverrideRow(
            window_id=window_id, made_at=_at(0), kind="engage_kill_switch", reason="r", **_stamp()
        ),
    )
    _decision(conn, run_id, override_id=kill)
    append(
        conn,
        KillSwitchRow(
            window_id=window_id + 1,
            at=_at(1),
            state="engaged",
            source="owner",
            override_id=kill,
            **_stamp(1),
        ),
    )
    (found,) = journal.overrides_for(conn, window_id)
    assert not found.consumed
    assert [o.override_id for o in journal.unconsumed_kill_switch_overrides(conn, window_id)] == [
        kill
    ]


# --- bounded page reads (#435) ------------------------------------------------------------------


def _timed_order(conn: duckdb.DuckDBPyConnection, run_id: int, coid: str, minutes: int) -> str:
    """An order of its own decision, stamped `minutes` after `_NOW`."""
    decision_id = _decision(conn, run_id, security_id=f"SEC_{coid}")
    append(
        conn,
        OrderRow(
            client_order_id=coid,
            decision_id=decision_id,
            run_id=run_id,
            session=_SESSION,
            attempt=1,
            phase="buy",
            security_id=f"SEC_{coid}",
            symbol=f"SEC_{coid}",
            side="buy",
            notional=100.0,
            sells_in_flight_at_submit=False,
            **_stamp(minutes),
        ),
    )
    return coid


def test_orders_for_a_limit_reads_the_newest_orders_newest_first(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    window_id = _window(conn)
    run_id = _run(conn, window_id)
    other = _run(conn, _window(conn))
    # inserted out of known_at order; "b" and "c" tie on known_at
    for coid, minutes in (("d", 4), ("a", 1), ("c", 3), ("b", 3), ("e", 2)):
        _timed_order(conn, run_id, coid, minutes)
    _timed_order(conn, other, "z", 9)  # another window's newest order

    every = journal.orders_for(conn, window_id=window_id)
    assert [o.client_order_id for o in every] == ["a", "e", "c", "b", "d"]  # unchanged
    newest = journal.orders_for(conn, window_id=window_id, limit=3)
    assert [o.client_order_id for o in newest] == ["d", "c", "b"]  # ties by id, descending
    assert journal.orders_for(conn, window_id=window_id, limit=10) == sorted(
        every, key=lambda o: (o.known_at, o.client_order_id), reverse=True
    )
    with pytest.raises(ValueError, match="limit"):
        journal.orders_for(conn, window_id=window_id, limit=0)


def test_events_and_outcomes_filter_by_client_order_ids_within_the_window(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    window_id = _window(conn)
    run_id = _run(conn, window_id)
    other = _run(conn, _window(conn))
    for coid, minutes in (("a", 1), ("b", 2), ("c", 3)):
        _timed_order(conn, run_id, coid, minutes)
        _event(conn, coid, "accepted", minutes + 10)
        append(
            conn,
            OutcomeRow(
                client_order_id=coid,
                through_session=_SESSION,
                kind="not_executed",
                **_stamp(minutes + 20),
            ),
        )
    _timed_order(conn, other, "z", 9)
    _event(conn, "z", "accepted", 19)

    def events(**kw: Any) -> list[str]:
        return [e.client_order_id for e in journal.order_events_for(conn, **kw)]

    assert events(window_id=window_id) == ["a", "b", "c"]  # unchanged
    assert events(window_id=window_id, client_order_ids=["c", "a"]) == ["a", "c"]
    assert events(window_id=window_id, client_order_ids=["z"]) == []  # still the window's
    assert events(window_id=None, client_order_ids=["z", "b"]) == ["b", "z"]
    assert events(window_id=window_id, client_order_ids=[]) == []
    with pytest.raises(TypeError):
        journal.order_events_for(conn, window_id=window_id, client_order_ids="a")

    def outcomes(**kw: Any) -> list[str]:
        return [o.client_order_id for o in journal.outcomes_for(conn, window_id, **kw)]

    assert outcomes() == ["a", "b", "c"]  # unchanged
    assert outcomes(client_order_ids=["b"]) == ["b"]
    assert outcomes(client_order_ids=[]) == []
