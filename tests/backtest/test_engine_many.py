"""`engine.run_many`: a read group from one read set per step (strategy-lab spec req 2,
the "Read groups" and "Failure isolation" criteria; plan T105).

Every test runs the variants over `backtest.test_results`' prices (five names and both
benchmarks on a seeded random walk; the five are members at every session, so every
cadence plans), at three cost levels, and compares them with separate `engine.run`
calls on a fresh provider.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

import polars as pl
import pytest

from backtest.fake_provider import FakeProvider
from backtest.test_results import BENCHMARKS, END, GAPS, NAMES, SESSIONS, START, _prices
from tradepartner.backtest import engine
from tradepartner.backtest.engine import (
    BacktestResult,
    RunManyResults,
    SharedReadFailed,
    run,
    run_many,
)
from tradepartner.backtest.provider import DataProvider
from tradepartner.backtest.schedule import read_time, rebalance_sessions
from tradepartner.config import Cadence, HypothesisFamily, Settings
from tradepartner.store import registry

LEVELS = (0.0, 15.0, 30.0)


def _provider() -> FakeProvider:
    members = {session: list(NAMES) for session in SESSIONS}
    return FakeProvider(prices=_prices(7), members=members, benchmarks=BENCHMARKS, gaps=GAPS)


def _handle(trial_id: int) -> registry.TrialHandle:
    return registry._issue_handle(
        trial_id=trial_id,
        hypothesis_id=trial_id,
        family="momentum",
        params_sha256="0" * 64,
        kind="in_sample",
        synthetic=True,
        started_at=datetime(2024, 11, 1, tzinfo=UTC),
        store_max_ingested_at=None,
        database=None,
    )


def _params(cadence: Cadence = "month_end", **strategy: Any) -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.4, **strategy},
        schedule={"rebalance_cadence": cadence},
        costs={"per_side_bps": 15.0, "sensitivity_per_side_bps": [0.0, 30.0]},
    )


def _variants(*params: Settings) -> list[tuple[Settings, registry.TrialHandle]]:
    return [(p, _handle(i)) for i, p in enumerate(params, start=1)]


def _alone(params: Settings, trial_id: int, start: date, end: date) -> dict[float, BacktestResult]:
    return run(params, _provider(), start, end, _handle(trial_id), LEVELS, family="momentum")


def _assert_same(got: Mapping[float, BacktestResult], want: Mapping[float, BacktestResult]) -> None:
    """Row for row: equity, rebalances, weights, targets, positions and frames."""
    assert sorted(got) == sorted(want)
    for level, result in want.items():
        other = got[level]
        assert other.equity == result.equity, level
        assert other.rebalances == result.rebalances, level
        assert other.weights == result.weights, level
        assert other.targets == result.targets, level
        assert other.position_values.equals(result.position_values), level
        assert len(other.marking_frames) == len(result.marking_frames)
        for mine, theirs in zip(other.marking_frames, result.marking_frames, strict=True):
            assert (mine.start, mine.end) == (theirs.start, theirs.end)
            assert mine.frame.equals(theirs.frame), (level, mine.end)


def _calls(provider: FakeProvider) -> Counter[tuple[str, datetime]]:
    return Counter((call.method, call.t) for call in provider.calls)


#: (cadence, start, end): the window each cadence runs over (daily kept short).
WINDOWS: dict[str, tuple[Cadence, date, date]] = {
    "month_end": ("month_end", START, END),
    "week_end": ("week_end", date(2024, 3, 1), date(2024, 6, 28)),
    "daily": ("daily", date(2024, 5, 1), date(2024, 5, 31)),
}


@pytest.mark.parametrize("window", list(WINDOWS.values()), ids=list(WINDOWS))
def test_without_marking_frames_a_group_keeps_none_and_every_other_field_is_equal(
    window: tuple[Cadence, date, date],
) -> None:
    """#1414: `keep_marking_frames=False` keeps no frame and changes nothing else."""
    cadence, start, end = window
    variants = _variants(_params(cadence, top_fraction=0.4), _params(cadence, top_fraction=0.8))
    kept = run_many(variants, _provider(), start, end, LEVELS, family="momentum")
    dropped = run_many(
        variants, _provider(), start, end, LEVELS, family="momentum", keep_marking_frames=False
    )
    assert sorted(dropped) == sorted(kept) == [1, 2] and not dropped.failures
    for trial_id, levels in kept.items():
        assert all(result.marking_frames for result in levels.values())
        assert all(result.marking_frames == () for result in dropped[trial_id].values())
        _assert_same(
            dropped[trial_id],
            {lv: dataclasses.replace(r, marking_frames=()) for lv, r in levels.items()},
        )


