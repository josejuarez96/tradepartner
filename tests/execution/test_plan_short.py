"""Signed decisions in the paper planner's pure layer (plan T229; paper spec
amendment #1872, items 3, 6 and 14, #1904).

Locked tests (Opus locks, Sonnet builds). `decisions_from` gains the keywords
`side` (the window's frozen `portfolio.side`) and `short_min_price` (the
frozen `portfolio.short_min_price`); `Decision` gains `position_side`; an
order's intent is (`side`, `position_side`) (item 3). A held short is the
ledger's unsigned `short_holdings` (T232). Every value below is computed by
hand in the comments; none is read back from the code under test.

The backtest parity test (`signed_target_weights` against the paper's target
notionals) is T235's, not this file's (#1904).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

import polars as pl
import pytest

from tradepartner.adapters.broker import Asset
from tradepartner.backtest.costs import Commissions, buy_notional_after_costs
from tradepartner.backtest.engine import Plan
from tradepartner.backtest.provider import GapReading
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import (
    BuyCosts,
    Decision,
    Decisions,
    HoldBook,
    decisions_from,
    is_full_exit,
)
from tradepartner.store.journal import OverrideRow

T = date(2026, 9, 30)  # rebalance session T_i
S = date(2026, 10, 1)  # the run's session; S-1 is T
STAMP = datetime(2026, 9, 30, 21, tzinfo=UTC)
FROZEN = RiskConfig()  # min_order_notional 1.0
SETTINGS = Settings(_env_file=None)
COSTS = BuyCosts(per_side_bps=10.0, commissions=Commissions(per_share=0.0, per_order=0.0))
NO_COSTS = BuyCosts(per_side_bps=0.0, commissions=Commissions(per_share=0.0, per_order=0.0))
SHORT_MIN_PRICE = 5.0  # the backtest spec's portfolio.short_min_price default
LONG_SHORT = "long_short"
LONG_ONLY = "long_only"
LONG = "long"
SHORT = "short"

NO_ACTIONS = pl.DataFrame(
    schema={
        "security_id": pl.Utf8,
        "action_type": pl.Utf8,
        "ex_date": pl.Date,
        "ratio_or_amount": pl.Float64,
        "known_at": pl.Datetime("us", "UTC"),
    }
)

# --- the one 50/50 book, by hand ------------------------------------------------------
#
# Reference prices (close(S-1), no splits):
PRICES = {
    "A": 50.0,  # held long 10, target +0.25: a long buy
    "B": 20.0,  # not held, target +0.25: a long buy
    "F": 25.0,  # held long 4, target -0.10: a flip (close long, open short)
    "G": 40.0,  # held short 5, target -0.01: a partial cover (not ETB: no check on a cover)
    "H": 20.0,  # held short 3, a member, no target: covered whole, left_targets
    "J": 30.0,  # held short 2, not a member: covered whole, left_universe
    "K": 50.0,  # not held, target -0.10: a short open
    "M": 50.0,  # held short 2, target -0.031: a short add
    "N": 20.0,  # not held, target -0.05, shortable False: skip_not_borrowable
    "N2": 20.0,  # not held, target -0.05, easy_to_borrow False: skip_not_borrowable
    "P": 4.0,  # not held, target -0.05, under the $5 floor: skip_short_price
    "Q": 10.0,  # not held, target -0.00005: 0.50 < 1.0, skip_below_minimum
    "R": 300.0,  # not held, target -0.02: 200 < one share at 300, skip_below_one_share
    "U": 100.0,  # held short 1, target -0.00995: a 0.50 cover, skip_below_minimum
    "V": 400.0,  # held short 2, target -0.079: a 10.00 cover < one share, skip_below_one_share
    "W": 10.0,  # held short 1, listing ended at S-1: skip_delisted
    "Z": 10.0,  # held short 1, in open_forced_exits: no decision
}
LONGS = {"A": 10.0, "F": 4.0}  # 500 + 100 = 600
SHORTS = {"G": 5.0, "H": 3.0, "J": 2.0, "M": 2.0, "U": 1.0, "V": 2.0, "W": 1.0, "Z": 1.0}
# Short value: G 200 + H 60 + J 60 + M 100 + U 100 + V 800 + W 10 + Z 10 = 1340.
CASH = 10_740.0
EQUITY = 10_000.0  # 10740 + 600 - 1340
TARGETS = {
    "A": 0.25,
    "B": 0.25,
    "F": -0.10,
    "G": -0.01,
    "K": -0.10,
    "M": -0.031,
    "N": -0.05,
    "N2": -0.05,
    "P": -0.05,
    "Q": -0.00005,
    "R": -0.02,
    "U": -0.00995,
    "V": -0.079,
}
# Long leg 0.50; short leg 0.10 + 0.01 + 0.10 + 0.031 + 0.05 x 3 + 0.00005 + 0.02
# + 0.00995 + 0.079 = 0.50: gross 1.0, net 0.0.
NOT_BORROWABLE = {"N": (False, True), "N2": (True, False), "G": (False, False)}


def price_of(security_id: str) -> float:
    return PRICES[security_id]


def _assets(flags: Mapping[str, tuple[bool, bool]] = NOT_BORROWABLE) -> dict[str, Asset]:
    """Every name fractionable; (shortable, easy_to_borrow) True unless `flags` says."""
    return {
        sid: Asset(
            tradable=True,
            fractionable=True,
            status="active",
            cusip=None,
            shortable=flags.get(sid, (True, True))[0],
            easy_to_borrow=flags.get(sid, (True, True))[1],
        )
        for sid in PRICES
    }


def _plan(targets: Mapping[str, float], members: tuple[str, ...]) -> Plan:
    scores = {sid: float(len(members) - i) for i, sid in enumerate(sorted(members))}
    return Plan(
        session=T,
        fill_session=S,
        targets=dict(targets),
        n_universe=len(members),
        n_static_listings=0,
        n_excluded_no_history=0,
        gap=GapReading(count_share=0.0, size_share=0.0),
        members=tuple(sorted(members)),
        scores=scores,
        excluded_no_history=(),
        exclusions={"no_history": ()},
    )


BOOK_MEMBERS = tuple(sid for sid in PRICES if sid != "J")


def _book_plan() -> Plan:
    return _plan(TARGETS, BOOK_MEMBERS)


def _book_ledger() -> Ledger:
    return Ledger(positions=dict(LONGS), cash=CASH, through=S, short_holdings=dict(SHORTS))


def _run(
    plan: Plan,
    ledger: Ledger,
    *,
    assets: Mapping[str, Asset] | None = None,
    listings_at: Mapping[str, date | None] | None = None,
    open_forced_exits: frozenset[str] = frozenset(),
    overrides: tuple[OverrideRow, ...] = (),
    costs: BuyCosts = COSTS,
    **window: Any,
) -> Decisions:
    return decisions_from(
        plan,
        ledger,
        overrides,
        assets if assets is not None else _assets(),
        listings_at or {},
        open_forced_exits,
        FROZEN,
        SETTINGS,
        price_of=price_of,
        actions_as_of=NO_ACTIONS,
        costs=costs,
        **window,
    )


def _book() -> Decisions:
    return _run(
        _book_plan(),
        _book_ledger(),
        listings_at={"W": T},  # ended on S-1
        open_forced_exits=frozenset({"Z"}),
        side=LONG_SHORT,
        short_min_price=SHORT_MIN_PRICE,
    )


def _key(d: Decision) -> tuple[Any, ...]:
    return (d.security_id, d.decision, d.side, d.position_side, d.reason)


# The hand list: by security_id, a flip's close before its open (the phase order).
HAND = [
    ("A", "trade", "buy", LONG, None),
    ("B", "trade", "buy", LONG, None),
    ("F", "trade", "sell", LONG, "left_targets"),
    ("F", "trade", "sell", SHORT, None),
    ("G", "trade", "buy", SHORT, None),
    ("H", "trade", "buy", SHORT, "left_targets"),
    ("J", "trade", "buy", SHORT, "left_universe"),
    ("K", "trade", "sell", SHORT, None),
    ("M", "trade", "sell", SHORT, None),
    ("N", "skip_not_borrowable", None, SHORT, None),
    ("N2", "skip_not_borrowable", None, SHORT, None),
    ("P", "skip_short_price", None, SHORT, None),
    ("Q", "skip_below_minimum", None, SHORT, None),
    ("R", "skip_below_one_share", None, SHORT, None),
    ("U", "skip_below_minimum", None, SHORT, None),
    ("V", "skip_below_one_share", None, SHORT, None),
    ("W", "skip_delisted", None, SHORT, None),
]


def test_the_50_50_books_decisions_equal_the_hand_list() -> None:
    """One decision per name, two for the flip F, none for Z (an open forced
    exit); every short decision, skips included, `position_side = short`."""
    got = _book().decisions
    assert [_key(d) for d in got] == HAND
    for d in got:
        assert d.rebalance_session == T
        assert d.override_id is None


def test_the_signed_weights_by_hand() -> None:
    """`target_weight` is the plan's signed weight; `drifted_weight` the held
    signed value over equity 10,000 (a short negative)."""
    got = _book().decisions
    weights = {(d.security_id, d.side, d.position_side): d for d in got}
    expected_drift = {
        "A": 500 / EQUITY,  # 0.05
        "B": 0.0,
        "G": -200 / EQUITY,  # -0.02
        "H": -60 / EQUITY,  # -0.006
        "J": -60 / EQUITY,
        "K": 0.0,
        "M": -100 / EQUITY,  # -0.01
        "N": 0.0,
        "N2": 0.0,
        "P": 0.0,
        "Q": 0.0,
        "R": 0.0,
        "U": -100 / EQUITY,  # -0.01
        "V": -800 / EQUITY,  # -0.08
        "W": -10 / EQUITY,  # -0.001
    }
    by_name = {d.security_id: d for d in got if d.security_id != "F"}
    for sid, drift in expected_drift.items():
        assert by_name[sid].drifted_weight == pytest.approx(drift, abs=1e-12), sid
        assert by_name[sid].target_weight == pytest.approx(TARGETS.get(sid, 0.0), abs=1e-12), sid
    close = weights[("F", "sell", LONG)]
    assert close.target_weight == pytest.approx(-0.10)
    assert close.drifted_weight == pytest.approx(100 / EQUITY)  # 0.01, the held long
    assert weights[("F", "sell", SHORT)].target_weight == pytest.approx(-0.10)


def test_the_sizes_and_the_signed_target_notional_by_hand() -> None:
    """`planned_notional` unsigned; `target_notional` signed by the position
    change: a long buy +, a partial cover +, a short open or add -; a full exit
    by quantity with no `target_notional`."""
    got = {(d.security_id, d.side, d.position_side): d for d in _book().decisions}
    # Long buys: (0.25 - 0.05) x 10000 = 2000 and 0.25 x 10000 = 2500. Their sum
    # 4500 is under the spendable (9500 after costs, below), so neither is scaled.
    a = got[("A", "buy", LONG)]
    assert (a.planned_notional, a.target_notional) == (pytest.approx(2000.0), pytest.approx(2000.0))
    b = got[("B", "buy", LONG)]
    assert (b.planned_notional, b.target_notional) == (pytest.approx(2500.0), pytest.approx(2500.0))
    # Flip F: the long close sells the whole 4 shares; the open is sized from a
    # zero holding, 0.10 x 10000 = 1000.
    close = got[("F", "sell", LONG)]
    assert (close.planned_quantity, close.planned_notional, close.target_notional) == (
        4.0,
        None,
        None,
    )
    open_ = got[("F", "sell", SHORT)]
    assert open_.planned_quantity is None
    assert open_.planned_notional == pytest.approx(1000.0)
    assert open_.target_notional == pytest.approx(-1000.0)
    # G: (-0.01 - (-0.02)) x 10000 = +100, a partial cover by notional.
    g = got[("G", "buy", SHORT)]
    assert (g.planned_quantity, g.planned_notional, g.target_notional) == (
        None,
        pytest.approx(100.0),
        pytest.approx(100.0),
    )
    # H and J: covered whole by quantity, 3 and 2 shares.
    for sid, shares in (("H", 3.0), ("J", 2.0)):
        cover = got[(sid, "buy", SHORT)]
        assert (cover.planned_quantity, cover.planned_notional, cover.target_notional) == (
            shares,
            None,
            None,
        )
    # K: a short open, -0.10 x 10000 = -1000.
    k = got[("K", "sell", SHORT)]
    assert (k.planned_notional, k.target_notional) == (
        pytest.approx(1000.0),
        pytest.approx(-1000.0),
    )
    # M: (-0.031 - (-0.01)) x 10000 = -210, a short add.
    m = got[("M", "sell", SHORT)]
    assert (m.planned_notional, m.target_notional) == (pytest.approx(210.0), pytest.approx(-210.0))


def test_skips_carry_no_side_and_no_size() -> None:
    for d in _book().decisions:
        if d.decision.startswith("skip_"):
            assert (d.side, d.planned_notional, d.planned_quantity, d.target_notional) == (
                None,
                None,
                None,
                None,
            ), d.security_id


def test_a_short_decision_is_whole_share_whatever_fractionable_says() -> None:
    """Every name is `fractionable` here; a long stays fractional, a short
    decision (trade or skip) is whole-share (item 3: no fractional short)."""
    for d in _book().decisions:
        assert d.whole_share is (d.position_side == SHORT), (d.security_id, d.position_side)


def test_the_long_buys_spendable_is_free_cash_plus_the_long_sells() -> None:
    """Spec item 14 (c): spendable = cash - short value + long sells, at the
    reference prices; a short open's proceeds and a cover's cost leave it alone.

    Ledger: cash 1500, long C 2 x 100 = 200, short K 10 x 50 = 500; equity
    1500 + 200 - 500 = 1200. Plan: B +1.0 (a buy of 1200), C out of the targets
    (a long sell of 200), K -0.50 (held -500/1200; target -600: an add of 100),
    L -0.25 (a short open of 300).
    Spendable = 1500 - 500 + 200 = 1200, after 10 bp: 1200 / 1.001.
    B's target = 1200 x min(1, (1200 / 1.001) / 1200) = 1200 / 1.001 = 1198.8012.
    (Counting cash alone, 1500 + 200, or the short proceeds, would leave B at 1200.)
    """
    prices = {"B": 20.0, "C": 100.0, "K": 50.0, "L": 25.0}
    local_prices = dict(prices)

    def local_price(sid: str) -> float:
        return local_prices[sid]

    plan = _plan({"B": 1.0, "K": -0.5, "L": -0.25}, ("B", "C", "K", "L"))
    ledger = Ledger(positions={"C": 2.0}, cash=1500.0, through=S, short_holdings={"K": 10.0})
    assets = {
        sid: Asset(
            tradable=True,
            fractionable=True,
            status="active",
            cusip=None,
            shortable=True,
            easy_to_borrow=True,
        )
        for sid in prices
    }
    got = decisions_from(
        plan,
        ledger,
        (),
        assets,
        {},
        frozenset(),
        FROZEN,
        SETTINGS,
        price_of=local_price,
        actions_as_of=NO_ACTIONS,
        costs=COSTS,
        side=LONG_SHORT,
        short_min_price=SHORT_MIN_PRICE,
    )
    by = {(d.security_id, d.side, d.position_side): d for d in got.decisions}
    spendable = buy_notional_after_costs(1200.0, 10.0, COSTS.commissions, price=20.0)
    assert spendable == pytest.approx(1200.0 / 1.001)
    b = by[("B", "buy", LONG)]
    assert b.planned_notional == pytest.approx(1200.0)
    assert b.target_notional == pytest.approx(1200.0 * min(1.0, spendable / 1200.0))
    assert b.target_notional == pytest.approx(1198.8011988011988)
    assert by[("C", "sell", LONG)].planned_quantity == 2.0
    assert by[("K", "sell", SHORT)].target_notional == pytest.approx(-100.0)
    assert by[("L", "sell", SHORT)].target_notional == pytest.approx(-300.0)


# --- item 14 (e): the short skips' order ---------------------------------------------


@pytest.mark.parametrize(
    ("price", "flags", "target", "expected"),
    [
        # Not borrowable and under the floor: the borrow check is first.
        (4.0, (False, False), -0.05, "skip_not_borrowable"),
        # Under the floor and under the minimum (0.00005 x 10000 = 0.50 < 1.0).
        (4.0, (True, True), -0.00005, "skip_short_price"),
        # Under the minimum and under one share (0.50 < 1.0 and 0.50 / 300 < 1).
        (300.0, (True, True), -0.00005, "skip_below_minimum"),
        # Under one share only (200 / 300 < 1).
        (300.0, (True, True), -0.02, "skip_below_one_share"),
        # Every check passed: a short open of 0.05 x 10000 = 500.
        (300.0, (True, True), -0.05, "trade"),
    ],
)
def test_the_short_skips_apply_in_item_14s_order(
    price: float, flags: tuple[bool, bool], target: float, expected: str
) -> None:
    """One name, not held, in an all-cash book of 10,000."""
    local_prices = {"Y": price}
    plan = _plan({"Y": target}, ("Y",))
    ledger = Ledger(positions={}, cash=EQUITY, through=S)
    assets = {
        "Y": Asset(
            tradable=True,
            fractionable=True,
            status="active",
            cusip=None,
            shortable=flags[0],
            easy_to_borrow=flags[1],
        )
    }
    got = decisions_from(
        plan,
        ledger,
        (),
        assets,
        {},
        frozenset(),
        FROZEN,
        SETTINGS,
        price_of=local_prices.__getitem__,
        actions_as_of=NO_ACTIONS,
        costs=COSTS,
        side=LONG_SHORT,
        short_min_price=SHORT_MIN_PRICE,
    )
    (y,) = got.decisions
    assert (y.decision, y.position_side) == (expected, SHORT)
    if expected == "trade":
        assert (y.side, y.planned_notional, y.target_notional) == (
            "sell",
            pytest.approx(500.0),
            pytest.approx(-500.0),
        )


def test_a_price_at_the_floor_is_shortable() -> None:
    """`skip_short_price` is strictly below `short_min_price`: 5.00 passes."""
    plan = _plan({"Y": -0.05}, ("Y",))
    ledger = Ledger(positions={}, cash=EQUITY, through=S)
    assets = {
        "Y": Asset(
            tradable=True,
            fractionable=True,
            status="active",
            cusip=None,
            shortable=True,
            easy_to_borrow=True,
        )
    }
    got = decisions_from(
        plan,
        ledger,
        (),
        assets,
        {},
        frozenset(),
        FROZEN,
        SETTINGS,
        price_of={"Y": 5.0}.__getitem__,
        actions_as_of=NO_ACTIONS,
        costs=COSTS,
        side=LONG_SHORT,
        short_min_price=5.0,
    )
    (y,) = got.decisions
    assert (y.decision, y.side, y.position_side) == ("trade", "sell", SHORT)


# --- overrides, rows and is_full_exit on a short --------------------------------------


def test_exclude_name_covers_a_held_short_whole() -> None:
    override = OverrideRow(
        override_id=7,
        window_id=1,
        made_at=STAMP,
        rebalance_session=T,
        security_id="H",
        kind="exclude_name",
        reason="the owner's reason, long enough",
        known_at=STAMP,
        ingested_at=STAMP,
    )
    got = _run(
        _plan({"A": 0.5, "H": -0.5}, ("A", "H")),
        Ledger(positions={}, cash=1060.0, through=S, short_holdings={"H": 3.0}),
        overrides=(override,),
        side=LONG_SHORT,
        short_min_price=SHORT_MIN_PRICE,
    )
    h = next(d for d in got.decisions if d.security_id == "H")
    assert (h.decision, h.side, h.position_side, h.reason, h.override_id) == (
        "override",
        "buy",
        SHORT,
        "exclude_name",
        7,
    )
    assert (h.planned_quantity, h.planned_notional) == (3.0, None)


def test_the_row_carries_position_side() -> None:
    d = Decision(
        security_id="K",
        rebalance_session=T,
        decision="trade",
        whole_share=True,
        target_weight=-0.10,
        drifted_weight=0.0,
        side="sell",
        position_side=SHORT,
        planned_notional=1000.0,
        target_notional=-1000.0,
    )
    row = d.row(run_id=1, known_at=STAMP, ingested_at=STAMP, book_id="main")
    assert (row.side, row.position_side, row.target_notional) == ("sell", SHORT, -1000.0)


def test_a_long_decisions_position_side_defaults_to_long() -> None:
    d = Decision(
        security_id="A",
        rebalance_session=T,
        decision="trade",
        whole_share=False,
        target_weight=0.5,
        drifted_weight=0.0,
        side="buy",
        planned_notional=10.0,
    )
    assert d.position_side == LONG
    assert d.row(run_id=1, known_at=STAMP, ingested_at=STAMP, book_id="main").position_side == LONG


def _row(**fields: Any) -> Any:
    base: dict[str, Any] = {
        "security_id": "H",
        "rebalance_session": T,
        "decision": "trade",
        "whole_share": True,
        "target_weight": 0.0,
        "drifted_weight": -0.006,
        "side": "buy",
        "position_side": SHORT,
    }
    base.update(fields)
    return Decision(**base).row(run_id=1, known_at=STAMP, ingested_at=STAMP, book_id="main")


@pytest.mark.parametrize(
    ("fields", "full"),
    [
        ({"reason": "left_targets", "planned_quantity": 3.0}, True),
        ({"reason": "left_universe", "planned_quantity": 3.0}, True),
        (
            {
                "decision": "override",
                "reason": "exclude_name",
                "override_id": 7,
                "planned_quantity": 3.0,
            },
            True,
        ),
        # A partial cover by notional is a trim, not a full exit.
        ({"reason": None, "planned_notional": 100.0}, False),
        # A short open is never a full exit.
        ({"side": "sell", "reason": None, "planned_notional": 100.0}, False),
        # A long buy stays what it is today.
        ({"position_side": LONG, "reason": None, "planned_notional": 100.0}, False),
    ],
)
def test_is_full_exit_reads_a_whole_cover(fields: dict[str, Any], full: bool) -> None:
    assert is_full_exit(_row(**fields)) is full


# --- the window keywords: refusals and the long book unchanged ------------------------


def _long_plan() -> Plan:
    return _plan({"A": 0.5, "B": 0.5}, ("A", "B", "F"))


def _long_ledger() -> Ledger:
    # A 10 x 50 = 500, F 4 x 25 = 100 (left_targets), cash 1000; equity 1600.
    return Ledger(positions={"A": 10.0, "F": 4.0}, cash=1000.0, through=S)


def test_long_only_refuses_a_negative_target() -> None:
    with pytest.raises(ValueError):
        _run(_plan({"A": 0.5, "K": -0.5}, ("A", "K")), _long_ledger(), side=LONG_ONLY)


def test_long_only_refuses_a_held_short() -> None:
    ledger = Ledger(positions={"A": 10.0}, cash=1000.0, through=S, short_holdings={"H": 3.0})
    with pytest.raises(ValueError):
        _run(_long_plan(), ledger, side=LONG_ONLY)


def test_long_short_refuses_a_band() -> None:
    with pytest.raises(ValueError):
        _run(
            _book_plan(),
            _book_ledger(),
            side=LONG_SHORT,
            short_min_price=SHORT_MIN_PRICE,
            band=0.01,
        )


def test_long_short_refuses_hold() -> None:
    hold = HoldBook(entries={}, ordinal=0, hold_sessions=1, hold_target_names=2, top_fraction=0.1)
    with pytest.raises(ValueError):
        _run(
            _book_plan(),
            _book_ledger(),
            side=LONG_SHORT,
            short_min_price=SHORT_MIN_PRICE,
            hold=hold,
        )


def test_long_short_requires_short_min_price() -> None:
    with pytest.raises(ValueError):
        _run(_book_plan(), _book_ledger(), side=LONG_SHORT)


def test_an_unknown_side_is_refused() -> None:
    with pytest.raises(ValueError):
        _run(_long_plan(), _long_ledger(), side="short_only", short_min_price=SHORT_MIN_PRICE)


def test_a_long_book_is_unchanged_under_either_side() -> None:
    """The defaults are today's planner: `side = long_only` stated or not, and a
    `long_short` window whose plan and ledger hold no short, give the same
    decisions, every one `position_side = long`."""
    today = _run(_long_plan(), _long_ledger())
    stated = _run(_long_plan(), _long_ledger(), side=LONG_ONLY)
    signed = _run(_long_plan(), _long_ledger(), side=LONG_SHORT, short_min_price=SHORT_MIN_PRICE)
    assert stated == today
    assert signed.decisions == today.decisions
    assert [d.position_side for d in today.decisions] == [LONG, LONG, LONG]
    assert [(d.security_id, d.side, d.reason) for d in today.decisions] == [
        ("A", "buy", None),
        ("B", "buy", None),
        ("F", "sell", "left_targets"),
    ]
