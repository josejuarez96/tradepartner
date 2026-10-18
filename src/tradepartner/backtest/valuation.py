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


def _held_bars(frame: pl.DataFrame, positions: Mapping[str, float]) -> pl.DataFrame:
    """The held names' bars, sorted by `security_id`, `session`: `security_id`, `session`,
    `close` and, when the frame has it, `open`. Raises `ValueError` on a duplicate
    `(security_id, session)` bar or a null `security_id`, `session` or `close`."""
    columns = ["security_id", "session", "close", *(["open"] if "open" in frame.columns else [])]
    bars = frame.select(columns).filter(pl.col("security_id").is_in(sorted(positions)))
    if bars.select(pl.struct("security_id", "session").is_duplicated().any()).item():
        raise ValueError("marking frame has a duplicate (security_id, session) bar")
    if bars.select(["security_id", "session", "close"]).null_count().sum_horizontal().item():
        raise ValueError("marking frame has a null security_id, session or close")
    return bars.sort("security_id", "session")


def _check_prices(frame: pl.DataFrame, field: str, price: str = "price") -> None:
    """Raise `ValueError` (as `_check_price`) for the first row, in frame order, whose
    `price` column is null, not finite or not positive."""
    bad = frame.filter(
        pl.col(price).is_null() | ~(pl.col(price).is_finite() & (pl.col(price) > 0))
    ).head(1)
    for sid, session, value in bad.select("security_id", "session", price).iter_rows():
        _check_price(sid, session, field, value)


def _price_at_or_before(
    bars: pl.DataFrame,
    held: pl.DataFrame,
    session: date,
    fill_price: FillPrice,
) -> pl.DataFrame:
    """Each held name's price at its last bar at or before `session`: `security_id`,
    `base` (the fill price when that bar is on `session`, else its close, both checked
    positive and finite) and `base_session`, the bar's session.

    Raises `ValueError` for a held name with no bar at or before `session`."""
    last = (
        bars.filter(pl.col("session") <= session)
        .group_by("security_id")
        .agg(pl.all().sort_by("session").last())
    )
    joined = held.select("security_id").join(last, on="security_id", how="left").sort("security_id")
    missing = joined.filter(pl.col("session").is_null()).head(1)
    for (sid,) in missing.select("security_id").iter_rows():
        raise ValueError(f"no bar for {sid} at or before {session.isoformat()}")
    on_day = pl.col("session") == session
    joined = joined.with_columns(
        price=pl.when(on_day).then(pl.col(fill_price)).otherwise(pl.col("close"))
    )
    _check_prices(joined.filter(on_day), fill_price)
    _check_prices(joined.filter(~on_day), "close")
    return joined.select("security_id", pl.col("price").alias("base"), base_session="session")


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
    if not positions:
        return pl.DataFrame(schema=_VALUE_SCHEMA)
    bars = _held_bars(marking_frame, positions)
    ids = sorted(positions)
    held = pl.DataFrame({"security_id": ids, "position": [positions[sid] for sid in ids]})
    base = _price_at_or_before(bars, held, fill_session, fill_price)
    grid = held.join(base, on="security_id").join(
        pl.DataFrame({"session": sessions}, schema={"session": pl.Date}), how="cross"
    )
    marked = grid.join(
        bars.select("security_id", "session", "close"),
        on=["security_id", "session"],
        how="left",
    ).sort("security_id", "session")
    _check_prices(marked.drop_nulls("close"), "close", "close")
    has_bar = pl.col("close").is_not_null()
    return (
        marked.with_columns(
            value=pl.when(has_bar).then(pl.col("position") * pl.col("close") / pl.col("base")),
            marked_at=pl.when(has_bar).then(pl.col("session")),
        )
        .with_columns(
            pl.col("value").forward_fill().over("security_id").fill_null(pl.col("position")),
            pl.col("marked_at")
            .forward_fill()
            .over("security_id")
            .fill_null(pl.col("base_session")),
        )
        .select(list(_VALUE_SCHEMA))
    )


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
    ids = sorted(positions)
    for sid in ids:
        mark = marked_at.get(sid)
        if mark is None or mark > rebalance_session:
            raise ValueError(
                f"{sid} needs a marked_at session at or before {rebalance_session.isoformat()}, "
                f"got {mark}"
            )
    if not ids:
        return {}
    bars = _held_bars(marking_frame, positions)
    held = pl.DataFrame(
        {
            "security_id": ids,
            "position": [positions[sid] for sid in ids],
            "mark": [marked_at[sid] for sid in ids],
        },
        schema={"security_id": pl.Utf8, "position": pl.Float64, "mark": pl.Date},
    )
    marks = held.join(
        bars.select("security_id", pl.col("session").alias("mark"), pl.col("close").alias("base")),
        on=["security_id", "mark"],
        how="left",
    )
    for sid, mark in (
        marks.filter(pl.col("base").is_null()).select("security_id", "mark").head(1).iter_rows()
    ):
        raise ValueError(f"no bar for {sid} on its last mark {mark.isoformat()}")
    _check_prices(marks.rename({"mark": "session"}), "close", "base")
    target = _price_at_or_before(bars, held, fill_session, fill_price)
    both = marks.join(
        target.select("security_id", pl.col("base").alias("target")), on="security_id"
    )
    carried = both.sort("security_id").with_columns(
        carried=pl.col("position") * pl.col("target") / pl.col("base")
    )
    return dict(carried.select("security_id", "carried").iter_rows())


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
