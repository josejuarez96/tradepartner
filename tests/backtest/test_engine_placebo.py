"""The kill ladder's rung-5 placebos in the engine (backtest spec amendment #1820, item 2;
ADR 0023; plan T217c).

Locked tests (split trial, phase 1): written before the build, against the plan line's
Tests clause. Under `strategy.placebo` the plan replaces the family's scores after the
signal and before construction (`placebo.shuffle` or `placebo.random_entry`, seeded by
the string `"<placebo_seed>:<rebalance session ordinal>"`, T217's pure generators):
`Plan.scores` holds the placebo's scores, which construction used, and every other plan
field (members, exclusions, counts, the gap) is the real signal's.

The main fixture is `test_engine_many`'s random walk (five names A-E, scored at every
session) plus a member Z with no bars, so every real plan excludes Z as `no_history`
and the placebo plans must keep that exclusion and its count. The hand rankings below are
`random.Random(f"3:{session.toordinal()}").shuffle(["A", "B", "C", "D", "E"])` on Python
3.12 (`test_placebo.test_generator_pin` pins the generator).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from backtest import test_engine, test_engine_delay, test_engine_hold, test_engine_many
from backtest.fake_provider import FakeProvider
from backtest.test_results import BENCHMARKS, GAPS, NAMES, SESSIONS, _prices
from tradepartner.backtest import placebo
from tradepartner.backtest.engine import BacktestResult, Plan, plan, run
from tradepartner.backtest.portfolio import target_weights
from tradepartner.backtest.schedule import fill_session, rebalance_sessions
from tradepartner.config import Cadence, Settings

LEVELS = (0.0, 15.0)
MONTHLY_START, MONTHLY_END = date(2024, 1, 31), date(2024, 9, 30)
MONTHLY = rebalance_sessions(MONTHLY_START, MONTHLY_END, "month_end")
DAILY_START, DAILY_END = date(2024, 5, 1), date(2024, 5, 10)
DAILY = rebalance_sessions(DAILY_START, DAILY_END, "daily")

#: The seed-3 rankings, best first, at each plan session (`random_entry` scores them
#: 5 down to 1). Month-ends, then the daily sessions of the `hold` case.
RANKING_SEED_3 = {
    date(2024, 1, 31): "DECAB",
    date(2024, 2, 29): "CBDEA",
    date(2024, 3, 28): "ACBDE",
    date(2024, 4, 30): "DAEBC",
    date(2024, 5, 31): "DEBAC",
    date(2024, 6, 28): "AEDBC",
    date(2024, 7, 31): "DBEAC",
    date(2024, 8, 30): "CBEAD",
    date(2024, 5, 1): "BCEDA",
    date(2024, 5, 2): "ABDCE",
    date(2024, 5, 3): "ACBDE",
    date(2024, 5, 6): "ACEBD",
    date(2024, 5, 7): "EBDAC",
    date(2024, 5, 8): "BEDCA",
    date(2024, 5, 9): "CABDE",
}
#: `random_entry` under `hold` (daily, hold_sessions = 2, hold_target_names = 2), held
#: after each fill, by hand: plan 0 enters B, C; plan 2 exits them and enters A, C from
#: its own ranking; plan 4 enters E, B; plan 6 enters C, A.
HOLD_HELD_SEED_3 = {
    date(2024, 5, 2): {"B", "C"},
    date(2024, 5, 3): {"B", "C"},
    date(2024, 5, 6): {"A", "C"},
    date(2024, 5, 7): {"A", "C"},
    date(2024, 5, 8): {"B", "E"},
    date(2024, 5, 9): {"B", "E"},
    date(2024, 5, 10): {"A", "C"},
}


def _provider() -> FakeProvider:
    members = {session: [*NAMES, "Z"] for session in SESSIONS}
    return FakeProvider(prices=_prices(7), members=members, benchmarks=BENCHMARKS, gaps=GAPS)


def _params(cadence: Cadence = "month_end", **strategy: Any) -> Settings:
    return test_engine_many._params(cadence, **strategy)


def _hold(**strategy: Any) -> Settings:
    return _params("daily", weighting="hold", hold_sessions=2, hold_target_names=2, **strategy)


def _run(settings: Settings, start: date, end: date) -> dict[float, BacktestResult]:
    return run(
        settings, _provider(), start, end, test_engine_many._handle(1), LEVELS, family="momentum"
    )


def _seed(seed: int, session: date) -> str:
    return f"{seed}:{session.toordinal()}"


def _hand_scores(session: date) -> dict[str, float]:
    ranking = RANKING_SEED_3[session]
    return {sid: float(len(ranking) - i) for i, sid in enumerate(ranking)}


def _held(result: BacktestResult, session: date) -> set[str]:
    values = result.position_values.filter(pl.col("session") == session)
    return {sid for sid, value in values.select("security_id", "value").iter_rows() if value > 0}


def _assert_real_except_scores(got: Plan, real: Plan) -> None:
    """Every plan field but `scores` and `targets` is the real signal's."""
    for name in (
        "session",
        "fill_session",
        "members",
        "n_universe",
        "n_static_listings",
        "gap",
        "n_excluded_no_history",
        "excluded_no_history",
        "counts",
        "exclusions",
    ):
        assert getattr(got, name) == getattr(real, name), (real.session, name)


