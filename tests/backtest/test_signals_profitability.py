"""Tests for `signals.gross_profitability` (backtest spec amendment #720, rules 0 to 6;
plan T85b). Synthetic facts frames only; no store."""

from __future__ import annotations

import ast
import math
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from tradepartner.backtest import signals
from tradepartner.backtest.portfolio import target_weights
from tradepartner.backtest.signals import ProfitabilitySignal, gross_profitability
from tradepartner.calendar import is_half_day, last_session_of_month, session_close

T_SESSION = last_session_of_month(2024, 6)  # 2024-06-28
T = session_close(T_SESSION)
FY = date(2023, 12, 31)  # the latest fiscal year end visible at T
FILED = datetime(2024, 2, 20, 21, 5, tzinfo=UTC)

DEFAULTS: dict[str, object] = {
    "annual_period_days": (350, 380),
    "max_fact_age_days": 548,
    "exclude_sic_ranges": ((6000, 6999),),
    "include_derived": True,
    "basis": "gross",
}

Row = tuple[str, str, date, int, float, datetime, str]


def gp(
    sid: str,
    value: float,
    *,
    end: date = FY,
    days: int = 365,
    known_at: datetime = FILED,
    basis: str = "reported",
) -> Row:
    """A `gross_profit` duration row."""
    return (sid, "gross_profit", end, days, value, known_at, basis)


def ta(sid: str, value: float, *, end: date = FY, known_at: datetime = FILED) -> Row:
    """A `total_assets` instant row (`period_days = 0`)."""
    return (sid, "total_assets", end, 0, value, known_at, "reported")


def frame(rows: list[Row]) -> pl.DataFrame:
    """A frame shaped like `statement_facts_as_of`'s (the columns the signal reads)."""
    return pl.DataFrame(
        {
            "security_id": [r[0] for r in rows],
            "fact_name": [r[1] for r in rows],
            "period_start": [r[2] - timedelta(days=r[3]) if r[3] else None for r in rows],
            "period_end": [r[2] for r in rows],
            "period_days": [r[3] for r in rows],
            "value": [r[4] for r in rows],
            "basis": [r[6] for r in rows],
            "known_at": [r[5] for r in rows],
        },
        schema={
            "security_id": pl.Utf8,
            "fact_name": pl.Utf8,
            "period_start": pl.Date,
            "period_end": pl.Date,
            "period_days": pl.Int32,
            "value": pl.Float64,
            "basis": pl.Utf8,
            "known_at": pl.Datetime("us", "UTC"),
        },
    )


def run(
    rows: list[Row],
    *,
    t: datetime = T,
    ids: list[str] | None = None,
    sics: dict[str, int | None] | None = None,
    **overrides: object,
) -> ProfitabilitySignal:
    names = ids if ids is not None else sorted({r[0] for r in rows})
    kwargs = {**DEFAULTS, **overrides}
    return gross_profitability(
        frame(rows),
        sics if sics is not None else {},
        t,
        security_ids=names,
        **kwargs,  # type: ignore[arg-type]
    )


def test_scores_gross_profit_over_total_assets() -> None:
    sig = run([gp("A", 40.0), ta("A", 200.0), gp("B", 10.0), ta("B", 100.0)])
    assert sig.scores == {"A": pytest.approx(0.2), "B": pytest.approx(0.1)}
    assert all(not ids for ids in sig.excluded.values())
    assert sig.counts["n_ranked"] == 2


# Rule 0 and the read time.


def test_row_known_after_t_is_ignored() -> None:
    late = T + timedelta(seconds=1)
    rows = [
        gp("A", 40.0),
        ta("A", 200.0),
        # A's next fiscal year, accepted one second after the close: must not be used.
        gp("A", 999.0, end=date(2024, 3, 31), known_at=late),
        ta("A", 1.0, end=date(2024, 3, 31), known_at=late),
        # B has only rows accepted after t.
        gp("B", 10.0, known_at=late),
        ta("B", 100.0, known_at=late),
    ]
    sig = run(rows)
    assert sig.scores == {"A": pytest.approx(0.2)}
    assert sig.excluded["no_facts"] == ("B",)


def test_row_known_exactly_at_t_is_used() -> None:
    sig = run([gp("A", 40.0, known_at=T), ta("A", 200.0, known_at=T)])
    assert sig.scores == {"A": pytest.approx(0.2)}


def test_naive_t_raises() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        run([gp("A", 40.0), ta("A", 200.0)], t=T.replace(tzinfo=None))


