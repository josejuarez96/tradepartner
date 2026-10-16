"""No look-ahead in rule 7's class-member link and explained jump (#1682, T190).

A class title learned from a later cover page (a listing revision known after
`t`) never links a member at `t`, and a spin-off or a new common class known
after `t` never explains a shares jump at `t`: the read at `t` equals the read
on the store truncated at `t`, and it changes only from the evidence's own
`known_at`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import pytest

from lookahead.harness import TruncatedStore
from tradepartner.config import Settings
from tradepartner.store.db import insert_row
from tradepartner.universe import shares_as_of, universe_as_of

SID = "SEC_SPLIT_BETWEEN"  # fixture: 5M as of 2018-10-29, listed "Common Stock"
SETTINGS = Settings(_env_file=None)
FACT_DAY = date(2019, 2, 28)
JUMP_DAY = date(2019, 5, 31)
RETITLED = datetime(2019, 4, 1, 21, 0, tzinfo=UTC)  # the cover page naming Class B
SPUN = datetime(2019, 7, 1, 21, 0, tzinfo=UTC)  # the spin-off row's known_at
PROBES = [
    datetime(2019, 3, 4, 21, 0, tzinfo=UTC),  # both class rows known, no title yet
    datetime(2019, 3, 31, 21, 0, tzinfo=UTC),
    RETITLED,
    datetime(2019, 6, 3, 21, 0, tzinfo=UTC),  # the jump known, its spin-off not yet
    datetime(2019, 6, 28, 21, 0, tzinfo=UTC),
    SPUN,
    datetime(2019, 7, 15, 21, 0, tzinfo=UTC),
]


def _row(conn: duckdb.DuckDBPyConnection, table: str, known: datetime, **cols: object) -> None:
    insert_row(conn, table, {**cols, "known_at": known, "ingested_at": known})


def _fact(
    conn: duckdb.DuckDBPyConnection, as_of: date, member: str, value: float, known: datetime
) -> None:
    _row(
        conn,
        "facts",
        known,
        security_id=SID,
        fact_name="shares_outstanding",
        as_of_date=as_of,
        class_member=member,
        value=value,
        filing_accession=f"0000000000-{as_of:%y%m%d}-{member or 0}",
        source="edgar",
        provenance="filing",
    )


@pytest.fixture
def store(fixture_store: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """Two class rows on one date (`CommonClassB` beside exchangeable shares)
    known 2019-03-01, the class retitled "Class B Common Stock" only from
    `RETITLED`; then a 1,000x jump known 2019-06-03, explained by a spin-off
    (ex 2019-04-15, inside the span) known only from `SPUN`."""
    known = datetime(2019, 3, 1, 21, 0, tzinfo=UTC)
    _fact(fixture_store, FACT_DAY, "CommonClassB", 15_000_000, known)
    _fact(fixture_store, FACT_DAY, "ClassBExchangeableShares", 900_000, known)
    _row(
        fixture_store,
        "listings",
        RETITLED,
        security_id=SID,
        ticker="SPBT",
        exchange="NYSE",
        class_title="Class B Common Stock",
        valid_from=date(2017, 1, 3),
        source="edgar",
        provenance="filing",
    )
    _fact(fixture_store, JUMP_DAY, "", 15_000_000_000, datetime(2019, 6, 3, 21, 0, tzinfo=UTC))
    _row(
        fixture_store,
        "corporate_actions",
        SPUN,
        security_id=SID,
        action_type="spinoff",
        ex_date=date(2019, 4, 15),
        ratio_or_amount=0.5,
        announced_at=None,
        source_action_id="SEC_PARENT",
        cancelled=False,
        source="alpaca",
        provenance="action",
    )
    return fixture_store


@pytest.fixture
def truncated(store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    cut = TruncatedStore(store)
    try:
        yield cut
    finally:
        cut.close()


def test_a_later_title_never_links_a_member_at_t(store: duckdb.DuckDBPyConnection) -> None:
    before = shares_as_of(store, datetime(2019, 3, 31, 21, 0, tzinfo=UTC), [SID], SETTINGS)
    assert before.ambiguous == {SID}
    after = shares_as_of(store, RETITLED, [SID], SETTINGS)
    assert after.ambiguous == set()
    assert after.shares[SID] == (FACT_DAY, 15_000_000)


def test_a_later_spinoff_never_explains_a_jump_at_t(store: duckdb.DuckDBPyConnection) -> None:
    before = shares_as_of(store, datetime(2019, 6, 28, 21, 0, tzinfo=UTC), [SID], SETTINGS)
    assert before.shares[SID] == (FACT_DAY, 15_000_000)
    assert before.outliers["explained"].to_list() == [False]
    assert before.fallbacks["as_of_date"].to_list() == [JUMP_DAY]
    after = shares_as_of(store, SPUN, [SID], SETTINGS)
    assert after.shares[SID] == (JUMP_DAY, 15_000_000_000)
    assert after.outliers["explained"].to_list() == [True]


def test_the_link_and_the_explanation_are_truncation_invariant(
    store: duckdb.DuckDBPyConnection, truncated: TruncatedStore
) -> None:
    for t in PROBES:
        full = shares_as_of(store, t, None, SETTINGS)
        cut = shares_as_of(truncated.at(t), t, None, SETTINGS)
        assert full.shares == cut.shares, t
        assert full.ambiguous == cut.ambiguous, t
        assert full.outliers.equals(cut.outliers), t
        assert full.fallbacks.equals(cut.fallbacks), t
        u_full = universe_as_of(store, t, SETTINGS)
        u_cut = universe_as_of(truncated.at(t), t, SETTINGS)
        assert u_full.members.equals(u_cut.members), t
        assert u_full.exclusions.equals(u_cut.exclusions), t
