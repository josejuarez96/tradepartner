"""The backfill's price fetch set holds only ids a fetched bar can land on (#875).

`_window_names` used to fetch every security with a `_fetched` listing live
in the month. The price resolver drops non-equity rows (`listing_kind`) and
ends a row where the security's next row starts, so a SPAC's warrant class
typed `spac`, a bank's preferred depositary typed `depositary`, and an old
listing on another exchange that a later, delisted row superseded were
fetched every month for nothing and showed up as permanent holes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import duckdb
import pytest
from test_backfill import (
    JUNE_WINDOW,
    LATER,
    MAY_WINDOW,
    SINCE,
    _backfill,
    _History,
    _sessions,
)
from test_ingest import ACME, SPY, _at, _fact, _filings, _loose

from tradepartner.adapters.filings import (
    CoverListing,
    CoverPage,
    DelistingFiling,
    FilingHeader,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.backfill import _price_chunk, _window_names, fill_holes
from tradepartner.config import Settings
from tradepartner.ingest import OK, _read
from tradepartner.repair import store_resolver
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

SPAC = "0000000005"  # SIC 6770: every class id of it is typed spac
SPAC_WARRANTS = f"{SPAC}:redeemable-warrants"
BANK = "0000000009"  # its preferred depositary class is typed depositary
BANK_DEPOSITARY = f"{BANK}:depositary-shares"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def _with_non_equity(**kwargs: object) -> FixtureFilingSource:
    """`_filings` plus a SPAC with a common and a warrant class, and a bank
    with a common class and a preferred depositary share."""
    companies = [
        (
            SPAC,
            6770,
            5,
            (
                CoverListing("Class A Common Stock", "SPC", "NASDAQ"),
                CoverListing("Redeemable Warrants", "SPCW", "NASDAQ"),
            ),
        ),
        (
            BANK,
            6022,
            6,
            (
                CoverListing("Common Stock", "BNK", "NYSE"),
                CoverListing(
                    "Depositary Shares, each representing a 1/40th interest in a share of Series A",
                    "BNK PRA",
                    "NYSE",
                ),
            ),
        ),
    ]
    index, headers, covers, facts = [], [], [], []
    for cik, sic, day, listings in companies:
        accession = f"{cik}-19-000001"
        accepted = _at(2019, 3, day)
        index.append(FilingIndexEntry(cik, f"Co {cik}", "10-K", accession, accepted))
        headers.append(FilingHeader(cik, accession, "10-K", sic, accepted))
        covers.append(CoverPage(cik, accession, accepted, listings))
        facts.append(_fact(cik, "", 1_000_000, accession, accepted))
    return _filings(
        extra_index=index,
        extra_headers=headers,
        extra_covers=covers,
        extra_facts=facts,
        **kwargs,  # type: ignore[arg-type]
    )


def _fetch_set(settings: Settings, window: tuple[date, date]) -> list[str]:
    return _window(settings, window)[0]


def _window(
    settings: Settings, window: tuple[date, date], at: datetime = LATER
) -> tuple[list[str], list[str]]:
    """The fetch set and the staleness denominator of `window` at `at`,
    with the store's resolver then, as `_price_chunk` builds it."""
    with _read(settings) as conn:
        resolver = store_resolver(conn, at, settings)
        ids, listed, *_ = _window_names(conn, at, window, settings, resolver)
    return ids, listed


def test_non_equity_rows_typed_spac_or_depositary_are_not_fetched(settings: Settings) -> None:
    prices = _History()
    assert _backfill(settings, prices, filings=_with_non_equity()).ok
    for fetched in prices.fetched.values():
        assert {SPAC, BANK, ACME, SPY} <= fetched  # their common classes still are
        assert not {SPAC_WARRANTS, BANK_DEPOSITARY} & fetched
    assert not {SPAC_WARRANTS, BANK_DEPOSITARY} & set(_fetch_set(settings, MAY_WINDOW))
    found = fill_holes(settings, prices=_History(), since=SINCE, clock=lambda: LATER, dry_run=True)
    assert found.holes == ()  # never fetched, never a hole