@pytest.mark.parametrize("zone", ["America/New_York", "Asia/Tokyo"])
def test_non_utc_t_is_read_as_the_same_instant(zone: str) -> None:
    # #1051: a tz-aware close in another zone (Tokyo's wall date is the next day) is
    # the same instant as the UTC close; `known_at <= t` holds at it and a row one
    # second later stays unseen.
    t_local = T.astimezone(ZoneInfo(zone))
    late = T + timedelta(seconds=1)
    rows = [
        gp("A", 40.0, known_at=T),
        ta("A", 200.0, known_at=T),
        gp("B", 10.0, known_at=late),
        ta("B", 100.0, known_at=late),
    ]
    sig = run(rows, t=t_local)
    assert sig == run(rows)
    assert sig.scores == {"A": pytest.approx(0.2)}
    assert sig.excluded["no_facts"] == ("B",)


def test_t_out_of_utc_range_raises_value_error() -> None:
    with pytest.raises(ValueError, match="out of the range"):
        run(
            [gp("A", 40.0), ta("A", 200.0)],
            t=datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5))),
        )


@pytest.mark.parametrize(
    "t",
    [
        T - timedelta(hours=2),  # mid-session on T
        T + timedelta(seconds=1),  # just after T's close
        T + timedelta(microseconds=1),  # no tolerance around the close
        datetime(2024, 7, 3, 20, 0, tzinfo=UTC),  # a half day at the normal 16:00 close
        T - timedelta(days=1) + timedelta(hours=8),  # the night after the previous close
        session_close(T_SESSION) + timedelta(days=1),  # Saturday, 24h after Friday's close
        datetime(2024, 7, 4, 20, 0, tzinfo=UTC),  # a holiday at a normal close time
    ],
    ids=[
        "mid-session",
        "after-close",
        "after-close-1us",
        "half-day-16h",
        "overnight",
        "weekend",
        "holiday",
    ],
)
def test_t_that_is_not_a_session_close_raises(t: datetime) -> None:
    """#1084: the spec's `t` is `close(T)`; any other instant would measure freshness
    from the previous session, so it is refused."""
    with pytest.raises(ValueError, match="not a session close"):
        run([gp("A", 40.0), ta("A", 200.0)], t=t)


@pytest.mark.parametrize(
    "day",
    [date(2024, 7, 3), date(2024, 3, 11), date(2024, 11, 4)],
    ids=["half-day", "after-spring-forward", "after-fall-back"],
)
def test_session_close_is_accepted_on_half_days_and_across_dst(day: date) -> None:
    """The guard compares UTC instants: a 13:00 New York half-day close, and the 20:00Z
    and 21:00Z closes either side of a DST switch, are all session closes."""
    assert is_half_day(day) == (day == date(2024, 7, 3))
    sig = run([gp("A", 40.0), ta("A", 200.0)], t=session_close(day))
    assert sig.scores == {"A": pytest.approx(0.2)}


def test_no_look_ahead_future_rows_never_change_the_result() -> None:
    """Every row accepted after `t` (a newer year, a fresher denominator, a better
    value for an excluded name) leaves the result equal to the visible frame's."""
    visible = [
        gp("A", 40.0),
        ta("A", 200.0),
        gp("B", 10.0, end=date(2022, 6, 30)),  # stale at T
        ta("B", 100.0, end=date(2022, 6, 30)),
        gp("C", 5.0),  # no denominator at T
    ]
    late = T + timedelta(microseconds=1)
    future = [
        gp("A", 1.0, end=date(2024, 6, 28), known_at=late),
        ta("A", 1000.0, end=date(2024, 6, 28), known_at=late),
        gp("B", 90.0, known_at=late),
        ta("B", 100.0, known_at=late),
        ta("C", 10.0, known_at=late),
        gp("D", 50.0, known_at=late + timedelta(days=30)),
        ta("D", 60.0, known_at=late + timedelta(days=30)),
    ]
    ids = ["A", "B", "C", "D"]
    assert run(visible + future, ids=ids) == run(visible, ids=ids)


# Rule 2: which numerator row.


@pytest.mark.parametrize(
    ("label", "days"), [("quarter", 91), ("nine-month", 273), ("10-KT stub", 184)]
)
def test_non_annual_rows_are_ignored(label: str, days: int) -> None:
    later = date(2024, 3, 31)
    rows = [
        gp("A", 40.0),
        ta("A", 200.0),
        gp("A", 999.0, end=later, days=days),  # newer period_end, but not annual
        ta("A", 1.0, end=later),
        gp("B", 10.0, end=later, days=days),  # B's only numerator is not annual
        ta("B", 100.0, end=later),
    ]
    sig = run(rows)
    assert sig.scores == {"A": pytest.approx(0.2)}, label
    assert sig.excluded["no_facts"] == ("B",)


