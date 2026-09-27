"""Client order ids, a pure function of the journal (ADR 0010 point 3).

Every order attempt's id is `f"{prefix}-{session:%Y%m%d}-{security_id}-{side}-{attempt}"`,
where `prefix` is `paper.order_id_prefix` and `attempt` is 1 plus the count of
`orders` rows already journaled on the session for that (security, side),
across every decision, counting rows written earlier in the same batch in
write order. So two decisions on one name on one session never share an id,
and a re-attempt never reuses a terminal order's id.

No length limit lives here: the broker's limit is
`alpaca.client_order_id_max_length`, a fact the recording task (T48b) sets
and the adapter enforces, so no numeric literal enters this module.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime

from tradepartner.store.journal import OrderRow
from tradepartner.store.schema import SIDES


def _check_side(side: str) -> None:
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}, got {side!r}")


def client_order_id(prefix: str, session: date, security_id: str, side: str, attempt: int) -> str:
    """The client order id of one attempt (ADR 0010 point 3).

    `session` is the run's trading session as a `date` (a `datetime` is
    refused, since its date part depends on its time zone). `attempt` is a
    positive integer, normally from `next_attempt`. Raises `ValueError` on an
    empty prefix or security id, a side outside `schema.SIDES`, or an attempt
    below 1.
    """
    if not prefix:
        raise ValueError("prefix must be non-empty")
    if isinstance(session, datetime) or not isinstance(session, date):
        raise ValueError(f"session must be a date, got {type(session).__name__}")
    if not security_id:
        raise ValueError("security_id must be non-empty")
    _check_side(side)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError(f"attempt must be an integer of at least 1, got {attempt!r}")
    return f"{prefix}-{session:%Y%m%d}-{security_id}-{side}-{attempt}"


def next_attempt(orders_on_session: Iterable[OrderRow], security_id: str, side: str) -> int:
    """1 plus the journaled `orders` rows for (`security_id`, `side`).

    `orders_on_session` is every `orders` row already journaled on the run's
    session, across every decision and phase, with the rows written earlier
    in the same batch appended in write order. Only the rows given are read;
    rows for another security or side are not counted.
    """
    _check_side(side)
    return 1 + sum(
        1 for row in orders_on_session if row.security_id == security_id and row.side == side
    )
