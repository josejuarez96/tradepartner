"""`tradepartner identity-check`: the identity answer key against a store (#3275,
ADR 0029, T396).

Stores are temp files built from the 11-09 sitting rows of Apache and Marriott
(`tests/fixtures/form25_withdrawals`); the command never touches the real store.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
import typer
from store.test_form25_withdrawals import _bar, _load
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.calendar import all_sessions, last_completed_session
from tradepartner.config import Settings
from tradepartner.store import identity_check
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.identity_answer_key import parse_key
from tradepartner.store.schema import init_schema

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)
APA, APA_CORP, MAR = "0000006769", "0001841666", "0001048286"
MAR_PRESS = (
    "https://www.prnewswire.com/news-releases/"
    "marriott-international-inc-to-delist-from-nyse-chicago-stock-exchange-301120767.html"
)


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _securities(conn: duckdb.DuckDBPyConnection) -> None:
    """One `securities` row per security id the fixture listings name."""
    ids = [r[0] for r in conn.execute("SELECT DISTINCT security_id FROM listings").fetchall()]
    for sid in ids:
        insert_row(
            conn,
            "securities",
            {
                "security_id": sid,
                "cik": sid.split(":")[0],
                "name": sid,
                "known_at": datetime(2019, 1, 1, tzinfo=UTC),
                "ingested_at": datetime(2019, 1, 1, tzinfo=UTC),
                "source": "edgar",
                "provenance": "filing",
            },
        )


def _fill_bars(conn: duckdb.DuckDBPyConnection) -> None:
    """The fixture holds only the bars around the 2020 and 2021 events; the key
    also has the securities trading on, so add every session of those spans."""
    spans = {
        APA: (date(2020, 6, 9), date(2021, 2, 26)),
        APA_CORP: (date(2021, 3, 1), last_completed_session(NOW)),
        MAR: (date(2020, 6, 9), last_completed_session(NOW)),
    }
    for sid, (first, last) in spans.items():
        have = {
            r[0]
            for r in conn.execute(
                "SELECT session FROM prices_daily WHERE security_id = ?", [sid]
            ).fetchall()
        }
        for session in all_sessions():
            if first <= session <= last and session not in have:
                _bar(conn, sid, session)


def build(path: Path, *, with_data: bool = True) -> Settings:
    settings = Settings(_env_file=None, store={"path": str(path)})
    with open_for_write(settings) as conn:
        if with_data:
            _load(conn)
            _securities(conn)
            _fill_bars(conn)
        else:
            init_schema(conn)
    return settings


@pytest.fixture
def store(tmp_path: Path) -> Settings:
    return build(tmp_path / "s.duckdb")


def invoke(settings: Settings, *args: str) -> tuple[int, str]:
    app = cli.make_app(settings=lambda: settings, clock=lambda: NOW)
    result = CliRunner().invoke(app, ["identity-check", *args])
    return result.exit_code, result.output


def digest(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def key(*, last_end: str = "2020-09-18") -> str:
    """Marriott's history alone, with the NYSE Chicago line_end date a parameter."""
    cite = f'cite = {{ kind = "press_release", ref = "{MAR_PRESS}", date = 2020-08-31 }}'
    return f"""
[[history]]
id = "mar"
[[history.bars]]
cik = "{MAR}"
ticker = "MAR"
first = 2020-09-21
{cite}
[[history.expected]]
kind = "line_end"
cik = "{MAR}"
exchange = "NYSE Chicago"
last = {last_end}
{cite}
[[history.expected]]
kind = "line_end"
cik = "{MAR}"
exchange = "Nasdaq"
{cite}
[[history.expected]]
kind = "holds"
cik = "{MAR}"
ticker = "MAR"
first = 2020-09-21
{cite}
[[history.expected]]
kind = "bars"
cik = "{MAR}"
ticker = "MAR"
first = 2020-09-21
last = 2020-12-15
{cite}
"""


def run(settings: Settings, text: str) -> identity_check.CheckReport:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return identity_check.check(conn, settings, parse_key(text, NOW.date()), NOW)


def test_a_true_key_passes_on_the_fixture_store(store: Settings) -> None:
    report = run(store, key())
    assert report.misses == ()
    assert report.histories == 1


def test_a_wrong_line_end_is_a_miss_that_names_its_citation(store: Settings) -> None:
    report = run(store, key(last_end="2020-09-17"))
    (miss,) = report.misses
    assert miss.history == "mar"
    assert "line_end" in miss.why and "2020-09-18" in miss.why
    assert MAR_PRESS in str(miss) and "2020-08-31" in str(miss)