@pytest.mark.parametrize("days", [363, 370, 350, 380])
def test_52_and_53_week_years_are_annual(days: int) -> None:
    sig = run([gp("A", 40.0, days=days), ta("A", 200.0)])
    assert sig.scores == {"A": pytest.approx(0.2)}


@pytest.mark.parametrize("days", [349, 381])
def test_outside_annual_bounds_is_ignored(days: int) -> None:
    sig = run([gp("A", 40.0, days=days), ta("A", 200.0)])
    assert sig.excluded["no_facts"] == ("A",)


def test_two_annual_rows_with_one_period_end_take_the_larger_period_days() -> None:
    rows = [gp("A", 40.0, days=364), gp("A", 60.0, days=371), ta("A", 200.0)]
    assert run(rows).scores == {"A": pytest.approx(0.3)}


def test_comparative_is_superseded_by_the_filings_own_year_once_known() -> None:
    fy22, fy23 = date(2022, 12, 31), date(2023, 12, 31)
    fy22_filed = datetime(2023, 2, 21, 21, 0, tzinfo=UTC)
    rows = [
        # FY2022, first seen in the FY2022 10-K.
        gp("A", 30.0, end=fy22, known_at=fy22_filed),
        ta("A", 150.0, end=fy22, known_at=fy22_filed),
        # FY2023 from the FY2023 10-K.
        gp("A", 60.0, end=fy23, known_at=FILED),
        ta("A", 200.0, end=fy23, known_at=FILED),
        # B first files with the FY2023 10-K: its FY2022 rows are that filing's
        # comparatives, accepted together with its own year.
        gp("B", 10.0, end=fy22),
        ta("B", 100.0, end=fy22),
        gp("B", 50.0, end=fy23),
        ta("B", 100.0, end=fy23),
    ]
    before = session_close(last_session_of_month(2024, 1))
    assert run(rows, t=before).scores == {"A": pytest.approx(0.2)}  # FY2022: 30/150
    assert run(rows).scores == {"A": pytest.approx(0.3), "B": pytest.approx(0.5)}


# Rules 3 to 5: exclusions, each under its reason.


def test_no_annual_row_is_no_facts() -> None:
    sig = run([ta("A", 200.0)])
    assert sig.scores == {}
    assert sig.excluded["no_facts"] == ("A",)


def test_requested_name_without_rows_is_no_facts_and_unrequested_rows_ignored() -> None:
    sig = run([gp("Z", 40.0), ta("Z", 200.0)], ids=["A"])
    assert sig.scores == {}
    assert sig.excluded["no_facts"] == ("A",)


def test_total_assets_at_another_period_end_is_no_facts() -> None:
    sig = run([gp("A", 40.0), ta("A", 200.0, end=date(2023, 9, 30))])
    assert sig.excluded["no_facts"] == ("A",)


def test_total_assets_known_after_t_is_no_facts() -> None:
    sig = run([gp("A", 40.0), ta("A", 200.0, known_at=T + timedelta(seconds=1))])
    assert sig.excluded["no_facts"] == ("A",)


def test_stale_pair_is_excluded_and_the_bound_is_inclusive() -> None:
    at_bound = T_SESSION - timedelta(days=548)
    past_bound = at_bound - timedelta(days=1)
    rows = [
        gp("A", 40.0, end=at_bound),
        ta("A", 200.0, end=at_bound),
        gp("B", 10.0, end=past_bound),
        ta("B", 100.0, end=past_bound),
    ]
    sig = run(rows)
    assert sig.scores == {"A": pytest.approx(0.2)}
    assert sig.excluded["stale_facts"] == ("B",)
    assert run(rows, max_fact_age_days=600).scores.keys() == {"A", "B"}


@pytest.mark.parametrize(
    ("numerator", "denominator"),
    [
        (40.0, 0.0),
        (40.0, -5.0),
        (40.0, math.nan),
        (40.0, math.inf),
        (math.nan, 200.0),
        (math.inf, 200.0),
        (-math.inf, 200.0),
    ],
)
def test_malformed_values_are_excluded_never_scored(numerator: float, denominator: float) -> None:
    sig = run([gp("A", numerator), ta("A", denominator)])
    assert sig.scores == {}
    assert sig.excluded["malformed"] == ("A",)


def test_negative_gross_profit_is_scored() -> None:
    assert run([gp("A", -10.0), ta("A", 200.0)]).scores == {"A": pytest.approx(-0.05)}


def test_stale_comes_before_malformed() -> None:
    old = T_SESSION - timedelta(days=900)
    sig = run([gp("A", 40.0, end=old), ta("A", 0.0, end=old)])
    assert sig.excluded["stale_facts"] == ("A",)
    assert sig.excluded["malformed"] == ()


# Rule 1: scope.


