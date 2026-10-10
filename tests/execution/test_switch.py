"""The kill switch (Phase 4 plan T59; spec req 5, "Kill switch and reconciliation"
acceptance; ADR 0010 point 4): the derived state, the writers and the drawdown
trigger."""

from __future__ import annotations

import inspect
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tradepartner.config import Settings
from tradepartner.execution import switch
from tradepartner.execution.switch import (
    ReleaseRefused,
    SwitchState,
    WriteFailed,
    derive,
    drawdown_armed,
    drawdown_check,
    drawdown_peak,
    engage,
    engage_from_overrides,
    faulted_runs,
    open_window_of,
    release,
)
from tradepartner.store import schema
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    JournalIntegrityError,
    KillSwitchRow,
    OverrideRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PaperWindowStopRow,
    ReconciliationRow,
    ResumeInvocationRow,
    append,
    kill_switch_events_for,
    runs_for,
    unconsumed_kill_switch_overrides,
)

_ROOT = Path(__file__).resolve().parents[2]
_T0 = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)
_SESSION = date(2026, 10, 1)
_WINDOW = 1


def _at(minutes: int) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _stamp(minutes: int) -> dict[str, datetime]:
    return {"known_at": _at(minutes), "ingested_at": _at(minutes)}


def _window(window_id: int = _WINDOW, starting_equity: float = 1000.0) -> PaperWindowRow:
    return PaperWindowRow(
        window_id=window_id,
        hypothesis_id=1,
        first_rebalance_session=_SESSION,
        account_id="PA1",
        starting_cash=starting_equity,
        starting_equity=starting_equity,
        code_version="abc",
        started_at=_at(0),
        frozen_json="{}",
        frozen_sha256="0" * 64,
        **_stamp(0),
    )


def _run(run_id: int, minutes: int, window_id: int = _WINDOW) -> PaperRunRow:
    return PaperRunRow(
        run_id=run_id,
        window_id=window_id,
        session=_SESSION,
        kind="rebalance",
        started_at=_at(minutes),
        invoked_by="scheduler",
        code_version="abc",
        **_stamp(minutes),
    )


def _result(run_id: int, status: str, minutes: int) -> PaperRunResultRow:
    return PaperRunResultRow(
        run_id=run_id, finished_at=_at(minutes), status=status, clock_fault=False, **_stamp(minutes)
    )


def _row(
    event_id: int,
    state: str,
    minutes: int,
    *,
    source: str = "owner",
    window_id: int = _WINDOW,
    peak_equity: float | None = None,
) -> KillSwitchRow:
    return KillSwitchRow(
        event_id=event_id,
        window_id=window_id,
        at=_at(minutes),
        state=state,
        source=source,
        peak_equity=peak_equity,
        **_stamp(minutes),
    )


# derive: the derived state, pure over the window's rows.


def test_a_window_with_no_rows_and_no_runs_is_not_engaged() -> None:
    state = derive(_window(), [], [], [], reading_run=None, lock_free=True)
    assert state == SwitchState(engaged=False, run_in_progress=False, causes=())


def test_the_latest_engaged_row_engages() -> None:
    state = derive(_window(), [_row(1, "engaged", 5)], [], [], reading_run=None, lock_free=True)
    assert state.engaged
    assert state.causes == ("kill_switch event 1 engaged (owner)",)


def test_a_release_after_the_engaged_row_ends_it() -> None:
    rows = [_row(1, "engaged", 5), _row(2, "released", 9)]
    assert not derive(_window(), rows, [], [], reading_run=None, lock_free=True).engaged


def test_the_latest_row_is_the_last_written_not_the_latest_stamped() -> None:
    # A halt-path row after a ClockError carries a real-time stamp that can sort
    # before an earlier row's; `event_id` is write order.
    rows = [_row(1, "released", 9), _row(2, "engaged", 5)]
    assert derive(_window(), rows, [], [], reading_run=None, lock_free=True).engaged


def test_engaged_row_is_the_latest_row_by_write_order_when_it_is_engaged() -> None:
    """`engaged_row` is the row `causes` names first (#699): the run's
    `kill_switch` alert reads it, so the alert and the derivation share one
    definition of "latest engaged row"."""
    other_window = _row(9, "engaged", 1, window_id=_WINDOW + 1)
    rows = [other_window, _row(1, "released", 9), _row(2, "engaged", 5, source="drawdown")]
    state = derive(_window(), rows, [], [], reading_run=None, lock_free=True)
    assert state.engaged_row == rows[2]
    assert state.causes == ("kill_switch event 2 engaged (drawdown)",)


