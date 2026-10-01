"""Fake-clock and fake-cash checks for the wrapper's buys phase (T60e)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import duckdb

from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.adapters.fake_broker import Accept, FillAt
from tradepartner.calendar import session_open

from .test_wrapper_phases import (
    A,
    B,
    C,
    Env,
    S,
    _decision,
    _execute,
    _gate,
    _hold,
    _query,
    _submits,
)

pytest_plugins = ("execution.test_wrapper_phases",)


def _submission_times(env: Env) -> list[tuple[OrderRequest, datetime]]:
    times: list[tuple[OrderRequest, datetime]] = []
    env.fake.on_submit = lambda request: times.append((request, env.clock.now))
    return times


def test_buy_waits_until_sell_deadline_when_sell_stays_live(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    opening = session_open(S)
    env.clock.now = opening - timedelta(minutes=20)
    sell = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    env.fake.script(Accept())
    times = _submission_times(env)

    _execute(_gate(env, alerter_conn), env, [sell, buy])

    assert [request.side for request, _ in times] == [Side.SELL, Side.BUY]
    assert times[0][1] < opening
    assert times[1][1] >= opening + timedelta(seconds=env.settings.paper.sell_wait_seconds)
    assert env.fake.get_order(times[0][0].client_order_id).status.value == "accepted"
    assert _query(
        env.settings,
        "SELECT sells_in_flight_at_submit FROM orders WHERE decision_id = ?",
        [buy.decision_id],
    ) == [(True,)]


def test_buy_starts_as_soon_as_every_sell_is_terminal(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    _hold(env, A, 10.0)
    opening = session_open(S)
    env.clock.now = opening - timedelta(minutes=20)
    sell = _decision(env, A, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    env.fake.script(FillAt())
    times = _submission_times(env)

    _execute(_gate(env, alerter_conn), env, [sell, buy])

    assert [request.side for request, _ in times] == [Side.SELL, Side.BUY]
    assert (
        times[0][1]
        <= times[1][1]
        < opening + timedelta(seconds=env.settings.paper.sell_wait_seconds)
    )
    assert _query(
        env.settings,
        "SELECT sells_in_flight_at_submit FROM orders WHERE decision_id = ?",
        [buy.decision_id],
    ) == [(False,)]


def test_whole_share_buy_is_last_and_fills_stay_within_buffered_cash(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env.new_fake(cash=500.0, round_cash_to_cent=True)
    whole = _decision(env, A, "buy", notional=300.0, whole_share=True)
    fractional = _decision(env, B, "buy", notional=300.0)
    other = _decision(env, C, "buy", notional=300.0)
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    start_cash = Decimal(repr(env.fake.account().cash))
    times = _submission_times(env)

    outcome = _execute(_gate(env, alerter_conn), env, [whole, fractional, other])

    submitted = [request for request, _ in times]
    assert submitted == _submits(env.fake)
    assert submitted[-1].symbol == "DUALA"
    assert submitted[-1].quantity is not None
    assert all(request.side is Side.BUY for request in submitted)
    spent = sum(
        (Decimal(repr(fill.quantity)) * Decimal(repr(fill.price)) for fill in env.fake.fills()),
        Decimal(0),
    )
    assert spent <= start_cash
    assert env.fake.account().cash >= 0
    assert outcome.cash_left is not None and outcome.cash_left >= 0