def test_a_store_without_the_security_misses_every_expectation(tmp_path: Path) -> None:
    empty = build(tmp_path / "e.duckdb", with_data=False)
    report = run(empty, key())
    assert len(report.misses) == 4


def test_a_bar_gap_in_the_window_is_a_miss(store: Settings) -> None:
    with open_for_write(store) as conn:
        conn.execute(
            "DELETE FROM prices_daily WHERE security_id = ? AND session = ?",
            [MAR, date(2020, 10, 1)],
        )
    report = run(store, key())
    assert [m.expected.kind for m in report.misses] == ["bars"]


def test_the_packaged_seeds_run_and_marriott_agrees_with_the_outside_sources(
    store: Settings,
) -> None:
    code, out = invoke(store)
    assert "mar-nyse-chicago-2020: ok" in out
    assert "apa-apache-holdco-2021: " in out
    assert (code == 1) == ("FAIL" in out)


def test_the_cli_passes_with_exit_0_on_a_history_the_store_meets(store: Settings) -> None:
    code, out = invoke(store, "--history", "mar-nyse-chicago-2020")
    assert code == 0, out
    assert "0 misses" in out and f"store: {store.store.path}" in out


def test_a_miss_prints_its_citation_and_exits_1(
    store: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    wrong = parse_key(key(last_end="2020-09-17"), NOW.date())
    monkeypatch.setattr(identity_check, "load_answer_key", lambda check_date=None: wrong)
    code, out = invoke(store)
    assert code == 1
    (miss_line,) = [line for line in out.splitlines() if line.startswith("  miss ")]
    assert f"[press_release {MAR_PRESS}, 2020-08-31]" in miss_line
    assert "mar: FAIL (1)" in out


def test_an_unknown_history_is_a_usage_error(store: Settings) -> None:
    code, out = invoke(store, "--history", "nope")
    assert code == 2
    assert "nope" in out


def test_a_missing_store_is_exit_2(tmp_path: Path) -> None:
    gone = Settings(_env_file=None, store={"path": str(tmp_path / "gone.duckdb")})
    code, out = invoke(gone)
    assert code == 2
    assert "no store" in out


def test_store_option_points_at_another_file(store: Settings, tmp_path: Path) -> None:
    other = Settings(_env_file=None, store={"path": str(tmp_path / "nowhere.duckdb")})
    code, out = invoke(other, "--store", store.store.path, "--history", "mar-nyse-chicago-2020")
    assert code == 0, out
    assert store.store.path in out


def test_a_run_writes_nothing(store: Settings) -> None:
    before = digest(store.store.path)
    invoke(store)
    assert digest(store.store.path) == before


def test_the_store_is_opened_read_only(store: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    modes: list[bool] = []
    real = duckdb.connect

    def spy(*args: object, **kwargs: object) -> duckdb.DuckDBPyConnection:
        modes.append(bool(kwargs.get("read_only")))
        return real(*args, **kwargs)  # type: ignore[call-overload,no-any-return]

    monkeypatch.setattr(duckdb, "connect", spy)
    invoke(store)
    assert modes and all(modes)


def test_the_option_set_is_pinned() -> None:
    command = typer.main.get_command(cli.make_app()).commands["identity-check"]  # type: ignore[attr-defined]
    assert {opt for p in command.params for opt in p.opts} >= {"--store", "--history"}


def test_rows_known_after_the_check_leave_the_result_unchanged(store: Settings) -> None:
    before = run(store, key())
    later = datetime(2027, 1, 1, tzinfo=UTC)
    with open_for_write(store) as conn:
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": MAR,
                "session": date(2020, 10, 1),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 0,
                "known_at": later,
                "ingested_at": later,
                "source": "alpaca_sip",
                "provenance": "bar",
            },
        )
        insert_row(
            conn,
            "securities",
            {
                "security_id": MAR,
                "cik": "0000000001",
                "name": "x",
                "known_at": later,
                "ingested_at": later,
                "source": "edgar",
                "provenance": "filing",
            },
        )
    # a zero-volume bar revision and a CIK change known after the check change nothing
    assert before.misses == ()
    assert run(store, key()).misses == ()


def test_a_retracted_security_is_not_read(store: Settings) -> None:
    t = datetime(2026, 11, 1, tzinfo=UTC)
    with open_for_write(store) as conn:
        insert_row(
            conn,
            "securities",
            {
                "security_id": MAR,
                "cik": MAR,
                "name": "x",
                "retracted": True,
                "known_at": t,
                "ingested_at": t,
                "source": "edgar",
                "provenance": "filing",
            },
        )
    assert run(store, key()).misses
