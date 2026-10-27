"""Check-only benchmark identity (backtest spec amendment #1598, item 3; T243).

`CHECK_ONLY_IDS` names the securities stored for the index check alone: a trial's
`benchmark_security_ids` call refuses one by name, the index check passes
`check_only=True`. The full backfill-seeded path is in `tests/test_benchmark_backfill.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest

from tradepartner.store import benchmarks
from tradepartner.store.benchmarks import (
    CHECK_ONLY_IDS,
    BenchmarkIdentityError,
    benchmark_security_ids,
)
from tradepartner.store.db import insert_row
from tradepartner.store.schema import init_schema

KNOWN = datetime(2019, 1, 2, 21, tzinfo=UTC)
COMMON = {
    "known_at": KNOWN,
    "ingested_at": KNOWN,
    "source": "config",
    "provenance": "snapshot_static",
}


def test_the_check_only_set_is_pinned_by_value() -> None:
    assert frozenset({"BENCH:RSP"}) == CHECK_ONLY_IDS
    assert isinstance(CHECK_ONLY_IDS, frozenset)


@pytest.fixture
def conn(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect(str(tmp_path / "s.duckdb"))
    c.execute("SET TimeZone='UTC'")
    init_schema(c)
    for sid, ticker in (("BENCH:RSP", "RSP"), ("BENCH:SPY", "SPY")):
        insert_row(
            c,
            "securities",
            {"security_id": sid, "cik": "0000000001", "name": ticker, "benchmark": True} | COMMON,
        )
        insert_row(
            c,
            "listings",
            {
                "security_id": sid,
                "ticker": ticker,
                "exchange": "NYSE",
                "class_title": None,
                "valid_from": date(2016, 1, 4),
            }
            | COMMON,
        )
    return c


def test_a_trial_read_of_a_check_only_symbol_raises(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(BenchmarkIdentityError, match=r"RSP.*check-only"):
        benchmark_security_ids(conn, ["SPY", "RSP"], start=date(2019, 1, 2))
    assert benchmark_security_ids(conn, ["SPY"], start=date(2019, 1, 2)) == {"SPY": "BENCH:SPY"}


def test_the_index_check_resolves_both_series(conn: duckdb.DuckDBPyConnection) -> None:
    ids = benchmark_security_ids(conn, ["SPY", "RSP"], start=date(2019, 1, 2), check_only=True)
    assert ids == {"RSP": "BENCH:RSP", "SPY": "BENCH:SPY"}
    assert set(ids.values()) >= benchmarks.CHECK_ONLY_IDS
