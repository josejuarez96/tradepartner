"""Tests for the data-health page (Phase 2 T21, spec reqs 11 and 12).

Driven headless through `streamlit.testing.v1.AppTest` on the shell over a
temp-file copy of the fixture universe, with the page's clock pinned after
the fixture's last session. `load_health_view` is also exercised directly:
it is the page's only reader and needs no Streamlit.
"""

from __future__ import annotations

import re
import tomllib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import load_universe_fixtures
from streamlit.testing.v1 import AppTest

from tradepartner.calendar import all_sessions, session_close
from tradepartner.config import Settings
from tradepartner.dashboard import app, health_page, theme
from tradepartner.health import INTEGRITY_RULES, coverage, health_report
from tradepartner.ingest import SOURCES
from tradepartner.store import schema
from tradepartner.store.db import configure_connection, insert_row

ROOT = Path(__file__).resolve().parents[2]
_APP_PATH = str(ROOT / "src" / "tradepartner" / "dashboard" / "app.py")
_PAGE_PY = ROOT / "src" / "tradepartner" / "dashboard" / "health_page.py"
_CONFIG_TOML = ROOT / ".streamlit" / "config.toml"
UNIVERSE_DIR = ROOT / "tests" / "fixtures" / "universe"

LAST = date(2020, 6, 30)
#: After the fixture's last session: every fixture row is known.
T = session_close(LAST) + timedelta(hours=3)
FINISHED = datetime(2020, 6, 30, 23, 0, tzinfo=UTC)
WINDOW = (date(2020, 6, 1), LAST)


def _settings(path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(path)})


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    path = tmp_path / "store.duckdb"
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        load_universe_fixtures(conn, UNIVERSE_DIR)
        insert_row(
            conn,
            "ingestion_runs",
            {
                "run_id": 1,
                "started_at": FINISHED - timedelta(minutes=5),
                "finished_at": FINISHED,
                "status": "ok",
                "source": SOURCES[0],
                "mode": "daily",
                "rows_added": 3,
                "chunk_cursor": "2020-06-30",
                "message": "fixture run",
            },
        )
    finally:
        conn.close()
    return path


def _app(monkeypatch: pytest.MonkeyPatch, store_path: Path, now: datetime = T) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(store_path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store_path.parent / "does-not-exist.env"))
    monkeypatch.setattr(health_page, "_now", lambda: now)
    at = AppTest.from_file(_APP_PATH, default_timeout=60)
    at.run()
    return at


def _text(at: AppTest) -> str:
    parts: list[Any] = [m.value for m in at.markdown]
    parts += [c.value for c in at.caption]
    parts += [h.value for h in at.subheader]
    parts += [f"{m.label} {m.value}" for m in at.metric]
    parts += [e.value for kind in (at.info, at.warning, at.error, at.success) for e in kind]
    return "\n".join(str(p) for p in parts)


def _connect(store_path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(store_path), read_only=True)
    configure_connection(conn)
    return conn


# --- load_health_view (pure reader) -----------------------------------------


def test_view_carries_the_health_report(store_path: Path) -> None:
    settings = _settings(store_path)
    with _connect(store_path) as conn:
        view = health_page.load_health_view(conn, T, settings, WINDOW)
        report = health_report(conn, T, settings)
    assert view.report.session == LAST
    assert view.report.coverage == report.coverage
    assert view.report.delisted.count == report.delisted.count
    assert view.threshold == settings.ingest.max_missing_share


def test_series_has_every_session_in_the_window(store_path: Path) -> None:
    with _connect(store_path) as conn:
        view = health_page.load_health_view(conn, T, _settings(store_path), WINDOW)
    expected = [s for s in all_sessions() if WINDOW[0] <= s <= WINDOW[1]]
    assert view.series["session"].to_list() == expected


