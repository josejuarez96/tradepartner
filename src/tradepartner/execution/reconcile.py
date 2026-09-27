"""Reconciliation compare: the ledger against the broker (Phase 4 spec req 6,
Definitions > Ledger; plan T55).

`compare(ledger, positions, open_orders, lagging, account, explanations, window,
frozen, *, order_id_prefix)` is pure. It reads no clock, store or broker: the
caller (T61's `reconcile_now`) gathers every input, writes the
`reconciliations` row from the result, journals the result's `adjustments`
(present only on an `ok` result) and raises `ReconciliationError` on a
`mismatch`.

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
  not hold open is `unexplained_open_order`, and one it does hold open for
  another symbol or side is `open_order_differs`. A journal order that was
  acknowledged and is absent from `open_orders()` is `missing_open_order`
  unless its `get_order` reading is terminal (finished; the collector journals
  it once its fills arrive).

**Allowances, never a re-base.** An acknowledged order is `fills_lagging` when
its reading's `filled_quantity` exceeds its journaled fills by more than the
quantity tolerance. Its symbol may then differ, in the order's direction, by up
to the unjournaled quantity, and cash, in the order's direction, by up to the
unjournaled notional (`filled_quantity` x `filled_avg_price` less the journaled
notional, the spec's synthetic-fill residual). A reading that cannot bound this
(a non-finite or oversized `filled_quantity`, a missing or non-positive
`filled_avg_price`) is `bad_reading`. While any order is `pending`, the status
is `pending_unresolved` whatever the differences; a pending order allows its
symbol to differ in its direction by up to its quantity (a notional order's at
the buffered reference price) and cash by up to its notional (a quantity
order's at quantity x reference price x (1 + `risk.whole_share_price_buffer`)).
Allowances add up per symbol and for cash; each keeps the tolerance; every
difference an allowance absorbs is listed under `allowed`.

**Explanations** (all read by the caller from rows known at close(S-1)):

- A split with `ex_date <= S` is applied by the ledger itself, from the same
  `live_actions_as_of(close(S-1))` frame, so compare adds no split: a split is
  never applied twice, and one known only after close(S-1) leaves the ledger in
  pre-split shares and the broker's quantity a `quantity` mismatch.
- An ended listing (`explanations.ended`) explains a held name the broker no
  longer holds: a `corporate_action_cash` adjustment removes the ledger
  quantity and credits the cash difference as proceeds, which must be between
  zero and the removed names' value at reference price x (1 +
  `risk.whole_share_price_buffer`); above that, or below zero, it is a `cash`
  mismatch. The proceeds are shared among ended names by that value.
- A spin-off child (`explanations.spinoffs`: child to parent and ratio) explains
  a broker position the ledger lacks when it is at most parent held x ratio and
  less than one share below it (cash in lieu of a fraction): a `spinoff_receipt`
  adjustment adds it.
- A credited dividend (`explanations.dividends`: cash due per name) explains a
  cash difference equal to the total due: one `dividend_cash` adjustment per
  name. It is not journaled when the cash does not show it.

The caller passes only spin-offs and dividends not yet journaled (ex or pay date
after the window's last `ok` reconciliation). While any order is lagging or
pending, no explanation is journaled: an ended or spun-off name is allowed and
listed under `deferred`, its cash allowed up to the same bound, and the next
reconciliation without an open allowance journals it once, so a wrong
adjustment never enters the append-only journal.

**Status**: `mismatch` over `pending_unresolved` over `fills_lagging` over `ok`.
Only an `ok` result carries `broker_cash`, the value that re-bases the ledger's
cash, and `adjustments`. `mismatches_json` always lists the mismatches, the
lagging and pending ids, the allowed and deferred differences and the
explanations. Non-finite inputs, a repeated order id, an unknown side or a
symbol mapped from two names raise `ValueError`.
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
_SELL = "sell"
_ID_SEPARATOR = "-"


@dataclass(frozen=True)
class JournalOpenOrder:
    """One order the journal holds non-terminal: its row, whether it was never
    acknowledged (`pending`), the sums of its live journaled fills' quantity and
    notional (quantity x price), and the broker's `get_order` reading (None for
    a `pending` order, which is never read)."""

    order: OrderRow
    pending: bool
    journaled_quantity: float
    journaled_notional: float
    reading: Order | None


@dataclass(frozen=True)
class Explanations:
    """What the store knew at close(S-1), gathered by the caller.

    `symbols`: `security_id` to broker symbol, for every name the ledger or the
    broker may hold. `ended`: names whose listing ended by S. `spinoffs`: child
    `security_id` to (parent `security_id`, shares per parent share),
    `ex_date <= S`, not yet journaled. `dividends`: cash due per name for the
    dividends paid by S, not yet journaled. `reference_prices`: close(S-1) per
    `security_id`."""

    symbols: Mapping[str, str]
    ended: frozenset[str] = frozenset()
    spinoffs: Mapping[str, tuple[str, float]] = field(default_factory=dict)
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
    """An `adjustments` row an explanation implies: signed `quantity` and
    `cash` deltas, as `execution.ledger` applies them."""

    kind: str
    security_id: str
    quantity: float | None
    cash: float | None
    explanation: str


@dataclass(frozen=True)
class Allowed:
    """A difference an allowance or a deferred explanation absorbed."""

    reason: str
    difference: float
    security_id: str | None = None


@dataclass(frozen=True)
class Reconciliation:
    """The result of one compare (module docstring)."""

    status: str
    broker_cash: float | None
    mismatches: tuple[Mismatch, ...]
    lagging_ids: tuple[str, ...]
    pending_ids: tuple[str, ...]
    adjustments: tuple[ProposedAdjustment, ...]
    explained: tuple[ProposedAdjustment, ...] = ()
    allowed: tuple[Allowed, ...] = ()

    @property
    def mismatches_json(self) -> str:
        """The `reconciliations.mismatches_json` value, stable for equal results."""
        return json.dumps(
            {
                "mismatches": [vars(m) for m in self.mismatches],
                "lagging": list(self.lagging_ids),
                "pending": list(self.pending_ids),
                "explained": [vars(a) for a in self.explained],
                "allowed": [vars(a) for a in self.allowed],
            },
            sort_keys=True,
            allow_nan=False,
        )


@dataclass
class _Band:
    """Allowed range of a broker-minus-ledger difference, before tolerance."""

    low: float = 0.0
    high: float = 0.0

    def widen(self, amount: float) -> None:
        self.low = min(self.low, self.low + amount)
        self.high = max(self.high, self.high + amount)

    def allows(self, difference: float, tolerance: float) -> bool:
        return self.low - tolerance <= difference <= self.high + tolerance

    @property
    def open(self) -> bool:
        return self.low < 0 or self.high > 0


def _finite(value: float, what: str) -> float:
    if isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{what} is {value!r}, not a finite number")
    return value


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
    `order_id_prefix` is `paper.order_id_prefix`."""
    q_tol = frozen.reconcile_quantity_tolerance
    cash_tol = frozen.reconcile_cash_tolerance
    buffer = frozen.whole_share_price_buffer
    _validate(ledger, positions, account, explanations)
    security_of = _reverse(explanations.symbols)
    mismatches: list[Mismatch] = []
    if account.account_id != window.account_id:
        mismatches.append(
            Mismatch("account_id", f"broker {account.account_id!r}, window {window.account_id!r}")
        )

    bands: dict[str, _Band] = defaultdict(_Band)
    cash_band = _Band()
    lagging_ids: list[str] = []
    pending_ids: list[str] = []
    seen: set[str] = set()
    for item in lagging:
        order = item.order
        if order.client_order_id in seen:
            raise ValueError(f"order {order.client_order_id!r} is listed twice")
        seen.add(order.client_order_id)
        if order.side not in (_BUY, _SELL):
            raise ValueError(f"order {order.client_order_id!r} has side {order.side!r}")
        sign = 1 if order.side == _BUY else -1
        if item.pending:
            pending_ids.append(order.client_order_id)
            shares, spend = _pending_bounds(order, explanations, buffer)
            bands[order.security_id].widen(sign * shares)
            cash_band.widen(-sign * spend)
            continue
        lag = _lag(item, q_tol)
        if isinstance(lag, Mismatch):
            mismatches.append(lag)
        elif lag is not None:
            lagging_ids.append(order.client_order_id)
            shares, notional = lag
            bands[order.security_id].widen(sign * shares)
            cash_band.widen(-sign * notional)

    mismatches += _order_mismatches(open_orders, lagging, order_id_prefix)

    expected = dict(ledger.positions)
    broker: dict[str, float] = {}
    for symbol, position in positions.items():
        security_id = security_of.get(symbol)
        if security_id is None:
            mismatches.append(
                Mismatch("unknown_symbol", "broker holds a symbol never journaled", symbol=symbol)
            )
        else:
            broker[security_id] = position.quantity

    deferring = bool(lagging_ids or pending_ids)
    explained: list[ProposedAdjustment] = []
    allowed: list[Allowed] = []

    removed = [
        name
        for name in sorted(explanations.ended)
        if expected.get(name, 0.0) > q_tol and name not in broker
    ]
    removed_quantity = {name: expected[name] for name in removed}
    for child, (parent, ratio) in sorted(explanations.spinoffs.items()):
        received = broker.get(child, 0.0)
        due = expected.get(parent, 0.0) * _finite(ratio, f"spin-off ratio of {child}")
        # At most parent x ratio, and less than one share below it (cash in
        # lieu of a fractional share).
        if (
            abs(expected.get(child, 0.0)) <= q_tol
            and q_tol < received <= due + q_tol
            and received > due - 1
        ):
            expected[child] = received
            explained.append(
                ProposedAdjustment(
                    "spinoff_receipt", child, received, None, f"spin-off child of {parent}"
                )
            )

    difference = account.cash - ledger.cash
    cash_ok = cash_band.allows(difference, cash_tol)
    if removed:
        value = [expected[name] * explanations.reference_prices.get(name, 0.0) for name in removed]
        cap = math.fsum(value) * (1 + buffer)
        proceeds = difference
        if deferring:
            cash_band.widen(cap)
            cash_ok = cash_band.allows(difference, cash_tol)
        elif -cash_tol <= proceeds <= cap + cash_tol:
            cash_ok = True
            proceeds = min(max(proceeds, 0.0), cap)
            explained += _ended_adjustments(removed, expected, value, proceeds)
        else:
            cash_ok = False
        for name in removed:
            expected.pop(name)
    dividends = {
        name: _finite(amount, f"dividend due on {name}")
        for name, amount in sorted(explanations.dividends.items())
    }
    if not removed and not cash_ok and dividends and not deferring:
        credited = math.fsum(dividends.values())
        if abs(difference - credited) <= cash_tol:
            cash_ok = True
            explained += [
                ProposedAdjustment("dividend_cash", name, None, amount, "credited dividend")
                for name, amount in dividends.items()
            ]
    if not cash_ok:
        mismatches.append(Mismatch("cash", f"broker {account.cash}, ledger {ledger.cash}"))
    elif abs(difference) > cash_tol and deferring:
        allowed.append(Allowed("cash within the open allowances", difference))

    if deferring:
        for adjustment in [a for a in explained if a.kind == "spinoff_receipt"]:
            allowed.append(
                Allowed(
                    "deferred spin-off receipt", adjustment.quantity or 0.0, adjustment.security_id
                )
            )
        allowed += [
            Allowed("deferred ended listing", -removed_quantity[name], name) for name in removed
        ]
        explained = []

    for security_id in sorted(set(expected) | set(broker)):
        band = bands.get(security_id, _Band())
        found = _position_mismatch(
            security_id,
            expected.get(security_id, 0.0),
            broker.get(security_id),
            band,
            explanations.symbols.get(security_id),
            q_tol,
        )
        if found:
            mismatches.append(found)
        elif band.open:
            gap = broker.get(security_id, 0.0) - expected.get(security_id, 0.0)
            if abs(gap) > q_tol:
                allowed.append(Allowed("quantity within the open allowances", gap, security_id))

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
        adjustments=tuple(explained) if status == OK else (),
        explained=tuple(explained),
        allowed=tuple(allowed),
    )


