"""Tests for `tradepartner.backtest.signals` (backtest spec req 3, plan T34)."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from tradepartner.backtest.signals import MomentumSignal, momentum_12_1
from tradepartner.calendar import last_session_of_month

# Rebalance at the last session of June 2024: T = 2024-06.
T_SESSION = last_session_of_month(2024, 6)  # 2024-06-28
END_BAR = last_session_of_month(2024, 5)  # T - skip(1): 2024-05-31
START_BAR = last_session_of_month(2023, 6)  # T - formation(12): 2023-06-30


def _frame(rows: list[tuple[str, date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "security_id": [r[0] for r in rows],
            "session": [r[1] for r in rows],
            "close": [r[2] for r in rows],
        },
        schema={"security_id": pl.Utf8, "session": pl.Date, "close": pl.Float64},
    )


BASE_ROWS = [
    ("A", START_BAR, 100.0),
    ("A", END_BAR, 150.0),
    ("A", T_SESSION, 10.0),  # skip month: a crash in June must not count
    ("B", START_BAR, 50.0),
    ("B", END_BAR, 40.0),
    ("B", T_SESSION, 400.0),
]


def test_score_is_end_bar_over_start_bar_minus_one() -> None:
    sig = momentum_12_1(_frame(BASE_ROWS), T_SESSION, 12, 1)
    assert sig.scores == pytest.approx({"A": 0.5, "B": -0.2}, rel=1e-15)
    assert sig.excluded == ()


def test_skip_month_is_excluded() -> None:
    """The June 2024 bars (month T) change wildly and must not affect the score."""
    moved = [(s, d, c * 7 if d == T_SESSION else c) for s, d, c in BASE_ROWS]
    assert momentum_12_1(_frame(moved), T_SESSION, 12, 1) == momentum_12_1(
        _frame(BASE_ROWS), T_SESSION, 12, 1
    )


def test_skip_zero_uses_month_t_itself() -> None:
    sig = momentum_12_1(_frame(BASE_ROWS), T_SESSION, 12, 0)
    assert sig.scores == pytest.approx({"A": 10.0 / 100.0 - 1, "B": 400.0 / 50.0 - 1})


def test_missing_formation_start_bar_excluded_and_counted() -> None:
    rows = [r for r in BASE_ROWS if not (r[0] == "B" and r[1] == START_BAR)]
    sig = momentum_12_1(_frame(rows), T_SESSION, 12, 1)
    assert set(sig.scores) == {"A"}
    assert sig.excluded == ("B",)
    assert sig.n_excluded == 1


def test_missing_end_bar_excluded_and_counted() -> None:
    rows = [r for r in BASE_ROWS if not (r[0] == "A" and r[1] == END_BAR)]
    sig = momentum_12_1(_frame(rows), T_SESSION, 12, 1)
    assert set(sig.scores) == {"B"}
    assert sig.excluded == ("A",)


def test_a_bar_near_but_not_at_the_month_end_does_not_count() -> None:
    """The spec names the last session of the month; an earlier bar is not a substitute."""
    rows = [
        ("A", date(2023, 6, 29), 100.0),  # the day before the formation-start bar
        ("A", END_BAR, 150.0),
    ]
    sig = momentum_12_1(_frame(rows), T_SESSION, 12, 1)
    assert sig.scores == {}
    assert sig.excluded == ("A",)


def test_requested_names_without_any_bar_are_excluded() -> None:
    sig = momentum_12_1(_frame(BASE_ROWS), T_SESSION, 12, 1, security_ids=["A", "Z", "B"])
    assert set(sig.scores) == {"A", "B"}
    assert sig.excluded == ("Z",)


def test_names_outside_security_ids_are_not_scored() -> None:
    sig = momentum_12_1(_frame(BASE_ROWS), T_SESSION, 12, 1, security_ids=["B"])
    assert set(sig.scores) == {"B"}


def test_a_session_after_t_session_is_never_read() -> None:
    """Rows after T would change nothing if ignored; poison them and compare."""
    later = last_session_of_month(2024, 7)
    poisoned = [
        *BASE_ROWS,
        ("A", later, -1.0),
        ("C", START_BAR, 1.0),  # C's end bar exists only after T
        ("C", later, 99.0),
    ]
    clean = momentum_12_1(_frame(BASE_ROWS), T_SESSION, 12, 1)
    got = momentum_12_1(_frame(poisoned), T_SESSION, 12, 1)
    assert got.scores == clean.scores
    assert got.excluded == ("C",)


def test_t_session_must_be_a_month_end_session() -> None:
    with pytest.raises(ValueError, match="last session"):
        momentum_12_1(_frame(BASE_ROWS), date(2024, 6, 27), 12, 1)


@pytest.mark.parametrize(("formation", "skip"), [(1, 1), (12, 12), (0, 0), (12, -1)])
def test_formation_must_exceed_skip(formation: int, skip: int) -> None:
    with pytest.raises(ValueError):
        momentum_12_1(_frame(BASE_ROWS), T_SESSION, formation, skip)


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf")])
def test_non_positive_or_non_finite_close_refused(bad: float) -> None:
    rows = [("A", START_BAR, bad), ("A", END_BAR, 150.0)]
    with pytest.raises(ValueError, match="positive and finite"):
        momentum_12_1(_frame(rows), T_SESSION, 12, 1)


def test_duplicate_bar_refused() -> None:
    rows = [*BASE_ROWS, ("A", END_BAR, 151.0)]
    with pytest.raises(ValueError, match="duplicate"):
        momentum_12_1(_frame(rows), T_SESSION, 12, 1)


def test_result_is_ordered_by_security_id() -> None:
    rows = [(s, d, c) for s in ("C", "A", "B") for d, c in ((START_BAR, 1.0), (END_BAR, 2.0))]
    sig = momentum_12_1(_frame(rows), T_SESSION, 12, 1)
    assert list(sig.scores) == ["A", "B", "C"]
    assert isinstance(sig, MomentumSignal)
