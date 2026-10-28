"""The ledger: the positions and cash the journal implies (Phase 4 spec,
Definitions > Ledger; plan T51).

The ledger is never stored as truth. It is rebuilt, per paper window, by one
pure function over journal rows the caller has already read, stated for one
session `through`:

- **Positions** per `security_id` come from the live fills (`store.journal.fills_for`,
  so a superseded fill is never counted twice) and the journaled adjustments
  (`carried_residue`, `spinoff_receipt`, `merger_receipt`, `corporate_action_cash`,
  `dividend_cash`) dated on or before `through`, each split-adjusted by the
  splits in `actions_as_of` whose ex-date falls after the row's own date and
  on or before `through`. The caller reads `actions_as_of` with
  `store.asof.live_actions_as_of(conn, close(S-1))`, so a split known only
  after that cut is never applied (no look-ahead). Rows dated after `through`
  are left out, so an earlier session's ledger (a back-filled mark) can be
  rebuilt from the whole window's rows.
- **Cash** is the `broker_cash` of the window's last `ok` or `breaks` reconciliation plus
  every fill and adjustment delta known after it, or `starting_cash` plus every
  delta before the first one. A row whose `known_at` equals the
  reconciliation's is inside it. A base taken after `through` cannot state
  `through` and is refused.

Conventions this module fixes for the writers (T61 reconcile, T64 window start):

- An adjustment's `quantity` and `cash` are **signed deltas** applied as
  written. A spin-off receipt or a carried residue adds shares; an ended
  listing's `corporate_action_cash` row removes them with a negative
  `quantity` and credits the proceeds as positive `cash`; a `dividend_cash`
  row carries `cash` only.
- An adjustment's `quantity` is in the share units of its `session`: adjusted
  by the splits with `ex_date <= session`, never by a later one (the ledger
  applies those).
- An adjustment that explains a reconciliation's cash is journaled with a
  `known_at` at or before that reconciliation's `known_at`, since the
  reconciliation's `broker_cash` already holds it; a later stamp would count
  the cash twice.
- A reconciliation is `ok` only when every fill the broker has booked is
  collected (the `fills_lagging` status exists for the other case), so a fill
  known after an `ok` reconciliation was not in its `broker_cash`.
- A fill journaled after a reconciliation is stamped strictly after that
  reconciliation's `known_at`: `store.journal.append` refuses a fill at or
  before the latest one (#650). So a fill whose `known_at` ties the base was
  journaled before it and is in its `broker_cash`, and the strict comparison
  in `from_journal` never drops a later fill.

- **Short holdings (T232, paper spec amendment #1872 item 10).** A fill of an
  order whose `position_side` is `short` (a `sell_to_open` sell, a
  `buy_to_close` buy) moves `Ledger.short_holdings`, an unsigned quantity per
  `security_id` apart from `positions`, so a long-side oversell stays the
  negative position `short_names` reports. Cash is signed as for any fill:
  a short sale's proceeds are credited and a cover debited. `equity` is cash +
  long value - short value at the marks, and `free_cash` (ADR 0010's restated
  cash rule) is cash less the short value at the same marks; the rule's other
  term, the unfilled notional of our own non-terminal buys, is the phase
  sizing's (T230). A cover beyond the held short is a journal error
  (`ValueError`), and a fully covered short is dropped like a zero position.

- **True-ups (paper spec amendment 2026-10-26 #2266, items 6 and 9).** An
  `auto_true_up` or `owner_true_up` row with a quantity states its name at the
  broker's **absolute** quantity (`explanation_json` key `broker_quantity`; the
  row's `quantity` is only the delta). It is replayed at its session, after
  that session's split and fills, and overwrites the position: only a split
  with a later `ex_date` acts on it, so a split row that lands late with an
  earlier `ex_date` (already in the broker's figure) is never applied on top.
  Its cash part is an ordinary row, counted when known after the base.

A row's own date for the split test is its `session` (an adjustment) or the
New York date of `filled_at` (a fill): a fill on the ex-date is already in
post-split shares. Quantities within `quantity_tolerance` of zero
(`risk.reconcile_quantity_tolerance` from the frozen window) are dropped.
Positions are summed exactly, as `Fraction`s: each quantity from its shortest
decimal form and each split ratio as the nearest small fraction (1/3, not
0.3333333333333333). So fills on the broker's quantity grid add up exactly,
before or after a split, and a full exit's `risk.round_down` sells the whole
holding, not one grid step less (#1296).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from fractions import Fraction
from zoneinfo import ZoneInfo

import polars as pl

from tradepartner.store.journal import AdjustmentRow, OrderedFill, OrderRow, ReconciliationRow
from tradepartner.store.schema import (
    AUTO_TRUE_UP_KIND,
    JOURNAL_ENUMS,
    LONG,
    OWNER_TRUE_UP_KIND,
)

_NEW_YORK = ZoneInfo("America/New_York")
_STAMP_FLOOR = datetime.min.replace(tzinfo=UTC)
_SPLIT = "split"
_OK = "ok"
_BUY = "buy"
_SELL = "sell"
_CASH_IN_LIEU = "cash_in_lieu"
_SHORT_FILL = "short_fill"
_TRUE_UP = "true_up"
#: The adjustment kinds that state a name at the broker's absolute quantity
#: (paper spec amendment 2026-10-26 #2266, item 9).
TRUE_UP_KINDS = frozenset({AUTO_TRUE_UP_KIND, OWNER_TRUE_UP_KIND})
_ADJUSTMENT_KINDS = frozenset(JOURNAL_ENUMS[("adjustments", "kind")])

#: The replay's ranks within one session (#1627): the session's split, then its
#: `cash_in_lieu` row, then a fill or an adjustment stated on it. Named because
#: `tests/test_no_literals.py` allows only 0, 1 and 2 (and minus 1) in
#: `execution/`, so the two later ranks are not numeric literals.
_REPLAY_SPLIT_RANK = "0_split"
_REPLAY_CASH_IN_LIEU_RANK = "1_cash_in_lieu"
_REPLAY_FILL_RANK = "2_fill"
_REPLAY_ADJUSTMENT_RANK = "3_adjustment"
#: A true-up states its name after every other row of its session; a fill
#: journaled after it (the broker's figure did not hold it) follows it.
_REPLAY_TRUE_UP_RANK = "4_true_up_and_late_fill"


def _finite(value: float, what: str) -> float:
    if not math.isfinite(value):
        raise ValueError(f"{what} is {value}, not a finite number")
    return value


@dataclass(frozen=True)
class Ledger:
    """Positions (`security_id` -> shares, dust dropped) and cash, stated for
    session `through`. A negative position is kept, so reconciliation can
    report it, but `equity` and `weights` refuse it: the system is long-only."""

    positions: Mapping[str, float]
    cash: float
    through: date
    #: Fills whose cash this ledger applied since its base (strictly after the
    #: base's row, stated on or before `through`); a ledger built by hand is 0.
    #: The cash-bound multiplier (`risk.reconcile_cash_tolerance` x (1 + this/2),
    #: #1583, T183) scales the whole-dollar rounding each fill can leave.
    fills_since_base: int = 0
    #: Shares held short per `security_id` (unsigned, dust dropped), from fills
    #: of `short` orders; empty for a long book and for a ledger built by hand.
    short_holdings: Mapping[str, float] = field(default_factory=dict)

    @property
    def short_names(self) -> tuple[str, ...]:
        """Names the journal implies are held short: always a journal error."""
        return tuple(sorted(name for name, quantity in self.positions.items() if quantity < 0))

    def _values(self, marks: Mapping[str, float]) -> dict[str, float]:
        if self.short_names:
            raise ValueError(f"negative positions in {list(self.short_names)}")
        held = sorted(self.positions)
        missing = [name for name in held if name not in marks]
        if missing:
            raise KeyError(f"no mark for held names {missing}")
        for name in held:
            if not math.isfinite(marks[name]) or marks[name] <= 0:
                raise ValueError(f"mark for {name} is {marks[name]}, not a positive number")
        return {name: self.positions[name] * marks[name] for name in held}

    def _short_value(self, marks: Mapping[str, float]) -> float:
        """The short holdings at their marks, under `_values`' mark refusals."""
        missing = sorted(name for name in self.short_holdings if name not in marks)
        if missing:
            raise KeyError(f"no mark for short names {missing}")
        total = 0.0
        for name in sorted(self.short_holdings):
            if not math.isfinite(marks[name]) or marks[name] <= 0:
                raise ValueError(f"mark for {name} is {marks[name]}, not a positive number")
            total += self.short_holdings[name] * marks[name]
        return total

    def equity(self, marks: Mapping[str, float]) -> float:
        """Cash plus every long position at its mark, less every short holding
        at its mark. A held name with no mark raises `KeyError`; a non-positive
        or non-finite mark, or a negative position, raises `ValueError`; marks
        for names not held are ignored."""
        return self.cash + sum(self._values(marks).values()) - self._short_value(marks)

    def free_cash(self, marks: Mapping[str, float]) -> float:
        """The cash the buys may use: cash less the short holdings at their
        marks (ADR 0010's restated cash rule, the ledger's part). The long
        value is never added. Mark refusals as for `equity`."""
        return self.cash - self._short_value(marks)

    def weights(self, marks: Mapping[str, float]) -> dict[str, float]:
        """Each held name's value over equity, under `equity`'s refusals.
        Equity that is not a positive number raises `ValueError`."""
        if self.short_holdings:
            raise ValueError(
                f"weights are not defined with short holdings {sorted(self.short_holdings)}"
            )
        values = self._values(marks)
        equity = self.cash + sum(values.values())
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError(f"equity is {equity}, weights need a positive equity")
        return {name: value / equity for name, value in values.items()}


