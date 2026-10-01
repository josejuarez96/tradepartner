"""The planning step of a tracking run (Phase 4 spec req 7 step 6, Definitions >
Plan trial; plan T63c).

**`rebalance_kind`** names the run's planning kind on session S: `rebalance`
when S is the fill session F_i of a rebalance T_i of the window, `catch_up`
while T_i is pending (no `executed` or `missed` event of the window's runs)
and S is at most the frozen `paper.max_catch_up_sessions` sessions after F_i,
else None. **`due_rebalance`** returns the same with its T_i.

**`plan_rebalance`** plans T_i once:

1. **Reads, before any write.** The journaled decisions of T_i: when there
   are any, a re-run or catch-up re-uses them and never re-plans (`reused`).
   With `lagging` (an order is `fills_lagging`) nothing is planned and the
   rebalance stays pending (`lagging`). Otherwise: the window's hypothesis and
   its frozen parameters; the ledger stated for S, with the splits known at
   close(S-1); the reference prices; the listings ended at close(S-1); the
   open or in-flight `forced_exit` decisions; the overrides; and the first
   `assets` read, for the held names.
2. **One write transaction** on `conn`, committed or rolled back as a whole:
   the plan trial (`registry.open_trial`, `kind=tracking`, window [T_i, T_i],
   data cutoff read_time(T_i)) is opened first, because `StoreProvider` reads
   only under an open trial (Definitions > Plan trial: every engine read sits
   under a trial); `engine.plan` reads at close(T_i) over a `StoreProvider` on
   the same connection, so it sees the uncommitted trial row; the second
   `assets` read, for the targets not held; `plan.decisions_from`; then the
   `signals`, `decisions` and `paper_plans` rows; then the trial's `ok`
   result with no statistics, and the commit. The plan line asks for every
   read before the transaction; the engine's reads cannot be, since the
   provider refuses a handle whose trial row does not exist, so they sit
   inside it. What the line protects still holds: a fault in any read rolls
   back only this step's uncommitted rows, never the halt rows the wrapper
   writes on its own connection, and no partial decision set is ever
   committed.

**Exits.** A failure of the plan trial itself (the registry's open or
result, the provider's construction, the tracking-window rule, or
`engine.plan` raising anything that is not a `SystemFaultError` or
`StaleDataError`) raises `PlanTrialError`, deliberately not a
`SystemFaultError`, so the run ends `failed` through its any-other-exception
path, never the halt path. A `SystemFaultError` or `StaleDataError` raised
inside the step, and any exception of `assets_read` whatever its type (that
read goes through the wrapper's classification, so a halt is under way), is
re-raised unchanged. Every exit rolls the transaction back first, so it leaves
no trial, `signals`, `decisions` or `paper_plans` row.

This module imports no adapter (the `assets` reads go through `assets_read`),
calls no broker and reads no clock: rows are stamped with `now`, the run's
clock reading, which the caller takes. `conn` is a write connection with no
transaction open.
"""

from __future__ import annotations

import bisect
import json
import math
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from functools import partial
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from tradepartner.backtest import engine
from tradepartner.backtest.costs import Commissions
from tradepartner.backtest.holdout import Flags, Frozen, Reasons, Window, decide
from tradepartner.backtest.hypothesis import load_frozen
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import all_sessions, is_session, previous_session, session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.errors import StaleDataError, SystemFaultError
from tradepartner.execution.ledger import Ledger, from_journal
from tradepartner.execution.plan import (
    AssetFlags,
    BuyCosts,
    Decisions,
    State,
    decision_state,
    decisions_from,
)
from tradepartner.store import journal as store_journal
from tradepartner.store import registry
from tradepartner.store.asof import listings_as_of, live_actions_as_of, prices_as_of
from tradepartner.store.delistings import listing_ends_as_of
from tradepartner.store.journal import (
    DecisionRow,
    JournalRow,
    PaperPlanRow,
    PaperRunRow,
    PaperWindowRow,
    RebalanceEventRow,
)

