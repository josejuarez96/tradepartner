"""Trade cost model (backtest spec reqs 4 and 6, ADR 0004).

Pure functions. The cost of one trade is

    notional * per_side_bps / 10_000 + shares * commission_per_share + commission_per_order

charged from cash at the fill, on buys and sells alike. `per_side_bps` is a parameter
rather than read from `Settings` because every run is evaluated at the base level and at
each sensitivity level from the same reads, with commissions unchanged (req 6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from tradepartner.config import CostsConfig, Settings

BPS_PER_UNIT = 10_000


@dataclass(frozen=True)
class Commissions:
    """Broker commissions, held fixed across cost sensitivity levels."""

    per_share: float
    per_order: float

    @classmethod
    def from_config(cls, costs: CostsConfig) -> Commissions:
        """The commissions named in `costs.*`."""
        return cls(per_share=costs.commission_per_share, per_order=costs.commission_per_order)


def trade_cost(
    notional: float, shares: float, per_side_bps: float, commissions: Commissions
) -> float:
    """Cost of one trade of `notional` dollars and `shares` shares (both magnitudes).

    `shares` is the share count at the **raw** fill price (notional / raw price), as
    reported shares are defined in spec req 2, not a count derived from an adjusted-as-of-t
    price: after a reverse split the adjusted count is larger or smaller than the shares
    actually traded, and the per-share commission would be wrong.

    A trade of zero notional and zero shares is not an order and costs nothing, so the
    per-order commission is not charged for it.
    """
    if notional < 0 or shares < 0:
        raise ValueError(
            f"notional and shares are traded magnitudes and must be non-negative, "
            f"got notional={notional}, shares={shares}"
        )
    if notional == 0 and shares == 0:
        return 0.0
    return (
        notional * per_side_bps / BPS_PER_UNIT
        + shares * commissions.per_share
        + commissions.per_order
    )


def buy_notional_after_costs(
    cash: float, per_side_bps: float, commissions: Commissions, *, price: float
) -> float:
    """Largest buy notional whose notional plus cost does not exceed `cash`.

    With no commissions this is `cash / (1 + per-side rate)` (req 4). A per-share
    commission depends on the share count, hence `price`; a per-order commission comes
    off the top. Returns 0 when `cash` does not cover the per-order commission. The
    result is stepped down, by at least one ulp of `cash`, while rounding would leave
    cash negative, whichever order the caller subtracts notional and cost in.
    """
    if cash < 0:
        raise ValueError(f"cash must be non-negative, got {cash}")
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")
    available = cash - commissions.per_order
    if available <= 0:
        return 0.0
    notional = available / (1 + per_side_bps / BPS_PER_UNIT + commissions.per_share / price)
    # Each step removes at least one ulp of `cash` (the scale of the rounding error), so
    # the loop ends after a few steps even when `notional` itself is tiny.
    while notional > 0:
        overshoot = _overshoot(cash, notional, price, per_side_bps, commissions)
        if overshoot <= 0:
            break
        notional = max(notional - max(overshoot, math.ulp(cash)), 0.0)
    return notional


def _overshoot(
    cash: float, notional: float, price: float, per_side_bps: float, commissions: Commissions
) -> float:
    """How far paying `notional` plus its cost overdraws `cash`, in the worst of the
    orders a caller might debit them in; zero or negative means it fits."""
    cost = trade_cost(notional, notional / price, per_side_bps, commissions)
    return max(notional + cost - cash, -(cash - notional - cost), -(cash - cost - notional))


def sensitivity_levels(settings: Settings) -> list[float]:
    """Base `costs.per_side_bps` plus every `costs.sensitivity_per_side_bps`, sorted, unique.

    The base is always `settings.costs.per_side_bps`, never a position in this list (with
    the defaults, the first level is 0 bp and the base is 15 bp). N, V, DSR and the red flag
    use the base level only (req 6).
    """
    costs = settings.costs
    return sorted({costs.per_side_bps, *costs.sensitivity_per_side_bps})