@pytest.mark.parametrize(
    ("rows", "runs", "results"),
    [
        ([], [], []),
        ([_row(1, "engaged", 5), _row(2, "released", 9)], [], []),
        # engaged by derivation alone: a faulted run after the release
        (
            [_row(1, "engaged", 5), _row(2, "released", 9)],
            [_run(1, 10)],
            [_result(1, "failed", 12)],
        ),
        # only another window's row is engaged
        ([_row(1, "engaged", 5, window_id=_WINDOW + 1)], [], []),
    ],
)
def test_engaged_row_is_none_unless_the_latest_row_is_engaged(
    rows: list[KillSwitchRow], runs: list[PaperRunRow], results: list[PaperRunResultRow]
) -> None:
    state = derive(_window(), rows, runs, results, reading_run=None, lock_free=True)
    assert state.engaged_row is None


@pytest.mark.parametrize("status", ["halted", "crashed", "failed"])
def test_an_earlier_faulted_run_engages_with_no_engaged_row(status: str) -> None:
    runs = [_run(1, 0), _run(2, 60)]
    results = [_result(1, status, 5)]
    state = derive(_window(), [], runs, results, reading_run=2, lock_free=True)
    assert state.engaged
    assert state.causes == (f"run 1 {status}",)


@pytest.mark.parametrize("status", ["ok", "stale", "skipped_kill_switch", "no_session"])
def test_an_earlier_run_with_any_other_result_does_not_engage(status: str) -> None:
    runs = [_run(1, 0), _run(2, 60)]
    state = derive(_window(), [], runs, [_result(1, status, 5)], reading_run=2, lock_free=True)
    assert not state.engaged


def test_an_earlier_run_without_a_result_engages_when_the_lock_is_free() -> None:
    state = derive(_window(), [], [_run(1, 0)], [], reading_run=None, lock_free=True)
    assert state.engaged
    assert state.causes == ("run 1 unfinished",)
    assert not state.run_in_progress


def test_the_reading_run_never_counts_itself() -> None:
    state = derive(_window(), [], [_run(1, 0)], [], reading_run=1, lock_free=True)
    assert not state.engaged


def test_a_release_after_the_faulted_run_started_clears_it() -> None:
    runs = [_run(1, 0), _run(2, 60)]
    rows = [_row(1, "engaged", 3, source="fault"), _row(2, "released", 30)]
    state = derive(_window(), rows, runs, [_result(1, "halted", 5)], reading_run=2, lock_free=True)
    assert not state.engaged


def test_a_release_before_the_faulted_run_started_does_not_clear_it() -> None:
    runs = [_run(1, 10), _run(2, 60)]
    rows = [_row(1, "engaged", 3), _row(2, "released", 5)]
    state = derive(
        _window(), rows, runs, [_result(1, "crashed", 15)], reading_run=2, lock_free=True
    )
    assert state.engaged
    assert state.causes == ("run 1 crashed",)


def test_faulted_runs_are_the_runs_a_release_would_clear() -> None:
    # #397: resume judges these runs' rejections; the rule is `derive`'s.
    runs = [_run(1, 0), _run(2, 40), _run(3, 60), _run(4, 70), _run(5, 0, window_id=_WINDOW + 1)]
    rows = [_row(1, "engaged", 3), _row(2, "released", 30), _row(3, "engaged", 61)]
    results = [
        _result(1, "halted", 5),  # cleared by the release at 30
        _result(2, "ok", 45),
        _result(3, "failed", 65),
        _result(4, "crashed", 75),
        _result(5, "halted", 5),  # another window's
    ]
    assert faulted_runs(_window(), rows, runs, results) == (3, 4)
    state = derive(_window(), rows, runs, results, reading_run=None, lock_free=True)
    assert state.causes[1:] == ("run 3 failed", "run 4 crashed")


def test_a_release_never_clears_an_unfinished_run() -> None:
    # The release's clock may run ahead of the crashed run's: a release stamped
    # after its `started_at` still did not see it (resume closes it first).
    runs = [_run(1, 0), _run(2, 60)]
    rows = [_row(1, "engaged", 3), _row(2, "released", 120)]
    state = derive(_window(), rows, runs, [_result(1, "ok", 5)], reading_run=None, lock_free=True)
    assert state.engaged
    assert state.causes == ("run 2 unfinished",)


def test_a_release_before_the_faulted_run_finished_does_not_clear_it() -> None:
    runs = [_run(1, 0), _run(2, 60)]
    rows = [_row(1, "engaged", 3), _row(2, "released", 10)]
    results = [_result(1, "halted", 20)]
    state = derive(_window(), rows, runs, results, reading_run=2, lock_free=True)
    assert state.causes == ("run 1 halted",)


