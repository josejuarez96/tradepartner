"""The backfill's price fetch set holds only ids a fetched bar can land on (#875).

`_window_names` used to fetch every security with a `_fetched` listing live
in the month. The price resolver drops non-equity rows (`listing_kind`) and
ends a row where the security's next row starts, so a SPAC's warrant class
typed `spac`, a bank's preferred depositary typed `depositary`, and an old
listing on another exchange that a later, delisted row superseded were
fetched every month for nothing and showed up as permanent holes.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

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
from tradepartner.backfill import _window_names, fill_holes
from tradepartner.config import Settings
from tradepartner.ingest import OK, _read
from tradepartner.repair import store_resolver

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


def _window(settings: Settings, window: tuple[date, date]) -> tuple[list[str], list[str]]:
    """The fetch set and the staleness denominator of `window` at `LATER`,
    with the store's resolver then, as `_price_chunk` builds it."""
    with _read(settings) as conn:
        resolver = store_resolver(conn, LATER, settings)
        ids, listed, *_ = _window_names(conn, LATER, window, settings, resolver)
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
