"""Exit-criteria check (Phase 4 spec req 15; plan T66): `paper check`'s four
queries against the latest `paper_windows` row, through `check.check`."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tradepartner.calendar import next_session, session_close
from tradepartner.config import Settings
from tradepartner.execution import check as check_module
from tradepartner.execution import outcomes as outcomes_module
from tradepartner.execution.check import check
from tradepartner.execution.plan import stop_session
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    DecisionRow,
    FillRow,
    OrderRow,
    OverrideRow,
    PaperReportRow,
    PaperWindowRow,
    PaperWindowStopRow,
    append,
)

T0 = date(2026, 9, 30)  # a rebalance session, month 0 start
T1 = date(2026, 10, 30)  # month 0 end / month 1 start
T2 = date(2026, 11, 30)  # month 1 end: the last completed rebalance

WINDOW_ID = 1
HYPOTHESIS_ID = 1
TRIAL_ID = 1
MIN_REBALANCES = 2
MIN_OVERRIDE_REASON_CHARS = 20
#: After close(T2), so `_last_completed_rebalance_session` resolves to T2.
NOW = datetime(2026, 12, 15, 12, tzinfo=UTC)


def _utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 20, tzinfo=UTC)


_T0_UTC = _utc(T0)


@pytest.fixture
def settings(fixture_store_path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(fixture_store_path)})


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_module, "utc_now", lambda: NOW)


def _frozen_json() -> str:
    return json.dumps(
        {
            "paper.min_rebalances": MIN_REBALANCES,
            "paper.min_override_reason_chars": MIN_OVERRIDE_REASON_CHARS,
            "paper.tracking_k": 2.0,
            "paper.tracking_rule": "raw",
            "execution.fill_price": "close",
        }
    )


def _insert_hypothesis(conn: duckdb.DuckDBPyConnection) -> None:
    params = json.dumps({"costs.per_side_bps": 5.0})
    conn.execute(
        "INSERT INTO hypotheses (hypothesis_id, slug, family, title, doc_path, doc_sha256, "
        "params_json, params_sha256, in_sample_start, holdout_start, holdout_end, "
        "registered_at, registered_by) VALUES (?, 'h-check', 'momentum', 'check', "
        "'docs/hypotheses/h-check.md', '0'||repeat('0', 63), ?, '0'||repeat('0', 63), "
        "?, ?, ?, ?, 'test')",
        [HYPOTHESIS_ID, params, T0, T0, T0, _utc(T0)],
    )


def _insert_trial(
    conn: duckdb.DuckDBPyConnection,
    *,
    equity_t1: float = 100_000.0,
    cost_paid_t0: float = 0.0,
) -> None:
    conn.execute(
        "INSERT INTO trials (trial_id, hypothesis_id, kind, started_at, start_session, "
        "end_session, code_version, synthetic, run_by) VALUES "
        "(?, ?, 'tracking', ?, ?, ?, 'test', FALSE, 'test')",
        [TRIAL_ID, HYPOTHESIS_ID, _utc(T0), T0, T2],
    )
    for session, equity in ((T0, 100_000.0), (T1, equity_t1), (T2, 100_000.0)):
        conn.execute(
            "INSERT INTO trial_equity (trial_id, series, cost_per_side_bps, session, equity) "
            "VALUES (?, 'strategy', 5.0, ?, ?)",
            [TRIAL_ID, session, equity],
        )
    for session, cost_paid in ((T0, cost_paid_t0), (T1, 0.0)):
        conn.execute(
            "INSERT INTO trial_rebalances (trial_id, cost_per_side_bps, session, fill_session, "
            "n_universe, n_static_listings, n_targets, turnover, cost_paid, n_missing_fill, "
            "n_delisting_exits, n_stale_exits, n_excluded_no_history, n_dropped_dividends, "
            "n_late_dividends) VALUES (?, 5.0, ?, ?, 1, 1, 1, 0.0, ?, 0, 0, 0, 0, 0, 0)",
            [TRIAL_ID, session, session, cost_paid],
        )


def _window(*, started: datetime = _T0_UTC) -> PaperWindowRow:
    return PaperWindowRow(
        window_id=WINDOW_ID,
        hypothesis_id=HYPOTHESIS_ID,
        first_rebalance_session=T0,
        account_id="PA1",
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=started,
        frozen_json=_frozen_json(),
        frozen_sha256="0" * 64,
        known_at=started,
        ingested_at=started,
    )


def _insert_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    session: date,
    *,
    invoked_by: str = "scheduler",
    kind: str = "rebalance",
) -> None:
    conn.execute(
        "INSERT INTO paper_runs (run_id, window_id, session, kind, started_at, invoked_by, "
        "code_version, known_at, ingested_at) VALUES (?, ?, ?, ?, ?, ?, 'test', ?, ?)",
        [run_id, WINDOW_ID, session, kind, _utc(session), invoked_by, _utc(session), _utc(session)],
    )


def _insert_rebalance_event(conn: duckdb.DuckDBPyConnection, session: date, run_id: int) -> None:
    conn.execute(
        "INSERT INTO rebalance_events (rebalance_session, run_id, status, known_at, "
        "ingested_at) VALUES (?, ?, 'executed', ?, ?)",
        [session, run_id, _utc(session), _utc(session)],
    )


def _insert_cash_mark(conn: duckdb.DuckDBPyConnection, run_id: int, session: date) -> None:
    conn.execute(
        "INSERT INTO positions_daily (run_id, session, security_id, quantity, cash, "
        "known_at, ingested_at) VALUES (?, ?, NULL, 0.0, 100000.0, ?, ?)",
        [run_id, session, _utc(session), _utc(session)],
    )


def _insert_decision(
    conn: duckdb.DuckDBPyConnection, decision_id: int, run_id: int, session: date
) -> None:
    conn.execute(
        "INSERT INTO decisions (decision_id, run_id, rebalance_session, security_id, side, "
        "planned_notional, whole_share, decision, known_at, ingested_at) "
        "VALUES (?, ?, ?, 'SEC_A', 'buy', 1000.0, FALSE, 'trade', ?, ?)",
        [decision_id, run_id, session, _utc(session), _utc(session)],
    )


def _insert_complete_order(conn: duckdb.DuckDBPyConnection, *, with_outcome: bool = True) -> None:
    """One order at T0: decision 1 -> order 'o1' -> filled -> a position_return
    outcome, the chain check's passing baseline."""
    coid = "o1"
    conn.execute(
        "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, phase, "
        "security_id, symbol, side, notional, sells_in_flight_at_submit, known_at, "
        "ingested_at) VALUES (?, 1, 1, ?, 1, 'buy', 'SEC_A', 'SEC_A', 'buy', 1000.0, FALSE, "
        "?, ?)",
        [coid, T0, _utc(T0), _utc(T0)],
    )
    conn.execute(
        "INSERT INTO order_events (client_order_id, event_at, status, known_at, ingested_at) "
        "VALUES (?, ?, 'filled', ?, ?)",
        [coid, _utc(T0), _utc(T0), _utc(T0)],
    )
    append(
        conn,
        FillRow(
            client_order_id=coid,
            filled_at=_utc(T0),
            quantity=10.0,
            price=100.0,
            price_implied=False,
            broker_fill_id="f1",
            source="broker_feed",
            known_at=_utc(T0),
            ingested_at=_utc(T0),
        ),
    )
    if with_outcome:
        conn.execute(
            "INSERT INTO outcomes (client_order_id, through_session, kind, value, "
            "known_at, ingested_at) VALUES (?, ?, 'position_return', 1.0, ?, ?)",
            [coid, T1, _utc(T1), _utc(T1)],
        )


