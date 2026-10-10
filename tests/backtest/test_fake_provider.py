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


def test_sessions_from_bounds_the_frame_after_the_as_of_read() -> None:
    """T99: the fake honours the bound as the store does, after the latest-revision
    selection, and records it on the call."""
    provider = _provider()
    feb = provider.adjusted_prices(T_FEB, ["A"], include_dividends=False, sessions_from=FEB)
    assert feb.select("session", "close").rows() == [(FEB, 11.0)]
    jan = provider.adjusted_prices(T_FEB, ["A"], include_dividends=False, sessions_from=JAN)
    assert jan.select("session", "close").rows() == [(JAN, 5.0), (FEB, 11.0)]
    assert [call.sessions_from for call in provider.calls] == [FEB, JAN]
    with pytest.raises(TypeError, match="sessions_from"):
        provider.adjusted_prices(T_FEB, ["A"], include_dividends=False, sessions_from=T_FEB)


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


def test_a_revised_dividend_first_known_earlier_is_not_late() -> None:
    """Late means first known in (t_prev, t] (spec req 5), not merely revised there."""
    first_known = session_close(date(2024, 1, 12))
    revised = session_close(date(2024, 2, 5))
    dividends = pl.DataFrame(
        {
            "security_id": ["A", "A"],
            "ex_date": [date(2024, 1, 10), date(2024, 1, 10)],
            "ratio_or_amount": [0.5, 0.55],
            "known_at": [first_known, revised],
        }
    )
    provider = FakeProvider(
        prices=_provider().prices, members={}, benchmarks={}, dividends=dividends
    )
    assert provider.late_dividends(T_JAN, T_FEB, ["A"]).is_empty()
    # Seen from a window that contains the first sighting, it is late, at its latest revision.
    late = provider.late_dividends(session_close(date(2024, 1, 11)), T_FEB, ["A"])
    assert late.select("ex_date", "ratio_or_amount").rows() == [(date(2024, 1, 10), 0.55)]


def test_two_revisions_with_the_same_known_at_are_refused() -> None:
    prices = pl.DataFrame(
        {
            "security_id": ["A", "A"],
            "session": [JAN, JAN],
            "open": [1.0, 2.0],
            "close": [1.0, 2.0],
            "known_at": [T_JAN, T_JAN],
        }
    )
    provider = FakeProvider(prices=prices, members={}, benchmarks={})
    with pytest.raises(ValueError, match="same known_at"):
        provider.adjusted_prices(T_FEB, ["A"], include_dividends=False)


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


# --- the turnover read (T165b) ---------------------------------------------------------


def _turnover_provider() -> FakeProvider:
    jan2, jan3 = date(2024, 1, 2), date(2024, 1, 3)
    raw = pl.DataFrame(
        {
            "security_id": ["A", "A", "A", "B", "B"],
            "session": [jan2, jan3, JAN, jan2, JAN],
            "close": [10.0, 10.0, 10.0, 20.0, 20.0],
            "volume": [100, 200, 300, 0, 50],  # B's 2024-01-02 bar did not trade
            "known_at": [T_JAN, T_JAN, T_JAN, T_JAN, T_FEB],  # B's Jan 31 bar is late
        }
    )
    shares = pl.DataFrame(
        {
            "security_id": ["A", "A", "A", "B"],
            "as_of_date": [date(2023, 10, 1), date(2024, 1, 15), date(2024, 1, 15), JAN],
            "value": [1_000.0, 1_100.0, 1_200.0, 500.0],
            # A's 2024-01-15 fact is restated after January's close; B's is filed then.
            "known_at": [T_JAN, T_JAN, T_FEB, T_FEB],
        }
    )
    splits = pl.DataFrame(
        {
            "security_id": ["A", "A", "B", "B"],
            "action_id": ["s1", "s1", "s2", "s2"],
            # A's split is re-dated once known; B's is cancelled after February's close.
            "ex_date": [date(2024, 1, 10), date(2024, 1, 12), date(2024, 2, 5), date(2024, 2, 5)],
            "ratio": [2.0, 2.0, 3.0, 3.0],
            "cancelled": [False, False, False, True],
            "known_at": [T_JAN - timedelta(days=30), T_JAN, T_FEB, T_FEB + timedelta(days=1)],
        }
    )
    return FakeProvider(
        prices=raw.drop("volume"),
        raw=raw,
        members={},
        benchmarks={},
        shares_rows=shares,
        split_rows=splits,
    )


def test_turnover_inputs_read_only_rows_known_at_t() -> None:
    provider = _turnover_provider()
    jan = provider.turnover_inputs(T_JAN, ["A", "B"], date(2024, 1, 1))
    feb = provider.turnover_inputs(T_FEB, ["A", "B"], date(2024, 1, 1))
    # The zero-volume bar is a missing row; B's late bar is absent at January's close.
    assert jan.bars.select("security_id", "session", "volume").rows() == [
        ("A", date(2024, 1, 2), 100),
        ("A", date(2024, 1, 3), 200),
        ("A", JAN, 300),
    ]
    assert feb.bars.filter(pl.col("security_id") == "B")["session"].to_list() == [JAN]
    # The latest as_of_date's latest revision known at t; B has no pick in January.
    assert dict(jan.shares) == {"A": (date(2024, 1, 15), 1_100.0)}
    assert dict(feb.shares) == {"A": (date(2024, 1, 15), 1_200.0), "B": (JAN, 500.0)}
    # One split per identity, at its latest known ex-date; none before it is known.
    assert dict(jan.splits) == {"A": ((date(2024, 1, 12), 2.0),)}
    assert dict(feb.splits) == {"A": ((date(2024, 1, 12), 2.0),), "B": ((date(2024, 2, 5), 3.0),)}
    later = provider.turnover_inputs(T_FEB + timedelta(days=2), ["B"], date(2024, 2, 1))
    assert dict(later.splits) == {}


def test_turnover_inputs_bound_the_bars_and_are_recorded() -> None:
    provider = _turnover_provider()
    got = provider.turnover_inputs(T_JAN, ["A"], date(2024, 1, 3))
    assert got.bars["session"].to_list() == [date(2024, 1, 3), JAN]
    assert provider.calls == [
        Call("turnover_inputs", T_JAN, ids=("A",), sessions_from=date(2024, 1, 3))
    ]
    with pytest.raises(TypeError, match="sessions_from"):
        provider.turnover_inputs(T_JAN, ["A"], T_JAN)  # type: ignore[arg-type]