def test_sic_in_a_range_is_excluded_before_scoring_and_missing_sic_passes() -> None:
    rows = [r for sid in "ABCDE" for r in (gp(sid, 40.0), ta(sid, 200.0))]
    sics: dict[str, int | None] = {"A": 6000, "B": 6999, "C": 5999, "D": None}
    sig = run(rows, sics=sics)  # E has no SIC entry at all
    assert sig.excluded["sector"] == ("A", "B")
    assert sorted(sig.scores) == ["C", "D", "E"]


def test_sector_comes_before_no_facts() -> None:
    sig = run([], ids=["A"], sics={"A": 6500})
    assert sig.excluded["sector"] == ("A",)
    assert sig.excluded["no_facts"] == ()


# include_derived.


def test_include_derived_true_uses_and_counts_derived_rows() -> None:
    rows = [gp("A", 40.0, basis="derived"), ta("A", 200.0), gp("B", 10.0), ta("B", 100.0)]
    sig = run(rows, include_derived=True)
    assert sig.scores == {"A": pytest.approx(0.2), "B": pytest.approx(0.1)}
    assert sig.derived == ("A",)
    assert sig.counts["n_derived"] == 1


def test_include_derived_false_skips_derived_rows() -> None:
    older = date(2022, 12, 31)
    rows = [
        gp("A", 40.0, basis="derived"),
        ta("A", 200.0),
        # B falls back to its older reported year once its derived year is skipped.
        gp("B", 99.0, basis="derived"),
        ta("B", 100.0),
        gp("B", 30.0, end=older),
        ta("B", 150.0, end=older),
    ]
    sig = run(rows, include_derived=False)
    assert sig.scores == {"B": pytest.approx(0.2)}
    assert sig.excluded["no_facts"] == ("A",)
    assert sig.derived == ()
    assert sig.counts["n_derived"] == 0


# Rule 6: ranking and weights.


def test_ties_rank_by_security_id() -> None:
    rows = [r for sid in ("C", "A", "B") for r in (gp(sid, 20.0), ta(sid, 100.0))]
    rows += [gp("D", 50.0), ta("D", 100.0)]
    sig = run(rows)
    assert sig.ranked == ("D", "A", "B", "C")


def test_weights_come_from_target_weights_over_scored_names_only() -> None:
    scored = [r for i in range(5) for r in (gp(f"S{i}", 10.0 + i), ta(f"S{i}", 100.0))]
    excluded = [ta(f"X{i}", 100.0) for i in range(5)]  # no numerator: no_facts
    sig = run(scored + excluded)
    assert len(sig.excluded["no_facts"]) == 5
    # ceil(0.2 * 5 scored) = 1 name; over all 10 requested names it would be 2.
    assert target_weights(sig.scores, 0.2, "equal") == {"S4": 1.0}


def test_counts_cover_every_requested_name_once() -> None:
    rows = [
        gp("A", 40.0),
        ta("A", 200.0),
        gp("B", 40.0, basis="derived"),
        ta("B", 200.0),
        gp("C", 40.0),
        ta("C", 0.0),
        gp("D", 40.0, end=date(2020, 12, 31)),
        ta("D", 200.0, end=date(2020, 12, 31)),
    ]
    sig = run(rows, ids=["A", "B", "C", "D", "E", "F"], sics={"F": 6100})
    assert sig.counts == {
        "n_ranked": 2,
        "n_excluded_no_facts": 1,
        "n_excluded_stale_facts": 1,
        "n_excluded_sector": 1,
        "n_excluded_malformed": 1,
        "n_derived": 1,
    }


# Arguments.


def test_unimplemented_basis_raises() -> None:
    with pytest.raises(ValueError, match="basis"):
        run([gp("A", 40.0), ta("A", 200.0)], basis="cash")


def test_signals_module_holds_no_period_age_or_sic_literal() -> None:
    """Every period length, age and SIC is an argument (spec acceptance, #720).

    Module-wide, the only numeric literals are 0, 1 and 2 (the momentum code's bounds,
    one-day step and ISO-week slice); the profitability code holds none but 0."""
    source = Path(signals.__file__).read_text()
    tree = ast.parse(source)

    def literals(node: ast.AST) -> set[object]:
        return {
            n.value
            for n in ast.walk(node)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, int | float)
            and not isinstance(n.value, bool)
        }

    assert literals(tree) <= {0, 1, 2}
    prof = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef | ast.ClassDef)
        and ("profitab" in n.name.lower() or n.name.startswith("_gp"))
    ]
    assert {n.name for n in prof} >= {"gross_profitability", "ProfitabilitySignal"}
    for node in prof:
        assert literals(node) <= {0}, node.name
