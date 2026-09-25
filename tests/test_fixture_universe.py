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
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import duckdb
import pytest
from conftest import load_universe_fixtures
from dateutil.relativedelta import relativedelta

from tradepartner.calendar import is_session, next_session, previous_session, session_close
from tradepartner.config import MasterConfig, UniverseConfig
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


def test_no_security_trades_before_its_earliest_listing() -> None:
    """For every security, the earliest listing `valid_from` must be <= its
    first bar session -- a security cannot have a priced bar before it was
    listed (review round 4, SHOULD FIX 2). No case here is exempted: every
    security's earliest listing is meant to land on or before its first bar."""
    listings = _read_rows("listings")
    bars = _read_rows("prices_daily")

    earliest_listing: dict[str, date] = {}
    for row in listings:
        valid_from = date.fromisoformat(row["valid_from"])
        security_id = row["security_id"]
        if security_id not in earliest_listing or valid_from < earliest_listing[security_id]:
            earliest_listing[security_id] = valid_from

    first_bar: dict[str, date] = {}
    for row in bars:
        session = date.fromisoformat(row["session"])
        security_id = row["security_id"]
        if security_id not in first_bar or session < first_bar[security_id]:
            first_bar[security_id] = session

    for security_id, first_session in first_bar.items():
        assert security_id in earliest_listing, f"{security_id} has bars but no listing"
        assert earliest_listing[security_id] <= first_session, (
            f"{security_id}: earliest listing valid_from {earliest_listing[security_id]} "
            f"is after its first bar session {first_session}"
        )


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


# --- Review round 3, SHOULD FIX 9: assert the timing rules using the -------
# --- calendar directly, not just re-reading the generator's own output. ---


def _read_rows(table: str) -> list[dict[str, str]]:
    with (_FIXTURES_DIR / f"{table}.csv").open(newline="") as fh:
        return list(csv.DictReader(fh))


# The backfilled split's corporate_actions row: its known_at follows the
# ordinary first-seen rule (close before ex-date, asserted in
# test_first_seen_action_known_at_and_revision_rule below like every other
# case), but it was not ingested until years later, in a 2026 backfill run
# -- see test_backfilled_split_known_at_is_close_before_ex_date_ingested_late.
_BACKFILLED_SPLIT_SECURITY_ID = "SEC_SPLIT_BACKFILLED"


def test_every_bar_is_on_a_real_session_with_known_at_matching_session_close() -> None:
    """A bar's `known_at` must equal `calendar.session_close(session)` --
    except a *revision* row (a second row for the same `(security_id,
    session)`), whose `known_at` must instead equal its own `ingested_at`
    and be later than the original close (spec's bars timing rule; review
    round 3 item 9)."""
    rows = _read_rows("prices_daily")
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        session = date.fromisoformat(row["session"])
        assert is_session(session), f"{row['security_id']} has a bar on non-session {session}"
        groups[(row["security_id"], row["session"])].append(row)

    for (security_id, session_str), group in groups.items():
        session = date.fromisoformat(session_str)
        expected_close = session_close(session)
        ordered = sorted(group, key=lambda r: r["known_at"])
        original_known_at = datetime.fromisoformat(ordered[0]["known_at"])
        assert original_known_at == expected_close, (
            f"{security_id} {session_str}: original bar known_at {original_known_at} "
            f"!= session_close {expected_close}"
        )
        for revision in ordered[1:]:
            assert revision["known_at"] == revision["ingested_at"], (
                f"{security_id} {session_str}: revision known_at != ingested_at"
            )
            assert datetime.fromisoformat(revision["known_at"]) > expected_close, (
                f"{security_id} {session_str}: revision known_at is not later than "
                "the original session close"
            )


