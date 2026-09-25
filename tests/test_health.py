"""Tests for `tradepartner.health` (spec req 11 and "Health and page"; plan T18).

Metrics run on the fixture store at the close of 2020-06-30, the fixture's
common end session, where every fixture row is known. Each integrity rule
is shown to fail on one injected violation and to name only itself. Rules
the schema's own constraints already block (NULL `known_at`, duplicates,
provenance outside the table's set) are injected into an unconstrained
copy of the fixture store, the shape a store built by other code, or a
future schema, could have.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest
from lookahead.harness import PROBE_EPSILON, TruncatedStore, probe_timestamps

from tradepartner.calendar import last_session_of_month, session_close
from tradepartner.config import Settings
from tradepartner.gap import survivorship_gap
from tradepartner.health import (
    BARS_ON_SESSIONS,
    GUARDED_SIC_DEFAULT,
    INTEGRITY_RULES,
    KNOWN_AT_NOT_AFTER_INGESTED_AT,
    KNOWN_AT_NOT_NULL,
    NO_BARS_AFTER_DELISTING,
    NO_DUPLICATE_BARS,
    NON_OVERLAPPING_LISTINGS,
    PROVENANCE_ALLOWED,
    SOURCE_NOT_NULL,
    HealthReport,
    bar_gaps,
    coverage,
    delisted_names,
    health_report,
    integrity_checks,
    last_ingests,
    static_reliance,
    unclassifiable,
)
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.schema import TABLE_NAMES, TABLE_PROVENANCE_VALUES

T_END = session_close(date(2020, 6, 30))
#: Close of 2018-10-25: after SEC_TRANSFER's Form 25, before its NASDAQ
#: listing is known (fixture README), so it counts as delisted there.
T_TRANSFER_PROBE = datetime(2018, 10, 25, 20, 0, tzinfo=UTC)

DELISTED_AT_END = (
    "SEC_25NSE",
    "SEC_BOUNDARY_DELIST",
    "SEC_CLEAN_MERGER",
    "SEC_DUAL_PFD",
    "SEC_REUSE_1",
    "SEC_TRUNC_DELIST",
    "SEC_WINDOW_DELIST",
)
LIVE_AT_END = (
    "SEC_DIV_CANCELLED",
    "SEC_DIV_REVISED",
    "SEC_DUAL_A",
    "SEC_DUAL_B",
    "SEC_FACTS_RESTATED",
    "SEC_FACTS_STALE",
    "SEC_MTUM",
    "SEC_REUSE_2",
    "SEC_SPLIT_BACKFILLED",
    "SEC_SPLIT_BETWEEN",
    "SEC_SPLIT_FUTURE",
    "SEC_SPLIT_PLAIN",
    "SEC_SPLIT_REDATED",
    "SEC_SPLIT_REDATED_NOID",
    "SEC_SPY",
    "SEC_STATIC_PRE2019",
    "SEC_TICKCHANGE",
    "SEC_TRANSFER",
)


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.fixture
def settings() -> Settings:
    """Overrides conftest's `settings`: health reads no store path."""
    return _settings()


def _bar(
    sid: str, session: date, *, close: float = 10.0, known: datetime | None = None
) -> dict[str, Any]:
    known = known if known is not None else session_close(session)
    return {
        "security_id": sid,
        "session": session,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000_000,
        "known_at": known,
        "ingested_at": known,
        "source": "fixture",
        "provenance": "bar",
    }


