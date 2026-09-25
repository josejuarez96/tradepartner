"""`PriceSource` interface and the price-side timing rules (spec req 5, req 6;
"Interfaces": `PriceSource.bars(ids, start, end)`,
`.corporate_actions(ids, start, end)`).

Three things live here, and nothing else:

- **Records.** `Bar` (one raw OHLCV row) and `CorporateAction` (one split or
  dividend), frozen and validated at construction. Each carries its own
  `known_at` (tz-aware UTC, normalized on construction) and `source`. Neither
  carries `ingested_at` or `provenance`: the ingest job (T16) stamps
  `ingested_at` when it writes, and provenance is fixed by record type
  (`bar` for `Bar`, `action` for `CorporateAction`, per
  `store.schema.TABLE_PROVENANCE_VALUES`).
- **Timing rules**, as pure functions every adapter uses so no two adapters
  can stamp `known_at` differently:
  - `bar_known_at(session)`: a bar for session S is knowable at the XNYS
    close of S, early close on a half day (spec "Master column sources":
    bars, `known_at` = session close).
  - `action_first_seen_known_at(ex_date, announced_at=None)`: the source's
    announcement time if it gives one, else the close of the last session
    before `ex_date` (spec req 5, the "first-seen proxy").
  - `revision_of(incoming, stored, ingested_at=...)`: a later record that
    differs from the stored one for the same key becomes a new record with
    `known_at = ingested_at`; identical values are a no-op; a revision is
    never back-dated (spec req 5 and "Definitions" > Revision). For an
    action the key is the source's id when it gives one, so a re-date or a
    cancellation is a revision too (#108).
- **The interface.** `PriceSource` resolves by `security_id` only, never by
  ticker (spec req 3: "adapters never resolve by bare ticker"). An id the
  source does not know raises `UnknownSecurityIdError` for the whole call,
  rather than silently returning nothing for it: a caller that passed a
  ticker by mistake must find out.

**Split ratio convention.** `ratio_or_amount` for a split is new shares per
old share (a 2-for-1 split is `2.0`); `store.asof.adjusted_prices_as_of`
divides pre-split prices by it. It must be positive and finite. A
dividend's `ratio_or_amount` is the cash amount per share, finite and
non-negative.
"""

from __future__ import annotations

import abc
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import overload

from tradepartner.calendar import previous_session, session_close
from tradepartner.timeutil import ensure_tz_aware_utc


class ActionType(StrEnum):
    """The corporate-action types a `PriceSource` supplies (spec req 5)."""

    SPLIT = "split"
    DIVIDEND = "dividend"


class UnknownSecurityIdError(LookupError):
    """A `PriceSource` call named a `security_id` the source cannot resolve.

    Raised for the whole call, naming only the unknown ids. A bare ticker
    passed where a `security_id` belongs lands here too.
    """


def _require_identifier(value: object, *, field_name: str) -> str:
    """A non-empty `str` with no surrounding whitespace, or `ValueError`."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(
            f"{field_name} must be a non-empty string with no surrounding whitespace, got {value!r}"
        )
    return value


def _require_date(value: object, *, field_name: str) -> date:
    """A calendar `date` that is not a `datetime`, or `TypeError`.

    `datetime` subclasses `date`, so it is rejected explicitly: a session or
    ex-date is a calendar day, not an instant.
    """
    if not isinstance(value, date) or isinstance(value, datetime):
        raise TypeError(f"{field_name} must be a date (not a datetime), got {value!r}")
    return value


def _require_finite(value: object, *, field_name: str) -> float:
    """A finite real number (not a `bool`) as `float`, or `ValueError`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field_name} must be a real number, got {value!r}")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class Bar:
    """One raw (unadjusted) daily OHLCV bar for one security and session.

    Validated on construction: prices finite and positive, `low <= open,
    close <= high`, `volume` a non-negative `int`, `session` a `date`,
    `known_at` tz-aware (normalized to UTC).
    """

    security_id: str
    session: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    known_at: datetime
    source: str

    def __post_init__(self) -> None:
        _require_identifier(self.security_id, field_name="security_id")
        _require_identifier(self.source, field_name="source")
        _require_date(self.session, field_name="session")
        prices = {}
        for name in ("open", "high", "low", "close"):
            value = _require_finite(getattr(self, name), field_name=name)
            if value <= 0:
                raise ValueError(f"{name} must be positive, got {value!r}")
            prices[name] = value
            object.__setattr__(self, name, value)
        if not prices["low"] <= min(prices["open"], prices["close"]):
            raise ValueError(f"low {prices['low']} is above open or close: {prices}")
        if not max(prices["open"], prices["close"]) <= prices["high"]:
            raise ValueError(f"high {prices['high']} is below open or close: {prices}")
        if isinstance(self.volume, bool) or not isinstance(self.volume, int) or self.volume < 0:
            raise ValueError(f"volume must be a non-negative int, got {self.volume!r}")
        object.__setattr__(
            self, "known_at", ensure_tz_aware_utc(self.known_at, field_name="known_at")
        )

    @property
    def key(self) -> tuple[str, date]:
        """The natural key a revision is matched on: `(security_id, session)`."""
        return (self.security_id, self.session)

    def same_values(self, other: Bar) -> bool:
        """True if `other` carries the same OHLCV values (`known_at` and
        `source` are not values: a re-fetch from another source with the
        same numbers is not a revision)."""
        return (self.open, self.high, self.low, self.close, self.volume) == (
            other.open,
            other.high,
            other.low,
            other.close,
            other.volume,
        )


