"""A buy the broker cancels or expires unfilled (#1982): the unspent-cash check reads
the cash after the run's collection, so the idle cash alerts; the batch's
`cash_left` (cash at sizing, before the asynchronous terminal) cannot hide it.

Break-test scenario (2026-10-20): a broker-side cancel of one of three buys left ~33%
of equity idle, the rebalance read `executed` and no alert was written, because
`run.py` passed `batch.cash_left` to `_unspent_cash`."""

from __future__ import annotations

from execution.test_run_stop import buy_id
from execution.test_run_trade import F_0, Env, at
from execution.test_run_trade import _no_env_file as _no_env_file  # autouse
from execution.test_run_trade import env as env
from execution.test_run_trade import window as window
from tradepartner.adapters.fake_broker import Expire
from tradepartner.store.journal import PaperWindowRow


def test_a_buy_the_broker_cancels_leaves_idle_cash_that_alerts(
    env: Env, window: PaperWindowRow
) -> None:
    env.fill_on_sleep = False
    victim = buy_id(env, F_0, "SEC_TRANSFER")
    done: list[bool] = []

    def hook(_now: object) -> None:
        if not done:
            env.fake.cancel(victim)  # broker-side cancel, not ours
            done.append(True)
        env.fill_open()

    env.on_sleep.append(hook)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert env.rebalance_events() == [
        (env.rebalance_events()[0][0], "executed", None, outcome.run_id)
    ]
    ((run_id, session, message),) = env.alerts("unspent_cash")
    assert (run_id, session) == (outcome.run_id, F_0)
    assert "max_unspent_cash_fraction" in message


def test_a_buy_that_expires_unfilled_leaves_idle_cash_that_alerts(
    env: Env, window: PaperWindowRow
) -> None:
    env.fill_on_sleep = False
    victim = buy_id(env, F_0, "SEC_TRANSFER")
    done: list[bool] = []

    def hook(_now: object) -> None:
        if not done:
            env.fake.apply(victim, Expire())
            done.append(True)
        env.fill_open()

    env.on_sleep.append(hook)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert len(env.alerts("unspent_cash")) == 1


def test_a_fully_filled_rebalance_raises_no_unspent_cash_alert(
    env: Env, window: PaperWindowRow
) -> None:
    env.run(at(F_0))
    assert env.alerts("unspent_cash") == []