__all__ = [
    "AssetsRead",
    "Journal",
    "PlanOutcome",
    "PlanTrialError",
    "RebalanceKind",
    "due_rebalance",
    "frozen_max_catch_up_sessions",
    "plan_rebalance",
    "rebalance_kind",
    "reference_prices",
]

RebalanceKind = Literal["rebalance", "catch_up"]
#: `symbols -> {symbol: flags}`: the broker's `assets` read, through the wrapper.
AssetsRead = Callable[[Sequence[str]], Mapping[str, AssetFlags]]

_REBALANCE: RebalanceKind = "rebalance"
_CATCH_UP: RebalanceKind = "catch_up"
_TRACKING: Literal["tracking"] = "tracking"
_RUN = "run"
_OK = "ok"
_SPLIT = "split"
_LISTED = "listed"
_FORCED_EXIT = "forced_exit"
_RUN_BY = "paper run"
_MAX_CATCH_UP_KEY = "paper.max_catch_up_sessions"
_LIVE_STATES = frozenset({State.OPEN, State.IN_FLIGHT})
_NEW_YORK = ZoneInfo("America/New_York")


class PlanTrialError(RuntimeError):
    """The plan trial failed (module docstring); deliberately not a
    `SystemFaultError`, so the run ends `failed` and never halts for it."""


class Journal(Protocol):
    """The journal writer the step appends through (`store.journal` fits)."""

    def append(self, conn: duckdb.DuckDBPyConnection, row: JournalRow) -> int | None:
        """Insert `row` in the caller's transaction and return its id."""
        ...


@dataclass(frozen=True)
class PlanOutcome:
    """What the step did for rebalance `rebalance_session`: `planned` (this
    call journaled the decisions under trial `plan_trial_id`), `reused` (the
    journal already held them) or `lagging` (no plan; the rebalance stays
    pending). `decisions` are the journaled rows, ids included."""

    rebalance_session: date
    status: Literal["planned", "reused", "lagging"]
    decisions: tuple[DecisionRow, ...] = ()
    plan_trial_id: int | None = None


# --- the kind ------------------------------------------------------------------------


def frozen_max_catch_up_sessions(window: PaperWindowRow) -> int:
    """The window's frozen `paper.max_catch_up_sessions` from `frozen_json`."""
    try:
        parsed = json.loads(window.frozen_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"window {window.window_id} frozen_json is not JSON") from exc
    value = parsed.get(_MAX_CATCH_UP_KEY) if isinstance(parsed, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            f"window {window.window_id} frozen_json has no non-negative integer "
            f"{_MAX_CATCH_UP_KEY} (got {value!r})"
        )
    return value


def _check_session(session: date) -> None:
    if isinstance(session, datetime) or not isinstance(session, date) or not is_session(session):
        raise ValueError(f"session must be an XNYS session date, got {session!r}")


def _sessions_after(first: date, last: date) -> int:
    """How many sessions `last` is after `first` (both sessions)."""
    sessions = all_sessions()
    return bisect.bisect_left(sessions, last) - bisect.bisect_left(sessions, first)


def _latest_due(window: PaperWindowRow, session: date) -> date | None:
    """The latest rebalance T_i of the window whose fill session is on or before S."""
    due = [
        t
        for t in rebalance_sessions(window.first_rebalance_session, session)
        if fill_session(t) <= session
    ]
    return due[-1] if due else None


