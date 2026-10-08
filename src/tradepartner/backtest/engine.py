"""The backtest loop (backtest spec reqs 1-6; plan T37b; at a cadence, strategy-lab
spec req 6, plan T98).

Rebalance sessions T_0..T_n come from the calendar at the frozen
`schedule.rebalance_cadence` (`month_end`, `week_end` or `daily`), through
`schedule.rebalance_sessions`; the run reads the provider only at their closes, and
each step covers one period (T_i, T_{i+1}]. At close(T_0) it plans the first targets.
Step i (i = 0..n-1) then reads once at t = close(T_{i+1}): the marking frame (held and
target names, dividends included), raw bars for share counts, dropped and late
dividends, and, unless T_{i+1} is the last rebalance, the next plan (universe, signal
frame, gap, static listings). Every cost level is evaluated from that one read set:

1. carry the positions from close(T_i) to the fill price on F_i (`carry_to_fill`);
2. trade to the targets planned at close(T_i) (`fills.apply_trades`);
3. value the positions at every session's close through T_{i+1} (`value_positions`).

The rebalance row for T_i records that plan, the fill on F_i and the dividends step i
read. No plan is made at the last rebalance T_n: its fill falls after the data cutoff,
so a run to T_n is exactly the prefix of a longer run. The order holds at every
cadence: at `daily`, F_i = T_{i+1}, so the fill at F_i is applied and the period valued
before the plan at close(T_{i+1}) reads the drifted weights, on the same session.

**The plan** (`_plan`) reads the universe, then the registered family's signal inputs.
For momentum, the frozen `schedule.signal_anchor` and cadence decide the anchors;
its price frame starts at formation anchor A_form (`sessions_from`). Profitability
instead reads annual facts and SICs at the same rebalance close. The marking read is
unbounded for either family.

**Exits** (req 5), decided from step i's read after the fill, on the names still held:

- *Delisting*: the name's current listing at the read (latest `valid_from`) is
  `delisted` (a transfer to another exchange, `transferred`, is held through, whether
  its new listing is already current and `listed` or still pending -- #555/#557). It is
  sold at its last close on its final session, that listing's `end_session` (its last
  bar in the marking frame when it has none); bars printed after it (an OTC tail) are
  not marked.
- *Stale*: otherwise, no bar in the marking frame on the last
  `backtest.stale_exit_sessions` sessions through T_{i+1}; sold at its last bar's close.

The sale is booked at that session, or at F_i when the session is earlier (the step
that read the exit is the first that can book it; the name's value was frozen at that
close since, so only the cost moves). Either way only the cost is booked before the read
that decided the exit: the value sold is the close already marked. It pays
`per_side_bps` and commissions, at most the proceeds; the position is zero from that
session on and the proceeds sit in cash.
Exit notional and costs are added to the row's `turnover` and `cost_paid`.

**Read groups** (strategy-lab spec req 2, plan T105): `run_many` runs several
variants that differ only in keys that change the computation, not the reads, in
lockstep from one read set per step: each step's marking reads are made once for the
union of the variants' ids, and the plan's universe, gap and signal read once for the
group (the signal read for the union of what each variant's reader asks, from the
earliest `sessions_from`). Each variant is served exactly its own ids and bound, so its
result equals a separate `run`'s; `run` is `run_many` with one variant, whose every
request is one provider call as asked. A variant's own failure drops only it; a failed
read fails the group (`SharedReadFailed`).

**Benchmarks** (req 3): each series from `benchmark_ids` at close(T_0) (by symbol over
the run's window, #840; a run whose universe ever holds a benchmark id is refused) is
bought with the initial capital at F_0's fill price, sized after costs (the only trade), and then
carried and valued through every step's marking frame, which includes the benchmark
ids, so dividends are reinvested exactly as for the strategy. Equity rows carry the
series name and no cash.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Final, TypeVar, cast

import polars as pl

from tradepartner.backtest.costs import Commissions, buy_notional_after_costs, trade_cost
from tradepartner.backtest.fills import apply_trades
from tradepartner.backtest.portfolio import target_weights
from tradepartner.backtest.provider import DataProvider, GapReading
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.backtest.strategies import signal_for
from tradepartner.backtest.valuation import (
    StepFrame,
    carry_to_fill,
    stitched_returns,
    value_positions,
)
from tradepartner.calendar import all_sessions, previous_session
from tradepartner.config import HypothesisFamily, Settings
from tradepartner.store.delistings import DELISTED
from tradepartner.store.registry import EquityRow, RebalanceRow, TrialHandle, WeightRow
from tradepartner.universe import Universe

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
class Plan:
    """What the read at close(T_i) decides, identical for every cost level (public for
    Phase 4's tracking runs, which plan with the engine's own function, plan T53).

    `members` (the universe, sorted), `scores` and
    `excluded_no_history` (momentum members without both anchor bars) are the reads behind the
    targets, kept for Phase 4's `decisions_from` (plan T53b); the loop does not read
    them. `exclusions` (reason to ids) holds every reason the family's signal declares,
    empty or not, and `counts` (name to value) its counts, written whole to the trial
    registry (ADR 0014 point 3; #1153, T127); `excluded_no_history` and
    `n_excluded_no_history` are `exclusions.get("no_history", ())` and its length, kept as
    fields so a `Plan` is built as before. Both mappings default to empty, so a plan
    built without them records no counts."""

    session: date
    fill_session: date
    targets: dict[str, float]
    n_universe: int
    n_static_listings: int
    n_excluded_no_history: int
    gap: GapReading
    members: tuple[str, ...]
    scores: dict[str, float]
    excluded_no_history: tuple[str, ...]
    counts: Mapping[str, int] = field(default_factory=dict)
    exclusions: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass
class _Holding:
    """One benchmark's buy-and-hold state at one cost level; `marked_at` is None until
    the buy at F_0."""

    security_id: str
    cash: float
    value: float = 0.0
    marked_at: date | None = None


@dataclass(frozen=True)
class _Exit:
    """A forced sale: booked at the close of `session`, worth `value` there."""

    security_id: str
    session: date
    value: float
    cost: float
    stale: bool


@dataclass
class _Book:
    """One cost level's state between steps."""

    level: float
    cash: float
    positions: dict[str, float] = field(default_factory=dict)
    marked_at: dict[str, date] = field(default_factory=dict)
    benchmarks: dict[str, _Holding] = field(default_factory=dict)
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


def _plan(
    provider: DataProvider,
    params: Settings,
    session: date,
    family: HypothesisFamily,
) -> Plan:
    strategy = signal_for(family)
    cadence = params.schedule.rebalance_cadence
    t = read_time(session, cadence)
    universe = provider.universe(t)
    members = sorted(universe.members["security_id"].to_list())
    signal = strategy.signal(
        strategy.reader(provider, params, session, t, members), params, session, t, members
    )
    undeclared = set(signal.exclusions) - set(strategy.exclusion_reasons)
    if undeclared:
        raise ValueError(f"{family} signal reported undeclared exclusions {sorted(undeclared)}")
    exclusions = {
        reason: tuple(signal.exclusions.get(reason, ())) for reason in strategy.exclusion_reasons
    }
    no_history = exclusions.get("no_history", ())
    construction = getattr(params, strategy.section)
    return Plan(
        session=session,
        fill_session=fill_session(session, cadence),
        targets=target_weights(signal.scores, construction.top_fraction, construction.weighting),
        n_universe=len(members),
        n_static_listings=provider.static_listing_count(t, members),
        n_excluded_no_history=len(no_history),
        gap=provider.survivorship_gap(t),
        members=tuple(members),
        scores=signal.scores,
        excluded_no_history=no_history,
        counts=dict(signal.counts),
        exclusions=exclusions,
    )


#: The private name T40b's plan-timing test builds its copy of `_plan` with (kept so
#: T40 and T40b stay untouched).
_Plan = Plan


def plan(
    provider: DataProvider,
    params: Settings,
    session: date,
    family: HypothesisFamily,
) -> Plan:
    """The plan at rebalance session `session` (a rebalance session at the frozen
    `schedule.rebalance_cadence`), read at close(`session`): universe, family signal,
    targets and the gap, from `params` (the frozen hypothesis parameters). The backtest
    loop calls exactly this function (`_plan`) at every rebalance it plans."""
    return _plan(provider, params, session, family)


def _no_benchmark_members(plan: Plan, benchmarks: Mapping[str, str]) -> Plan:
    """`plan`, or `ValueError` if a benchmark security is a universe member: a benchmark
    is a reference series read by symbol (#840), never a tradable name."""
    inside = sorted(name for name, sid in benchmarks.items() if sid in plan.members)
    if inside:
        raise ValueError(
            f"benchmark {', '.join(inside)} is a universe member at {plan.session.isoformat()}; "
            "a benchmark is a reference series, never a tradable name"
        )
    return plan


def _ended(listing_ends: pl.DataFrame, session: date) -> dict[str, date | None]:
    """Securities whose current listing at the read is `delisted`, each with its final
    session (`end_session`, None when it has none).

    The current listing is the row with the latest `valid_from` on or before `session`,
    as in `universe._current_listings`: a filing ends only the listing it names, so an
    earlier listing left behind by a ticker change stays `listed` and must not decide.
    A `transferred` listing (a Form 25 transfer to another exchange, still trading) is
    not an end, the same rule `execution.planning._ended` uses (#555): the business
    keeps trading, just elsewhere -- including in the narrow window where the new
    listing is already known but its own `valid_from` has not arrived yet, so the
    current row is still the old exchange's, status `transferred`, not `listed`. Rows
    without `valid_from` (one listing per security) are taken as they are.
    """
    dated = "valid_from" in listing_ends.columns
    current: dict[str, tuple[date | None, str, date | None]] = {}
    for row in listing_ends.iter_rows(named=True):
        valid_from = row["valid_from"] if dated else None
        if valid_from is not None and valid_from > session:
            continue
        held = current.get(row["security_id"])
        if held is None or (valid_from is not None and (held[0] is None or valid_from > held[0])):
            current[row["security_id"]] = (valid_from, row["status"], row["end_session"])
    return {sid: end for sid, (_, status, end) in current.items() if status == DELISTED}


def _exits(
    book: _Book,
    positions: Mapping[str, float],
    values: pl.DataFrame,
    reads: tuple[pl.DataFrame, pl.DataFrame, dict[str, date | None]],
    fill: date,
    end: date,
    params: Settings,
) -> list[_Exit]:
    """The delisting and stale exits of `positions` at one cost level (module docstring)."""
    frame, raw, ended = reads
    last_bars = dict(
        frame.filter(pl.col("session") <= end)
        .group_by("security_id")
        .agg(pl.col("session").max())
        .iter_rows()
    )
    rows = values.select("security_id", "session", "value").iter_rows()
    at = {(sid, session): value for sid, session, value in rows}
    commissions = Commissions.from_config(params.costs)
    exits: list[_Exit] = []
    for sid in sorted(positions):
        last = last_bars.get(sid)
        if sid in ended:
            final, stale = ended[sid] or last, False
        elif last is not None and len(_sessions(last, end)) > params.backtest.stale_exit_sessions:
            final, stale = last, True
        else:
            continue
        session = fill if final is None else min(max(final, fill), end)
        value = at[(sid, session)]
        raw_close = _last_close(raw, sid, session)
        if raw_close is None:
            raise ValueError(f"{sid} has no raw bar at or before {session.isoformat()} to exit at")
        cost = trade_cost(value, value / raw_close, book.level, commissions)
        exits.append(_Exit(sid, session, value, min(cost, value), stale))
    return exits


def _last_close(raw: pl.DataFrame, sid: str, session: date) -> float | None:
    rows = raw.filter((pl.col("security_id") == sid) & (pl.col("session") <= session))
    return None if rows.is_empty() else float(rows.sort("session")["close"][-1])


def _book_exits(values: pl.DataFrame, exits: Sequence[_Exit]) -> pl.DataFrame:
    """`values` with each exited position zero from its exit session on."""
    if not exits:
        return values
    booked = pl.DataFrame(
        [(e.security_id, e.session) for e in exits],
        schema={"security_id": pl.Utf8, "exit_on": pl.Date},
        orient="row",
    )
    return (
        values.join(booked, on="security_id", how="left")
        .with_columns(
            pl.when(pl.col("exit_on").is_not_null() & (pl.col("session") >= pl.col("exit_on")))
            .then(0.0)
            .otherwise(pl.col("value"))
            .alias("value")
        )
        .drop("exit_on")
        .sort("security_id", "session")
    )


def _benchmark_step(
    book: _Book, plan: Plan, end: date, frame: pl.DataFrame, raw: pl.DataFrame, params: Settings
) -> None:
    """Buy (at F_0) or carry each benchmark, then value it through `end`."""
    fill_price = params.execution.fill_price
    for name, holding in book.benchmarks.items():
        sid = holding.security_id
        if holding.marked_at is None:
            raw_price = _price_on(raw, sid, plan.fill_session, fill_price)
            if _price_on(frame, sid, plan.fill_session, fill_price) is None or raw_price is None:
                raise ValueError(
                    f"benchmark {name} ({sid}) has no bar on {plan.fill_session.isoformat()}"
                )
            commissions = Commissions.from_config(params.costs)
            notional = buy_notional_after_costs(
                holding.cash, book.level, commissions, price=raw_price
            )
            cost = trade_cost(notional, notional / raw_price, book.level, commissions)
            holding.cash = holding.cash - notional - cost
            positions = {sid: notional}
        else:
            positions = carry_to_fill(
                {sid: holding.value},
                frame,
                plan.session,
                plan.fill_session,
                fill_price=fill_price,
                marked_at={sid: holding.marked_at},
            )
        values = value_positions(
            positions, frame, plan.fill_session, fill_price=fill_price, through=end
        )
        for session, value, marked in values.select("session", "value", "marked_at").iter_rows():
            equity = math.fsum([holding.cash, value])
            book.equity.append(EquityRow(name, book.level, session, equity, None))
            holding.value, holding.marked_at = value, marked


def _held_before(book: _Book, sid: str, ex_date: date) -> bool:
    """Whether `sid` was held at the close before its ex-date (entitled to the dividend)."""
    return previous_session(ex_date) in book.held_on.get(sid, set())


def _step(
    book: _Book,
    plan: Plan,
    end: date,
    frame: pl.DataFrame,
    raw: pl.DataFrame,
    dividends: tuple[pl.DataFrame, pl.DataFrame],
    ended: dict[str, date | None],
    params: Settings,
) -> None:
    """Carry, fill, value and exit one cost level through the period (T_i, T_{i+1}]."""
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
    negative = values.filter(pl.col("value") < 0)
    if negative.height:
        sid, session, value = negative.select("security_id", "session", "value").row(0)
        raise ValueError(
            f"negative position value {value} for {sid} on {session}: the backtester is "
            "long-only (ADR 0014, 0015), a negative value would be silently dropped"
        )
    exits = _exits(
        book, fill.positions, values, (frame, raw, ended), plan.fill_session, end, params
    )
    values = _book_exits(values, exits)
    totals = dict(values.group_by("session").agg(pl.col("value").sum()).iter_rows())
    cash = fill.cash
    for session in _sessions(plan.fill_session, end):
        proceeds = [e.value - e.cost for e in exits if e.session == session]
        cash = math.fsum([cash, *proceeds])
        equity = math.fsum([cash, totals.get(session, 0.0)])
        book.equity.append(EquityRow(STRATEGY_SERIES, book.level, session, equity, cash))
    before = math.fsum([book.cash, *carried.values()])
    exited = math.fsum(e.value for e in exits)

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

    book.cash = cash
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
            raw_price = _price_on(raw, sid, plan.fill_session, fill_price)
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
            turnover=fill.turnover + (exited / (2 * before) if before > 0 else 0.0),
            cost_paid=math.fsum([fill.cost_paid, *(e.cost for e in exits)]),
            gap_count_share=plan.gap.count_share,
            gap_size_share=plan.gap.size_share,
            n_missing_fill=len(fill.missing),
            n_delisting_exits=sum(not e.stale for e in exits),
            n_stale_exits=sum(e.stale for e in exits),
            n_dropped_dividends=n_dropped,
            n_late_dividends=n_late,
            counts=plan.counts,
        )
    )


