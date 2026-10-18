"""Tests for `tradepartner.backtest.costs` (spec reqs 4 and 6, ADR 0004, plan T33).

The three-trade fixture is checked against values computed by hand, one cost
component at a time and then all together, as ADR 0004 requires: `bt` models costs
differently, so the oracle runs at zero cost and the cost model is tested here.
"""

from __future__ import annotations

import ast
import math
import time
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from tradepartner.backtest.costs import (
    Commissions,
    buy_notional_after_costs,
    sensitivity_levels,
    trade_cost,
)
from tradepartner.calendar import all_sessions
from tradepartner.config import Settings

COSTS_PY = Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "backtest" / "costs.py"

# (notional, shares): a whole-share buy, a whole-share sell, a fractional buy.
THREE_TRADES = [(10_000.0, 100.0), (2_500.0, 50.0), (333.33, 3.3333)]

# Hand-computed per trade: notional * bps / 10_000, shares * per_share, per_order.
BPS_ONLY = [15.0, 3.75, 0.499995]  # at 15 bp
PER_SHARE_ONLY = [0.5, 0.25, 0.0166665]  # at $0.005 per share
PER_ORDER_ONLY = [1.0, 1.0, 1.0]  # at $1 per order
ALL_THREE = [16.5, 5.0, 1.5166615]


def _settings(**costs: object) -> Settings:
    return Settings(_env_file=None, costs=costs)


@pytest.mark.parametrize(
    ("per_side_bps", "commissions", "expected"),
    [
        (15.0, Commissions(per_share=0.0, per_order=0.0), BPS_ONLY),
        (0.0, Commissions(per_share=0.005, per_order=0.0), PER_SHARE_ONLY),
        (0.0, Commissions(per_share=0.0, per_order=1.0), PER_ORDER_ONLY),
        (15.0, Commissions(per_share=0.005, per_order=1.0), ALL_THREE),
    ],
    ids=["bps", "per_share", "per_order", "all"],
)
def test_three_trade_fixture_matches_hand_computed_costs(
    per_side_bps: float, commissions: Commissions, expected: list[float]
) -> None:
    got = [trade_cost(n, s, per_side_bps, commissions) for n, s in THREE_TRADES]
    assert got == pytest.approx(expected, rel=1e-12)
    assert sum(got) == pytest.approx(sum(expected), rel=1e-12)


def test_zero_cost_is_the_identity() -> None:
    zero = Commissions(per_share=0.0, per_order=0.0)
    assert [trade_cost(n, s, 0.0, zero) for n, s in THREE_TRADES] == [0.0, 0.0, 0.0]
    assert buy_notional_after_costs(12_345.67, 0.0, zero, price=41.0) == 12_345.67


def test_no_trade_costs_nothing_even_with_a_per_order_commission() -> None:
    """A zero trade is not an order, so no per-order commission is charged."""
    assert trade_cost(0.0, 0.0, 15.0, Commissions(per_share=0.005, per_order=1.0)) == 0.0


@pytest.mark.parametrize(("notional", "shares"), [(-1.0, 1.0), (1.0, -1.0)])
def test_trade_cost_rejects_negative_sizes(notional: float, shares: float) -> None:
    """Callers pass traded magnitudes; a sign here would turn a cost into a credit."""
    with pytest.raises(ValueError, match="non-negative"):
        trade_cost(notional, shares, 15.0, Commissions(per_share=0.0, per_order=0.0))


def test_buy_notional_scaled_by_one_over_one_plus_rate() -> None:
    """Spec req 4: with no commissions, notional = cash / (1 + per-side rate)."""
    zero = Commissions(per_share=0.0, per_order=0.0)
    got = buy_notional_after_costs(10_015.0, 15.0, zero, price=50.0)
    assert got == pytest.approx(10_000.0, rel=1e-12)


