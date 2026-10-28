"""A failing in-window run of book A leaves book B's rows and broker calls
unchanged (#2459; ADR 0017 B.3, ADR 0015 seam 1).

Two books share one store, each with an open rebalance window and its own
account and scripted fake. Book A's broker raises on `submit`; both books run.
Book B's orders, fills and fake-broker call log must equal a baseline run of B
alone (same setup, A never runs), and no row of A carries B's account.

The harness is `test_run_trade.py`'s `Env`, one per book on one store copy.
Ids (`run_id`, `decision_id`, ...) legitimately differ between the two runs
(A's failed run takes the first), so the comparison reads the ids' content:
client order ids carry the book token, not a run id, and compare byte-equal.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tradepartner.adapters.broker import OrderRequest
from tradepartner.adapters.fake_broker import FakeBroker
from tradepartner.config import Settings

from .test_run_trade import (
    BROKER_ASSETS,
    F_0,
    FAKE_CASH,
    Clock,
    Env,
    _no_env_file,  # noqa: F401  (autouse fixture)
    at,
)

BOOK_A, BOOK_B = "aaaa", "bbbb"
ACCOUNT_A, ACCOUNT_B = "PA1", "PB1"
NOW = at(F_0)


class SubmitDown(Exception):
    """Book A's broker refusing to take any order."""


def _book_env(store: Path, book: str, account: str, clock: Clock) -> Env:
    settings = Settings(
        _env_file=None,
        store={"path": str(store)},
        alpaca={"quantity_decimals": 6, "client_order_id_max_length": 48},
        paper={"book_id": book},
    )
    env = Env(settings, clock)
    env.fake = FakeBroker(
        assets=FakeBroker.active_assets(*BROKER_ASSETS),
        clock=clock,
        price_of=lambda symbol: env.prices[symbol],
        auto_fill=False,
        cash=FAKE_CASH,
        account_id=account,
    )
    return env


def _two_books(store: Path, tmp_path: Path) -> tuple[Env, Env]:
    """Books A and B, each with an open window on its own account, in `store`."""
    clock = Clock(NOW)
    a = _book_env(store, BOOK_A, ACCOUNT_A, clock)
    b = _book_env(store, BOOK_B, ACCOUNT_B, Clock(NOW))
    a_window = a.open_window(tmp_path=tmp_path)
    b_row = replace(a_window, window_id=None, account_id=ACCOUNT_B, book_id=BOOK_B)
    (b_id,) = b.append(b_row)
    b.window = replace(b_row, window_id=b_id)
    return a, b


def _fail_a_submit(a: Env) -> list[OrderRequest]:
    attempts: list[OrderRequest] = []

    def refuse(request: OrderRequest) -> None:
        attempts.append(request)
        raise SubmitDown("book A's broker is down")

    a.fake.on_submit = refuse
    return attempts


_ATTEMPT = re.compile(r"(-(?:buy|sell|short|cover))-\d+$")


def _unattempted(value: Any) -> Any:
    """`value` with the attempt counter of any client order id in it dropped.

    `journal.orders_on_session` counts attempts across windows on purpose (ids
    are unique store-wide), so A's journaled order on a name moves B's first
    attempt on that name from 1 to 2. The id still carries B's book token, so it
    cannot collide; the counter is the one thing that legitimately differs."""
    if isinstance(value, str):
        return _ATTEMPT.sub(r"\1", value)
    if isinstance(value, OrderRequest):
        return replace(value, client_order_id=_unattempted(value.client_order_id))
    if isinstance(value, tuple):
        return tuple(_unattempted(v) for v in value)
    return value


def _observed(env: Env) -> dict[str, Any]:
    """What book B's run left: its orders and fills (by content, not run ids),
    its window's results, and every call its fake saw."""
    book = env.settings.paper.book_id
    orders = [
        (_unattempted(r[0]), *r[1:5], *r[6:])
        for r in env.query(
            "SELECT o.client_order_id, o.security_id, o.side, o.phase, o.session, o.attempt, "
            "o.quantity FROM orders o JOIN paper_runs r USING (run_id) "
            "JOIN paper_windows w USING (window_id) WHERE w.book_id = ? "
            "ORDER BY o.client_order_id",
            [book],
        )
    ]
    fills = [
        (_unattempted(r[0]), *r[1:])
        for r in env.query(
            "SELECT f.client_order_id, f.filled_at, f.quantity, f.price, f.source, f.price_implied "
            "FROM fills f JOIN orders o USING (client_order_id) JOIN paper_runs r USING (run_id) "
            "JOIN paper_windows w USING (window_id) WHERE w.book_id = ? "
            "ORDER BY f.client_order_id, f.broker_fill_id",
            [book],
        )
    ]
    calls = [(c.method, _unattempted(c.args)) for c in env.fake.calls]
    return {
        "orders": orders,
        "fills": fills,
        "calls": calls,
        "held": env.held(),
        "kill": env.query(
            "SELECT k.state FROM kill_switch k JOIN paper_windows w USING (window_id) "
            "WHERE w.book_id = ? ORDER BY k.event_id",
            [book],
        ),
    }


