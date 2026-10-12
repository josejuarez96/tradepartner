"""The risk-free return per rebalance period (backtest spec, amendment 2026-10-11 #1498;
ADR 0020). Pure functions, no I/O: the engine reads the `rates` frame, and the caller
resolves the as-of rate of each session before it gets here.

- `to_yield` turns the published 3-month bill rate into the 91-day bond-equivalent
  yield: under the `discount` basis `y = 365 d / (360 - 91 d)`; under `yield` it is `d`.
- `session_accruals` gives each session's accrual `y(s) * n_s / 365`, with `n_s` the
  calendar days from the previous session, so a weekend or a holiday accrues to the next
  session as a bill does.
- `period_risk_free` compounds the accruals over the sessions of each period
  `(T_i, T_{i+1}]`: `rf_i = prod(1 + accrual) - 1`.
- `constant_period_returns` is the `constant` source: `(1 + r)^(1/ppy) - 1` for every
  period, exactly what the metrics computed before the series existed.

A session with no rate known at its close raises `RiskFreeMissing`: never zero, never the
constant, never carried forward from before the series starts.

The day-count constants 365, 360 and 91 are named once here (and allowed by the backtest
AST-literal test).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from itertools import pairwise
from typing import Final, Literal

__all__ = [
    "BILL_TERM_DAYS",
    "DAYS_PER_YEAR",
    "DISCOUNT_YEAR_DAYS",
    "Basis",
    "RiskFreeMissing",
    "annualised",
    "constant_period_returns",
    "period_risk_free",
    "session_accruals",
    "to_yield",
]

#: The bond-equivalent yield's year, in days.
DAYS_PER_YEAR: Final[int] = 365
#: The discount quote's year, in days (the bank-discount convention).
DISCOUNT_YEAR_DAYS: Final[int] = 360
#: The bill's term, in days (DTB3 is the 3-month bill: 91 days).
BILL_TERM_DAYS: Final[int] = 91

Basis = Literal["discount", "yield"]


class RiskFreeMissing(ValueError):
    """A session has no risk-free rate known at its close (backtest spec, `risk_free_missing`).

    `series` is the rate series and `session` the first session without a rate."""

    def __init__(self, series: str, session: date) -> None:
        self.series = series
        self.session = session
        super().__init__(f"risk_free_missing: no {series} rate known at the close of {session}")


def to_yield(rate: float, basis: Basis = "discount") -> float:
    """The annual bond-equivalent yield of the published `rate` (a fraction: 5% is 0.05).

    `discount`: `365 d / (360 - 91 d)` (d = 0.05 gives 0.0513434...); `yield`: `rate`."""
    if not math.isfinite(rate):
        raise ValueError(f"rate must be finite, got {rate}")
    if basis == "yield":
        return rate
    if basis != "discount":
        raise ValueError(f"basis must be 'discount' or 'yield', got {basis!r}")
    return DAYS_PER_YEAR * rate / (DISCOUNT_YEAR_DAYS - BILL_TERM_DAYS * rate)


def session_accruals(
    sessions: Sequence[date],
    yields: Mapping[date, float],
    *,
    series: str = "DTB3",
) -> dict[date, float]:
    """The accrual `y(s) * n_s / 365` of every session after the first.

    `sessions` is ascending and starts at the anchor session (T_0, which accrues
    nothing); `yields` maps each later session to its annual yield (`to_yield`, the
    as-of rate at that session's close). `n_s` is the calendar days since the previous
    session in `sessions`. Raises `RiskFreeMissing` for the first session absent from
    `yields`."""
    accruals: dict[date, float] = {}
    for previous, session in pairwise(sessions):
        if session <= previous:
            raise ValueError(f"sessions must be strictly ascending: {previous} then {session}")
        if session not in yields:
            raise RiskFreeMissing(series, session)
        accruals[session] = yields[session] * (session - previous).days / DAYS_PER_YEAR
    return accruals


def period_risk_free(
    sessions: Sequence[date],
    period_ends: Sequence[date],
    rates: Mapping[date, float],
    *,
    basis: Basis = "discount",
    series: str = "DTB3",
) -> list[float]:
    """The risk-free return of each period `(T_i, T_{i+1}]` over the run's sessions.

    `sessions` is every session from T_0 to T_n ascending; `period_ends` is T_0..T_n, a
    subsequence of `sessions` starting and ending at its ends; `rates` maps each session
    after T_0 to the published rate (a fraction) known at its close. Returns n values,
    each the product over the period's sessions of `1 + accrual`, minus 1. Raises
    `RiskFreeMissing` when a session after T_0 has no rate."""
    if len(period_ends) <= 1:
        raise ValueError("need at least two period ends (T_0 and T_1)")
    if not sessions or sessions[0] != period_ends[0] or sessions[-1] != period_ends[-1]:
        raise ValueError("sessions must run from the first to the last period end")
    if not set(period_ends) <= set(sessions):
        raise ValueError("period_ends must be sessions of the run")
    accruals = session_accruals(
        sessions, {s: to_yield(r, basis) for s, r in rates.items()}, series=series
    )
    out: list[float] = []
    for start, end in pairwise(period_ends):
        growth = 1.0
        for session in sessions:
            if start < session <= end:
                growth *= 1 + accruals[session]
        out.append(growth - 1)
    return out


def constant_period_returns(annual_rate: float, periods_per_year: int, n: int) -> list[float]:
    """`n` equal period returns of the constant annual rate, `(1 + r)^(1/ppy) - 1` each."""
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    if n < 0:
        raise ValueError(f"n must be non-negative, got {n}")
    return [(1 + annual_rate) ** (1 / periods_per_year) - 1] * n


def annualised(period_returns: Sequence[float], periods_per_year: int) -> float:
    """`risk_free_annual`: `(prod(1 + rf_i))^(ppy / n) - 1` over the run's periods."""
    if not period_returns:
        raise ValueError("need at least one period return")
    return math.prod(1 + r for r in period_returns) ** (periods_per_year / len(period_returns)) - 1
