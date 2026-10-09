"""Signal anchors on a synthetic frame (strategy-lab spec req 7 and req 1(d); T95)."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from tradepartner.backtest.signals import (
    anchor_sessions,
    check_anchor_feasible,
    momentum,
    momentum_12_1,
)

FIRST_SESSION = date(2016, 1, 4)  # the store's first bar session (ADR 0009)


def _frame(rows: list[tuple[str, date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "security_id": [r[0] for r in rows],
            "session": [r[1] for r in rows],
            "close": [r[2] for r in rows],
        },
        schema={"security_id": pl.Utf8, "session": pl.Date, "close": pl.Float64},
    )


# Month-end anchors for T in June 2024 with (12, 1): A_form 2023-06-30, A_skip 2024-05-31.
MONTH_ROWS = [
    ("A", date(2023, 6, 30), 100.0),
    ("A", date(2024, 5, 31), 150.0),
    ("A", date(2024, 6, 7), 1.0),  # weekly T's inside the month must not move the score
    ("A", date(2024, 6, 21), 900.0),
    ("B", date(2023, 6, 30), 50.0),
    ("B", date(2024, 5, 31), 40.0),
    ("B", date(2024, 6, 14), 7.0),
]


def test_month_end_anchor_equals_momentum_12_1_at_a_month_end() -> None:
    t = date(2024, 6, 28)
    frame = _frame(MONTH_ROWS)
    phase3 = momentum_12_1(frame, t, 12, 1, security_ids=["A", "B"])
    got = momentum(frame, t, 12, 1, "month_end", "month_end", security_ids=["A", "B"])
    assert got == phase3
    assert got.scores == pytest.approx({"A": 0.5, "B": -0.2})


def test_month_end_anchor_is_constant_across_the_weekly_ts_of_one_month() -> None:
    weekly = [date(2024, 6, 7), date(2024, 6, 14), date(2024, 6, 21), date(2024, 6, 28)]
    frame = _frame(MONTH_ROWS)
    results = {
        momentum(frame, t, 12, 1, "month_end", "week_end", security_ids=["A", "B"]).scores["A"]
        for t in weekly
    }
    assert results == {0.5}


def test_offset_anchors_from_2024_06_28() -> None:
    t = date(2024, 6, 28)
    assert anchor_sessions(t, 12, 1, "offset") == (date(2023, 6, 28), date(2024, 5, 28))
    rows = [
        ("A", date(2023, 6, 28), 100.0),
        ("A", date(2023, 6, 30), 1.0),  # the month-end bars are not read under offset
        ("A", date(2024, 5, 28), 120.0),
        ("A", date(2024, 5, 31), 1.0),
    ]
    got = momentum(_frame(rows), t, 12, 1, "offset", "daily", security_ids=["A"])
    assert got.scores == pytest.approx({"A": 0.2})


def test_offset_on_a_weekend_reads_the_friday_before() -> None:
    # 2024-06-15 is a Saturday: the Friday before, in the same month.
    assert anchor_sessions(date(2024, 7, 15), 12, 1, "offset")[1] == date(2024, 6, 14)
    # 2024-06-01 is a Saturday, the 1st: the Friday before is in the prior month.
    assert anchor_sessions(date(2024, 7, 1), 12, 1, "offset")[1] == date(2024, 5, 31)
    rows = [("A", date(2023, 6, 30), 10.0), ("A", date(2024, 5, 31), 11.0)]
    got = momentum(_frame(rows), date(2024, 7, 1), 12, 1, "offset", "daily", security_ids=["A"])
    assert got.scores == pytest.approx({"A": 0.1})


def test_offset_with_skip_zero_reads_t_itself() -> None:
    t = date(2024, 6, 12)
    assert anchor_sessions(t, 1, 0, "offset") == (date(2024, 5, 10), t)


def test_a_session_after_t_is_never_read() -> None:
    t = date(2024, 6, 12)  # a Wednesday, a daily rebalance
    clean_rows = [
        ("A", date(2023, 5, 12), 100.0),
        ("A", date(2024, 5, 10), 110.0),
        ("B", date(2023, 5, 12), 100.0),
    ]
    poisoned = [
        *clean_rows,
        ("A", date(2024, 6, 13), -1.0),
        ("B", date(2024, 6, 14), 99.0),  # B's skip bar would exist only after T
    ]
    ids = ["A", "B"]
    clean = momentum(_frame(clean_rows), t, 13, 1, "offset", "daily", security_ids=ids)
    got = momentum(_frame(poisoned), t, 13, 1, "offset", "daily", security_ids=ids)
    assert anchor_sessions(t, 13, 1, "offset") == (date(2023, 5, 12), date(2024, 5, 10))
    assert got == clean


def test_a_name_lacking_a_bar_is_excluded_and_counted() -> None:
    t = date(2024, 6, 28)
    rows = [
        ("A", date(2023, 6, 28), 100.0),
        ("A", date(2024, 5, 28), 120.0),
        ("B", date(2024, 5, 28), 120.0),  # no formation bar
    ]
    got = momentum(_frame(rows), t, 12, 1, "offset", "week_end", security_ids=["A", "B", "C"])
    assert set(got.scores) == {"A"}
    assert got.excluded == ("B", "C")
    assert got.n_excluded == 2


def test_t_must_be_a_rebalance_session_at_its_cadence() -> None:
    with pytest.raises(ValueError, match="ISO week"):
        momentum(
            _frame(MONTH_ROWS), date(2024, 6, 12), 12, 1, "offset", "week_end", security_ids=["A"]
        )


def test_an_anchor_after_t_is_refused() -> None:
    # month_end anchor, skip 0, at a week end mid-month: A_0 is 2024-06-28 > T.
    with pytest.raises(ValueError, match="after t_session"):
        momentum(
            _frame(MONTH_ROWS),
            date(2024, 6, 14),
            12,
            0,
            "month_end",
            "week_end",
            security_ids=["A"],
        )


def test_check_anchor_feasible() -> None:
    first = date(2020, 1, 31)
    refused = check_anchor_feasible(12, 0, "month_end", "week_end", first, FIRST_SESSION)
    assert refused is not None and "after T" in refused
    early = check_anchor_feasible(12, 1, "month_end", "month_end", date(2016, 6, 30), FIRST_SESSION)
    assert early is not None and "2015-06-30" in early and "2016-01-04" in early
    assert check_anchor_feasible(12, 0, "month_end", "month_end", first, FIRST_SESSION) is None
    assert check_anchor_feasible(12, 0, "offset", "week_end", first, FIRST_SESSION) is None
    assert check_anchor_feasible(12, 1, "month_end", "daily", first, FIRST_SESSION) is None
    assert check_anchor_feasible(12, 1, "offset", "daily", date(2017, 1, 4), FIRST_SESSION) is None


def test_a_good_friday_week_end_is_a_rebalance_session() -> None:
    rows = [("A", date(2023, 3, 28), 10.0), ("A", date(2024, 2, 28), 12.0)]
    got = momentum(_frame(rows), date(2024, 3, 28), 12, 1, "offset", "week_end", security_ids=["A"])
    assert got.scores == pytest.approx({"A": 0.2})
