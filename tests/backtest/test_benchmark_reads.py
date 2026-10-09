"""Benchmark reads by symbol (#840).

The fixture universe with its benchmark master rows (SEC_SPY, SEC_MTUM) re-stamped
the way the real store holds SPY's: `snapshot_static` rows known only at the
snapshot fetch (2026-10-03), long after every in-sample `t`, while their bars stay
known at each session's close. Under the strict #35 rule the provider found no
benchmark at any in-sample `t`, and every trial failed at `write_results`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
from conftest import load_universe_fixtures

from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.config import Settings
from tradepartner.store import registry, schema
from tradepartner.store.benchmarks import BenchmarkIdentityError, benchmark_security_ids
from tradepartner.store.db import configure_connection, insert_row, open_for_write, open_read_only
from tradepartner.store.master import securities_as_of

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
SNAPSHOT = datetime(2026, 10, 3, 2, 16, 55, tzinfo=UTC)
T_JAN = datetime(2019, 1, 31, 21, 0, tzinfo=UTC)
T_EARLY = datetime(2018, 3, 29, 20, 0, tzinfo=UTC)
JAN = date(2019, 1, 31)


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _settings(path: Path, **extra: Any) -> Settings:
    return Settings(_env_file=None, store={"path": str(path)}, **extra)


def _late_benchmarks(conn: duckdb.DuckDBPyConnection) -> None:
    """Benchmark master rows known only at the 2026 snapshot, as on the real store."""
    for table in ("securities", "listings", "classifications"):
        conn.execute(
            f"UPDATE {table} SET known_at = ?, ingested_at = ? "
            "WHERE security_id IN ('SEC_SPY', 'SEC_MTUM')",
            [SNAPSHOT, SNAPSHOT],
        )


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.settings = _settings(path)
        conn = duckdb.connect(str(path))
        try:
            configure_connection(conn)
            schema.init_schema(conn)
            load_universe_fixtures(conn, UNIVERSE_DIR)
            _late_benchmarks(conn)
        finally:
            conn.close()
        registry_settings = _settings(path.parent / "real.duckdb")
        with open_for_write(self.settings) as conn:
            hypothesis = registry.register_hypothesis(
                conn,
                slug="h-bench",
                family="momentum",
                title="test",
                doc_path="docs/hypotheses/h-bench.md",
                doc_sha256="0" * 64,
                params={"costs.per_side_bps": 15.0},
                in_sample_start=date(2017, 1, 31),
                holdout_start=date(2023, 1, 1),
                holdout_end=date(2025, 12, 31),
                registered_by="owner",
                settings=registry_settings,
            )
            self.handle = registry.open_trial(
                conn,
                hypothesis_id=hypothesis.hypothesis_id,
                kind="in_sample",
                start_session=date(2018, 10, 31),
                end_session=date(2019, 7, 31),
                data_cutoff=datetime(2019, 7, 31, 20, tzinfo=UTC),
                synthetic=True,
                run_by="test",
                settings=registry_settings,
            )

    def connect(self) -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        return open_read_only(self.settings)

    def provider(self, frozen: Settings | None = None) -> StoreProvider:
        return StoreProvider(self.connect, self.handle, frozen or self.settings)

    @contextmanager
    def write(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with open_for_write(self.settings) as conn:
            yield conn


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "late.duckdb")


def _listing(sid: str, ticker: str, valid_from: date) -> dict[str, Any]:
    known = datetime(2018, 1, 2, 21, 0, tzinfo=UTC)
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": "NYSE",
        "class_title": "Common Stock",
        "valid_from": valid_from,
        "known_at": known,
        "ingested_at": known,
        "source": "edgar",
        "provenance": "filing",
    }


def _security(sid: str, *, benchmark: bool) -> dict[str, Any]:
    known = datetime(2018, 1, 2, 21, 0, tzinfo=UTC)
    return {
        "security_id": sid,
        "cik": "CIK0001999999",
        "name": sid,
        "benchmark": benchmark,
        "known_at": known,
        "ingested_at": known,
        "source": "edgar",
        "provenance": "filing",
    }


# --- the #840 reproduction ------------------------------------------------------


def test_snapshot_static_benchmark_rows_hidden_by_the_strict_rule(store: Store) -> None:
    """The cause, on the store: the master rows are invisible at an in-sample t."""
    with store.write() as conn:
        known = securities_as_of(conn, T_JAN)
    assert not known.filter(pl.col("benchmark")).height


def test_benchmark_ids_found_by_symbol_at_an_in_sample_t(store: Store) -> None:
    """#840: the provider still finds SPY and MTUM at T_JAN."""
    with store.provider() as provider:
        assert provider.benchmark_ids(T_JAN) == {"MTUM": "SEC_MTUM", "SPY": "SEC_SPY"}
        assert provider.benchmark_ids(T_EARLY, through=JAN) == {
            "MTUM": "SEC_MTUM",
            "SPY": "SEC_SPY",
        }


def test_only_the_frozen_benchmark_names(store: Store) -> None:
    with store.provider(_settings(store.path, benchmarks=["SPY"])) as provider:
        assert provider.benchmark_ids(T_JAN) == {"SPY": "SEC_SPY"}