def _insert_override(conn: duckdb.DuckDBPyConnection, reason: str) -> None:
    now = _utc(T0)
    append(
        conn,
        OverrideRow(
            window_id=WINDOW_ID,
            made_at=now,
            rebalance_session=T0,
            security_id="SEC_B",
            kind="keep_name",
            reason=reason,
            known_at=now,
            ingested_at=now,
        ),
    )


def _insert_paper_report(conn: duckdb.DuckDBPyConnection, through_session: date) -> None:
    now = _utc(T2)
    append(
        conn,
        PaperReportRow(
            window_id=WINDOW_ID,
            trial_id=TRIAL_ID,
            through_session=through_session,
            run_at=now,
            known_at=now,
            ingested_at=now,
        ),
    )


def _build_passing_fixture(
    conn: duckdb.DuckDBPyConnection,
    *,
    equity_t1: float = 100_000.0,
    report_through: date = T2,
    t0_executed_by: str = "scheduler",
    t1_executed_by: str | None = "scheduler",
    extra_no_trade_run: bool = False,
    duplicate_t0_run: bool = False,
    t0_missed: bool = False,
    chain_outcome: bool = True,
    override_reason: str = "a sufficiently long documented reason",
) -> None:
    _insert_hypothesis(conn)
    _insert_trial(conn, equity_t1=equity_t1)
    window = _window()
    append(conn, window)
    _insert_run(conn, 1, T0, invoked_by=t0_executed_by)
    _insert_run(conn, 2, T1, invoked_by=t1_executed_by or "scheduler")
    _insert_run(conn, 3, T2, invoked_by="scheduler")
    if duplicate_t0_run:
        _insert_run(conn, 11, T0, invoked_by="scheduler", kind="catch_up")
        _insert_rebalance_event(conn, T0, 11)
    if extra_no_trade_run:
        _insert_run(conn, 12, T1, invoked_by="scheduler", kind="catch_up")
    if t0_missed:
        conn.execute(
            "INSERT INTO rebalance_events (rebalance_session, run_id, status, known_at, "
            "ingested_at) VALUES (?, 1, 'missed', ?, ?)",
            [T0, _utc(T0), _utc(T0)],
        )
    else:
        _insert_rebalance_event(conn, T0, 1)
    if t1_executed_by is not None:
        _insert_rebalance_event(conn, T1, 2)
    _insert_cash_mark(conn, 1, T0)
    _insert_cash_mark(conn, 2, T1)
    _insert_cash_mark(conn, 3, T2)
    _insert_decision(conn, 1, 1, T0)
    _insert_complete_order(conn, with_outcome=chain_outcome)
    _insert_override(conn, override_reason)
    _insert_paper_report(conn, report_through)


