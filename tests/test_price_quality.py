"""The price-quality gate (#787): zero-volume bars are missing, and an unexplained
one-day jump goes to the owner's review list and fails universe rule 6 until accepted.

Every case revises one fixture bar of SEC_DUAL_A (a member at `T_LATE`) inside its
12-month history window and outside its 20-session liquidity window, so only the
gate can move it. The revision is known at `REVISED_AT`, a session after the bar,
so a read before that instant must not see it (no look-ahead).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import duckdb
import polars as pl
import pytest
from lookahead.harness import TruncatedStore
from pydantic import ValidationError

from tradepartner.config import Settings, parse_accepted_jump
from tradepartner.gap import survivorship_gap
from tradepartner.health import health_report
from tradepartner.store.asof import adjusted_prices_as_of, price_jumps_as_of, prices_as_of
from tradepartner.store.db import insert_row
from tradepartner.universe import universe_as_of

SID = "SEC_DUAL_A"
JUMP = date(2019, 3, 15)
BACK = date(2019, 3, 18)  # the next session: the price returns
REVISED_AT = datetime(2019, 3, 18, 12, 0, tzinfo=UTC)
BEFORE_REVISION = datetime(2019, 3, 15, 21, 0, tzinfo=UTC)
BACK_KNOWN = datetime(2019, 3, 18, 21, 0, tzinfo=UTC)
T_LATE = datetime(2019, 6, 28, 20, 0, tzinfo=UTC)


def _settings(**universe: Any) -> Settings:
    return Settings(_env_file=None, universe=universe)


def _bar(conn: duckdb.DuckDBPyConnection, session: date) -> dict[str, Any]:
    frame = prices_as_of(conn, T_LATE, [SID]).filter(pl.col("session") == session)
    (row,) = frame.iter_rows(named=True)
    return row


def _revise(
    conn: duckdb.DuckDBPyConnection, *, close_times: float = 1.0, volume: int | None = None
) -> float:
    """Revise SID's `JUMP` bar at `REVISED_AT`; returns the original close."""
    row = _bar(conn, JUMP)
    insert_row(
        conn,
        "prices_daily",
        row
        | {
            "close": row["close"] * close_times,
            "high": max(row["high"], row["close"] * close_times),
            "volume": row["volume"] if volume is None else volume,
            "known_at": REVISED_AT,
            "ingested_at": REVISED_AT,
        },
    )
    return float(row["close"])


def _action(
    conn: duckdb.DuckDBPyConnection, kind: str, ex_date: date, amount: float, known_at: datetime
) -> None:
    insert_row(
        conn,
        "corporate_actions",
        {
            "security_id": SID,
            "action_type": kind,
            "ex_date": ex_date,
            "ratio_or_amount": amount,
            "announced_at": None,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "fixture",
            "provenance": "action",
        },
    )


def _excluded(conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings) -> dict[str, Any]:
    u = universe_as_of(conn, t, settings)
    return {r["security_id"]: (r["rule"], r["reason"]) for r in u.exclusions.iter_rows(named=True)}


def _members(conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings) -> set[str]:
    return set(universe_as_of(conn, t, settings).members["security_id"].to_list())


# --- rule 1: a zero-volume bar is missing, not a price ------------------------------