def test_a_configured_benchmark_missing_from_the_store_is_refused_by_name(store: Store) -> None:
    with (
        store.provider(_settings(store.path, benchmarks=["SPY", "QUAL"])) as provider,
        pytest.raises(BenchmarkIdentityError, match=r"benchmark QUAL: .*benchmark-backfill"),
    ):
        provider.benchmark_ids(T_JAN)


# --- fail closed on identity ----------------------------------------------------


def test_a_symbol_listed_by_two_benchmark_securities_is_refused(store: Store) -> None:
    with store.write() as conn:
        insert_row(conn, "securities", _security("BENCH:SPY", benchmark=True))
        insert_row(conn, "listings", _listing("BENCH:SPY", "SPY", date(2018, 1, 2)))
    with (
        store.provider() as provider,
        pytest.raises(BenchmarkIdentityError, match=r"benchmark SPY is ambiguous"),
    ):
        provider.benchmark_ids(T_JAN)


def test_a_ticker_reused_inside_the_window_is_refused(store: Store) -> None:
    """Another company's listing under 'SPY' from 2019-03-01: a run through 2019-06
    is refused, one ending before the reuse is not."""
    with store.write() as conn:
        insert_row(conn, "securities", _security("OTHER", benchmark=False))
        insert_row(conn, "listings", _listing("OTHER", "spy ", date(2019, 3, 1)))
    with store.provider() as provider:
        assert provider.benchmark_ids(T_JAN, through=date(2019, 2, 28))["SPY"] == "SEC_SPY"
        with pytest.raises(BenchmarkIdentityError, match=r"benchmark SPY .*reused.*OTHER"):
            provider.benchmark_ids(T_JAN, through=date(2019, 6, 28))
        with pytest.raises(BenchmarkIdentityError, match=r"reused"):
            provider.benchmark_ids(T_JAN)  # no end given: open-ended, refused


def test_a_reuse_that_ended_before_the_window_is_not_refused(store: Store) -> None:
    """A company that held 'MTUM' in 2017 and moved to another ticker in 2018."""
    with store.write() as conn:
        insert_row(conn, "securities", _security("OLD", benchmark=False))
        insert_row(conn, "listings", _listing("OLD", "MTUM", date(2017, 1, 3)))
        insert_row(conn, "listings", _listing("OLD", "OLDX", date(2018, 6, 1)))
        ids = benchmark_security_ids(conn, ["MTUM"], start=JAN, through=date(2019, 6, 28))
        assert ids == {"MTUM": "SEC_MTUM"}
        with pytest.raises(BenchmarkIdentityError, match=r"reused.*OLD"):
            benchmark_security_ids(conn, ["MTUM"], start=date(2018, 1, 2), through=JAN)


def test_a_security_no_longer_flagged_benchmark_is_not_one(store: Store) -> None:
    """The latest `securities` row decides the flag."""
    later = datetime(2026, 10, 4, tzinfo=UTC)
    with store.write() as conn:
        insert_row(
            conn,
            "securities",
            _security("SEC_MTUM", benchmark=False) | {"known_at": later, "ingested_at": later},
        )
    with (
        store.provider() as provider,
        pytest.raises(BenchmarkIdentityError, match=r"benchmark MTUM: no benchmark"),
    ):
        provider.benchmark_ids(T_JAN)


# --- the bars stay point-in-time ------------------------------------------------


def test_benchmark_bars_are_point_in_time(store: Store) -> None:
    """No look-ahead: only bars known at t, latest revision as of t; a revision known
    after t is not seen at t."""
    session = date(2019, 1, 15)
    revised_at = datetime(2019, 2, 5, 21, 0, tzinfo=UTC)
    with store.write() as conn:
        (bar,) = conn.execute(
            "SELECT * FROM prices_daily WHERE security_id = 'SEC_SPY' AND session = ?", [session]
        ).fetchall()
        names = [d[0] for d in conn.description]  # type: ignore[union-attr]
        row = dict(zip(names, bar, strict=True))
        insert_row(
            conn,
            "prices_daily",
            row
            | {
                "close": row["close"] * 2,
                "high": row["high"] * 2,
                "known_at": revised_at,
                "ingested_at": revised_at,
            },
        )
    with store.provider() as provider:
        sid = provider.benchmark_ids(T_JAN)["SPY"]
        raw = provider.raw_prices(T_JAN, [sid])
        later = provider.raw_prices(datetime(2019, 2, 28, 21, 0, tzinfo=UTC), [sid])
    assert raw["session"].max() == JAN
    assert (raw["known_at"] <= T_JAN).all()
    at_session = raw.filter(pl.col("session") == session)["close"].item()
    assert at_session == pytest.approx(row["close"])
    assert later.filter(pl.col("session") == session)["close"].item() == pytest.approx(
        row["close"] * 2
    )


# --- never a universe member ----------------------------------------------------


@pytest.mark.parametrize("t", [T_EARLY, T_JAN, datetime(2026, 10, 5, 21, 0, tzinfo=UTC)])
def test_a_benchmark_is_never_a_universe_member(store: Store, t: datetime) -> None:
    """Reading benchmarks by symbol puts nothing in the universe, even once their master
    rows are known (the last `t`, after the snapshot)."""
    with store.provider() as provider:
        ids = set(provider.benchmark_ids(T_JAN).values())
        members = set(provider.universe(t).members["security_id"].to_list())
    assert ids == {"SEC_SPY", "SEC_MTUM"}
    assert not ids & members
