"""Tests for `survivorship_gap` (spec "Survivorship gap"; plan T15).

The hand-computed case is a small synthetic store over June 2019: T is the
close of 2019-06-28, the previous rebalance session 2019-05-31, so W is the
June sessions. The fixture-store cases use the fixture README's delistings.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner.calendar import next_session, session_close
from tradepartner.config import Settings
from tradepartner.gap import SurvivorshipGap, survivorship_gap
from tradepartner.store import schema
from tradepartner.store.db import configure_connection, insert_row

T_JUNE = session_close(date(2019, 6, 28))
_KNOWN = datetime(2019, 1, 2, 21, 0, tzinfo=UTC)


def _settings(**gap: Any) -> Settings:
    return Settings(_env_file=None, gap=gap)


def _meta(known_at: datetime, source: str, provenance: str) -> dict[str, Any]:
    return {
        "known_at": known_at,
        "ingested_at": known_at,
        "source": source,
        "provenance": provenance,
    }


def _filing(known_at: datetime = _KNOWN) -> dict[str, Any]:
    return _meta(known_at, "edgar", "filing")


def _sessions(first: date, last: date) -> list[date]:
    out, day = [], first
    while day <= last:
        out.append(day)
        day = next_session(day)
    return out


class _Store:
    """An empty store plus helpers to add one synthetic name at a time."""

    def __init__(self) -> None:
        self.conn = duckdb.connect(":memory:")
        configure_connection(self.conn)
        schema.init_schema(self.conn)

    def security(
        self,
        sid: str,
        *,
        security_type: str = "common",
        benchmark: bool = False,
        bars: tuple[date, date] | None = None,
        close: float = 10.0,
        shares: float | None = None,
        exchange: str = "NYSE",
    ) -> None:
        insert_row(
            self.conn,
            "securities",
            {"security_id": sid, "cik": f"CIK_{sid}", "name": sid, "benchmark": benchmark}
            | _filing(),
        )
        insert_row(
            self.conn,
            "classifications",
            {"security_id": sid, "sic": 7372, "security_type": security_type, "rule": "test"}
            | _filing(),
        )
        self.listing(sid, exchange, date(2019, 1, 2), _KNOWN)
        if bars is not None:
            for session in _sessions(*bars):
                insert_row(
                    self.conn,
                    "prices_daily",
                    {
                        "security_id": sid,
                        "session": session,
                        "open": close,
                        "high": close,
                        "low": close,
                        "close": close,
                        "volume": 1_000_000,
                    }
                    | _meta(session_close(session), "alpaca", "bar"),
                )
        if shares is not None:
            insert_row(
                self.conn,
                "facts",
                {
                    "security_id": sid,
                    "fact_name": "shares_outstanding",
                    "as_of_date": date(2019, 3, 31),
                    "class_member": "",
                    "value": shares,
                    "filing_accession": "",
                }
                | _filing(datetime(2019, 4, 15, 20, 30, tzinfo=UTC)),
            )

    def listing(self, sid: str, exchange: str, valid_from: date, known_at: datetime) -> None:
        insert_row(
            self.conn,
            "listings",
            {
                "security_id": sid,
                "ticker": sid,
                "exchange": exchange,
                "class_title": "Common Stock",
                "valid_from": valid_from,
            }
            | _filing(known_at),
        )

    def form_25(self, sid: str, filed: date, exchange: str = "NYSE") -> None:
        filed_at = datetime(filed.year, filed.month, filed.day, 20, 30, tzinfo=UTC)
        insert_row(
            self.conn,
            "delistings",
            {
                "security_id": sid,
                "form": "25",
                "class_title": "Common Stock",
                "exchange": exchange,
                "filed_at": filed_at,
                "effective_on": filed + timedelta(days=10),
            }
            | _filing(filed_at),
        )


def _as_store(conn: duckdb.DuckDBPyConnection) -> _Store:
    """A `_Store` over an existing connection, to add names to it."""
    store = _Store.__new__(_Store)
    store.conn = conn
    return store


@pytest.fixture
def june() -> Iterator[duckdb.DuckDBPyConnection]:
    """The hand-computed store. Value = shares x last raw close.

    | id | case | in L | in M | value |
    |---|---|---|---|---|
    | A | survivor with a bar at T | yes | no | 1,000 x 10 |
    | B | listed, last bar 06-26, none at T | yes | no_bar_at_t | 500 x 20 |
    | C | clean delisting: Form 25 06-14, last bar 06-13 | yes | no | 100 x 30 |
    | D | Form 25 06-21, last bar 06-07: 9 sessions before 06-20 | yes | truncated | 200 x 5 |
    | E | Form 25 06-21, last bar 06-13: exactly 5 sessions | yes | no | 100 x 10 |
    | F | Form 25 05-15, before W | no | - | - |
    | G | benchmark | no | - | - |
    | H | ETF | no | - | - |
    | I | listed, never a bar | yes | no_bar_at_t | 1,000 x none = 0 |
    | J | transferred NYSE -> NASDAQ inside W, bars continue | yes | no | 100 x 10 |

    L = 7 names, M = {B, D, I}: count 3/7, size 11,000 / 26,000.
    """
    store = _Store()
    s = store.security
    s("A", bars=(date(2019, 5, 1), date(2019, 6, 28)), close=10.0, shares=1_000)
    s("B", bars=(date(2019, 5, 1), date(2019, 6, 26)), close=20.0, shares=500)
    s("C", bars=(date(2019, 5, 1), date(2019, 6, 13)), close=30.0, shares=100)
    store.form_25("C", date(2019, 6, 14))
    s("D", bars=(date(2019, 5, 1), date(2019, 6, 7)), close=5.0, shares=200)
    store.form_25("D", date(2019, 6, 21))
    s("E", bars=(date(2019, 5, 1), date(2019, 6, 13)), close=10.0, shares=100)
    store.form_25("E", date(2019, 6, 21))
    s("F", bars=(date(2019, 5, 1), date(2019, 5, 1)), close=10.0, shares=100)
    store.form_25("F", date(2019, 5, 15))
    s("G", benchmark=True, bars=(date(2019, 5, 1), date(2019, 6, 26)), shares=100)
    s("H", security_type="etf", bars=(date(2019, 5, 1), date(2019, 6, 26)), shares=100)
    s("I", shares=1_000)
    s("J", bars=(date(2019, 5, 1), date(2019, 6, 28)), close=10.0, shares=100)
    store.form_25("J", date(2019, 6, 10))
    store.listing("J", "NASDAQ", date(2019, 6, 12), datetime(2019, 6, 12, 20, 30, tzinfo=UTC))
    try:
        yield store.conn
    finally:
        store.conn.close()


def _missing(gap: SurvivorshipGap) -> dict[str, str]:
    return {r["security_id"]: r["reason"] for r in gap.missing.iter_rows(named=True)}


def test_hand_computed_count_and_size_share(june: duckdb.DuckDBPyConnection) -> None:
    gap = survivorship_gap(june, T_JUNE, _settings())
    assert gap.session == date(2019, 6, 28)
    assert gap.previous_rebalance == date(2019, 5, 31)
    assert set(gap.listed) == {"A", "B", "C", "D", "E", "I", "J"}
    assert _missing(gap) == {"B": "no_bar_at_t", "D": "truncated_tail", "I": "no_bar_at_t"}
    assert gap.count_share == pytest.approx(3 / 7)
    assert gap.size_share == pytest.approx(11_000 / 26_000)


def test_missing_rows_carry_the_evidence(june: duckdb.DuckDBPyConnection) -> None:
    rows = {
        r["security_id"]: r for r in survivorship_gap(june, T_JUNE).missing.iter_rows(named=True)
    }
    assert rows["D"]["last_bar"] == date(2019, 6, 7)
    assert rows["D"]["reference_session"] == date(2019, 6, 20)
    assert rows["D"]["tail_sessions"] == 9
    assert rows["D"]["value"] == pytest.approx(1_000)
    assert rows["I"]["last_bar"] is None
    assert rows["I"]["value"] == 0.0


def test_off_universe_exchange_listing_is_outside_l(june: duckdb.DuckDBPyConnection) -> None:
    # An OTC common name with no bars is not the population the gap bounds.
    _Store.security(_as_store(june), "K", exchange="OTC", shares=1_000)
    gap = survivorship_gap(june, T_JUNE, _settings())
    assert "K" not in gap.listed
    assert gap.count_share == pytest.approx(3 / 7)


def test_unclassifiable_counts_only_names_listed_in_w(june: duckdb.DuckDBPyConnection) -> None:
    store = _as_store(june)
    store.security("U1", security_type="unclassifiable", bars=(date(2019, 5, 1), date(2019, 6, 28)))
    store.security("U2", security_type="unclassifiable", bars=(date(2019, 5, 1), date(2019, 5, 1)))
    store.form_25("U2", date(2019, 5, 15))
    gap = survivorship_gap(june, T_JUNE, _settings())
    assert "U1" in gap.unclassifiable
    assert "U2" not in gap.unclassifiable
    assert not {"U1", "U2"} & set(gap.listed)


def test_zero_volume_tail_does_not_hide_a_truncated_tail(
    june: duckdb.DuckDBPyConnection,
) -> None:
    # D last traded on 06-07. Halt markers through 06-20 do not repair its
    # nine-session tail before the Form 25. A later-known traded revision
    # changes the verdict only after it becomes known.
    for session in _sessions(date(2019, 6, 10), date(2019, 6, 20)):
        insert_row(
            june,
            "prices_daily",
            {
                "security_id": "D",
                "session": session,
                "open": 5.0,
                "high": 5.0,
                "low": 5.0,
                "close": 5.0,
                "volume": 0,
            }
            | _meta(session_close(session), "alpaca", "bar"),
        )
    late = T_JUNE + timedelta(hours=1)
    insert_row(
        june,
        "prices_daily",
        {
            "security_id": "D",
            "session": date(2019, 6, 20),
            "open": 5.0,
            "high": 5.0,
            "low": 5.0,
            "close": 5.0,
            "volume": 1_000_000,
        }
        | _meta(late, "alpaca", "bar"),
    )
    at_close = survivorship_gap(june, T_JUNE, _settings())
    row = at_close.missing.filter(at_close.missing["security_id"] == "D").row(0, named=True)
    assert (row["reason"], row["last_bar"], row["tail_sessions"]) == (
        "truncated_tail",
        date(2019, 6, 7),
        9,
    )
    assert "D" not in _missing(survivorship_gap(june, late, _settings()))


def _bar_at_t(conn: duckdb.DuckDBPyConnection, sid: str, volume: int, known_at: datetime) -> None:
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": sid,
            "session": date(2019, 6, 28),
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": volume,
        }
        | _meta(known_at, "alpaca", "bar"),
    )


def test_a_zero_volume_bar_at_t_is_no_bar_at_t(june: duckdb.DuckDBPyConnection) -> None:
    # Z traded through 06-27; its only bar at T is a halt marker (volume 0), so a
    # live listing has no trade at T (#801 1a). A traded revision of that bar
    # clears it, but only from the instant the revision is known.
    _as_store(june).security("Z", bars=(date(2019, 5, 1), date(2019, 6, 27)), shares=100)
    _bar_at_t(june, "Z", 0, T_JUNE)
    late = T_JUNE + timedelta(hours=1)
    _bar_at_t(june, "Z", 1_000_000, late)
    at_close = survivorship_gap(june, T_JUNE, _settings())
    row = at_close.missing.filter(at_close.missing["security_id"] == "Z").row(0, named=True)
    assert (row["reason"], row["last_bar"]) == ("no_bar_at_t", date(2019, 6, 27))
    assert "Z" not in _missing(survivorship_gap(june, late, _settings()))


def test_boundary_tail_equal_to_the_threshold_is_not_missing(
    june: duckdb.DuckDBPyConnection,
) -> None:
    assert "E" not in _missing(survivorship_gap(june, T_JUNE, _settings()))
    assert _missing(survivorship_gap(june, T_JUNE, _settings(missing_tail_sessions=4)))["E"] == (
        "truncated_tail"
    )


def test_override_settings_change_the_result_without_the_environment(
    june: duckdb.DuckDBPyConnection,
) -> None:
    env_before = dict(os.environ)
    loose = survivorship_gap(june, T_JUNE, _settings(missing_tail_sessions=9))
    assert dict(os.environ) == env_before
    assert "D" not in _missing(loose)
    assert loose.count_share == pytest.approx(2 / 7)
    assert loose.settings["gap"]["missing_tail_sessions"] == 9


def test_explicit_previous_rebalance_sets_the_window(june: duckdb.DuckDBPyConnection) -> None:
    # W = (06-14, 06-28]: C's Form 25 (06-14) is before W, so C is not in L.
    gap = survivorship_gap(june, T_JUNE, previous_rebalance=date(2019, 6, 14))
    assert gap.previous_rebalance == date(2019, 6, 14)
    assert "C" not in gap.listed
    assert {"D", "E"} <= set(gap.listed)


def test_previous_rebalance_must_be_an_earlier_session(june: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(ValueError, match="previous_rebalance"):
        survivorship_gap(june, T_JUNE, previous_rebalance=date(2019, 6, 28))
    with pytest.raises(ValueError, match="previous_rebalance"):
        survivorship_gap(june, T_JUNE, previous_rebalance=date(2019, 6, 15))  # a Saturday


def test_mid_month_t_defaults_to_the_last_month_end(june: duckdb.DuckDBPyConnection) -> None:
    gap = survivorship_gap(june, session_close(date(2019, 6, 19)))
    assert gap.previous_rebalance == date(2019, 5, 31)


def test_empty_store_reports_zero_shares() -> None:
    store = _Store()
    try:
        gap = survivorship_gap(store.conn, T_JUNE)
    finally:
        store.conn.close()
    assert gap.listed == ()
    assert gap.count_share == 0.0
    assert gap.size_share == 0.0


def test_a_bare_date_is_refused(june: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(TypeError):
        survivorship_gap(june, date(2019, 6, 28))  # type: ignore[arg-type]


# --- the fixture universe (tests/fixtures/universe/README.md) ---


def test_truncated_history_delisting_is_missing(fixture_store: duckdb.DuckDBPyConnection) -> None:
    # TRHX: last bar 2018-05-25, Form 25 2018-06-14: 12 sessions > 5.
    gap = survivorship_gap(fixture_store, session_close(date(2018, 6, 29)))
    assert "SEC_TRUNC_DELIST" in gap.listed
    assert _missing(gap)["SEC_TRUNC_DELIST"] == "truncated_tail"
    row = gap.missing.filter(gap.missing["security_id"] == "SEC_TRUNC_DELIST").row(0, named=True)
    assert row["tail_sessions"] == 12


def test_clean_merger_delisting_is_not_missing(fixture_store: duckdb.DuckDBPyConnection) -> None:
    gap = survivorship_gap(fixture_store, session_close(date(2018, 7, 31)))
    assert "SEC_CLEAN_MERGER" in gap.listed
    assert "SEC_CLEAN_MERGER" not in _missing(gap)


def test_boundary_delisting_is_not_missing(fixture_store: duckdb.DuckDBPyConnection) -> None:
    gap = survivorship_gap(fixture_store, session_close(date(2018, 10, 31)))
    assert "SEC_BOUNDARY_DELIST" in gap.listed
    assert "SEC_BOUNDARY_DELIST" not in _missing(gap)


def test_transfer_is_listed_and_not_missing(fixture_store: duckdb.DuckDBPyConnection) -> None:
    gap = survivorship_gap(fixture_store, session_close(date(2018, 10, 31)))
    assert "SEC_TRANSFER" in gap.listed
    assert "SEC_TRANSFER" not in _missing(gap)


def test_a_form_25_not_yet_known_leaves_the_name_listed_and_missing(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # WNDX: last bar 2019-06-24; its Form 25 is accepted 2019-06-28 20:30
    # UTC, after that session's close. At the close it is still listed with
    # no bar at T; once the filing is known it is a clean delisting.
    at_close = survivorship_gap(fixture_store, session_close(date(2019, 6, 28)))
    assert _missing(at_close)["SEC_WINDOW_DELIST"] == "no_bar_at_t"
    after = survivorship_gap(fixture_store, datetime(2019, 6, 28, 21, 0, tzinfo=UTC))
    assert "SEC_WINDOW_DELIST" in after.listed
    assert "SEC_WINDOW_DELIST" not in _missing(after)


def test_benchmarks_and_non_common_are_outside_the_denominator(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    gap = survivorship_gap(fixture_store, session_close(date(2019, 6, 28)))
    assert not {"SEC_SPY", "SEC_MTUM", "SEC_DUAL_PFD", "SEC_UNCLASSIFIABLE"} & set(gap.listed)
    assert {"SEC_DUAL_A", "SEC_DUAL_B"} <= set(gap.listed)


def test_side_categories_are_reported_separately(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    late = survivorship_gap(fixture_store, session_close(date(2019, 6, 28)))
    assert "SEC_UNCLASSIFIABLE" in late.unclassifiable
    # STLF has bars only from 2019-02-01: under 12 months at T (rule 6).
    assert "SEC_FACTS_STALE" in late.truncated_history
    stale = survivorship_gap(fixture_store, session_close(date(2020, 4, 6)))
    assert "SEC_FACTS_STALE" in stale.stale_shares
    for gap in (late, stale):
        side = set(gap.unclassifiable) | set(gap.truncated_history) | set(gap.stale_shares)
        assert not side & set(_missing(gap))


# --- stale listings (ADR 0003 amendment #1199; spec acceptance criterion) -----

#: S's last bar: before W (June 2019); Good Friday and Memorial Day fall after it.
_S_LAST = date(2019, 3, 29)
#: Sessions after S's last bar up to T's session, counted on the XNYS calendar.
_S_DARK = len(_sessions(next_session(_S_LAST), date(2019, 6, 28)))


def _bar(conn: duckdb.DuckDBPyConnection, sid: str, session: date, known_at: datetime) -> None:
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": sid,
            "session": session,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 1_000_000,
        }
        | _meta(known_at, "alpaca", "bar"),
    )


@pytest.fixture
def dark() -> Iterator[duckdb.DuckDBPyConnection]:
    """June 2019 again (W = the June sessions), with long-dark names.

    | id | case |
    |---|---|
    | A | survivor with a bar at T |
    | B | listed, last bar 06-26 (inside W), none at T |
    | I | listed, never a bar |
    | S | listed, last bar 03-29 (before W), `_S_DARK` sessions dark at T |
    | K | as S, plus its NYSE listing ended by a Form 25 filed 06-14 (inside W)
    |   | and a NASDAQ listing from 06-25 (not a transfer: 7 sessions later) |
    """
    store = _Store()
    s = store.security
    s("A", bars=(date(2019, 1, 2), date(2019, 6, 28)), shares=1_000)
    s("B", bars=(date(2019, 1, 2), date(2019, 6, 26)), shares=100)
    s("I", shares=100)
    s("S", bars=(date(2019, 1, 2), _S_LAST), shares=100)
    s("K", bars=(date(2019, 1, 2), _S_LAST), shares=100)
    store.form_25("K", date(2019, 6, 14))
    store.listing("K", "NASDAQ", date(2019, 6, 25), datetime(2019, 6, 25, 20, 30, tzinfo=UTC))
    try:
        yield store.conn
    finally:
        store.conn.close()


def _stale(gap: SurvivorshipGap) -> dict[str, tuple[date, int]]:
    return {
        r["security_id"]: (r["last_bar"], r["dark_sessions"])
        for r in gap.stale_listings.iter_rows(named=True)
    }


def test_the_default_is_63_sessions() -> None:
    assert Settings(_env_file=None).gap.stale_listing_sessions == 63


def test_a_stale_listing_leaves_l_and_m_and_is_reported(dark: duckdb.DuckDBPyConnection) -> None:
    gap = survivorship_gap(dark, T_JUNE, _settings(stale_listing_sessions=_S_DARK - 1))
    assert _stale(gap) == {"S": (_S_LAST, _S_DARK)}
    assert "S" not in gap.listed and "S" not in _missing(gap)
    side = set(gap.unclassifiable) | set(gap.truncated_history) | set(gap.stale_shares)
    assert "S" not in side
    assert gap.listed == ("A", "B", "I", "K")
    assert _missing(gap) == {"B": "no_bar_at_t", "I": "no_bar_at_t", "K": "truncated_tail"}
    assert gap.count_share == pytest.approx(3 / 4)
    assert gap.settings["gap"]["stale_listing_sessions"] == _S_DARK - 1


def test_dark_exactly_the_key_is_still_no_bar_at_t(dark: duckdb.DuckDBPyConnection) -> None:
    gap = survivorship_gap(dark, T_JUNE, _settings(stale_listing_sessions=_S_DARK))
    assert gap.stale_listings.is_empty()
    assert _missing(gap)["S"] == "no_bar_at_t"


def test_the_dark_count_is_in_sessions_not_days(dark: duckdb.DuckDBPyConnection) -> None:
    # 03-29 to 06-28 is 91 days and two weekday holidays (Good Friday, Memorial Day).
    weekdays = sum(
        1
        for n in range(1, (date(2019, 6, 28) - _S_LAST).days + 1)
        if (_S_LAST + timedelta(days=n)).weekday() < 5
    )
    assert weekdays - 2 == _S_DARK
    for key, stale in ((_S_DARK - 1, True), (_S_DARK, False)):
        gap = survivorship_gap(dark, T_JUNE, _settings(stale_listing_sessions=key))
        assert ("S" in _stale(gap)) is stale


def test_a_key_below_w_never_makes_a_death_inside_w_stale(
    dark: duckdb.DuckDBPyConnection,
) -> None:
    # B is dark 1 session at T, its last bar inside W: never stale, whatever the key.
    gap = survivorship_gap(dark, T_JUNE, _settings(stale_listing_sessions=0))
    assert set(_stale(gap)) == {"S"}
    assert _missing(gap)["B"] == "no_bar_at_t"


def test_a_listing_with_no_bar_at_all_stays_no_bar_at_t(dark: duckdb.DuckDBPyConnection) -> None:
    gap = survivorship_gap(dark, T_JUNE, _settings(stale_listing_sessions=0))
    assert _missing(gap)["I"] == "no_bar_at_t"
    assert "I" not in _stale(gap)


def test_a_form_25_inside_w_keeps_a_stale_security_in_l_as_truncated_tail(
    dark: duckdb.DuckDBPyConnection,
) -> None:
    gap = survivorship_gap(dark, T_JUNE, _settings(stale_listing_sessions=0))
    assert "K" in gap.listed and "K" not in _stale(gap)
    row = gap.missing.filter(pl.col("security_id") == "K").row(0, named=True)
    assert (row["reason"], row["last_bar"], row["reference_session"]) == (
        "truncated_tail",
        _S_LAST,
        date(2019, 6, 13),
    )


def test_the_delisted_path_is_unchanged(june: duckdb.DuckDBPyConnection) -> None:
    before = survivorship_gap(june, T_JUNE, _settings(stale_listing_sessions=10**6))
    after = survivorship_gap(june, T_JUNE, _settings(stale_listing_sessions=0))
    assert after.missing.equals(before.missing) and after.listed == before.listed
    assert after.stale_listings.is_empty()


def test_a_stale_name_with_a_new_bar_is_live_again(dark: duckdb.DuckDBPyConnection) -> None:
    settings = _settings(stale_listing_sessions=_S_DARK - 1)
    _bar(dark, "S", date(2019, 7, 15), session_close(date(2019, 7, 15)))
    at_bar = survivorship_gap(dark, session_close(date(2019, 7, 15)), settings)
    assert "S" in at_bar.listed and "S" not in _missing(at_bar)
    later = survivorship_gap(dark, session_close(date(2019, 7, 31)), settings)
    assert _missing(later)["S"] == "no_bar_at_t" and "S" not in _stale(later)
    assert "K" in _stale(later)  # its Form 25 (06-14) is before July's W now


def test_a_bar_known_after_t_does_not_make_a_stale_name_live(
    dark: duckdb.DuckDBPyConnection,
) -> None:
    settings = _settings(stale_listing_sessions=_S_DARK - 1)
    # A June bar first known in July, and a bar on a session after T's.
    _bar(dark, "S", date(2019, 6, 10), datetime(2019, 7, 2, 20, 30, tzinfo=UTC))
    _bar(dark, "S", date(2019, 7, 1), session_close(date(2019, 7, 1)))
    gap = survivorship_gap(dark, T_JUNE, settings)
    assert _stale(gap) == {"S": (_S_LAST, _S_DARK)}
    assert "S" not in gap.listed


def test_an_older_open_ended_row_does_not_keep_a_stale_security(
    dark: duckdb.DuckDBPyConnection,
) -> None:
    # A ticker change leaves S's first row `listed` and open-ended beside the new one.
    insert_row(
        dark,
        "listings",
        {
            "security_id": "S",
            "ticker": "S2",
            "exchange": "NYSE",
            "class_title": "Common Stock",
            "valid_from": date(2019, 2, 1),
        }
        | _filing(datetime(2019, 2, 1, 20, 30, tzinfo=UTC)),
    )
    gap = survivorship_gap(dark, T_JUNE, _settings(stale_listing_sessions=_S_DARK - 1))
    assert _stale(gap) == {"S": (_S_LAST, _S_DARK)}
    assert "S" not in gap.listed


def test_a_stale_unclassifiable_name_leaves_its_side_category(
    dark: duckdb.DuckDBPyConnection,
) -> None:
    _as_store(dark).security("U", security_type="unclassifiable", bars=(date(2019, 1, 2), _S_LAST))
    live = survivorship_gap(dark, T_JUNE, _settings(stale_listing_sessions=_S_DARK))
    assert "U" in live.unclassifiable and "U" not in _stale(live)
    stale = survivorship_gap(dark, T_JUNE, _settings(stale_listing_sessions=_S_DARK - 1))
    assert "U" not in stale.unclassifiable
    assert _stale(stale)["U"] == (_S_LAST, _S_DARK)
