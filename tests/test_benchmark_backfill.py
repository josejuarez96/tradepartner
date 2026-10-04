"""The owner's one-off benchmark backfill (#840): `backfill.backfill_benchmark` and
`tradepartner backfill-benchmark`.

A temp-file store and `test_backfill`'s `_History` stub price source: nothing here
touches the network or the owner's store.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from test_backfill import _History, _sessions
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.adapters.prices import (
    ActionType,
    CorporateAction,
    action_first_seen_known_at,
    bar_known_at,
)
from tradepartner.backfill import BenchmarkSeed, backfill_benchmark
from tradepartner.config import Settings
from tradepartner.ingest import OK, STALE
from tradepartner.store.benchmarks import BenchmarkIdentityError, benchmark_security_ids
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

SINCE = date(2019, 4, 10)
NOW = datetime(2019, 6, 29, 2, 0, tzinfo=UTC)  # after the 2019-06-28 close and settle
MTUM = "BENCH:MTUM"
SEED = BenchmarkSeed(cik="0001100663", name="iShares MSCI USA Momentum Factor ETF", exchange="CBOE")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 0},
        alpaca_api_key="test-key",
        alpaca_api_secret="test-secret",
    )
    with open_for_write(s) as conn:
        init_schema(conn)
    return s


def _read(settings: Settings, sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        return conn.execute(sql).fetchall()


def _run(settings: Settings, prices: _History, **kwargs: Any) -> Any:
    kwargs.setdefault("symbol", "MTUM")
    kwargs.setdefault("seed", SEED)
    return backfill_benchmark(
        settings, prices=prices, since=SINCE, clock=kwargs.pop("clock", lambda: NOW), **kwargs
    )


def test_seeds_the_missing_benchmark_and_backfills_its_bars(settings: Settings) -> None:
    ex = date(2019, 6, 20)
    div = CorporateAction(MTUM, ActionType.DIVIDEND, ex, 0.3, action_first_seen_known_at(ex), "x")
    prices = _History(actions=[div])
    result = _run(settings, prices)
    assert (result.security_id, result.seeded, result.ok) == (MTUM, True, True)
    assert [r.status for r in result.runs] == [OK] * 3
    assert {c[1] for c in prices.calls if c[0] == "bars"} == {
        date(2019, 4, 10),
        date(2019, 5, 1),
        date(2019, 6, 1),
    }
    assert all(ids == {MTUM} for ids in prices.fetched.values())  # nothing else fetched
    assert _read(
        settings, "SELECT security_id, cik, name, benchmark, source, provenance FROM securities"
    ) == [(MTUM, SEED.cik, SEED.name, True, "config", "snapshot_static")]
    [(ticker, exchange, valid_from, known_at)] = _read(
        settings, "SELECT ticker, exchange, valid_from, known_at FROM listings"
    )
    assert (ticker, exchange, valid_from <= SINCE, known_at) == ("MTUM", "CBOE", True, NOW)
    bars = _read(settings, "SELECT session, known_at FROM prices_daily ORDER BY session")
    assert [s for s, _ in bars] == _sessions(SINCE, date(2019, 6, 28))
    # Point in time: each bar is known at its own session's close, not at the run.
    assert all(known == bar_known_at(session) for session, known in bars)
    assert _read(settings, "SELECT ex_date FROM corporate_actions") == [(ex,)]
    # No run row: an `ok` row would read as a fresh store to the freshness checks.
    assert _read(settings, "SELECT count(*) FROM ingestion_runs") == [(0,)]
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        ids = benchmark_security_ids(conn, ["MTUM"], start=SINCE, through=date(2019, 6, 28))
    assert ids == {"MTUM": MTUM}


def test_a_repeat_finds_the_seeded_security_and_adds_nothing(settings: Settings) -> None:
    _run(settings, _History())
    again = _run(settings, _History(), seed=None, clock=lambda: NOW + timedelta(hours=1))
    assert (again.seeded, again.ok) == (False, True)
    assert [r.rows_added for r in again.runs] == [0, 0, 0]
    assert _read(settings, "SELECT count(*) FROM securities") == [(1,)]


def test_a_symbol_outside_the_benchmarks_is_refused_before_any_fetch(settings: Settings) -> None:
    prices = _History()
    with pytest.raises(ValueError, match="not a configured benchmark"):
        _run(settings, prices, symbol="AAPL")
    assert prices.calls == []
    assert _read(settings, "SELECT count(*) FROM securities") == [(0,)]


def test_a_missing_benchmark_without_a_seed_is_refused(settings: Settings) -> None:
    prices = _History()
    with pytest.raises(ValueError, match="MTUM is not in the store"):
        _run(settings, prices, seed=None)
    assert prices.calls == []


def test_seeding_is_refused_when_another_security_holds_the_ticker(settings: Settings) -> None:
    known = datetime(2018, 1, 2, 21, tzinfo=UTC)
    with open_for_write(settings) as conn:
        insert_row(
            conn,
            "listings",
            {
                "security_id": "0000000042",
                "ticker": "MTUM",
                "exchange": "NYSE",
                "class_title": "Common Stock",
                "valid_from": date(2018, 1, 2),
                "known_at": known,
                "ingested_at": known,
                "source": "edgar",
                "provenance": "filing",
            },
        )
    prices = _History()
    with pytest.raises(BenchmarkIdentityError, match="ticker held by 0000000042"):
        _run(settings, prices)
    assert prices.calls == []
    assert _read(settings, "SELECT count(*) FROM securities") == [(0,)]


def test_a_month_with_a_missing_session_is_stale_and_halts(settings: Settings) -> None:
    prices = _History(gaps={(MTUM, date(2019, 5, 14))})
    result = _run(settings, prices)
    assert [r.status for r in result.runs] == [OK, STALE]
    assert "2019-05-14" in result.runs[-1].message
    assert result.exit_code == 1
    assert _read(settings, "SELECT max(session) FROM prices_daily") == [(date(2019, 4, 30),)]


def test_the_seed_is_validated() -> None:
    with pytest.raises(ValueError, match="10-digit"):
        BenchmarkSeed(cik="1100663", name="x", exchange="CBOE")
    with pytest.raises(ValueError, match="name"):
        BenchmarkSeed(cik="0001100663", name=" ", exchange="CBOE")


# --- the command ------------------------------------------------------------------


def _invoke(settings: Settings, args: Sequence[str], prices: _History) -> Any:
    app = cli.make_app(settings=lambda: settings, clock=lambda: NOW, price_source=lambda s: prices)
    return CliRunner().invoke(app, ["backfill-benchmark", *args])


SEED_ARGS = ["--cik", SEED.cik, "--name", SEED.name, "--exchange", SEED.exchange]


def test_the_command_seeds_and_prints_one_line_per_month(settings: Settings) -> None:
    result = _invoke(settings, ["MTUM", "--since", "2019-04-10", *SEED_ARGS], _History())
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert lines[0] == f"MTUM: {MTUM} seeded"
    assert len(lines) == 4
    assert all(line.startswith("alpaca: ok") for line in lines[1:])


def test_the_command_refuses_a_partial_seed_and_a_foreign_symbol(settings: Settings) -> None:
    prices = _History()
    partial = _invoke(settings, ["MTUM", "--since", "2019-04-10", "--cik", SEED.cik], prices)
    assert partial.exit_code == 2
    assert "go together" in partial.output
    foreign = _invoke(settings, ["AAPL", "--since", "2019-04-10"], prices)
    assert foreign.exit_code == 2
    assert "not a configured benchmark" in foreign.output
    assert prices.calls == []


def test_the_command_needs_the_alpaca_keys(settings: Settings) -> None:
    keyless = settings.model_copy(update={"alpaca_api_key": None, "alpaca_api_secret": None})
    prices = _History()
    result = _invoke(keyless, ["MTUM", "--since", "2019-04-10", *SEED_ARGS], prices)
    assert result.exit_code == 2
    assert "ALPACA_API_KEY" in result.output
    assert "test-key" not in result.output
    assert prices.calls == []