def _price_on(frame: pl.DataFrame, sid: str, session: date, fill_price: str) -> float | None:
    """`sid`'s `fill_price` field on `session` in `frame`, None without a bar there."""
    rows = frame.filter((pl.col("security_id") == sid) & (pl.col("session") == session))
    return None if rows.is_empty() else float(rows[fill_price].item())


def run(
    params: Settings,
    provider: DataProvider,
    start: date,
    end: date,
    handle: TrialHandle,
    cost_levels: Sequence[float],
    *,
    family: HypothesisFamily,
) -> dict[float, BacktestResult]:
    """Run the strategy over the rebalance sessions in `[start, end]` at the frozen
    `schedule.rebalance_cadence`, at every cost level in `cost_levels` (per-side bps;
    commissions from `params.costs`) from one read set: `run_many` with one variant,
    whose failure, shared read or not, is raised as the error itself.

    `params` is the trial's frozen `Settings` (`hypothesis.load_frozen`, which reads the
    schedule keys through `frozen.frozen_values`). Raises `TypeError` without a
    `TrialHandle` (no id, no run; ADR 0005) and `ValueError` for fewer than two
    rebalance sessions, a bad cost level or a non-zero `backtest.cash_rate` (interest on
    cash is not implemented), all before any provider call.
    """
    failure: Exception | None = None
    outcome = RunManyResults()
    try:
        outcome = run_many([(params, handle)], provider, start, end, cost_levels, family=family)
    except SharedReadFailed as exc:
        failure = exc.cause
    if failure is None and outcome.failures:
        failure = outcome.failures[handle.trial_id]
    if failure is not None:
        raise failure
    return outcome[handle.trial_id]