def test_a_row_superseded_before_the_month_is_not_fetched(settings: Settings) -> None:
    # ACME moves from NYSE to NASDAQ on 2019-04-15 and a Form 25 delists
    # the NASDAQ row on 2019-05-16. The older NYSE row has no end of its
    # own: it used to keep ACME fetched (and a hole) for every later month.
    # It still keeps ACME in June's staleness denominator (#875 leaves that
    # alone), so 1 of 4 counted names is missing: loosen the share.
    settings = _loose(settings)
    moved = CoverPage(
        ACME,
        f"{ACME}-19-000020",
        _at(2019, 4, 15),
        (CoverListing("Common Stock", "ACME", "NASDAQ"),),
    )
    form_25 = DelistingFiling(
        ACME,
        "25",
        "Common Stock",
        "NASDAQ",
        f"{ACME}-19-000025",
        _at(2019, 5, 6),
        date(2019, 5, 16),
    )
    gone = {(ACME, s) for s in _sessions(date(2019, 5, 17), JUNE_WINDOW[1])}
    prices = _History(gaps=gone)
    result = _backfill(
        settings, prices, filings=_filings(extra_covers=[moved], delistings=[form_25])
    )
    assert [run.status for run in result.runs] == [OK] * 4, result.runs
    assert ACME in prices.fetched[date(2019, 4, 1)]  # both rows: superseded mid-month
    assert ACME in prices.fetched[date(2019, 5, 1)]  # the NASDAQ row runs to its end
    assert ACME not in prices.fetched[date(2019, 6, 1)]
    assert ACME not in _fetch_set(settings, JUNE_WINDOW)
    found = fill_holes(
        settings,
        prices=_History(),
        since=SINCE,
        clock=lambda: LATER + timedelta(hours=1),
        dry_run=True,
    )
    assert found.holes == ()


# --- the first-span lead's months (#974) --------------------------------------

LED = "0000000021"  # first filing 2016-03-01, first cover page naming LEDX 2019-03-15
REUSED = "0000000022"  # first span OLDT, which OLDCO listed from 2017-01-05
OLDCO = "0000000023"
OTC_LED = "0000000024"  # its first span is on OTC
JUNE_2017 = (date(2017, 6, 1), date(2017, 6, 30))


def _with_first_spans(**kwargs: object) -> FixtureFilingSource:
    """`_filings` plus companies whose first ticker-bearing cover page
    (2019-03-15) is years after their first filing (2016): LED leads LEDX
    back to 2016; REUSED's OLDT is refused (OLDCO lists OLDT from 2017);
    OTC_LED's first span is on OTC."""
    index, headers, covers = [], [], []
    for cik, ticker, exchange, first in (
        (LED, "LEDX", "NYSE", _at(2016, 3, 1)),
        (REUSED, "OLDT", "NYSE", _at(2016, 3, 2)),
        (OTC_LED, "OTCX", "OTC", _at(2016, 3, 3)),
    ):
        index.append(FilingIndexEntry(cik, f"Co {cik}", "10-K", f"{cik}-16-000001", first))
        accession = f"{cik}-19-000001"
        cover = _at(2019, 3, 15)
        index.append(FilingIndexEntry(cik, f"Co {cik}", "10-K", accession, cover))
        headers.append(FilingHeader(cik, accession, "10-K", 3571, cover))
        covers.append(
            CoverPage(cik, accession, cover, (CoverListing("Common Stock", ticker, exchange),))
        )
    old = f"{OLDCO}-17-000001"
    index.append(FilingIndexEntry(OLDCO, "Old Co", "10-K", old, _at(2017, 1, 5)))
    headers.append(FilingHeader(OLDCO, old, "10-K", 3571, _at(2017, 1, 5)))
    covers.append(
        CoverPage(OLDCO, old, _at(2017, 1, 5), (CoverListing("Common Stock", "OLDT", "NYSE"),))
    )
    return _filings(
        extra_index=index,
        extra_headers=headers,
        extra_covers=covers,
        **kwargs,  # type: ignore[arg-type]
    )