def _splits_by_security(
    actions_as_of: pl.DataFrame, through: date
) -> dict[str, list[tuple[date, float]]]:
    splits: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for row in actions_as_of.iter_rows(named=True):
        if row["action_type"] == _SPLIT and row["ex_date"] <= through:
            ratio = _finite(float(row["ratio_or_amount"]), f"split ratio of {row['security_id']}")
            if ratio <= 0:
                raise ValueError(f"split of {row['security_id']} has ratio {ratio}")
            splits[row["security_id"]].append((row["ex_date"], ratio))
    return splits


def _split_ratio(ratio: float) -> Fraction:
    """`ratio` as the small fraction it stores: the nearest fraction with a
    denominator up to `Fraction.limit_denominator`'s default, when that
    fraction is the same `float` (a 1:3 split's 0.3333333333333333 is 1/3),
    else its shortest decimal form."""
    small = Fraction(ratio).limit_denominator()
    return small if float(small) == ratio else _exact(ratio)


def _exact(value: float) -> Fraction:
    """`value` as the exact `Fraction` of its shortest decimal form."""
    return Fraction(repr(value))


def _check_window(kind: str, row_window: int, window_id: int) -> None:
    if row_window != window_id:
        raise ValueError(f"{kind} of window {row_window} in a ledger of window {window_id}")


