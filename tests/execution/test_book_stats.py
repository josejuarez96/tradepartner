"""Hand-value tests for the book's performance summary (plan T270, #2170)."""

from __future__ import annotations

from datetime import date

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
