"""#3084: Form 25 class matching for SPAC unit components and comma heads.

A SPAC's common class is listed as "Class A common stock included as part of
the units", and some issuers' cover pages put a company name or par value
before the first comma ("HD Supply Holdings, Inc. common stock, par value
..."). Rules 1-3 of `_form25_classes` matched neither, so the Form 25 ended
no class and wrote no `delistings` row. Rule 4 reads both shapes, and only
when rules 1-3 resolve nothing, so a filing resolved today keeps exactly its
classes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from tradepartner.adapters.filings import DelistingFiling
from tradepartner.store.delistings import build_delistings
from tradepartner.store.master import MasterBuild, _form25_classes, _form25_title

SPAC = {
    "common": ["Class A common stock included as part of the units"],
    "warrants": ["Redeemable warrants included as part of the units"],
    "units": [
        "Units, each consisting of one Class A common share, $0.0001 par value, "
        "and one-fifth of one redeemable warrant"
    ],
}


@pytest.mark.parametrize(
    "title",
    [
        "Class A Common Stock, Warrant, Unit",
        "Class A Common Stock",
        "Class A Common Stock; Units, each consisting of one share and one-half warrant",
        "common stock, warrants, units",
    ],
)
def test_a_spac_form_25_ends_its_common_class(title: str) -> None:
    assert _form25_classes(title, SPAC) == ("common",)


def test_a_sail_securities_component_is_a_unit_component() -> None:
    classes = {
        "common": ["Shares of Class A common stock included as part of the SAILSM securities"],
        "sail": ["SAILSM securities, each consisting of one Class A share"],
    }
    assert _form25_classes("Class A Common Stock", classes) == ("common",)


def test_a_warrant_form_25_ends_the_warrant_component() -> None:
    classes = {
        "common": ["Class A common stock included as part of the units"],
        "warrants": ["Redeemable warrants included as part of the units"],
    }
    assert _form25_classes("Redeemable warrants", classes) == ("warrants",)


@pytest.mark.parametrize(
    "listing",
    [
        "HD Supply Holdings, Inc. common stock, par value $0.01 per share",
        "ShockWave Medical Inc., common stock, par value $0.001 per share",
        "$.01 Par Value, Common Stock",
    ],
)
def test_a_comma_head_naming_no_common_equity_is_read_past(listing: str) -> None:
    assert _form25_classes("Common Stock", {"only": [listing]}) == ("only",)


@pytest.mark.parametrize(
    "listing",
    [
        "Warrants, each exercisable for one share of Common Stock",
        "Units, each consisting of one share of common stock",
        "Rights, each to receive one-tenth of one common share",
        "Depositary Shares, each representing 1/10th of a share of Preferred Stock",
    ],
)
def test_another_class_is_never_read_as_common(listing: str) -> None:
    assert _form25_title(listing) == listing
    assert _form25_classes("Common Stock", {"other": [listing]}) == ()


@pytest.mark.parametrize(
    "title",
    [
        "Series A Preference Shares, convertible into Common Stock",
        "Exchangeable Shares, exchangeable for common stock",
        "Subscription Receipts, each exchangeable for one common share",
        "American Depository Shares, each representing one ordinary share",
        "Shares, each convertible into common stock",
        "Warrants, each exercisable for one share of Common Stock",
    ],
)
def test_a_filing_for_another_class_never_ends_the_only_common_class(title: str) -> None:
    """quant-auditor on #3095: a description after the comma is never read
    as the class, so the issuer's one plain-common class stays listed."""
    assert _form25_title(title) == title
    assert _form25_classes(title, {"common": ["Common Stock, par value $0.01"]}) == ()


def test_community_in_a_name_is_no_unit() -> None:
    """The not-common words count at a word start, so a bank's name reads past."""
    listing = "First Community Bancorp, Inc., common stock, par value $1"
    assert _form25_title(listing) == "common stock, par value $1"
    assert _form25_classes("Common Stock", {"bank": [listing]}) == ("bank",)


def test_a_filing_rules_1_to_3_resolve_keeps_exactly_its_classes() -> None:
    """Old reading: "Units, Class A Common Stock and Warrants" ends the units
    class (rule 3, by its head). Rule 4 would read the common class, but it
    never runs when rules 1-3 resolve, so no stored episode moves."""
    classes = {
        "common": ["Class A common stock included as part of the units"],
        "units": ["Units"],
    }
    assert _form25_classes("Units, Class A Common Stock and Warrants", classes) == ("units",)


def test_two_classes_reading_alike_stay_unresolved() -> None:
    """A component listing and a plain one of the same class read alike
    under rule 4: two matches are never a guess."""
    classes = {
        "component": ["Class A common stock included as part of the units"],
        "plain": ["Class A common stock, par value $0.0001"],
        "units": ["Units, each consisting of one share of Class A common stock"],
    }
    assert _form25_classes("Class A Common Stock, Warrants", classes) == ("plain",)
    two_components = {
        "a": ["Class A common stock included as part of the units"],
        "b": ["Class A common stock included in the units"],
    }
    assert _form25_classes("Class A Common Stock, Warrant, Unit", two_components) == ()


# --- the `delistings` rows ------------------------------------------------------

CIK = "0001824677"
INGESTED_AT = datetime(2026, 11, 6, tzinfo=UTC)
KNOWN = datetime(2021, 3, 1, 21, 0, tzinfo=UTC)


def _row(security_id: str, ticker: str, title: str) -> dict[str, Any]:
    return {
        "security_id": security_id,
        "ticker": ticker,
        "exchange": "NASDAQ",
        "class_title": title,
        "valid_from": date(2021, 3, 2),
        "known_at": KNOWN,
        "ingested_at": INGESTED_AT,
        "source": "edgar",
        "provenance": "filing",
    }


def _master() -> MasterBuild:
    ids = {
        CIK: ("OEPW", SPAC["common"][0]),
        f"{CIK}:warrants": ("OEPWW", SPAC["warrants"][0]),
        f"{CIK}:units": ("OEPWU", SPAC["units"][0]),
    }
    return MasterBuild(
        securities=tuple(
            {
                "security_id": sid,
                "cik": CIK,
                "name": "One Equity Partners Open Water I Corp.",
                "benchmark": False,
                "known_at": KNOWN,
                "ingested_at": INGESTED_AT,
                "source": "edgar",
                "provenance": "filing",
            }
            for sid in ids
        ),
        listings=tuple(_row(sid, ticker, title) for sid, (ticker, title) in ids.items()),
        unmatched_snapshot=(),
        missing_benchmarks=(),
    )


def test_a_spac_liquidation_form_25_writes_the_common_classs_row() -> None:
    filing = DelistingFiling(
        cik=CIK,
        form="25-NSE",
        class_title="Class A Common Stock, Warrant, and Unit",
        exchange="NASDAQ",
        accession="0001354457-22-000737",
        accepted_at=datetime(2022, 12, 12, 21, 30, tzinfo=UTC),
    )
    build = build_delistings([filing], _master(), ingested_at=INGESTED_AT)
    assert [(r["security_id"], r["known_at"]) for r in build.delistings] == [
        (CIK, filing.accepted_at)
    ]
    assert build.unmatched == ()
