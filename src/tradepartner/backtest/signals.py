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
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl
from dateutil.relativedelta import relativedelta

from tradepartner.calendar import is_session, last_session_of_month, next_session, previous_session
from tradepartner.config import Cadence, SignalAnchor

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
