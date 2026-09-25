"""Shared tz-aware-UTC datetime validation (CLAUDE.md: "Datetimes are
always timezone-aware UTC").

This is a leaf module: it imports only the standard library and nothing
from `tradepartner` itself, so both `tradepartner.store.db` and
`tradepartner.adapters.broker` can depend on it without creating a cycle.
`ensure_tz_aware_utc` is the single enforcement point for that rule; before
this module existed, `store.db.ensure_tz_aware` and
`adapters.broker._ensure_tz_aware_utc` each implemented their own copy,
which could drift out of sync (issue #30).
"""

from __future__ import annotations

from datetime import UTC, datetime


def ensure_tz_aware_utc(value: datetime, *, field_name: str) -> datetime:
    """Raise `ValueError` if `value` is naive (no `tzinfo`, or a `tzinfo`
    whose `utcoffset()` is `None`, which Python also treats as naive and
    `astimezone` would read as host-local time); otherwise return it
    normalized to UTC (`.astimezone(UTC)`), so two values built from
    equivalent instants in different tzinfos always compare and print the
    same way.

    A value close to `datetime.min`/`datetime.max` with a non-zero UTC
    offset can shift past the representable range once converted to UTC
    (e.g. `datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5)))`), which
    `.astimezone(UTC)` raises as `OverflowError`; this re-raises that as a
    `ValueError` naming `field_name`, so callers only ever see one
    exception type for an invalid value (issue #43).
    """
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be tz-aware, got a naive datetime: {value!r}")
    try:
        return value.astimezone(UTC)
    except OverflowError as exc:
        raise ValueError(
            f"{field_name}={value!r} is out of the range representable in UTC"
        ) from exc
