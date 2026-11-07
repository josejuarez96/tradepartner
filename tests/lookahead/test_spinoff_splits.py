"""No look-ahead in the spin-off-is-not-a-split rule (#3058).

Alpaca answers one event, P's distribution of C on E, twice: as a `spin_offs`
row and as a forward split of P (ratio 1.111). Through the real ingest path
(`AlpacaPriceSource.corporate_actions`, then `ingest._add_actions`), the split
is refused and the spin-off stored. Then, at every `t` on a grid around E:

- with spin-offs off (the frozen default), P's adjusted frame equals the frame
  of a store with no action at all: the refused split moves nothing, at no `t`;
- with spin-offs on, it equals that frame before the spin-off's own `known_at`
  (nothing derived from the spin-off is visible earlier), and from then on
  differs only on P's sessions before E, by the dividend-in-kind factor.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from tradepartner.adapters.alpaca_prices import AlpacaPriceSource, ListingResolver
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.ingest import _add_actions
from tradepartner.store import schema
from tradepartner.store.asof import adjusted_prices_as_of
from tradepartner.store.db import configure_connection, insert_row

SESSIONS = (
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
)
E = SESSIONS[3]
RUN = datetime(2024, 2, 1, 21, tzinfo=UTC)
SETTINGS = Settings(_env_file=None)  # type: ignore[call-arg]
P, C = "SEC_P", "SEC_C"
PARENT_CLOSE, CHILD_CLOSE, RATIO = 100.0, 20.0, 0.5
FACTOR = 1.0 - RATIO * CHILD_CLOSE / PARENT_CLOSE


def _bar(sid: str, session: date, close: float) -> dict[str, Any]:
    stamp = session_close(session)
    return {
        "security_id": sid,
        "session": session,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1000,
        "known_at": stamp,
        "ingested_at": stamp,
        "source": "alpaca_sip",
        "provenance": "bar",
    }


def _store(*, ingest: bool) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    for session in SESSIONS:
        insert_row(conn, "prices_daily", _bar(P, session, PARENT_CLOSE if session < E else 90.0))
    for session in SESSIONS[3:]:
        insert_row(conn, "prices_daily", _bar(C, session, CHILD_CLOSE))
    if ingest:
        payload = {
            "forward_splits": [
                {
                    "id": "echo",
                    "symbol": "P",
                    "ex_date": E.isoformat(),
                    "process_date": E.isoformat(),
                    "old_rate": 1,
                    "new_rate": 1.111,
                }
            ],
            "spin_offs": [
                {
                    "id": "spin",
                    "source_symbol": "P",
                    "new_symbol": "C",
                    "ex_date": E.isoformat(),
                    "process_date": E.isoformat(),
                    "source_rate": 2,
                    "new_rate": 1,
                }
            ],
        }
        source = AlpacaPriceSource(
            ListingResolver(
                [
                    {"security_id": P, "ticker": "P", "valid_from": date(2016, 1, 4)},
                    {"security_id": C, "ticker": "C", "valid_from": E},
                ]
            ),
            fetch_actions=lambda symbols, start, end: payload,
            settings=SETTINGS,
            spinoff_splits=(),
        )
        actions = source.corporate_actions([P, C], E, E)
        assert [a.action_type.value for a in actions] == ["spinoff"]
        _add_actions(conn, actions, (E, E), ingested_at=RUN)
    return conn


@pytest.fixture
def stores() -> Any:
    ingested, bare = _store(ingest=True), _store(ingest=False)
    yield ingested, bare
    ingested.close()
    bare.close()


def _frame(conn: duckdb.DuckDBPyConnection, t: datetime, *, spin_offs: bool) -> pl.DataFrame:
    return adjusted_prices_as_of(
        conn, t, [P], include_dividends=True, include_spin_offs=spin_offs, settings=SETTINGS
    )


def _grid() -> list[datetime]:
    closes = [session_close(day) for day in SESSIONS]
    return sorted({*closes, *(c - timedelta(seconds=1) for c in closes), RUN})


def test_the_refused_split_moves_no_read_at_any_t(stores: Any) -> None:
    ingested, bare = stores
    [(kind,)] = ingested.execute("SELECT DISTINCT action_type FROM corporate_actions").fetchall()
    assert kind == "spinoff"
    for t in _grid():
        assert_frame_equal(_frame(ingested, t, spin_offs=False), _frame(bare, t, spin_offs=False))


def test_nothing_from_the_spin_off_is_visible_before_its_known_at(stores: Any) -> None:
    ingested, bare = stores
    [(known_at,)] = ingested.execute("SELECT known_at FROM corporate_actions").fetchall()
    assert known_at > session_close(E) - timedelta(days=1)  # the #1712 floor at least
    for t in _grid():
        with_flag, plain = _frame(ingested, t, spin_offs=True), _frame(bare, t, spin_offs=True)
        if t < known_at:
            assert_frame_equal(with_flag, plain)
            continue
        before = pl.col("session") < E
        assert_frame_equal(with_flag.filter(~before), plain.filter(~before))
        assert with_flag.filter(before)["close"].to_list() == pytest.approx(
            [PARENT_CLOSE * FACTOR] * plain.filter(before).height, abs=1e-12
        )