def _first_span_lead_off(settings: Settings) -> Settings:
    alpaca = settings.alpaca.model_copy(update={"first_span_lead": False})
    return settings.model_copy(update={"alpaca": alpaca})


def test_a_month_inside_a_first_span_lead_fetches_the_led_id(settings: Settings) -> None:
    assert _backfill(settings, _History(), filings=_with_first_spans()).ok
    ids, listed = _window(settings, JUNE_2017)
    assert LED in ids
    assert REUSED not in ids  # refused: OLDCO's OLDT covers the window
    assert OTC_LED not in ids  # an OTC first span is never fetched
    assert LED not in listed  # no listing of it known in the month: never counted
    with _read(settings) as conn:
        resolver = store_resolver(conn, LATER, settings)
    assert resolver.lead("OLDT", date(2016, 6, 1)) is None
    assert resolver.lead("OTCX", date(2017, 6, 1)) == OTC_LED  # led, but on OTC
    assert resolver.report.first_span_refused == 1


def test_the_switch_off_fetches_no_led_id(settings: Settings) -> None:
    assert _backfill(settings, _History(), filings=_with_first_spans()).ok
    ids, _ = _window(_first_span_lead_off(settings), JUNE_2017)
    assert not {LED, REUSED, OTC_LED} & set(ids)


def test_a_led_id_is_fetched_only_once_its_cover_page_is_known(settings: Settings) -> None:
    # quant-auditor on #989: the fetch set reads the store at the clock. Before
    # the cover page naming LEDX is known there is no span, so no lead.
    assert _backfill(settings, _History(), filings=_with_first_spans()).ok
    before, _ = _window(settings, JUNE_2017, at=_at(2019, 3, 14))
    assert LED not in before
    after, _ = _window(settings, JUNE_2017, at=_at(2019, 3, 16))
    assert LED in after


UNREADABLE_LED = "0000000025"  # first filing 2016; first span's ticker reads as junk (#844)


def _with_unreadable_first_span(**kwargs: object) -> FixtureFilingSource:
    """`_filings` plus a company whose first span's only row has an
    unreadable ticker (#844: `alpaca_prices._unreadable`) -- kept "as
    written" since it is the security's first row (no earlier readable
    ticker to read it as, `_clean_rows`'s docstring), so the resolver
    still records a `FirstSpanLead` for it, under that unreadable
    string."""
    cik = UNREADABLE_LED
    accession = f"{cik}-19-000001"
    cover = _at(2019, 3, 15)
    index = [
        FilingIndexEntry(cik, f"Co {cik}", "10-K", f"{cik}-16-000001", _at(2016, 3, 1)),
        FilingIndexEntry(cik, f"Co {cik}", "10-K", accession, cover),
    ]
    headers = [FilingHeader(cik, accession, "10-K", 3571, cover)]
    covers = [CoverPage(cik, accession, cover, (CoverListing("Common Stock", "F&G", "NYSE"),))]
    return _filings(
        extra_index=index,
        extra_headers=headers,
        extra_covers=covers,
        **kwargs,  # type: ignore[arg-type]
    )


