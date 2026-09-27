"""Reconciliation compare: the ledger against the broker (Phase 4 spec req 6,
Definitions > Ledger; plan T55).

`compare(ledger, positions, open_orders, lagging, account, explanations, window,
frozen, *, order_id_prefix)` is pure. It reads no clock, store or broker: the
caller (T61's `reconcile_now`) gathers every input, writes the
`reconciliations` row from the result and journals the adjustments the result
proposes, and raises `ReconciliationError` on a `mismatch`.

**What is compared.**

- `account.account_id` against the window's.
- Per symbol, the ledger quantity stated through S (`execution.ledger`, keyed by
  `security_id` and mapped to the broker symbol by `explanations.symbols`)
  against `positions()`, within the frozen `risk.reconcile_quantity_tolerance`.
  A broker symbol the map does not know is `unknown_symbol` (never journaled); a
  held `security_id` with no symbol is `unknown_security`.
- Ledger cash against `account.cash` within `risk.reconcile_cash_tolerance`.
- The journal's non-terminal orders (`lagging`) against `open_orders()`, both
  ways. A broker order without this system's `order_id_prefix` and its `-`
  separator is `foreign_order`. An own order the journal holds `pending` is a
  resume matter (req 4), not a mismatch. Any other own order the journal does
  not hold open is `unexplained_open_order`. A journal order that was
  acknowledged and is absent from `open_orders()` is `missing_open_order`
  unless its `get_order` reading is terminal (finished; the collector journals
  it once its fills arrive).

**Explanations** (all read by the caller from rows known at close(S-1)):

- A split with `ex_date <= S` is applied by the ledger itself, from the same
  `live_actions_as_of(close(S-1))` frame, so compare adds no split: a split is
  never applied twice, and one known only after close(S-1) leaves the ledger in
  pre-split shares and the broker's quantity a `quantity` mismatch.
- An ended listing (`explanations.ended`) explains a held name the broker no
  longer holds: a `corporate_action_cash` adjustment removes the ledger
  quantity and credits the unexplained cash difference, shared among ended
  names by quantity times reference price. Cash can only arrive, so a negative
  difference stays a `cash` mismatch.
- A spin-off child (`explanations.spinoffs`, child to parent, `ex_date <= S`)
  explains a broker position the ledger lacks when the ledger holds the parent:
  a `spinoff_receipt` adjustment adds it.
- A credited dividend (`explanations.dividends`, cash per share for dividends
  paid by S) explains a cash difference equal to their total over the ledger's
  holdings: one `dividend_cash` adjustment per name. It is not journaled when
  the cash does not show it.

**Allowances, never a re-base.** An acknowledged order is `fills_lagging` when
its reading's `filled_quantity` exceeds its journaled fills by more than the
quantity tolerance. Its symbol may then differ, in the order's direction, by up
to the unjournaled quantity, and cash by up to that quantity times the reading's
`filled_avg_price`. While any order is `pending`, the status is
`pending_unresolved` whatever the differences, any quantity difference on a
pending order's symbol is allowed, and cash may differ by up to the sum of the
pending orders' notionals (a quantity order at quantity times reference price
times (1 + `risk.whole_share_price_buffer`)). The allowances add up per symbol
and for cash, and each keeps the tolerance.

**Status**: `mismatch` over `pending_unresolved` over `fills_lagging` over `ok`.
Only an `ok` result carries `broker_cash`, the value that re-bases the ledger's
cash. `mismatches_json` always lists the mismatches, the lagging and pending
ids and what was explained.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tradepartner.adapters.broker import TERMINAL_STATUSES, Account, Order, Position
from tradepartner.config import RiskConfig
from tradepartner.execution.ledger import Ledger
from tradepartner.store.journal import OrderRow, PaperWindowRow

OK = "ok"
MISMATCH = "mismatch"
PENDING_UNRESOLVED = "pending_unresolved"
FILLS_LAGGING = "fills_lagging"

_BUY = "buy"
_ID_SEPARATOR = "-"


@dataclass(frozen=True)
class JournalOpenOrder:
    """One order the journal holds non-terminal: its row, whether it was never
    acknowledged (`pending`), the sum of its live journaled fills, and the
    broker's `get_order` reading (None for a `pending` order, never read)."""

    order: OrderRow
    pending: bool
    journaled_quantity: float
    reading: Order | None


