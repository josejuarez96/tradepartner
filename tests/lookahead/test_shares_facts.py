"""No look-ahead in rule 7's class-member link and explained jump (#1682, T190),
nor in the per-class link at ingest (T190d, `edgar.class_member_link`).

A class title learned from a later cover page (a listing revision known after
`t`) never links a member at `t`, and a spin-off or a new common class known
after `t` never explains a shares jump at `t`: the read at `t` equals the read
on the store truncated at `t`, and it changes only from the evidence's own
`known_at`. At ingest, the class a fact accepted at `t` lands on is the same
as when every cover page and filing accepted after `t` is withheld: a class
title first known after `t` links nothing, and a cover page known after `t`
neither kills nor revives a class.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from lookahead.harness import TruncatedStore
from tradepartner.adapters.filings import (
    CoverListing,
    CoverPage,
    FactRecord,
    FilingHeader,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.config import Settings
from tradepartner.ingest import _build_filings
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


# --- T190d: the per-class link at ingest ------------------------------------

CIK = "0000000009"
LINKED = SETTINGS.model_copy(
    update={"edgar": SETTINGS.edgar.model_copy(update={"class_member_link": True})}
)
INGESTED = datetime(2019, 12, 31, 23, 0, tzinfo=UTC)


def _at(month: int, day: int, year: int = 2019) -> datetime:
    return datetime(year, month, day, 21, 0, tzinfo=UTC)


def _cover(n: int, at: datetime, *titles: tuple[str, str]) -> CoverPage:
    listings = tuple(CoverListing(title, ticker, "NYSE") for title, ticker in titles)
    return CoverPage(CIK, f"{CIK}-19-{n:06d}", at, listings)


def _source(pages: list[CoverPage], facts: list[FactRecord]) -> FixtureFilingSource:
    first = FilingIndexEntry(CIK, "Look Corp", "10-K", f"{CIK}-17-000001", _at(1, 3, 2017))
    return FixtureFilingSource(
        index=[first],
        cover_pages=pages,
        headers=[FilingHeader(CIK, p.accession, "10-K", 2080, p.accepted_at) for p in pages],
        facts=facts,
    )


def _landing(pages: list[CoverPage], facts: list[FactRecord]) -> dict[str, str | None]:
    """Per fact accession and member, the security its row lands on (`None`
    when unmatched), the link on."""
    *_, rows, unmatched = _build_filings(_source(pages, facts), LINKED, INGESTED)
    out: dict[str, str | None] = {
        f"{r['filing_accession']}/{r['class_member']}": r["security_id"] for r in rows
    }
    out.update({f"{u.accession}/{u.class_member}": None for u in unmatched})
    return out


def _share(member: str, value: float, page: CoverPage) -> FactRecord:
    return FactRecord(
        CIK,
        "EntityCommonStockSharesOutstanding",
        page.accepted_at.date(),
        member,
        value,
        page.accession,
        page.accepted_at,
    )


def _assert_truncation_invariant(pages: list[CoverPage], facts: list[FactRecord]) -> None:
    full = _landing(pages, facts)
    for fact in facts:
        t = fact.accepted_at
        cut = _landing([p for p in pages if p.accepted_at <= t], [fact])
        key = f"{fact.accession}/{fact.class_member}"
        assert full[key] == cut[key], (key, t)


def test_a_class_title_first_known_after_the_acceptance_links_nothing() -> None:
    # The nonvoting title arrives on a later cover page (same pair, retitled):
    # a `NonvotingCommonStock` fact before it stays unmatched, one after lands.
    voting = ("Class A Common Stock (voting)", "LKA")
    pages = [
        _cover(1, _at(1, 4), voting, ("Class B Common Stock", "LKB")),
        _cover(2, _at(3, 1), voting, ("Class B Common Stock", "LKB")),
        _cover(3, _at(5, 1), voting, ("Class B Common Stock (nonvoting)", "LKB")),
    ]
    early = _share("NonvotingCommonStock", 300.0, pages[1])
    late = _share("NonvotingCommonStock", 301.0, pages[2])
    got = _landing(pages, [early, late])
    assert got[f"{early.accession}/NonvotingCommonStock"] is None
    assert got[f"{late.accession}/NonvotingCommonStock"] == f"{CIK}:class-b-common-stock"
    _assert_truncation_invariant(pages, [early, late])


def test_a_cover_page_known_after_the_acceptance_neither_kills_nor_revives() -> None:
    series = [("Series A Common Stock", "LKA"), ("Series B Common Stock", "LKB")]
    pages = [
        _cover(1, _at(1, 4), *series),
        *(_cover(2 + i, _at(2 + i, 4), series[0]) for i in range(4)),  # four pages without B
        _cover(6, _at(7, 1), *series),  # B shown again
    ]
    third = _share("", 10.0, pages[3])  # B missed three pages: not dead yet
    fourth = _share("", 11.0, pages[4])  # four missed: dead, the total is A's
    shown_again = _share("", 12.0, pages[5])
    got = _landing(pages, [third, fourth, shown_again])
    assert got[f"{third.accession}/"] is None  # the later misses do not kill it at t
    assert got[f"{fourth.accession}/"] == CIK  # the later page does not revive it at t
    assert got[f"{shown_again.accession}/"] is None  # its own page shows B: alive again
    _assert_truncation_invariant(pages, [third, fourth, shown_again])


def test_an_acceptance_is_compared_at_its_own_instant() -> None:
    # The fact and the page share an accession and instant: the page counts.
    series = [("Series A Common Stock", "LKA"), ("Series B Common Stock", "LKB")]
    pages = [
        _cover(1, _at(1, 4), *series),
        *(_cover(2 + i, _at(2 + i, 4), series[0]) for i in range(4)),
    ]
    fourth = _share("", 11.0, pages[4])
    a_second_early = replace(fourth, accepted_at=pages[4].accepted_at - timedelta(seconds=1))
    assert _landing(pages, [fourth])[f"{fourth.accession}/"] == CIK
    assert _landing(pages, [a_second_early])[f"{fourth.accession}/"] is None