def _check_fill(fill: OrderedFill, orders: Mapping[str, OrderRow], seen: set[int]) -> None:
    row = fill.fill
    if row.fill_id is None or row.fill_id in seen:
        raise ValueError(
            f"fill id {row.fill_id!r} of {row.client_order_id!r} is missing or repeated"
        )
    seen.add(row.fill_id)
    order = orders.get(row.client_order_id)
    if order is None:
        raise ValueError(f"fill {row.fill_id} of {row.client_order_id!r} has no orders row")
    if (order.side, order.security_id) != (fill.side, fill.security_id):
        raise ValueError(
            f"fill {row.fill_id} ({fill.side} {fill.security_id}) disagrees with its "
            f"order {order.client_order_id!r} ({order.side} {order.security_id})"
        )
    if fill.side not in (_BUY, _SELL):
        raise ValueError(f"fill {row.fill_id} has side {fill.side!r}")
    if _finite(row.quantity, f"fill {row.fill_id} quantity") <= 0:
        raise ValueError(f"fill {row.fill_id} quantity is {row.quantity}, must be positive")
    # Mirrors the `fills` CHECKs: a `broker_feed` price is positive; an implied
    # (`broker_status`) residual price is whatever makes the order's total match
    # the broker's average price, so it is only required to be finite.
    if _finite(row.price, f"fill {row.fill_id} price") <= 0 and not row.price_implied:
        raise ValueError(f"fill {row.fill_id} price is {row.price}, must be positive")