# --- read groups (strategy-lab spec req 2, plan T105) --------------------------------

#: The message every open variant of a read group fails with when a read it shares
#: fails (strategy-lab spec req 2).
SHARED_READ_FAILED: Final = "shared read failed"


class SharedReadFailed(RuntimeError):
    """A provider read shared by a read group failed, so every variant still open fails
    with it (strategy-lab spec req 2). `trial_ids` names them, `cause` is the
    provider's error."""

    def __init__(self, trial_ids: Sequence[int], cause: Exception) -> None:
        self.trial_ids = tuple(trial_ids)
        self.cause = cause
        names = ", ".join(str(trial_id) for trial_id in self.trial_ids)
        super().__init__(f"{SHARED_READ_FAILED} (trials {names}): {type(cause).__name__}: {cause}")


class RunManyResults(dict[int, dict[float, BacktestResult]]):
    """`run_many`'s result: trial id to that variant's per-level results, for every
    variant that completed. `failures` maps each variant whose own computation raised
    (its signal, targets, fills or valuation) to the error; the others are unaffected."""

    def __init__(self) -> None:
        super().__init__()
        self.failures: dict[int, Exception] = {}


class _ReadError(Exception):
    """A real provider call made by `_SharedReads` failed (its `__cause__`)."""


_Key = tuple[object, ...]
_Request = tuple[tuple[str, ...], date | None]
_T = TypeVar("_T")


