"""Per-fill slippage against the session's SIP open (#1733)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import pytest

from tradepartner.calendar import session_close
from tradepartner.execution import slippage
from tradepartner.store.db import configure_connection
from tradepartner.store.journal import FillRow, OrderedFill
from tradepartner.store.schema import init_schema

SESSION = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 14, 5, tzinfo=UTC)  # 10:05 New York


def _fill(side: str, security: str, price: float, fill_id: int, at: datetime = NOW) -> OrderedFill:
    row = FillRow(
        fill_id=fill_id,
        client_order_id=f"o{fill_id}",
        filled_at=at,
        quantity=1.0,
        price=price,
        price_implied=False,
        broker_fill_id=f"b{fill_id}",
        source="broker_status",
        known_at=at,
        ingested_at=at,
    )
    return OrderedFill(
        fill=row, side=side, security_id=security, symbol=security, run_id=1, window_id=1
    )


@pytest.fixture
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _bar(conn: duckdb.DuckDBPyConnection, security: str, open_: float, close: float) -> None:
    known = session_close(SESSION)
    conn.execute(
        "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
        "known_at, ingested_at, source, provenance) VALUES (?, ?, ?, 999, 1, ?, 10, ?, ?, "
        "'test', 'bar')",
        [security, SESSION, open_, close, known, known],
    )


def test_buy_above_open_and_sell_below_open_are_both_adverse_positive() -> None:
    assert slippage.slippage_bp("buy", 100.5, 100.0) == pytest.approx(50.0)
    assert slippage.slippage_bp("sell", 99.5, 100.0) == pytest.approx(50.0)
    assert slippage.slippage_bp("buy", 99.5, 100.0) == pytest.approx(-50.0)
    assert slippage.slippage_bp("sell", 100.5, 100.0) == pytest.approx(-50.0)


def test_quantile_on_a_small_hand_set() -> None:
    values = [5.0, 1.0, 3.0, 2.0, 4.0]
    assert slippage.quantile(values, 0.5) == pytest.approx(3.0)
    assert slippage.quantile(values, 0.9) == pytest.approx(4.6)
    assert slippage.quantile([], 0.5) is None


def test_summarise_per_side_overall_and_missing_bars() -> None:
    fills = [
        _fill("buy", "A", 100.5, 1),  # +50 bp
        _fill("buy", "B", 200.0, 2),  # 0 bp
        _fill("sell", "A", 99.0, 3),  # +100 bp
        _fill("sell", "C", 10.0, 4),  # no bar
    ]
    opens = {("A", SESSION): 100.0, ("B", SESSION): 200.0}
    out = slippage.summarise(fills, opens)
    assert out.no_bar == 1
    assert (out.buy.count, out.buy.median_bp, out.buy.p90_bp) == (2, 25.0, pytest.approx(45.0))
    assert (out.sell.count, out.sell.median_bp, out.sell.p90_bp) == (1, 100.0, 100.0)
    assert out.overall.count == 3
    assert out.overall.median_bp == pytest.approx(50.0)
    assert out.overall.p90_bp == pytest.approx(90.0)


def test_window_slippage_reads_the_raw_open_and_counts_a_missing_bar(
    store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bar(store, "A", open_=100.0, close=101.0)  # high 999: only the open may be used
    fills = [_fill("buy", "A", 100.2, 1), _fill("sell", "Z", 5.0, 2)]
    monkeypatch.setattr(slippage.store_journal, "fills_for", lambda *_a, **_k: fills)
    out = slippage.window_slippage(store, 1)
    assert out.no_bar == 1
    assert out.buy.median_bp == pytest.approx(20.0)
    assert out.sell.count == 0


def test_a_bar_known_after_the_session_close_is_not_used(
    store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    late = datetime(2026, 10, 20, tzinfo=UTC)
    store.execute(
        "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
        "known_at, ingested_at, source, provenance) VALUES ('A', ?, 100, 100, 100, 100, 10, "
        "?, ?, 'test', 'bar')",
        [SESSION, late, late],
    )
    monkeypatch.setattr(
        slippage.store_journal, "fills_for", lambda *_a, **_k: [_fill("buy", "A", 101.0, 1)]
    )
    assert slippage.window_slippage(store, 1).no_bar == 1


def test_the_line_names_both_sides_overall_and_the_missing_count() -> None:
    out = slippage.summarise([_fill("buy", "A", 100.5, 1)], {("A", SESSION): 100.0})
    assert slippage.slippage_line(out) == (
        "slippage vs open (bp, adverse positive): buy n=1 median 50.00 p90 50.00; "
        "sell n=0; all n=1 median 50.00 p90 50.00; no bar 0"
    )
