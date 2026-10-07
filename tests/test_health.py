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
from pydantic import ValidationError

from tradepartner.calendar import all_sessions, last_session_of_month, session_close
from tradepartner.config import Settings, parse_accepted_same_day_pair
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
    STATEMENT_BASIS_ALLOWED,
    STATEMENT_DERIVED_MATCHES_COMPONENTS,
    STATEMENT_PERIOD_DAYS,
    HealthReport,
    bar_gaps,
    coverage,
    delisted_names,
    health_report,
    integrity_checks,
    last_ingests,
    statement_counts,
    statement_coverage,
    static_reliance,
    unclassifiable,
)
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.schema import TABLE_NAMES, TABLE_PROVENANCE_VALUES

T_END = session_close(date(2020, 6, 30))
#: Close of 2018-10-25: after SEC_TRANSFER's Form 25, before its NASDAQ
#: listing is known (fixture README), so it counts as delisted there.
T_TRANSFER_PROBE = datetime(2018, 10, 25, 20, 0, tzinfo=UTC)
#: Close of 2018-12-17 (test_universe.py's `T_DUAL`): `universe_as_of` admits
#: SEC_DUAL_A and SEC_DUAL_B (CIK0001000006, cap summed over both classes)
#: among 5 members; neither statement fact below is known yet by any other
#: fixture row, so inserting one directly is the only way to put a fresh
#: fact under a real universe member without picking a T that also drags in
#: unrelated exclusions.
T_DUAL = datetime(2018, 12, 17, 21, 0, tzinfo=UTC)
DUAL_CIK = "CIK0001000006"
#: Reported `gross_profit` rows the fixture knows at T_END: the profitability
#: baseline (backtest plan T85c), one per issuer and fiscal year whose issuer
#: has a securities row by then (fixture README, "Statement facts").
REPORTED_GROSS_PROFIT_AT_END = 105

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


def _statement_fact(
    cik: str,
    fact_name: str,
    *,
    period_end: date,
    period_days: int = 364,
    value: float = 100.0,
    accession: str = "ACC",
    basis: str = "reported",
    known: datetime,
) -> dict[str, Any]:
    """A `statement_facts` row, the common test shape (period_start `None`
    exactly when `period_days = 0`, as the schema's own `CHECK` requires)."""
    period_start = None if period_days == 0 else period_end - timedelta(days=period_days)
    return {
        "cik": cik,
        "fact_name": fact_name,
        "xbrl_tag": f"us-gaap:{fact_name}",
        "period_start": period_start,
        "period_end": period_end,
        "period_days": period_days,
        "value": value,
        "unit": "USD",
        "form": "10-K",
        "filing_accession": accession,
        "basis": basis,
        "comparative": False,
        "known_at": known,
        "ingested_at": known,
        "source": "edgar",
        "provenance": "filing",
    }


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


