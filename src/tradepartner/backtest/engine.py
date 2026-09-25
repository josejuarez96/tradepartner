"""The backtest loop (backtest spec reqs 1-6; plan T37b).

Rebalance sessions T_0..T_n come from the calendar; the run reads the provider only at
their closes. At close(T_0) it plans the first targets. Step i (i = 0..n-1) then reads
once at t = close(T_{i+1}): the marking frame (held and target names, dividends
included), raw bars for share counts, dropped and late dividends, and, unless T_{i+1} is
the last rebalance, the next plan (universe, signal frame, gap, static listings). Every
cost level is evaluated from that one read set:

1. carry the positions from close(T_i) to the fill price on F_i (`carry_to_fill`);
2. trade to the targets planned at close(T_i) (`fills.apply_trades`);
3. value the positions at every session's close through T_{i+1} (`value_positions`).

The rebalance row for T_i records that plan, the fill on F_i and the dividends step i
read. No plan is made at the last rebalance T_n: its fill falls after the data cutoff,
so a run to T_n is exactly the prefix of a longer run. Delisting and stale exits and
the benchmark series are T37c; their counters are zero here.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

import polars as pl

from tradepartner.backtest.costs import Commissions
from tradepartner.backtest.fills import apply_trades
from tradepartner.backtest.portfolio import target_weights
from tradepartner.backtest.provider import DataProvider, GapReading
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.backtest.signals import momentum_12_1
from tradepartner.backtest.valuation import (
    StepFrame,
    carry_to_fill,
    stitched_returns,
    value_positions,
)
from tradepartner.calendar import all_sessions, previous_session
from tradepartner.config import Settings
from tradepartner.store.registry import EquityRow, RebalanceRow, TrialHandle, WeightRow

STRATEGY_SERIES = "strategy"

_POSITION_SCHEMA = {"security_id": pl.Utf8, "session": pl.Date, "value": pl.Float64}


@dataclass(frozen=True)
class BacktestResult:
    """One cost level's run. `targets` is keyed by fill session; `position_values` holds
    each position's dollar value at every session's close; `marking_frames` are the
    per-step frames, shared by every level (req 14 stitches them)."""

    cost_per_side_bps: float
    equity: tuple[EquityRow, ...]
    rebalances: tuple[RebalanceRow, ...]
    weights: tuple[WeightRow, ...]
    position_values: pl.DataFrame
    marking_frames: tuple[StepFrame, ...]
    targets: Mapping[date, Mapping[str, float]]

    def stitched_returns(self) -> pl.DataFrame:
        """`valuation.stitched_returns` over this run's marking frames."""
        return stitched_returns(self.marking_frames)


@dataclass(frozen=True)
class _Plan:
    """What the read at close(T_i) decides, identical for every cost level."""

    session: date
    fill_session: date
    targets: dict[str, float]
    n_universe: int
    n_static_listings: int
    n_excluded_no_history: int
    gap: GapReading


@dataclass
class _Book:
    """One cost level's state between steps."""

    level: float
    cash: float
    positions: dict[str, float] = field(default_factory=dict)
    marked_at: dict[str, date] = field(default_factory=dict)
    held_on: dict[str, set[date]] = field(default_factory=dict)
    equity: list[EquityRow] = field(default_factory=list)
    rebalances: list[RebalanceRow] = field(default_factory=list)
    weights: list[WeightRow] = field(default_factory=list)
    values: list[pl.DataFrame] = field(default_factory=list)


def _check_levels(cost_levels: Sequence[float]) -> list[float]:
    if not cost_levels:
        raise ValueError("need at least one cost level")
    for level in cost_levels:
        if not (math.isfinite(level) and level >= 0):
            raise ValueError(f"cost level must be non-negative and finite, got {level}")
    return sorted(set(cost_levels))


def _sessions(first: date, last: date) -> list[date]:
    sessions = all_sessions()
    return list(sessions[bisect.bisect_left(sessions, first) : bisect.bisect_right(sessions, last)])


def _plan(provider: DataProvider, params: Settings, session: date) -> _Plan:
    t = read_time(session)
    universe = provider.universe(t)
    members = sorted(universe.members["security_id"].to_list())
    strategy = params.strategy
    frame = provider.adjusted_prices(t, members, strategy.signal_total_return)
    signal = momentum_12_1(
        frame,
        session,
        strategy.formation_months,
        strategy.skip_months,
        security_ids=members,
    )
    return _Plan(
        session=session,
        fill_session=fill_session(session),
        targets=target_weights(signal.scores, strategy.top_fraction, strategy.weighting),
        n_universe=len(members),
        n_static_listings=provider.static_listing_count(t, members),
        n_excluded_no_history=signal.n_excluded,
        gap=provider.survivorship_gap(t),
    )


def _held_before(book: _Book, sid: str, ex_date: date) -> bool:
    """Whether `sid` was held at the close before its ex-date (entitled to the dividend)."""
    return previous_session(ex_date) in book.held_on.get(sid, set())


