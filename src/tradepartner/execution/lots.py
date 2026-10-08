"""The wash-sale lot ledger (Phase 4 spec req 13, plan T56; research G7).

`rebuild(fills, orders, assets, account)` is pure over the journal's live fills
(`store.journal.fills_for`, so a superseded fill never reaches it) and returns
`(lots, disposals, flags)`, numbered from 1 in trade order, for a writer to
journal. Nothing here reads a clock or the store.

- **Lots.** One per acquisition: the account columns, `security_id`, broker
  symbol, the CUSIP from `assets` (None when the broker gives none), the trade
  instant (UTC) and its exchange-local date, quantity and cost basis. The trade
  date is the fill's own, never the settlement date (G7 R19, R20).
- **Disposals.** Each sale closes lots first in, first out by trade time, one
  disposal per lot touched, with proceeds, realised gain or loss before any
  wash-sale adjustment, and the tax year of the local trade date (R20). A sale
  of more than the ledger holds raises `LotLedgerError`: the vehicle is long
  only, and so does any order whose `position_side` is not long (ADR 0015
  seam 2).
- **Merged orders.** An order with any `price_implied` fill (the synthetic
  residual of `paper resume`) becomes one acquisition or one sale: its fills'
  total quantity at the order's average price (their total value over their
  total quantity, which is the broker's average the residual was implied
  from), at its earliest fill's time, with no `fill_id`. So an implied price
  below zero never gives a lot a negative basis.
- **Wash-sale scan.** Each loss disposal, in disposal order (R13), is matched
  to acquisitions of the same `security_id` in any account of the ledger whose
  local trade date is at most `WASH_SALE_WINDOW_DAYS` from its own, before or
  after (R1), in acquisition order (R11). A replacement share absorbs at most
  one loss (R13), and shares closed by the same sale are never its own
  replacement. Taken literally (an owner question on #308), the unsold rest
of a lot, or another fill of the same order, bought inside the window does
count as a replacement. Each disposal is scanned on its own, so a loss and a gain closed
  the same day are flagged and reported apart, never netted (R14); a December
  sale is matched to a January purchase like any other (R15). Each match is a
  `WashSaleFlag` with the replacement lot, the matched quantity and the
  disallowed amount (the loss per share times that quantity). Because
  `rebuild` runs over every fill, a later acquisition re-scans every earlier
  loss.

Amounts are computed in `Decimal` from each float's `repr`, so FIFO residues
are exact and a lot's disposals add up to its basis exactly. Out of scope here
(Phase 6, G7 items 6 to 9 and 11): basis and holding-period carry-forward onto
replacement lots, split adjustment of lots, 1099-B figures and the
substantially-identical policy (only the same `security_id` replaces).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from tradepartner.adapters.broker import Asset
from tradepartner.store.journal import OrderedFill, OrderRow
from tradepartner.store.schema import LONG

#: IRC section 1091(a): the window runs from this many days before a loss sale
#: to this many days after it (the "61-day period" of 26 CFR 1.1091-1(a)).
WASH_SALE_WINDOW_DAYS = 30

_EXCHANGE_TZ = ZoneInfo("America/New_York")
_ZERO = Decimal(0)


class LotLedgerError(ValueError):
    """The fills cannot form a lot ledger (a sale beyond the holding, a fill
    without its order, a superseded fill passed in)."""


@dataclass(frozen=True)
class LedgerAccount:
    """The account the fills belong to: its id, type (`paper`, `taxable`,
    `ira`, `roth_ira`, ...) and owner (`self`, `spouse`, ...), G7 item 1."""

    account_id: str
    account_type: str
    owner: str


@dataclass(frozen=True)
class Lot:
    """One acquisition, the `lots` row without its journal timestamps."""

    lot_id: int
    account_id: str
    account_type: str
    account_owner: str
    security_id: str
    symbol: str
    cusip: str | None
    trade_at: datetime
    trade_date_local: date
    quantity: float
    cost_basis: float
    fill_id: int | None
    client_order_id: str  # the acquiring order (not a `lots` column; T62 attributes by it)
    book_id: str  # the acquiring order's book (ADR 0015 seam 1, plan T133)


@dataclass(frozen=True)
class Disposal:
    """The part of one sale that closed one lot, the `disposals` row without
    its journal timestamps."""

    disposal_id: int
    lot_id: int
    account_id: str
    trade_at: datetime
    trade_date_local: date
    quantity: float
    proceeds: float
    realised_pnl: float
    tax_year: int
    fill_id: int | None
    client_order_id: str  # the selling order (not a `disposals` column; T62 attributes by it)
    book_id: str  # the selling order's book (ADR 0015 seam 1, plan T133)


@dataclass(frozen=True)
class WashSaleFlag:
    """One replacement lot matched to one loss disposal, the
    `wash_sale_flags` row without `scanned_at` and its journal timestamps."""

    flag_id: int
    disposal_id: int
    replacement_lot_id: int
    matched_quantity: float
    disallowed_amount: float


@dataclass(frozen=True)
class _Trade:
    """One acquisition or sale: a fill, or an order's merged fills."""

    side: str
    security_id: str
    symbol: str
    trade_at: datetime
    quantity: Decimal
    value: Decimal  # quantity x price
    fill_id: int | None
    order_key: int  # the lowest fill_id, for a stable order within an instant
    client_order_id: str
    book_id: str  # the order's book (ADR 0015 seam 1, plan T133)


