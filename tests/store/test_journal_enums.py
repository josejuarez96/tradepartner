"""The journal's status enums and the closed sets built from them (#3643).

`store.journal_enums` is the one place the order-event and decision statuses are
spelt. This slice is a refactor with no behaviour change, so the first test pins what
must not move: the three closed sets (values and order), the two status tuples
`journal` exports, and the text of the three `CREATE TABLE` statements whose `CHECK`s
are generated from those sets. The digest was computed on main before any source
edit; it passes unchanged before and after. The other tests hold the enums to the
sets.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

import pytest

from tradepartner.store import journal, journal_enums, schema

_ORDER_EVENT_STATUSES = (
    "pending",
    "accepted",
    "replay",
    "cancel_requested",
    "cancel_noop",
    "cancel_failed",
    "filled",
    "expired",
    "rejected",
    "cancelled",
    "pending_unresolved",
)
_DECISION_KINDS = (
    "trade",
    "skip_below_minimum",
    "skip_untradable",
    "skip_below_one_share",
    "skip_delisted",
    "skip_zero",
    "dust",
    "override",
    "forced_exit",
    "skip_not_borrowable",
    "skip_short_price",
    "skip_restricted",
)
_DECISION_EVENT_STATUSES = ("skipped", "written_off")
_TERMINAL = ("filled", "expired", "rejected", "cancelled")
_ACKNOWLEDGED = ("accepted", "replay", "filled", "expired", "rejected", "cancelled")

#: sha256 of the three `CREATE TABLE` texts on main (2b665310c), before #3643.
_DDL_DIGEST = "cd419d4d459251bdcb069ce1e4d05a59fae8396389b395fef9ef743f02d689b4"

_ENUM_OF: tuple[tuple[tuple[str, str], str], ...] = (
    (("order_events", "status"), "OrderEventStatus"),
    (("decisions", "decision"), "DecisionKind"),
    (("decision_events", "status"), "DecisionEventStatus"),
)


def test_the_closed_sets_are_exactly_todays_tuples() -> None:
    assert schema.JOURNAL_ENUMS[("order_events", "status")] == _ORDER_EVENT_STATUSES
    assert schema.JOURNAL_ENUMS[("decisions", "decision")] == _DECISION_KINDS
    assert schema.JOURNAL_ENUMS[("decision_events", "status")] == _DECISION_EVENT_STATUSES
    assert journal.TERMINAL_ORDER_STATUSES == _TERMINAL
    assert journal.ACKNOWLEDGED_ORDER_STATUSES == _ACKNOWLEDGED
    ddl = schema._CREATE_ORDER_EVENTS + schema._CREATE_DECISIONS + schema._CREATE_DECISION_EVENTS
    assert hashlib.sha256(ddl.encode("utf-8")).hexdigest() == _DDL_DIGEST


def test_the_closed_sets_hold_plain_strings() -> None:
    """The sets feed `CHECK` text and SQL parameters, so they stay exact `str`s."""
    for key, _ in _ENUM_OF:
        assert all(type(value) is str for value in schema.JOURNAL_ENUMS[key]), key


@pytest.mark.parametrize(("key", "enum_name"), _ENUM_OF)
def test_each_enum_matches_its_closed_set(key: tuple[str, str], enum_name: str) -> None:
    enum: type[StrEnum] = getattr(journal_enums, enum_name)
    assert issubclass(enum, StrEnum)
    assert tuple(member.value for member in enum) == schema.JOURNAL_ENUMS[key]
    assert all(member.name == member.value.upper() for member in enum)


def test_the_restricted_skip_member_is_the_schema_constant() -> None:
    assert journal_enums.DecisionKind.SKIP_RESTRICTED.value == schema.SKIP_RESTRICTED_REASON


def test_the_terminal_statuses_are_the_four_that_end_an_order() -> None:
    status = journal_enums.OrderEventStatus
    assert journal_enums.TERMINAL_ORDER_EVENT_STATUSES == (
        status.FILLED,
        status.EXPIRED,
        status.REJECTED,
        status.CANCELLED,
    )
    assert journal.TERMINAL_ORDER_STATUSES == journal_enums.TERMINAL_ORDER_EVENT_STATUSES
