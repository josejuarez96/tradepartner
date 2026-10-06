"""The B3 hypothesis file parses through `backtest.hypothesis` (backtest plan T85;
spec amendment #720). Its window is compared with H1's file, not with `family_rules`.
Nothing here registers it: the owner does that on the real store (T85f).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from tradepartner.backtest import hypothesis, schedule
from tradepartner.config import Settings

HYPOTHESES = Path(__file__).resolve().parents[2] / "docs" / "hypotheses"
B3_PATH = HYPOTHESES / "b3-gross-profitability.md"
H1_PATH = HYPOTHESES / "h1-momentum-12-1.md"


def test_b3_file_parses_with_slug_and_family() -> None:
    parsed = hypothesis.parse_file(B3_PATH)
    assert parsed.slug == B3_PATH.stem
    assert parsed.family == "profitability"
    assert parsed.family in Settings(_env_file=None).hypotheses.families
    assert hypothesis.required_keys("profitability") <= set(parsed.file_params)
    assert not any(k.startswith("strategy.") for k in parsed.file_params)


def test_b3_window_equals_h1s() -> None:
    b3, h1 = hypothesis.parse_file(B3_PATH), hypothesis.parse_file(H1_PATH)
    assert b3.in_sample_start == h1.in_sample_start
    assert (b3.holdout_start, b3.holdout_end) == (h1.holdout_start, h1.holdout_end)


def test_b3_session_counts_match_h1s() -> None:
    """41 in-sample rebalance sessions and 34 for the pinned holdout run, counted as
    `tests/backtest/test_h1_file.py` counts them."""
    parsed = hypothesis.parse_file(B3_PATH)
    before_holdout = schedule.rebalance_sessions(
        parsed.in_sample_start, parsed.holdout_start - timedelta(days=1)
    )
    assert len(before_holdout) == 41
    assert len(schedule.rebalance_sessions(before_holdout[-1], parsed.holdout_end)) == 34
