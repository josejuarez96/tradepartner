"""The report-only cumulative economic-fidelity line (#3180).

A book can pass every per-period tracking check while its cumulative return drifts
far from the trial's. `economic_fidelity` reads `compare_periods`'s own rows and
never changes them: every per-period verdict stays what it was. Hand-built journals
and a fixture trial, `test_report_months.py`'s "no store" idiom.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime

import polars as pl
import pytest

from tradepartner.backtest.hypothesis import frozen_keys
from tradepartner.backtest.schedule import rebalance_sessions
from tradepartner.config import FROZEN_PAPER_KEYS, Settings
from tradepartner.execution.report import (
    Journal,
    PeriodComparison,
    TrialPeriods,
    compare_periods,
    economic_fidelity,
)
from tradepartner.execution.window import _frozen_params
from tradepartner.store.journal import (
    DecisionRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    RebalanceEventRow,
)

WINDOW_ID = 7
SESSIONS = tuple(rebalance_sessions(date(2025, 12, 1), date(2026, 7, 31), "month_end"))
_IDS = itertools.count(100)


def _utc(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 20, tzinfo=UTC)


def _window(tracking_rule: str = "raw") -> PaperWindowRow:
    frozen = {
        "paper.tracking_k": 2.0,
        "paper.tracking_rule": tracking_rule,
        "execution.fill_price": "close",
    }
    return PaperWindowRow(
        window_id=WINDOW_ID,
        hypothesis_id=1,
        first_rebalance_session=SESSIONS[0],
        account_id="PA1",
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=_utc(SESSIONS[0]),
        frozen_json=json.dumps(frozen),
        frozen_sha256="0" * 64,
        known_at=_utc(SESSIONS[0]),
        ingested_at=_utc(SESSIONS[0]),
    )


def _equity(start: float, period_return: float, sessions: Sequence[date]) -> dict[date, float]:
    return {s: start * (1 + period_return) ** i for i, s in enumerate(sessions)}


def _journal(
    paper: Mapping[date, float],
    *,
    rebalance_events: Sequence[RebalanceEventRow] = (),
    decisions: Sequence[DecisionRow] = (),
) -> Journal:
    marks = [
        PositionDailyRow(
            run_id=i + 1,
            session=s,
            security_id=None,
            quantity=0.0,
            cash=cash,
            known_at=_utc(s),
            ingested_at=_utc(s),
        )
        for i, (s, cash) in enumerate(paper.items())
    ]
    runs = [
        PaperRunRow(
            run_id=i + 1,
            window_id=WINDOW_ID,
            session=s,
            kind="rebalance",
            started_at=_utc(s),
            invoked_by="scheduler",
            code_version="abc",
            known_at=_utc(s),
            ingested_at=_utc(s),
        )
        for i, s in enumerate(paper)
    ]
    return Journal(
        positions_daily=marks,
        adjustments=(),
        decisions=decisions,
        runs=runs,
        rebalance_events=rebalance_events,
    )


def _no_actions() -> pl.DataFrame:
    return pl.DataFrame(
        schema={
            "security_id": pl.Utf8,
            "action_type": pl.Utf8,
            "ex_date": pl.Date,
            "ratio_or_amount": pl.Float64,
            "known_at": pl.Datetime(time_zone="UTC"),
            "cancelled": pl.Boolean,
        }
    )


def _no_price(_security_id: str, _session: date) -> float | None:
    return None


def _compare(
    paper: Mapping[date, float],
    trial_equity: Mapping[date, float],
    *,
    cost: float = 0.01,
    tracking_rule: str = "raw",
    rebalance_events: Sequence[RebalanceEventRow] = (),
    decisions: Sequence[DecisionRow] = (),
) -> PeriodComparison:
    sessions = tuple(trial_equity)
    trial = TrialPeriods(
        sessions=sessions,
        equity=trial_equity,
        cost_paid={s: cost * trial_equity[s] for s in sessions},
    )
    return compare_periods(
        _window(tracking_rule),
        trial,
        _journal(paper, rebalance_events=rebalance_events, decisions=decisions),
        _no_actions(),
        _no_price,
        None,
    )


def _missed(session: date) -> RebalanceEventRow:
    return RebalanceEventRow(
        rebalance_session=session,
        run_id=1,
        status="missed",
        reason="catch_up_lapsed",
        known_at=_utc(session),
        ingested_at=_utc(session),
    )


def test_a_book_that_passes_every_period_can_diverge_cumulatively() -> None:
    """Paper beats the trial by 1.5% a period against a 2 x 1% tolerance: every
    period passes, yet over seven periods the cumulative gap is far past 100 bp."""
    trial_equity = _equity(100_000.0, 0.0, SESSIONS)
    paper = _equity(100_000.0, 0.015, SESSIONS)
    comparison = _compare(paper, trial_equity)
    assert comparison.passed
    assert all(p.passed for p in comparison.periods)
    assert len(comparison.periods) == len(SESSIONS) - 1

    fidelity = economic_fidelity(comparison, max_gap_bps=100.0, min_periods=6)

    expected_paper = 1.015 ** (len(SESSIONS) - 1) - 1
    assert fidelity.paper_return == pytest.approx(expected_paper)
    assert fidelity.trial_return == pytest.approx(0.0)
    assert fidelity.gap_bps == pytest.approx(expected_paper * 10_000)
    assert fidelity.periods == len(SESSIONS) - 1
    assert fidelity.verdict == "divergent"
    assert fidelity.excluded == ()
    assert fidelity.max_gap_bps == 100.0
    assert fidelity.min_periods == 6


def test_cumulative_returns_equal_the_endpoint_ratios() -> None:
    trial_equity = {s: 100_000.0 + 500.0 * i for i, s in enumerate(SESSIONS)}
    paper = {s: 50_000.0 + 240.0 * i for i, s in enumerate(SESSIONS)}
    fidelity = economic_fidelity(_compare(paper, trial_equity), max_gap_bps=100.0, min_periods=6)
    first, last = SESSIONS[0], SESSIONS[-1]
    assert fidelity.paper_return == pytest.approx(paper[last] / paper[first] - 1)
    assert fidelity.trial_return == pytest.approx(trial_equity[last] / trial_equity[first] - 1)
    assert fidelity.gap_bps == pytest.approx(
        (fidelity.paper_return - fidelity.trial_return) * 10_000
    )


def test_a_gap_within_the_threshold_is_consistent_and_the_edge_is_inclusive() -> None:
    trial_equity = _equity(100_000.0, 0.01, SESSIONS)
    comparison = _compare(trial_equity, trial_equity)
    fidelity = economic_fidelity(comparison, max_gap_bps=100.0, min_periods=6)
    assert fidelity.gap_bps == pytest.approx(0.0)
    assert fidelity.verdict == "consistent"

    # A gap exactly at the threshold is consistent (the edge is inclusive).
    exact = economic_fidelity(comparison, max_gap_bps=0.0, min_periods=6)
    assert exact.verdict == "consistent"


def test_a_negative_gap_past_the_threshold_is_divergent() -> None:
    trial_equity = _equity(100_000.0, 0.015, SESSIONS)
    paper = _equity(100_000.0, 0.0, SESSIONS)
    fidelity = economic_fidelity(_compare(paper, trial_equity), max_gap_bps=100.0, min_periods=6)
    assert fidelity.gap_bps < -100.0
    assert fidelity.verdict == "divergent"


def test_fewer_periods_than_the_minimum_is_too_few_periods_whatever_the_gap() -> None:
    sessions = SESSIONS[:4]
    trial_equity = _equity(100_000.0, 0.0, sessions)
    paper = _equity(100_000.0, 0.015, sessions)
    comparison = _compare(paper, trial_equity)
    fidelity = economic_fidelity(comparison, max_gap_bps=100.0, min_periods=6)
    assert fidelity.periods == 3
    assert fidelity.gap_bps > 100.0  # still computed and printed
    assert fidelity.verdict == "too_few_periods"
    assert economic_fidelity(comparison, max_gap_bps=100.0, min_periods=3).verdict == ("divergent")


def test_no_periods_is_too_few_periods() -> None:
    empty = PeriodComparison(
        tracking_rule="raw", tracking_k=2.0, periods=(), passed=True, failing_period=None
    )
    fidelity = economic_fidelity(empty, max_gap_bps=100.0, min_periods=1)
    assert fidelity.periods == 0
    assert fidelity.paper_return == 0.0
    assert fidelity.trial_return == 0.0
    assert fidelity.verdict == "too_few_periods"


def test_excluded_periods_are_listed_and_stay_in_the_cumulative() -> None:
    """A missed period (raw rule) and an override period (residual rule) are excluded
    from the per-period check; the cumulative line keeps their returns and names them."""
    trial_equity = _equity(100_000.0, 0.0, SESSIONS)
    paper = dict(trial_equity)
    # A 10% jump in the missed period: excluded from the check, never from the cumulative.
    for s in SESSIONS[3:]:
        paper[s] = trial_equity[s] * 1.10
    missed_at = SESSIONS[2]
    override_at = SESSIONS[4]
    override = DecisionRow(
        decision_id=next(_IDS),
        run_id=1,
        rebalance_session=override_at,
        security_id="SEC_A",
        whole_share=False,
        decision="override",
        known_at=_utc(override_at),
        ingested_at=_utc(override_at),
    )
    comparison = _compare(
        paper,
        trial_equity,
        tracking_rule="residual",
        rebalance_events=[_missed(missed_at)],
        decisions=[override],
    )
    assert comparison.passed
    fidelity = economic_fidelity(comparison, max_gap_bps=100.0, min_periods=6)
    assert fidelity.excluded == ((missed_at, "missed"), (override_at, "override"))
    assert fidelity.periods == len(SESSIONS) - 1
    assert fidelity.paper_return == pytest.approx(0.10)
    assert fidelity.verdict == "divergent"


def test_the_fidelity_line_never_changes_a_per_period_verdict() -> None:
    """Byte-identical verdicts: the comparison is the same object before and after,
    and its rows' check fields are what `compare_periods` alone decides."""
    trial_equity = _equity(100_000.0, 0.0, SESSIONS)
    paper = _equity(100_000.0, 0.015, SESSIONS)
    paper[SESSIONS[-1]] = paper[SESSIONS[-2]] * 1.05  # one failing period at the end
    comparison = _compare(paper, trial_equity)
    before = repr(comparison)
    verdicts = [(p.rebalance_session, p.excluded, p.passed) for p in comparison.periods]
    economic_fidelity(comparison, max_gap_bps=1.0, min_periods=1)
    assert repr(comparison) == before
    assert not comparison.passed
    assert comparison.failing_period == SESSIONS[-2]
    assert verdicts == [(s, False, True) for s in SESSIONS[:-2]] + [(SESSIONS[-2], False, False)]