@dataclass(frozen=True)
class Explanations:
    """What the store knew at close(S-1), gathered by the caller.

    `symbols`: `security_id` to broker symbol, for every name the ledger or the
    broker may hold. `ended`: names whose listing ended by S. `spinoffs`: child
    `security_id` to parent, `ex_date <= S`. `dividends`: cash per share of the
    dividends paid by S. `reference_prices`: close(S-1) per `security_id`, for
    pending quantity orders and for sharing an ended listing's cash."""

    symbols: Mapping[str, str]
    ended: frozenset[str] = frozenset()
    spinoffs: Mapping[str, str] = field(default_factory=dict)
    dividends: Mapping[str, float] = field(default_factory=dict)
    reference_prices: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Mismatch:
    """One difference nothing explains."""

    kind: str
    detail: str
    security_id: str | None = None
    symbol: str | None = None
    client_order_id: str | None = None


@dataclass(frozen=True)
class ProposedAdjustment:
    """An `adjustments` row an explanation implies, for the caller to journal:
    signed `quantity` and `cash` deltas, as `execution.ledger` applies them."""

    kind: str
    security_id: str
    quantity: float | None
    cash: float | None
    explanation: str


@dataclass(frozen=True)
class Reconciliation:
    """The result of one compare (module docstring)."""

    status: str
    broker_cash: float | None
    mismatches: tuple[Mismatch, ...]
    lagging_ids: tuple[str, ...]
    pending_ids: tuple[str, ...]
    adjustments: tuple[ProposedAdjustment, ...]

    @property
    def mismatches_json(self) -> str:
        """The `reconciliations.mismatches_json` value, stable for equal results."""
        return json.dumps(
            {
                "mismatches": [vars(m) for m in self.mismatches],
                "lagging": list(self.lagging_ids),
                "pending": list(self.pending_ids),
                "explained": [vars(a) for a in self.adjustments],
            },
            sort_keys=True,
        )


@dataclass
class _Band:
    """Allowed range of a broker-minus-ledger difference, before tolerance."""

    low: float = 0.0
    high: float = 0.0
    any: bool = False

    def widen(self, amount: float) -> None:
        self.low = min(self.low, self.low + amount)
        self.high = max(self.high, self.high + amount)

    def allows(self, difference: float, tolerance: float) -> bool:
        return self.any or self.low - tolerance <= difference <= self.high + tolerance


