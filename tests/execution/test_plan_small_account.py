"""The small-account profile `risk.small_account` (Phase 4 spec amendment
2026-10-18 item 8; ADR 0023 D6; plan T206).

A generic 25-name plan at $100 equity, inside ADR 0010's unchanged 5 % limits:
21 targets at 4 % ($4.00 each) and 4 at 0.5 % ($0.50 each, below the $1
`risk.min_order_notional`), five held names leaving the targets ($2.00 each,
sold whole). With the section on, every buy is a notional order at
max(planned, `risk.min_order_notional`), so the four $0.50 buys go as $1.00
orders and nothing is skipped; sells stay by quantity. With the section off
(the default, and every window frozen before the section existed) the same
plan gives today's requests exactly: the four $0.50 buys are plan-time
`skip_below_minimum`. The rounded notional is what the limits see: a rounding
up that breaches a position-weight, order-notional or buy-reserve limit halts
the batch with `LimitBreachError` before any submit (the wrapper cases at the
end, on `test_wrapper_phases`' harness).

The chain is the wrapper's own: `plan.decisions_from` -> `plan.decision_state`
-> `phases.sell_orders` / `phases.buy_orders` -> `risk.check_phase` ->
`phases.requests_for`. The profile is built inside each test, never at import,
so a missing section fails the test that needs it and not the module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner.adapters.broker import Account, Asset, OrderRequest, Side
from tradepartner.backtest.costs import Commissions
from tradepartner.backtest.engine import Plan
from tradepartner.backtest.provider import GapReading
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import LimitBreachError
from tradepartner.execution.ids import client_order_id
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.phases import PhaseOrders, buy_orders, requests_for, sell_orders
from tradepartner.execution.plan import (
    BuyCosts,
    Decisions,
    DecisionState,
    State,
    decision_state,
    decisions_from,
)
from tradepartner.execution.reconcile_run import frozen_risk
from tradepartner.execution.risk import Skip, Skips, Violations, check_phase
from tradepartner.store.journal import DecisionRow, PaperWindowRow
from tradepartner.store.schema import JOURNAL_ENUMS

from .test_wrapper_phases import (
    SYMBOLS,
    A,
    C,
    Env,
    _decision,
    _execute,
    _gate,
    _missed,
    _open_buy,
    _submits,
)

pytest_plugins = ("execution.test_wrapper_phases",)

T = date(2026, 9, 30)  # rebalance session T_i (a month-end)
S = date(2026, 10, 1)  # its fill session, the run's S; S-1 is T
STAMP = datetime(2026, 9, 30, 21, tzinfo=UTC)
DECIMALS = 9
PREFIX = "tp"
BOOK = "main"
SETTINGS = Settings(_env_file=None)  # paper.live_capital_reference 100.0
COSTS = BuyCosts(per_side_bps=10.0, commissions=Commissions(per_share=0.0, per_order=0.0))

#: The 25 targets: 21 at 4 % of $100 ($4.00) and 4 at 0.5 % ($0.50, under the $1 minimum).
FULL = tuple(f"N{i:02d}" for i in range(1, 22))
SMALL = tuple(f"N{i:02d}" for i in range(22, 26))
TARGETS = FULL + SMALL
#: Five held names, members of the universe but no longer targets: `left_targets` sells.
OLD = tuple(f"OLD{i}" for i in range(1, 6))
WEIGHTS = {**dict.fromkeys(FULL, 0.04), **dict.fromkeys(SMALL, 0.005)}
PRICES = {**dict.fromkeys(TARGETS, 50.0), **dict.fromkeys(OLD, 10.0)}
OLD_SHARES = 0.2  # x $10 = $2.00 each, $10.00 in all
START_CASH = 90.0  # + $10.00 held = $100.00 equity
CASH_AFTER_SELLS = 100.0  # the five sells at the reference price
FULL_BUY = 4.0
MIN_BUY = 1.0  # RiskConfig().min_order_notional: what a $0.50 buy is ordered at
#: $100 less (21 x $4.00 + 4 x $1.00) x (1 + 10 bps) = 100 - 88.088.
CASH_LEFT_ON = 11.912
#: Section off: $100 less 21 x $4.00 x (1 + 10 bps) = 100 - 84.084.
CASH_LEFT_OFF = 15.916
ACTIONS = pl.DataFrame(
    schema={
        "security_id": pl.Utf8,
        "action_type": pl.Utf8,
        "ex_date": pl.Date,
        "ratio_or_amount": pl.Float64,
        "known_at": pl.Datetime("us", "UTC"),
    }
)
#: A window frozen before T206: today's sixteen `risk.*` keys at their defaults
#: and no `risk.small_account` (written out by hand, as the open windows hold it).
PRE_T206_FROZEN_JSON = json.dumps(
    {
        "risk.cash_in_lieu_tolerance": 0.1,
        "risk.clock_max_sessions_late": 1,
        "risk.max_broker_clock_skew_seconds": 60.0,
        "risk.max_drawdown": 0.3,
        "risk.max_fill_lag_sessions": 1,
        "risk.max_gross_exposure": 1.0,
        "risk.max_order_notional_fraction": 0.05,
        "risk.max_orders_per_run": 250,
        "risk.max_position_weight": 0.05,
        "risk.max_rejections_per_run": 5,
        "risk.max_skips_per_run": 10,
        "risk.max_unspent_cash_fraction": 0.05,
        "risk.min_order_notional": 1.0,
        "risk.reconcile_cash_tolerance": 0.01,
        "risk.reconcile_quantity_tolerance": 1e-06,
        "risk.whole_share_price_buffer": 0.02,
    },
    sort_keys=True,
)


def _profile(**limits: Any) -> RiskConfig:
    """The window's frozen risk section with `risk.small_account` on."""
    return RiskConfig.model_validate({"small_account": {"enabled": True}, **limits})