def test_a_held_lock_shows_the_latest_unfinished_run_as_in_progress() -> None:
    runs = [_run(1, 0), _run(2, 60)]
    state = derive(_window(), [], runs, [_result(1, "ok", 5)], reading_run=None, lock_free=False)
    assert state == SwitchState(engaged=False, run_in_progress=True, causes=())


def test_a_held_lock_does_not_hide_an_older_unfinished_run() -> None:
    # Only one process holds the lock, so only the latest run can be live.
    runs = [_run(1, 0), _run(2, 60)]
    state = derive(_window(), [], runs, [], reading_run=None, lock_free=False)
    assert state.engaged
    assert state.run_in_progress
    assert state.causes == ("run 1 unfinished",)


def test_a_held_lock_does_not_hide_an_engaged_row() -> None:
    rows = [_row(1, "engaged", 5)]
    state = derive(_window(), rows, [_run(1, 10)], [], reading_run=None, lock_free=False)
    assert state.engaged
    assert state.run_in_progress


def test_another_windows_rows_and_runs_are_ignored() -> None:
    # A closed window's halted run and its engaged row never engage the new window.
    other = 7
    rows = [_row(1, "engaged", 5, window_id=other)]
    runs = [_run(1, 0, window_id=other), _run(2, 60)]
    results = [_result(1, "halted", 5), _result(2, "ok", 65)]
    state = derive(_window(), rows, runs, results, reading_run=None, lock_free=True)
    assert not state.engaged


def test_all_causes_are_listed_in_order() -> None:
    runs = [_run(1, 0), _run(2, 20), _run(3, 60)]
    results = [_result(1, "failed", 5)]
    rows = [_row(1, "engaged", 30, source="drawdown")]
    state = derive(_window(), rows, runs, results, reading_run=3, lock_free=True)
    assert state.causes == (
        "kill_switch event 1 engaged (drawdown)",
        "run 1 failed",
        "run 2 unfinished",
    )


def test_no_setting_reaches_the_derivation() -> None:
    for fn in (derive, drawdown_check, drawdown_peak, drawdown_armed):
        params = inspect.signature(fn).parameters.values()
        assert not any("Settings" in str(p.annotation) for p in params), fn.__name__


# The drawdown trigger (ADR 0010 point 1: strict, once per crossing, peak reset on release).


@pytest.mark.parametrize(
    ("equity", "fires"),
    [(1000.0, False), (800.0, False), (700.0, False), (699.99, True), (500.0, True)],
)
def test_drawdown_fires_strictly_beyond_the_threshold(equity: float, fires: bool) -> None:
    assert drawdown_check(equity, 1000.0, 0.30, armed=True) is fires


@pytest.mark.parametrize(("peak", "max_drawdown"), [(100.0, 0.3), (1234.5, 0.1), (3.0, 0.7)])
def test_drawdown_at_exactly_the_threshold_does_not_fire(peak: float, max_drawdown: float) -> None:
    for equity in (peak - peak * max_drawdown, peak * (1 - max_drawdown)):
        assert not drawdown_check(equity, peak, max_drawdown, armed=True)
    assert drawdown_check(peak * (1 - max_drawdown) - 0.01, peak, max_drawdown, armed=True)


@pytest.mark.parametrize(
    ("equity", "peak"),
    [
        (float("nan"), 1000.0),
        (float("-inf"), 1000.0),
        (0.0, float("nan")),
        (0.0, float("inf")),
        (0.0, 0.0),
        (-5.0, -10.0),
    ],
)
def test_drawdown_refuses_a_value_it_cannot_judge(equity: float, peak: float) -> None:
    with pytest.raises(ValueError, match="drawdown"):
        drawdown_check(equity, peak, 0.30, armed=True)


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), 0.0, -1.0])
def test_the_peak_refuses_an_unusable_release_value(bad: float | None) -> None:
    rows = [_row(1, "engaged", 5), _row(2, "released", 9, peak_equity=bad)]
    with pytest.raises(ValueError, match="peak"):
        drawdown_peak(_window(), rows)


def test_a_disarmed_drawdown_never_fires() -> None:
    assert not drawdown_check(1.0, 1000.0, 0.30, armed=False)


def test_the_peak_is_the_starting_equity_then_the_last_release() -> None:
    window = _window(starting_equity=1000.0)
    assert drawdown_peak(window, []) == 1000.0
    rows = [
        _row(1, "engaged", 5, source="drawdown"),
        _row(2, "released", 9, peak_equity=650.0),
        _row(3, "engaged", 20),
        _row(4, "released", 30, peak_equity=640.0),
    ]
    assert drawdown_peak(window, rows) == 640.0
    assert drawdown_peak(window, rows[:2]) == 650.0


