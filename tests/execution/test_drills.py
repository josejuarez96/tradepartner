"""Full tracking-run fault drills against the temporary fixture store (T201)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from alpaca.common.exceptions import APIError

from tradepartner.adapters.broker import Asset, OrderRequest
from tradepartner.adapters.fake_broker import PartialFill, Reject, SetPosition, TransportFault
from tradepartner.config import RiskConfig
from tradepartner.errors import ReconciliationError
from tradepartner.store.journal import OverrideRow, PaperWindowRow

from .test_run_trade import (
    F_0,
    SYMBOLS,
    T_0,
    Env,
    at,
    bought,
    delist,
    split,
)
from .test_run_trade import (
    env as env,
)
from .test_run_trade import (
    window as window,
)


def journal(
    env: Env,
) -> tuple[list[tuple[object, ...]], list[tuple[object, ...]], list[tuple[object, ...]]]:
    """The three order journal tables, in stable insertion order."""
    return (
        env.query("SELECT client_order_id, security_id FROM orders ORDER BY client_order_id"),
        env.query(
            "SELECT client_order_id, status FROM order_events ORDER BY event_at, client_order_id"
        ),
        env.query("SELECT client_order_id, quantity FROM fills ORDER BY fill_id"),
    )


def test_the_fakes_retry_model_is_the_raw_clients_predicate() -> None:
    """The fake's modelled retry delegates to the raw client's `_retryable`
    rule, so changing T200's 5xx or connection-error predicate turns the retry
    drills below red. Neither this pin nor those drills exercises the raw
    client's retry loop itself (tests/adapters/test_alpaca_trading_raw.py does)."""
    from tradepartner.adapters.alpaca_trading_raw import _retryable

    assert _retryable(http_fault(502)) and _retryable(http_fault(500))
    assert _retryable(requests.ConnectionError("reset"))


def http_fault(status: int) -> APIError:
    """An SDK error with the status read by the trading client's retry rule."""
    return APIError(
        '{"code":50000000,"message":"temporary"}',
        SimpleNamespace(response=SimpleNamespace(status_code=status)),
    )


def test_sleeping_mac_rebalance_stays_pending_then_catches_up(
    env: Env, window: PaperWindowRow
) -> None:
    late = env.run(at(F_0, 14, 1))
    assert late.status == "ok"
    assert env.rebalance_events() == []
    assert journal(env) == ([], [], [])
    assert env.alerts("halted") == []

    catch_up = env.run(at(date(2019, 5, 2), 12, 30))
    orders, events, fills = journal(env)
    assert catch_up.status == "ok"
    assert env.rebalance_events() == [(T_0, "executed", None, catch_up.run_id)]
    assert len(orders) == len(fills) == 3
    assert [status for _, status in events].count("filled") == 3
    assert env.alerts("halted") == []


def test_class_share_is_sent_to_broker_in_alpaca_form(env: Env, window: PaperWindowRow) -> None:
    with env.connect() as conn:
        conn.execute("UPDATE listings SET ticker = 'GEF-B' WHERE security_id = 'SEC_DUAL_B'")
    env.price_at(F_0)
    price = env.prices["DUALB"]
    assets = {symbol: Asset(True, True, "active", None) for symbol in SYMBOLS.values()}
    del assets["DUALB"]
    assets["GEF.B"] = Asset(True, True, "active", None)
    env.new_fake(assets=assets)
    outcome = env.run(at(F_0), prices={"GEF.B": price})
    orders, events, fills = journal(env)
    assert outcome.status == "ok"
    assert {order.symbol for order in env.submits()} >= {"GEF.B"}
    assert len(orders) == len(fills) == 3 and len(events) >= 6
    assert env.alerts("halted") == []


def test_502_on_submit_run_completes_after_the_modelled_retry(
    env: Env, window: PaperWindowRow
) -> None:
    env.fake.script_call_fault("submit", http_fault(502))
    outcome = env.run(at(F_0))
    orders, events, fills = journal(env)
    assert outcome.status == "ok"
    assert len(orders) == len(fills) == 3
    assert [status for _, status in events].count("filled") == 3
    assert len([call for call in env.fake.calls if call.method == "submit"]) == 4
    assert env.alerts("halted") == []


def test_unretryable_4xx_on_submit_reaches_the_wrapper_and_halts(
    env: Env, window: PaperWindowRow
) -> None:
    """A fault the retry rule does not cover is not absorbed: the run halts."""
    env.fake.script_call_fault("submit", http_fault(400))
    with pytest.raises(Exception):  # noqa: B017
        env.run(at(F_0))
    assert env.result(env.latest_run())[0] == "halted"
    assert len(env.alerts("halted")) == 1
    assert not journal(env)[2]


