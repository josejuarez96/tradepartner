"""Reviewed listing rows the cover-page rules cannot derive (#3293).

The #3288 diagnosis found a security whose old line a Form 25 ends while its
bars run on: the new venue's CERT (or an 8-A12B) states the move, and no row
says so. `store/listing_reviews.csv` carries one cited row per filing;
`build_master` writes it, point in time.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from tradepartner.adapters.edgar import normalize_exchange
from tradepartner.adapters.filings import (
    CoverListing,
    CoverPage,
    DelistingFiling,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.calendar import all_sessions as _all_sessions
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.repair import misattributed, store_resolver
from tradepartner.store import schema
from tradepartner.store.asof import listings_as_of
from tradepartner.store.build_fingerprint import REVIEWED_LISTINGS_KEY, build_fingerprint
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.delistings import (
    DELISTED,
    LISTED,
    build_delistings,
    listing_ends_as_of,
    write_delistings,
)
from tradepartner.store.listing_reviews import (
    ReviewedListing,
    parse_reviews,
    reviewed_listings,
)
from tradepartner.store.master import build_master, session_of, write_master

HEADER = "security_id,ticker,exchange,class_title,valid_from,known_at,accession,reason,issue\n"
GOOD = "0000028917,DDS,TXSE,,2026-09-30,2026-09-30T20:58:51+00:00,0002054847-26-000023,why,#3293\n"
NOW = datetime(2026, 11, 10, 12, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)


def all_sessions(first: date, last: date) -> list[date]:
    return [d for d in _all_sessions() if first <= d <= last]


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)  # type: ignore[arg-type]


# --- the loader ----------------------------------------------------------


def test_good_row_parses_with_untitled_class() -> None:
    (row,) = parse_reviews(HEADER + GOOD)
    assert row.class_title is None
    assert row.exchange == "TXSE"
    assert row.known_at == _utc(2026, 9, 30, 20, 58, 51)
    assert row.key == ("0000028917", "DDS", "TXSE", date(2026, 9, 30))


@pytest.mark.parametrize(
    "bad",
    [
        GOOD.replace("0000028917", "28917"),  # not a ten-digit CIK id
        GOOD.replace(",TXSE,", ",Texas Exchange,"),  # not an exchange code
        GOOD.replace("2026-09-30,", "2026-10-03,", 1),  # a Saturday
        GOOD.replace("+00:00", ""),  # naive known_at
        GOOD.replace("+00:00", "-04:00"),  # not UTC
        GOOD.replace("0002054847-26-000023", "000023"),  # not an accession
        GOOD.replace("#3293", "3293"),  # no issue
        GOOD.replace(",why,", ",,"),  # no reason
        GOOD + GOOD,  # duplicate key
        GOOD.replace(",why", ""),  # wrong field count
    ],
)
def test_malformed_rows_are_refused(bad: str) -> None:
    with pytest.raises(ValueError, match=r"listing_reviews\.csv"):
        parse_reviews(HEADER + bad)


def test_wrong_header_is_refused() -> None:
    with pytest.raises(ValueError, match="line 1"):
        parse_reviews("security_id,ticker\n" + GOOD)


def test_packaged_file_cites_each_row_to_its_filing() -> None:
    rows = reviewed_listings()
    assert {row.ticker for row in rows} == {"DDS", "TCBI", "IBOC", "CAE", "CNL", "LINK"}
    assert {row.ticker: row.exchange for row in rows} == {
        "DDS": "TXSE",
        "TCBI": "TXSE",
        "IBOC": "TXSE",
        "CAE": "NASDAQ",
        "CNL": "NASDAQ",
        "LINK": "NASDAQ",
    }
    for row in rows:
        assert row.issue == "#3293"
        assert normalize_exchange(row.exchange) == row.exchange
        # A listing starts on the session of the filing that states it.
        assert row.valid_from == session_of(row.known_at)


def test_txse_is_a_code_only_the_reviewed_rows_use() -> None:
    """The parsers do not read the Texas Stock Exchange (a parser change needs a
    cache-version bump); `TXSE` is only the exchange tag of the reviewed rows."""
    assert normalize_exchange("Texas Stock Exchange LLC") != "TXSE"
    assert normalize_exchange("TXSE") == "TXSE"


def test_fingerprint_names_each_reviewed_entry_with_its_cik() -> None:
    entries = build_fingerprint(SETTINGS).per_name[REVIEWED_LISTINGS_KEY]
    assert entries == [row.entry for row in reviewed_listings()]
    assert any(entry.startswith("0000028917|DDS|TXSE|2026-09-30|") for entry in entries)


# --- the build -----------------------------------------------------------

CIK = "0001077428"
REVIEW = ReviewedListing(
    CIK,
    "TCBI",
    "TXSE",
    None,
    date(2026, 10, 7),
    _utc(2026, 10, 7, 21, 22, 27),
    "0002054847-26-000035",
    "test",
    "#3293",
)


def _issuer(
    cik: str,
    ticker: str,
    exchange: str,
    cover_at: datetime,
    form25: tuple[str, datetime, date] | None = None,
) -> FixtureFilingSource:
    delistings = ()
    if form25 is not None:
        accession, filed, effective = form25
        delistings = (
            DelistingFiling(cik, "25", "Common Stock", exchange, accession, filed, effective),
        )
    return FixtureFilingSource(
        index=[FilingIndexEntry(cik, "Issuer Inc", "10-K", f"{cik}-19-000001", cover_at)],
        cover_pages=[
            CoverPage(
                cik, f"{cik}-19-000001", cover_at, (CoverListing("Common Stock", ticker, exchange),)
            )
        ],
        delistings=delistings,
    )


def _tcbi() -> FixtureFilingSource:
    return _issuer(
        CIK,
        "TCBI",
        "NASDAQ",
        _utc(2019, 7, 18, 20),
        ("0001077428-26-000099", _utc(2026, 10, 7, 20), date(2026, 10, 17)),
    )


def test_row_is_a_filing_listing_known_at_the_cited_acceptance() -> None:
    built = build_master(_tcbi(), SETTINGS, ingested_at=NOW, reviewed=[REVIEW])
    (row,) = [r for r in built.listings if r["exchange"] == "TXSE"]
    assert (row["security_id"], row["ticker"], row["valid_from"]) == (
        CIK,
        "TCBI",
        date(2026, 10, 7),
    )
    assert row["known_at"] == REVIEW.known_at
    assert (row["source"], row["provenance"]) == ("edgar", "filing")
    assert row["class_title"] is None
    assert built.reviewed_skipped == ()


def test_row_is_absent_before_its_filing_was_accepted() -> None:
    before = REVIEW.known_at - timedelta(seconds=1)
    built = build_master(_tcbi(), SETTINGS, ingested_at=before, reviewed=[REVIEW])
    assert [r for r in built.listings if r["exchange"] == "TXSE"] == []
    assert built.reviewed_skipped == ((REVIEW.entry, "not_yet_known"),)
    at = build_master(_tcbi(), SETTINGS, ingested_at=REVIEW.known_at, reviewed=[REVIEW])
    assert [r["exchange"] for r in at.listings].count("TXSE") == 1


def test_no_look_ahead_in_a_stored_build() -> None:
    conn = _store()
    write_master(conn, build_master(_tcbi(), SETTINGS, ingested_at=NOW, reviewed=[REVIEW]))
    early = REVIEW.known_at - timedelta(seconds=1)
    assert listings_as_of(conn, early)["exchange"].to_list() == ["NASDAQ"]
    assert sorted(listings_as_of(conn, REVIEW.known_at)["exchange"].to_list()) == [
        "NASDAQ",
        "TXSE",
    ]


def test_row_for_a_security_the_build_lacks_is_left_out_and_counted() -> None:
    other = ReviewedListing(
        "0000000001",
        "ZZZ",
        "TXSE",
        None,
        date(2026, 10, 7),
        _utc(2026, 10, 7, 21),
        "0002054847-26-000001",
        "test",
        "#3293",
    )
    built = build_master(_tcbi(), SETTINGS, ingested_at=NOW, reviewed=[other])
    assert all(r["ticker"] != "ZZZ" for r in built.listings)
    assert built.reviewed_skipped == ((other.entry, "no_security"),)


def test_row_the_build_already_derives_is_not_written_twice() -> None:
    same = ReviewedListing(
        CIK,
        "TCBI",
        "NASDAQ",
        None,
        session_of(_utc(2019, 7, 18, 20)),
        _utc(2019, 7, 18, 20),
        "0001077428-19-000001",
        "test",
        "#3293",
    )
    built = build_master(_tcbi(), SETTINGS, ingested_at=NOW, reviewed=[same])
    assert len(built.listings) == 1


def test_default_build_reads_the_packaged_file_and_skips_what_it_lacks() -> None:
    built = build_master(_tcbi(), SETTINGS, ingested_at=NOW)
    assert [r["exchange"] for r in built.listings].count("TXSE") == 1
    assert {entry for entry, why in built.reviewed_skipped if why == "no_security"} == {
        row.entry for row in reviewed_listings() if row.security_id != CIK
    }


# --- what the row changes ------------------------------------------------


def _store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    return conn


def _bars(conn: duckdb.DuckDBPyConnection, sid: str, first: date, last: date) -> None:
    for day in all_sessions(first, last):
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": sid,
                "session": day,
                "open": 10.0,
                "high": 10.0,
                "low": 10.0,
                "close": 10.0,
                "volume": 1000,
                "known_at": session_close(day),
                "ingested_at": session_close(day),
                "source": "alpaca_sip",
                "provenance": "bar",
            },
        )


def _orphans(
    source: FixtureFilingSource, reviewed: list[ReviewedListing], first: date, last: date, sid: str
) -> tuple[list[tuple[str, date]], duckdb.DuckDBPyConnection]:
    conn = _store()
    built = build_master(source, SETTINGS, ingested_at=NOW, reviewed=reviewed)
    write_master(conn, built)
    write_delistings(conn, build_delistings(source.delistings(), built, ingested_at=NOW))
    _bars(conn, sid, first, last)
    resolver = store_resolver(conn, NOW, SETTINGS)
    keys = [(sid, day) for day in all_sessions(first, last)]
    found = misattributed(resolver, [(s, d, 1) for s, d in keys], ()).bars
    return sorted(found), conn


def test_transfer_to_the_new_venue_keeps_the_bars_and_ends_the_old_line() -> None:
    # TCBI: Nasdaq Form 25 filed 2026-10-07 (effective Saturday 10-17) while
    # the bars run on; the Texas Stock Exchange CERT is the same day.
    first, last = date(2026, 9, 28), date(2026, 11, 6)
    without, _ = _orphans(_tcbi(), [], first, last, CIK)
    assert without and without[-1] == (CIK, last)  # the Form 25 reads as a delisting
    found, conn = _orphans(_tcbi(), [REVIEW], first, last, CIK)
    assert found == []
    ends = {
        (r["exchange"]): (r["status"], r["end_session"])
        for r in listing_ends_as_of(conn, NOW, SETTINGS).iter_rows(named=True)
    }
    assert ends["TXSE"] == (LISTED, None)
    assert ends["NASDAQ"] == ("transferred", date(2026, 10, 6))


def test_relisting_on_the_same_exchange_keeps_the_old_line_to_its_last_bar() -> None:
    # LINK: a Nasdaq Form 25 in 2019, then the 2021 CERT relists it on Nasdaq.
    cik = "0000828146"
    source = _issuer(
        cik,
        "LINK",
        "NASDAQ",
        _utc(2016, 3, 1, 20),
        ("0000828146-19-000002", _utc(2019, 2, 4, 21), date(2019, 2, 14)),
    )
    review = next(row for row in reviewed_listings() if row.ticker == "LINK")
    first, last = date(2019, 1, 28), date(2021, 5, 28)
    without, _ = _orphans(source, [], first, last, cik)
    assert without and without[0][1] == date(2019, 2, 14)
    found, conn = _orphans(source, [review], first, last, cik)
    assert found == []
    ends = [
        (str(r["valid_from"]), r["status"], r["end_session"])
        for r in listing_ends_as_of(conn, NOW, SETTINGS).sort("valid_from").iter_rows(named=True)
    ]
    assert ends == [
        ("2016-03-01", DELISTED, date(2021, 3, 23)),
        ("2021-03-24", LISTED, None),
    ]
