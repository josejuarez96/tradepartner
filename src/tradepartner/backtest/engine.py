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
instead reads annual facts and SICs at the same rebalance close. The marking read
starts at the earliest held formation anchor or stale-exit window.

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

**Hold mode and the band** (backtest spec amendment #1731, T209b): under
`strategy.weighting = "hold"` each cost level's book keeps the plan index at which every
held name entered (`_Book.entries`) and `portfolio.hold_membership` decides, at each fill,
who exits on session N (`hold_sessions` plans after entry) and who enters at
`1 / hold_target_names` of equity; a live name is not traded and the rest is cash. The
plan's `targets` are then the candidates (the top `hold_target_names` ranked names). Under
either weighting `strategy.rebalance_band` leaves every name whose drift is inside the
band alone (`portfolio.apply_rebalance_band`): such names are set aside, `apply_trades`
trades only the rest (its targets rescaled to the whole equity), and a band of 0 with
`equal` weighting takes no new path at all.

**Cash accrual** (amendment #1731): under `backtest.cash_rate = "store"` the cash of each
book grows by the session's `rf(s)` accrual (`riskfree`, over the frame read once at the
data cutoff, before the first step) at every session after T_0: cash held to the fill
accrues through F_i before the trade, exit proceeds from the session after theirs.

**The signed book** (spec amendment #1872, items 4 to 6; `portfolio.side = "long_short"`,
plan T222): the same loop carries signed dollar values. Under `long_short` `_step` marks
the positions with `value_positions(signed=True)`, trades with `apply_trades(signed=True)`
(the long buys capped by free cash, cash minus the short magnitudes), and keeps a
zero-excluded position set, so a short is a negative value; a short holding is recorded
in `held_on` too, so the dividend counts see it. At every session after the plan's read the
book pays the borrow fee (`costs.borrow_bps_per_year`, calendar days over a 360-day year,
on the previous close's short values, the carry window from the plan to the fill
included) and, under `cash_rate = "store"`, accrues `rf(s)` on free cash only (the short
proceeds earn nothing). A session whose equity is not positive raises `ValueError` naming
the session (a `failed` trial), never a silent ruin. `_exits`
covers a delisted short at its last close and a stale one at its last bar's close like a
long, and its cost is a magnitude, so the sign only decides which way cash moves. Under
`long_only` (`side` at its default) no new branch is taken and every pinned run is
byte-identical.

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
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Final, TypeVar, cast

import polars as pl

from tradepartner.backtest import placebo
from tradepartner.backtest.costs import (
    Commissions,
    adv_by_security,
    borrow_fee,
    buy_notional_after_costs,
    trade_cost,
)
from tradepartner.backtest.fills import FillResult, apply_trades
from tradepartner.backtest.metrics import PERCENT_POINTS_PER_UNIT
from tradepartner.backtest.portfolio import (
    _equal_weight,
    _selected_count,
    apply_rebalance_band,
    buffered_targets,
    drifted_weights,
    hold_membership,
    target_weights,
)
from tradepartner.backtest.provider import (
    TURNOVER_BAR_COLUMNS,
    DataProvider,
    GapReading,
    TurnoverInputs,
)
from tradepartner.backtest.riskfree import (
    RiskFreeMissing,
    published_by_session,
    session_accruals,
    to_yield,
)
from tradepartner.backtest.schedule import (
    _is_rebalance_session,
    fill_session,
    read_time,
    rebalance_sessions,
)
from tradepartner.backtest.signals import anchor_sessions
from tradepartner.backtest.strategies import signal_for
from tradepartner.backtest.valuation import (
    StepFrame,
    carry_to_fill,
    stitched_returns,
    value_positions,
)
from tradepartner.calendar import (
    all_sessions,
    last_completed_session,
    next_session,
    previous_session,
    session_close,
    sessions_in_month_window,
)
from tradepartner.config import Cadence, HypothesisFamily, Settings
from tradepartner.store.delistings import DELISTED
from tradepartner.store.registry import EquityRow, RebalanceRow, TrialHandle, WeightRow
from tradepartner.universe import Universe

STRATEGY_SERIES = "strategy"

_POSITION_SCHEMA = {"security_id": pl.Utf8, "session": pl.Date, "value": pl.Float64}


@dataclass(frozen=True)
class BacktestResult:
    """One cost level's run. `targets` is keyed by fill session; `position_values` holds
    each position's dollar value at every session's close; `marking_frames` are the
    per-step frames, shared by every level (req 14 stitches them), empty when the run
    was asked not to keep them (`run_many(..., keep_marking_frames=False)`); `weights`
    and `position_values` are empty when it was asked to keep no detail
    (`run_many(..., keep_detail=False)`, #1448). `rates` is the risk-free series the
    trial's `metrics.risk_free_series` names, as `DataProvider.rates` returned it at the
    run's data cutoff (one read per run, the same frame at every level; backtest spec,
    amendment 2026-10-11, T170c); `None` unless `metrics.risk_free_source` is `store`,
    when the provider's `rates` is never called."""

    cost_per_side_bps: float
    equity: tuple[EquityRow, ...]
    rebalances: tuple[RebalanceRow, ...]
    weights: tuple[WeightRow, ...]
    position_values: pl.DataFrame
    marking_frames: tuple[StepFrame, ...]
    targets: Mapping[date, Mapping[str, float]]
    rates: pl.DataFrame | None = None

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
    held_since: dict[str, date] = field(default_factory=dict)
    entries: dict[str, int] = field(default_factory=dict)
    equity: list[EquityRow] = field(default_factory=list)
    rebalances: list[RebalanceRow] = field(default_factory=list)
    weights: list[WeightRow] = field(default_factory=list)
    values: list[pl.DataFrame] = field(default_factory=list)
    keep_detail: bool = True
    chained_positions: int = 0  # #821: positions renamed by a successor chain this step


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


@dataclass(frozen=True)
class _Construction:
    """The portfolio-construction keys of the family's own section."""

    weighting: str
    top_fraction: float
    band: float
    hold_sessions: int
    hold_target_names: int

    def target_names(self, n_ranked: int) -> int:
        """The names a hold-mode book aims to hold: `hold_target_names`, or the count
        `top_fraction` selects when that is 0."""
        return self.hold_target_names or _selected_count(n_ranked, self.top_fraction)


def _construction(params: Settings, family: HypothesisFamily) -> _Construction:
    section = getattr(params, signal_for(family).section)
    return _Construction(
        weighting=section.weighting,
        top_fraction=section.top_fraction,
        band=getattr(section, "rebalance_band", 0.0),
        hold_sessions=getattr(section, "hold_sessions", 1),
        hold_target_names=getattr(section, "hold_target_names", 0),
    )


def _ranked(scores: Mapping[str, float]) -> list[str]:
    """Names by score descending, ties by `security_id` (as `target_weights` ranks)."""
    return sorted(scores, key=lambda sid: (-scores[sid], sid))


def _hold_candidates(scores: Mapping[str, float], construction: _Construction) -> dict[str, float]:
    """A hold-mode plan's targets: the names it may enter, the top `target_names` ranked,
    each at `1 / target_names`. A live name holds a slot and the free slots fill from the
    best names not live, so no entry is ranked below `target_names`."""
    ranked = _ranked(scores)
    count = construction.target_names(len(ranked))
    if not ranked or count == 0:
        return {}
    weight = _equal_weight(count)
    return {sid: weight for sid in sorted(ranked[:count])}


def _read_session(session: date, d: int, cadence: Cadence) -> date:
    """The session a plan at rebalance session `session` reads at: `session` itself for
    `d = 0`; otherwise step back `d` XNYS sessions, then back to the latest rebalance
    session at `cadence` (the signal readers refuse any other). A delay (ADR 0023 rung 4)
    moves only the read: the plan, its fill and the book it is built against stay
    `session`'s."""
    day = session
    for _ in range(d):
        day = previous_session(day)
    while d > 0 and not _is_rebalance_session(day, cadence):
        day = previous_session(day)
    return day


def _plan(
    provider: DataProvider,
    params: Settings,
    session: date,
    family: HypothesisFamily,
) -> Plan:
    strategy = signal_for(family)
    cadence = params.schedule.rebalance_cadence
    read = _read_session(session, params.strategy.signal_delay_sessions, cadence)
    t = read_time(read, cadence)
    universe = provider.universe(t)
    members = sorted(universe.members["security_id"].to_list())
    signal = strategy.signal(
        strategy.reader(provider, params, read, t, members), params, read, t, members
    )
    undeclared = set(signal.exclusions) - set(strategy.exclusion_reasons)
    if undeclared:
        raise ValueError(f"{family} signal reported undeclared exclusions {sorted(undeclared)}")
    exclusions = {
        reason: tuple(signal.exclusions.get(reason, ())) for reason in strategy.exclusion_reasons
    }
    no_history = exclusions.get("no_history", ())
    scores = signal.scores
    mode = params.strategy.placebo
    if mode != "none":
        seed = f"{params.strategy.placebo_seed}:{session.toordinal()}"
        scores = (placebo.shuffle if mode == "shuffle" else placebo.random_entry)(scores, seed)
    construction = getattr(params, strategy.section)
    if construction.weighting == "hold":
        targets = _hold_candidates(scores, _construction(params, family))
    else:
        targets = target_weights(scores, construction.top_fraction, construction.weighting)
    return Plan(
        session=session,
        fill_session=fill_session(session, cadence),
        targets=targets,
        n_universe=len(members),
        n_static_listings=provider.static_listing_count(t, members),
        n_excluded_no_history=len(no_history),
        gap=provider.survivorship_gap(t),
        members=tuple(members),
        scores=scores,
        excluded_no_history=no_history,
        counts=dict(signal.counts),
        exclusions=exclusions,
    )


#: The `Plan.exclusions` key for a universe candidate with no bar at the read session
#: (paper spec amendment 2026-10-20, #1997).
NO_T0_BAR: Final = "no_t0_bar"


def no_t0_bar_candidates(
    provider: DataProvider, params: Settings, session: date
) -> tuple[str, ...]:
    """Securities the universe dropped only because the read session's (T_0) bar is
    missing (#1997): rule 6 (`history`, `missing_bars`) with every other session of the
    history window present. Sorted ids, read at the same `t` as `plan`'s universe.

    A partial ingest (SPY present, one name's T_0 bar absent) drops such a name with no
    trace; the paper planner records it as `excluded_no_t0_bar` and alerts. They are not
    universe members, so `Plan.n_universe` and the targets are untouched. Pure over the
    provider's as-of reads: bars known at `t` only."""
    cadence = params.schedule.rebalance_cadence
    read = _read_session(session, params.strategy.signal_delay_sessions, cadence)
    t = read_time(read, cadence)
    universe = provider.universe(t)
    dropped = universe.exclusions.filter(
        (pl.col("rule_name") == "history") & (pl.col("reason") == "missing_bars")
    )["security_id"].to_list()
    if not dropped:
        return ()
    history = set(sessions_in_month_window(universe.session, params.universe.min_history_months))
    bars = provider.raw_prices(t, sorted(dropped)).filter(pl.col("session") <= universe.session)
    have: dict[str, set[date]] = {}
    for sid, day in bars.select("security_id", "session").iter_rows():
        have.setdefault(sid, set()).add(day)
    return tuple(
        sorted(
            sid
            for sid in dropped
            if universe.session not in have.get(sid, set())
            and history - {universe.session} <= have.get(sid, set())
        )
    )


#: The private name T40b's plan-timing test builds its copy of `_plan` with (kept so
#: T40 and T40b stay untouched).
_Plan = Plan


def plan(
    provider: DataProvider,
    params: Settings,
    session: date,
    family: HypothesisFamily,
    *,
    previous_targets: Collection[str] | None = None,
) -> Plan:
    """The plan at rebalance session `session` (a rebalance session at the frozen
    `schedule.rebalance_cadence`), read at close(`session`): universe, family signal,
    targets and the gap, from `params` (the frozen hypothesis parameters). The backtest
    loop calls exactly this function (`_plan`) at every rebalance it plans.

    `previous_targets` is the previous plan's targets of the same run, the buffer's
    carried state (spec amendment #657, item 4). At the default
    `strategy.hold_top_fraction` it is ignored and the call is unchanged. For a
    registration with the buffer on it is required: a call that omits it (leaves it
    `None`) raises `ValueError` naming the key, and an explicit empty mapping means a
    run's first plan (`P = ∅`). The paper planner calls this without the argument, so a
    buffered window's plan trial fails closed rather than trading an unbuffered plan
    under a buffered label."""
    return _apply_buffer(_plan(provider, params, session, family), params, previous_targets)


def _apply_buffer(built: Plan, params: Settings, previous_targets: Collection[str] | None) -> Plan:
    """`built` with the `momentum` buy/hold buffer applied (spec amendment #657, item 2).

    At the default `strategy.hold_top_fraction` (0.0) `built` is returned unchanged and
    no new path runs. Otherwise the targets are rebuilt from `buffered_targets` over the
    plan's own `scores` (already past B10's screen, rung 4's delay and rung 5's placebo)
    under `equal` weighting, so a held name is reset to the common weight, and
    `n_retained` joins the plan's counts, reported only here (item 2). `previous_targets`
    is the previous plan's targets after the successor-chain renaming; `None` is the
    omitted argument, refused so no run records results under a buffered label."""
    hold = params.strategy.hold_top_fraction
    if hold == 0.0:
        return built
    if previous_targets is None:
        raise ValueError(
            "engine.plan needs previous_targets for a registration with "
            f"strategy.hold_top_fraction = {hold!r} (the buy/hold buffer); pass the "
            "previous plan's targets, or an empty mapping for a run's first plan"
        )
    targets, n_retained = buffered_targets(
        built.scores, previous_targets, params.strategy.top_fraction, hold
    )
    counts = dict(built.counts)
    counts["n_retained"] = n_retained
    return replace(built, targets=targets, counts=counts)


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
    adv: Mapping[str, float],
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
        # A trade's cost is a magnitude: a short's value is negative, and its cover pays
        # the same model as a long's sale (spec amendment #1872, item 7). `abs` is the
        # identity on the positive value a `long_only` book carries.
        notional = abs(value)
        cost = trade_cost(
            notional,
            notional / raw_close,
            book.level,
            commissions,
            spread_bps=params.costs.spread_bps,
            impact_coeff=params.costs.impact_coeff,
            adv=adv.get(sid),
        )
        exits.append(_Exit(sid, session, value, min(cost, notional), stale))
    return exits


def _short_value(positions: Mapping[str, float]) -> float:
    """The absolute dollar value of the short positions (Σ |v| over v < 0), 0 when none."""
    return math.fsum(-value for value in positions.values() if value < 0)


def _shorts(positions: Mapping[str, float]) -> dict[str, float]:
    """The short positions (negative values) of a signed book, keyed by security."""
    return {sid: value for sid, value in positions.items() if value < 0}


def _shorts_by_session(values: pl.DataFrame) -> dict[date, dict[str, float]]:
    """Each session's short positions (negative values), keyed by security.

    A signed book's borrow fee and free-cash accrual read the short values at the
    previous session's close (spec amendment #1872, item 6), so they are grouped once
    over the step's post-exit marking frame."""
    out: dict[date, dict[str, float]] = {}
    for session, sid, value in values.select("session", "security_id", "value").iter_rows():
        if value < 0:
            out.setdefault(session, {})[sid] = value
    return out


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
    adv = (
        adv_by_security(raw, plan.session, params.costs.adv_sessions)
        if params.costs.impact_coeff != 0.0
        else {}
    )
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
                holding.cash,
                book.level,
                commissions,
                price=raw_price,
                spread_bps=params.costs.spread_bps,
                impact_coeff=params.costs.impact_coeff,
                adv=adv.get(sid),
            )
            cost = trade_cost(
                notional,
                notional / raw_price,
                book.level,
                commissions,
                spread_bps=params.costs.spread_bps,
                impact_coeff=params.costs.impact_coeff,
                adv=adv.get(sid),
            )
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