# --- the defaults (pass on main; must stay green) ------------------------------------


def test_placebo_none_and_seed_zero_named_explicitly_are_the_twin_run() -> None:
    settings = test_engine_delay._with(
        test_engine._twin_settings(), "strategy", placebo="none", placebo_seed=0
    )
    results = test_engine_hold._twin_results(settings)
    digests = {level: test_engine._run_digest(result) for level, result in results.items()}
    assert digests == test_engine.TWIN_RUN_DIGESTS


def test_fixture_real_plans_exclude_z_and_score_the_five() -> None:
    """Precondition: the real plan has an exclusion and a count for the placebo to keep."""
    for session in MONTHLY[:-1]:
        real = plan(_provider(), _params(), session, "momentum")
        assert real.exclusions["no_history"] == ("Z",)
        assert real.counts["n_excluded_no_history"] == 1
        assert sorted(real.scores) == list(NAMES)


def test_hand_rankings_are_the_generators() -> None:
    """The literal rankings above are `placebo.random_entry`'s at seed 3."""
    for session, ranking in RANKING_SEED_3.items():
        scores = placebo.random_entry(dict.fromkeys(NAMES, 0.0), _seed(3, session))
        assert scores == _hand_scores(session), (session, ranking)


# --- shuffle -------------------------------------------------------------------------


def test_shuffle_plan_holds_the_shuffled_scores_and_the_real_plans_other_fields() -> None:
    settings = _params(placebo="shuffle", placebo_seed=7)
    moved = 0
    for session in MONTHLY[:-1]:
        real = plan(_provider(), _params(), session, "momentum")
        got = plan(_provider(), settings, session, "momentum")
        shuffled = placebo.shuffle(real.scores, _seed(7, session))
        assert got.scores == shuffled, session
        assert got.targets == target_weights(shuffled, 0.4, "equal"), session
        _assert_real_except_scores(got, real)
        moved += got.targets != real.targets
    assert moved > 0  # the placebo does move the book


def test_shuffle_with_one_seed_is_identical_across_two_runs() -> None:
    settings = _params(placebo="shuffle", placebo_seed=7)
    test_engine_many._assert_same(
        _run(settings, MONTHLY_START, MONTHLY_END), _run(settings, MONTHLY_START, MONTHLY_END)
    )


def test_shuffle_differs_across_seeds_and_from_the_real_run() -> None:
    seven = _run(_params(placebo="shuffle", placebo_seed=7), MONTHLY_START, MONTHLY_END)
    eight = _run(_params(placebo="shuffle", placebo_seed=8), MONTHLY_START, MONTHLY_END)
    real = _run(_params(), MONTHLY_START, MONTHLY_END)
    assert seven[15.0].targets != eight[15.0].targets
    assert seven[15.0].targets != real[15.0].targets


