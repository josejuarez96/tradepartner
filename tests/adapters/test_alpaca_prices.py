"""Tests for tradepartner.adapters.alpaca_prices (T12): parsers over T3's
recorded Alpaca payloads (`tests/fixtures/alpaca/`).

Plan T12: raw closes; `known_at` per the timing rules in `adapters.prices`;
corporate actions on the first-seen proxy (Alpaca gives no announcement
time, #101); symbols resolved through the security master, never by bare
ticker.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, ClassVar

import pytest

from tradepartner.adapters.alpaca_prices import (
    AlpacaPriceSource,
    ListingResolver,
    RegistrantEvidence,
    alpaca_symbol,
    feed_source,
    is_same_day_typo,
    parse_bars,
    parse_corporate_actions,
    registrant_evidence,
    same_alpaca_symbol,
)
from tradepartner.adapters.prices import (
    ActionType,
    UnknownSecurityIdError,
    action_first_seen_known_at,
    bar_known_at,
)
from tradepartner.config import Settings

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "alpaca"


def _json(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def _listing(
    security_id: str, ticker: str, valid_from: date, class_title: str | None = None
) -> dict[str, object]:
    return {
        "security_id": security_id,
        "ticker": ticker,
        "valid_from": valid_from,
        "class_title": class_title,
    }


START = date(2016, 1, 4)
LISTINGS = [
    _listing("SEC_AAPL", "AAPL", START),
    _listing("SEC_MSFT", "MSFT", START),
    _listing("SEC_KO", "KO", START),
    _listing("BENCH:SPY", "SPY", START),
    _listing("BENCH:MTUM", "MTUM", START),
]


@pytest.fixture
def resolver() -> ListingResolver:
    return ListingResolver(LISTINGS)


class TestResolver:
    def test_ticker_change_keeps_one_security(self) -> None:
        resolver = ListingResolver(
            [
                _listing("SEC_META", "FB", date(2016, 1, 4)),
                _listing("SEC_META", "META", date(2022, 6, 9)),
            ]
        )
        assert resolver.resolve("FB", date(2020, 1, 2)) == "SEC_META"
        assert resolver.resolve("FB", date(2022, 6, 9)) is None
        assert resolver.resolve("META", date(2022, 6, 9)) == "SEC_META"
        assert resolver.resolve("META", date(2020, 1, 2)) is None

    def test_reused_ticker_goes_to_the_company_of_the_day(self) -> None:
        resolver = ListingResolver(
            [
                _listing("SEC_OLD", "REUSE", date(2016, 1, 4)),
                _listing("SEC_NEW", "REUSE", date(2022, 3, 1)),
            ]
        )
        assert resolver.resolve("REUSE", date(2021, 12, 31)) == "SEC_OLD"
        assert resolver.resolve("REUSE", date(2022, 3, 1)) == "SEC_NEW"

    def test_before_the_first_listing_is_unresolved(self, resolver: ListingResolver) -> None:
        assert resolver.resolve("AAPL", date(2015, 12, 31)) is None
        assert resolver.resolve("NOPE", date(2020, 1, 2)) is None

    def test_two_equities_starting_on_one_day_are_unassigned_not_raised(self) -> None:
        # Owner rule 3 (#735): Revlon and its parent both listed REV from
        # 2020-03-12; neither gets the rows, the run does not abort.
        resolver = ListingResolver(
            [
                _listing("0000887921", "REV", date(2020, 3, 12), "Class A Common Stock"),
                _listing("0000890547", "REV", date(2020, 3, 12), "Class A Common Stock"),
            ]
        )
        assert resolver.resolve("REV", date(2020, 6, 1)) is None
        assert resolver.report.ambiguous_spans == 2
        assert "2 ambiguous" in resolver.report.summary()

    def test_second_exchange_listing_same_ticker_is_one_span(self) -> None:
        resolver = ListingResolver(
            [_listing("SEC_X", "XX", START), _listing("SEC_X", "XX", date(2019, 5, 1))]
        )
        assert resolver.resolve("XX", date(2020, 1, 2)) == "SEC_X"
        assert resolver.symbols("SEC_X", START, date(2020, 1, 2)) == ["XX"]

    def test_a_same_day_typo_keeps_the_ticker_the_security_held(self) -> None:
        # #819 (was owner rule 2, #735): FutureFuel's cover page of
        # 2024-05-10 lists Ford's F beside its own FF. FutureFuel held FF
        # the day before, so F is a typo: FutureFuel keeps FF from that day
        # on (it lost about 600 sessions before), and Ford keeps F.
        day = date(2024, 5, 10)
        resolver = ListingResolver(
            [
                _listing("0000037996", "F", date(1994, 2, 10)),
                _listing("0000037996", "F", date(2019, 7, 24), "Common Stock"),
                _listing("0001337298", "FF", date(2005, 9, 2)),
                _listing("0001337298", "FF", date(2020, 8, 7), "Common Stock"),
                _listing("0001337298", "F", day, "Common Stock"),
                _listing("0001337298", "FF", day, "Common Stock"),
            ]
        )
        assert resolver.resolve("FF", date(2024, 5, 9)) == "0001337298"
        assert resolver.resolve("FF", day) == "0001337298"
        assert resolver.resolve("FF", date(2026, 9, 30)) == "0001337298"
        assert resolver.resolve("F", day) == "0000037996"
        assert resolver.resolve("F", date(2026, 9, 30)) == "0000037996"
        assert resolver.symbols("0001337298", day, date(2026, 9, 30)) == ["FF"]
        assert resolver.report.same_day_typos == 1
        assert (resolver.report.same_day_securities, resolver.report.same_day_listings) == (0, 0)
        assert "1 same-day typo listings dropped" in resolver.report.summary()

    def test_a_same_day_typo_after_a_second_exchange_keeps_the_ticker(self) -> None:
        # TMPM (2022-03-17) names its warrants' TMPMW on the common's row;
        # CLRC (2024-03-14) names CLCR, its ticker of 2022, again.
        tmpm, clrc = "0001823524", "0001903392"
        resolver = ListingResolver(
            [
                _listing(tmpm, "TMPM", date(2020, 11, 30), "Class A Ordinary Shares"),
                _listing(tmpm, "TMPM", date(2022, 3, 17), "Class A Ordinary Shares"),
                _listing(tmpm, "TMPMW", date(2022, 3, 17), "Class A Ordinary Shares"),
                _listing(clrc, "CLCR", date(2022, 6, 10), "Class A Ordinary Share"),
                _listing(clrc, "CLRC", date(2022, 11, 9), "Class A Ordinary Share"),
                _listing(clrc, "CLCR", date(2024, 3, 14), "Class A Ordinary Share"),
                _listing(clrc, "CLRC", date(2024, 3, 14), "Class A Ordinary Share"),
            ]
        )
        assert resolver.resolve("TMPM", date(2022, 3, 17)) == tmpm
        assert resolver.resolve("TMPM", date(2023, 1, 3)) == tmpm
        assert resolver.resolve("TMPMW", date(2022, 3, 17)) is None
        assert resolver.resolve("CLRC", date(2024, 3, 14)) == clrc
        assert resolver.resolve("CLCR", date(2024, 3, 14)) is None
        assert resolver.resolve("CLCR", date(2022, 7, 1)) == clrc
        assert resolver.report.same_day_typos == 2

    def test_two_new_tickers_on_one_day_are_unassigned_from_that_day(self) -> None:
        # Owner rule 2 (#735) where neither ticker is the one held before:
        # nothing shows which is right, so neither is assigned.
        day = date(2024, 5, 10)
        resolver = ListingResolver(
            [
                _listing("0000037996", "F", date(1994, 2, 10)),
                _listing("0001337298", "FF", date(2020, 8, 7), "Common Stock"),
                _listing("0001337298", "F", day, "Common Stock"),
                _listing("0001337298", "FFX", day, "Common Stock"),
            ]
        )
        assert resolver.resolve("FF", date(2024, 5, 9)) == "0001337298"
        assert resolver.resolve("FFX", day) is None
        assert resolver.resolve("F", day) == "0000037996"
        assert resolver.symbols("0001337298", day, date(2024, 5, 31)) == []
        assert (resolver.report.same_day_securities, resolver.report.same_day_listings) == (1, 2)
        assert resolver.report.same_day_typos == 0

    def test_notes_and_preferred_under_the_common_ticker_are_left_out(self) -> None:
        # Owner rule 1 (#735): JNJ's notes and KSU's preferred list under
        # the common's ticker; the common resolves, the others are counted.
        jnj, ksu = "0000200406", "0000054480"
        resolver = ListingResolver(
            [
                _listing(jnj, "JNJ", START),
                _listing(
                    f"{jnj}:0-650pct-notes-due-may-2024",
                    "JNJ",
                    date(2019, 7, 29),
                    "0.650% Notes due May 2024",
                ),
                _listing(
                    f"{jnj}:floating-rate-notes",
                    "JNJ",
                    date(2019, 7, 29),
                    "Floating Rate Notes due 2020",
                ),
                _listing(ksu, "KSU", date(2019, 7, 19), "Common Stock, $.01 Par Value"),
                _listing(
                    f"{ksu}:preferred-stock",
                    "KSU",
                    date(2019, 7, 19),
                    "Preferred Stock, Par Value $25 Per Share",
                ),
                _listing(
                    f"{ksu}:rights", "KSU", date(2019, 7, 19), "Preferred Stock Purchase Rights"
                ),
            ]
        )
        assert resolver.resolve("JNJ", date(2020, 1, 2)) == jnj
        assert resolver.resolve("KSU", date(2020, 1, 2)) == ksu
        assert resolver.report.non_equity == {"debt": 1, "coupon": 1, "preferred": 2}
        notes = f"{jnj}:0-650pct-notes-due-may-2024"
        assert resolver.knows(notes)
        assert resolver.symbols(notes, START, date(2020, 1, 2)) == []

    def test_a_note_starting_after_the_common_does_not_take_its_ticker(self) -> None:
        # The silent case: the latest span wins a ticker, so a note listed
        # under MSFT years after the common would otherwise price as MSFT.
        resolver = ListingResolver(
            [
                _listing("0000789019", "MSFT", START),
                _listing(
                    "0000789019:2-125pct-notes-due-2021",
                    "MSFT",
                    date(2019, 10, 23),
                    "2.125% Notes due 2021",
                ),
            ]
        )
        assert resolver.resolve("MSFT", date(2020, 1, 2)) == "0000789019"

    def test_a_later_class_of_the_company_never_takes_its_ticker(self) -> None:
        # AIN: the cover page of 2019 lists the unlisted Class B under the
        # Class A's ticker. The Class A keeps it; the Class B never holds
        # it, not even after the Class A moves to another ticker.
        resolver = ListingResolver(
            [
                _listing("0000819793", "AIN", START),
                _listing(
                    "0000819793:class-b-common-stock",
                    "AIN",
                    date(2019, 7, 31),
                    "Class B Common Stock",
                ),
                _listing("0000819793", "AINX", date(2023, 1, 3), "Class A Common Stock"),
            ]
        )
        assert resolver.resolve("AIN", date(2020, 1, 2)) == "0000819793"
        assert resolver.resolve("AIN", date(2023, 6, 1)) is None
        assert resolver.report.later_class_spans == 1

    def test_another_company_taking_the_ticker_later_still_wins(self) -> None:
        resolver = ListingResolver(
            [
                _listing("0000000001", "REUSE", START, "Common Stock"),
                _listing("0000000002", "REUSE", date(2022, 3, 1), "Common Stock"),
            ]
        )
        assert resolver.resolve("REUSE", date(2022, 3, 1)) == "0000000002"
        assert resolver.report.later_class_spans == 0

    @pytest.mark.parametrize(
        "ticker",
        ["", "N/A", "n/a", "NA", "None", "NONE", "Not applicable", "-", " 0 ", "true", "No"],
    )
    def test_placeholder_tickers_are_left_out(self, ticker: str) -> None:
        resolver = ListingResolver(
            [
                _listing("0000000001", ticker, START, "Common Stock"),
                _listing("0000000002", ticker, START, "Common Stock"),
                _listing("0000000003", "ABC", START, "Common Stock"),
                _listing("0000000003", ticker, START, "Common Stock"),  # no second ticker
            ]
        )
        assert resolver.resolve(ticker, date(2020, 1, 2)) is None
        assert resolver.resolve("ABC", date(2020, 1, 2)) == "0000000003"
        assert resolver.report.placeholder == 3
        assert resolver.report.same_day_securities == 0
        assert resolver.symbols("0000000001", START, date(2020, 1, 2)) == []

    def test_ticker_taken_through_a_rename_is_contested(self) -> None:
        # Roundhill's ETF traded as META before Facebook renamed FB -> META;
        # Alpaca serves Facebook's history under META too (#104).
        resolver = ListingResolver(
            [
                _listing("SEC_ROUNDHILL", "META", date(2021, 6, 30)),
                _listing("SEC_FB", "FB", START),
                _listing("SEC_FB", "META", date(2022, 6, 9)),
            ]
        )
        assert resolver.resolve("META", date(2021, 9, 1)) is None
        assert resolver.resolve("META", date(2022, 7, 1)) == "SEC_FB"
        assert resolver.resolve("FB", date(2021, 9, 1)) == "SEC_FB"
        [span] = resolver.contested_spans
        assert (span.security_id, span.ticker) == ("SEC_ROUNDHILL", "META")
        payload = {
            "feed": "sip",
            "bars": {
                "META": [
                    {
                        "t": "2021-09-01T04:00:00Z",
                        "o": 380,
                        "h": 385,
                        "l": 378,
                        "c": 382.0,
                        "v": 9_000_000,
                        "n": 90_000,
                        "vw": 381.0,
                    }
                ]
            },
        }
        parsed = parse_bars(payload, resolver.resolve)
        assert not parsed.bars
        assert parsed.unresolved == (("META", date(2021, 9, 1)),)

    def test_plain_reuse_is_not_contested(self) -> None:
        resolver = ListingResolver(
            [_listing("SEC_OLD", "REUSE", START), _listing("SEC_NEW", "REUSE", date(2022, 3, 1))]
        )
        assert resolver.contested_spans == ()

    @pytest.mark.parametrize("ticker", ["TRUE", "NO"])
    def test_a_word_ticker_in_capitals_resolves(self, ticker: str) -> None:
        resolver = ListingResolver([_listing("0000000001", ticker, START, "Common Stock")])
        assert resolver.resolve(ticker, date(2020, 1, 2)) == "0000000001"

    def test_a_later_not_applicable_row_never_takes_na(self) -> None:
        # #736 review: Nano Labs trades as NA; Courtside's cover page (2023,
        # exchange NONE) and BioCancell's untraded ordinary shares (2024)
        # also write NA. NA holds nothing: no row goes to the wrong company.
        resolver = ListingResolver(
            [
                _listing(
                    "0001872302",
                    "NA",
                    date(2022, 6, 10),
                    "American depositary shares, each representing two Class A shares",
                ),
                _listing("0001940177", "NA", date(2023, 8, 14), "NA"),
                _listing(
                    "0001534248:ordinary-shares",
                    "NA",
                    date(2024, 3, 28),
                    "Ordinary shares, no par-value",
                ),
            ]
        )
        for session in (date(2022, 7, 1), date(2023, 8, 15), date(2024, 3, 29), date(2026, 9, 30)):
            assert resolver.resolve("NA", session) is None
        assert resolver.report.placeholder == 3

    def test_a_left_out_listing_still_shadows_an_older_company(self) -> None:
        # Company 3's units take T in 2015: company 1's span (2005, never
        # ended by a later listing) must not get 2016's bars back.
        resolver = ListingResolver(
            [
                _listing("0000000001", "T", date(2005, 1, 3), "Common Stock"),
                _listing("0000000003:units", "T", date(2015, 1, 2), "Units"),
            ]
        )
        assert resolver.resolve("T", date(2014, 12, 31)) == "0000000001"
        assert resolver.resolve("T", date(2016, 1, 4)) is None

    def test_a_later_class_still_shadows_an_older_company(self) -> None:
        resolver = ListingResolver(
            [
                _listing("0000000001", "T", date(2005, 1, 3), "Common Stock"),
                _listing("0000000002", "T", date(2010, 1, 4), "Class A Common Stock"),
                _listing("0000000002:class-b", "T", date(2012, 1, 3), "Class B Common Stock"),
                _listing("0000000002", "U", date(2014, 1, 2), "Class A Common Stock"),
            ]
        )
        assert resolver.resolve("T", date(2011, 1, 3)) == "0000000002"
        assert resolver.resolve("T", date(2013, 1, 2)) == "0000000002"
        assert resolver.resolve("T", date(2015, 1, 2)) is None

    def test_a_warrant_row_on_the_commons_day_does_not_cost_the_common(self) -> None:
        resolver = ListingResolver(
            [
                _listing("0000000001", "ABC", START, "Class A Common Stock"),
                _listing("0000000001", "ABCW", START, "Redeemable Warrants"),
            ]
        )
        assert resolver.resolve("ABC", date(2020, 1, 2)) == "0000000001"
        assert resolver.report.same_day_securities == 0

    def test_listings_from_a_later_day_never_change_an_earlier_mapping(self) -> None:
        # No look-ahead: dropping every row from `cut` on leaves each
        # earlier session's mapping as it was (renames aside: contested
        # spans are the documented exception).
        cut = date(2019, 7, 1)
        rows = [
            _listing("0000000001", "AAA", START),
            _listing(
                "0000000001",
                "AAA",
                date(2020, 2, 3),
                "Common Shares (including Rights under Shareholder Rights Plan)",
            ),
            _listing("0000000001:notes", "AAA", date(2019, 10, 1), "1.5% Notes due 2029"),
            _listing("0000000002", "BBB", START),
            _listing("0000000002", "BB", date(2021, 3, 1), "Common Stock"),
            _listing("0000000002", "BBB", date(2021, 3, 1), "Common Stock"),
            _listing("0000000003", "CCC", START),
            _listing("0000000003:class-b", "CCC", date(2019, 8, 1), "Class B Common Stock"),
            _listing("0000000004", "DDD", START),
            _listing("0000000005:warrants", "DDD", date(2020, 6, 1), "Warrants"),
            _listing("0000000006", "EEE", date(2017, 5, 1), "Common Stock"),
            _listing("0000000007", "EEE", date(2019, 9, 3), "Common Stock"),
        ]
        full = ListingResolver(rows)
        early = ListingResolver([r for r in rows if r["valid_from"] < cut])  # type: ignore[operator]
        sessions = [date(y, m, 1) for y in range(2016, 2020) for m in range(1, 13)]
        for ticker in ("AAA", "BBB", "BB", "CCC", "DDD", "EEE"):
            for session in (s for s in sessions if s < cut):
                assert full.resolve(ticker, session) == early.resolve(ticker, session), (
                    ticker,
                    session,
                )
        assert full.resolve("AAA", date(2019, 3, 1)) == "0000000001"

    def test_symbols_over_a_range(self) -> None:
        resolver = ListingResolver(
            [
                _listing("SEC_META", "FB", date(2016, 1, 4)),
                _listing("SEC_META", "META", date(2022, 6, 9)),
            ]
        )
        assert resolver.symbols("SEC_META", date(2022, 1, 3), date(2022, 12, 30)) == ["FB", "META"]
        assert resolver.symbols("SEC_META", date(2023, 1, 3), date(2023, 12, 29)) == ["META"]
        assert resolver.symbols("SEC_NONE", date(2023, 1, 3), date(2023, 12, 29)) == []


def _shares(security_id: str, as_of: date, value: float, known: date) -> dict[str, object]:
    return {
        "security_id": security_id,
        "fact_name": "shares_outstanding",
        "as_of_date": as_of,
        "value": value,
        "known_at": datetime(known.year, known.month, known.day, 16, tzinfo=UTC),
    }


def _ended(
    security_id: str,
    ticker: str,
    class_title: str,
    effective_on: date,
    status: str = "delisted",
    valid_from: date = date(1994, 1, 3),
) -> dict[str, object]:
    """A `listing_ends_as_of` row: the listing (from `valid_from`) ended by a Form 25."""
    return {
        "security_id": security_id,
        "ticker": ticker,
        "class_title": class_title,
        "valid_from": valid_from,
        "status": status,
        "effective_on": effective_on,
        "end_session": None,
    }


RUN_DAY = date(2026, 10, 4)


def _evidence(
    facts: list[dict[str, object]],
    ends: list[dict[str, object]] | None = None,
    as_of: date = RUN_DAY,
) -> RegistrantEvidence:
    return registrant_evidence(facts, ends or [], as_of=as_of, quiet_after_days=180)


# Real cases from backfill pre-flight F (#793), store values as filed.
AEP, AEP_TEXAS = "0000004904", "0001721781"
AEP_LISTINGS = [
    _listing(AEP, "AEP", date(1994, 5, 16)),
    _listing(AEP, "AEP", date(2025, 2, 13), "Common Stock, $6.50 par value"),
    _listing(AEP_TEXAS, "AEP", date(2026, 7, 30), "Common Stock, $6.50 par value"),
]
AEP_FACTS = [
    _shares(AEP, date(2026, 5, 5), 544104955, date(2026, 5, 5)),
    _shares(AEP, date(2026, 7, 30), 544397352, date(2026, 7, 30)),
    _shares(AEP_TEXAS, date(2026, 7, 30), 544397352, date(2026, 7, 30)),
]
MGEE, MGE = "0001161728", "0000061339"
MGEE_LISTINGS = [
    _listing(MGEE, "MGEE", date(2019, 8, 7), "Common Stock, $1 Par Value"),
    _listing(MGE, "MGEE", date(2026, 2, 27), "Common Stock, $1 Par Value"),
]
MGEE_FACTS = [
    _shares(MGE, date(2015, 7, 31), 34668370, date(2015, 8, 6)),
    _shares(MGE, date(2026, 2, 20), 17347894, date(2026, 2, 27)),
    _shares(MGEE, date(2026, 7, 31), 37784012, date(2026, 8, 5)),
]


class TestRegistrantCheck:
    """Rule 6 (#793): another company's later span of a ticker takes it only
    from a holder that has left it (a delisted equity listing) or gone
    quiet; until then it is a co-registrant or disputed claim."""

    def test_a_subsidiary_citing_the_parents_ticker_never_takes_it(self) -> None:
        # AEP Texas's cover page of 2026-07-30 lists AEP's common stock and
        # reports AEP's share count: a co-registrant, AEP keeps its bars.
        resolver = ListingResolver(AEP_LISTINGS, _evidence(AEP_FACTS))
        assert resolver.resolve("AEP", date(2026, 9, 15)) == AEP
        assert resolver.symbols(AEP_TEXAS, date(2026, 1, 2), date(2026, 9, 30)) == []
        assert resolver.report.co_registrant_spans == 1
        assert "1 co-registrant and 0 disputed claims" in resolver.report.summary()

    def test_an_operating_partnership_citing_the_reits_ticker_never_takes_it(self) -> None:
        maa, maa_lp = "0000912595", "0001581776"
        resolver = ListingResolver(
            [
                _listing(maa, "MAA", date(1996, 5, 14)),
                _listing(maa, "MAA", date(2019, 10, 31), "Common Stock, par value $.01"),
                _listing(maa_lp, "MAA", date(2025, 10, 31), "Common Stock, par value $.01"),
            ],
            _evidence(
                [
                    _shares(maa_lp, date(2013, 11, 4), 74776229, date(2013, 11, 7)),
                    _shares(maa, date(2025, 10, 27), 117081742, date(2025, 10, 31)),
                    _shares(maa_lp, date(2025, 10, 27), 117081742, date(2025, 10, 31)),
                    _shares(maa, date(2026, 7, 30), 116021957, date(2026, 7, 30)),
                ],
                # A preferred listing's delisting is not the common leaving.
                [_ended(maa, "MAA-PI", "8.50% Series I Preferred Stock", date(2026, 10, 1))],
            ),
        )
        assert resolver.resolve("MAA", date(2026, 9, 15)) == maa

    def test_a_co_registrant_never_inherits_the_parents_old_ticker(self) -> None:
        # MPT's operating partnership cites MPW; the REIT moves to MPT. The
        # partnership never traded MPW and does not take it then.
        listings = [*AEP_LISTINGS, _listing(AEP, "AEPX", date(2026, 9, 1), "Common Stock")]
        resolver = ListingResolver(listings, _evidence(AEP_FACTS))
        assert resolver.resolve("AEP", date(2026, 8, 31)) == AEP
        assert resolver.resolve("AEP", date(2026, 9, 15)) is None
        assert resolver.symbols(AEP_TEXAS, date(2026, 1, 2), date(2026, 12, 31)) == []

    def test_an_old_shared_count_is_no_proof_of_a_combined_filing(self) -> None:
        # Two counts that match years before the claim are a coincidence,
        # not the combined filing that produced the claimant's listing.
        facts = [
            _shares(MGE, date(2019, 3, 31), 5000000, date(2019, 4, 30)),
            _shares(MGEE, date(2019, 3, 31), 5000000, date(2019, 4, 30)),
            *MGEE_FACTS,
        ]
        resolver = ListingResolver(MGEE_LISTINGS, _evidence(facts))
        assert resolver.resolve("MGEE", date(2026, 9, 15)) is None
        assert resolver.report.disputed_spans == 1

    def test_a_waiting_claim_never_ties_with_a_new_listing_on_its_first_day(self) -> None:
        holdings, spinco, newco = "0001808834", "0001821393", "0009999999"
        resolver = ListingResolver(
            [
                _listing(holdings, "AAN", date(2020, 10, 29), "Common Stock"),
                _listing(holdings, "PRG", date(2021, 2, 25), "Common Stock"),
                _listing(spinco, "AAN", date(2021, 2, 23), "Common Stock"),
                _listing(newco, "AAN", date(2021, 2, 25), "Common Stock"),
            ],
            _evidence([_shares(holdings, date(2026, 7, 24), 39000000, date(2026, 7, 29))]),
        )
        assert resolver.resolve("AAN", date(2021, 3, 1)) == newco
        assert resolver.report.ambiguous_spans == 0

    def test_an_exchange_transfer_is_not_the_holder_leaving(self) -> None:
        ends = [_ended(AEP, "AEP", "Common Stock", date(2019, 3, 1), status="transferred")]
        resolver = ListingResolver(AEP_LISTINGS, _evidence(AEP_FACTS, ends))
        assert resolver.resolve("AEP", date(2026, 9, 15)) == AEP

    def test_a_claim_on_a_live_holders_ticker_without_proof_is_disputed(self) -> None:
        # Madison Gas & Electric lists MGE Energy's MGEE from 2026-02-27 but
        # reports its own share count: nothing shows which registrant's
        # stock trades, so the ticker resolves to nothing while both last.
        resolver = ListingResolver(MGEE_LISTINGS, _evidence(MGEE_FACTS))
        assert resolver.resolve("MGEE", date(2026, 2, 26)) == MGEE
        assert resolver.resolve("MGEE", date(2026, 9, 15)) is None
        assert resolver.symbols(MGE, date(2026, 1, 2), date(2026, 9, 30)) == []
        assert resolver.report.disputed_spans == 1
        assert "0 co-registrant and 1 disputed claims" in resolver.report.summary()

    def test_the_verdict_does_not_turn_on_which_company_filed_last(self) -> None:
        later = _shares(MGE, date(2026, 8, 31), 17347894, date(2026, 9, 3))
        resolver = ListingResolver(MGEE_LISTINGS, _evidence([*MGEE_FACTS, later]))
        assert resolver.resolve("MGEE", date(2026, 9, 15)) is None
        assert resolver.report.disputed_spans == 1

    def test_a_disputed_claim_stops_shadowing_when_the_claimant_moves_on(self) -> None:
        listings = [*MGEE_LISTINGS, _listing(MGE, "MGEX", date(2026, 6, 1), "Common Stock")]
        resolver = ListingResolver(listings, _evidence(MGEE_FACTS))
        assert resolver.resolve("MGEE", date(2026, 5, 1)) is None
        assert resolver.resolve("MGEE", date(2026, 7, 1)) == MGEE

    def test_a_disputed_claim_waits_for_the_holder_to_move_off_the_ticker(self) -> None:
        # Aaron's SpinCo lists AAN from 2021-02-23; Aaron's Holdings, still
        # filing, moves to PRG on 2021-02-25. The spin-off holds AAN from then.
        holdings, spinco = "0001808834", "0001821393"
        resolver = ListingResolver(
            [
                _listing(holdings, "AAN", date(2020, 10, 29), "Common Stock"),
                _listing(holdings, "PRG", date(2021, 2, 25), "Common Stock"),
                _listing(spinco, "AAN", date(2021, 2, 23), "Common Stock"),
            ],
            _evidence(
                [
                    _shares(spinco, date(2021, 2, 19), 33000000, date(2021, 2, 23)),
                    _shares(spinco, date(2024, 7, 31), 31000000, date(2024, 8, 5)),
                    _shares(holdings, date(2026, 7, 24), 39000000, date(2026, 7, 29)),
                ]
            ),
        )
        assert resolver.resolve("AAN", date(2021, 2, 23)) is None
        assert resolver.resolve("AAN", date(2021, 2, 25)) == spinco
        assert resolver.resolve("AAN", date(2022, 1, 14)) == spinco
        assert resolver.symbols(spinco, date(2021, 1, 4), date(2021, 2, 24)) == []
        assert resolver.symbols(spinco, date(2021, 1, 4), date(2021, 3, 31)) == ["AAN"]
        assert resolver.report.disputed_spans == 1

    def test_a_holder_leaving_later_never_hands_its_earlier_bars_to_the_claimant(self) -> None:
        # Were AEP delisted in 2027, AEP Texas would take AEP only from then;
        # the sessions before are unassigned, no longer AEP's on proof.
        ends = [_ended(AEP, "AEP", "Common Stock", date(2027, 3, 1), valid_from=date(2025, 2, 13))]
        facts = [*AEP_FACTS, _shares(AEP, date(2027, 2, 15), 545000000, date(2027, 2, 20))]
        resolver = ListingResolver(AEP_LISTINGS, _evidence(facts, ends, date(2027, 6, 1)))
        assert resolver.resolve("AEP", date(2026, 7, 29)) == AEP
        assert resolver.resolve("AEP", date(2026, 9, 15)) is None
        assert resolver.resolve("AEP", date(2027, 3, 1)) == AEP_TEXAS
        assert resolver.report.disputed_spans == 1

    def test_a_holding_company_successor_takes_the_ticker_after_the_old_common_is_delisted(
        self,
    ) -> None:
        # NorthWestern Energy Group (2023 reorganization): the old company's
        # common was delisted by a 25-NSE before the group's first listing.
        nwe, group = "0000073088", "0001993004"
        resolver = ListingResolver(
            [
                _listing(nwe, "NWE", date(2019, 7, 23), "Common stock"),
                _listing(nwe, "NWE", date(2020, 10, 21), "Common stock"),
                _listing(group, "NWE", date(2023, 10, 26), "Common stock"),
            ],
            _evidence(
                [
                    _shares(nwe, date(2023, 10, 20), 61242238, date(2023, 10, 26)),
                    _shares(group, date(2023, 10, 20), 61242238, date(2023, 10, 26)),
                    _shares(group, date(2026, 7, 24), 61517850, date(2026, 7, 29)),
                ],
                [
                    _ended(
                        nwe, "NWE", "Common stock", date(2023, 10, 9), valid_from=date(2020, 10, 21)
                    )
                ],
            ),
        )
        assert resolver.resolve("NWE", date(2023, 10, 6)) == nwe
        # #819: the old common ends at its delisting; the group's bars
        # before its first listing (2023-10-26) are unassigned, never the
        # old company's.
        assert resolver.resolve("NWE", date(2023, 10, 25)) is None
        assert resolver.resolve("NWE", date(2024, 1, 16)) == group
        assert resolver.report.co_registrant_spans == 0
        assert resolver.report.disputed_spans == 0

    def test_a_successor_takes_the_ticker_once_the_old_company_is_quiet(self) -> None:
        # Xerox Holdings (2019): the old registrant's last share count was
        # filed before the new one's first listing.
        old, new = "0000108772", "0001770450"
        resolver = ListingResolver(
            [
                _listing(old, "XRX", date(2019, 8, 6), "Common Stock, $1 par value"),
                _listing(new, "XRX", date(2019, 11, 6), "Common Stock, $1 par value"),
            ],
            _evidence(
                [
                    _shares(old, date(2019, 7, 31), 221283933, date(2019, 8, 6)),
                    _shares(new, date(2019, 10, 31), 216188261, date(2019, 11, 6)),
                    _shares(new, date(2026, 7, 31), 131314511, date(2026, 8, 6)),
                ]
            ),
        )
        assert resolver.resolve("XRX", date(2022, 1, 14)) == new
        assert resolver.report.disputed_spans == 0

    def test_an_early_successor_listing_waits_for_the_old_company_to_go_quiet(self) -> None:
        # First Seacoast's second step: the new company lists FSEA from
        # 2022-09-13, the old one files until 2023-03-24 and then stops.
        old, new = "0001769267", "0001943802"
        resolver = ListingResolver(
            [
                _listing(old, "FSEA", date(2019, 8, 13), "Common Stock"),
                _listing(new, "FSEA", date(2022, 9, 13), "Common Stock"),
            ],
            _evidence(
                [  # the same count on the same day, but the old one went quiet
                    _shares(old, date(2023, 3, 14), 5075345, date(2023, 3, 24)),
                    _shares(new, date(2023, 3, 14), 5075345, date(2023, 3, 24)),
                    _shares(new, date(2026, 8, 3), 4704425, date(2026, 8, 7)),
                ]
            ),
        )
        assert resolver.resolve("FSEA", date(2022, 9, 12)) == old
        assert resolver.resolve("FSEA", date(2022, 12, 15)) is None
        assert resolver.resolve("FSEA", date(2023, 3, 27)) == new

    def test_a_holder_whose_common_was_delisted_loses_the_ticker_though_it_still_files(
        self,
    ) -> None:
        # Crane: the old registrant's common had a 25-NSE in 2022 and the
        # company still files; the new Crane Co takes CR as before.
        old, new = "0000025445", "0001944013"
        resolver = ListingResolver(
            [
                _listing(old, "CR", date(2019, 7, 30), "Common Stock"),
                _listing(new, "CR", date(2022, 12, 15), "Common Stock"),
            ],
            _evidence(
                [
                    _shares(old, date(2026, 7, 31), 57561304, date(2026, 8, 5)),
                    _shares(new, date(2026, 7, 29), 57800356, date(2026, 7, 31)),
                ],
                [
                    _ended(
                        old, "CR", "Common Stock", date(2022, 5, 27), valid_from=date(2019, 7, 30)
                    )
                ],
            ),
        )
        assert resolver.resolve("CR", date(2024, 1, 16)) == new

    def test_without_evidence_on_the_holder_the_newer_company_still_wins(self) -> None:
        assert ListingResolver(AEP_LISTINGS).resolve("AEP", date(2026, 9, 15)) == AEP_TEXAS
        only_claimant = _evidence([AEP_FACTS[2]])
        resolver = ListingResolver(AEP_LISTINGS, only_claimant)
        assert resolver.resolve("AEP", date(2026, 9, 15)) == AEP_TEXAS
        assert resolver.report.co_registrant_spans == 0

    def test_registrant_evidence_reads_shares_and_delisted_equity_listings(self) -> None:
        evidence = registrant_evidence(
            [
                *AEP_FACTS,
                _shares("0000000001:class-b", date(2020, 1, 2), 5, date(2020, 1, 3)),
                {**_shares(AEP, date(2027, 1, 2), 9, date(2027, 1, 2)), "fact_name": "revenue"},
            ],
            [
                _ended("0000000001:class-a", "XA", "Class A Common Stock", date(2021, 1, 4)),
                _ended("0000000001", "XA", "5.25% Notes due 2030", date(2022, 1, 4)),
                _ended("0000000001", "XA", "Common Stock", date(2023, 1, 4), "transferred"),
            ],
            as_of=RUN_DAY,
            quiet_after_days=180,
        )
        assert evidence.last_filed[AEP] == date(2026, 7, 30)
        assert evidence.same_count(AEP, AEP_TEXAS, date(2026, 7, 30))
        assert not evidence.same_count(AEP, AEP_TEXAS, date(2027, 2, 1))
        assert evidence.left_on(AEP, date(1994, 5, 16)) is None
        assert evidence.delisted_on == {"0000000001:class-a": (date(2021, 1, 4),)}
        assert evidence.delisted_listings == {
            "0000000001:class-a": ((date(1994, 1, 3), "XA", date(2021, 1, 4)),)
        }
        # quiet: no share count for more than 180 days before the run
        assert evidence.left_on("0000000001:class-b", date(2019, 1, 2)) == date(2020, 1, 4)
        assert evidence.left_on("0000000001:class-a", date(2019, 1, 2)) == date(2020, 1, 4)
        assert evidence.left_on("0000000001:class-a", date(2020, 6, 1)) == date(2020, 1, 4)

    def test_a_leaving_day_is_never_before_the_form_25_is_accepted(self) -> None:
        # quant-auditor on #830: a stated effective day before the filing's
        # acceptance would end sessions before anyone knew of the Form 25.
        row = {
            **_ended("0000000001", "XA", "Common Stock", date(2021, 1, 4)),
            "delisting_filed_at": datetime(2021, 1, 8, 21, tzinfo=UTC),
        }
        evidence = registrant_evidence([], [row], as_of=RUN_DAY, quiet_after_days=180)
        assert evidence.delisted_on == {"0000000001": (date(2021, 1, 9),)}


class TestOwnDelisting:
    """#819: a span ends at its own delisting, so a reused ticker never
    prices a delisted security; a later row of the ticker keeps it."""

    def test_a_reused_ticker_never_prices_a_delisted_security(self) -> None:
        # Eagle Bulk (EGLE) merged into Star Bulk; its NYSE listing (from
        # 2023-03-10, after a move from NASDAQ) was delisted from
        # 2024-04-19. Alpaca served another equity's EGLE bars from 2025.
        egle = "0001322439"
        resolver = ListingResolver(
            [
                _listing(egle, "EGLE", date(2020, 3, 12), "Common Stock"),
                _listing(egle, "EGLE", date(2023, 3, 10), "Common Stock"),
            ],
            _evidence(
                [_shares(egle, date(2024, 2, 23), 10000000, date(2024, 3, 1))],
                [
                    _ended(
                        egle,
                        "EGLE",
                        "Common Stock",
                        date(2024, 4, 19),
                        valid_from=date(2023, 3, 10),
                    )
                ],
            ),
        )
        assert resolver.resolve("EGLE", date(2024, 4, 18)) == egle
        for session in (date(2024, 4, 19), date(2025, 4, 16), date(2026, 10, 2)):
            assert resolver.resolve("EGLE", session) is None
            assert not resolver.holds(egle, session)
        assert resolver.holds(egle, date(2024, 4, 18))
        assert resolver.symbols(egle, date(2024, 5, 1), date(2026, 10, 2)) == []
        assert resolver.symbols(egle, date(2024, 4, 1), date(2026, 10, 2)) == ["EGLE"]
        assert resolver.report.ended_spans == 1
        assert "1 spans ended at their own delisting" in resolver.report.summary()

    def test_a_delisted_holder_never_hands_its_ticker_back_to_an_older_company(self) -> None:
        older, dead = "0000000001", "0000000002"
        resolver = ListingResolver(
            [
                _listing(older, "REUSE", date(2005, 1, 3), "Common Stock"),
                _listing(dead, "REUSE", date(2018, 3, 1), "Common Stock"),
            ],
            _evidence(
                [],
                [
                    _ended(
                        dead, "REUSE", "Common Stock", date(2021, 6, 1), valid_from=date(2018, 3, 1)
                    )
                ],
            ),
        )
        assert resolver.resolve("REUSE", date(2017, 1, 3)) == older
        assert resolver.resolve("REUSE", date(2020, 1, 2)) == dead
        assert resolver.resolve("REUSE", date(2022, 1, 3)) is None

    def test_a_successor_listed_before_the_delisting_keeps_the_ticker(self) -> None:
        # DraftKings' 2022 reorganization: the new company lists DKNG from
        # 2022-05-06, the old one's Form 25 takes effect 2022-05-15. The
        # newer span holds DKNG; the old one's end never shadows it.
        old, new = "0001772757", "0001883685"
        resolver = ListingResolver(
            [
                _listing(old, "DKNG", date(2020, 11, 13), "Class A common stock"),
                _listing(new, "DKNG", date(2022, 5, 6), "Class A Common Stock"),
            ],
            _evidence(
                [_shares(new, date(2026, 7, 31), 490000000, date(2026, 8, 5))],
                [
                    _ended(
                        old,
                        "DKNG",
                        "Class A Common Stock",
                        date(2022, 5, 15),
                        valid_from=date(2020, 11, 13),
                    )
                ],
            ),
        )
        assert resolver.resolve("DKNG", date(2022, 5, 5)) == old
        assert resolver.resolve("DKNG", date(2022, 5, 16)) == new
        assert resolver.resolve("DKNG", date(2026, 9, 30)) == new

    def test_a_new_company_takes_a_dead_ticker_only_from_its_own_listing(self) -> None:
        # MRLN: Marlin delisted 2022; Bleichroeder lists MRLN from
        # 2026-05-15. The flat 2024 bars belong to neither.
        marlin, bleichroeder = "0001260968", "0002028707:common-stock"
        resolver = ListingResolver(
            [
                _listing(marlin, "MRLN", date(2020, 3, 13), "Common Stock"),
                _listing(bleichroeder, "MRLN", date(2026, 5, 15), "Common Stock"),
            ],
            _evidence(
                [_shares(marlin, date(2021, 10, 22), 12026394, date(2021, 10, 29))],
                [
                    _ended(
                        marlin,
                        "MRLN",
                        "Common Stock",
                        date(2022, 1, 30),
                        valid_from=date(2020, 3, 13),
                    )
                ],
            ),
        )
        assert resolver.resolve("MRLN", date(2022, 1, 28)) == marlin
        assert resolver.resolve("MRLN", date(2024, 12, 2)) is None
        assert resolver.resolve("MRLN", date(2026, 5, 15)) == bleichroeder

    def test_a_renamed_security_loses_its_old_ticker_at_the_delisting(self) -> None:
        # Old IAC (now Match Group): its IAC common was delisted from
        # 2020-07-10 and its MTCH row starts 2020-08-10; the new IAC lists
        # IAC from 2020-08-10. The new IAC's July bars are nobody's.
        old, new = "0000891103", "0001800227"
        resolver = ListingResolver(
            [
                _listing(old, "IAC", date(2019, 8, 8), "Common Stock"),
                _listing(old, "MTCH", date(2020, 8, 10), "Common Stock"),
                _listing(new, "IAC", date(2020, 8, 10), "Common stock"),
            ],
            _evidence(
                [_shares(old, date(2026, 7, 31), 229550985, date(2026, 8, 5))],
                [
                    _ended(
                        old, "IAC", "Common Stock", date(2020, 7, 10), valid_from=date(2019, 8, 8)
                    )
                ],
            ),
        )
        assert resolver.resolve("IAC", date(2020, 7, 9)) == old
        assert resolver.resolve("IAC", date(2020, 7, 13)) is None
        assert resolver.resolve("IAC", date(2020, 8, 10)) == new
        assert resolver.resolve("MTCH", date(2020, 8, 10)) == old

    def test_a_later_row_of_the_ticker_keeps_the_span_through_an_old_form_25(self) -> None:
        # Citigroup (causes 3): an undated snapshot listing of C, a 2012
        # Form 25 against it, and the first cover page in 2019. C traded
        # throughout; its 2016-2019 bars stay.
        citi = "0000831001"
        resolver = ListingResolver(
            [
                _listing(citi, "C", date(1994, 1, 13)),
                _listing(citi, "C", date(2019, 8, 1), "Common Stock, par value $.01 per share"),
            ],
            _evidence(
                [_shares(citi, date(2026, 7, 31), 1800000000, date(2026, 8, 5))],
                [
                    _ended(
                        citi, "C", "Common Stock", date(2012, 12, 28), valid_from=date(1994, 1, 13)
                    )
                ],
            ),
        )
        assert resolver.resolve("C", date(2016, 1, 4)) == citi
        assert resolver.resolve("C", date(2026, 9, 30)) == citi
        assert resolver.report.ended_spans == 0
        assert resolver.report.kept_spans == 1
        assert "1 kept through one by a later row" in resolver.report.summary()

    def test_a_reorganized_security_with_a_new_row_keeps_its_ticker(self) -> None:
        # CMPR (#820): a reorganization's Form 25 ends the old shares'
        # listing from 2019-12-13; the same ticker's new row starts later.
        # The span runs on, through the sessions before the new row.
        cimpress = "0001262976"
        listings = [
            _listing(cimpress, "CMPR", date(2019, 8, 7), "Ordinary Shares"),
            _listing(cimpress, "CMPR", date(2020, 2, 5), "Ordinary Shares"),
        ]
        ends = [
            _ended(
                cimpress, "CMPR", "Ordinary Shares", date(2019, 12, 13), valid_from=date(2019, 8, 7)
            )
        ]
        facts = [_shares(cimpress, date(2026, 7, 31), 25000000, date(2026, 8, 5))]
        resolver = ListingResolver(listings, _evidence(facts, ends))
        assert resolver.resolve("CMPR", date(2019, 12, 20)) == cimpress
        assert resolver.resolve("CMPR", date(2026, 9, 30)) == cimpress
        # Without the new row (before #820), the span ends at the delisting.
        alone = ListingResolver(listings[:1], _evidence(facts, ends))
        assert alone.resolve("CMPR", date(2019, 12, 20)) is None

    def test_a_delisted_co_registrant_never_blanks_the_holders_ticker(self) -> None:
        # code-review on #830: AEP Texas never held AEP (co-registrant); its
        # own listing's delisting must not shadow AEP from then on.
        ends = [
            _ended(
                AEP_TEXAS,
                "AEP",
                "Common Stock",
                date(2026, 9, 1),
                valid_from=date(2026, 7, 30),
            )
        ]
        resolver = ListingResolver(AEP_LISTINGS, _evidence(AEP_FACTS, ends))
        assert resolver.report.co_registrant_spans == 1
        assert resolver.resolve("AEP", date(2026, 9, 15)) == AEP
        assert resolver.report.ended_spans == 0

    def test_a_later_placeholder_row_never_hands_the_ticker_back(self) -> None:
        # code-review on #830: a delisted shell keeps filing with trading
        # symbol "None"; the older company does not get REUSE back then.
        older, dead = "0000000001", "0000000002"
        resolver = ListingResolver(
            [
                _listing(older, "REUSE", date(2005, 1, 3), "Common Stock"),
                _listing(dead, "REUSE", date(2018, 3, 1), "Common Stock"),
                _listing(dead, "None", date(2022, 3, 1), "Common Stock"),
            ],
            _evidence(
                [],
                [
                    _ended(
                        dead,
                        "REUSE",
                        "Common Stock",
                        date(2021, 6, 1),
                        valid_from=date(2018, 3, 1),
                    )
                ],
            ),
        )
        assert resolver.resolve("REUSE", date(2021, 6, 1)) is None
        assert resolver.resolve("REUSE", date(2023, 1, 3)) is None

    def test_a_delisted_typo_listing_never_ends_the_held_tickers_span(self) -> None:
        # code-review on #830: the dropped typo row shares its day with the
        # held ticker's row; its delisting must not end FutureFuel's FF.
        day = date(2024, 5, 10)
        resolver = ListingResolver(
            [
                _listing("0001337298", "FF", date(2020, 8, 7), "Common Stock"),
                _listing("0001337298", "F", day, "Common Stock"),
                _listing("0001337298", "FF", day, "Common Stock"),
            ],
            _evidence(
                [],
                [_ended("0001337298", "F", "Common Stock", date(2024, 6, 1), valid_from=day)],
            ),
        )
        assert resolver.resolve("FF", date(2024, 7, 1)) == "0001337298"
        assert resolver.report.ended_spans == 0

    def test_the_cut_acts_only_from_the_effective_day(self) -> None:
        # No look-ahead: the Form 25 is accepted before its effective day,
        # and every session before that day keeps its mapping.
        dead = "0000000002"
        listings = [_listing(dead, "GONE", date(2018, 3, 1), "Common Stock")]
        ends = [_ended(dead, "GONE", "Common Stock", date(2021, 6, 1), valid_from=date(2018, 3, 1))]
        with_end = ListingResolver(listings, _evidence([], ends))
        without = ListingResolver(listings, _evidence([]))
        sessions = [date(y, m, 1) for y in range(2018, 2022) for m in range(1, 13)]
        for session in (s for s in sessions if s < date(2021, 6, 1)):
            assert with_end.resolve("GONE", session) == without.resolve("GONE", session)

    def test_without_evidence_nothing_is_cut(self) -> None:
        resolver = ListingResolver([_listing("0000000002", "GONE", date(2018, 3, 1))])
        assert resolver.resolve("GONE", date(2026, 9, 30)) == "0000000002"
        assert resolver.report.ended_spans == 0


class TestBars:
    def test_raw_closes_across_the_split(self, resolver: ListingResolver) -> None:
        parsed = parse_bars(_json("daily_bars.json"), resolver.resolve)
        closes = {b.session: b.close for b in parsed.bars if b.security_id == "SEC_AAPL"}
        # AAPL split 4:1 with ex-date 2020-08-31: the source's raw closes, unadjusted.
        assert closes[date(2020, 8, 28)] == 499.23
        assert closes[date(2020, 8, 31)] == 129.04

    def test_known_at_is_the_session_close(self, resolver: ListingResolver) -> None:
        parsed = parse_bars(_json("daily_bars.json"), resolver.resolve)
        assert parsed.bars
        for bar in parsed.bars:
            assert bar.known_at == bar_known_at(bar.session)
        first = next(b for b in parsed.bars if b.security_id == "SEC_AAPL")
        assert first.session == date(2020, 8, 3)
        assert first.known_at == datetime(2020, 8, 3, 20, 0, tzinfo=UTC)

    def test_every_symbol_and_bar_is_parsed(self, resolver: ListingResolver) -> None:
        payload = _json("daily_bars.json")
        parsed = parse_bars(payload, resolver.resolve)
        assert len(parsed.bars) == sum(len(rows) for rows in payload["bars"].values())
        assert not parsed.unresolved and not parsed.placeholders

    def test_source_is_keyed_on_the_feed(self, resolver: ListingResolver) -> None:
        sip = parse_bars(_json("daily_bars.json"), resolver.resolve)
        iex = parse_bars(_json("daily_bars_iex.json"), resolver.resolve)
        assert {b.source for b in sip.bars} == {"alpaca_sip"}
        assert {b.source for b in iex.bars} == {"alpaca_iex"}

    def test_unknown_feed_raises(self) -> None:
        with pytest.raises(ValueError, match="feed"):
            feed_source({"feed": "delayed_sip", "bars": {}})
        with pytest.raises(ValueError, match="feed"):
            feed_source({"bars": {}})

    def test_unresolved_symbols_are_reported(self) -> None:
        parsed = parse_bars(_json("daily_bars.json"), ListingResolver(LISTINGS[1:]).resolve)
        assert {symbol for symbol, _ in parsed.unresolved} == {"AAPL"}
        assert all(b.security_id != "SEC_AAPL" for b in parsed.bars)

    def test_zero_volume_placeholder_is_not_a_bar(self, resolver: ListingResolver) -> None:
        payload = copy.deepcopy(_json("daily_bars.json"))
        last = payload["bars"]["KO"][-1]
        payload["bars"]["KO"].append(
            {
                **last,
                "t": "2020-10-01T04:00:00Z",
                "v": 0,
                "n": 0,
                "o": last["c"],
                "h": last["c"],
                "l": last["c"],
                "vw": last["c"],
            }
        )
        parsed = parse_bars(payload, resolver.resolve)
        assert ("SEC_KO", date(2020, 10, 1)) in parsed.placeholders
        assert all(b.session != date(2020, 10, 1) for b in parsed.bars)

    def test_half_day_known_at_early_close(self, resolver: ListingResolver) -> None:
        payload = {
            "feed": "sip",
            "bars": {
                "KO": [
                    {
                        "t": "2020-11-27T05:00:00Z",
                        "o": 50,
                        "h": 51,
                        "l": 49,
                        "c": 50.5,
                        "v": 100,
                        "n": 10,
                        "vw": 50.2,
                    }
                ]
            },
        }
        [bar] = parse_bars(payload, resolver.resolve).bars
        assert bar.session == date(2020, 11, 27)
        assert bar.known_at == datetime(2020, 11, 27, 18, 0, tzinfo=UTC)

    def test_non_session_bar_raises(self, resolver: ListingResolver) -> None:
        payload = {
            "feed": "sip",
            "bars": {
                "KO": [
                    {
                        "t": "2020-08-08T04:00:00Z",
                        "o": 50,
                        "h": 51,
                        "l": 49,
                        "c": 50.5,
                        "v": 100,
                        "n": 10,
                        "vw": 50.2,
                    }
                ]
            },
        }
        with pytest.raises(ValueError, match="session"):
            parse_bars(payload, resolver.resolve)

    def test_boolean_zeros_are_not_a_placeholder(self, resolver: ListingResolver) -> None:
        payload = {
            "feed": "sip",
            "bars": {
                "KO": [
                    {
                        "t": "2020-08-03T04:00:00Z",
                        "o": 50,
                        "h": 51,
                        "l": 49,
                        "c": 50.5,
                        "v": False,
                        "n": False,
                        "vw": 50.2,
                    }
                ]
            },
        }
        with pytest.raises(ValueError):
            parse_bars(payload, resolver.resolve)

    @pytest.mark.parametrize("trades", [False, 1.5, -1, "10"])
    def test_bad_trade_count_raises(self, resolver: ListingResolver, trades: object) -> None:
        payload = {
            "feed": "sip",
            "bars": {
                "KO": [
                    {
                        "t": "2020-08-03T04:00:00Z",
                        "o": 50,
                        "h": 51,
                        "l": 49,
                        "c": 50.5,
                        "v": 0,
                        "n": trades,
                        "vw": 50.2,
                    }
                ]
            },
        }
        with pytest.raises(ValueError, match="trade count"):
            parse_bars(payload, resolver.resolve)

    def test_repeated_bar_raises(self, resolver: ListingResolver) -> None:
        row = {
            "t": "2020-08-03T04:00:00Z",
            "o": 50,
            "h": 51,
            "l": 49,
            "c": 50.5,
            "v": 100,
            "n": 10,
            "vw": 50.2,
        }
        with pytest.raises(ValueError, match="repeats"):
            parse_bars({"feed": "sip", "bars": {"KO": [row, dict(row)]}}, resolver.resolve)

    def test_malformed_bar_raises_value_error(self, resolver: ListingResolver) -> None:
        payload = {"feed": "sip", "bars": {"KO": [{"t": "2020-08-03T04:00:00Z", "o": 50}]}}
        with pytest.raises(ValueError, match="malformed"):
            parse_bars(payload, resolver.resolve)


class TestCorporateActions:
    def test_split_on_the_first_seen_proxy(self, resolver: ListingResolver) -> None:
        parsed = parse_corporate_actions(_json("corporate_actions.json"), resolver.resolve)
        [split] = [a for a in parsed.actions if a.action_type is ActionType.SPLIT]
        assert (split.security_id, split.ex_date, split.ratio_or_amount) == (
            "SEC_AAPL",
            date(2020, 8, 31),
            4.0,
        )
        # No announcement time from Alpaca: the close of 2020-08-28.
        assert split.announced_at is None
        assert split.known_at == datetime(2020, 8, 28, 20, 0, tzinfo=UTC)
        assert split.source == "alpaca"

    def test_dividends(self, resolver: ListingResolver) -> None:
        parsed = parse_corporate_actions(_json("corporate_actions.json"), resolver.resolve)
        dividends = {
            (a.security_id, a.ex_date): a
            for a in parsed.actions
            if a.action_type is ActionType.DIVIDEND
        }
        assert set(dividends) == {
            ("SEC_AAPL", date(2020, 8, 7)),
            ("SEC_MSFT", date(2020, 8, 19)),
            ("BENCH:MTUM", date(2020, 9, 23)),
        }
        aapl = dividends[("SEC_AAPL", date(2020, 8, 7))]
        assert aapl.ratio_or_amount == 0.82
        assert aapl.known_at == action_first_seen_known_at(date(2020, 8, 7))

    def test_source_action_id_is_alpacas_id(self, resolver: ListingResolver) -> None:
        parsed = parse_corporate_actions(_json("corporate_actions.json"), resolver.resolve)
        ids = {(a.security_id, a.ex_date): a.source_action_id for a in parsed.actions}
        assert ids[("SEC_AAPL", date(2020, 8, 7))] == "d09386ae-0c2b-4280-8aa0-295e545354b1"
        assert all(ids.values())  # every recorded action carries an id (#108)

    def test_an_action_without_an_id_has_none(self, resolver: ListingResolver) -> None:
        payload = {"cash_dividends": [{"symbol": "AAPL", "ex_date": "2020-08-07", "rate": 0.82}]}
        [action] = parse_corporate_actions(payload, resolver.resolve).actions
        assert action.source_action_id is None

    @pytest.mark.parametrize("bad", [7, "", "   "])
    def test_a_malformed_id_raises(self, resolver: ListingResolver, bad: object) -> None:
        row = {"symbol": "AAPL", "ex_date": "2020-08-07", "rate": 0.82, "id": bad}
        with pytest.raises((ValueError, TypeError)):
            parse_corporate_actions({"cash_dividends": [row]}, resolver.resolve)

    def test_reverse_split_ratio(self, resolver: ListingResolver) -> None:
        payload = {
            "reverse_splits": [
                {"symbol": "KO", "ex_date": "2020-09-14", "old_rate": 10, "new_rate": 1}
            ]
        }
        [action] = parse_corporate_actions(payload, resolver.resolve).actions
        assert (action.action_type, action.ratio_or_amount) == (ActionType.SPLIT, 0.1)

    def test_other_categories_are_reported(self, resolver: ListingResolver) -> None:
        payload = {
            **_json("corporate_actions.json"),
            "spin_offs": [{"source_symbol": "KO", "ex_date": "2020-09-14"}],
            "stock_dividends": [],
        }
        parsed = parse_corporate_actions(payload, resolver.resolve)
        assert parsed.unsupported == ("spin_offs",)

    def test_resolved_on_the_session_before_ex_date(self) -> None:
        # A ticker change on the ex-date: the action belongs to the security
        # trading under the old symbol the session before.
        resolver = ListingResolver(
            [_listing("SEC_A", "OLD", START), _listing("SEC_A", "NEW", date(2020, 8, 31))]
        )
        payload = {"cash_dividends": [{"symbol": "OLD", "ex_date": "2020-08-31", "rate": 0.1}]}
        [action] = parse_corporate_actions(payload, resolver.resolve).actions
        assert action.security_id == "SEC_A"

    def test_unresolved_actions_are_reported(self) -> None:
        parsed = parse_corporate_actions(
            _json("corporate_actions.json"), ListingResolver(LISTINGS[1:]).resolve
        )
        assert {symbol for symbol, _ in parsed.unresolved} == {"AAPL"}

    @pytest.mark.parametrize(
        ("row", "match"),
        [
            ({"old_rate": 0, "new_rate": 4}, "positive"),
            ({"old_rate": 1, "new_rate": 10**400}, "malformed|finite"),
            ({"old_rate": -2, "new_rate": -1}, "positive"),
            ({"old_rate": True, "new_rate": 4}, "number"),
            ({"old_rate": "1", "new_rate": 4}, "number"),
        ],
    )
    def test_bad_split_rates_raise(
        self, resolver: ListingResolver, row: dict[str, object], match: str
    ) -> None:
        payload = {"forward_splits": [{"symbol": "AAPL", "ex_date": "2020-08-31", **row}]}
        with pytest.raises(ValueError, match=match):
            parse_corporate_actions(payload, resolver.resolve)

    @pytest.mark.parametrize("rate", [True, -0.5, float("nan"), "0.82"])
    def test_bad_dividend_rates_raise(self, resolver: ListingResolver, rate: object) -> None:
        payload = {"cash_dividends": [{"symbol": "AAPL", "ex_date": "2020-08-07", "rate": rate}]}
        with pytest.raises(ValueError):
            parse_corporate_actions(payload, resolver.resolve)

    def test_repeated_action_raises(self, resolver: ListingResolver) -> None:
        row = {"symbol": "AAPL", "ex_date": "2020-08-07", "rate": 0.82}
        with pytest.raises(ValueError, match="repeats"):
            parse_corporate_actions({"cash_dividends": [row, dict(row)]}, resolver.resolve)

    def test_category_that_is_not_a_list_raises(self, resolver: ListingResolver) -> None:
        with pytest.raises(ValueError, match="not a list"):
            parse_corporate_actions({"next_page_token": "abc"}, resolver.resolve)

    def test_malformed_action_raises_value_error(self, resolver: ListingResolver) -> None:
        with pytest.raises(ValueError, match="malformed"):
            parse_corporate_actions({"forward_splits": [{"symbol": "AAPL"}]}, resolver.resolve)


def _row(day: date, close: float) -> dict[str, object]:
    return {
        "t": f"{day.isoformat()}T04:00:00Z",
        "o": close,
        "h": close,
        "l": close,
        "c": close,
        "v": 1_000_000,
        "n": 10_000,
        "vw": close,
    }


class TestRenameLead:
    """#843 (owner decision (d) -> (b)): a renamed company's new ticker also
    resolves to it, inside the old ticker's span, on the sessions where the
    company has no bar under its own symbols in the same payload, so the
    hole between the old symbol's last bar and the cover page that shows
    the new ticker closes. Alpaca copies a renamed company's history onto
    the new symbol (#104 research: META's pre-rename bars equal FB's)."""

    FB = "0001326801"
    CHANGE = date(2022, 6, 9)  # META's first session
    COVER = date(2022, 7, 27)  # the first cover page naming META
    FB_DAYS: ClassVar[list[date]] = [date(2022, 6, 6), date(2022, 6, 7), date(2022, 6, 8)]
    META_DAYS: ClassVar[list[date]] = [
        *FB_DAYS,
        date(2022, 6, 9),
        date(2022, 6, 10),
        date(2022, 7, 26),
        date(2022, 7, 27),
    ]

    def _listings(self) -> list[dict[str, object]]:
        return [
            _listing(self.FB, "FB", START, "Class A Common Stock"),
            _listing(self.FB, "META", self.COVER, "Class A Common Stock"),
        ]

    def _payload(self, **extra: list[dict[str, object]]) -> dict[str, Any]:
        return {
            "feed": "sip",
            "bars": {
                "FB": [_row(d, 190.0 + i) for i, d in enumerate(self.FB_DAYS)],
                # Alpaca's META history repeats FB's closes before the change.
                "META": [_row(d, 190.0 + i) for i, d in enumerate(self.META_DAYS)],
                **extra,
            },
        }

    def test_the_new_symbol_closes_the_hole_from_the_old_symbols_last_bar(self) -> None:
        resolver = ListingResolver(self._listings(), rename_lead_days=400)
        parsed = parse_bars(self._payload(), resolver.resolve, resolver.lead)
        sessions = [b.session for b in parsed.bars if b.security_id == self.FB]
        assert sessions == self.META_DAYS  # each once: FB's three, then META's
        assert [b.close for b in parsed.bars][:3] == [190.0, 191.0, 192.0]
        assert set(parsed.unresolved) == {("META", d) for d in self.FB_DAYS}
        for bar in parsed.bars:
            assert bar.known_at == bar_known_at(bar.session)  # no record stamped early

    def test_without_a_lead_window_the_hole_stays(self) -> None:
        resolver = ListingResolver(self._listings())
        parsed = parse_bars(self._payload(), resolver.resolve, resolver.lead)
        assert [b.session for b in parsed.bars] == [*self.FB_DAYS, date(2022, 7, 27)]

    def test_the_lead_never_changes_what_resolve_says(self) -> None:
        # No look-ahead in the key mapping: the lead only fills sessions the
        # security has no bar on, and `resolve` is the same with or without it.
        sessions = [date(2022, m, d) for m in (5, 6, 7, 8) for d in (2, 9, 15, 27)]
        with_lead = ListingResolver(self._listings(), rename_lead_days=400)
        without = ListingResolver(self._listings())
        for ticker in ("FB", "META"):
            for session in sessions:
                assert with_lead.resolve(ticker, session) == without.resolve(ticker, session)

    def test_the_window_is_bounded_by_config(self) -> None:
        resolver = ListingResolver(self._listings(), rename_lead_days=30)
        assert resolver.lead("META", date(2022, 6, 27)) == self.FB
        assert resolver.lead("META", date(2022, 6, 24)) is None  # 33 days before
        assert resolver.lead("META", self.COVER) is None  # META's own span from here

    def test_the_lead_is_fetched_held_and_kept_by_the_repair(self) -> None:
        resolver = ListingResolver(self._listings(), rename_lead_days=400)
        assert resolver.symbols(self.FB, date(2022, 6, 1), date(2022, 6, 30)) == ["FB", "META"]
        assert resolver.symbols(self.FB, date(2020, 6, 1), date(2020, 6, 30)) == ["FB"]
        assert resolver.holds(self.FB, date(2022, 6, 10))  # repair-resolution keeps it

    def test_a_contested_holder_of_the_new_ticker_never_blocks_the_lead(self) -> None:
        # Roundhill's META ETF (a span the rename contests) resolves to
        # nothing; Alpaca's META history there is Facebook's.
        resolver = ListingResolver(
            [*self._listings(), _listing("SEC_ROUNDHILL", "META", date(2021, 6, 30))],
            rename_lead_days=400,
        )
        assert resolver.lead("META", date(2022, 6, 10)) == self.FB
        assert resolver.lead("META", date(2021, 9, 1)) == self.FB  # FB has bars: never used

    def test_an_old_ticker_reused_at_once_takes_no_lead(self) -> None:
        # PROG -> PRG while the new Aaron's lists AAN the same day: AAN's
        # rows resolve to Aaron's, and the lead steps aside (#869's case;
        # code-review on #893: a stale AAN bar on PROG must stay repairable).
        prog, aarons = "0001808834", "0001821393"
        resolver = ListingResolver(
            [
                _listing(prog, "AAN", START, "Common Stock"),
                _listing(prog, "PRG", date(2021, 2, 25), "Common Stock"),
                _listing(aarons, "AAN", date(2020, 12, 1), "Common Stock"),
            ],
            rename_lead_days=400,
        )
        day = date(2020, 12, 2)
        payload = {"feed": "sip", "bars": {"AAN": [_row(day, 18.41)], "PRG": [_row(day, 48.0)]}}
        parsed = parse_bars(payload, resolver.resolve, resolver.lead)
        assert {(b.security_id, b.close) for b in parsed.bars} == {(aarons, 18.41)}
        assert not resolver.holds(prog, day)
        assert resolver.lead("PRG", date(2020, 11, 30)) == prog  # before the reuse

    def test_a_placeholder_under_the_old_symbol_stops_the_lead(self) -> None:
        resolver = ListingResolver(self._listings(), rename_lead_days=400)
        day = date(2022, 6, 9)
        placeholder = {**_row(day, 192.0), "v": 0, "n": 0}
        payload = self._payload()
        payload["bars"]["FB"].append(placeholder)
        parsed = parse_bars(payload, resolver.resolve, resolver.lead)
        assert day not in [b.session for b in parsed.bars]
        assert (self.FB, day) in parsed.placeholders
        assert ("META", day) in parsed.unresolved

    def test_the_repair_judges_actions_without_the_lead(self) -> None:
        # quant-auditor and code-review on #893: actions take no lead. Here
        # OLD is contested (another company renames into it later), so only
        # the lead holds the renamed company's gap sessions.
        from tradepartner.repair import misattributed

        sid, other = "0000000003", "0000000004"
        resolver = ListingResolver(
            [
                _listing(sid, "OLD", START, "Common Stock"),
                _listing(sid, "NEWT", date(2022, 7, 1), "Common Stock"),
                _listing(other, "XO", START, "Common Stock"),
                _listing(other, "OLD", date(2023, 1, 3), "Common Stock"),
            ],
            rename_lead_days=400,
        )
        day = date(2022, 6, 15)
        assert resolver.holds(sid, day)
        assert not resolver.holds(sid, day, lead=False)
        found = misattributed(resolver, [(sid, day, 1)], [(sid, date(2022, 6, 16), 1)])
        assert dict(found.bars) == {}
        assert dict(found.actions) == {(sid, date(2022, 6, 16)): 1}

    def test_a_span_ended_at_its_own_delisting_has_no_lead(self) -> None:
        sid = "0000000002"
        resolver = ListingResolver(
            [
                _listing(sid, "OLD", date(2018, 3, 1), "Common Stock"),
                _listing(sid, "NEWT", date(2022, 3, 1), "Common Stock"),
            ],
            _evidence(
                [],
                [_ended(sid, "OLD", "Common Stock", date(2021, 6, 1), valid_from=date(2018, 3, 1))],
            ),
            rename_lead_days=400,
        )
        assert resolver.lead("NEWT", date(2021, 9, 1)) is None

    def test_the_price_source_asks_for_both_symbols_and_fills(self) -> None:
        calls: list[list[str]] = []

        def fetch(symbols: list[str], start: date, end: date) -> dict[str, Any]:
            calls.append(symbols)
            return self._payload()

        source = AlpacaPriceSource(
            ListingResolver(self._listings(), rename_lead_days=400),
            fetch_bars=fetch,
            settings=_settings(),
        )
        bars = source.bars([self.FB], date(2022, 6, 1), date(2022, 6, 30))
        assert calls == [["FB", "META"]]
        assert [b.session for b in bars] == [d for d in self.META_DAYS if d.month == 6]


class TestSuccessorDuplicate:
    """#874: a repair successor (`<cik>@<date>`, #826) whose row duplicates
    its predecessor's (same ticker, same `valid_from`) holds the ticker;
    the pair is not ambiguous, so the ticker never resolves to nobody."""

    OLD_MATCH, IAC, SUCC = "0001575189", "0000891103", "0000891103@2020-08-10"

    def _match(self) -> list[dict[str, object]]:
        day = date(2020, 8, 10)
        return [
            _listing(self.OLD_MATCH, "MTCH", date(2019, 3, 1), "Common Stock"),
            _listing(self.IAC, "IAC", START, "Common Stock"),
            _listing(self.IAC, "MTCH", day, "Common Stock"),
            _listing(self.SUCC, "MTCH", day, "Common Stock"),
        ]

    def test_the_successor_holds_the_duplicated_ticker(self) -> None:
        resolver = ListingResolver(self._match())
        for session in (date(2020, 8, 12), date(2022, 9, 1), date(2026, 9, 1)):
            assert resolver.resolve("MTCH", session) == self.SUCC, session
            assert resolver.holds(self.SUCC, session)
            assert not resolver.holds(self.IAC, session)
        assert resolver.resolve("IAC", date(2020, 3, 2)) == self.IAC
        assert resolver.report.ambiguous_spans == 0
        assert resolver.report.successor_duplicates == 1
        assert "1 predecessor rows duplicated by a successor" in resolver.report.summary()
        assert resolver.symbols(self.SUCC, date(2020, 8, 3), date(2020, 8, 31)) == ["MTCH"]
        assert resolver.symbols(self.IAC, date(2020, 8, 3), date(2020, 8, 31)) == ["IAC"]

    def test_the_successor_id_may_carry_another_day(self) -> None:
        # CHRD: 0001486159@2021-03-08 duplicates the 2022-08-04 CHRD row.
        day = date(2022, 8, 4)
        resolver = ListingResolver(
            [
                _listing("0001486159", "OAS", START, "Common Stock"),
                _listing("0001486159", "CHRD", day, "Common Stock"),
                _listing("0001486159@2021-03-08", "OAS", date(2021, 3, 8), "Common Stock"),
                _listing("0001486159@2021-03-08", "CHRD", day, "Common Stock"),
            ]
        )
        assert resolver.resolve("CHRD", date(2024, 1, 2)) == "0001486159@2021-03-08"

    def test_a_class_predecessor_is_succeeded_too(self) -> None:
        day = date(2021, 8, 5)
        resolver = ListingResolver(
            [
                _listing("0000020520:common-stock", "FYBR", day, "Common Stock"),
                _listing("0000020520@2021-08-05-2", "FYBR", day, "Common Stock"),
            ]
        )
        assert resolver.resolve("FYBR", date(2022, 1, 3)) == "0000020520@2021-08-05-2"

    @pytest.mark.parametrize("second", [False, True])
    def test_a_successor_of_a_successor_holds_the_ticker(self, second: bool) -> None:
        # code-review on #886: a second relisting succeeds the first successor.
        day = date(2022, 1, 3)
        newer = "0000000001@2021-06-01-2" if second else "0000000001@2022-01-03"
        older = "0000000001@2021-06-01" if second else "0000000001@2018-01-02"
        resolver = ListingResolver(
            [
                _listing(older, "CCC", day, "Common Stock"),
                _listing(newer, "CCC", day, "Common Stock"),
            ]
        )
        assert resolver.resolve("CCC", date(2023, 1, 3)) == newer
        assert resolver.report.successor_duplicates == 1

    @pytest.mark.parametrize("order", [1, -1])
    def test_a_disputed_predecessor_never_outwaits_its_successor(self, order: int) -> None:
        # quant-auditor and code-review on #886: both rows claim a live
        # holder's ticker; once the holder moves on, the successor holds it.
        holder, pred, succ = "0000000009", "0000000005", "0000000005@2020-08-10"
        day, moved = date(2020, 8, 10), date(2021, 3, 1)
        rows = [
            _listing(holder, "XX", date(2010, 1, 4), "Common Stock"),
            _listing(holder, "YY", moved, "Common Stock"),
            _listing(pred, "PP", date(2010, 1, 4), "Common Stock"),
            *[_listing(sid, "XX", day, "Common Stock") for sid in (pred, succ)[::order]],
        ]
        facts = [
            _shares(holder, date(2022, 6, 30), 100.0, date(2022, 8, 1)),
            _shares(pred, date(2022, 6, 30), 50.0, date(2022, 8, 1)),
        ]
        resolver = ListingResolver(rows, _evidence(facts))
        assert resolver.resolve("XX", date(2020, 9, 1)) is None  # the holder's wait
        assert resolver.resolve("XX", date(2022, 1, 3)) == succ
        assert resolver.report.successor_duplicates == 1

    @pytest.mark.parametrize(
        "other",
        [
            "0000000002@2021-08-05",
            "0000020520:x@2021-08-05",
            "0000020520@21-08-05",
            "0000020520@2021-08-05x",
        ],
    )
    def test_anything_else_on_the_same_day_stays_ambiguous(self, other: str) -> None:
        day = date(2021, 8, 5)
        resolver = ListingResolver(
            [
                _listing("0000020520", "FYBR", day, "Common Stock"),
                _listing(other, "FYBR", day, "Common Stock"),
            ]
        )
        assert resolver.resolve("FYBR", date(2022, 1, 3)) is None
        assert resolver.report.ambiguous_spans == 2
        assert resolver.report.successor_duplicates == 0

    def test_the_successor_rule_never_changes_an_earlier_mapping(self) -> None:
        # No look-ahead: the successor and the duplicate land on one day, and
        # every session before it keeps its mapping (old Match Group's own
        # MTCH left out: IAC's rename into MTCH contests it, the documented
        # exception).
        rows = self._match()[1:]
        cut = date(2020, 8, 10)
        full = ListingResolver(rows)
        early = ListingResolver([r for r in rows if r["valid_from"] < cut])  # type: ignore[operator]
        sessions = [date(y, m, 1) for y in range(2016, 2021) for m in range(1, 13)]
        for ticker in ("MTCH", "IAC"):
            for session in (s for s in sessions if s < cut):
                assert full.resolve(ticker, session) == early.resolve(ticker, session)


class TestUnreadableTickers:
    """#844: a cover page that puts junk in the trading-symbol field (an
    exchange name, a footnote marker) never ends the company's span."""

    VMC = "0001396009"

    def _vmc(self) -> list[dict[str, object]]:
        return [
            _listing(self.VMC, "VMC", date(2019, 7, 29), "Common Stock"),
            _listing(self.VMC, "New York Stock Exchange", date(2020, 11, 6), "Common Stock"),
            _listing(self.VMC, "VMC", date(2021, 2, 25), "Common Stock"),
            _listing(self.VMC, "New York Stock Exchange", date(2021, 5, 5), "Common Stock"),
            _listing(self.VMC, "VMC", date(2022, 2, 25), "Common Stock"),
        ]

    def test_an_exchange_name_in_the_ticker_field_keeps_the_span(self) -> None:
        resolver = ListingResolver(self._vmc())
        for session in (date(2020, 11, 6), date(2020, 12, 1), date(2021, 6, 1), date(2022, 3, 1)):
            assert resolver.resolve("VMC", session) == self.VMC, session
            assert resolver.holds(self.VMC, session)
        assert resolver.resolve("New York Stock Exchange", date(2020, 12, 1)) is None
        assert resolver.symbols(self.VMC, date(2020, 1, 2), date(2022, 6, 1)) == ["VMC"]
        assert resolver.report.unreadable == 2
        assert "2 unreadable-ticker listings ignored" in resolver.report.summary()

    @pytest.mark.parametrize("junk", ["VAL*", "BAX (NYSE)", "LCINQ (1)", "UPH(1)"])
    def test_a_footnote_marker_is_the_same_ticker(self, junk: str) -> None:
        held = alpaca_symbol(junk)
        assert held is not None
        resolver = ListingResolver(
            [
                _listing("0000000001", held, date(2019, 7, 29), "Common Stock"),
                _listing("0000000001", junk, date(2020, 10, 29), "Common Stock"),
            ]
        )
        assert resolver.resolve(held, date(2021, 1, 4)) == "0000000001"
        assert resolver.symbols("0000000001", START, date(2021, 1, 4)) == [held]
        assert resolver.report.unreadable == 0  # read as the symbol, not kept as junk

    def test_a_junk_row_on_a_rename_day_never_costs_the_new_ticker(self) -> None:
        day = date(2022, 6, 9)
        resolver = ListingResolver(
            [
                _listing("0000000001", "OLD", START, "Common Stock"),
                _listing("0000000001", "NEWT", day, "Common Stock"),
                _listing("0000000001", "New York Stock Exchange", day, "Common Stock"),
            ]
        )
        assert resolver.resolve("NEWT", date(2022, 7, 1)) == "0000000001"
        assert resolver.resolve("OLD", date(2022, 7, 1)) is None
        assert resolver.report.same_day_securities == 0
        assert resolver.report.unreadable == 1

    def test_a_junk_row_after_a_placeholder_still_holds_nothing(self) -> None:
        # Only a readable equity ticker just before is carried on: after a
        # placeholder row (no ticker listed) the junk row is left as it was.
        resolver = ListingResolver(
            [
                _listing("0000000001", "AAA", START, "Common Stock"),
                _listing("0000000001", "N/A", date(2020, 1, 2), "Common Stock"),
                _listing("0000000001", "New York Stock Exchange", date(2021, 1, 4), "Common Stock"),
            ]
        )
        assert resolver.resolve("AAA", date(2021, 6, 1)) is None
        assert resolver.report.unreadable == 0

    def test_a_security_listing_only_junk_is_known_and_never_sent(self) -> None:
        resolver = ListingResolver(
            [_listing("0000000001", "New York Stock Exchange", START, "Common Stock")]
        )
        assert resolver.knows("0000000001")
        assert resolver.report.unreadable == 0

    def test_a_delisted_junk_last_row_still_ends_the_span(self) -> None:
        # quant-auditor on #863: the Form 25 lands on the junk row's listing,
        # keyed under the junk string; rule 7 must still cut the span.
        junk_day = date(2021, 1, 4)
        resolver = ListingResolver(
            [
                _listing("0000000001", "XYZ", date(2019, 1, 2), "Common Stock"),
                _listing("0000000001", "New York Stock Exchange", junk_day, "Common Stock"),
            ],
            _evidence(
                [],
                [
                    _ended(
                        "0000000001",
                        "New York Stock Exchange",
                        "Common Stock",
                        date(2021, 3, 1),
                        valid_from=junk_day,
                    )
                ],
            ),
        )
        assert resolver.resolve("XYZ", date(2021, 2, 1)) == "0000000001"
        assert resolver.resolve("XYZ", date(2022, 6, 1)) is None
        assert resolver.report.ended_spans == 1

    def test_a_junk_row_never_carries_a_same_day_typo_on(self) -> None:
        # code-review on #863: Ford's typo day lists F and FF; a later junk
        # row must be read as F, never as FutureFuel's FF.
        resolver = ListingResolver(
            [
                _listing("ff", "FF", date(2019, 1, 2), "Common Stock"),
                _listing("ford", "F", date(2020, 1, 2), "Common Stock"),
                _listing("ford", "F", date(2020, 4, 1), "Common Stock"),
                _listing("ford", "FF", date(2020, 4, 1), "Common Stock"),
                _listing("ford", "New York Stock Exchange", date(2020, 7, 1), "Common Stock"),
            ]
        )
        assert resolver.resolve("FF", date(2020, 8, 3)) == "ff"
        assert resolver.resolve("F", date(2020, 8, 3)) == "ford"
        assert (resolver.report.unreadable, resolver.report.same_day_typos) == (1, 1)

    def test_a_dropped_junk_row_is_counted_once(self) -> None:
        day = date(2022, 6, 9)
        resolver = ListingResolver(
            [
                _listing("0000000001", "OLD", START, "Common Stock"),
                _listing("0000000001", "NEWT", day, "Common Stock"),
                _listing("0000000001", "New York Stock Exchange", day, "Common Stock"),
            ]
        )
        assert (resolver.report.unreadable, resolver.report.same_day_typos) == (1, 0)

    def test_a_junk_row_never_changes_an_earlier_mapping(self) -> None:
        # No look-ahead: a junk row, and every row after it, change nothing
        # before its day; nor does it hand the ticker to another company.
        cut = date(2020, 11, 6)
        rows = [
            *self._vmc(),
            _listing("0000000009", "VMC", date(2016, 3, 1), "Common Stock"),
        ]
        full = ListingResolver(rows)
        early = ListingResolver([r for r in rows if r["valid_from"] < cut])  # type: ignore[operator]
        sessions = [date(y, m, 1) for y in range(2016, 2023) for m in range(1, 13)]
        for session in (s for s in sessions if s < cut):
            assert full.resolve("VMC", session) == early.resolve("VMC", session), session
        for session in (s for s in sessions if s >= date(2019, 7, 29)):
            assert full.resolve("VMC", session) == self.VMC, session


class _Recorded:
    """Fetchers returning the recorded payloads, and remembering the calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], date, date]] = []

    def bars(self, symbols: list[str], start: date, end: date) -> dict[str, Any]:
        self.calls.append(("bars", symbols, start, end))
        return _json("daily_bars.json")

    def actions(self, symbols: list[str], start: date, end: date) -> Any:
        # Like Alpaca, the window filters on process_date, not ex_date (#101).
        self.calls.append(("actions", symbols, start, end))
        return {
            category: [
                row for row in rows if start <= date.fromisoformat(row["process_date"]) <= end
            ]
            for category, rows in _json("corporate_actions.json").items()
        }


def _settings(**alpaca: object) -> Settings:
    return Settings(_env_file=None, alpaca=alpaca)  # type: ignore[call-arg]


class TestAlpacaPriceSource:
    def _source(
        self, recorded: _Recorded, listings: list[dict[str, object]] = LISTINGS, **alpaca: object
    ) -> AlpacaPriceSource:
        return AlpacaPriceSource(
            ListingResolver(listings),
            fetch_bars=recorded.bars,
            fetch_actions=recorded.actions,
            settings=_settings(**alpaca),
        )

    def test_actions_processed_after_the_window_are_kept(self) -> None:
        # MSFT's dividend goes ex 2020-08-19 but is processed 2020-09-10.
        recorded = _Recorded()
        actions = self._source(recorded).corporate_actions(
            ["SEC_MSFT"], date(2020, 8, 1), date(2020, 8, 31)
        )
        assert [(a.security_id, a.ex_date) for a in actions] == [("SEC_MSFT", date(2020, 8, 19))]
        assert recorded.calls[-1][3] == date(2020, 11, 29)  # end + 90 days
        # With no lag the process-date window misses it: the defect the lag fixes.
        unpadded = self._source(_Recorded(), actions_process_lag_days=0)
        assert unpadded.corporate_actions(["SEC_MSFT"], date(2020, 8, 1), date(2020, 8, 31)) == []

    def test_actions_fetch_the_symbol_held_the_session_before_start(self) -> None:
        listings = [_listing("SEC_A", "OLD", START), _listing("SEC_A", "NEW", date(2020, 8, 31))]
        recorded = _Recorded()
        self._source(recorded, listings).corporate_actions(
            ["SEC_A"], date(2020, 8, 31), date(2020, 9, 30)
        )
        assert recorded.calls[-1][1] == ["NEW", "OLD"]

    def test_actions_window_is_padded_on_both_sides(self) -> None:
        recorded = _Recorded()
        self._source(recorded).corporate_actions(["SEC_MSFT"], date(2020, 8, 1), date(2020, 8, 31))
        assert recorded.calls[-1][2:] == (date(2020, 5, 3), date(2020, 11, 29))

    def test_reports_reset_on_every_call(self) -> None:
        recorded = _Recorded()
        source = self._source(recorded)
        source.bars(["SEC_AAPL"], date(2020, 8, 3), date(2020, 9, 30))
        assert source.last_bars_report is not None
        assert source.bars([], date(2020, 8, 3), date(2020, 9, 30)) == []
        assert source.last_bars_report is None
        source.corporate_actions(["SEC_AAPL"], date(2020, 8, 3), date(2020, 9, 30))
        assert source.last_actions_report is not None
        with pytest.raises(UnknownSecurityIdError):
            source.corporate_actions(["NOPE"], date(2020, 8, 3), date(2020, 9, 30))
        assert source.last_actions_report is None

    def test_reports_of_the_last_call_are_kept(self) -> None:
        recorded = _Recorded()
        source = self._source(
            recorded, listings=[*LISTINGS[1:], _listing("SEC_AAPL", "AAPL", date(2020, 9, 1))]
        )
        source.bars(["SEC_AAPL"], date(2020, 8, 3), date(2020, 9, 30))
        assert source.last_bars_report is not None
        assert ("AAPL", date(2020, 8, 31)) in source.last_bars_report.unresolved
        source.corporate_actions(["SEC_AAPL"], date(2020, 9, 1), date(2020, 9, 30))
        assert source.last_actions_report is not None

    def test_bars_by_security_id_in_range(self) -> None:
        recorded = _Recorded()
        bars = self._source(recorded).bars(["SEC_AAPL"], date(2020, 8, 27), date(2020, 8, 31))
        assert [(b.security_id, b.session) for b in bars] == [
            ("SEC_AAPL", date(2020, 8, 27)),
            ("SEC_AAPL", date(2020, 8, 28)),
            ("SEC_AAPL", date(2020, 8, 31)),
        ]
        assert recorded.calls == [("bars", ["AAPL"], date(2020, 8, 27), date(2020, 8, 31))]

    def test_actions_by_security_id_in_range(self) -> None:
        recorded = _Recorded()
        actions = self._source(recorded).corporate_actions(
            ["SEC_AAPL", "SEC_MSFT"], date(2020, 8, 1), date(2020, 8, 31)
        )
        assert [(a.security_id, a.action_type.value, a.ex_date) for a in actions] == [
            ("SEC_AAPL", "dividend", date(2020, 8, 7)),
            ("SEC_AAPL", "split", date(2020, 8, 31)),
            ("SEC_MSFT", "dividend", date(2020, 8, 19)),
        ]

    def test_unknown_id_raises(self) -> None:
        with pytest.raises(UnknownSecurityIdError):
            self._source(_Recorded()).bars(["AAPL"], date(2020, 8, 3), date(2020, 8, 31))

    def test_empty_ids_fetch_nothing(self) -> None:
        recorded = _Recorded()
        assert self._source(recorded).bars([], date(2020, 8, 3), date(2020, 8, 31)) == []
        assert recorded.calls == []

    def test_bare_string_ids_raise(self) -> None:
        with pytest.raises(TypeError):
            self._source(_Recorded()).bars("SEC_AAPL", date(2020, 8, 3), date(2020, 8, 31))

    def test_no_record_is_stamped_early(self) -> None:
        recorded = _Recorded()
        source = self._source(recorded)
        ids = [row["security_id"] for row in LISTINGS]
        for bar in source.bars(ids, date(2020, 8, 3), date(2020, 9, 30)):
            assert bar.known_at == bar_known_at(bar.session)
        for action in source.corporate_actions(ids, date(2020, 8, 3), date(2020, 9, 30)):
            assert action.known_at == action_first_seen_known_at(action.ex_date)


class TestAlpacaSymbols:
    """#737: a master ticker goes to Alpaca only in Alpaca's symbol form.

    alpaca-py comma-joins every symbol into one `symbols=` parameter of one
    request and raises `APIError` for the whole call on any error status, so
    one invalid symbol is taken to fail the whole chunk: such a ticker is
    never sent. Its security gets no bars under it, never another's."""

    @pytest.mark.parametrize(
        ("ticker", "symbol"),
        [
            ("AAPL", "AAPL"),
            ("BRK.B", "BRK.B"),
            ("NKTX ", "NKTX"),
            ('"""CDTX"""', "CDTX"),
            ("'XOM'", "XOM"),
            ("Caap", "CAAP"),
            ("LEDs", "LEDS"),
            ("CRD-A", "CRD.A"),
            ("GEF-B", "GEF.B"),
            ("BRK/B", "BRK.B"),
            ("crd-a", "CRD.A"),
            ("BF.B", "BF.B"),
            ("bf-b", "BF.B"),
            # #844: a footnote marker or an exchange in brackets after it.
            ("VAL*", "VAL"),
            ("DNR*", "DNR"),
            ("CVIAQ*", "CVIAQ"),
            ("BAX (NYSE)", "BAX"),
            ("UPH(1)", "UPH"),
            ("LCINQ (1)", "LCINQ"),
            ("QTEKQ (1) ", "QTEKQ"),
            ("ABC (Nasdaq GS)", "ABC"),
            ("ABC (NYSE American)", "ABC"),
        ],
    )
    def test_safe_spellings_become_the_alpaca_symbol(self, ticker: str, symbol: str) -> None:
        assert alpaca_symbol(ticker) == symbol

    @pytest.mark.parametrize(
        "ticker",
        [
            "New York Stock Exchange",
            "Trading SymbolSLP",
            "*",
            "(NYSE)",
            # quant-auditor on #863: a bracketed class or series is no footnote.
            "HEI (A)",
            "BRK (A)",
            "GOOG (Class C)",
            "BAC (Series L)",
            "XYZ (Pfd)",
            "F (2029)",  # code-review on #863: a year is no footnote
            "F&G",
            "C/28",
            "CUBI/PC",
            "AAPL,MSFT",
            "",
            "  ",
            "-",
            "1234",
            "BRK..B",
            "BRK.",
            ".B",
            "CRD-A-B",
            "\ufb01t",  # the ligature 'fi' upper-cases to 'FIT'
            "\u00c9CO",
        ],
    )
    def test_anything_else_is_not_an_alpaca_symbol(self, ticker: str) -> None:
        assert alpaca_symbol(ticker) is None

    @pytest.mark.parametrize(
        "ticker", ["C27C", "PG25", "PCAR26", "DE22B", "PTN1", "CK0000731288", "ABC.B1", "AB1-C"]
    )
    def test_a_ticker_with_a_digit_is_not_an_alpaca_symbol(self, ticker: str) -> None:
        # #792: from 2019-08 cover pages bring 510 tickers with digits (notes
        # such as Citi's C27C); Alpaca rejected every one alone, none had bars.
        assert alpaca_symbol(ticker) is None

    def test_a_note_ticker_is_never_sent_and_is_named(self) -> None:
        listings = [
            _listing("SEC_AAPL", "AAPL", START),
            _listing("SEC_C_NOTE", "C27C", START),
            _listing("SEC_BRK", "BRK.B", START),
        ]
        calls: list[list[str]] = []

        def fetch(symbols: list[str], start: date, end: date) -> dict[str, Any]:
            calls.append(symbols)
            return self._bars_under({"BRK.B": "KO"})

        source = AlpacaPriceSource(
            ListingResolver(listings), fetch_bars=fetch, settings=_settings()
        )
        bars = source.bars(
            ["SEC_AAPL", "SEC_C_NOTE", "SEC_BRK"], date(2020, 8, 3), date(2020, 8, 7)
        )
        assert calls == [["AAPL", "BRK.B"]]
        assert {b.security_id for b in bars} == {"SEC_AAPL", "SEC_BRK"}
        assert source.last_excluded_symbols == ("C27C",)
        assert "'C27C'" in source.symbol_summary()

    @staticmethod
    def _bars_under(symbols: dict[str, str]) -> dict[str, Any]:
        """The recorded payload with each `{new: recorded}` symbol's rows
        served under `new` as well."""
        payload = _json("daily_bars.json")
        for new, recorded in symbols.items():
            payload["bars"][new] = copy.deepcopy(payload["bars"][recorded])
        return payload

    def test_an_invalid_ticker_is_never_sent_and_is_named(self) -> None:
        listings = [
            _listing("SEC_AAPL", "AAPL", START),
            _listing("SEC_CRD", "CRD-A", START),
            _listing("SEC_FG", "F&G", START),
        ]
        calls: list[list[str]] = []

        def fetch(symbols: list[str], start: date, end: date) -> dict[str, Any]:
            calls.append(symbols)
            return self._bars_under({"CRD.A": "KO"})

        source = AlpacaPriceSource(
            ListingResolver(listings), fetch_bars=fetch, settings=_settings()
        )
        bars = source.bars(["SEC_AAPL", "SEC_CRD", "SEC_FG"], date(2020, 8, 3), date(2020, 8, 7))
        assert calls == [["AAPL", "CRD.A"]]
        assert {b.security_id for b in bars} == {"SEC_AAPL", "SEC_CRD"}
        crd = [b for b in bars if b.security_id == "SEC_CRD"]
        assert len(crd) == 5 and crd[0].close == _json("daily_bars.json")["bars"]["KO"][0]["c"]
        assert source.last_excluded_symbols == ("F&G",)
        assert "1 master ticker(s) not sent to Alpaca, not Alpaca symbols: 'F&G'" in (
            source.symbol_summary()
        )

    def test_only_invalid_tickers_fetch_nothing(self) -> None:
        recorded = _Recorded()
        source = AlpacaPriceSource(
            ListingResolver([_listing("SEC_X", "New York Stock Exchange", START)]),
            fetch_bars=recorded.bars,
            fetch_actions=recorded.actions,
            settings=_settings(),
        )
        assert source.bars(["SEC_X"], date(2020, 8, 3), date(2020, 8, 7)) == []
        assert source.corporate_actions(["SEC_X"], date(2020, 8, 3), date(2020, 8, 7)) == []
        assert recorded.calls == []
        assert source.last_excluded_symbols == ("New York Stock Exchange",)

    def test_actions_are_asked_and_resolved_under_the_alpaca_symbol(self) -> None:
        listings = [_listing("SEC_CRD", "CRD-A", START)]
        calls: list[list[str]] = []

        def fetch(symbols: list[str], start: date, end: date) -> dict[str, Any]:
            calls.append(symbols)
            row = {**_json("corporate_actions.json")["cash_dividends"][0], "symbol": "CRD.A"}
            return {"cash_dividends": [row]}

        source = AlpacaPriceSource(
            ListingResolver(listings), fetch_actions=fetch, settings=_settings()
        )
        actions = source.corporate_actions(["SEC_CRD"], date(2020, 8, 3), date(2020, 8, 31))
        assert calls == [["CRD.A"]]
        assert [(a.security_id, a.ex_date) for a in actions] == [("SEC_CRD", date(2020, 8, 7))]

    def test_two_spellings_are_one_ticker_to_the_resolver(self) -> None:
        # 'CRD-A' and 'CRD.A' are one Alpaca symbol, so one ticker: the
        # newer listing takes it, as for any reused ticker.
        listings = [
            _listing("SEC_OLD", "CRD-A", START),
            _listing("SEC_NEW", "CRD.A", date(2020, 1, 2)),
        ]

        def fetch(symbols: list[str], start: date, end: date) -> dict[str, Any]:
            return self._bars_under({"CRD.A": "KO"})

        source = AlpacaPriceSource(
            ListingResolver(listings), fetch_bars=fetch, settings=_settings()
        )
        bars = source.bars(["SEC_OLD", "SEC_NEW"], date(2020, 8, 3), date(2020, 8, 7))
        assert {b.security_id for b in bars} == {"SEC_NEW"}

    def test_a_spelling_variant_is_still_contested(self) -> None:
        # quant-auditor pass 1 on #760: X held 'META ' (trailing space) before
        # FB renamed into META. Alpaca serves FB's history under META on X's
        # dates; they must stay unassigned, as they do for X spelled 'META'.
        for spelling in ("META", "META ", "meta"):
            listings = [
                _listing("SEC_X", spelling, START),
                _listing("SEC_X", "XNEW", date(2020, 9, 1)),
                _listing("SEC_FB", "FB", START),
                _listing("SEC_FB", "META", date(2020, 10, 1)),
            ]

            def fetch(symbols: list[str], start: date, end: date) -> dict[str, Any]:
                return self._bars_under({"META": "KO"})

            source = AlpacaPriceSource(
                ListingResolver(listings), fetch_bars=fetch, settings=_settings()
            )
            assert source.bars(["SEC_X"], date(2020, 8, 3), date(2020, 8, 7)) == [], spelling
            assert source.last_bars_report is not None
            assert ("META", date(2020, 8, 3)) in source.last_bars_report.unresolved

    def test_two_spellings_of_one_security_resolve_to_it(self) -> None:
        listings = [
            _listing("SEC_CAAP", "Caap", START),
            _listing("SEC_CAAP", "CAAP", date(2020, 8, 5)),
        ]
        calls: list[list[str]] = []

        def fetch(symbols: list[str], start: date, end: date) -> dict[str, Any]:
            calls.append(symbols)
            return self._bars_under({"CAAP": "KO"})

        source = AlpacaPriceSource(
            ListingResolver(listings), fetch_bars=fetch, settings=_settings()
        )
        bars = source.bars(["SEC_CAAP"], date(2020, 8, 3), date(2020, 8, 7))
        assert calls == [["CAAP"]]
        assert [b.session.day for b in bars] == [3, 4, 5, 6, 7]
        assert {b.security_id for b in bars} == {"SEC_CAAP"}

    def test_two_spellings_from_one_day_resolve_to_nothing(self) -> None:
        # code-review on #760: two securities whose spellings are one symbol
        # from the same day are ambiguous: their rows go to nobody, and the
        # chunk goes on rather than raising.
        listings = [_listing("SEC_A", "Caap", START), _listing("SEC_B", "CAAP", START)]

        def fetch(symbols: list[str], start: date, end: date) -> dict[str, Any]:
            return self._bars_under({"CAAP": "KO"})

        source = AlpacaPriceSource(
            ListingResolver(listings), fetch_bars=fetch, settings=_settings()
        )
        assert source.bars(["SEC_A", "SEC_B"], date(2020, 8, 3), date(2020, 8, 7)) == []
        assert source.last_bars_report is not None
        assert ("CAAP", date(2020, 8, 3)) in source.last_bars_report.unresolved

    def test_a_placeholder_word_never_joins_a_real_symbol(self) -> None:
        # 'true' is a placeholder (#735) and keeps its spelling: upper-casing
        # it would make it TrueCar's TRUE.
        listings = [
            _listing("SEC_TRUE", "TRUE", START),
            _listing("SEC_P", "true", date(2020, 1, 2)),
        ]
        resolver = ListingResolver(listings)
        assert resolver.resolve("TRUE", date(2020, 8, 3)) == "SEC_TRUE"
        assert resolver.symbols("SEC_P", START, date(2020, 8, 7)) == []

    def test_the_run_line_names_the_tickers_not_sent_last(self) -> None:
        listings = [_listing("SEC_AAPL", "AAPL", START), _listing("SEC_FG", "F&G", START)]
        source = AlpacaPriceSource(
            ListingResolver(listings), fetch_bars=_Recorded().bars, settings=_settings()
        )
        source.bars(["SEC_AAPL", "SEC_FG"], date(2020, 8, 3), date(2020, 8, 7))
        assert source.resolution_summary().endswith(
            "; 1 master ticker(s) not sent to Alpaca, not Alpaca symbols: 'F&G'"
        )

    def test_a_valid_ticker_alone_names_no_exclusion(self) -> None:
        source = AlpacaPriceSource(
            ListingResolver(LISTINGS), fetch_bars=_Recorded().bars, settings=_settings()
        )
        source.bars(["SEC_AAPL"], date(2020, 8, 3), date(2020, 8, 7))
        assert source.last_excluded_symbols == ()
        assert source.symbol_summary() == ""


class TestSameDayTypoRule:
    """#846: the rule `ListingResolver` uses to tell a cover-page typo
    from a genuine same-day pair, shared with `health._overlapping_listings`
    so the two can never drift."""

    def test_the_held_ticker_in_the_pair_is_a_typo(self) -> None:
        assert is_same_day_typo("FF", {"F", "FF"}) is True

    def test_neither_ticker_held_is_a_genuine_pair(self) -> None:
        assert is_same_day_typo("FF", {"F", "FFX"}) is False

    def test_no_ticker_held_before_is_a_genuine_pair(self) -> None:
        assert is_same_day_typo(None, {"F", "FF"}) is False


class TestSameAlpacaSymbolFold:
    """#846: MOTV U / MOTV.U is one Alpaca symbol under two filer
    spellings of a unit's one-letter suffix, the same fold
    `alpaca_symbol` already does for `-` and `/` (`CRD-A`, `CRD.A`)."""

    @pytest.mark.parametrize(
        ("one", "other"),
        [
            ("MOTV U", "MOTV.U"),
            ("MOTV.U", "MOTV U"),
            ("BRK B", "BRK.B"),
            ("crd a", "CRD.A"),
            ("BAX (NYSE)", "BAX"),  # #844: a bracketed exchange after the symbol
        ],
    )
    def test_a_space_class_suffix_is_the_dot_spelling(self, one: str, other: str) -> None:
        assert same_alpaca_symbol(one, other) is True

    @pytest.mark.parametrize(
        ("one", "other"),
        [("HACAR", "HCACR"), ("CLCR", "CLRC"), ("F", "FF"), ("BAX (NYSE)", "BAXX")],
    )
    def test_different_tickers_are_not_one_symbol(self, one: str, other: str) -> None:
        assert same_alpaca_symbol(one, other) is False