@dataclass(frozen=True)
class CorporateAction:
    """One split or dividend for one security and ex-date.

    Validated on construction: `action_type` coerced to `ActionType` (an
    unknown string raises `ValueError`), `ex_date` a `date`, a split ratio
    positive and finite, a dividend amount non-negative and finite,
    `known_at` tz-aware (normalized to UTC), `source_action_id` `None` or a
    non-empty identifier, `cancelled` a `bool`.

    `source_action_id` is the source's own stable id for the event, when it
    gives one; it decides the record's identity (`key`), so a re-dated
    action is a revision of one event (#108). `cancelled` marks a revision
    that withdraws the event.
    """

    security_id: str
    action_type: ActionType
    ex_date: date
    ratio_or_amount: float
    known_at: datetime
    source: str
    source_action_id: str | None = None
    cancelled: bool = False

    def __post_init__(self) -> None:
        _require_identifier(self.security_id, field_name="security_id")
        _require_identifier(self.source, field_name="source")
        if self.source_action_id is not None:
            _require_identifier(self.source_action_id, field_name="source_action_id")
        if not isinstance(self.cancelled, bool):
            raise TypeError(f"cancelled must be a bool, got {self.cancelled!r}")
        _require_date(self.ex_date, field_name="ex_date")
        action_type = ActionType(self.action_type)
        object.__setattr__(self, "action_type", action_type)
        value = _require_finite(self.ratio_or_amount, field_name="ratio_or_amount")
        if action_type is ActionType.SPLIT and value <= 0:
            raise ValueError(f"a split ratio must be positive, got {value!r}")
        if action_type is ActionType.DIVIDEND and value < 0:
            raise ValueError(f"a dividend amount must be non-negative, got {value!r}")
        object.__setattr__(self, "ratio_or_amount", value)
        object.__setattr__(
            self, "known_at", ensure_tz_aware_utc(self.known_at, field_name="known_at")
        )

    @property
    def key(self) -> tuple[str, str, str] | tuple[str, str, date]:
        """The identity a revision is matched on (#108):
        `(security_id, "source_action_id", source_action_id)` when the
        source gives an id, else `(security_id, action_type, ex_date)`."""
        if self.source_action_id is not None:
            return (self.security_id, "source_action_id", self.source_action_id)
        return (self.security_id, self.action_type.value, self.ex_date)

    def same_values(self, other: CorporateAction) -> bool:
        """True if `other` carries the same type, ex-date, `ratio_or_amount`
        and `cancelled` (type and ex-date matter only under an id key, where
        they are values rather than part of the key)."""
        return (self.action_type, self.ex_date, self.ratio_or_amount, self.cancelled) == (
            other.action_type,
            other.ex_date,
            other.ratio_or_amount,
            other.cancelled,
        )


def bar_known_at(session: date) -> datetime:
    """When a bar for `session` becomes knowable: the XNYS close of that
    session (the early close on a half day), tz-aware UTC.

    Raises `TypeError` for a `datetime` and `ValueError` if `session` is not
    an XNYS session.
    """
    return session_close(session)


