"""#3057: `repair_rename_listings`, the dry-runnable backfill of new-ticker listing rows."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.repair import RepairRefused, repair_rename_listings
from tradepartner.store import registry
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

REL = "rename-test"
RUN = datetime(2026, 11, 8, 18, tzinfo=UTC)
PKI, LB = "0000031791", "0000000009"


def _listing(sid: str, ticker: str, valid_from: date, known_at: datetime) -> dict[str, Any]:
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": "NYSE",
        "class_title": None,
        "valid_from": valid_from,
        "known_at": known_at,
        "ingested_at": known_at,
        "source": "edgar",
        "provenance": "filing",
    }


def _change(sid: str, old: str, new: str, day: date) -> dict[str, Any]:
    at = datetime(day.year, day.month, day.day, 21, tzinfo=UTC)
    return {
        "security_id": sid,
        "old_symbol": old,
        "new_symbol": new,
        "old_cusip": "",
        "new_cusip": "",
        "process_date": day,
        "source_action_id": f"{sid}-{new}",
        "known_at": at,
        "ingested_at": at,
        "source": "alpaca",
        "provenance": "action",
    }


@pytest.fixture
def store(settings: Settings) -> Settings:
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(
            conn,
            "listings",
            _listing(PKI, "PKI", date(2019, 8, 6), datetime(2019, 8, 6, tzinfo=UTC)),
        )
        insert_row(
            conn, "listings", _listing(LB, "LB", date(2019, 9, 6), datetime(2019, 9, 6, tzinfo=UTC))
        )
        insert_row(conn, "symbol_changes", _change(PKI, "PKI", "RVTY", date(2023, 5, 16)))
        insert_row(
            conn, "symbol_changes", _change(LB, "LB", "BBWI", date(2021, 8, 2))
        )  # unevidenced
        insert_row(
            conn,
            "snapshot_sightings",
            {
                "cik": PKI,
                "ticker": "RVTY",
                "exchange": "NYSE",
                "known_at": datetime(2026, 11, 2, 23, tzinfo=UTC),
                "ingested_at": datetime(2026, 11, 2, 23, 5, tzinfo=UTC),
            },
        )
        registry.open_data_release(conn, conn, name=REL, backup_path="b.duckdb", reason="#3057")
    return settings


def _tickers(settings: Settings) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(
            "SELECT security_id, ticker, valid_from, known_at, provenance FROM listings "
            "ORDER BY security_id, valid_from"
        ).fetchall()


def test_a_dry_run_lists_the_rows_and_refusals_and_writes_nothing(store: Settings) -> None:
    before = _tickers(store)
    result = repair_rename_listings(store, clock=lambda: RUN, dry_run=True)
    assert [(r[0], r[1], r[3]) for r in result.rows] == [(PKI, "RVTY", date(2023, 5, 16))]
    assert [(r.security_id, r.reason) for r in result.refusals] == [(LB, "no_evidence")]
    assert "would add 1 new-ticker listing rows of 2 name changes" in result.summary()
    assert _tickers(store) == before


def test_a_real_run_writes_exactly_the_dry_runs_rows_known_at_their_evidence(
    store: Settings,
) -> None:
    result = repair_rename_listings(store, clock=lambda: RUN, expect=1, release=REL)
    assert result.dry_run is False
    added = [r for r in _tickers(store) if r[1] == "RVTY"]
    assert added == [
        (PKI, "RVTY", date(2023, 5, 16), datetime(2026, 11, 2, 23, tzinfo=UTC), "snapshot")
    ]
    again = repair_rename_listings(store, clock=lambda: RUN, dry_run=True)
    assert again.rows == ()  # idempotent: the stored row covers the change


def test_a_real_run_refuses_another_count_and_a_missing_release(store: Settings) -> None:
    with pytest.raises(RepairRefused, match="not the expected 5"):
        repair_rename_listings(store, clock=lambda: RUN, expect=5, release=REL)
    with pytest.raises(RepairRefused, match="no open data release named 'other'"):
        repair_rename_listings(store, clock=lambda: RUN, expect=1, release="other")
    with pytest.raises(ValueError, match="named data release"):
        repair_rename_listings(store, clock=lambda: RUN, expect=1)
    assert all(r[1] != "RVTY" for r in _tickers(store))
