"""The H1 hypothesis file parses through `backtest.hypothesis` (plan T35b, spec req 18).

`docs/hypotheses/h1-momentum-12-1.md` must name every required key with `family=momentum`
and the owner's answers to spec open questions 1, 2 and 8 (#156). Nothing here registers
it: the owner does that on the real store after merge (T45b).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from tradepartner.backtest import hypothesis, schedule
from tradepartner.calendar import last_session_of_month
from tradepartner.config import Settings

H1_PATH = Path(__file__).resolve().parents[2] / "docs" / "hypotheses" / "h1-momentum-12-1.md"
SLUG = "h1-momentum-12-1"
LAST_IN_SAMPLE_REBALANCE = date(2023, 12, 29)


def test_h1_file_parses_with_slug_family_and_dates() -> None:
    parsed = hypothesis.parse_file(H1_PATH)
    assert parsed.slug == SLUG == H1_PATH.stem
    assert parsed.family == "momentum"
    assert parsed.family in Settings(_env_file=None).hypotheses.families
    assert parsed.title
    # Owner answers to spec open questions 8 and 2 (#156).
    assert parsed.in_sample_start == date(2017, 1, 31)
    assert parsed.holdout_start == date(2024, 1, 1)
    assert parsed.holdout_end == date(2026, 9, 30)


def test_h1_file_names_every_required_key() -> None:
    parsed = hypothesis.parse_file(H1_PATH)
    assert hypothesis.required_keys() <= set(parsed.file_params)
    assert set(parsed.file_params) <= set(hypothesis.frozen_keys())


def test_h1_file_records_owner_answers_and_spec_defaults() -> None:
    params = hypothesis.parse_file(H1_PATH).file_params
    # Open question 1: top 10% of the universe, equal weight.
    assert params["strategy.top_fraction"] == 0.10
    assert params["strategy.weighting"] == "equal"
    # Spec defaults for the rest of strategy.* and costs.*.
    assert params["strategy.formation_months"] == 12
    assert params["strategy.skip_months"] == 1
    assert params["strategy.signal_total_return"] is True
    assert params["costs.per_side_bps"] == 15.0
    assert params["costs.commission_per_share"] == 0.0
    assert params["costs.commission_per_order"] == 0.0
    assert params["costs.sensitivity_per_side_bps"] == [0.0, 30.0, 60.0, 100.0]
    # The three optional frozen keys the file's prose relies on.
    assert params["universe.top_n_by_cap"] == 1000
    assert params["execution.fill_price"] == "close"
    assert params["alpaca.historical_feed"] == "sip"


def test_h1_frozen_set_builds_over_default_settings() -> None:
    parsed = hypothesis.parse_file(H1_PATH)
    frozen = hypothesis.frozen_params(parsed, Settings(_env_file=None))
    assert set(frozen) == set(hypothesis.frozen_keys())
    assert frozen["holdout.start"] == "2024-01-01"
    assert frozen["holdout.end"] == "2026-09-30"
    assert frozen["strategy.top_fraction"] == 0.10
    assert frozen["universe.top_n_by_cap"] == 1000
    assert frozen["execution.fill_price"] == "close"
    assert frozen["alpaca.historical_feed"] == "sip"


def test_h1_power_arithmetic_counts() -> None:
    """The session counts the file's power arithmetic states, on the XNYS calendar."""
    parsed = hypothesis.parse_file(H1_PATH)
    assert parsed.in_sample_start == last_session_of_month(2017, 1)
    # Spec req 11: the in-sample default window ends at the last rebalance before holdout.start.
    before_holdout = schedule.rebalance_sessions(
        parsed.in_sample_start, parsed.holdout_start - timedelta(days=1)
    )
    assert before_holdout[-1] == LAST_IN_SAMPLE_REBALANCE
    assert len(before_holdout) == 84
    # The holdout window itself holds 33 month-ends; the pinned holdout run starts one
    # rebalance earlier so that January 2024 belongs to a window (34 sessions, 33 returns).
    assert len(schedule.rebalance_sessions(parsed.holdout_start, parsed.holdout_end)) == 33
    assert len(schedule.rebalance_sessions(LAST_IN_SAMPLE_REBALANCE, parsed.holdout_end)) == 34
    assert parsed.holdout_end == last_session_of_month(2026, 9)
