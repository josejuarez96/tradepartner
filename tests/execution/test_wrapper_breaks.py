"""Restriction behind the existing halt (paper spec amendment 2026-10-26, #2266, items
5 and 7; plan T282a): a name with an open material break takes no added exposure, its
reducing orders are sized to never cross zero on the ledger or the broker, and the
break cap stops the book. Pure `decisions_from` cases first, then the wrapper."""

from __future__ import annotations

import duckdb
import pytest

from tradepartner.adapters.broker import OrderRequest, Position, Side
from tradepartner.config import RiskConfig
from tradepartner.errors import ReconciliationError
from tradepartner.execution.plan import decisions_from
from tradepartner.store import journal
from tradepartner.store.journal import ReconcileBreakRow

from . import test_planning as planning_tests
from .test_decisions_from import (
    COSTS,
    FROZEN,
    SETTINGS,
    _actions,
    _assets,
    _by_name,
    _ledger,
    _override,
    _plan,
    _run,
    price_of,
)
from .test_wrapper_phases import (
    GONE,
    PREV,
    SYMBOLS,
    A,
    B,
    C,
    Env,
    S,
    _append,
    _decision,
    _decision_events,
    _execute,
    _gate,
    _hold,
    _open_sell,
    _query,
    _submits,
)

pytest_plugins = ("execution.test_wrapper_phases",)

env_planning = planning_tests.env


# --- pure: the plan ------------------------------------------------------------------


def _decide(restricted: tuple[str, ...], **kwargs: object):  # type: ignore[no-untyped-def]
    plan = kwargs.pop("plan", None) or _plan()
    ledger = kwargs.pop("ledger", None) or _ledger({"A": 10.0, "C": 5.0, "X": 1.0})
    return decisions_from(
        plan,  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        kwargs.pop("overrides", ()),  # type: ignore[arg-type]
        _assets(),
        {},
        frozenset(),
        FROZEN,
        SETTINGS,
        price_of=price_of,
        actions_as_of=_actions(),
        costs=COSTS,
        restricted=frozenset(restricted),
        **kwargs,  # type: ignore[arg-type]
    )


def test_an_unrestricted_plan_is_todays_decisions() -> None:
    assert _decide(()) == _run()


def test_a_restricted_target_not_held_is_skip_restricted_and_the_rest_trade() -> None:
    got = _by_name(_decide(("B",)))
    b = got["B"]
    assert (b.decision, b.side, b.planned_notional, b.planned_quantity) == (
        "skip_restricted",
        None,
        None,
        None,
    )
    assert got["A"].decision == "trade" and got["A"].side == "buy"
    assert got["C"].reason == "left_targets"


def test_a_restricted_held_name_that_would_add_is_skip_restricted() -> None:
    got = _by_name(_decide(("A",)))
    assert got["A"].decision == "skip_restricted"
    assert got["A"].drifted_weight > 0  # still stated at its current weight


def test_a_restricted_held_name_keeps_its_weight_and_the_rest_is_not_scaled_over_it() -> None:
    """A 30 x 50 = 1500, cash 500, equity 2000: A sits at 0.75 over its 0.5 target.
    Unrestricted, A is sold down. Restricted it keeps its weight (nothing is sold) and
    B shares only the 0.25 left: 0.5 x 0.5 x 2000 = 500."""
    ledger = _ledger({"A": 30.0}, cash=500.0)
    free = _by_name(_decide((), ledger=ledger))
    assert free["A"].side == "sell"
    got = _by_name(_decide(("A",), ledger=ledger))
    assert got["A"].decision == "skip_zero" and got["A"].side is None
    assert got["B"].planned_notional == pytest.approx(500.0)


def test_a_restricted_name_leaving_the_targets_is_still_sold_whole() -> None:
    got = _by_name(_decide(("C", "X")))
    assert (got["C"].decision, got["C"].side, got["C"].planned_quantity) == ("trade", "sell", 5.0)
    assert (got["X"].decision, got["X"].reason) == ("trade", "left_universe")


