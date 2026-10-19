"""Tests for the short-side rules of `execution.risk` (plan T231; ADR 0010's
amendment of 2026-10-19; paper spec amendment #1872, items 1, 3, 4 and 7).

`check_phase` gains three keyword arguments, each defaulting to today's long
behaviour so every `test_risk.py` case is unchanged:

- `window_side`: the window's frozen `portfolio.side` (`long_only` or
  `long_short`; default `long_only`);
- `broker_paper`: the broker's `paper` property (default `False`);
- `net_exposure`: the window's frozen `portfolio.net_exposure`, passed only for
  the phase after which a batch containing a rebalance's decisions is complete
  (the buy phase); `None` (the default) skips `net_exposure_band`.

An order's intent is the pair (`side`, `position_side`) (spec item 3): a
`sell` + `short` `PhaseOrder` is a `sell_to_open` (a short open), a `buy` +
`short` one a `buy_to_close` (a cover). A held short is a negative
`Ledger.positions` quantity (the broker's sign, spec item 10), and equity is
cash + long value - short value.

The hand book (`_short_ledger`): A long 40 at $10 ($400), D short 4 at $100
($400), cash $10,000; equity 10,000 + 400 - 400 = $10,000, gross 0.08, net 0.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from tradepartner.adapters.broker import Account, Asset
from tradepartner.backtest.costs import Commissions
from tradepartner.config import RiskConfig
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import BuyCosts
from tradepartner.execution.risk import PhaseOrder, Skips, Violations, check_phase
from tradepartner.store import schema
from tradepartner.store.journal import DecisionRow, OrderRow

_NOW = datetime(2026, 10, 19, 13, 0, tzinfo=UTC)
_S = date(2026, 10, 19)
_PRICES = {"A": 10.0, "B": 20.0, "C": 50.0, "D": 100.0}
_NO_COSTS = BuyCosts(per_side_bps=0.0, commissions=Commissions(per_share=0.0, per_order=0.0))
_SHORT = "short"
_LONG_SHORT = "long_short"
_LONG_ONLY = "long_only"


def _price_of(security_id: str) -> float:
    return _PRICES[security_id]


def _short_ledger(**positions: float) -> Ledger:
    """A long 40 ($400), D short 4 ($400), cash 10,000: equity $10,000."""
    return Ledger(positions=positions or {"A": 40.0, "D": -4.0}, cash=10_000.0, through=_S)


def _account(cash: float = 10_000.0, equity: float = 10_000.0) -> Account:
    return Account(account_id="PA1", cash=cash, buying_power=2 * cash, equity=equity, as_of=_NOW)


_ASSETS = {
    name: Asset(
        tradable=True,
        fractionable=True,
        status="active",
        cusip=None,
        shortable=True,
        easy_to_borrow=True,
    )
    for name in _PRICES
}


def _order(security_id: str, side: str, decision_id: int = 1, **kw: Any) -> PhaseOrder:
    base: dict[str, Any] = {
        "decision_id": decision_id,
        "security_id": security_id,
        "symbol": security_id,
        "side": side,
        "decision": "trade",
        "price": _PRICES[security_id],
    }
    return PhaseOrder(**{**base, **kw})


def _short_open(security_id: str, quantity: float, decision_id: int = 10, **kw: Any) -> PhaseOrder:
    """A `sell_to_open`: a whole-share quantity sell on the short side."""
    return _order(
        security_id,
        "sell",
        decision_id,
        quantity=quantity,
        whole_share=True,
        position_side=_SHORT,
        **kw,
    )


def _cover(security_id: str, quantity: float, decision_id: int = 20, **kw: Any) -> PhaseOrder:
    """A `buy_to_close`: a whole-share quantity buy on the short side."""
    return _order(
        security_id,
        "buy",
        decision_id,
        quantity=quantity,
        whole_share=True,
        position_side=_SHORT,
        **kw,
    )


def _check(
    orders: list[PhaseOrder],
    frozen: RiskConfig | None = None,
    **kw: Any,
) -> Violations | Skips:
    """`check_phase` on the hand book, a `long_short` window on a paper broker
    unless the test says otherwise."""
    args: dict[str, Any] = {
        "ledger": _short_ledger(),
        "account": _account(),
        "assets": _ASSETS,
        "frozen": frozen or RiskConfig(),
        "quantity_decimals": 6,
        "price_of": _price_of,
        "costs": _NO_COSTS,
        "window_side": _LONG_SHORT,
        "broker_paper": True,
    }
    args.update(kw)
    return check_phase(orders, **args)


def _rules(result: Violations | Skips) -> set[str]:
    assert isinstance(result, Violations), result
    return {v.rule for v in result.violations}


def _passes(result: Violations | Skips) -> Skips:
    assert isinstance(result, Skips), result
    return result


# --- refused_position_side: lifted only for a long_short window on paper (item 1) ---------


#: Every (window side, broker paper) pair; only the first lifts the refusal.
_COMBINATIONS = [
    (_LONG_SHORT, True, True),
    (_LONG_SHORT, False, False),
    (_LONG_ONLY, True, False),
    (_LONG_ONLY, False, False),
]


@pytest.mark.parametrize(("window_side", "broker_paper", "lifted"), _COMBINATIONS)
def test_a_short_open_passes_only_in_a_long_short_window_on_a_paper_broker(
    window_side: str, broker_paper: bool, lifted: bool
) -> None:
    """A $400 short open of B (0.04 of equity; gross after 0.12, net -0.04)
    breaks no limit: it passes when lifted and is refused by name, and by that
    rule alone, otherwise."""
    result = _check([_short_open("B", 20.0)], window_side=window_side, broker_paper=broker_paper)
    if lifted:
        assert [o.security_id for o in _passes(result).orders] == ["B"]
    else:
        assert _rules(result) == {"refused_position_side"}


def test_the_pinned_live_refusal_one_short_decision_on_a_broker_that_is_not_paper() -> None:
    """The plan line's pin: one short decision, a `long_short` window, a broker
    whose `paper` is `False`: the batch halts `refused_position_side` (a
    `limit_breach`, so the wrapper raises `LimitBreachError` before any submit)
    naming the decision. The Phase 6 live path inherits this."""
    result = _check([_short_open("B", 20.0, decision_id=7)], broker_paper=False)
    assert isinstance(result, Violations), result
    (violation,) = result.violations
    assert (violation.kind, violation.rule) == ("limit_breach", "refused_position_side")
    assert "decision 7" in violation.detail


def _session_decision(position_side: str) -> DecisionRow:
    return DecisionRow(
        decision_id=11,
        run_id=1,
        rebalance_session=_S,
        security_id="B",
        side="sell",
        planned_notional=400.0,
        target_notional=-400.0,
        whole_share=True,
        decision="trade",
        position_side=position_side,
        known_at=_NOW,
        ingested_at=_NOW,
    )


def _session_order(position_side: str) -> OrderRow:
    return OrderRow(
        client_order_id="tp-main-20261019-B-short-1",
        decision_id=11,
        run_id=1,
        session=_S,
        attempt=1,
        phase="buy",
        security_id="B",
        symbol="B",
        side="sell",
        quantity=20.0,
        sells_in_flight_at_submit=False,
        position_side=position_side,
        known_at=_NOW,
        ingested_at=_NOW,
    )


def _long_batch() -> list[PhaseOrder]:
    """A 2-share long sell of A and a $50 long buy of C at target 0.04."""
    return [
        _order("A", "sell", 1, quantity=2.0),
        _order("C", "buy", 2, notional=50.0, target_weight=0.04),
    ]


@pytest.mark.parametrize(("window_side", "broker_paper", "lifted"), _COMBINATIONS)
@pytest.mark.parametrize("row_kind", ["decision", "order"])
def test_a_short_row_on_the_session_is_refused_unless_lifted(
    window_side: str, broker_paper: bool, lifted: bool, row_kind: str
) -> None:
    """A short `decisions` or `orders` row of the session beside a long batch:
    passes only in a `long_short` window on paper; a short row in a `long_only`
    window on paper (the plan line's second pin) halts by name."""
    row = _session_decision(_SHORT) if row_kind == "decision" else _session_order(_SHORT)
    result = _check(
        _long_batch(),
        window_side=window_side,
        broker_paper=broker_paper,
        rows_on_session=[row],
    )
    if lifted:
        assert [o.security_id for o in _passes(result).orders] == ["A", "C"]
    else:
        assert _rules(result) == {"refused_position_side"}


@pytest.mark.parametrize(
    ("window_side", "broker_paper"),
    [
        ("long_shrt", True),  # a misspelt side
        ("LONG_SHORT", True),  # another case
        (_LONG_SHORT, 1),  # truthy, not the literal `True`
        (_LONG_SHORT, "True"),
    ],
)
def test_the_lift_fails_closed_on_anything_but_the_exact_pair(
    window_side: str, broker_paper: Any
) -> None:
    """Only `window_side == "long_short"` with `broker_paper is True` lifts the
    refusal (the adapters' own `is not True` guard); any other value refuses."""
    result = _check([_short_open("B", 20.0)], window_side=window_side, broker_paper=broker_paper)
    assert _rules(result) == {"refused_position_side"}


def test_a_long_batch_is_unchanged_by_the_new_arguments() -> None:
    """A long-only book's batch reads the same under every window side and
    broker: the long ledger (A 10 at $10, B 5 at $20, cash 1,000) and the long
    batch pass with the same orders as with the defaults."""
    long_ledger = Ledger(positions={"A": 10.0, "B": 5.0}, cash=1000.0, through=_S)
    base = {"ledger": long_ledger, "account": _account(cash=1000.0, equity=1200.0)}
    default = check_phase(
        _long_batch(),
        assets=_ASSETS,
        frozen=RiskConfig(),
        quantity_decimals=6,
        price_of=_price_of,
        costs=_NO_COSTS,
        **base,
    )
    for window_side, broker_paper, _ in _COMBINATIONS:
        assert (
            _check(_long_batch(), window_side=window_side, broker_paper=broker_paper, **base)
            == default
        )


# --- gross exposure: sum of |held value| over signed equity (ADR 0010 amendment) ----------


def test_gross_exposure_counts_the_short_leg_over_signed_equity() -> None:
    """After a $400 short open of B: |A 400| + |B -400| + |D -400| = 1,200 over
    equity 10,000 (cash + long - short) = 0.12. A cap of 0.115 breaches; 0.122
    passes. (Equity read as 10,800, cash + |values|, would pass 0.115; read as
    9,600 would breach 0.122; today's long-only sum, 0.04, would pass both.)"""
    orders = [_short_open("B", 20.0)]
    assert _rules(_check(orders, RiskConfig(max_gross_exposure=0.115))) == {"max_gross_exposure"}
    _passes(_check(orders, RiskConfig(max_gross_exposure=0.122)))


def test_a_cover_lowers_gross_exposure_after_the_phase() -> None:
    """Covering D's 4 shares leaves A's $400: gross 0.04 after the phase, under
    a cap of 0.05 that the book's 0.08 before the phase is over."""
    _passes(_check([_cover("D", 4.0, full_exit=True)], RiskConfig(max_gross_exposure=0.05)))


# --- max_short_weight: a short open's |value| after the phase over equity ------------------


def test_a_short_open_counts_the_held_short_after_the_phase() -> None:
    """D is held short 4 ($400, 0.04). Opening 1 more: 5 x 100 = 500 / 10,000 =
    0.05, at the 0.05 default, passes; opening 2: 600 / 10,000 = 0.06 breaches."""
    _passes(_check([_short_open("D", 1.0)]))
    assert _rules(_check([_short_open("D", 2.0)])) == {"max_short_weight"}


def test_max_short_weight_is_read_from_the_frozen_section() -> None:
    """The B open is 0.04 of equity: over a frozen 0.03."""
    assert _rules(_check([_short_open("B", 20.0)], RiskConfig(max_short_weight=0.03))) == {
        "max_short_weight"
    }


def test_a_held_short_over_the_cap_that_the_phase_does_not_open_is_not_checked() -> None:
    """D held short 6 ($600, over 0.05 of equity) and a phase that only buys C:
    drift is the forced cover's (`cover_drift`), never a halt here."""
    _passes(_check(_long_batch(), ledger=_short_ledger(A=40.0, D=-6.0)))


# --- net_exposure_band: |net after the batch - frozen portfolio.net_exposure| ---------------


def test_the_net_band_breaches_by_name() -> None:
    """After the B open, net = (400 - 400 - 400) / 10,000 = -0.04. Against a
    frozen net of 0.0: 0.04 passes the 0.10 default and breaches a 0.03 band.
    Against 0.07: 0.11 breaches the default; against 0.05: 0.09 passes."""
    orders = [_short_open("B", 20.0)]
    _passes(_check(orders, net_exposure=0.0))
    assert _rules(_check(orders, RiskConfig(net_exposure_band=0.03), net_exposure=0.0)) == {
        "net_exposure_band"
    }
    assert _rules(_check(orders, net_exposure=0.07)) == {"net_exposure_band"}
    _passes(_check(orders, net_exposure=0.05))


def test_the_net_band_is_not_checked_without_a_frozen_net() -> None:
    """`net_exposure=None` (a sell phase, a forced-exit-only batch): even a band
    of 0 does not halt the B open's -0.04."""
    _passes(_check([_short_open("B", 20.0)], RiskConfig(net_exposure_band=0.0)))


def test_a_long_only_window_never_consults_the_net_band() -> None:
    """A long-only window on paper with a frozen net of 0.0 and a band of 0: the
    long book's net (about 0.2) is never compared."""
    long_ledger = Ledger(positions={"A": 10.0, "B": 5.0}, cash=1000.0, through=_S)
    _passes(
        _check(
            _long_batch(),
            RiskConfig(net_exposure_band=0.0),
            window_side=_LONG_ONLY,
            ledger=long_ledger,
            account=_account(cash=1000.0, equity=1200.0),
            net_exposure=0.0,
        )
    )


# --- min_short_equity: account().equity before any short open -----------------------------


def test_a_short_open_below_the_minimum_account_equity_halts() -> None:
    """The broker's `account().equity` (not the ledger's) is read: 1,999.99
    halts the B open; exactly 2,000.0 passes ("below it halts")."""
    orders = [_short_open("B", 20.0)]
    assert _rules(_check(orders, account=_account(equity=1999.99))) == {"min_short_equity"}
    _passes(_check(orders, account=_account(equity=2000.0)))


def test_min_short_equity_is_read_from_the_frozen_section() -> None:
    frozen = RiskConfig(min_short_equity=20_000.0)
    assert _rules(_check([_short_open("B", 20.0)], frozen)) == {"min_short_equity"}


@pytest.mark.parametrize(
    "orders",
    [
        [_cover("D", 4.0, full_exit=True)],
        [_order("C", "buy", 2, notional=50.0, target_weight=0.04)],
    ],
    ids=["cover", "long_buy"],
)
def test_a_batch_with_no_short_open_ignores_the_minimum_equity(orders: list[PhaseOrder]) -> None:
    """Account equity 1,000 is below the minimum, but a cover or a long buy
    opens no short."""
    _passes(_check(orders, account=_account(equity=1000.0)))


# --- the restated cash rule: cash less the held short value -------------------------------


def test_buys_are_within_cash_less_the_held_short_value() -> None:
    """Cash 800 less D's held short at the reference price ($400) leaves $400:
    a $400.00 buy of C passes, $400.01 breaches (today's rule, within $800,
    would pass both)."""
    poor = _account(cash=800.0)
    _passes(_check([_order("C", "buy", 2, notional=400.0, target_weight=0.04)], account=poor))
    assert _rules(
        _check([_order("C", "buy", 2, notional=400.01, target_weight=0.04)], account=poor)
    ) == {"buys_within_cash"}


def test_a_short_open_in_the_phase_does_not_take_the_buys_cash() -> None:
    """Only the held shorts are deducted: the phase's own short open has not
    raised its proceeds into the cash read yet, so a $400 B open beside the
    $400.00 C buy still passes on cash 800 (cash after both fills, 800 + 400 -
    400 = 800, still covers both shorts' 800)."""
    orders = [
        _short_open("B", 20.0),
        _order("C", "buy", 2, notional=400.0, target_weight=0.04),
    ]
    _passes(_check(orders, account=_account(cash=800.0)))


# --- the structural rules: covers, one side per name, whole-share shorts -----------------


def test_a_cover_within_the_held_short_passes() -> None:
    """A full cover of D's 4 and a partial cover of 2 both pass (a cover is a
    buy that needs no target weight)."""
    _passes(_check([_cover("D", 4.0, full_exit=True)]))
    _passes(_check([_cover("D", 2.0)]))


def test_a_full_cover_is_a_full_exit_exempt_from_the_per_order_limit() -> None:
    """D held short 6 ($600, over the 0.05 x 9,800 = $490 per-order limit; equity
    10,000 + 400 - 600): a full cover, a trade's or a forced exit's
    (`cover_drift` and the other `cover_*` reasons), is a buy marked as an exit
    and passes, as a long full exit does; a partial cover of 6 is not exempt."""
    ledger = _short_ledger(A=40.0, D=-6.0)
    _passes(_check([_cover("D", 6.0, full_exit=True)], ledger=ledger))
    _passes(_check([_cover("D", 6.0, full_exit=True, decision="forced_exit")], ledger=ledger))
    assert _rules(_check([_cover("D", 6.0)], ledger=ledger)) == {"max_order_notional_fraction"}


@pytest.mark.parametrize(
    ("security_id", "quantity"),
    [
        ("D", 5.0),  # one share over the held short of 4
        ("B", 1.0),  # not held
        ("A", 1.0),  # held long, not short
    ],
)
def test_a_cover_over_the_held_short_breaches(security_id: str, quantity: float) -> None:
    assert "cover_within_holding" in _rules(_check([_cover(security_id, quantity)]))


@pytest.mark.parametrize(
    "orders",
    [
        [_short_open("A", 1.0)],  # a short open of a name held long
        [_order("D", "buy", 2, notional=50.0, target_weight=0.005)],  # a long buy of a held short
        [  # a flip inside one phase: close A's long and open A short
            _order("A", "sell", 1, quantity=40.0, full_exit=True),
            _short_open("A", 1.0),
        ],
    ],
    ids=["short_a_long", "buy_a_short", "flip_in_phase"],
)
def test_no_name_is_long_and_short_and_no_flip_within_a_phase(orders: list[PhaseOrder]) -> None:
    """A flip is a close in the sell phase and an open in the buy phase, never
    one phase (spec item 4)."""
    assert "one_side_per_name" in _rules(_check(orders))


def test_a_long_sell_on_a_short_book_stays_within_the_long_holding() -> None:
    """A `sell_to_close` never becomes a short: 41 of A's 40 breaches, and so
    does a long sell of D, held only short."""
    assert "sell_within_holding" in _rules(_check([_order("A", "sell", 1, quantity=41.0)]))
    assert "sell_within_holding" in _rules(_check([_order("D", "sell", 1, quantity=1.0)]))


@pytest.mark.parametrize(
    "order",
    [
        _order("B", "sell", 10, notional=400.0, position_side=_SHORT),
        _short_open("B", 2.5),
        _order("D", "buy", 20, notional=200.0, position_side=_SHORT),
        _cover("D", 1.5),
    ],
    ids=["notional_open", "fractional_open", "notional_cover", "fractional_cover"],
)
def test_a_short_request_by_notional_or_fractional_quantity_is_refused(order: PhaseOrder) -> None:
    """Short opens and covers go by whole-share quantity only (spec item 3;
    Alpaca refuses fractional shorts): anything else is a `ValueError`, as the
    ADR's row says, never a skip."""
    with pytest.raises(ValueError):
        _check([order])


def test_the_hand_long_short_batch_passes_every_rule() -> None:
    """One buy phase of the hand book: open B short 20 ($400), buy C $50 at
    0.04. Gross after = (400 + 400 + 400 + 50) / 10,000 = 0.125, net =
    (400 + 50 - 400 - 400) / 10,000 = -0.035 against a frozen 0.0, B's short
    weight 0.04, cash 10,000 - 400 = 9,600 for $50: every rule passes."""
    orders = [
        _short_open("B", 20.0),
        _order("C", "buy", 2, notional=50.0, target_weight=0.04),
    ]
    result = _passes(_check(orders, net_exposure=0.0))
    assert [o.security_id for o in result.orders] == ["B", "C"]
    assert result.orders[0].position_side == _SHORT
    assert result.orders[1].position_side == schema.LONG