def _both_ways(fixture_store_path: Path, tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """(B alone, B after A's failed run), as `_observed` reads them."""
    baseline_store = tmp_path / "baseline.duckdb"
    shutil.copy(fixture_store_path, baseline_store)

    # Baseline: both windows open, only B runs.
    _, b_alone = _two_books(baseline_store, tmp_path / "base")
    assert b_alone.run(NOW).status == "ok"
    baseline = _observed(b_alone)
    assert len(baseline["orders"]) == 3, "the baseline must actually trade"
    assert baseline["fills"], "the baseline must actually fill"

    # Together: A's submit raises, then B runs.
    a, b = _two_books(fixture_store_path, tmp_path / "both")
    attempts = _fail_a_submit(a)
    with pytest.raises(SubmitDown):
        a.run(NOW)
    assert attempts, "book A must have reached its submit"
    assert b.run(NOW).status == "ok"
    return baseline, _observed(b)


def test_a_failing_run_of_book_a_leaves_book_bs_orders_and_call_shape_unchanged(
    fixture_store_path: Path, tmp_path: Path
) -> None:
    baseline, together = _both_ways(fixture_store_path, tmp_path)
    assert together["orders"] == baseline["orders"]
    assert [c[0] for c in together["calls"]] == [c[0] for c in baseline["calls"]]
    assert together["kill"] == baseline["kill"] == []
    assert sorted(together["held"]) == sorted(baseline["held"])


def test_a_failing_run_of_book_a_leaves_book_bs_fills_and_broker_calls_equal_to_a_solo_run(
    fixture_store_path: Path, tmp_path: Path
) -> None:
    baseline, together = _both_ways(fixture_store_path, tmp_path)
    assert together["fills"] == baseline["fills"]
    assert together["calls"] == baseline["calls"]
    assert together["held"] == baseline["held"]


def test_no_row_of_book_a_carries_book_bs_account(fixture_store_path: Path, tmp_path: Path) -> None:
    a, b = _two_books(fixture_store_path, tmp_path)
    _fail_a_submit(a)
    with pytest.raises(SubmitDown):
        a.run(NOW)
    assert b.run(NOW).status == "ok"
    assert b.count("fills") > 0, "B bought, so the account check has rows to see"

    for table in ("paper_windows", "lots", "disposals"):
        rows = b.query(f"SELECT book_id, account_id FROM {table}")
        for book, account in rows:
            expected = {BOOK_A: ACCOUNT_A, BOOK_B: ACCOUNT_B}[book]
            assert account == expected, (table, book, account)

    # Book A's journal: its window, runs and orders are its own, and A's broker
    # never saw B's account (B's orders never reached A's fake).
    b_ids = {
        row[0]
        for row in b.query(
            "SELECT o.client_order_id FROM orders o JOIN paper_runs r USING (run_id) "
            "JOIN paper_windows w USING (window_id) WHERE w.book_id = ?",
            [BOOK_B],
        )
    }
    a_ids = {c.args[0].client_order_id for c in a.fake.calls if c.method == "submit"}
    assert a_ids and not (a_ids & b_ids)
    assert all(f"-{BOOK_A}-" in cid for cid in a_ids)
    assert all(f"-{BOOK_B}-" in cid for cid in b_ids)


def test_the_reserve_reads_only_its_own_books_orders_and_all_of_them(
    fixture_store_path: Path, tmp_path: Path
) -> None:
    """#2475: book A's journaled open buys never reach book B's reserve; B's own
    orders (any window or session) all do."""
    from tradepartner.execution.wrapper import _own_book_rows

    a, b = _two_books(fixture_store_path, tmp_path)
    _fail_a_submit(a)
    with pytest.raises(SubmitDown):
        a.run(NOW)
    assert b.run(NOW).status == "ok"
    with b.connect() as conn:
        a_orders, a_events, a_fills = _own_book_rows(conn, BOOK_A)
        b_orders, b_events, b_fills = _own_book_rows(conn, BOOK_B)
    assert len(a_orders) == 3 and len(b_orders) == 3
    assert not {o.client_order_id for o in a_orders} & {o.client_order_id for o in b_orders}
    assert {e.client_order_id for e in a_events} <= {o.client_order_id for o in a_orders}
    assert {e.client_order_id for e in b_events} <= {o.client_order_id for o in b_orders}
    assert not a_fills
    assert b_fills, "B's own fills are read"


def test_book_bs_buys_left_open_by_an_earlier_window_still_count_and_as_never_do(
    fixture_store_path: Path, tmp_path: Path
) -> None:
    """#2475: with B on a second (later) window, its first window's journaled buys
    are still in B's reserve reads; A's open buys are in neither."""
    from tradepartner.execution.wrapper import _own_book_rows

    a, b = _two_books(fixture_store_path, tmp_path)
    _fail_a_submit(a)
    with pytest.raises(SubmitDown):
        a.run(NOW)
    assert b.run(NOW).status == "ok"
    first_window = b.window.window_id
    (second_id,) = b.append(replace(b.window, window_id=None))
    assert second_id != first_window
    with b.connect() as conn:
        orders, _, _ = _own_book_rows(conn, BOOK_B)
        windows = {
            r[0]
            for r in conn.execute(
                "SELECT r.window_id FROM orders o JOIN paper_runs r USING (run_id) "
                "WHERE o.client_order_id IN (SELECT unnest(?))",
                [[o.client_order_id for o in orders]],
            ).fetchall()
        }
    assert len(orders) == 3
    assert windows == {first_window}, "B's buys come from its earlier window, not the new one"
    assert all(o.book_id == BOOK_B for o in orders)
