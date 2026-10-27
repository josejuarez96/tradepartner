"""Reconciliation breaks as pure functions (paper spec amendment 2026-10-26,
#2266, items 2, 4, 5 and 14; ADR 0025; plan T280b).

A **break** is a name-level difference the explainers leave: it restricts one
name and waits for the owner while the rest of the book trades. This module
holds no I/O and reads no clock: every function is a pure function of its
arguments, so the same reconciliation inputs give the same breaks (item 13).
The callers (T281 on) read the broker, the journal and the store and journal
the events (`store.journal.ReconcileBreakRow`).

- **Kinds** (items 2 and 3): `NAME_KINDS` become breaks; every other kind,
  an unknown one included, is book-level and halts as before (fail closed).
  `FINDING_KINDS` are the three a true-up acknowledges rather than adopts.
- **Identity** (item 4): `identity` gives the break's name (its `security_id`,
  else the broker symbol) and id `<window_id>:<name>:<opening reconciliation_id>`.
- **Parts** (item 2): `BreakPart` is one part of a break (quantity, cash or a
  finding); `parts_json` writes them in one canonical order.
- **Cause** (item 14): `part_cause` is computed once at detection from the
  store rows known at close(S-1) and the broker activities on the name, each
  dated inside `risk.break_cause_lookback_sessions` XNYS sessions up to S, and
  set only by evidence that can produce the kind. Own trading on the name
  forces `unattributed`; `break_cause` folds the parts, `unattributed` first.
- **Size and materiality** (items 4 and 5): `dollar_size`, `is_material`
  (both thresholds, cumulative per name) and `over_cap` (the open break cap).
- **State** (item 4): derived from each break's latest event in write order.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from tradepartner.adapters.broker import CashActivity
from tradepartner.calendar import is_session, previous_session, session_close
from tradepartner.config import RiskConfig
from tradepartner.execution.reconcile import (
    _CASH_IN_LIEU,
    _DELISTING_CASH_TYPES,
    _DIVIDEND_TYPES,
    MERGER_CONFLICT,
    UNEXPLAINED_DIVIDEND_GROUP,
)
from tradepartner.store.journal import ReconcileBreakRow, break_id_for
from tradepartner.store.schema import BREAK_EVENTS, OPEN_BREAK_EVENTS

#: The kind of item 1's name cash break: a qualifying broker activity taken out
#: of the cash difference and put on its name.
NAME_CASH = "name_cash"
UNKNOWN_SYMBOL = "unknown_symbol"
SHORT_EXTERNAL_CHANGE = "short_external_change"
#: Item 2: the kinds that become a break on one name.
NAME_KINDS: frozenset[str] = frozenset(
    {
        "quantity",
        "ledger_only_position",
        "broker_only_position",
        "unknown_security",
        SHORT_EXTERNAL_CHANGE,
        UNKNOWN_SYMBOL,
        MERGER_CONFLICT,
        UNEXPLAINED_DIVIDEND_GROUP,
        NAME_CASH,
    }
)
#: Item 4: the parts keyed by a `finding_key`, acknowledged and never adopted.
FINDING_KINDS: frozenset[str] = frozenset(
    {UNKNOWN_SYMBOL, MERGER_CONFLICT, UNEXPLAINED_DIVIDEND_GROUP}
)
#: Item 5: kinds that make their break material whatever its size.
_ALWAYS_MATERIAL: frozenset[str] = FINDING_KINDS | {SHORT_EXTERNAL_CHANGE}

StoreKind = Literal["split", "spinoff", "dividend", "merger", "listing_end", "symbol_change"]
#: Item 14's table, one row per kind: the store rows and the broker activity
#: types that can produce it. A name cash break's row is chosen by its activity
#: type (`_cash_fits`), and no broker activity is its cause: it is the break.
_POSITION_ADDED = (
    frozenset({"spinoff", "symbol_change"}),
    frozenset({"SSO", "SSP", "NC", "SC", "REORG"}),
)
_FITS: Mapping[str, tuple[frozenset[str], frozenset[str]]] = {
    "quantity": (frozenset({"split"}), frozenset({"SSP", "SSO", "REORG"})),
    "ledger_only_position": (
        frozenset({"listing_end", "merger"}),
        frozenset({"MA", "REORG", "VOF", "NC", "SC"}),
    ),
    "broker_only_position": _POSITION_ADDED,
    UNKNOWN_SYMBOL: _POSITION_ADDED,
    "unknown_security": (frozenset({"listing_end", "symbol_change"}), frozenset({"NC", "SC"})),
    SHORT_EXTERNAL_CHANGE: (frozenset({"merger", "listing_end"}), frozenset()),
}
#: Item 14: `unattributed` first, so a break counts as unattributed if any part is.
_CAUSE_ORDER = ("unattributed", "data", "broker_activity", "corporate_action")
_BPS = Decimal(10_000)


@dataclass(frozen=True)
class StoreEvidence:
    """One store row that may explain a break on its name (item 14): its type,
    the date it takes effect (`ex_date`, `effective_date`, the listing's end or
    the change's date) and its `known_at`. The caller passes the rows of the
    name; for `broker_only_position` and `unknown_symbol`, the spin-offs of
    held parents and the symbol changes of held names."""

    kind: StoreKind
    on: date
    known_at: datetime

    def __post_init__(self) -> None:
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise ValueError(f"StoreEvidence.known_at must be tz-aware, got {self.known_at!r}")


@dataclass(frozen=True)
class BreakPart:
    """One part of a break (item 2): a quantity part, a cash part (its activity
    ids) or a finding part (its `finding_key`), with its own cause."""

    kind: str
    cause: str
    ledger_quantity: float | None = None
    broker_quantity: float | None = None
    cash_amount: float | None = None
    activity_ids: tuple[str, ...] = ()
    finding_key: str | None = None


def is_name_level(kind: str) -> bool:
    """Whether a mismatch kind is a name-level break (item 2); every other kind,
    an unknown one included, is book-level (item 3)."""
    return kind in NAME_KINDS


def identity(
    window_id: int, security_id: str | None, symbol: str | None, reconciliation_id: int
) -> tuple[str, str]:
    """The break's name and id (item 4): the name is `security_id`, else the
    broker `symbol`; the id is `<window_id>:<name>:<opening reconciliation_id>`."""
    name = security_id if security_id is not None else symbol
    if name is None:
        raise ValueError("a break names neither a security_id nor a symbol")
    return name, break_id_for(window_id, name, reconciliation_id)


def unknown_symbol_key(symbol: str, broker_quantity: float) -> str:
    """An `unknown_symbol` part's finding key: the symbol and broker quantity."""
    return json.dumps([UNKNOWN_SYMBOL, symbol, float(broker_quantity)])


def merger_conflict_key(merger_ids: Iterable[str]) -> str:
    """A `merger_conflict` part's finding key: the sorted merger ids."""
    return json.dumps([MERGER_CONFLICT, sorted(merger_ids)])


def dividend_group_key(security_id: str, ex_dates: Iterable[date]) -> str:
    """An `unexplained_dividend_group` part's key: the name and sorted ex-dates."""
    days = sorted(d.isoformat() for d in ex_dates)
    return json.dumps([UNEXPLAINED_DIVIDEND_GROUP, security_id, days])


def parts_json(parts: Iterable[BreakPart]) -> str:
    """`parts_json` (item 4): every part with sorted activity ids, the parts in
    one order whatever order they came in."""
    entries = [asdict(part) | {"activity_ids": sorted(part.activity_ids)} for part in parts]
    rendered = sorted(json.dumps(e, sort_keys=True) for e in entries)
    return "[" + ", ".join(rendered) + "]"


def _cash_fits(activity_type: str | None) -> frozenset[str]:
    """Item 14's store rows for a name cash break of `activity_type`."""
    if activity_type in _DIVIDEND_TYPES:
        return frozenset({"dividend"})
    if activity_type in _DELISTING_CASH_TYPES:
        return frozenset({"merger", "listing_end"})
    if activity_type == _CASH_IN_LIEU:
        return frozenset({"split", "spinoff"})
    return frozenset()


def _window(session: date, lookback_sessions: int) -> tuple[date, date] | None:
    """The first and last day of the `lookback_sessions` XNYS sessions up to and
    including `session`, or None for a lookback of 0."""
    if lookback_sessions <= 0:
        return None
    if not is_session(session):
        raise ValueError(f"{session.isoformat()} is not an XNYS session")
    first = session
    for _ in range(lookback_sessions - 1):
        first = previous_session(first)
    return first, session


def part_cause(
    kind: str,
    *,
    session: date,
    lookback_sessions: int,
    store_rows: Iterable[StoreEvidence],
    activities: Iterable[CashActivity],
    own_trading: bool,
    activity_type: str | None = None,
) -> str:
    """One part's cause at detection (item 14), never rewritten afterwards.

    `own_trading` is whether an own fill or own non-terminal order traded the
    name since the window's latest `ok` or `breaks` reconciliation: it forces
    `unattributed`, whatever the evidence. Otherwise `merger_conflict` and
    `unexplained_dividend_group` are `data`; a store row known at close(S-1)
    and dated in the window that fits the kind is `corporate_action`; a broker
    activity on the name dated in the window that fits it is
    `broker_activity`; anything else is `unattributed`. `activity_type` is a
    name cash break's activity type. Raises `ValueError` for a book-level kind."""
    if not is_name_level(kind):
        raise ValueError(f"{kind!r} is not a name-level break kind")
    if own_trading:
        return "unattributed"
    if kind in (MERGER_CONFLICT, UNEXPLAINED_DIVIDEND_GROUP):
        return "data"
    window = _window(session, lookback_sessions)
    if window is None:
        return "unattributed"
    first, last = window
    cutoff = session_close(previous_session(session))
    store_fits, broker_fits = (
        (_cash_fits(activity_type), frozenset()) if kind == NAME_CASH else _FITS[kind]
    )
    if any(
        row.kind in store_fits and first <= row.on <= last and row.known_at <= cutoff
        for row in store_rows
    ):
        return "corporate_action"
    if any(a.activity_type in broker_fits and first <= a.date <= last for a in activities):
        return "broker_activity"
    return "unattributed"


def break_cause(parts: Sequence[BreakPart]) -> str:
    """The `opened` event's cause over its parts: `unattributed` if any part is,
    then `data`, then `broker_activity`, then `corporate_action`."""
    if not parts:
        raise ValueError("a break with no parts has no cause")
    return min((p.cause for p in parts), key=_CAUSE_ORDER.index)


def _finite(value: float | None, what: str, *, nonneg: bool = False) -> Decimal:
    """`value` as an exact Decimal (None is 0); `ValueError` when it is not
    finite, or negative under `nonneg` (a size or a total is never negative)."""
    if value is None:
        return Decimal(0)
    if not math.isfinite(value) or (nonneg and value < 0):
        raise ValueError(f"{what} must be finite{' and >= 0' if nonneg else ''}, got {value!r}")
    return Decimal(repr(value))


def dollar_size(
    ledger_quantity: float | None,
    broker_quantity: float | None,
    reference_price: float | None,
    cash_amount: float | None,
) -> float | None:
    """|broker - ledger| x reference price + |cash| (item 4); a missing quantity
    is 0. A name with no usable reference price (None, non-finite or not above
    0) has a null size, which is material."""
    delta = abs(
        _finite(broker_quantity, "broker_quantity") - _finite(ledger_quantity, "ledger_quantity")
    )
    cash = abs(_finite(cash_amount, "cash_amount"))
    if reference_price is None or not math.isfinite(reference_price) or reference_price <= 0:
        return None
    return float(delta * Decimal(repr(reference_price)) + cash)


def is_material(
    kinds: Iterable[str],
    size: float | None,
    *,
    prior_auto_accepted: float,
    ledger_equity: float,
    risk: RiskConfig,
) -> bool:
    """Item 5: a break is immaterial when its size plus the sizes already
    auto-accepted on the name in the window is at or below both
    `risk.break_materiality_usd` and `risk.break_materiality_bps` of the ledger
    equity. Always material: an unpriced break, a `short_external_change` or
    finding part, and a non-finite or non-positive equity (fail closed)."""
    kinds = tuple(kinds)
    if not kinds or not all(is_name_level(kind) for kind in kinds):
        raise ValueError(f"materiality is judged for name-level kinds only, got {kinds!r}")
    if size is None or any(kind in _ALWAYS_MATERIAL for kind in kinds):
        return True
    if not math.isfinite(ledger_equity) or ledger_equity <= 0:
        return True
    by_share = Decimal(repr(risk.break_materiality_bps)) * Decimal(repr(ledger_equity)) / _BPS
    threshold = min(Decimal(repr(risk.break_materiality_usd)), by_share)
    total = _finite(prior_auto_accepted, "prior_auto_accepted", nonneg=True) + _finite(
        size, "size", nonneg=True
    )
    return total > threshold


def over_cap(
    open_material_sizes: Sequence[float | None], *, ledger_equity: float, risk: RiskConfig
) -> bool:
    """Item 5's `break_cap`: more open material breaks than
    `risk.max_open_breaks`, or their priced sizes summing above
    `risk.max_open_break_share` of the ledger equity. An unpriced break counts
    toward the count only; with any break open, a non-finite or non-positive
    equity is over the cap (fail closed)."""
    if not open_material_sizes:
        return False
    if len(open_material_sizes) > risk.max_open_breaks:
        return True
    if not math.isfinite(ledger_equity) or ledger_equity <= 0:
        return True
    total = sum((_finite(s, "size", nonneg=True) for s in open_material_sizes), Decimal(0))
    return total > Decimal(repr(risk.max_open_break_share)) * Decimal(repr(ledger_equity))


def is_open(event: str) -> bool:
    """Whether a break whose latest event is `event` is open (item 4)."""
    if event not in BREAK_EVENTS:
        raise ValueError(f"{event!r} is not a break event")
    return event in OPEN_BREAK_EVENTS


def open_breaks(events: Iterable[ReconcileBreakRow]) -> dict[str, ReconcileBreakRow]:
    """Each open break's latest event, by `break_id`, read in write order
    (`event_id`) and listed in the write order of that latest event."""
    rows = list(events)
    if any(e.event_id is None for e in rows):
        raise ValueError("a break event without an event_id has not been written")
    latest: dict[str, ReconcileBreakRow] = {}
    for event in sorted(rows, key=lambda e: e.event_id or 0):
        latest.pop(event.break_id, None)
        latest[event.break_id] = event
    return {bid: e for bid, e in latest.items() if is_open(e.event)}