def _validate(
    ledger: Ledger,
    positions: Mapping[str, Position],
    account: Account,
    explanations: Explanations,
) -> None:
    _finite(ledger.cash, "ledger cash")
    _finite(account.cash, "broker cash")
    for name, quantity in ledger.positions.items():
        _finite(quantity, f"ledger quantity of {name}")
    for symbol, position in positions.items():
        _finite(position.quantity, f"broker quantity of {symbol}")
    for name, price in explanations.reference_prices.items():
        if _finite(price, f"reference price of {name}") <= 0:
            raise ValueError(f"reference price of {name} is {price}, must be positive")


def _reverse(symbols: Mapping[str, str]) -> dict[str, str]:
    reverse: dict[str, str] = {}
    for security_id, symbol in symbols.items():
        if reverse.setdefault(symbol, security_id) != security_id:
            raise ValueError(f"symbol {symbol!r} maps from {reverse[symbol]!r} and {security_id!r}")
    return reverse


def _pending_bounds(
    order: OrderRow, explanations: Explanations, buffer: float
) -> tuple[float, float]:
    """(shares, cash) a pending order could have moved: quantity, or notional
    at the buffered reference price, and the other way round."""
    price = explanations.reference_prices.get(order.security_id)
    if price is None:
        raise ValueError(f"pending order {order.client_order_id!r} has no reference price")
    if order.notional is not None:
        notional = abs(_finite(order.notional, f"notional of {order.client_order_id}"))
        return notional / price * (1 + buffer), notional
    if order.quantity is None:
        raise ValueError(f"pending order {order.client_order_id!r} has no size")
    quantity = abs(_finite(order.quantity, f"quantity of {order.client_order_id}"))
    return quantity, quantity * price * (1 + buffer)


