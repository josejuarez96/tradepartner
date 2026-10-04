"""Tests for the `FilingSource` records and the fixture adapter (T8)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    DelistingFiling,
    FactRecord,
    FilingHeader,
    FilingIndexEntry,
    FilingSource,
    StatementFactRecord,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource

CIK = "0000320193"
OTHER = "0000789019"
T1 = datetime(2019, 3, 1, 20, 30, tzinfo=UTC)
T2 = datetime(2020, 3, 2, 20, 30, tzinfo=UTC)


def _source() -> FixtureFilingSource:
    return FixtureFilingSource(
        index=[
            FilingIndexEntry(CIK, "Apple Inc", "10-K", "a2", T2),
            FilingIndexEntry(CIK, "Apple Inc", "10-K", "a1", T1),
        ],
        snapshot=[CompanySnapshotEntry(CIK, "APPLE INC", "AAPL", "NASDAQ", T2)],
        facts=[
            FactRecord(CIK, "shares", date(2019, 2, 1), "", 1.0, "a1", T1),
            FactRecord(CIK, "other", date(2019, 2, 1), "", 2.0, "a1", T1),
        ],
        headers=[
            FilingHeader(CIK, "a1", "10-K", 3571, T1),
            FilingHeader(CIK, "a3", "8-K", 3571, T2),
        ],
        cover_pages=[
            CoverPage(CIK, "a1", T1, (CoverListing("Common Stock", "AAPL", "NASDAQ"),)),
            CoverPage(OTHER, "b1", T1, (CoverListing("Common Stock", "MSFT", "NASDAQ"),)),
        ],
        delistings=[DelistingFiling(CIK, "25", "Notes", "NYSE", "d1", T2)],
    )


def test_filing_source_is_abstract() -> None:
    with pytest.raises(TypeError):
        FilingSource()  # type: ignore[abstract]


def test_naive_timestamp_raises() -> None:
    with pytest.raises(ValueError):
        FilingIndexEntry(CIK, "Apple Inc", "10-K", "a1", datetime(2019, 3, 1))  # noqa: DTZ001


def test_date_instead_of_datetime_raises() -> None:
    with pytest.raises(TypeError):
        CompanySnapshotEntry(CIK, "x", "X", "NYSE", date(2019, 3, 1))  # type: ignore[arg-type]


@pytest.mark.parametrize("cik", ["320193", "CIK0000320193", 320193, "00003201930"])
def test_cik_must_be_ten_digits(cik: object) -> None:
    with pytest.raises(ValueError):
        FilingIndexEntry(cik, "Apple Inc", "10-K", "a1", T1)  # type: ignore[arg-type]


def test_timestamp_normalized_to_utc() -> None:
    eastern = datetime(2019, 3, 1, 15, 30, tzinfo=timezone(timedelta(hours=-5)))
    entry = FilingIndexEntry(CIK, "Apple Inc", "10-K", "a1", eastern)
    assert entry.accepted_at == T1
    assert entry.accepted_at.tzinfo == UTC


def test_index_sorted_and_since_filters() -> None:
    source = _source()
    assert [e.accession for e in source.filing_index()] == ["a1", "a2"]
    assert [e.accession for e in source.filing_index(since=T2)] == ["a2"]


def test_per_cik_filters() -> None:
    source = _source()
    assert [f.fact_name for f in source.facts(CIK, ["shares"])] == ["shares"]
    assert [h.form for h in source.filing_headers(CIK, ["10-K"])] == ["10-K"]
    assert [p.accession for p in source.cover_pages(CIK)] == ["a1"]
    assert source.delistings(since=T2 + timedelta(seconds=1)) == []


def test_known_by_keeps_only_records_knowable_at_t() -> None:
    early = _source().known_by(T1)
    assert [e.accession for e in early.filing_index()] == ["a1"]
    assert early.companies_snapshot() == []
    assert early.delistings() == []
    assert [h.accession for h in early.filing_headers(CIK, ["10-K", "8-K"])] == ["a1"]
    assert _source().known_ats() == [T1, T2]


def test_known_by_raises_rather_than_silently_dropping_statement_facts() -> None:
    """`known_by` has no as-of filter for statement facts yet (T76b's job);
    a source carrying any must raise there rather than return a copy whose
    `statement_facts(cik)` silently answers `[]` for every `t`."""
    record = StatementFactRecord(
        CIK,
        "revenue",
        "us-gaap:Revenues",
        date(2019, 1, 1),
        date(2019, 12, 31),
        1.0,
        "USD",
        "10-K",
        "a1",
        T1,
        date(2019, 2, 14),
        False,
    )
    source = FixtureFilingSource(statement_facts=[record])
    with pytest.raises(NotImplementedError, match="statement_facts"):
        source.known_by(T2)


# --- StatementFactRecord and FixtureFilingSource.statement_facts (#660, T76) ---


def test_statement_fact_period_days_is_computed_not_stored() -> None:
    """`period_days` is a property (`(period_end - period_start).days`, or
    `0` for an instant), never a constructor argument -- the spec's
    `StatementFactRecord` field list has no such field (interfaces)."""
    duration = StatementFactRecord(
        CIK,
        "revenue",
        "us-gaap:Revenues",
        date(2019, 1, 1),
        date(2019, 12, 31),
        1.0,
        "USD",
        "10-K",
        "a1",
        T1,
        date(2019, 2, 14),
        False,
    )
    assert duration.period_days == 364
    instant = StatementFactRecord(
        CIK,
        "total_assets",
        "us-gaap:Assets",
        None,
        date(2019, 12, 31),
        1.0,
        "USD",
        "10-K",
        "a1",
        T1,
        date(2019, 2, 14),
        False,
    )
    assert instant.period_days == 0


def test_statement_fact_period_end_must_be_after_period_start() -> None:
    with pytest.raises(ValueError, match="period_end"):
        StatementFactRecord(
            CIK,
            "revenue",
            "us-gaap:Revenues",
            date(2019, 1, 1),
            date(2019, 1, 1),
            1.0,
            "USD",
            "10-K",
            "a1",
            T1,
            date(2019, 2, 14),
            False,
        )


def test_statement_fact_accepted_at_none_for_an_unstamped_accession() -> None:
    """An accession with no stamp record yet: `accepted_at=None` is valid
    (unlike every other record's `accepted_at`/`fetched_at`, which must be
    tz-aware), and `filed` -- the companyfacts date the ingest's hold rule
    reads -- is required regardless."""
    record = StatementFactRecord(
        CIK,
        "revenue",
        "us-gaap:Revenues",
        date(2019, 1, 1),
        date(2019, 12, 31),
        1.0,
        "USD",
        "10-K",
        "a1",
        None,
        date(2019, 2, 14),
        False,
    )
    assert record.accepted_at is None
    assert record.filed == date(2019, 2, 14)


def test_statement_fact_accepted_at_naive_still_raises() -> None:
    """`accepted_at` is tz-checked only when it is *not* None -- a naive
    value is still refused, never silently accepted like the None case."""
    with pytest.raises(ValueError, match="tz-aware"):
        StatementFactRecord(
            CIK,
            "revenue",
            "us-gaap:Revenues",
            date(2019, 1, 1),
            date(2019, 12, 31),
            1.0,
            "USD",
            "10-K",
            "a1",
            datetime(2019, 2, 14),  # noqa: DTZ001
            date(2019, 2, 14),
            False,
        )


def test_statement_fact_filed_the_day_after_acceptance_is_served_by_accepted_at_alone() -> None:
    """A fixture entry whose companyfacts `filed` date differs from its
    acceptance date (an after-hours filing): the fixture adapter serves it
    keyed by `cik` only, with both fields intact, so a future ingest's
    hold rule (the only reader of `filed`) sees exactly what the record
    carries rather than something the adapter derived or dropped.
    `statement_facts` itself has no `filed` column at all (spec
    "Statement facts" > Interfaces; `tests/store/test_schema.py` checks
    the table's column set), so this record-level field is as far as
    `filed` travels in this PR."""
    accepted_at = datetime(2019, 2, 13, 23, 55, tzinfo=UTC)
    filed_the_next_day = date(2019, 2, 14)
    record = StatementFactRecord(
        CIK,
        "revenue",
        "us-gaap:Revenues",
        date(2019, 1, 1),
        date(2019, 12, 31),
        1.0,
        "USD",
        "10-K",
        "a1",
        accepted_at,
        filed_the_next_day,
        False,
    )
    (served,) = FixtureFilingSource(statement_facts=[record]).statement_facts(CIK)
    assert served.accepted_at == accepted_at
    assert served.filed == filed_the_next_day


def test_statement_facts_served_per_cik() -> None:
    other_cik = StatementFactRecord(
        OTHER,
        "revenue",
        "us-gaap:Revenues",
        date(2019, 1, 1),
        date(2019, 12, 31),
        2.0,
        "USD",
        "10-K",
        "b1",
        T1,
        date(2019, 2, 14),
        False,
    )
    this_cik = StatementFactRecord(
        CIK,
        "revenue",
        "us-gaap:Revenues",
        date(2019, 1, 1),
        date(2019, 12, 31),
        1.0,
        "USD",
        "10-K",
        "a1",
        T2,
        date(2019, 2, 14),
        False,
    )
    source = FixtureFilingSource(statement_facts=[other_cik, this_cik])
    assert source.statement_facts(CIK) == [this_cik]
    assert source.statement_facts(OTHER) == [other_cik]


def test_statement_facts_sorted_with_unstamped_entries_first() -> None:
    """An unstamped entry (`accepted_at=None`) sorts deterministically
    rather than raising on a `None`/`datetime` comparison."""
    unstamped = StatementFactRecord(
        CIK,
        "revenue",
        "us-gaap:Revenues",
        date(2019, 1, 1),
        date(2019, 12, 31),
        1.0,
        "USD",
        "10-K",
        "a1",
        None,
        date(2019, 2, 14),
        False,
    )
    stamped = StatementFactRecord(
        CIK,
        "total_assets",
        "us-gaap:Assets",
        None,
        date(2019, 12, 31),
        2.0,
        "USD",
        "10-K",
        "a2",
        T1,
        date(2019, 2, 14),
        False,
    )
    source = FixtureFilingSource(statement_facts=[stamped, unstamped])
    assert source.statement_facts(CIK) == [unstamped, stamped]