def test_a_restricted_name_leaving_the_targets_does_not_hold_weight_from_the_others() -> None:
    """Its exit proceeds are the others' budget: sizes equal the unrestricted plan's."""
    free = _by_name(_decide(()))
    got = _by_name(_decide(("C",)))
    assert got["A"].planned_notional == free["A"].planned_notional
    assert got["B"].planned_notional == free["B"].planned_notional


def test_a_restricted_exclude_name_override_still_sells() -> None:
    got = _by_name(_decide(("A",), overrides=(_override("exclude_name", "A", override_id=5),)))
    assert (got["A"].decision, got["A"].side, got["A"].reason) == (
        "override",
        "sell",
        "exclude_name",
    )


def test_skip_restricted_is_not_a_plan_order_and_leaves_the_live_capital_count() -> None:
    free, held = _run(), _decide(("B",))
    assert held.n_orders_below_min_at_live_capital <= free.n_orders_below_min_at_live_capital


# --- the wrapper -----------------------------------------------------------------------


def _break(
    env: Env,
    security_id: str,
    *,
    material: bool = True,
    size: float | None = 10.0,
    event: str = "opened",
    n: int = 1,
) -> ReconcileBreakRow:
    at = env.run.started_at
    return ReconcileBreakRow(
        break_id=f"{env.window.window_id}:{security_id}:{n}",
        window_id=env.window.window_id,  # type: ignore[arg-type]
        book_id=env.window.book_id,
        event=event,
        parts_json="[]",
        cause="unattributed",
        security_id=security_id,
        material=material,
        dollar_size=size,
        first_seen_session=PREV,
        reconciliation_id=1,
        reason="the owner's reason" if event in ("trued_up", "reopened") else None,
        known_at=at,
        ingested_at=at,
    )


def _broker_sells(env: Env, security_id: str, quantity: float) -> None:
    """Take `quantity` of the name out of the broker only, as an outside sale would."""
    coid = f"fx-away-{security_id}"
    env.fake.submit(OrderRequest(coid, SYMBOLS[security_id], Side.SELL, quantity=quantity))
    env.fake.simulate_fill(coid)


