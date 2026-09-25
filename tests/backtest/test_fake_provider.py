"""The in-memory `FakeProvider` honours `known_at <= t` and records every call (T37)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from backtest.fake_provider import Call, FakeProvider
from tradepartner.backtest.provider import DataProvider, GapReading
from tradepartner.calendar import session_close

JAN, FEB = date(2024, 1, 31), date(2024, 2, 29)
T_JAN, T_FEB = session_close(JAN), session_close(FEB)


def _provider() -> FakeProvider:
    prices = pl.DataFrame(
        {
            "security_id": ["A", "A", "A", "B"],
            "session": [JAN, FEB, JAN, FEB],
            "open": [10.0, 11.0, 5.0, 20.0],
            "close": [10.0, 11.0, 5.0, 20.0],
            # A's January bar is restated (halved) once February's close knows a split.
            "known_at": [T_JAN, T_FEB, T_FEB, T_FEB],
        }
    )
    dividends = pl.DataFrame(
        {
            "security_id": ["A", "A"],
            "ex_date": [date(2024, 1, 10), date(2024, 2, 10)],
            "ratio_or_amount": [0.5, 0.25],
            "known_at": [T_FEB, T_FEB],
        }
    )
    return FakeProvider(
        prices=prices,
        members={JAN: ["A"], FEB: ["A", "B"]},
        benchmarks={"SPY": "SPY_ID"},
        dividends=dividends,
        dropped=dividends.filter(pl.col("ex_date") == date(2024, 2, 10)),
        gaps={T_FEB: GapReading(count_share=0.1, size_share=0.02)},
        static_listings={"B"},
    )


def test_fake_provider_satisfies_the_protocol() -> None:
    assert isinstance(_provider(), DataProvider)


def test_prices_are_latest_revision_known_at_t() -> None:
    provider = _provider()
    jan = provider.adjusted_prices(T_JAN, ["A", "B"], include_dividends=True)
    assert jan.select("security_id", "session", "close").rows() == [("A", JAN, 10.0)]
    feb = provider.adjusted_prices(T_FEB, ["A"], include_dividends=False)
    assert feb.select("session", "close").rows() == [(JAN, 5.0), (FEB, 11.0)]


def test_universe_members_and_other_reads() -> None:
    provider = _provider()
    assert provider.universe(T_JAN).members["security_id"].to_list() == ["A"]
    assert provider.universe(T_FEB).session == FEB
    assert provider.benchmark_ids(T_FEB) == {"SPY": "SPY_ID"}
    assert provider.survivorship_gap(T_FEB) == GapReading(count_share=0.1, size_share=0.02)
    assert provider.survivorship_gap(T_JAN) == GapReading(count_share=0.0, size_share=0.0)
    assert provider.static_listing_count(T_FEB, ["A", "B"]) == 1
    assert provider.dropped_dividends(T_FEB, ["A", "B"])["security_id"].to_list() == ["A"]
    assert provider.dropped_dividends(T_JAN, ["A"]).is_empty()


def test_late_dividends_are_known_in_the_window_with_ex_date_at_or_before_t_prev() -> None:
    late = _provider().late_dividends(T_JAN, T_FEB, ["A"])
    # The 01-10 dividend was learnt at close(02-29): late. The 02-10 one is inside month.
    assert late.select("security_id", "ex_date").rows() == [("A", date(2024, 1, 10))]


def test_every_call_is_recorded_with_its_t() -> None:
    provider = _provider()
    provider.universe(T_JAN)
    provider.adjusted_prices(T_FEB, ["A"], include_dividends=True)
    provider.late_dividends(T_JAN, T_FEB, ["A"])
    assert provider.calls == [
        Call("universe", T_JAN),
        Call("adjusted_prices", T_FEB, ids=("A",), include_dividends=True),
        Call("late_dividends", T_FEB, ids=("A",), t_prev=T_JAN),
    ]
    assert provider.read_times() == {T_JAN, T_FEB}


def test_naive_or_bare_date_t_is_refused() -> None:
    provider = _provider()
    with pytest.raises(ValueError):
        provider.universe(datetime(2024, 1, 31, 21))  # noqa: DTZ001 (naive on purpose)
    with pytest.raises(TypeError):
        provider.universe(JAN)  # type: ignore[arg-type]
    assert provider.calls == []


def test_listing_ends_filter_by_known_at() -> None:
    ends = pl.DataFrame(
        {
            "security_id": ["A"],
            "status": ["delisted"],
            "end_session": [FEB],
            "known_at": [T_FEB],
        }
    )
    provider = FakeProvider(
        prices=_provider().prices, members={}, benchmarks={}, listing_ends_rows=ends
    )
    assert provider.listing_ends(T_JAN, ["A"]).is_empty()
    assert provider.listing_ends(T_FEB, ["A"])["status"].to_list() == ["delisted"]


def test_t_is_recorded_in_utc() -> None:
    provider = _provider()
    provider.universe(T_JAN.astimezone(ZoneInfo("America/New_York")))
    assert provider.calls[0].t == T_JAN
    assert provider.calls[0].t.utcoffset() == timedelta(0)