def test_traded_reads_drop_a_zero_volume_bar_from_when_it_is_known(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _revise(fixture_store, volume=0)
    on_jump = pl.col("session") == JUMP
    assert prices_as_of(fixture_store, T_LATE, [SID]).filter(on_jump)["volume"].item() == 0
    assert prices_as_of(fixture_store, T_LATE, [SID], traded_only=True).filter(on_jump).is_empty()
    adjusted = adjusted_prices_as_of(
        fixture_store, T_LATE, [SID], include_dividends=True, traded_only=True
    )
    assert adjusted.filter(on_jump).is_empty()
    assert adjusted.height == prices_as_of(fixture_store, T_LATE, [SID]).height - 1
    # No look-ahead: before the zero-volume revision is known, the bar traded.
    before = prices_as_of(fixture_store, BEFORE_REVISION, [SID], traded_only=True)
    assert before.filter(on_jump).height == 1


def test_a_zero_volume_bar_fails_rule_6_as_missing_data(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    assert SID in _members(fixture_store, T_LATE, _settings())
    _revise(fixture_store, volume=0)
    assert _excluded(fixture_store, T_LATE, _settings())[SID] == (6, "missing_bars")


# --- rule 2: unexplained jumps --------------------------------------------------------


def test_an_unexplained_jump_is_listed_both_ways(fixture_store: duckdb.DuckDBPyConnection) -> None:
    assert price_jumps_as_of(fixture_store, T_LATE, settings=_settings()).is_empty()
    original = _revise(fixture_store, close_times=3.0)
    jumps = price_jumps_as_of(fixture_store, T_LATE, [SID], settings=_settings())
    assert jumps.select("security_id", "session", "accepted").rows() == [
        (SID, JUMP, False),
        (SID, BACK, False),
    ]
    assert jumps["ratio"][0] == pytest.approx(3 * original / jumps["prev_close"][0])
    assert jumps["prev_session"][1] == JUMP


def test_the_fixture_splits_explain_their_raw_moves(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # SEC_SPLIT_BETWEEN's raw close falls 3:1 on 2019-01-11, SEC_SPLIT_FUTURE's 4:1
    # on 2019-02-14; both splits are stored, so neither is a jump.
    raw = prices_as_of(fixture_store, T_LATE, ["SEC_SPLIT_BETWEEN"])
    closes = dict(raw.select("session", "close").iter_rows())
    assert closes[date(2019, 1, 11)] / closes[date(2019, 1, 10)] < 0.4
    assert price_jumps_as_of(fixture_store, T_LATE, settings=_settings()).is_empty()


def test_a_split_explains_a_jump_only_from_when_it_is_known(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _revise(fixture_store, close_times=3.0)
    split_known = datetime(2019, 4, 1, 21, 0, tzinfo=UTC)
    _action(fixture_store, "split", JUMP, 1 / 3, split_known)
    before = price_jumps_as_of(fixture_store, BACK_KNOWN, [SID], settings=_settings())
    after = price_jumps_as_of(fixture_store, split_known, [SID], settings=_settings())
    assert before["session"].to_list() == [JUMP, BACK]
    assert after["session"].to_list() == [BACK]


def test_a_dividend_explains_a_drop(fixture_store: duckdb.DuckDBPyConnection) -> None:
    original = _revise(fixture_store, close_times=3.0)
    _action(fixture_store, "dividend", BACK, 2 * original, REVISED_AT)
    jumps = price_jumps_as_of(fixture_store, T_LATE, [SID], settings=_settings())
    assert jumps["session"].to_list() == [JUMP]


def test_the_jump_list_reads_only_rows_known_at_t(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """No look-ahead: at every probe the list equals the one over a store
    truncated at that probe, and the jump appears only once its bar is known."""
    _revise(fixture_store, close_times=3.0)
    _action(fixture_store, "split", JUMP, 1 / 3, datetime(2019, 4, 1, 21, 0, tzinfo=UTC))
    truncated = TruncatedStore(fixture_store)
    probes = [
        BEFORE_REVISION,
        REVISED_AT,
        BACK_KNOWN,
        datetime(2019, 4, 1, 21, 0, tzinfo=UTC),
        T_LATE,
    ]
    try:
        for t in probes:
            full = price_jumps_as_of(fixture_store, t, settings=_settings())
            cut = price_jumps_as_of(truncated.at(t), t, settings=_settings())
            assert full.equals(cut), t
            assert (full["session"] <= t.date()).all()
    finally:
        truncated.close()
    assert price_jumps_as_of(fixture_store, BEFORE_REVISION, settings=_settings()).is_empty()


def test_an_unaccepted_jump_in_the_window_fails_rule_6(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _revise(fixture_store, close_times=3.0)
    assert _excluded(fixture_store, T_LATE, _settings())[SID] == (6, "price_jump")
    # Before the revision is known the name is judged on the bars known then.
    before = _excluded(fixture_store, BEFORE_REVISION, _settings())
    assert before.get(SID, (0, ""))[1] != "price_jump"


def test_accepting_both_jumps_readmits_the_name(fixture_store: duckdb.DuckDBPyConnection) -> None:
    _revise(fixture_store, close_times=3.0)
    one = _settings(accepted_price_jumps=[f"{SID}@{JUMP}"])
    both = _settings(accepted_price_jumps=[f"{SID}@{JUMP}", f"{SID}@{BACK}"])
    assert _excluded(fixture_store, T_LATE, one)[SID] == (6, "price_jump")
    assert SID in _members(fixture_store, T_LATE, both)
    accepted = price_jumps_as_of(fixture_store, T_LATE, [SID], settings=both)
    assert accepted["accepted"].to_list() == [True, True]


def test_a_jump_outside_the_history_window_does_not_exclude(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _revise(fixture_store, close_times=3.0)
    assert SID in _members(fixture_store, T_LATE, _settings(min_history_months=3))


def test_a_price_jump_is_not_counted_as_truncated_history(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _revise(fixture_store, close_times=3.0)
    settings = _settings()
    assert _excluded(fixture_store, T_LATE, settings)[SID] == (6, "price_jump")
    gap = survivorship_gap(fixture_store, T_LATE, settings)
    assert SID in gap.listed
    assert SID not in gap.truncated_history


def test_health_lists_every_jump_and_marks_the_accepted(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _revise(fixture_store, close_times=3.0)
    report = health_report(fixture_store, T_LATE, _settings())
    assert report.price_jumps.pending.select("security_id", "session").rows() == [
        (SID, JUMP),
        (SID, BACK),
    ]
    accepted = health_report(
        fixture_store, T_LATE, _settings(accepted_price_jumps=[f"{SID}@{JUMP}"])
    )
    assert accepted.price_jumps.frame.height == 2
    assert accepted.price_jumps.pending["session"].to_list() == [BACK]
    assert report.ok  # a review list, not an integrity failure


# --- config -----------------------------------------------------------------------------


def test_accepted_jump_entries_parse_and_bad_ones_are_refused() -> None:
    assert parse_accepted_jump("0001234567:B@2017-04-04") == ("0001234567:B", date(2017, 4, 4))
    for bad in ("SEC_A", "@2017-04-04", "SEC_A@2017-13-01"):
        with pytest.raises(ValueError):
            parse_accepted_jump(bad)
        with pytest.raises(ValidationError):
            _settings(accepted_price_jumps=[bad])


@pytest.mark.parametrize(
    "bounds", [{"max_jump_ratio": 1.0}, {"min_jump_ratio": 1.0}, {"min_jump_ratio": 0.0}]
)
def test_jump_bounds_must_straddle_one(bounds: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        _settings(**bounds)
