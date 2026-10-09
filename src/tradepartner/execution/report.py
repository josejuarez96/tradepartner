"""Tracking comparison, monthly decomposition (Phase 4 spec req 10, ADR 0005
check 1; plan T65).

`compare_months(window, trial, journal, actions, prices, closes,
stop_session) -> MonthlyComparison` is pure: no clock, no store, no live
`Settings` read. Per rebalance month i (consecutive sessions
`trial.sessions[i]` = T_i and `trial.sessions[i + 1]` = T_{i+1}) it
computes:

- the **raw difference**: paper's return (ledger equity at close(T_{i+1})
  over close(T_i), both from `journal.positions_daily`) minus the trial's
  month return (`trial.equity[T_{i+1}] / trial.equity[T_i] - 1`);
- the **dividend term**: over equity at close(T_i), the sum over
  `corporate_actions` dividend rows with `ex_date` in (T_i, T_{i+1}] and
  `known_at <= close(T_{i+1})` of amount x the ledger quantity held on the
  ex-date (`positions_daily`), excluding any dividend the broker already
  credited (an `adjustments` row of kind `dividend_cash` for the same name
  and session, req 6: paper got the cash already, so counting it again would
  double it);
- the **fill-timing term**: over equity at close(T_i), [sum over buys of
  (the paper fill price - the frozen `execution.fill_price` bar price on the
  fill's session) x filled quantity, less the same sum over sells], so a buy
  that paid more or a sell that received less than the bar makes the term
  positive (`prices` is the caller's bound accessor for that frozen bar: this
  module never reads `Settings.execution.fill_price` itself: the caller binds
  `prices` once from the window's frozen key, req 14, #366 Q20, #526);
- the **residual**: raw + dividend term + fill-timing term;
- the **residue term**, printed beside the others and never folded into the
  residual: over equity at close(T_i), the sum over names with a residue at
  close(T_i) (`execution.plan.residue`, evaluated on rows known at close(T_i))
  of the residue quantity x (close(T_{i+1}) split-adjusted back to T_i's
  basis, minus close(T_i)) -- `closes` is the caller's separate bound
  accessor for these close(T_i)/close(T_{i+1}) reads, always the `close`
  bar regardless of the frozen `execution.fill_price` (#606: a hypothesis
  registered with `fill_price = open` must not value residues at the open;
  residues price at the close, like the marks, the same as every other
  term's close(T_i)/close(T_{i+1}) reads);
- the **modelled cost**: the trial's base-level `cost_paid` at T_i over the
  trial's own equity at close(T_i) (spec req 10: "the trial's base-level
  `cost_paid` at T_i over its equity at close(T_i)" - "its" is the trial's,
  not paper's, so the threshold tracks the trial's own scale and is unaffected
  by paper's account size relative to the trial's).

The check applies `window`'s frozen `paper.tracking_k` to that month's
modelled cost, against the series the frozen `paper.tracking_rule` names
(`raw` or `residual`): under `raw` only months with a `missed` rebalance
event are excluded from the check (listed separately); under `residual`,
months with an override are excluded too (a `decisions` row with
`decision = "override"` at T_i). A month with a `skip_*` decision is listed
with its names whichever rule applies, never excluded. A month whose
T_{i+1} falls on or after `stop_session` is left out of the result
entirely ("uncompared"): no term is computed for it. `window.frozen_json`
is read for `paper.tracking_k` and `paper.tracking_rule` only, never live
`Settings` (spec req 14).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal, cast
from zoneinfo import ZoneInfo

import duckdb
import polars as pl

from tradepartner.backtest.frozen import frozen_values
from tradepartner.backtest.holdout import Flags
from tradepartner.backtest.run import run_hypothesis
from tradepartner.backtest.schedule import fill_session, rebalance_sessions
from tradepartner.calendar import (
    previous_session,
    session_close,
)
from tradepartner.config import Cadence, Settings
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.marks import equity_at
from tradepartner.execution.plan import residue as residue_of
from tradepartner.execution.plan import stop_session as stop_session_of_request
from tradepartner.execution.window import window_cadence
from tradepartner.store import journal as store_journal
from tradepartner.store import registry
from tradepartner.store.asof import prices_as_of
from tradepartner.store.db import open_for_write, utc_now
from tradepartner.store.journal import (
    AdjustmentRow,
    DecisionEventRow,
    DecisionRow,
    OrderedFill,
    OrderRow,
    PaperPlanRow,
    PaperReportRow,
    PaperRunRow,
    PaperWindowRow,
    PaperWindowStopRow,
    PositionDailyRow,
    RebalanceEventRow,
)

PriceOf = Callable[[str, date], float | None]
Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]

_NEW_YORK = ZoneInfo("America/New_York")
#: How far back the rebalance-session searches look: the slowest cadence, `month_end`,
#: has a rebalance session within two calendar months of any day.
_REBALANCE_SEARCH = timedelta(days=62)
_DIVIDEND = "dividend"
_SPLIT = "split"
_DIVIDEND_CASH = "dividend_cash"
_MISSED = "missed"
_OVERRIDE = "override"
_SKIP_PREFIX = "skip_"
_RAW = "raw"
_RESIDUAL = "residual"
_PAPER_TRACKING_K = "paper.tracking_k"
_PAPER_TRACKING_RULE = "paper.tracking_rule"

#: The backtest conventions paper cannot copy, printed with every report
#: (spec req 10, last sentence).
CONVENTIONS: tuple[str, ...] = (
    "stale_exit",
    "missing_fill",
    "exit_at_last_close",
    "sells_and_buys_at_one_price_versus_two_phases",
    "unspent_cash",
    "residues",
)


@dataclass(frozen=True)
class TrialMonths:
    """The tracking trial's base-level series as `compare_months` needs them.

    `sessions` is every rebalance session the trial covers, T_0 to the last
    completed T, sorted ascending with no gaps; consecutive entries give each
    month's (T_i, T_{i+1}) pair. `equity` is the base-level strategy equity
    (`trial_equity`, the strategy series at the trial's base
    `cost_per_side_bps`) at close of each session in `sessions`. `cost_paid`
    is the base-level modelled cost paid at each rebalance in `sessions`
    (`trial_rebalances.cost_paid`), keyed by the rebalance's own session
    (T_i, not T_{i+1}).
    """

    sessions: Sequence[date]
    equity: Mapping[date, float]
    cost_paid: Mapping[date, float]
    #: The tracking trial's own id (plan T65b): `compare_targets` needs it to read
    #: `trial_weights`, keyed by `fill_session`, not `session`, so it is not one of
    #: this dataclass's other maps. `None` only for a caller (an existing test) that
    #: never calls `compare_targets`; `compare_months` never reads this field.
    trial_id: int | None = None
    #: The cadence `sessions` are rebalance sessions at: the window's frozen cadence
    #: (`window.window_cadence`, #1286), which `_trial_months` reads `sessions` at and
    #: `compare_targets` passes to `fill_session`. The `month_end` default serves only a
    #: hand-built test trial; `paper report` and `paper check` always set it.
    cadence: Cadence = "month_end"


@dataclass(frozen=True)
class Journal:
    """The window's journal rows `compare_months` and `compare_targets` read.
    `fills` need not be pre-filtered of superseded ones: `compare_months` drops
    `fill.superseded_by is not None` rows itself, so a superseded fill and its
    replacement are counted once, through the live one only."""

    positions_daily: Sequence[PositionDailyRow]
    adjustments: Sequence[AdjustmentRow]
    decisions: Sequence[DecisionRow]
    decision_events: Sequence[DecisionEventRow] = field(default_factory=tuple)
    orders: Sequence[OrderRow] = field(default_factory=tuple)
    fills: Sequence[OrderedFill] = field(default_factory=tuple)
    runs: Sequence[PaperRunRow] = field(default_factory=tuple)
    rebalance_events: Sequence[RebalanceEventRow] = field(default_factory=tuple)
    #: The window's `paper_plans` rows (plan T65b): `compare_targets` reads each
    #: fill session's `store_max_ingested_at` from here, never from `Settings`.
    plans: Sequence[PaperPlanRow] = field(default_factory=tuple)


@dataclass(frozen=True)
class MonthRow:
    """One compared month i: T_i, T_{i+1} and every req 10 term."""

    rebalance_session: date
    next_session: date
    raw: float
    dividend_term: float
    fill_timing_term: float
    residual: float
    residue_term: float
    modelled_cost: float
    missed: bool
    override: bool
    skip_names: tuple[str, ...]
    excluded: bool
    passed: bool


@dataclass(frozen=True)
class MonthlyComparison:
    """The req 10 tracking comparison over every compared month."""

    tracking_rule: str
    tracking_k: float
    months: tuple[MonthRow, ...]
    passed: bool
    failing_month: date | None
    conventions: tuple[str, ...] = CONVENTIONS


def _frozen_paper(window: PaperWindowRow) -> tuple[float, str]:
    """(`paper.tracking_k`, `paper.tracking_rule`) from `window.frozen_json`,
    never live `Settings` (spec req 14)."""
    try:
        parsed = json.loads(window.frozen_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"window {window.window_id} frozen_json is not JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"window {window.window_id} frozen_json is not an object")
    missing = [key for key in (_PAPER_TRACKING_K, _PAPER_TRACKING_RULE) if key not in parsed]
    if missing:
        raise ValueError(f"window {window.window_id} frozen_json lacks keys {missing}")
    tracking_k = parsed[_PAPER_TRACKING_K]
    tracking_rule = parsed[_PAPER_TRACKING_RULE]
    if not isinstance(tracking_k, int | float) or isinstance(tracking_k, bool):
        raise ValueError(f"window {window.window_id} {_PAPER_TRACKING_K} is {tracking_k!r}")
    if tracking_rule not in (_RAW, _RESIDUAL):
        raise ValueError(f"window {window.window_id} {_PAPER_TRACKING_RULE} is {tracking_rule!r}")
    return float(tracking_k), tracking_rule


def _local(at: datetime) -> date:
    return at.astimezone(_NEW_YORK).date()


def _quantity_at(marks: Sequence[PositionDailyRow], security_id: str, session: date) -> float:
    for row in marks:
        if row.session == session and row.security_id == security_id:
            return row.quantity
    return 0.0


def _actions_as_of(actions: pl.DataFrame, t: datetime) -> pl.DataFrame:
    """The latest revision of each `corporate_actions` identity with
    `known_at <= t`, dropped if cancelled: a simplified in-memory mirror of
    `store.asof.live_actions_as_of`'s `_ACTION_IDENTITY_PARTITION` (#108): the
    identity is `(security_id, source_action_id)` when the frame carries a
    non-empty `source_action_id`, else `(security_id, action_type, ex_date)`,
    so a re-dated event (a changed `ex_date` on the same `source_action_id`)
    stays one row at its latest ex-date, never two."""
    if actions.is_empty():
        return actions
    frame = actions.filter(pl.col("known_at") <= t)
    if frame.is_empty():
        return frame
    if "source_action_id" in frame.columns:
        has_source = pl.col("source_action_id").fill_null("") != ""
        identity_expr = (
            pl.when(has_source)
            .then(pl.format("src:{}:{}", "security_id", "source_action_id"))
            .otherwise(
                pl.format(
                    "typ:{}:{}:{}", "security_id", "action_type", pl.col("ex_date").cast(pl.Utf8)
                )
            )
            .alias("_identity")
        )
        frame = (
            frame.with_columns(identity_expr)
            .sort("known_at")
            .group_by("_identity", maintain_order=True)
            .last()
            .drop("_identity")
        )
    else:
        frame = (
            frame.sort("known_at")
            .group_by(["security_id", "action_type", "ex_date"], maintain_order=True)
            .last()
        )
    if "cancelled" in frame.columns:
        frame = frame.filter(~pl.col("cancelled").fill_null(False))
    return frame


def _split_factor(
    actions_as_of_next: pl.DataFrame, security_id: str, after: date, through: date
) -> float:
    """The product of split ratios of `security_id` with `after < ex_date <= through`."""
    if actions_as_of_next.is_empty():
        return 1.0
    factor = 1.0
    for row in actions_as_of_next.filter(
        (pl.col("security_id") == security_id) & (pl.col("action_type") == _SPLIT)
    ).iter_rows(named=True):
        ex_date = row["ex_date"]
        if after < ex_date <= through:
            factor *= float(row["ratio_or_amount"])
    return factor


def _dividend_term(
    actions_as_of_next: pl.DataFrame,
    adjustments: Sequence[AdjustmentRow],
    marks: Sequence[PositionDailyRow],
    t_i: date,
    t_next: date,
) -> float:
    if actions_as_of_next.is_empty():
        return 0.0
    #: Sessions a `dividend_cash` adjustment was journaled for each name:
    #: reconcile stamps it at the reconciliation session, on or after the
    #: ex-date (`execution.reconcile_run._dividends`), never on the ex-date
    #: itself, so the match is "on or after", not "equal to". Only a credit
    #: known by close(T_{i+1}) counts (the same cutoff the dividend term
    #: itself is computed under): a later credit was not knowable when this
    #: month was last reported and must not retroactively zero its term.
    cutoff = session_close(t_next)
    credited: dict[str, list[date]] = {}
    for adjustment in adjustments:
        if (
            adjustment.kind == _DIVIDEND_CASH
            and adjustment.security_id is not None
            and adjustment.known_at <= cutoff
        ):
            credited.setdefault(adjustment.security_id, []).append(adjustment.session)
    total = 0.0
    for action_row in actions_as_of_next.filter(pl.col("action_type") == _DIVIDEND).iter_rows(
        named=True
    ):
        ex_date = action_row["ex_date"]
        if not (t_i < ex_date <= t_next):
            continue
        security_id = action_row["security_id"]
        if any(session >= ex_date for session in credited.get(security_id, ())):
            continue
        #: Entitlement is held as of the session before the ex-date (the
        #: record date; `execution.reconcile_run._dividends` uses the same
        #: `previous_session(ex_date)` holding), never the ex-date's own close.
        quantity = _quantity_at(marks, security_id, previous_session(ex_date))
        total += float(action_row["ratio_or_amount"]) * quantity
    return total


def _fill_timing_term(
    fills: Sequence[OrderedFill], prices: PriceOf, t_i: date, t_next: date
) -> float:
    total = 0.0
    for item in fills:
        if item.fill.superseded_by is not None:
            continue
        session = _local(item.fill.filled_at)
        if not (t_i < session <= t_next):
            continue
        bar = prices(item.security_id, session)
        if bar is None:
            raise ValueError(f"no bar price for {item.security_id} on {session}")
        delta = (item.fill.price - bar) * item.fill.quantity
        if item.side == "sell":
            delta = -delta
        total += delta
    return total


def _residue_term(
    journal: Journal,
    actions_as_of_i: pl.DataFrame,
    actions_as_of_next: pl.DataFrame,
    closes: PriceOf,
    window_id: int,
    t_i: date,
    t_next: date,
) -> float:
    held = {
        row.security_id: row.quantity
        for row in journal.positions_daily
        if row.session == t_i and row.security_id is not None and row.quantity != 0
    }
    if not held:
        return 0.0
    #: `execution.plan.residue` requires its `decisions`, `decision_events`
    #: and `positions_daily` rows to be those known to the run on the ledger's
    #: session (its own docstring), and raises on a row whose run is not among
    #: `runs`: restrict all four to runs at or before T_i so a later month's
    #: decision (e.g. a later `dust` or `untradable` close) can never reach
    #: back into this month's residue (no look-ahead).
    known_runs = tuple(r for r in journal.runs if r.session is not None and r.session <= t_i)
    known_run_ids = {r.run_id for r in known_runs}
    known_decisions = [d for d in journal.decisions if d.run_id in known_run_ids]
    known_decision_events = [e for e in journal.decision_events if e.run_id in known_run_ids]
    known_marks = [m for m in journal.positions_daily if m.run_id in known_run_ids]
    total = 0.0
    for security_id, quantity in held.items():
        ledger = Ledger(positions={security_id: quantity}, cash=0.0, through=t_i)
        quantity_at_risk = residue_of(
            security_id,
            journal.adjustments,
            known_decisions,
            known_decision_events,
            known_marks,
            ledger,
            actions_as_of_i,
            window_id=window_id,
            runs=known_runs,
        )
        if quantity_at_risk == 0.0:
            continue
        close_i = closes(security_id, t_i)
        close_next = closes(security_id, t_next)
        if close_i is None or close_next is None:
            raise ValueError(f"no close for {security_id} on {t_i} or {t_next}")
        factor = _split_factor(actions_as_of_next, security_id, t_i, t_next)
        adjusted_next = close_next * factor
        total += quantity_at_risk * (adjusted_next - close_i)
    return total


def compare_months(
    window: PaperWindowRow,
    trial: TrialMonths,
    journal: Journal,
    actions: pl.DataFrame,
    prices: PriceOf,
    closes: PriceOf,
    stop_session: date | None,
) -> MonthlyComparison:
    """The req 10 tracking comparison, every term per month (module docstring).

    `actions` is the raw `corporate_actions` rows the window's lifetime could
    ever need (not pre-cut to one as-of instant): `compare_months` applies its
    own per-month cutoffs, close(T_i) for the residue term and close(T_{i+1})
    for the dividend and split terms, since no single cutoff serves every
    month. `window.window_id` must be set.

    `prices` serves only the fill-timing term's frozen-bar reads; `closes`
    serves the residue term's close(T_i)/close(T_{i+1}) reads and always
    reads the `close` bar, whatever `execution.fill_price` froze (#606):
    residues price at the close, like the marks, regardless of fill_price.
    """
    if window.window_id is None:
        raise ValueError("the window has no window_id")
    tracking_k, tracking_rule = _frozen_paper(window)
    live_fills = [item for item in journal.fills if item.fill.superseded_by is None]
    months: list[MonthRow] = []
    failing: date | None = None
    for t_i, t_next in zip(trial.sessions, trial.sessions[1:], strict=False):
        if stop_session is not None and t_next >= stop_session:
            continue
        if t_i not in trial.equity or t_next not in trial.equity:
            raise ValueError(f"trial has no equity for {t_i} or {t_next}")
        if t_i not in trial.cost_paid:
            raise ValueError(f"trial has no cost_paid for {t_i}")
        paper_equity_i = equity_at(journal.positions_daily, t_i)
        paper_equity_next = equity_at(journal.positions_daily, t_next)
        paper_return = paper_equity_next / paper_equity_i - 1
        trial_return = trial.equity[t_next] / trial.equity[t_i] - 1
        raw = paper_return - trial_return

        actions_as_of_i = _actions_as_of(actions, session_close(t_i))
        actions_as_of_next = _actions_as_of(actions, session_close(t_next))

        dividend_term = (
            _dividend_term(
                actions_as_of_next, journal.adjustments, journal.positions_daily, t_i, t_next
            )
            / paper_equity_i
        )
        fill_timing_term = _fill_timing_term(live_fills, prices, t_i, t_next) / paper_equity_i
        residual = raw + dividend_term + fill_timing_term
        residue_term = (
            _residue_term(
                journal, actions_as_of_i, actions_as_of_next, closes, window.window_id, t_i, t_next
            )
            / paper_equity_i
        )
        modelled_cost = trial.cost_paid[t_i] / trial.equity[t_i]

        missed = any(
            event.rebalance_session == t_i and event.status == _MISSED
            for event in journal.rebalance_events
        )
        override = any(
            d.rebalance_session == t_i and d.decision == _OVERRIDE for d in journal.decisions
        )
        skip_names = tuple(
            sorted(
                {
                    d.security_id
                    for d in journal.decisions
                    if d.rebalance_session == t_i and d.decision.startswith(_SKIP_PREFIX)
                }
            )
        )

        excluded = missed or (tracking_rule == _RESIDUAL and override)
        value = residual if tracking_rule == _RESIDUAL else raw
        threshold = tracking_k * modelled_cost
        month_passed = excluded or abs(value) <= threshold
        if not month_passed and failing is None:
            failing = t_i

        months.append(
            MonthRow(
                rebalance_session=t_i,
                next_session=t_next,
                raw=raw,
                dividend_term=dividend_term,
                fill_timing_term=fill_timing_term,
                residual=residual,
                residue_term=residue_term,
                modelled_cost=modelled_cost,
                missed=missed,
                override=override,
                skip_names=skip_names,
                excluded=excluded,
                passed=month_passed,
            )
        )

    return MonthlyComparison(
        tracking_rule=tracking_rule,
        tracking_k=tracking_k,
        months=tuple(months),
        passed=all(m.passed for m in months),
        failing_month=failing,
    )


# --- Target comparison (spec req 10, last paragraph; plan T65b) --------------------

_WEIGHT_TOLERANCE = 1e-9
#: "Any bar, action or filing" (spec req 10): the fact tables a late-ingested row
#: can live in, each with `known_at`/`ingested_at` (module docstring, "Definitions").
_LATE_DATA_TABLES: tuple[str, ...] = ("prices_daily", "corporate_actions", "facts")
_EXECUTION_FILL_PRICE = "execution.fill_price"
Difference = Literal["override", "late_data", "bug"]


@dataclass(frozen=True)
class TargetRow:
    """One name whose paper target differed from the tracking trial's at one
    fill session (spec req 10's target comparison). `paper_weight` is the
    decisions' `target_weight` at `rebalance_session` (None when paper named no
    target for it there); `trial_weight` is the trial's `trial_weights` at the
    matching fill session (None when the trial named no target for it there)."""

    rebalance_session: date
    security_id: str
    paper_weight: float | None
    trial_weight: float | None
    difference: Difference


@dataclass(frozen=True)
class TargetComparison:
    """Every differing name across the tracking trial's sessions (empty when
    paper's targets matched the trial's `trial_weights` everywhere, within
    `_WEIGHT_TOLERANCE`)."""

    rows: tuple[TargetRow, ...]


def _weight_or_zero(weight: float | None) -> float:
    return 0.0 if weight is None else weight


def _trial_weights_at(
    store: duckdb.DuckDBPyConnection, trial_id: int
) -> dict[tuple[date, str], float]:
    """`{(fill_session, security_id): target_weight}` for `trial_id`'s base-level
    `trial_weights` (targets are identical across cost levels, backtest spec
    "Data / interfaces")."""
    rows = store.execute(
        "SELECT fill_session, security_id, target_weight FROM trial_weights WHERE trial_id = ?",
        [trial_id],
    ).fetchall()
    return {(fill, security_id): float(weight) for fill, security_id, weight in rows}


def _latest_plan(plans: Sequence[PaperPlanRow], rebalance_session: date) -> PaperPlanRow | None:
    """The latest (by `known_at`) `paper_plans` row planning `rebalance_session`,
    or None when it was never planned."""
    candidates = [p for p in plans if p.rebalance_session == rebalance_session]
    return max(candidates, key=lambda p: p.known_at) if candidates else None


def _late_data(
    store: duckdb.DuckDBPyConnection,
    security_id: str,
    cutoff: datetime,
    max_ingested_at: datetime,
) -> bool:
    """Whether `security_id` has a `prices_daily`, `corporate_actions` or `facts`
    row known by `cutoff` (close(T_i)) but ingested after `max_ingested_at` (the
    plan's `store_max_ingested_at`): the tracking trial saw a row the paper plan
    could not (spec req 10)."""
    for table in _LATE_DATA_TABLES:
        row = store.execute(
            f"SELECT 1 FROM {table} WHERE security_id = ? AND known_at <= ? "
            "AND ingested_at > ? LIMIT 1",
            [security_id, cutoff, max_ingested_at],
        ).fetchone()
        if row is not None:
            return True
    return False


def compare_targets(
    window: PaperWindowRow,
    trial: TrialMonths,
    journal: Journal,
    store: duckdb.DuckDBPyConnection,
) -> TargetComparison:
    """The req 10 target comparison: per fill session F_i =
    `schedule.fill_session(T_i)`, the decisions' `target_weight` at rebalance
    session T_i against the tracking trial's `trial_weights` at F_i (module
    docstring's last paragraph). A differing name (beyond `_WEIGHT_TOLERANCE`,
    absent on either side counted as weight 0) is reported `override` when its
    decision at T_i is an `override`; else `late_data` when `_late_data` finds a
    late-ingested row for it; else `bug` (a look-ahead or determinism fault).
    Equal weights are not reported, so an empty `TargetComparison` is "every
    fill session passed".

    `store` is a read-only connection: `trial_weights` and the fact tables are
    read directly (there is no registry reader for `trial_weights`, and the
    fact tables are read once per differing name, not materialised whole).
    Raises `ValueError` only when `trial.trial_id` is None (the caller must
    have read it from a real `trials` row). A rebalance session never planned
    at all (a `missed` run, or one not yet due) is skipped, not raised on
    (quant-auditor finding on PR #525): there is nothing paper decided there
    to compare. A session that was planned but whose `paper_plans` row lacks
    `store_max_ingested_at` has no cutoff for the late-data check, so a
    differing name there defaults to `bug` rather than raising.

    `trial.sessions[-1]` is never compared: like `compare_months`'s
    `zip(sessions, sessions[1:])`, the trial's last rebalance session has no
    further session to fill into inside this trial, so the engine never
    planned it (`trial_weights` carries no row at `fill_session(sessions[-1])`)
    — comparing it would flag every one of paper's names there `bug` for no
    reason (quant-auditor finding on PR #525)."""
    if trial.trial_id is None:
        raise ValueError("the trial has no trial_id")
    trial_weights = _trial_weights_at(store, trial.trial_id)
    rows: list[TargetRow] = []
    for t_i in trial.sessions[:-1]:
        f_i = fill_session(t_i, trial.cadence)
        session_decisions = {
            d.security_id: d for d in journal.decisions if d.rebalance_session == t_i
        }
        names = set(session_decisions) | {sid for fs, sid in trial_weights if fs == f_i}
        if not names:
            continue
        # A rebalance session never planned at all (a `missed` run, or one still
        # pending) has nothing paper decided to compare: skip it rather than
        # raise (quant-auditor finding on PR #525 — `paper report` must not
        # crash on a window with a missed or not-yet-due rebalance). When a
        # session WAS planned but its row lacks `store_max_ingested_at` (a data
        # anomaly, not a missed run), the late-data check below has no cutoff
        # to use and a differing name there defaults to `bug`.
        plan = _latest_plan(journal.plans, t_i)
        if plan is None:
            continue
        max_ingested_at = plan.store_max_ingested_at
        cutoff = session_close(t_i)
        for security_id in sorted(names):
            decision = session_decisions.get(security_id)
            paper_weight = decision.target_weight if decision is not None else None
            trial_weight = trial_weights.get((f_i, security_id))
            if (
                abs(_weight_or_zero(paper_weight) - _weight_or_zero(trial_weight))
                <= _WEIGHT_TOLERANCE
            ):
                continue
            difference: Difference
            if decision is not None and decision.decision == _OVERRIDE:
                difference = "override"
            elif max_ingested_at is not None and _late_data(
                store, security_id, cutoff, max_ingested_at
            ):
                difference = "late_data"
            else:
                difference = "bug"
            rows.append(
                TargetRow(
                    rebalance_session=t_i,
                    security_id=security_id,
                    paper_weight=paper_weight,
                    trial_weight=trial_weight,
                    difference=difference,
                )
            )
    return TargetComparison(rows=tuple(rows))


# --- `paper report` (spec req 10; plan T65b) ---------------------------------------


@dataclass(frozen=True)
class Report:
    """What `report()` computed and wrote: the two req 10 comparisons and the
    `paper_reports` row it appended."""

    monthly: MonthlyComparison
    targets: TargetComparison
    paper_report: PaperReportRow


def _local_date(at: datetime) -> date:
    return at.astimezone(_NEW_YORK).date()


def _last_completed_rebalance_session(now: datetime, cadence: Cadence) -> date:
    """The latest rebalance session at `cadence` whose close is at or before `now`:
    `paper report`'s "last completed T" (spec req 10)."""
    today = _local_date(now)
    sessions = rebalance_sessions(today - _REBALANCE_SEARCH, today, cadence)
    return next(t for t in reversed(sessions) if session_close(t) <= now)


def _stop_session_of(stops: Sequence[PaperWindowStopRow]) -> date | None:
    """The stop session (`plan.stop_session`) of the window's `requested` stop, or
    None when the window was never asked to stop (still open, or abandoned with
    no `requested` row of its own kind read here)."""
    requested = [s for s in stops if s.state == "requested"]
    return stop_session_of_request(min(s.at for s in requested)) if requested else None


def _frozen_fill_price(window: PaperWindowRow) -> Literal["close", "open"]:
    """`execution.fill_price` from `window.frozen_json` (req 14 amendment, #366 Q20,
    owner): the tracking trial's fill-timing term and the `prices` callable
    it binds use the convention frozen at `paper start`, never live
    `Settings` (`config.FROZEN_EXECUTION_KEYS`). The residue term is unaffected
    by this freeze (#606): it always reads through a separate `closes`
    accessor pinned to `close`, so a window frozen with `fill_price = "open"`
    still prices residues at the close, like the marks."""
    try:
        parsed = json.loads(window.frozen_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"window {window.window_id} frozen_json is not JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"window {window.window_id} frozen_json is not an object")
    value = parsed.get(_EXECUTION_FILL_PRICE)
    if value not in ("close", "open"):
        raise ValueError(f"window {window.window_id} {_EXECUTION_FILL_PRICE} is {value!r}")
    return cast(Literal["close", "open"], value)


def _price_of(
    store: duckdb.DuckDBPyConnection, fill_price_key: Literal["close", "open"]
) -> PriceOf:
    """A `PriceOf` reading `fill_price_key`'s raw bar for `(security_id, session)`,
    known as of close(`session`) (ADR 0003 rule 2: a late bar is stamped at the
    session close, so this is exactly the cutoff a same-day read would see)."""

    def price(security_id: str, session: date) -> float | None:
        frame = prices_as_of(store, session_close(session), [security_id]).filter(
            pl.col("session") == session
        )
        if frame.is_empty():
            return None
        return float(frame[fill_price_key][0])

    return price


def _journal_for(conn: duckdb.DuckDBPyConnection, window_id: int) -> Journal:
    """The full `Journal` of `window_id`'s rows, through `store.journal`'s single
    readers (module docstring, "Journal not initialised" pattern aside: this
    assumes the journal is already initialised, as it must be for an open or
    closed window to exist)."""
    with_events = store_journal.decisions_for(conn, window_id)
    return Journal(
        positions_daily=store_journal.positions_daily_for(conn, window_id),
        adjustments=store_journal.adjustments_for(conn, window_id),
        decisions=tuple(d.decision for d in with_events),
        decision_events=tuple(e for d in with_events for e in d.events),
        orders=store_journal.orders_for(conn, window_id=window_id),
        fills=store_journal.fills_for(conn, window_id=window_id),
        runs=tuple(rw.run for rw in store_journal.runs_for(conn, window_id)),
        rebalance_events=store_journal.rebalance_events_for(conn, window_id),
        plans=store_journal.plans_for(conn, window_id),
    )


def _trial_months(conn: duckdb.DuckDBPyConnection, trial_id: int, cadence: Cadence) -> TrialMonths:
    """`TrialMonths` for `trial_id`'s base cost level (backtest spec: "targets are
    identical across levels"; `trial_id` carried so `compare_targets` can read
    `trial_weights` itself), at the window's `cadence` (#1286: one period per
    rebalance period of that cadence, an ISO week at `week_end`).

    `sessions` is `schedule.rebalance_sessions(start, end, cadence)` over the trial's own
    window, never every `trial_equity` row: that table carries one row per
    calendar session (the daily equity curve `engine.run` marks through), not
    one per rebalance, so reading its `session` column directly would hand
    `compare_months` the wrong, far denser set of "months"."""
    found = conn.execute(
        "SELECT hypothesis_id, start_session, end_session FROM trials WHERE trial_id = ?",
        [trial_id],
    ).fetchone()
    if found is None:
        raise ValueError(f"no trial {trial_id}")
    hypothesis_id, start_session, end_session = found
    hypothesis = registry.get_hypothesis_by_id(conn, hypothesis_id)
    base_level = float(frozen_values(hypothesis)[registry.BASE_COST_KEY])
    sessions = tuple(rebalance_sessions(start_session, end_session, cadence))
    equity_rows = conn.execute(
        "SELECT session, equity FROM trial_equity "
        "WHERE trial_id = ? AND series = 'strategy' AND cost_per_side_bps = ?",
        [trial_id, base_level],
    ).fetchall()
    equity = {session: float(value) for session, value in equity_rows}
    cost_rows = conn.execute(
        "SELECT session, cost_paid FROM trial_rebalances "
        "WHERE trial_id = ? AND cost_per_side_bps = ?",
        [trial_id, base_level],
    ).fetchall()
    cost_paid = {session: float(value) for session, value in cost_rows}
    return TrialMonths(
        sessions=sessions,
        equity={session: equity[session] for session in sessions if session in equity},
        cost_paid=cost_paid,
        trial_id=trial_id,
        cadence=cadence,
    )


def _before(session: date, cadence: Cadence) -> date:
    """The last rebalance session at `cadence` strictly before `session` (spec req 15:
    "a closed window is checked through its last completed rebalance session before
    the stop session")."""
    last = session - timedelta(days=1)
    return rebalance_sessions(last - _REBALANCE_SEARCH, last, cadence)[-1]


def report(settings: Settings, connect: Connect) -> Report:
    """`paper report` (spec req 10): opens a `kind=tracking` trial over
    `[T_0, last completed T]` on the real store with the hypothesis's frozen
    parameters (`run.run_hypothesis`, `store_path=settings.store.path`), runs
    both comparisons and appends a `paper_reports` row.

    `connect` opens one short-lived read-only connection per step, as
    `StoreProvider`'s does (backtest spec "Interfaces"); the write (appending
    `paper_reports`) and the tracking trial's own writes go through `settings`
    directly (`run_hypothesis`, then `store.db.open_for_write`), never held open
    at the same time as a `connect()` read (DuckDB allows one connection mode
    per file per process). Raises `ValueError` when no window is open, when the
    frozen `execution.fill_price` is missing or invalid (read and validated up
    front, before the tracking trial runs, so a bad freeze fails fast rather
    than after paying for a trial run; quant-auditor finding on PR #525; `paper
    start` writes it since #526, so only a window started earlier lacks it), or
    when the tracking trial does not finish `ok`.

    "Last completed T" is capped strictly before the window's stop session
    (req 15), when it has one: a stopped window's `last completed T` is never
    one paper could still have rebalanced at, so a session at or after the
    stop is never asked of the tracking trial (quant-auditor finding on PR
    #525 — this also keeps `compare_targets` from ever being asked about a
    session the window stopped before planning).
    """
    with connect() as conn:
        window = store_journal.latest_window(conn)
        if window is None:
            raise ValueError("no paper window is open")
        if window.window_id is None:
            raise ValueError("the window has no window_id")
        window_id = window.window_id
        fill_price_key = _frozen_fill_price(window)
        hypothesis = registry.get_hypothesis_by_id(conn, window.hypothesis_id)
        cadence = window_cadence(conn, window)
        t0 = window.first_rebalance_session
        last_t = _last_completed_rebalance_session(utc_now(), cadence)
        stop_session = _stop_session_of(store_journal.window_stops_for(conn, window_id))
        while stop_session is not None and last_t >= stop_session:
            last_t = _before(last_t, cadence)

    outcome = run_hypothesis(
        hypothesis.slug,
        t0,
        last_t,
        Flags(),
        synthetic=False,
        store_path=settings.store.path,
        run_by="paper_report",
        kind="tracking",
    )
    if outcome.status != "ok":
        raise ValueError(f"tracking trial {outcome.trial_id} did not run ({outcome.status})")

    with connect() as conn:
        trial = _trial_months(conn, outcome.trial_id, cadence)
        journal = _journal_for(conn, window_id)
        actions = conn.execute("SELECT * FROM corporate_actions").pl()
        prices = _price_of(conn, fill_price_key)
        #: Residues always price at the close, like the marks, never through
        #: the frozen `execution.fill_price` bar (#606): bound separately
        #: from `prices` so a window frozen with `fill_price = "open"` cannot
        #: value a residue at the open.
        closes = _price_of(conn, "close")
        monthly = compare_months(window, trial, journal, actions, prices, closes, stop_session)
        targets = compare_targets(window, trial, journal, conn)

    now = utc_now()
    paper_report_row = PaperReportRow(
        window_id=window_id,
        trial_id=outcome.trial_id,
        through_session=last_t,
        run_at=now,
        known_at=now,
        ingested_at=now,
    )
    with open_for_write(settings) as write_conn:
        store_journal.append(write_conn, paper_report_row)

    return Report(monthly=monthly, targets=targets, paper_report=paper_report_row)