def _spin_off_path(sid: str, mapping: Mapping[str, str]) -> tuple[str, ...]:
    """IDs from a parent through its live successors, stopping before a cycle."""
    path: list[str] = []
    seen: set[str] = set()
    current = sid
    while current not in seen:
        seen.add(current)
        path.append(current)
        if current not in mapping:
            break
        current = mapping[current]
    return tuple(path)


def _spin_off_counts(
    book: _Book,
    start: date,
    end: date,
    spin_offs: tuple[pl.DataFrame, pl.DataFrame],
    chains: Sequence[tuple[str, str]],
) -> dict[str, int]:
    """Count held parents using only the successor chains live at this step."""
    mapping = dict(chains)
    unvalued, late = spin_offs
    return {
        "n_spin_offs_unvalued": sum(
            start < ex <= end
            and any(_held_before(book, parent, ex) for parent in _spin_off_path(sid, mapping))
            for sid, ex in unvalued.select("security_id", "ex_date").iter_rows()
        ),
        "n_late_spin_offs": sum(
            any(_held_before(book, parent, ex) for parent in _spin_off_path(sid, mapping))
            for sid, ex in late.select("security_id", "ex_date").iter_rows()
        ),
    }


def _spin_off_held_ids(
    ever_held: Sequence[str], chains: Sequence[tuple[str, str]]
) -> tuple[str, ...]:
    """Include raw predecessor IDs for the late spin-off provider read."""
    mapping = dict(chains)
    held = set(ever_held)
    predecessors = {pred for pred in mapping if held.intersection(_spin_off_path(pred, mapping))}
    return tuple(sorted(held | predecessors))


