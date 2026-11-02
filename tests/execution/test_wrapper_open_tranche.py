"""Phase (i'), the open tranche, in the wrapper (paper spec amendment 2026-11-01
item 2 and item 10's wrapper lines; plan T371).

Every case runs `RiskGatedBroker.execute` on the fixture store with the scripted
fake, the clock 20 minutes before open(S) unless it says otherwise. A sell left
`Accept`ed stays in flight, so (ii) waits for the sells' deadline after the open."""

from __future__ import annotations

import itertools
import random
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import duckdb
import pytest

from tradepartner.adapters.broker import Account, OrderRequest, Side
from tradepartner.adapters.fake_broker import Accept, Expire, FillAt
from tradepartner.calendar import session_open
from tradepartner.config import BookExecution
from tradepartner.errors import LimitBreachError, SystemFaultError
from tradepartner.execution import phases, wrapper
from tradepartner.execution.plan import State
from tradepartner.store.journal import DecisionRow

from .test_wrapper_phases import (
    A,
    B,
    C,
    Env,
    S,
    _decision,
    _gate,
    _hold,
    _override,
    _query,
    _submits,
)

pytest_plugins = ("execution.test_wrapper_phases", "execution.test_wrapper_buys")

OPEN = session_open(S)


def _mapped(env: Env, **execution: Any) -> None:
    """Put the env's book in `paper.execution` (V1 every session by default)."""
    execution.setdefault("buy_timing", "at_open")
    paper = env.settings.paper.model_copy(
        update={"execution": {env.window.book_id: BookExecution(**execution)}}
    )
    env.settings = env.settings.model_copy(update={"paper": paper})


def _line(notes: list[str]) -> dict[str, Any]:
    (line,) = [n for n in notes if n.startswith("paper_execution ")]
    return wrapper.paper_execution_fields(line)


def _times(env: Env) -> list[tuple[OrderRequest, datetime]]:
    times: list[tuple[OrderRequest, datetime]] = []
    env.fake.on_submit = lambda request: times.append((request, env.clock.now))
    return times


def _orders(env: Env, decision: DecisionRow) -> list[tuple[int, bool]]:
    return [
        (attempt, in_flight)
        for attempt, in_flight in _query(
            env.settings,
            "SELECT attempt, sells_in_flight_at_submit FROM orders WHERE decision_id = ?"
            " ORDER BY attempt",
            [decision.decision_id],
        )
    ]


def _written_off(env: Env) -> list[int]:
    return [
        d
        for (d,) in _query(
            env.settings, "SELECT decision_id FROM decision_events WHERE status = 'written_off'"
        )
    ]


def _batch(env: Env, b: float = 400.0, c: float = 300.0, **c_kwargs: Any) -> list[DecisionRow]:
    """A sell of the held A and buys of B and C."""
    _hold(env, A, 10.0)
    return [
        _decision(env, A, "sell", notional=500.0),
        _decision(env, B, "buy", notional=b),
        _decision(env, C, "buy", notional=c, **c_kwargs),
    ]


def _count_reads(env: Env) -> list[float]:
    reads: list[float] = []
    account = env.fake.account

    def counted() -> Account:
        reading = account()
        reads.append(reading.cash)
        return reading

    env.fake.account = counted  # type: ignore[method-assign]
    return reads