def test_the_trigger_is_armed_until_it_fires_and_again_after_a_release() -> None:
    fired = [_row(1, "engaged", 5, source="drawdown")]
    assert drawdown_armed(_WINDOW, [])
    assert not drawdown_armed(_WINDOW, fired)
    assert drawdown_armed(_WINDOW, [*fired, _row(2, "released", 9, peak_equity=650.0)])
    # An owner engagement does not disarm it.
    assert drawdown_armed(_WINDOW, [_row(1, "engaged", 5)])
    # Another window's drawdown row is not this one's.
    assert drawdown_armed(_WINDOW, [_row(1, "engaged", 5, source="drawdown", window_id=9)])


def test_after_a_release_it_refires_only_on_a_new_crossing() -> None:
    window = _window(starting_equity=1000.0)
    rows = [_row(1, "engaged", 5, source="drawdown"), _row(2, "released", 9, peak_equity=650.0)]
    peak, armed = drawdown_peak(window, rows), drawdown_armed(_WINDOW, rows)
    assert not drawdown_check(650.0, peak, 0.30, armed=armed)
    assert not drawdown_check(600.0, peak, 0.30, armed=armed)
    assert drawdown_check(450.0, peak, 0.30, armed=armed)


# The writers, on a real store file.


class _Clock:
    def __init__(self, start: int = 100) -> None:
        self.now = _at(start)

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


@pytest.fixture
def store(settings: Settings) -> Settings:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        append(conn, _window())
    return settings


def _rows(settings: Settings) -> list[KillSwitchRow]:
    with open_read_only(settings) as conn:
        return kill_switch_events_for(conn, _WINDOW)


def test_engage_appends_one_engaged_row(store: Settings) -> None:
    clock = _Clock()
    event_id = engage(
        store,
        clock,
        window_id=_WINDOW,
        source="fault",
        fault_type="ReconciliationError",
        reason="position mismatch",
        run_id=4,
    )
    assert event_id == 1
    (row,) = _rows(store)
    assert (row.state, row.source, row.fault_type, row.reason, row.run_id) == (
        "engaged",
        "fault",
        "ReconciliationError",
        "position mismatch",
        4,
    )
    assert row.at == row.known_at == row.ingested_at == clock.now


def test_engage_refuses_an_unknown_source_or_a_fault_without_its_type(store: Settings) -> None:
    with pytest.raises(ValueError, match="source"):
        engage(store, _Clock(), window_id=_WINDOW, source="page", reason="x")
    with pytest.raises(ValueError, match="fault_type"):
        engage(store, _Clock(), window_id=_WINDOW, source="fault", reason="x")
    assert _rows(store) == []