@dataclass(frozen=True)
class _Entry:
    """One real keyed read: the ids and session bound it was made for, and its value."""

    ids: tuple[str, ...]
    sessions_from: date | None
    value: object

    def covers(self, ids: Sequence[str], sessions_from: date | None) -> bool:
        bounded = self.sessions_from is None or (
            sessions_from is not None and sessions_from >= self.sessions_from
        )
        return bounded and set(ids) <= set(self.ids)

    def restricted(self, ids: Sequence[str], sessions_from: date | None) -> object:
        """The value as a read for exactly `ids` from `sessions_from` returns it: rows
        of other ids, and bars before the bound, left out (the bound applies after the
        as-of read, provider docstring, so the factors are unchanged)."""
        if tuple(ids) == self.ids and sessions_from == self.sessions_from:
            return self.value
        if isinstance(self.value, pl.DataFrame):
            frame = self.value.filter(pl.col("security_id").is_in(list(ids)))
            if sessions_from is not None and sessions_from != self.sessions_from:
                frame = frame.filter(pl.col("session") >= sessions_from)
            return frame
        mapping = cast(Mapping[str, object], self.value)
        return {sid: mapping[sid] for sid in ids}


def _keyed_call(provider: DataProvider, key: _Key, ids: list[str], bound: date | None) -> object:
    """The provider read `key` names, for `ids` (and `bound`, the price read's only)."""
    method, *args = key
    t = cast(datetime, args[0])
    if method == "adjusted_prices":
        include_dividends = cast(bool, args[1])
        if bound is None:
            return provider.adjusted_prices(t, ids, include_dividends)
        return provider.adjusted_prices(t, ids, include_dividends, sessions_from=bound)
    if method == "late_dividends":
        return provider.late_dividends(t, cast(datetime, args[1]), ids)
    read = cast(Callable[[datetime, list[str]], object], getattr(provider, cast(str, method)))
    return read(t, ids)


