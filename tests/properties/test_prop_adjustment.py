"""Property tests for split adjustment in `tradepartner.store.asof` (#3655, plan T445):
P11 (factors compose) and P12 (no look-ahead past an ex-date).

One in-memory store per test function, built once and reused by every example; each
example writes under a fresh `security_id`, so examples never see each other's rows.
Dividends and spin-offs are a later slice (`include_dividends` stays False).
"""

from __future__ import annotations

import itertools
import math
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
import pytest
from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from tradepartner import calendar
from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.asof import adjusted_prices_as_of, prices_as_of
from tradepartner.store.db import configure_connection, insert_row

_FINITE = {"allow_nan": False, "allow_infinity": False}
_N_SESSIONS = 12
_NEW_YORK = ZoneInfo("America/New_York")
#: Every action is known here, before the first bar and so before every read time.
_ACTIONS_KNOWN_AT = datetime(2018, 12, 3, 12, 0, tzinfo=UTC)
_SETTINGS = Settings(_env_file=None)
_PRICE_COLUMNS = ("open", "high", "low", "close")


def _sessions() -> list[date]:
    """12 consecutive XNYS sessions from the first of 2019."""
    sessions = [calendar.next_session(date(2018, 12, 31))]
    while len(sessions) < _N_SESSIONS:
        sessions.append(calendar.next_session(sessions[-1]))
    return sessions


_SESSIONS = _sessions()
_STORES: dict[str, duckdb.DuckDBPyConnection] = {}
_IDS = itertools.count(1)


def _store(name: str) -> duckdb.DuckDBPyConnection:
    """The in-memory store named `name`, built on first use (no fixture: a `@given`
    test takes no function-scoped fixture)."""
    if name not in _STORES:
        conn = configure_connection(duckdb.connect(":memory:"))
        schema.init_schema(conn)
        _STORES[name] = conn
    return _STORES[name]


def _write(
    conn: duckdb.DuckDBPyConnection,
    security_id: str,
    closes: list[float],
    splits: dict[int, float],
) -> None:
    """One bar per session (known at its own close) and one split per `splits` entry
    (session index of the ex-date -> ratio), every split known before the first bar."""
    for index, (session, close) in enumerate(zip(_SESSIONS, closes, strict=True)):
        known = calendar.session_close(session)
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": security_id,
                "session": session,
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 1000 + index,
                "known_at": known,
                "ingested_at": known,
                "source": "property-test",
                "provenance": "bar",
            },
        )
    for index, ratio in splits.items():
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": security_id,
                "action_type": "split",
                "ex_date": _SESSIONS[index],
                "ratio_or_amount": ratio,
                "known_at": _ACTIONS_KNOWN_AT,
                "ingested_at": _ACTIONS_KNOWN_AT,
                "source": "property-test",
                "provenance": "action",
            },
        )


_CLOSES = st.lists(st.floats(1, 1000, **_FINITE), min_size=_N_SESSIONS, max_size=_N_SESSIONS)
#: 1 to 3 splits on distinct ex-dates: session index -> ratio.
_SPLITS = st.dictionaries(
    st.integers(0, _N_SESSIONS - 1), st.floats(0.05, 20, **_FINITE), min_size=1, max_size=3
)
_READ_AT = st.integers(0, _N_SESSIONS - 1)


@hyp_settings(max_examples=25)
@given(closes=_CLOSES, splits=_SPLITS, read_at=_READ_AT)
def test_split_factors_compose_over_the_ex_dates_at_or_before_t(
    closes: list[float], splits: dict[int, float], read_at: int
) -> None:
    """P11: an adjusted close is the raw close times the product of `1 / ratio` over the
    splits with `session < ex_date <= t`'s session; volume stays raw."""
    conn = _store("compose")
    security_id = f"P11_{next(_IDS)}"
    _write(conn, security_id, closes, splits)
    t = calendar.session_close(_SESSIONS[read_at])
    assert t.astimezone(_NEW_YORK).date() == _SESSIONS[read_at]

    adjusted = adjusted_prices_as_of(conn, t, [security_id], settings=_SETTINGS)
    raw = prices_as_of(conn, t, [security_id])

    assert adjusted["session"].to_list() == _SESSIONS[: read_at + 1]
    assert raw["session"].to_list() == _SESSIONS[: read_at + 1]
    assert adjusted["volume"].to_list() == [1000 + i for i in range(read_at + 1)]
    assert raw["close"].to_list() == closes[: read_at + 1]
    for index in range(read_at + 1):
        applied = [ratio for ex, ratio in sorted(splits.items()) if index < ex <= read_at]
        for column in _PRICE_COLUMNS:
            value = adjusted[column][index]
            if applied:
                want = closes[index] * math.prod(1 / ratio for ratio in applied)
                assert value == pytest.approx(want, rel=1e-9), (column, index)
            else:
                assert value == closes[index], (column, index)


@hyp_settings(max_examples=25)
@given(closes=_CLOSES, splits=_SPLITS, read_at=_READ_AT)
def test_a_split_whose_ex_date_is_after_t_changes_nothing(
    closes: list[float], splits: dict[int, float], read_at: int
) -> None:
    """P12, no look-ahead: at a `t` whose New York date is before a split's ex-date,
    the frame is the frame of a store that never held that split, although the split
    was already known."""
    security_id = f"P12_{next(_IDS)}"
    past = {ex: ratio for ex, ratio in splits.items() if ex <= read_at}
    _write(_store("with_future_splits"), security_id, closes, splits)
    _write(_store("without_future_splits"), security_id, closes, past)
    t = calendar.session_close(_SESSIONS[read_at])

    frames: list[pl.DataFrame] = [
        adjusted_prices_as_of(_store(name), t, [security_id], settings=_SETTINGS)
        for name in ("with_future_splits", "without_future_splits")
    ]
    assert frames[0].height == read_at + 1
    assert frames[0].equals(frames[1])