def test_buy_notional_with_commissions_hand_computed() -> None:
    """N + N*0.0015 + (N/50)*0.005 + 1 = 10_000  =>  N = 9_999 / 1.0016."""
    c = Commissions(per_share=0.005, per_order=1.0)
    got = buy_notional_after_costs(10_000.0, 15.0, c, price=50.0)
    assert got == pytest.approx(9_999.0 / 1.0016, rel=1e-12)


@pytest.mark.parametrize("cash", [0.0, 0.5, 1.0])
def test_buy_notional_zero_when_cash_does_not_cover_the_order_fee(cash: float) -> None:
    c = Commissions(per_share=0.0, per_order=1.0)
    assert buy_notional_after_costs(cash, 15.0, c, price=50.0) == 0.0


@pytest.mark.parametrize("cash", [1e-6, 0.1, 1.0, 999.99, 12_345.678901, 1e5, 3.3e7])
@pytest.mark.parametrize("price", [0.37, 5.0, 123.456, 4_321.0])
@pytest.mark.parametrize(
    "commissions",
    [Commissions(per_share=0.0, per_order=0.0), Commissions(per_share=0.005, per_order=0.35)],
)
def test_post_cost_cash_never_negative_at_the_ladder_maximum(
    cash: float, price: float, commissions: Commissions
) -> None:
    top = max(sensitivity_levels(Settings(_env_file=None)))
    notional = buy_notional_after_costs(cash, top, commissions, price=price)
    assert notional >= 0.0
    remaining = cash - notional - trade_cost(notional, notional / price, top, commissions)
    assert remaining >= 0.0 or notional == 0.0


def test_buy_notional_rejects_bad_inputs() -> None:
    zero = Commissions(per_share=0.0, per_order=0.0)
    with pytest.raises(ValueError):
        buy_notional_after_costs(-1.0, 15.0, zero, price=10.0)
    with pytest.raises(ValueError):
        buy_notional_after_costs(100.0, 15.0, zero, price=0.0)


def test_levels_sorted_and_include_the_base() -> None:
    assert sensitivity_levels(Settings(_env_file=None)) == [0.0, 15.0, 30.0, 60.0, 100.0]


def test_levels_deduplicate_a_base_already_on_the_ladder() -> None:
    s = _settings(per_side_bps=30.0, sensitivity_per_side_bps=[100.0, 0.0, 30.0])
    assert sensitivity_levels(s) == [0.0, 30.0, 100.0]


def test_commissions_from_config() -> None:
    s = _settings(commission_per_share=0.004, commission_per_order=0.25)
    assert Commissions.from_config(s.costs) == Commissions(per_share=0.004, per_order=0.25)


# Spec acceptance (Costs and metrics): no numeric literals in costs.py other than these.
ALLOWED_LITERALS = {0, 1, -1, 2, 4, 12, 10_000}


def test_costs_module_has_no_stray_numeric_literals() -> None:
    tree = ast.parse(COSTS_PY.read_text(encoding="utf-8"))
    stray = [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
        and node.value not in ALLOWED_LITERALS
    ]
    assert stray == []


@pytest.mark.parametrize("excess", [1e-15, 1e-12, 2.7e-15, 1e-9, 1e-6])
@pytest.mark.parametrize("per_order", [1.0, 0.35, 7.0])
def test_cash_just_above_the_order_fee_terminates_and_never_overspends(
    excess: float, per_order: float
) -> None:
    """Cash a sliver above `per_order` (quant-auditor, PR #124): the rounding guard must
    step on the scale of `cash`, not of the tiny notional, or it loops ~1e12 times."""
    c = Commissions(per_share=0.005, per_order=per_order)
    cash = per_order + excess
    started = time.monotonic()
    notional = buy_notional_after_costs(cash, 100.0, c, price=50.0)
    assert time.monotonic() - started < 1.0
    assert notional >= 0.0
    if notional > 0:
        cost = trade_cost(notional, notional / 50.0, 100.0, c)
        assert cash - notional - cost >= 0.0
        assert cash - cost - notional >= 0.0


