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