def test_period_returns_are_paper_and_trial_and_their_difference_is_raw() -> None:
    trial_equity = _equity(100_000.0, 0.01, SESSIONS)
    paper = _equity(100_000.0, 0.012, SESSIONS)
    for p in _compare(paper, trial_equity).periods:
        assert p.paper_return == pytest.approx(0.012)
        assert p.trial_return == pytest.approx(0.01)
        assert p.raw == p.paper_return - p.trial_return  # type: ignore[operator]


def test_a_hand_built_row_without_returns_is_refused() -> None:
    trial_equity = _equity(100_000.0, 0.0, SESSIONS[:3])
    comparison = _compare(trial_equity, trial_equity)
    bare = replace(comparison.periods[0], paper_return=None, trial_return=None)
    with pytest.raises(ValueError, match="no paper or trial return"):
        economic_fidelity(replace(comparison, periods=(bare,)), max_gap_bps=100.0, min_periods=1)


def test_non_finite_returns_are_refused() -> None:
    trial_equity = _equity(100_000.0, 0.0, SESSIONS[:3])
    comparison = _compare(trial_equity, trial_equity)
    bad = replace(comparison.periods[0], paper_return=math.nan)
    with pytest.raises(ValueError, match="not finite"):
        economic_fidelity(replace(comparison, periods=(bad,)), max_gap_bps=100.0, min_periods=1)