@pytest.mark.parametrize("window", list(WINDOWS.values()), ids=list(WINDOWS))
def test_without_detail_a_group_keeps_no_positions_or_weights_and_the_rest_is_equal(
    window: tuple[Cadence, date, date],
) -> None:
    """#1448: `keep_detail=False` keeps no position values and no weight rows at any
    level, and changes nothing else."""
    cadence, start, end = window
    variants = _variants(_params(cadence, top_fraction=0.4), _params(cadence, top_fraction=0.8))
    kept = run_many(variants, _provider(), start, end, LEVELS, family="momentum")
    dropped = run_many(
        variants, _provider(), start, end, LEVELS, family="momentum", keep_detail=False
    )
    assert sorted(dropped) == sorted(kept) == [1, 2] and not dropped.failures
    for trial_id, levels in kept.items():
        assert all(r.weights and not r.position_values.is_empty() for r in levels.values())
        for result in dropped[trial_id].values():
            assert result.weights == ()
            assert result.position_values.is_empty()
            assert (
                result.position_values.schema
                == levels[result.cost_per_side_bps].position_values.schema
            )
        _assert_same(
            dropped[trial_id],
            {
                lv: dataclasses.replace(r, weights=(), position_values=r.position_values.clear())
                for lv, r in levels.items()
            },
        )


class TestReadGroups:
    """The spec's "Read groups" criterion."""

    @pytest.mark.parametrize("window", list(WINDOWS.values()), ids=list(WINDOWS))
    def test_two_top_fraction_variants_read_like_one_run_and_equal_separate_runs(
        self, window: tuple[Cadence, date, date]
    ) -> None:
        cadence, start, end = window
        first, second = _params(cadence, top_fraction=0.4), _params(cadence, top_fraction=0.8)
        one = _provider()
        run(first, one, start, end, _handle(1), LEVELS, family="momentum")
        shared = _provider()
        out = run_many(_variants(first, second), shared, start, end, LEVELS, family="momentum")
        # Exactly one run's provider calls at every read time: one read set per step.
        assert _calls(shared) == _calls(one)
        assert not out.failures
        _assert_same(out[1], _alone(first, 1, start, end))
        _assert_same(out[2], _alone(second, 2, start, end))
        assert out[1][15.0].targets != out[2][15.0].targets  # the variants do differ

    def test_formation_variants_share_one_signal_read_from_the_earliest_bound(self) -> None:
        """`formation_months` moves A_form, so the group's one signal read starts at the
        earliest bound and each variant sees its own frame (the bound applies after the
        as-of read, T99)."""
        twelve, six = _params(formation_months=12), _params(formation_months=6)
        one = _provider()
        run(twelve, one, START, END, _handle(1), LEVELS, family="momentum")
        shared = _provider()
        out = run_many(_variants(six, twelve), shared, START, END, LEVELS, family="momentum")
        assert _calls(shared) == _calls(one)
        signal_reads = [
            c for c in shared.calls if c.method == "adjusted_prices" and c.sessions_from
        ]
        alone = [c for c in one.calls if c.method == "adjusted_prices" and c.sessions_from]
        assert [c.sessions_from for c in signal_reads] == [c.sessions_from for c in alone]
        _assert_same(out[1], _alone(six, 1, START, END))
        _assert_same(out[2], _alone(twelve, 2, START, END))

    def test_variants_differing_in_signal_total_return_still_equal_separate_runs(self) -> None:
        """Not one read group (different reads), yet still served right: each request
        that no shared read covers is made as asked."""
        price, total = _params(signal_total_return=False), _params(signal_total_return=True)
        out = run_many(_variants(price, total), _provider(), START, END, LEVELS, family="momentum")
        _assert_same(out[1], _alone(price, 1, START, END))
        _assert_same(out[2], _alone(total, 2, START, END))

    def test_a_variant_without_a_handle_raises_before_any_read(self) -> None:
        provider = _provider()
        variants: list[Any] = [(_params(), _handle(1)), (_params(top_fraction=0.8), None)]
        with pytest.raises(TypeError, match="TrialHandle"):
            run_many(variants, provider, START, END, LEVELS, family="momentum")
        assert provider.calls == []

    def test_two_cadences_or_one_trial_twice_are_refused_before_any_read(self) -> None:
        provider = _provider()
        with pytest.raises(ValueError, match="one cadence"):
            run_many(
                _variants(_params("month_end"), _params("week_end")),
                provider,
                START,
                END,
                LEVELS,
                family="momentum",
            )
        twice = [(_params(), _handle(1)), (_params(top_fraction=0.8), _handle(1))]
        with pytest.raises(ValueError, match="own trial"):
            run_many(twice, provider, START, END, LEVELS, family="momentum")
        assert provider.calls == []