def test_series_rows_and_missing_share(store_path: Path) -> None:
    settings = _settings(store_path)
    with _connect(store_path) as conn:
        view = health_page.load_health_view(conn, T, settings, WINDOW)
        last = coverage(conn, T, settings)
        rows = conn.execute(
            "SELECT count(*) FROM prices_daily WHERE session = ? AND known_at <= ?", [LAST, T]
        ).fetchone()
    row = view.series.row(-1, named=True)
    assert rows is not None and row["rows"] == rows[0]
    assert row["live"] == len(last.live)
    assert row["missing"] == len(last.missing)
    expected = len(last.missing) / len(last.live) if last.live else 0.0
    assert row["missing_share"] == pytest.approx(expected)


def _write(store_path: Path, sql: str, params: list[Any]) -> None:
    with duckdb.connect(str(store_path)) as conn:
        configure_connection(conn)
        conn.execute(sql, params)


def _rows_on(view: health_page.HealthView, session: date) -> dict[str, Any]:
    [row] = view.series.filter(view.series["session"] == session).to_dicts()
    return row


def test_series_reads_only_bars_known_at_t(store_path: Path) -> None:
    day = date(2020, 6, 29)
    late = T + timedelta(days=1)
    # A revision of an existing bar, first known after T.
    _write(
        store_path,
        """
        INSERT INTO prices_daily
        SELECT security_id, session, open, high, low, close + 1, volume, ?, ?, source, provenance
        FROM prices_daily WHERE session = ? ORDER BY security_id LIMIT 1
        """,
        [late, late, day],
    )
    settings = _settings(store_path)
    with _connect(store_path) as conn:
        at_t = health_page.load_health_view(conn, T, settings, WINDOW)
        after = health_page.load_health_view(conn, late + timedelta(hours=1), settings, WINDOW)
    assert _rows_on(after, day)["rows"] == _rows_on(at_t, day)["rows"] + 1


def test_a_name_delisted_later_is_live_before_its_end(store_path: Path) -> None:
    """SEC_WINDOW_DELIST is delisted at T (last bar 2019-06-24) but was live in June 2019:
    a bar it lacks then is missing."""
    day, window = date(2019, 6, 5), (date(2019, 6, 3), date(2019, 6, 7))
    settings = _settings(store_path)
    with _connect(store_path) as conn:
        before = _rows_on(health_page.load_health_view(conn, T, settings, window), day)
    _write(
        store_path,
        "DELETE FROM prices_daily WHERE security_id = ? AND session = ?",
        ["SEC_WINDOW_DELIST", day],
    )
    with _connect(store_path) as conn:
        after = _rows_on(health_page.load_health_view(conn, T, settings, window), day)
    assert after["live"] == before["live"]
    assert after["missing"] == before["missing"] + 1
    assert after["rows"] == before["rows"] - 1


def test_as_of_last_updated_and_staleness(store_path: Path) -> None:
    with _connect(store_path) as conn:
        fresh = health_page.load_health_view(conn, T, _settings(store_path), WINDOW)
        later = session_close(date(2020, 7, 7)) + timedelta(hours=3)
        stale = health_page.load_health_view(conn, later, _settings(store_path), WINDOW)
        known = conn.execute("SELECT max(known_at) FROM prices_daily").fetchone()
    assert known is not None
    assert fresh.as_of is not None and fresh.as_of >= known[0]
    assert fresh.last_updated == FINISHED
    assert fresh.stale_sessions == 0
    # 2020-07-01, 07-02, 07-06 and 07-07 have no bars (07-03 is a holiday).
    assert stale.stale_sessions == 4


def test_empty_store_view(tmp_path: Path) -> None:
    path = tmp_path / "empty.duckdb"
    conn = duckdb.connect(str(path))
    configure_connection(conn)
    schema.init_schema(conn)
    view = health_page.load_health_view(conn, T, _settings(path), WINDOW)
    conn.close()
    assert view.as_of is None
    assert view.last_updated is None
    assert view.stale_sessions is None
    assert view.series["rows"].sum() == 0


# --- render (headless) -------------------------------------------------------


