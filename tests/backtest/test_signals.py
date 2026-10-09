"""Tests for `tradepartner.backtest.signals` (backtest spec req 3, plan T34)."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime, timedelta
from typing import Any

import polars as pl
import pytest

from tradepartner.backtest.portfolio import _selected_count
from tradepartner.backtest.provider import TurnoverInputs
from tradepartner.backtest.signals import (
    MomentumSignal,
    TurnoverScreen,
    formation_sessions,
    momentum_12_1,
    turnover_screen,
)
from tradepartner.calendar import last_session_of_month, session_close

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


# --- the turnover screen (backtest spec amendment #1358, item 2; T165c) ---------------

FORMATION = formation_sessions(T_SESSION, "month_end")
T_READ = session_close(T_SESSION)
KNOWN = session_close(date(2024, 5, 31))
#: A 2-for-1 inside the formation month: eight sessions before it, eleven from it.
SPLIT_EX = date(2024, 6, 13)


def _bars(rows: list[tuple[str, date, float, datetime]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "security_id": pl.Utf8,
            "session": pl.Date,
            "volume": pl.Float64,
            "known_at": pl.Datetime("us", "UTC"),
        },
        orient="row",
    )


def _month(sid: str, volume: float, *, split_ex: date | None = None) -> list[Any]:
    """`sid`'s formation-month bars: `volume` a session, doubled from a 2-for-1's
    ex-date on (a bar before the split is in pre-split shares)."""
    return [
        (sid, s, volume * (2 if split_ex is not None and s >= split_ex else 1), KNOWN)
        for s in FORMATION
    ]


def _inputs(
    rows: list[Any],
    shares: dict[str, tuple[date, float]],
    splits: dict[str, tuple[tuple[date, float], ...]] | None = None,
) -> TurnoverInputs:
    return TurnoverInputs(t=T_READ, bars=_bars(rows), shares=shares, splits=splits or {})


def _screen(inputs: TurnoverInputs, fraction: float, ids: list[str]) -> TurnoverScreen:
    return turnover_screen(inputs, FORMATION, T_SESSION, fraction, security_ids=ids)


def test_formation_sessions_are_the_rebalance_period_s_sessions() -> None:
    assert len(FORMATION) == 19  # June 2024, Juneteenth closed
    assert (FORMATION[0], FORMATION[-1]) == (date(2024, 6, 3), T_SESSION)
    assert date(2024, 6, 19) not in FORMATION
    # Good Friday 2024 closes the market: the week's last session is the Thursday.
    assert formation_sessions(date(2024, 3, 28), "week_end") == tuple(
        date(2024, 3, d) for d in (25, 26, 27, 28)
    )
    assert formation_sessions(T_SESSION, "daily") == (T_SESSION,)
    with pytest.raises(ValueError, match="is not the last session of its month"):
        formation_sessions(date(2024, 6, 27), "month_end")


def test_the_screen_keeps_the_top_fraction_by_turnover_ties_by_id() -> None:
    volumes = {"A": 5.0, "B": 4.0, "C": 4.0, "D": 2.0, "E": 1.0}
    ids = sorted(volumes)
    inputs = _inputs(
        [r for sid, v in volumes.items() for r in _month(sid, v)],
        {sid: (date(2024, 5, 31), 100.0) for sid in ids},
    )
    # ceil(0.5 * 5) = 3; at 0.4 the tie between B and C goes to B.
    assert _screen(inputs, 0.5, ids).kept == ("A", "B", "C")
    screen = _screen(inputs, 0.4, ids)
    assert (screen.kept, screen.excluded, screen.unusable) == (("A", "B"), ("C", "D", "E"), ())
    assert screen.turnover["A"] == pytest.approx(19 * 5.0 / 100.0)
    # The count rule reads the fraction as its decimal: 0.1 of 30 names is 3, not 4.
    many = [f"N{i:02d}" for i in range(30)]
    inputs = _inputs(
        [r for i, sid in enumerate(many) for r in _month(sid, 1.0 + i)],
        {sid: (date(2024, 5, 31), 100.0) for sid in many},
    )
    assert _screen(inputs, 0.1, many).kept == ("N27", "N28", "N29")


def test_a_name_without_a_usable_turnover_is_excluded_and_left_out_of_the_count() -> None:
    ids = ["A", "B", "C", "D", "E", "F", "G", "H"]
    shares = {sid: (date(2024, 5, 31), 100.0) for sid in ids if sid != "F"}
    shares["G"] = (date(2024, 5, 31), 0.0)
    rows = [r for i, sid in enumerate(ids) for r in _month(sid, 10.0 - i)]
    rows = [r for r in rows if (r[0], r[1]) != ("E", date(2024, 6, 12))]  # one bar missing
    late = T_READ + timedelta(microseconds=1)
    rows = [(*r[:3], late) if (r[0], r[1]) == ("H", T_SESSION) else r for r in rows]
    rows += [("A", date(2024, 5, 31), 1e9, KNOWN)]  # before the formation month: unread
    screen = _screen(_inputs(rows, shares), 0.5, ids)
    assert screen.unusable == ("E", "F", "G", "H")
    assert screen.n_excluded_no_turnover == 4
    # The denominator is the four usable names: ceil(0.5 * 4) = 2.
    assert screen.n_screened == _selected_count(4, 0.5) == 2
    assert screen.kept == ("A", "B")
    assert screen.excluded == ("C", "D", "E", "F", "G", "H")
    assert set(screen.kept).isdisjoint(screen.excluded)
    assert sorted((*screen.kept, *screen.excluded)) == ids
    assert set(screen.turnover) == {"A", "B", "C", "D"}
    assert screen.turnover["A"] == pytest.approx(19 * 10.0 / 100.0)


def test_a_split_inside_the_formation_month_leaves_the_turnover_rank_unchanged() -> None:
    """A 2-for-1 on 2024-06-13 against a no-split twin: the shares fact (dated before
    it) and the eight earlier bars move into post-split units. Without either move S's
    turnover changes and it crosses a cut at 1/3 (upward) or 2/3 (downward)."""
    ids = ["L", "S", "U"]
    shares = {sid: (date(2024, 5, 31), 1000.0) for sid in ids}
    others = _month("U", 12.0) + _month("L", 8.0)
    twin = _inputs(others + _month("S", 10.0), shares)
    split = _inputs(
        others + _month("S", 10.0, split_ex=SPLIT_EX), shares, {"S": ((SPLIT_EX, 2.0),)}
    )
    assert _screen(split, 1.0, ids).turnover == _screen(twin, 1.0, ids).turnover
    for fraction, kept in ((1 / 3, ("U",)), (2 / 3, ("S", "U"))):
        assert _screen(twin, fraction, ids).kept == kept
        assert _screen(split, fraction, ids).kept == kept


@pytest.mark.parametrize("as_of", [date(2024, 5, 31), date(2024, 4, 30)])
def test_a_fact_is_moved_only_by_the_splits_after_its_own_date(as_of: date) -> None:
    """A 2-for-1 on 2024-05-15: a fact dated after it is already post-split and is not
    moved again; a fact dated before it (1,000 raw) is moved by it too."""
    raw = 2000.0 if as_of > date(2024, 5, 15) else 1000.0
    inputs = _inputs(_month("S", 20.0), {"S": (as_of, raw)}, {"S": ((date(2024, 5, 15), 2.0),)})
    assert _screen(inputs, 1.0, ["S"]).turnover == {"S": 19 * 20.0 / 2000.0}


def test_the_screen_refuses_malformed_inputs() -> None:
    shares = {"A": (date(2024, 5, 31), 100.0)}
    inputs = _inputs(_month("A", 1.0), shares)
    for fraction in (0.0, 1.5):
        with pytest.raises(ValueError, match="must be in"):
            _screen(inputs, fraction, ["A"])
    with pytest.raises(ValueError, match="end at"):
        turnover_screen(inputs, FORMATION[:-1], T_SESSION, 0.5, security_ids=["A"])
    with pytest.raises(ValueError, match="is not the session of t"):
        turnover_screen(
            dataclasses.replace(inputs, t=KNOWN), FORMATION, T_SESSION, 0.5, security_ids=["A"]
        )
    with pytest.raises(ValueError, match="duplicate"):
        _screen(_inputs(_month("A", 1.0) * 2, shares), 0.5, ["A"])
    with pytest.raises(ValueError, match="must be positive"):
        _screen(_inputs(_month("A", 1.0), shares, {"A": ((SPLIT_EX, 0.0),)}), 0.5, ["A"])