def _book_targets(
    book: _Book,
    plan: Plan,
    index: int,
    carried: Mapping[str, float],
    cash: float,
    construction: _Construction,
) -> tuple[dict[str, float], set[str]]:
    """The targets one book trades to at the plan's fill, and the names set aside.

    Under `equal` weighting the targets are the plan's. Under `hold` the book's entry
    sessions (`book.entries`, updated here) give the membership at plan `index`: an
    entering name is targeted at `1 / target_names`, an exiting one is left out (sold
    whole) and a live one is set aside at its current weight. With a band the targets
    go through `apply_rebalance_band` against the drifted weights, and every name whose
    effective weight is its current one is set aside too. A set-aside name is never
    traded, which keeps an ulp-sized `target * equity - value` out of the orders."""
    desired = dict(plan.targets)
    aside: set[str] = set()
    equity = math.fsum([cash, *carried.values()])
    current = (
        drifted_weights(carried, cash)
        if equity > 0 and (construction.band > 0 or construction.weighting == "hold")
        else {}
    )
    if construction.weighting == "hold":
        ranked = _ranked(plan.scores)
        membership = hold_membership(
            ranked,
            book.entries,
            index,
            construction.hold_sessions,
            construction.hold_target_names,
            construction.top_fraction,
        )
        book.entries = dict(membership.entries)
        entering = set(membership.entering)
        count = construction.target_names(len(ranked))
        weight = _equal_weight(count) if count else 0.0
        aside = {sid for sid in membership.held if sid not in entering and sid in current}
        desired = {sid: weight for sid in membership.entering}
        desired.update({sid: current[sid] for sid in aside})
    if construction.band > 0 and current:
        desired = apply_rebalance_band(desired, current, construction.band)
        aside |= {sid for sid, weight in desired.items() if current.get(sid) == weight}
    return desired, aside