def test_levels_always_contain_the_base_wherever_it_sorts() -> None:
    """The base is `settings.costs.per_side_bps`, never a position in the list: with the
    defaults, `levels[0]` is 0 bp, not the 15 bp base."""
    s = Settings(_env_file=None)
    levels = sensitivity_levels(s)
    assert s.costs.per_side_bps in levels
    assert levels[0] != s.costs.per_side_bps


# --- T210: the cost terms `spread_bps` and `impact_coeff`, ADV ------------------------
#
# Backtest spec, amendment 2026-10-18 (#1731) "Cost terms", with amendment #1792:
#
#   cost = notional * (per_side_bps + spread_bps) / 10^4
#          + notional * impact_coeff * sqrt(notional / ADV)
#          + shares * commission_per_share + commission_per_order
#
# `trade_cost` and `buy_notional_after_costs` take `spread_bps=0.0`, `impact_coeff=0.0`
# and `adv: float | None = None` keyword-only, so every call in `engine.py` is unchanged.
# ADV is `adv_by_security(raw_frame, through, sessions)`: per id, the median of raw
# `close` * raw `volume` over its traded bars in the last `sessions` XNYS sessions at or
# before `through`, the bars it has when fewer; no traded bar there, no ADV.

# Hand-computed spread at 5 bp: notional * 5 / 10_000.
SPREAD_5_ONLY = [5.0, 1.25, 0.1666650]
# ADV per trade, chosen so notional / ADV is 1e-2, 1e-2, 1e-4 (square roots 0.1, 0.1,
# 0.01). At impact_coeff 0.1: 10_000 * 0.1 * 0.1 = 100; 2_500 * 0.1 * 0.1 = 25;
# 333.33 * 0.1 * 0.01 = 0.33333.
THREE_ADVS = [1_000_000.0, 250_000.0, 3_333_300.0]
IMPACT_01_ONLY = [100.0, 25.0, 0.33333]
# Everything at once: 15 bp + 5 bp spread, impact 0.1, $0.005 per share, $1 per order.
# Trade 1: 10_000 * 20 / 10_000 = 20 + 100 + 0.5 + 1 = 121.5
# Trade 2: 2_500 * 20 / 10_000 = 5 + 25 + 0.25 + 1 = 31.25
# Trade 3: 333.33 * 20 / 10_000 = 0.66666 + 0.33333 + 0.0166665 + 1 = 2.0166565
ALL_TERMS = [121.5, 31.25, 2.0166565]
NO_COMMISSIONS = Commissions(per_share=0.0, per_order=0.0)


def test_spread_is_a_per_side_bps_term_on_the_three_trades() -> None:
    got = [trade_cost(n, s, 0.0, NO_COMMISSIONS, spread_bps=5.0) for n, s in THREE_TRADES]
    assert got == pytest.approx(SPREAD_5_ONLY, rel=1e-12)
    # It adds to per_side_bps: 15 bp + 5 bp spread is the 20 bp cost.
    for n, s in THREE_TRADES:
        assert trade_cost(n, s, 15.0, NO_COMMISSIONS, spread_bps=5.0) == pytest.approx(
            trade_cost(n, s, 20.0, NO_COMMISSIONS), rel=1e-12
        )


def test_impact_is_coeff_times_root_participation_on_the_three_trades() -> None:
    got = [
        trade_cost(n, s, 0.0, NO_COMMISSIONS, impact_coeff=0.1, adv=adv)
        for (n, s), adv in zip(THREE_TRADES, THREE_ADVS, strict=True)
    ]
    assert got == pytest.approx(IMPACT_01_ONLY, rel=1e-12)
    # Square-root, not linear: four times the notional at the same ADV is 8x the impact.
    one = trade_cost(1_000.0, 10.0, 0.0, NO_COMMISSIONS, impact_coeff=0.1, adv=1e6)
    four = trade_cost(4_000.0, 40.0, 0.0, NO_COMMISSIONS, impact_coeff=0.1, adv=1e6)
    assert four == pytest.approx(8 * one, rel=1e-12)
    assert one == pytest.approx(1_000.0 * 0.1 * math.sqrt(1e-3), rel=1e-12)