def _unconstrained(source: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    """A copy of `source` whose tables have the same columns and rows but
    none of the schema's `NOT NULL`, `CHECK` or `UNIQUE` constraints."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    for table in TABLE_NAMES:
        arrow_table = source.execute(f"SELECT * FROM {table}").to_arrow_table()
        conn.register("_copy_source", arrow_table)
        try:
            conn.execute(f"CREATE TABLE {table} AS SELECT * FROM _copy_source")
        finally:
            conn.unregister("_copy_source")
    return conn


@pytest.fixture
def loose_store(fixture_store: duckdb.DuckDBPyConnection) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = _unconstrained(fixture_store)
    try:
        yield conn
    finally:
        conn.close()


def _failed(report_checks: Any) -> set[str]:
    return {check.rule for check in report_checks if not check.passed}


# --- The whole report on the fixture store ---------------------------------


def test_fixture_store_passes_every_integrity_rule(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    report = health_report(fixture_store, T_END, settings)
    assert isinstance(report, HealthReport)
    assert [check.rule for check in report.integrity] == list(INTEGRITY_RULES)
    assert report.ok, report.failures
    assert report.failures == ()
    for check in report.integrity:
        assert check.violations.is_empty(), check.rule


def test_report_carries_every_metric(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    report = health_report(fixture_store, T_END, settings)
    assert report.t == T_END
    assert report.session == date(2020, 6, 30)
    assert report.coverage == coverage(fixture_store, T_END, settings)
    assert report.gaps.rows.equals(bar_gaps(fixture_store, T_END).rows)
    assert report.static_reliance == static_reliance(fixture_store, T_END, settings)
    assert report.delisted.frame.equals(delisted_names(fixture_store, T_END, settings).frame)
    assert report.unclassifiable == unclassifiable(fixture_store, T_END)
    assert report.ingests == last_ingests(fixture_store, T_END)
    expected_gap = survivorship_gap(fixture_store, T_END, settings)
    assert report.survivorship.listed == expected_gap.listed
    assert report.survivorship.missing.equals(expected_gap.missing)
    assert report.survivorship.count_share == expected_gap.count_share
    assert report.survivorship.size_share == expected_gap.size_share
    assert report.survivorship.unclassifiable == expected_gap.unclassifiable
    assert report.survivorship.truncated_history == expected_gap.truncated_history
    assert report.survivorship.stale_shares == expected_gap.stale_shares
    assert report.settings == {"liquidity_rule_enabled": True, "fill_price": "close"}


def test_report_shows_liquidity_and_fill_price_settings(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    settings = _settings(
        universe={"liquidity_rule_enabled": False}, execution={"fill_price": "open"}
    )
    report = health_report(fixture_store, T_END, settings)
    assert report.settings == {"liquidity_rule_enabled": False, "fill_price": "open"}


def test_bare_date_and_naive_datetime_are_refused(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    with pytest.raises(TypeError):
        health_report(fixture_store, date(2020, 6, 30), settings)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        health_report(fixture_store, datetime(2020, 6, 30, 20), settings)  # noqa: DTZ001


# --- Last successful ingest per source -------------------------------------


def _run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    source: str,
    status: str,
    started: datetime,
    *,
    cursor: str | None = None,
    message: str | None = None,
) -> None:
    insert_row(
        conn,
        "ingestion_runs",
        {
            "run_id": run_id,
            "started_at": started,
            "finished_at": started + timedelta(minutes=5),
            "status": status,
            "source": source,
            "mode": "daily",
            "rows_added": 0,
            "chunk_cursor": cursor,
            "message": message,
        },
    )


def test_last_ingests_with_no_runs_lists_every_source_as_never(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    ingests = last_ingests(fixture_store, T_END)
    assert [i.source for i in ingests] == ["edgar", "alpaca"]
    for status in ingests:
        assert status.last_ok_finished_at is None
        assert status.latest_status is None


def test_last_ingests_reports_last_ok_and_latest_run_per_source(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    day1 = datetime(2020, 6, 29, 22, 0, tzinfo=UTC)
    day2 = datetime(2020, 6, 30, 22, 0, tzinfo=UTC)
    _run(fixture_store, "e1", "edgar", "ok", day1, cursor="2020-06-29")
    _run(fixture_store, "e2", "edgar", "ok", day2, cursor="2020-06-30")
    _run(fixture_store, "a1", "alpaca", "ok", day1, cursor="2020-06-29")
    _run(fixture_store, "a2", "alpaca", "stale", day2, message="SPY missing")
    # A run started after T is not part of the picture at T.
    _run(fixture_store, "a3", "alpaca", "ok", day2 + timedelta(days=1))

    at = day2 + timedelta(hours=1)
    edgar, alpaca = last_ingests(fixture_store, at)
    assert edgar.source == "edgar"
    assert edgar.last_ok_finished_at == day2 + timedelta(minutes=5)
    assert edgar.last_ok_cursor == "2020-06-30"
    assert edgar.latest_status == "ok"
    assert alpaca.source == "alpaca"
    assert alpaca.last_ok_finished_at == day1 + timedelta(minutes=5)
    assert alpaca.last_ok_cursor == "2020-06-29"
    assert alpaca.latest_status == "stale"
    assert alpaca.latest_message == "SPY missing"


def test_last_ingests_ignores_a_run_not_finished_at_t(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # A run row is written when the run ends, so it is known at finished_at.
    day1 = datetime(2020, 6, 29, 22, 0, tzinfo=UTC)
    day2 = datetime(2020, 6, 30, 22, 0, tzinfo=UTC)
    _run(fixture_store, "a1", "alpaca", "ok", day1, cursor="2020-06-29")
    _run(fixture_store, "a2", "alpaca", "failed", day2, message="boom")
    _, alpaca = last_ingests(fixture_store, day2 + timedelta(minutes=2))
    assert alpaca.latest_status == "ok"
    assert alpaca.last_ok_cursor == "2020-06-29"
    _, alpaca = last_ingests(fixture_store, day2 + timedelta(minutes=5))
    assert alpaca.latest_status == "failed"


def test_last_ingests_keeps_an_unknown_source_after_the_known_ones(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _run(fixture_store, "x1", "vendor_x", "ok", datetime(2020, 6, 29, 22, tzinfo=UTC))
    sources = [i.source for i in last_ingests(fixture_store, T_END)]
    assert sources == ["edgar", "alpaca", "vendor_x"]


# --- Coverage ---------------------------------------------------------------


def test_coverage_on_fixture_store_is_complete(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    cov = coverage(fixture_store, T_END, settings)
    assert cov.session == date(2020, 6, 30)
    assert cov.live == LIVE_AT_END
    assert cov.missing == ()
    assert cov.share == 1.0
    assert cov.first_bar == date(2017, 1, 3)
    assert cov.last_bar == date(2020, 6, 30)
    assert cov.names_with_bars == 26


def test_coverage_names_a_live_name_without_a_bar_at_the_session(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    fixture_store.execute(
        "DELETE FROM prices_daily WHERE security_id = 'SEC_DUAL_B' AND session = ?",
        [date(2020, 6, 30)],
    )
    cov = coverage(fixture_store, T_END, settings)
    assert cov.missing == ("SEC_DUAL_B",)
    assert cov.share == pytest.approx((len(LIVE_AT_END) - 1) / len(LIVE_AT_END))


def test_coverage_counts_common_and_benchmark_names_only(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    # Same population as ingest's staleness check: a live unclassifiable
    # name with no bar is not a coverage miss.
    fixture_store.execute(
        "DELETE FROM prices_daily WHERE security_id = 'SEC_UNCLASSIFIABLE' AND session = ?",
        [date(2020, 6, 30)],
    )
    cov = coverage(fixture_store, T_END, settings)
    assert "SEC_UNCLASSIFIABLE" not in cov.live
    assert cov.missing == ()


def test_coverage_ignores_bars_known_after_t(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    t = session_close(date(2020, 6, 29))
    cov = coverage(fixture_store, t, settings)
    assert cov.session == date(2020, 6, 29)
    assert cov.last_bar == date(2020, 6, 29)
    assert cov.missing == ()


# --- Gaps -------------------------------------------------------------------


def test_fixture_store_has_no_interior_bar_gaps(fixture_store: duckdb.DuckDBPyConnection) -> None:
    gaps = bar_gaps(fixture_store, T_END)
    assert gaps.rows.is_empty()
    assert gaps.names_with_gaps == 0
    assert gaps.missing_sessions == 0


def test_bar_gaps_count_interior_holes_and_the_longest_run(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # SPLT: three consecutive sessions; SPBT: one session. The holiday
    # 2018-11-22 inside SPFT's range is not a session, so not a gap.
    for sid, sessions in {
        "SEC_SPLIT_PLAIN": [date(2019, 3, 5), date(2019, 3, 6), date(2019, 3, 7)],
        "SEC_SPLIT_BETWEEN": [date(2019, 5, 1)],
    }.items():
        for session in sessions:
            fixture_store.execute(
                "DELETE FROM prices_daily WHERE security_id = ? AND session = ?", [sid, session]
            )
    gaps = bar_gaps(fixture_store, T_END)
    assert gaps.names_with_gaps == 2
    assert gaps.missing_sessions == 4
    rows = {r["security_id"]: r for r in gaps.rows.iter_rows(named=True)}
    assert rows["SEC_SPLIT_PLAIN"]["missing_sessions"] == 3
    assert rows["SEC_SPLIT_PLAIN"]["longest_run"] == 3
    assert rows["SEC_SPLIT_PLAIN"]["first_bar"] == date(2018, 9, 4)
    assert rows["SEC_SPLIT_PLAIN"]["last_bar"] == date(2020, 6, 30)
    assert rows["SEC_SPLIT_BETWEEN"]["missing_sessions"] == 1
    assert rows["SEC_SPLIT_BETWEEN"]["longest_run"] == 1


def test_bar_gaps_ignore_bars_after_t(fixture_store: duckdb.DuckDBPyConnection) -> None:
    # A hole whose later edge is only known after T is a trailing end, not a gap.
    fixture_store.execute(
        "DELETE FROM prices_daily WHERE security_id = 'SEC_SPLIT_PLAIN' AND session = ?",
        [date(2019, 3, 5)],
    )
    assert bar_gaps(fixture_store, session_close(date(2019, 3, 5))).rows.is_empty()
    assert bar_gaps(fixture_store, session_close(date(2019, 3, 6))).names_with_gaps == 1


# --- Unclassifiable ---------------------------------------------------------


def test_unclassifiable_count(fixture_store: duckdb.DuckDBPyConnection) -> None:
    result = unclassifiable(fixture_store, T_END)
    assert result.unclassifiable == ("SEC_UNCLASSIFIABLE",)
    assert result.unclassified == ()
    assert result.count == 1


def test_security_with_no_classification_counts_as_unclassified(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    fixture_store.execute("DELETE FROM classifications WHERE security_id = 'SEC_DUAL_B'")
    result = unclassifiable(fixture_store, T_END)
    assert result.unclassified == ("SEC_DUAL_B",)
    assert result.count == 2


# --- snapshot_static reliance -----------------------------------------------


def test_static_reliance_at_the_latest_t(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    reliance = static_reliance(fixture_store, T_END, settings)
    assert reliance.ids == ("SEC_MTUM", "SEC_SPY", "SEC_STATIC_PRE2019")
    assert reliance.count == 3
    assert reliance.by_table == {"securities": 2, "listings": 3, "classifications": 2}


def test_static_listing_counts_only_from_its_known_at(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    # PRE9's snapshot_static listing is known 2020-01-15 (#35): before
    # that, nothing about it rests on a static row.
    before = session_close(date(2019, 12, 31))
    assert "SEC_STATIC_PRE2019" not in static_reliance(fixture_store, before, settings).ids


# --- Delisted names -----------------------------------------------------------


def test_delisted_names_count_and_list(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    delisted = delisted_names(fixture_store, T_END, settings)
    assert delisted.count == len(DELISTED_AT_END)
    assert tuple(delisted.frame["security_id"]) == DELISTED_AT_END
    row = delisted.frame.filter(pl.col("security_id") == "SEC_25NSE").row(0, named=True)
    assert row["ticker"] == "NSEX"
    assert row["form"] == "25-NSE"
    assert row["end_session"] == date(2018, 8, 22)
    assert row["effective_on"] == date(2018, 9, 2)
    # A transfer is not a delisting once the new listing is known.
    assert "SEC_TRANSFER" not in delisted.frame["security_id"].to_list()


def test_transfer_counts_as_delisted_before_the_new_listing_is_known(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    ids = delisted_names(fixture_store, T_TRANSFER_PROBE, settings).frame["security_id"]
    assert "SEC_TRANSFER" in ids.to_list()


# --- Integrity rules, one injected violation each ----------------------------


def test_null_known_at_fails_its_rule(loose_store: duckdb.DuckDBPyConnection) -> None:
    loose_store.execute("UPDATE facts SET known_at = NULL WHERE security_id = 'SEC_FACTS_RESTATED'")
    checks = integrity_checks(loose_store, T_END, _settings())
    assert _failed(checks) == {KNOWN_AT_NOT_NULL}
    check = next(c for c in checks if c.rule == KNOWN_AT_NOT_NULL)
    assert check.violations.to_dicts() == [{"table": "facts", "rows": 2}]


def test_known_at_after_ingested_at_fails_its_rule(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    loose = _unconstrained(fixture_store)
    loose.execute(
        "UPDATE corporate_actions SET ingested_at = known_at - INTERVAL 1 DAY "
        "WHERE security_id = 'SEC_SPLIT_PLAIN'"
    )
    checks = integrity_checks(loose, T_END, _settings())
    assert _failed(checks) == {KNOWN_AT_NOT_AFTER_INGESTED_AT}
    check = next(c for c in checks if c.rule == KNOWN_AT_NOT_AFTER_INGESTED_AT)
    assert check.violations.to_dicts() == [{"table": "corporate_actions", "rows": 1}]


def test_null_source_fails_its_rule(loose_store: duckdb.DuckDBPyConnection) -> None:
    loose_store.execute("UPDATE listings SET source = NULL WHERE security_id = 'SEC_DUAL_A'")
    checks = integrity_checks(loose_store, T_END, _settings())
    assert _failed(checks) == {SOURCE_NOT_NULL}


def test_provenance_outside_the_tables_set_fails_its_rule(
    loose_store: duckdb.DuckDBPyConnection,
) -> None:
    loose_store.execute(
        "UPDATE prices_daily SET provenance = 'filing' "
        "WHERE security_id = 'SEC_DUAL_A' AND session = '2019-01-02'"
    )
    checks = integrity_checks(loose_store, T_END, _settings())
    assert _failed(checks) == {PROVENANCE_ALLOWED}
    check = next(c for c in checks if c.rule == PROVENANCE_ALLOWED)
    assert check.violations.to_dicts() == [
        {"table": "prices_daily", "provenance": "filing", "rows": 1}
    ]


@pytest.mark.parametrize("day", [date(2018, 11, 22), date(2019, 3, 9)], ids=["holiday", "weekend"])
def test_bar_on_a_non_session_fails_its_rule(
    fixture_store: duckdb.DuckDBPyConnection, day: date
) -> None:
    known = datetime(day.year, day.month, day.day, 21, 0, tzinfo=UTC)
    insert_row(fixture_store, "prices_daily", _bar("SEC_DUAL_A", day, known=known))
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == {BARS_ON_SESSIONS}
    check = next(c for c in checks if c.rule == BARS_ON_SESSIONS)
    assert check.violations.to_dicts() == [{"session": day, "rows": 1}]


def test_duplicate_bar_fails_its_rule(loose_store: duckdb.DuckDBPyConnection) -> None:
    loose_store.execute(
        "INSERT INTO prices_daily SELECT * FROM prices_daily "
        "WHERE security_id = 'SEC_DUAL_A' AND session = '2019-01-02'"
    )
    checks = integrity_checks(loose_store, T_END, _settings())
    assert _failed(checks) == {NO_DUPLICATE_BARS}
    check = next(c for c in checks if c.rule == NO_DUPLICATE_BARS)
    [row] = check.violations.to_dicts()
    assert (row["security_id"], row["session"], row["rows"]) == ("SEC_DUAL_A", date(2019, 1, 2), 2)


def test_overlapping_listing_fails_its_rule(fixture_store: duckdb.DuckDBPyConnection) -> None:
    # A second SEC_DUAL_A listing from the same session on another exchange.
    known = datetime(2017, 1, 3, 21, 0, tzinfo=UTC)
    insert_row(
        fixture_store,
        "listings",
        {
            "security_id": "SEC_DUAL_A",
            "ticker": "DUALA",
            "exchange": "NASDAQ",
            "class_title": "Class A Common Stock",
            "valid_from": date(2017, 1, 3),
            "known_at": known,
            "ingested_at": known,
            "source": "fixture",
            "provenance": "filing",
        },
    )
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == {NON_OVERLAPPING_LISTINGS}
    check = next(c for c in checks if c.rule == NON_OVERLAPPING_LISTINGS)
    assert check.violations["security_id"].to_list() == ["SEC_DUAL_A"]


def test_listing_live_after_the_next_one_started_fails_its_rule(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # BKFL lists on NASDAQ from 2018-06-01 while its NYSE line runs on
    # until a Form 25 filed 2019-01-10: seven months of two live listings,
    # far outside `master.transfer_window_sessions`. The derived end of
    # the NYSE line stops before the NASDAQ start, so only the filing
    # session shows the overlap.
    listed = datetime(2018, 6, 1, 21, 0, tzinfo=UTC)
    insert_row(
        fixture_store,
        "listings",
        {
            "security_id": "SEC_SPLIT_BACKFILLED",
            "ticker": "BKFL",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": date(2018, 6, 1),
            "known_at": listed,
            "ingested_at": listed,
            "source": "fixture",
            "provenance": "filing",
        },
    )
    filed = datetime(2019, 1, 10, 21, 0, tzinfo=UTC)
    insert_row(
        fixture_store,
        "delistings",
        {
            "security_id": "SEC_SPLIT_BACKFILLED",
            "form": "25",
            "class_title": "Common Stock",
            "exchange": "NYSE",
            "filed_at": filed,
            "effective_on": date(2019, 1, 20),
            "known_at": filed,
            "ingested_at": filed,
            "source": "fixture",
            "provenance": "filing",
        },
    )
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == {NON_OVERLAPPING_LISTINGS}
    check = next(c for c in checks if c.rule == NON_OVERLAPPING_LISTINGS)
    [row] = check.violations.to_dicts()
    assert row["security_id"] == "SEC_SPLIT_BACKFILLED"
    assert (row["exchange"], row["next_exchange"]) == ("NYSE", "NASDAQ")
    assert row["filing_session"] == date(2019, 1, 10)
    assert row["next_valid_from"] == date(2018, 6, 1)


def test_ticker_change_and_transfer_are_not_overlaps(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    checks = integrity_checks(fixture_store, T_END, _settings())
    check = next(c for c in checks if c.rule == NON_OVERLAPPING_LISTINGS)
    assert check.passed


def test_bar_after_a_delisting_takes_effect_fails_its_rule(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # TRHX: Form 25 filed 2018-06-14, effective 2018-06-24.
    insert_row(fixture_store, "prices_daily", _bar("SEC_TRUNC_DELIST", date(2018, 7, 2)))
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == {NO_BARS_AFTER_DELISTING}
    check = next(c for c in checks if c.rule == NO_BARS_AFTER_DELISTING)
    [row] = check.violations.to_dicts()
    assert row["security_id"] == "SEC_TRUNC_DELIST"
    assert row["session"] == date(2018, 7, 2)
    assert row["effective_on"] == date(2018, 6, 24)


def test_bar_between_filing_and_effective_date_is_allowed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    insert_row(fixture_store, "prices_daily", _bar("SEC_TRUNC_DELIST", date(2018, 6, 20)))
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == set()


def test_bar_on_a_later_listing_of_the_same_security_is_allowed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # TRHX relists on NASDAQ in 2019; its bars there are not after its
    # 2018 delisting's end.
    known = datetime(2019, 3, 1, 21, 0, tzinfo=UTC)
    insert_row(
        fixture_store,
        "listings",
        {
            "security_id": "SEC_TRUNC_DELIST",
            "ticker": "TRHX",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": date(2019, 3, 1),
            "known_at": known,
            "ingested_at": known,
            "source": "fixture",
            "provenance": "filing",
        },
    )
    insert_row(fixture_store, "prices_daily", _bar("SEC_TRUNC_DELIST", date(2019, 3, 4)))
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == set()


def test_changed_guarded_sic_default_fails_its_rule(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # The config validator refuses the change; a copy with the guard
    # bypassed stands for a store read with tampered settings.
    settings = _settings()
    tampered = settings.model_copy(
        update={
            "universe": settings.universe.model_copy(update={"exclude_sic_ranges": ((4900, 4949),)})
        }
    )
    report = health_report(fixture_store, T_END, tampered)
    assert not report.ok
    assert report.failures == (GUARDED_SIC_DEFAULT,)


def test_report_fails_naming_each_injected_rule(fixture_store: duckdb.DuckDBPyConnection) -> None:
    insert_row(fixture_store, "prices_daily", _bar("SEC_TRUNC_DELIST", date(2018, 7, 2)))
    report = health_report(fixture_store, T_END, _settings())
    assert not report.ok
    assert report.failures == (NO_BARS_AFTER_DELISTING,)


# --- No look-ahead ------------------------------------------------------------


@pytest.fixture
def truncated(fixture_store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    store = TruncatedStore(fixture_store)
    try:
        yield store
    finally:
        store.close()


def _probes(conn: duckdb.DuckDBPyConnection) -> list[datetime]:
    """Probes around every `known_at` outside `prices_daily`, and around the
    bar `known_at`s that matter to these metrics: month-end closes and
    revisions (a bar known later than its session's close). The other
    1,700-odd bar closes differ from their neighbours by one ordinary bar."""
    tables = [table for table in TABLE_PROVENANCE_VALUES if table != "prices_daily"]
    probes = set(probe_timestamps(conn, tables))
    bars = conn.execute("SELECT DISTINCT session, known_at FROM prices_daily").fetchall()
    for session, known_at in bars:
        month_end = session == last_session_of_month(session.year, session.month)
        if month_end or known_at != session_close(session):
            probes.update((known_at - PROBE_EPSILON, known_at + PROBE_EPSILON))
    return sorted(probes)


def test_as_of_metrics_invariant_under_truncation(
    fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore, settings: Settings
) -> None:
    """Every metric read as of T equals the same metric on the store cut
    to `known_at <= T`, at the probes of `_probes` (the survivorship gap
    has its own suite over every probe in `tests/lookahead/`)."""
    for t in _probes(fixture_store):
        cut = truncated.at(t)
        assert coverage(fixture_store, t, settings) == coverage(cut, t, settings), t
        assert bar_gaps(fixture_store, t).rows.equals(bar_gaps(cut, t).rows), t
        assert static_reliance(fixture_store, t, settings) == static_reliance(cut, t, settings), t
        full_delisted = delisted_names(fixture_store, t, settings).frame
        assert full_delisted.equals(delisted_names(cut, t, settings).frame), t
        assert unclassifiable(fixture_store, t) == unclassifiable(cut, t), t