@dataclass
class _OpenLot:
    lot: Lot
    quantity: Decimal
    basis: Decimal


def rebuild(
    fills: Sequence[OrderedFill],
    orders: Sequence[OrderRow],
    assets: Mapping[str, Asset],
    account: LedgerAccount,
) -> tuple[list[Lot], list[Disposal], list[WashSaleFlag]]:
    """Lots, FIFO disposals and wash-sale flags from `fills_for`'s live fills
    (module docstring). `orders` must hold every fill's order; `assets` maps a
    broker symbol to its `Asset` for the CUSIP.

    A loss's flags depend on acquisitions after it, as the rule requires, so a
    caller reporting the ledger as of an instant t must pass only fills with
    `known_at <= t`; `fills_for` does not filter by `known_at`. Raises
    `LotLedgerError` on a sale beyond the holding, which a split, a spin-off
    receipt or a stock merger (none of them a fill) produces, and on a fill
    with a non-finite quantity or price or a naive `filled_at`: a writer must
    catch it and alert rather than fail its run. Also raises it on any order
    whose `position_side` is not `schema.LONG`: closing short lots is the
    shorting ADR's (ADR 0015 seam 2), so every `Lot` and `Disposal` is long
    and `LotRow` and `DisposalRow` keep the DDL default."""
    for order in orders:
        if order.position_side != LONG:
            raise LotLedgerError(
                f"order {order.client_order_id!r} has position_side "
                f"{order.position_side!r}: the lot ledger is long only"
            )
    lot_list: list[Lot] = []
    disposals: list[Disposal] = []
    same_sale: dict[int, dict[int, Decimal]] = {}  # disposal id -> lot id -> quantity sold
    open_lots: dict[str, list[_OpenLot]] = {}
    for trade in _trades(fills, orders):
        local = trade.trade_at.astimezone(_EXCHANGE_TZ).date()
        if trade.side == "buy":
            asset = assets.get(trade.symbol)
            lot = Lot(
                lot_id=len(lot_list) + 1,
                account_id=account.account_id,
                account_type=account.account_type,
                account_owner=account.owner,
                security_id=trade.security_id,
                symbol=trade.symbol,
                cusip=None if asset is None else asset.cusip,
                trade_at=trade.trade_at,
                trade_date_local=local,
                quantity=float(trade.quantity),
                cost_basis=float(trade.value),
                fill_id=trade.fill_id,
                client_order_id=trade.client_order_id,
                book_id=trade.book_id,
            )
            lot_list.append(lot)
            open_lots.setdefault(trade.security_id, []).append(
                _OpenLot(lot, trade.quantity, trade.value)
            )
            continue
        closed = _close_fifo(open_lots.get(trade.security_id, []), trade)
        this_sale = {held.lot.lot_id: sold for held, sold, _ in closed}
        for held, sold, basis in closed:
            proceeds = trade.value * sold / trade.quantity
            disposal = Disposal(
                disposal_id=len(disposals) + 1,
                lot_id=held.lot.lot_id,
                account_id=account.account_id,
                trade_at=trade.trade_at,
                trade_date_local=local,
                quantity=float(sold),
                proceeds=float(proceeds),
                realised_pnl=float(proceeds - basis),
                tax_year=local.year,
                fill_id=trade.fill_id,
                client_order_id=trade.client_order_id,
                book_id=trade.book_id,
            )
            disposals.append(disposal)
            same_sale[disposal.disposal_id] = this_sale
    return lot_list, disposals, _wash_sale_flags(lot_list, disposals, same_sale)


