"""The journal's status enums: one spelling of each order-event and decision state (#3643).

`schema.JOURNAL_ENUMS` builds the closed sets, and so the `CHECK`s, of
`order_events.status`, `decisions.decision` and `decision_events.status` from these
classes; `journal` builds its terminal and acknowledged tuples from them. Member
order is the order of the `CHECK` text, so adding, removing or reordering a member
changes the DDL and needs a migration. Every member is a `str` equal to the stored
value, so a caller can compare a row's `status` with a member directly.

A leaf module: it imports `enum` only, so any layer may import it.
"""

from __future__ import annotations

from enum import StrEnum


class OrderEventStatus(StrEnum):
    """The closed set of `order_events.status`."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REPLAY = "replay"
    CANCEL_REQUESTED = "cancel_requested"
    CANCEL_NOOP = "cancel_noop"
    CANCEL_FAILED = "cancel_failed"
    FILLED = "filled"
    EXPIRED = "expired"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    PENDING_UNRESOLVED = "pending_unresolved"


class DecisionKind(StrEnum):
    """The closed set of `decisions.decision`."""

    TRADE = "trade"
    SKIP_BELOW_MINIMUM = "skip_below_minimum"
    SKIP_UNTRADABLE = "skip_untradable"
    SKIP_BELOW_ONE_SHARE = "skip_below_one_share"
    SKIP_DELISTED = "skip_delisted"
    SKIP_ZERO = "skip_zero"
    DUST = "dust"
    OVERRIDE = "override"
    FORCED_EXIT = "forced_exit"
    SKIP_NOT_BORROWABLE = "skip_not_borrowable"
    SKIP_SHORT_PRICE = "skip_short_price"
    SKIP_RESTRICTED = "skip_restricted"


class DecisionEventStatus(StrEnum):
    """The closed set of `decision_events.status`."""

    SKIPPED = "skipped"
    WRITTEN_OFF = "written_off"


#: `order_events` statuses that end an order.
TERMINAL_ORDER_EVENT_STATUSES: tuple[OrderEventStatus, ...] = (
    OrderEventStatus.FILLED,
    OrderEventStatus.EXPIRED,
    OrderEventStatus.REJECTED,
    OrderEventStatus.CANCELLED,
)
