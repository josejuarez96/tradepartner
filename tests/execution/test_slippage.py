"""Per-fill slippage against the session's SIP open (#1733)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest

from tradepartner.calendar import session_close
from tradepartner.execution import slippage
from tradepartner.store.db import configure_connection
from tradepartner.store.journal import FillRow, OrderedFill
from tradepartner.store.schema import init_schema

SESSION = date(2026, 10, 14)
NOW = datetime(2026, 10, 14, 14, 5, tzinfo=UTC)  # 10:05 New York, on SESSION
AFTER = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)  # SESSION is complete


def _fill(
    side: str,
    security: str,
    price: float,
    fill_id: int,
    *,
    order: str | None = None,
    qty: float = 1.0,
    implied: bool = False,
    at: datetime = NOW,
    session: date | None = SESSION,
) -> OrderedFill:
    order_id = order or f"o{fill_id}"
    row = FillRow(
        fill_id=fill_id,
        client_order_id=order_id,
        filled_at=at,
        quantity=qty,
        price=price,
        price_implied=implied,
        broker_fill_id=f"b{fill_id}",
        source="broker_status",
        known_at=at,
        ingested_at=at,
    )
    return OrderedFill(
        fill=row,
        side=side,
        security_id=security,
        symbol=security,
        run_id=1,
        window_id=1,
        order_session=session,
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


def test_partial_fills_are_one_order_at_their_vwap() -> None:
    fills = [
        _fill("buy", "A", 100.4, 1, order="o", qty=1.0),
        _fill("buy", "A", 100.6, 2, order="o", qty=1.0),
    ]
    out = slippage.summarise(fills, {("A", SESSION): 100.0}, AFTER)
    assert out.buy.count == 1  # orders, not fills
    assert out.buy.median_bp == pytest.approx(50.0)


def test_a_synthetic_residual_fill_is_priced_through_the_order_average() -> None:
    """Journaled 10 @ 102.0, broker average 100.5 over 20, so the implied residual is
    10 @ 99.0 (a price nobody traded at): the order's number is the average's."""
    fills = [
        _fill("buy", "A", 102.0, 1, order="o", qty=10.0),
        _fill("buy", "A", 99.0, 2, order="o", qty=10.0, implied=True),
    ]
    out = slippage.summarise(fills, {("A", SESSION): 100.0}, AFTER)
    assert out.buy.count == 1
    assert out.buy.wmean_bp == pytest.approx(slippage.slippage_bp("buy", 100.5, 100.0))
    assert out.buy.wmean_bp == pytest.approx(50.0)


def test_summarise_per_side_overall_and_missing_bars() -> None:
    fills = [
        _fill("buy", "A", 100.5, 1),  # +50 bp
        _fill("buy", "B", 200.0, 2),  # 0 bp
        _fill("sell", "A", 99.0, 3),  # +100 bp
        _fill("sell", "C", 10.0, 4),  # no bar
    ]
    opens = {("A", SESSION): 100.0, ("B", SESSION): 200.0}
    out = slippage.summarise(fills, opens, AFTER)
    assert out.no_bar == 1
    assert (out.buy.count, out.buy.median_bp, out.buy.p90_bp) == (2, 25.0, pytest.approx(45.0))
    assert (out.sell.count, out.sell.median_bp, out.sell.p90_bp) == (1, 100.0, 100.0)
    assert out.overall.count == 3
    assert out.overall.median_bp == pytest.approx(50.0)
    assert out.overall.p90_bp == pytest.approx(90.0)


def test_the_weighted_mean_weights_each_order_by_its_notional() -> None:
    fills = [
        _fill("buy", "A", 100.1, 1, qty=10.0),  # +10 bp on 1001
        _fill("buy", "B", 201.0, 2, qty=15.0),  # +50 bp on 3015
    ]
    opens = {("A", SESSION): 100.0, ("B", SESSION): 200.0}
    out = slippage.summarise(fills, opens, AFTER)
    assert out.buy.wmean_bp == pytest.approx((10 * 1001 + 50 * 3015) / (1001 + 3015))
    assert out.buy.median_bp == pytest.approx(30.0)  # the diagnostic stays unweighted


def test_orders_are_keyed_on_their_journaled_session_not_the_fill_date() -> None:
    other = date(2026, 10, 13)
    fills = [_fill("buy", "A", 199.0, 1, session=other, at=datetime(2026, 10, 13, 15, tzinfo=UTC))]
    out = slippage.summarise(fills, {("A", SESSION): 100.0, ("A", other): 200.0}, AFTER)
    assert out.buy.median_bp == pytest.approx(-50.0)  # against 200.0, the order's session


def test_a_fill_off_its_orders_session_is_counted_apart() -> None:
    next_day = datetime(2026, 10, 15, 15, tzinfo=UTC)
    fills = [_fill("buy", "A", 101.0, 1, at=next_day), _fill("buy", "A", 100.5, 2)]
    out = slippage.summarise(fills, {("A", SESSION): 100.0}, next_day)
    assert (out.off_session, out.buy.count, out.no_bar) == (1, 1, 0)


def test_an_order_of_a_session_not_yet_closed_is_pending_not_missing() -> None:
    out = slippage.summarise([_fill("buy", "A", 100.5, 1)], {}, NOW)
    assert (out.pending, out.no_bar, out.buy.count) == (1, 0, 0)


def test_window_slippage_reads_the_raw_open_and_counts_a_missing_bar(
    store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bar(store, "A", open_=100.0, close=101.0)  # high 999: only the open may be used
    fills = [_fill("buy", "A", 100.2, 1), _fill("sell", "Z", 5.0, 2)]
    monkeypatch.setattr(slippage.store_journal, "fills_for", lambda *_a, **_k: fills)
    out = slippage.window_slippage(store, 1, AFTER)
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
    assert slippage.window_slippage(store, 1, AFTER).no_bar == 1


def test_the_line_leads_with_the_weighted_mean_and_names_every_exclusion() -> None:
    out = slippage.summarise([_fill("buy", "A", 100.5, 1)], {("A", SESSION): 100.0}, AFTER)
    assert slippage.slippage_line(out) == (
        "slippage vs open (bp, adverse positive, per order): "
        "buy n=1 wmean 50.00 median 50.00 p90 50.00; sell n=0; "
        "all n=1 wmean 50.00 median 50.00 p90 50.00; no bar 0; off session 0; pending 0"
    )


def test_a_fill_without_its_order_session_is_refused_naming_the_fill(
    store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fills = [_fill("buy", "A", 100.5, 7, session=None)]
    monkeypatch.setattr(slippage.store_journal, "fills_for", lambda *_a, **_k: fills)
    with pytest.raises(ValueError, match=r"fill 7 \(b7\)"):
        slippage.window_slippage(store, 1, AFTER)


# --- bounded status read (#1741) ----------------------------------------------------

_OLDER = date(2026, 10, 13)
_MIDDLE = date(2026, 10, 14)
_NEWEST = date(2026, 10, 15)
_SATURDAY = date(2026, 10, 17)
_AFTER_ALL = datetime(2026, 10, 18, 14, tzinfo=UTC)


def _at(session: date) -> datetime:
    return datetime(session.year, session.month, session.day, 15, tzinfo=UTC)


def _bar_on(
    conn: duckdb.DuckDBPyConnection, security: str, session: date, open_: float, close: float
) -> None:
    known = session_close(session)
    conn.execute(
        "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
        "known_at, ingested_at, source, provenance) VALUES (?, ?, ?, 999, 1, ?, 10, ?, ?, "
        "'test', 'bar')",
        [security, session, open_, close, known, known],
    )


def test_a_bounded_read_measures_only_the_newest_sessions(
    store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1741: `paper status` passes `sessions=N`, so the read is bounded to the
    newest N order sessions, one `prices_as_of` per session at most; the whole
    window is never measured."""
    for session in (_OLDER, _MIDDLE, _NEWEST):
        _bar_on(store, "A", session, open_=100.0, close=101.0)
    fills = [
        _fill("buy", "A", 110.0, 1, order="oldest", session=_OLDER, at=_at(_OLDER)),
        _fill("buy", "A", 101.0, 2, order="middle", session=_MIDDLE, at=_at(_MIDDLE)),
        _fill("buy", "A", 102.0, 3, order="newest", session=_NEWEST, at=_at(_NEWEST)),
    ]
    monkeypatch.setattr(slippage.store_journal, "fills_for", lambda *_a, **_k: fills)
    reads: list[datetime] = []
    real = slippage.prices_as_of

    def counting(
        conn: duckdb.DuckDBPyConnection, as_of: datetime, securities: list[str], **kwargs: object
    ) -> object:
        reads.append(as_of)
        return real(conn, as_of, securities, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(slippage, "prices_as_of", counting)
    out = slippage.window_slippage(store, 1, _AFTER_ALL, sessions=2)
    assert len(reads) == 2  # at most the newest two sessions' prices
    assert out.buy.count == 2  # and only those two orders are measured
    assert out.buy.p90_bp == pytest.approx(190.0)  # the oldest order's 1000 bp is absent
    assert session_close(_OLDER) not in reads


def test_an_order_session_that_is_not_a_trading_session_raises(
    store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property's error source (#1741): a fill whose session has no calendar
    entry makes the slippage computation raise, which `paper report` must then
    catch so its row is still written."""
    at = datetime(2026, 10, 17, 15, tzinfo=UTC)
    fills = [_fill("buy", "A", 100.0, 1, session=_SATURDAY, at=at)]
    monkeypatch.setattr(slippage.store_journal, "fills_for", lambda *_a, **_k: fills)
    with pytest.raises(ValueError, match="not an XNYS session: 2026-10-17"):
        slippage.window_slippage(store, 1, _AFTER_ALL)


def test_the_report_line_names_the_reason_when_the_summary_is_unavailable() -> None:
    assert slippage.slippage_report_lines(None, "not an XNYS session: 2026-10-17") == [
        "slippage n/a (not an XNYS session: 2026-10-17)"
    ]
    assert slippage.slippage_report_lines(None) == []


# --- `paper cost`: the measured per-side cost (T383, #3118 item 3) -------------------

_MON, _TUE, _WED, _THU = (
    date(2026, 10, 26),
    date(2026, 10, 27),
    date(2026, 10, 28),
    date(2026, 10, 29),
)
_REBALANCE_RUN, _STOP_RUN = 1, 9


def _cost_fill(
    order: str,
    side: str,
    security: str,
    session: date,
    qty: float,
    price: float,
    fill_id: int,
    *,
    run_id: int = _REBALANCE_RUN,
    known: datetime | None = None,
) -> OrderedFill:
    at = _at(session)
    stamp = known or at
    row = FillRow(
        fill_id=fill_id,
        client_order_id=order,
        filled_at=at,
        quantity=qty,
        price=price,
        price_implied=False,
        broker_fill_id=f"b{fill_id}",
        source="broker_status",
        known_at=stamp,
        ingested_at=stamp,
    )
    return OrderedFill(
        fill=row,
        side=side,
        security_id=security,
        symbol=security,
        run_id=run_id,
        window_id=1,
        order_session=session,
    )


#: Hand-computed: o1 buy +100 bp on 1,010; o2 buy -50 bp on 2,985 (two fills at 99.5);
#: o3 sell +200 bp on 980; o4 sell -100 bp on 2,020. Buys -48,250 / 3,995 = -12.0776;
#: sells -6,000 / 3,000 = -2.0; pooled -54,250 / 6,995 = -7.7556, frozen as -10.
_COST_FILLS = [
    _cost_fill("o1", "buy", "A", _MON, 10, 101.0, 1),
    _cost_fill("o2", "buy", "B", _MON, 10, 99.5, 2),
    _cost_fill("o2", "buy", "B", _MON, 20, 99.5, 3),
    _cost_fill("o3", "sell", "C", _TUE, 20, 49.0, 4),
    _cost_fill("o4", "sell", "D", _TUE, 10, 202.0, 5),
]
_COST_OPENS = {("A", _MON): 100.0, ("B", _MON): 100.0, ("C", _TUE): 50.0, ("D", _TUE): 200.0}
_LATER = datetime(2026, 11, 2, 22, tzinfo=UTC)


def _cost(
    fills: list[OrderedFill],
    *,
    cutoff: datetime = _LATER,
    stop_at: datetime | None = None,
    month_end: frozenset[date] = frozenset(),
    known: dict[str, datetime] | None = None,
    opens: dict[tuple[str, date], float] | None = None,
) -> slippage.MeasuredCost:
    return slippage.measured_cost(
        fills,
        _COST_OPENS if opens is None else opens,
        cutoff,
        stop_run_ids=frozenset({_STOP_RUN}),
        stop_at=stop_at,
        month_end_sessions=month_end,
        orders_known_at=known or {},
    )


def test_the_cost_weights_each_order_by_its_filled_notional_with_the_sign_per_side() -> None:
    out = _cost(_COST_FILLS)
    assert out.slippage.buy.wmean_bp == pytest.approx(-48_250 / 3_995)
    assert out.slippage.sell.wmean_bp == pytest.approx(-2.0)
    assert out.slippage.overall.wmean_bp == pytest.approx(-54_250 / 6_995)
    assert (out.slippage.buy.count, out.slippage.sell.count) == (2, 2)
    assert out.figure_bp == -10.0


def test_the_figure_rounds_to_the_nearest_5_bp_and_a_tie_rounds_up() -> None:
    assert [slippage.round_to_5bp(v) for v in (61.4, 62.4, 62.5, 57.5, -7.7556, -2.5)] == [
        60.0,
        60.0,
        65.0,
        60.0,
        -10.0,
        0.0,
    ]


def test_stop_run_orders_and_orders_on_or_after_the_stop_session_are_left_out() -> None:
    fills = [
        *_COST_FILLS,
        _cost_fill("s1", "sell", "A", _TUE, 5, 1.0, 6, run_id=_STOP_RUN),  # -9,900 bp
        _cost_fill("late", "buy", "A", _WED, 5, 1.0, 7),  # a rebalance after the stop
    ]
    # 22:00 New York on Tuesday is Wednesday in UTC: Tuesday's session had closed,
    # so the stop starts at Wednesday's session and Tuesday's orders stay in.
    out = _cost(fills, stop_at=datetime(2026, 10, 28, 2, tzinfo=UTC))
    assert (out.stop_run, out.after_stop, out.stop_session) == (1, 1, _WED)
    assert out.slippage.overall.wmean_bp == pytest.approx(-54_250 / 6_995)


def test_the_stop_stamp_is_read_as_a_new_york_session() -> None:
    during = slippage.stop_session_of(datetime(2026, 10, 27, 15, tzinfo=UTC))  # 11:00 NY
    evening = slippage.stop_session_of(datetime(2026, 10, 28, 1, tzinfo=UTC))  # 21:00 NY Tue
    weekend = slippage.stop_session_of(datetime(2026, 10, 31, 3, tzinfo=UTC))  # Fri 23:00 NY
    assert (during, evening, weekend) == (_TUE, _WED, date(2026, 11, 2))
    out = _cost(_COST_FILLS, stop_at=datetime(2026, 10, 27, 15, tzinfo=UTC))
    assert (out.after_stop, out.slippage.sell.count) == (2, 0)


def test_no_bar_off_session_and_pending_are_counted_apart_never_an_error() -> None:
    off = _cost_fill("off", "buy", "A", _MON, 1, 100.0, 8)
    off = OrderedFill(
        fill=FillRow(
            **{**off.fill.__dict__, "filled_at": _at(_TUE), "known_at": _at(_TUE)},
        ),
        side=off.side,
        security_id=off.security_id,
        symbol=off.symbol,
        run_id=off.run_id,
        window_id=off.window_id,
        order_session=_MON,
    )
    fills = [
        *_COST_FILLS,
        _cost_fill("nobar", "buy", "Z", _MON, 1, 10.0, 9),
        off,
        _cost_fill("pend", "sell", "A", _THU, 1, 10.0, 10),
    ]
    out = _cost(fills, cutoff=datetime(2026, 10, 29, 16, tzinfo=UTC))  # Thursday, mid-session
    assert (out.slippage.no_bar, out.slippage.off_session, out.slippage.pending) == (1, 1, 1)
    assert out.slippage.overall.wmean_bp == pytest.approx(-54_250 / 6_995)


def test_orders_on_sessions_a_month_end_book_traded_are_figured_beside_the_pool() -> None:
    out = _cost(_COST_FILLS, month_end=frozenset({_MON}))
    assert out.month_end.count == 2
    assert out.month_end.wmean_bp == pytest.approx(-48_250 / 3_995)
    assert out.slippage.overall.count == 4  # still in the pool


def test_nothing_known_after_the_cutoff_changes_the_cost() -> None:
    cutoff = session_close(_TUE)
    later = cutoff + timedelta(minutes=1)
    base = _cost(_COST_FILLS, cutoff=cutoff)
    added = [
        *_COST_FILLS,
        _cost_fill("o4", "sell", "D", _TUE, 90, 1.0, 11, known=later),  # a late fill
        _cost_fill("lateorder", "buy", "A", _TUE, 1, 1.0, 12),  # its order known later
    ]
    out = _cost(added, cutoff=cutoff, known={"lateorder": later})
    assert out == base


def test_the_window_cost_opens_nothing_known_after_the_cutoff(
    store: duckdb.DuckDBPyConnection,
) -> None:
    """Store-backed: a fill, an order and a bar known after the cutoff change nothing."""
    _seed_cost_window(store)
    cutoff = session_close(_TUE)
    before = slippage.window_cost(store, 1, cutoff)
    later = cutoff + timedelta(hours=1)
    _journal_order(store, "late", "buy", "A", _TUE, known=later)
    _journal_fill(store, 20, "late", _TUE, 1, 1.0, known=later)
    _journal_fill(store, 21, "o4", _TUE, 50, 1.0, known=later)
    store.execute(
        "UPDATE prices_daily SET known_at = ?, ingested_at = ? WHERE security_id = 'C'",
        [later, later],
    )
    after = slippage.window_cost(store, 1, cutoff)
    assert before.slippage.overall.wmean_bp == pytest.approx(-54_250 / 6_995)
    assert after.slippage.overall.count == 3  # C's bar is now unknown at the cutoff
    assert after.slippage.no_bar == 1
    assert after.figure_bp is not None
    # once known, the late fill moves o4's VWAP and the late order is counted (no bar
    # for A on Tuesday); C's bar stays unknown as of its own session's close
    much_later = slippage.window_cost(store, 1, later + timedelta(days=30))
    assert much_later.slippage.no_bar == 2
    assert much_later.slippage.sell.wmean_bp != after.slippage.sell.wmean_bp


def test_the_window_cost_reads_stop_runs_the_stop_and_month_end_sessions(
    store: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_cost_window(store)
    _journal_run(store, _STOP_RUN, 1, _WED, "stop")
    _journal_order(store, "s1", "sell", "A", _WED, run_id=_STOP_RUN)
    _journal_fill(store, 30, "s1", _WED, 5, 1.0)
    stamp = datetime(2026, 10, 28, 2, tzinfo=UTC)
    store.execute(
        'INSERT INTO paper_window_stops (window_id, "at", state, known_at, ingested_at) '
        "VALUES (1, ?, 'requested', ?, ?)",
        [stamp, stamp, stamp],
    )
    # window 2 is another book, month_end, which traded on Monday
    store.execute(
        "INSERT INTO paper_windows (window_id, hypothesis_id, first_rebalance_session, "
        "account_id, starting_cash, starting_equity, code_version, started_at, frozen_json, "
        "frozen_sha256, book_id, known_at, ingested_at) VALUES (2, 7, ?, 'PA2', 1, 1, 't', ?, "
        "'{}', ?, 'main', ?, ?)",
        [_MON, _at(_MON), "0" * 64, _at(_MON), _at(_MON)],
    )
    _journal_run(store, 2, 2, _MON, "rebalance")
    _journal_order(store, "m1", "buy", "A", _MON, run_id=2)
    monkeypatch.setattr(
        slippage, "_hypothesis_cadence", lambda _c, hid: "month_end" if hid == 7 else "daily"
    )
    out = slippage.window_cost(store, 1, _LATER)
    assert (out.stop_run, out.stop_session, out.month_end.count) == (1, _WED, 2)
    assert out.slippage.overall.count == 4


def _journal_run(
    conn: duckdb.DuckDBPyConnection, run_id: int, window_id: int, session: date, kind: str
) -> None:
    at = _at(session) - timedelta(hours=7)
    conn.execute(
        "INSERT INTO paper_runs (run_id, window_id, session, kind, started_at, invoked_by, "
        "code_version, known_at, ingested_at) VALUES (?, ?, ?, ?, ?, 'scheduler', 't', ?, ?)",
        [run_id, window_id, session, kind, at, at, at],
    )


def _journal_order(
    conn: duckdb.DuckDBPyConnection,
    order: str,
    side: str,
    security: str,
    session: date,
    *,
    run_id: int = _REBALANCE_RUN,
    known: datetime | None = None,
) -> None:
    at = known or _at(session) - timedelta(hours=6)
    conn.execute(
        "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, phase, "
        "security_id, symbol, side, notional, sells_in_flight_at_submit, known_at, "
        "ingested_at) VALUES (?, 1, ?, ?, 1, ?, ?, ?, ?, 1, false, ?, ?)",
        [order, run_id, session, side, security, security, side, at, at],
    )


def _journal_fill(
    conn: duckdb.DuckDBPyConnection,
    fill_id: int,
    order: str,
    session: date,
    qty: float,
    price: float,
    *,
    known: datetime | None = None,
) -> None:
    at = _at(session)
    stamp = known or at
    conn.execute(
        "INSERT INTO fills (fill_id, client_order_id, filled_at, quantity, price, "
        "price_implied, broker_fill_id, source, known_at, ingested_at) VALUES "
        "(?, ?, ?, ?, ?, false, ?, 'broker_feed', ?, ?)",
        [fill_id, order, at, qty, price, f"b{fill_id}", stamp, stamp],
    )


def _seed_cost_window(conn: duckdb.DuckDBPyConnection) -> None:
    """Window 1 (`daily`) with `_COST_FILLS` journaled and their bars."""
    conn.execute(
        "INSERT INTO paper_windows (window_id, hypothesis_id, first_rebalance_session, "
        "account_id, starting_cash, starting_equity, code_version, started_at, frozen_json, "
        "frozen_sha256, book_id, known_at, ingested_at) VALUES (1, 1, ?, 'PA1', 1, 1, 't', ?, "
        "'{}', ?, 'daily', ?, ?)",
        [_MON, _at(_MON), "0" * 64, _at(_MON), _at(_MON)],
    )
    _journal_run(conn, _REBALANCE_RUN, 1, _MON, "rebalance")
    for item in _COST_FILLS:
        if (
            conn.execute(
                "SELECT 1 FROM orders WHERE client_order_id = ?", [item.fill.client_order_id]
            ).fetchone()
            is None
        ):
            _journal_order(
                conn, item.fill.client_order_id, item.side, item.security_id, item.order_session
            )  # type: ignore[arg-type]
        _journal_fill(
            conn,
            item.fill.fill_id or 0,
            item.fill.client_order_id,
            item.order_session,  # type: ignore[arg-type]
            item.fill.quantity,
            item.fill.price,
        )
    for (security, session), open_ in _COST_OPENS.items():
        _bar_on(conn, security, session, open_=open_, close=open_)