@pytest.fixture
def locked_store(store: Settings) -> Iterator[Settings]:
    """The store held open for writing by another process."""
    script = textwrap.dedent(
        f"""
        import sys
        import duckdb

        conn = duckdb.connect({store.store.path!r})
        print("held", flush=True)
        sys.stdin.read()
        conn.close()
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=_ROOT,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    try:
        yield store.model_copy(
            update={"store": store.store.model_copy(update={"lock_retry_seconds": 0})}
        )
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=30)


def test_engage_returns_a_write_failure_when_the_store_stays_locked(
    locked_store: Settings,
) -> None:
    outcome = engage(
        locked_store,
        _Clock(),
        window_id=_WINDOW,
        source="fault",
        fault_type="ClockError",
        reason="clock stale",
        run_id=2,
    )
    assert isinstance(outcome, WriteFailed)
    assert "lock" in outcome.error


def test_after_a_failed_write_the_next_run_derives_engaged(locked_store: Settings) -> None:
    # The halted run's result row is lost with the switch row: the run is unfinished.
    runs = [_run(1, 0), _run(2, 60)]
    assert isinstance(
        engage(locked_store, _Clock(), window_id=_WINDOW, source="fault", fault_type="X", run_id=1),
        WriteFailed,
    )
    assert derive(_window(), [], runs, [], reading_run=2, lock_free=True).engaged


def _override(settings: Settings, kind: str, reason: str = "manual stop") -> int:
    with open_for_write(settings) as conn:
        override_id = append(
            conn,
            OverrideRow(
                window_id=_WINDOW,
                made_at=_at(50),
                kind=kind,
                reason=reason,
                security_id="S1" if kind != "engage_kill_switch" else None,
                rebalance_session=_SESSION if kind != "engage_kill_switch" else None,
                **_stamp(50),
            ),
        )
    assert override_id is not None
    return override_id


def test_an_override_engages_exactly_once(store: Settings) -> None:
    first = _override(store, "engage_kill_switch", "earnings week")
    _override(store, "exclude_name")
    second = _override(store, "engage_kill_switch", "travel")
    clock = _Clock()
    assert engage_from_overrides(store, clock, window_id=_WINDOW, run_id=3) == [1, 2]
    rows = _rows(store)
    assert [(r.override_id, r.source, r.reason, r.run_id) for r in rows] == [
        (first, "owner", "earnings week", 3),
        (second, "owner", "travel", 3),
    ]
    assert engage_from_overrides(store, clock, window_id=_WINDOW, run_id=4) == []
    assert len(_rows(store)) == 2


def test_the_derivation_ignores_a_settle_order_row(store: Settings) -> None:
    """Spec req 17 (#571): a `settle_order` override is no engagement and no release:
    nothing consumes it into a `kill_switch` row, and the derived state is what it
    was before it, engaged or not."""
    with open_for_write(store) as conn:
        append(
            conn,
            OverrideRow(
                window_id=_WINDOW,
                made_at=_at(50),
                security_id="S1",
                client_order_id="tp-1",
                kind="settle_order",
                reason="the broker no longer knows the order",
                **_stamp(50),
            ),
        )
        assert unconsumed_kill_switch_overrides(conn, _WINDOW) == []
    assert engage_from_overrides(store, _Clock(), window_id=_WINDOW, run_id=3) == []
    assert _rows(store) == []
    assert not derive(_window(), _rows(store), [], [], reading_run=None, lock_free=True).engaged

    engage(store, _Clock(), window_id=_WINDOW, source="owner", reason="kill first")
    assert engage_from_overrides(store, _Clock(), window_id=_WINDOW, run_id=4) == []
    assert derive(_window(), _rows(store), [], [], reading_run=None, lock_free=True).engaged


def test_an_override_released_stays_consumed(store: Settings) -> None:
    _override(store, "engage_kill_switch")
    engage_from_overrides(store, _Clock(), window_id=_WINDOW, run_id=3)
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    release(
        store,
        _Clock(),
        window_id=_WINDOW,
        resume_id=resume_id,
        reconciliation_id=reconciliation_id,
        peak_equity=900.0,
        seen_event_id=_seen(store),
    )
    assert engage_from_overrides(store, _Clock(), window_id=_WINDOW, run_id=5) == []
    assert not derive(_window(), _rows(store), [], [], reading_run=None, lock_free=True).engaged


def test_engage_from_overrides_returns_a_write_failure_when_locked(locked_store: Settings) -> None:
    outcome = engage_from_overrides(locked_store, _Clock(), window_id=_WINDOW, run_id=3)
    assert isinstance(outcome, WriteFailed)


# Resumes default to minute 200, after every `_Clock` engagement (minute 100
# on), and `_release` stamps from minute 300: a coherent timeline.
def _resume_and_reconciliation(
    settings: Settings,
    status: str,
    window_id: int = _WINDOW,
    *,
    resume_at: int = 200,
    reconciled_at: int = 201,
) -> tuple[int, int]:
    return _resume(settings, resume_at), _reconciliation(settings, status, window_id, reconciled_at)


def _resume(settings: Settings, minutes: int = 200) -> int:
    with open_for_write(settings) as conn:
        resume_id = append(
            conn,
            ResumeInvocationRow(
                at=_at(minutes),
                reason="checked",
                accept_broker_fills=False,
                accept_rejections=False,
                **_stamp(minutes),
            ),
        )
    assert resume_id is not None
    return resume_id


def _reconciliation(
    settings: Settings, status: str = "ok", window_id: int = _WINDOW, minutes: int = 201
) -> int:
    with open_for_write(settings) as conn:
        reconciliation_id = append(
            conn,
            ReconciliationRow(
                window_id=window_id, at=_at(minutes), status=status, **_stamp(minutes)
            ),
        )
    assert reconciliation_id is not None
    return reconciliation_id


def test_release_appends_the_released_row_with_the_peak(store: Settings) -> None:
    engage(store, _Clock(), window_id=_WINDOW, source="owner", reason="paper kill")
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    event_id = release(
        store,
        _Clock(),
        window_id=_WINDOW,
        resume_id=resume_id,
        reconciliation_id=reconciliation_id,
        peak_equity=875.5,
        seen_event_id=_seen(store),
    )
    rows = _rows(store)
    assert rows[-1].event_id == event_id
    assert (rows[-1].state, rows[-1].source, rows[-1].resume_id) == ("released", "owner", resume_id)
    assert (rows[-1].reconciliation_id, rows[-1].peak_equity) == (reconciliation_id, 875.5)
    assert not derive(_window(), rows, [], [], reading_run=None, lock_free=True).engaged
    assert drawdown_peak(_window(), rows) == 875.5


@pytest.mark.parametrize("status", ["mismatch", "pending_unresolved", "fills_lagging"])
def test_release_refuses_a_reconciliation_that_is_not_ok(store: Settings, status: str) -> None:
    resume_id, reconciliation_id = _resume_and_reconciliation(store, status)
    with pytest.raises(ReleaseRefused, match="not ok"):
        release(
            store,
            _Clock(),
            window_id=_WINDOW,
            resume_id=resume_id,
            reconciliation_id=reconciliation_id,
            peak_equity=900.0,
            seen_event_id=_seen(store),
        )
    assert _rows(store) == []


def test_release_refuses_another_windows_reconciliation_or_an_unknown_resume(
    store: Settings,
) -> None:
    resume_id, other_windows = _resume_and_reconciliation(store, "ok", window_id=9)
    with pytest.raises(ReleaseRefused, match="window"):
        release(
            store,
            _Clock(),
            window_id=_WINDOW,
            resume_id=resume_id,
            reconciliation_id=other_windows,
            peak_equity=900.0,
            seen_event_id=_seen(store),
        )
    _, reconciliation_id = _resume_and_reconciliation(store, "ok")
    with pytest.raises(ReleaseRefused, match="no resume_invocations row"):
        release(
            store,
            _Clock(),
            window_id=_WINDOW,
            resume_id=999,
            reconciliation_id=reconciliation_id,
            peak_equity=900.0,
            seen_event_id=_seen(store),
        )
    assert _rows(store) == []


def test_the_module_never_updates_or_deletes() -> None:
    text = inspect.getsource(switch).upper()
    assert "UPDATE " not in text
    assert "DELETE " not in text


def _seen(settings: Settings, window_id: int = _WINDOW) -> int:
    """The window's highest `kill_switch` `event_id` so far: what a resume
    journaled now would pass as `seen_event_id`."""
    with open_read_only(settings) as conn:
        return max((e.event_id or 0 for e in kill_switch_events_for(conn, window_id)), default=0)


def _release(
    settings: Settings,
    resume_id: int,
    reconciliation_id: int,
    peak: float = 900.0,
    seen: int | None = None,
) -> int:
    return release(
        settings,
        _Clock(300),
        window_id=_WINDOW,
        resume_id=resume_id,
        reconciliation_id=reconciliation_id,
        peak_equity=peak,
        seen_event_id=_seen(settings) if seen is None else seen,
    )


def _engaged(settings: Settings) -> None:
    assert isinstance(
        engage(settings, _Clock(), window_id=_WINDOW, source="owner", reason="paper kill"), int
    )


@pytest.mark.parametrize("peak", [float("nan"), float("inf"), 0.0, -100.0])
def test_release_refuses_an_unusable_peak(store: Settings, peak: float) -> None:
    _engaged(store)
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    with pytest.raises(ReleaseRefused, match="peak_equity"):
        _release(store, resume_id, reconciliation_id, peak)
    assert [r.state for r in _rows(store)] == ["engaged"]


def test_release_refuses_an_older_ok_reconciliation(store: Settings) -> None:
    _engaged(store)
    resume_id, old_ok = _resume_and_reconciliation(store, "ok")
    _reconciliation(store, "mismatch", minutes=62)
    with pytest.raises(ReleaseRefused, match="latest"):
        _release(store, resume_id, old_ok)
    assert [r.state for r in _rows(store)] == ["engaged"]


def test_release_refuses_an_older_resume(store: Settings) -> None:
    _engaged(store)
    old_resume = _resume(store, 60)
    _resume(store, 61)
    reconciliation_id = _reconciliation(store, minutes=62)
    with pytest.raises(ReleaseRefused, match="latest resume"):
        _release(store, old_resume, reconciliation_id)


def test_release_refuses_a_resume_that_already_released(store: Settings) -> None:
    _engaged(store)
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    _release(store, resume_id, reconciliation_id)
    _engaged(store)
    again = _reconciliation(store, minutes=210)
    with pytest.raises(ReleaseRefused, match="already released"):
        _release(store, resume_id, again)


def test_release_refuses_a_reconciliation_older_than_the_resume(store: Settings) -> None:
    _engaged(store)
    resume_id, reconciliation_id = _resume_and_reconciliation(
        store, "ok", resume_at=60, reconciled_at=59
    )
    with pytest.raises(ReleaseRefused, match="older than resume"):
        _release(store, resume_id, reconciliation_id)


def test_release_refuses_when_nothing_is_engaged(store: Settings) -> None:
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    with pytest.raises(ReleaseRefused, match="not engaged"):
        _release(store, resume_id, reconciliation_id)
    assert _rows(store) == []


def test_release_refuses_while_a_run_is_unfinished(store: Settings) -> None:
    with open_for_write(store) as conn:
        append(conn, _run(5, 10))
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    with pytest.raises(ReleaseRefused, match=r"run\(s\) 5 unfinished"):
        _release(store, resume_id, reconciliation_id)
    assert _rows(store) == []


def test_release_clears_a_crash_the_resume_closed(store: Settings) -> None:
    with open_for_write(store) as conn:
        append(conn, _run(5, 10))
        append(conn, _result(5, "crashed", 59))
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    _release(store, resume_id, reconciliation_id)
    with open_read_only(store) as conn:
        runs = runs_for(conn, _WINDOW)
    state = derive(
        _window(),
        _rows(store),
        [r.run for r in runs],
        [r.result for r in runs if r.result is not None],
        reading_run=None,
        lock_free=True,
    )
    assert not state.engaged


def test_release_refuses_a_window_that_is_not_open(store: Settings) -> None:
    with open_for_write(store) as conn:
        append(
            conn,
            PaperWindowStopRow(window_id=_WINDOW, at=_at(80), state="closed", **_stamp(80)),
        )
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    with pytest.raises(ReleaseRefused, match="not the open window"):
        _release(store, resume_id, reconciliation_id)


@pytest.mark.parametrize("release_at", [200, 199])
def test_release_refuses_a_row_that_would_not_clear_the_switch(
    store: Settings, release_at: int
) -> None:
    """A release stamped at (or, under skew, before) a halted run's
    `finished_at` would not clear it: nothing is written, so the drawdown peak
    is not reset while the switch stays engaged (#413)."""
    with open_for_write(store) as conn:
        append(conn, _run(5, 10))
        append(conn, _result(5, "halted", 200))
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    with pytest.raises(ReleaseRefused, match="would not clear"):
        release(
            store,
            lambda: _at(release_at),
            window_id=_WINDOW,
            resume_id=resume_id,
            reconciliation_id=reconciliation_id,
            peak_equity=900.0,
            seen_event_id=_seen(store),
        )
    assert _rows(store) == []


def test_release_refuses_a_resume_that_released_another_window(store: Settings) -> None:
    """`resume_invocations` carry no window, so a resume that released window
    1 cannot release window 2 (#414)."""
    _engaged(store)
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    _release(store, resume_id, reconciliation_id)
    with open_for_write(store) as conn:
        append(
            conn,
            PaperWindowStopRow(window_id=_WINDOW, at=_at(80), state="closed", **_stamp(80)),
        )
        append(conn, _window(2))
        append(
            conn,
            KillSwitchRow(window_id=2, at=_at(85), state="engaged", source="owner", **_stamp(85)),
        )
    second = _reconciliation(store, window_id=2, minutes=220)
    with pytest.raises(ReleaseRefused, match="already released"):
        release(
            store,
            _Clock(),
            window_id=2,
            resume_id=resume_id,
            reconciliation_id=second,
            peak_equity=900.0,
            seen_event_id=_seen(store, 2),
        )
    with open_read_only(store) as conn:
        assert [r.state for r in kill_switch_events_for(conn, 2)] == ["engaged"]


def _broken_clock() -> datetime:
    raise OSError("clock")


def test_a_clock_error_in_engage_from_overrides_propagates(store: Settings) -> None:
    """The clock is read outside the write: its error is not a write failure
    (#415)."""
    _override(store, "engage_kill_switch")
    with pytest.raises(OSError, match="clock"):
        engage_from_overrides(store, _broken_clock, window_id=_WINDOW, run_id=3)
    assert _rows(store) == []


def test_the_writers_read_the_clock_before_taking_the_store(store: Settings) -> None:
    """Each writer's clock reading happens with no write connection open:
    the probe opens a read-only one, which fails while a write one is open
    (#415)."""
    _override(store, "engage_kill_switch")
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    clock = _Clock()

    def probing_clock() -> datetime:
        with open_read_only(store):
            pass
        return clock()

    assert engage_from_overrides(store, probing_clock, window_id=_WINDOW, run_id=3) == [1]
    assert isinstance(
        release(
            store,
            probing_clock,
            window_id=_WINDOW,
            resume_id=resume_id,
            reconciliation_id=reconciliation_id,
            peak_equity=900.0,
            seen_event_id=_seen(store),
        ),
        int,
    )


def _engagement(minutes: int, source: str = "drawdown") -> KillSwitchRow:
    return KillSwitchRow(
        window_id=_WINDOW,
        at=_at(minutes),
        state="engaged",
        source=source,
        fault_type="ReconciliationError" if source == "fault" else None,
        **_stamp(minutes),
    )


@pytest.mark.parametrize(
    ("source", "stamped_at"),
    [("drawdown", 65), ("fault", 65), ("drawdown", 55)],  # 55: a clock behind the resume's
)
def test_release_refuses_an_engagement_written_after_the_resume(
    store: Settings, source: str, stamped_at: int
) -> None:
    """A drawdown or fault engaged after the owner's resume is one the owner
    never saw: the release is refused by write order, whatever its stamp, so
    nothing is written and the switch stays engaged until a new resume (#447)."""
    with open_for_write(store) as conn:
        append(conn, _row(1, "engaged", 30, source="owner"))
    resume_id = _resume(store, 60)
    seen = _seen(store)
    with open_for_write(store) as conn:
        append(conn, _engagement(stamped_at, source))
    reconciliation_id = _reconciliation(store, minutes=70)
    with pytest.raises(ReleaseRefused, match="after resume"):
        _release(store, resume_id, reconciliation_id, seen=seen)
    rows = _rows(store)
    assert [r.state for r in rows] == ["engaged", "engaged"]
    assert derive(_window(), rows, [], [], reading_run=None, lock_free=True).engaged


def test_release_clears_engagements_the_resume_saw(store: Settings) -> None:
    with open_for_write(store) as conn:
        append(conn, _row(1, "engaged", 30, source="owner"))
        append(conn, _engagement(40, "fault"))
    resume_id = _resume(store, 60)
    seen = _seen(store)
    reconciliation_id = _reconciliation(store, minutes=70)
    _release(store, resume_id, reconciliation_id, seen=seen)
    assert not derive(_window(), _rows(store), [], [], reading_run=None, lock_free=True).engaged


def test_release_refuses_a_negative_seen_event_id(store: Settings) -> None:
    _engaged(store)
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok")
    with pytest.raises(ReleaseRefused, match="seen_event_id"):
        _release(store, resume_id, reconciliation_id, seen=-1)


# --- books (ADR 0017 B.2 and B.5, plan T155) -------------------------------------------

_B_WINDOW = 2


def _b_window() -> PaperWindowRow:
    return replace(_window(_B_WINDOW), book_id="b", account_id="PB1")


def test_a_release_on_b_releases_b_alone_beside_main_s_open_window(store: Settings) -> None:
    """`b`'s open window is never a second open window for `main`'s release, nor
    the reverse: each release reads its own book's open window (B.5)."""
    with open_for_write(store) as conn:
        append(conn, _b_window())
    engage(store, _Clock(), window_id=_WINDOW, source="owner", reason="paper kill")
    engage(store, _Clock(), window_id=_B_WINDOW, source="owner", reason="paper kill --book b")
    resume_id, reconciliation_id = _resume_and_reconciliation(store, "ok", _B_WINDOW)

    event_id = release(
        store,
        _Clock(300),
        window_id=_B_WINDOW,
        resume_id=resume_id,
        reconciliation_id=reconciliation_id,
        peak_equity=900.0,
        seen_event_id=_seen(store, _B_WINDOW),
    )

    with open_read_only(store) as conn:
        b_rows = kill_switch_events_for(conn, _B_WINDOW)
    assert b_rows[-1].event_id == event_id
    assert not derive(_b_window(), b_rows, [], [], reading_run=None, lock_free=True).engaged
    assert derive(_window(), _rows(store), [], [], reading_run=None, lock_free=True).engaged

    resume_id, reconciliation_id = _resume_and_reconciliation(
        store, "ok", resume_at=250, reconciled_at=251
    )
    _release(store, resume_id, reconciliation_id)
    assert not derive(_window(), _rows(store), [], [], reading_run=None, lock_free=True).engaged


def test_open_window_of_reads_only_the_window_s_own_book(store: Settings) -> None:
    with open_for_write(store) as conn:
        append(conn, _b_window())
    with open_read_only(store) as conn:
        main, b = open_window_of(conn, _WINDOW), open_window_of(conn, _B_WINDOW)
        assert main is not None and (main.window_id, main.book_id) == (_WINDOW, "main")
        assert b is not None and (b.window_id, b.book_id) == (_B_WINDOW, "b")
        assert open_window_of(conn, 99) is None
    with open_for_write(store) as conn:
        append(
            conn,
            PaperWindowStopRow(window_id=_B_WINDOW, at=_at(80), state="closed", **_stamp(80)),
        )
        append(conn, replace(_window(3), window_id=None))
    with open_read_only(store) as conn:
        assert open_window_of(conn, _B_WINDOW) is None
        with pytest.raises(JournalIntegrityError, match="book 'main'"):
            open_window_of(conn, _WINDOW)