def test_connection_error_on_order_read_run_completes_after_the_modelled_retry(
    env: Env, window: PaperWindowRow
) -> None:
    env.fake.script_call_fault("get_order", requests.ConnectionError("connection reset"))
    outcome = env.run(at(F_0))
    orders, events, fills = journal(env)
    assert outcome.status == "ok"
    assert len(orders) == len(fills) == 3
    assert [status for _, status in events].count("filled") == 3
    assert len([call for call in env.fake.calls if call.method == "get_order"]) >= 2
    assert env.alerts("halted") == []


def test_500_on_order_read_run_completes_after_the_modelled_retry(
    env: Env, window: PaperWindowRow
) -> None:
    env.fake.script_call_fault("get_order", http_fault(500))
    outcome = env.run(at(F_0))
    orders, events, fills = journal(env)
    assert outcome.status == "ok"
    assert len(orders) == len(fills) == 3
    assert [status for _, status in events].count("filled") == 3
    assert len([call for call in env.fake.calls if call.method == "get_order"]) >= 2
    assert env.alerts("halted") == []


def test_unknown_buy_asset_is_one_skip_and_other_names_trade(
    env: Env, window: PaperWindowRow
) -> None:
    env.fake.remove_asset("DUALB")
    outcome = env.run(at(F_0))
    orders, events, fills = journal(env)
    assert outcome.status == "ok"
    assert len(orders) == 2 and len(fills) == 2 and len(events) >= 4
    assert env.query("SELECT reason FROM decision_events WHERE reason = 'skip_asset_unknown'")
    assert len(env.alerts("skipped")) == 1


def test_mid_run_fractionability_refusal_skips_only_the_buy(
    env: Env, window: PaperWindowRow
) -> None:
    def change_asset(request: OrderRequest) -> None:
        if request.symbol == "SPFT":
            env.fake.set_asset("SPFT", Asset(True, False, "active", None))

    env.fake.on_submit = change_asset
    outcome = env.run(at(F_0))
    orders, _events, fills = journal(env)
    assert outcome.status == "ok"
    # Orders are journaled before submit: three rows, two fills.
    assert len(orders) == 3 and len(fills) == 2
    assert env.query("SELECT reason FROM decision_events WHERE reason = 'skip_not_fractionable'")
    assert len(env.alerts("skipped")) == 1


def test_untradable_buy_is_skipped_before_submit(env: Env, window: PaperWindowRow) -> None:
    # The not-tradable 403 is unrecorded; the skip keys on the asset read, never on a code.
    env.fake.set_asset("SPFT", Asset(False, True, "active", None))
    outcome = env.run(at(F_0))
    orders, events, fills = journal(env)
    assert outcome.status == "ok"
    assert len(orders) == len(fills) == 2
    assert [status for _, status in events].count("filled") == 2
    assert env.query(
        "SELECT e.reason FROM decision_events e JOIN decisions d USING (decision_id) "
        "WHERE d.security_id = 'SEC_SPLIT_FUTURE' AND e.status = 'skipped'"
    ) == [("skip_untradable",)]
    assert len(env.alerts("unspent_cash")) == 1


def test_skip_share_cap_halts_before_any_buy(env: Env, tmp_path: Path) -> None:
    frozen = RiskConfig.model_validate(
        {
            **RiskConfig(
                max_position_weight=0.4, max_order_notional_fraction=0.4, max_skips_per_run=0
            ).model_dump(),
            "max_skip_share": 0.25,
        }
    )
    env.open_window(tmp_path=tmp_path, frozen=frozen)
    env.fake.remove_asset("DUALB")
    with pytest.raises(Exception) as caught:
        env.run(at(F_0))
    assert type(caught.value).__name__ == "SkipCapError"
    assert env.result(env.latest_run())[:2] == ("halted", "SkipCapError")
    assert len(env.alerts("skip_cap")) == 1
    assert journal(env) == ([], [], [])


@pytest.mark.xfail(strict=True, reason="#1828")
def test_sell_refused_after_position_changes_is_reconciliation_fault(
    env: Env, window: PaperWindowRow
) -> None:
    bought(env)
    assert window.window_id is not None
    t_1 = date(2019, 5, 31)
    stamp = at(F_0, 22)
    env.append(
        OverrideRow(
            window_id=window.window_id,
            made_at=stamp,
            rebalance_session=t_1,
            security_id="SEC_TRANSFER",
            kind="exclude_name",
            reason="drill the sell after broker position changed",
            known_at=stamp,
            ingested_at=stamp,
        )
    )

    def shrink_on_sell(request: OrderRequest) -> None:
        if request.side.value == "sell" and request.symbol == "TRNS":
            env.fake.apply_account(SetPosition("TRNS", 0))

    env.fake.on_submit = shrink_on_sell
    before = journal(env)
    with pytest.raises(ReconciliationError):
        env.run(at(date(2019, 6, 3)))
    assert env.result(env.latest_run())[:2] == ("halted", "ReconciliationError")
    assert len(env.alerts("halted")) == 1
    orders, events, fills = journal(env)
    assert len(orders) > len(before[0]) and len(fills) == len(before[2])
    assert any(status == "pending" for _, status in events)