def _fill_to_targets(
    carried: Mapping[str, float],
    cash: float,
    targets: Mapping[str, float],
    aside: set[str],
    frame: pl.DataFrame,
    raw: pl.DataFrame,
    plan: Plan,
    book: _Book,
    params: Settings,
    adv: Mapping[str, float],
    *,
    signed: bool = False,
) -> FillResult:
    """`apply_trades` towards `targets`, leaving the `aside` names untouched.

    Without any, exactly the plain call. With some, only the other positions are traded
    and their targets are rescaled from the whole equity to the part `apply_trades` sees
    (`aside` excluded), so the orders are the same ones the whole book would place, the
    turnover is over the whole equity, and the set-aside positions come back unchanged.
    `signed` selects `apply_trades(signed=True)` for a `long_short` book (a signed book
    is refused a band and `hold` weighting, so its `aside` is always empty)."""
    fill_price = params.execution.fill_price
    commissions = Commissions.from_config(params.costs)
    if not aside:
        return apply_trades(
            carried,
            cash,
            targets,
            frame,
            raw,
            plan.fill_session,
            fill_price=fill_price,
            per_side_bps=book.level,
            commissions=commissions,
            spread_bps=params.costs.spread_bps,
            impact_coeff=params.costs.impact_coeff,
            adv=adv,
            signed=signed,
        )
    kept = {sid: value for sid, value in carried.items() if sid in aside}
    traded = {sid: value for sid, value in carried.items() if sid not in aside}
    part = math.fsum([cash, *traded.values()])
    whole = math.fsum([cash, *carried.values()])
    scale = whole / part if part > 0 else 1.0
    fill = apply_trades(
        traded,
        cash,
        {sid: weight * scale for sid, weight in targets.items() if sid not in aside},
        frame,
        raw,
        plan.fill_session,
        fill_price=fill_price,
        per_side_bps=book.level,
        commissions=commissions,
        spread_bps=params.costs.spread_bps,
        impact_coeff=params.costs.impact_coeff,
        adv=adv,
        signed=signed,
    )
    return replace(
        fill,
        positions={**fill.positions, **kept},
        turnover=fill.turnover * part / whole if whole > 0 else 0.0,
    )


def _step(
    book: _Book,
    plan: Plan,
    index: int,
    end: date,
    frame: pl.DataFrame,
    raw: pl.DataFrame,
    dividends: tuple[pl.DataFrame, pl.DataFrame],
    ended: dict[str, date | None],
    params: Settings,
    construction: _Construction,
    accruals: Mapping[date, float] | None,
    spin_offs: tuple[pl.DataFrame, pl.DataFrame] | None = None,
    chains: Sequence[tuple[str, str]] = (),
) -> None:
    """Carry, fill, value and exit one cost level through the period (T_i, T_{i+1}].

    `index` is the plan's place among the rebalance sessions (a hold-mode book's clock);
    `accruals` is the cash accrual of each session after T_0, None for a cash rate of 0.
    `spin_offs` is the step's `(unvalued, late)` spin-off reads, present only under the
    trial's `adjust.apply_spin_offs` (spec amendment #1894, item 5), so a trial without
    the key writes neither count and its rows are byte-identical.

    Under `portfolio.side = "long_short"` the book is signed (spec amendment #1872, items
    4 to 6): the same carries, fills and marks take `signed=True`, a short is a negative
    value kept in the position set and in `held_on`, every session after the plan's read
    pays the borrow fee (the carry window to the fill included) and accrues `rf(s)` on
    free cash only, and an equity that is not positive ends the run with a `ValueError`
    naming the session."""
    signed = params.portfolio.side == "long_short"
    fill_price = params.execution.fill_price
    carried = carry_to_fill(
        book.positions,
        frame,
        plan.session,
        plan.fill_session,
        fill_price=fill_price,
        marked_at=book.marked_at,
        signed=signed,
    )
    held_cash = book.cash
    if accruals is not None:
        if signed:
            # Free cash is cash less the short proceeds, which earn nothing (item 6).
            for session in _sessions(plan.session, plan.fill_session)[1:]:
                held_cash += (held_cash - _short_value(book.positions)) * accruals[session]
        else:
            for session in _sessions(plan.session, plan.fill_session)[1:]:
                held_cash *= 1 + accruals[session]
    borrow_bps = params.costs.borrow_bps_per_year
    if signed and borrow_bps > 0:
        # The carry window (T_i, F_i] is charged too, on the positions held at
        # close(T_i) (`book.positions`): the short pays for every session it is held, so
        # a daily book (whose fill session is the step's end) is not charged nothing, and
        # a monthly book is not charged from the fill only and missing one session each
        # rebalance. The post-fill loop then charges from the fill on (item 6).
        carried_shorts = _shorts(book.positions)
        for session in _sessions(plan.session, plan.fill_session)[1:]:
            held_cash -= borrow_fee(
                carried_shorts, borrow_bps, (session - previous_session(session)).days
            )
    targets, aside = _book_targets(book, plan, index, carried, held_cash, construction)
    adv = (
        adv_by_security(raw, plan.session, params.costs.adv_sessions)
        if params.costs.impact_coeff != 0.0
        else {}
    )
    fill = _fill_to_targets(
        carried, held_cash, targets, aside, frame, raw, plan, book, params, adv, signed=signed
    )
    values = value_positions(
        fill.positions,
        frame,
        plan.fill_session,
        fill_price=fill_price,
        through=end,
        signed=signed,
    )
    if not signed:
        negative = values.filter(pl.col("value") < 0)
        if negative.height:
            sid, session, value = negative.select("security_id", "session", "value").row(0)
            raise ValueError(
                f"negative position value {value} for {sid} on {session}: the backtester is "
                "long-only (ADR 0014, 0015), a negative value would be silently dropped"
            )
    exits = _exits(
        book, fill.positions, values, (frame, raw, ended), plan.fill_session, end, params, adv
    )
    values = _book_exits(values, exits)
    totals = dict(values.group_by("session").agg(pl.col("value").sum()).iter_rows())
    shorts = _shorts_by_session(values) if signed else {}
    cash = fill.cash
    for session in _sessions(plan.fill_session, end):
        if session > plan.fill_session:
            previous = previous_session(session)
            held_shorts = shorts.get(previous, {})
            if accruals is not None:
                if signed:
                    free = cash - _short_value(held_shorts)
                    cash += free * accruals[session]
                else:
                    cash *= 1 + accruals[session]
            if signed and borrow_bps > 0:
                cash -= borrow_fee(held_shorts, borrow_bps, (session - previous).days)
        proceeds = [e.value - e.cost for e in exits if e.session == session]
        cash = math.fsum([cash, *proceeds])
        equity = math.fsum([cash, totals.get(session, 0.0)])
        if signed and equity <= 0:
            raise ValueError(
                f"the book's equity is gone on {session.isoformat()}: equity {equity} is not "
                "positive"
            )
        book.equity.append(EquityRow(STRATEGY_SERIES, book.level, session, equity, cash))
    before = math.fsum([held_cash, *carried.values()])
    # A short's exit value is negative; its turnover contribution is the magnitude.
    exited = math.fsum(abs(e.value) for e in exits)

    dropped, late = dividends
    n_late = sum(
        _held_before(book, sid, ex) for sid, ex in late.select("security_id", "ex_date").iter_rows()
    )
    # `!= 0` keeps a short's negative value, so a short holding counts for a dividend
    # (item 5); under `long_only` the negative-value guard above leaves only zero and
    # positive values there, so it selects exactly what `> 0` did.
    for sid, session, value in values.select("security_id", "session", "value").iter_rows():
        if value != 0:
            book.held_on.setdefault(sid, set()).add(session)
    n_dropped = sum(
        plan.session < ex <= end and _held_before(book, sid, ex)
        for sid, ex in dropped.select("security_id", "ex_date").iter_rows()
    )
    # The two spin-off counts (spec amendment #1894, item 4 and 5): parents held on the
    # ex-date, written only under the opt-in key. A valued spin-off is already in the
    # frame (a dividend in kind, item 2), so it is neither read nor counted.
    counts = _rebalance_counts(plan, book)
    if spin_offs is not None:
        counts = dict(counts)
        counts.update(_spin_off_counts(book, plan.session, end, spin_offs, chains))

    book.cash = cash
    previous_positions = set(book.positions)
    last = values.filter(pl.col("session") == end)
    # `!= 0` keeps a short's negative value (item 4); under `long_only` the guard above
    # already excluded negative values, so it selects exactly what `> 0` did.
    book.positions = {
        sid: value for sid, value in last.select("security_id", "value").iter_rows() if value != 0
    }
    book.held_since = {
        sid: book.held_since[sid] if sid in previous_positions else plan.session
        for sid in book.positions
    }
    book.marked_at = {
        sid: marked
        for sid, marked in last.select("security_id", "marked_at").iter_rows()
        if sid in book.positions
    }
    book.entries = {sid: entered for sid, entered in book.entries.items() if sid in book.positions}
    if book.keep_detail:
        book.values.append(values.select(list(_POSITION_SCHEMA)))
        fill_prices = {t.security_id: t.raw_fill_price for t in fill.trades}
        for sid, weight in sorted(targets.items()):
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
            n_targets=len(targets),
            turnover=fill.turnover + (exited / (2 * before) if before > 0 else 0.0),
            cost_paid=math.fsum([fill.cost_paid, *(e.cost for e in exits)]),
            gap_count_share=plan.gap.count_share,
            gap_size_share=plan.gap.size_share,
            n_missing_fill=len(fill.missing),
            n_delisting_exits=sum(not e.stale for e in exits),
            n_stale_exits=sum(e.stale for e in exits),
            n_dropped_dividends=n_dropped,
            n_late_dividends=n_late,
            counts=counts,
        )
    )