def price_of(security_id: str) -> float:
    return PRICES[security_id]


def _asset(fractionable: bool = True) -> Asset:
    return Asset(tradable=True, fractionable=fractionable, status="active", cusip=None)


def _assets(*not_fractionable: str) -> dict[str, Asset]:
    return {sid: _asset(sid not in not_fractionable) for sid in PRICES}


def _plan() -> Plan:
    members = TARGETS + OLD
    return Plan(
        session=T,
        fill_session=S,
        targets=dict(WEIGHTS),
        n_universe=len(members),
        n_static_listings=0,
        n_excluded_no_history=0,
        gap=GapReading(count_share=0.0, size_share=0.0),
        members=members,
        scores={sid: float(len(members) - i) for i, sid in enumerate(members)},
        excluded_no_history=(),
        exclusions={"no_history": ()},
    )


def _account(cash: float) -> Account:
    return Account(account_id="PA1", cash=cash, buying_power=cash, equity=cash, as_of=STAMP)


@dataclass(frozen=True)
class Chain:
    """What the wrapper would compute for the plan on S, step by step."""

    decided: Decisions
    rows: tuple[DecisionRow, ...]
    states: Mapping[int, DecisionState]
    sells: PhaseOrders
    sells_checked: Violations | Skips
    buys: PhaseOrders
    buys_checked: Violations | Skips
    requests: tuple[OrderRequest, ...]


