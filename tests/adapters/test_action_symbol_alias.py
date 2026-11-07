"""#3054: a corporate action filed under a security's pre-rename symbol.

Alpaca's bars follow a renamed company back through its history, but an action
row carries the symbol of its day. When the master's only ticker evidence before
a rename is the new ticker back-dated (a `snapshot_static` row or a first-span
lead), the old symbol was never asked and the action never stored (UBSH -> AUB
2019-05-20, HRS -> LHX 2019-07-01: every dividend before the change missing).

A stored Alpaca name change `old -> new` on P is the evidence: the security's
actions under `old` on sessions before P are its own, while no other security
resolves `old` there and the security is held under `new` that session.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

from tradepartner.adapters.alpaca_prices import (
    AlpacaPriceSource,
    ListingResolver,
    parse_corporate_actions,
)
from tradepartner.adapters.prices import action_first_seen_known_at
from tradepartner.config import Settings

AUB = "0000883948"
OTHER = "0000000002"
START = date(2016, 1, 4)
RENAME = date(2019, 5, 20)  # UBSH -> AUB


def _settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def _listing(security_id: str, ticker: str, valid_from: date) -> dict[str, object]:
    return {
        "security_id": security_id,
        "ticker": ticker,
        "valid_from": valid_from,
        "class_title": None,
    }


def _change(security_id: str, old: str, new: str, process_date: date) -> dict[str, object]:
    return {
        "security_id": security_id,
        "old_symbol": old,
        "new_symbol": new,
        "process_date": process_date,
    }


def _backdated() -> ListingResolver:
    """AUB listed from 2016 under its current ticker (a back-dated snapshot row),
    with Alpaca's UBSH -> AUB change stored."""
    return ListingResolver(
        [_listing(AUB, "AUB", START)], symbol_changes=[_change(AUB, "UBSH", "AUB", RENAME)]
    )


def _dividend(symbol: str, ex_date: str, rate: float, action_id: str) -> dict[str, object]:
    return {"id": action_id, "symbol": symbol, "ex_date": ex_date, "rate": rate}


class TestActionAlias:
    def test_the_old_symbol_resolves_to_the_renamed_security_before_the_change(self) -> None:
        resolver = _backdated()
        assert resolver.resolve("UBSH", date(2016, 2, 9)) is None  # the master never lists it
        assert resolver.action_alias("UBSH", date(2016, 2, 9)) == AUB
        assert resolver.action_alias("UBSH", date(2019, 5, 17)) == AUB

    def test_never_on_or_after_the_change(self) -> None:
        resolver = _backdated()
        assert resolver.action_alias("UBSH", RENAME) is None
        assert resolver.action_alias("UBSH", date(2020, 3, 2)) is None

    def test_never_while_another_security_resolves_the_old_symbol(self) -> None:
        resolver = ListingResolver(
            [_listing(AUB, "AUB", START), _listing(OTHER, "UBSH", date(2017, 1, 3))],
            symbol_changes=[_change(AUB, "UBSH", "AUB", RENAME)],
        )
        assert resolver.action_alias("UBSH", date(2016, 6, 1)) == AUB
        assert resolver.action_alias("UBSH", date(2018, 6, 1)) is None  # OTHER holds it

    def test_never_where_the_security_is_not_held_under_the_new_symbol(self) -> None:
        # The master shows AUB under a third ticker before 2018: that span is
        # the evidence there, not the change.
        resolver = ListingResolver(
            [_listing(AUB, "THIRD", START), _listing(AUB, "AUB", date(2018, 1, 2))],
            symbol_changes=[_change(AUB, "UBSH", "AUB", RENAME)],
        )
        assert resolver.action_alias("UBSH", date(2017, 6, 1)) is None
        assert resolver.action_alias("UBSH", date(2018, 6, 1)) == AUB

    def test_the_nearest_later_change_names_the_symbol(self) -> None:
        resolver = ListingResolver(
            [_listing(AUB, "AUB", START)],
            symbol_changes=[
                _change(AUB, "FIRST", "UBSH", date(2017, 3, 1)),
                _change(AUB, "UBSH", "AUB", RENAME),
            ],
        )
        # Before 2017-03-01 the security traded as FIRST, not UBSH.
        assert resolver.action_alias("FIRST", date(2016, 6, 1)) == AUB
        assert resolver.action_alias("UBSH", date(2016, 6, 1)) is None
        assert resolver.action_alias("UBSH", date(2018, 6, 1)) == AUB
        assert resolver.action_alias("FIRST", date(2018, 6, 1)) is None
        assert resolver.alias_symbols(AUB, date(2016, 6, 1), date(2016, 6, 30)) == [
            "FIRST",
            "UBSH",
        ]

    def test_a_cusip_only_change_is_no_alias(self) -> None:
        resolver = ListingResolver(
            [_listing(AUB, "AUB", START)], symbol_changes=[_change(AUB, "AUB", "AUB", RENAME)]
        )
        assert resolver.alias_symbols(AUB, START, RENAME) == []

    def test_alias_symbols_name_the_old_symbol_for_windows_before_the_change(self) -> None:
        resolver = _backdated()
        assert resolver.alias_symbols(AUB, date(2016, 2, 1), date(2016, 2, 29)) == ["UBSH"]
        assert resolver.alias_symbols(AUB, date(2019, 5, 1), date(2019, 5, 31)) == ["UBSH"]
        assert resolver.alias_symbols(AUB, date(2019, 6, 1), date(2019, 6, 30)) == []

    def test_holds_for_actions_counts_an_aliased_session(self) -> None:
        # repair's misattribution check must keep what the alias assigns.
        assert _backdated().holds(AUB, date(2016, 2, 8), actions=True)


