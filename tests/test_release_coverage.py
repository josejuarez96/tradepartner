"""Release coverage (#3274): a read-only before/after check across a data release."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import duckdb
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.config import Settings
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.release_coverage import failing, release_coverage
from tradepartner.store.schema import init_schema

NOW = datetime(2020, 7, 1, 23, 0, tzinfo=UTC)
START = date(2020, 1, 1)


def _days(n: int) -> list[date]:
    return [START + timedelta(days=i) for i in range(n)]


def _bar(conn: duckdb.DuckDBPyConnection, sid: str, day: date, close: float = 1.0) -> None:
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": sid,
            "session": day,
            "open": 1.0,
            "high": max(1.0, close),
            "low": min(1.0, close),
            "close": close,
            "volume": 1,
            "known_at": NOW,
            "ingested_at": NOW,
            "source": "test",
            "provenance": "bar",
        },
    )


def _master(conn: duckdb.DuckDBPyConnection, sid: str, ticker: str) -> None:
    insert_row(
        conn,
        "securities",
        {
            "security_id": sid,
            "cik": "1",
            "name": f"{ticker} Inc",
            "known_at": NOW,
            "ingested_at": NOW,
            "source": "test",
            "provenance": "snapshot",
        },
    )
    insert_row(
        conn,
        "listings",
        {
            "security_id": sid,
            "ticker": ticker,
            "exchange": "XNYS",
            "valid_from": START,
            "known_at": NOW,
            "ingested_at": NOW,
            "source": "test",
            "provenance": "snapshot",
        },
    )


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None, store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 0}
    )


def _seed(settings: Settings) -> None:
    """Before: A has 20 sessions, B has 20, C has 20. Opens a release on a backup."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        for sid, tk in (("A", "AAA"), ("B", "BBB"), ("C", "CCC")):
            _master(conn, sid, tk)
            for d in _days(20):
                _bar(conn, sid, d)


def _backup_and_open(settings: Settings, tmp_path: Path) -> Path:
    backup = tmp_path / "backup.duckdb"
    backup.write_bytes(Path(settings.store.path).read_bytes())
    app = cli.make_app(settings=lambda: settings, clock=lambda: NOW)
    res = CliRunner().invoke(
        app,
        [
            "decision",
            "data-release",
            "open",
            "--name",
            "rel",
            "--backup",
            str(backup),
            "--reason",
            "t",
        ],
    )
    assert res.exit_code == 0, res.output
    return backup


def _coverage(settings: Settings) -> tuple[int, str]:
    app = cli.make_app(settings=lambda: settings, clock=lambda: NOW)
    res = CliRunner().invoke(app, ["decision", "data-release", "coverage", "--name", "rel"])
    return res.exit_code, res.output


def _edit(settings: Settings, sql: str, params: list[object] | None = None) -> None:
    with open_for_write(settings) as conn:
        conn.execute(sql, params or [])


