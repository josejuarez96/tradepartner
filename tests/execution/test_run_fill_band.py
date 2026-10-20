"""The fill-price sanity band (#1981): a fill outside `paper.max_fill_price_deviation`
around the order's reference price halts the run with a named `halted` alert.

Break-test scenario (2026-10-20): a buy filled at 100x or 0.01x the S-1 close ended
`ok`, with no alert, and both reconciliations agreed (journal and broker held the same
bad fill). Runs on the T63d `Env` (`test_run_trade.py`)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from execution.test_run_stop import buy_id
from execution.test_run_trade import F_0, Env, at
from execution.test_run_trade import _no_env_file as _no_env_file  # autouse
from execution.test_run_trade import env as env
from execution.test_run_trade import window as window
from tradepartner.adapters.fake_broker import FillAt
from tradepartner.config import PaperConfig
from tradepartner.errors import SystemFaultError
from tradepartner.execution.collect import fill_band_violations
from tradepartner.store.journal import FillRow, OrderedFill, PaperWindowRow


def _fill_victim_at(env: Env, factor: float) -> str:
    """Fill the TRNS buy at `factor` x its reference close and the rest at it."""
    env.fill_on_sleep = False
    victim = buy_id(env, F_0, "SEC_TRANSFER")

    def hook(_now: object) -> None:
        for o in env.fake.open_orders():
            if o.client_order_id == victim:
                env.fake.apply(victim, FillAt(env.prices["TRNS"] * factor))
            else:
                env.fake.simulate_fill(o.client_order_id)

    env.on_sleep.append(hook)
    return victim


def test_the_default_band_is_a_quarter_of_the_reference_price() -> None:
    assert PaperConfig().max_fill_price_deviation == 0.25


@pytest.mark.parametrize("factor", [100.0, 0.01])
def test_a_fill_far_outside_the_band_halts_with_a_named_alert(
    env: Env, window: PaperWindowRow, factor: float
) -> None:
    _fill_victim_at(env, factor)
    with pytest.raises(SystemFaultError, match="fill_price_outside_band"):
        env.run(at(F_0))
    run_id = env.latest_run()
    assert env.result(run_id)[0] == "halted"
    ((_, _, message),) = env.alerts("halted")
    assert "fill_price_outside_band" in message
    assert "SEC_TRANSFER" in message
    assert env.query("SELECT run_id FROM kill_switch")[-1][0] == run_id


@pytest.mark.parametrize("factor", [1.2, 0.8, 1.0])
def test_a_normal_fill_and_a_large_real_gap_inside_the_band_pass(
    env: Env, window: PaperWindowRow, factor: float
) -> None:
    _fill_victim_at(env, factor)
    outcome = env.run(at(F_0))
    assert outcome.status == "ok", env.result(env.latest_run())
    assert env.alerts("halted") == []


def test_the_band_comes_from_config_not_a_constant(env: Env, window: PaperWindowRow) -> None:
    """A tighter configured band halts the same +20% fill the default passes."""
    env.settings = env.settings.model_copy(
        update={"paper": env.settings.paper.model_copy(update={"max_fill_price_deviation": 0.1})}
    )
    _fill_victim_at(env, 1.2)
    with pytest.raises(SystemFaultError, match="fill_price_outside_band"):
        env.run(at(F_0))


def test_a_halted_bad_fill_is_not_rejudged_by_the_next_run(
    env: Env, window: PaperWindowRow
) -> None:
    """Only fills journaled in the run itself are judged: the next run sees the
    kill switch, not the same fill again."""
    _fill_victim_at(env, 100.0)
    with pytest.raises(SystemFaultError):
        env.run(at(F_0))
    env.on_sleep.clear()
    second = env.run(at(F_0.replace(day=2)))
    assert second.status == "skipped_kill_switch"


def _ordered(price: float, security_id: str = "SEC_A") -> OrderedFill:
    stamp = datetime(2019, 5, 1, 13, 31, tzinfo=UTC)
    return OrderedFill(
        fill=FillRow(
            client_order_id="tp-main-20190501-SEC_A-buy-1",
            filled_at=stamp,
            quantity=1.0,
            price=price,
            price_implied=False,
            broker_fill_id="f1",
            source="broker_feed",
            known_at=stamp,
            ingested_at=stamp,
        ),
        side="buy",
        security_id=security_id,
        symbol="A",
        run_id=1,
        window_id=1,
    )


def test_the_band_edges_are_inclusive_and_a_missing_reference_is_a_violation() -> None:
    ref = {"SEC_A": 100.0}
    assert fill_band_violations([_ordered(125.0), _ordered(75.0)], ref, 0.25) == ()
    assert len(fill_band_violations([_ordered(125.01)], ref, 0.25)) == 1
    assert len(fill_band_violations([_ordered(74.99)], ref, 0.25)) == 1
    assert len(fill_band_violations([_ordered(float("nan"))], ref, 0.25)) == 1
    assert len(fill_band_violations([_ordered(100.0, "SEC_B")], ref, 0.25)) == 1
