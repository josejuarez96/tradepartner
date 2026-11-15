"""The `figi_identity` health block (plan T399, #3437): reports counts by kind
and rows, fails only while `openfigi.max_identity_disagreements` is set."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pytest

from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.health import (
    FIGI_IDENTITY_DISAGREEMENTS,
    figi_identity,
    health_report,
)
from tradepartner.store.db import insert_row
from tradepartner.store.figi_identity import (
    FIGI_SHARED_ACROSS_CIKS,
    KINDS,
    UNMAPPED_WHILE_LISTED,
)

T_END = session_close(date(2020, 6, 30))
OBSERVED = datetime(2020, 6, 29, 21, 0, tzinfo=UTC)


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def _figi(
    sid: str, *, mapped: bool = True, figi: str = "BBG1", known: datetime = OBSERVED
) -> dict[str, Any]:
    return {
        "security_id": sid,
        "ticker": sid,
        "figi": figi if mapped else "",
        "composite_figi": figi if mapped else "",
        "share_class_figi": f"SC-{sid}" if mapped else "",
        "security_type": "Common Stock",
        "security_type2": "Common Stock",
        "market_sector": "Equity" if mapped else "",
        "name": sid,
        "exch_code": "US",
        "mapped": mapped,
        "known_at": known,
        "ingested_at": known,
        "provenance": "openfigi",
        "source": "openfigi",
    }


def _disagree(conn: duckdb.DuckDBPyConnection) -> None:
    """SEC_DUAL_A and SEC_MTUM (two CIKs) share a FIGI; SEC_STATIC_PRE2019 is
    unmapped (live at the observation in the fixture store)."""
    for sid in ("SEC_DUAL_A", "SEC_MTUM"):
        insert_row(conn, "openfigi_types", _figi(sid, figi="BBGSAME"))
    insert_row(conn, "openfigi_types", _figi("SEC_FACTS_RESTATED", mapped=False))


def test_the_block_reports_counts_by_kind_and_rows(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _disagree(fixture_store)
    block = figi_identity(fixture_store, T_END, _settings())
    assert block.observations == 3
    assert set(block.counts) == set(KINDS)
    assert block.counts[FIGI_SHARED_ACROSS_CIKS] == 2
    assert block.counts[UNMAPPED_WHILE_LISTED] == 1
    assert block.first_rows(1).height == 1
    assert any("query echo" in line for line in block.cannot_check)
    assert block.verdict == "3 disagreements (report-only)"


def test_unset_the_block_reports_and_fails_nothing(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _disagree(fixture_store)
    report = health_report(fixture_store, T_END, _settings())
    assert report.figi_identity.disagreements == 3
    assert FIGI_IDENTITY_DISAGREEMENTS not in report.failures
    assert report.figi_identity.failed is False


def test_exit_code_unchanged_with_the_key_unset(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    before = health_report(fixture_store, T_END, _settings())
    _disagree(fixture_store)
    after = health_report(fixture_store, T_END, _settings())
    assert (after.failures, after.ok, after.check_failures) == (
        before.failures,
        before.ok,
        before.check_failures,
    )


def test_set_the_rows_over_the_threshold_fail(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _disagree(fixture_store)
    tight = health_report(
        fixture_store, T_END, _settings(openfigi={"max_identity_disagreements": 2})
    )
    assert FIGI_IDENTITY_DISAGREEMENTS in tight.failures
    assert tight.figi_identity.verdict == "3 disagreements: fail (max 2)"
    loose = health_report(
        fixture_store, T_END, _settings(openfigi={"max_identity_disagreements": 3})
    )
    assert FIGI_IDENTITY_DISAGREEMENTS not in loose.failures


def test_set_with_no_observation_fails_closed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    report = health_report(
        fixture_store, T_END, _settings(openfigi={"max_identity_disagreements": 0})
    )
    assert FIGI_IDENTITY_DISAGREEMENTS in report.failures
    assert report.figi_identity.verdict == "nothing checked: fail"


def test_negative_threshold_is_refused() -> None:
    with pytest.raises(ValueError):
        _settings(openfigi={"max_identity_disagreements": -1})
