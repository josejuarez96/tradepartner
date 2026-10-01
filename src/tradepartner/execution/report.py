"""Tracking comparison, monthly decomposition (Phase 4 spec req 10, ADR 0005
check 1; plan T65).

`compare_months(window, trial, journal, actions, prices, stop_session) ->
MonthlyComparison` is pure: no clock, no store, no live `Settings` read. Per
rebalance month i (consecutive sessions `trial.sessions[i]` = T_i and
`trial.sessions[i + 1]` = T_{i+1}) it computes:

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
  module never reads `Settings.execution.fill_price` itself, since req 14
  does not freeze that key into the window - the caller binds `prices` once,
  which also serves the raw `close(T_i)`/`close(T_{i+1})` reads the other
  terms need, since the frozen convention is pinned to `close` by ADR 0007's
  T3 decision and never varies in practice);
- the **residual**: raw + dividend term + fill-timing term;
- the **residue term**, printed beside the others and never folded into the
  residual: over equity at close(T_i), the sum over names with a residue at
  close(T_i) (`execution.plan.residue`, evaluated on rows known at close(T_i))
  of the residue quantity x (close(T_{i+1}) split-adjusted back to T_i's
  basis, minus close(T_i));
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
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import polars as pl

from tradepartner.calendar import previous_session, session_close
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.plan import residue as residue_of
from tradepartner.store.journal import (
    AdjustmentRow,
    DecisionEventRow,
    DecisionRow,
    OrderedFill,
    OrderRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    RebalanceEventRow,
)

PriceOf = Callable[[str, date], float | None]

_NEW_YORK = ZoneInfo("America/New_York")
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


@dataclass(frozen=True)
class Journal:
    """The window's journal rows `compare_months` reads. `fills` need not be
    pre-filtered of superseded ones: `compare_months` drops
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


def _equity_at(marks: Sequence[PositionDailyRow], session: date) -> float:
    """Ledger equity stated for `session`: the cash of its row with no
    `security_id` plus every name's value, from rows of that exact session.
    Raises when the session has no usable mark (fail closed: a month this
    module is asked to compare must be fully marked)."""
    rows = [m for m in marks if m.session == session]
    cash_rows = [m.cash for m in rows if m.security_id is None]
    if len(cash_rows) != 1 or cash_rows[0] is None:
        raise ValueError(f"no cash mark for {session}")
    values = [m.value for m in rows if m.security_id is not None]
    if any(v is None for v in values):
        raise ValueError(f"a position mark for {session} has no value")
    return cash_rows[0] + sum(v for v in values if v is not None)


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
    prices: PriceOf,
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
        close_i = prices(security_id, t_i)
        close_next = prices(security_id, t_next)
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
    stop_session: date | None,
) -> MonthlyComparison:
    """The req 10 tracking comparison, every term per month (module docstring).

    `actions` is the raw `corporate_actions` rows the window's lifetime could
    ever need (not pre-cut to one as-of instant): `compare_months` applies its
    own per-month cutoffs, close(T_i) for the residue term and close(T_{i+1})
    for the dividend and split terms, since no single cutoff serves every
    month. `window.window_id` must be set.
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
        paper_equity_i = _equity_at(journal.positions_daily, t_i)
        paper_equity_next = _equity_at(journal.positions_daily, t_next)
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
                journal, actions_as_of_i, actions_as_of_next, prices, window.window_id, t_i, t_next
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