def _rebalance_counts(plan: Plan, book: _Book) -> Mapping[str, int]:
    """The rebalance row's counts: the plan's, plus `n_chained_positions` (#821,
    T187b) on the one step whose read saw the chain and renamed `book`'s
    positions (0 in every other step, so no extra count row is written)."""
    if not book.chained_positions:
        return plan.counts
    counts = dict(plan.counts)
    counts["n_chained_positions"] = book.chained_positions
    book.chained_positions = 0
    return counts


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
    keep_marking_frames: bool = True,
) -> dict[float, BacktestResult]:
    """Run the strategy over the rebalance sessions in `[start, end]` at the frozen
    `schedule.rebalance_cadence`, at every cost level in `cost_levels` (per-side bps;
    commissions from `params.costs`) from one read set: `run_many` with one variant,
    whose failure, shared read or not, is raised as the error itself.
    `keep_marking_frames` is `run_many`'s (False keeps no step's marking frame, #1417).

    `params` is the trial's frozen `Settings` (`hypothesis.load_frozen`, which reads the
    schedule keys through `frozen.frozen_values`). Raises `TypeError` without a
    `TrialHandle` (no id, no run; ADR 0005) and `ValueError` for fewer than two
    rebalance sessions, a bad cost level, a numeric non-zero `backtest.cash_rate` or
    `cash_rate = "store"` without `metrics.risk_free_source = "store"`
    (`refused_cash_rate`), all before any provider call.
    """
    failure: Exception | None = None
    outcome = RunManyResults()
    try:
        outcome = run_many(
            [(params, handle)],
            provider,
            start,
            end,
            cost_levels,
            family=family,
            keep_marking_frames=keep_marking_frames,
        )
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
    if method == "late_dividends" or method == "late_spin_offs":
        return cast(Callable[[datetime, datetime, list[str]], object], getattr(provider, method))(
            t, cast(datetime, args[1]), ids
        )
    read = cast(Callable[[datetime, list[str]], object], getattr(provider, cast(str, method)))
    return read(t, ids)


