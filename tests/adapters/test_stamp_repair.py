"""Tests for the #1382 stamps repair rules (plan task T166)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from tradepartner.adapters.stamp_repair import (
    MANIFEST_COLUMNS,
    AuditDecision,
    ManifestRow,
    audit_decision,
    audit_sample,
    classify_cik,
    in_window_change_allowed,
    is_provably_early,
    ny_utc_offset,
    shift_stamp,
)

EDT = timedelta(hours=4)
EST = timedelta(hours=5)


def _utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)  # type: ignore[misc]


# --- the offset -------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        # 2026: DST starts Sun 8 Mar, ends Sun 1 Nov.
        (date(2026, 3, 7), EST),
        (date(2026, 3, 8), EDT),
        (date(2026, 3, 9), EDT),
        (date(2026, 10, 31), EDT),
        (date(2026, 11, 1), EST),
        (date(2026, 11, 2), EST),
        # 2024: DST starts Sun 10 Mar, ends Sun 3 Nov.
        (date(2024, 3, 8), EST),
        (date(2024, 3, 11), EDT),
        (date(2024, 11, 1), EDT),
        (date(2024, 11, 4), EST),
        (date(2025, 1, 15), EST),
        (date(2025, 7, 15), EDT),
    ],
)
def test_offset_is_4h_on_edt_and_5h_on_est_dates(day: date, expected: timedelta) -> None:
    assert ny_utc_offset(day) == expected


def test_shift_adds_the_offset_on_the_stamps_date() -> None:
    # Wall clock 16:05 New York written as UTC.
    assert shift_stamp(_utc(2026, 3, 6, 16, 5)) == _utc(2026, 3, 6, 21, 5)
    assert shift_stamp(_utc(2026, 3, 9, 16, 5)) == _utc(2026, 3, 9, 20, 5)
    assert shift_stamp(_utc(2026, 10, 30, 16, 5)) == _utc(2026, 10, 30, 20, 5)
    assert shift_stamp(_utc(2026, 11, 2, 16, 5)) == _utc(2026, 11, 2, 21, 5)


def test_shift_uses_the_offset_at_the_wall_clock_time_on_a_dst_sunday() -> None:
    # 2026-03-08 01:30 New York was still EST; 03:30 was EDT.
    assert shift_stamp(_utc(2026, 3, 8, 1, 30)) == _utc(2026, 3, 8, 6, 30)
    assert shift_stamp(_utc(2026, 3, 8, 3, 30)) == _utc(2026, 3, 8, 7, 30)
    # 2026-11-01 00:30 New York was still EDT.
    assert shift_stamp(_utc(2026, 11, 1, 0, 30)) == _utc(2026, 11, 1, 4, 30)


def test_shift_refuses_a_naive_stamp() -> None:
    with pytest.raises(ValueError):
        shift_stamp(datetime(2026, 3, 6, 16, 5))  # noqa: DTZ001


# --- the filing-date rule ---------------------------------------------------


def test_friday_stamp_with_monday_filing_date_is_provably_early() -> None:
    # Fri 2026-03-06 16:05 New York (EST) = 21:05 UTC, indexed Mon 03-09.
    stamp = _utc(2026, 3, 6, 21, 5)
    assert is_provably_early(stamp, date(2026, 3, 9))
    assert not is_provably_early(stamp, date(2026, 3, 6))


def test_after_cutoff_friday_stamp_filed_monday_is_not_early() -> None:
    # Fri 17:45 New York: Monday is the right filing date.
    assert not is_provably_early(_utc(2026, 3, 6, 22, 45), date(2026, 3, 9))


@pytest.mark.parametrize(
    ("ny_time", "early"),
    [(time(17, 29), True), (time(17, 29, 59), True), (time(17, 30), False), (time(17, 31), False)],
)
def test_cutoff_pair_17_29_and_17_31(ny_time: time, early: bool) -> None:
    # Wed 2026-07-15, EDT: New York + 4 h = UTC; indexed the next day.
    utc = datetime.combine(date(2026, 7, 15), ny_time, tzinfo=UTC) + EDT
    assert is_provably_early(utc, date(2026, 7, 16)) is early


def test_weekend_and_holiday_stamps_prove_nothing() -> None:
    # Sat 2026-03-07 10:00 New York, indexed Monday.
    assert not is_provably_early(_utc(2026, 3, 7, 15, 0), date(2026, 3, 9))
    # Thu 2026-11-26 (Thanksgiving) 10:00 New York, indexed Friday.
    thanksgiving = _utc(2026, 11, 26, 15, 0)
    assert is_provably_early(thanksgiving, date(2026, 11, 27))
    assert not is_provably_early(
        thanksgiving, date(2026, 11, 27), holidays=frozenset({date(2026, 11, 26)})
    )


def test_good_friday_is_an_edgar_business_day() -> None:
    # Good Friday 2026-04-03: NYSE is closed, EDGAR is open. 10:00 New York
    # (EDT) indexed Monday 04-06 is provably early with EDGAR's holidays.
    good_friday = _utc(2026, 4, 3, 14, 0)
    assert is_provably_early(good_friday, date(2026, 4, 6), holidays=frozenset({date(2026, 1, 1)}))


def test_rule_reads_the_new_york_date_not_the_utc_date() -> None:
    # 2026-03-09 01:00 UTC is Sun 03-08 21:00 New York: a weekend, no proof.
    assert not is_provably_early(_utc(2026, 3, 9, 1, 0), date(2026, 3, 10))


# --- the CIK class ----------------------------------------------------------


def test_class_true_when_every_probe_equals_the_stamp() -> None:
    a, b = _utc(2026, 3, 6, 21, 5), _utc(2026, 7, 15, 14, 0)
    assert classify_cik([(a, a), (b, b)]) == "true"


def test_class_early_when_every_probe_equals_stamp_plus_offset() -> None:
    a, b = _utc(2026, 3, 6, 16, 5), _utc(2026, 7, 15, 10, 0)
    assert classify_cik([(a, a + EST), (b, b + EDT)]) == "early"


def test_class_mixed_otherwise() -> None:
    a, b = _utc(2026, 3, 6, 16, 5), _utc(2026, 7, 15, 10, 0)
    assert classify_cik([(a, a + EST), (b, b)]) == "mixed"
    # A wrong offset (EST on an EDT date) is not early.
    assert classify_cik([(b, b + EST)]) == "mixed"


def test_class_needs_a_probe() -> None:
    with pytest.raises(ValueError):
        classify_cik([])


# --- the audit sample -------------------------------------------------------


def _shifted() -> dict[str, list[str]]:
    shifted = {f"{c:010d}": [f"{c:010d}-26-{i:06d}" for i in range(c % 7 + 1)] for c in range(60)}
    shifted["0000009999"] = [f"0000009999-26-{i:06d}" for i in range(400)]
    return shifted


def test_sample_is_deterministic_for_a_seed() -> None:
    shifted = _shifted()
    reordered = {k: list(reversed(v)) for k, v in reversed(list(shifted.items()))}
    first = audit_sample(shifted, seed=1382)
    assert first == audit_sample(shifted, seed=1382)
    assert first == audit_sample(reordered, seed=1382)
    assert first != audit_sample(shifted, seed=1383)


def test_sample_covers_every_cik_plus_one_percent() -> None:
    shifted = _shifted()
    shifted["0000008888"] = []  # no shifted store-backing stamp: nothing to audit
    sample = audit_sample(shifted, seed=7)
    ciks = {cik for cik, _ in sample}
    assert ciks == {cik for cik, accs in shifted.items() if accs}
    total = sum(len(v) for v in shifted.values())
    assert len(sample) == 61 + -(-total // 100)
    assert len(set(sample)) == len(sample)
    for cik, accession in sample:
        assert accession in shifted[cik]


def test_sample_when_rest_is_smaller_than_one_percent() -> None:
    assert audit_sample({"1": ["a"], "2": ["b"]}, seed=0) == [("1", "a"), ("2", "b")]


# --- the stop rule ----------------------------------------------------------


def test_mismatched_ciks_go_filing_by_filing() -> None:
    mismatches = {f"{c:04d}": 0 for c in range(500)}
    mismatches["0007"] = 2
    decision = audit_decision(mismatches)
    assert decision == AuditDecision(
        per_filing_ciks=("0007",), audited_ciks=500, mismatched_ciks=1, stop=False
    )


def test_stop_rule_triggers_just_past_one_percent() -> None:
    at_boundary = {f"{c:04d}": (1 if c < 2 else 0) for c in range(200)}  # 2/200 = 1%
    assert not audit_decision(at_boundary).stop
    past = {f"{c:04d}": (1 if c < 3 else 0) for c in range(200)}  # 3/200 = 1.5%
    assert audit_decision(past).stop
    tight = {f"{c:04d}": (1 if c < 2 else 0) for c in range(199)}  # 2/199 > 1%
    assert audit_decision(tight).stop


def test_stop_rule_on_no_audit_does_not_stop() -> None:
    assert audit_decision({}) == AuditDecision((), 0, 0, False)


# --- the manifest row -------------------------------------------------------


def test_manifest_row_round_trips_through_a_record() -> None:
    old = _utc(2026, 3, 6, 16, 5)
    row = ManifestRow("0000320193", "0000320193-26-000001", old, old + EST, "shift", "class early")
    record = row.to_record()
    assert tuple(record) == MANIFEST_COLUMNS
    assert record["old_stamp"] == "2026-03-06T16:05:00+00:00"
    assert ManifestRow.from_record(record) == row
    assert row.changed


@pytest.mark.parametrize("method", ["kept", "unrepaired"])
def test_kept_and_unrepaired_rows_keep_the_stamp(method: str) -> None:
    old = _utc(2026, 3, 6, 16, 5)
    assert not ManifestRow("1", "a", old, old, method, "").changed  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ManifestRow("1", "a", old, old + EST, method, "")  # type: ignore[arg-type]


def test_shift_row_must_move_by_the_offset_and_sgml_row_may_not() -> None:
    old = _utc(2026, 3, 6, 16, 5)
    with pytest.raises(ValueError):
        ManifestRow("1", "a", old, old + EDT, "shift", "")
    assert ManifestRow("1", "a", old, old + EDT, "sgml", "header").changed


def test_manifest_row_refuses_unknown_method_and_naive_stamps() -> None:
    old = _utc(2026, 3, 6, 16, 5)
    with pytest.raises(ValueError):
        ManifestRow("1", "a", old, old, "moved", "")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ManifestRow("1", "a", datetime(2026, 3, 6), old, "sgml", "")  # noqa: DTZ001
    record = ManifestRow("1", "a", old, old, "kept", "").to_record()
    record["method"] = "moved"
    with pytest.raises(ValueError):
        ManifestRow.from_record(record)


# --- the in-window rule (T166f, amendment 2026-10-11 decision (d)) -----------

WINDOW = date(2026, 10, 9)
# 2026-10-09 18:30Z is 14:30 New York (EDT): a late stamp served 4 h late.
LATE = _utc(2026, 10, 9, 22, 30)


def _row(old: datetime, new: datetime, method: str = "sgml") -> ManifestRow:
    return ManifestRow("320193", "0000320193-26-000001", old, new, method, "test")  # type: ignore[arg-type]


def test_window_rule_passes_an_earlier_sgml_move_of_exactly_the_offset() -> None:
    assert in_window_change_allowed(_row(LATE, LATE - EDT), WINDOW)


def test_window_rule_passes_an_earlier_sgml_move_within_the_offset() -> None:
    assert in_window_change_allowed(_row(LATE, LATE - timedelta(hours=1)), WINDOW)


def test_window_rule_passes_a_row_outside_the_window() -> None:
    old = _utc(2026, 9, 1, 14)
    assert in_window_change_allowed(_row(old, shift_stamp(old), "shift"), WINDOW)
    assert in_window_change_allowed(_row(old, old + timedelta(hours=9)), WINDOW)


def test_window_rule_passes_anything_with_no_open_window_and_an_unchanged_row() -> None:
    assert in_window_change_allowed(_row(LATE, LATE + timedelta(hours=9)), None)
    assert in_window_change_allowed(_row(LATE, LATE, "kept"), WINDOW)


def test_window_rule_refuses_a_later_move_in_the_window() -> None:
    assert not in_window_change_allowed(_row(LATE, LATE + timedelta(minutes=1)), WINDOW)


def test_window_rule_refuses_a_shift_in_the_window() -> None:
    assert not in_window_change_allowed(_row(LATE, shift_stamp(LATE), "shift"), WINDOW)


def test_window_rule_refuses_an_earlier_move_larger_than_the_offset() -> None:
    too_far = LATE - EDT - timedelta(seconds=1)
    assert not in_window_change_allowed(_row(LATE, too_far), WINDOW)


def test_window_rule_counts_a_new_stamp_inside_the_window_as_inside() -> None:
    # Old stamp the day before the window, moved later into it: refused.
    old = _utc(2026, 10, 9, 3, 30)  # 2026-10-08 23:30 New York
    assert not in_window_change_allowed(_row(old, old + timedelta(hours=1)), WINDOW)


def test_window_rule_uses_the_old_stamps_date_offset_in_est() -> None:
    # 2026-11-03 is EST: 5 h is allowed, 5 h and a second is not.
    old = _utc(2026, 11, 3, 23)
    assert in_window_change_allowed(_row(old, old - EST), WINDOW)
    assert not in_window_change_allowed(_row(old, old - EST - timedelta(seconds=1)), WINDOW)