def _trades(fills: Sequence[OrderedFill], orders: Sequence[OrderRow]) -> list[_Trade]:
    """One `_Trade` per fill, or per order when any of its fills is implied,
    in trade order."""
    known = {order.client_order_id: order for order in orders}
    by_order: dict[str, list[OrderedFill]] = {}
    for item in fills:
        row = item.fill
        if row.superseded_by is not None:
            raise LotLedgerError(
                f"fill {row.fill_id} is superseded: pass the fills_for accessor's live fills"
            )
        if row.client_order_id not in known:
            raise LotLedgerError(f"fill {row.fill_id} has no order {row.client_order_id!r}")
        if item.side not in {"buy", "sell"}:
            raise LotLedgerError(f"fill {row.fill_id} has side {item.side!r}")
        if not (math.isfinite(row.quantity) and math.isfinite(row.price)):
            raise LotLedgerError(
                f"fill {row.fill_id} needs a finite quantity and price: "
                f"{row.quantity!r}, {row.price!r}"
            )
        if row.filled_at.tzinfo is None:
            raise LotLedgerError(f"fill {row.fill_id} has a naive filled_at")
        by_order.setdefault(row.client_order_id, []).append(item)
    trades: list[_Trade] = []
    for group in by_order.values():
        parts = [(item, _dec(item.fill.quantity), _dec(item.fill.price)) for item in group]
        if any(item.fill.price_implied for item in group):
            first = min(group, key=lambda item: (item.fill.filled_at, _fill_key(item)))
            trades.append(
                _Trade(
                    side=first.side,
                    security_id=first.security_id,
                    symbol=first.symbol,
                    trade_at=first.fill.filled_at,
                    quantity=sum((q for _, q, _ in parts), _ZERO),
                    value=sum((q * p for _, q, p in parts), _ZERO),
                    fill_id=None,
                    order_key=min(_fill_key(item) for item in group),
                    client_order_id=first.fill.client_order_id,
                    book_id=known[first.fill.client_order_id].book_id,
                )
            )
            continue
        trades.extend(
            _Trade(
                side=item.side,
                security_id=item.security_id,
                symbol=item.symbol,
                trade_at=item.fill.filled_at,
                quantity=quantity,
                value=quantity * price,
                fill_id=item.fill.fill_id,
                order_key=_fill_key(item),
                client_order_id=item.fill.client_order_id,
                book_id=known[item.fill.client_order_id].book_id,
            )
            for item, quantity, price in parts
        )
    return sorted(trades, key=lambda trade: (trade.trade_at, trade.order_key))


def _close_fifo(held: list[_OpenLot], sale: _Trade) -> list[tuple[_OpenLot, Decimal, Decimal]]:
    """Close `sale.quantity` from the oldest lots first: (lot, quantity, basis)
    per lot touched. Lots are appended in trade order, so the list is FIFO."""
    if sum((lot.quantity for lot in held), _ZERO) < sale.quantity:
        raise LotLedgerError(
            f"sale of {sale.quantity} {sale.symbol} at {sale.trade_at.isoformat()} is more "
            f"than the ledger holds"
        )
    closed: list[tuple[_OpenLot, Decimal, Decimal]] = []
    remaining = sale.quantity
    while remaining > _ZERO:
        lot = held[0]
        sold = min(remaining, lot.quantity)
        basis = lot.basis if sold == lot.quantity else lot.basis * sold / lot.quantity
        lot.quantity -= sold
        lot.basis -= basis
        remaining -= sold
        closed.append((lot, sold, basis))
        if lot.quantity == _ZERO:
            held.pop(0)
    return closed


def _wash_sale_flags(
    lot_list: Sequence[Lot],
    disposals: Sequence[Disposal],
    same_sale: Mapping[int, Mapping[int, Decimal]],
) -> list[WashSaleFlag]:
    """Match each loss disposal, in disposal order, to replacement shares in
    acquisition order; each share absorbs one loss (module docstring)."""
    by_acquisition = sorted(lot_list, key=lambda lot: (lot.trade_at, lot.lot_id))
    absorbed: dict[int, Decimal] = {}
    flags: list[WashSaleFlag] = []
    for disposal in disposals:
        loss = -_dec(disposal.realised_pnl)
        if loss <= _ZERO:
            continue
        security = next(lot.security_id for lot in lot_list if lot.lot_id == disposal.lot_id)
        sold_here = same_sale[disposal.disposal_id]
        quantity = _dec(disposal.quantity)
        unmatched = quantity
        for lot in by_acquisition:
            if unmatched == _ZERO:
                break
            days_apart = abs((lot.trade_date_local - disposal.trade_date_local).days)
            if lot.security_id != security or days_apart > WASH_SALE_WINDOW_DAYS:
                continue
            available = (
                _dec(lot.quantity)
                - absorbed.get(lot.lot_id, _ZERO)
                - sold_here.get(lot.lot_id, _ZERO)
            )
            matched = min(unmatched, available)
            if matched <= _ZERO:
                continue
            absorbed[lot.lot_id] = absorbed.get(lot.lot_id, _ZERO) + matched
            unmatched -= matched
            flags.append(
                WashSaleFlag(
                    flag_id=len(flags) + 1,
                    disposal_id=disposal.disposal_id,
                    replacement_lot_id=lot.lot_id,
                    matched_quantity=float(matched),
                    disallowed_amount=float(loss * matched / quantity),
                )
            )
    return flags


def _fill_key(item: OrderedFill) -> int:
    fill_id = item.fill.fill_id
    if fill_id is None:
        raise LotLedgerError(f"a fill of {item.fill.client_order_id!r} has no fill_id")
    return fill_id


def _dec(value: float) -> Decimal:
    return Decimal(repr(float(value)))