def test_deleted_span_is_reported_and_exits_1(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _seed(s)
    _backup_and_open(s, tmp_path)
    _edit(s, "DELETE FROM prices_daily WHERE security_id='A' AND session >= ?", [_days(20)[8]])
    code, out = _coverage(s)
    assert code == 1, out
    assert "A AAA Inc AAA: lost 2020-01-09..2020-01-20, 12 sessions, NOT MOVED" in out


def test_span_moved_to_a_successor_id_exits_0(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _seed(s)
    _backup_and_open(s, tmp_path)
    with open_for_write(s) as conn:
        conn.execute("DELETE FROM prices_daily WHERE security_id='A'")
        _master(conn, "A2", "AAA2")
        for d in _days(20):
            _bar(conn, "A2", d)
    code, out = _coverage(s)
    assert code == 0, out
    assert "A AAA Inc AAA: lost 2020-01-01..2020-01-20, 20 sessions, moved to A2" in out


def test_loss_at_or_below_threshold_passes(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _seed(s)
    _backup_and_open(s, tmp_path)
    _edit(s, "DELETE FROM prices_daily WHERE security_id='A' AND session >= ?", [_days(20)[15]])
    code, out = _coverage(s)  # 5 lost, default threshold 5: not above it
    assert code == 0, out
    assert "0 security(ies) lost more than 5 sessions" in out


def test_small_losses_across_securities_exceed_store_total(tmp_path: Path) -> None:
    s = Settings(
        _env_file=None,
        store={
            "path": str(tmp_path / "store.duckdb"),
            "lock_retry_seconds": 0,
            "release_max_lost_sessions": 5,
            "release_max_total_lost_sessions": 4,
        },
    )
    _seed(s)
    _backup_and_open(s, tmp_path)
    _edit(s, "DELETE FROM prices_daily WHERE session >= ?", [_days(20)[18]])
    code, out = _coverage(s)
    assert code == 1, out
    assert "6 total unmoved sessions" in out
    for sid in ("A", "B", "C"):
        assert f"{sid} " in out


def test_missing_backup_table_is_named_refusal(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _seed(s)
    backup = _backup_and_open(s, tmp_path)
    with duckdb.connect(str(backup)) as conn:
        conn.execute("CREATE SCHEMA other")
        conn.execute("CREATE TABLE other.listings AS SELECT * FROM main.listings")
        conn.execute("DROP TABLE listings")
    code, out = _coverage(s)
    assert code == 1, out
    assert "ReleaseCoverageError" in out and "listings" in out
    assert "Traceback" not in out and "Catalog Error" not in out


def test_a_revised_bar_is_no_loss(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _seed(s)
    _backup_and_open(s, tmp_path)
    with open_for_write(s) as conn:
        conn.execute(
            "UPDATE prices_daily SET known_at = known_at + INTERVAL 1 HOUR, "
            "ingested_at = ingested_at + INTERVAL 1 HOUR WHERE security_id='A'"
        )
    assert _coverage(s)[0] == 0


def test_check_writes_nothing(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _seed(s)
    backup = _backup_and_open(s, tmp_path)
    _edit(s, "DELETE FROM prices_daily WHERE security_id='B'")
    store, before = Path(s.store.path), (Path(s.store.path).read_bytes(), backup.read_bytes())
    assert _coverage(s)[0] == 1
    assert (store.read_bytes(), backup.read_bytes()) == before


def test_unknown_release_is_a_usage_error(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _seed(s)
    code, out = _coverage(s)
    assert code == 2 and "no release named" in out


def test_core_counts_partial_move(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _seed(s)
    backup = _backup_and_open(s, tmp_path)
    with open_for_write(s) as conn:
        conn.execute("DELETE FROM prices_daily WHERE security_id='A'")
        for d in _days(8):
            _bar(conn, "C2", d)  # same bar as A's: a real move
        for d in _days(20):
            _bar(conn, "Z", d, close=9.0)  # unrelated prices: not a move
    with duckdb.connect(s.store.path, read_only=True) as conn:
        conn.execute(f"ATTACH '{backup}' AS bk (READ_ONLY)")
        losses = release_coverage(conn, backup_alias="bk")
    (loss,) = losses
    assert (loss.lost, loss.moved, loss.unmoved, loss.moved_to) == (20, 8, 12, ("C2",))
    assert failing(losses, max_lost_sessions=5) == losses


def test_sub_dollar_move_uses_absolute_price_floor(tmp_path: Path) -> None:
    s = _settings(tmp_path)
    _seed(s)
    with open_for_write(s) as conn:
        conn.execute("UPDATE prices_daily SET low = 0.5, close = 0.5 WHERE security_id = 'A'")
    _backup_and_open(s, tmp_path)
    with open_for_write(s) as conn:
        conn.execute("DELETE FROM prices_daily WHERE security_id = 'A'")
        for d in _days(20):
            _bar(conn, "A2", d, close=0.50000075)
    code, out = _coverage(s)
    assert code == 0, out
    assert "20 sessions, moved to A2" in out


def test_unknown_release_name_is_scrubbed(tmp_path: Path) -> None:
    secret = "release-name-secret-value"
    s = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 0},
        fred_api_key=secret,
    )
    _seed(s)
    app = cli.make_app(settings=lambda: s, clock=lambda: NOW)
    res = CliRunner().invoke(app, ["decision", "data-release", "coverage", "--name", secret])
    assert res.exit_code == 2
    assert "no release named '<scrubbed>'" in res.output
    assert secret not in res.output