def _chain(frozen: RiskConfig, assets: Mapping[str, Asset] | None = None) -> Chain:
    assets = assets if assets is not None else _assets()
    held = dict.fromkeys(OLD, OLD_SHARES)
    decided = decisions_from(
        _plan(),
        Ledger(positions=held, cash=START_CASH, through=S),
        (),
        assets,
        {},
        frozenset(),
        frozen,
        SETTINGS,
        price_of=price_of,
        actions_as_of=ACTIONS,
        costs=COSTS,
    )
    rows = tuple(
        replace(d.row(run_id=1, known_at=STAMP, ingested_at=STAMP, book_id=BOOK), decision_id=i)
        for i, d in enumerate(decided.decisions, start=1)
    )
    states = {
        row.decision_id: decision_state(row, (), (), (), (), ACTIONS, price_of, frozen, session=S)
        for row in rows
        if row.decision_id is not None
    }
    sells = sell_orders(
        rows,
        states,
        Ledger(positions=held, cash=START_CASH, through=S),
        {},
        (),
        ACTIONS,
        price_of,
        assets,
        frozen,
        session=S,
        quantity_decimals=DECIMALS,
    )
    sells_checked = check_phase(
        [o.to_risk(o.security_id, listing_ended=False) for o in sells.orders],
        Ledger(positions=held, cash=START_CASH, through=S),
        _account(START_CASH),
        assets,
        frozen,
        DECIMALS,
        price_of=price_of,
        costs=COSTS,
    )
    buys = buy_orders(
        rows, states, CASH_AFTER_SELLS, sells, price_of, COSTS, assets, frozen, session=S
    )
    buys_checked = check_phase(
        [o.to_risk(o.security_id, listing_ended=False) for o in buys.orders],
        Ledger(positions={}, cash=CASH_AFTER_SELLS, through=S),
        _account(CASH_AFTER_SELLS),
        assets,
        frozen,
        DECIMALS,
        price_of=price_of,
        costs=COSTS,
        prior_orders=len(sells.orders),
    )
    listings = {sid: sid for sid in PRICES}
    sell_requests = requests_for(sells, listings, PREFIX, BOOK, (), 128, session=S)
    buy_requests = requests_for(buys, listings, PREFIX, BOOK, (), 128, session=S)
    return Chain(
        decided,
        rows,
        states,
        sells,
        sells_checked,
        buys,
        buys_checked,
        (*sell_requests, *buy_requests),
    )


def _sell_request(sid: str) -> OrderRequest:
    coid = client_order_id(PREFIX, BOOK, S, sid, "sell", 1)
    return OrderRequest(coid, sid, Side.SELL, quantity=OLD_SHARES)


def _buy_request(sid: str, notional: float) -> OrderRequest:
    coid = client_order_id(PREFIX, BOOK, S, sid, "buy", 1)
    return OrderRequest(coid, sid, Side.BUY, notional=notional)


#: Today's requests for the plan (the section off): five whole sells by quantity,
#: then the 21 $4.00 notional buys; the four $0.50 buys never reach an order.
TODAY_REQUESTS = (
    *(_sell_request(sid) for sid in OLD),
    *(_buy_request(sid, FULL_BUY) for sid in FULL),
)


def _kinds(chain: Chain) -> dict[str, tuple[str, str | None]]:
    return {d.security_id: (d.decision, d.side) for d in chain.decided.decisions}


# --- section on: the 25-name plan at $100 -------------------------------------------


def test_a_25_name_plan_at_100_submits_25_notional_buys_at_or_above_the_minimum() -> None:
    """Section on: every target is a `trade` buy (no plan-time `skip_below_minimum`
    for the $0.50 ones), the five held names are `left_targets` sells, and the buys
    phase sizes 25 notional orders: $4.00 for the 4 % names and the $1.00 minimum
    for the 0.5 % ones, none by quantity, none deferred, none skipped."""
    chain = _chain(_profile())

    kinds = _kinds(chain)
    assert {sid: kinds[sid] for sid in TARGETS} == dict.fromkeys(TARGETS, ("trade", "buy"))
    assert {sid: kinds[sid] for sid in OLD} == dict.fromkeys(OLD, ("trade", "sell"))
    assert all(state.state is State.OPEN for state in chain.states.values())

    assert chain.buys.skips == ()
    assert chain.buys.deferred == ()
    by_name = {o.security_id: o for o in chain.buys.orders}
    assert sorted(by_name) == sorted(TARGETS)
    assert {sid: (o.notional, o.quantity) for sid, o in by_name.items()} == {
        **{sid: (FULL_BUY, None) for sid in FULL},
        **{sid: (MIN_BUY, None) for sid in SMALL},
    }
    assert all(not o.whole_share for o in chain.buys.orders)
    assert all(
        o.notional is not None and o.notional >= RiskConfig().min_order_notional
        for o in chain.buys.orders
    )
    # The rounded $1.00, not the planned $0.50, is what the cash pays.
    assert chain.buys.cash_left == pytest.approx(CASH_LEFT_ON, abs=1e-9)


