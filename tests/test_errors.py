"""The shared fault types (ADR 0007 point 4; Phase 4 plan T46).

Every `SystemFaultError` takes the halt path and engages the kill switch;
`StaleDataError` halts without engaging it, so it must not be one. None is
a `ValueError`: the wrapper must never read a fault as a bad value.
"""

from __future__ import annotations

import pytest

from tradepartner import errors

SYSTEM_FAULTS = (
    "ClockError",
    "ReconciliationError",
    "RejectionCapError",
    "SkipCapError",
    "KillSwitchEngagedError",
    "LimitBreachError",
    "AcknowledgementTimeoutError",
)


def test_clock_error_is_a_system_fault_and_not_a_value_error() -> None:
    assert issubclass(errors.ClockError, errors.SystemFaultError)
    assert not issubclass(errors.ClockError, ValueError)


def test_system_fault_error_is_a_plain_exception() -> None:
    assert errors.SystemFaultError.__bases__ == (Exception,)


@pytest.mark.parametrize("name", SYSTEM_FAULTS)
def test_every_system_fault_has_system_fault_error_as_its_direct_parent(name: str) -> None:
    cls = getattr(errors, name)
    assert cls.__bases__ == (errors.SystemFaultError,)
    assert not issubclass(cls, ValueError)


def test_stale_data_error_is_not_a_system_fault() -> None:
    assert errors.StaleDataError.__bases__ == (Exception,)
    assert not issubclass(errors.StaleDataError, errors.SystemFaultError)
    assert not issubclass(errors.StaleDataError, ValueError)


def test_the_module_exports_exactly_the_listed_types() -> None:
    assert set(errors.__all__) == {"SystemFaultError", "StaleDataError", *SYSTEM_FAULTS}


def test_a_system_fault_is_caught_as_one_and_keeps_its_cause() -> None:
    original = RuntimeError("clock down")
    with pytest.raises(errors.SystemFaultError) as caught:
        raise errors.ClockError("clock failed") from original
    assert caught.value.__cause__ is original