class _SharedReads:
    """The provider as every variant of a read group sees it within one step: each read
    the group shares is made once, for the union of the variants' ids (`share`), and
    each variant's own request is served from it restricted to its ids. A request no
    shared read covers is made as asked. With `covering` false (a group of one, `run`)
    a read serves only the one request it was made for, so every request is one
    provider call exactly as asked. Every real call's error is a `_ReadError`."""

    def __init__(self, provider: DataProvider, *, covering: bool) -> None:
        self._provider = provider
        self._covering = covering
        self._entries: dict[_Key, list[_Entry]] = {}
        self._memo: dict[_Key, object] = {}

    def new_step(self) -> None:
        """Drop the previous step's reads (every key holds its read time)."""
        self._entries.clear()
        self._memo.clear()

    def _real(self, call: Callable[[], _T]) -> _T:
        try:
            return call()
        except Exception as exc:
            raise _ReadError from exc

    def share(self, key: _Key, requests: Sequence[_Request]) -> None:
        """Make the read `key` names once for every request in `requests`: one request's
        ids as given, several requests' union (sorted) from the earliest bound."""
        if len(requests) == 1:
            ids, bound = requests[0]
        else:
            ids = tuple(sorted({sid for request_ids, _ in requests for sid in request_ids}))
            bounds = [request_bound for _, request_bound in requests]
            bound = None if None in bounds else min(b for b in bounds if b is not None)
        value = self._real(lambda: _keyed_call(self._provider, key, list(ids), bound))
        self._entries.setdefault(key, []).append(_Entry(tuple(ids), bound, value))

    def _read(self, key: _Key, ids: Sequence[str], bound: date | None = None) -> object:
        entries = self._entries.setdefault(key, [])
        exact = [e for e in entries if e.ids == tuple(ids) and e.sessions_from == bound]
        if not self._covering:
            if not exact:
                self.share(key, [(tuple(ids), bound)])
                exact = [entries[-1]]
            entries.remove(exact[0])
            return exact[0].value
        for entry in [*exact, *entries]:
            if entry.covers(ids, bound):
                return entry.restricted(ids, bound)
        self.share(key, [(tuple(ids), bound)])
        return entries[-1].value

    def _once(self, key: _Key, call: Callable[[], _T]) -> _T:
        if not self._covering:
            return self._real(call)
        if key not in self._memo:
            self._memo[key] = self._real(call)
        return cast(_T, self._memo[key])

    # --- DataProvider ------------------------------------------------------------

    def universe(self, t: datetime) -> Universe:
        return self._once(("universe", t), lambda: self._provider.universe(t))

    def adjusted_prices(
        self,
        t: datetime,
        ids: Sequence[str],
        include_dividends: bool,
        *,
        sessions_from: date | None = None,
    ) -> pl.DataFrame:
        key = ("adjusted_prices", t, include_dividends)
        return cast(pl.DataFrame, self._read(key, ids, sessions_from))

    def raw_prices(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return cast(pl.DataFrame, self._read(("raw_prices", t), ids))

    def listing_ends(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return cast(pl.DataFrame, self._read(("listing_ends", t), ids))

    def benchmark_ids(self, t: datetime, through: date | None = None) -> Mapping[str, str]:
        return self._once(
            ("benchmark_ids", t, through), lambda: self._provider.benchmark_ids(t, through)
        )

    def survivorship_gap(self, t: datetime) -> GapReading:
        return self._once(("survivorship_gap", t), lambda: self._provider.survivorship_gap(t))

    def dropped_dividends(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return cast(pl.DataFrame, self._read(("dropped_dividends", t), ids))

    def late_dividends(self, t_prev: datetime, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return cast(pl.DataFrame, self._read(("late_dividends", t_prev, t), ids))

    def static_listing_count(self, t: datetime, ids: Sequence[str]) -> int:
        return self._once(
            ("static_listing_count", t, tuple(ids)),
            lambda: self._provider.static_listing_count(t, ids),
        )

    def statement_facts(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return cast(pl.DataFrame, self._read(("statement_facts", t), ids))

    def sics(self, t: datetime, ids: Sequence[str]) -> Mapping[str, int | None]:
        return cast(Mapping[str, int | None], self._read(("sics", t), ids))


class _Requests:
    """A stand-in provider a variant's signal reader runs against to say which keyed
    reads it needs (each returns an empty value the reader only wraps); every other
    read goes to the shared view."""

    def __init__(self, view: _SharedReads) -> None:
        self.view = view
        self.asked: dict[_Key, list[_Request]] = {}

    def _ask(self, key: _Key, ids: Sequence[str], bound: date | None = None) -> pl.DataFrame:
        self.asked.setdefault(key, []).append((tuple(ids), bound))
        return pl.DataFrame()

    def universe(self, t: datetime) -> Universe:
        return self.view.universe(t)

    def adjusted_prices(
        self,
        t: datetime,
        ids: Sequence[str],
        include_dividends: bool,
        *,
        sessions_from: date | None = None,
    ) -> pl.DataFrame:
        return self._ask(("adjusted_prices", t, include_dividends), ids, sessions_from)

    def raw_prices(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return self._ask(("raw_prices", t), ids)

    def listing_ends(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return self._ask(("listing_ends", t), ids)

    def benchmark_ids(self, t: datetime, through: date | None = None) -> Mapping[str, str]:
        return self.view.benchmark_ids(t, through)

    def survivorship_gap(self, t: datetime) -> GapReading:
        return self.view.survivorship_gap(t)

    def dropped_dividends(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return self._ask(("dropped_dividends", t), ids)

    def late_dividends(self, t_prev: datetime, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return self._ask(("late_dividends", t_prev, t), ids)

    def static_listing_count(self, t: datetime, ids: Sequence[str]) -> int:
        return self.view.static_listing_count(t, ids)

    def statement_facts(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return self._ask(("statement_facts", t), ids)

    def sics(self, t: datetime, ids: Sequence[str]) -> Mapping[str, int | None]:
        self._ask(("sics", t), ids)
        return {}


@dataclass
class _Variant:
    """One variant's run state between steps."""

    settings: Settings
    handle: TrialHandle
    plan: Plan | None = None
    books: list[_Book] = field(default_factory=list)
    frames: list[StepFrame] = field(default_factory=list)
    targets: dict[date, Mapping[str, float]] = field(default_factory=dict)

    @property
    def trial_id(self) -> int:
        return self.handle.trial_id


def _check_variants(
    variants: Sequence[tuple[Settings, TrialHandle]], cost_levels: Sequence[float]
) -> list[float]:
    """Every refusal before any read; the checked cost levels."""
    if not variants:
        raise ValueError("run_many needs at least one variant")
    for _, handle in variants:
        if not isinstance(handle, TrialHandle):
            raise TypeError(f"a run needs a TrialHandle from registry.open_trial, got {handle!r}")
    levels = _check_levels(cost_levels)
    ids = [handle.trial_id for _, handle in variants]
    if len(set(ids)) != len(ids):
        raise ValueError(f"each variant needs its own trial, got trial ids {ids}")
    for params, _ in variants:
        if params.backtest.cash_rate != 0:
            raise ValueError("backtest.cash_rate other than 0 is not implemented")
    cadences = {params.schedule.rebalance_cadence for params, _ in variants}
    if len(cadences) != 1:
        raise ValueError(f"a read group runs at one cadence, got {sorted(cadences)}")
    return levels


def _plan_all(
    view: _SharedReads,
    group: list[_Variant],
    session: date,
    family: HypothesisFamily,
    on_failure: Callable[[_Variant, Exception], None],
) -> dict[int, Plan]:
    """Each open variant's plan at `session`, from one signal read for the group: with
    several variants, each one's reader first says what it reads (`_Requests`), and
    each keyed read is made once for their union before any variant plans."""
    if len(group) > 1:
        strategy = signal_for(family)
        t = read_time(session, group[0].settings.schedule.rebalance_cadence)
        members = sorted(view.universe(t).members["security_id"].to_list())
        requests = _Requests(view)
        for variant in list(group):
            try:
                strategy.reader(requests, variant.settings, session, t, members)
            except _ReadError:
                raise
            except Exception as exc:
                on_failure(variant, exc)
        for key, asked in requests.asked.items():
            view.share(key, asked)
    plans: dict[int, Plan] = {}
    for variant in list(group):
        try:
            plans[variant.trial_id] = _plan(view, variant.settings, session, family)
        except _ReadError:
            raise
        except Exception as exc:
            on_failure(variant, exc)
    return plans


def run_many(
    variants: Sequence[tuple[Settings, TrialHandle]],
    provider: DataProvider,
    start: date,
    end: date,
    cost_levels: Sequence[float],
    *,
    family: HypothesisFamily,
) -> RunManyResults:
    """Run a read group's variants (strategy-lab spec req 2): every variant's frozen
    `Settings` with its own `TrialHandle`, over the rebalance sessions in `[start, end]`
    at their one cadence, at every cost level, from **one read set per step**: the
    marking reads for the union of the variants' ids and one signal read for the union
    of their requests, each variant served its own ids only, so each result equals a
    separate `run`'s.

    Raises `TypeError` when any variant lacks a `TrialHandle` and `ValueError` for the
    refusals `run` names, two variants sharing a trial or cadences that differ, all
    before any provider call. A failure inside one variant's own computation (its
    signal, targets, fills or valuation) is recorded in `failures` against that
    variant only, which is then dropped; a failed provider read raises
    `SharedReadFailed` naming every variant still open.
    """
    signal_for(family)
    levels = _check_variants(variants, cost_levels)
    cadence = variants[0][0].schedule.rebalance_cadence
    sessions = rebalance_sessions(start, end, cadence)
    if len(sessions) < 2:
        raise ValueError(f"need at least two rebalance sessions in [{start}, {end}]")

    out = RunManyResults()
    group = [_Variant(params, handle) for params, handle in variants]

    def failed(variant: _Variant, exc: Exception) -> None:
        out.failures[variant.trial_id] = exc
        group.remove(variant)

    view = _SharedReads(provider, covering=len(group) > 1)
    try:
        _run_group(view, group, sessions, levels, family, failed)
    except _ReadError as exc:
        cause = exc.__cause__
        assert isinstance(cause, Exception)
        raise SharedReadFailed([v.trial_id for v in group], cause) from cause

    for variant in group:
        out[variant.trial_id] = {
            book.level: BacktestResult(
                cost_per_side_bps=book.level,
                equity=tuple(book.equity),
                rebalances=tuple(book.rebalances),
                weights=tuple(book.weights),
                position_values=pl.concat([pl.DataFrame(schema=_POSITION_SCHEMA), *book.values]),
                marking_frames=tuple(variant.frames),
                targets=dict(variant.targets),
            )
            for book in variant.books
        }
    return out


def _run_group(
    view: _SharedReads,
    group: list[_Variant],
    sessions: Sequence[date],
    levels: Sequence[float],
    family: HypothesisFamily,
    failed: Callable[[_Variant, Exception], None],
) -> None:
    """`run_many`'s loop over `group` (the module docstring's steps, in lockstep); a
    failed variant leaves `group` through `failed`."""
    cadence = group[0].settings.schedule.rebalance_cadence
    plans = _plan_all(view, group, sessions[0], family, failed)
    if not group:
        return
    benchmarks = dict(
        sorted(view.benchmark_ids(read_time(sessions[0], cadence), through=sessions[-1]).items())
    )
    for variant in list(group):
        try:
            variant.plan = _no_benchmark_members(plans[variant.trial_id], benchmarks)
        except Exception as exc:
            failed(variant, exc)
            continue
        capital = variant.settings.backtest.initial_capital
        variant.books = [
            _Book(
                level=level,
                cash=capital,
                benchmarks={name: _Holding(sid, capital) for name, sid in benchmarks.items()},
            )
            for level in levels
        ]
        for book in variant.books:
            book.equity.append(
                EquityRow(STRATEGY_SERIES, book.level, sessions[0], capital, capital)
            )
            for name in benchmarks:
                book.equity.append(EquityRow(name, book.level, sessions[0], capital, None))

    for index, step_end in enumerate(sessions[1:], start=1):
        if not group:
            return
        view.new_step()
        t = read_time(step_end, cadence)
        own = [_marking_ids(variant, benchmarks) for variant in group]
        t_prev = read_time(cast(Plan, group[0].plan).session, cadence)
        view.share(("adjusted_prices", t, True), [(marked, None) for _, marked, _ in own])
        view.share(("raw_prices", t), [(marked, None) for _, marked, _ in own])
        view.share(("listing_ends", t), [(ids, None) for ids, _, _ in own])
        view.share(("dropped_dividends", t), [(ids, None) for ids, _, _ in own])
        view.share(("late_dividends", t_prev, t), [(held, None) for _, _, held in own])
        last = index == len(sessions) - 1
        next_plans = {} if last else _plan_all(view, group, step_end, family, failed)
        for variant in list(group):
            try:
                _variant_step(view, variant, step_end, benchmarks, next_plans.get(variant.trial_id))
            except _ReadError:
                raise
            except Exception as exc:
                failed(variant, exc)


def _marking_ids(
    variant: _Variant, benchmarks: Mapping[str, str]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """The variant's step reads' ids: planned and held names, those plus the
    benchmarks (marked), and every name it ever held (late dividends)."""
    plan = cast(Plan, variant.plan)
    ids = sorted({*plan.targets, *(sid for book in variant.books for sid in book.positions)})
    marked = sorted({*ids, *benchmarks.values()})
    ever_held = sorted({sid for book in variant.books for sid in book.held_on})
    return tuple(ids), tuple(marked), tuple(ever_held)


def _variant_step(
    view: _SharedReads,
    variant: _Variant,
    step_end: date,
    benchmarks: Mapping[str, str],
    next_plan: Plan | None,
) -> None:
    """One variant's step through `step_end` from the shared reads, restricted to its
    own ids: carry, fill, value and exit every level, then take `next_plan`."""
    plan = cast(Plan, variant.plan)
    params = variant.settings
    cadence = params.schedule.rebalance_cadence
    t, t_prev = read_time(step_end, cadence), read_time(plan.session, cadence)
    ids, marked, ever_held = (list(part) for part in _marking_ids(variant, benchmarks))
    frame = view.adjusted_prices(t, marked, True)
    raw = view.raw_prices(t, marked)
    ended = _ended(view.listing_ends(t, ids), step_end)
    dropped = view.dropped_dividends(t, ids)
    late = view.late_dividends(t_prev, t, ever_held)
    if next_plan is not None:
        next_plan = _no_benchmark_members(next_plan, benchmarks)
    variant.frames.append(StepFrame(start=plan.session, end=step_end, frame=frame))
    variant.targets[plan.fill_session] = dict(plan.targets)
    for book in variant.books:
        _step(book, plan, step_end, frame, raw, (dropped, late), ended, params)
        _benchmark_step(book, plan, step_end, frame, raw, params)
    if next_plan is not None:
        variant.plan = next_plan
