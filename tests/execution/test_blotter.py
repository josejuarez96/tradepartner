"""Hand-computed session blotter reads (T258)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from tradepartner.calendar import session_close
from tradepartner.execution import blotter, slippage
from tradepartner.store import journal
from tradepartner.store.db import configure_connection
from tradepartner.store.journal import (
    DecisionRow,
    FillRow,
    OrderEventRow,
    OrderRow,
    PaperPlanRow,
    PaperRunRow,
    SignalRow,
)
from tradepartner.store.schema import init_schema

SESSION = date(2026, 10, 14)
OTHER = date(2026, 10, 13)
AT = datetime(2026, 10, 14, 15, tzinfo=UTC)
AFTER = datetime(2026, 10, 15, 15, tzinfo=UTC)


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    db = duckdb.connect(":memory:")
    configure_connection(db)
    init_schema(db)
    try:
        yield db
    finally:
        db.close()


def stamp(at: datetime = AT) -> dict[str, datetime]:
    return {"known_at": at, "ingested_at": at}


def run(db: duckdb.DuckDBPyConnection, window: int = 1) -> int:
    result = journal.append(
        db,
        PaperRunRow(
            window_id=window,
            session=SESSION,
            kind="rebalance",
            started_at=AT,
            invoked_by="scheduler",
            code_version="abc",
            **stamp(),
        ),
    )
    assert result is not None
    return result


def order(
    db: duckdb.DuckDBPyConnection,
    run_id: int,
    oid: str,
    side: str,
    *,
    session: date = SESSION,
    symbol: str = "AAA",
    rank: int | None = None,
    reason: str | None = None,
    quantity: float | None = 2.0,
    notional: float | None = None,
    order_run_id: int | None = None,
) -> None:
    decision_id = journal.append(
        db,
        DecisionRow(
            run_id=run_id,
            rebalance_session=session,
            security_id=symbol,
            side=side,
            whole_share=True,
            decision="trade",
            reason=reason,
            **stamp(),
        ),
    )
    assert decision_id is not None
    journal.append(
        db,
        OrderRow(
            client_order_id=oid,
            decision_id=decision_id,
            run_id=order_run_id or run_id,
            session=session,
            attempt=1,
            phase="buy" if side == "buy" else "sell",
            security_id=symbol,
            symbol=symbol,
            side=side,
            quantity=quantity,
            notional=notional,
            sells_in_flight_at_submit=False,
            **stamp(),
        ),
    )
    if rank is not None:
        journal.append(
            db,
            PaperPlanRow(
                run_id=run_id,
                plan_trial_id=1,
                rebalance_session=session,
                n_universe=1,
                n_targets=1,
                n_orders_below_min_at_live_capital=0,
                **stamp(),
            ),
        )
        journal.append(
            db,
            SignalRow(
                run_id=run_id,
                rebalance_session=session,
                security_id=symbol,
                rank=rank,
                reason="selected",
                **stamp(),
            ),
        )


def fill(
    db: duckdb.DuckDBPyConnection,
    oid: str,
    price: float,
    qty: float = 1.0,
    *,
    at: datetime = AT,
    source: str = "broker_feed",
    superseded_by: int | None = None,
) -> int:
    row_id = journal.append(
        db,
        FillRow(
            client_order_id=oid,
            filled_at=at,
            quantity=qty,
            price=price,
            price_implied=source == "broker_status",
            broker_fill_id=f"fill-{oid}-{price}-{source}-{superseded_by}",
            source=source,
            superseded_by=superseded_by,
            **stamp(at + timedelta(seconds=1)),
        ),
    )
    assert row_id is not None
    return row_id


def bar(db: duckdb.DuckDBPyConnection, symbol: str, open_: float, at: datetime) -> None:
    db.execute(
        "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
        "known_at, ingested_at, source, provenance) VALUES (?, ?, ?, 200, 1, 100, 10, "
        "?, ?, 'test', 'bar')",
        [symbol, SESSION, open_, at, at],
    )


def test_hand_values_status_reasons_scope_and_summary(conn: duckdb.DuckDBPyConnection) -> None:
    first = run(conn)
    other_window = run(conn, 2)
    order(conn, first, "buy", "buy", rank=3)
    order(conn, first, "sell", "sell", symbol="BBB", quantity=None, notional=99.5)
    order(conn, first, "reject", "buy", symbol="CCC", reason="left_universe")
    order(conn, first, "old", "buy", session=OTHER, symbol="DDD")
    order(conn, other_window, "foreign", "buy", symbol="EEE")
    fill(conn, "buy", 100.0)
    fill(conn, "buy", 102.0)
    fill(conn, "sell", 99.5)
    journal.append(conn, OrderEventRow(client_order_id="reject", status="rejected", **stamp()))
    bar(conn, "AAA", 100.0, session_close(SESSION))
    bar(conn, "BBB", 100.0, session_close(SESSION))
    result = blotter.session_blotter(conn, 1, SESSION, AFTER, 10)
    rows = {r.client_order_id: r for r in result.rows}
    assert set(rows) == {"buy", "sell", "reject"}
    assert rows["buy"].fill_price == pytest.approx(101.0)
    assert rows["buy"].filled_quantity == 2.0
    assert rows["buy"].slippage_bp == pytest.approx(100.0)
    assert rows["buy"].signal_rank == 3
    assert (rows["buy"].decision, rows["buy"].reason) == ("trade", None)
    assert (rows["sell"].notional, rows["sell"].quantity) == (99.5, None)
    assert rows["sell"].slippage_bp == pytest.approx(50.0)
    assert (rows["reject"].status, rows["reject"].fill_price) == ("rejected", None)
    assert rows["reject"].reason == "left_universe"
    assert rows["reject"].slippage_note == "no fill"
    fills = journal.fills_for(conn, window_id=1, client_order_ids=["buy", "sell", "reject"])
    expected = slippage.summarise(fills, slippage.read_opens(conn, fills), AFTER)
    assert result.summary == expected
    assert result.summary.overall.wmean_bp == pytest.approx((202 * 100 + 99.5 * 50) / 301.5)
    assert blotter.sessions(conn, 1) == (SESSION, OTHER)
    assert blotter.sessions(conn, 2) == (SESSION,)


def test_pending_no_bar_off_session_and_revised_bar(conn: duckdb.DuckDBPyConnection) -> None:
    rid = run(conn)
    for oid, symbol in (("pending", "AAA"), ("missing", "BBB"), ("off", "CCC")):
        order(conn, rid, oid, "buy", symbol=symbol)
    fill(conn, "pending", 101.0)
    fill(conn, "missing", 101.0)
    fill(conn, "off", 101.0, at=AFTER)
    bar(conn, "AAA", 100.0, session_close(SESSION))
    bar(conn, "AAA", 90.0, AFTER)  # later revision must be invisible
    bar(conn, "CCC", 100.0, session_close(SESSION))
    early = {r.client_order_id: r for r in blotter.session_blotter(conn, 1, SESSION, AT, 10).rows}
    assert all(r.slippage_note == "pending" for r in early.values())
    late = {r.client_order_id: r for r in blotter.session_blotter(conn, 1, SESSION, AFTER, 10).rows}
    assert late["pending"].slippage_bp == pytest.approx(100.0)
    assert late["missing"].slippage_note == "no bar"
    assert late["off"].slippage_note == "off session"


def test_superseded_fill_cap_and_read_only(conn: duckdb.DuckDBPyConnection) -> None:
    rid = run(conn)
    order(conn, rid, "buy", "buy")
    implied = fill(conn, "buy", 100.0, source="broker_status")
    fill(conn, "buy", 101.0, superseded_by=implied)
    bar(conn, "AAA", 100.0, session_close(SESSION))
    order(conn, rid, "second", "buy", symbol="BBB")
    fill(conn, "second", 102.0)
    bar(conn, "BBB", 100.0, session_close(SESSION))
    before = {
        name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        for name in ("orders", "order_events", "fills", "signals", "decisions", "trials")
    }
    result = blotter.session_blotter(conn, 1, SESSION, AFTER, 1)
    assert result.capped
    assert len(result.rows) == 1
    assert result.summary.overall.count == 2
    full = {r.client_order_id: r for r in blotter.session_blotter(conn, 1, SESSION, AFTER, 10).rows}
    assert full["buy"].fill_price == pytest.approx(100.0)
    assert before == {
        name: conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in before
    }


def test_implied_fill_only_affects_vwap_and_first_terminal_status(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    rid = run(conn)
    order(conn, rid, "implied", "buy")
    fill(conn, "implied", 102.0)
    fill(conn, "implied", 99.0, source="broker_status")
    journal.append(
        conn,
        OrderEventRow(
            client_order_id="implied", status="filled", **stamp(AT + timedelta(minutes=2))
        ),
    )
    journal.append(
        conn,
        OrderEventRow(
            client_order_id="implied", status="expired", **stamp(AT + timedelta(minutes=3))
        ),
    )
    bar(conn, "AAA", 100.0, session_close(SESSION))
    row = blotter.session_blotter(conn, 1, SESSION, AFTER, 10).rows[0]
    assert row.fill_price == pytest.approx(100.5)
    assert row.slippage_bp == pytest.approx(50.0)
    assert row.status == "filled"


def test_catch_up_uses_decisions_plan_rank_and_exit_has_none(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    planning_run = run(conn)
    catch_up_run = run(conn)
    order(conn, planning_run, "catchup", "buy", rank=7, order_run_id=catch_up_run)
    exit_plan_run = run(conn)
    order(conn, exit_plan_run, "exit", "sell", symbol="EXIT", rank=2, reason="left_targets")
    rows = {r.client_order_id: r for r in blotter.session_blotter(conn, 1, SESSION, AFTER, 10).rows}
    assert rows["catchup"].signal_rank == 7
    assert rows["exit"].signal_rank is None


def test_missing_decision_raises_journal_integrity_error(conn: duckdb.DuckDBPyConnection) -> None:
    rid = run(conn)
    journal.append(
        conn,
        OrderRow(
            client_order_id="orphan-decision",
            decision_id=999,
            run_id=rid,
            session=SESSION,
            attempt=1,
            phase="buy",
            security_id="AAA",
            symbol="AAA",
            side="buy",
            quantity=1.0,
            sells_in_flight_at_submit=False,
            **stamp(),
        ),
    )
    with pytest.raises(journal.JournalIntegrityError, match=r"orphan-decision.*decision"):
        blotter.session_blotter(conn, 1, SESSION, AFTER, 10)


def test_zero_quantity_live_fill_is_a_no_bar_exclusion(conn: duckdb.DuckDBPyConnection) -> None:
    rid = run(conn)
    order(conn, rid, "zero", "buy")
    stored_order = journal.orders_for(conn, window_id=1)[0]
    fake = journal.OrderedFill(
        FillRow(
            client_order_id="zero",
            filled_at=AT,
            quantity=0.0,
            price=100.0,
            price_implied=False,
            broker_fill_id="zero",
            source="broker_feed",
            **stamp(),
        ),
        side="buy",
        security_id="AAA",
        symbol="AAA",
        run_id=rid,
        window_id=1,
        order_session=SESSION,
    )
    opens = {("AAA", SESSION): 100.0}
    assert slippage.summarise([fake], opens, AFTER).no_bar == 1
    row = blotter._row(stored_order, [fake], [], ("trade", None), None, opens, AFTER)
    assert row.slippage_note == "no bar"


def test_read_scopes_fills_to_session_ids(
    conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = run(conn)
    order(conn, rid, "here", "buy")
    order(conn, rid, "other-day", "buy", session=OTHER, symbol="BBB")
    another = run(conn, 2)
    order(conn, another, "other-window", "buy", symbol="CCC")
    real = journal.fills_for
    seen: list[set[str]] = []

    def scoped_fills(
        db: duckdb.DuckDBPyConnection, *, window_id: int, client_order_ids: list[str]
    ) -> list[journal.OrderedFill]:
        seen.append(set(client_order_ids))
        return real(db, window_id=window_id, client_order_ids=client_order_ids)

    monkeypatch.setattr(blotter.journal, "fills_for", scoped_fills)
    result = blotter.session_blotter(conn, 1, SESSION, AFTER, 1)
    assert [r.client_order_id for r in result.rows] == ["here"]
    assert seen == [{"here"}]


def test_malformed_sell_decision_raises_journal_integrity_error(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    order(conn, run(conn), "bad-sell", "sell", reason="exclude_name")
    with pytest.raises(journal.JournalIntegrityError, match="neither a full exit nor a trim"):
        blotter.session_blotter(conn, 1, SESSION, AFTER, 10)


def test_first_and_last_fill_times_and_full_exit_flag(conn: duckdb.DuckDBPyConnection) -> None:
    rid = run(conn)
    order(conn, rid, "two", "buy")
    order(conn, rid, "none", "buy", symbol="BBB")
    order(conn, rid, "out", "sell", symbol="CCC", reason="left_targets")
    order(conn, rid, "trim", "sell", symbol="DDD")
    fill(conn, "two", 101.0, at=AT + timedelta(minutes=5))
    fill(conn, "two", 100.0, at=AT)
    rows = {r.client_order_id: r for r in blotter.session_blotter(conn, 1, SESSION, AFTER, 10).rows}
    assert rows["two"].first_fill_at == AT
    assert rows["two"].last_fill_at == AT + timedelta(minutes=5)
    assert (rows["none"].first_fill_at, rows["none"].last_fill_at) == (None, None)
    assert (rows["out"].full_exit, rows["trim"].full_exit) == (True, False)


def test_known_by_leaves_out_later_orders_events_and_fills(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    rid = run(conn)
    order(conn, rid, "buy", "buy")
    fill(conn, "buy", 100.0, at=AT)  # known AT + 1s
    fill(conn, "buy", 110.0, at=AT + timedelta(hours=1))  # known later
    journal.append(
        conn,
        OrderEventRow(client_order_id="buy", status="filled", **stamp(AT + timedelta(hours=2))),
    )
    bar(conn, "AAA", 100.0, session_close(SESSION))
    cut = AT + timedelta(minutes=1)
    seen = blotter.session_blotter(conn, 1, SESSION, AFTER, 10, known_by=cut)
    assert seen.rows[0].filled_quantity == 1.0
    assert seen.rows[0].status == "open"
    assert seen.summary.overall.count == 1
    full = blotter.session_blotter(conn, 1, SESSION, AFTER, 10)
    assert (full.rows[0].filled_quantity, full.rows[0].status) == (2.0, "filled")
    assert (
        blotter.session_blotter(conn, 1, SESSION, AFTER, 10, known_by=AT - timedelta(days=1)).rows
        == ()
    )
    with pytest.raises(ValueError, match="known_by"):
        blotter.session_blotter(conn, 1, SESSION, AFTER, 10, known_by=cut.replace(tzinfo=None))
