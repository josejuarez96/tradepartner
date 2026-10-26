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
  of more than the ledger holds raises `LotLedgerError`.
- **Short lots (T232, paper spec amendment #1872 item 10).** An order's
  `position_side` is `short` for a `sell_to_open` (a sell) and a `buy_to_close`
  (a buy). A short sale opens a lot with `position_side == "short"`, an
  unsigned quantity and `cost_basis` = -(the sale's value); a cover closes
  short lots first in, first out, one disposal per lot, with `proceeds` =
  -(the cover's cost). So `realised_pnl = proceeds - basis` holds for both
  sides (owner decision 2026-10-19, #1903), the formula `lots_reconcile` reads.
  A cover is at most the held short, and a name is never long and short at
  once: a short sale of a name held long, a purchase of one held short, and a
  cover with no short lot each raise `LotLedgerError`. Wash-sale scanning of
  short disposals, and short lots as replacements, are deferred to Phase 6
  (ADR 0015, ADR 0024 item 8): the scan reads long lots and disposals only.
- **Merged orders.** An order with any `price_implied` fill (the synthetic
  residual of `paper resume`) becomes one acquisition or one sale: its fills'
  total quantity at the order's average price (their total value over their
  total quantity, which is the broker's average the residual was implied
  from), at its earliest fill's time, with no `fill_id`. So an implied price
  below zero never gives a lot a negative basis.
- **Wash-sale scan.** Each loss disposal, in disposal order (R13), is matched
  to acquisitions of the same `security_id` in any account of the ledger whose
  local trade date is at most `WASH_SALE_WINDOW_DAYS` from its own, before or
  after (R1), in acquisition order (R11). The disposal's sale and the shares it
  closed are scaled to today's share units - the units a replacement lot
  reports - before matching (#1980), so a split or a stock merger between them
  never matches in mixed units; `matched_quantity` is therefore in those units.
  A replacement share absorbs at most one loss (R13), and shares closed by the
  same sale are never its own replacement. Taken literally (an owner question
  on #308), the unsold rest of a lot, or another fill of the same order, bought
  inside the window does count as a replacement. Each disposal is scanned on
  its own, so a loss and a gain closed the same day are flagged and reported
  apart, never netted (R14); a December sale is matched to a January purchase
  like any other (R15). Each match is a `WashSaleFlag` with the replacement
  lot, the matched quantity and the disallowed amount (the loss per share times
  that quantity). Because `rebuild` runs over every fill, a later acquisition
  re-scans every earlier loss.

- **Adjustments (#516, spec req 13; long lots only).** `rebuild` also takes the
  window's journaled `adjustments`, the `live_actions_as_of(close(S-1))` frame
  (its `split` rows) and the session S (`through`). Within a session the order
  is split, `cash_in_lieu`, fills, other adjustments (the positions ledger's).
  A **split** scales every lot's quantity and keeps its basis and date;
  **cash in lieu** rounds the name's exact holding down to whole shares and
  disposes of the fraction FIFO at the journaled cash; **`corporate_action_cash`**
  closes the name's lots FIFO as one sale at its cash; a **spin-off receipt**
  opens a zero-basis lot on its session (or, when `explanation_json` names
  `parent_security_id` and `basis_allocation`, moves that share of the parent's
  basis to it); a **carried residue** opens a zero-basis lot on its session; a
  **stock merger** (a `corporate_action_cash` and a `merger_receipt` naming one
  `merger_id`) re-keys the target's lots to the acquirer at the ratio of the two
  quantities, carrying basis and date, with no disposal. A residue or a
  spin-off child is never a wash-sale replacement. Any corporate action on an
  open short lot raises (short lots are Phase 6).

Amounts are computed in `Fraction` from each float's `repr`, so FIFO residues
are exact and a lot's disposals add up to its basis exactly. Out of scope here
(Phase 6, G7 items 6 to 9 and 11): basis and holding-period carry-forward onto
replacement lots, 1099-B figures and the substantially-identical policy (only
the same `security_id` replaces).
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from fractions import Fraction
from functools import partial
from zoneinfo import ZoneInfo

import polars as pl

from tradepartner.adapters.broker import Asset
from tradepartner.calendar import session_close
from tradepartner.execution.ledger import _split_ratio
from tradepartner.store.journal import AdjustmentRow, OrderedFill, OrderRow
from tradepartner.store.schema import LONG

#: A short lot or disposal (T232); the other side is `schema.LONG`.
SHORT = "short"

#: IRC section 1091(a): the window runs from this many days before a loss sale
#: to this many days after it (the "61-day period" of 26 CFR 1.1091-1(a)).
WASH_SALE_WINDOW_DAYS = 30

_EXCHANGE_TZ = ZoneInfo("America/New_York")
_ZERO = Fraction(0)
_ONE = Fraction(1)

# Event ranks within one session, the positions ledger's own order.
_RANK_SPLIT = 0
_RANK_CASH_IN_LIEU = 1
_RANK_FILL = 2
_RANK_ADJUSTMENT = _RANK_FILL + 1

_SPLIT = "split"
_CASH_IN_LIEU = "cash_in_lieu"
_CORPORATE_ACTION_CASH = "corporate_action_cash"
_MERGER_RECEIPT = "merger_receipt"
_SPINOFF_RECEIPT = "spinoff_receipt"
_CARRIED_RESIDUE = "carried_residue"
#: The `explanation_json` key both rows of a stock merger name (paper spec
#: amendment #1762 item 5).
_MERGER_KEY = "merger_id"
#: The `explanation_json` key of a delisting's late cash (#411): a cash-only
#: `corporate_action_cash` row that is part of the proceeds of the ended-listing
#: removal row before it.
_DELISTING_KEY = "delisting_cash"


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
    position_side: str = LONG  # `schema.LONG` or `SHORT` (T232)


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
    position_side: str = LONG  # the closed lot's side (T232)


@dataclass(frozen=True)
class WashSaleFlag:
    """One replacement lot matched to one loss disposal, the
    `wash_sale_flags` row without `scanned_at` and its journal timestamps.
    `matched_quantity` is in today's share units (the replacement lot's), so a
    split or stock merger between the sale and the lot never leaves it mixed
    (#1980)."""

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
    quantity: Fraction
    value: Fraction  # quantity x price
    fill_id: int | None
    order_key: int  # the lowest fill_id, for a stable order within an instant
    client_order_id: str
    book_id: str  # the order's book (ADR 0015 seam 1, plan T133)
    position_side: str = LONG  # the order's `position_side` (T232)


@dataclass
class _OpenLot:
    """A lot's running state. `acquired` and `reported_basis` are what the `Lot`
    row reports (the acquired quantity in today's share units, the basis net of
    any spin-off allocation); `quantity` and `basis` are what is still open."""

    lot_id: int
    security_id: str
    #: The security whose share units `quantity`/`acquired` are in. A merger
    #: re-keys it to the acquirer for every lot, even one already fully sold,
    #: while `security_id`/`symbol`/`cusip` keep the target's on a sold lot so
    #: its own sale still reconciles (#1980).
    unit_security: str
    symbol: str
    cusip: str | None
    trade_at: datetime
    trade_date_local: date
    fill_id: int | None
    client_order_id: str
    book_id: str
    position_side: str
    acquired: Fraction
    #: What the `Lot` row reports as its quantity: `acquired`, except that a
    #: merger leaves a lot sold before it in its own security's units, so the
    #: row never mixes the target's symbol with the acquirer's count (#1980).
    reported_quantity: Fraction
    reported_basis: Fraction
    quantity: Fraction
    basis: Fraction
    #: Every security the lot has been (a merger re-keys it), for the wash-sale scan.
    identities: set[str] = field(default_factory=set)

    def to_lot(self, account: LedgerAccount) -> Lot:
        return Lot(
            lot_id=self.lot_id,
            account_id=account.account_id,
            account_type=account.account_type,
            account_owner=account.owner,
            security_id=self.security_id,
            symbol=self.symbol,
            cusip=self.cusip,
            trade_at=self.trade_at,
            trade_date_local=self.trade_date_local,
            quantity=float(self.reported_quantity),
            cost_basis=float(self.reported_basis),
            fill_id=self.fill_id,
            client_order_id=self.client_order_id,
            book_id=self.book_id,
            position_side=self.position_side,
        )


class _Ledger:
    """The running state of one `rebuild`."""

    def __init__(
        self,
        account: LedgerAccount,
        assets: Mapping[str, Asset],
        symbols: Mapping[str, str],
    ) -> None:
        self.account = account
        self.assets = assets
        self.symbols = symbols
        self.lots: list[_OpenLot] = []
        self.disposals: list[Disposal] = []
        self.disposal_security: dict[int, str] = {}
        self.same_sale: dict[int, dict[int, Fraction]] = {}
        #: Each disposal's factor from its sale's share units to today's (a
        #: split or a merger after it scales it; #1980).
        self.disposal_units: dict[int, Fraction] = {}
        #: Its security today, so a merger re-keys it with the lots it made.
        self.disposals_by_security: dict[str, list[int]] = {}
        self.longs: dict[str, list[_OpenLot]] = {}
        self.shorts: dict[str, list[_OpenLot]] = {}
        self.no_replacement: set[int] = set()  # lots no purchase made (never a replacement)

    # -- lots and disposals ------------------------------------------------------

    def open_lot(
        self,
        security_id: str,
        symbol: str,
        trade_at: datetime,
        quantity: Fraction,
        basis: Fraction,
        *,
        fill_id: int | None,
        client_order_id: str,
        book_id: str,
        position_side: str = LONG,
    ) -> _OpenLot:
        asset = self.assets.get(symbol)
        lot = _OpenLot(
            lot_id=len(self.lots) + 1,
            security_id=security_id,
            unit_security=security_id,
            symbol=symbol,
            cusip=None if asset is None else asset.cusip,
            trade_at=trade_at,
            trade_date_local=trade_at.astimezone(_EXCHANGE_TZ).date(),
            fill_id=fill_id,
            client_order_id=client_order_id,
            book_id=book_id,
            position_side=position_side,
            acquired=quantity,
            reported_quantity=quantity,
            reported_basis=basis,
            quantity=quantity,
            basis=basis,
            identities={security_id},
        )
        self.lots.append(lot)
        book = self.shorts if position_side == SHORT else self.longs
        book.setdefault(security_id, []).append(lot)
        return lot

    def dispose(
        self,
        closed: Sequence[tuple[_OpenLot, Fraction, Fraction]],
        proceeds_of: Callable[[Fraction], Fraction],
        *,
        trade_at: datetime,
        fill_id: int | None,
        client_order_id: str,
        book_id: str | None,
        position_side: str = LONG,
    ) -> None:
        """One disposal per lot in `closed` ((lot, quantity, basis)), with the
        proceeds `proceeds_of(quantity)`; the lot's own book when `book_id` is None."""
        local = trade_at.astimezone(_EXCHANGE_TZ).date()
        this_sale = {held.lot_id: sold for held, sold, _ in closed}
        for held, sold, basis in closed:
            proceeds = proceeds_of(sold)
            disposal = Disposal(
                disposal_id=len(self.disposals) + 1,
                lot_id=held.lot_id,
                account_id=self.account.account_id,
                trade_at=trade_at,
                trade_date_local=local,
                quantity=float(sold),
                proceeds=float(proceeds),
                realised_pnl=float(proceeds - basis),
                tax_year=local.year,
                fill_id=fill_id,
                client_order_id=client_order_id,
                book_id=held.book_id if book_id is None else book_id,
                position_side=position_side,
            )
            self.disposals.append(disposal)
            self.disposal_security[disposal.disposal_id] = held.security_id
            self.same_sale[disposal.disposal_id] = this_sale
            self.disposal_units[disposal.disposal_id] = _ONE
            self.disposals_by_security.setdefault(held.security_id, []).append(disposal.disposal_id)

    def refuse_short(self, security_id: str, what: str) -> None:
        if self.shorts.get(security_id):
            raise LotLedgerError(
                f"{what} on {security_id}, which has an open short lot: corporate actions on "
                f"a short lot are not covered by the lot ledger"
            )

    def symbol_of(self, security_id: str) -> str:
        return self.symbols.get(security_id, security_id)

    # -- trades ------------------------------------------------------------------

    def trade(self, trade: _Trade) -> None:
        short = trade.position_side == SHORT
        opening = trade.side == ("sell" if short else "buy")
        mine, other = (self.shorts, self.longs) if short else (self.longs, self.shorts)
        if opening and other.get(trade.security_id):
            raise LotLedgerError(
                f"order {trade.client_order_id!r} has position_side {trade.position_side!r} "
                f"on {trade.symbol}, held {'long' if short else 'short'}: a name is never "
                f"long and short at once"
            )
        if opening:
            self.open_lot(
                trade.security_id,
                trade.symbol,
                trade.trade_at,
                trade.quantity,
                -trade.value if short else trade.value,
                fill_id=trade.fill_id,
                client_order_id=trade.client_order_id,
                book_id=trade.book_id,
                position_side=trade.position_side,
            )
            return
        what = (
            f"{trade.side} order {trade.client_order_id!r} (position_side "
            f"{trade.position_side!r}) of {trade.quantity} {trade.symbol} at "
            f"{trade.trade_at.isoformat()}"
        )
        closed = _close_fifo(mine.get(trade.security_id, []), trade.quantity, what)
        sign = -1 if short else 1
        self.dispose(
            closed,
            lambda sold: sign * trade.value * sold / trade.quantity,
            trade_at=trade.trade_at,
            fill_id=trade.fill_id,
            client_order_id=trade.client_order_id,
            book_id=trade.book_id,
            position_side=trade.position_side,
        )

    # -- splits ------------------------------------------------------------------

    def split(self, security_id: str, ratio: Fraction) -> None:
        self.refuse_short(security_id, "split")
        for lot in self.lots:
            if lot.unit_security == security_id and lot.position_side == LONG:
                lot.quantity *= ratio
                lot.acquired *= ratio
                if lot.security_id == security_id:
                    lot.reported_quantity *= ratio
        self._rescale_disposals(security_id, ratio)

    def _rescale_disposals(self, security_id: str, ratio: Fraction) -> None:
        """A split scales every lot's quantity but leaves a disposal's recorded
        sale in its own units (#1980); record the factor so the wash-sale scan
        can read the disposal, and the shares it closed, in today's units."""
        for disposal_id in self.disposals_by_security.get(security_id, []):
            self.disposal_units[disposal_id] *= ratio

    def _rekey_disposals(self, target: str, acquirer: str, ratio: Fraction) -> None:
        """A merger re-keys the target's lots to the acquirer at `ratio`; its
        disposals move with them, into the acquirer's units and under every
        later acquirer split (#1980)."""
        moved = self.disposals_by_security.pop(target, [])
        for disposal_id in moved:
            self.disposal_units[disposal_id] *= ratio
        if moved:
            self.disposals_by_security.setdefault(acquirer, []).extend(moved)

    # -- adjustments -------------------------------------------------------------

    def cash_in_lieu(self, row: AdjustmentRow) -> None:
        """Round the name's exact holding down to whole shares (the positions
        ledger's rule, #1627) and dispose of the fraction at the journaled cash."""
        security = _security(row)
        self.refuse_short(security, row.kind)
        _require_long_delta(row, negative=True)
        held = self.longs.get(security, [])
        exact = sum((lot.quantity for lot in held), _ZERO)
        removed = exact - math.floor(exact)
        if removed == _ZERO:
            return
        cash = _cash(row)
        closed = _close_fifo(held, removed, _describe(row))
        self.dispose(
            closed,
            lambda sold: cash * sold / removed,
            trade_at=_session_at(row),
            fill_id=None,
            client_order_id=_placeholder(row),
            book_id=None,
        )

    def removal(self, row: AdjustmentRow, cash_total: Fraction | None = None) -> None:
        """A position removed for cash: one FIFO sale at the journaled cash."""
        security = _security(row)
        self.refuse_short(security, row.kind)
        _require_long_delta(row, negative=True)
        removed = -_exact(row.quantity)
        cash = _cash(row) if cash_total is None else cash_total
        closed = _close_fifo(self.longs.get(security, []), removed, _describe(row))
        self.dispose(
            closed,
            lambda sold: cash * sold / removed,
            trade_at=_session_at(row),
            fill_id=None,
            client_order_id=_placeholder(row),
            book_id=None,
        )

    def merge(
        self,
        removal: AdjustmentRow,
        receipt: AdjustmentRow,
        cash_rows: Sequence[AdjustmentRow] = (),
    ) -> None:
        """Carry each target lot into the acquirer, disposing of the portion
        sold for removal cash and, later, for cash in lieu (#2151). A lot
        already fully sold keeps its target identity, but its share units
        follow the acquirer for later splits (#1980)."""
        target, acquirer = _security(removal), _security(receipt)
        self.refuse_short(target, removal.kind)
        self.refuse_short(acquirer, receipt.kind)
        _require_long_delta(removal, negative=True)
        _require_long_delta(receipt, negative=False)
        held = self.longs.get(target, [])
        removed = -_exact(removal.quantity)
        total = sum((lot.quantity for lot in held), _ZERO)
        if not math.isclose(float(total), float(removed)):
            raise LotLedgerError(
                f"stock merger {_describe(removal)} removes {removed} but the ledger holds {total}"
            )
        received = _exact(receipt.quantity)
        terms = _explanation(removal)
        kind = terms.get("merger_kind", "stock")
        removal_cash = _cash(removal)
        same_session_cil = (
            _merger_number(terms.get("same_session_cil_cash", 0), removal, "same_session_cil_cash")
            if kind == "stock_and_cash"
            else _ZERO
        )
        if same_session_cil > removal_cash:
            raise LotLedgerError(f"merger {_describe(removal)} has CIL above its removal cash")
        boot = removal_cash - same_session_cil if kind == "stock_and_cash" else _ZERO
        if boot > _ZERO:
            price = _merger_number(terms.get("acquirer_price"), removal, "acquirer_price")
            if price <= _ZERO:
                raise LotLedgerError(f"merger {_describe(removal)} has no positive acquirer price")
            sold_fraction = boot / (boot + received * price)
        else:
            sold_fraction = _ZERO
        if sold_fraction >= _ONE:
            raise LotLedgerError(f"merger {_describe(removal)} has no basis left for its receipt")
        if sold_fraction:
            closed: list[tuple[_OpenLot, Fraction, Fraction]] = []
            for lot in held:
                sold = lot.quantity * sold_fraction
                basis = lot.basis * sold_fraction
                lot.quantity -= sold
                lot.basis -= basis
                lot.reported_basis -= basis
                closed.append((lot, sold, basis))
            self.dispose(
                closed,
                lambda sold: boot * sold / (removed * sold_fraction),
                trade_at=_session_at(removal),
                fill_id=None,
                client_order_id=_placeholder(removal),
                book_id=None,
            )
        ratio = received / removed
        open_ratio = received / (removed * (_ONE - sold_fraction))
        symbol = self.symbol_of(acquirer)
        asset = self.assets.get(symbol)
        held_ids = {lot.lot_id for lot in held}
        for lot in self.lots:
            if lot.unit_security != target or lot.position_side != LONG:
                continue
            lot.unit_security = acquirer
            lot.quantity *= open_ratio if lot.lot_id in held_ids else ratio
            lot.acquired *= ratio
        for lot in held:
            lot.security_id, lot.symbol = acquirer, symbol
            lot.cusip = None if asset is None else asset.cusip
            lot.reported_quantity *= ratio
            lot.identities.add(acquirer)
        self._rekey_disposals(target, acquirer, ratio)
        merged = self.longs.setdefault(acquirer, [])
        merged.extend(held)
        merged.sort(key=lambda lot: (lot.trade_at, lot.lot_id))
        self.longs[target] = []
        cil_cash = sum(
            (_cash(row) for row in cash_rows if row.quantity is None),
            removal_cash if kind == "stock" else same_session_cil,
        )
        if cil_cash > _ZERO:
            fractional = _merger_number(
                terms.get("fractional_shares"), removal, "fractional_shares"
            )
            if fractional <= _ZERO:
                raise LotLedgerError(f"merger {_describe(removal)} has CIL but no fraction")
            cil_fraction = fractional / (received + fractional)
            closed_cil: list[tuple[_OpenLot, Fraction, Fraction]] = []
            for lot in held:
                sold = fractional * lot.quantity / received
                basis = lot.basis * cil_fraction
                lot.basis -= basis
                lot.reported_basis -= basis
                closed_cil.append((lot, sold, basis))
            cil_row = max(
                (row for row in cash_rows if row.quantity is None and row.cash),
                key=lambda row: (row.session, _adjustment_key(row)),
                default=removal,
            )
            self.dispose(
                closed_cil,
                lambda sold: cil_cash * sold / fractional,
                trade_at=_session_at(cil_row),
                fill_id=None,
                client_order_id=_placeholder(cil_row),
                book_id=None,
            )

    def spinoff(self, row: AdjustmentRow) -> None:
        """A spin-off child: one zero-basis lot on the session, unless the row
        names the parent and the fraction of its basis that moves to the child."""
        child = _security(row)
        self.refuse_short(child, row.kind)
        _require_long_delta(row, negative=False)
        moved = _ZERO
        terms = _explanation(row)
        parent, allocation = terms.get("parent_security_id"), terms.get("basis_allocation")
        if parent is not None and allocation is not None:
            if not isinstance(parent, str):
                raise LotLedgerError(f"spin-off {_describe(row)} names parent {parent!r}")
            self.refuse_short(parent, row.kind)
            fraction = _allocation(allocation, row)
            for lot in self.longs.get(parent, []):
                share = lot.basis * fraction
                lot.basis -= share
                lot.reported_basis -= share
                moved += share
        self._receipt(row, child, moved)

    def residue(self, row: AdjustmentRow) -> None:
        """A residue carried into the window: a zero-basis lot on its session."""
        security = _security(row)
        self.refuse_short(security, row.kind)
        _require_long_delta(row, negative=False)
        self._receipt(row, security, _ZERO)

    def _receipt(self, row: AdjustmentRow, security: str, basis: Fraction) -> None:
        lot = self.open_lot(
            security,
            self.symbol_of(security),
            _session_at(row),
            _exact(row.quantity),
            basis,
            fill_id=None,
            client_order_id=_placeholder(row),
            book_id=row.book_id,
        )
        self.no_replacement.add(lot.lot_id)


def _security(row: AdjustmentRow) -> str:
    if row.security_id is None:
        raise LotLedgerError(f"adjustment {_describe(row)} names no security")
    return row.security_id


def _describe(row: AdjustmentRow) -> str:
    return f"{row.kind} {row.adjustment_id!r} of {row.security_id} on {row.session.isoformat()}"


def _placeholder(row: AdjustmentRow) -> str:
    """A `client_order_id` no order holds, for the rows an adjustment makes."""
    return f"adjustment:{row.adjustment_id}:{row.kind}:{row.session.isoformat()}:{row.security_id}"


def _session_at(row: AdjustmentRow) -> datetime:
    """The instant an adjustment takes effect: its session's close."""
    try:
        return session_close(row.session)
    except ValueError as exc:
        raise LotLedgerError(f"adjustment {_describe(row)}: {exc}") from exc


def _exact(value: float | None) -> Fraction:
    """`value` as the exact `Fraction` of its shortest decimal form."""
    if value is None or not math.isfinite(value):
        raise LotLedgerError(f"a quantity or amount must be finite: {value!r}")
    return Fraction(repr(float(value)))


def _cash(row: AdjustmentRow) -> Fraction:
    if row.cash is None:
        raise LotLedgerError(f"adjustment {_describe(row)} has no cash")
    return _exact(row.cash)


def _require_long_delta(row: AdjustmentRow, *, negative: bool) -> None:
    """A removal (`negative`) or a receipt moves a long lot only with the sign of
    a long: the other sign is a short's, which the lot ledger does not take."""
    quantity = _exact(row.quantity)
    if (quantity < _ZERO) != negative or quantity == _ZERO:
        raise LotLedgerError(
            f"adjustment {_describe(row)} has quantity {row.quantity!r}: the sign of a "
            f"short lot, which the lot ledger does not take"
        )


def _explanation(row: AdjustmentRow) -> Mapping[str, object]:
    """The row's `explanation_json` as an object, or empty when it is absent or
    not one (today's free text names no terms)."""
    if row.explanation_json is None:
        return {}
    try:
        parsed = json.loads(row.explanation_json)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _allocation(value: object, row: AdjustmentRow) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise LotLedgerError(f"spin-off {_describe(row)} has basis_allocation {value!r}")
    fraction = _exact(float(value))
    if not _ZERO <= fraction <= 1:
        raise LotLedgerError(f"spin-off {_describe(row)} has basis_allocation {value!r}")
    return fraction


def _merger_id(row: AdjustmentRow) -> str | None:
    value = _explanation(row).get(_MERGER_KEY)
    return value if isinstance(value, str) else None


def _merger_number(value: object, row: AdjustmentRow, key: str) -> Fraction:
    """A finite nonnegative merger term journaled at receipt time."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise LotLedgerError(f"merger {_describe(row)} lacks {key}")
    number = _exact(float(value))
    if number < _ZERO:
        raise LotLedgerError(f"merger {_describe(row)} has negative {key}")
    return number


def _adjustment_key(row: AdjustmentRow) -> tuple[object, ...]:
    """A total order over adjustments, so the order they arrive in never matters."""
    return (
        row.kind,
        row.security_id or "",
        row.quantity if row.quantity is not None else 0.0,
        row.cash if row.cash is not None else 0.0,
        row.explanation_json or "",
        row.adjustment_id if row.adjustment_id is not None else 0,
    )


def rebuild(
    fills: Sequence[OrderedFill],
    orders: Sequence[OrderRow],
    assets: Mapping[str, Asset],
    account: LedgerAccount,
    *,
    adjustments: Sequence[AdjustmentRow] = (),
    actions: pl.DataFrame | None = None,
    through: date | None = None,
    symbols: Mapping[str, str] | None = None,
) -> tuple[list[Lot], list[Disposal], list[WashSaleFlag]]:
    """Lots, FIFO disposals and wash-sale flags from `fills_for`'s live fills
    (module docstring). `orders` must hold every fill's order; `assets` maps a
    broker symbol to its `Asset` for the CUSIP.

    `adjustments` are the window's `adjustments` rows, `actions` a
    `live_actions_as_of(close(S-1))` frame (its `split` rows count), `through`
    the session S (a split applies when `ex_date <= through`, an adjustment when
    `session <= through`; every one when None) and `symbols` maps a
    `security_id` to its broker symbol, for the lots an adjustment opens or
    converts (the security id when absent). Long lots only: a corporate action
    on an open short lot raises.

    A loss's flags depend on acquisitions after it, as the rule requires, so a
    caller reporting the ledger as of an instant t must pass only fills with
    `known_at <= t`; `fills_for` does not filter by `known_at`. Raises
    `LotLedgerError` on a sale or cover beyond the holding, on a corporate action
    the ledger does not take (module docstring), and on a
    fill with a non-finite quantity or price or a naive `filled_at`: a writer
    must catch it and alert rather than fail its run. Also raises it on any
    order whose `position_side` is neither `schema.LONG` nor `SHORT`, and on
    a name that would be long and short at once (module docstring)."""
    for order in orders:
        if order.position_side not in (LONG, SHORT):
            raise LotLedgerError(
                f"order {order.client_order_id!r} has position_side "
                f"{order.position_side!r}: only 'long' and 'short' lots exist"
            )
    state = _Ledger(account, assets, symbols or {})
    for _, event in sorted(_events(fills, orders, adjustments, actions, through), key=_by_key):
        event(state)
    lot_list = [lot.to_lot(account) for lot in state.lots]
    return (
        lot_list,
        state.disposals,
        _wash_sale_flags(
            lot_list,
            state.disposals,
            state.same_sale,
            state.disposal_security,
            state.no_replacement,
            {lot.lot_id: lot.identities for lot in state.lots},
            state.disposal_units,
            {lot.lot_id: lot.acquired for lot in state.lots},
        ),
    )


_Event = Callable[[_Ledger], None]


def _by_key(item: tuple[tuple[object, ...], _Event]) -> tuple[object, ...]:
    return item[0]


def _events(
    fills: Sequence[OrderedFill],
    orders: Sequence[OrderRow],
    adjustments: Sequence[AdjustmentRow],
    actions: pl.DataFrame | None,
    through: date | None,
) -> list[tuple[tuple[object, ...], _Event]]:
    """Every event of the window with its sort key: the session, then split,
    `cash_in_lieu`, fills and other adjustments (the positions ledger's own
    order), then a total order within each."""
    events: list[tuple[tuple[object, ...], _Event]] = []
    if actions is not None and not actions.is_empty():
        for item in actions.iter_rows(named=True):
            if item["action_type"] != _SPLIT or (through is not None and item["ex_date"] > through):
                continue
            ratio = float(item["ratio_or_amount"])
            if not math.isfinite(ratio) or ratio <= 0:
                raise LotLedgerError(f"split of {item['security_id']} has ratio {ratio}")
            security_id = item["security_id"]
            events.append(
                (
                    (item["ex_date"], _RANK_SPLIT, security_id, ratio),
                    partial(_Ledger.split, security_id=security_id, ratio=_split_ratio(ratio)),
                )
            )
    for trade in _trades(fills, orders):
        events.append(
            (
                (
                    trade.trade_at.astimezone(_EXCHANGE_TZ).date(),
                    _RANK_FILL,
                    trade.trade_at,
                    trade.order_key,
                ),
                partial(_Ledger.trade, trade=trade),
            )
        )
    window = [r for r in adjustments if through is None or r.session <= through]
    removals = _by_merger(window, _CORPORATE_ACTION_CASH, unique=False)
    receipts = _by_merger(window, _MERGER_RECEIPT, unique=True)
    cash_rows: dict[str, list[AdjustmentRow]] = {}
    quantity_removals = {
        merger_id
        for row in window
        if row.kind == _CORPORATE_ACTION_CASH and row.quantity is not None
        if (merger_id := _merger_id(row)) is not None
    }
    for row in window:
        merger_id = _merger_id(row)
        if merger_id is not None and row.kind == _CORPORATE_ACTION_CASH:
            if row.quantity is None and merger_id not in quantity_removals:
                raise LotLedgerError(f"merger cash {_describe(row)} has no merger removal row")
            cash_rows.setdefault(merger_id, []).append(row)
    late = _late_delisting_cash(window)
    for row in window:
        if _is_late_delisting_cash(row):
            continue  # included with its removal's proceeds
        action = _adjustment_event(row, removals, receipts, cash_rows, late.get(id(row), ()))
        if action is not None:
            rank = _RANK_CASH_IN_LIEU if row.kind == _CASH_IN_LIEU else _RANK_ADJUSTMENT
            events.append(((row.session, rank, *_adjustment_key(row)), action))
    return events


def _is_late_delisting_cash(row: AdjustmentRow) -> bool:
    """Whether `row` is a delisting's late cash: a cash-only `corporate_action_cash`
    row marked `delisting_cash` and naming no merger (#411)."""
    return (
        row.kind == _CORPORATE_ACTION_CASH
        and row.quantity is None
        and _merger_id(row) is None
        and _explanation(row).get(_DELISTING_KEY) is True
    )


def _late_delisting_cash(window: Sequence[AdjustmentRow]) -> dict[int, list[AdjustmentRow]]:
    """Each ended-listing removal row (by `id`) to the late cash rows of its name
    that follow it, so the removal disposes the lots at the whole proceeds (#411).
    A late row goes to the latest removal of its name stated on or before its
    session; one with none raises."""
    removals: dict[str, list[AdjustmentRow]] = {}
    for row in window:
        if (
            row.kind == _CORPORATE_ACTION_CASH
            and row.quantity is not None
            and row.quantity < 0
            and _merger_id(row) is None
            and row.security_id is not None
        ):
            removals.setdefault(row.security_id, []).append(row)
    found: dict[int, list[AdjustmentRow]] = {}
    for row in window:
        if not _is_late_delisting_cash(row):
            continue
        earlier = [r for r in removals.get(row.security_id or "", []) if r.session <= row.session]
        if not earlier:
            raise LotLedgerError(f"delisting cash {_describe(row)} has no removal row")
        target = max(earlier, key=lambda r: (r.session, r.adjustment_id or 0))
        found.setdefault(id(target), []).append(row)
    return found


def _by_merger(
    window: Sequence[AdjustmentRow], kind: str, *, unique: bool
) -> dict[str | None, AdjustmentRow]:
    """The `kind` rows keyed by their merger id. When `unique`, two rows of one
    id raise (a merger has one receipt, and a second would be lost); later
    cash-only removal rows naming an id are allowed (item 2)."""
    found: dict[str | None, AdjustmentRow] = {}
    for row in window:
        merger = _merger_id(row)
        if row.kind != kind or merger is None:
            continue
        if unique and merger in found:
            raise LotLedgerError(
                f"merger {merger!r} has two {kind} rows ({_describe(found[merger])} and "
                f"{_describe(row)}): the lot ledger takes one removal and one receipt"
            )
        found[merger] = row
    return found


def _adjustment_event(
    row: AdjustmentRow,
    removals: Mapping[str | None, AdjustmentRow],
    receipts: Mapping[str | None, AdjustmentRow],
    cash_rows: Mapping[str, Sequence[AdjustmentRow]],
    late: Sequence[AdjustmentRow] = (),
) -> _Event | None:
    """The ledger action an adjustment makes, or None when it has no lot effect
    (a dividend, a fee) or is half of a merger the removal row carries out."""
    if row.kind == _CASH_IN_LIEU:
        return partial(_Ledger.cash_in_lieu, row=row)
    if row.kind == _CORPORATE_ACTION_CASH:
        merger = _merger_id(row)
        if row.quantity is None and merger is not None:
            return None  # included with its merger's removal when the ledger rebuilds
        if merger is not None and merger in receipts:
            return partial(
                _Ledger.merge,
                removal=row,
                receipt=receipts[merger],
                cash_rows=cash_rows.get(merger, ()),
            )
        if merger is not None:
            cash_total = sum((_cash(cash_row) for cash_row in cash_rows[merger]), _ZERO)
            return partial(_Ledger.removal, row=row, cash_total=cash_total)
        if late:
            cash_total = _cash(row) + sum((_cash(r) for r in late), _ZERO)
            return partial(_Ledger.removal, row=row, cash_total=cash_total)
        return partial(_Ledger.removal, row=row)
    if row.kind == _MERGER_RECEIPT:
        if _merger_id(row) in removals:
            return None
        raise LotLedgerError(
            f"merger receipt {_describe(row)} names no removal row: a stock merger needs both"
        )
    if row.kind == _SPINOFF_RECEIPT:
        return partial(_Ledger.spinoff, row=row)
    if row.kind == _CARRIED_RESIDUE:
        return partial(_Ledger.residue, row=row)
    return None


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
                    position_side=known[first.fill.client_order_id].position_side,
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
                position_side=known[item.fill.client_order_id].position_side,
            )
            for item, quantity, price in parts
        )
    return sorted(trades, key=lambda trade: (trade.trade_at, trade.order_key))


def _close_fifo(
    held: list[_OpenLot], quantity: Fraction, what: str
) -> list[tuple[_OpenLot, Fraction, Fraction]]:
    """Close `quantity` from the oldest lots first: (lot, quantity, basis) per
    lot touched. Lots are kept in trade order, so the list is FIFO. `what`
    describes the sale for the error."""
    if sum((lot.quantity for lot in held), _ZERO) < quantity:
        raise LotLedgerError(f"{what} is more than the ledger holds")
    closed: list[tuple[_OpenLot, Fraction, Fraction]] = []
    remaining = quantity
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
    same_sale: Mapping[int, Mapping[int, Fraction]],
    disposal_security: Mapping[int, str],
    no_replacement: set[int],
    identities: Mapping[int, set[str]],
    disposal_units: Mapping[int, Fraction],
    lot_acquired: Mapping[int, Fraction],
) -> list[WashSaleFlag]:
    """Match each loss disposal, in disposal order, to replacement shares in
    acquisition order; each share absorbs one loss (module docstring). A lot no
    purchase made (a spin-off child, a carried residue) is never a replacement.
    A disposal's sale and the shares it closed are scaled to today's share units
    first (#1980), so a split or merger in between never matches more, or
    fewer, shares than the loss covers. `lot_acquired` is each lot's exact
    `acquired`, never the rounded float a `Lot` reports, so a reverse split's
    non-terminating ratio cannot leave a float remainder (#1980)."""
    by_acquisition = sorted(
        (lot for lot in lot_list if lot.position_side == LONG and lot.lot_id not in no_replacement),
        key=lambda lot: (lot.trade_at, lot.lot_id),
    )
    absorbed: dict[int, Fraction] = {}
    flags: list[WashSaleFlag] = []
    for disposal in disposals:
        loss = -_dec(disposal.realised_pnl)
        if loss <= _ZERO or disposal.position_side != LONG:
            continue
        security = disposal_security[disposal.disposal_id]
        sold_here = same_sale[disposal.disposal_id]
        scale = disposal_units[disposal.disposal_id]
        quantity = sold_here[disposal.lot_id] * scale
        unmatched = quantity
        for lot in by_acquisition:
            if unmatched == _ZERO:
                break
            days_apart = abs((lot.trade_date_local - disposal.trade_date_local).days)
            if security not in identities[lot.lot_id] or days_apart > WASH_SALE_WINDOW_DAYS:
                continue
            available = (
                lot_acquired[lot.lot_id]
                - absorbed.get(lot.lot_id, _ZERO)
                - sold_here.get(lot.lot_id, _ZERO) * scale
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


def _dec(value: float) -> Fraction:
    """`value` as the exact `Fraction` of its shortest decimal form."""
    return Fraction(repr(float(value)))