def test_the_25_name_plan_passes_the_unchanged_limits_with_zero_skips() -> None:
    """ADR 0010's limits are unchanged (5 % weight, 5 % order notional, 250
    orders), and the plan fits them: both phases pass `check_phase` with no skip."""
    frozen = _profile()
    assert (frozen.max_position_weight, frozen.max_order_notional_fraction) == (0.05, 0.05)
    chain = _chain(frozen)
    assert isinstance(chain.sells_checked, Skips), chain.sells_checked
    assert isinstance(chain.buys_checked, Skips), chain.buys_checked
    assert chain.sells_checked.skips == ()
    assert chain.buys_checked.skips == ()
    assert len(chain.sells_checked.orders) == len(OLD)
    assert len(chain.buys_checked.orders) == len(TARGETS)
    assert not any(d.decision.startswith("skip_") for d in chain.decided.decisions)


def test_the_25_name_plan_submits_notional_buys_and_sells_by_quantity() -> None:
    """The requests the wrapper submits: the five sells by quantity (the whole
    0.2-share holding, never a notional) and 25 notional buys at or above the
    minimum, never a quantity."""
    chain = _chain(_profile())
    assert chain.requests == (
        *(_sell_request(sid) for sid in OLD),
        *(_buy_request(sid, FULL_BUY) for sid in FULL),
        *(_buy_request(sid, MIN_BUY) for sid in SMALL),
    )


def test_the_live_capital_count_is_printed_as_today_and_binds_nothing() -> None:
    """`n_orders_below_min_at_live_capital` still counts the four $0.50 buys at the
    $100 live reference, with the section on as off, while the section's plan
    orders all four."""
    assert _chain(RiskConfig()).decided.n_orders_below_min_at_live_capital == len(SMALL)
    on = _chain(_profile())
    assert on.decided.n_orders_below_min_at_live_capital == len(SMALL)
    assert {o.security_id for o in on.buys.orders} >= set(SMALL)


# --- the one allowed skip -------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason="T202")
def test_a_scripted_non_fractionable_name_is_the_one_allowed_skip() -> None:
    """Section on, the `assets` read scripts N25 not fractionable: its buy is never
    a whole-share order but the one skip, `skip_not_fractionable`, taken before
    sizing; it counts toward the skip cap and the journal accepts the reason
    (T202's allowlist); the other 24 buys are notional orders and the batch passes."""
    nf = SMALL[-1]
    chain = _chain(_profile(), _assets(nf))
    decision_id = {row.security_id: row.decision_id for row in chain.rows}[nf]
    assert decision_id is not None
    assert chain.buys.skips == (Skip(decision_id, nf, "skip_not_fractionable"),)
    assert chain.buys.skips[0].counts_toward_cap
    assert chain.buys.deferred == ()
    assert sorted(o.security_id for o in chain.buys.orders) == sorted(set(TARGETS) - {nf})
    assert all(o.quantity is None and o.notional is not None for o in chain.buys.orders)
    assert isinstance(chain.buys_checked, Skips), chain.buys_checked
    assert len(chain.buys_checked.orders) == len(TARGETS) - 1
    assert "skip_not_fractionable" in JOURNAL_ENUMS[("decision_events", "reason")]


# --- section off: the open windows are byte-identical -------------------------------


def _pre_t206_window() -> PaperWindowRow:
    return PaperWindowRow(
        window_id=1,
        hypothesis_id=1,
        first_rebalance_session=T,
        account_id="PA1",
        starting_cash=100.0,
        starting_equity=100.0,
        code_version="pre-T206",
        started_at=STAMP,
        frozen_json=PRE_T206_FROZEN_JSON,
        frozen_sha256="0" * 64,
        known_at=STAMP,
        ingested_at=STAMP,
    )


@pytest.mark.parametrize("source", ["default", "frozen_before_t206"])
def test_section_off_the_plan_gives_todays_requests_byte_for_byte(source: str) -> None:
    """The section's default, and a window frozen before the section existed (its
    `frozen_json` lacks `risk.small_account`; `frozen_risk` reads it, never
    refuses it), are off; the plan's requests are today's exactly: the four
    $0.50 buys are plan-time `skip_below_minimum`, the other orders unchanged."""
    frozen = RiskConfig() if source == "default" else frozen_risk(_pre_t206_window())
    chain = _chain(frozen)

    kinds = _kinds(chain)
    assert {sid: kinds[sid] for sid in SMALL} == dict.fromkeys(SMALL, ("skip_below_minimum", None))
    assert chain.requests == TODAY_REQUESTS
    assert [repr(r) for r in chain.requests] == [repr(r) for r in TODAY_REQUESTS]
    assert chain.buys.cash_left == pytest.approx(CASH_LEFT_OFF, abs=1e-9)
    assert frozen.small_account.enabled is False


