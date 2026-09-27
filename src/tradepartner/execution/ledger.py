"""The ledger: the positions and cash the journal implies (Phase 4 spec,
Definitions > Ledger; plan T51).

The ledger is never stored as truth. It is rebuilt, per paper window, by one
pure function over journal rows the caller has already read, stated for one
session `through`:

- **Positions** per `security_id` come from the live fills (`store.journal.fills_for`,
  so a superseded fill is never counted twice) and the journaled adjustments
  (`carried_residue`, `spinoff_receipt`, `corporate_action_cash`,
  `dividend_cash`) dated on or before `through`, each split-adjusted by the
  splits in `actions_as_of` whose ex-date falls after the row's own date and
  on or before `through`. The caller reads `actions_as_of` with
  `store.asof.live_actions_as_of(conn, close(S-1))`, so a split known only
  after that cut is never applied (no look-ahead). Rows dated after `through`
  are left out, so an earlier session's ledger (a back-filled mark) can be
  rebuilt from the whole window's rows.
- **Cash** is the `broker_cash` of the window's last `ok` reconciliation plus
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

A row's own date for the split test is its `session` (an adjustment) or the
New York date of `filled_at` (a fill): a fill on the ex-date is already in
post-split shares. Quantities within `quantity_tolerance` of zero
(`risk.reconcile_quantity_tolerance` from the frozen window) are dropped.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

import polars as pl

from tradepartner.store.journal import AdjustmentRow, OrderedFill, OrderRow, ReconciliationRow
from tradepartner.store.schema import JOURNAL_ENUMS

_NEW_YORK = ZoneInfo("America/New_York")
_SPLIT = "split"
_OK = "ok"
_BUY = "buy"
_SELL = "sell"
_ADJUSTMENT_KINDS = frozenset(JOURNAL_ENUMS[("adjustments", "kind")])


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

    def equity(self, marks: Mapping[str, float]) -> float:
        """Cash plus every held position at its mark. A held name with no mark
        raises `KeyError`; a non-positive or non-finite mark, or a negative
        position, raises `ValueError`; marks for names not held are ignored."""
        return self.cash + sum(self._values(marks).values())

    def weights(self, marks: Mapping[str, float]) -> dict[str, float]:
        """Each held name's value over equity, under `equity`'s refusals.
        Equity that is not a positive number raises `ValueError`."""
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


def _split_factor(splits: Iterable[tuple[date, float]], stated_on: date) -> float:
    """The product of the ratios of splits that took effect after `stated_on`."""
    factor = 1.0
    for ex_date, ratio in splits:
        if stated_on < ex_date:
            factor *= ratio
    return factor


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
    _finite(row.quantity, f"fill {row.fill_id} quantity")
    _finite(row.price, f"fill {row.fill_id} price")


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


def _cash_base(
    reconciliation: ReconciliationRow | None, window_id: int, starting_cash: float, through: date
) -> tuple[float, datetime | None]:
    _finite(starting_cash, "starting_cash")
    if reconciliation is None:
        return starting_cash, None
    name = f"reconciliation {reconciliation.reconciliation_id}"
    _check_window("reconciliation", reconciliation.window_id, window_id)
    if reconciliation.status != _OK:
        raise ValueError(f"{name} is {reconciliation.status!r}, the cash base must be an ok one")
    if reconciliation.broker_cash is None:
        raise ValueError(f"ok {name} has no broker_cash")
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
    is the window's latest `ok` row stated on or before `through`, or None.
    `quantity_tolerance` is the frozen `risk.reconcile_quantity_tolerance`.

    Raises `ValueError` on: a row of another window; a fill without its order,
    disagreeing with it, or repeated; a repeated adjustment or an unknown kind;
    a base that is not `ok`, lacks `broker_cash` or is after `through`; any
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
    positions: dict[str, float] = defaultdict(float)

    seen_fills: set[int] = set()
    for fill in fills:
        _check_window("fill", fill.window_id, window_id)
        _check_fill(fill, by_id, seen_fills)
        row = fill.fill
        stated_on = row.filled_at.astimezone(_NEW_YORK).date()
        if stated_on > through:
            continue
        sign = 1 if fill.side == _BUY else -1
        factor = _split_factor(splits.get(fill.security_id, ()), stated_on)
        positions[fill.security_id] += sign * row.quantity * factor
        if after_base(row.known_at):
            cash -= sign * row.quantity * row.price

    seen_adjustments: set[int] = set()
    for adjustment in adjustments:
        _check_window("adjustment", adjustment.window_id, window_id)
        _check_adjustment(adjustment, seen_adjustments)
        if adjustment.session > through:
            continue
        if adjustment.quantity is not None and adjustment.security_id is not None:
            factor = _split_factor(splits.get(adjustment.security_id, ()), adjustment.session)
            positions[adjustment.security_id] += adjustment.quantity * factor
        if adjustment.cash is not None and after_base(adjustment.known_at):
            cash += adjustment.cash

    held = {name: q for name, q in positions.items() if abs(q) > quantity_tolerance}
    return Ledger(positions=held, cash=cash, through=through)