def test_passes_on_a_fixture_meeting_every_req_15_criterion(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn)
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    assert {line.name for line in lines} == {
        "rebalance_count",
        "tracking",
        "chain",
        "override_reason",
    }
    assert all(line.passed for line in lines), lines


def test_rebalance_count_fails_one_short_when_a_rebalance_is_tty_executed(
    settings: Settings,
) -> None:
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn, t1_executed_by="tty")
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    line = next(line for line in lines if line.name == "rebalance_count")
    assert not line.passed
    assert "1 scheduler-executed" in line.detail
    assert str(T1) in line.detail.split("tty-executed")[-1]


def test_rebalance_count_lists_tty_and_no_trade_runs_without_counting_them(
    settings: Settings,
) -> None:
    with open_for_write(settings) as conn:
        _build_passing_fixture(
            conn,
            t1_executed_by="tty",
            extra_no_trade_run=True,
        )
        # A single scheduler-executed rebalance (T0) is enough once min_rebalances
        # is lowered to 1, so this fixture can pass while still exercising both
        # "listed, not counted" cases together.
        conn.execute(
            "UPDATE paper_windows SET frozen_json = ? WHERE window_id = ?",
            [
                json.dumps(
                    {
                        "paper.min_rebalances": 1,
                        "paper.min_override_reason_chars": MIN_OVERRIDE_REASON_CHARS,
                        "paper.tracking_k": 2.0,
                        "paper.tracking_rule": "raw",
                        "execution.fill_price": "close",
                    }
                ),
                WINDOW_ID,
            ],
        )
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    line = next(line for line in lines if line.name == "rebalance_count")
    assert line.passed
    assert str(T1) in line.detail.split("tty-executed")[-1].split("no-trade")[0]
    assert str(T1) in line.detail.split("no-trade fill-session runs")[-1]