def action_first_seen_known_at(ex_date: date, *, announced_at: datetime | None = None) -> datetime:
    """`known_at` for a corporate action seen for the first time (spec req 5):
    `announced_at` if the source supplies one (normalized to UTC; a naive
    value raises `ValueError`), else the close of the last XNYS session
    strictly before `ex_date`.

    `ex_date` need not itself be a session. A `datetime` raises `TypeError`.
    """
    _require_date(ex_date, field_name="ex_date")
    if announced_at is not None:
        return ensure_tz_aware_utc(announced_at, field_name="announced_at")
    return session_close(previous_session(ex_date))


@overload
def revision_of(incoming: Bar, stored: Bar | None, *, ingested_at: datetime) -> Bar | None: ...


@overload
def revision_of(
    incoming: CorporateAction, stored: CorporateAction | None, *, ingested_at: datetime
) -> CorporateAction | None: ...


def revision_of(
    incoming: Bar | CorporateAction,
    stored: Bar | CorporateAction | None,
    *,
    ingested_at: datetime,
) -> Bar | CorporateAction | None:
    """Apply the revision rule to a freshly fetched record (spec req 5).

    - `stored is None` (first seen): `incoming` unchanged; its `known_at`
      is already the first-seen stamp, and must not be after `ingested_at`
      (a bar fetched mid-session is stamped at a close that has not
      happened yet: storing it would serve a partial bar as final). A
      cancelled action cannot be first seen: there is nothing to cancel.
    - Same values as `stored`: `None`, meaning nothing to write.
    - Different values: `incoming` with `known_at = ingested_at`.

    Raises `ValueError` if `ingested_at` is naive, if a first-seen record's
    `known_at` is after `ingested_at` or it is a cancelled action, if the two records do not share a
    type and natural key, or if `ingested_at` is not strictly
    after `stored.known_at` (the revision would be back-dated to, or tie
    with, the value it replaces).
    """
    ingested_at = ensure_tz_aware_utc(ingested_at, field_name="ingested_at")
    if stored is None:
        if isinstance(incoming, CorporateAction) and incoming.cancelled:
            raise ValueError(
                f"{incoming.key} is a cancelled action seen for the first time; a cancel "
                "only revises an action already stored"
            )
        if incoming.known_at > ingested_at:
            raise ValueError(
                f"{incoming.key} is stamped known_at {incoming.known_at.isoformat()}, after "
                f"ingested_at {ingested_at.isoformat()}; it is not knowable yet"
            )
        return incoming
    if type(incoming) is not type(stored) or incoming.key != stored.key:
        raise ValueError(
            f"cannot revise {type(stored).__name__} key {stored.key} with "
            f"{type(incoming).__name__} key {incoming.key}"
        )
    if incoming.same_values(stored):  # type: ignore[arg-type]
        return None
    if ingested_at <= stored.known_at:
        raise ValueError(
            f"revision of {incoming.key} at ingested_at {ingested_at.isoformat()} is not after "
            f"the stored known_at {stored.known_at.isoformat()}; a revision is never back-dated"
        )
    return replace(incoming, known_at=ingested_at)


def check_request(security_ids: Sequence[str], start: date, end: date) -> list[str]:
    """Validate a `PriceSource` call's arguments and return the ids as a list.

    A bare `str` is rejected with `TypeError` (it is a `Sequence[str]` of
    characters, never what a caller meant); `start`/`end` must be `date`s,
    not `datetime`s, with `start <= end` (`ValueError` otherwise). Every
    implementation calls this first so they all refuse the same inputs.
    """
    if isinstance(security_ids, str) or not isinstance(security_ids, Sequence):
        raise TypeError(
            f"security_ids must be a sequence of security_id strings, got {security_ids!r}"
        )
    _require_date(start, field_name="start")
    _require_date(end, field_name="end")
    if start > end:
        raise ValueError(f"start {start.isoformat()} is after end {end.isoformat()}")
    return list(security_ids)


class PriceSource(abc.ABC):
    """A source of raw daily bars and corporate actions, keyed by
    `security_id` (spec req 6).

    Both methods take an inclusive `[start, end]` calendar-date range (on
    `session` for bars, on `ex_date` for actions), validated by
    `check_request`, and return records sorted by natural key then
    `known_at`. Every returned record already carries its `known_at` under
    the timing rules in this module. An unknown id raises
    `UnknownSecurityIdError`; an empty `security_ids` returns `[]`.
    """

    @abc.abstractmethod
    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        """Raw daily bars for `security_ids` with `start <= session <= end`."""

    @abc.abstractmethod
    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        """Splits and dividends for `security_ids` with `start <= ex_date <= end`."""