def test_the_thresholds_are_config_defaults_and_validated() -> None:
    paper = Settings(_env_file=None).paper
    assert paper.fidelity_max_gap_bps == pytest.approx(100.0)
    assert paper.fidelity_min_periods == 6
    for bad in ({"fidelity_max_gap_bps": -1.0}, {"fidelity_min_periods": 0}):
        with pytest.raises(ValueError):
            Settings(_env_file=None, paper=bad)


def test_the_thresholds_enter_no_window_or_trial_fingerprint() -> None:
    """Report-only, run-time keys: never in a window's `frozen_json` (and so never in
    its `frozen_sha256`) nor in a hypothesis registration's frozen set."""
    names = ("fidelity_max_gap_bps", "fidelity_min_periods")
    assert not set(names) & set(FROZEN_PAPER_KEYS)
    assert not any(k.endswith(names) for k in frozen_keys())
    assert not any(k.startswith("paper.") for k in frozen_keys())

    def frozen_of(settings: Settings) -> dict[str, object]:
        costs = settings.costs.model_dump()
        registered = {
            "costs.per_side_bps": costs["per_side_bps"],
            "costs.commission_per_share": costs["commission_per_share"],
            "costs.commission_per_order": costs["commission_per_order"],
            "execution.fill_price": settings.execution.fill_price,
        }
        return _frozen_params(settings, registered, "month_end")

    default = frozen_of(Settings(_env_file=None))
    changed = frozen_of(
        Settings(_env_file=None, paper={"fidelity_max_gap_bps": 7.0, "fidelity_min_periods": 2})
    )
    assert default == changed
    assert not any("fidelity" in k for k in default)
