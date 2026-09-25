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


def _names(rows: list[tuple[str, date, float]]) -> list[str]:
    """The universe for a test: every name that appears in its rows."""
    return sorted({r[0] for r in rows})


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
    sig = momentum_12_1(_frame(BASE_ROWS), T_SESSION, 12, 1, security_ids=_names(BASE_ROWS))
    assert sig.scores == pytest.approx({"A": 0.5, "B": -0.2}, rel=1e-15)
    assert sig.excluded == ()


def test_skip_month_is_excluded() -> None:
    """The June 2024 bars (month T) change wildly and must not affect the score."""
    moved = [(s, d, c * 7 if d == T_SESSION else c) for s, d, c in BASE_ROWS]
    assert momentum_12_1(
        _frame(moved), T_SESSION, 12, 1, security_ids=_names(moved)
    ) == momentum_12_1(_frame(BASE_ROWS), T_SESSION, 12, 1, security_ids=_names(BASE_ROWS))


def test_skip_zero_uses_month_t_itself() -> None:
    sig = momentum_12_1(_frame(BASE_ROWS), T_SESSION, 12, 0, security_ids=_names(BASE_ROWS))
    assert sig.scores == pytest.approx({"A": 10.0 / 100.0 - 1, "B": 400.0 / 50.0 - 1})


def test_missing_formation_start_bar_excluded_and_counted() -> None:
    rows = [r for r in BASE_ROWS if not (r[0] == "B" and r[1] == START_BAR)]
    sig = momentum_12_1(_frame(rows), T_SESSION, 12, 1, security_ids=_names(rows))
    assert set(sig.scores) == {"A"}
    assert sig.excluded == ("B",)
    assert sig.n_excluded == 1


def test_missing_end_bar_excluded_and_counted() -> None:
    rows = [r for r in BASE_ROWS if not (r[0] == "A" and r[1] == END_BAR)]
    sig = momentum_12_1(_frame(rows), T_SESSION, 12, 1, security_ids=_names(rows))
    assert set(sig.scores) == {"B"}
    assert sig.excluded == ("A",)


def test_a_bar_near_but_not_at_the_month_end_does_not_count() -> None:
    """The spec names the last session of the month; an earlier bar is not a substitute."""
    rows = [
        ("A", date(2023, 6, 29), 100.0),  # the day before the formation-start bar
        ("A", END_BAR, 150.0),
    ]
    sig = momentum_12_1(_frame(rows), T_SESSION, 12, 1, security_ids=_names(rows))
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
    universe = ["A", "B", "C"]
    clean = momentum_12_1(_frame(BASE_ROWS), T_SESSION, 12, 1, security_ids=universe)
    got = momentum_12_1(_frame(poisoned), T_SESSION, 12, 1, security_ids=universe)
    assert got.scores == clean.scores
    assert got.excluded == ("C",)


def test_t_session_must_be_a_month_end_session() -> None:
    with pytest.raises(ValueError, match="last session"):
        momentum_12_1(_frame(BASE_ROWS), date(2024, 6, 27), 12, 1, security_ids=_names(BASE_ROWS))


@pytest.mark.parametrize(("formation", "skip"), [(1, 1), (12, 12), (0, 0), (12, -1)])
def test_formation_must_exceed_skip(formation: int, skip: int) -> None:
    with pytest.raises(ValueError):
        momentum_12_1(_frame(BASE_ROWS), T_SESSION, formation, skip, security_ids=_names(BASE_ROWS))


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf")])
def test_non_positive_or_non_finite_close_refused(bad: float) -> None:
    rows = [("A", START_BAR, bad), ("A", END_BAR, 150.0)]
    with pytest.raises(ValueError, match="positive and finite"):
        momentum_12_1(_frame(rows), T_SESSION, 12, 1, security_ids=_names(rows))


def test_duplicate_bar_refused() -> None:
    rows = [*BASE_ROWS, ("A", END_BAR, 151.0)]
    with pytest.raises(ValueError, match="duplicate"):
        momentum_12_1(_frame(rows), T_SESSION, 12, 1, security_ids=_names(rows))


def test_result_is_ordered_by_security_id() -> None:
    rows = [(s, d, c) for s in ("C", "A", "B") for d, c in ((START_BAR, 1.0), (END_BAR, 2.0))]
    sig = momentum_12_1(_frame(rows), T_SESSION, 12, 1, security_ids=_names(rows))
    assert list(sig.scores) == ["A", "B", "C"]
    assert isinstance(sig, MomentumSignal)


def test_security_ids_is_required() -> None:
    """No default: scoring every name in a frame that also holds non-universe holdings
    would let a name that left the universe be selected again (ADR 0006)."""
    with pytest.raises(TypeError):
        momentum_12_1(_frame(BASE_ROWS), T_SESSION, 12, 1)  # type: ignore[call-arg]


def test_truncation_invariance_with_poison_on_every_later_month_end() -> None:
    """The result on the full frame equals the result on the frame cut at T, with
    poisoned bars on every month-end after T (and every session of month T)."""
    later_month_ends = [last_session_of_month(2024, m) for m in range(7, 13)]
    poison = [
        (sid, d, value) for sid in ("A", "B", "D") for d in later_month_ends for value in (1e9,)
    ]
    full = _frame([*BASE_ROWS, *poison])
    cut = full.filter(pl.col("session") <= T_SESSION)
    universe = ["A", "B", "D"]
    assert momentum_12_1(full, T_SESSION, 12, 1, security_ids=universe) == momentum_12_1(
        cut, T_SESSION, 12, 1, security_ids=universe
    )
    for skip in (0, 1, 3):
        assert momentum_12_1(full, T_SESSION, 12, skip, security_ids=universe) == momentum_12_1(
            cut, T_SESSION, 12, skip, security_ids=universe
        )


def test_null_close_refused_not_treated_as_missing() -> None:
    frame = pl.DataFrame(
        {"security_id": ["A", "A"], "session": [START_BAR, END_BAR], "close": [None, 150.0]},
        schema={"security_id": pl.Utf8, "session": pl.Date, "close": pl.Float64},
    )
    with pytest.raises(ValueError, match="null"):
        momentum_12_1(frame, T_SESSION, 12, 1, security_ids=["A"])


def test_month_arithmetic_across_year_boundary_and_february() -> None:
    """Rebalance at the end of March 2024 with skip 1 reads end-February (leap year);
    formation 12 reads end-March 2023."""
    t = last_session_of_month(2024, 3)
    rows = [
        ("A", last_session_of_month(2023, 3), 100.0),
        ("A", last_session_of_month(2024, 2), 120.0),
    ]
    sig = momentum_12_1(_frame(rows), t, 12, 1, security_ids=["A"])
    assert sig.scores == pytest.approx({"A": 0.2})
    t_jan = last_session_of_month(2024, 1)
    rows_jan = [
        ("A", last_session_of_month(2023, 1), 100.0),
        ("A", last_session_of_month(2023, 12), 90.0),
    ]
    assert momentum_12_1(_frame(rows_jan), t_jan, 12, 1, security_ids=["A"]).scores == (
        pytest.approx({"A": -0.1})
    )
