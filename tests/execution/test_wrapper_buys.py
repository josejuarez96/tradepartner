"""Fake-clock and fake-cash checks for the wrapper's buys phase (T60e)."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal

import duckdb
import pytest

from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.adapters.fake_broker import Accept, FillAt
from tradepartner.calendar import session_open
from tradepartner.config import Settings
from tradepartner.store.journal import PaperRunResultRow, PaperWindowRow

from .test_wrapper_phases import (
    CUT,
    FROZEN,
    PREV,
    PRICE,
    SYMBOLS,
    A,
    B,
    C,
    Env,
    FixedClock,
    S,
    _append,
    _bar,
    _decision,
    _execute,
    _gate,
    _hold,
    _query,
    _run,
    _settings,
    _submits,
)

pytest_plugins = ("execution.test_wrapper_phases",)


@pytest.fixture
def timed_env(
    journal_settings: Settings, open_window: PaperWindowRow, fixed_clock: FixedClock
) -> Env:
    """Build the fixture's run after moving the clock before the open."""
    settings = _settings(journal_settings.store.path)
    for security_id in SYMBOLS:
        _bar(settings, security_id, PRICE)
    earlier = _run(settings, open_window, PREV, CUT - timedelta(hours=3))
    _append(
        settings,
        PaperRunResultRow(
            run_id=earlier.run_id,  # type: ignore[arg-type]
            finished_at=CUT - timedelta(hours=2),
            status="ok",
            clock_fault=False,
            known_at=CUT - timedelta(hours=2),
            ingested_at=CUT - timedelta(hours=2),
        ),
    )
    opening = session_open(S)
    fixed_clock.now = opening - timedelta(minutes=20)
    run = _run(settings, open_window, S, opening - timedelta(minutes=25))
    return Env(settings, open_window, fixed_clock, earlier, run)


def _submission_times(env: Env) -> list[tuple[OrderRequest, datetime]]:
    times: list[tuple[OrderRequest, datetime]] = []
    env.fake.on_submit = lambda request: times.append((request, env.clock.now))
    return times


def test_buy_waits_until_sell_deadline_when_sell_stays_live(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env = timed_env
    _hold(env, A, 10.0)
    _hold(env, C, 10.0)
    opening = session_open(S)
    sell = _decision(env, A, "sell", notional=500.0)
    second_sell = _decision(env, C, "sell", notional=500.0)
    buy = _decision(env, B, "buy", notional=3000.0)
    env.fake.script(FillAt())
    env.fake.script(Accept())
    times = _submission_times(env)

    _execute(_gate(env, alerter_conn), env, [sell, second_sell, buy])

    assert [request.side for request, _ in times] == [Side.SELL, Side.SELL, Side.BUY]
    assert times[0][1] < opening
    assert times[2][1] >= opening + timedelta(seconds=env.settings.paper.sell_wait_seconds)
    assert env.fake.get_order(times[0][0].client_order_id).status.value == "filled"
    assert env.fake.get_order(times[1][0].client_order_id).status.value == "accepted"
    assert _query(
        env.settings,
        "SELECT sells_in_flight_at_submit FROM orders WHERE decision_id = ?",
        [buy.decision_id],
    ) == [(True,)]


def test_buy_starts_as_soon_as_every_sell_is_terminal(
    timed_env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    env = timed_env
    _hold(env, A, 10.0)
    opening = session_open(S)
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
    starting_cash = Decimal("610.00")
    env.new_fake(cash=float(starting_cash), round_cash_to_cent=True)
    whole = _decision(env, A, "buy", notional=300.0, whole_share=True)
    fractional = _decision(env, B, "buy", notional=300.0)
    other = _decision(env, C, "buy", notional=300.0)
    env.fake.script(FillAt())
    env.fake.script(FillAt())
    buffered_price = Decimal(str(PRICE)) * (
        Decimal(1) + Decimal(str(FROZEN.whole_share_price_buffer))
    )
    env.fake.script(FillAt(float(buffered_price)))
    start_cash = Decimal(repr(env.fake.account().cash))
    times = _submission_times(env)

    outcome = _execute(_gate(env, alerter_conn), env, [whole, fractional, other])

    submitted = [request for request, _ in times]
    assert submitted == _submits(env.fake)
    assert submitted[-1].symbol == "DUALA"
    rate = Decimal(1) + Decimal(str(env.settings.costs.per_side_bps)) / Decimal(10_000)
    per_buy = (starting_cash / rate / Decimal(3)).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    assert [request.notional for request in submitted[:2]] == [float(per_buy)] * 2
    assert submitted[-1].quantity == 1.0
    assert (starting_cash / rate / Decimal(3) / Decimal(str(PRICE))).to_integral_value(
        rounding=ROUND_DOWN
    ) == 2  # an unbuffered sizing would order two shares
    assert all(request.side is Side.BUY for request in submitted)
    commission_per_order = Decimal(str(env.settings.costs.commission_per_order))
    commission_per_share = Decimal(str(env.settings.costs.commission_per_share))
    buffered_cost = sum(
        (
            Decimal(str(request.notional)) * rate
            + Decimal(str(request.notional)) / Decimal(str(PRICE)) * commission_per_share
            + commission_per_order
            for request in submitted[:2]
        ),
        Decimal(0),
    )
    buffered_cost += (
        Decimal(str(submitted[-1].quantity)) * buffered_price * rate
        + Decimal(str(submitted[-1].quantity)) * commission_per_share
        + commission_per_order
    )
    assert buffered_cost <= Decimal(str(outcome.cash))
    spent = sum(
        (Decimal(repr(fill.quantity)) * Decimal(repr(fill.price)) for fill in env.fake.fills()),
        Decimal(0),
    )
    assert spent <= start_cash
    assert env.fake.account().cash >= 0
    assert outcome.cash_left is not None and outcome.cash_left >= 0
