"""`close_gaps` (#3077): stored close against the official close, fail-closed."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from tradepartner.close_gap import close_gaps
from tradepartner.config import Settings


def frame(rows: dict[date, float]) -> pl.DataFrame:
    return pl.DataFrame(
        {"session": list(rows), "close": list(rows.values())},
        schema={"session": pl.Date, "close": pl.Float64},
    )


def test_flags_the_march_2020_gap_and_passes_a_tight_one() -> None:
    stored = frame({date(2020, 3, 11): 274.25, date(2020, 3, 12): 255.24})
    official = frame({date(2020, 3, 11): 274.36, date(2020, 3, 12): 248.11})
    out = close_gaps(stored, official, 0.005)
    assert out["session"].to_list() == [date(2020, 3, 12)]
    assert out["gap"][0] == pytest.approx(255.24 / 248.11 - 1)


def test_a_missing_stored_bar_is_a_gap() -> None:
    official = frame({date(2020, 3, 12): 248.11})
    out = close_gaps(frame({date(2020, 3, 11): 1.0}), official, 0.005)
    assert out["session"].to_list() == [date(2020, 3, 12)]
    assert out["stored"][0] is None and out["gap"][0] is None


def test_an_empty_reference_refuses() -> None:
    with pytest.raises(ValueError, match="empty reference"):
        close_gaps(frame({date(2020, 3, 12): 1.0}), frame({}), 0.005)


def test_threshold_is_exclusive_and_not_negative() -> None:
    official = frame({date(2020, 3, 12): 100.0})
    assert close_gaps(frame({date(2020, 3, 12): 100.5}), official, 0.01).is_empty()
    assert close_gaps(frame({date(2020, 3, 12): 101.5}), official, 0.01).height == 1
    with pytest.raises(ValueError, match="negative"):
        close_gaps(frame({date(2020, 3, 12): 1.0}), official, -0.1)


def test_default_threshold_comes_from_config() -> None:
    assert Settings(_env_file=None).index_check.max_close_gap == 0.005  # type: ignore[call-arg]
