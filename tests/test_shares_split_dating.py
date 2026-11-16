"""A post-split share count dated before the split's ex-date (#3055).

SHW's 10-Q accepted 2021-04-28 gives 265.9M shares "as of 2021-03-31", the
count after its 3-for-1 split (ex 2021-04-01); TJX's accepted 2018-12-04 gives
1.233B "as of 2018-11-03", after its 2-for-1 split (ex 2018-11-07). Rule 8 moves
a fact by every split with `as_of_date < ex_date <= session`, so it applied the
split a second time: SHW's cap x3 for a quarter, TJX's x2. Under
`universe.shares_split_dating` (on), a fact accepted on or after the ex-date of
a split dated after it, whose value over the last accepted fact is closer to
that split's ratio than to 1, is read as already split: its value is divided by
the ratio at its own `as_of_date`, so rule 8 moves it back to the filed count.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store.db import insert_row
from tradepartner.universe import shares_as_of, universe_as_of

SID = "SEC_SPLIT_BETWEEN"  # fixture: 5M as of 2018-10-29; 3-for-1 split ex 2019-01-11
T_LATE = datetime(2019, 6, 28, 20, 0, tzinfo=UTC)
COVER = date(2019, 1, 4)  # a cover date one week before the ex-date
FILED_AFTER = datetime(2019, 2, 5, 21, 0, tzinfo=UTC)
FILED_BEFORE = datetime(2019, 1, 8, 21, 0, tzinfo=UTC)


def _settings(**universe: Any) -> Settings:
    """The switch on (it lands off, frozen at `false`)."""
    return Settings(_env_file=None, universe={"shares_split_dating": True, **universe})


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


def _shares(conn: duckdb.DuckDBPyConnection, settings: Settings) -> float:
    """SID's rule 8 shares (split-moved to the session) in the universe at `T_LATE`."""
    members = universe_as_of(conn, T_LATE, settings).members
    (row,) = members.filter(members["security_id"] == SID).iter_rows(named=True)
    return float(row["shares"])


def test_the_switch_lands_off_and_is_frozen_off() -> None:
    from tradepartner.backtest import frozen

    assert Settings(_env_file=None).universe.shares_split_dating is False
    assert ("universe.shares_split_dating", False, 46) in frozen.FROZEN_KEY_DEFAULTS


def test_a_post_split_count_dated_before_the_ex_date_is_not_split_twice(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, COVER, 15_300_000, FILED_AFTER)
    pick = shares_as_of(fixture_store, T_LATE, [SID], _settings())
    assert pick.shares[SID] == (COVER, pytest.approx(5_100_000))
    assert _shares(fixture_store, _settings()) == pytest.approx(15_300_000)


def test_switched_off_the_split_is_applied_again_as_before(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, COVER, 15_300_000, FILED_AFTER)
    off = _settings(shares_split_dating=False)
    assert shares_as_of(fixture_store, T_LATE, [SID], off).shares[SID] == (COVER, 15_300_000)
    assert _shares(fixture_store, off) == pytest.approx(45_900_000)


def test_a_pre_split_count_filed_after_the_ex_date_stays_as_filed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, COVER, 5_100_000, FILED_AFTER)
    assert shares_as_of(fixture_store, T_LATE, [SID], _settings()).shares[SID] == (
        COVER,
        5_100_000,
    )
    assert _shares(fixture_store, _settings()) == pytest.approx(15_300_000)


def test_a_fact_filed_before_the_ex_date_is_never_read_as_split(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """Accepted before the ex-date, the count cannot include the split: a 3x
    move is read as filed (an issuance), as before."""
    _fact(fixture_store, COVER, 15_300_000, FILED_BEFORE)
    assert shares_as_of(fixture_store, T_LATE, [SID], _settings()).shares[SID] == (
        COVER,
        15_300_000,
    )


def test_the_next_fact_after_the_ex_date_stays_in_line(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """SHW's next 10-Q (263.3M as of 2021-06-30) is in line with the read-as-split
    fact, so no fallback is reported and the latest fact is used."""
    _fact(fixture_store, COVER, 15_300_000, FILED_AFTER)
    _fact(fixture_store, date(2019, 4, 30), 15_200_000, datetime(2019, 5, 6, 21, 0, tzinfo=UTC))
    pick = shares_as_of(fixture_store, T_LATE, [SID], _settings())
    assert pick.shares[SID] == (date(2019, 4, 30), 15_200_000)
    assert pick.fallbacks.is_empty()
    assert pick.outliers.is_empty()


def test_a_first_fact_has_no_baseline_and_stays_as_filed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    fixture_store.execute("DELETE FROM facts WHERE security_id = ?", [SID])
    _fact(fixture_store, COVER, 15_300_000, FILED_AFTER)
    assert shares_as_of(fixture_store, T_LATE, [SID], _settings()).shares[SID] == (
        COVER,
        15_300_000,
    )