def _lag(item: JournalOpenOrder, tolerance: float) -> tuple[float, float] | Mismatch | None:
    """(unjournaled shares, unjournaled notional) of a lagging order, None when
    it is not lagging, or a `bad_reading` mismatch when the reading cannot
    bound the lag."""
    reading, order = item.reading, item.order
    if reading is None or reading.filled_quantity is None:
        return None
    filled = reading.filled_quantity
    unjournaled = filled - item.journaled_quantity
    if not unjournaled > tolerance:
        return None
    too_many = order.quantity is not None and filled > order.quantity + tolerance
    price = reading.filled_avg_price
    if (
        not math.isfinite(filled)
        or too_many
        or price is None
        or not math.isfinite(price)
        or price <= 0
    ):
        return Mismatch(
            "bad_reading",
            f"filled {filled} at {price} cannot bound the lag",
            security_id=order.security_id,
            client_order_id=order.client_order_id,
        )
    notional = filled * price - item.journaled_notional
    return unjournaled, max(notional, 0.0)


def _order_mismatches(
    open_orders: Sequence[Order], lagging: Sequence[JournalOpenOrder], prefix: str
) -> list[Mismatch]:
    own = prefix + _ID_SEPARATOR
    journal = {item.order.client_order_id: item for item in lagging}
    broker_open = {order.client_order_id for order in open_orders}
    found: list[Mismatch] = []
    for order in open_orders:
        coid = order.client_order_id
        item = journal.get(coid)
        if not coid.startswith(own):
            kind, detail = "foreign_order", "open order without this system's prefix"
        elif item is None:
            kind, detail = "unexplained_open_order", "own open order the journal does not hold open"
        elif (order.symbol, order.side.value) != (item.order.symbol, item.order.side):
            kind, detail = "open_order_differs", "open order differs from its journal row"
        else:
            continue
        found.append(Mismatch(kind, detail, symbol=order.symbol, client_order_id=coid))
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
    value: Sequence[float],
    proceeds: float,
) -> list[ProposedAdjustment]:
    """One `corporate_action_cash` row per removed name, the proceeds shared by
    reference value (equally when no value is known, the proceeds then zero)."""
    total = math.fsum(value)
    weights = list(value) if total > 0 else [1.0] * len(removed)
    total = total if total > 0 else float(len(removed))
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
) -> Mismatch | None:
    held = abs(expected) > tolerance
    if symbol is None:
        return (
            Mismatch("unknown_security", "ledger name with no broker symbol", security_id)
            if held
            else None
        )
    if band.allows((broker or 0.0) - expected, tolerance):
        return None
    if broker is None:
        kind = "ledger_only_position"
    elif not held:
        kind = "broker_only_position"
    else:
        kind = "quantity"
    return Mismatch(kind, f"broker {broker}, ledger {expected}", security_id, symbol)
