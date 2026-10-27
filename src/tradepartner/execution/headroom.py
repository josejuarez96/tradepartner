"""The Risk card's numbers: each limit the run enforces, how close the book is
to it, and what crossing it does (plan T260, amendment 2026-10-20 #2096 item 3).

`compute` is pure over the window, its last marked session's `positions_daily`
rows, its `kill_switch` rows and its frozen `RiskConfig`. `read(conn, window)`
gathers those (`journal.positions_daily_for`, `journal.kill_switch_events_for`,
`reconcile_run.frozen_risk`), plus the window's frozen `portfolio.side` and the
largest name's ticker, and calls it. Nothing here writes a row, reads a clock or
calls a broker.

Four rows, each with the value, the window's frozen limit (never live
`Settings`: the limit the run enforces), the headroom (limit - value, in
percentage points) and what the limit does:

- **Cash**: the ledger's cash less the held shorts' value, floored at 0 as
  `run._unspent_cash` computes it, as a share of equity, against
  `risk.max_unspent_cash_fraction`. The alert reads the broker's cash; this row
  reads the ledger's.
- **Gross exposure**: the sum of |value| over equity, against
  `risk.max_gross_exposure`.
- **Largest position**: the largest |value| over equity, against
  `risk.max_position_weight` (risk.py rule 3: a buy of it is refused; drift
  alone never halts).
- **Drawdown**: (peak - equity) / peak with the rule's peak
  (`switch.drawdown_peak`), against `risk.max_drawdown`. The card also gives the
  dollars equity can fall before the switch engages, equity - peak x (1 -
  limit), and reads `engaged` while `switch.drawdown_armed` is false.

Equity is `marks.equity_at` at the last marked session; before the first mark it
is the window's starting equity, with its starting cash and no position. A row is
`within` only strictly inside its limit (open question 5: no warning band; at the
limit counts as at or over it).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal

import duckdb
import polars as pl

from tradepartner.backtest.frozen import frozen_values
from tradepartner.calendar import session_close
from tradepartner.config import RiskConfig
from tradepartner.execution.marks import equity_at
from tradepartner.execution.reconcile_run import frozen_risk
from tradepartner.execution.switch import drawdown_armed, drawdown_peak
from tradepartner.store import journal, registry
from tradepartner.store.asof import listings_as_of
from tradepartner.store.journal import KillSwitchRow, PaperWindowRow, PositionDailyRow

RowStatus = Literal["within", "over", "engaged"]

_SHORT = "short"
_LONG_SHORT = "long_short"
_SIDE_KEY = "portfolio.side"

_CASH_DOES = (
    "an unspent_cash alert after a rebalance (the alert reads the broker's cash, "
    "this row the ledger's)"
)
_GROSS_DOES = "a buy batch above it halts"
_POSITION_DOES = "a buy of it is refused; drift alone never halts"
_DRAWDOWN_DOES = "the kill switch engages"


@dataclass(frozen=True)
class LimitRow:
    """One limit: `value` and `limit` as fractions, `headroom_pp` = (limit -
    value) x 100 (negative when over), and what crossing the limit does."""

    key: str
    label: str
    value: float
    limit: float
    headroom_pp: float
    status: RowStatus
    does: str


@dataclass(frozen=True)
class Headroom:
    """The Risk card: the four `rows` (cash, gross, position, drawdown) at the
    close of `session` (None before the first mark), the equity and cash read,
    the largest name, the drawdown peak and the dollars left before the switch."""

    session: date | None
    equity: float
    cash: float
    rows: tuple[LimitRow, ...]
    largest_security_id: str | None
    largest_ticker: str | None
    peak: float
    dollars_before_switch: float
    drawdown_engaged: bool
    long_short: bool


def _status(value: float, limit: float) -> RowStatus:
    return "within" if value < limit and not math.isclose(value, limit) else "over"


def _row(key: str, label: str, value: float, limit: float, does: str) -> LimitRow:
    return LimitRow(
        key=key,
        label=label,
        value=value,
        limit=limit,
        headroom_pp=(limit - value) * 100.0,
        status=_status(value, limit),
        does=does,
    )


def compute(
    window: PaperWindowRow,
    marks: Sequence[PositionDailyRow],
    kill_switch_rows: Sequence[KillSwitchRow],
    risk: RiskConfig,
    *,
    side: str = "long_only",
    tickers: Mapping[str, str] | None = None,
) -> Headroom:
    """The Risk card over `marks` (only the latest session among them is read;
    none means before the first mark) and the window's `kill_switch_rows`, with
    the frozen `risk` limits (module docstring). Raises `UnreadableMarkError`
    when the session's rows cannot state equity, and `ValueError` when equity is
    not positive or the drawdown peak is unusable."""
    if window.window_id is None:
        raise ValueError("the window has no window_id")
    session = max((r.session for r in marks), default=None)
    held: list[PositionDailyRow] = []
    if session is None:
        equity, cash = window.starting_equity, window.starting_cash
    else:
        today = [r for r in marks if r.session == session]
        equity = equity_at(today, session)
        cash = today[0].cash if today[0].cash is not None else math.nan
        held = [r for r in today if r.security_id is not None]
    if not (math.isfinite(equity) and equity > 0):
        raise ValueError(f"window {window.window_id} equity {equity!r} is not positive")
    values = {r.security_id: r.value or 0.0 for r in held if r.security_id is not None}
    shorts = math.fsum(
        abs(r.value or 0.0) for r in held if r.position_side == _SHORT or (r.value or 0.0) < 0
    )
    idle = max(cash - shorts, 0.0)
    gross = math.fsum(abs(v) for v in values.values())
    largest = min(values, key=lambda sid: (-abs(values[sid]), sid)) if values else None
    largest_weight = abs(values[largest]) / equity if largest is not None else 0.0

    own = [r for r in kill_switch_rows if r.window_id == window.window_id]
    peak = drawdown_peak(window, own)
    engaged = not drawdown_armed(window.window_id, own)
    drawdown = (peak - equity) / peak
    drawdown_row = _row("drawdown", "Drawdown", drawdown, risk.max_drawdown, _DRAWDOWN_DOES)
    if engaged:
        drawdown_row = replace(drawdown_row, status="engaged")
    rows = (
        _row("cash", "Cash", idle / equity, risk.max_unspent_cash_fraction, _CASH_DOES),
        _row("gross", "Gross exposure", gross / equity, risk.max_gross_exposure, _GROSS_DOES),
        _row(
            "position",
            "Largest position",
            largest_weight,
            risk.max_position_weight,
            _POSITION_DOES,
        ),
        drawdown_row,
    )
    return Headroom(
        session=session,
        equity=equity,
        cash=cash,
        rows=rows,
        largest_security_id=largest,
        largest_ticker=(tickers or {}).get(largest) if largest is not None else None,
        peak=peak,
        dollars_before_switch=equity - peak * (1.0 - risk.max_drawdown),
        drawdown_engaged=engaged,
        long_short=side == _LONG_SHORT,
    )


def _side(conn: duckdb.DuckDBPyConnection, window: PaperWindowRow) -> str:
    """The window's hypothesis's frozen `portfolio.side`; `ValueError` when the
    hypothesis cannot be read."""
    try:
        hypothesis = registry.get_hypothesis_by_id(conn, window.hypothesis_id)
    except registry.RegistryError as exc:
        raise ValueError(f"window {window.window_id}: {exc}") from exc
    return str(frozen_values(hypothesis).get(_SIDE_KEY, "long_only"))


def _ticker(conn: duckdb.DuckDBPyConnection, security_id: str, session: date) -> str | None:
    """The ticker listed for `security_id` as known at `session`'s close."""
    listings = listings_as_of(conn, session_close(session), [security_id])
    known = listings.filter(pl.col("valid_from") <= session).sort("valid_from")
    return str(known["ticker"][-1]) if known.height else None


def read(conn: duckdb.DuckDBPyConnection, window: PaperWindowRow) -> Headroom:
    """The Risk card for `window` from the store (module docstring); writes
    nothing. Raises `ValueError` for an unreadable `frozen_json` or hypothesis,
    and `UnreadableMarkError` (a `ValueError`) for an unreadable mark."""
    if window.window_id is None:
        raise ValueError("the window has no window_id")
    risk = frozen_risk(window)
    side = _side(conn, window)
    marks = journal.positions_daily_for(conn, window.window_id)
    session = marks[-1].session if marks else None
    last = [r for r in marks if r.session == session]
    kill_switch_rows = journal.kill_switch_events_for(conn, window.window_id)
    card = compute(window, last, kill_switch_rows, risk, side=side)
    if card.largest_security_id is None or card.session is None:
        return card
    return replace(card, largest_ticker=_ticker(conn, card.largest_security_id, card.session))
