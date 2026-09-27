"""Fault types shared by the order path (ADR 0007 point 4; Phase 4 spec
"System fault"; plan T46).

A **system fault** is a fault in the system, not in one order: every
`SystemFaultError` takes the halt path and engages the kill switch, and the
risk-gated wrapper checks for it before its rejection allowlist, so no
subclass can ever be read as a per-order rejection. `StaleDataError` sits
beside them, not under them: it is a data fault that halts the run without
engaging the switch.

None of these is a `ValueError`. `ensure_tz_aware_utc` and the value objects
keep raising `ValueError` for an invalid value; the call site that knows
where a value came from (a clock, a reconciliation) raises the fault type,
chained from the original.

A leaf module: standard library only, so every adapter can import it.
"""

from __future__ import annotations

__all__ = [
    "AcknowledgementTimeoutError",
    "ClockError",
    "KillSwitchEngagedError",
    "LimitBreachError",
    "ReconciliationError",
    "RejectionCapError",
    "SkipCapError",
    "StaleDataError",
    "SystemFaultError",
]


class SystemFaultError(Exception):
    """A fault in the system: halt the run and engage the kill switch."""


class ClockError(SystemFaultError):
    """The clock raised, or returned a reading that is not a tz-aware
    `datetime` representable in UTC, or (in the wrapper) an implausible or
    non-monotonic one (ADR 0007 points 4 and 5)."""


class ReconciliationError(SystemFaultError):
    """Local and broker state disagree after every known explanation."""


class RejectionCapError(SystemFaultError):
    """A run's rejections exceeded `risk.max_rejections_per_run`, or every
    order of a run was rejected (ADR 0007 point 1)."""


class SkipCapError(SystemFaultError):
    """A run skipped more orders than its configured cap allows."""


class KillSwitchEngagedError(SystemFaultError):
    """An order path was entered while the kill switch is engaged."""


class LimitBreachError(SystemFaultError):
    """A planned or resulting position breaks a configured risk limit."""


class AcknowledgementTimeoutError(SystemFaultError):
    """A submitted order was neither acknowledged nor terminal within
    `paper.accept_wait_seconds`."""


class StaleDataError(Exception):
    """The data a run needs is stale: halt the run and alert, but do not
    engage the kill switch (the next successful ingest clears it)."""
