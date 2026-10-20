"""The fill-price sanity band (#1981): a fill outside `paper.max_fill_price_deviation`
around the order's reference price halts the run with a named `halted` alert.

Break-test scenario (2026-10-20): a buy filled at 100x or 0.01x the S-1 close ended
`ok`, with no alert, and both reconciliations agreed (journal and broker held the same
bad fill). Runs on the T63d `Env` (`test_run_trade.py`)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl
import pytest

from execution.test_run_stop import MAY_3, buy_id, request_stop, sell_id
from execution.test_run_trade import F_0, T_0, Env, at, bought
from execution.test_run_trade import _no_env_file as _no_env_file  # autouse
from execution.test_run_trade import env as env
from execution.test_run_trade import window as window
from tradepartner.adapters.fake_broker import FillAt
from tradepartner.calendar import session_open
from tradepartner.config import PaperConfig
from tradepartner.errors import SystemFaultError
from tradepartner.execution.collect import PriceBand, fill_band_violations
from tradepartner.store.journal import FillRow, OrderedFill, OverrideRow, PaperWindowRow


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


@pytest.mark.parametrize("factor", [100.0, 0.01])
def test_a_bad_sell_fill_halts_before_any_buy_is_sized_on_its_cash(
    env: Env, window: PaperWindowRow, factor: float
) -> None:
    """The sells phase's collection is judged too: a sell filled at 100x would inflate
    the cash the buys phase then sizes from, so the halt comes before any buy."""
    bought(env)
    t_1, f_1 = date(2019, 5, 31), date(2019, 6, 3)
    stamp = at(T_0, 22)
    assert window.window_id is not None
    env.append(
        OverrideRow(
            window_id=window.window_id,
            made_at=stamp,
            rebalance_session=t_1,
            security_id="SEC_TRANSFER",
            kind="exclude_name",
            reason="the owner's reason, long enough",
            known_at=stamp,
            ingested_at=stamp,
        )
    )
    opening = session_open(f_1)
    victim = sell_id(env, f_1, "SEC_TRANSFER")
    env.fill_on_sleep = False

    def fill_at_the_open(now: datetime) -> None:
        if now < opening:
            return
        for o in env.fake.open_orders():
            if o.client_order_id == victim:
                env.fake.apply(victim, FillAt(env.prices["TRNS"] * factor))
            else:
                env.fake.simulate_fill(o.client_order_id)

    env.on_sleep.append(fill_at_the_open)
    with pytest.raises(SystemFaultError, match="fill_price_outside_band"):
        env.run(at(f_1, 12, 30))
    run_id = env.latest_run()
    assert env.result(run_id)[0] == "halted"
    assert [o for o in env.orders() if o[5] == run_id and o[2] == "buy"] == []


@pytest.mark.parametrize("factor", [100.0, 0.01])
def test_a_stop_runs_forced_exit_filled_at_a_bad_price_halts(
    env: Env, window: PaperWindowRow, factor: float
) -> None:
    """A `window_stop` exit is no pending-rebalance name, yet its fill is judged: the
    run's collection prices the names of the orders it collects."""
    bought(env)
    request_stop(env, at(F_0, 22, 0))
    victim = sell_id(env, MAY_3, "SEC_TRANSFER")
    env.fill_on_sleep = False

    def hook(_now: object) -> None:
        for o in env.fake.open_orders():
            if o.client_order_id == victim:
                env.fake.apply(victim, FillAt(env.prices["TRNS"] * factor))
            else:
                env.fake.simulate_fill(o.client_order_id)

    env.on_sleep.append(hook)
    with pytest.raises(SystemFaultError, match="fill_price_outside_band"):
        env.run(at(MAY_3))
    assert env.result(env.latest_run())[0] == "halted"


def _ordered(
    price: float, security_id: str = "SEC_A", order_session: date | None = None
) -> OrderedFill:
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
        order_session=order_session,
    )


def _band(
    reference: dict[str, float], actions: pl.DataFrame | None = None, session: date = F_0
) -> PriceBand:
    empty = pl.DataFrame(
        {"security_id": [], "action_type": [], "ex_date": [], "ratio_or_amount": []},
        schema={
            "security_id": pl.String,
            "action_type": pl.String,
            "ex_date": pl.Date,
            "ratio_or_amount": pl.Float64,
        },
    )
    return PriceBand(reference.__getitem__, empty if actions is None else actions, session, 0.25)


def test_the_band_edges_are_inclusive_and_a_nonfinite_price_is_a_violation() -> None:
    band = _band({"SEC_A": 100.0})
    assert fill_band_violations([_ordered(125.0), _ordered(75.0)], band) == ()
    assert len(fill_band_violations([_ordered(125.01)], band)) == 1
    assert len(fill_band_violations([_ordered(74.99)], band)) == 1
    assert len(fill_band_violations([_ordered(float("nan"))], band)) == 1


def test_a_name_with_no_reference_is_not_judged() -> None:
    """An exit of an unpriced name (#569) has nothing to compare with."""
    assert fill_band_violations([_ordered(1e9, "SEC_B")], _band({"SEC_A": 100.0})) == ()


def test_a_late_fill_across_a_split_is_judged_against_its_orders_own_reference() -> None:
    """An order of the session before a 2-for-1 split fills at the pre-split price 100
    and is collected on the ex-date, whose reference price is the adjusted 50: the
    order's own reference is 50 x 2 = 100, so it passes; a post-split order does not
    get the factor."""
    ex = date(2019, 5, 1)
    actions = pl.DataFrame(
        {
            "security_id": ["SEC_A"],
            "action_type": ["split"],
            "ex_date": [ex],
            "ratio_or_amount": [2.0],
        }
    )
    band = _band({"SEC_A": 50.0}, actions, session=ex)
    before = date(2019, 4, 30)
    assert fill_band_violations([_ordered(100.0, order_session=before)], band) == ()
    assert len(fill_band_violations([_ordered(100.0, order_session=ex)], band)) == 1
