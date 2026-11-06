"""The stamp audit judge (#2936): early, exact, late and the one-session look-ahead."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from tradepartner.adapters.stamp_audit import (
    StoredStamp,
    audit_stamps,
    visible_a_session_early,
)

NY = ZoneInfo("America/New_York")


def _ny(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=NY).astimezone(UTC)


def _as_utc(text: str) -> datetime:
    """The defect: an Eastern wall clock labelled UTC."""
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def test_the_issue_example_is_early_by_the_offset() -> None:
    # 0001558370-17-008504: accepted 2017-11-08 12:53:25 ET, stored 12:53:25 UTC.
    truth = {"0001558370-17-008504": _ny("2017-11-08 12:53:25")}
    stored = [
        StoredStamp("facts", "0000000001", "0001558370-17-008504", _as_utc("2017-11-08 12:53:25"))
    ]
    audit = audit_stamps(stored, truth)["facts"]
    assert (audit.checked, audit.early, audit.early_by_ny_offset) == (1, 1, 1)
    assert audit.look_ahead
    assert audit.session_early == 0  # filed before the close: same session


def test_after_the_close_it_is_a_session_early() -> None:
    accepted = _ny("2024-03-12 17:30:00")  # EDT
    wrong = _as_utc("2024-03-12 17:30:00")  # 13:30 ET
    assert visible_a_session_early(wrong, accepted)
    assert not visible_a_session_early(accepted, accepted)


def test_exact_and_late_are_not_look_ahead() -> None:
    truth = {"a": _ny("2020-01-15 10:00:00"), "b": _ny("2020-01-15 10:00:00")}
    stored = [
        StoredStamp("statement_facts", "1", "a", truth["a"]),
        StoredStamp("statement_facts", "1", "b", _as_utc("2020-01-15 16:00:00")),
        StoredStamp("statement_facts", "1", "no-header", _as_utc("2020-01-15 00:00:00")),
    ]
    audit = audit_stamps(stored, truth)["statement_facts"]
    assert (audit.checked, audit.exact, audit.late, audit.early) == (2, 1, 1, 0)
    assert not audit.look_ahead
