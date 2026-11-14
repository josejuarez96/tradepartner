"""A tracking-stock group split off into a new issuer (#3363, #3288 G6).

Braves shape: the old issuer's Form 25 ended its tracking classes, then its
next cover page listed the same tickers again (with an 8-A12B in the window),
which the #820 rule made repair successors although the new issuer already
held the tickers. Liberty Live shape: the old issuer's cover pages simply
stopped listing the class (no Form 25) while the new issuer lists the tickers,
so the old class's span never ended.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from tradepartner.adapters.alpaca_prices import ListingResolver, is_placeholder_ticker
from tradepartner.adapters.filings import CoverListing, CoverPage, DelistingFiling, FilingIndexEntry
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.config import Settings
from tradepartner.store.master import build_master

INGESTED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
OLD = "0000900100"  # the old issuer (Liberty Media)
NEW = "0000900200"  # the issuer the group was split off into (Atlanta Braves Holdings)
_n = iter(range(1, 1_000_000))


def _at(year: int, month: int, day: int, hour: int = 20) -> datetime:
    return datetime(year, month, day, hour, 30, tzinfo=UTC)


def _filing(cik: str, name: str, form: str, at: datetime) -> FilingIndexEntry:
    return FilingIndexEntry(cik, name, form, f"{cik}-{next(_n):06d}", at)


def _cover(cik: str, at: datetime, *listings: tuple[str, str, str]) -> CoverPage:
    return CoverPage(
        cik,
        f"{cik}-{next(_n):06d}",
        at,
        tuple(CoverListing(title, ticker, exchange) for title, ticker, exchange in listings),
    )


def _settings() -> Settings:
    return Settings(_env_file=None)


A = ("Series A Braves Common Stock", "BATRA", "Nasdaq")
C = ("Series C Braves Common Stock", "BATRK", "Nasdaq")
OTHER = ("Series A Media Common Stock", "FWONA", "Nasdaq")


def _braves(*, stale_old_page: bool = True, ticker_reuse: bool = False) -> FixtureFilingSource:
    """Old issuer's Form 25 (effective 2023-07-28) ends the Braves classes; the
    new issuer lists them from 2023-07-28 (A) and 2023-08-04 (C); the old
    issuer's 2023-08-04 page lists them again beside an 8-A12B."""
    relist_at = _at(2023, 8, 4)
    new_pages = [
        _cover(NEW, _at(2023, 7, 28), A),
        _cover(NEW, _at(2023, 8, 4), A, C),
    ]
    if ticker_reuse:  # the new issuer only takes the ticker after the old page
        new_pages = [_cover(NEW, _at(2023, 9, 1), A, C)]
    return FixtureFilingSource(
        index=[
            _filing(OLD, "Liberty Media", "10-K", _at(2019, 3, 1)),
            _filing(OLD, "Liberty Media", "8-A12B", _at(2023, 7, 20)),
            _filing(NEW, "Atlanta Braves Holdings", "10-12B", _at(2023, 6, 1)),
        ],
        cover_pages=[
            _cover(OLD, _at(2019, 8, 8), A, C, OTHER),
            _cover(OLD, _at(2023, 5, 5), A, C, OTHER),
            *([_cover(OLD, relist_at, A, C, OTHER)] if stale_old_page else []),
            _cover(OLD, _at(2023, 11, 3), OTHER),
            *new_pages,
        ],
        delistings=[
            DelistingFiling(OLD, "25", A[0], "Nasdaq", "25-a", _at(2023, 7, 20), date(2023, 7, 28)),
            DelistingFiling(OLD, "25", C[0], "Nasdaq", "25-c", _at(2023, 7, 20), date(2023, 7, 28)),
        ],
    )


def test_the_old_issuers_relisting_of_a_ticker_the_new_issuer_holds_is_no_successor() -> None:
    build = build_master(_braves(), _settings(), ingested_at=INGESTED_AT)
    assert build.successions == ()
    assert not [r for r in build.securities if "@" in r["security_id"]]
    assert not [r for r in build.listings if "@" in r["security_id"]]
    resolver = ListingResolver(build.listings)
    assert resolver.resolve("BATRA", date(2023, 8, 10)) == NEW
    assert resolver.resolve("BATRK", date(2023, 8, 10)) == f"{NEW}:series-c-braves-common-stock"
    # The old class still ends at its own Form 25's listing, untouched.
    assert resolver.resolve("BATRA", date(2023, 5, 8)) == OLD


def test_the_old_issuers_other_classes_keep_their_rows() -> None:
    build = build_master(_braves(), _settings(), ingested_at=INGESTED_AT)
    fwon = [r for r in build.listings if r["ticker"] == "FWONA"]
    assert len(fwon) == 1


def test_a_ticker_the_new_issuer_takes_only_after_the_relisting_is_not_dropped() -> None:
    # No look-ahead: the new issuer's row starts after the old page, so on that
    # page the successor was the only holder and stays.
    build = build_master(_braves(ticker_reuse=True), _settings(), ingested_at=INGESTED_AT)
    assert {s.security_id for s in build.successions} == {
        f"{OLD}@2023-08-04",
        f"{OLD}@2023-08-04-2",
    }


LIVE_A = ("Series A Live Common Stock", "LLYVA", "Nasdaq")
LIVE_C = ("Series C Live Common Stock", "LLYVK", "Nasdaq")
HOLD = "0000900300"  # Liberty Live Holdings


