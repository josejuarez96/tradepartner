"""The shares cap-jump review list (#3055): `universe.shares_cap_jumps`.

A shares fact times the raw close at its own `as_of_date` is the cap the filing
implies; a split moves the count and the close together, so the product does not
move with it. A fact whose implied cap is out of line, by more than
`health.shares_cap_jump_ratio` either way, with both its neighbours (or with the
one before it, when it is the latest) is listed for review: WFC's 1.82B between
3.75B and 3.66B (cap halved), SHW's post-split count at the pre-split close (x3),
a filer's x1,000 scale error. Report only: it changes no universe.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.health import health_report
from tradepartner.store.db import insert_row
from tradepartner.universe import shares_cap_jumps

SID = "SEC_SPLIT_BETWEEN"  # fixture: 5M as of 2018-10-29; 3-for-1 split ex 2019-01-11
BEFORE_SPLIT = datetime(2019, 1, 9, 21, 0, tzinfo=UTC)
T_LATE = datetime(2019, 6, 28, 20, 0, tzinfo=UTC)


def _settings(**health: Any) -> Settings:
    return Settings(_env_file=None, health=health)


def _fact(conn: duckdb.DuckDBPyConnection, as_of: date, value: float, known: datetime) -> None:
    insert_row(
        conn,
        "facts",
        {
            "security_id": SID,
            "fact_name": "shares_outstanding",
            "as_of_date": as_of,
            "class_member": "",
            "value": value,
            "filing_accession": f"0000000000-{as_of:%y%m%d}-0",
            "known_at": known,
            "ingested_at": known,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def _known(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 21, 0, tzinfo=UTC)


def _flagged(conn: duckdb.DuckDBPyConnection, t: datetime, **health: Any) -> list[date]:
    frame = shares_cap_jumps(conn, t, _settings(**health))
    return frame.filter(frame["security_id"] == SID)["as_of_date"].to_list()


def test_facts_in_line_are_not_listed(fixture_store: duckdb.DuckDBPyConnection) -> None:
    _fact(fixture_store, date(2018, 11, 30), 5_050_000, _known(date(2018, 12, 3)))
    _fact(fixture_store, date(2018, 12, 31), 5_100_000, _known(date(2019, 1, 3)))
    assert _flagged(fixture_store, BEFORE_SPLIT) == []


def test_a_halved_count_between_two_in_line_ones_is_listed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """WFC 2023-07-21: 1.82B between 3.75B and 3.66B, no split."""
    _fact(fixture_store, date(2018, 11, 30), 2_000_000, _known(date(2018, 12, 3)))
    _fact(fixture_store, date(2018, 12, 31), 4_950_000, _known(date(2019, 1, 3)))
    frame = shares_cap_jumps(fixture_store, BEFORE_SPLIT, _settings())
    (row,) = frame.filter(frame["security_id"] == SID).iter_rows(named=True)
    assert row["as_of_date"] == date(2018, 11, 30)
    assert row["value"] == 2_000_000
    assert row["prev_as_of"] == date(2018, 10, 29)
    assert row["next_as_of"] == date(2018, 12, 31)
    assert row["cap"] == pytest.approx(row["value"] * row["close"])
    assert row["cap"] / row["prev_cap"] < 1 / 2


def test_a_lasting_level_change_is_listed_only_while_it_is_the_latest(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """A stock merger doubles the count and the cap: listed until a next fact
    confirms the level."""
    _fact(fixture_store, date(2018, 11, 30), 10_500_000, _known(date(2018, 12, 3)))
    assert _flagged(fixture_store, datetime(2018, 12, 4, 21, 0, tzinfo=UTC)) == [date(2018, 11, 30)]
    _fact(fixture_store, date(2018, 12, 31), 10_400_000, _known(date(2019, 1, 3)))
    assert _flagged(fixture_store, BEFORE_SPLIT) == []


def test_a_split_moves_count_and_close_together_and_is_not_listed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, date(2019, 4, 30), 15_200_000, _known(date(2019, 5, 6)))
    assert _flagged(fixture_store, T_LATE) == []


def test_a_post_split_count_at_the_pre_split_close_is_listed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """SHW 2021-03-31: the post-split count dated before the ex-date (x3)."""
    _fact(fixture_store, date(2019, 1, 4), 15_300_000, _known(date(2019, 2, 5)))
    _fact(fixture_store, date(2019, 4, 30), 15_200_000, _known(date(2019, 5, 6)))
    assert _flagged(fixture_store, T_LATE) == [date(2019, 1, 4)]


def test_the_ratio_comes_from_config(fixture_store: duckdb.DuckDBPyConnection) -> None:
    _fact(fixture_store, date(2018, 11, 30), 2_000_000, _known(date(2018, 12, 3)))
    _fact(fixture_store, date(2018, 12, 31), 4_950_000, _known(date(2019, 1, 3)))
    assert _flagged(fixture_store, BEFORE_SPLIT, shares_cap_jump_ratio=3.0) == []


def test_a_fact_with_no_close_near_its_date_is_skipped(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """No bar within `health.shares_cap_close_days` before the date: no implied cap."""
    _fact(fixture_store, date(2016, 6, 30), 50_000, _known(date(2016, 7, 5)))
    assert date(2016, 6, 30) not in _flagged(fixture_store, BEFORE_SPLIT)


def test_health_carries_the_list(fixture_store: duckdb.DuckDBPyConnection) -> None:
    _fact(fixture_store, date(2018, 11, 30), 2_000_000, _known(date(2018, 12, 3)))
    _fact(fixture_store, date(2018, 12, 31), 4_950_000, _known(date(2019, 1, 3)))
    report = health_report(fixture_store, BEFORE_SPLIT, _settings())
    frame = report.shares_cap_jumps.frame
    assert frame.filter(frame["security_id"] == SID)["as_of_date"].to_list() == [date(2018, 11, 30)]