def test_two_executed_rows_on_one_rebalance_session_count_once(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn, duplicate_t0_run=True)
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    line = next(line for line in lines if line.name == "rebalance_count")
    assert line.passed
    assert "2 scheduler-executed rebalance session(s)" in line.detail
    assert str(T0) in line.detail
    assert str(T1) in line.detail


def test_tracking_fails_one_short_of_compared_months_on_an_excluded_month(
    settings: Settings,
) -> None:
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn, t0_missed=True)
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    line = next(line for line in lines if line.name == "tracking")
    assert not line.passed
    assert "1 compared non-excluded month(s)" in line.detail


def test_tracking_fails_as_report_stale_when_through_session_is_not_last_completed(
    settings: Settings,
) -> None:
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn, report_through=T1)
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    line = next(line for line in lines if line.name == "tracking")
    assert not line.passed
    assert "report stale" in line.detail


def test_tracking_fails_on_a_tracking_miss(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn, equity_t1=50_000.0)
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    line = next(line for line in lines if line.name == "tracking")
    assert not line.passed
    assert "tracking check failed" in line.detail


def test_chain_fails_on_an_incomplete_chain_once_due(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn, chain_outcome=False)
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    line = next(line for line in lines if line.name == "chain")
    assert not line.passed
    assert "o1" in line.detail
    assert "position_return" in line.detail


def test_chain_not_due_is_not_a_failure(settings: Settings) -> None:
    """An order of the rebalance just before the last completed one (T1) has no
    run after close(T2) yet, so its missing outcome is not due: the chain check
    must not fail on it."""
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn)
        _insert_decision(conn, 2, 2, T1)
        conn.execute(
            "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, "
            "phase, security_id, symbol, side, notional, sells_in_flight_at_submit, "
            "known_at, ingested_at) VALUES ('o2', 2, 2, ?, 1, 'buy', 'SEC_A', 'SEC_A', "
            "'buy', 1000.0, FALSE, ?, ?)",
            [T1, _utc(T1), _utc(T1)],
        )
        # No terminal event, no fill, no outcome for 'o2': its chain would be
        # incomplete if it were due, but it is not (no run after T2 exists yet).
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    line = next(line for line in lines if line.name == "chain")
    assert line.passed


def test_chain_becomes_due_once_a_run_exists_after_its_threshold(settings: Settings) -> None:
    """The same 'o2' as `test_chain_not_due_is_not_a_failure`, but with a further
    run after T_{i+1} = T2: now its missing terminal event and outcome are due,
    and the check must flag it (pins the `session > threshold` boundary from
    both sides, not just the "not yet due" one)."""
    t3 = outcomes_module._rebalance_after(T2, "month_end")
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn)
        _insert_decision(conn, 2, 2, T1)
        conn.execute(
            "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, "
            "phase, security_id, symbol, side, notional, sells_in_flight_at_submit, "
            "known_at, ingested_at) VALUES ('o2', 2, 2, ?, 1, 'buy', 'SEC_A', 'SEC_A', "
            "'buy', 1000.0, FALSE, ?, ?)",
            [T1, _utc(T1), _utc(T1)],
        )
        _insert_run(conn, 4, t3, invoked_by="scheduler", kind="mark")
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    line = next(line for line in lines if line.name == "chain")
    assert not line.passed
    assert "o2" in line.detail
    assert "is not terminal" in line.detail


# --- req 15 (3)'s rule from req 17 (#571): a live fill after the terminal event --------