def test_health_report_on_a_version_8_store_does_not_crash_on_the_missing_table(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """A read-only connection never migrates (`store.schema.init_schema`), so
    the owner's store sits at version 8 -- no `statement_facts` table yet --
    from the moment this PR (#660/T76) merges until the next `tradepartner
    ingest` run. `health._count_per_table`/`_bad_provenance` loop over every
    `TABLE_PROVENANCE_VALUES` table, `statement_facts` now included; before
    the fix in this PR, that raised `duckdb.CatalogException` on exactly this
    shape instead of reporting normally (code-review of PR #729)."""
    fixture_store.execute("DELETE FROM schema_version WHERE version = 9")
    fixture_store.execute("DROP TABLE statement_facts")
    report = health_report(fixture_store, T_END, settings)
    assert isinstance(report, HealthReport)
    assert [check.rule for check in report.integrity] == list(INTEGRITY_RULES)
    assert report.ok, report.failures


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
    assert cov.share == pytest.approx(17 / 18)


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
    # A second SEC_DUAL_A listing from the same session on another exchange
    # under another ticker: two lines, not one line tagged twice (#822).
    known = datetime(2017, 1, 3, 21, 0, tzinfo=UTC)
    insert_row(
        fixture_store,
        "listings",
        {
            "security_id": "SEC_DUAL_A",
            "ticker": "DUALX",
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


def test_same_day_typo_pair_beside_the_held_ticker_is_not_an_overlap(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # #846: a cover-page typo beside the ticker the security already held
    # (#819, #830) is filer noise, not a genuine overlap. SEC_DUAL_A
    # already holds DUALA (fixture listing from 2017-01-03); a later
    # cover page naming DUALX beside it, the same day, is the typo and
    # DUALA is kept -- `health` must apply the resolver's own rule
    # (`is_same_day_typo`, shared, never copied) so the two can't drift.
    known = datetime(2019, 6, 3, 21, 0, tzinfo=UTC)
    for ticker in ("DUALA", "DUALX"):
        insert_row(
            fixture_store,
            "listings",
            {
                "security_id": "SEC_DUAL_A",
                "ticker": ticker,
                "exchange": "NYSE",
                "class_title": "Class A Common Stock",
                "valid_from": date(2019, 6, 3),
                "known_at": known,
                "ingested_at": known,
                "source": "fixture",
                "provenance": "filing",
            },
        )
    checks = integrity_checks(fixture_store, T_END, _settings())
    check = next(c for c in checks if c.rule == NON_OVERLAPPING_LISTINGS)
    assert check.passed, check.violations


def test_same_day_pair_with_no_held_ticker_match_still_fails_its_rule(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # Control for the tolerance above: a same-start pair where neither
    # ticker is the one the security held before, and the two are not one
    # Alpaca symbol under different spellings, is still a genuine overlap
    # (spec req 11, amendment #822: "Same-start rows of different tickers
    # still fail").
    known = datetime(2019, 6, 3, 21, 0, tzinfo=UTC)
    for ticker in ("DUALY", "DUALZ"):
        insert_row(
            fixture_store,
            "listings",
            {
                "security_id": "SEC_DUAL_A",
                "ticker": ticker,
                "exchange": "NYSE",
                "class_title": "Class A Common Stock",
                "valid_from": date(2019, 6, 3),
                "known_at": known,
                "ingested_at": known,
                "source": "fixture",
                "provenance": "filing",
            },
        )
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == {NON_OVERLAPPING_LISTINGS}


def test_a_held_ticker_from_a_non_equity_row_does_not_excuse_an_equity_pair(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # Regression (code-review on #846): the resolver's own rule reads the
    # held ticker off the row just before only when that row is itself an
    # equity row (`ListingResolver._drop_same_day_typos`); a warrant row
    # naming XYZ does not excuse a later XYZ/XYZQ equity pair the way an
    # equity row would -- health must not disagree with the resolver here.
    for ticker, class_title, valid_from in (
        ("XYZ", "Warrants to purchase Common Stock", date(2018, 1, 2)),
        ("XYZ", "Common Stock", date(2019, 6, 3)),
        ("XYZQ", "Common Stock", date(2019, 6, 3)),
    ):
        known = session_close(valid_from)
        insert_row(
            fixture_store,
            "listings",
            {
                "security_id": "SEC_MIXED_KIND",
                "ticker": ticker,
                "exchange": "NASDAQ",
                "class_title": class_title,
                "valid_from": valid_from,
                "known_at": known,
                "ingested_at": known,
                "source": "fixture",
                "provenance": "filing",
            },
        )
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == {NON_OVERLAPPING_LISTINGS}
    check = next(c for c in checks if c.rule == NON_OVERLAPPING_LISTINGS)
    assert "SEC_MIXED_KIND" in check.violations["security_id"].to_list()


def test_a_held_non_equity_ticker_does_not_excuse_a_mixed_kind_pair(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # Regression (quant-auditor pass 2 on #846): the gate must trigger
    # when EITHER row of a same-start pair is equity, not only the one
    # that happens to sort first alphabetically. WARR (warrant) continues
    # its own non-equity ticker from before; ZNEW is a brand-new equity
    # ticker the same day. WARR sorts before ZNEW, so gating on "current"
    # alone (the pre-fix code) let WARR's non-equity history wrongly
    # excuse ZNEW, an equity row the resolver's EQUITY-only path would
    # never treat as a typo of a warrant's ticker.
    known_before = session_close(date(2018, 1, 2))
    insert_row(
        fixture_store,
        "listings",
        {
            "security_id": "SEC_MIXED_PAIR",
            "ticker": "WARR",
            "exchange": "NASDAQ",
            "class_title": "Warrants to purchase Common Stock",
            "valid_from": date(2018, 1, 2),
            "known_at": known_before,
            "ingested_at": known_before,
            "source": "fixture",
            "provenance": "filing",
        },
    )
    known_pair = session_close(date(2019, 6, 3))
    for ticker, class_title in (
        ("WARR", "Warrants to purchase Common Stock"),
        ("ZNEW", "Common Stock"),
    ):
        insert_row(
            fixture_store,
            "listings",
            {
                "security_id": "SEC_MIXED_PAIR",
                "ticker": ticker,
                "exchange": "NASDAQ",
                "class_title": class_title,
                "valid_from": date(2019, 6, 3),
                "known_at": known_pair,
                "ingested_at": known_pair,
                "source": "fixture",
                "provenance": "filing",
            },
        )
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == {NON_OVERLAPPING_LISTINGS}
    check = next(c for c in checks if c.rule == NON_OVERLAPPING_LISTINGS)
    assert "SEC_MIXED_PAIR" in check.violations["security_id"].to_list()


def test_held_ticker_matches_through_a_class_suffix_spelling(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # Regression (code-review on #846): `ListingResolver` folds every
    # ticker through `alpaca_symbol` (BF-A -> BF.A) before comparing the
    # held ticker against a same-day pair; health must fold the same way,
    # not compare raw spellings, or a held `BF-A` would not excuse a
    # `BF.A`/`BF.B` pair the resolver already treats as the held ticker.
    known_before = session_close(date(2018, 1, 2))
    insert_row(
        fixture_store,
        "listings",
        {
            "security_id": "SEC_SUFFIX_FOLD",
            "ticker": "BF-A",
            "exchange": "NYSE",
            "class_title": "Class A Common Stock",
            "valid_from": date(2018, 1, 2),
            "known_at": known_before,
            "ingested_at": known_before,
            "source": "fixture",
            "provenance": "filing",
        },
    )
    known_pair = session_close(date(2019, 6, 3))
    for ticker in ("BF.A", "BF.B"):
        insert_row(
            fixture_store,
            "listings",
            {
                "security_id": "SEC_SUFFIX_FOLD",
                "ticker": ticker,
                "exchange": "NYSE",
                "class_title": "Class A Common Stock",
                "valid_from": date(2019, 6, 3),
                "known_at": known_pair,
                "ingested_at": known_pair,
                "source": "fixture",
                "provenance": "filing",
            },
        )
    checks = integrity_checks(fixture_store, T_END, _settings())
    check = next(c for c in checks if c.rule == NON_OVERLAPPING_LISTINGS)
    assert "SEC_SUFFIX_FOLD" not in check.violations["security_id"].to_list()


def test_same_day_pair_is_one_alpaca_symbol_is_not_an_overlap(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # #846: MOTV U / MOTV.U is one Alpaca symbol under two filer spellings
    # for a unit's one-letter suffix; `alpaca_symbol` already folds `-`
    # and `/` the same way for a class suffix (CRD-A / CRD.A), so a space
    # is folded too (`same_alpaca_symbol`), without a resolver change.
    known = datetime(2021, 12, 15, 21, 0, tzinfo=UTC)
    for ticker in ("MOTV U", "MOTV.U"):
        insert_row(
            fixture_store,
            "listings",
            {
                "security_id": "SEC_NEW_UNITS",
                "ticker": ticker,
                "exchange": "NASDAQ",
                "class_title": "Units, each consisting of one share and one warrant",
                "valid_from": date(2021, 12, 15),
                "known_at": known,
                "ingested_at": known,
                "source": "fixture",
                "provenance": "filing",
            },
        )
    checks = integrity_checks(fixture_store, T_END, _settings())
    check = next(c for c in checks if c.rule == NON_OVERLAPPING_LISTINGS)
    assert check.passed, check.violations


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


def _listing_row(
    sid: str, ticker: str, exchange: str, valid_from: date, class_title: str = "Common Stock"
) -> dict[str, Any]:
    known = session_close(valid_from)
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": exchange,
        "class_title": class_title,
        "valid_from": valid_from,
        "known_at": known,
        "ingested_at": known,
        "source": "fixture",
        "provenance": "filing",
    }


def _form25_row(sid: str, exchange: str, filed_on: date, effective_on: date) -> dict[str, Any]:
    filed = session_close(filed_on)
    return {
        "security_id": sid,
        "form": "25",
        "class_title": "Common Stock",
        "exchange": exchange,
        "filed_at": filed,
        "effective_on": effective_on,
        "known_at": filed,
        "ingested_at": filed,
        "source": "fixture",
        "provenance": "filing",
    }


@pytest.mark.parametrize("successor_exchange", ["NONE", "OTC"])
def test_late_form25_after_a_move_off_exchange_is_not_an_overlap(
    fixture_store: duckdb.DuckDBPyConnection, successor_exchange: str
) -> None:
    # SCON/BPTH (#822): suspended, quoted OTC (a NONE or OTC row) from
    # 2018-06-01, Form 25 filed months later. No second exchange line.
    insert_row(
        fixture_store,
        "listings",
        _listing_row("SEC_SPLIT_BACKFILLED", "BKFL", successor_exchange, date(2018, 6, 1)),
    )
    insert_row(
        fixture_store,
        "delistings",
        _form25_row("SEC_SPLIT_BACKFILLED", "NYSE", date(2019, 1, 10), date(2019, 1, 20)),
    )
    checks = integrity_checks(fixture_store, T_END, _settings())
    check = next(c for c in checks if c.rule == NON_OVERLAPPING_LISTINGS)
    assert check.passed, check.violations


def test_late_form25_is_checked_against_the_next_exchange_line_past_an_otc_row(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # An OTC row between the NYSE line and a NASDAQ line does not hide the
    # NYSE line still being live seven months into the NASDAQ one.
    insert_row(
        fixture_store,
        "listings",
        _listing_row("SEC_SPLIT_BACKFILLED", "BKFL", "OTC", date(2018, 3, 1)),
    )
    insert_row(
        fixture_store,
        "listings",
        _listing_row("SEC_SPLIT_BACKFILLED", "BKFL", "NASDAQ", date(2018, 6, 1)),
    )
    insert_row(
        fixture_store,
        "delistings",
        _form25_row("SEC_SPLIT_BACKFILLED", "NYSE", date(2019, 1, 10), date(2019, 1, 20)),
    )
    checks = integrity_checks(fixture_store, T_END, _settings())
    check = next(c for c in checks if c.rule == NON_OVERLAPPING_LISTINGS)
    [row] = check.violations.to_dicts()
    assert (row["exchange"], row["next_exchange"]) == ("NYSE", "NASDAQ")
    assert row["next_valid_from"] == date(2018, 6, 1)


@pytest.mark.parametrize("late_form25", [False, True])
@pytest.mark.parametrize("class_title", ["Class A Common Stock", "CLASS A COMMON STOCK, $0.01 PAR"])
def test_same_day_rows_differing_only_in_exchange_tag_or_title_wording_are_tolerated(
    fixture_store: duckdb.DuckDBPyConnection, late_form25: bool, class_title: str
) -> None:
    # INTT/PLAG/PRPB.U/CEI (#822): the same ticker filed on the same day
    # under two exchange tags (and in CEI's case two wordings of the class
    # title) is one line tagged twice, also when a Form 25 later ends one of
    # the two rows.
    insert_row(
        fixture_store,
        "listings",
        _listing_row("SEC_DUAL_A", "DUALA", "NASDAQ", date(2017, 1, 3), class_title),
    )
    if late_form25:
        insert_row(
            fixture_store,
            "delistings",
            _form25_row("SEC_DUAL_A", "NYSE", date(2020, 6, 30), date(2020, 7, 10)),
        )
    checks = integrity_checks(fixture_store, T_END, _settings())
    check = next(c for c in checks if c.rule == NON_OVERLAPPING_LISTINGS)
    assert check.passed, check.violations


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


def test_bar_before_a_later_same_ticker_listing_is_allowed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # #829: TRHX's next listing (2019-03-01) is the same ticker (resolver
    # rule 7, #819): the span runs on through the 2018-06-24 delisting, so
    # a bar between the effective date and that later row is the security's
    # own, not a resumption to flag.
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
    insert_row(fixture_store, "prices_daily", _bar("SEC_TRUNC_DELIST", date(2018, 7, 2)))
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == set()


def test_bar_before_a_later_different_ticker_listing_still_fails(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # Same shape as above, but the later row is a different ticker: a
    # genuine reused-ticker or unrelated-security resumption, still flagged.
    known = datetime(2019, 3, 1, 21, 0, tzinfo=UTC)
    insert_row(
        fixture_store,
        "listings",
        {
            "security_id": "SEC_TRUNC_DELIST",
            "ticker": "TRHY",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": date(2019, 3, 1),
            "known_at": known,
            "ingested_at": known,
            "source": "fixture",
            "provenance": "filing",
        },
    )
    insert_row(fixture_store, "prices_daily", _bar("SEC_TRUNC_DELIST", date(2018, 7, 2)))
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == {NO_BARS_AFTER_DELISTING}
    check = next(c for c in checks if c.rule == NO_BARS_AFTER_DELISTING)
    [row] = check.violations.to_dicts()
    assert row["security_id"] == "SEC_TRUNC_DELIST"
    assert row["session"] == date(2018, 7, 2)


def test_bar_before_a_later_same_ticker_non_equity_row_still_fails(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # The resolver's rule 7 only runs a span on when both rows are EQUITY
    # (spec data-foundation.md): a later row of the same ticker that is a
    # non-equity class (a right, here) never does, so this is still a
    # genuine resumption.
    known = datetime(2019, 3, 1, 21, 0, tzinfo=UTC)
    insert_row(
        fixture_store,
        "listings",
        {
            "security_id": "SEC_TRUNC_DELIST",
            "ticker": "TRHX",
            "exchange": "NASDAQ",
            "class_title": "Rights",
            "valid_from": date(2019, 3, 1),
            "known_at": known,
            "ingested_at": known,
            "source": "fixture",
            "provenance": "filing",
        },
    )
    insert_row(fixture_store, "prices_daily", _bar("SEC_TRUNC_DELIST", date(2018, 7, 2)))
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == {NO_BARS_AFTER_DELISTING}
    check = next(c for c in checks if c.rule == NO_BARS_AFTER_DELISTING)
    [row] = check.violations.to_dicts()
    assert row["security_id"] == "SEC_TRUNC_DELIST"
    assert row["session"] == date(2018, 7, 2)


def test_bar_before_a_same_day_typo_pairs_real_continuation_is_allowed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # #829 (code-review): the next listing after a delisting can itself be
    # a same-day filer-typo pair (#846) -- the resolver follows the ticker
    # the security held just before that day, not whichever row a sort
    # happens to put first. Here the real continuation (NYSE/TRHX) sorts
    # after the typo row (NASDAQ/TRHZ) by (exchange, ticker); both must be
    # checked.
    known = datetime(2019, 3, 1, 21, 0, tzinfo=UTC)
    insert_row(
        fixture_store,
        "listings",
        {
            "security_id": "SEC_TRUNC_DELIST",
            "ticker": "TRHZ",
            "exchange": "NASDAQ",
            "class_title": "Common Stock",
            "valid_from": date(2019, 3, 1),
            "known_at": known,
            "ingested_at": known,
            "source": "fixture",
            "provenance": "filing",
        },
    )
    insert_row(
        fixture_store,
        "listings",
        {
            "security_id": "SEC_TRUNC_DELIST",
            "ticker": "TRHX",
            "exchange": "NYSE",
            "class_title": "Common Stock",
            "valid_from": date(2019, 3, 1),
            "known_at": known,
            "ingested_at": known,
            "source": "fixture",
            "provenance": "filing",
        },
    )
    insert_row(fixture_store, "prices_daily", _bar("SEC_TRUNC_DELIST", date(2018, 7, 2)))
    checks = integrity_checks(fixture_store, T_END, _settings())
    assert _failed(checks) == set()


_TRHX = "SEC_TRUNC_DELIST"
#: TRHX's Form 25 takes effect on this Sunday; its last fixture bar is
#: 2018-05-25 (fixture README).
_TRHX_EFFECTIVE = date(2018, 6, 24)


def _sessions(first: date, last: date) -> list[date]:
    return [s for s in all_sessions() if first <= s <= last]


def _violating_sessions(conn: duckdb.DuckDBPyConnection, settings: Settings) -> list[date]:
    checks = integrity_checks(conn, T_END, settings)
    check = next(c for c in checks if c.rule == NO_BARS_AFTER_DELISTING)
    return list(check.violations["session"])


def test_trading_that_continues_past_the_effective_date_is_allowed(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # CMPR (#822): the old shares' Form 25 takes effect and the same line
    # keeps trading without a break. The listing's end is its last bar.
    for session in _sessions(date(2018, 5, 29), date(2018, 9, 28)):
        insert_row(fixture_store, "prices_daily", _bar(_TRHX, session))
    assert _violating_sessions(fixture_store, _settings()) == []


def test_bars_resuming_after_a_gap_past_the_effective_date_fail(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # EGLE (#822): the line trades on to 2018-06-29, stops, and bars come
    # back months later: every resumed bar fails, the tail before does not.
    for session in _sessions(date(2018, 5, 29), date(2018, 6, 29)):
        insert_row(fixture_store, "prices_daily", _bar(_TRHX, session))
    resumed = _sessions(date(2018, 10, 1), date(2018, 10, 5))
    for session in resumed:
        insert_row(fixture_store, "prices_daily", _bar(_TRHX, session))
    assert _violating_sessions(fixture_store, _settings()) == resumed


@pytest.mark.parametrize("off_exchange", ["NONE", "OTC"])
def test_an_otc_row_before_the_effective_date_does_not_end_the_check(
    fixture_store: duckdb.DuckDBPyConnection, off_exchange: str
) -> None:
    # Suspension sequence: TRHX quoted OTC from 2018-06-01, before its Form
    # 25 takes effect. Bars resuming months later still fail.
    insert_row(
        fixture_store, "listings", _listing_row(_TRHX, "TRHX", off_exchange, date(2018, 6, 1))
    )
    resumed = _sessions(date(2018, 10, 1), date(2018, 10, 5))
    for session in resumed:
        insert_row(fixture_store, "prices_daily", _bar(_TRHX, session))
    assert _violating_sessions(fixture_store, _settings()) == resumed


@pytest.mark.parametrize("window", [1, 5])
def test_a_gap_up_to_the_transfer_window_continues_the_tail(
    fixture_store: duckdb.DuckDBPyConnection, window: int
) -> None:
    settings = _settings(master={"transfer_window_sessions": window})
    tail = _sessions(date(2018, 5, 29), date(2018, 6, 29))
    after = _sessions(date(2018, 7, 2), date(2018, 8, 31))
    for session in tail:
        insert_row(fixture_store, "prices_daily", _bar(_TRHX, session))
    # `window` missing sessions, then trading again: still the tail.
    for session in after[window : window + 3]:
        insert_row(fixture_store, "prices_daily", _bar(_TRHX, session))
    assert _violating_sessions(fixture_store, settings) == []
    # One more missing session: the bars after it resume, and fail.
    late = after[window + 3 + window + 1 :]
    for session in late:
        insert_row(fixture_store, "prices_daily", _bar(_TRHX, session))
    assert _violating_sessions(fixture_store, settings) == late


@pytest.mark.parametrize(
    ("first_bar", "fails"), [(date(2018, 6, 25), False), (date(2018, 7, 9), True)]
)
def test_with_no_earlier_bar_the_gap_counts_from_the_effective_date(
    fixture_store: duckdb.DuckDBPyConnection, first_bar: date, fails: bool
) -> None:
    fixture_store.execute("DELETE FROM prices_daily WHERE security_id = ?", [_TRHX])
    bars = _sessions(first_bar, first_bar + timedelta(days=14))
    for session in bars:
        insert_row(fixture_store, "prices_daily", _bar(_TRHX, session))
    assert _violating_sessions(fixture_store, _settings()) == (bars if fails else [])


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
        # The two integrity rules derived at `t` as the listings are read
        # (module docstring), not merely read off stored rows (#827).
        full_checks = {c.rule: c for c in integrity_checks(fixture_store, t, settings)}
        cut_checks = {c.rule: c for c in integrity_checks(cut, t, settings)}
        for rule in (NON_OVERLAPPING_LISTINGS, NO_BARS_AFTER_DELISTING):
            assert full_checks[rule].violations.equals(cut_checks[rule].violations), (rule, t)


# --- Owner-accepted same-day pairs (#855) -----------------------------------

#: #855's shape: a security's first-ever listing is a same-day pair of two
#: tickers differing by a letter transposition (HACAR/HCACR, 2026-09-23 on the
#: owner's store): no held ticker anchors the typo rule, and they are not one
#: Alpaca symbol under two spellings.
FIRST_PAIR_SID = "0002079013:share-rights"
FIRST_PAIR_DAY = date(2019, 6, 3)
FIRST_PAIR_ENTRY = f"{FIRST_PAIR_SID}@{FIRST_PAIR_DAY.isoformat()}"


def _insert_first_listing_pair(conn: duckdb.DuckDBPyConnection) -> None:
    for ticker in ("HACAR", "HCACR"):
        insert_row(
            conn,
            "listings",
            _listing_row(FIRST_PAIR_SID, ticker, "NASDAQ", FIRST_PAIR_DAY, "Rights"),
        )


def _accepting(*entries: str) -> Settings:
    return _settings(universe={"accepted_same_day_pairs": list(entries)})


def test_a_first_listing_same_day_pair_fails_until_the_owner_accepts_it(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _insert_first_listing_pair(fixture_store)
    unaccepted = health_report(fixture_store, T_END, _settings())
    assert unaccepted.failures == (NON_OVERLAPPING_LISTINGS,)
    assert unaccepted.accepted_same_day_pairs.frame.is_empty()

    report = health_report(fixture_store, T_END, _accepting(FIRST_PAIR_ENTRY))
    assert report.ok, report.failures
    # Never silently hidden: the accepted pair is listed in the report.
    [row] = report.accepted_same_day_pairs.frame.to_dicts()
    assert (row["security_id"], row["ticker"], row["next_ticker"]) == (
        FIRST_PAIR_SID,
        "HACAR",
        "HCACR",
    )
    assert row["valid_from"] == row["next_valid_from"] == FIRST_PAIR_DAY
    # `integrity_checks` on its own reads the same list.
    assert _failed(integrity_checks(fixture_store, T_END, _accepting(FIRST_PAIR_ENTRY))) == set()


@pytest.mark.parametrize(
    "entry",
    [
        f"{FIRST_PAIR_SID}@2019-06-04",  # another day
        f"{FIRST_PAIR_SID}-x@{FIRST_PAIR_DAY.isoformat()}",  # another security
        f"SEC_DUAL_A@{FIRST_PAIR_DAY.isoformat()}",  # another security, same day
    ],
)
def test_an_accepted_pair_entry_excuses_only_its_own_security_and_day(
    fixture_store: duckdb.DuckDBPyConnection, entry: str
) -> None:
    _insert_first_listing_pair(fixture_store)
    report = health_report(fixture_store, T_END, _accepting(entry))
    assert report.failures == (NON_OVERLAPPING_LISTINGS,)
    check = next(c for c in report.integrity if c.rule == NON_OVERLAPPING_LISTINGS)
    assert check.violations["security_id"].to_list() == [FIRST_PAIR_SID]
    assert report.accepted_same_day_pairs.frame.is_empty()


def test_an_accepted_pair_does_not_excuse_a_late_form25_on_its_row(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # The entry tolerates the same-start test only: a Form 25 on the pair's
    # line filed months after the next exchange line started still fails.
    _insert_first_listing_pair(fixture_store)
    insert_row(
        fixture_store,
        "listings",
        _listing_row(FIRST_PAIR_SID, "HCACR", "NYSE", date(2019, 7, 1), "Rights"),
    )
    insert_row(
        fixture_store,
        "delistings",
        _form25_row(FIRST_PAIR_SID, "NASDAQ", date(2020, 1, 10), date(2020, 1, 20)),
    )
    report = health_report(fixture_store, T_END, _accepting(FIRST_PAIR_ENTRY))
    check = next(c for c in report.integrity if c.rule == NON_OVERLAPPING_LISTINGS)
    assert not check.passed
    assert set(check.violations["next_valid_from"].to_list()) == {date(2019, 7, 1)}
    assert report.accepted_same_day_pairs.frame.height == 1


@pytest.mark.parametrize("bad", ["SEC_A", "@2019-06-03", "SEC_A@2019-13-01", "SEC_A@"])
def test_accepted_same_day_pair_entries_are_validated(bad: str) -> None:
    assert parse_accepted_same_day_pair(FIRST_PAIR_ENTRY) == (FIRST_PAIR_SID, FIRST_PAIR_DAY)
    with pytest.raises(ValueError):
        parse_accepted_same_day_pair(bad)
    with pytest.raises(ValidationError):
        _accepting(bad)


def test_accepted_same_day_pairs_default_empty() -> None:
    assert _settings().universe.accepted_same_day_pairs == []


def test_an_accepted_day_is_one_line_for_every_row_of_that_day(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # Code-review on #917: HACAR tagged on two exchanges that day (one line,
    # #822) sorts HACAR@NASDAQ, HACAR@NYSE, HCACR@NYSE. HACAR@NASDAQ's own
    # Form 25, filed later, must not find the accepted partner HCACR as its
    # "next exchange line": the accepted day is one line for all its rows.
    for ticker, exchange in (("HACAR", "NASDAQ"), ("HACAR", "NYSE"), ("HCACR", "NYSE")):
        insert_row(
            fixture_store,
            "listings",
            _listing_row(FIRST_PAIR_SID, ticker, exchange, FIRST_PAIR_DAY, "Rights"),
        )
    insert_row(
        fixture_store,
        "delistings",
        _form25_row(FIRST_PAIR_SID, "NASDAQ", date(2020, 1, 10), date(2020, 1, 20)),
    )
    assert health_report(fixture_store, T_END, _settings()).failures == (NON_OVERLAPPING_LISTINGS,)
    report = health_report(fixture_store, T_END, _accepting(FIRST_PAIR_ENTRY))
    assert report.ok, report.integrity
    assert report.accepted_same_day_pairs.frame["next_ticker"].to_list() == ["HCACR"]


# --- Statement facts (#660, T77c) -------------------------------------------


def test_statement_counts_parses_every_statement_underscore_figure() -> None:
    message = (
        "5 securities; statement_held: 3, statement_unstampable: 0, "
        "statement_vintage_late: 2, statement_conflicts: 1, statement_restated: 4, "
        "statement_non_usd: 0, statement_malformed: 0, statement_derived: 7, "
        "statement_none: 1; missing benchmarks: none"
    )
    assert statement_counts(message) == {
        "held": 3,
        "unstampable": 0,
        "vintage_late": 2,
        "conflicts": 1,
        "restated": 4,
        "non_usd": 0,
        "malformed": 0,
        "derived": 7,
        "none": 1,
    }


def test_statement_counts_empty_on_none_or_no_figures() -> None:
    assert statement_counts(None) == {}
    assert statement_counts("5 securities; missing benchmarks: none") == {}


def test_statement_facts_switched_off_reports_no_coverage(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    off = _settings(edgar={"statement_facts_enabled": False})  # on by default since T78
    report = health_report(fixture_store, T_END, off)
    assert report.statement.enabled is False
    assert report.statement.coverage is None
    assert report.statement.counts == {}
    assert report.statement.vintage_late == 0


def test_statement_facts_report_reads_the_latest_edgar_run_message(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    insert_row(
        fixture_store,
        "ingestion_runs",
        {
            "run_id": "edgar-1",
            "started_at": T_END - timedelta(minutes=5),
            "finished_at": T_END - timedelta(minutes=1),
            "status": "ok",
            "source": "edgar",
            "mode": "daily",
            "rows_added": 0,
            "chunk_cursor": None,
            "message": "0 securities; statement_vintage_late: 2, statement_derived: 1",
        },
    )
    report = health_report(fixture_store, T_END, settings)
    assert report.statement.counts == {"vintage_late": 2, "derived": 1}
    assert report.statement.vintage_late == 2


def test_statement_coverage_counts_a_fresh_member_with_both_facts(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # SEC_DUAL_A/SEC_DUAL_B (CIK0001000006) are both universe members at
    # T_DUAL; inserting one fresh revenue and total_assets row under their
    # cik makes both classes count, since the fact belongs to the issuer.
    known = datetime(2018, 6, 8, 20, 30, tzinfo=UTC)
    for name, days in (("revenue", 181), ("total_assets", 0)):
        insert_row(
            fixture_store,
            "statement_facts",
            _statement_fact(
                DUAL_CIK, name, period_end=date(2018, 6, 30), period_days=days, known=known
            ),
        )
    cov = statement_coverage(fixture_store, T_DUAL, _settings())
    assert cov.fresh == 2
    assert cov.total == 5
    assert cov.share == pytest.approx(2 / 5)


def test_statement_coverage_excludes_a_member_missing_one_fact(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    known = datetime(2018, 6, 8, 20, 30, tzinfo=UTC)
    insert_row(
        fixture_store,
        "statement_facts",
        _statement_fact(DUAL_CIK, "revenue", period_end=date(2018, 6, 30), known=known),
    )
    cov = statement_coverage(fixture_store, T_DUAL, _settings())
    assert cov.fresh == 0
    assert cov.total == 5


def test_statement_coverage_excludes_a_stale_fact(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    known = datetime(2016, 1, 10, 20, 30, tzinfo=UTC)
    for name, days in (("revenue", 181), ("total_assets", 0)):
        insert_row(
            fixture_store,
            "statement_facts",
            # More than universe.max_shares_age_days (400) before T_DUAL.
            _statement_fact(
                DUAL_CIK, name, period_end=date(2016, 1, 1), period_days=days, known=known
            ),
        )
    cov = statement_coverage(fixture_store, T_DUAL, _settings())
    assert cov.fresh == 0
    assert cov.total == 5


def test_statement_coverage_ignores_a_fact_not_yet_known_at_t(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    before = datetime(2020, 2, 19, 20, 0, tzinfo=UTC)  # before CIK0001000003's known_at
    after = datetime(2020, 2, 21, 20, 0, tzinfo=UTC)  # after it
    assert statement_coverage(fixture_store, before, _settings()).derived == 0
    assert statement_coverage(fixture_store, after, _settings()).derived == 1


def test_statement_coverage_derived_share_is_not_scoped_to_the_universe(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # SEC_CLEAN_MERGER (CIK0001000003) is delisted long before T_END and is
    # never a universe member, yet its derived gross_profit row still
    # counts: the derived share is a parse-quality count, not a portfolio
    # one (module docstring).
    cov = statement_coverage(fixture_store, T_END, _settings())
    assert cov.derived == 1
    assert cov.reported == REPORTED_GROSS_PROFIT_AT_END
    assert cov.derived_share == pytest.approx(1 / (1 + REPORTED_GROSS_PROFIT_AT_END))


def test_statement_coverage_derived_share_counts_a_dual_class_row_once(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # DUAL_CIK (CIK0001000006) joins through securities_as_of once per
    # class (SEC_DUAL_A, SEC_DUAL_B, SEC_DUAL_PFD): one derived gross_profit
    # row for the issuer must count once, not three times.
    known = datetime(2020, 1, 1, tzinfo=UTC)
    insert_row(
        fixture_store,
        "statement_facts",
        _statement_fact(
            DUAL_CIK,
            "gross_profit",
            period_end=date(2019, 12, 31),
            basis="derived",
            accession="DUALGP",
            known=known,
        ),
    )
    cov = statement_coverage(fixture_store, T_END, _settings())
    assert cov.derived == 2  # CIK0001000003's existing fixture row, plus this one
    assert cov.reported == REPORTED_GROSS_PROFIT_AT_END


def test_statement_coverage_on_a_version_8_store_does_not_crash(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    fixture_store.execute("DELETE FROM schema_version WHERE version = 9")
    fixture_store.execute("DROP TABLE statement_facts")
    cov = statement_coverage(fixture_store, T_END, _settings())
    assert cov.fresh == 0 and cov.total == 0 and cov.derived == 0 and cov.reported == 0


def test_statement_period_days_rule_fails_on_an_injected_mismatch(
    loose_store: duckdb.DuckDBPyConnection,
) -> None:
    known = datetime(2020, 1, 1, tzinfo=UTC)
    bad = _statement_fact("CIK9999999", "revenue", period_end=date(2019, 12, 31), known=known)
    bad["period_days"] = 999  # does not equal period_end - period_start
    insert_row(loose_store, "statement_facts", bad)
    report = health_report(loose_store, T_END, _settings())
    check = next(c for c in report.integrity if c.rule == STATEMENT_PERIOD_DAYS)
    assert not check.passed
    assert check.violations.height == 1
    for other in report.integrity:
        if other.rule != STATEMENT_PERIOD_DAYS:
            assert other.passed, other.rule


def test_statement_basis_rule_fails_on_an_injected_bad_basis(
    loose_store: duckdb.DuckDBPyConnection,
) -> None:
    known = datetime(2020, 1, 1, tzinfo=UTC)
    bad = _statement_fact("CIK9999999", "revenue", period_end=date(2019, 12, 31), known=known)
    bad["basis"] = "guessed"
    insert_row(loose_store, "statement_facts", bad)
    report = health_report(loose_store, T_END, _settings())
    check = next(c for c in report.integrity if c.rule == STATEMENT_BASIS_ALLOWED)
    assert not check.passed
    assert check.violations.to_dicts() == [{"basis": "guessed", "rows": 1}]
    for other in report.integrity:
        if other.rule != STATEMENT_BASIS_ALLOWED:
            assert other.passed, other.rule


def test_statement_derived_matches_components_fails_on_a_wrong_value(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    known = datetime(2020, 1, 1, tzinfo=UTC)
    cik = "CIK7777777"
    for name, value in (("revenue", 1000.0), ("cost_of_revenue", 600.0)):
        insert_row(
            fixture_store,
            "statement_facts",
            _statement_fact(
                cik, name, period_end=date(2019, 12, 31), value=value, accession="ACC3", known=known
            ),
        )
    insert_row(
        fixture_store,
        "statement_facts",
        _statement_fact(
            cik,
            "gross_profit",
            period_end=date(2019, 12, 31),
            value=123.0,  # wrong: should be 1000.0 - 600.0 = 400.0
            accession="ACC3",
            basis="derived",
            known=known,
        ),
    )
    report = health_report(fixture_store, T_END, _settings())
    check = next(c for c in report.integrity if c.rule == STATEMENT_DERIVED_MATCHES_COMPONENTS)
    assert not check.passed
    assert check.violations.to_dicts()[0]["revenue"] == 1000.0
    assert check.violations.to_dicts()[0]["cost_of_revenue"] == 600.0


def test_statement_derived_matches_components_fails_on_a_missing_component(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    known = datetime(2020, 1, 1, tzinfo=UTC)
    cik = "CIK6666666"
    insert_row(
        fixture_store,
        "statement_facts",
        _statement_fact(
            cik, "revenue", period_end=date(2019, 12, 31), accession="ACC4", known=known
        ),
    )
    insert_row(
        fixture_store,
        "statement_facts",
        _statement_fact(
            cik,
            "gross_profit",
            period_end=date(2019, 12, 31),
            value=1.0,
            accession="ACC4",
            basis="derived",
            known=known,
        ),
    )
    report = health_report(fixture_store, T_END, _settings())
    check = next(c for c in report.integrity if c.rule == STATEMENT_DERIVED_MATCHES_COMPONENTS)
    assert not check.passed
    assert check.violations.height == 1
    row = check.violations.to_dicts()[0]
    assert row["revenue"] == 100.0  # the inserted revenue value
    assert row["cost_of_revenue"] is None


def test_statement_derived_matches_components_passes_on_a_correct_value(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    known = datetime(2020, 1, 1, tzinfo=UTC)
    cik = "CIK5555555"
    for name, value in (("revenue", 1000.0), ("cost_of_revenue", 600.0)):
        insert_row(
            fixture_store,
            "statement_facts",
            _statement_fact(
                cik, name, period_end=date(2019, 12, 31), value=value, accession="ACC5", known=known
            ),
        )
    insert_row(
        fixture_store,
        "statement_facts",
        _statement_fact(
            cik,
            "gross_profit",
            period_end=date(2019, 12, 31),
            value=400.0,
            accession="ACC5",
            basis="derived",
            known=known,
        ),
    )
    report = health_report(fixture_store, T_END, _settings())
    check = next(c for c in report.integrity if c.rule == STATEMENT_DERIVED_MATCHES_COMPONENTS)
    assert check.passed


def test_statement_integrity_rules_pass_on_a_version_8_store(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    fixture_store.execute("DELETE FROM schema_version WHERE version = 9")
    fixture_store.execute("DROP TABLE statement_facts")
    report = health_report(fixture_store, T_END, settings)
    for rule in (
        STATEMENT_PERIOD_DAYS,
        STATEMENT_DERIVED_MATCHES_COMPONENTS,
        STATEMENT_BASIS_ALLOWED,
    ):
        check = next(c for c in report.integrity if c.rule == rule)
        assert check.passed, rule
