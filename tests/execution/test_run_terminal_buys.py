"""A buy the broker cancels or expires unfilled (#1982): the unspent-cash check reads
the cash after the run's collection, so the idle cash alerts; the batch's
`cash_left` (cash at sizing, before the asynchronous terminal) cannot hide it.

Break-test scenario (2026-10-20): a broker-side cancel of one of three buys left ~33%
of equity idle, the rebalance read `executed` and no alert was written, because
`run.py` passed `batch.cash_left` to `_unspent_cash`."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from execution.test_run_stop import buy_id
from execution.test_run_trade import F_0, FROZEN, Env, at
from execution.test_run_trade import _no_env_file as _no_env_file  # autouse
from execution.test_run_trade import env as env
from execution.test_run_trade import window as window
from tradepartner.adapters.fake_broker import Expire
from tradepartner.config import BookExecution
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


def test_a_held_shorts_proceeds_are_not_idle_cash(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The broker's cash holds a short sale's proceeds as collateral: a cancelled buy's
    idle cash is netted against the shorts' market value, so a book whose cash is all
    short proceeds raises no alert."""
    real = env.fake.account

    def with_shorts() -> object:
        account = real()
        return replace(account, short_market_value=-account.cash)

    monkeypatch.setattr(env.fake, "account", with_shorts)
    env.fill_on_sleep = False
    victim = buy_id(env, F_0, "SEC_TRANSFER")
    done: list[bool] = []

    def hook(_now: object) -> None:
        if not done:
            env.fake.cancel(victim)
            done.append(True)
        env.fill_open()

    env.on_sleep.append(hook)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert env.alerts("unspent_cash") == []


# --- the cash sleeve is not unspent cash (T370b) -------------------------------------


@pytest.fixture
def loose_window(env: Env, tmp_path: Path) -> PaperWindowRow:
    """A window whose frozen unspent-cash bound (32%) sits between the cancelled buy's
    idle cash (about 33%) and the 40% a 10% sleeve leaves, less that sleeve (30%)."""
    return env.open_window(
        tmp_path=tmp_path, frozen=FROZEN.model_copy(update={"max_unspent_cash_fraction": 0.32})
    )


def _cancel_one_buy(env: Env) -> None:
    env.fill_on_sleep = False
    victim = buy_id(env, F_0, "SEC_TRANSFER")
    done: list[bool] = []

    def hook(_now: object) -> None:
        if not done:
            env.fake.cancel(victim)
            done.append(True)
        env.fill_open()

    env.on_sleep.append(hook)


def _sleeve(env: Env, c: float) -> None:
    env.settings = env.settings.model_copy(
        update={
            "paper": env.settings.paper.model_copy(
                update={"execution": {"main": BookExecution(cash_sleeve_fraction=c)}}
            )
        }
    )


def test_cash_equal_to_the_sleeve_writes_no_alert(env: Env, loose_window: PaperWindowRow) -> None:
    _cancel_one_buy(env)
    _sleeve(env, 0.10)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert env.alerts("unspent_cash") == []


def test_the_same_idle_cash_with_no_sleeve_alerts(env: Env, loose_window: PaperWindowRow) -> None:
    _cancel_one_buy(env)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    ((_, _, message),) = env.alerts("unspent_cash")
    assert "executed with 33433.20 cash unspent" in message


def test_cash_above_the_sleeve_plus_the_bound_still_alerts(
    env: Env, loose_window: PaperWindowRow
) -> None:
    _cancel_one_buy(env)
    _sleeve(env, 0.01)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    ((_, _, message),) = env.alerts("unspent_cash")
    assert "executed with 33098.86 cash unspent" in message  # 34,098.86 less 1% of 100,000


def test_a_book_outside_the_map_is_unchanged(env: Env, window: PaperWindowRow) -> None:
    env.settings = env.settings.model_copy(
        update={
            "paper": env.settings.paper.model_copy(
                update={"execution": {"other": BookExecution(cash_sleeve_fraction=0.10)}}
            )
        }
    )
    _cancel_one_buy(env)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert len(env.alerts("unspent_cash")) == 1