def _insert_order(
    conn: duckdb.DuckDBPyConnection,
    coid: str,
    *,
    status: str,
    reason: str | None = None,
    at: datetime = _T0_UTC,
    decision_id: int = 1,
    run_id: int = 1,
    session: date = T0,
) -> None:
    """An order of decision 1 at T0 with one terminal event stamped `at`."""
    conn.execute(
        "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, phase, "
        "security_id, symbol, side, notional, sells_in_flight_at_submit, known_at, "
        "ingested_at) VALUES (?, ?, ?, ?, 1, 'buy', 'SEC_A', 'SEC_A', 'buy', 1000.0, FALSE, "
        "?, ?)",
        [coid, decision_id, run_id, session, _utc(session), _utc(session)],
    )
    conn.execute(
        "INSERT INTO order_events (client_order_id, status, reason, known_at, ingested_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [coid, status, reason, at, at],
    )


def _insert_fill(
    conn: duckdb.DuckDBPyConnection,
    coid: str,
    broker_fill_id: str,
    at: datetime,
    *,
    source: str = "broker_feed",
    superseded_by: int | None = None,
) -> int:
    fill_id = append(
        conn,
        FillRow(
            client_order_id=coid,
            filled_at=at,
            quantity=1.0,
            price=100.0,
            price_implied=source == "broker_status",
            broker_fill_id=broker_fill_id,
            source=source,
            superseded_by=superseded_by,
            known_at=at,
            ingested_at=at,
        ),
    )
    assert fill_id is not None
    return fill_id


def _insert_outcome(conn: duckdb.DuckDBPyConnection, coid: str, kind: str) -> None:
    conn.execute(
        "INSERT INTO outcomes (client_order_id, through_session, kind, value, "
        "known_at, ingested_at) VALUES (?, ?, ?, 0.0, ?, ?)",
        [coid, T1, kind, _utc(T1), _utc(T1)],
    )


def _chain(settings: Settings) -> check_module.CheckLine:
    """The chain line alone: these fixtures' extra fills have no bars for the
    tracking line to price."""
    with open_read_only(settings) as conn:
        return check_module._chain_line(conn, WINDOW_ID, "month_end")


def test_chain_counts_an_owner_settled_order_complete_once_not_executed_is_written(
    settings: Settings,
) -> None:
    """Spec req 17: a `cancelled` / `owner_settled_unknown` order owes `not_executed`
    like any `cancelled` one; once written, its chain is complete."""
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn)
        _insert_order(conn, "tp-settled", status="cancelled", reason="owner_settled_unknown")
    line = _chain(settings)
    assert not line.passed
    assert "tp-settled" in line.detail and "not_executed" in line.detail

    with open_for_write(settings) as conn:
        _insert_outcome(conn, "tp-settled", "not_executed")
    assert _chain(settings).passed


def test_chain_lists_a_live_fill_journaled_after_the_terminal_event(settings: Settings) -> None:
    """Spec req 15 (3) as amended by req 17: a live fill whose `known_at` is after its
    order's terminal event is an incomplete chain whatever its outcome rows, so a
    settlement can never silently absorb a real fill."""
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn)
        _insert_order(conn, "tp-settled", status="cancelled", reason="owner_settled_unknown")
        _insert_outcome(conn, "tp-settled", "not_executed")
        _insert_fill(conn, "tp-settled", "late-1", _T0_UTC + timedelta(days=1))
        _insert_outcome(conn, "tp-settled", "position_return")
    line = _chain(settings)
    assert not line.passed
    assert "tp-settled" in line.detail
    assert "live fill journaled after its terminal event" in line.detail


def test_chain_lists_a_late_live_fill_even_before_the_outcome_is_due(settings: Settings) -> None:
    """The rule reads the journal as it stands: an order of T1, whose outcome is not
    due yet, is still listed once a live fill lands after its terminal event."""
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn)
        _insert_decision(conn, 2, 2, T1)
        _insert_order(
            conn, "o2", status="expired", at=_utc(T1), decision_id=2, run_id=2, session=T1
        )
        _insert_fill(conn, "o2", "late-2", _utc(T1) + timedelta(hours=1))
    line = _chain(settings)
    assert not line.passed
    assert "o2" in line.detail and "after its terminal event" in line.detail


