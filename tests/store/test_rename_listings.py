"""#3057: a name change whose new ticker no cover page starts gets a listing row."""

from __future__ import annotations

from datetime import UTC, date, datetime

from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.config import Settings
from tradepartner.store.master import (
    RENAME_EXCHANGE_DIFFERS,
    RENAME_HELD,
    RENAME_NO_EVIDENCE,
    RENAME_OLD_NOT_CURRENT,
    RenameChange,
    build_master,
    derive_rename_listings,
)

CIK = "0000031791"
NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
CHANGE_KNOWN = datetime(2023, 5, 16, 20, 0, tzinfo=UTC)
FETCH = datetime(2026, 11, 2, 23, 0, tzinfo=UTC)


def _row(ticker: str, valid_from: date, known_at: datetime, exchange: str = "NYSE") -> dict:
    return {
        "security_id": CIK,
        "ticker": ticker,
        "exchange": exchange,
        "valid_from": valid_from,
        "known_at": known_at,
    }


OLD = _row("PKI", date(2019, 8, 6), datetime(2019, 8, 6, 18, tzinfo=UTC))
CHANGE = RenameChange(CIK, "PKI", "RVTY", date(2023, 5, 16), CHANGE_KNOWN)
SEEN = CompanySnapshotEntry(CIK, "", "RVTY", "NYSE", FETCH)


def test_a_fetched_new_ticker_gets_a_row_from_the_change_known_at_the_fetch() -> None:
    rows, refused = derive_rename_listings([OLD], [SEEN], [CHANGE], ingested_at=NOW)
    assert refused == ()
    [row] = rows
    assert (row.ticker, row.exchange, row.valid_from, row.known_at) == (
        "RVTY",
        "NYSE",
        date(2023, 5, 16),
        FETCH,
    )


def test_no_look_ahead_the_row_is_not_known_before_its_evidence() -> None:
    rows, _ = derive_rename_listings([OLD], [SEEN], [CHANGE], ingested_at=NOW)
    assert rows[0].known_at >= SEEN.fetched_at
    early = datetime(2026, 11, 1, tzinfo=UTC)  # a build before the fetch sees nothing to go on
    rows, refused = derive_rename_listings([OLD], [], [CHANGE], ingested_at=early)
    assert rows == ()
    assert [r.reason for r in refused] == [RENAME_NO_EVIDENCE]


def test_a_change_not_yet_known_adds_nothing() -> None:
    late = RenameChange(CIK, "PKI", "RVTY", date(2023, 5, 16), NOW.replace(year=2027))
    assert derive_rename_listings([OLD], [SEEN], [late], ingested_at=NOW) == ((), ())


def test_a_fetch_before_the_change_was_known_is_no_evidence() -> None:
    stale = CompanySnapshotEntry(CIK, "", "RVTY", "NYSE", datetime(2023, 1, 1, tzinfo=UTC))
    rows, refused = derive_rename_listings([OLD], [stale], [CHANGE], ingested_at=NOW)
    assert rows == ()
    assert [r.reason for r in refused] == [RENAME_NO_EVIDENCE]


def test_a_cover_page_row_already_starting_the_ticker_is_enough() -> None:
    covered = _row("RVTY", date(2023, 5, 16), datetime(2023, 5, 17, tzinfo=UTC))
    assert derive_rename_listings([OLD, covered], [SEEN], [CHANGE], ingested_at=NOW) == ((), ())


def test_a_late_cover_row_is_back_dated_to_the_change() -> None:
    # CHK -> EXE: the change is 2024-10-02, the first cover page naming EXE 2026-04-28.
    old = _row("CHK", date(2021, 3, 1), datetime(2021, 3, 1, tzinfo=UTC), "NASDAQ")
    late = _row("EXE", date(2026, 4, 28), datetime(2026, 4, 28, 20, tzinfo=UTC), "NASDAQ")
    change = RenameChange(
        CIK, "CHK", "EXE", date(2024, 10, 2), datetime(2024, 10, 2, 20, tzinfo=UTC)
    )
    rows, refused = derive_rename_listings([old, late], [], [change], ingested_at=NOW)
    assert refused == ()
    [row] = rows
    assert (row.ticker, row.valid_from, row.known_at) == (
        "EXE",
        date(2024, 10, 2),
        late["known_at"],
    )


def test_the_old_ticker_must_be_the_one_in_force() -> None:
    other = _row("ZZZ", date(2021, 1, 4), datetime(2021, 1, 4, tzinfo=UTC))
    rows, refused = derive_rename_listings([OLD, other], [SEEN], [CHANGE], ingested_at=NOW)
    assert rows == ()
    assert [r.reason for r in refused] == [RENAME_OLD_NOT_CURRENT]


def test_evidence_on_another_exchange_is_refused() -> None:
    other = CompanySnapshotEntry(CIK, "", "RVTY", "NASDAQ", FETCH)
    rows, refused = derive_rename_listings([OLD], [other], [CHANGE], ingested_at=NOW)
    assert rows == ()
    assert [r.reason for r in refused] == [RENAME_EXCHANGE_DIFFERS]


def test_a_ticker_another_security_held_is_refused() -> None:
    held = RenameChange(
        CIK, "PKI", "RVTY", date(2023, 5, 16), CHANGE_KNOWN, held_by=(("0000000001", "NYSE"),)
    )
    rows, refused = derive_rename_listings([OLD], [SEEN], [held], ingested_at=NOW)
    assert rows == ()
    assert [r.reason for r in refused] == [RENAME_HELD]


def test_a_rerun_derives_the_same_row() -> None:
    once = derive_rename_listings([OLD], [SEEN], [CHANGE], ingested_at=NOW)
    again = derive_rename_listings([OLD], [SEEN, SEEN], [CHANGE, CHANGE], ingested_at=NOW)
    assert once == again


def test_build_master_adds_the_row_beside_a_cover_page_class() -> None:
    first = datetime(2005, 3, 1, 20, 30, tzinfo=UTC)
    source = FixtureFilingSource(
        index=[FilingIndexEntry(CIK, "Perkin", "10-K", f"{CIK}-000001", first)],
        cover_pages=[
            CoverPage(
                CIK,
                f"{CIK}-000002",
                datetime(2019, 8, 6, 18, tzinfo=UTC),
                (CoverListing("Common Stock", "PKI", "NYSE"),),
            )
        ],
        snapshot=[CompanySnapshotEntry(CIK, "PERKIN", "RVTY", "NYSE", FETCH)],
    )
    build = build_master(source, Settings(_env_file=None), ingested_at=NOW, renames=[CHANGE])
    rows = sorted(
        (r["ticker"], r["valid_from"], r["provenance"])
        for r in build.listings
        if r["security_id"] == CIK
    )
    assert ("RVTY", date(2023, 5, 16), "snapshot") in rows
    assert ("PKI", date(2019, 8, 6), "filing") in rows
    assert build.rename_refusals == ()
    without = build_master(source, Settings(_env_file=None), ingested_at=NOW)
    assert "RVTY" not in {r["ticker"] for r in without.listings if r["security_id"] == CIK}