@pytest.mark.xfail(strict=True, reason="T202b")
def test_ack_timeout_stays_pending_until_next_run(env: Env, window: PaperWindowRow) -> None:
    env.fake.script(TransportFault(after_record=True, error=TimeoutError("ack delayed")))
    first = env.run(at(F_0))
    orders, events, fills = journal(env)
    assert first.status == "ok"
    # The run goes on past the one unacknowledged order: only it is unresolved.
    assert [status for _, status in events].count("pending_unresolved") == 1
    assert len(fills) == len(orders) - 1
    assert len(env.alerts("pending_unresolved")) == 1

    second = env.run(at(date(2019, 5, 2)))
    assert second.status == "ok"
    assert env.query(
        "SELECT status FROM reconciliations ORDER BY reconciliation_id DESC LIMIT 1"
    ) == [("ok",)]


def test_partial_fill_is_journaled_without_completing_the_order(
    env: Env, window: PaperWindowRow
) -> None:
    env.fill_on_sleep = False

    def fill_one(_now: object) -> None:
        if env.count("fills"):
            return
        [order, *_] = env.fake.open_orders()
        env.fake.apply(order.client_order_id, PartialFill(1, env.prices[order.symbol]))

    env.on_sleep.append(fill_one)
    outcome = env.run(at(F_0))
    orders, events, fills = journal(env)
    assert outcome.status == "ok"
    assert len(orders) == 3 and len(fills) == 1
    assert fills[0][1] == 1
    assert not any(status == "filled" for _, status in events)
    assert env.alerts("halted") == []


def test_accepted_then_rejected_order_has_terminal_journal_event(
    env: Env, window: PaperWindowRow
) -> None:
    env.fill_on_sleep = False

    def reject_one(_now: object) -> None:
        for order in env.fake.open_orders():
            env.fake.apply(order.client_order_id, Reject())
            break
        env.on_sleep.clear()

    env.on_sleep.append(reject_one)
    outcome = env.run(at(F_0))
    orders, events, fills = journal(env)
    assert outcome.status == "ok"
    assert orders and not fills
    assert any(status == "rejected" for _, status in events)
    assert env.alerts("halted") == []


def test_held_delisted_name_is_decided_on_the_next_rebalance(env: Env, tmp_path: Path) -> None:
    """#1480/#411: record today's behaviour for a held name delisted before the
    next rebalance (2019-06-03, T_1 = 2019-05-31). The broker answers it inactive."""
    env.open_window(
        tmp_path=tmp_path,
        frozen=RiskConfig(
            max_position_weight=0.6, max_order_notional_fraction=0.6, max_skips_per_run=0
        ),
    )
    bought(env)
    day = date(2019, 6, 3)
    delist(env, "SEC_DUAL_B", at(date(2019, 5, 31), 19, 0))
    env.fake.set_asset("DUALB", Asset(False, False, "inactive", None), from_session=day)
    outcome = env.run(at(day), prices={symbol: env.prices[symbol] for symbol in SYMBOLS.values()})
    assert outcome.status == "ok"
    decisions = env.query("SELECT security_id, decision FROM decisions WHERE run_id = ?", [2])
    assert ("SEC_DUAL_B", "skip_delisted") in decisions
    assert ("SEC_DUAL_B", "forced_exit") in decisions
    # Today the forced exit of an untradable name closes with a note, not an order,
    # and the position stays at the broker: recorded as-is, no fix here.
    assert outcome.notes == ("forced exit of SEC_DUAL_B (delisted) closed: untradable",)
    assert not [o for o in env.orders() if o[1] == "SEC_DUAL_B" and o[4] == day]
    assert "DUALB" in env.held()
    assert env.alerts("halted") == []


def test_known_split_on_held_name_reconciles_next_run(env: Env, window: PaperWindowRow) -> None:
    bought(env)
    before = journal(env)
    held = env.held()["TRNS"]
    day = date(2019, 5, 2)
    split(env, "SEC_TRANSFER", day, 2.0, at(F_0, 19, 0))
    env.fake.apply_split("TRNS", 2.0)
    price = env.prices["TRNS"] / 2.0
    outcome = env.run(at(day), prices={"TRNS": price})
    assert outcome.status == "ok"
    assert env.held()["TRNS"] == pytest.approx(held * 2)
    assert journal(env) == before
    # Current reconciliation explains the known split without an adjustments row.
    assert env.query("SELECT kind FROM adjustments WHERE security_id = 'SEC_TRANSFER'") == []
    assert env.alerts("halted") == []
