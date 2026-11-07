"""A rebalance halted between its sells and its buys (#3131, Audit C case 9b).

The sells of T_1 fill pre-open; the buys raise, the run halts and the kill
switch engages. The owner resumes after F_1 + the frozen
`paper.max_catch_up_sessions` (2 in this fixture) but before F_2. Before #3131
T_1 lapsed `missed` and the sell proceeds sat in cash until T_2 (a month on a
`month_end` book). A part-executed rebalance now stays catchable until the
session before F_2, so the next run after the release buys the rest.

Runs `tracking_run` and `resume` on a temp-file copy of the fixture store with
the scripted fake (`test_run_trade.Env`); no broker, no `.env`.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest

from tradepartner.adapters.alpaca_trading_raw import AlpacaTradingError
from tradepartner.calendar import previous_session
from tradepartner.execution.resume import resume
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import OverrideRow, PaperWindowRow, rebalances_with_fills

from .test_run_trade import (
    T_0,
    Env,
    at,
    bought,
)
from .test_run_trade import (
    _no_env_file as _no_env_file,
)
from .test_run_trade import (
    env as env,
)
from .test_run_trade import (
    window as window,
)

T_1, F_1 = date(2019, 5, 31), date(2019, 6, 3)
T_2_FILL = date(2019, 7, 1)  # F_2: T_2 = 2019-06-28
LATE = date(2019, 6, 7)  # F_1 + 4 sessions, past the fixture's bound of 2


def _exclude(env: Env, window: PaperWindowRow, security_id: str) -> None:
    """T_1 drops one target, so its rebalance sells it and buys the other two up."""
    stamp = at(T_0, 22)
    assert window.window_id is not None
    env.append(
        OverrideRow(
            window_id=window.window_id,
            made_at=stamp,
            rebalance_session=T_1,
            security_id=security_id,
            kind="exclude_name",
            reason="test: T_1 sells this name",
            known_at=stamp,
            ingested_at=stamp,
        )
    )


def _release(env: Env, when: datetime) -> None:
    """The owner's `paper resume` on the fake and the temp store."""
    env.clock.now = when
    base = [when]

    def ticking() -> datetime:
        base[0] += timedelta(microseconds=1)
        env.clock.now = base[0]
        return base[0]

    resume(
        env.settings,
        lambda: open_for_write(env.settings),
        env.fake,
        ticking,
        "test: owner resumes late",
        False,
        accept_rejections=False,
    )


def _halt_after_the_sells(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    bought(env)
    _exclude(env, window, "SEC_TRANSFER")
    original = env.fake.submit

    def submit(request: Any) -> Any:
        if request.side == "buy":
            raise AlpacaTradingError(500, "internal error", retried=True)
        return original(request)

    monkeypatch.setattr(env.fake, "submit", submit)
    with pytest.raises(Exception):  # noqa: B017 - the halt path re-raises its fault
        env.run(at(F_1))
    monkeypatch.setattr(env.fake, "submit", original)
    assert env.held().get("TRNS", 0.0) < 1e-3  # the sell filled; its proceeds are cash


def _cash_fraction(env: Env) -> float:
    cash = float(env.fake.account().cash)
    equity = cash + sum(q * env.prices[s] for s, q in env.held().items())
    return cash / equity


def test_a_halt_between_sells_and_buys_is_caught_up_after_the_bound(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    _halt_after_the_sells(env, window, monkeypatch)
    _release(env, at(date(2019, 6, 6), 22, 0))
    late = env.run(at(LATE))
    assert (late.kind, late.status) == ("catch_up", "ok"), env.result(late.run_id)
    late_buys = [o for o in env.orders() if o[5] == late.run_id and o[2] == "buy"]
    assert {"SEC_DUAL_B", "SEC_SPLIT_FUTURE"} <= {o[1] for o in late_buys}
    assert _cash_fraction(env) < 0.02
    assert (T_1, "executed") in [(e[0], e[1]) for e in env.rebalance_events()]
    assert not [e for e in env.rebalance_events() if e[0] == T_1 and e[1] == "missed"]


def test_a_part_executed_rebalance_still_lapses_at_the_next_fill_session(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never past F_2: with the switch still engaged T_1 is pending after the old
    bound, and lapses (`kill_switch`) in the run of F_2, which plans T_2."""
    _halt_after_the_sells(env, window, monkeypatch)
    late = env.run(at(LATE))
    assert late.status == "skipped_kill_switch"
    assert not [e for e in env.rebalance_events() if e[0] == T_1]
    last = env.run(at(previous_session(T_2_FILL)))
    assert not [e for e in env.rebalance_events() if e[0] == T_1], last
    nxt = env.run(at(T_2_FILL))
    assert nxt.status == "skipped_kill_switch"
    assert (T_1, "missed", "kill_switch", nxt.run_id) in env.rebalance_events()


def test_rebalances_with_fills_names_only_rebalances_with_a_live_fill(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert window.window_id is not None
    with open_for_write(env.settings) as conn:
        assert rebalances_with_fills(conn, window.window_id) == frozenset()
    _halt_after_the_sells(env, window, monkeypatch)
    with open_for_write(env.settings) as conn:
        assert rebalances_with_fills(conn, window.window_id) == frozenset({T_0, T_1})
        assert rebalances_with_fills(conn, window.window_id + 1) == frozenset()
