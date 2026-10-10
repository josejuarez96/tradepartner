"""Momentum signal at a signal anchor (backtest spec req 3, handoff H1; strategy-lab
spec req 7).

Score of a name at rebalance session `t_session` (T):

    close(A_skip) / close(A_form) - 1

where A_k is the anchor k months before T, per the frozen `schedule.signal_anchor`:

- `month_end` (the Phase 3 rule and H1's): the last XNYS session of the calendar month
  k months before T's month. At `month_end` cadence this is `momentum_12_1`; at a
  faster cadence the score changes only when a month ends.
- `offset`: the last XNYS session on or before the calendar date T - k months
  (`dateutil.relativedelta`), so the score refreshes every rebalance.

`formation_months` and `skip_months` stay in months at every cadence. A name lacking
either bar is excluded and counted.

Only rows with `session <= t_session` are read, so a frame that extends past the
rebalance (as a later as-of read might) cannot leak into the score; an anchor after T
is refused here and, at registration, by `check_anchor_feasible`. Which adjustment the
frame carries (splits only, or dividends too per `strategy.signal_total_return`) is the
caller's choice when it reads the frame.

`turnover_screen` is the `momentum` family's share-turnover screen (backtest spec
amendment #1358, item 2; B10): before the rank, it keeps the top
`strategy.turnover_top_fraction` of the members by formation-period turnover (share
volume over shares outstanding, both moved into T's share units by the splits known at
`t`). `formation_sessions` gives that period.

`gross_profitability` is the `profitability` family's signal (backtest spec amendment
#720, rules 0 to 6): annual gross profit over the total assets of the same fiscal year
end, from a `statement_facts_as_of` frame. Every period length, age and SIC it uses is
an argument, never a literal here.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Collection, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

import polars as pl
from dateutil.relativedelta import relativedelta

from tradepartner.backtest.portfolio import _selected_count
from tradepartner.backtest.provider import TurnoverInputs
from tradepartner.calendar import (
    is_session,
    last_completed_session,
    last_session_of_month,
    next_session,
    previous_session,
    session_close,
)
from tradepartner.config import Cadence, SignalAnchor
from tradepartner.timeutil import ensure_tz_aware_utc

_PERIOD_NAMES: dict[Cadence, str] = {
    "month_end": "the last session of its month",
    "week_end": "the last session of its ISO week",
    "daily": "a session",
}


@dataclass(frozen=True)
class MomentumSignal:
    """Scores for the names that have both bars, and the names excluded for lacking one.

    Both are ordered by `security_id`.
    """

    scores: dict[str, float]
    excluded: tuple[str, ...]

    @property
    def n_excluded(self) -> int:
        """Count of names excluded for a missing bar (`n_excluded_no_history`)."""
        return len(self.excluded)


def _is_rebalance_session(day: date, cadence: Cadence) -> bool:
    """T ends its period at `cadence`: read from the calendar, never weekday arithmetic."""
    if not is_session(day):
        return False
    if cadence == "month_end":
        return day == last_session_of_month(day.year, day.month)
    if cadence == "week_end":
        return next_session(day).isocalendar()[:2] != day.isocalendar()[:2]
    return True


def _check_months(formation_months: int, skip_months: int) -> None:
    if skip_months < 0 or formation_months <= skip_months:
        raise ValueError(
            f"need formation_months > skip_months >= 0, got "
            f"formation_months={formation_months}, skip_months={skip_months}"
        )


def _anchor(t_session: date, months_back: int, anchor: SignalAnchor) -> date:
    target = t_session - relativedelta(months=months_back)
    if anchor == "month_end":
        return last_session_of_month(target.year, target.month)
    return previous_session(target + timedelta(days=1))


def anchor_sessions(
    t_session: date, formation_months: int, skip_months: int, anchor: SignalAnchor
) -> tuple[date, date]:
    """(A_form, A_skip): the formation and skip anchors at `t_session` (spec req 7).

    Under `month_end`, A_k is the last session of the calendar month k months before
    T's month; under `offset`, the last session on or before T - k months. Raises
    `ValueError` unless `formation_months > skip_months >= 0`.
    """
    _check_months(formation_months, skip_months)
    return (
        _anchor(t_session, formation_months, anchor),
        _anchor(t_session, skip_months, anchor),
    )


def check_anchor_feasible(
    formation_months: int,
    skip_months: int,
    anchor: SignalAnchor,
    cadence: Cadence,
    first_rebalance: date,
    first_session: date,
) -> str | None:
    """The refusal message of strategy-lab spec req 1(d), or `None` when feasible.

    Refused: an anchor that can fall after T (`month_end` anchor with
    `skip_months = 0` at a cadence other than `month_end`, where A_0 is the month's
    last session), or a formation anchor at the window's first rebalance before the
    store's first bar session; either would hold cash silently.
    """
    if anchor == "month_end" and skip_months == 0 and cadence != "month_end":
        return (
            f"signal anchor month_end with strategy.skip_months = 0 at cadence {cadence} "
            "can fall after T (A_0 is the last session of T's month)"
        )
    a_form, _ = anchor_sessions(first_rebalance, formation_months, skip_months, anchor)
    if a_form < first_session:
        return (
            f"formation anchor {a_form.isoformat()} at the first rebalance "
            f"{first_rebalance.isoformat()} precedes the store's first session "
            f"{first_session.isoformat()}"
        )
    return None


def momentum(
    frame: pl.DataFrame,
    t_session: date,
    formation_months: int,
    skip_months: int,
    anchor: SignalAnchor,
    cadence: Cadence,
    *,
    security_ids: Collection[str],
) -> MomentumSignal:
    """Momentum scores at `t_session` from `frame` (`security_id`, `session`, `close`).

    `security_ids` names the names to score: the universe at `t`, required because the
    frame also carries holdings that may have left the universe (ADR 0006). A requested
    name with no bar at all is excluded like one missing a single bar.

    Raises `ValueError` if `t_session` is not a rebalance session at `cadence`, if
    `formation_months <= skip_months` or `skip_months < 0`, if an anchor falls after
    `t_session`, if a bar is duplicated or has a null field, or if a close used is not
    positive and finite.
    """
    _check_months(formation_months, skip_months)
    if not _is_rebalance_session(t_session, cadence):
        raise ValueError(f"t_session {t_session} is not {_PERIOD_NAMES[cadence]}")
    start_bar, end_bar = anchor_sessions(t_session, formation_months, skip_months, anchor)
    if end_bar > t_session:
        raise ValueError(
            f"skip anchor {end_bar} falls after t_session {t_session} "
            f"(anchor {anchor}, skip_months {skip_months}, cadence {cadence})"
        )

    visible = frame.filter(pl.col("session") <= t_session)
    bars = visible.filter(pl.col("session").is_in([start_bar, end_bar])).select(
        "security_id", "session", "close"
    )
    if bars.select(pl.struct("security_id", "session").is_duplicated().any()).item():
        raise ValueError("frame has a duplicate (security_id, session) bar")
    if bars.null_count().sum_horizontal().item() > 0:
        raise ValueError("frame has a null security_id, session or close in an anchor bar")

    closes: dict[tuple[str, date], float] = {
        (sid, session): close for sid, session, close in bars.iter_rows()
    }
    names = sorted(set(security_ids))
    scores: dict[str, float] = {}
    excluded: list[str] = []
    for sid in names:
        start, end = closes.get((sid, start_bar)), closes.get((sid, end_bar))
        if start is None or end is None:
            excluded.append(sid)
            continue
        for value in (start, end):
            if not (math.isfinite(value) and value > 0):
                raise ValueError(f"close for {sid} must be positive and finite, got {value}")
        scores[sid] = end / start - 1
    return MomentumSignal(scores=scores, excluded=tuple(excluded))


def momentum_12_1(
    frame: pl.DataFrame,
    t_session: date,
    formation_months: int,
    skip_months: int,
    *,
    security_ids: Collection[str],
) -> MomentumSignal:
    """`momentum` at the `month_end` anchor and cadence: the Phase 3 rule, kept for
    its callers. Raises as `momentum` does (`t_session` must end its month)."""
    return momentum(
        frame,
        t_session,
        formation_months,
        skip_months,
        "month_end",
        "month_end",
        security_ids=security_ids,
    )


_PERIOD_KEYS: dict[Cadence, Callable[[date], Hashable]] = {
    "month_end": lambda s: (s.year, s.month),
    "week_end": lambda s: s.isocalendar()[:2],
    "daily": lambda s: s,
}


def formation_sessions(t_session: date, cadence: Cadence) -> tuple[date, ...]:
    """The turnover screen's formation sessions at rebalance session T (spec amendment
    #1358, item 2): the sessions after the previous rebalance session at `cadence`,
    through T, ascending. At `month_end` these are T's calendar month's sessions, at
    `week_end` its ISO week's, at `daily` T alone. Raises `ValueError` if T is not a
    rebalance session at `cadence`."""
    if not _is_rebalance_session(t_session, cadence):
        raise ValueError(f"t_session {t_session} is not {_PERIOD_NAMES[cadence]}")
    key = _PERIOD_KEYS[cadence]
    sessions = [t_session]
    while key(earlier := previous_session(sessions[-1])) == key(t_session):
        sessions.append(earlier)
    return tuple(reversed(sessions))


@dataclass(frozen=True)
class TurnoverScreen:
    """The turnover screen at one rebalance (spec amendment #1358, item 2).

    `kept`: the screened names, the ones momentum ranks. `excluded`: every other
    requested name, all under reason `no_turnover` (no usable turnover, or below the
    cut). `unusable`: the part of `excluded` with no usable turnover (no shares pick, or
    a formation bar missing), counted as `n_excluded_no_turnover`. Each is ordered by
    `security_id`. `turnover`: each usable name's turnover in T's share units, by
    `security_id`. `n_screened` is `len(kept)`.
    """

    kept: tuple[str, ...]
    excluded: tuple[str, ...]
    unusable: tuple[str, ...]
    turnover: dict[str, float]

    @property
    def n_screened(self) -> int:
        """The kept count (`n_screened`)."""
        return len(self.kept)

    @property
    def n_excluded_no_turnover(self) -> int:
        """Members with no usable turnover, the screen's denominator's complement."""
        return len(self.unusable)


def _split_factor(splits: Sequence[tuple[date, float]], after: date, through: date) -> float:
    """The product of the ratios of the splits with `after < ex_date <= through`."""
    factor = 1.0
    for ex_date, ratio in splits:
        if after < ex_date <= through:
            factor *= ratio
    return factor


def turnover_screen(
    inputs: TurnoverInputs,
    formation_sessions: Sequence[date],
    t_session: date,
    fraction: float,
    *,
    security_ids: Collection[str],
) -> TurnoverScreen:
    """The top `fraction` of `security_ids` (the universe at `inputs.t`) by turnover
    over `formation_sessions` (which end at `t_session`, `inputs.t`'s session).

    A name's turnover is the sum over the formation sessions of its share volume over
    its shares outstanding, both in T's share units: the raw shares pick `(as_of_date,
    value)` times the ratio of every split with `as_of_date < ex_date <= T`, each
    session's volume times the ratio of every split with `session < ex_date <= T`. Only
    bars with `known_at <= inputs.t` are read. A name with no positive, finite shares
    pick, or with no traded bar on any formation session, has no usable turnover: it is
    excluded (`unusable`), never given a turnover of zero. The kept count is
    `portfolio`'s count rule (the ceiling of the exact decimal `fraction` times the
    usable count), by turnover descending, ties by `security_id` ascending.

    Raises `ValueError` for a `fraction` outside (0, 1], formation sessions that are
    empty, unsorted or do not end at `t_session`, a `t_session` that is not
    `inputs.t`'s session, a duplicate `(security_id, session)` bar, or a split ratio
    that is not positive and finite.
    """
    if not (0 < fraction <= 1):
        raise ValueError(f"turnover fraction must be in (0, 1], got {fraction}")
    sessions = tuple(formation_sessions)
    if not sessions or sessions[-1] != t_session or list(sessions) != sorted(set(sessions)):
        raise ValueError(f"formation sessions must be ascending and end at {t_session}")
    if last_completed_session(inputs.t) != t_session:
        raise ValueError(f"t_session {t_session} is not the session of t {inputs.t.isoformat()}")
    names = sorted(set(security_ids))
    bars = inputs.bars.filter(
        pl.col("known_at") <= inputs.t,
        pl.col("security_id").is_in(names),
        pl.col("session").is_in(sessions),
    ).select("security_id", "session", "volume")
    if bars.select(pl.struct("security_id", "session").is_duplicated().any()).item():
        raise ValueError("turnover bars have a duplicate (security_id, session) bar")
    volumes: dict[str, dict[date, float]] = {}
    for sid, session, volume in bars.iter_rows():
        if volume is not None and math.isfinite(volume) and volume > 0:
            volumes.setdefault(sid, {})[session] = float(volume)
    for sid, splits in inputs.splits.items():
        for ex_date, ratio in splits:
            if not (math.isfinite(ratio) and ratio > 0):
                raise ValueError(f"split ratio of {sid} on {ex_date} must be positive: {ratio}")

    turnover: dict[str, float] = {}
    unusable: list[str] = []
    for sid in names:
        pick, by_session = inputs.shares.get(sid), volumes.get(sid, {})
        if (
            pick is None
            or not (math.isfinite(pick[1]) and pick[1] > 0)
            or any(s not in by_session for s in sessions)
        ):
            unusable.append(sid)
            continue
        splits = inputs.splits.get(sid, ())
        shares = pick[1] * _split_factor(splits, pick[0], t_session)
        volume = math.fsum(by_session[s] * _split_factor(splits, s, t_session) for s in sessions)
        turnover[sid] = volume / shares
    ranked = sorted(turnover, key=lambda sid: (-turnover[sid], sid))
    kept = set(ranked[: _selected_count(len(ranked), fraction)])
    return TurnoverScreen(
        kept=tuple(sid for sid in names if sid in kept),
        excluded=tuple(sid for sid in names if sid not in kept),
        unusable=tuple(unusable),
        turnover=turnover,
    )


ProfitabilityExclusion = Literal["sector", "no_facts", "stale_facts", "malformed"]
_PROFITABILITY_EXCLUSIONS: tuple[ProfitabilityExclusion, ...] = (
    "sector",
    "no_facts",
    "stale_facts",
    "malformed",
)
#: The numerator fact per implemented `profitability.basis`; `cash`
#: (`operating_cash_flow`) arrives with B3b's file.
_PROFITABILITY_NUMERATOR: dict[str, str] = {"gross": "gross_profit"}
_PROFITABILITY_DENOMINATOR = "total_assets"
#: `statement_facts`' key per name (its `UNIQUE` with `security_id` for `cik`).
_FACT_KEY = ("security_id", "fact_name", "period_end", "period_days")
_FACT_COLUMNS_READ = (*_FACT_KEY, "period_start", "value", "basis", "known_at")


@dataclass(frozen=True)
class ProfitabilitySignal:
    """Scores of the names that passed rules 1 to 5, and every other requested name
    under the reason that excluded it (spec amendment #720).

    `scores` is ordered by `security_id`; each `excluded` tuple too, with every reason
    present. `derived` lists the scored names whose numerator row has
    `basis = derived` (`n_derived`).
    """

    scores: dict[str, float]
    excluded: dict[ProfitabilityExclusion, tuple[str, ...]]
    derived: tuple[str, ...]

    @property
    def ranked(self) -> tuple[str, ...]:
        """Scored names by score descending, ties by `security_id` ascending (rule 6),
        the order `portfolio.target_weights` selects from."""
        return tuple(sorted(self.scores, key=lambda sid: (-self.scores[sid], sid)))

    @property
    def counts(self) -> dict[str, int]:
        """The six `trial_rebalances` counts this signal reports (spec amendment #720)."""
        return {
            "n_ranked": len(self.scores),
            **{
                f"n_excluded_{reason}": len(self.excluded[reason])
                for reason in _PROFITABILITY_EXCLUSIONS
            },
            "n_derived": len(self.derived),
        }


def _profitability_in_sector(sic: int | None, ranges: Sequence[tuple[int, int]]) -> bool:
    return sic is not None and any(low <= sic <= high for low, high in ranges)


def gross_profitability(
    facts: pl.DataFrame,
    sics: Mapping[str, int | None],
    t: datetime,
    *,
    security_ids: Collection[str],
    annual_period_days: tuple[int, int],
    max_fact_age_days: int,
    exclude_sic_ranges: Sequence[tuple[int, int]],
    include_derived: bool,
    basis: str,
) -> ProfitabilitySignal:
    """Gross profitability scores at read time `t` (spec amendment #720, rules 0 to 6).

    `facts` is shaped like `statement_facts_as_of(t, ids)` (`security_id`,
    `fact_name`, `period_start`, `period_end`, `period_days`, `value`, `basis`,
    `known_at`); `sics` maps a name to its SIC known at `t` (missing or `None`: no SIC).
    For each name in `security_ids`, in this order:

    0. rows with `known_at > t` are dropped (the frame is not trusted to be as-of);
    1. a SIC inside any inclusive `exclude_sic_ranges` range: `sector`;
    2. the numerator: among the name's annual rows (`period_days` inside the inclusive
       `annual_period_days`), the latest `period_end`, the larger `period_days` on a
       tie; `basis = derived` rows only when `include_derived`. None: `no_facts`;
    3. the `total_assets` instant row at the same `period_end`. None: `no_facts`;
    4. `t`'s session minus `period_end` over `max_fact_age_days`: `stale_facts`;
    5. a non-finite numerator, or a non-positive or non-finite denominator: `malformed`;
    6. otherwise scored numerator / denominator (`ranked` gives rule 6's order).

    A tz-aware `t` in any zone is read as the same instant in UTC. `t` must be a
    session close (`close(T)`, half days included): any other instant would measure
    rule 4's freshness from the previous session (#1084).

    Raises `ValueError` for a naive `t` (or one out of UTC's range), a `t` that is
    not a session close, a `basis` other than `gross`, a frame missing a column, or a duplicate
    `(security_id, fact_name, period_end, period_days)` row.
    """
    t = ensure_tz_aware_utc(t, field_name="t")  # `known_at` is UTC; polars needs one zone
    t_session = last_completed_session(t)
    t_close = session_close(t_session)
    if t != t_close:
        raise ValueError(
            f"t {t.isoformat()} is not a session close; the last close at or before it "
            f"is {t_close.isoformat()} ({t_session.isoformat()})"
        )
    if basis not in _PROFITABILITY_NUMERATOR:
        raise ValueError(f"profitability basis {basis!r} is not implemented; only 'gross'")
    missing = [c for c in _FACT_COLUMNS_READ if c not in facts.columns]
    if missing:
        raise ValueError(f"facts frame lacks columns {missing}")
    numerator_name = _PROFITABILITY_NUMERATOR[basis]
    low_days, high_days = annual_period_days
    names = sorted(set(security_ids))

    visible = facts.select(_FACT_COLUMNS_READ).filter(
        pl.col("known_at") <= t, pl.col("security_id").is_in(names)
    )
    if visible.select(pl.struct(_FACT_KEY).is_duplicated().any()).item():
        raise ValueError(
            "facts frame has a duplicate (security_id, fact_name, period_end, period_days) row"
        )
    numerators = visible.filter(
        pl.col("fact_name") == numerator_name,
        pl.col("period_days").is_between(low_days, high_days),
        pl.lit(include_derived) | (pl.col("basis") != "derived"),
    ).sort("security_id", "period_end", "period_days", descending=[False, True, True])
    best: dict[str, tuple[date, float, bool]] = {}
    for sid, period_end, value, row_basis in numerators.select(
        "security_id", "period_end", "value", "basis"
    ).iter_rows():
        best.setdefault(sid, (period_end, value, row_basis == "derived"))
    # An instant row (`period_days = 0`) is the one with no `period_start`: the
    # table's CHECK ties the two, and this keeps a period length out of the code.
    assets: dict[tuple[str, date], float] = {
        (sid, period_end): value
        for sid, period_end, value in visible.filter(
            pl.col("fact_name") == _PROFITABILITY_DENOMINATOR, pl.col("period_start").is_null()
        )
        .select("security_id", "period_end", "value")
        .iter_rows()
    }

    scores: dict[str, float] = {}
    derived: list[str] = []
    excluded: dict[ProfitabilityExclusion, list[str]] = {r: [] for r in _PROFITABILITY_EXCLUSIONS}
    for sid in names:
        if _profitability_in_sector(sics.get(sid), exclude_sic_ranges):
            excluded["sector"].append(sid)
            continue
        numerator = best.get(sid)
        denominator = None if numerator is None else assets.get((sid, numerator[0]))
        if numerator is None or denominator is None:
            excluded["no_facts"].append(sid)
            continue
        period_end, value, is_derived = numerator
        if (t_session - period_end).days > max_fact_age_days:
            excluded["stale_facts"].append(sid)
            continue
        if not (math.isfinite(value) and math.isfinite(denominator) and denominator > 0):
            excluded["malformed"].append(sid)
            continue
        scores[sid] = value / denominator
        if is_derived:
            derived.append(sid)
    return ProfitabilitySignal(
        scores=scores,
        excluded={reason: tuple(ids) for reason, ids in excluded.items()},
        derived=tuple(derived),
    )