def _check_adjustment(row: AdjustmentRow, seen: set[int]) -> None:
    if row.adjustment_id is None or row.adjustment_id in seen:
        raise ValueError(f"adjustment id {row.adjustment_id!r} is missing or repeated")
    seen.add(row.adjustment_id)
    if row.kind not in _ADJUSTMENT_KINDS:
        raise ValueError(f"adjustment {row.adjustment_id} has kind {row.kind!r}")
    if row.quantity is not None:
        if row.security_id is None:
            raise ValueError(f"adjustment {row.adjustment_id} has a quantity but no security_id")
        _finite(row.quantity, f"adjustment {row.adjustment_id} quantity")
    if row.cash is not None:
        _finite(row.cash, f"adjustment {row.adjustment_id} cash")


def true_up_quantity(row: AdjustmentRow) -> float | None:
    """The absolute broker quantity a true-up row states its name at: its
    `explanation_json` key `broker_quantity` (item 9: the row's own `quantity`
    is only the delta). None for a row with no quantity (a cash part or an
    acknowledged finding); `ValueError` when a quantity row lacks a finite
    absolute figure, so a true-up never guesses one."""
    if row.quantity is None:
        return None
    try:
        payload = json.loads(row.explanation_json or "")
    except ValueError:
        payload = None
    value = payload.get("broker_quantity") if isinstance(payload, dict) else None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(
            f"adjustment {row.adjustment_id} has no broker_quantity to state the name at"
        )
    return _finite(float(value), f"adjustment {row.adjustment_id} broker_quantity")


def _cash_base(
    reconciliation: ReconciliationRow | None, window_id: int, starting_cash: float, through: date
) -> tuple[float, datetime | None]:
    _finite(starting_cash, "starting_cash")
    if reconciliation is None:
        return starting_cash, None
    name = f"reconciliation {reconciliation.reconciliation_id}"
    _check_window("reconciliation", reconciliation.window_id, window_id)
    if reconciliation.status not in (_OK, "breaks"):
        raise ValueError(f"{name} is {reconciliation.status!r}, the cash base must be reconciled")
    if reconciliation.broker_cash is None:
        raise ValueError(f"{name} has no broker_cash")
    if reconciliation.at.astimezone(_NEW_YORK).date() > through:
        raise ValueError(f"{name} at {reconciliation.at} is after {through}, the ledger's session")
    return _finite(reconciliation.broker_cash, f"{name} broker_cash"), reconciliation.known_at


