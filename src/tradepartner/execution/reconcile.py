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
  another symbol or side, or whose shape is not the default
  (`risk.order_shape_violation`: anything but a market day order of a
  `us_equity`, `simple` order with no limit or stop price and no legs; ADR
  0015 seam 3), is `open_order_differs`. A journal order that was
  acknowledged and is absent from `open_orders()` is `missing_open_order`
  unless its `get_order` reading is terminal (finished; the collector journals
  it once its fills arrive). One absent from `open_orders()` whose reading is
  not the default shape (a limit order that filled before this
  reconciliation, say) is `order_differs` whatever its status (#1293, owner
  2026-10-10).

**Allowances, never a re-base.** An acknowledged order is `fills_lagging` when
its reading's `filled_quantity` exceeds its journaled fills by more than the
quantity tolerance. Its symbol may then differ, in the order's direction, by up
to the unjournaled quantity, and cash, in the order's direction, by up to the
unjournaled notional (`filled_quantity` x `filled_avg_price` less the journaled
notional, the spec's synthetic-fill residual). A reading that cannot bound this
(a non-finite `filled_quantity`, one above a quantity order's size, a filled
value above a notional order's notional x (1 + `risk.whole_share_price_buffer`),
a missing or non-positive `filled_avg_price`) is `bad_reading`. While any
order is `pending`, the status
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
- A ticker change known at close(S-1) (`explanations.symbol_changes`,
  `security_id` to `(old symbol, new symbol)`) is the one identity explanation
  (#1761): the caller has mapped the name to the broker's new symbol from the
  store alone, so the ledger's position under the old symbol matches the
  broker's under the new one, and `compare` records the mapping on the row's
  `symbol_changes` key. No identity rule is invented, so a change not known at
  close(S-1), a quantity difference under the new symbol, or two candidate new
  symbols stay a mismatch.
- A credited dividend (`explanations.dividends`: cash due per name) explains a
  cash difference equal to the total due: one `dividend_cash` adjustment per
  name. It is not journaled when the cash does not show it. Beside an ended
  listing the proceeds take the cash first, and the dividends are journaled
  beside them only when the cash exceeds the proceeds' cap (#400). A
  negative amount due raises `ValueError` unless the name is a short in
  `Ledger.short_holdings`, where the due is negative (-quantity x amount) and
  the cash band is [due, 0]: no debit passes and journals nothing, the whole
  due debited passes and journals a negative `dividend_cash`, and a positive
  due on a short raises (T233, paper spec item 10).
- A short (`Ledger.short_holdings`) is compared as a negative quantity against
  the broker's. Any difference no own fill explains (a buy-in, a recall, an
  external add, a broker long) is a `short_external_change` mismatch on the
  name, never adopted; our own lagging fills explain it through the allowances
  above. A cash merger on a short is not explained here (#1930, T249s): it
  halts.
- An open cash merger on a long (`explanations.mergers`, acquiree `security_id`
  to `OpenMerger`; paper spec amendment #1762 items 1, 2 and 4, T249). While
  the ledger still holds the acquiree and the broker no longer does, one
  `corporate_action_cash` row naming the merger id removes the held quantity
  and credits the cash it takes, which lies in [0, held x `cash_per_share`];
  the ended-listing band does not apply to that name. Once the removal is
  journaled, a later credit up to the rest of the due is one cash-only
  `corporate_action_cash` row naming the id. The mergers take the cash left
  after the fees, the cash in lieu and the dividends chosen, and before an
  ended name's proceeds. At the first reconciliation after the merger's window
  (`OpenMerger.final`), a journaled total below the due less the cash bound is a
  `cash` mismatch naming the merger, once. A `CIL` on an open merger's acquiree
  or acquirer symbol is left in the cash difference for the merger, never
  booked against the stock-dividend rule. For a stock kind on a held long
  (T249b), a `merger_receipt` adds the acquirer shares; its fractional-share
  cash band stays open for a later `CIL`. A held short (T249s) is not explained.
- A conflicting merger pair (`explanations.merger_conflicts`, acquiree to
  detail: two ids for one acquiree and one `effective_date`) is a
  `merger_conflict` mismatch; the caller raises it once per pair and never
  passes either id in `mergers`.
- A held name with an equal-amount dividend group the reviewed list does not
  explain (`explanations.dividend_findings`, #1625) is an
  `unexplained_dividend_group` mismatch whatever the cash shows: the caller
  never sums such a group, and neither one payment nor two is assumed.

The caller passes only spin-offs and dividends not yet journaled (ex or pay date
after the window's last `ok` reconciliation). While any order is lagging or
pending, no explanation is journaled: an ended or spun-off name is allowed and
listed under `deferred`, its cash allowed up to the same bound, a dividend's
credit allowed up to the amount due (#399), and the next
reconciliation without an open allowance journals it once, so a wrong
adjustment never enters the append-only journal.

**Status**: `mismatch` over `pending_unresolved` over `fills_lagging` over `ok`.
Only an `ok` result carries `broker_cash`, the value that re-bases the ledger's
cash, and `adjustments`. `mismatches_json` always lists the mismatches, the
lagging and pending ids, the allowed and deferred differences, the explanations
and the ticker changes the symbol map applied (`symbol_changes`, #1761).
Non-finite inputs, a repeated order id, an unknown side or a
symbol mapped from two names raise `ValueError`.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from tradepartner.adapters.broker import (
    TERMINAL_STATUSES,
    Account,
    CashActivity,
    Order,
    Position,
)
from tradepartner.config import RiskConfig
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.risk import order_shape_violation
from tradepartner.store.journal import OrderRow, PaperWindowRow

OK = "ok"
MISMATCH = "mismatch"
PENDING_UNRESOLVED = "pending_unresolved"
FILLS_LAGGING = "fills_lagging"
#: The mismatch kind of an equal-amount dividend group the reviewed list does
#: not explain on a held name (#1625, #1613).
UNEXPLAINED_DIVIDEND_GROUP = "unexplained_dividend_group"
#: The mismatch kind of two merger rows with different ids for one acquiree and
#: one `effective_date` (paper spec amendment #1762 item 1, T249).
MERGER_CONFLICT = "merger_conflict"
#: The `mergers.kind` T249 explains; the stock kinds are T249b's.
MERGER_CASH = "cash"

_BUY = "buy"
_SELL = "sell"
_ID_SEPARATOR = "-"


#: The non-trade activity types the ledger knows (spec 2026-10-12, item 2). A
#: `FEE` is booked as `fee_cash`; a dividend type is left to the store's dividend
#: rule; `CIL` (cash in lieu of stock, #1627) is booked as `cash_in_lieu` when the
#: broker credits whole shares plus cash for a held stock dividend's fractional
#: remainder; a corporate-action type (and any row of null `net_amount`) is read
#: and recorded but never booked and never a mismatch by itself; any other cash
#: type is `unexplained_activity` unless it is already inside the base (#1641: its
#: cash is already in the cash the ledger starts from, so it is never a mismatch).
_FEE = "FEE"
_EXECUTED = "executed"
#: `CIL` — cash the broker pays in lieu of a fractional share from a stock
#: dividend (Alpaca `AccountActivityType.CIL` = "Cash in Lieu of Stock"; Trading
#: API "Account activities" > "Activity types", docs.alpaca.markets, and the
#: `alpaca.trading.enums.ActivityType.CIL` in alpaca-py, which docs cite).
_CASH_IN_LIEU = "CIL"
_DIVIDEND_TYPES = frozenset(
    {"DIV", "DIVCGL", "DIVCGS", "DIVNRA", "DIVROC", "DIVTW", "DIVFEE", "DIVTXEX"}
)
_CORPORATE_ACTION_TYPES = frozenset({"SSP", "SSO", "MA", "NC", "SC", "REORG"})


def _cash_bound(cash_tolerance: float, fills_since_base: int) -> Decimal:
    """The cash-tolerance bound (#1583, T183): `risk.reconcile_cash_tolerance` x
    (1 + fills_since_base / 2), in `Decimal`, inclusive. A cent of rounding per
    fill per side replaces the whole-dollar #1290 band; 0 fills keep the bare key.
    It replaces the bare key in every cash comparison but `bad_reading`."""
    return Decimal(str(cash_tolerance)) * (Decimal(1) + Decimal(fills_since_base) / Decimal(2))


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
class OpenMerger:
    """One open merger of a held or removed acquiree (paper spec amendment
    #1762 item 1; T249), as the caller read it from `mergers_as_of(close(S-1))`
    and the journal.

    `merger_id` is Alpaca's id, which every row journaled for the merger names.
    `window_end` is `payable_date` (or `effective_date`) plus
    `risk.reconcile_merger_lag_sessions` sessions. `removed_quantity` is None
    while the ledger still holds the acquiree (open under (a)), else the
    quantity the journaled removal took (its own row, or the ended-listing
    band's row for the acquiree). `journaled_cash` is the cash journaled for it
    so far (its own rows by id, plus the band's removal row). `final` is True at
    the first completed reconciliation after `window_end` at which the row was
    known and the removal journaled or happening: the one that checks the due."""

    merger_id: str
    kind: str
    acquirer: str | None
    share_ratio: float | None
    cash_per_share: float | None
    effective_date: date
    payable_date: date | None
    window_end: date
    removed_quantity: float | None = None
    journaled_cash: float = 0.0
    final: bool = False
    cash_upper: float | None = None


@dataclass(frozen=True)
class _MergerDue:
    """What one open cash merger may take at this reconciliation: `held` is the
    quantity it removes now (None for a cash-only payment), `upper` the whole
    due, `cap` the most it may take now."""

    name: str
    merger: OpenMerger
    held: float | None
    upper: float
    cap: float
    lower: float
    receipt: float | None = None


@dataclass(frozen=True)
class Explanations:
    """What the store knew at close(S-1), gathered by the caller.

    `symbols`: `security_id` to broker symbol, for every name the ledger or the
    broker may hold. `ended`: names whose listing ended by S. `spinoffs`: child
    `security_id` to (parent `security_id`, shares per parent share),
    `ex_date <= S`, not yet journaled. `dividends`: cash due per name for the
    dividends paid by S, not yet journaled. `reference_prices`: close(S-1) per
    `security_id`. `activities`: the broker's non-trade cash activities this
    reconciliation read (the caller reads them after `account()`; empty in the
    read-only `explanations_as_of` path, #1583 T183). `seen_activity_ids` and
    `activity_seen_before`: the caller-applied seen-sets and the fallback cutoff
    that decide whether a `FEE`'s id is already inside the base."""

    symbols: Mapping[str, str]
    ended: frozenset[str] = frozenset()
    spinoffs: Mapping[str, tuple[str, float]] = field(default_factory=dict)
    dividends: Mapping[str, float] = field(default_factory=dict)
    #: The ticker changes the symbol map applied this reconciliation (#1761):
    #: `security_id` to `(old symbol, new symbol)`, from stored `symbol_changes`
    #: rows known at close(S-1). `compare` records them on the row's
    #: `symbol_changes` key; they move no position, quantity or cash.
    symbol_changes: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    #: Held names with an equal-amount dividend group the reviewed list does
    #: not explain, each with its detail (#1625): never summed into
    #: `dividends`, each an `unexplained_dividend_group` mismatch.
    dividend_findings: Mapping[str, str] = field(default_factory=dict)
    reference_prices: Mapping[str, float] = field(default_factory=dict)
    #: Held `security_id`s with a stock dividend (#1627) stored as a `split` action
    #: (its ratio in `(1, alpaca.stock_dividend_max_rate]`), each with
    #: `(first_date, last_date, ratio)` and `ex_date <= S`: the ex-date, the last
    #: session a `CIL` for it may post (`first_date` +
    #: `alpaca.cash_in_lieu_max_lag_sessions`, the payable-date window), and the
    #: ratio that values the fraction at the post-ex price. The broker credits
    #: whole shares plus a `CIL` cash-in-lieu activity for the fractional
    #: remainder, so `compare` books the `cash_in_lieu` adjustment against it.
    stock_dividends: Mapping[str, tuple[date, date, float]] = field(default_factory=dict)
    #: The pre-ex reference for each name in `stock_dividends` (#1627): the raw
    #: close of its latest bar on or before `previous_session(ex_date)`, which is
    #: the pre-split price the broker's cash in lieu fractionalizes. It is read
    #: explicitly for the ex-date, never the close before the reconciliation
    #: session, so the valuation is the same whatever the reconciliation date
    #: (a `CIL` posted on the payable date divides the same reference by the
    #: ratio). A name with no bar here has no reference and its `CIL` halts.
    stock_dividend_references: Mapping[str, float] = field(default_factory=dict)
    activities: tuple[CashActivity, ...] = ()
    seen_activity_ids: frozenset[str] = frozenset()
    activity_seen_before: date | None = None
    #: The open mergers (#1762 item 1, T249): acquiree `security_id` to the one
    #: open merger the caller chose for it. Never an id of a conflicting pair.
    mergers: Mapping[str, OpenMerger] = field(default_factory=dict)
    #: The conflicting merger pairs raised at this reconciliation: acquiree
    #: `security_id` to the detail naming both ids (#1762 item 1, T249).
    merger_conflicts: Mapping[str, str] = field(default_factory=dict)


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
    `cash` deltas, as `execution.ledger` applies them.

    `security_id` and `quantity` are None for a cash-only row (a `dividend_cash`,
    a `corporate_action_cash` proceeds share, a booked `fee_cash`). A booked
    `cash_in_lieu` (#1627) carries the stock-dividend `security_id`, the fractional
    `quantity` it removes (negative), its `cash` and a `broker_activity_id`.
    `broker_activity_id` is the broker cash-activity id a booked `fee_cash` (or a
    `cash_in_lieu`) is keyed by (null for every other kind); the store's
    `adjustments.broker_activity_id` is `UNIQUE`, so a broker fact is booked once
    by id (#1583, T183)."""

    kind: str
    security_id: str | None
    quantity: float | None
    cash: float | None
    explanation: str
    broker_activity_id: str | None = None
    #: The merger id a merger's `corporate_action_cash` row names (#1762, T249);
    #: None for every other row, and then left out of `mismatches_json`.
    merger_id: str | None = None


def _adjustment_json(adjustment: ProposedAdjustment) -> dict[str, object]:
    """An adjustment's `mismatches_json` entry: its fields, `merger_id` only when set."""
    entry = dict(vars(adjustment))
    if entry.get("merger_id") is None:
        entry.pop("merger_id", None)
    return entry


@dataclass(frozen=True)
class Allowed:
    """A difference an allowance or a deferred explanation absorbed."""

    reason: str
    difference: float
    security_id: str | None = None


@dataclass(frozen=True)
class ActivityRecord:
    """One non-trade cash activity a reconciliation read: its broker id, type
    and status, for the row's record and the base's seen-set the next
    reconciliation reads (`Reconciliation.activities`, #1583, T183)."""

    activity_id: str
    activity_type: str
    status: str


@dataclass(frozen=True)
class SymbolChangeRecord:
    """One held name's ticker change a reconciliation applied (#1761): the
    store's `symbol_changes` mapping, recorded on the `reconciliations` row. It
    moves no position, quantity or cash."""

    security_id: str
    old_symbol: str
    new_symbol: str


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
    activities: tuple[ActivityRecord, ...] = ()
    symbol_changes: tuple[SymbolChangeRecord, ...] = ()

    @property
    def mismatches_json(self) -> str:
        """The `reconciliations.mismatches_json` value, stable for equal results."""
        return json.dumps(
            {
                "mismatches": [vars(m) for m in self.mismatches],
                "lagging": list(self.lagging_ids),
                "pending": list(self.pending_ids),
                "explained": [_adjustment_json(a) for a in self.explained],
                "allowed": [vars(a) for a in self.allowed],
                "activities": [vars(a) for a in self.activities],
                "symbol_changes": [vars(s) for s in self.symbol_changes],
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


def _inside_base(activity: CashActivity, explanations: Explanations) -> bool:
    """Whether a cash activity's id is already inside the base (#1583, T183):
    in a caller-applied seen-set, or — only when both the base's recorded set
    and the window's recorded set are null — dated strictly before the window's
    New York start date. The caller applies the seen-sets and the fallback."""
    if activity.activity_id in explanations.seen_activity_ids:
        return True
    cut = explanations.activity_seen_before
    return cut is not None and activity.date < cut


def _activity_mismatch(activity: CashActivity, inside_base: bool) -> Mismatch | None:
    """A mismatch of kind `unexplained_activity` for a read this reconciliation
    cannot place (#1583, T183), else None: a `FEE` or dividend row whose status
    is not `executed`, or a cash activity (non-null `net_amount`) of a type the
    rule does not know (`INT`, `JNLC`, ...). A corporate action (a known type or
    a null `net_amount`) is never a mismatch by itself; an `executed` `FEE` or
    dividend row is left to booking / the store's dividend rule. An unknown cash
    type already **inside the base** (`inside_base`, #1641) is never a mismatch
    either, because its cash is already in the cash the ledger starts from (the
    account's own funding `JNLC`, an interest row, ...); a `FEE`/dividend whose
    `status` is not `executed` still halts even inside the base, since a booked
    or recorded fact the broker later reverses is a real discrepancy. The detail
    names the type, the id, the date, the amount and the status."""
    amount = "null" if activity.net_amount is None else activity.net_amount
    detail = (
        f"{activity.activity_type!r} activity {activity.activity_id!r} on {activity.date} "
        f"amount {amount} status {activity.status!r}"
    )
    kind = activity.activity_type
    if kind in (_FEE, _CASH_IN_LIEU) or kind in _DIVIDEND_TYPES:
        return None if activity.status == _EXECUTED else Mismatch("unexplained_activity", detail)
    if kind in _CORPORATE_ACTION_TYPES or activity.net_amount is None:
        return None
    if inside_base:
        return None
    return Mismatch("unexplained_activity", detail)


def _place_cash_in_lieu(
    activity: CashActivity,
    explanations: Explanations,
    expected: dict[str, float],
    broker: dict[str, float],
    security_of: Mapping[str, str],
    q_tol: float,
    tolerance: float,
) -> tuple[Mismatch | None, ProposedAdjustment | None]:
    """Place a `CIL` cash-in-lieu activity for a held stock dividend (#1627): the
    book must hold the stock-dividend issuer, the broker must credit the whole
    shares (floor of the ledger's fractional expectation) and the CIL's
    `net_amount` must match the expected cash within `tolerance`
    (`risk.cash_in_lieu_tolerance`, relative); then the fractional remainder is
    removed as a `cash_in_lieu` adjustment (`quantity` the removed fraction,
    `cash` the broker's amount, keyed by its id). The remainder is valued at the
    post-ex price: the pre-ex reference (`Explanations.stock_dividend_references`,
    the close of the latest bar on or before `previous_session(ex_date)`, per old
    share) divided by the split's ratio, once, whatever the reconciliation date
    (fix #1627-6a). The CIL may post from the ex-date through
    `alpaca.cash_in_lieu_max_lag_sessions` sessions later (`dividend[1]`), since
    Alpaca pays cash in lieu on the payable date. An unmatched CIL (a non-executed
    one is already classified by `_activity_mismatch`, so this never sees it), a
    CIL outside the lag window or on a name with no stock dividend, a CIL whose
    issuer has no reference price, or one whose amount is off beyond the tolerance
    is `unexplained_activity` (#1583, T183)."""
    if activity.status != _EXECUTED or activity.net_amount is None:
        # The read loop's `_activity_mismatch` already classifies a non-executed
        # CIL (and never a mismatch for a null one); a second mismatch here would
        # double-count it.
        return None, None
    security_id = security_of.get(activity.symbol) if activity.symbol is not None else None
    dividend = explanations.stock_dividends.get(security_id) if security_id is not None else None
    if security_id is None or dividend is None or not (dividend[0] <= activity.date <= dividend[1]):
        detail = (
            f"{activity.activity_type!r} activity {activity.activity_id!r} on {activity.date} "
            f"amount {activity.net_amount} status {activity.status!r}; no stock dividend "
            f"this session"
        )
        return Mismatch("unexplained_activity", detail), None
    reference = explanations.stock_dividend_references.get(security_id)
    if reference is None:
        return Mismatch(
            "unexplained_activity",
            f"CIL activity {activity.activity_id!r} on {activity.date} has no reference "
            f"price for {security_id}",
        ), None
    ledger_frac = expected.get(security_id, 0.0)
    broker_shares = broker.get(security_id, float("nan"))
    whole = float(math.floor(ledger_frac))
    removed = ledger_frac - broker_shares
    # The broker credited the whole shares (floor of the ledger's split-applied
    # expectation) and paid cash for the sub-share remainder.
    if not (q_tol < removed < 1 + q_tol and abs(broker_shares - whole) <= q_tol):
        return Mismatch(
            "unexplained_activity",
            f"CIL activity {activity.activity_id!r} on {activity.date} is not a whole-share "
            f"credit (ledger {ledger_frac} fractional, broker {broker_shares})",
        ), None
    expected[security_id] = broker_shares
    expected_cash = removed * (reference / dividend[2])
    net = activity.net_amount
    if not (abs(net - expected_cash) <= tolerance * expected_cash):
        expected[security_id] = ledger_frac
        return Mismatch(
            "unexplained_activity",
            f"CIL activity {activity.activity_id!r} on {activity.date} amount {net} is off "
            f"the expected cash {expected_cash} of {removed} fractional share(s) at "
            f"{reference} / {dividend[2]}",
        ), None
    proposal = ProposedAdjustment(
        "cash_in_lieu",
        security_id,
        -removed,
        net,
        f"CIL: cash in lieu of {removed} fractional share(s)",
        broker_activity_id=activity.activity_id,
    )
    return None, proposal


def _cash_allows(band: _Band, difference: Decimal, bound: Decimal) -> bool:
    """Whether `difference` (in `Decimal`) is within `band` widened by the cash
    bound `bound` (inclusive; #1583, T183). Kept in `Decimal` so a cent at a
    six-figure cash balance compares exactly, not by float residue."""
    return Decimal(str(band.low)) - bound <= difference <= Decimal(str(band.high)) + bound


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
    cash_in_lieu_tolerance = frozen.cash_in_lieu_tolerance
    buffer = frozen.whole_share_price_buffer
    cash_bound = _cash_bound(cash_tol, ledger.fills_since_base)
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
        lag = _lag(item, q_tol, buffer, cash_tol)
        if isinstance(lag, Mismatch):
            mismatches.append(lag)
        elif lag is not None:
            lagging_ids.append(order.client_order_id)
            shares, notional = lag
            bands[order.security_id].widen(sign * shares)
            cash_band.widen(-sign * notional)

    mismatches += _order_mismatches(open_orders, lagging, order_id_prefix)

    expected = dict(ledger.positions)
    # A short is signed negative here, so the broker's negative quantity compares
    # against it directly and the lag bands (a buy widens up, a sell down) read
    # a cover or an open the right way round (T233).
    shorts = frozenset(name for name, held in ledger.short_holdings.items() if held > q_tol)
    for name in shorts:
        if name in expected and abs(expected[name]) > q_tol:
            raise ValueError(f"{name} is held long and short in one ledger")
        expected[name] = -_finite(ledger.short_holdings[name], f"short quantity of {name}")
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

    # --- booked `FEE` cash activities, before every other explanation (#1583) ---
    activities = explanations.activities
    activity_records = tuple(
        ActivityRecord(a.activity_id, a.activity_type, a.status) for a in activities
    )
    fee_proposals: list[ProposedAdjustment] = []
    # `cash` is reported in cents and a broker fee is sub-cent, so the broker and
    # ledger balances are six-figure: the difference is taken in `Decimal`, from
    # each balance's shortest form, so a whole-cent fee never leaves float residue.
    difference_decimal = Decimal(str(account.cash)) - Decimal(str(ledger.cash))
    read_ids: set[str] = set()
    for activity in activities:
        if activity.activity_id in read_ids:
            # A broker fact is keyed by its id (spec item 2): an id listed twice
            # in one read is a broker fault, never two facts to book.
            raise ValueError(
                f"cash activity id {activity.activity_id!r} is listed twice in one read"
            )
        read_ids.add(activity.activity_id)
        # `_inside_base` before `_activity_mismatch` (#1641): an activity already
        # in the ledger's base is already in the cash the ledger starts from, so an
        # unknown cash type inside the base is never `unexplained_activity` (and a
        # base `FEE` is never booked again). Every activity is still classified — a
        # non-executed `FEE`/dividend inside the base still halts — and only an
        # activity outside the base is booked.
        inside_base = _inside_base(activity, explanations)
        unexplained = _activity_mismatch(activity, inside_base)
        if unexplained is not None:
            mismatches.append(unexplained)
            continue
        if activity.activity_type == _FEE and activity.net_amount is not None and not inside_base:
            described = activity.description or ""
            fee_proposals.append(
                ProposedAdjustment(
                    "fee_cash",
                    None,
                    None,
                    activity.net_amount,
                    (
                        f"{activity.activity_type}: {described}"
                        if described
                        else activity.activity_type
                    ),
                    broker_activity_id=activity.activity_id,
                )
            )
    for fee in fee_proposals:
        difference_decimal -= Decimal(str(fee.cash))
    explained.extend(fee_proposals)

    # --- booked `CIL` cash-in-lieu activities for a held stock dividend (#1627) ---
    cil_proposals: list[ProposedAdjustment] = []
    # A `CIL` on an open merger's acquiree or acquirer symbol is placed against
    # the merger (#1762 item 4): it stays in the cash difference the merger reads.
    merger_symbols = {
        explanations.symbols[name]
        for acquiree, merger in explanations.mergers.items()
        for name in (acquiree, merger.acquirer)
        if name is not None and name in explanations.symbols
    }
    for activity in activities:
        if activity.activity_type != _CASH_IN_LIEU or _inside_base(activity, explanations):
            continue
        if activity.symbol is not None and activity.symbol in merger_symbols:
            continue
        mismatch, proposal = _place_cash_in_lieu(
            activity, explanations, expected, broker, security_of, q_tol, cash_in_lieu_tolerance
        )
        if mismatch is not None:
            mismatches.append(mismatch)
        if proposal is not None:
            cil_proposals.append(proposal)
    for cil in cil_proposals:
        difference_decimal -= Decimal(str(cil.cash))
    explained.extend(cil_proposals)

    dues, invalid_receipts = _merger_dues(
        explanations.mergers,
        expected,
        broker,
        shorts,
        q_tol,
        explanations.reference_prices,
        buffer,
    )
    for name, acquirer, detail in invalid_receipts:
        mismatches.append(Mismatch("quantity", detail, name, explanations.symbols.get(name)))
        expected.pop(name, None)
        if acquirer is not None:
            expected[acquirer] = broker.get(acquirer, 0.0)
    merger_removed = {due.name for due in dues if due.held is not None}
    # The merger rule applies to its acquiree instead of the ended-listing band.
    removed = [
        name
        for name in sorted(explanations.ended)
        if expected.get(name, 0.0) > q_tol and name not in broker and name not in merger_removed
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

    dividends = {
        name: _finite(amount, f"dividend due on {name}")
        for name, amount in sorted(explanations.dividends.items())
    }
    # A short's due is negative (-quantity x amount; item 10) and its cash band is
    # [due, 0]; a long's is [0, due]. A negative due on any other name, or a
    # positive one on a short, is the caller's fault.
    negative = sorted(
        name for name, amount in dividends.items() if amount < 0 and name not in shorts
    )
    if negative:
        raise ValueError(f"dividend due is negative for {negative}")
    positive_short = sorted(
        name for name, amount in dividends.items() if amount > 0 and name in shorts
    )
    if positive_short:
        raise ValueError(f"dividend due is positive for the short {positive_short}")
    credited = math.fsum(dividends.values())
    if deferring and dividends:
        # A credit (or a short's debit) while an allowance is open is allowed,
        # never journaled (#399).
        for amount in dividends.values():
            cash_band.widen(amount)
        allowed += [
            Allowed("deferred dividend", amount, name) for name, amount in dividends.items()
        ]
    merger_cap = math.fsum(due.cap for due in dues)
    if deferring and dues:
        # A merger's removal and credit while an allowance is open are allowed,
        # never journaled; the next reconciliation without one journals them.
        cash_band.widen(merger_cap)
        allowed += [
            Allowed("deferred merger removal", -due.held, due.name)
            for due in dues
            if due.held is not None
        ]
        for merger_due in dues:
            if merger_due.receipt is not None and merger_due.merger.acquirer is not None:
                bands[merger_due.merger.acquirer].widen(merger_due.receipt)
                allowed.append(
                    Allowed(
                        "deferred merger receipt",
                        merger_due.receipt,
                        merger_due.merger.acquirer,
                    )
                )
    cash_ok = _cash_allows(cash_band, difference_decimal, cash_bound)
    if dues and not deferring:
        cash_ok, journaled = _merger_cash(
            dues,
            merger_cap,
            difference_decimal,
            cash_bound,
            dividends,
            shorts,
            removed,
            expected,
            explanations.reference_prices,
            buffer,
        )
        explained += journaled
        mismatches += _merger_final_checks(dues, journaled, cash_bound, explanations.symbols)
        for receipt in (a for a in journaled if a.kind == "merger_receipt"):
            if receipt.security_id is not None and receipt.quantity is not None:
                expected[receipt.security_id] = (
                    expected.get(receipt.security_id, 0.0) + receipt.quantity
                )
        for name in removed:
            expected.pop(name)
    elif removed:
        value = [expected[name] * explanations.reference_prices.get(name, 0.0) for name in removed]
        cap = math.fsum(value) * (1 + buffer)
        cap_decimal = Decimal(str(cap))
        if deferring:
            cash_band.widen(cap)
            cash_ok = _cash_allows(cash_band, difference_decimal, cash_bound)
        else:
            # The proceeds take the cash first: paper is not expected to credit a
            # dividend (spec req 6), and every dividend due is offered, paid or
            # not. Only cash beyond the proceeds' cap is read as the dividends
            # credited beside them (#400).
            credited_decimal = Decimal(str(credited))
            cash_ok = False
            for with_dividends in (False, True) if dividends else (False,):
                proceeds = difference_decimal - (credited_decimal if with_dividends else Decimal(0))
                if -cash_bound <= proceeds <= cap_decimal + cash_bound:
                    cash_ok = True
                    proceeds_f = float(min(max(proceeds, Decimal(0)), cap_decimal))
                    explained += _ended_adjustments(removed, expected, value, proceeds_f)
                    if with_dividends:
                        explained += _dividend_adjustments(dividends)
                    break
        for name in removed:
            expected.pop(name)
    elif not cash_ok and dividends and not deferring:
        # Everything paid, then the longs' credits alone, then the shorts' debits
        # alone; the first that the difference matches is journaled.
        subsets = (
            dividends,
            {n: a for n, a in dividends.items() if n not in shorts},
            {n: a for n, a in dividends.items() if n in shorts},
        )
        for subset in subsets:
            if subset and abs(difference_decimal - Decimal(str(math.fsum(subset.values())))) <= (
                cash_bound
            ):
                cash_ok = True
                explained += _dividend_adjustments(subset)
                break
    for name in sorted(merger_removed):
        expected.pop(name)
    if not cash_ok:
        mismatches.append(Mismatch("cash", f"broker {account.cash}, ledger {ledger.cash}"))
    elif abs(difference_decimal) > cash_bound and deferring:
        allowed.append(Allowed("cash within the open allowances", float(difference_decimal)))

    if deferring:
        for adjustment in [a for a in explained if a.kind in ("spinoff_receipt", "merger_receipt")]:
            allowed.append(
                Allowed(
                    "deferred spin-off receipt", adjustment.quantity or 0.0, adjustment.security_id
                )
            )
        allowed += [
            Allowed("deferred ended listing", -removed_quantity[name], name) for name in removed
        ]
        # A booked `FEE` beside a lagging or pending order is `deferred fee`: its
        # cash is still subtracted from the difference, but it is never journaled
        # here; the next `ok` reconciliation reads the same activity id, finds it
        # not inside its base, and books it once (#1583, T183).
        allowed += [
            Allowed("deferred fee", fee.cash, None) for fee in fee_proposals if fee.cash is not None
        ]
        # A booked `CIL` cash-in-lieu beside a lagging or pending order is likewise
        # recorded here (and never journaled here): its cash is still subtracted
        # from the difference and the position rounded to whole shares, but the id
        # is not journaled, so the next `ok` reconciliation reads it, finds it not
        # inside its base, and books it once (#1627).
        allowed += [
            Allowed("deferred cash in lieu", cil.cash, cil.security_id)
            for cil in cil_proposals
            if cil.cash is not None
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
            short=security_id in shorts,
        )
        if found:
            mismatches.append(found)
        elif band.open:
            gap = broker.get(security_id, 0.0) - expected.get(security_id, 0.0)
            if abs(gap) > q_tol:
                allowed.append(Allowed("quantity within the open allowances", gap, security_id))

    for security_id, detail in sorted(explanations.merger_conflicts.items()):
        mismatches.append(
            Mismatch(
                kind=MERGER_CONFLICT,
                detail=detail,
                security_id=security_id,
                symbol=explanations.symbols.get(security_id),
            )
        )

    for security_id, detail in sorted(explanations.dividend_findings.items()):
        mismatches.append(
            Mismatch(
                kind=UNEXPLAINED_DIVIDEND_GROUP,
                detail=detail,
                security_id=security_id,
                symbol=explanations.symbols.get(security_id),
            )
        )

    if mismatches:
        status = MISMATCH
    elif pending_ids:
        status = PENDING_UNRESOLVED
    elif lagging_ids:
        status = FILLS_LAGGING
    else:
        status = OK
    # The ticker changes the symbol map applied (#1761): recorded on the row,
    # never a difference, so they are listed whatever the status.
    symbol_changes = tuple(
        SymbolChangeRecord(name, old, new)
        for name, (old, new) in sorted(explanations.symbol_changes.items())
    )
    return Reconciliation(
        status=status,
        broker_cash=account.cash if status == OK else None,
        mismatches=tuple(mismatches),
        lagging_ids=tuple(sorted(lagging_ids)),
        pending_ids=tuple(sorted(pending_ids)),
        adjustments=tuple(explained) if status == OK else (),
        explained=tuple(explained),
        allowed=tuple(allowed),
        activities=activity_records,
        symbol_changes=symbol_changes,
    )


def _merger_dues(
    mergers: Mapping[str, OpenMerger],
    expected: Mapping[str, float],
    broker: Mapping[str, float],
    shorts: frozenset[str],
    q_tol: float,
    reference_prices: Mapping[str, float],
    buffer: float,
) -> tuple[list[_MergerDue], list[tuple[str, str | None, str]]]:
    """Cash bounds and receipts for open long mergers (#1762 items 2, 4, 5)."""
    dues: list[_MergerDue] = []
    invalid: list[tuple[str, str | None, str]] = []
    for name, merger in sorted(mergers.items()):
        if name in shorts:
            continue
        rate = _finite(merger.cash_per_share or 0.0, f"cash_per_share of merger {merger.merger_id}")
        stock = merger.kind != MERGER_CASH
        if merger.removed_quantity is None:
            held = expected.get(name, 0.0)
            if held <= q_tol or name in broker:
                continue
            receipt: float | None = None
            upper = held * rate
            if stock:
                acquirer = merger.acquirer
                ratio = merger.share_ratio
                if acquirer is None or ratio is None:
                    invalid.append(
                        (name, acquirer, f"merger {merger.merger_id} has no acquirer or ratio")
                    )
                    continue
                receipt = broker.get(acquirer, 0.0) - expected.get(acquirer, 0.0)
                due_shares = held * _finite(ratio, f"share ratio of merger {merger.merger_id}")
                if not (q_tol < receipt <= due_shares + q_tol and receipt > due_shares - 1 - q_tol):
                    invalid.append(
                        (
                            name,
                            acquirer,
                            f"merger {merger.merger_id} receipt {receipt} outside "
                            f"({due_shares - 1}, {due_shares}]",
                        )
                    )
                    continue
                fractional = max(due_shares - receipt, 0.0)
                price = reference_prices.get(acquirer)
                if fractional > q_tol and price is None:
                    invalid.append(
                        (
                            name,
                            acquirer,
                            f"merger {merger.merger_id} has no acquirer reference price",
                        )
                    )
                    continue
                upper += fractional * (price or 0.0) * (1 + buffer)
            dues.append(_MergerDue(name, merger, held, upper, upper, held * rate, receipt))
        else:
            removed = _finite(merger.removed_quantity, f"removed quantity of {merger.merger_id}")
            journaled = _finite(merger.journaled_cash, f"journaled cash of {merger.merger_id}")
            lower = removed * rate
            upper = merger.cash_upper if merger.cash_upper is not None else lower
            dues.append(_MergerDue(name, merger, None, upper, max(upper - journaled, 0.0), lower))
    return dues, invalid


def _merger_cash(
    dues: Sequence[_MergerDue],
    merger_cap: float,
    difference: Decimal,
    bound: Decimal,
    dividends: Mapping[str, float],
    shorts: frozenset[str],
    removed: Sequence[str],
    expected: Mapping[str, float],
    reference_prices: Mapping[str, float],
    buffer: float,
) -> tuple[bool, list[ProposedAdjustment]]:
    """Whether the cash difference is explained with open mergers present, and
    the rows it journals (#1762 items 2 and 4). The mergers read the cash left
    after the reconciliation's other explanations, so the exact readings come
    first: the mergers paid in full, then the mergers paid nothing, and only
    then a part paid (the difference left, up to their caps, unless it is within
    the cash bound). In each reading the dividends are offered as `compare`
    offers them (none paid first, then all, the longs', the shorts'); an ended
    name's proceeds take the rest, in [0, its cap], and with no ended name the
    rest must be within the cash bound. So a credited dividend or an ended
    name's proceeds are never booked as a merger's part payment when they
    explain the cash alone. With an ended name beside a merger that may take
    cash, only a split where exactly one of "paid in full" and "paid nothing"
    fits is journaled; any other split is ambiguous and fails closed on `cash`.
    A removal is journaled whatever it takes; a
    cash-only row only when it takes cash. Two mergers taking cash at once share
    it in acquiree order, each up to its cap: their sum is right, the split
    between the ids may not be."""
    subsets = (
        dividends,
        {n: a for n, a in dividends.items() if n not in shorts},
        {n: a for n, a in dividends.items() if n in shorts},
    )
    options: list[Mapping[str, float]] = [{}, *(subset for subset in subsets if subset)]
    value = [expected[name] * reference_prices.get(name, 0.0) for name in removed]
    band_cap = Decimal(str(math.fsum(value) * (1 + buffer)))
    cap = Decimal(str(merger_cap))
    top = band_cap if removed else Decimal(0)

    def reading(mode: str) -> list[ProposedAdjustment] | None:
        """The rows of the first dividend option that fits `mode`, else None."""
        for chosen in options:
            rest = difference - Decimal(str(math.fsum(chosen.values())))
            if mode == "full":
                # Paid in full: the cash received when it is within the bound of
                # the due (never more than the due), else the due itself.
                take = min(max(rest, Decimal(0)), cap) if abs(rest - cap) <= bound else cap
            elif mode == "none":
                take = Decimal(0)
            else:
                take = min(max(rest, Decimal(0)), cap) if rest > bound else Decimal(0)
            left = rest - take
            if not -bound <= left <= top + bound:
                continue
            rows = _merger_adjustments(dues, float(take))
            if removed:
                proceeds = float(min(max(left, Decimal(0)), band_cap))
                rows += _ended_adjustments(removed, expected, value, proceeds)
            if chosen:
                rows += _dividend_adjustments(chosen)
            return rows
        return None

    if removed and cap > 0:
        # An ended name beside a merger that may take cash: only an unambiguous
        # split is journaled (the mergers paid in full or paid nothing, exactly
        # one of the two fitting); anything else fails closed on `cash`.
        full, none = reading("full"), reading("none")
        if (full is None) == (none is None):
            return False, []
        return True, full if full is not None else none or []
    for mode in ("full", "none", "part"):
        rows = reading(mode)
        if rows is not None:
            return True, rows
    return False, []


def _merger_adjustments(dues: Sequence[_MergerDue], take: float) -> list[ProposedAdjustment]:
    """The rows the mergers journal: `take` shared in order, each up to its cap;
    a removal always (its quantity, whatever cash it took), a payment only when
    it took cash."""
    rows: list[ProposedAdjustment] = []
    left = take
    for due in dues:
        cash = min(left, due.cap)
        left -= cash
        merger = due.merger
        if due.held is not None:
            rows.append(
                ProposedAdjustment(
                    "corporate_action_cash",
                    due.name,
                    -due.held,
                    cash,
                    f"merger {merger.merger_id}: {due.held} acquiree shares removed",
                    merger_id=merger.merger_id,
                )
            )
            if due.receipt is not None and merger.acquirer is not None:
                rows.append(
                    ProposedAdjustment(
                        "merger_receipt",
                        merger.acquirer,
                        due.receipt,
                        None,
                        f"merger {merger.merger_id}: acquirer shares received",
                        merger_id=merger.merger_id,
                    )
                )
        elif cash > 0:
            rows.append(
                ProposedAdjustment(
                    "corporate_action_cash",
                    due.name,
                    None,
                    cash,
                    f"cash merger {merger.merger_id}: cash paid after the removal",
                    merger_id=merger.merger_id,
                )
            )
    return rows


def merger_due_detail(merger_id: str) -> str:
    """The prefix of the `cash` mismatch detail that names a merger whose due was
    not met by the end of its window (#1762 item 4); the caller reads it back so
    the halt happens once."""
    return f"merger {merger_id!r} due not met"


def _merger_final_checks(
    dues: Sequence[_MergerDue],
    journaled: Sequence[ProposedAdjustment],
    bound: Decimal,
    symbols: Mapping[str, str],
) -> list[Mismatch]:
    """At the first reconciliation after a merger's window (`OpenMerger.final`),
    a journaled total below the due less the cash bound is a `cash` mismatch
    naming the merger (#1762 item 4): cash never paid on a long."""
    taken: dict[str, float] = defaultdict(float)
    for row in journaled:
        if row.merger_id is not None and row.cash is not None:
            taken[row.merger_id] += row.cash
    found: list[Mismatch] = []
    for due in dues:
        merger = due.merger
        if not merger.final:
            continue
        total = merger.journaled_cash + taken.get(merger.merger_id, 0.0)
        if Decimal(str(total)) < Decimal(str(due.lower)) - bound:
            found.append(
                Mismatch(
                    "cash",
                    f"{merger_due_detail(merger.merger_id)}: {total} of {due.lower} "
                    f"journaled by the end of its window on {merger.window_end}",
                    due.name,
                    symbols.get(due.name),
                )
            )
    return found


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


def _lag(
    item: JournalOpenOrder, tolerance: float, buffer: float, cash_tolerance: float
) -> tuple[float, float] | Mismatch | None:
    """(unjournaled shares, unjournaled notional) of a lagging order, None when
    it is not lagging, or a `bad_reading` mismatch when the reading cannot
    bound the lag: more shares than a quantity order asked for, or more
    filled value than a notional order's notional x (1 + `buffer`), beyond the
    cash tolerance (#398)."""
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
        or (
            order.notional is not None
            and filled * price > abs(order.notional) * (1 + buffer) + cash_tolerance
        )
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
        elif (shape := order_shape_violation(order)) is not None:
            kind, detail = "open_order_differs", f"{shape.rule}: {shape.detail}"
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
        if reading is not None and (shape := order_shape_violation(reading)) is not None:
            found.append(
                Mismatch(
                    "order_differs",
                    f"{shape.rule}: {shape.detail}",
                    security_id=item.order.security_id,
                    client_order_id=coid,
                )
            )
    return found


def _dividend_adjustments(dividends: Mapping[str, float]) -> list[ProposedAdjustment]:
    """One `dividend_cash` row per name with the dividend credited."""
    return [
        ProposedAdjustment("dividend_cash", name, None, amount, "credited dividend")
        for name, amount in dividends.items()
    ]


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
    *,
    short: bool = False,
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
    if short:
        # Any short change our own fills do not explain: a buy-in, a recall, an
        # external add, or the wrong sign (paper spec items 9 and 10).
        kind = "short_external_change"
    elif broker is None:
        kind = "ledger_only_position"
    elif not held:
        kind = "broker_only_position"
    else:
        kind = "quantity"
    return Mismatch(kind, f"broker {broker}, ledger {expected}", security_id, symbol)