#: The four variants of the failure tests, trial ids 1 to 4.
FOUR = (0.2, 0.4, 0.6, 0.8)
#: The rebalance whose plan fails for variant 3 (inside the loop, not the first plan).
FAIL_AT = rebalance_sessions(START, END)[2]


def _failing_for_variant_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Variant 3's own signal raises at `FAIL_AT` (a variant-specific fake)."""
    original = engine._plan

    def plan(
        provider: DataProvider, params: Settings, session: date, family: HypothesisFamily
    ) -> engine.Plan:
        if params.strategy.top_fraction == FOUR[2] and session == FAIL_AT:
            raise ArithmeticError("variant 3's signal broke")
        return original(provider, params, session, family)

    monkeypatch.setattr(engine, "_plan", plan)


@dataclasses.dataclass
class _BrokenRaw(FakeProvider):
    """Raw bars fail at one read time: a shared read every variant needs."""

    broken_at: datetime | None = None

    def raw_prices(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        if t == self.broken_at:
            raise OSError("store went away")
        return super().raw_prices(t, ids)


def _broken(at: date) -> _BrokenRaw:
    base = _provider()
    return _BrokenRaw(
        prices=base.prices, members=base.members, benchmarks=BENCHMARKS, broken_at=read_time(at)
    )


class TestFailureIsolation:
    """The spec's "Failure isolation" criterion, as `run_many` sees it."""

    def test_variant_3s_own_error_leaves_1_2_and_4_intact(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        params = [_params(top_fraction=f) for f in FOUR]
        want = {i: _alone(p, i, START, END) for i, p in enumerate(params, start=1)}
        _failing_for_variant_3(monkeypatch)
        out = run_many(_variants(*params), _provider(), START, END, LEVELS, family="momentum")
        assert isinstance(out, RunManyResults)
        assert sorted(out) == [1, 2, 4]
        assert list(out.failures) == [3]
        assert isinstance(out.failures[3], ArithmeticError)
        for trial_id in (1, 2, 4):
            _assert_same(out[trial_id], want[trial_id])

    def test_a_shared_read_error_names_every_open_variant(self) -> None:
        broken_at = rebalance_sessions(START, END)[3]
        params = [_params(top_fraction=f) for f in FOUR]
        with pytest.raises(SharedReadFailed, match="shared read failed") as raised:
            run_many(_variants(*params), _broken(broken_at), START, END, LEVELS, family="momentum")
        assert raised.value.trial_ids == (1, 2, 3, 4)
        assert isinstance(raised.value.cause, OSError)

    def test_a_shared_read_error_names_only_the_variants_still_open(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _failing_for_variant_3(monkeypatch)
        broken_at = rebalance_sessions(START, END)[4]  # after variant 3 failed
        params = [_params(top_fraction=f) for f in FOUR]
        with pytest.raises(SharedReadFailed) as raised:
            run_many(_variants(*params), _broken(broken_at), START, END, LEVELS, family="momentum")
        assert raised.value.trial_ids == (1, 2, 4)

    def test_run_raises_a_read_error_as_itself(self) -> None:
        """`run` is `run_many` with one variant, and its errors are unchanged."""
        with pytest.raises(OSError, match="store went away"):
            run(
                _params(),
                _broken(rebalance_sessions(START, END)[1]),
                START,
                END,
                _handle(1),
                LEVELS,
                family="momentum",
            )

    def test_run_raises_its_own_computation_error_as_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _failing_for_variant_3(monkeypatch)
        with pytest.raises(ArithmeticError, match="variant 3"):
            run(
                _params(top_fraction=FOUR[2]),
                _provider(),
                START,
                END,
                _handle(3),
                LEVELS,
                family="momentum",
            )