def test_an_unreadable_first_span_ticker_is_never_fetched(settings: Settings) -> None:
    """quant-auditor (PR #1132): `_led` must still check the first-span
    lead's ticker is an Alpaca symbol before admitting it. An unreadable
    ticker on a security's only (first) row is kept as written, never
    dropped (#844's "as written" rule), so the resolver records a
    `FirstSpanLead` for it anyway -- the fetch must not send that
    ticker, the same guard `_assignable` applies to an ordinary span."""
    assert _backfill(settings, _History(), filings=_with_unreadable_first_span()).ok
    ids, _ = _window(settings, JUNE_2017)
    assert UNREADABLE_LED not in ids
    with _read(settings) as conn:
        resolver = store_resolver(conn, LATER, settings)
    lead = resolver.first_span_lead(UNREADABLE_LED)
    assert lead is not None and lead.ticker == "F&G"  # recorded, but unreadable


def test_price_chunk_builds_its_resolver_at_the_clock_when_the_month_starts(
    settings: Settings,
) -> None:
    """#1122 item 3 (#990.4): `_price_chunk` must read the store's
    resolver at the clock it calls when the month starts (`started`), not
    at any later `clock()` call within the same chunk (`ingested_at`, the
    write's `finished_at`, ...). The store is built directly (not via
    `backfill()`) so every row's `known_at` is under this test's control:
    the reference symbol is known well before either probe, and LEDX's
    cover page is known at `_at(2019, 3, 15)`. The fake clock returns a
    value just before that cover page on its *first* call only, and a
    value well after it on every call after that (never running out, so
    a wrong call reads "after" regardless of how many times `_price_
    chunk` calls `clock()`) -- it would wrongly fetch LED here if the
    resolver read anything but the very first call."""
    filed = _at(2016, 3, 1)
    cover = _at(2019, 3, 15)
    filing_common = {"ingested_at": filed, "source": "edgar", "provenance": "filing"}
    ref_known = _at(2010, 1, 1)
    ref_common = {"ingested_at": ref_known, "source": "edgar", "provenance": "filing"}
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(
            conn,
            "securities",
            {"security_id": LED, "cik": LED, "name": "Led Co", "known_at": filed} | filing_common,
        )
        insert_row(
            conn,
            "classifications",
            {
                "security_id": LED,
                "sic": 3571,
                "security_type": "common",
                "rule": "common_default",
                "known_at": filed,
            }
            | filing_common,
        )
        insert_row(
            conn,
            "listings",
            {
                "security_id": LED,
                "ticker": "LEDX",
                "exchange": "NYSE",
                "class_title": "Common Stock",
                "valid_from": date(2019, 3, 15),
                "known_at": cover,
                "ingested_at": cover,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        insert_row(
            conn,
            "securities",
            {"security_id": SPY, "cik": "0000884394", "name": "SPY Trust", "known_at": ref_known}
            | ref_common,
        )
        insert_row(
            conn,
            "classifications",
            {
                "security_id": SPY,
                "sic": None,
                "security_type": "common",
                "rule": "common_default",
                "known_at": ref_known,
            }
            | ref_common,
        )
        insert_row(
            conn,
            "listings",
            {
                "security_id": SPY,
                "ticker": "SPY",
                "exchange": "NYSE",
                "class_title": "Common Stock",
                "valid_from": date(2010, 1, 1),
                "known_at": ref_known,
            }
            | ref_common,
        )
    first_call = iter([_at(2019, 3, 14)])

    def clock() -> datetime:
        return next(first_call, _at(2019, 3, 16))

    calls: list[datetime] = []

    def spy(conn: duckdb.DuckDBPyConnection, at: datetime, settings: Settings) -> Any:
        calls.append(at)
        return store_resolver(conn, at, settings)

    prices = _History()
    with patch("tradepartner.backfill.store_resolver", side_effect=spy):
        chunk = _price_chunk(settings, prices, SINCE, JUNE_2017, clock)
    assert chunk is not None and chunk.status == OK, chunk
    # Pinned directly, not just inferred from an admission boundary: the
    # resolver is built at the very first clock() call ("started"), never
    # at `ingested_at` or the write's `finished_at` (both later calls).
    assert calls == [_at(2019, 3, 14)]
    assert LED not in prices.fetched[date(2017, 6, 1)]