def test_first_seen_action_known_at_and_revision_rule() -> None:
    """First-seen corporate action: `known_at` <= the close of the session
    before ex-date (an announcement, if present, is always earlier still).
    A revision (a later row for the same key): `known_at == ingested_at`,
    later than the first-seen row (spec req 5; review round 3 item 9)."""
    rows = _read_rows("corporate_actions")
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        groups[(row["security_id"], row["action_type"], row["ex_date"])].append(row)

    for (security_id, action_type, ex_date), group in groups.items():
        close_before_ex = session_close(previous_session(date.fromisoformat(ex_date)))
        ordered = sorted(group, key=lambda r: r["known_at"])
        first_known_at = datetime.fromisoformat(ordered[0]["known_at"])
        assert first_known_at <= close_before_ex, (
            f"{security_id} {action_type} {ex_date}: first-seen known_at {first_known_at} "
            f"is after the close before ex-date {close_before_ex}"
        )
        for revision in ordered[1:]:
            assert revision["known_at"] == revision["ingested_at"], (
                f"{security_id} {action_type} {ex_date}: revision known_at != ingested_at"
            )
            assert datetime.fromisoformat(revision["known_at"]) > first_known_at


def test_backfilled_split_known_at_is_close_before_ex_date_ingested_late() -> None:
    """The T6 acceptance case "backfilled 2018 split in 2026": per spec req
    5, a first-seen action with no announcement has known_at = the close of
    the session before ex-date, *regardless of when it was ingested* -- so
    this split's known_at is in 2018 like any other first-seen action, and
    only its ingested_at (a 2026 backfill run discovering it late) is years
    later. known_at must also be no later than the 2019-01-31 probe close,
    so that probe's adjusted_prices_as_of sees the split (review round 4
    MUST FIX 1; this inverts the previous round's known_at-in-2026
    assertion, which violated req 5)."""
    rows = [
        r
        for r in _read_rows("corporate_actions")
        if r["security_id"] == _BACKFILLED_SPLIT_SECURITY_ID
    ]
    assert len(rows) == 1
    row = rows[0]
    known_at = datetime.fromisoformat(row["known_at"])
    ingested_at = datetime.fromisoformat(row["ingested_at"])
    ex_date = date.fromisoformat(row["ex_date"])
    assert ex_date.year == 2018

    expected_known_at = session_close(previous_session(ex_date))
    assert known_at == expected_known_at

    assert ingested_at.year == 2026
    assert known_at <= ingested_at
    assert known_at < ingested_at, "expected a late (non-instant) ingestion for this row"

    probe_day = date(2019, 1, 31)
    probe_session = probe_day if is_session(probe_day) else next_session(probe_day)
    probe_t = session_close(probe_session)
    assert known_at <= probe_t


def test_transfer_known_at_ordering_and_valid_from_within_window() -> None:
    """The exchange-transfer case: the new listing's known_at must be later
    than the Form 25 filing's known_at, and its valid_from must fall within
    `master.transfer_window_sessions` of the filing date (spec req 4;
    review round 3 item 9)."""
    delistings = [r for r in _read_rows("delistings") if r["security_id"] == "SEC_TRANSFER"]
    new_listings = [
        r
        for r in _read_rows("listings")
        if r["security_id"] == "SEC_TRANSFER" and r["exchange"] == "NASDAQ"
    ]
    assert len(delistings) == 1
    assert len(new_listings) == 1
    filing_known_at = datetime.fromisoformat(delistings[0]["known_at"])
    new_known_at = datetime.fromisoformat(new_listings[0]["known_at"])
    assert new_known_at > filing_known_at

    filed_date = datetime.fromisoformat(delistings[0]["filed_at"]).date()
    valid_from = date.fromisoformat(new_listings[0]["valid_from"])
    sessions_between_filing_and_valid_from = 0
    day = filed_date
    while day < valid_from:
        day = next_session(day)
        sessions_between_filing_and_valid_from += 1
    assert sessions_between_filing_and_valid_from <= MasterConfig().transfer_window_sessions


def _missing_sessions_in_trailing_window(known_sessions: set[date], end: date, months: int) -> int:
    """Sessions missing from `known_sessions` in the trailing window the
    spec's calendar-month-window rule defines: every session `s` with
    `end - relativedelta(months=months) < s <= end`."""
    start_exclusive = end - relativedelta(months=months)
    missing = 0
    day = start_exclusive
    while True:
        day = next_session(day)
        if day > end:
            break
        if day not in known_sessions:
            missing += 1
    return missing


