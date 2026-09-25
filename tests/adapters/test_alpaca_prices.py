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
from typing import Any

import pytest

from tradepartner.adapters.alpaca_prices import (
    AlpacaPriceSource,
    ListingResolver,
    feed_source,
    parse_bars,
    parse_corporate_actions,
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


def _listing(security_id: str, ticker: str, valid_from: date) -> dict[str, object]:
    return {"security_id": security_id, "ticker": ticker, "valid_from": valid_from}


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

    def test_same_ticker_same_start_is_ambiguous(self) -> None:
        resolver = ListingResolver([_listing("A", "DUP", START), _listing("B", "DUP", START)])
        with pytest.raises(ValueError, match="ambiguous"):
            resolver.resolve("DUP", date(2020, 1, 2))

    def test_second_exchange_listing_same_ticker_is_one_span(self) -> None:
        resolver = ListingResolver(
            [_listing("SEC_X", "XX", START), _listing("SEC_X", "XX", date(2019, 5, 1))]
        )
        assert resolver.resolve("XX", date(2020, 1, 2)) == "SEC_X"
        assert resolver.symbols("SEC_X", START, date(2020, 1, 2)) == ["XX"]

    def test_one_security_two_tickers_same_day_raises(self) -> None:
        with pytest.raises(ValueError, match="same day"):
            ListingResolver([_listing("SEC_X", "AA", START), _listing("SEC_X", "BB", START)])

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