class _SharedReads:
    """The provider as every variant of a read group sees it within one step: each read
    the group shares is made once, for the union of the variants' ids (`share`), and
    each variant's own request is served from it restricted to its ids. A request no
    shared read covers is made as asked. With `covering` false (a group of one, `run`)
    ordinary reads serve only the request they were made for. Marking reads
    use a cross-step cache when the provider can report newly known inputs;
    otherwise they are made as asked. Every real call's error is a
    `_ReadError`."""

    def __init__(self, provider: DataProvider, *, covering: bool) -> None:
        self._provider = provider
        self._covering = covering
        self._entries: dict[_Key, list[_Entry]] = {}
        self._memo: dict[_Key, object] = {}
        self._marks: tuple[datetime, tuple[str, ...], date, pl.DataFrame] | None = None

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
        value: object
        if key[0] == "marking_prices":
            assert bound is not None
            value = self._real(lambda: self._marking_prices(cast(datetime, key[1]), ids, bound))
        else:
            value = self._real(lambda: _keyed_call(self._provider, key, list(ids), bound))
        self._entries.setdefault(key, []).append(_Entry(tuple(ids), bound, value))

    def _marking_prices(self, t: datetime, ids: Sequence[str], sessions_from: date) -> pl.DataFrame:
        """Extend prior marks only when no newly known input can change them."""
        cache = self._marks
        changed_ids = getattr(self._provider, "marking_changed_ids", None)
        if cache is None or changed_ids is None or t <= cache[0]:
            frame = self._provider.adjusted_prices(t, ids, True, sessions_from=sessions_from)
        else:
            previous_t, previous_ids, previous_from, previous = cache
            changed = set(changed_ids(previous_t, t, ids))
            rebuild = [
                sid
                for sid in ids
                if sid not in previous_ids or sid in changed or sessions_from < previous_from
            ]
            reuse = [sid for sid in ids if sid not in rebuild]
            parts: list[pl.DataFrame] = []
            if reuse:
                new_from = next_session(last_completed_session(previous_t))
                advancing = new_from <= last_completed_session(t)
                kept = previous.filter(
                    pl.col("security_id").is_in(reuse)
                    & (pl.col("session") >= sessions_from)
                    & (pl.col("session") < new_from if advancing else pl.lit(True))
                )
                if advancing:
                    added = self._provider.adjusted_prices(
                        t, reuse, True, sessions_from=max(new_from, sessions_from)
                    )
                    kept = pl.concat([kept, added])
                parts.append(kept)
            if rebuild:
                parts.append(
                    self._provider.adjusted_prices(t, rebuild, True, sessions_from=sessions_from)
                )
            frame = (
                pl.concat(parts).sort("security_id", "session")
                if parts
                else self._provider.adjusted_prices(t, ids, True, sessions_from=sessions_from)
            )
        self._marks = (t, tuple(ids), sessions_from, frame)
        return frame

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

    def chains(self, t: datetime) -> tuple[tuple[str, str], ...]:
        return self._once(("chains", t), lambda: self._provider.chains(t))

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

    def marking_prices(self, t: datetime, ids: Sequence[str], sessions_from: date) -> pl.DataFrame:
        return cast(pl.DataFrame, self._read(("marking_prices", t), ids, sessions_from))

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

    def unvalued_spin_offs(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return cast(pl.DataFrame, self._read(("unvalued_spin_offs", t), ids))

    def late_spin_offs(self, t_prev: datetime, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return cast(pl.DataFrame, self._read(("late_spin_offs", t_prev, t), ids))

    def static_listing_count(self, t: datetime, ids: Sequence[str]) -> int:
        return self._once(
            ("static_listing_count", t, tuple(ids)),
            lambda: self._provider.static_listing_count(t, ids),
        )

    def statement_facts(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return cast(pl.DataFrame, self._read(("statement_facts", t), ids))

    def sics(self, t: datetime, ids: Sequence[str]) -> Mapping[str, int | None]:
        return cast(Mapping[str, int | None], self._read(("sics", t), ids))

    def rates(self, t: datetime, series: str) -> pl.DataFrame:
        # Not an id read: made as asked, once per step for one `(t, series)` (T170b);
        # the engine reads it once, at the cutoff, under `store` only (`_run_group`).
        return self._once(("rates", t, series), lambda: self._provider.rates(t, series))

    def turnover_inputs(
        self, t: datetime, ids: Sequence[str], sessions_from: date
    ) -> TurnoverInputs:
        # Made as asked, once per step for one `(t, ids, sessions_from)` (T165b).
        return self._once(
            ("turnover_inputs", t, tuple(ids), sessions_from),
            lambda: self._provider.turnover_inputs(t, ids, sessions_from),
        )


#: The columns of `DataProvider.rates` (`store.asof.rates_as_of`), for the declaring pass's
#: empty frame.
_RATES_SCHEMA: Final[dict[str, pl.DataType | type[pl.DataType]]] = {
    "series": pl.String,
    "observation_date": pl.Date,
    "value": pl.Float64,
    "vintage_date": pl.Date,
    "known_at": pl.Datetime("us", "UTC"),
}


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

    def chains(self, t: datetime) -> tuple[tuple[str, str], ...]:
        # Not a keyed read: the engine reads it once per step itself, never a
        # signal reader (T187b).
        return self.view.chains(t)

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

    def unvalued_spin_offs(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return self._ask(("unvalued_spin_offs", t), ids)

    def late_spin_offs(self, t_prev: datetime, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return self._ask(("late_spin_offs", t_prev, t), ids)

    def static_listing_count(self, t: datetime, ids: Sequence[str]) -> int:
        return self.view.static_listing_count(t, ids)

    def statement_facts(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        return self._ask(("statement_facts", t), ids)

    def sics(self, t: datetime, ids: Sequence[str]) -> Mapping[str, int | None]:
        self._ask(("sics", t), ids)
        return {}

    def rates(self, t: datetime, series: str) -> pl.DataFrame:
        # Not a keyed read: nothing is declared or read here; the empty frame has the
        # provider's columns (T170b, T170c).
        return pl.DataFrame(schema=_RATES_SCHEMA)

    def turnover_inputs(
        self, t: datetime, ids: Sequence[str], sessions_from: date
    ) -> TurnoverInputs:
        # Not a keyed read: nothing is declared or read here (an empty value the
        # reader only wraps); `_plan` makes the real read once per step (T165b).
        return TurnoverInputs(
            t=t, bars=pl.DataFrame(schema=list(TURNOVER_BAR_COLUMNS)), shares={}, splits={}
        )


@dataclass
class _Variant:
    """One variant's run state between steps."""

    settings: Settings
    handle: TrialHandle
    plan: Plan | None = None
    books: list[_Book] = field(default_factory=list)
    keep_frames: bool = True
    keep_detail: bool = True
    frames: list[StepFrame] = field(default_factory=list)
    last_stitched: dict[str, date] = field(default_factory=dict)
    targets: dict[date, Mapping[str, float]] = field(default_factory=dict)
    rates: pl.DataFrame | None = None
    accruals: dict[date, float] | None = None

    @property
    def trial_id(self) -> int:
        return self.handle.trial_id


def _check_variants(
    variants: Sequence[tuple[Settings, TrialHandle]], cost_levels: Sequence[float], provider: object
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
        cash_rate = params.backtest.cash_rate
        if cash_rate == "store":
            if params.metrics.risk_free_source != "store":
                raise ValueError(
                    "refused_cash_rate: backtest.cash_rate = 'store' needs "
                    "metrics.risk_free_source = 'store' in the same file"
                )
        elif cash_rate != 0:
            raise ValueError(
                f"refused_cash_rate: backtest.cash_rate must be 0.0 or 'store', got {cash_rate}"
            )
    for params, _ in variants:
        if params.strategy.weighting == "hold" and params.schedule.rebalance_cadence != "daily":
            raise ValueError(
                "strategy.weighting = 'hold' needs schedule.rebalance_cadence = 'daily': the "
                "hold clock counts plans, and the spec counts sessions"
            )
    cadences = {params.schedule.rebalance_cadence for params, _ in variants}
    if len(cadences) != 1:
        raise ValueError(f"a read group runs at one cadence, got {sorted(cadences)}")
    # One provider holds one frozen Settings, so its `adjust.apply_spin_offs` decides the
    # group's frames while each variant's own decides its counts (#1894, item 3): a mixed
    # group would count spin-offs it never read, so it is refused before any read.
    spin_offs = {params.adjust.apply_spin_offs for params, _ in variants}
    if len(spin_offs) != 1:
        raise ValueError(
            "a read group reads one provider: every variant needs the same "
            f"adjust.apply_spin_offs, got {sorted(spin_offs)}"
        )
    # The provider's own frozen `Settings` decide its frames; a provider built from other
    # settings would serve frames the variant's counts do not match (#1894, item 3). A
    # provider with no `settings` (a fake) carries no flag to check.
    provider_settings = getattr(provider, "settings", None)
    if provider_settings is not None:
        flag = variants[0][0].adjust.apply_spin_offs
        if provider_settings.adjust.apply_spin_offs != flag:
            raise ValueError(
                "the provider's adjust.apply_spin_offs "
                f"({provider_settings.adjust.apply_spin_offs}) differs from the variants' "
                f"({flag}): its frames would not carry the spin-off factor the counts assume"
            )
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
    each keyed read is made once for their union before any variant plans.

    Each variant's plan carries the buffer (`_apply_buffer`): its `previous_targets` is
    the plan the run loop carries for it, `variant.plan`, which is `None` at the run's
    first plan and the previous step's plan -- already renamed through the live
    successor chains by `_rename_chained` -- at every later one (spec amendment #657,
    items 2 and 4)."""
    if len(group) > 1:
        strategy = signal_for(family)
        cadence = group[0].settings.schedule.rebalance_cadence
        requests = _Requests(view)
        universes: dict[date, list[str]] = {}
        for variant in list(group):
            read = _read_session(session, variant.settings.strategy.signal_delay_sessions, cadence)
            t = read_time(read, cadence)
            if read not in universes:
                universes[read] = sorted(view.universe(t).members["security_id"].to_list())
            try:
                strategy.reader(requests, variant.settings, read, t, universes[read])
            except _ReadError:
                raise
            except Exception as exc:
                on_failure(variant, exc)
        for key, asked in requests.asked.items():
            view.share(key, asked)
    plans: dict[int, Plan] = {}
    for variant in list(group):
        previous = () if variant.plan is None else variant.plan.targets
        try:
            plans[variant.trial_id] = _apply_buffer(
                _plan(view, variant.settings, session, family), variant.settings, previous
            )
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
    keep_marking_frames: bool = True,
    keep_detail: bool = True,
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

    `keep_marking_frames=False` keeps no step's marking frame, so each result's
    `marking_frames` is empty and memory stays flat over a long run (#1414: a daily
    group's frames, with the Arrow buffers they pin, grew without bound). Nothing else
    in the results changes; only the `bt` oracle (`BacktestResult.stitched_returns`)
    reads the frames.

    `keep_detail=False` keeps no position values and no weight rows at any level, so
    each result's `position_values` is empty (with its schema) and its `weights` is
    `()` (#1448: a summary-level sweep writes neither). Nothing else changes.
    """
    signal_for(family)
    levels = _check_variants(variants, cost_levels, provider)
    cadence = variants[0][0].schedule.rebalance_cadence
    sessions = rebalance_sessions(start, end, cadence)
    if len(sessions) < 2:
        raise ValueError(f"need at least two rebalance sessions in [{start}, {end}]")

    out = RunManyResults()
    group = [
        _Variant(params, handle, keep_frames=keep_marking_frames, keep_detail=keep_detail)
        for params, handle in variants
    ]

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
                rates=variant.rates,
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
    # The one risk-free read, at the run's data cutoff and only under `store` (backtest
    # spec, amendment 2026-10-11): `constant` never touches the provider's `rates`. A
    # cash rate of `store` needs it before the first step, to accrue cash as it goes.
    cutoff = read_time(sessions[-1], cadence)
    read: dict[str, pl.DataFrame] = {}

    def rates_for(series: str) -> pl.DataFrame:
        if series not in read:
            read[series] = view.rates(cutoff, series)
        return read[series]

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
                keep_detail=variant.keep_detail,
            )
            for level in levels
        ]
        for book in variant.books:
            book.equity.append(
                EquityRow(STRATEGY_SERIES, book.level, sessions[0], capital, capital)
            )
            for name in benchmarks:
                book.equity.append(EquityRow(name, book.level, sessions[0], capital, None))
        if variant.settings.backtest.cash_rate == "store":
            try:
                variant.accruals = _cash_accruals(variant.settings, rates_for, sessions)
            except RiskFreeMissing as exc:
                failed(variant, exc)

    for index, step_end in enumerate(sessions[1:], start=1):
        if not group:
            return
        view.new_step()
        t = read_time(step_end, cadence)
        # #821 (T187b): the live successor chains known at this step's read, applied
        # to every variant's books and plans before the marking ids are built, so a
        # held predecessor becomes its successor at one point (one share for one
        # share) and the step's reads name the successor.
        chains = view.chains(t)
        if chains:
            for variant in group:
                _rename_chained(variant, chains)
        own = [_marking_ids(variant, benchmarks) for variant in group]
        t_prev = read_time(cast(Plan, group[0].plan).session, cadence)
        view.share(
            ("marking_prices", t),
            [
                (marked, _marking_bound(variant, step_end, family, marked))
                for variant, (_, marked, _) in zip(group, own, strict=True)
            ],
        )
        view.share(("raw_prices", t), [(marked, None) for _, marked, _ in own])
        view.share(("listing_ends", t), [(ids, None) for ids, _, _ in own])
        view.share(("dropped_dividends", t), [(ids, None) for ids, _, _ in own])
        view.share(("late_dividends", t_prev, t), [(held, None) for _, _, held in own])
        # A spin-off read happens only under the opt-in key (#1894, item 3), so a group
        # with no `adjust.apply_spin_offs = true` variant makes none and stays byte-identical.
        if any(variant.settings.adjust.apply_spin_offs for variant in group):
            view.share(("unvalued_spin_offs", t), [(ids, None) for ids, _, _ in own])
            view.share(
                ("late_spin_offs", t_prev, t),
                [(_spin_off_held_ids(held, chains), None) for _, _, held in own],
            )
        last = index == len(sessions) - 1
        next_plans = {} if last else _plan_all(view, group, step_end, family, failed)
        for variant in list(group):
            try:
                _variant_step(
                    view,
                    variant,
                    index - 1,
                    step_end,
                    benchmarks,
                    next_plans.get(variant.trial_id),
                    family,
                    chains,
                )
            except _ReadError:
                raise
            except Exception as exc:
                failed(variant, exc)
    for variant in group:
        metrics = variant.settings.metrics
        if metrics.risk_free_source == "store":
            variant.rates = rates_for(metrics.risk_free_series)


def _cash_accruals(
    params: Settings, rates_for: Callable[[str], pl.DataFrame], sessions: Sequence[date]
) -> dict[date, float]:
    """The accrual of each session after T_0 (`riskfree.session_accruals`) from the
    frame read at the data cutoff, filtered to what was known at each session's close.
    Raises `RiskFreeMissing` for the first session with no rate known at its close."""
    metrics = params.metrics
    series = metrics.risk_free_series
    every = _sessions(sessions[0], sessions[-1])
    rows = rates_for(series).filter(pl.col("series") == series)
    fractions = rows.select("known_at", "observation_date", "value").iter_rows()
    published = published_by_session(
        (
            (known_at, observed, value / PERCENT_POINTS_PER_UNIT)
            for known_at, observed, value in fractions
        ),
        every,
        session_close,
    )
    yields = {s: to_yield(rate, metrics.risk_free_basis) for s, rate in published.items()}
    return session_accruals(every, yields, series=series)


def _rename_chained(variant: _Variant, chains: Sequence[tuple[str, str]]) -> None:
    """Rename a variant's books and plans through the live successor chains
    (#821, T187b): each book's positions (one share for one share, summing a
    collision), `held_on`, `held_since`, `entries` and `marked_at` keys, its
    plan's targets, the targets it recorded per fill session, its last-stitched
    sessions, and its benchmarks' holdings. `chained_positions` counts the
    positions renamed for the book's next rebalance row; the map is idempotent,
    so a later step re-reads no renamed name. The engine's `_marking_ids` and
    reads then name the successor."""
    mapping = dict(chains)
    plan = variant.plan
    if plan is not None:
        targets = {mapping.get(sid, sid): weight for sid, weight in plan.targets.items()}
        if targets != plan.targets:
            variant.plan = replace(plan, targets=targets)
    variant.last_stitched = {
        mapping.get(sid, sid): session for sid, session in variant.last_stitched.items()
    }
    variant.targets = {
        session: {mapping.get(sid, sid): weight for sid, weight in targets.items()}
        for session, targets in variant.targets.items()
    }
    for book in variant.books:
        positions: dict[str, float] = {}
        renamed = 0
        for sid, value in book.positions.items():
            successor = mapping.get(sid, sid)
            renamed += successor != sid
            positions[successor] = positions.get(successor, 0.0) + value
        book.positions = positions
        book.chained_positions += renamed
        book.marked_at = {mapping.get(sid, sid): day for sid, day in book.marked_at.items()}
        held_since: dict[str, date] = {}
        for sid, day in book.held_since.items():
            successor = mapping.get(sid, sid)
            held_since[successor] = min(day, held_since.get(successor, day))
        book.held_since = held_since
        entries: dict[str, int] = {}
        for sid, count in book.entries.items():
            successor = mapping.get(sid, sid)
            entries[successor] = count + entries.get(successor, 0)
        book.entries = entries
        held_on: dict[str, set[date]] = {}
        for sid, days in book.held_on.items():
            held_on.setdefault(mapping.get(sid, sid), set()).update(days)
        book.held_on = held_on
        for holding in book.benchmarks.values():
            holding.security_id = mapping.get(holding.security_id, holding.security_id)


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


def _marking_bound(
    variant: _Variant, step_end: date, family: HypothesisFamily, marked: Sequence[str]
) -> date:
    """Earliest held formation anchor or stale-exit window, including last marks."""
    plan = cast(Plan, variant.plan)
    settings = variant.settings
    sessions = all_sessions()
    at = bisect.bisect_right(sessions, step_end) - 1
    stale = sessions[max(0, at - settings.backtest.stale_exit_sessions)]
    bounds = [stale, plan.session]
    bounds.extend(variant.last_stitched[sid] for sid in marked if sid in variant.last_stitched)
    if family in ("momentum", "combined"):

        def formation(session: date) -> date:
            return anchor_sessions(
                session,
                settings.strategy.formation_months,
                settings.strategy.skip_months,
                settings.schedule.signal_anchor,
            )[0]

        bounds.append(formation(plan.session))
    for book in variant.books:
        bounds.extend(book.marked_at.values())
        bounds.extend(book.held_since.values())
        bounds.extend(
            holding.marked_at
            for holding in book.benchmarks.values()
            if holding.marked_at is not None
        )
        if family in ("momentum", "combined"):
            bounds.extend(formation(since) for since in book.held_since.values())
    return min(bounds)


def _variant_step(
    view: _SharedReads,
    variant: _Variant,
    index: int,
    step_end: date,
    benchmarks: Mapping[str, str],
    next_plan: Plan | None,
    family: HypothesisFamily,
    chains: Sequence[tuple[str, str]],
) -> None:
    """One variant's step through `step_end` from the shared reads, restricted to its
    own ids: carry, fill, value and exit every level, then take `next_plan`. `index` is
    the current plan's place among the rebalance sessions."""
    plan = cast(Plan, variant.plan)
    params = variant.settings
    cadence = params.schedule.rebalance_cadence
    t, t_prev = read_time(step_end, cadence), read_time(plan.session, cadence)
    ids, marked, ever_held = (list(part) for part in _marking_ids(variant, benchmarks))
    frame = view.marking_prices(t, marked, _marking_bound(variant, step_end, family, marked))
    raw = view.raw_prices(t, marked)
    ended = _ended(view.listing_ends(t, ids), step_end)
    dropped = view.dropped_dividends(t, ids)
    late = view.late_dividends(t_prev, t, ever_held)
    spin_offs: tuple[pl.DataFrame, pl.DataFrame] | None = None
    if params.adjust.apply_spin_offs:
        # The late reader selects raw `source_action_id` parents. Keep the
        # predecessor in this spin-off-only read after the book is renamed.
        spin_held = _spin_off_held_ids(ever_held, chains)
        spin_offs = (
            view.unvalued_spin_offs(t, ids),
            view.late_spin_offs(t_prev, t, spin_held),
        )
    if next_plan is not None:
        next_plan = _no_benchmark_members(next_plan, benchmarks)
    if variant.keep_frames:
        variant.frames.append(StepFrame(start=plan.session, end=step_end, frame=frame))
    for sid, session in (
        frame.filter((pl.col("session") > plan.session) & (pl.col("session") <= step_end))
        .group_by("security_id")
        .agg(pl.col("session").max())
        .iter_rows()
    ):
        variant.last_stitched[sid] = session
    variant.targets[plan.fill_session] = dict(plan.targets)
    construction = _construction(params, family)
    for book in variant.books:
        _step(
            book,
            plan,
            index,
            step_end,
            frame,
            raw,
            (dropped, late),
            ended,
            params,
            construction,
            variant.accruals,
            spin_offs,
            chains,
        )
        _benchmark_step(book, plan, step_end, frame, raw, params)
    if next_plan is not None:
        variant.plan = next_plan