def test_every_term_together_on_the_three_trades() -> None:
    commissions = Commissions(per_share=0.005, per_order=1.0)
    got = [
        trade_cost(n, s, 15.0, commissions, spread_bps=5.0, impact_coeff=0.1, adv=adv)
        for (n, s), adv in zip(THREE_TRADES, THREE_ADVS, strict=True)
    ]
    assert got == pytest.approx(ALL_TERMS, rel=1e-12)
    assert sum(got) == pytest.approx(154.7666565, rel=1e-12)


#: Notionals, share counts, levels and commissions for the bitwise identity check.
_IDENTITY_CASES = [
    (n, sh, bps, c)
    for n, sh in [*THREE_TRADES, (1e-9, 1e-11), (12_345.678901, 98.7654321), (3.3e7, 1.1e5)]
    for bps in (0.0, 15.0, 37.5, 100.0)
    for c in (NO_COMMISSIONS, Commissions(per_share=0.005, per_order=0.35))
]


def test_zero_terms_are_the_old_formula_to_the_last_bit() -> None:
    """`==`, not approx: at spread 0 and impact 0 every cost is today's expression
    `notional * per_side_bps / 10_000 + shares * per_share + per_order`, bit for bit,
    whatever ADV is passed (or none)."""
    for n, sh, bps, c in _IDENTITY_CASES:
        old = n * bps / 10_000 + sh * c.per_share + c.per_order
        assert trade_cost(n, sh, bps, c) == old
        assert trade_cost(n, sh, bps, c, spread_bps=0.0, impact_coeff=0.0) == old
        assert trade_cost(n, sh, bps, c, spread_bps=0.0, impact_coeff=0.0, adv=5e5) == old
    assert (
        trade_cost(0.0, 0.0, 15.0, NO_COMMISSIONS, spread_bps=5.0, impact_coeff=0.1, adv=1e6) == 0.0
    )


@pytest.mark.parametrize("cash", [0.0, 0.5, 1e-6, 999.99, 12_345.678901, 3.3e7])
@pytest.mark.parametrize("price", [0.37, 50.0, 4_321.0])
@pytest.mark.parametrize("bps", [0.0, 15.0, 100.0])
@pytest.mark.parametrize(
    "commissions",
    [NO_COMMISSIONS, Commissions(per_share=0.005, per_order=1.0)],
)
def test_zero_terms_size_a_buy_as_today_to_the_last_bit(
    cash: float, price: float, bps: float, commissions: Commissions
) -> None:
    old = buy_notional_after_costs(cash, bps, commissions, price=price)
    assert (
        buy_notional_after_costs(
            cash, bps, commissions, price=price, spread_bps=0.0, impact_coeff=0.0
        )
        == old
    )
    assert (
        buy_notional_after_costs(
            cash, bps, commissions, price=price, spread_bps=0.0, impact_coeff=0.0, adv=5e5
        )
        == old
    )


@pytest.mark.parametrize(("n", "sh"), THREE_TRADES)
def test_a_higher_impact_coeff_never_lowers_a_trade_cost(n: float, sh: float) -> None:
    costs = [
        trade_cost(n, sh, 15.0, NO_COMMISSIONS, impact_coeff=k, adv=2e5)
        for k in (0.0, 0.01, 0.1, 0.5, 2.0)
    ]
    assert costs == sorted(costs)
    assert len(set(costs)) == len(costs)