def _live(*, new_pages: bool = True, old_pages_after: bool = True) -> FixtureFilingSource:
    pages = [
        _cover(OLD, _at(2023, 11, 3), OTHER, LIVE_A, LIVE_C),
        _cover(OLD, _at(2025, 11, 10), OTHER, LIVE_A, LIVE_C),
    ]
    if old_pages_after:  # the old issuer files again and no longer lists the class
        pages.append(_cover(OLD, _at(2026, 2, 27), OTHER))
    if new_pages:
        pages.append(_cover(HOLD, _at(2026, 2, 26), LIVE_A, LIVE_C))
    return FixtureFilingSource(
        index=[
            _filing(OLD, "Liberty Media", "10-K", _at(2019, 3, 1)),
            _filing(HOLD, "Liberty Live Holdings", "10-K", _at(2026, 2, 26)),
        ],
        cover_pages=pages,
        delistings=[],
    )


def test_a_class_the_old_issuer_stops_listing_ends_where_the_new_issuer_lists_it() -> None:
    build = build_master(_live(), _settings(), ingested_at=INGESTED_AT)
    ends = [r for r in build.listings if is_placeholder_ticker(str(r["ticker"]))]
    assert {r["security_id"] for r in ends} == {
        f"{OLD}:series-a-live-common-stock",
        f"{OLD}:series-c-live-common-stock",
    }
    assert {r["valid_from"] for r in ends} == {date(2026, 2, 26)}
    # Known when both facts are: the new issuer's page and the old issuer's
    # first page without the class.
    assert {r["known_at"] for r in ends} == {_at(2026, 2, 27)}
    resolver = ListingResolver(build.listings)
    assert resolver.resolve("LLYVA", date(2026, 2, 20)) == f"{OLD}:series-a-live-common-stock"
    assert resolver.resolve("LLYVA", date(2026, 3, 2)) == HOLD


def test_no_end_before_the_old_issuer_has_filed_without_the_class() -> None:
    # Only the new issuer's page is known: the old issuer may still list it.
    build = build_master(_live(old_pages_after=False), _settings(), ingested_at=INGESTED_AT)
    assert not [r for r in build.listings if is_placeholder_ticker(str(r["ticker"]))]


def test_no_end_when_no_other_issuer_lists_the_ticker() -> None:
    build = build_master(_live(new_pages=False), _settings(), ingested_at=INGESTED_AT)
    assert not [r for r in build.listings if is_placeholder_ticker(str(r["ticker"]))]


def test_a_class_with_a_form_25_is_left_to_it() -> None:
    source = _live()
    source = FixtureFilingSource(
        index=list(source.filing_index()),
        cover_pages=[p for cik in (OLD, HOLD) for p in source.cover_pages(cik)],
        delistings=[
            DelistingFiling(
                OLD, "25", LIVE_A[0], "Nasdaq", "25-live", _at(2026, 2, 1), date(2026, 2, 11)
            )
        ],
    )
    build = build_master(source, _settings(), ingested_at=INGESTED_AT)
    assert not [
        r
        for r in build.listings
        if is_placeholder_ticker(str(r["ticker"]))
        and r["security_id"].endswith("a-live-common-stock")
    ]


def test_an_unrelated_holder_years_earlier_is_no_split_off() -> None:
    other = "0000900400"
    source = FixtureFilingSource(
        index=[
            _filing(other, "Old Co", "10-K", _at(2005, 3, 1)),
            _filing(OLD, "Zed Co", "10-K", _at(2016, 3, 1)),
            _filing(OLD, "Zed Co", "8-A12B", _at(2020, 5, 29)),
        ],
        cover_pages=[
            _cover(other, _at(2005, 4, 1), ("Common Stock", "ZZZ", "Nasdaq")),
            _cover(OLD, _at(2016, 4, 1), ("Common Stock", "ZZZ", "Nasdaq")),
            _cover(OLD, _at(2023, 4, 1), ("Common Stock", "ZZZ", "Nasdaq")),
        ],
        delistings=[
            DelistingFiling(
                OLD, "25-NSE", "Common Stock", "Nasdaq", "25-z", _at(2020, 6, 1), date(2020, 6, 11)
            )
        ],
    )
    build = build_master(source, _settings(), ingested_at=INGESTED_AT)
    assert len(build.successions) == 1


def test_a_row_known_later_does_not_drop_an_earlier_known_one() -> None:
    # The new issuer's pair starts first but is only known after the old page.
    source = _braves()
    pages = [p for cik in (OLD, NEW) for p in source.cover_pages(cik) if cik != NEW]
    pages += [_cover(NEW, _at(2023, 8, 4, 23), A, C)]  # same session, known 2.5 h later
    late = FixtureFilingSource(
        index=list(source.filing_index()), cover_pages=pages, delistings=source.delistings()
    )
    build = build_master(late, _settings(), ingested_at=INGESTED_AT)
    # Same start session, but known after the old page: the row stays.
    assert build.successions


def test_a_form_25_of_another_class_does_not_block_the_end() -> None:
    old = _braves(stale_old_page=False)
    live = _live()
    source = FixtureFilingSource(
        index=[*old.filing_index(), *live.filing_index()],
        cover_pages=[*live.cover_pages(OLD), *old.cover_pages(OLD)[:2], *live.cover_pages(HOLD)],
        delistings=old.delistings(),
    )
    build = build_master(source, _settings(), ingested_at=INGESTED_AT)
    ends = [r for r in build.listings if is_placeholder_ticker(str(r["ticker"]))]
    assert len(ends) == 2
