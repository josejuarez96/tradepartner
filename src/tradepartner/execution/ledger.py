"""The ledger: the positions and cash the journal implies (Phase 4 spec,
Definitions > Ledger; plan T51).

The ledger is never stored as truth. It is rebuilt, per paper window, by one
pure function over journal rows the caller has already read:

- **Positions** per `security_id` come from the live fills (`store.journal.fills_for`,
  so a superseded fill is never counted twice) and the journaled adjustments
  (`carried_residue`, `spinoff_receipt`, `corporate_action_cash`,
  `dividend_cash`), each split-adjusted by the splits in `actions_as_of` whose
  ex-date falls after the row's own date and on or before `through`. The caller
  reads `actions_as_of` with `store.asof.live_actions_as_of(conn, close(S-1))`,
  so a split known only after that cut is never applied (no look-ahead).
- **Cash** is the `broker_cash` of the window's last `ok` reconciliation plus
  every fill and adjustment delta known after it, or `starting_cash` plus every
  delta before the first one. A row whose `known_at` equals the
  reconciliation's is inside it.

Conventions this module fixes for the writers (T61 and T64): an adjustment's
`quantity` and `cash` are **signed deltas** applied as written. A spin-off
receipt or a carried residue adds shares; an ended listing's
`corporate_action_cash` row removes them with a negative `quantity` and
credits the proceeds as positive `cash`; a `dividend_cash` row carries `cash`
only. A row's own date for the split test is its `session` (an adjustment) or
the New York date of `filled_at` (a fill): a fill on the ex-date is already in
post-split shares.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class Ledger:
    """Positions (`security_id` -> shares, exact zeros dropped) and cash."""

    positions: Mapping[str, float]
    cash: float
    through: date
    _held: tuple[str, ...] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_held", tuple(sorted(self.positions)))

    def _values(self, marks: Mapping[str, float]) -> dict[str, float]:
        missing = [name for name in self._held if name not in marks]
        if missing:
            raise KeyError(f"no mark for held names {missing}")
        return {name: self.positions[name] * marks[name] for name in self._held}

    def equity(self, marks: Mapping[str, float]) -> float:
        """Cash plus every held position at its mark. A held name with no mark
        raises `KeyError`; marks for names not held are ignored."""
        return self.cash + sum(self._values(marks).values())

    def weights(self, marks: Mapping[str, float]) -> dict[str, float]:
        """Each held name's value over equity. Non-positive equity raises
        `ValueError`, since weights would be meaningless."""
        values = self._values(marks)
        equity = self.cash + sum(values.values())
        if equity <= 0:
            raise ValueError(f"equity is {equity}, weights need a positive equity")
        return {name: value / equity for name, value in values.items()}


def _splits_by_security(
    actions_as_of: pl.DataFrame, through: date
) -> dict[str, list[tuple[date, float]]]:
    splits: dict[str, list[tuple[date, float]]] = defaultdict(list)
    for row in actions_as_of.iter_rows(named=True):
        if row["action_type"] == _SPLIT and row["ex_date"] <= through:
            ratio = float(row["ratio_or_amount"])
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


def _check_window(kind: str, window_id: int, expected: int | None) -> int:
    if expected is not None and window_id != expected:
        raise ValueError(f"{kind} of window {window_id} in a ledger of window {expected}")
    return window_id


def _check_fill(fill: OrderedFill, orders: Mapping[str, OrderRow]) -> None:
    order = orders.get(fill.fill.client_order_id)
    if order is None:
        raise ValueError(
            f"fill {fill.fill.fill_id} of {fill.fill.client_order_id!r} has no orders row"
        )
    if (order.side, order.security_id) != (fill.side, fill.security_id):
        raise ValueError(
            f"fill {fill.fill.fill_id} ({fill.side} {fill.security_id}) disagrees with its "
            f"order {order.client_order_id!r} ({order.side} {order.security_id})"
        )
    if fill.side not in (_BUY, _SELL):
        raise ValueError(f"fill {fill.fill.fill_id} has side {fill.side!r}")


def _check_adjustment(row: AdjustmentRow) -> None:
    if row.kind not in _ADJUSTMENT_KINDS:
        raise ValueError(f"adjustment {row.adjustment_id} has kind {row.kind!r}")
    if row.quantity is not None and row.security_id is None:
        raise ValueError(f"adjustment {row.adjustment_id} has a quantity but no security_id")


def from_journal(
    fills: Sequence[OrderedFill],
    orders: Sequence[OrderRow],
    adjustments: Sequence[AdjustmentRow],
    actions_as_of: pl.DataFrame,
    last_ok_reconciliation: ReconciliationRow | None,
    starting_cash: float,
    through: date,
) -> Ledger:
    """The window's ledger stated for session `through` (module docstring).

    `fills` are the window's live fills from `store.journal.fills_for`; `orders`
    holds at least every fill's order, and a fill without one, or disagreeing
    with it on side or security, raises `ValueError`. `adjustments` are the
    window's rows (`store.journal.adjustments_for`). `actions_as_of` is a
    `live_actions_as_of` frame read at close(S-1); only its `split` rows count.
    `last_ok_reconciliation` is the window's latest `ok` row or None, and a row
    that is not `ok` or lacks `broker_cash` raises `ValueError`. Rows of two
    windows in one call raise `ValueError`.
    """
    if isinstance(through, datetime) or not isinstance(through, date):
        raise ValueError(f"through must be a session date, got {type(through).__name__}")

    window: int | None = None
    for fill in fills:
        window = _check_window("fill", fill.window_id, window)
    for adjustment in adjustments:
        window = _check_window("adjustment", adjustment.window_id, window)
    if last_ok_reconciliation is not None:
        _check_window("reconciliation", last_ok_reconciliation.window_id, window)
        if last_ok_reconciliation.status != _OK:
            raise ValueError(
                f"reconciliation {last_ok_reconciliation.reconciliation_id} is "
                f"{last_ok_reconciliation.status!r}, the cash base must be an ok one"
            )
        if last_ok_reconciliation.broker_cash is None:
            raise ValueError(
                f"ok reconciliation {last_ok_reconciliation.reconciliation_id} has no broker_cash"
            )

    by_id = {order.client_order_id: order for order in orders}
    splits = _splits_by_security(actions_as_of, through)
    base_known_at: datetime | None = None
    cash = starting_cash
    if last_ok_reconciliation is not None and last_ok_reconciliation.broker_cash is not None:
        base_known_at = last_ok_reconciliation.known_at
        cash = last_ok_reconciliation.broker_cash

    def after_base(known_at: datetime) -> bool:
        return base_known_at is None or known_at > base_known_at

    positions: dict[str, float] = defaultdict(float)
    for fill in fills:
        _check_fill(fill, by_id)
        row = fill.fill
        sign = 1 if fill.side == _BUY else -1
        stated_on = row.filled_at.astimezone(_NEW_YORK).date()
        factor = _split_factor(splits.get(fill.security_id, ()), stated_on)
        positions[fill.security_id] += sign * row.quantity * factor
        if after_base(row.known_at):
            cash -= sign * row.quantity * row.price

    for adjustment in adjustments:
        _check_adjustment(adjustment)
        if adjustment.quantity is not None and adjustment.security_id is not None:
            factor = _split_factor(splits.get(adjustment.security_id, ()), adjustment.session)
            positions[adjustment.security_id] += adjustment.quantity * factor
        if adjustment.cash is not None and after_base(adjustment.known_at):
            cash += adjustment.cash

    held = {name: quantity for name, quantity in positions.items() if quantity != 0}
    return Ledger(positions=held, cash=cash, through=through)
