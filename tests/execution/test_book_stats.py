"""Hand-value tests for the book's performance summary (plan T270, #2170)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from tradepartner.calendar import is_session
from tradepartner.execution import book_stats as bs
from tradepartner.execution import performance

S = [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)]
AS_OF = S[-1]


def test_max_drawdown_hand_case() -> None:
    eq = dict(zip(S, [100.0, 110.0, 99.0, 105.0], strict=True))
    dd = bs.max_drawdown(eq, AS_OF)
    assert dd is not None
    assert dd.pct == pytest.approx(-10.0)
    assert dd.dollars == pytest.approx(-11.0)
    assert (dd.peak_session, dd.trough_session) == (S[1], S[2])


def test_max_drawdown_monotone_and_short() -> None:
    dd = bs.max_drawdown(dict(zip(S, [1.0, 2.0, 3.0, 4.0], strict=True)), AS_OF)
    assert dd is not None and dd.pct == 0.0 and dd.dollars == 0.0
    assert bs.max_drawdown({}, AS_OF) is None
    assert bs.max_drawdown({S[0]: 5.0}, AS_OF) is None


def test_best_worst_day() -> None:
    eq = dict(zip(S, [100.0, 110.0, 99.0, 105.0], strict=True))
    res = bs.best_worst_day(eq, AS_OF)
    assert res is not None
    best, worst = res
    assert (best.session, best.dollars) == (S[1], 10.0)
    assert best.pct == pytest.approx(10.0)
    assert (worst.session, worst.dollars) == (S[2], -11.0)
    assert worst.pct == pytest.approx(-10.0)
    assert bs.best_worst_day({S[0]: 1.0}, AS_OF) is None
    assert bs.best_worst_day({}, AS_OF) is None


def test_no_lookahead_point_after_as_of_ignored() -> None:
    eq = dict(zip(S[:3], [100.0, 110.0, 99.0], strict=True))
    eq[date(2026, 10, 26)] = 1.0
    as_of = S[2]
    dd = bs.max_drawdown(eq, as_of)
    assert dd is not None and dd.trough_session == S[2]
    res = bs.best_worst_day(eq, as_of)
    assert res is not None and res[1].session == S[2]
    sl = bs.range_slice(eq, eq, "since_start", as_of)
    assert sl.book[-1][0] == S[2]


def test_since_start_covers_identical_sessions_and_drops_missing() -> None:
    book = dict(zip(S, [100.0, 110.0, 99.0, 105.0], strict=True))
    spy = {S[0]: 500.0, S[1]: 505.0, S[3]: 515.0}  # SPY lacks S[2]
    sl = bs.range_slice(book, spy, "since_start", AS_OF)
    assert [s for s, _ in sl.book] == [s for s, _ in sl.spy] == [S[0], S[1], S[3]]
    assert sl.book[0][1] == sl.spy[0][1] == 100.0
    assert sl.book_return_pct == pytest.approx(
        performance.cumulative_return_pct({S[0]: 100.0, S[1]: 110.0, S[3]: 105.0})
    )
    assert sl.spy_return_pct == pytest.approx(3.0)
    assert sl.note is None


def test_range_young_book_notes_and_starts_at_first_session() -> None:
    book = dict(zip(S, [100.0, 110.0, 99.0, 105.0], strict=True))
    sl = bs.range_slice(book, book, "1M", AS_OF)
    assert sl.note == "range longer than the book's history"
    assert sl.start_session == S[0]
    assert sl.book[0][1] == 100.0


def test_range_1m_start_is_last_session_on_or_before_calendar_date() -> None:
    # as_of 2026-11-16 -> 2026-10-16 (Fri, a session): the slice starts there.
    sessions = [date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 19), date(2026, 11, 16)]
    book = dict(zip(sessions, [10.0, 20.0, 30.0, 40.0], strict=True))
    sl = bs.range_slice(book, None, "1M", date(2026, 11, 16))
    assert sl.start_session == date(2026, 10, 16)
    assert sl.note is None
    assert sl.book[0] == (date(2026, 10, 16), 100.0)
    assert sl.spy == [] and sl.spy_return_pct is None
    # 3M back from 2026-11-16 is 2026-08-16 (Sunday): start is Fri 2026-08-14.
    assert not is_session(date(2026, 8, 16))
    early = {date(2026, 8, 14): 1.0, date(2026, 8, 13): 5.0, date(2026, 11, 16): 2.0}
    sl3 = bs.range_slice(early, None, "3M", date(2026, 11, 16))
    assert sl3.start_session == date(2026, 8, 14)
    assert sl3.note is None


def test_range_empty_series() -> None:
    sl = bs.range_slice({}, {}, "3M", AS_OF)
    assert sl.book == [] and sl.book_return_pct is None and sl.start_session is None
    one = bs.range_slice({S[0]: 5.0}, {S[0]: 9.0}, "since_start", AS_OF)
    assert one.book_return_pct == 0.0


def test_unknown_range_raises() -> None:
    with pytest.raises(ValueError, match="unknown range"):
        bs.range_slice({}, None, "1Y", AS_OF)  # type: ignore[arg-type]


def test_tracking_progress() -> None:
    assert bs.tracking_progress(2, 4).text == "2 of 4 rebalances"
    assert not bs.tracking_progress(3, 4).enough
    done = bs.tracking_progress(4, 4)
    assert done.enough and "enough to judge" in done.text
    assert "enough to judge" not in bs.tracking_progress(None, 4).text
    assert not bs.tracking_progress(None, 4).enough
    with pytest.raises(ValueError):
        bs.tracking_progress(1, 0)


# --- the forward return exam's inputs (plan T381a) -----------------------------------

#: The first rebalance session of a fixture exam window and the month-ends after it.
EXAM_T0 = date(2026, 10, 30)
EXAM_ENDS = [date(2026, 10, 30), date(2026, 11, 30), date(2026, 12, 31), date(2027, 1, 29)]


def _exam_inputs(
    ends: list[date],
) -> tuple[dict[date, float], dict[date, float], dict[date, float]]:
    """Equity, benchmark and accruals on every session from the base to `ends[-1]`:
    equity grows 2% a month-end step, the benchmark 1%, each session accrues 0.0001."""
    sessions = [ends[0], *bs.sessions_between(ends[0], ends[-1])]
    equity: dict[date, float] = {}
    bench: dict[date, float] = {}
    level_e, level_b = 100.0, 50.0
    for s in sessions:
        if s in ends[1:]:
            level_e *= 1.02
            level_b *= 1.01
        equity[s] = level_e
        bench[s] = level_b
    accruals = {s: 0.0001 for s in sessions[1:]}
    return equity, bench, accruals


def test_exam_series_hand_values() -> None:
    equity, bench, accruals = _exam_inputs(EXAM_ENDS)
    series = bs.exam_series(equity, bench, accruals, EXAM_T0, EXAM_ENDS[-1])
    assert series.month_ends == tuple(EXAM_ENDS)
    assert series.months == 3
    assert series.net_excess == pytest.approx([0.01, 0.01, 0.01])
    november = len(bs.sessions_between(EXAM_ENDS[0], EXAM_ENDS[1]))
    assert series.bench_over_cash[0] == pytest.approx(0.01 - (1.0001**november - 1.0))


def test_exam_series_starts_at_the_first_month_end_on_or_after_t0() -> None:
    """A mid-month T_0: the stub to the first month-end is not a month."""
    equity, bench, accruals = _exam_inputs(EXAM_ENDS)
    series = bs.exam_series(equity, bench, accruals, date(2026, 10, 15), EXAM_ENDS[-1])
    assert series.month_ends[0] == EXAM_ENDS[0]
    assert series.months == 3
    assert bs.exam_series({}, bench, accruals, EXAM_T0, EXAM_ENDS[-1]).months == 0


def test_exam_series_has_no_look_ahead() -> None:
    """The series up to month k is unchanged when later sessions are added or moved."""
    equity, bench, accruals = _exam_inputs(EXAM_ENDS)
    short = bs.exam_series(equity, bench, accruals, EXAM_T0, EXAM_ENDS[2])
    longer = bs.exam_series(equity, bench, accruals, EXAM_T0, EXAM_ENDS[-1])
    assert longer.net_excess[: short.months] == short.net_excess
    altered = {s: (v * 3.0 if s > EXAM_ENDS[2] else v) for s, v in equity.items()}
    cut = bs.exam_series(altered, bench, accruals, EXAM_T0, EXAM_ENDS[2])
    assert cut == short
    # as_of past the last mark reads only the marked month-ends
    marked_to_dec = {s: v for s, v in equity.items() if s <= EXAM_ENDS[2]}
    assert bs.exam_series(marked_to_dec, bench, accruals, EXAM_T0, EXAM_ENDS[-1]) == short


def test_exam_series_refuses_a_missing_mark_benchmark_or_accrual() -> None:
    equity, bench, accruals = _exam_inputs(EXAM_ENDS)
    gap = {s: v for s, v in equity.items() if s != EXAM_ENDS[1]}
    with pytest.raises(ValueError, match="equity mark at month-end 2026-11-30"):
        bs.exam_series(gap, bench, accruals, EXAM_T0, EXAM_ENDS[-1])
    with pytest.raises(ValueError, match="benchmark level"):
        bs.exam_series(equity, {EXAM_ENDS[0]: 1.0}, accruals, EXAM_T0, EXAM_ENDS[-1])
    with pytest.raises(ValueError, match="T-bill accrual"):
        bs.exam_series(equity, bench, {}, EXAM_T0, EXAM_ENDS[-1])


def test_window_ended_only_after_the_stop() -> None:
    stop = datetime(2027, 1, 4, 15, 0, tzinfo=UTC)
    assert not bs.window_ended(None, stop)
    assert not bs.window_ended(stop, stop - timedelta(seconds=1))
    assert bs.window_ended(stop, stop)
    assert bs.window_ended(stop, stop + timedelta(days=1))


def test_minimums_met_by_counts_fills_and_rebalances_by_each_look_s_month_end() -> None:
    """False at a look whose fills by its month-end are below the minimum, true once
    they reach it; an unreached look and a window without the frozen key are unmet."""
    ends = [date(2026, 10, 30), date(2026, 11, 30), date(2026, 12, 31)]
    rebalances = [date(2026, 10, 30), date(2026, 11, 30), date(2026, 12, 31)]
    fills = [date(2026, 11, 2)] * 3 + [date(2026, 12, 1)] * 2
    met = bs.minimums_met_by((1, 2, 3), ends, rebalances, fills, min_rebalances=2, min_exam_fills=5)
    assert met == {1: False, 2: True, 3: False}
    fewer = bs.minimums_met_by((1, 2), ends, rebalances, fills, min_rebalances=3, min_exam_fills=5)
    assert fewer == {1: False, 2: True}
    unfrozen = bs.minimums_met_by(
        (1, 2), ends, rebalances, fills, min_rebalances=1, min_exam_fills=None
    )
    assert unfrozen == {1: False, 2: False}


def test_minimums_met_by_needs_each_minimum_on_its_own() -> None:
    """Unmet rebalances with enough fills, and unmet fills with enough rebalances, are
    each unmet (T381 folds both into one flag, so each is tested here)."""
    ends = [date(2026, 10, 30), date(2026, 11, 30)]
    rebalances = [date(2026, 10, 30), date(2026, 11, 30)]
    fills = [date(2026, 11, 2)] * 10
    kwargs = {"min_rebalances": 2, "min_exam_fills": 10}
    assert bs.minimums_met_by((1,), ends, rebalances, fills, **kwargs) == {1: True}
    assert bs.minimums_met_by((1,), ends, rebalances[:1], fills, **kwargs) == {1: False}
    assert bs.minimums_met_by((1,), ends, rebalances, fills[:9], **kwargs) == {1: False}