def compare(
    ledger: Ledger,
    positions: Mapping[str, Position],
    open_orders: Sequence[Order],
    lagging: Sequence[JournalOpenOrder],
    account: Account,
    explanations: Explanations,
    window: PaperWindowRow,
    frozen: RiskConfig,
    *,
    order_id_prefix: str,
) -> Reconciliation:
    """Compare the ledger stated through S with the broker (module docstring).

    `positions` is `Broker.positions()` (keyed by symbol), `open_orders` is
    `Broker.open_orders()`, `lagging` is every non-terminal journal order of the
    window with its reading, `frozen` is the window's frozen `risk` section and
    `order_id_prefix` is `paper.order_id_prefix`. Raises `ValueError` on an
    input it cannot compare (a pending quantity order with no reference price,
    a symbol mapped from two names)."""
    quantity_tolerance = frozen.reconcile_quantity_tolerance
    cash_tolerance = frozen.reconcile_cash_tolerance
    mismatches: list[Mismatch] = []
    if account.account_id != window.account_id:
        mismatches.append(
            Mismatch("account_id", f"broker {account.account_id!r}, window {window.account_id!r}")
        )

    security_of = _reverse(explanations.symbols)
    bands: dict[str, _Band] = defaultdict(_Band)
    cash_band = _Band()
    lagging_ids: list[str] = []
    pending_ids: list[str] = []
    for item in lagging:
        order = item.order
        sign = 1 if order.side == _BUY else -1
        if item.pending:
            pending_ids.append(order.client_order_id)
            bands[order.security_id].any = True
            spend = _pending_cash(order, explanations, frozen)
            cash_band.widen(spend)
            cash_band.widen(-spend)
            continue
        reading = item.reading
        if reading is None or reading.filled_quantity is None:
            continue
        unjournaled = reading.filled_quantity - item.journaled_quantity
        if unjournaled > quantity_tolerance:
            lagging_ids.append(order.client_order_id)
            price = reading.filled_avg_price or 0.0
            bands[order.security_id].widen(sign * unjournaled)
            cash_band.widen(-sign * unjournaled * price)

    mismatches += _order_mismatches(open_orders, lagging, order_id_prefix)

    expected = {name: q for name, q in ledger.positions.items()}
    broker_by_security: dict[str, float] = {}
    for symbol, position in positions.items():
        security_id = security_of.get(symbol)
        if security_id is None:
            mismatches.append(
                Mismatch("unknown_symbol", "broker holds a symbol never journaled", symbol=symbol)
            )
            continue
        broker_by_security[security_id] = position.quantity

    adjustments: list[ProposedAdjustment] = []
    removed: list[str] = []
    for security_id in sorted(explanations.ended):
        held = expected.get(security_id, 0.0)
        if held > quantity_tolerance and security_id not in broker_by_security:
            removed.append(security_id)
    for child, parent in sorted(explanations.spinoffs.items()):
        received = broker_by_security.get(child, 0.0)
        if (
            abs(expected.get(child, 0.0)) <= quantity_tolerance
            and received > quantity_tolerance
            and expected.get(parent, 0.0) > quantity_tolerance
        ):
            expected[child] = received
            adjustments.append(
                ProposedAdjustment(
                    "spinoff_receipt", child, received, None, f"spin-off child of {parent}"
                )
            )

    cash_difference = account.cash - ledger.cash
    cash_ok = cash_band.allows(cash_difference, cash_tolerance)
    dividends = {
        name: per_share * expected[name]
        for name, per_share in sorted(explanations.dividends.items())
        if expected.get(name, 0.0) > quantity_tolerance
    }
    if not cash_ok and dividends:
        credited = math.fsum(dividends.values())
        if cash_band.allows(cash_difference - credited, cash_tolerance):
            cash_ok = True
            adjustments += [
                ProposedAdjustment("dividend_cash", name, None, amount, "credited dividend")
                for name, amount in dividends.items()
            ]
    if removed:
        # The position removal is explained whatever the cash says; the cash
        # difference is the proceeds only when it is not negative.
        proceeds = 0.0
        if not cash_ok and cash_difference >= -cash_tolerance:
            cash_ok, proceeds = True, max(cash_difference, 0.0)
        adjustments += _ended_adjustments(removed, expected, explanations, proceeds)
        for security_id in removed:
            expected.pop(security_id)
    if not cash_ok:
        mismatches.append(Mismatch("cash", f"broker {account.cash}, ledger {ledger.cash}"))

    for security_id in sorted(set(expected) | set(broker_by_security)):
        mismatches += _position_mismatch(
            security_id,
            expected.get(security_id, 0.0),
            broker_by_security.get(security_id),
            bands.get(security_id, _Band()),
            explanations.symbols.get(security_id),
            quantity_tolerance,
        )

    if mismatches:
        status = MISMATCH
    elif pending_ids:
        status = PENDING_UNRESOLVED
    elif lagging_ids:
        status = FILLS_LAGGING
    else:
        status = OK
    return Reconciliation(
        status=status,
        broker_cash=account.cash if status == OK else None,
        mismatches=tuple(mismatches),
        lagging_ids=tuple(sorted(lagging_ids)),
        pending_ids=tuple(sorted(pending_ids)),
        adjustments=tuple(adjustments),
    )


