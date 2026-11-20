"""Source staleness in `health --check` (#3553): EDGAR, FRED and SEC forms are
measured by their last `ok` run in XNYS sessions at report time."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pytest

from tradepartner.calendar import all_sessions, session_close
from tradepartner.config import Settings
from tradepartner.health import HealthReport, health_report, health_verdict

T_END = session_close(date(2020, 6, 30))
_ET = ZoneInfo("America/New_York")
EDGAR_LIMIT = 3


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


def _sessions_before(end: date, n: int) -> date:
    sessions = all_sessions()
    return sessions[sessions.index(end) - n]


def _evening(day: date, at: time = time(15, 30)) -> datetime:
    """`at` New York time on `day` (before T_END's close, so never after `t`)."""
    return datetime.combine(day, at, _ET).astimezone(UTC)


_COUNTER = [0]


def _run(
    conn: duckdb.DuckDBPyConnection,
    source: str,
    finished: datetime,
    *,
    mode: str = "session",
    status: str = "ok",
    cursor: str | None = None,
    message: str | None = None,
) -> None:
    _COUNTER[0] += 1
    conn.execute(
        "INSERT INTO ingestion_runs VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)",
        [
            f"r{_COUNTER[0]}",
            finished - timedelta(minutes=5),
            finished,
            status,
            source,
            mode,
            cursor,
            message,
        ],
    )


def _edgar_ok(conn: duckdb.DuckDBPyConnection, sessions_old: int) -> None:
    _run(conn, "edgar", _evening(_sessions_before(date(2020, 6, 30), sessions_old)))


def _failures(conn: duckdb.DuckDBPyConnection, settings: Settings | None = None) -> tuple[str, ...]:
    return health_report(conn, T_END, settings or _settings()).check_failures


def _fresh_fred(conn: duckdb.DuckDBPyConnection) -> None:
    _run(conn, "fred", _evening(date(2020, 6, 30)))


def test_check_fails_on_stale_edgar(fixture_store: duckdb.DuckDBPyConnection) -> None:
    _edgar_ok(fixture_store, 5)
    _fresh_fred(fixture_store)
    assert "edgar_stale" in _failures(fixture_store)


@pytest.mark.parametrize(
    ("old", "stale"), [(0, False), (EDGAR_LIMIT, False), (EDGAR_LIMIT + 1, True)]
)
def test_edgar_limit_boundary(
    fixture_store: duckdb.DuckDBPyConnection, old: int, stale: bool
) -> None:
    _edgar_ok(fixture_store, old)
    _fresh_fred(fixture_store)
    report = health_report(fixture_store, T_END, _settings())
    edgar = next(f for f in report.freshness if f.source == "edgar")
    assert edgar.sessions_old == old
    assert ("edgar_stale" in report.check_failures) is stale


def test_never_ok_edgar_fails(fixture_store: duckdb.DuckDBPyConnection) -> None:
    _run(fixture_store, "edgar", _evening(date(2020, 6, 30)), status="failed")
    _fresh_fred(fixture_store)
    report = health_report(fixture_store, T_END, _settings())
    assert next(f for f in report.freshness if f.source == "edgar").sessions_old is None
    assert "edgar_stale" in report.check_failures
    assert "edgar never ran ok" in health_verdict(report)


@pytest.mark.parametrize(("old", "stale"), [(3, False), (4, True)])
def test_fred_limit_boundary(
    fixture_store: duckdb.DuckDBPyConnection, old: int, stale: bool
) -> None:
    _edgar_ok(fixture_store, 0)
    _run(fixture_store, "fred", _evening(_sessions_before(date(2020, 6, 30), old)))
    assert ("fred_stale" in _failures(fixture_store)) is stale


def test_fred_never_ok_fails_when_seeded(fixture_store: duckdb.DuckDBPyConnection) -> None:
    _edgar_ok(fixture_store, 0)
    assert "fred_stale" in _failures(fixture_store)


def test_fred_unseeded_adds_no_line(fixture_store: duckdb.DuckDBPyConnection) -> None:
    fixture_store.execute("DELETE FROM rates")
    _edgar_ok(fixture_store, 0)
    report = health_report(fixture_store, T_END, _settings())
    assert "fred_stale" not in report.check_failures
    assert all(f.source != "fred" for f in report.freshness)


def test_sec_forms_disabled_adds_no_line(fixture_store: duckdb.DuckDBPyConnection) -> None:
    _edgar_ok(fixture_store, 0)
    _fresh_fred(fixture_store)
    report = health_report(fixture_store, T_END, _settings())
    assert all(f.source != "sec_forms" for f in report.freshness)
    assert "sec_forms_stale" not in report.check_failures


@pytest.mark.parametrize(("old", "stale"), [(EDGAR_LIMIT, False), (EDGAR_LIMIT + 1, True)])
def test_sec_forms_takes_the_later_of_edgar_and_its_own_run(
    fixture_store: duckdb.DuckDBPyConnection, old: int, stale: bool
) -> None:
    settings = _settings(events={"sec_forms_enabled": True})
    _fresh_fred(fixture_store)
    _edgar_ok(fixture_store, 9)
    _run(fixture_store, "sec_forms", _evening(_sessions_before(date(2020, 6, 30), old)))
    assert ("sec_forms_stale" in _failures(fixture_store, settings)) is stale
    _edgar_ok(fixture_store, 1)
    assert "sec_forms_stale" not in _failures(fixture_store, settings)


def test_sec_forms_never_ok_fails_when_enabled(fixture_store: duckdb.DuckDBPyConnection) -> None:
    settings = _settings(events={"sec_forms_enabled": True})
    _fresh_fred(fixture_store)
    assert "sec_forms_stale" in _failures(fixture_store, settings)


def test_friday_evening_run_read_monday_evening_counts_one_session(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _run(fixture_store, "edgar", _evening(date(2020, 6, 26), time(18, 40)))
    monday = datetime.combine(date(2020, 6, 29), time(17, 0), _ET).astimezone(UTC)
    report = health_report(fixture_store, monday, _settings())
    assert report.session == date(2020, 6, 29)
    assert next(f for f in report.freshness if f.source == "edgar").sessions_old == 1


def test_run_finished_after_t_is_ignored(fixture_store: duckdb.DuckDBPyConnection) -> None:
    _edgar_ok(fixture_store, 9)
    _run(fixture_store, "edgar", T_END + timedelta(hours=2))
    report = health_report(fixture_store, T_END, _settings())
    assert next(f for f in report.freshness if f.source == "edgar").sessions_old == 9
    assert "edgar_stale" in report.check_failures


def test_failures_and_ok_are_unchanged_by_staleness(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _edgar_ok(fixture_store, 9)
    report = health_report(fixture_store, T_END, _settings())
    assert "edgar_stale" in report.check_failures
    assert "edgar_stale" not in report.failures
    assert report.ok


def test_verdict_names_the_stale_source(fixture_store: duckdb.DuckDBPyConnection) -> None:
    _edgar_ok(fixture_store, 9)
    _fresh_fred(fixture_store)
    report: HealthReport = health_report(fixture_store, T_END, _settings())
    assert "edgar 9 sessions since its last ok run" in health_verdict(report)


def test_rates_staleness_is_measured_at_report_time(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    latest = _sessions_before(date(2020, 6, 30), 8)
    fixture_store.execute("DELETE FROM rates WHERE observation_date > ?", [latest])
    _run(
        fixture_store,
        "fred",
        _evening(latest),
        cursor="rates;series=DTB3;2020",
        message="rates_inserted=1 stale_sessions=0",
    )
    series = next(
        s
        for s in health_report(fixture_store, T_END, _settings()).rates.series
        if s.series == "DTB3"
    )
    assert series.stale_sessions == 8
    assert any("stale: 8 sessions" in w for w in series.warnings)