def due_rebalance(
    window: PaperWindowRow,
    runs: Sequence[PaperRunRow],
    rebalance_events: Sequence[RebalanceEventRow],
    session: date,
    max_catch_up_sessions: int,
) -> tuple[RebalanceKind, date] | None:
    """The run's planning kind on S with its T_i, or None (module docstring).

    Only the latest rebalance due by S can be planned: an earlier one has a
    later rebalance's fill session between it and S, by which time it lapsed.
    `runs` places the events in the window (`run_id` -> `window_id`); an event
    of a run not among them raises, so a short run list cannot hide one."""
    _check_session(session)
    if isinstance(max_catch_up_sessions, bool) or max_catch_up_sessions < 0:
        raise ValueError(f"max_catch_up_sessions is {max_catch_up_sessions!r}")
    t_i = _latest_due(window, session)
    if t_i is None:
        return None
    f_i = fill_session(t_i)
    if session == f_i:
        return _REBALANCE, t_i
    windows = {run.run_id: run.window_id for run in runs}
    for event in rebalance_events:
        if event.run_id not in windows:
            raise ValueError(f"rebalance event of run {event.run_id}, which is not among the runs")
        if windows[event.run_id] == window.window_id and event.rebalance_session == t_i:
            return None  # executed or missed
    if _sessions_after(f_i, session) <= max_catch_up_sessions:
        return _CATCH_UP, t_i
    return None


def rebalance_kind(
    window: PaperWindowRow,
    runs: Sequence[PaperRunRow],
    rebalance_events: Sequence[RebalanceEventRow],
    session: date,
    max_catch_up_sessions: int,
) -> RebalanceKind | None:
    """`rebalance`, `catch_up` or None on session S (`due_rebalance`'s kind)."""
    due = due_rebalance(window, runs, rebalance_events, session, max_catch_up_sessions)
    return None if due is None else due[0]


# --- reads -----------------------------------------------------------------------------


def _cut(session: date) -> datetime:
    """close(S-1): the latest instant a fact read for S may be known at."""
    return session_close(previous_session(session))


def reference_prices(
    conn: duckdb.DuckDBPyConnection,
    session: date,
    names: Collection[str],
    actions_as_of: pl.DataFrame,
) -> dict[str, float]:
    """The reference price on S per name (spec Definitions > Reference price):
    the close of its latest bar on or before S-1 known at close(S-1), divided
    by the split ratios with ex-date in (S-1, S] in `actions_as_of` (read at
    close(S-1)). A name with no such bar raises `ValueError`."""
    _check_session(session)
    if not names:
        return {}
    previous = previous_session(session)
    bars = prices_as_of(conn, _cut(session), sorted(names)).filter(pl.col("session") <= previous)
    latest = {
        row["security_id"]: float(row["close"])
        for row in bars.sort("session").group_by("security_id").last().iter_rows(named=True)
    }
    missing = sorted(set(names) - set(latest))
    if missing:
        raise ValueError(f"no bar on or before {previous} known at close(S-1) for {missing}")
    prices: dict[str, float] = {}
    for sid, close in latest.items():
        factor = 1.0
        for row in actions_as_of.iter_rows(named=True):
            if (
                row["security_id"] == sid
                and row["action_type"] == _SPLIT
                and previous < row["ex_date"] <= session
            ):
                factor *= float(row["ratio_or_amount"])
        price = close / factor
        if not (math.isfinite(price) and price > 0):
            raise ValueError(f"reference price of {sid} is {price}")
        prices[sid] = price
    return prices


def _current(listings: pl.DataFrame, day: date) -> dict[str, dict[str, Any]]:
    """Per security, its listing row with the latest `valid_from` on or before `day`
    (the first in frame order on a tie, as `universe_as_of` reads it)."""
    current: dict[str, dict[str, Any]] = {}
    for row in listings.iter_rows(named=True):
        valid_from = row["valid_from"]
        if valid_from is not None and valid_from > day:
            continue
        held = current.get(row["security_id"])
        if held is None or (
            valid_from is not None
            and (held["valid_from"] is None or valid_from > held["valid_from"])
        ):
            current[row["security_id"]] = row
    return current


def _ended(
    conn: duckdb.DuckDBPyConnection, session: date, names: Sequence[str], params: Settings
) -> dict[str, date | None]:
    """Names whose current listing at close(S-1) is not `listed`, each with its
    end session (None when unknown): `decisions_from`'s `listings_at`."""
    if not names:
        return {}
    frame = listing_ends_as_of(conn, _cut(session), params, list(names))
    current = _current(frame, previous_session(session))
    return {sid: row["end_session"] for sid, row in current.items() if row["status"] != _LISTED}


