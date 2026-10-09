"""The engine's only view of market data (backtest spec req 1).

`DataProvider` is the protocol the engine is written against. Every method takes a
tz-aware `t`, and the engine only ever passes a rebalance close, close(T_{i+1}), so
nothing is read with a later time than the step being computed. The store-backed
implementation (T38, `store_provider.py`) answers each method from the as-of API with
the trial's frozen `Settings`; tests use the in-memory fake in
`tests/backtest/fake_provider.py`, which records every call.

Frames are `polars` frames with the columns of the as-of function each method stands
for, so the store provider can return them unchanged.

The two statement reads (`statement_facts`, `sics`; backtest spec amendment #720, plan
T85c) serve the `profitability` family: a statement fact is visible at `t` from its
filing's acceptance (`known_at <= t`), and the engine passes `t = session_close(T)`, so a
10-K accepted after the close of a rebalance session is first read at the next one.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, Protocol, runtime_checkable

import polars as pl

from tradepartner.timeutil import ensure_tz_aware_utc
from tradepartner.universe import Universe

#: The `statement_facts` names `DataProvider.statement_facts` returns: the `profitability`
#: family's numerator and denominator (`operating_cash_flow` joins with B3b).
STATEMENT_FACT_NAMES: Final[tuple[str, ...]] = ("gross_profit", "total_assets")


@dataclass(frozen=True)
class GapReading:
    """The survivorship gap at one rebalance (ADR 0003 rule 5): count and size share of
    the listings active in the window that are missing."""

    count_share: float
    size_share: float


def check_t(t: datetime, *, name: str = "t") -> datetime:
    """`t` normalized to UTC. Raises `TypeError` for a non-datetime (a bare date) and
    `ValueError` for a naive datetime. Providers call this before any read."""
    if not isinstance(t, datetime):
        raise TypeError(f"{name} must be a tz-aware datetime, got {type(t).__name__}: {t!r}")
    return ensure_tz_aware_utc(t, field_name=name)


@runtime_checkable
class DataProvider(Protocol):
    """Point-in-time reads for one backtest run, each as of a tz-aware `t`."""

    def universe(self, t: datetime) -> Universe:
        """`universe_as_of(t)`: members and exclusions at the session closing at `t`."""
        ...

    def adjusted_prices(
        self,
        t: datetime,
        ids: Sequence[str],
        include_dividends: bool,
        *,
        sessions_from: date | None = None,
    ) -> pl.DataFrame:
        """`adjusted_prices_as_of(t, ids, include_dividends=..., sessions_from=...)`:
        bars known at `t`, latest revision, adjusted for every action known at `t` with
        ex-date at or before it; `security_id`, `session`, `open`, `high`, `low`, `close`,
        ... per the as-of API. The marking frame (dividends included) and the signal frame
        are both this call. `sessions_from` leaves bars before that session out of the
        result after the as-of read, so the factors and the `known_at` cut are unchanged
        and only the frame is shorter (strategy-lab T99); `None` bounds nothing."""
        ...

    def raw_prices(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        """`prices_as_of(t, ids)`: unadjusted bars known at `t`, latest revision. Used only
        for the raw fill price behind reported `shares` and the per-share commission
        (req 2; costs are charged on shares actually traded), never for a ratio."""
        ...

    def listing_ends(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        """`listing_ends_as_of(t)` for `ids`: each listing with `status` (listed,
        delisted, transferred) and `end_session`, derived from rows known at `t`."""
        ...

    def benchmark_ids(self, t: datetime, through: date | None = None) -> Mapping[str, str]:
        """Benchmark series name (`SPY`, `MTUM`) to `security_id`, by symbol (#840): not
        gated on the master rows' `known_at`, and refused (`ValueError`) when a name is
        missing, ambiguous, or its ticker is another security's in `[session(t),
        through]` (`through=None`: open-ended). The bars are read point-in-time."""
        ...

    def survivorship_gap(self, t: datetime) -> GapReading:
        """`survivorship_gap(t)`: count and size share at the rebalance closing at `t`."""
        ...

    def dropped_dividends(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        """`dropped_dividends_as_of(t, ids)`: dividends known at `t` that the adjusted
        frame leaves unapplied, one row per `(security_id, ex_date)`."""
        ...

    def late_dividends(self, t_prev: datetime, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        """Dividends on `ids` first known in `(t_prev, t]` whose ex-date is at or before
        `t_prev`'s session, however late (req 5): `security_id`, `ex_date`,
        `ratio_or_amount`, `known_at`. Whether the name was held on the ex-date is the
        engine's to decide; it passes the names it may have held."""
        ...

    def static_listing_count(self, t: datetime, ids: Sequence[str]) -> int:
        """How many of `ids` have a current listing at `t` that rests on
        `snapshot_static` rows (#35)."""
        ...

    def statement_facts(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        """`statement_facts_as_of(t, ids)` restricted to `STATEMENT_FACT_NAMES`: the
        first-vintage rows whose filing was accepted at or before `t` (`known_at <= t`),
        joined to `ids` through the securities known at `t` on `cik`, so a multi-class
        issuer's row appears once per listed class and a CIK with no securities row at
        `t` contributes nothing. Columns: `security_id` then every `statement_facts`
        column (`fact_name`, `period_start`, `period_end`, `period_days`, `value`,
        `basis`, `known_at`, ...)."""
        ...

    def sics(self, t: datetime, ids: Sequence[str]) -> Mapping[str, int | None]:
        """Each of `ids` mapped to the `sic` of its latest `classifications` row known at
        `t` (`classifications_as_of(t, ids)`); `None` when it has no such row or the row
        carries no SIC."""
        ...
