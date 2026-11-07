"""No look-ahead in #3084's Form 25 coverage.

1. A `delistings` row read by the new title rule (rule 4) is known at its
   Form 25's acceptance: building from only the filings accepted by `t`
   gives exactly the full build's rows known by `t`, for every `t`.
2. Rule 4 never reaches a class whose security the store first knew after
   the filing (`_resolve`'s `known_at <= accepted_at` gate holds for it).
3. An issuer's HTML Form 25 is stamped at its own acceptance, never at the
   run's clock (the source test `test_an_issuers_html_form_25_is_read_and_cached`
   pins the same at the source; here at the parser).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from tradepartner.adapters.edgar import parse_delisting_html
from tradepartner.adapters.filings import DelistingFiling
from tradepartner.store.delistings import build_delistings
from tradepartner.store.master import MasterBuild

INGESTED_AT = datetime(2026, 11, 6, tzinfo=UTC)
SPAC = "0001824677"
HDS = "0001573097"


def _security(sid: str, cik: str, known_at: datetime) -> dict[str, Any]:
    return {
        "security_id": sid,
        "cik": cik,
        "name": "X",
        "benchmark": False,
        "known_at": known_at,
        "ingested_at": INGESTED_AT,
        "source": "edgar",
        "provenance": "filing",
    }


def _listing(sid: str, ticker: str, title: str, known_at: datetime) -> dict[str, Any]:
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": "NASDAQ",
        "class_title": title,
        "valid_from": known_at.date() + timedelta(days=1),
        "known_at": known_at,
        "ingested_at": INGESTED_AT,
        "source": "edgar",
        "provenance": "filing",
    }


def _master(spac_known: datetime) -> MasterBuild:
    early = datetime(2019, 1, 2, 21, 0, tzinfo=UTC)
    return MasterBuild(
        securities=(
            _security(SPAC, SPAC, spac_known),
            _security(f"{SPAC}:units", SPAC, spac_known),
            _security(HDS, HDS, early),
        ),
        listings=(
            _listing(
                SPAC, "OEPW", "Class A common shares included as part of the units", spac_known
            ),
            _listing(f"{SPAC}:units", "OEPWU", "Units, each consisting of one share", spac_known),
            _listing(HDS, "HDS", "HD Supply Holdings, Inc. common stock, par value $0.01", early),
        ),
        unmatched_snapshot=(),
        missing_benchmarks=(),
    )


def _filing(cik: str, title: str, accepted_at: datetime) -> DelistingFiling:
    return DelistingFiling(
        cik=cik,
        form="25-NSE",
        class_title=title,
        exchange="NASDAQ",
        accession=f"{cik}-{accepted_at:%Y%m%d}",
        accepted_at=accepted_at,
    )


FILINGS = (
    _filing(HDS, "Common Stock", datetime(2020, 12, 14, 21, 0, tzinfo=UTC)),
    _filing(
        SPAC, "Class A Common Stock, Warrant, and Unit", datetime(2022, 12, 12, 21, 30, tzinfo=UTC)
    ),
)


def test_rows_known_by_t_are_the_rows_built_from_filings_accepted_by_t() -> None:
    master = _master(datetime(2021, 3, 1, 21, 0, tzinfo=UTC))
    full = build_delistings(FILINGS, master, ingested_at=INGESTED_AT).delistings
    assert {r["security_id"] for r in full} == {HDS, SPAC}  # both read by rule 4
    for filing in FILINGS:
        for t in (filing.accepted_at - timedelta(microseconds=1), filing.accepted_at):
            seen = [f for f in FILINGS if f.accepted_at <= t]
            built = build_delistings(seen, master, ingested_at=INGESTED_AT).delistings
            assert built == tuple(r for r in full if r["known_at"] <= t)
    assert all(r["known_at"] == r["filed_at"] for r in full)


def test_rule_4_never_reaches_a_security_known_after_the_filing() -> None:
    spac_filing = FILINGS[1]
    later = spac_filing.accepted_at + timedelta(microseconds=1)
    build = build_delistings([spac_filing], _master(later), ingested_at=INGESTED_AT)
    assert build.delistings == ()
    assert build.unmatched == (spac_filing,)
    at_filing = build_delistings(
        [spac_filing], _master(spac_filing.accepted_at), ingested_at=INGESTED_AT
    )
    assert [r["security_id"] for r in at_filing.delistings] == [SPAC]


def test_an_html_form_25_is_known_at_its_acceptance() -> None:
    accepted = datetime(2026, 4, 20, 21, 5, 11, tzinfo=UTC)
    document = (
        "<p>Commission File Number 001-39273</p><p>LYRA THERAPEUTICS, INC.</p>"
        "<p>THE NASDAQ CAPITAL MARKET</p><p>(Exact name of Issuer as specified in its "
        "charter, and name of Exchange where security is listed and/or registered)</p>"
        "<p>(Address, including zip code, of Issuer's principal executive offices)</p>"
        "<p>Common Stock</p><p>(Description of class of securities)</p>"
        "<p>Date: April 20, 2026</p>"
    )
    filing = parse_delisting_html(
        document, cik="1327273", form="25", accession="0001193125-26-164179", accepted_at=accepted
    )
    assert filing.accepted_at == accepted
    master = MasterBuild(
        securities=(_security("0001327273", "0001327273", accepted - timedelta(days=900)),),
        listings=(_listing("0001327273", "LYRA", "Common Stock", accepted - timedelta(days=900)),),
        unmatched_snapshot=(),
        missing_benchmarks=(),
    )
    [row] = build_delistings([filing], master, ingested_at=INGESTED_AT).delistings
    assert row["known_at"] == accepted
    assert row["effective_on"] == date(2026, 4, 30)  # Rule 12d2-2(d)(1): 10 days after filing
    early = replace(filing, accepted_at=accepted - timedelta(days=901))
    assert build_delistings([early], master, ingested_at=INGESTED_AT).delistings == ()