def _symbols(
    conn: duckdb.DuckDBPyConnection, session: date, names: Sequence[str]
) -> dict[str, str]:
    """Each name's ticker: its current listing at S among rows known at close(S-1).
    A name with none raises, since its `assets` read cannot be made."""
    if not names:
        return {}
    current = _current(listings_as_of(conn, _cut(session), list(names)), session)
    missing = sorted(set(names) - set(current))
    if missing:
        raise ValueError(f"no listing known at close(S-1) for {missing}")
    symbols = {sid: str(current[sid]["ticker"]) for sid in names}
    if len(set(symbols.values())) != len(symbols):
        raise ValueError(f"two names share a ticker: {symbols}")
    return symbols


def _assets(assets_read: AssetsRead, symbols: Mapping[str, str]) -> dict[str, AssetFlags]:
    """The `assets` read for `symbols`' names, keyed by `security_id`. Whatever
    `assets_read` raises propagates unchanged; a symbol it does not answer for
    raises `ValueError` after the call."""
    if not symbols:
        return {}
    answer = assets_read(sorted(symbols.values()))
    missing = sorted(sym for sym in symbols.values() if sym not in answer)
    if missing:
        raise ValueError(f"the assets read did not answer for {missing}")
    return {sid: answer[symbol] for sid, symbol in symbols.items()}


def _ledger(
    conn: duckdb.DuckDBPyConnection,
    window: PaperWindowRow,
    window_id: int,
    session: date,
    actions: pl.DataFrame,
    frozen: RiskConfig,
) -> Ledger:
    ok = [
        r
        for r in store_journal.reconciliations_for(conn, window_id)
        if r.status == _OK and r.at.astimezone(_NEW_YORK).date() <= session
    ]
    return from_journal(
        store_journal.fills_for(conn, window_id=window_id),
        store_journal.orders_for(conn, window_id=window_id),
        store_journal.adjustments_for(conn, window_id),
        actions,
        ok[-1] if ok else None,
        window.starting_cash,
        session,
        window_id=window_id,
        quantity_tolerance=frozen.reconcile_quantity_tolerance,
    )


def _open_forced_exits(
    conn: duckdb.DuckDBPyConnection,
    window_id: int,
    session: date,
    actions: pl.DataFrame,
    frozen: RiskConfig,
    price_of: Callable[[str], float],
) -> frozenset[str]:
    """Names with an open or in-flight `forced_exit` decision in the window."""
    exits = [
        d
        for d in store_journal.decisions_for(conn, window_id)
        if d.decision.decision == _FORCED_EXIT
    ]
    if not exits:
        return frozenset()
    orders = store_journal.orders_for(conn, window_id=window_id)
    events = store_journal.order_events_for(conn, window_id=window_id)
    fills = store_journal.fills_for(conn, window_id=window_id)
    live: set[str] = set()
    for exit_ in exits:
        state = decision_state(
            exit_.decision,
            exit_.events,
            orders,
            events,
            fills,
            actions,
            price_of,
            frozen,
            session=session,
        )
        if state.state in _LIVE_STATES:
            live.add(exit_.decision.security_id)
    return frozenset(live)


@contextmanager
def _lend(conn: duckdb.DuckDBPyConnection) -> Iterator[duckdb.DuckDBPyConnection]:
    """`conn` as a `StoreProvider` connection factory: lent, never closed."""
    yield conn


def _trial[R](step: Callable[[], R], what: str) -> R:
    """Run one part of the plan trial: a `SystemFaultError` or `StaleDataError`
    passes unchanged, anything else becomes `PlanTrialError`."""
    try:
        return step()
    except (SystemFaultError, StaleDataError):
        raise
    except Exception as exc:
        raise PlanTrialError(f"plan trial failed at {what}: {exc}") from exc


