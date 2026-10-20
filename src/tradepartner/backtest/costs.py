"""Trade cost model (backtest spec reqs 4 and 6, ADR 0004).

Pure functions. The cost of one trade is

    notional * per_side_bps / 10_000 + shares * commission_per_share + commission_per_order

charged from cash at the fill, on buys and sells alike. `per_side_bps` is a parameter
rather than read from `Settings` because every run is evaluated at the base level and at
each sensitivity level from the same reads, with commissions unchanged (req 6).

Amendment 2026-10-18 (#1731) "Cost terms" adds a spread term and an ADV-based impact
term:

    cost = notional * (per_side_bps + spread_bps) / 10^4
           + notional * impact_coeff * sqrt(notional / ADV)
           + shares * commission_per_share + commission_per_order

    `spread_bps` and `impact_coeff` are keyword-only with identity defaults so every existing
    call stays valid. ADV comes from `adv_by_security(raw_frame, through, sessions)`: per id,
    the median of raw close x raw volume over traded bars in the last `sessions` XNYS
    sessions at or before `through` (no look-ahead: a bar at or after the fill never enters).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

import polars as pl

from tradepartner.calendar import all_sessions
from tradepartner.config import CostsConfig, Settings

BPS_PER_UNIT = 10_000
# Backtest spec amendment #1872 item 6: borrow uses calendar days and a 360-day year.
BORROW_DAY_COUNT = 360


def borrow_fee(short_values: Mapping[str, float], bps_per_year: float, calendar_days: int) -> float:
    """Fee on short values over calendar days, using a 360-day year."""
    if not math.isfinite(bps_per_year) or bps_per_year < 0:
        raise ValueError("bps_per_year must be non-negative and finite")
    if calendar_days < 0:
        raise ValueError("calendar_days must be non-negative")
    if any(not math.isfinite(value) for value in short_values.values()):
        raise ValueError("short values must be finite")
    if any(value > 0 for value in short_values.values()):
        raise ValueError("short values must be non-positive")
    return (
        math.fsum(abs(value) for value in short_values.values())
        * bps_per_year
        / BPS_PER_UNIT
        * calendar_days
        / BORROW_DAY_COUNT
    )


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
    notional: float,
    shares: float,
    per_side_bps: float,
    commissions: Commissions,
    *,
    spread_bps: float = 0.0,
    impact_coeff: float = 0.0,
    adv: float | None = None,
) -> float:
    """Cost of one trade of `notional` dollars and `shares` shares (both magnitudes).

    `shares` is the share count at the **raw** fill price (notional / raw price), as
    reported shares are defined in spec req 2, not a count derived from an adjusted-as-of-t
    price: after a reverse split the adjusted count is larger or smaller than the shares
    actually traded, and the per-share commission would be wrong.

    A trade of zero notional and zero shares is not an order and costs nothing, so the
    per-order commission is not charged for it.

    The cost is

        notional * (per_side_bps + spread_bps) / 10_000
        + notional * impact_coeff * sqrt(notional / ADV)
        + shares * commission_per_share + commission_per_order

    A non-zero `impact_coeff` requires a positive `adv`; a zero ADV is never a silent
    zero impact.
    """
    if notional < 0 or shares < 0:
        raise ValueError(
            f"notional and shares are traded magnitudes and must be non-negative, "
            f"got notional={notional}, shares={shares}"
        )
    if notional == 0 and shares == 0:
        return 0.0
    impact = 0.0
    if impact_coeff != 0.0:
        if adv is None or adv <= 0:
            raise ValueError(
                f"impact_coeff is non-zero ({impact_coeff}) but ADV is missing or "
                f"non-positive ({adv}); a non-zero impact needs a positive ADV"
            )
        impact = notional * impact_coeff * math.sqrt(notional / adv)
    return (
        notional * (per_side_bps + spread_bps) / BPS_PER_UNIT
        + impact
        + shares * commissions.per_share
        + commissions.per_order
    )


def buy_notional_after_costs(
    cash: float,
    per_side_bps: float,
    commissions: Commissions,
    *,
    price: float,
    spread_bps: float = 0.0,
    impact_coeff: float = 0.0,
    adv: float | None = None,
) -> float:
    """Largest buy notional whose notional plus cost does not exceed `cash`.

    With no commissions this is `cash / (1 + per-side rate)` (req 4). A per-share
    commission depends on the share count, hence `price`; a per-order commission comes
    off the top. Returns 0 when `cash` does not cover the per-order commission. The
    result is stepped down, by at least one ulp of `cash`, while rounding would leave
    cash negative, whichever order the caller subtracts notional and cost in.

    The spread term adds to `per_side_bps`; the impact term is notional-dependent
    (`sqrt(notional / ADV)`), so the size is found by stepping down until notional plus
    cost fits. A non-zero `impact_coeff` requires a positive `adv`.
    """
    if cash < 0:
        raise ValueError(f"cash must be non-negative, got {cash}")
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")
    if impact_coeff != 0.0 and (adv is None or adv <= 0):
        raise ValueError(
            f"impact_coeff is non-zero ({impact_coeff}) but ADV is missing or "
            f"non-positive ({adv}); a non-zero impact needs a positive ADV"
        )
    available = cash - commissions.per_order
    if available <= 0:
        return 0.0
    if impact_coeff == 0.0:
        # Linear cost (spread just adds to per_side_bps): closed-form estimate plus
        # ulp stepping, unchanged to the last bit from the pre-T210 formula.
        notional = available / (
            1 + (per_side_bps + spread_bps) / BPS_PER_UNIT + commissions.per_share / price
        )
        while notional > 0:
            overshoot = _overshoot(
                cash,
                notional,
                price,
                per_side_bps,
                commissions,
                spread_bps=spread_bps,
                impact_coeff=0.0,
                adv=None,
            )
            if overshoot <= 0:
                break
            notional = max(notional - max(overshoot, math.ulp(cash)), 0.0)
        return notional
    # Non-linear cost (impact depends on sqrt(notional)): the initial linear estimate is
    # an upper bound, so bisect the monotonic notional + cost(notional) against `cash`,
    # then step down one ulp at a time to guarantee no overshoot in either debit order.
    hi = available / (
        1 + (per_side_bps + spread_bps) / BPS_PER_UNIT + commissions.per_share / price
    )
    lo = 0.0
    while lo < hi:
        mid = (lo + hi) / 2
        if mid in (lo, hi):
            break
        cost = trade_cost(
            mid,
            mid / price,
            per_side_bps,
            commissions,
            spread_bps=spread_bps,
            impact_coeff=impact_coeff,
            adv=adv,
        )
        if mid + cost <= cash:
            lo = mid
        else:
            hi = mid
    result = lo
    while result > 0:
        overshoot = _overshoot(
            cash,
            result,
            price,
            per_side_bps,
            commissions,
            spread_bps=spread_bps,
            impact_coeff=impact_coeff,
            adv=adv,
        )
        if overshoot <= 0:
            break
        result = max(result - math.ulp(cash), 0.0)
    return result


def _overshoot(
    cash: float,
    notional: float,
    price: float,
    per_side_bps: float,
    commissions: Commissions,
    *,
    spread_bps: float = 0.0,
    impact_coeff: float = 0.0,
    adv: float | None = None,
) -> float:
    """How far paying `notional` plus its cost overdraws `cash`, in the worst of the
    orders a caller might debit them in; zero or negative means it fits."""
    cost = trade_cost(
        notional,
        notional / price,
        per_side_bps,
        commissions,
        spread_bps=spread_bps,
        impact_coeff=impact_coeff,
        adv=adv,
    )
    return max(notional + cost - cash, -(cash - notional - cost), -(cash - cost - notional))


def sensitivity_levels(settings: Settings) -> list[float]:
    """Base `costs.per_side_bps` plus every `costs.sensitivity_per_side_bps`, sorted, unique.

    The base is always `settings.costs.per_side_bps`, never a position in this list (with
    the defaults, the first level is 0 bp and the base is 15 bp). N, V, DSR and the red flag
    use the base level only (req 6).
    """
    costs = settings.costs
    return sorted({costs.per_side_bps, *costs.sensitivity_per_side_bps})


def adv_by_security(raw_frame: pl.DataFrame, through: date, sessions: int) -> dict[str, float]:
    """Per id, the median of raw close x raw volume over traded bars in the last
    `sessions` XNYS sessions at or before `through`; the bars it has when fewer; no
    entry for a name with no traded bar in the window. No look-ahead: a bar at or after
    `through` never enters."""
    if sessions <= 0:
        return {}
    cal = [s for s in all_sessions() if s <= through]
    window = set(cal[-sessions:])
    frame = raw_frame.filter(pl.col("session").is_in(window) & (pl.col("volume") > 0))
    if frame.is_empty():
        return {}
    frame = frame.with_columns((pl.col("close") * pl.col("volume")).alias("_dollar_volume"))
    result: dict[str, float] = {}
    for sid, dv in frame.group_by("security_id").agg(pl.col("_dollar_volume").median()).iter_rows():
        result[sid] = float(dv)
    return result
