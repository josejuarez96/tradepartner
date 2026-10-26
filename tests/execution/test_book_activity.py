"""Today's activity in plain sentences (T271)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import duckdb
import pytest

from tradepartner.execution import blotter, book_activity
from tradepartner.store import journal
from tradepartner.store.db import configure_connection
from tradepartner.store.journal import (
    DecisionRow,
    FillRow,
    OrderEventRow,
    OrderRow,
    PaperPlanRow,
    PaperRunRow,
    PositionDailyRow,
)
from tradepartner.store.schema import init_schema

SESSION = date(2026, 10, 14)
PRIOR = date(2026, 10, 13)
OPEN = datetime(2026, 10, 14, 13, 31, tzinfo=UTC)  # 9:31 AM EDT
EVENING = datetime(2026, 10, 14, 22, tzinfo=UTC)
ET = ZoneInfo("America/New_York")


def row(
    symbol: str,
    side: str,
    *,
    qty: float = 10.0,
    price: float = 100.0,
    status: str = "filled",
    first: datetime | None = OPEN,
    last: datetime | None = OPEN + timedelta(minutes=3),
    full_exit: bool = False,
    oid: str | None = None,
    security_id: str | None = None,
) -> blotter.BlotterRow:
    filled = status in ("filled", "partial")
    return blotter.BlotterRow(
        client_order_id=oid or f"{symbol}-{side}-{status}",
        symbol=symbol,
        side=side,
        quantity=qty,
        notional=None,
        filled_quantity=qty if filled else 0.0,
        fill_price=price if filled else None,
        status=status,
        decision="trade",
        reason=None,
        signal_rank=None,
        slippage_bp=None,
        slippage_note=None,
        first_fill_at=first if filled else None,
        last_fill_at=last if filled else None,
        full_exit=full_exit,
        security_id=symbol if security_id is None else security_id,
    )


def test_hand_values_ranges_exits_new_names_and_digest() -> None:
    rows = [
        row("AAA", "sell", qty=10, price=500.0, full_exit=True),
        row("BBB", "sell", qty=10, price=500.0, first=OPEN + timedelta(minutes=1)),
        row("CCC", "sell", qty=5, price=240.0, last=OPEN + timedelta(minutes=2)),
        row("DDD", "buy", qty=10, price=200.0),
        row("EEE", "buy", qty=20, price=100.0),
        row("FFF", "buy", qty=10, price=190.0),
        row("GGG", "buy", qty=10, price=100.0),
        row("HHH", "buy", status="rejected"),
    ]
    result = book_activity.summarise(rows, ET, held_before={"EEE", "FFF", "AAA"})
    assert result.sells.count == 3
    assert result.sells.dollars == pytest.approx(11200.0)
    assert result.sells.time_range == "9:31 to 9:34 AM ET"
    assert result.sells.full_exits == ("AAA",)
    assert result.buys.count == 4
    assert result.buys.dollars == pytest.approx(6900.0)
    assert result.buys.new_names == ("DDD", "GGG")
    assert result.digest == "Sold 3 names for $11,200, bought 4 for $6,900; 1 order did not fill"
    assert result.sells.sentence == (
        "Sold 3 names for $11,200, 9:31 to 9:34 AM ET; fully out of AAA."
    )
    assert "HHH" in result.problems[0].sentence
    assert "AAA" in (result.sells.sentence or "") and "-sell" not in (result.sells.sentence or "")


def test_unknown_held_set_names_nothing_new() -> None:
    result = book_activity.summarise([row("DDD", "buy")], ET)
    assert result.buys.new_names == ()


def test_empty_security_id_is_not_called_a_new_name() -> None:
    result = book_activity.summarise([row("DDD", "buy", security_id="")], ET, held_before=set())
    assert result.buys.new_names == ()


def test_cross_day_range_includes_both_eastern_dates() -> None:
    first = datetime(2026, 10, 26, 20, 1, tzinfo=UTC)  # Oct 26 4:01 PM EDT
    last = datetime(2026, 10, 27, 13, 31, tzinfo=UTC)  # Oct 27 9:31 AM EDT
    assert book_activity.time_range(first, last) == "Oct 26 4:01 PM to Oct 27 9:31 AM ET"


def test_same_day_range_keeps_compact_format() -> None:
    assert book_activity.time_range(OPEN, OPEN + timedelta(minutes=3)) == "9:31 to 9:34 AM ET"


def test_range_is_eastern_not_utc_and_crosses_noon() -> None:
    first = datetime(2026, 10, 14, 15, 58, tzinfo=UTC)  # 11:58 AM EDT
    last = datetime(2026, 10, 14, 16, 3, tzinfo=UTC)  # 12:03 PM EDT
    assert book_activity.time_range(first, last) == "11:58 AM to 12:03 PM ET"
    assert book_activity.time_range(OPEN, OPEN) == "9:31 AM ET"
    winter = datetime(2026, 12, 1, 14, 31, tzinfo=UTC)  # 9:31 AM EST
    assert book_activity.time_range(winter, winter) == "9:31 AM ET"
    assert "UTC" not in book_activity.time_range(OPEN, last)
    with pytest.raises(ValueError):
        book_activity.time_range(OPEN.replace(tzinfo=None), last)


@pytest.mark.parametrize("status", sorted(book_activity.STATUS_SENTENCES))
def test_every_status_has_a_sentence_naming_the_ticker(status: str) -> None:
    result = book_activity.summarise([row("ZZZ", "buy", status=status)], ET)
    (problem,) = result.problems
    assert problem.symbol == "ZZZ"
    assert problem.sentence == (
        f"The buy of ZZZ did not fill: {book_activity.STATUS_SENTENCES[status]}."
    )
    assert problem.detail == status
    assert result.digest == "Nothing filled; 1 order did not fill"


def test_unknown_status_needs_a_look_and_keeps_raw_status() -> None:
    result = book_activity.summarise([row("ZZZ", "sell", status="mystery")], ET)
    (problem,) = result.problems
    assert problem.sentence == "The sell of ZZZ did not fill: needs a look."
    assert problem.detail == "mystery"


def test_filled_status_without_fill_needs_a_look() -> None:
    result = book_activity.summarise([row("ZZZ", "buy", status="filled", qty=0.0)], ET)
    assert result.problems[0].detail == "filled"
    assert "needs a look" in result.problems[0].sentence


def test_digest_variants() -> None:
    one = book_activity.summarise([row("AAA", "sell", price=12400.0, qty=1)], ET)
    assert one.digest == "Sold 1 name for $12,400"
    two = book_activity.summarise(
        [row("AAA", "buy", status="open"), row("BBB", "buy", status="expired")], ET
    )
    assert two.digest == "Nothing filled; 2 orders did not fill"


def test_no_orders_says_no_trades_and_why_when_plan_says() -> None:
    assert book_activity.summarise([], ET).digest == "No trades today"

    def plan(targets: int, below: int) -> PaperPlanRow:
        return PaperPlanRow(
            run_id=1,
            plan_trial_id=1,
            rebalance_session=SESSION,
            n_universe=10,
            n_targets=targets,
            n_orders_below_min_at_live_capital=below,
            known_at=OPEN,
            ingested_at=OPEN,
        )

    assert (
        book_activity.summarise([], ET, plan=plan(0, 0)).digest
        == "No trades today: the plan holds no names."
    )
    assert (
        book_activity.summarise([], ET, plan=plan(5, 2)).digest
        == "No trades today: every planned order was below the minimum size."
    )
    assert book_activity.summarise([], ET, plan=plan(5, 0)).digest == "No trades today"


# --- journal-backed `today` -------------------------------------------------


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    db = duckdb.connect(":memory:")
    configure_connection(db)
    init_schema(db)
    try:
        yield db
    finally:
        db.close()


def stamp(at: datetime = OPEN) -> dict[str, datetime]:
    return {"known_at": at, "ingested_at": at}


def start_run(db: duckdb.DuckDBPyConnection) -> int:
    run_id = journal.append(
        db,
        PaperRunRow(
            window_id=1,
            session=SESSION,
            kind="rebalance",
            started_at=OPEN,
            invoked_by="scheduler",
            code_version="abc",
            **stamp(),
        ),
    )
    assert run_id is not None
    return run_id


def place(
    db: duckdb.DuckDBPyConnection,
    run_id: int,
    oid: str,
    symbol: str,
    side: str,
    *,
    reason: str | None = None,
    at: datetime = OPEN,
) -> None:
    decision_id = journal.append(
        db,
        DecisionRow(
            run_id=run_id,
            rebalance_session=SESSION,
            security_id=symbol,
            side=side,
            whole_share=True,
            decision="trade",
            reason=reason,
            **stamp(at),
        ),
    )
    assert decision_id is not None
    journal.append(
        db,
        OrderRow(
            client_order_id=oid,
            decision_id=decision_id,
            run_id=run_id,
            session=SESSION,
            attempt=1,
            phase=side,
            security_id=symbol,
            symbol=symbol,
            side=side,
            quantity=10.0,
            sells_in_flight_at_submit=False,
            **stamp(at),
        ),
    )


def fill(
    db: duckdb.DuckDBPyConnection,
    oid: str,
    price: float,
    filled_at: datetime,
    known_at: datetime | None = None,
    qty: float = 10.0,
) -> None:
    journal.append(
        db,
        FillRow(
            client_order_id=oid,
            filled_at=filled_at,
            quantity=qty,
            price=price,
            price_implied=False,
            broker_fill_id=f"f-{oid}-{filled_at.isoformat()}",
            source="broker_feed",
            **stamp(known_at or filled_at + timedelta(seconds=1)),
        ),
    )


def hold(db: duckdb.DuckDBPyConnection, run_id: int, symbol: str, qty: float) -> None:
    journal.append(
        db,
        PositionDailyRow(
            run_id=run_id, session=PRIOR, security_id=symbol, quantity=qty, **stamp(OPEN)
        ),
    )


def test_today_reads_ticker_sentences_from_the_journal(conn: duckdb.DuckDBPyConnection) -> None:
    run_id = start_run(conn)
    place(conn, run_id, "o1", "AAA", "sell", reason="left_targets")
    place(conn, run_id, "o2", "BBB", "buy")
    place(conn, run_id, "o3", "CCC", "buy")
    hold(conn, run_id, "AAA", 10.0)
    hold(conn, run_id, "BBB", 5.0)
    fill(conn, "o1", 50.0, OPEN)
    fill(conn, "o1", 52.0, OPEN + timedelta(minutes=2), qty=10.0)
    fill(conn, "o2", 100.0, OPEN + timedelta(minutes=3))
    journal.append(conn, OrderEventRow(client_order_id="o3", status="rejected", **stamp(OPEN)))
    result = book_activity.today(conn, 1, SESSION, EVENING, limit=50)
    assert result.sells.time_range == "9:31 to 9:33 AM ET"
    assert result.sells.dollars == pytest.approx(1020.0)
    assert result.sells.full_exits == ("AAA",)
    assert result.buys.new_names == ()  # BBB was already held; CCC never filled
    assert result.digest == "Sold 1 name for $1,020, bought 1 for $1,000; 1 order did not fill"
    assert result.problems[0].symbol == "CCC"


def test_new_name_is_one_with_no_position_before_the_session(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    run_id = start_run(conn)
    place(conn, run_id, "o2", "BBB", "buy")
    hold(conn, run_id, "BBB", 0.0)  # closed out earlier: not held
    fill(conn, "o2", 100.0, OPEN)
    assert book_activity.today(conn, 1, SESSION, EVENING, limit=5).buys.new_names == ("BBB",)


def test_no_look_ahead_fills_orders_and_events_after_as_of_are_left_out(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    run_id = start_run(conn)
    place(conn, run_id, "early", "AAA", "buy")
    place(conn, run_id, "late-order", "BBB", "buy", at=EVENING + timedelta(hours=1))
    fill(conn, "early", 100.0, OPEN)
    fill(conn, "early", 100.0, OPEN + timedelta(minutes=5), known_at=EVENING + timedelta(hours=2))
    fill(conn, "late-order", 100.0, OPEN, known_at=EVENING + timedelta(hours=2))
    journal.append(
        conn,
        OrderEventRow(
            client_order_id="early", status="cancelled", **stamp(EVENING + timedelta(hours=3))
        ),
    )
    as_of = book_activity.today(conn, 1, SESSION, EVENING, limit=5)
    assert as_of.buys.count == 1
    assert as_of.buys.dollars == pytest.approx(1000.0)
    assert as_of.buys.time_range == "9:31 AM ET"
    assert as_of.problems == ()  # the cancel is not yet known
    later = book_activity.today(conn, 1, SESSION, EVENING + timedelta(hours=4), limit=5)
    assert later.buys.count == 2
    assert later.buys.dollars == pytest.approx(3000.0)
    assert later.problems[0].detail == "cancelled"
    assert later.problems[0].part_filled
    assert later.problems[0].sentence == "The buy of AAA part filled, then it was cancelled."
    assert later.digest == "Bought 2 names for $3,000"  # a part fill is not "did not fill"


def test_today_refuses_a_naive_as_of_and_a_capped_read(conn: duckdb.DuckDBPyConnection) -> None:
    run_id = start_run(conn)
    place(conn, run_id, "a", "AAA", "buy")
    place(conn, run_id, "b", "BBB", "buy")
    with pytest.raises(ValueError, match="UTC"):
        book_activity.today(conn, 1, SESSION, EVENING.replace(tzinfo=None), limit=5)
    with pytest.raises(ValueError, match="raise limit"):
        book_activity.today(conn, 1, SESSION, EVENING, limit=1)


def test_empty_session_reads_the_plan_row(conn: duckdb.DuckDBPyConnection) -> None:
    run_id = start_run(conn)
    journal.append(
        conn,
        PaperPlanRow(
            run_id=run_id,
            plan_trial_id=1,
            rebalance_session=SESSION,
            n_universe=10,
            n_targets=0,
            n_orders_below_min_at_live_capital=0,
            **stamp(),
        ),
    )
    assert (
        book_activity.today(conn, 1, SESSION, EVENING, limit=5).digest
        == "No trades today: the plan holds no names."
    )


def test_marks_and_plan_known_after_as_of_are_ignored(conn: duckdb.DuckDBPyConnection) -> None:
    run_id = start_run(conn)
    place(conn, run_id, "o2", "BBB", "buy")
    fill(conn, "o2", 100.0, OPEN)
    journal.append(
        conn,
        PositionDailyRow(
            run_id=run_id,
            session=PRIOR,
            security_id="BBB",
            quantity=5.0,
            **stamp(EVENING + timedelta(hours=1)),
        ),
    )
    assert book_activity.today(conn, 1, SESSION, EVENING, limit=5).buys.new_names == ("BBB",)
    empty = duckdb.connect(":memory:")
    configure_connection(empty)
    init_schema(empty)
    r = start_run(empty)
    journal.append(
        empty,
        PaperPlanRow(
            run_id=r,
            plan_trial_id=1,
            rebalance_session=SESSION,
            n_universe=10,
            n_targets=0,
            n_orders_below_min_at_live_capital=0,
            **stamp(EVENING + timedelta(hours=1)),
        ),
    )
    assert book_activity.today(empty, 1, SESSION, EVENING, limit=5).digest == "No trades today"
    empty.close()


def test_new_name_compares_security_ids_not_ticker_text() -> None:
    renamed = row("NEW", "buy")
    renamed = blotter.BlotterRow(**{**renamed.__dict__, "security_id": "sec-1"})
    held_under_other_ticker = book_activity.summarise([renamed], ET, held_before={"sec-1"})
    assert held_under_other_ticker.buys.new_names == ()
    assert book_activity.summarise([renamed], ET, held_before={"NEW"}).buys.new_names == ("NEW",)