def _reverse(symbols: Mapping[str, str]) -> dict[str, str]:
    reverse: dict[str, str] = {}
    for security_id, symbol in symbols.items():
        if reverse.setdefault(symbol, security_id) != security_id:
            raise ValueError(f"symbol {symbol!r} maps from {reverse[symbol]!r} and {security_id!r}")
    return reverse


def _pending_cash(order: OrderRow, explanations: Explanations, frozen: RiskConfig) -> float:
    """What a pending order could have moved in cash: its notional, or its
    quantity at the buffered reference price."""
    if order.notional is not None:
        return abs(order.notional)
    price = explanations.reference_prices.get(order.security_id)
    if price is None or order.quantity is None:
        raise ValueError(
            f"pending order {order.client_order_id!r} has no notional and no reference price"
        )
    return abs(order.quantity) * price * (1 + frozen.whole_share_price_buffer)


def _order_mismatches(
    open_orders: Sequence[Order], lagging: Sequence[JournalOpenOrder], prefix: str
) -> list[Mismatch]:
    own = prefix + _ID_SEPARATOR
    journal = {item.order.client_order_id: item for item in lagging}
    broker_open = {order.client_order_id for order in open_orders}
    found: list[Mismatch] = []
    for order in open_orders:
        coid = order.client_order_id
        if not coid.startswith(own):
            found.append(
                Mismatch(
                    "foreign_order",
                    "open order without this system's prefix",
                    symbol=order.symbol,
                    client_order_id=coid,
                )
            )
        elif coid not in journal:
            found.append(
                Mismatch(
                    "unexplained_open_order",
                    "own open order the journal does not hold open",
                    symbol=order.symbol,
                    client_order_id=coid,
                )
            )
    for coid, item in sorted(journal.items()):
        if item.pending or coid in broker_open:
            continue
        reading = item.reading
        if reading is None or reading.status not in TERMINAL_STATUSES:
            found.append(
                Mismatch(
                    "missing_open_order",
                    "journal-open order the broker does not list open",
                    security_id=item.order.security_id,
                    client_order_id=coid,
                )
            )
    return found


def _ended_adjustments(
    removed: Sequence[str],
    expected: Mapping[str, float],
    explanations: Explanations,
    proceeds: float,
) -> list[ProposedAdjustment]:
    """One `corporate_action_cash` row per removed name, the proceeds shared by
    quantity times reference price (equally when no weight is known)."""
    weights = [expected[name] * explanations.reference_prices.get(name, 0.0) for name in removed]
    total = math.fsum(weights)
    if total <= 0:
        weights, total = [1.0] * len(removed), float(len(removed))
    return [
        ProposedAdjustment(
            "corporate_action_cash",
            name,
            -expected[name],
            proceeds * weight / total,
            "listing ended",
        )
        for name, weight in zip(removed, weights, strict=True)
    ]


def _position_mismatch(
    security_id: str,
    expected: float,
    broker: float | None,
    band: _Band,
    symbol: str | None,
    tolerance: float,
) -> list[Mismatch]:
    held = abs(expected) > tolerance
    if symbol is None:
        if not held:
            return []
        return [Mismatch("unknown_security", "ledger name with no broker symbol", security_id)]
    difference = (broker or 0.0) - expected
    if band.allows(difference, tolerance):
        return []
    if broker is None:
        kind = "ledger_only_position"
    elif not held:
        kind = "broker_only_position"
    else:
        kind = "quantity"
    return [Mismatch(kind, f"broker {broker}, ledger {expected}", security_id, symbol)]
