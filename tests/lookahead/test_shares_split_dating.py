"""No look-ahead in rule 7's split dating (#3055, `universe.shares_split_dating`),
nor in the shares cap-jump review list (`universe.shares_cap_jumps`).

A fact dated before a split's ex-date and accepted after it is read as already
split only from the split row's own `known_at`: before it, the fact is read as
filed. The read at `t` equals the read on the store truncated at `t`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import pytest

from lookahead.harness import TruncatedStore
from tradepartner.config import Settings
from tradepartner.store.db import insert_row
from tradepartner.universe import shares_as_of, shares_cap_jumps, universe_as_of

SID = "SEC_SPLIT_BETWEEN"  # fixture: 5M as of 2018-10-29; 3-for-1 split ex 2019-01-11
SETTINGS = Settings(_env_file=None, universe={"shares_split_dating": True})
COVER = date(2019, 1, 4)
FILED = datetime(2019, 2, 5, 21, 0, tzinfo=UTC)
SPLIT_KNOWN = datetime(2019, 3, 1, 21, 0, tzinfo=UTC)  # a late split row
PROBES = [
    datetime(2019, 1, 31, 21, 0, tzinfo=UTC),  # the fact not yet filed
    datetime(2019, 2, 6, 21, 0, tzinfo=UTC),  # filed, the split not yet known
    datetime(2019, 2, 28, 21, 0, tzinfo=UTC),
    SPLIT_KNOWN,
    datetime(2019, 3, 29, 20, 0, tzinfo=UTC),
]


@pytest.fixture
def store(fixture_store: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """SID's split row known only from `SPLIT_KNOWN`; a post-split count dated
    `COVER` (before the ex-date) accepted `FILED` (after it)."""
    fixture_store.execute(
        "UPDATE corporate_actions SET known_at = ?, ingested_at = ? "
        "WHERE security_id = ? AND action_type = 'split'",
        [SPLIT_KNOWN, SPLIT_KNOWN, SID],
    )
    insert_row(
        fixture_store,
        "facts",
        {
            "security_id": SID,
            "fact_name": "shares_outstanding",
            "as_of_date": COVER,
            "class_member": "",
            "value": 15_300_000,
            "filing_accession": "0000000000-190104-0",
            "known_at": FILED,
            "ingested_at": FILED,
            "source": "edgar",
            "provenance": "filing",
        },
    )
    return fixture_store


@pytest.fixture
def truncated(store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    cut = TruncatedStore(store)
    try:
        yield cut
    finally:
        cut.close()


def test_a_split_known_later_never_dates_a_fact_at_t(store: duckdb.DuckDBPyConnection) -> None:
    before = shares_as_of(store, datetime(2019, 2, 28, 21, 0, tzinfo=UTC), [SID], SETTINGS)
    assert before.shares[SID] == (COVER, 15_300_000)
    after = shares_as_of(store, SPLIT_KNOWN, [SID], SETTINGS)
    assert after.shares[SID] == (COVER, pytest.approx(5_100_000))


@pytest.mark.parametrize("t", PROBES)
def test_the_read_at_t_equals_the_truncated_read(
    store: duckdb.DuckDBPyConnection, truncated: TruncatedStore, t: datetime
) -> None:
    full = shares_as_of(store, t, None, SETTINGS)
    cut = shares_as_of(truncated.at(t), t, None, SETTINGS)
    assert full.shares == cut.shares
    assert full.outliers.equals(cut.outliers)
    u_full = universe_as_of(store, t, SETTINGS)
    u_cut = universe_as_of(truncated.at(t), t, SETTINGS)
    assert u_full.members.equals(u_cut.members)


@pytest.mark.parametrize("t", PROBES)
def test_the_cap_jump_list_at_t_equals_the_truncated_list(
    store: duckdb.DuckDBPyConnection, truncated: TruncatedStore, t: datetime
) -> None:
    """A fact or bar known after `t` never enters, nor leaves, the list at `t`: the
    `COVER` fact (x3 at its pre-split close) is listed from its own `known_at`."""
    full = shares_cap_jumps(store, t, SETTINGS)
    assert full.equals(shares_cap_jumps(truncated.at(t), t, SETTINGS))
    assert (COVER in full["as_of_date"].to_list()) == (t >= FILED)
