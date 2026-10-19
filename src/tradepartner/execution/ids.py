"""Client order ids, a pure function of the journal (ADR 0010 point 3,
superseded on the format by ADR 0015 seam 1, plan T133).

Every order attempt's id is
`f"{prefix}-{book}-{session:%Y%m%d}-{security_id}-{side}-{attempt}"`,
where `side` is the intent's token (`ID_TOKENS`: `buy`, `sell`, `short`, `cover`;
T228), `prefix` is `paper.order_id_prefix`, `book` is the window's `book_id`,
and `attempt` is 1 plus the count of `orders` rows already journaled on the
session for that (security, token), across every decision, counting rows
written earlier in the same batch in write order. So two decisions on one
name on one session never share an id, and a re-attempt never reuses a
terminal order's id.

`book` must match `^[A-Za-z0-9]+$` (no `-` or `:`, so prefix and book parse
from the left; `security_id` may contain a `:`), refused otherwise. The reader
knows the configured prefix (`reconcile.foreign_order`), and the attempt
parses from the right (`wrapper.py`'s `int(request.client_order_id.rsplit("-",
1)[1])`): neither end changes here.

No length limit lives here: the broker's limit is
`alpaca.client_order_id_max_length`, a fact the recording task (T48b) sets
and the adapter enforces, and the whole id is checked against it in
`phases.requests_for`, so no numeric literal enters this module.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date, datetime

from tradepartner.store.journal import OrderRow
from tradepartner.store.schema import LONG

#: The book token's grammar (ADR 0015 seam 1): alphanumerics only, so the
#: prefix and the book parse from the left of the id and `security_id`'s `:`
#: and `-` never confuse them.
_BOOK_PATTERN = re.compile(r"^[A-Za-z0-9]+$")


#: The id's intent tokens (paper spec amendment 2026-10-19 item 5, T228): `buy`
#: and `sell` for the long intents (so every long id is byte-identical to the
#: pre-short format), `short` for a short open and `cover` for a short close.
ID_TOKENS: tuple[str, ...] = ("buy", "sell", "short", "cover")

#: Alpaca `position_intent` -> id token.
_INTENT_TOKENS: dict[str, str] = {
    "buy_to_open": "buy",
    "sell_to_close": "sell",
    "sell_to_open": "short",
    "buy_to_close": "cover",
}

#: An `orders` row's (side, position_side) -> id token.
_ROW_TOKENS: dict[tuple[str, str], str] = {
    ("buy", LONG): "buy",
    ("sell", LONG): "sell",
    ("sell", "short"): "short",
    ("buy", "short"): "cover",
}


def token_for(position_intent: str) -> str:
    """The id token of an Alpaca `position_intent`; `ValueError` outside the four."""
    try:
        return _INTENT_TOKENS[position_intent]
    except KeyError:
        raise ValueError(
            f"position_intent must be one of {tuple(_INTENT_TOKENS)}, got {position_intent!r}"
        ) from None


def _check_side(side: str) -> None:
    if side not in ID_TOKENS:
        raise ValueError(f"token must be one of {ID_TOKENS}, got {side!r}")


def _check_book(book: str) -> None:
    if not isinstance(book, str) or _BOOK_PATTERN.match(book) is None:
        raise ValueError(f"book must match ^[A-Za-z0-9]+$, got {book!r}")


def client_order_id(
    prefix: str, book: str, session: date, security_id: str, side: str, attempt: int
) -> str:
    """The client order id of one attempt (ADR 0010 point 3, ADR 0015 seam 1).

    `book` is the window's `book_id`, `session` the run's trading session as a
    `date` (a `datetime` is refused, since its date part depends on its time
    zone), and `attempt` a positive integer, normally from `next_attempt`.
    Raises `ValueError` on an empty prefix, a `book` outside
    `^[A-Za-z0-9]+$`, a non-date session, an empty security id, a side outside
    `schema.SIDES`, or an attempt below 1.
    """
    if not prefix:
        raise ValueError("prefix must be non-empty")
    _check_book(book)
    if isinstance(session, datetime) or not isinstance(session, date):
        raise ValueError(f"session must be a date, got {type(session).__name__}")
    if not security_id:
        raise ValueError("security_id must be non-empty")
    _check_side(side)
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError(f"attempt must be an integer of at least 1, got {attempt!r}")
    return f"{prefix}-{book}-{session:%Y%m%d}-{security_id}-{side}-{attempt}"


def next_attempt(orders_on_session: Iterable[OrderRow], security_id: str, side: str) -> int:
    """1 plus the journaled `orders` rows for (`security_id`, `side`).

    `orders_on_session` is every `orders` row already journaled on the run's
    session, across every decision and phase, with the rows written earlier
    in the same batch appended in write order. Only the rows given are read;
    rows for another security or side are not counted.

    The rows are checked against themselves, because a wrongly scoped read
    would re-issue an id: they must all be on one session, and the matching
    rows' `attempt` values must be exactly 1, 2, ... n in write order. A gap,
    a duplicate or a stray session raises `ValueError`.
    """
    _check_side(side)
    sessions: set[date] = set()
    attempts: list[int] = []
    for row in orders_on_session:
        sessions.add(row.session)
        token = _ROW_TOKENS.get((row.side, row.position_side))
        if row.security_id == security_id and token == side:
            attempts.append(row.attempt)
    if len(sessions) > 1:
        raise ValueError(f"orders_on_session spans sessions {sorted(sessions)}")
    expected = list(range(1, len(attempts) + 1))
    if attempts != expected:
        raise ValueError(
            f"journaled attempts for ({security_id}, {side}) are {attempts}, expected {expected}"
        )
    return len(attempts) + 1
