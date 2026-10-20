"""`tradepartner index-check` (backtest spec amendment #1598, T245).

The command runs on temp copies of the fixture universe with a check-only RSP planted
(`tests/backtest/test_index_check_store.py`); it never touches the real store.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
import typer
from backtest.test_index_check_store import (
    JULY,
    JUNE,
    NOW,
    build_store,
    settings_for,
    writer,
)
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.config import Settings

PROTECTED_LINE = (
    "protected months show gaps only (owner, 2026-10-12, #1598); not a trial, nothing written"
)
DOT = "·"


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


@pytest.fixture
def store(tmp_path: Path) -> Settings:
    return build_store(tmp_path / "s.duckdb", boundary=date(2019, 6, 28))


def invoke(settings: Settings, *args: str) -> tuple[int, str]:
    app = cli.make_app(settings=lambda: settings, clock=lambda: NOW)
    result = CliRunner().invoke(app, ["index-check", *args])
    return result.exit_code, result.output


def table(output: str, first_header: str) -> list[dict[str, str]]:
    """The rows of the ` | ` table whose header starts with `first_header`, by column."""
    lines = output.splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if " | " in line and line.split(" | ")[0].strip() == first_header
    )
    headers = [c.strip() for c in lines[start].split(" | ")]
    rows: list[dict[str, str]] = []
    for line in lines[start + 2 :]:
        if not line.strip():
            break
        rows.append(dict(zip(headers, [c.strip() for c in line.split(" | ")], strict=False)))
    return rows


def test_the_option_set_is_pinned() -> None:
    command = typer.main.get_command(cli.make_app()).commands["index-check"]  # type: ignore[attr-defined]
    flags = {opt for param in command.params for opt in param.opts}
    assert flags == {"--start", "--end", "--out"}


def test_it_prints_the_header_and_the_tables(store: Settings) -> None:
    code, out = invoke(store, "--start", JUNE, "--end", JULY)
    assert code == 0, out
    assert PROTECTED_LINE in out
    assert f"store: {store.store.path}" in out
    assert "edge E: 2019-06-28 (development_boundary)" in out
    assert "schema version:" in out and "code version:" in out and "data vintage" in out
    assert "adjust.apply_spin_offs:" in out and '"rank_by": "cap"' in out
    assert "worst months, equal weight vs RSP" in out and "worst months, cap weight vs SPY" in out


def test_a_protected_months_level_cells_are_dots_by_column(store: Settings) -> None:
    _, out = invoke(store, "--start", JUNE, "--end", JULY)
    june, july = table(out, "month")
    levels = ["EW", "RSP", "CW", "SPY"]
    assert all(july[c] == DOT for c in levels)
    assert all(june[c] not in ("", DOT) for c in levels)
    gaps = ["EW-RSP", "CW-SPY"]
    assert all(july[c].endswith("%") and june[c].endswith("%") for c in gaps)
    (year,) = [r for r in table(out, "year") if r["year"] == "2019"]
    assert all(year[c] == DOT for c in ("EW", "RSP", "CW", "SPY"))
    zones = {r["year"]: r for r in table(out, "year") if r["year"].endswith("total")}
    assert zones["protected total"]["EW"] == DOT
    assert zones["development total"]["EW"] not in ("", DOT)


def test_out_writes_the_masked_report_and_nothing_else(store: Settings, tmp_path: Path) -> None:
    out_path = tmp_path / "lib" / "index-check.json"
    code, _ = invoke(store, "--start", JUNE, "--end", JULY, "--out", str(out_path))
    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    june, july = payload["months"]
    assert "equal_return" in june and "cap_bench_return" in june
    assert not any("return" in key for key in july)
    assert payload["header"]["protected_line"] == PROTECTED_LINE
    assert payload["header"]["code_version"]
    protected = [n for w in payload["worst"]["cap"] if w["month"] == JULY for n in w["names"]]
    assert protected and not any("name_return" in n for n in protected)


def test_a_run_writes_no_row_to_the_store(store: Settings) -> None:
    def counts() -> dict[str, int]:
        with writer(store) as conn:
            return {
                t: conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]  # type: ignore[index]
                for (t,) in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
            }

    before = counts()
    assert invoke(store, "--start", JUNE, "--end", JULY)[0] == 0
    assert counts() == before


def test_it_reads_the_configured_store_and_a_missing_one_exits_1(tmp_path: Path) -> None:
    code, out = invoke(settings_for(tmp_path / "nothing-here.duckdb"), "--start", JUNE)
    assert code == 1 and "no store at" in out


@pytest.mark.parametrize(
    ("args", "needle"),
    [
        (["--start", JULY, "--end", JUNE], "refused_window"),
        (["--start", "2019-6", "--end", JULY], "refused_window"),
        (["--start", JUNE, "--end", "2026-10"], "refused_window"),
        (["--start", "1990-01", "--end", "1990-02"], "refused_window"),
    ],
)
def test_a_refused_window_exits_2(store: Settings, args: list[str], needle: str) -> None:
    code, out = invoke(store, *args)
    assert code == 2 and needle in out
    assert "by month" not in out


def test_a_rank_by_other_than_cap_exits_2(tmp_path: Path) -> None:
    path = tmp_path / "d.duckdb"
    build_store(path)
    code, out = invoke(settings_for(path, universe={"rank_by": "dollar_volume"}), "--start", JUNE)
    assert code == 2 and "refused_rank_by" in out


def test_a_benchmark_identity_error_exits_2(store: Settings) -> None:
    with writer(store) as conn:
        conn.execute("UPDATE securities SET benchmark = FALSE WHERE security_id = 'BENCH:RSP'")
    code, out = invoke(store, "--start", JUNE, "--end", JULY)
    assert code == 2 and "RSP" in out


def test_a_missing_benchmark_bar_exits_1_with_its_name(store: Settings) -> None:
    with writer(store) as conn:
        conn.execute(
            "DELETE FROM prices_daily WHERE security_id = 'SEC_SPY' AND session = '2019-07-31'"
        )
    code, out = invoke(store, "--start", JUNE, "--end", JULY)
    assert code == 1
    assert "missing_benchmark_bar SPY 2019-07-31" in out
    assert "by month" not in out