def test_section_off_a_non_fractionable_buy_still_goes_by_whole_shares() -> None:
    """Off, a non-fractionable name keeps today's whole-share basis: its plan-time
    decision carries `whole_share`, and its $4.00 slice of a $50 share floors to
    no share, so the buys phase defers it (no order, no skip)."""
    frozen = RiskConfig()
    nf = FULL[0]
    chain = _chain(frozen, _assets(nf))
    row = {r.security_id: r for r in chain.rows}[nf]
    assert row.whole_share is True
    assert chain.buys.skips == ()
    assert chain.buys.deferred == (row.decision_id,)
    assert nf not in {o.security_id for o in chain.buys.orders}
    assert frozen.small_account.enabled is False


# --- the rounded notional is what the limits see (the wrapper, before any submit) ------


def test_a_sub_minimum_buy_is_submitted_at_the_minimum_through_the_wrapper(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """Section on, a $0.50 buy goes to the broker as one notional order of the
    $1.00 minimum (today it is deferred: no order at all)."""
    buy = _decision(env, A, "buy", notional=0.5, weight=0.00001)
    outcome = _execute(_gate(env, alerter_conn, _profile()), env, [buy])
    assert outcome.status == "ok"
    (request,) = _submits(env.fake)
    assert (request.symbol, request.side, request.notional, request.quantity) == (
        SYMBOLS[A],
        Side.BUY,
        MIN_BUY,
        None,
    )


@pytest.mark.parametrize(
    ("limits", "planned", "weight", "rule"),
    [
        # Equity $100,000: a $3,000 (3 %) buy rounded up to a $6,000 minimum holds
        # 6 % after the phase, over the 5 % weight; the order limit is opened to 10 %.
        (
            {"min_order_notional": 6000.0, "max_order_notional_fraction": 0.10},
            3000.0,
            0.03,
            "max_position_weight",
        ),
        # The same $6,000 order is over the 5 % order-notional limit; the weight
        # limit is opened to 10 %.
        (
            {"min_order_notional": 6000.0, "max_position_weight": 0.10},
            3000.0,
            0.03,
            "max_order_notional_fraction",
        ),
    ],
)
def test_a_rounded_up_buy_breaching_a_weight_or_order_limit_halts_before_any_submit(
    env: Env,
    alerter_conn: duckdb.DuckDBPyConnection,
    limits: dict[str, float],
    planned: float,
    weight: float,
    rule: str,
) -> None:
    """The limits see max(planned, `risk.min_order_notional`), not the planned
    figure: the rounding up is a `LimitBreachError` naming the rule, with zero
    submits and the rebalance `missed`, never a silently placed order (today the
    buy is deferred below the minimum instead)."""
    buy = _decision(env, A, "buy", notional=planned, weight=weight)
    with pytest.raises(LimitBreachError, match=rule):
        _execute(_gate(env, alerter_conn, _profile(**limits)), env, [buy])
    assert _submits(env.fake) == []
    assert _missed(env.settings) == [("missed", "limit_breach")]


def test_a_rounded_up_buy_over_the_cash_left_by_the_buy_reserve_halts_before_any_submit(
    env: Env, alerter_conn: duckdb.DuckDBPyConnection
) -> None:
    """Cash $5,000 less an earlier run's open $2,000 buy leaves the buys phase
    $3,000; a $1,000 buy rounded up to a $3,500 minimum needs more than that
    (though not more than the account's $5,000, nor 5 % of equity): a
    `LimitBreachError` on the cash rule, with zero submits."""
    env.new_fake(cash=5000.0)
    _open_buy(env, C, 2000.0)
    buy = _decision(env, A, "buy", notional=1000.0, weight=0.01)
    with pytest.raises(LimitBreachError, match="buys_within_cash"):
        _execute(_gate(env, alerter_conn, _profile(min_order_notional=3500.0)), env, [buy])
    assert _submits(env.fake) == []
    assert _missed(env.settings) == [("missed", "limit_breach")]