def test_page_is_the_shells_data_health_entry() -> None:
    assert app._PAGES["Data health"] is health_page.render


def test_render_shows_every_metric(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> None:
    at = _app(monkeypatch, store_path)
    assert not at.exception
    text = _text(at)
    for label in (
        "Data health",
        "as of",
        "last updated",
        "Coverage",
        "Interior gaps",
        "Delisted names",
        "Last update",
        "Missing share per session",
        "Bars per session",
        "Integrity checks",
        "Sources",
        "Survivorship gap",
        "Unclassifiable",
        "snapshot_static reliance",
        "Liquidity rule",
        "Fill price",
        "Gap report",
    ):
        assert label in text, label
    for rule in INTEGRITY_RULES:
        assert rule in text, rule
    for source in SOURCES:
        assert source in text, source
    settings = _settings(store_path)
    with _connect(store_path) as conn:
        report = health_report(conn, T, settings)
    assert f"Delisted names {report.delisted.count}" in text
    assert f"Unclassifiable: {report.unclassifiable.count}" in text
    assert f"snapshot_static reliance: {report.static_reliance.count}" in text
    frames = {tuple(df.value.columns) for df in at.dataframe}
    assert tuple(report.delisted.frame.columns) in frames
    assert tuple(report.gaps.rows.columns) in frames


def test_render_marks_a_stale_store(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> None:
    later = session_close(date(2020, 7, 7)) + timedelta(hours=3)
    at = _app(monkeypatch, store_path, now=later)
    assert not at.exception
    assert "stale: 4 sessions" in _text(at)
    assert ":orange-badge[" in _text(at)
    assert "stale:" not in _text(_app(monkeypatch, store_path))


def test_render_empty_store_has_no_warning(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "empty.duckdb"
    with duckdb.connect(str(path)) as conn:
        configure_connection(conn)
        schema.init_schema(conn)
    at = _app(monkeypatch, path)
    assert not at.exception
    assert not (at.warning or at.info or at.error)
    assert "never" in _text(at)
    assert "no bars yet" in _text(at)
    assert not at.get("arrow_vega_lite_chart")  # no empty chart frames


def test_a_half_picked_window_keeps_the_rest_of_the_page(
    monkeypatch: pytest.MonkeyPatch, store_path: Path
) -> None:
    at = _app(monkeypatch, store_path)
    at.date_input(key="health_window").set_value((date(2020, 6, 1),)).run()
    assert not at.exception
    text = _text(at)
    assert "as of" in text and "Integrity checks" in text
    assert "Pick an end date" in text


def test_page_reads_only_through_the_shells_connection(
    monkeypatch: pytest.MonkeyPatch, store_path: Path
) -> None:
    opened: list[object] = []
    real_connect = duckdb.connect

    def _counting_connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        opened.append(kwargs.get("read_only"))
        return real_connect(*args, **kwargs)

    at = _app(monkeypatch, store_path)
    monkeypatch.setattr(duckdb, "connect", _counting_connect)
    at.run()
    assert not at.exception
    assert opened == [True]


# --- theme -------------------------------------------------------------------


def test_no_colour_literal_in_page_code() -> None:
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", _PAGE_PY.read_text(encoding="utf-8"))


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_config_toml_mirrors_the_theme_tokens(mode: str) -> None:
    config = tomllib.loads(_CONFIG_TOML.read_text(encoding="utf-8"))["theme"][mode]
    assert config == theme.streamlit_theme(theme.PALETTES[mode])


def test_palettes_follow_the_design_standard() -> None:
    light, dark = theme.PALETTES["light"], theme.PALETTES["dark"]
    assert light.background == "#f6f7f4" and dark.background == "#12161a"
    assert light.series == ("#2a78d6", "#eb6834", "#1baf7a", "#eda100")
    assert dark.series == ("#3987e5", "#d95926", "#199e70", "#c98500")
    assert (light.critical, light.critical_bg) == ("#b23f36", "#f7dedb")
