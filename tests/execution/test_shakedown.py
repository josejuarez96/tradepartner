"""`paper shakedown` (ADR 0017 part E; paper spec req 15 as amended 2026-10-09;
plan T157b): the span, the seven criteria E.1 to E.7, each passing on the clean
fixture span and failing on its synthetic violation, the thresholds read from
the `shakedown_span` row, the restarts and the read-only contract.

The clean fixture: a `shakedown_span` row made Friday 2026-10-09 with N = 3 and
M = 2, so the span is Monday 2026-10-12 to Wednesday 2026-10-14 at `NOW`
(Thursday before the close); one window (book `main`) opened 2026-10-08; one
scheduler run per session, `ok` with a filled order on the 12th and 13th and the
owner's drill on the 14th (an owner `engaged` row, the scheduled rebalance
`skipped_kill_switch` with no order, a `released` row); an `ok` reconciliation
per run; an `ok` delivery on the default non-store channel (`macos`); and
`health_report` stubbed to pass.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.execution import ops
from tradepartner.execution import shakedown as shakedown_module
from tradepartner.execution.shakedown import Shakedown, ShakedownLine, shakedown
from tradepartner.execution.waiting import SHAKEDOWN_BOOK, WaitingItem
from tradepartner.store.db import open_for_write, open_read_only

MADE_AT = datetime(2026, 10, 9, 15, tzinfo=UTC)  # Friday
D1, D2, D3 = date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 14)
D4 = date(2026, 10, 15)
NOW = datetime(2026, 10, 15, 15, tzinfo=UTC)  # Thursday, before its close
WINDOW_STARTED = datetime(2026, 10, 8, 14, tzinfo=UTC)
QUANTITY_TOLERANCE = 1e-6
CASH_TOLERANCE = 0.01
NAMES = (
    "E.1 sessions",
    "E.2 reconciliation",
    "E.3 orders",
    "E.4 kill-switch drill",
    "E.5 journal",
    "E.6 alerts",
    "E.7 data",
)


def _at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


@dataclass
class _Health:
    ok: bool = True
    failures: tuple[str, ...] = field(default_factory=tuple)


@pytest.fixture
def health(monkeypatch: pytest.MonkeyPatch) -> _Health:
    """`health_report` stubbed: the fixture store's fact tables are not the
    subject here; one test flips it."""
    state = _Health()
    monkeypatch.setattr(shakedown_module, "health_report", lambda conn, t, s: state)
    return state


@pytest.fixture
def settings(fixture_store_path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(fixture_store_path)})


@pytest.fixture
def conn(fixture_store_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(str(fixture_store_path))
    try:
        yield connection
    finally:
        connection.close()


_ID_COLUMNS = {
    "owner_decisions": "decision_id",
    "paper_runs": "run_id",
    "decisions": "decision_id",
    "reconciliations": "reconciliation_id",
    "kill_switch": "event_id",
    "alerts": "alert_id",
    "fills": "fill_id",
}


class _Journal:
    """Writes the fixture rows with explicit ids and stamps."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def next_id(self, table: str) -> int:
        row = self.conn.execute(
            f"SELECT COALESCE(MAX({_ID_COLUMNS[table]}), 0) + 1 FROM {table}"
        ).fetchone()
        assert row is not None
        return int(row[0])

    def span(self, sessions: int = 3, order_sessions: int = 2, made_at: datetime = MADE_AT) -> None:
        self.conn.execute(
            "INSERT INTO owner_decisions (decision_id, made_at, kind, values_json, reason) "
            "VALUES (?, ?, 'shakedown_span', ?, 'H1 goes live first')",
            [
                self.next_id("owner_decisions"),
                made_at,
                json.dumps({"order_sessions": order_sessions, "sessions": sessions}),
            ],
        )

    def note(self, alert_id: int) -> None:
        self.conn.execute(
            "INSERT INTO owner_decisions (decision_id, made_at, kind, values_json, reason) "
            "VALUES (?, ?, 'shakedown_note', ?, 'explained')",
            [self.next_id("owner_decisions"), NOW, json.dumps({"alert_id": alert_id})],
        )

    def window(
        self,
        *,
        window_id: int = 1,
        book_id: str = "main",
        started: datetime = WINDOW_STARTED,
        min_reason: int = 10,
    ) -> None:
        frozen = {
            "paper.min_override_reason_chars": min_reason,
            "paper.min_rebalances": 6,
            "risk.reconcile_cash_tolerance": CASH_TOLERANCE,
            "risk.reconcile_quantity_tolerance": QUANTITY_TOLERANCE,
            "risk.min_order_notional": 1.0,
        }
        self.conn.execute(
            "INSERT INTO paper_windows (window_id, hypothesis_id, first_rebalance_session, "
            "account_id, starting_cash, starting_equity, code_version, started_at, frozen_json, "
            "frozen_sha256, book_id, known_at, ingested_at) "
            "VALUES (?, 1, ?, 'PA1', 1000.0, 1000.0, 'test', ?, ?, ?, ?, ?, ?)",
            [
                window_id,
                date(2026, 10, 30),
                started,
                json.dumps(frozen),
                "0" * 64,
                book_id,
                started,
                started,
            ],
        )

    def run(
        self,
        session: date,
        status: str | None = "ok",
        *,
        window_id: int = 1,
        kind: str = "rebalance",
        invoked_by: str = "scheduler",
        hour: int = 19,
        fault_type: str | None = None,
    ) -> int:
        run_id = self.next_id("paper_runs")
        started = _at(session, hour)
        self.conn.execute(
            "INSERT INTO paper_runs (run_id, window_id, session, kind, started_at, invoked_by, "
            "code_version, known_at, ingested_at) VALUES (?, ?, ?, ?, ?, ?, 'test', ?, ?)",
            [run_id, window_id, session, kind, started, invoked_by, started, started],
        )
        if status is not None:
            finished = _at(session, hour + 1)
            self.conn.execute(
                "INSERT INTO paper_run_results (run_id, finished_at, status, fault_type, "
                "clock_fault, known_at, ingested_at) VALUES (?, ?, ?, ?, FALSE, ?, ?)",
                [run_id, finished, status, fault_type, finished, finished],
            )
        return run_id

    def decision(
        self,
        run_id: int,
        session: date,
        *,
        planned_notional: float | None = 1000.0,
        planned_quantity: float | None = None,
        side: str = "buy",
        position_side: str = "long",
        whole_share: bool = False,
    ) -> int:
        decision_id = self.next_id("decisions")
        self.conn.execute(
            "INSERT INTO decisions (decision_id, run_id, rebalance_session, security_id, side, "
            "planned_notional, planned_quantity, whole_share, decision, position_side, "
            "known_at, ingested_at) VALUES (?, ?, ?, 'SEC_A', ?, ?, ?, ?, 'trade', ?, ?, ?)",
            [
                decision_id,
                run_id,
                session,
                side,
                planned_notional,
                planned_quantity,
                whole_share,
                position_side,
                _at(session, 19),
                _at(session, 19),
            ],
        )
        return decision_id

    def order(
        self,
        coid: str,
        decision_id: int,
        run_id: int,
        session: date,
        *,
        notional: float | None = 1000.0,
        quantity: float | None = None,
        fills: tuple[float, ...] = (10.0,),
        price: float = 100.0,
        fill_after_terminal: bool = False,
        side: str = "buy",
        position_side: str = "long",
        hour: int = 19,
        terminal_hour: int | None = None,
    ) -> None:
        stamp = _at(session, hour)
        ended = stamp if terminal_hour is None else _at(session, terminal_hour)
        self.conn.execute(
            "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, phase, "
            "security_id, symbol, side, notional, quantity, position_side, "
            "sells_in_flight_at_submit, known_at, ingested_at) "
            "VALUES (?, ?, ?, ?, 1, ?, 'SEC_A', 'SEC_A', ?, ?, ?, ?, FALSE, ?, ?)",
            [
                coid,
                decision_id,
                run_id,
                session,
                side,
                side,
                notional,
                quantity,
                position_side,
                stamp,
                stamp,
            ],
        )
        self.conn.execute(
            "INSERT INTO order_events (client_order_id, event_at, status, known_at, ingested_at) "
            "VALUES (?, ?, 'filled', ?, ?)",
            [coid, ended, ended, ended],
        )
        fill_at = _at(session, 21) if fill_after_terminal else stamp
        for index, qty in enumerate(fills):
            # raw SQL: `journal.append` refuses a fill stamped before the latest
            # reconciliation, and these fixtures are written out of time order
            self.conn.execute(
                "INSERT INTO fills (fill_id, client_order_id, filled_at, quantity, price, "
                "price_implied, broker_fill_id, source, known_at, ingested_at) "
                "VALUES (?, ?, ?, ?, ?, FALSE, ?, 'broker_feed', ?, ?)",
                [
                    self.next_id("fills"),
                    coid,
                    fill_at,
                    qty,
                    price,
                    f"{coid}-f{index}",
                    fill_at,
                    fill_at,
                ],
            )

    def reconciliation(
        self, at: datetime, status: str = "ok", *, window_id: int = 1, book_id: str = "main"
    ) -> int:
        rid = self.next_id("reconciliations")
        self.conn.execute(
            'INSERT INTO reconciliations (reconciliation_id, window_id, "at", status, book_id, '
            "known_at, ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [rid, window_id, at, status, book_id, at, at],
        )
        return rid

    def switch(
        self, at: datetime, state: str, source: str = "owner", *, window_id: int = 1
    ) -> None:
        self.conn.execute(
            'INSERT INTO kill_switch (event_id, window_id, "at", state, source, known_at, '
            "ingested_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [self.next_id("kill_switch"), window_id, at, state, source, at, at],
        )

    def alert(
        self,
        session: date,
        kind: str,
        *,
        run_id: int | None = None,
        book_id: str = "main",
        delivered: tuple[str, ...] = (),
    ) -> int:
        alert_id = self.next_id("alerts")
        at = _at(session, 20)
        self.conn.execute(
            'INSERT INTO alerts (alert_id, run_id, session, kind, message, "at", book_id, '
            "known_at, ingested_at) VALUES (?, ?, ?, ?, 'test', ?, ?, ?, ?)",
            [alert_id, run_id, session, kind, at, book_id, at, at],
        )
        for channel in delivered:
            self.conn.execute(
                'INSERT INTO alert_deliveries (alert_id, channel, "at", ok, known_at, '
                "ingested_at) VALUES (?, ?, ?, TRUE, ?, ?)",
                [alert_id, channel, at, at, at],
            )
        return alert_id

    def override(self, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO overrides (override_id, window_id, made_at, rebalance_session, "
            "security_id, kind, reason, known_at, ingested_at) "
            "VALUES (1, 1, ?, ?, 'SEC_B', 'keep_name', ?, ?, ?)",
            [_at(D1, 13), date(2026, 10, 30), reason, _at(D1, 13), _at(D1, 13)],
        )


def _hypothesis(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        "INSERT INTO hypotheses (hypothesis_id, slug, family, title, doc_path, doc_sha256, "
        "params_json, params_sha256, in_sample_start, holdout_start, holdout_end, "
        "registered_at, registered_by) VALUES (1, 'h-shake', 'momentum', 'shake', "
        "'docs/hypotheses/h-shake.md', repeat('0', 64), ?, repeat('0', 64), ?, ?, ?, ?, 'test')",
        [
            json.dumps({"costs.per_side_bps": 5.0}),
            date(2020, 1, 31),
            date(2025, 1, 31),
            date(2025, 12, 31),
            _at(date(2026, 1, 2), 12),
        ],
    )


def _clean(
    conn: duckdb.DuckDBPyConnection,
    *,
    drill: bool = True,
    drill_orders: bool = False,
    second_fill: bool = True,
    deliveries: bool = True,
) -> _Journal:
    """The clean span (module docstring); the flags remove one piece each."""
    j = _Journal(conn)
    _hypothesis(conn)
    j.span()
    j.window()
    for session, has_order in ((D1, True), (D2, True)):
        run_id = j.run(session)
        if has_order:
            decision_id = j.decision(run_id, session)
            fills = (10.0,) if (session == D1 or second_fill) else ()
            j.order(f"o-{session}", decision_id, run_id, session, fills=fills)
        j.reconciliation(_at(session, 19))
        j.reconciliation(_at(session, 20))
    if drill:
        j.switch(_at(D3, 14), "engaged")
    drilled = j.run(D3, "skipped_kill_switch" if drill else "ok")
    if drill_orders:
        decision_id = j.decision(drilled, D3)
        j.order("o-drill", decision_id, drilled, D3, fills=())
    if drill:
        j.switch(_at(D3, 22), "released")
    if deliveries:
        j.alert(D1, "kill_switch", delivered=("macos",))
    return j


def _run(settings: Settings, now: datetime = NOW) -> Shakedown:
    with open_read_only(settings) as conn:
        return shakedown(conn, settings, now=now)


def _line(result: Shakedown, name: str) -> ShakedownLine:
    return next(line for line in result.lines if line.name == name)


def _failing(result: Shakedown) -> list[str]:
    return [line.name for line in result.lines if not line.passed]


# --- the clean span ---------------------------------------------------------------


@pytest.mark.usefixtures("health")
def test_a_clean_span_passes_with_seven_lines(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn)
    conn.close()
    result = _run(settings)
    assert [line.name for line in result.lines] == list(NAMES)
    assert result.passed, [(line.name, line.detail) for line in result.lines]
    assert result.span.sessions == (D1, D2, D3)
    assert "2 order session(s)" in _line(result, "E.1 sessions").detail
    assert "sessions >= 3, order sessions >= 2" in _line(result, "E.1 sessions").thresholds
    assert all(line.query and line.rows and line.thresholds for line in result.lines)


def test_no_span_row_raises(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    conn.close()
    with pytest.raises(ValueError, match="no shakedown_span decision"):
        _run(settings)


@pytest.mark.usefixtures("health")
def test_the_newest_span_row_restarts_the_span(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    j.span(made_at=_at(D1, 15))
    conn.close()
    result = _run(settings)
    assert result.span.sessions == (D2, D3)
    assert "2 span session(s), need >= 3" in _line(result, "E.1 sessions").detail


# --- E.1 --------------------------------------------------------------------------


@pytest.mark.usefixtures("health")
def test_e1_fails_on_a_session_with_no_scheduler_run_and_no_note(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    j.run(D4)  # irrelevant: after the span
    conn.execute("UPDATE paper_runs SET invoked_by = 'tty' WHERE session = ?", [D2])
    conn.close()
    result = _run(settings)
    assert _failing(result) == ["E.1 sessions"]
    assert f"{D2}: no scheduler run" in _line(result, "E.1 sessions").detail


@pytest.mark.usefixtures("health")
def test_e1_passes_a_missing_run_by_a_note_on_its_missed_run_alert(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    conn.execute("DELETE FROM paper_run_results WHERE run_id = 2")
    conn.execute("UPDATE paper_runs SET session = ?, kind = 'mark' WHERE run_id = 2", [D4])
    # the run on D3 alerts the missing D2 (`run.py`: S-1)
    alert_id = j.alert(D3, "missed_run")
    j.note(alert_id)
    conn.close()
    result = _run(settings)
    line = _line(result, "E.1 sessions")
    # the order session count drops to 1 (the D2 order's run moved off the span)
    assert "1 session(s) passed by note" in line.detail
    assert f"{D2}: no scheduler run" not in line.detail


@pytest.mark.usefixtures("health")
def test_e1_a_missed_run_alert_without_a_note_still_fails(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    conn.execute("UPDATE paper_runs SET invoked_by = 'tty' WHERE session = ?", [D2])
    j.alert(D3, "missed_run")
    conn.close()
    line = _line(_run(settings), "E.1 sessions")
    assert not line.passed
    assert f"{D2}: no scheduler run with a shakedown_note" in line.detail


@pytest.mark.usefixtures("health")
def test_e1_fails_on_fewer_than_m_order_sessions(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn, second_fill=False)
    conn.close()
    result = _run(settings)
    assert _failing(result) == ["E.1 sessions"]
    assert "1 order session(s), need >= 2" in _line(result, "E.1 sessions").detail


@pytest.mark.usefixtures("health")
def test_e1_a_stale_run_passes_only_with_a_noted_stale_data_alert(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    conn.execute("UPDATE paper_run_results SET status = 'stale' WHERE run_id = 2")
    alert_id = j.alert(D2, "stale_data", run_id=2)
    conn.close()
    unnoted = _line(_run(settings), "E.1 sessions")
    assert not unnoted.passed
    assert "run 2 stale" in unnoted.detail
    with open_for_write(settings) as write:
        _Journal(write).note(alert_id)
    noted = _run(settings)
    assert noted.passed, [(line.name, line.detail) for line in noted.lines]
    assert "1 session(s) passed by note" in _line(noted, "E.1 sessions").detail


@pytest.mark.usefixtures("health")
def test_e1_a_skipped_kill_switch_run_outside_an_owner_engagement_fails(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn)
    conn.execute("UPDATE kill_switch SET source = 'fault' WHERE state = 'engaged'")
    conn.close()
    result = _run(settings)
    assert "run 3 skipped_kill_switch" in _line(result, "E.1 sessions").detail
    assert not _line(result, "E.1 sessions").passed


@pytest.mark.usefixtures("health")
def test_e1_counts_a_second_book_only_for_the_sessions_it_was_open(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    # book `b` opens on D2: D3 is its only required session
    j.window(window_id=2, book_id="b", started=_at(D2, 13))
    j.run(D3, window_id=2)
    conn.close()
    assert _run(settings).passed
    with open_for_write(settings) as write:
        write.execute("DELETE FROM paper_runs WHERE window_id = 2")
    line = _line(_run(settings), "E.1 sessions")
    assert not line.passed
    assert f"book b window 2 {D3}: no scheduler run" in line.detail


# --- E.2 --------------------------------------------------------------------------


@pytest.mark.usefixtures("health")
def test_e2_fails_on_an_unreleased_mismatch_and_the_span_is_pending(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    j.reconciliation(_at(D3, 23), "mismatch")  # after the drill's released row
    conn.close()
    result = _run(settings)
    assert result.span.start is None
    assert "mismatch reconciliation" in _line(result, "E.2 reconciliation").detail
    assert not _line(result, "E.2 reconciliation").passed
    assert "pending" in _line(result, "E.1 sessions").detail


@pytest.mark.usefixtures("health")
def test_e2_allows_fills_lagging_only_when_the_next_reconciliation_is_ok(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    j.reconciliation(_at(D3, 23), "fills_lagging")
    conn.close()
    line = _line(_run(settings), "E.2 reconciliation")
    assert not line.passed
    assert "has no next one yet" in line.detail
    with open_for_write(settings) as write:
        _Journal(write).reconciliation(_at(D4, 13))
    line = _line(_run(settings), "E.2 reconciliation")
    assert line.passed, line.detail
    assert "1 pending_unresolved/fills_lagging" in line.detail


# --- the restarts -------------------------------------------------------------------


@pytest.mark.usefixtures("health")
def test_a_released_mismatch_restarts_the_span_at_the_next_session(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    j.reconciliation(_at(D1, 21), "mismatch")
    j.switch(_at(D1, 21), "engaged", "fault")
    j.switch(_at(D1, 23), "released")
    conn.close()
    result = _run(settings)
    assert result.span.start == D2
    assert result.span.sessions == (D2, D3)
    assert "restarted after mismatch reconciliation" in _line(result, "E.1 sessions").detail
    assert _line(result, "E.2 reconciliation").passed  # the mismatch is before the span


@pytest.mark.usefixtures("health")
def test_a_halted_result_restarts_the_span_after_its_released_row(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    conn.execute("UPDATE paper_run_results SET status = 'halted' WHERE run_id = 1")
    j.switch(_at(D1, 20), "engaged", "fault")
    j.switch(_at(D2, 2), "released")  # 22:00 New York on D1
    conn.close()
    result = _run(settings)
    assert result.span.start == D2
    assert "restarted after halted run 1" in _line(result, "E.1 sessions").detail
    assert not _line(result, "E.1 sessions").passed


@pytest.mark.usefixtures("health")
def test_a_halted_result_with_no_released_row_leaves_the_span_pending(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn, drill=False)  # no released row anywhere
    conn.execute("UPDATE paper_run_results SET status = 'halted' WHERE run_id = 2")
    conn.close()
    result = _run(settings)
    assert result.span.start is None
    assert result.span.sessions == ()
    assert not _line(result, "E.1 sessions").passed


# --- E.3 --------------------------------------------------------------------------


@pytest.mark.usefixtures("health")
def test_e3_fails_on_a_decision_filled_beyond_its_planned_notional(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    j.order("o-dup", 1, 1, D1)  # a second order of decision 1, also filled 1000
    conn.close()
    result = _run(settings)
    assert _failing(result) == ["E.3 orders"]
    assert "decision 1 filled 2000.00 > planned 1000.00" in _line(result, "E.3 orders").detail


@pytest.mark.usefixtures("health")
def test_e3_fails_on_a_decision_filled_beyond_its_planned_quantity(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, planned_notional=None, planned_quantity=5.0)
    j.order("o-q1", decision_id, run_id, D1, notional=None, quantity=5.0, fills=(5.0,))
    j.order("o-q2", decision_id, run_id, D1, notional=None, quantity=5.0, fills=(5.0,))
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert not line.passed
    assert f"decision {decision_id} filled 10 shares > planned 5" in line.detail


@pytest.mark.usefixtures("health")
def test_e3_fails_on_an_order_filled_beyond_its_own_size(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, planned_notional=None, planned_quantity=50.0)
    j.order("o-big", decision_id, run_id, D1, notional=None, quantity=5.0, fills=(6.0,))
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert not line.passed
    assert "order o-big filled 6 shares > quantity 5" in line.detail


@pytest.mark.usefixtures("health")
def test_e3_allows_an_overfill_within_the_frozen_cash_tolerance(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, planned_notional=1000.0)
    j.order("o-tol", decision_id, run_id, D1, fills=(10.0,), price=100.0005)
    conn.close()
    assert _line(_run(settings), "E.3 orders").passed


# #2290: a notional decision whose orders go out by share count is judged in
# shares. Reference price 100, planned notional 1000, every order filled at 115.
_BY_SHARES = {
    "trim": ("sell", "long", False, 10.0),
    "whole-share buy": ("buy", "long", True, 9.0),  # floor(1000 / (100 * 1.02))
    "short open": ("sell", "short", False, 10.0),  # floor(1000 / 100)
    "partial cover": ("buy", "short", False, 10.0),
}


@pytest.mark.usefixtures("health")
@pytest.mark.parametrize("kind", sorted(_BY_SHARES))
def test_e3_a_quantity_order_of_a_notional_decision_filled_above_its_reference_passes(
    conn: duckdb.DuckDBPyConnection, settings: Settings, kind: str
) -> None:
    side, position_side, whole_share, shares = _BY_SHARES[kind]
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(
        run_id, D1, side=side, position_side=position_side, whole_share=whole_share
    )
    j.order(
        "o-shares",
        decision_id,
        run_id,
        D1,
        notional=None,
        quantity=shares,
        fills=(shares,),
        price=115.0,
        side=side,
        position_side=position_side,
    )
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert line.passed, line.detail


@pytest.mark.usefixtures("health")
def test_e3_fails_on_two_quantity_orders_of_a_notional_decision_sized_at_once(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, side="sell")
    for coid in ("o-t1", "o-t2"):
        j.order(coid, decision_id, run_id, D1, notional=None, quantity=10.0, side="sell")
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert not line.passed
    assert f"decision {decision_id} filled 20 shares > sized 10" in line.detail


@pytest.mark.usefixtures("health")
def test_e3_a_remainder_order_sized_after_an_earlier_fill_passes(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, side="sell")
    j.order(
        "o-r1",
        decision_id,
        run_id,
        D1,
        notional=None,
        quantity=10.0,
        fills=(4.0,),
        side="sell",
        hour=15,
    )
    # the remainder, re-converted at a lower reference price: more shares than 10 - 4.
    # Accepted by design: without a journaled reference price any re-conversion of a
    # remainder after a partial fill passes (ADR 0017 correction 2026-10-26, limit).
    j.order(
        "o-r2",
        decision_id,
        run_id,
        D1,
        notional=None,
        quantity=6.2,
        fills=(6.2,),
        side="sell",
        hour=16,
    )
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert line.passed, line.detail


@pytest.mark.usefixtures("health")
def test_e3_whole_short_cover_retry_uses_unfilled_shares_after_price_rise(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, side="buy", position_side="short", whole_share=True)
    j.order(
        "o-cover-1",
        decision_id,
        run_id,
        D1,
        notional=None,
        quantity=10.0,
        fills=(6.0,),
        price=175.0,
        side="buy",
        position_side="short",
        hour=15,
    )
    j.order(
        "o-cover-2",
        decision_id,
        run_id,
        D1,
        notional=None,
        quantity=4.0,
        fills=(4.0,),
        price=175.0,
        side="buy",
        position_side="short",
        hour=16,
    )
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert line.passed, line.detail


@pytest.mark.usefixtures("health")
def test_e3_whole_short_cover_retry_after_full_fill_has_no_remainder(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, side="buy", position_side="short", whole_share=True)
    for coid, shares, hour in (("o-cover-full", 10.0, 15), ("o-cover-extra", 1.0, 16)):
        j.order(
            coid,
            decision_id,
            run_id,
            D1,
            notional=None,
            quantity=shares,
            fills=(shares,),
            side="buy",
            position_side="short",
            hour=hour,
        )
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert not line.passed
    assert f"order o-cover-extra of decision {decision_id} sized with no remainder" in line.detail
    assert "10 of 10 shares filled before it" in line.detail


@pytest.mark.usefixtures("health")
def test_e3_fails_on_a_share_order_written_after_the_plan_was_filled(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, side="sell")
    for coid, hour in (("o-f1", 15), ("o-f2", 16)):
        j.order(coid, decision_id, run_id, D1, notional=None, quantity=10.0, side="sell", hour=hour)
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert not line.passed
    assert f"order o-f2 of decision {decision_id} sized with no remainder" in line.detail


@pytest.mark.usefixtures("health")
def test_e3_fails_on_a_whole_share_order_after_a_filled_notional_order(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1)
    j.order("o-m1", decision_id, run_id, D1, hour=15)  # notional 1000, filled 10 at 100
    j.order("o-m2", decision_id, run_id, D1, notional=None, quantity=10.0, hour=16)
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert not line.passed
    assert f"order o-m2 of decision {decision_id} sized with no remainder" in line.detail


@pytest.mark.usefixtures("health")
def test_e3_uses_frozen_trading_minimum_for_no_remainder(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    conn.execute(
        "UPDATE paper_windows SET frozen_json = json_merge_patch(frozen_json, ?) "
        "WHERE window_id = 1",
        [json.dumps({"risk.reconcile_cash_tolerance": 1.26})],
    )
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1)
    j.order("o-min1", decision_id, run_id, D1, fills=(9.989,), hour=15)
    j.order("o-min2", decision_id, run_id, D1, notional=None, quantity=0.011, fills=(), hour=16)
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert line.passed, line.detail  # $1.10 remains: above the frozen $1 minimum.


@pytest.mark.usefixtures("health")
def test_e3_zero_minimum_rejects_order_after_full_fill(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    conn.execute(
        "UPDATE paper_windows SET frozen_json = json_merge_patch(frozen_json, ?) "
        "WHERE window_id = 1",
        [json.dumps({"risk.min_order_notional": 0.0})],
    )
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1)
    j.order("o-zero1", decision_id, run_id, D1, fills=(10.0,), hour=15)
    j.order("o-zero2", decision_id, run_id, D1, fills=(), hour=16)
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert not line.passed
    assert f"order o-zero2 of decision {decision_id} sized with no remainder" in line.detail


@pytest.mark.usefixtures("health")
def test_e3_zero_minimum_allows_order_with_positive_remainder(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    conn.execute(
        "UPDATE paper_windows SET frozen_json = json_merge_patch(frozen_json, ?) "
        "WHERE window_id = 1",
        [json.dumps({"risk.min_order_notional": 0.0})],
    )
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1)
    j.order("o-part1", decision_id, run_id, D1, fills=(5.0,), hour=15)
    j.order("o-part2", decision_id, run_id, D1, notional=500.0, fills=(), hour=16)
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert line.passed, line.detail


@pytest.mark.usefixtures("health")
def test_e3_first_minimum_order_and_exact_minimum_remainder_pass(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    small = j.decision(run_id, D1, planned_notional=1.0)
    j.order("o-small", small, run_id, D1, notional=1.0, fills=(0.01,), hour=15)
    exact = j.decision(run_id, D1)
    j.order("o-exact1", exact, run_id, D1, fills=(9.99,), hour=16)
    j.order("o-exact2", exact, run_id, D1, notional=1.0, fills=(), hour=17)
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert line.passed, line.detail


@pytest.mark.usefixtures("health")
def test_e3_notional_order_after_full_fill_fails_once_per_order(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1)
    for coid, hour in (("o-n1", 15), ("o-n2", 16), ("o-n3", 17)):
        j.order(coid, decision_id, run_id, D1, hour=hour, fills=(10.0,) if hour == 15 else ())
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert not line.passed
    assert line.detail.count("order o-n3 of decision") == 1
    assert "order o-n2 of decision" in line.detail


@pytest.mark.usefixtures("health")
def test_a_past_now_ignores_later_journal_and_alert_rows(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn, deliveries=False)
    j.run(D4, status="halted", hour=16, fault_type="LimitBreachError")
    j.alert(D4, "stale_data")
    j.alert(D4, "kill_switch", delivered=("macos",))
    j.reconciliation(_at(D4, 16), status="mismatch")
    j.order("o-future", 1, 1, D4, fills=(), hour=16)
    conn.execute(
        "INSERT INTO fills (fill_id, client_order_id, filled_at, quantity, price, "
        "price_implied, broker_fill_id, source, known_at, ingested_at) "
        "VALUES (?, ?, ?, 10, 100, FALSE, 'future-fill', 'broker_feed', ?, ?)",
        [j.next_id("fills"), f"o-{D1}", _at(D4, 16), _at(D4, 16), _at(D4, 16)],
    )
    conn.execute(
        "INSERT INTO overrides (override_id, window_id, made_at, rebalance_session, "
        "security_id, kind, reason, known_at, ingested_at) "
        "VALUES (1, 1, ?, ?, 'SEC_B', 'keep_name', 'short', ?, ?)",
        [_at(D4, 16), D4, _at(D4, 16), _at(D4, 16)],
    )
    j.span(sessions=99, made_at=_at(D4, 16))
    conn.close()
    result = _run(settings, now=NOW)
    assert result.span.decision_id == 1
    assert _line(result, "E.2 reconciliation").passed
    assert _line(result, "E.3 orders").passed
    assert "2 order(s)" in _line(result, "E.3 orders").detail
    assert _line(result, "E.5 journal").passed
    assert not _line(result, "E.6 alerts").passed
    assert _line(result, "E.7 data").passed


@pytest.mark.usefixtures("health")
def test_a_later_real_fill_superseding_a_synthetic_fill_counts_once(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn)
    coid = f"o-{D1}"
    old_id = conn.execute("SELECT fill_id FROM fills WHERE client_order_id = ?", [coid]).fetchone()
    assert old_id is not None
    new_id = conn.execute("SELECT COALESCE(MAX(fill_id), 0) + 1 FROM fills").fetchone()
    assert new_id is not None
    conn.execute(
        "UPDATE fills SET source = 'broker_status', price_implied = TRUE WHERE fill_id = ?",
        [old_id[0]],
    )
    conn.execute(
        "INSERT INTO fills (fill_id, client_order_id, filled_at, quantity, price, "
        "price_implied, broker_fill_id, source, superseded_by, known_at, ingested_at) "
        "VALUES (?, ?, ?, 10, 100, FALSE, 'future-replacement', 'broker_feed', ?, ?, ?)",
        [new_id[0], coid, _at(D4, 16), old_id[0], _at(D4, 16), _at(D4, 16)],
    )
    conn.close()
    result = _run(settings, now=NOW)
    assert _line(result, "E.1 sessions").passed
    assert _line(result, "E.3 orders").passed
    after = _run(settings, now=_at(D4, 17))
    assert _line(after, "E.3 orders").passed


@pytest.mark.usefixtures("health")
def test_e3_fails_on_an_order_written_while_an_earlier_one_was_in_flight(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, side="sell")
    j.order(
        "o-i1",
        decision_id,
        run_id,
        D1,
        notional=None,
        quantity=10.0,
        fills=(5.0,),
        side="sell",
        hour=15,
        terminal_hour=17,
    )
    j.order(
        "o-i2",
        decision_id,
        run_id,
        D1,
        notional=None,
        quantity=10.0,
        fills=(5.0,),
        side="sell",
        hour=16,
    )
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert not line.passed
    assert "order o-i2 written while order o-i1 was in flight" in line.detail


@pytest.mark.usefixtures("health")
def test_e3_a_notional_buy_is_still_judged_in_dollars(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, planned_notional=1000.0)
    j.order("o-n", decision_id, run_id, D1, notional=1100.0, fills=(10.5,), price=100.0)
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert not line.passed
    assert f"decision {decision_id} filled 1050.00 > planned 1000.00" in line.detail


@pytest.mark.usefixtures("health")
def test_e3_fails_on_a_limit_breach_in_the_span(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn)
    conn.execute("UPDATE paper_run_results SET fault_type = 'LimitBreachError' WHERE run_id = 2")
    conn.close()
    line = _line(_run(settings), "E.3 orders")
    assert not line.passed
    assert "run 2 failed with LimitBreachError" in line.detail


@pytest.mark.usefixtures("health")
def test_the_tolerances_come_from_the_frozen_window_never_settings(
    conn: duckdb.DuckDBPyConnection, settings: Settings, fixture_store_path: Path
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, planned_notional=1000.0)
    j.order("o-over", decision_id, run_id, D1, fills=(10.0,), price=100.5)
    conn.close()
    loose = Settings(
        _env_file=None,
        store={"path": str(fixture_store_path)},
        risk={"reconcile_cash_tolerance": 1000.0, "reconcile_quantity_tolerance": 1000.0},
        paper={"min_override_reason_chars": 1},
    )
    strict = _run(settings)
    with open_read_only(loose) as read:
        relaxed = shakedown(read, loose, now=NOW)
    assert [(x.name, x.passed) for x in strict.lines] == [(x.name, x.passed) for x in relaxed.lines]
    assert not _line(relaxed, "E.3 orders").passed
    assert relaxed.span.sessions_needed == 3 and relaxed.span.order_sessions_needed == 2


# --- E.4 --------------------------------------------------------------------------


@pytest.mark.usefixtures("health")
def test_e4_clean_drill_alone_passes(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    _clean(conn)
    conn.close()
    line = _line(_run(settings), "E.4 kill-switch drill")
    assert line.passed
    assert line.state == "passed"


@pytest.mark.usefixtures("health")
def test_e4_clean_drill_and_ordering_catch_up_fails(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    bad_run = j.run(D3, "ok", kind="catch_up", hour=20)
    decision_id = j.decision(bad_run, D3)
    j.order("o-bad-drill", decision_id, bad_run, D3, fills=())
    conn.close()
    line = _line(_run(settings), "E.4 kill-switch drill")
    assert not line.passed
    assert line.state == "failing"
    assert f"run {bad_run}" in line.detail
    assert any(f"run {bad_run}" in problem for problem in line.problems)


@pytest.mark.usefixtures("health")
def test_e4_ordering_drill_alone_fails(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    _clean(conn, drill_orders=True)
    conn.close()
    line = _line(_run(settings), "E.4 kill-switch drill")
    assert not line.passed
    assert line.state == "failing"
    assert "run 3" in line.detail


@pytest.mark.usefixtures("health")
def test_e4_non_owner_engagement_does_not_hide_ordering_drill(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    j.switch(_at(D3, 19), "engaged", "drawdown")
    bad_run = j.run(D3, "ok", kind="catch_up", hour=20)
    decision_id = j.decision(bad_run, D3)
    j.order("o-stacked-drill", decision_id, bad_run, D3, fills=())
    conn.close()
    line = _line(_run(settings), "E.4 kill-switch drill")
    assert line.state == "failing"
    assert f"run {bad_run}" in line.detail


@pytest.mark.usefixtures("health")
def test_e4_unreleased_ordering_drill_names_run(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn, drill_orders=True)
    conn.execute("DELETE FROM kill_switch WHERE state = 'released'")
    conn.close()
    line = _line(_run(settings), "E.4 kill-switch drill")
    assert line.state == "failing"
    assert "run 3" in line.detail


@pytest.mark.usefixtures("health")
def test_e4_ordering_drill_today_fails_before_session_close(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    bad_run = j.run(D4, "ok", kind="catch_up", hour=14)
    j.switch(_at(D4, 13), "engaged")
    decision_id = j.decision(bad_run, D4)
    j.order("o-today-drill", decision_id, bad_run, D4, fills=(), hour=14)
    conn.close()
    line = _line(_run(settings), "E.4 kill-switch drill")
    assert line.state == "failing"
    assert f"run {bad_run}" in line.detail


@pytest.mark.usefixtures("health")
def test_e4_owner_engagement_opened_before_span_still_spoils(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn, drill_orders=True)
    conn.execute("UPDATE kill_switch SET \"at\" = ? WHERE state = 'engaged'", [MADE_AT])
    conn.close()
    line = _line(_run(settings), "E.4 kill-switch drill")
    assert line.state == "failing"
    assert "run 3" in line.detail


@pytest.mark.usefixtures("health")
def test_e4_ordering_run_outside_owner_engagement_does_not_spoil_drill(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D4, "ok", kind="catch_up", hour=14)
    decision_id = j.decision(run_id, D4)
    j.order("o-outside-drill", decision_id, run_id, D4, fills=(), hour=14)
    conn.close()
    assert _line(_run(settings), "E.4 kill-switch drill").state == "passed"


@pytest.mark.usefixtures("health")
def test_e4_fails_with_no_drill_sequence(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn, drill=False)
    conn.close()
    result = _run(settings)
    assert _failing(result) == ["E.4 kill-switch drill"]
    assert "no drill sequence" in _line(result, "E.4 kill-switch drill").detail


@pytest.mark.usefixtures("health")
def test_e4_a_drilled_run_that_wrote_an_order_does_not_count(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn, drill_orders=True)
    conn.close()
    line = _line(_run(settings), "E.4 kill-switch drill")
    assert not line.passed
    assert "drilled run(s) that wrote orders, not counted" in line.detail


@pytest.mark.usefixtures("health")
def test_e4_needs_the_released_row_after_the_drilled_run(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn)
    conn.execute("DELETE FROM kill_switch WHERE state = 'released'")
    conn.close()
    assert not _line(_run(settings), "E.4 kill-switch drill").passed


@pytest.mark.usefixtures("health")
def test_e4_prints_a_real_engagement_as_evidence(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    j.switch(_at(D2, 12), "engaged", "drawdown")
    j.switch(_at(D2, 13), "released")
    conn.close()
    line = _line(_run(settings), "E.4 kill-switch drill")
    assert line.passed
    assert "real engagements (evidence): window 1 drawdown" in line.detail


# --- E.5 --------------------------------------------------------------------------


@pytest.mark.usefixtures("health")
def test_e5_fails_on_a_live_fill_after_its_terminal_event(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    run_id = j.run(D1, invoked_by="tty", hour=15)
    decision_id = j.decision(run_id, D1, planned_notional=1000.0)
    j.order("o-late", decision_id, run_id, D1, fills=(10.0,), fill_after_terminal=True)
    conn.close()
    result = _run(settings)
    assert _failing(result) == ["E.5 journal"]
    assert "live fill journaled after its terminal event" in _line(result, "E.5 journal").detail


@pytest.mark.usefixtures("health")
def test_e5_fails_on_a_short_override_reason(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    j.override("short")
    conn.close()
    line = _line(_run(settings), "E.5 journal")
    assert not line.passed
    assert "override_reason" in line.detail


# --- E.6 --------------------------------------------------------------------------


@pytest.mark.usefixtures("health")
def test_e6_fails_when_a_channel_has_no_ok_delivery(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn, deliveries=False)
    conn.close()
    result = _run(settings)
    assert _failing(result) == ["E.6 alerts"]
    assert "no ok delivery on macos" in _line(result, "E.6 alerts").detail


# --- E.7 --------------------------------------------------------------------------


@pytest.mark.usefixtures("health")
def test_e7_fails_on_a_stale_data_alert_without_a_note(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    alert_id = j.alert(D2, "stale_data", run_id=2)
    conn.close()
    result = _run(settings)
    assert _failing(result) == ["E.7 data"]
    assert f"stale_data alert {alert_id}" in _line(result, "E.7 data").detail
    with open_for_write(settings) as write:
        _Journal(write).note(alert_id)
    assert _run(settings).passed


def test_e7_fails_when_health_fails(
    conn: duckdb.DuckDBPyConnection, settings: Settings, health: _Health
) -> None:
    _clean(conn)
    conn.close()
    health.ok, health.failures = False, ("prices_daily_gaps",)
    result = _run(settings)
    assert _failing(result) == ["E.7 data"]
    assert "health fails: prices_daily_gaps" in _line(result, "E.7 data").detail


# --- read-only ----------------------------------------------------------------------


@pytest.mark.usefixtures("health")
def test_shakedown_writes_nothing_and_runs_on_a_read_only_connection(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn)
    conn.close()

    def counts() -> dict[str, int]:
        with open_read_only(settings) as read:
            tables = [
                r[0] for r in read.execute("SELECT table_name FROM duckdb_tables()").fetchall()
            ]
            return {
                t: int(read.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])  # type: ignore[index]
                for t in tables
            }

    before = counts()
    with open_read_only(settings) as read:
        result = shakedown(read, settings, now=NOW)
        with pytest.raises(duckdb.Error):
            read.execute("CREATE TABLE probe (x INTEGER)")
    assert result.passed
    assert counts() == before


@pytest.mark.usefixtures("health")
def test_e1_an_owner_engagement_never_released_excuses_no_skipped_session(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn)
    conn.execute("DELETE FROM kill_switch WHERE state = 'released'")
    conn.close()
    line = _line(_run(settings), "E.1 sessions")
    assert not line.passed
    assert "run 3 skipped_kill_switch" in line.detail


@pytest.mark.usefixtures("health")
def test_a_restart_in_a_window_closed_after_it_ends_at_the_close(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn, drill=False)
    conn.execute("UPDATE paper_run_results SET status = 'halted' WHERE run_id = 1")
    j.window(window_id=2, book_id="b", started=_at(D1, 13))
    conn.execute(
        'INSERT INTO paper_window_stops (window_id, "at", state, known_at, ingested_at) '
        "VALUES (1, ?, 'abandoned', ?, ?)",
        [_at(D1, 22), _at(D1, 22), _at(D1, 22)],
    )
    conn.close()
    span = _run(settings).span
    assert span.start == D2


@pytest.mark.usefixtures("health")
def test_e1_an_owner_engagement_from_before_the_span_excuses_no_skipped_session(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn)
    conn.execute("UPDATE kill_switch SET \"at\" = ? WHERE state = 'engaged'", [MADE_AT])
    conn.close()
    result = _run(settings)
    assert "run 3 skipped_kill_switch" in _line(result, "E.1 sessions").detail
    assert not _line(result, "E.1 sessions").passed
    assert not _line(result, "E.4 kill-switch drill").passed


# --- progress or failing, and the owner's queue (#2298) ------------------------------


def _states(result: Shakedown) -> dict[str, str]:
    return {line.name: line.state for line in result.lines}


@pytest.mark.usefixtures("health")
def test_a_clean_span_is_every_line_passed(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn)
    conn.close()
    assert set(_states(_run(settings)).values()) == {"passed"}


@pytest.mark.usefixtures("health")
def test_a_short_span_and_too_few_order_sessions_are_in_progress(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn, second_fill=False)
    j.span(made_at=_at(D1, 15), sessions=3, order_sessions=2)
    conn.close()
    line = _line(_run(settings), "E.1 sessions")
    assert line.state == "in_progress"
    assert line.progress == ("2 of 3 sessions", "0 of 2 order sessions")
    assert line.problems == ()


@pytest.mark.usefixtures("health")
def test_a_pending_span_is_in_progress_on_e1(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn)
    conn.execute("UPDATE paper_run_results SET status = 'halted' WHERE run_id = 3")
    conn.execute("DELETE FROM kill_switch WHERE state = 'released'")
    conn.close()
    line = _line(_run(settings), "E.1 sessions")
    assert line.state == "in_progress"
    assert line.progress[0].startswith("span pending, awaiting the released kill-switch row")


@pytest.mark.usefixtures("health")
def test_a_missing_run_in_a_short_span_is_failing(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    j.span(made_at=_at(D1, 15))
    conn.execute("UPDATE paper_runs SET invoked_by = 'tty' WHERE session = ?", [D2])
    conn.close()
    line = _line(_run(settings), "E.1 sessions")
    assert line.state == "failing"
    assert "2 of 3 sessions" in line.progress
    assert line.problems == (
        f"book main window 1 {D2}: no scheduler run and no missed_run alert with a shakedown_note",
    )


@pytest.mark.usefixtures("health")
def test_no_drill_yet_is_in_progress(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    _clean(conn, drill=False)
    conn.close()
    pending = _line(_run(settings), "E.4 kill-switch drill")
    assert pending.state == "in_progress"
    assert pending.progress == ("kill-switch drill not done yet",)


@pytest.mark.usefixtures("health")
def test_a_drill_that_wrote_orders_is_failing(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _clean(conn, drill_orders=True)
    conn.close()
    line = _line(_run(settings), "E.4 kill-switch drill")
    assert line.state == "failing"
    assert line.problems and line.progress == ()


@pytest.mark.usefixtures("health")
def test_every_other_failing_line_is_failing(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn)
    j.override("short")
    conn.close()
    assert _states(_run(settings))["E.5 journal"] == "failing"


def test_e6_with_no_delivery_attempted_yet_is_progress_and_no_item(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    # orchestrator decision on #2298: nothing sent on a channel yet waits for traffic
    _clean(conn, deliveries=False)
    conn.close()
    line = _line(_run(settings), "E.6 alerts")
    assert (line.passed, line.state) == (False, "in_progress")
    assert line.progress == ("no alert sent on macos yet",) and line.problems == ()
    assert _queue(settings) == []


def test_e6_with_only_failed_deliveries_is_failing_and_an_item(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    j = _clean(conn, deliveries=False)
    alert_id = j.alert(D2, "kill_switch")
    conn.execute(
        'INSERT INTO alert_deliveries (alert_id, channel, "at", ok, known_at, ingested_at) '
        "VALUES (?, 'macos', ?, FALSE, ?, ?)",
        [alert_id, _at(D2, 20), _at(D2, 20), _at(D2, 20)],
    )
    conn.close()
    line = _line(_run(settings), "E.6 alerts")
    assert line.state == "failing"
    assert line.problems == ("every delivery on macos failed",)
    assert [i.line for i in _queue(settings)] == ["E.6 alerts"]


def test_the_queue_read_leaves_health_out(
    conn: duckdb.DuckDBPyConnection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clean(conn)
    conn.close()

    def refuse(*_args: object) -> None:
        raise AssertionError("health_report is not part of the queue read")

    monkeypatch.setattr(shakedown_module, "health_report", refuse)
    with open_read_only(settings) as read:
        result = shakedown(read, settings, now=NOW, check_health=False)
    line = _line(result, "E.7 data")
    assert line.passed and "health not checked in this read" in line.detail


def _queue(settings: Settings) -> list[WaitingItem]:
    with open_read_only(settings) as read:
        items = ops.waiting_on_owner(read, settings, NOW)
    return [i for i in items if i.kind == "shakedown_failing"]


def test_a_span_with_a_structural_e3_failure_shows_the_item(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    # the issue's first test: an E.3 failure that no further session clears
    # (#2290's shape: a decision filled beyond its plan) is an owner item, with
    # health left out of the read (no stub: it is never called)
    j = _clean(conn)
    j.order("o-dup", 1, 1, D1)  # a second order of decision 1, also filled 1000
    conn.close()
    (item,) = _queue(settings)
    assert item.book_id == SHAKEDOWN_BOOK and item.line == "E.3 orders"
    assert item.command == "tradepartner paper shakedown"
    assert item.detail.startswith("shakedown line E.3 orders is failing: ")
    assert "decision 1 filled 2000.00 > planned 1000.00" in item.detail
    assert "next: an order filled beyond its plan" in item.detail


def test_a_span_that_is_merely_short_shows_no_item(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    # the issue's second test: a span with too few sessions and order sessions so
    # far, and no drill yet, is progress, not a problem
    j = _clean(conn, drill=False)
    j.span(made_at=_at(D1, 15), sessions=10, order_sessions=5)
    j.alert(D2, "kill_switch", delivered=("macos",))  # keep E.6 met: progress lists E.1, E.4
    conn.close()
    assert _queue(settings) == []
    with open_read_only(settings) as read:
        progress = ops.owner_queue(read, settings, NOW).shakedown_progress
    assert progress == (
        "shakedown in progress: E.1 sessions: 2 of 10 sessions, 1 of 5 order sessions; "
        "E.4 kill-switch drill: kill-switch drill not done yet"
    )


def test_no_span_row_is_no_item_and_no_progress(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    conn.close()
    with open_read_only(settings) as read:
        queue = ops.owner_queue(read, settings, NOW)
    assert [i for i in queue.items if i.kind == "shakedown_failing"] == []
    assert queue.shakedown_progress is None


def test_any_error_in_the_shakedown_read_leaves_the_other_items(
    conn: duckdb.DuckDBPyConnection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # a defect in the diagnostic read must not hide a safety item (safety-reviewer)
    j = _clean(conn)
    j.switch(_at(D4, 12), "engaged")
    conn.close()

    def broken(*_args: object, **_kwargs: object) -> None:
        raise KeyError(42)

    monkeypatch.setattr(shakedown_module, "shakedown", broken)
    with open_read_only(settings) as read:
        items = ops.owner_queue(read, settings, _at(D4, 13)).items
    kinds = [i.kind for i in items]
    assert "kill_switch_engaged" in kinds
    (failing,) = [i for i in items if i.kind == "shakedown_failing"]
    assert failing.detail.startswith("the shakedown could not be read: KeyError: 42")


def test_the_queue_read_on_a_year_of_orders_stays_within_the_read_budget(
    conn: duckdb.DuckDBPyConnection, fixture_store_path: Path
) -> None:
    # The shakedown reads every order, event and fill (not only the span's), so pin
    # its cost on a journal of about a year of two books (5,000 orders) before the
    # span: well under half the lock wait, as the other owner reads are.
    j = _clean(conn)
    old = date(2026, 10, 8)
    run_id = j.run(old, hour=15)
    decision_id = j.decision(run_id, old)
    stamp = _at(old, 19)
    conn.execute(
        "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, phase, "
        "security_id, symbol, side, notional, quantity, position_side, "
        "sells_in_flight_at_submit, known_at, ingested_at) "
        "SELECT 'bulk-' || i, ?, ?, ?, 1, 'buy', 'SEC_A', 'SEC_A', 'buy', NULL, 1.0, 'long', "
        "FALSE, ?, ? FROM range(5000) t(i)",
        [decision_id, run_id, old, stamp, stamp],
    )
    conn.execute(
        "INSERT INTO order_events (client_order_id, event_at, status, known_at, ingested_at) "
        "SELECT 'bulk-' || i, ?, 'filled', ?, ? FROM range(5000) t(i)",
        [stamp, stamp, stamp],
    )
    first = j.next_id("fills")
    conn.execute(
        "INSERT INTO fills (fill_id, client_order_id, filled_at, quantity, price, "
        "price_implied, broker_fill_id, source, known_at, ingested_at) "
        "SELECT ? + i, 'bulk-' || i, ?, 1.0, 100.0, FALSE, 'bulk-f' || i, 'broker_feed', ?, ? "
        "FROM range(5000) t(i)",
        [first, stamp, stamp, stamp],
    )
    conn.close()
    fast = Settings(
        _env_file=None, store={"path": str(fixture_store_path), "lock_retry_seconds": 4}
    )
    with open_read_only(fast) as read:
        ops.owner_queue(read, fast, NOW)  # warm-up: the calendar and imports
        # Best of 3: the bound guards the read, not a busy CI runner (#2348).
        elapsed_times = []
        for _ in range(3):
            started = time.monotonic()
            queue = ops.owner_queue(read, fast, NOW)
            elapsed_times.append(time.monotonic() - started)
    assert queue.shakedown_progress is not None  # the shakedown really ran
    assert min(elapsed_times) < fast.store.lock_retry_seconds / 2
