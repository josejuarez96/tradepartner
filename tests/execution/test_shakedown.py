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
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.execution import shakedown as shakedown_module
from tradepartner.execution.shakedown import Shakedown, ShakedownLine, shakedown
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
    ) -> int:
        decision_id = self.next_id("decisions")
        self.conn.execute(
            "INSERT INTO decisions (decision_id, run_id, rebalance_session, security_id, side, "
            "planned_notional, planned_quantity, whole_share, decision, known_at, ingested_at) "
            "VALUES (?, ?, ?, 'SEC_A', 'buy', ?, ?, FALSE, 'trade', ?, ?)",
            [
                decision_id,
                run_id,
                session,
                planned_notional,
                planned_quantity,
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
    ) -> None:
        stamp = _at(session, 19)
        self.conn.execute(
            "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, phase, "
            "security_id, symbol, side, notional, quantity, sells_in_flight_at_submit, known_at, "
            "ingested_at) VALUES (?, ?, ?, ?, 1, 'buy', 'SEC_A', 'SEC_A', 'buy', ?, ?, FALSE, "
            "?, ?)",
            [coid, decision_id, run_id, session, notional, quantity, stamp, stamp],
        )
        self.conn.execute(
            "INSERT INTO order_events (client_order_id, event_at, status, known_at, ingested_at) "
            "VALUES (?, ?, 'filled', ?, ?)",
            [coid, stamp, stamp, stamp],
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