@pytest.mark.parametrize("adv", [None, 0.0])
def test_a_non_zero_impact_without_an_adv_raises(adv: float | None) -> None:
    """No ADV (or a zero one) is never a silent zero impact (spec amendment #1792,
    item 3); at impact 0 no ADV is needed."""
    with pytest.raises(ValueError, match=r"(?i)adv"):
        trade_cost(1_000.0, 10.0, 15.0, NO_COMMISSIONS, impact_coeff=0.1, adv=adv)
    with pytest.raises(ValueError, match=r"(?i)adv"):
        buy_notional_after_costs(
            1_000.0, 15.0, NO_COMMISSIONS, price=50.0, impact_coeff=0.1, adv=adv
        )
    assert trade_cost(1_000.0, 10.0, 15.0, NO_COMMISSIONS, spread_bps=5.0) == pytest.approx(2.0)


def test_buy_notional_after_the_spread_by_hand() -> None:
    """N (1 + 20 / 10_000) = 10_020 at 15 bp + 5 bp spread: N = 10_000."""
    got = buy_notional_after_costs(10_020.0, 15.0, NO_COMMISSIONS, price=50.0, spread_bps=5.0)
    assert got == pytest.approx(10_000.0, rel=1e-12)


def test_buy_notional_after_impact_by_hand() -> None:
    """N + 0.0015 N + 0.1 N sqrt(N / 1_000_000) = 10_115 at N = 10_000 (15 + 100)."""
    got = buy_notional_after_costs(
        10_115.0, 15.0, NO_COMMISSIONS, price=50.0, impact_coeff=0.1, adv=1e6
    )
    assert got == pytest.approx(10_000.0, rel=1e-9)
    cost = trade_cost(got, got / 50.0, 15.0, NO_COMMISSIONS, impact_coeff=0.1, adv=1e6)
    assert 10_115.0 - got - cost >= 0.0


@pytest.mark.parametrize("cash", [1e-6, 1.0, 999.99, 12_345.678901, 3.3e7])
@pytest.mark.parametrize("price", [0.37, 123.456])
@pytest.mark.parametrize("coeff", [0.01, 0.1, 2.0])
@pytest.mark.parametrize("adv", [1e3, 1e6, 1e9])
def test_post_cost_cash_never_negative_under_spread_and_impact(
    cash: float, price: float, coeff: float, adv: float
) -> None:
    """The buy sized after every term never overdraws cash, in either debit order."""
    c = Commissions(per_share=0.005, per_order=0.35)
    terms = {"spread_bps": 5.0, "impact_coeff": coeff, "adv": adv}
    notional = buy_notional_after_costs(cash, 100.0, c, price=price, **terms)
    assert notional >= 0.0
    if notional > 0:
        cost = trade_cost(notional, notional / price, 100.0, c, **terms)
        assert cash - notional - cost >= 0.0
        assert cash - cost - notional >= 0.0


# --- ADV from a hand raw frame ------------------------------------------------------

#: 25 consecutive XNYS sessions from 2024-01-02; S[k - 1] is the k-th.
S = [s for s in all_sessions() if s >= date(2024, 1, 2)][:25]
THROUGH = S[21]  # the 22nd session, 2024-02-01


def _raw(rows: list[tuple[str, int, float, float]]) -> pl.DataFrame:
    """Raw bars (security_id, k-th session, raw close, raw volume); `open` = close."""
    return pl.DataFrame(
        [(sid, S[k - 1], close, close, volume) for sid, k, close, volume in rows],
        schema={
            "security_id": pl.Utf8,
            "session": pl.Date,
            "open": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Float64,
        },
        orient="row",
    )


