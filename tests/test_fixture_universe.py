"""Tests for the fixture-universe generator (T5).

Covers: every spec req 13 case is present and named in the README;
regenerating into a tmp dir reproduces the committed CSVs byte-for-byte
(so a stale commit fails CI); the committed fixtures load through
`fixture_store` with no error and every table it populates is non-empty;
every row respects `known_at <= ingested_at`, a non-null `source`, and a
provenance value inside that table's allowed set.
"""

from __future__ import annotations

import csv
import importlib.util
import re
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import pytest
from conftest import load_universe_fixtures

from tradepartner.store import schema
from tradepartner.store.db import configure_connection

_FIXTURES_DIR = Path(__file__).parent / "fixtures" / "universe"
_GENERATOR_PATH = Path(__file__).parent.parent / "scripts" / "make_fixture_universe.py"


def _load_generator() -> object:
    """Import `scripts/make_fixture_universe.py` by path.

    `scripts/` is not a package (no `__init__.py`, and `pyproject.toml`
    is not otherwise touched by this task), so it cannot be imported as
    `scripts.make_fixture_universe`; load it directly from its file path
    instead.
    """
    spec = importlib.util.spec_from_file_location("make_fixture_universe", _GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_generator = _load_generator()
write_fixtures = _generator.write_fixtures  # type: ignore[attr-defined]

_REQ13_CASE_KEYWORDS = [
    "Truncated-history delisting",
    "Delisting within the gap window",
    "Clean merger delisting",
    "Form 25-NSE delisting",
    "Dual-class company",
    "Form 25 on a non-common class",
    "Exchange transfer",
    "Same-company ticker change",
    "Ticker reused by a different company",
    "Plain 2-for-1 split",
    "Split between a shares filing and a documented T",
    "Split known before a documented T with ex-date after T",
    "Holiday inside a bar range",
    "Half day inside a bar range",
    "Revised dividend",
    "Restated shares fact",
    "Stale shares fact",
    "Unclassifiable name",
    "snapshot_static-only pre-2019 listing",
    "Benchmark seeded from config with a dividend (req 13): SPY",
    "Benchmark seeded from config with a dividend (req 13): MTUM",
]

_CSV_TABLES = (
    "securities",
    "listings",
    "classifications",
    "delistings",
    "prices_daily",
    "corporate_actions",
    "facts",
)


def test_readme_documents_every_req13_case() -> None:
    readme = (_FIXTURES_DIR / "README.md").read_text()
    missing = [kw for kw in _REQ13_CASE_KEYWORDS if kw not in readme]
    assert not missing, f"README missing case(s): {missing}"


def test_regeneration_is_byte_identical(tmp_path: Path) -> None:
    write_fixtures(tmp_path)
    for name in (*[f"{t}.csv" for t in _CSV_TABLES], "README.md"):
        committed = (_FIXTURES_DIR / name).read_bytes()
        regenerated = (tmp_path / name).read_bytes()
        assert committed == regenerated, f"{name} differs between committed and regenerated output"


def test_regeneration_twice_is_byte_identical(tmp_path: Path) -> None:
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    write_fixtures(out1)
    write_fixtures(out2)
    for name in (*[f"{t}.csv" for t in _CSV_TABLES], "README.md"):
        assert (out1 / name).read_bytes() == (out2 / name).read_bytes()


def test_fixtures_load_through_fixture_store_and_are_nonempty() -> None:
    conn = duckdb.connect(":memory:")
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        load_universe_fixtures(conn, _FIXTURES_DIR)
        for table in _CSV_TABLES:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            assert count is not None and count[0] > 0, f"{table} is empty"
    finally:
        conn.close()


def test_every_csv_row_obeys_known_at_ingested_at_and_provenance() -> None:
    for table in _CSV_TABLES:
        path = _FIXTURES_DIR / f"{table}.csv"
        allowed_provenance = schema.TABLE_PROVENANCE_VALUES[table]
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        assert rows, f"{table}.csv has no data rows"
        for i, row in enumerate(rows, start=2):
            assert row["source"], f"{table}.csv:{i} has empty source"
            assert row["provenance"] in allowed_provenance, (
                f"{table}.csv:{i} provenance {row['provenance']!r} not in {allowed_provenance}"
            )
            known_at = datetime.fromisoformat(row["known_at"])
            ingested_at = datetime.fromisoformat(row["ingested_at"])
            assert known_at <= ingested_at, f"{table}.csv:{i} known_at > ingested_at"
            assert known_at.tzinfo is not None, f"{table}.csv:{i} known_at is not tz-aware"


_TZ_OFFSET_PATTERN = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")
_BARE_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_DATE_COLUMNS = {
    "listings": {"valid_from"},
    "delistings": {"effective_on"},
    "prices_daily": {"session"},
    "corporate_actions": {"ex_date"},
    "facts": {"as_of_date"},
}
_TZ_COLUMNS_COMMON = {"known_at", "ingested_at"}
_EXTRA_TZ_COLUMNS = {"delistings": {"filed_at"}}


def test_timestamp_and_date_cell_formats() -> None:
    for table in _CSV_TABLES:
        path = _FIXTURES_DIR / f"{table}.csv"
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            rows = list(reader)
        tz_columns = _TZ_COLUMNS_COMMON | _EXTRA_TZ_COLUMNS.get(table, set())
        date_columns = _DATE_COLUMNS.get(table, set())
        for i, row in enumerate(rows, start=2):
            for column in tz_columns:
                value = row.get(column) or ""
                assert _TZ_OFFSET_PATTERN.search(value), (
                    f"{table}.csv:{i} column {column} = {value!r} missing UTC offset"
                )
            for column in date_columns:
                value = row.get(column) or ""
                assert _BARE_DATE_PATTERN.fullmatch(value), (
                    f"{table}.csv:{i} column {column} = {value!r} is not a bare date"
                )


@pytest.mark.parametrize(
    "table,unique_cols",
    [
        ("securities", ("security_id", "known_at")),
        ("listings", ("security_id", "ticker", "exchange", "valid_from", "known_at")),
        ("classifications", ("security_id", "rule", "known_at")),
        ("delistings", ("security_id", "form", "class_title", "exchange", "filed_at", "known_at")),
        ("prices_daily", ("security_id", "session", "known_at")),
        ("corporate_actions", ("security_id", "action_type", "ex_date", "known_at")),
        ("facts", ("security_id", "fact_name", "as_of_date", "class_member", "known_at")),
    ],
)
def test_no_unique_key_violations(table: str, unique_cols: tuple[str, ...]) -> None:
    path = _FIXTURES_DIR / f"{table}.csv"
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(row[c] for c in unique_cols)
        assert key not in seen, f"{table}.csv duplicate key {key}"
        seen.add(key)


def test_no_bars_on_the_documented_holiday() -> None:
    with (_FIXTURES_DIR / "prices_daily.csv").open(newline="") as fh:
        reader = csv.DictReader(fh)
        sessions = {row["security_id"]: set() for row in reader}
    with (_FIXTURES_DIR / "prices_daily.csv").open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            sessions[row["security_id"]].add(row["session"])
    # 2018-11-22 is Thanksgiving (an XNYS holiday): no security should have a bar then.
    for security_id, days in sessions.items():
        assert "2018-11-22" not in days, f"{security_id} has a bar on the Thanksgiving holiday"
