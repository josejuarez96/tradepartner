"""12-1 momentum signal (backtest spec req 3, handoff H1).

Score of a name at rebalance session `t_session` (the last session of month T):

    close(month-end of T - skip_months) / close(month-end of T - formation_months) - 1

where month-end is the last XNYS session of that month, over an adjusted price frame.
A name lacking either bar is excluded and counted.

Only rows with `session <= t_session` are read, so a frame that extends past the
rebalance (as a later as-of read might) cannot leak into the score. Which adjustment
the frame carries (splits only, or dividends too per `strategy.signal_total_return`)
is the caller's choice when it reads the frame.
"""

from __future__ import annotations

import math
from collections.abc import Collection
from dataclasses import dataclass
from datetime import date

import polars as pl
from dateutil.relativedelta import relativedelta

from tradepartner.calendar import last_session_of_month


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


def _month_end(t_session: date, months_back: int) -> date:
    month = t_session - relativedelta(months=months_back)
    return last_session_of_month(month.year, month.month)


def momentum_12_1(
    frame: pl.DataFrame,
    t_session: date,
    formation_months: int,
    skip_months: int,
    *,
    security_ids: Collection[str] | None = None,
) -> MomentumSignal:
    """Momentum scores at `t_session` from `frame` (`security_id`, `session`, `close`).

    `security_ids` names the names to score (the universe at `t`); by default every
    name in the frame. A requested name with no bar at all is excluded like one missing
    a single bar. Raises `ValueError` if `t_session` is not the last session of its
    month, if `formation_months <= skip_months` or `skip_months < 0`, if a bar is
    duplicated, or if a close used is not positive and finite.
    """
    if skip_months < 0 or formation_months <= skip_months:
        raise ValueError(
            f"need formation_months > skip_months >= 0, got "
            f"formation_months={formation_months}, skip_months={skip_months}"
        )
    if t_session != last_session_of_month(t_session.year, t_session.month):
        raise ValueError(f"t_session {t_session} is not the last session of its month")
    start_bar = _month_end(t_session, formation_months)
    end_bar = _month_end(t_session, skip_months)

    visible = frame.filter(pl.col("session") <= t_session)
    bars = visible.filter(pl.col("session").is_in([start_bar, end_bar])).select(
        "security_id", "session", "close"
    )
    if bars.select(pl.struct("security_id", "session").is_duplicated().any()).item():
        raise ValueError("frame has a duplicate (security_id, session) bar")

    closes: dict[tuple[str, date], float] = {
        (sid, session): close for sid, session, close in bars.iter_rows()
    }
    names = (
        sorted(set(security_ids))
        if security_ids is not None
        else sorted(set(visible.get_column("security_id").to_list()))
    )
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
