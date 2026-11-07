"""`tradepartner close-gap` (#3077): read-only, exits 1 on a gap, fail-closed."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest
from backtest.test_index_check_store import NOW, build_store
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.config import Settings


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


@pytest.fixture
def store(tmp_path: Path) -> Settings:
    return build_store(tmp_path / "s.duckdb", boundary=date(2019, 6, 28))


def invoke(settings: Settings, *args: str) -> tuple[int, str]:
    app = cli.make_app(settings=lambda: settings, clock=lambda: NOW)
    result = CliRunner().invoke(app, ["close-gap", *args])
    return result.exit_code, result.output


def stored_closes(settings: Settings) -> list[tuple[date, float]]:
    conn = duckdb.connect(settings.store.path, read_only=True)
    try:
        return conn.execute(
            "SELECT session, close FROM prices_daily WHERE security_id = 'BENCH:RSP' "
            "AND volume > 0 ORDER BY session LIMIT 3"
        ).fetchall()
    finally:
        conn.close()


def reference(path: Path, rows: list[tuple[date, float]]) -> Path:
    path.write_text("session,close\n" + "".join(f"{d},{c}\n" for d, c in rows))
    return path


def test_matching_reference_passes(store: Settings, tmp_path: Path) -> None:
    ref = reference(tmp_path / "r.csv", stored_closes(store))
    code, out = invoke(store, "--reference", str(ref), "--symbol", "RSP")
    assert code == 0, out
    assert "0 of 3 official sessions" in out


def test_a_gap_and_a_missing_bar_exit_one(store: Settings, tmp_path: Path) -> None:
    rows = stored_closes(store)
    off = [(rows[0][0], rows[0][1] * 1.03), rows[1], (date(2019, 6, 1), 10.0)]
    ref = reference(tmp_path / "r.csv", off)
    code, out = invoke(store, "--reference", str(ref), "--symbol", "RSP")
    assert code == 1
    assert "2 of 3" in out and "no stored bar" in out and "-2.91%" in out


def test_empty_reference_refuses(store: Settings, tmp_path: Path) -> None:
    ref = reference(tmp_path / "r.csv", [])
    code, out = invoke(store, "--reference", str(ref), "--symbol", "RSP")
    assert code == 2 and "empty reference" in out
