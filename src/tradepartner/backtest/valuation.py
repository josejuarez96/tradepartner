"""Position valuation within one marking frame, and stitched returns (backtest spec req 2).

Positions are dollar values, never share counts. Within step i every price ratio comes
from that step's marking frame alone (adjusted as of the step's read time, dividends
included), so a split inside the month cancels and a dividend is reinvested in the
paying name on its ex-date. This module does no adjustment of its own: the frame it is
handed decides every ratio.

- `value_positions`: dollar values bought at the fill price on F_i, marked to the close
  of every XNYS session from F_i through T_{i+1}.
- `carry_to_fill`: the drifted value at close(T_i), priced forward to F_i's fill price by
  the next step's frame from the session it was last marked at (the carry between steps).
- `stitched_returns`: one price index per name for the `bt` oracle, each session's ratio
  taken from the frame of the step that contains it.

A name with no bar on a session keeps its last mark. Pure functions; frames need
`security_id`, `session`, `close` and, for `open` fills, `open`.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, get_args

import polars as pl

from tradepartner.calendar import all_sessions, is_session, next_session

FillPrice = Literal["close", "open"]

_VALUE_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "session": pl.Date,
    "value": pl.Float64,
    "marked_at": pl.Date,
}
_STITCHED_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "session": pl.Date,
    "open": pl.Float64,
    "close": pl.Float64,
    "close_return": pl.Float64,
}


@dataclass(frozen=True)
class _Bar:
    session: date
    open: float | None
    close: float


@dataclass(frozen=True)
class StepFrame:
    """Step i's marking frame and the sessions it prices: `(start, end]`, from
    T_i to T_{i+1}. The first step starts at T_0, so it covers F_0."""

    start: date
    end: date
    frame: pl.DataFrame


def _check_fill_price(fill_price: str) -> None:
    if fill_price not in get_args(FillPrice):
        raise ValueError(f"fill_price must be one of {get_args(FillPrice)}, got {fill_price!r}")


def _check_positions(positions: Mapping[str, float]) -> None:
    for sid, value in positions.items():
        if not (math.isfinite(value) and value >= 0):
            raise ValueError(f"position {sid} must be non-negative and finite, got {value}")


def _check_price(sid: str, session: date, field: str, value: float | None) -> float:
    if value is None or not (math.isfinite(value) and value > 0):
        raise ValueError(
            f"{field} for {sid} on {session.isoformat()} must be positive and finite, got {value}"
        )
    return value


def _bars(frame: pl.DataFrame, ids: set[str] | None = None) -> dict[str, list[_Bar]]:
    """Bars per `security_id`, ascending by session; `ids` limits the names read."""
    has_open = "open" in frame.columns
    columns = ["security_id", "session", "close", *(["open"] if has_open else [])]
    selected = frame.select(columns)
    if ids is not None:
        selected = selected.filter(pl.col("security_id").is_in(sorted(ids)))
    if selected.select(pl.struct("security_id", "session").is_duplicated().any()).item():
        raise ValueError("marking frame has a duplicate (security_id, session) bar")
    if selected.select(["security_id", "session", "close"]).null_count().sum_horizontal().item():
        raise ValueError("marking frame has a null security_id, session or close")
    bars: dict[str, list[_Bar]] = {}
    for row in selected.sort("security_id", "session").iter_rows(named=True):
        bar = _Bar(session=row["session"], open=row.get("open"), close=row["close"])
        bars.setdefault(row["security_id"], []).append(bar)
    return bars


def _last_at_or_before(series: Sequence[_Bar], session: date) -> _Bar | None:
    index = bisect.bisect_right([bar.session for bar in series], session)
    return series[index - 1] if index else None


def _fill_value(sid: str, bar: _Bar, fill_price: FillPrice) -> float:
    value = bar.open if fill_price == "open" else bar.close
    return _check_price(sid, bar.session, fill_price, value)


def _sessions(first: date, last: date) -> list[date]:
    """XNYS sessions from `first` (a session) through `last`, inclusive."""
    if not is_session(first):
        raise ValueError(f"fill_session {first.isoformat()} is not an XNYS session")
    if last < first:
        raise ValueError(f"through {last.isoformat()} is before fill_session {first.isoformat()}")
    sessions = all_sessions()
    return list(sessions[bisect.bisect_left(sessions, first) : bisect.bisect_right(sessions, last)])


def value_positions(
    positions: Mapping[str, float],
    marking_frame: pl.DataFrame,
    fill_session: date,
    *,
    fill_price: FillPrice,
    through: date,
) -> pl.DataFrame:
    """Dollar value of each position at the close of every session in
    `[fill_session, through]`: `security_id`, `session`, `value`, and `marked_at`, the
    session of the bar the value is marked at (the next step's `carry_to_fill` needs it).

    `positions` are dollar values at the fill on `fill_session`, bought at its `open` or
    `close` (`fill_price`); at session s a position is worth value * close(s) / P(fill).
    A name with no bar on `fill_session` (an unsold holding whose fill was missed) is
    marked from its last close before it, as `carry_to_fill` left it. A name with no bar
    on a session keeps its last mark. Bars after `through` are ignored.

    Raises `ValueError` for a negative or non-finite position, a duplicate or null bar,
    a price used that is not positive and finite, a held name with no bar at or before
    `fill_session`, a `fill_session` that is not a session, or `through` before it.
    """
    _check_fill_price(fill_price)
    _check_positions(positions)
    sessions = _sessions(fill_session, through)
    bars = _bars(marking_frame, set(positions))
    rows: list[tuple[str, date, float, date]] = []
    for sid in sorted(positions):
        series = bars.get(sid, [])
        base_bar = _last_at_or_before(series, fill_session)
        if base_bar is None:
            raise ValueError(f"no bar for {sid} at or before {fill_session.isoformat()}")
        if base_bar.session == fill_session:
            base = _fill_value(sid, base_bar, fill_price)
        else:
            base = _check_price(sid, base_bar.session, "close", base_bar.close)
        closes = {bar.session: bar.close for bar in series}
        value, marked_at = positions[sid], base_bar.session
        for session in sessions:
            if session in closes:
                close = _check_price(sid, session, "close", closes[session])
                value, marked_at = positions[sid] * close / base, session
            rows.append((sid, session, value, marked_at))
    return pl.DataFrame(rows, schema=_VALUE_SCHEMA, orient="row")


def carry_to_fill(
    positions: Mapping[str, float],
    marking_frame: pl.DataFrame,
    rebalance_session: date,
    fill_session: date,
    *,
    fill_price: FillPrice,
    marked_at: Mapping[str, date],
) -> dict[str, float]:
    """Dollar values at close(`rebalance_session`) priced forward to the fill price on
    `fill_session` by `marking_frame`, the next step's frame (req 2's carry).

    Each value was last marked at session `marked_at[sid]` by the previous frame; the
    carry is value * P(target) / close(marked_at) with both prices from this frame, so a
    split known only now cancels and a bar that arrived late between the last mark and
    the fill is counted once. The target is the fill price on `fill_session`, or, with
    no bar there, the close of the name's last bar before it (its last mark).

    Raises `ValueError` if `fill_session` is not the session after `rebalance_session`,
    if a name has no `marked_at` or one after `rebalance_session`, or if this frame has
    no bar on a name's `marked_at` session (a retracted bar the value was marked at).
    """
    _check_fill_price(fill_price)
    _check_positions(positions)
    if fill_session != next_session(rebalance_session):
        raise ValueError(
            f"fill_session {fill_session.isoformat()} is not the next session after "
            f"rebalance_session {rebalance_session.isoformat()}"
        )
    bars = _bars(marking_frame, set(positions))
    carried: dict[str, float] = {}
    for sid in sorted(positions):
        mark = marked_at.get(sid)
        if mark is None or mark > rebalance_session:
            raise ValueError(
                f"{sid} needs a marked_at session at or before {rebalance_session.isoformat()}, "
                f"got {mark}"
            )
        series = bars.get(sid, [])
        closes = {bar.session: bar.close for bar in series}
        if mark not in closes:
            raise ValueError(f"no bar for {sid} on its last mark {mark.isoformat()}")
        base = _check_price(sid, mark, "close", closes[mark])
        target_bar = _last_at_or_before(series, fill_session)
        if target_bar is None:  # unreachable: the bar on `mark` is at or before it
            raise ValueError(f"no bar for {sid} at or before {fill_session.isoformat()}")
        if target_bar.session == fill_session:
            target = _fill_value(sid, target_bar, fill_price)
        else:
            target = _check_price(sid, target_bar.session, "close", target_bar.close)
        carried[sid] = positions[sid] * target / base
    return carried


def stitched_returns(steps: Sequence[StepFrame]) -> pl.DataFrame:
    """One price index per name across steps: `security_id`, `session`, `open`, `close`
    (index levels) and `close_return`, for every bar in each step's `(start, end]`.

    Each session's ratio, close(s) / close(previous bar) and open(s) / close(previous
    bar), comes from the frame of the step that contains s, the previous bar being the
    name's last bar in that same frame. So index ratios inside one step reproduce that
    frame's ratios, and a revision in a later frame never reaches an earlier step. A
    name seen for the first time is anchored at its last bar at or before that step's
    start, the index there being the frame's close; a name whose first bar falls inside
    the step starts there with no `close_return`. A name seen before continues from its
    last stitched level L at session h: the level at the step's anchor bar a is
    L * close(a) / close(h), both closes from the current frame, so the index stays
    continuous even across steps whose frames did not carry the name.
    `close_return` is against the frame's previous bar, which may have no stitched row
    (a late bar); consumers such as `bt` should use the level columns.

    Raises `ValueError` if a step's `start` is not before its `end`, if the steps are
    not contiguous and ascending, if a frame lacks the bar on a name's last stitched
    session (a retracted bar), or on a bad bar (as `value_positions`).
    """
    for index, step in enumerate(steps):
        if step.start >= step.end:
            raise ValueError(
                f"step start {step.start.isoformat()} must be before end {step.end.isoformat()}"
            )
        if index and step.start != steps[index - 1].end:
            raise ValueError(
                "steps must be contiguous and ascending: each starts where the last ends"
            )
    last_level: dict[str, tuple[date, float]] = {}
    rows: list[tuple[str, date, float | None, float, float | None]] = []
    for step in steps:
        for sid, series in _bars(step.frame).items():
            inside = [bar for bar in series if step.start < bar.session <= step.end]
            if not inside:
                continue
            closes = {bar.session: bar.close for bar in series}
            anchor_bar = _last_at_or_before(series, step.start)
            if sid in last_level:
                h, level_h = last_level[sid]
                if h not in closes or anchor_bar is None:
                    raise ValueError(
                        f"frame for the step ending {step.end.isoformat()} has no bar for "
                        f"{sid} on {h.isoformat()}, its last stitched session"
                    )
                prev_close = _check_price(sid, anchor_bar.session, "close", anchor_bar.close)
                prev_level = level_h * prev_close / _check_price(sid, h, "close", closes[h])
            elif anchor_bar is None:
                first = inside[0]
                prev_close = prev_level = _check_price(sid, first.session, "close", first.close)
                rows.append((sid, first.session, first.open, prev_close, None))
                last_level[sid] = (first.session, prev_level)
                inside = inside[1:]
            else:
                prev_close = prev_level = _check_price(
                    sid, anchor_bar.session, "close", anchor_bar.close
                )
            for bar in inside:
                close = _check_price(sid, bar.session, "close", bar.close)
                ratio = close / prev_close
                level = prev_level * ratio
                open_level = None if bar.open is None else prev_level * bar.open / prev_close
                rows.append((sid, bar.session, open_level, level, ratio - 1))
                last_level[sid] = (bar.session, level)
                prev_close, prev_level = close, level
    return pl.DataFrame(rows, schema=_STITCHED_SCHEMA, orient="row").sort("security_id", "session")