def test_chain_ignores_a_fill_journaled_with_its_terminal_event(settings: Settings) -> None:
    """A collection journals the fill and the terminal event under one stamp: not
    after."""
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn)
    line = _chain(settings)
    assert line.passed


def test_chain_ignores_a_superseded_feed_fill_after_a_synthetic_one(settings: Settings) -> None:
    """Req 8's synthetic fill completes the order; the feed fill that arrives later is
    journaled `superseded_by` it, and is no live fill, so the chain stays complete."""
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn)
        _insert_order(conn, "tp-synthetic", status="filled")
        synthetic = _insert_fill(
            conn, "tp-synthetic", "synthetic-1", _T0_UTC, source="broker_status"
        )
        _insert_outcome(conn, "tp-synthetic", "position_return")
        _insert_fill(
            conn, "tp-synthetic", "feed-1", _T0_UTC + timedelta(days=1), superseded_by=synthetic
        )
    assert _chain(settings).passed


def test_chain_due_threshold_follows_order_phase_for_a_forced_exit_in_a_rebalance_batch(
    settings: Settings,
) -> None:
    """A forced exit traded inside a rebalance batch is submitted with phase
    `sell` (`wrapper.py`), not `exit`, and its decision carries no
    `rebalance_session`: its outcome is due at T_{i+1} of the rebalance it
    rode along with (`outcomes._horizon`), not right after its own session.
    Here it fills on a fill session just after T1 (so "right after its own
    session" would wrongly make it due by T2, the baseline fixture's latest
    run); the correct threshold, T2 itself, means it is not yet due with no
    run after T2 in the store."""
    forced_exit_session = next_session(T1)  # the fill session right after T1, well before T2
    assert T1 < forced_exit_session < T2
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn)
        conn.execute(
            "INSERT INTO decisions (decision_id, run_id, rebalance_session, security_id, "
            "side, planned_notional, whole_share, decision, known_at, ingested_at) "
            "VALUES (3, 3, NULL, 'SEC_B', 'sell', 500.0, FALSE, 'forced_exit', ?, ?)",
            [_utc(forced_exit_session), _utc(forced_exit_session)],
        )
        conn.execute(
            "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, "
            "phase, security_id, symbol, side, notional, sells_in_flight_at_submit, "
            "known_at, ingested_at) VALUES ('o3', 3, 3, ?, 1, 'sell', 'SEC_B', 'SEC_B', "
            "'sell', 500.0, FALSE, ?, ?)",
            [forced_exit_session, _utc(forced_exit_session), _utc(forced_exit_session)],
        )
        conn.execute(
            "INSERT INTO order_events (client_order_id, event_at, status, known_at, "
            "ingested_at) VALUES ('o3', ?, 'filled', ?, ?)",
            [_utc(forced_exit_session), _utc(forced_exit_session), _utc(forced_exit_session)],
        )
        append(
            conn,
            FillRow(
                client_order_id="o3",
                filled_at=_utc(forced_exit_session),
                quantity=5.0,
                price=50.0,
                price_implied=False,
                broker_fill_id="f3",
                source="broker_feed",
                known_at=_utc(forced_exit_session),
                ingested_at=_utc(forced_exit_session),
            ),
        )
        # A bar for SEC_B on the fill session: `compare_months`'s fill-timing
        # term (the tracking check, exercised regardless of this test's own
        # concern) needs one for any fill inside a compared month.
        conn.execute(
            "INSERT INTO prices_daily (security_id, session, open, high, low, close, "
            "volume, known_at, ingested_at, source, provenance) VALUES ('SEC_B', ?, 50.0, "
            "50.0, 50.0, 50.0, 0, ?, ?, 'test', 'bar')",
            [forced_exit_session, _utc(forced_exit_session), _utc(forced_exit_session)],
        )
        # No outcome row for 'o3': due only strictly after T2, not after its own
        # (mid-T1..T2) session.
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    line = next(line for line in lines if line.name == "chain")
    assert line.passed, line.detail