class TestSourceAsksTheOldSymbol:
    def test_a_dividend_under_the_old_symbol_lands_on_the_renamed_security(self) -> None:
        asked: list[list[str]] = []

        def fetch(symbols: list[str], _start: date, _end: date) -> dict[str, object]:
            asked.append(list(symbols))
            return {
                "cash_dividends": [
                    _dividend("UBSH", "2016-02-10", 0.19, "ubsh-q1"),
                    # Another company's row under the reused symbol after the
                    # change: never the renamed security's.
                    _dividend("UBSH", "2019-06-12", 0.25, "later-holder"),
                ]
            }

        source = AlpacaPriceSource(_backdated(), fetch_actions=fetch, settings=_settings())
        actions = source.corporate_actions([AUB], date(2016, 2, 1), date(2016, 2, 29))
        assert asked == [["AUB", "UBSH"]]
        assert [(a.security_id, a.ex_date, a.ratio_or_amount) for a in actions] == [
            (AUB, date(2016, 2, 10), 0.19)
        ]
        # The timing rule is the one every Alpaca action has: no earlier.
        assert actions[0].known_at == action_first_seen_known_at(date(2016, 2, 10))
        later = source.corporate_actions([AUB], date(2019, 6, 1), date(2019, 6, 30))
        assert later == []

    def test_without_the_stored_change_nothing_lands(self) -> None:
        source = AlpacaPriceSource(
            ListingResolver([_listing(AUB, "AUB", START)]),
            fetch_actions=lambda _s, _a, _b: {
                "cash_dividends": [_dividend("UBSH", "2016-02-10", 0.19, "ubsh-q1")]
            },
            settings=_settings(),
        )
        assert source.corporate_actions([AUB], date(2016, 2, 1), date(2016, 2, 29)) == []


class TestNameChangeOfABackdatedTicker:
    """The name change itself: its old symbol resolves to no one, because the
    master back-dates the new ticker; the new ticker's holder on the session
    before the change is the renamed security."""

    ROW: ClassVar[dict[str, str]] = {
        "id": "alpaca-ubsh-aub",
        "old_symbol": "UBSH",
        "new_symbol": "AUB",
        "old_cusip": "90539J109",
        "new_cusip": "04911A107",
        "process_date": "2019-05-20",
    }

    def test_it_is_stored_under_the_new_symbols_holder(self) -> None:
        resolver = ListingResolver([_listing(AUB, "AUB", START)])
        parsed = parse_corporate_actions({"name_changes": [self.ROW]}, resolver.resolve)
        [change] = parsed.symbol_changes
        assert (change.security_id, change.old_symbol, change.new_symbol) == (AUB, "UBSH", "AUB")
        assert parsed.symbol_change_backdated == 1
        assert parsed.unresolved == ()

    def test_a_new_ticker_first_listed_on_the_change_day_stays_unresolved(self) -> None:
        resolver = ListingResolver([_listing(AUB, "AUB", RENAME)])
        parsed = parse_corporate_actions({"name_changes": [self.ROW]}, resolver.resolve)
        assert parsed.symbol_changes == ()
        assert parsed.unresolved == (("UBSH", RENAME),)
        assert parsed.symbol_change_backdated == 0