@pytest.mark.parametrize(
    "security_id,probe_date",
    [
        ("SEC_SPLIT_BETWEEN", date(2019, 1, 28)),
        ("SEC_SPLIT_FUTURE", date(2018, 11, 23)),
        ("SEC_DUAL_A", date(2018, 12, 17)),
        ("SEC_DUAL_B", date(2018, 12, 17)),
    ],
)
def test_documented_probe_has_full_history_and_a_fresh_shares_fact(
    security_id: str, probe_date: date
) -> None:
    """Each of these documented probe T's must see >= universe.min_history_
    months of contiguous history and a shares fact known before T and no
    more than universe.max_shares_age_days old (review round 3, MUST FIX 4
    / item 9)."""
    probe_session = probe_date if is_session(probe_date) else next_session(probe_date)
    probe_t = session_close(probe_session)

    bars = [r for r in _read_rows("prices_daily") if r["security_id"] == security_id]
    known_sessions = {
        date.fromisoformat(r["session"])
        for r in bars
        if datetime.fromisoformat(r["known_at"]) <= probe_t
    }
    missing = _missing_sessions_in_trailing_window(
        known_sessions, probe_session, UniverseConfig().min_history_months
    )
    assert missing == 0, f"{security_id}: {missing} missing session(s) in the trailing window"

    facts = [
        r
        for r in _read_rows("facts")
        if r["security_id"] == security_id and datetime.fromisoformat(r["known_at"]) <= probe_t
    ]
    assert facts, f"{security_id}: no shares fact known by probe T {probe_t.isoformat()}"
    freshest = max(facts, key=lambda r: r["as_of_date"])
    age_days = (probe_session - date.fromisoformat(freshest["as_of_date"])).days
    assert age_days <= UniverseConfig().max_shares_age_days, (
        f"{security_id}: freshest shares fact is {age_days} days old at T, "
        f"exceeding max_shares_age_days={UniverseConfig().max_shares_age_days}"
    )


def test_stale_shares_probe_passes_history_but_fails_freshness() -> None:
    """SEC_FACTS_STALE's probe T must see full 12-month history (so rule 6
    passes) while its shares fact is older than max_shares_age_days (so
    only rule 7 fails) -- review round 3 MUST FIX 3."""
    security_id = "SEC_FACTS_STALE"
    facts = [r for r in _read_rows("facts") if r["security_id"] == security_id]
    assert len(facts) == 1
    as_of_date = date.fromisoformat(facts[0]["as_of_date"])
    probe_day = as_of_date + relativedelta(days=UniverseConfig().max_shares_age_days + 30)
    probe_session = probe_day if is_session(probe_day) else next_session(probe_day)
    probe_t = session_close(probe_session)

    bars = [r for r in _read_rows("prices_daily") if r["security_id"] == security_id]
    known_sessions = {
        date.fromisoformat(r["session"])
        for r in bars
        if datetime.fromisoformat(r["known_at"]) <= probe_t
    }
    assert probe_session in known_sessions, "expected a bar at the probe session itself"
    missing = _missing_sessions_in_trailing_window(
        known_sessions, probe_session, UniverseConfig().min_history_months
    )
    assert missing == 0

    age_days = (probe_session - as_of_date).days
    assert age_days > UniverseConfig().max_shares_age_days


def test_boundary_delisting_gap_equals_threshold_exactly() -> None:
    """The boundary delisting's gap between its last bar and the last
    session before its Form 25 filing must equal gap.missing_tail_sessions
    exactly -- not more, not less (review round 3 nit 12)."""
    from tradepartner.config import GapConfig

    security_id = "SEC_BOUNDARY_DELIST"
    bars = [r for r in _read_rows("prices_daily") if r["security_id"] == security_id]
    delistings = [r for r in _read_rows("delistings") if r["security_id"] == security_id]
    assert len(delistings) == 1
    last_bar = max(date.fromisoformat(r["session"]) for r in bars)
    filed_at = datetime.fromisoformat(delistings[0]["filed_at"])
    last_session_before_filing = previous_session(filed_at.date())
    gap = 0
    day = last_bar
    while day < last_session_before_filing:
        day = next_session(day)
        gap += 1
    assert gap == GapConfig().missing_tail_sessions