def _order(session: date, phase: str) -> OrderRow:
    return OrderRow(
        client_order_id="tp-threshold",
        decision_id=1,
        run_id=1,
        session=session,
        attempt=1,
        phase=phase,
        security_id="SEC_A",
        symbol="SEC_A",
        side="buy",
        sells_in_flight_at_submit=False,
        known_at=_utc(session),
        ingested_at=_utc(session),
    )


def test_order_due_threshold_is_the_orders_own_session_for_a_forced_exit() -> None:
    """Pins `_order_due_threshold`'s call-through to the shared
    `outcomes.outcome_horizon` (#597): a forced exit is due at its own
    session, whatever decision it carries."""
    order = _order(T1, phase="exit")
    assert check_module._order_due_threshold(order, None, "month_end") == T1


def test_order_due_threshold_uses_the_decisions_rebalance_session() -> None:
    decision = DecisionRow(
        decision_id=1,
        run_id=1,
        rebalance_session=T0,
        security_id="SEC_A",
        whole_share=False,
        decision="trade",
        known_at=_T0_UTC,
        ingested_at=_T0_UTC,
    )
    order = _order(next_session(T0), phase="buy")
    assert check_module._order_due_threshold(order, decision, "month_end") == T1


def test_order_due_threshold_falls_back_to_the_rebalance_before_the_order() -> None:
    order = _order(next_session(T1), phase="buy")
    assert check_module._order_due_threshold(order, None, "month_end") == T2


def test_override_reason_fails_on_a_whitespace_padded_reason_under_the_minimum(
    settings: Settings,
) -> None:
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn, override_reason="   too short   ")
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    line = next(line for line in lines if line.name == "override_reason")
    assert not line.passed
    assert "need >= 20" in line.detail


def test_check_never_reads_the_wash_sale_tables() -> None:
    source = Path(check_module.__file__).read_text()
    assert "wash_sale" not in source.lower()
    assert "WashSaleFlagRow" not in source
    assert "DisposalRow" not in source
    assert "LotRow" not in source
    for table in ("lots", "disposals", "wash_sale_flags"):
        assert f"FROM {table}" not in source
        assert f"{table}_for" not in source


def test_raises_when_no_window_exists(settings: Settings) -> None:
    with open_for_write(settings):
        pass  # just migrate the schema, no window
    with (
        open_read_only(settings) as conn,
        pytest.raises(ValueError, match="no paper window"),
    ):
        check(conn, settings)


def test_each_line_names_its_query(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        _build_passing_fixture(conn)
    with open_read_only(settings) as conn:
        lines = check(conn, settings)
    assert all(line.query for line in lines)


def test_last_completed_rebalance_session() -> None:
    assert (
        check_module._last_completed_rebalance_session(
            session_close(T2) + timedelta(seconds=1), "month_end"
        )
        == T2
    )
    assert check_module._last_completed_rebalance_session(session_close(T2), "month_end") == T2


@pytest.mark.parametrize(
    ("requested_at", "expected"),
    [
        pytest.param(datetime(2026, 10, 30, 22, tzinfo=UTC), T1, id="after-the-close"),
        # Saturday 2026-10-31: the next session, Monday 2026-11-02.
        pytest.param(datetime(2026, 10, 31, 15, tzinfo=UTC), date(2026, 11, 2), id="saturday"),
    ],
)
def test_check_stop_session_is_plan_stop_session(requested_at: datetime, expected: date) -> None:
    """check reads the stop session through `plan.stop_session` (#592), the one
    rule `run` and `report` share, from the earliest `requested` row."""
    stops = [
        PaperWindowStopRow(
            window_id=WINDOW_ID,
            at=at,
            state=state,
            known_at=at,
            ingested_at=at,
        )
        for at, state in (
            (requested_at + timedelta(days=3), "closed"),
            (requested_at + timedelta(days=1), "requested"),
            (requested_at, "requested"),
        )
    ]
    assert check_module._stop_session_of(stops) == stop_session(requested_at) == expected