@dataclass(frozen=True)
class _Reads:
    """Everything step 1 read for the transaction."""

    hypothesis: registry.HypothesisRecord
    params: Settings
    ledger: Ledger
    actions: pl.DataFrame
    prices: dict[str, float]
    ended: dict[str, date | None]
    forced: frozenset[str]
    overrides: tuple[store_journal.OverrideRow, ...]
    held_assets: dict[str, AssetFlags]


def _frozen_params(
    conn: duckdb.DuckDBPyConnection, hypothesis_id: int, settings: Settings
) -> tuple[registry.HypothesisRecord, Settings]:
    """The window's hypothesis and its frozen parameters over `settings`. A
    hypothesis re-registered since the window started has a different latest
    registration, whose parameters this window never froze: refused."""
    record = registry.get_hypothesis_by_id(conn, hypothesis_id)
    latest = registry.get_hypothesis(conn, record.slug)
    if latest.hypothesis_id != hypothesis_id:
        raise ValueError(
            f"hypothesis {record.slug!r} was re-registered as {latest.hypothesis_id} after "
            f"the window froze registration {hypothesis_id}"
        )
    return record, load_frozen(conn, record.slug, settings=settings)


def _read(
    conn: duckdb.DuckDBPyConnection,
    window: PaperWindowRow,
    window_id: int,
    session: date,
    settings: Settings,
    frozen: RiskConfig,
    assets_read: AssetsRead,
) -> _Reads:
    hypothesis, params = _trial(
        lambda: _frozen_params(conn, window.hypothesis_id, settings), "the frozen parameters"
    )
    actions = live_actions_as_of(conn, _cut(session))
    ledger = _ledger(conn, window, window_id, session, actions, frozen)
    held = sorted(sid for sid, quantity in ledger.positions.items() if quantity > 0)
    exits = sorted(
        d.decision.security_id
        for d in store_journal.decisions_for(conn, window_id)
        if d.decision.decision == _FORCED_EXIT
    )
    prices = reference_prices(conn, session, {*held, *exits}, actions)
    forced = _open_forced_exits(conn, window_id, session, actions, frozen, prices.__getitem__)
    overrides = tuple(o.override for o in store_journal.overrides_for(conn, window_id))
    held_assets = _assets(assets_read, _symbols(conn, session, held))
    return _Reads(
        hypothesis=hypothesis,
        params=params,
        ledger=ledger,
        actions=actions,
        prices=prices,
        ended=_ended(conn, session, held, params),
        forced=forced,
        overrides=overrides,
        held_assets=held_assets,
    )


def _open_trial(
    conn: duckdb.DuckDBPyConnection,
    reads: _Reads,
    t_i: date,
    settings: Settings,
) -> registry.TrialHandle:
    window = Window(t_i, t_i)
    verdict = decide(
        window,
        Frozen.from_hypothesis(reads.hypothesis),
        Flags(),
        Reasons(),
        None,
        (),
        tracking=True,
    )
    if verdict.outcome != _RUN:
        raise ValueError(verdict.message)
    return registry.open_trial(
        conn,
        hypothesis_id=reads.hypothesis.hypothesis_id,
        kind=_TRACKING,
        start_session=t_i,
        end_session=t_i,
        data_cutoff=read_time(t_i),
        synthetic=False,
        run_by=_RUN_BY,
        settings=settings,
    )


def _plan_at(
    conn: duckdb.DuckDBPyConnection, handle: registry.TrialHandle, params: Settings, t_i: date
) -> engine.Plan:
    lend = partial(_lend, conn)
    with StoreProvider(lend, handle, params, registry_connect=lend) as provider:
        return engine.plan(provider, params, t_i)


def _close_ok(conn: duckdb.DuckDBPyConnection, handle: registry.TrialHandle) -> None:
    status = registry.write_result(conn, handle, registry.ResultStatistics())
    if status != _OK:
        raise ValueError(f"the plan trial closed {status!r}: the store changed under it")


