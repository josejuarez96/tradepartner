"""Tests for `tradepartner.timeutil.ensure_tz_aware_utc`, the single shared
enforcement point for CLAUDE.md's "datetimes are always timezone-aware UTC"
rule."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone, tzinfo

import pytest

from tradepartner.timeutil import ensure_tz_aware_utc


def test_naive_datetime_raises_value_error_with_field_name() -> None:
    naive = datetime(2024, 1, 1, 12, 0, 0)  # noqa: DTZ001
    with pytest.raises(ValueError, match="submitted_at must be tz-aware"):
        ensure_tz_aware_utc(naive, field_name="submitted_at")


def test_utc_input_returned_equal_with_utc_tzinfo() -> None:
    value = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    result = ensure_tz_aware_utc(value, field_name="known_at")
    assert result == value
    assert result.tzinfo is UTC


def test_non_utc_aware_input_normalized_to_same_instant_in_utc() -> None:
    eastern = timezone(timedelta(hours=-5))
    value = datetime(2024, 1, 1, 7, 0, 0, tzinfo=eastern)
    result = ensure_tz_aware_utc(value, field_name="filled_at")
    assert result == value
    assert result.tzinfo is UTC
    assert result.utcoffset() == timedelta(0)


def test_overflow_near_datetime_min_with_positive_offset_raises_value_error() -> None:
    # A positive UTC offset pushes datetime.min further negative once
    # converted to UTC, which OverflowError's underlying C implementation
    # cannot represent; this must surface as a ValueError naming the field,
    # not an OverflowError.
    near_min = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=5)))
    with pytest.raises(ValueError, match="valid_from") as exc_info:
        ensure_tz_aware_utc(near_min, field_name="valid_from")
    assert "out of" in str(exc_info.value) or "range" in str(exc_info.value)
    assert exc_info.value.__cause__ is not None
    assert isinstance(exc_info.value.__cause__, OverflowError)


def test_overflow_near_datetime_max_with_negative_offset_raises_value_error() -> None:
    near_max = datetime.max.replace(tzinfo=timezone(timedelta(hours=-5)))
    with pytest.raises(ValueError, match="valid_to") as exc_info:
        ensure_tz_aware_utc(near_max, field_name="valid_to")
    assert isinstance(exc_info.value.__cause__, OverflowError)


def test_aware_datetime_max_with_utc_tzinfo_passes_unchanged() -> None:
    value = datetime.max.replace(tzinfo=UTC)
    result = ensure_tz_aware_utc(value, field_name="valid_to")
    assert result == value
    assert result.tzinfo is UTC


class _NoOffset(tzinfo):
    """A tzinfo whose `utcoffset()` is None: naive under Python's rules."""

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return None


def test_tzinfo_without_utcoffset_is_rejected_as_naive() -> None:
    value = datetime(2024, 1, 1, 12, 0, 0, tzinfo=_NoOffset())
    with pytest.raises(ValueError, match="known_at must be tz-aware"):
        ensure_tz_aware_utc(value, field_name="known_at")