def from_journal(
    fills: Sequence[OrderedFill],
    orders: Sequence[OrderRow],
    adjustments: Sequence[AdjustmentRow],
    actions_as_of: pl.DataFrame,
    last_ok_reconciliation: ReconciliationRow | None,
    starting_cash: float,
    through: date,
    *,
    window_id: int,
    quantity_tolerance: float,
) -> Ledger:
    """Window `window_id`'s ledger stated for session `through` (module docstring).

    `fills` are the window's live fills from `store.journal.fills_for`; `orders`
    holds at least every fill's order. `adjustments` are the window's rows
    (`store.journal.adjustments_for`). `actions_as_of` is a `live_actions_as_of`
    frame read at close(S-1); only its `split` rows count. `last_ok_reconciliation`
    is the window's latest `ok` or `breaks` base stated on or before `through`, or None.
    `quantity_tolerance` is the frozen `risk.reconcile_quantity_tolerance`.

    Raises `ValueError` on: a row of another window; a fill without its order,
    disagreeing with it, or repeated; a repeated adjustment or an unknown kind;
    a base that is neither `ok` nor `breaks`, lacks `broker_cash` or is after `through`; any
    non-finite number or non-positive split ratio; a non-date `through`.
    """
    if isinstance(through, datetime) or not isinstance(through, date):
        raise ValueError(f"through must be a session date, got {type(through).__name__}")
    if _finite(quantity_tolerance, "quantity_tolerance") < 0:
        raise ValueError(f"quantity_tolerance is {quantity_tolerance}, must be at least 0")
    cash, base_known_at = _cash_base(last_ok_reconciliation, window_id, starting_cash, through)

    def after_base(known_at: datetime) -> bool:
        return base_known_at is None or known_at > base_known_at

    by_id = {order.client_order_id: order for order in orders}
    splits = _splits_by_security(actions_as_of, through)
    # Summed exactly, never in `float`, so a full exit on a fine quantity grid
    # leaves no float residue, with or without a split (#1296).
    positions: dict[str, Fraction] = defaultdict(Fraction)
    shorts: dict[str, Fraction] = defaultdict(Fraction)  # unsigned short holdings (T232)

    # The rows are replayed in session order, not fills-then-adjustment deltas,
    # so a row's exact position is the shares held *then*: a split takes effect at
    # its own session and a `cash_in_lieu` round-down lands at its own point,
    # never after a split that took effect later (#1627). Order within a session:
    # the split of that ex-date (a fill dated on the ex-date is not scaled by it,
    # which `ex_date > stated_on` held before), then a `cash_in_lieu` row, which
    # rounds down the whole shares that split's remainder leaves, then a fill and
    # an adjustment stated on that session. Ranking the `CIL` before the day's
    # fills is safe: `compare` books one only when the broker holds exactly
    # `floor(ledger)`, so a broker holding a fraction the fill left cannot have a
    # bookable `CIL` that day; ranking it after them would floor that fraction
    # away (a fractional fill on the run's own session, after the CIL booked),
    # which is the case the ordering guards.
    replay: list[tuple[date, str, int, str, str, Fraction]] = []
    sequence = 0

    def _record(day: date, order: str, kind: str, security_id: str, amount: Fraction) -> None:
        nonlocal sequence
        replay.append((day, order, sequence, kind, security_id, amount))
        sequence += 1

    for name, rows in splits.items():
        for ex_date, ratio in rows:
            _record(ex_date, _REPLAY_SPLIT_RANK, "split", name, _split_ratio(ratio))

    # A true-up, and any fill journaled after one on its session, replay
    # together in the order they were stamped: a true-up's absolute quantity
    # holds every fill stamped before it and none stamped after.
    ordered: list[tuple[datetime, int, int, date, str, str, Fraction]] = []
    seen_fills: set[int] = set()
    fills_since_base = 0
    for fill in fills:
        _check_window("fill", fill.window_id, window_id)
        _check_fill(fill, by_id, seen_fills)
        row = fill.fill
        stated_on = row.filled_at.astimezone(_NEW_YORK).date()
        if stated_on > through:
            continue
        sign = 1 if fill.side == _BUY else -1
        kind = _SHORT_FILL if by_id[row.client_order_id].position_side != LONG else "fill"
        late = any(
            a.kind in TRUE_UP_KINDS
            and a.quantity is not None
            and a.security_id == fill.security_id
            and a.session == stated_on
            and a.known_at < row.known_at
            for a in adjustments
        )
        if late:
            ordered.append(
                (
                    row.known_at,
                    1,
                    row.fill_id or 0,
                    stated_on,
                    kind,
                    fill.security_id,
                    sign * _exact(row.quantity),
                )
            )
        else:
            _record(
                stated_on, _REPLAY_FILL_RANK, kind, fill.security_id, sign * _exact(row.quantity)
            )
        if after_base(row.known_at):
            cash -= sign * row.quantity * row.price
            fills_since_base += 1

    seen_adjustments: set[int] = set()
    for adjustment in adjustments:
        _check_window("adjustment", adjustment.window_id, window_id)
        _check_adjustment(adjustment, seen_adjustments)
        if adjustment.session > through:
            continue
        # `_REPLAY_CASH_IN_LIEU_RANK` for a `cash_in_lieu` row (right after that
        # session's split, before its fills), else `_REPLAY_ADJUSTMENT_RANK`.
        order = (
            _REPLAY_CASH_IN_LIEU_RANK
            if adjustment.kind == _CASH_IN_LIEU
            else _REPLAY_TRUE_UP_RANK
            if adjustment.kind in TRUE_UP_KINDS
            else _REPLAY_ADJUSTMENT_RANK
        )
        if adjustment.kind in TRUE_UP_KINDS and adjustment.security_id is not None:
            # Item 9: a true-up sets the figure. Replayed after the session's
            # split and fills, it overwrites what came before; only a split with
            # a later ex-date still acts on it, so a split row that lands late
            # with an earlier ex-date (already in the broker's quantity) is
            # never applied on top.
            absolute = true_up_quantity(adjustment)
            if absolute is not None:
                ordered.append(
                    (
                        adjustment.known_at,
                        0,
                        adjustment.adjustment_id or 0,
                        adjustment.session,
                        _TRUE_UP,
                        adjustment.security_id,
                        _exact(absolute),
                    )
                )
        elif adjustment.quantity is not None and adjustment.security_id is not None:
            _record(
                adjustment.session,
                order,
                adjustment.kind,
                adjustment.security_id,
                _exact(adjustment.quantity),
            )
        if adjustment.cash is not None and after_base(adjustment.known_at):
            cash += adjustment.cash

    for _known_at, _kind_rank, _id, day, kind, security_id, amount in sorted(ordered):
        _record(day, _REPLAY_TRUE_UP_RANK, kind, security_id, amount)

    for _day, _order, _seq, kind, security_id, amount in sorted(replay):
        if kind == "split":
            positions[security_id] *= amount
            if security_id in shorts:
                shorts[security_id] *= amount
        elif kind == _SHORT_FILL:
            # A short sale (a sell) adds to the short, a cover (a buy) removes.
            shorts[security_id] -= amount
            if shorts[security_id] < -_exact(quantity_tolerance):
                raise ValueError(
                    f"cover of {security_id} is {float(-shorts[security_id])} shares "
                    f"beyond the short"
                )
        elif kind == _TRUE_UP:
            positions[security_id] = amount
        elif kind == _CASH_IN_LIEU:
            # #1627: the row removes a stock dividend's fractional remainder at
            # its own point in the replay, so the issuer's exact `Fraction`
            # position is rounded down to the whole shares the broker credited
            # (a later split, a second stock dividend, or a fractional fill after
            # it act on the whole, never on the remainder). Applying the float
            # remainder instead leaves a sub-ulp share a whole-share exit
            # strands. Fail closed if the stored quantity is not the remainder
            # then removed.
            exact = positions[security_id]
            whole = math.floor(exact)
            if abs(float(exact - whole) + float(amount)) > quantity_tolerance:
                raise ValueError(
                    f"cash_in_lieu for {security_id} removes {float(exact - whole)} "
                    f"shares, not {float(-amount)}"
                )
            positions[security_id] = Fraction(whole)
        else:
            positions[security_id] += amount

    held = {name: float(q) for name, q in positions.items() if abs(q) > _exact(quantity_tolerance)}
    short_held: dict[str, float] = {}
    for name, short_quantity in shorts.items():
        if short_quantity > _exact(quantity_tolerance):
            short_held[name] = float(short_quantity)
    return Ledger(
        positions=held,
        cash=cash,
        through=through,
        fills_since_base=fills_since_base,
        short_holdings=short_held,
    )