def _adv_frame() -> pl.DataFrame:
    """A: $10 and volume 100 k on its k-th session (dollar volume 1_000 k), k = 1..22,
    then a 1e12-share bar on the 23rd (after THROUGH: never read).
    B: three bars, dollar volumes 500_000, 100_000, 300_000 (k = 20, 21, 22).
    C: one bar, after THROUGH.
    D: $1e10 a session for k = 1..19, then 1_000 (k = 20), 3_000 (k = 21) and a
    zero-volume bar on k = 22, which is not a bar.
    E: two bars, k = 1 and 2, $1_000 each.
    F: a 2-for-1 split between k = 20 and 21: raw $100 x 1_000 before, $50 x 2_000
    after, so its raw dollar volume is $100_000 on every one of its ten bars (k = 13..22).
    """
    a = [("A", k, 10.0, 100.0 * k) for k in range(1, 23)] + [("A", 23, 10.0, 1e12)]
    b = [("B", 20, 50.0, 10_000.0), ("B", 21, 20.0, 5_000.0), ("B", 22, 30.0, 10_000.0)]
    c = [("C", 24, 40.0, 1_000.0)]
    d = [("D", k, 10.0, 1e9) for k in range(1, 20)]
    d += [("D", 20, 10.0, 100.0), ("D", 21, 10.0, 300.0), ("D", 22, 10.0, 0.0)]
    e = [("E", 1, 10.0, 100.0), ("E", 2, 10.0, 100.0)]
    f = [("F", k, 100.0, 1_000.0) for k in range(13, 21)]
    f += [("F", 21, 50.0, 2_000.0), ("F", 22, 50.0, 2_000.0)]
    return _raw(a + b + c + d + e + f)


def _adv(frame: pl.DataFrame, sessions: int) -> dict[str, float]:
    from tradepartner.backtest.costs import adv_by_security

    return dict(adv_by_security(frame, THROUGH, sessions))


def test_adv_is_the_median_raw_dollar_volume_over_the_last_sessions() -> None:
    """20 sessions ending at THROUGH are k = 3..22. A: 3_000..22_000, an even count, so
    (12_000 + 13_000) / 2 = 12_500. B has three bars there: 300_000. D: seventeen bars at
    1e10, 1_000 and 3_000 (k = 22 is zero-volume): 1e10. F: ten bars, all 100_000, the
    split leaving raw dollar volume unchanged. C and E have no traded bar there."""
    assert _adv(_adv_frame(), 20) == {"A": 12_500.0, "B": 300_000.0, "D": 1e10, "F": 100_000.0}


def test_adv_window_is_sessions_not_bars() -> None:
    """Three sessions are k = 20..22: D's traded bars there are k = 20 and 21, median
    (1_000 + 3_000) / 2 = 2_000; it does not reach back past the window for a third bar.
    One session is THROUGH alone: D's bar there has zero volume, so D has no ADV."""
    three = _adv(_adv_frame(), 3)
    assert three == {"A": 21_000.0, "B": 300_000.0, "D": 2_000.0, "F": 100_000.0}
    assert _adv(_adv_frame(), 1) == {"A": 22_000.0, "B": 300_000.0, "F": 100_000.0}
    # Twenty-two sessions reach k = 1: E's two bars count.
    assert _adv(_adv_frame(), 22)["E"] == 1_000.0


def test_adv_ignores_every_bar_after_through() -> None:
    """No look-ahead: dropping the bars after THROUGH, or inflating them, changes
    nothing at THROUGH."""
    full = _adv_frame()
    cut = full.filter(pl.col("session") <= THROUGH)
    inflated = full.with_columns(
        pl.when(pl.col("session") > THROUGH)
        .then(pl.col("volume") * 1e6)
        .otherwise(pl.col("volume"))
        .alias("volume")
    )
    for sessions in (1, 3, 20, 22):
        assert _adv(full, sessions) == _adv(cut, sessions) == _adv(inflated, sessions)


def test_adv_feeds_the_impact_term_by_hand() -> None:
    """A $1_250 trade in A at THROUGH: ADV 12_500, participation 0.1, at impact_coeff 0.2
    the impact is 1_250 * 0.2 * sqrt(0.1) = 79.05694150420949."""
    adv = _adv(_adv_frame(), 20)["A"]
    cost = trade_cost(1_250.0, 125.0, 0.0, NO_COMMISSIONS, impact_coeff=0.2, adv=adv)
    assert cost == pytest.approx(79.05694150420949, rel=1e-12)
