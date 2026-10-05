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
    with _read(settings) as conn:
        ids, *_ = _window_names(conn, LATER, window, settings)
    return ids


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
