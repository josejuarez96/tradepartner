"""Tests for `store.drift_check` (#2629, T321): the rotation covers every CIK
within the window and is repeatable, a seeded drift is found, and the report
names at most ten CIKs."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from tradepartner.store.drift_check import (
    DRIFT_NAMED_MAX,
    compare_rows,
    drift_message,
    rotation_share,
    rotation_slot,
)

DAY0 = date(2026, 10, 1)
SEED = "edgar-drift"
CIKS = tuple(f"{n:010d}" for n in range(1, 401))


def test_the_rotation_covers_every_cik_within_the_window() -> None:
    window = 28
    seen: set[str] = set()
    for offset in range(window):
        seen.update(rotation_share(CIKS, DAY0 + timedelta(days=offset), window, SEED))
    assert seen == set(CIKS)


def test_any_window_start_covers_every_cik_and_each_cik_once() -> None:
    window = 7
    start = DAY0 + timedelta(days=3)
    shares = [rotation_share(CIKS, start + timedelta(days=i), window, SEED) for i in range(window)]
    flat = [c for share in shares for c in share]
    assert sorted(flat) == sorted(CIKS)


def test_the_same_seed_gives_the_same_share_and_another_seed_another() -> None:
    a = rotation_share(CIKS, DAY0, 28, SEED)
    assert a == rotation_share(CIKS, DAY0, 28, SEED)
    assert a == rotation_share(reversed(CIKS), DAY0, 28, SEED)
    assert a != rotation_share(CIKS, DAY0, 28, "other")
    assert 0 < len(a) < len(CIKS)


def test_a_cik_keeps_its_slot_when_other_ciks_come_and_go() -> None:
    slot = rotation_slot(CIKS[5], 28, SEED)
    night = date.fromordinal(DAY0.toordinal() - DAY0.toordinal() % 28 + slot)
    assert CIKS[5] in rotation_share(CIKS[:10], night, 28, SEED)
    assert CIKS[5] in rotation_share(CIKS, night, 28, SEED)


def test_a_one_day_rotation_checks_everything_nightly() -> None:
    assert rotation_share(CIKS, DAY0, 1, SEED) == CIKS


def test_a_rotation_shorter_than_one_day_is_refused() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        rotation_slot(CIKS[0], 0, SEED)
    with pytest.raises(ValueError, match="at least 1"):
        rotation_share(CIKS, DAY0, 0, SEED)


def _row(cik: str, **extra: Any) -> dict[str, Any]:
    stamp = datetime(2026, 10, 1, 22, tzinfo=UTC)
    return {"security_id": cik, "name": "Acme", "known_at": stamp, "ingested_at": stamp} | extra


def _store(ciks: tuple[str, ...]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    return {c: {"securities": [_row(c)], "listings": [_row(c, ticker="AC")]} for c in ciks}


def test_an_identical_rebuild_reports_no_drift_and_ignores_ingested_at() -> None:
    stored = _store(CIKS[:5])
    rebuilt = _store(CIKS[:5])
    later = datetime(2026, 10, 9, tzinfo=UTC)
    for tables in rebuilt.values():
        for rows in tables.values():
            for row in rows:
                row["ingested_at"] = later
    report = compare_rows(stored, rebuilt)
    assert (report.checked, report.count, report.named) == (5, 0, ())
    assert drift_message(report) == "incremental_drift: 0"


def test_a_seeded_rule_change_is_found_and_only_the_changed_ciks_named() -> None:
    stored = _store(CIKS[:6])
    rebuilt = _store(CIKS[:6])
    rebuilt[CIKS[2]]["securities"][0]["name"] = "Acme Corp"
    del rebuilt[CIKS[4]]["listings"][0]
    report = compare_rows(stored, rebuilt)
    assert report.drifted == (CIKS[2], CIKS[4])
    assert (
        drift_message(report)
        == f"incremental_drift: 2, incremental_drift_ciks: {CIKS[2]} {CIKS[4]}"
    )


def test_a_row_on_one_side_only_is_drift_even_for_a_whole_cik() -> None:
    stored = _store(CIKS[:2])
    rebuilt = _store(CIKS[:3])
    assert compare_rows(stored, rebuilt).drifted == (CIKS[2],)
    assert compare_rows(rebuilt, stored).drifted == (CIKS[2],)


def test_a_duplicated_row_is_drift() -> None:
    stored = _store(CIKS[:1])
    rebuilt = _store(CIKS[:1])
    rebuilt[CIKS[0]]["securities"].append(_row(CIKS[0]))
    assert compare_rows(stored, rebuilt).drifted == (CIKS[0],)


def test_the_report_names_at_most_ten_and_counts_them_all() -> None:
    stored = _store(CIKS[:15])
    rebuilt = _store(CIKS[:15])
    for cik in CIKS[:15]:
        rebuilt[cik]["securities"][0]["name"] = "Changed"
    report = compare_rows(stored, rebuilt)
    assert DRIFT_NAMED_MAX == 10
    assert report.count == 15
    assert report.named == CIKS[:10]
    assert drift_message(report).startswith("incremental_drift: 15, incremental_drift_ciks: ")
    assert len(drift_message(report).split("ciks: ")[1].split()) == 10


def test_comparison_reads_only_explicit_inputs_and_mutates_nothing() -> None:
    stored = _store(CIKS[:2])
    rebuilt = _store(CIKS[:2])
    before = repr(stored), repr(rebuilt)
    compare_rows(stored, rebuilt)
    assert (repr(stored), repr(rebuilt)) == before


def test_no_look_ahead_a_rebuild_over_rows_known_by_the_run_is_what_is_compared() -> None:
    """The check compares what the caller passes: a row stamped after the
    run's clock never enters a side, so the pure comparison cannot read the
    future. A stored row the rebuild could not yet know would show as drift,
    which the caller avoids by building both sides as of the run."""
    clock = datetime(2026, 10, 1, 22, tzinfo=UTC)
    stored = _store(CIKS[:1])
    future = _row(CIKS[0], known_at=clock + timedelta(days=1))
    as_of_both = {
        cik: {t: [r for r in rows if r["known_at"] <= clock] for t, rows in tables.items()}
        for cik, tables in {
            CIKS[0]: {"securities": [*stored[CIKS[0]]["securities"], future]}
        }.items()
    }
    assert (
        compare_rows({CIKS[0]: {"securities": stored[CIKS[0]]["securities"]}}, as_of_both).count
        == 0
    )