def _write(
    conn: duckdb.DuckDBPyConnection,
    journal: Journal,
    run_id: int,
    t_i: date,
    plan: engine.Plan,
    decided: Decisions,
    handle: registry.TrialHandle,
    now: datetime,
) -> tuple[DecisionRow, ...]:
    for signal in decided.signals:
        journal.append(conn, signal.row(run_id=run_id, known_at=now, ingested_at=now))
    rows: list[DecisionRow] = []
    for decision in decided.decisions:
        row = decision.row(run_id=run_id, known_at=now, ingested_at=now)
        decision_id = journal.append(conn, row)
        rows.append(DecisionRow(**{**row.__dict__, "decision_id": decision_id}))
    journal.append(
        conn,
        PaperPlanRow(
            run_id=run_id,
            plan_trial_id=handle.trial_id,
            rebalance_session=t_i,
            store_max_ingested_at=handle.store_max_ingested_at,
            n_universe=plan.n_universe,
            n_targets=len(plan.targets),
            n_orders_below_min_at_live_capital=decided.n_orders_below_min_at_live_capital,
            known_at=now,
            ingested_at=now,
        ),
    )
    return tuple(rows)


def plan_rebalance(
    conn: duckdb.DuckDBPyConnection,
    journal: Journal,
    window: PaperWindowRow,
    run: PaperRunRow,
    session: date,
    settings: Settings,
    frozen: RiskConfig,
    assets_read: AssetsRead,
    lagging: bool,
    *,
    now: datetime,
) -> PlanOutcome:
    """Plan the rebalance due on S = `session` (module docstring).

    T_i is the latest rebalance of the window whose fill session is on or
    before S; call this only when `rebalance_kind` is not None. `run` is the
    planning run (its `run_id` keys every row); `settings` the live settings,
    which the hypothesis's frozen parameters overlay for the plan; `frozen`
    the window's frozen `risk.*` section; `lagging` true while any order is
    `fills_lagging`; `now` the run's clock reading, stamped on every row.
    Raises `PlanTrialError`, or re-raises a fault or an `assets_read` error
    unchanged, after rolling back (module docstring)."""
    _check_session(session)
    if window.window_id is None or run.run_id is None:
        raise ValueError("the window and the run must be rows read from the store")
    if run.window_id != window.window_id:
        raise ValueError(f"run {run.run_id} is not of window {window.window_id}")
    t_i = _latest_due(window, session)
    if t_i is None:
        raise ValueError(f"no rebalance of window {window.window_id} is due on {session}")
    journaled = store_journal.decisions_for(conn, window.window_id, rebalance_session=t_i)
    if journaled:
        return PlanOutcome(t_i, "reused", tuple(d.decision for d in journaled))
    if lagging:
        return PlanOutcome(t_i, "lagging")
    reads = _read(conn, window, window.window_id, session, settings, frozen, assets_read)
    conn.begin()
    try:
        handle = _trial(lambda: _open_trial(conn, reads, t_i, settings), "its open")
        plan = _trial(lambda: _plan_at(conn, handle, reads.params, t_i), "engine.plan")
        held = set(reads.held_assets)
        new = sorted(set(plan.targets) - held)
        assets = {**reads.held_assets, **_assets(assets_read, _symbols(conn, session, new))}
        prices = {**reads.prices, **reference_prices(conn, session, new, reads.actions)}
        listings_at = {**reads.ended, **_ended(conn, session, new, reads.params)}
        costs = reads.params.costs
        decided = decisions_from(
            plan,
            reads.ledger,
            reads.overrides,
            assets,
            listings_at,
            reads.forced,
            frozen,
            settings,
            price_of=prices.__getitem__,
            actions_as_of=reads.actions,
            costs=BuyCosts(costs.per_side_bps, Commissions.from_config(costs)),
        )
        rows = _write(conn, journal, run.run_id, t_i, plan, decided, handle, now)
        _trial(lambda: _close_ok(conn, handle), "its result")
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
    return PlanOutcome(t_i, "planned", rows, handle.trial_id)
