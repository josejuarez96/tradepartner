"""Fills at one fill session (backtest spec req 4).

Trades are target minus drifted weights, fractional, at the fill price (`open` or
`close` of the fill session in the marking frame). Sells go first, so their proceeds fund
the buys; each buy is sized after its costs (`costs.buy_notional_after_costs`), so cash
never goes negative. A name with no bar on the fill session is not traded: a target name
is not bought and a held name to be sold keeps its last mark; both are `missing`.

Positions are dollar values at the fill price. The raw fill price (unadjusted, from
`DataProvider.raw_prices`) is used only for the share count behind the per-share
commission and the reported `shares`, never for a ratio.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import polars as pl

from tradepartner.backtest.costs import Commissions, buy_notional_after_costs, trade_cost
from tradepartner.backtest.valuation import FillPrice


@dataclass(frozen=True)
class Trade:
    """One executed trade: `notional` is positive for a buy and negative for a sell."""

    security_id: str
    notional: float
    cost: float
    fill_price: float
    raw_fill_price: float


@dataclass(frozen=True)
class FillResult:
    """Positions and cash after the fill, what it cost, and what could not be traded.

    `turnover` is one-sided: the traded notional over twice the equity before the fill.
    """

    positions: dict[str, float]
    cash: float
    cost_paid: float
    turnover: float
    trades: tuple[Trade, ...]
    missing: tuple[str, ...]


def _prices_on(frame: pl.DataFrame, session: date, field: str) -> dict[str, float]:
    rows = frame.filter(pl.col("session") == session).select("security_id", field)
    if rows.select(pl.col("security_id").is_duplicated().any()).item():
        raise ValueError(f"duplicate bar on {session.isoformat()}")
    prices: dict[str, float] = {}
    for sid, value in rows.iter_rows():
        if value is None or not (math.isfinite(value) and value > 0):
            raise ValueError(
                f"{field} for {sid} on {session.isoformat()} must be positive and finite, "
                f"got {value}"
            )
        prices[sid] = value
    return prices


def apply_trades(
    positions: Mapping[str, float],
    cash: float,
    targets: Mapping[str, float],
    marking_frame: pl.DataFrame,
    raw_frame: pl.DataFrame,
    fill_session: date,
    *,
    fill_price: FillPrice,
    per_side_bps: float,
    commissions: Commissions,
) -> FillResult:
    """Trade `positions` (dollar values at the fill price) and `cash` towards `targets`.

    Drifted weights are value over equity (cash plus every position). A name leaving the
    targets is sold whole; a partial sell or a buy is the weight difference times equity.
    Sells first, then buys in `security_id` order, each buy capped by the cash left and
    sized so notional plus cost fits in it; when cash runs short (after a missed sell, or
    costs), the names last in that order are the ones underfilled. A trim or a buy whose cost
    is at least its notional is skipped (a name leaving the targets is still sold whole), and
    cash is checked once all sells are done. Names whose trade is exactly zero are not
    orders. Raises `ValueError` for negative cash or positions, negative targets, or a
    traded name with a bar in the marking frame but none in `raw_frame`.
    """
    if not (math.isfinite(cash) and cash >= 0):
        raise ValueError(f"cash must be non-negative and finite, got {cash}")
    for sid, value in (*positions.items(), *targets.items()):
        if not (math.isfinite(value) and value >= 0):
            raise ValueError(f"{sid} must be non-negative and finite, got {value}")
    equity = math.fsum([cash, *positions.values()])
    prices = _prices_on(marking_frame, fill_session, fill_price)
    raw_prices = _prices_on(raw_frame, fill_session, fill_price)
    held = dict(positions)
    trades: list[Trade] = []
    missing: list[str] = []

    def price_of(sid: str) -> tuple[float, float] | None:
        if sid not in prices:
            return None
        if sid not in raw_prices:
            raise ValueError(f"{sid} has a bar on {fill_session.isoformat()} but no raw bar")
        return prices[sid], raw_prices[sid]

    def trade(sid: str, notional: float, price: tuple[float, float]) -> float:
        cost = trade_cost(abs(notional), abs(notional) / price[1], per_side_bps, commissions)
        trades.append(Trade(sid, notional, cost, price[0], price[1]))
        return cost

    names = sorted({*held, *targets})
    deltas = {
        sid: targets.get(sid, 0.0) * equity - held.get(sid, 0.0) if equity > 0 else 0.0
        for sid in names
    }
    for sid in names:
        if sid in targets or held.get(sid, 0.0) == 0:
            continue
        deltas[sid] = -held[sid]  # a name leaving the targets is sold whole

    for sid in [s for s in names if deltas[s] < 0]:
        price = price_of(sid)
        if price is None:
            missing.append(sid)
            continue
        notional = min(-deltas[sid], held[sid])
        cost = trade_cost(notional, notional / price[1], per_side_bps, commissions)
        if sid in targets and cost >= notional:
            continue  # a trim that costs at least what it raises is not an order
        trades.append(Trade(sid, -notional, cost, price[0], price[1]))
        cash = cash + notional - cost
        held[sid] -= notional
        if held[sid] == 0 or sid not in targets:
            del held[sid]
    if cash < 0:
        raise ValueError(f"the sells on {fill_session.isoformat()} cost more than they raise")

    for sid in [s for s in names if deltas[s] > 0]:
        price = price_of(sid)
        if price is None:
            missing.append(sid)
            continue
        notional = buy_notional_after_costs(
            min(deltas[sid], cash), per_side_bps, commissions, price=price[1]
        )
        if (
            notional <= 0
            or trade_cost(notional, notional / price[1], per_side_bps, commissions) >= notional
        ):
            continue  # a buy that costs at least its notional is not an order
        cost = trade(sid, notional, price)
        cash = cash - notional - cost
        held[sid] = held.get(sid, 0.0) + notional

    traded = math.fsum(abs(t.notional) for t in trades)
    return FillResult(
        positions=held,
        cash=cash,
        cost_paid=math.fsum(t.cost for t in trades),
        turnover=traded / (2 * equity) if equity > 0 else 0.0,
        trades=tuple(trades),
        missing=tuple(sorted(missing)),
    )