# --- random_entry under `equal` ------------------------------------------------------


def test_random_entry_plan_scores_are_the_hand_ranking() -> None:
    settings = _params(placebo="random_entry", placebo_seed=3)
    for session in MONTHLY[:-1]:
        real = plan(_provider(), _params(), session, "momentum")
        got = plan(_provider(), settings, session, "momentum")
        assert got.scores == _hand_scores(session), session
        assert got.scores == placebo.random_entry(real.scores, _seed(3, session))
        _assert_real_except_scores(got, real)


def test_random_entry_run_trades_the_hand_rankings_top_two_under_equal() -> None:
    """top_fraction 0.4 of five scored names selects two: the ranking's first two, each
    at one half, filled at the session after each month-end."""
    settings = _params(placebo="random_entry", placebo_seed=3)
    for result in _run(settings, MONTHLY_START, MONTHLY_END).values():
        expected = {
            fill_session(session, "month_end"): dict.fromkeys(
                sorted(RANKING_SEED_3[session][:2]), 0.5
            )
            for session in MONTHLY[:-1]
        }
        assert result.targets == expected


# --- random_entry under `hold` (T209b) -----------------------------------------------


def test_hold_fixture_real_run_differs_from_the_placebos() -> None:
    real = _run(_hold(), DAILY_START, DAILY_END)[0.0]
    assert any(_held(real, fill) != held for fill, held in HOLD_HELD_SEED_3.items())


def test_random_entry_under_hold_enters_from_the_hand_ranking_and_keeps_live_names() -> None:
    for result in _run(
        _hold(placebo="random_entry", placebo_seed=3), DAILY_START, DAILY_END
    ).values():
        for fill, held in HOLD_HELD_SEED_3.items():
            assert _held(result, fill) == held, fill


def test_random_entry_under_hold_plans_the_hand_rankings_candidates() -> None:
    """`Plan.targets` under `hold` are the ranking's top `hold_target_names`, and
    `Plan.scores` the ranking itself (the hold book ranks entries by them)."""
    settings = _hold(placebo="random_entry", placebo_seed=3)
    result = _run(settings, DAILY_START, DAILY_END)[0.0]
    for session in DAILY[:-1]:
        expected = dict.fromkeys(sorted(RANKING_SEED_3[session][:2]), 0.5)
        assert result.targets[fill_session(session, "daily")] == expected, session
        got = plan(_provider(), settings, session, "momentum")
        assert got.scores == _hand_scores(session), session
        _assert_real_except_scores(got, plan(_provider(), _hold(), session, "momentum"))


# --- exclusions and counts under B10's screen, on the fixture store ------------------


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


@pytest.fixture(scope="module")
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    with test_engine_delay.open_fixture_store() as conn:
        yield conn


PLACEBOS: Mapping[str, Any] = {"shuffle": placebo.shuffle, "random_entry": placebo.random_entry}


@pytest.mark.parametrize("mode", list(PLACEBOS))
def test_placebo_keeps_the_screens_exclusions_and_counts_on_the_fixture_store(
    store: duckdb.DuckDBPyConnection, mode: str
) -> None:
    """With B10's turnover screen on (`no_turnover` exclusions, `n_screened` counts), a
    placebo plan's exclusions, counts and members are the real plan's and its scores are
    the placebo's over the real scored names."""
    base = test_engine_delay._monthly_screened()
    shocked = test_engine_delay._with(base, "strategy", placebo=mode, placebo_seed=7)
    sessions = rebalance_sessions(
        test_engine_delay.MONTHLY_START, test_engine_delay.MONTHLY_END, "month_end"
    )
    real = test_engine_delay._store_plans(store, base, sessions)
    got = test_engine_delay._store_plans(store, shocked, sessions)
    for session in sessions:
        assert real[session].exclusions.get("no_turnover"), session
        _assert_real_except_scores(got[session], real[session])
        assert got[session].scores == PLACEBOS[mode](real[session].scores, _seed(7, session))