def _step(
    book: _Book,
    plan: _Plan,
    end: date,
    frame: pl.DataFrame,
    raw: pl.DataFrame,
    dividends: tuple[pl.DataFrame, pl.DataFrame],
    params: Settings,
) -> None:
    """Carry, fill and value one cost level through step (T_i, T_{i+1}]."""
    fill_price = params.execution.fill_price
    carried = carry_to_fill(
        book.positions,
        frame,
        plan.session,
        plan.fill_session,
        fill_price=fill_price,
        marked_at=book.marked_at,
    )
    fill = apply_trades(
        carried,
        book.cash,
        plan.targets,
        frame,
        raw,
        plan.fill_session,
        fill_price=fill_price,
        per_side_bps=book.level,
        commissions=Commissions.from_config(params.costs),
    )
    values = value_positions(
        fill.positions, frame, plan.fill_session, fill_price=fill_price, through=end
    )
    totals = dict(values.group_by("session").agg(pl.col("value").sum()).iter_rows())
    for session in _sessions(plan.fill_session, end):
        equity = math.fsum([fill.cash, totals.get(session, 0.0)])
        book.equity.append(EquityRow(STRATEGY_SERIES, book.level, session, equity, fill.cash))

    dropped, late = dividends
    n_late = sum(
        _held_before(book, sid, ex) for sid, ex in late.select("security_id", "ex_date").iter_rows()
    )
    for sid, session, value in values.select("security_id", "session", "value").iter_rows():
        if value > 0:
            book.held_on.setdefault(sid, set()).add(session)
    n_dropped = sum(
        plan.session < ex <= end and _held_before(book, sid, ex)
        for sid, ex in dropped.select("security_id", "ex_date").iter_rows()
    )

    book.cash = fill.cash
    last = values.filter(pl.col("session") == end)
    book.positions = {
        sid: value for sid, value in last.select("security_id", "value").iter_rows() if value > 0
    }
    book.marked_at = {
        sid: marked
        for sid, marked in last.select("security_id", "marked_at").iter_rows()
        if sid in book.positions
    }
    book.values.append(values.select(list(_POSITION_SCHEMA)))
    fill_prices = {t.security_id: t.raw_fill_price for t in fill.trades}
    for sid, weight in sorted(plan.targets.items()):
        raw_price = fill_prices.get(sid)
        if raw_price is None and sid not in fill.missing:
            raw_price = _raw_price(raw, sid, plan.fill_session, fill_price)
        # Shares are the dollar value at the fill (not at the close) over the raw price.
        shares = None if raw_price is None else fill.positions.get(sid, 0.0) / raw_price
        book.weights.append(WeightRow(plan.fill_session, sid, weight, raw_price, shares))
    book.rebalances.append(
        RebalanceRow(
            cost_per_side_bps=book.level,
            session=plan.session,
            fill_session=plan.fill_session,
            n_universe=plan.n_universe,
            n_static_listings=plan.n_static_listings,
            n_targets=len(plan.targets),
            turnover=fill.turnover,
            cost_paid=fill.cost_paid,
            gap_count_share=plan.gap.count_share,
            gap_size_share=plan.gap.size_share,
            n_missing_fill=len(fill.missing),
            n_delisting_exits=0,
            n_stale_exits=0,
            n_excluded_no_history=plan.n_excluded_no_history,
            n_dropped_dividends=n_dropped,
            n_late_dividends=n_late,
        )
    )


def _raw_price(raw: pl.DataFrame, sid: str, session: date, fill_price: str) -> float | None:
    """The raw fill price of a target name that needed no trade, if it has a bar."""
    rows = raw.filter((pl.col("security_id") == sid) & (pl.col("session") == session))
    return None if rows.is_empty() else float(rows[fill_price].item())


def run(
    params: Settings,
    provider: DataProvider,
    start: date,
    end: date,
    handle: TrialHandle,
    cost_levels: Sequence[float],
) -> dict[float, BacktestResult]:
    """Run the strategy over the rebalance sessions in `[start, end]` at every cost level
    in `cost_levels` (per-side bps; commissions from `params.costs`) from one read set.

    `params` is the trial's frozen `Settings`. Raises `TypeError` without a
    `TrialHandle` (no id, no run; ADR 0005) and `ValueError` for fewer than two
    rebalance sessions, a bad cost level or a non-zero `backtest.cash_rate` (interest on
    cash is not implemented), all before any provider call.
    """
    if not isinstance(handle, TrialHandle):
        raise TypeError(f"a run needs a TrialHandle from registry.open_trial, got {handle!r}")
    levels = _check_levels(cost_levels)
    if params.backtest.cash_rate != 0:
        raise ValueError("backtest.cash_rate other than 0 is not implemented")
    sessions = rebalance_sessions(start, end)
    if len(sessions) < 2:
        raise ValueError(f"need at least two rebalance sessions in [{start}, {end}]")

    capital = params.backtest.initial_capital
    books = [_Book(level=level, cash=capital) for level in levels]
    for book in books:
        book.equity.append(EquityRow(STRATEGY_SERIES, book.level, sessions[0], capital, capital))
    plan = _plan(provider, params, sessions[0])
    frames: list[StepFrame] = []
    targets: dict[date, Mapping[str, float]] = {}
    for index, step_end in enumerate(sessions[1:], start=1):
        t, t_prev = read_time(step_end), read_time(plan.session)
        ids = sorted({*plan.targets, *(sid for book in books for sid in book.positions)})
        frame = provider.adjusted_prices(t, ids, True)
        raw = provider.raw_prices(t, ids)
        dropped = provider.dropped_dividends(t, ids)
        ever_held = sorted({sid for book in books for sid in book.held_on})
        late = provider.late_dividends(t_prev, t, ever_held)
        next_plan = _plan(provider, params, step_end) if index < len(sessions) - 1 else None
        frames.append(StepFrame(start=plan.session, end=step_end, frame=frame))
        targets[plan.fill_session] = dict(plan.targets)
        for book in books:
            _step(book, plan, step_end, frame, raw, (dropped, late), params)
        if next_plan is not None:
            plan = next_plan

    return {
        book.level: BacktestResult(
            cost_per_side_bps=book.level,
            equity=tuple(book.equity),
            rebalances=tuple(book.rebalances),
            weights=tuple(book.weights),
            position_values=pl.concat([pl.DataFrame(schema=_POSITION_SCHEMA), *book.values]),
            marking_frames=tuple(frames),
            targets=dict(targets),
        )
        for book in books
    }