def test_v1_sends_the_sells_then_the_open_tranche_before_the_open_from_one_read(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """Item 10's V1 line, and (ii) of a run where every buy settled in (i') sends
    nothing and writes off nothing."""
    env = timed_env
    _mapped(env)
    env.new_fake(cash=5000.0)
    sell, buy_b, buy_c = _batch(env)
    env.fake.script(Accept())  # the sell stays in flight: no proceeds before the open
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    times = _times(env)
    reads = _count_reads(env)
    held = env.fake.account().cash
    reads.clear()
    notes: list[str] = []

    outcome = _gate(env, alerter_conn).execute(env.run, [sell, buy_b, buy_c], (), notes=notes)

    assert outcome.status == "ok"
    # Ascending remainder: C (300) before B (400); all three before the open.
    assert [(r.side, r.symbol) for r, _ in times] == [
        (Side.SELL, "DUALA"),
        (Side.BUY, "PREN"),
        (Side.BUY, "DUALB"),
    ]
    assert all(stamp < OPEN for _, stamp in times)
    assert [r.notional for r, _ in times[1:]] == [300.0, 400.0]
    assert reads == [held]  # one read: (i')'s; (ii) had nothing to buy
    assert _orders(env, buy_b) == [(1, True)] and _orders(env, buy_c) == [(1, True)]
    assert outcome.written_off == () and _written_off(env) == []
    assert outcome.cash is None  # (ii) read no cash
    fields = _line(notes)
    assert (fields["arm"], fields["fallback"]) == ("V1", "none")
    assert (fields["open_buys"], fields["open_notional"], fields["open_unsent"]) == (2, 700.0, 0)
    assert fields["idle_cash"] == pytest.approx(held)
    assert fields["min_cash"] == pytest.approx(held)


def test_a_whole_share_buy_is_never_in_the_open_tranche(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """A whole-share buy waits for (ii), which prices it after the open."""
    env = timed_env
    _mapped(env)
    env.new_fake(cash=5000.0)
    sell, notional, whole = _batch(env, b=300.0, c=250.0, whole_share=True)
    env.fake.script(Accept())
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    times = _times(env)
    notes: list[str] = []

    _gate(env, alerter_conn).execute(env.run, [sell, notional, whole], (), notes=notes)

    early = [r.symbol for r, stamp in times if r.side is Side.BUY and stamp < OPEN]
    assert early == ["DUALB"]
    (late,) = [r for r, stamp in times if r.side is Side.BUY and stamp >= OPEN]
    assert late.symbol == "PREN" and late.quantity is not None and late.notional is None
    assert _orders(env, whole) == [(1, True)]  # (ii)'s first attempt
    assert _line(notes)["open_buys"] == 1


def test_a_buy_over_b_is_left_for_ii_at_its_full_remainder(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """A name over B is left whole for (ii); the one that fits is taken."""
    env = timed_env
    _mapped(env)
    env.new_fake(cash=1400.0)  # 400 after the held A
    sell, big, small = _batch(env, b=5000.0, c=300.0)
    env.fake.script(Accept())
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    times = _times(env)
    notes: list[str] = []

    _gate(env, alerter_conn).execute(env.run, [sell, big, small], (), notes=notes)

    early = [(r.symbol, r.notional) for r, stamp in times if r.side is Side.BUY and stamp < OPEN]
    assert early == [("PREN", 300.0)]
    late = [r for r, stamp in times if r.side is Side.BUY and stamp >= OPEN]
    assert [r.symbol for r in late] == ["DUALB"]
    assert _orders(env, big) == [(1, True)]  # its first attempt is (ii)'s


def test_check_phase_refuses_an_over_filled_selection_before_any_buy(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = timed_env
    _mapped(env)
    env.new_fake(cash=1400.0)  # 400 after the held A
    sell, buy_b, buy_c = _batch(env)
    env.fake.script(Accept())
    real = phases.open_tranche

    def over_filled(buys: Any, budget: float, *args: Any, **kwargs: Any) -> Any:
        return real(buys, budget * 10, *args, **kwargs)

    monkeypatch.setattr(wrapper.phases, "open_tranche", over_filled)
    notes: list[str] = []

    with pytest.raises(LimitBreachError):
        _gate(env, alerter_conn).execute(env.run, [sell, buy_b, buy_c], (), notes=notes)

    assert [r.side for r in _submits(env.fake)] == [Side.SELL]
    assert _orders(env, buy_b) == [] and _orders(env, buy_c) == []


def test_an_open_tranche_buy_expired_unfilled_is_re_attempted_in_ii(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env = timed_env
    _mapped(env)
    env.new_fake(cash=5000.0)
    sell, buy_b, buy_c = _batch(env)
    env.fake.script(FillAt())  # the sell
    env.fake.script(Expire())  # C, (i')
    env.fake.script(FillAt())  # B, (i')
    env.fake.script(FillAt())  # C, (ii)
    notes: list[str] = []

    outcome = _gate(env, alerter_conn).execute(env.run, [sell, buy_b, buy_c], (), notes=notes)

    assert outcome.status == "ok"
    assert _orders(env, buy_c) == [(1, True), (2, False)]
    assert _orders(env, buy_b) == [(1, True)]
    assert buy_c.decision_id not in _written_off(env)
    assert len(_submits(env.fake)) == 4


def test_an_open_tranche_buy_in_flight_at_ii_gets_no_second_order(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env = timed_env
    _mapped(env)
    env.new_fake(cash=5000.0)
    sell, buy_b, buy_c = _batch(env)
    env.fake.script(FillAt())  # the sell
    env.fake.script(Accept())  # C stays live
    env.fake.script(FillAt())  # B
    notes: list[str] = []
    gate = _gate(env, alerter_conn)

    outcome = gate.execute(env.run, [sell, buy_b, buy_c], (), notes=notes)

    assert outcome.status == "ok"
    assert _orders(env, buy_c) == [(1, True)]
    assert len(_submits(env.fake)) == 3
    book = gate._read_book(env.run, [sell, buy_b, buy_c])
    assert book.states[buy_c.decision_id].state is State.IN_FLIGHT  # type: ignore[index]
    assert buy_c.decision_id not in _written_off(env)


def test_a_v1_run_reaching_i_prime_at_the_open_runs_v0_and_notes_it(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env = timed_env
    _mapped(env)
    env.new_fake(cash=5000.0)
    sell, buy_b, buy_c = _batch(env)
    env.clock.now = OPEN + timedelta(minutes=1)
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    notes: list[str] = []

    _gate(env, alerter_conn).execute(env.run, [sell, buy_b, buy_c], (), notes=notes)

    fields = _line(notes)
    assert (fields["arm"], fields["fallback"], fields["open_buys"]) == ("V1", "after_open", 0)
    # (ii) sent both; the sell was terminal by then, so this was the last phase.
    assert _orders(env, buy_b) == [(1, False)] and _orders(env, buy_c) == [(1, False)]


def test_the_open_arriving_mid_tranche_closes_the_unsent_rows_and_ii_buys_them(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env = timed_env
    _mapped(env)
    env.new_fake(cash=5000.0)
    sell, buy_b, buy_c = _batch(env)
    env.fake.script(Accept())
    env.fake.script(FillAt())
    env.fake.script(FillAt())

    def at_the_open(request: OrderRequest) -> None:
        if request.side is Side.BUY and env.clock.now < OPEN:
            env.clock.now = OPEN

    env.fake.on_submit = at_the_open
    notes: list[str] = []

    outcome = _gate(env, alerter_conn).execute(env.run, [sell, buy_b, buy_c], (), notes=notes)

    assert outcome.status == "ok"
    assert _orders(env, buy_c) == [(1, True)]
    assert _orders(env, buy_b) == [(1, True), (2, True)]  # closed unsent, then (ii)'s
    closed = _query(
        env.settings,
        "SELECT e.status, e.reason FROM order_events e JOIN orders o USING (client_order_id)"
        " WHERE o.decision_id = ? AND o.attempt = 1 AND e.status = 'cancelled'",
        [buy_b.decision_id],
    )
    assert closed == [("cancelled", "not_received")]
    b_orders = [r for r in _submits(env.fake) if r.symbol == "DUALB"]
    assert [r.notional for r in b_orders] == [400.0]  # only (ii)'s reached the broker
    fields = _line(notes)
    assert (fields["arm"], fields["fallback"]) == ("V1", "none")
    assert (fields["open_buys"], fields["open_unsent"]) == (1, 1)


def test_a_switch_engaged_after_the_sells_sends_no_open_tranche_order(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env = timed_env
    _mapped(env)
    env.new_fake(cash=5000.0)
    sell, buy_b, buy_c = _batch(env)
    env.fake.script(Accept())
    env.fake.on_submit = lambda _request: _override(env)
    notes: list[str] = []

    outcome = _gate(env, alerter_conn).execute(env.run, [sell, buy_b, buy_c], (), notes=notes)

    assert outcome.status == "skipped_kill_switch"
    assert [r.side for r in _submits(env.fake)] == [Side.SELL]
    assert _line(notes)["open_buys"] == 0


def test_a_switch_engaged_mid_tranche_stops_it_and_runs_no_ii(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env = timed_env
    _mapped(env)
    env.new_fake(cash=5000.0)
    sell, buy_b, buy_c = _batch(env)
    env.fake.script(Accept())
    env.fake.script(FillAt())

    def kill_on_buy(request: OrderRequest) -> None:
        if request.side is Side.BUY:
            _override(env)

    env.fake.on_submit = kill_on_buy
    notes: list[str] = []

    outcome = _gate(env, alerter_conn).execute(env.run, [sell, buy_b, buy_c], (), notes=notes)

    assert outcome.status == "skipped_kill_switch"
    assert [r.side for r in _submits(env.fake)] == [Side.SELL, Side.BUY]
    assert _orders(env, buy_b) == [(1, True)]  # journaled, closed unsent
    assert _line(notes)["open_buys"] == 1


def _negative(env: Env, cash: float = -1.0) -> None:
    account = env.fake.account
    env.fake.account = lambda: replace(account(), cash=cash)  # type: ignore[method-assign]


def test_negative_cash_at_the_open_tranche_read_halts(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env = timed_env
    _mapped(env)
    env.new_fake(cash=5000.0)
    batch = _batch(env)
    env.fake.script(Accept())
    _negative(env)
    notes: list[str] = []

    with pytest.raises(SystemFaultError, match="negative_cash"):
        _gate(env, alerter_conn).execute(env.run, batch, (), notes=notes)

    assert [r.side for r in _submits(env.fake)] == [Side.SELL]
    assert _line(notes)["min_cash"] == -1.0


@pytest.mark.parametrize("in_map", [True, False])
def test_negative_cash_at_the_ii_read_halts_only_a_book_in_the_map(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection, in_map: bool
) -> None:
    if in_map:
        _mapped(env, buy_timing="after_sells")
    _negative(env)
    buys = [_decision(env, sid, "buy", notional=300.0) for sid in (B, C)]

    if in_map:
        with pytest.raises(SystemFaultError, match="negative_cash"):
            _gate(env, alerter_conn).execute(env.run, buys, ())
    else:
        outcome = _gate(env, alerter_conn).execute(env.run, buys, ())
        assert outcome.cash == 0.0  # floored to zero, as today
    assert _submits(env.fake) == []


def test_a_long_short_window_runs_v0_and_notes_it(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = timed_env
    _mapped(env)
    env.new_fake(cash=5000.0)
    batch = _batch(env)
    env.fake.script(Accept())
    monkeypatch.setattr(wrapper, "_long_only", lambda _window: False)
    times = _times(env)
    notes: list[str] = []

    _gate(env, alerter_conn).execute(env.run, batch, (), notes=notes)

    assert _line(notes)["fallback"] == "long_short"
    assert all(stamp >= OPEN for r, stamp in times if r.side is Side.BUY)


def test_long_only_reads_the_frozen_side() -> None:
    def window(frozen: str) -> Any:
        return type("W", (), {"frozen_json": frozen})()

    assert wrapper._long_only(window("{}"))
    assert wrapper._long_only(window('{"portfolio.side": "long_only"}'))
    assert not wrapper._long_only(window('{"portfolio.side": "long_short"}'))
    assert not wrapper._long_only(window("not json"))


@pytest.mark.parametrize("timing", ["at_open", "after_sells"])
def test_when_every_open_buy_fills_v1_sends_as_many_orders_as_v0(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection, timing: str
) -> None:
    env = timed_env
    _mapped(env, buy_timing=timing)
    env.new_fake(cash=5000.0)
    batch = _batch(env)
    for _ in range(3):
        env.fake.script(FillAt())
    notes: list[str] = []

    _gate(env, alerter_conn).execute(env.run, batch, (), notes=notes)

    assert len(_submits(env.fake)) == 3
    assert _line(notes)["open_buys"] == (2 if timing == "at_open" else 0)


_SUBSETS = [s for n in range(3) for s in itertools.combinations((A, C), n)]


@pytest.mark.parametrize("unfilled", _SUBSETS, ids=lambda s: "+".join(s) or "none")
@pytest.mark.parametrize("seed", range(3))
def test_the_open_tranche_never_takes_the_cash_below_zero(
    timed_env: Env,
    alerter_conn: duckdb.DuckDBPyConnection,
    unfilled: tuple[str, ...],
    seed: int,
) -> None:
    """Item 10's property: whichever of the sells stay unfilled, with every (i')
    buy filled, the fake's cash never goes below zero, for random cash and
    remainders around the budget."""
    env = timed_env
    rng = random.Random(seed)
    _mapped(env)
    env.new_fake(cash=2000.0 + rng.uniform(1.0, 400.0))  # B = cash - 2000 held
    _hold(env, A, 10.0)
    _hold(env, C, 10.0)
    budget = Decimal(repr(env.fake.account().cash))
    sells = [_decision(env, sid, "sell", notional=500.0) for sid in (A, C)]
    buy = _decision(env, B, "buy", notional=round(float(budget) * rng.uniform(0.5, 1.5), 2))
    for sid in (A, C):
        env.fake.script(Accept() if sid in unfilled else FillAt())
    env.fake.script(FillAt())
    lowest: list[float] = []
    env.fake.on_submit = lambda _request: lowest.append(env.fake.account().cash)
    notes: list[str] = []

    _gate(env, alerter_conn).execute(env.run, [*sells, buy], (), notes=notes)

    assert env.fake.account().cash >= 0
    assert all(cash >= 0 for cash in lowest)
    fields = _line(notes)
    # B is the one read after the sells' submits: a sell the fake filled at once is
    # real cash in it (item 2, "No proceeds"); one left live adds nothing.
    assert Decimal(repr(fields["open_notional"])) <= Decimal(repr(fields["idle_cash"]))
    if set(unfilled) == {A, C}:
        assert Decimal(repr(fields["idle_cash"])) == budget