def test_a_buy_on_a_restricted_name_is_skip_restricted_and_the_others_submit(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _append(env.settings, _break(env, A))
    blocked = _decision(env, A, "buy", notional=3000.0)
    kept = _decision(env, B, "buy", notional=3000.0)

    outcome = _execute(_gate(env, alerter_conn), env, [blocked, kept])

    assert [r.symbol for r in _submits(env.fake)] == ["DUALB"]
    assert (blocked.decision_id, "skipped", "skip_restricted") in _decision_events(env.settings)
    assert {s.reason for s in outcome.skips} == {"skip_restricted"}


def test_skip_restricted_does_not_count_toward_the_skip_cap(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    for name in (A, B, C):
        _append(env.settings, _break(env, name))
    decisions = [_decision(env, name, "buy", notional=3000.0) for name in (A, B, C)]
    frozen = RiskConfig(max_skips_per_run=0, max_skip_share=0.0, max_open_breaks=3)

    outcome = _execute(_gate(env, alerter_conn, frozen), env, decisions)  # no SkipCapError

    assert _submits(env.fake) == []
    assert [s.reason for s in outcome.skips] == ["skip_restricted"] * 3


def test_a_sell_of_a_restricted_name_is_sized_to_the_smaller_of_ledger_and_broker(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    _broker_sells(env, A, 4.0)  # broker 6, ledger 10
    _append(env.settings, _break(env, A))
    sell = _decision(env, A, "sell", quantity=10.0, decision="trade", reason="left_targets")

    _execute(_gate(env, alerter_conn), env, [sell])

    (request,) = _submits(env.fake)
    assert (request.side, request.quantity) == (Side.SELL, 6.0)


def test_a_forced_exit_of_a_restricted_name_never_crosses_zero_on_the_broker(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    _broker_sells(env, A, 7.0)  # broker 3
    _append(env.settings, _break(env, A))
    exit_ = _decision(env, A, "sell", quantity=10.0, decision="forced_exit", reason="window_stop")

    _execute(_gate(env, alerter_conn), env, [], (exit_,))

    (request,) = _submits(env.fake)
    assert request.quantity == 3.0


def test_an_open_own_sell_comes_off_the_broker_side_of_the_limit(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """Ledger 10, broker 7, 4 already open: at most 3 more may go, never 6."""
    _hold(env, A, 10.0)
    _broker_sells(env, A, 3.0)
    _open_sell(env, A, 4.0)
    _append(env.settings, _break(env, A))
    exit_ = _decision(env, A, "sell", quantity=10.0, decision="forced_exit", reason="window_stop")

    _execute(_gate(env, alerter_conn), env, [], (exit_,))

    (request,) = _submits(env.fake)
    assert request.quantity == 3.0


def test_a_sell_when_the_broker_holds_none_is_skipped_not_sent_short(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    _broker_sells(env, A, 10.0)  # broker flat, ledger 10
    _append(env.settings, _break(env, A))
    sell = _decision(env, A, "sell", quantity=10.0, decision="trade", reason="left_targets")

    outcome = _execute(_gate(env, alerter_conn), env, [sell])

    assert _submits(env.fake) == []
    assert [s.reason for s in outcome.skips] == ["skip_restricted"]


def test_a_sell_within_both_books_is_sent_whole(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    _append(env.settings, _break(env, A))
    sell = _decision(env, A, "sell", quantity=10.0, decision="trade", reason="left_targets")

    _execute(_gate(env, alerter_conn), env, [sell])

    (request,) = _submits(env.fake)
    assert request.quantity == 10.0


def test_an_immaterial_or_closed_break_restricts_nothing(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _append(
        env.settings,
        _break(env, A, material=False),
        _break(env, B),
        _break(env, B, event="trued_up"),
    )
    buys = [_decision(env, A, "buy", notional=3000.0), _decision(env, B, "buy", notional=3000.0)]

    _execute(_gate(env, alerter_conn), env, buys)

    assert sorted(r.symbol for r in _submits(env.fake)) == ["DUALA", "DUALB"]


def test_no_break_reads_no_broker_position(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    before = len(env.fake.calls)
    sell = _decision(env, A, "sell", quantity=10.0, decision="trade", reason="left_targets")

    _execute(_gate(env, alerter_conn), env, [sell])

    assert "positions" not in {c.method for c in env.fake.calls[before:]}


@pytest.mark.parametrize("names", [(A, B, C, GONE)])
def test_four_material_breaks_stop_the_book_before_any_order(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, names: tuple[str, ...]
) -> None:
    for name in names:
        _append(env.settings, _break(env, name))
    buy = _decision(env, B, "buy", notional=3000.0)

    with pytest.raises(ReconciliationError, match="break_cap"):
        _execute(_gate(env, alerter_conn), env, [buy])

    assert _submits(env.fake) == []


def test_one_break_above_two_percent_of_equity_stops_the_book(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _append(env.settings, _break(env, A, size=1e9))
    buy = _decision(env, B, "buy", notional=3000.0)

    with pytest.raises(ReconciliationError, match="break_cap"):
        _execute(_gate(env, alerter_conn), env, [buy])

    assert _submits(env.fake) == []


def test_three_small_material_breaks_do_not_stop_the_book(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _append(env.settings, *(_break(env, name) for name in (A, C, GONE)))
    buy = _decision(env, B, "buy", notional=3000.0)

    _execute(_gate(env, alerter_conn), env, [buy])

    assert [r.symbol for r in _submits(env.fake)] == ["DUALB"]
    assert _query(env.settings, "SELECT count(*) FROM kill_switch") == [(0,)]


# --- the planning step ---------------------------------------------------------------


def _plan_break(
    env: planning_tests.Env, security_id: str, *, size: float = 10.0, n: int = 1
) -> None:
    at = env.run.started_at
    journal.append(
        env.conn,
        ReconcileBreakRow(
            break_id=f"{env.window.window_id}:{security_id}:{n}",
            window_id=env.window.window_id,  # type: ignore[arg-type]
            book_id=env.window.book_id,
            event="opened",
            parts_json="[]",
            cause="unattributed",
            security_id=security_id,
            material=True,
            dollar_size=size,
            first_seen_session=planning_tests.T_I,
            reconciliation_id=n,
            known_at=at,
            ingested_at=at,
        ),
    )


def test_planning_writes_skip_restricted_for_a_restricted_target_and_still_exits(
    env_planning: planning_tests.Env,
) -> None:
    free = {d.security_id: d for d in env_planning.plan().decisions}
    target = next(sid for sid, d in free.items() if d.side == "buy")
    env_planning.conn.execute("DELETE FROM decisions")  # replan on the same store
    env_planning.conn.execute("DELETE FROM paper_plans")
    _plan_break(env_planning, target)
    outcome = env_planning.plan()
    got = {d.security_id: d for d in outcome.decisions}
    assert (got[target].decision, got[target].side) == ("skip_restricted", None)
    assert {sid: d.reason for sid, d in got.items() if d.side == "sell"} == {
        sid: d.reason for sid, d in free.items() if d.side == "sell"
    }


def test_planning_past_the_break_cap_plans_nothing(env_planning: planning_tests.Env) -> None:
    for n, sid in enumerate(("SEC_A", "SEC_B", "SEC_C", "SEC_D"), 1):
        _plan_break(env_planning, sid, n=n)
    before = env_planning.counts()
    with pytest.raises(ReconciliationError, match="break_cap"):
        env_planning.plan()
    assert env_planning.counts() == before


def _cover(
    env: Env,
    alerter_conn: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
    *,
    broker: float,
    open_cover: float = 0.0,
) -> list[float | None]:
    """Restrict a cover of 10 on a name the ledger is short 10 and the broker `broker`,
    with an open short-opening sell of 4 and an open cover of `open_cover`."""
    from dataclasses import replace as _replace
    from datetime import timedelta

    from tradepartner.execution.ledger import Ledger
    from tradepartner.execution.phases import PhaseOrder, PhaseOrders
    from tradepartner.store.journal import OrderEventRow, OrderRow

    _append(env.settings, _break(env, A))
    gate = _gate(env, alerter_conn)
    monkeypatch.setattr(env.fake, "positions", lambda: {SYMBOLS[A]: Position(SYMBOLS[A], broker)})
    _hold(env, A, 10.0)
    _open_sell(env, A, 4.0)  # a sell that, on a short, deepens it
    if open_cover:
        at = env.run.started_at - timedelta(hours=1)
        decision = _decision(env, A, "buy", notional=1.0)
        _append(
            env.settings,
            OrderRow(
                client_order_id="fx-cover",
                decision_id=decision.decision_id,  # type: ignore[arg-type]
                run_id=env.earlier.run_id,
                session=PREV,
                attempt=1,
                phase="buy",
                security_id=A,
                symbol=SYMBOLS[A],
                side="buy",
                quantity=open_cover,
                position_side="short",
                sells_in_flight_at_submit=False,
                known_at=at,
                ingested_at=at,
            ),
            OrderEventRow(
                client_order_id="fx-cover", status="pending", known_at=at, ingested_at=at
            ),
        )
    book = gate._read_book(env.run, [_decision(env, A, "sell", quantity=1.0)])
    book = _replace(
        book, ledger=Ledger(positions={}, short_holdings={A: 10.0}, cash=0.0, through=S)
    )
    cover = PhaseOrder(
        decision_id=1,
        security_id=A,
        side="buy",
        decision="trade",
        price=100.0,
        quantity=10.0,
        whole_share=True,
        position_side="short",
    )
    out = gate._restrict(env.run, book, PhaseOrders((cover,), (), session=S), S)
    return [o.quantity for o in out.orders]


def test_a_short_cover_is_cut_to_the_smaller_short_and_ignores_open_short_sells(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ledger short 10, broker short 6, an open short-opening sell of 4: the cover is
    cut to 6 (the sell deepens the short and must not widen the limit)."""
    assert _cover(env, alerter_conn, monkeypatch, broker=-6.0) == [6.0]


def test_an_open_cover_comes_off_both_sides_of_a_shorts_limit(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _cover(env, alerter_conn, monkeypatch, broker=-7.0, open_cover=4.0) == [3.0]


def test_a_cover_the_open_cover_leaves_nothing_for_is_skipped(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _cover(env, alerter_conn, monkeypatch, broker=-3.0, open_cover=4.0) == []
