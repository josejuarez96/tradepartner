"""Tests for `backtest.store_provider.StoreProvider` (Phase 3 T38, spec req 1).

The provider is exercised on a temp-file copy of the fixture universe (a
read-only connection needs a file), with a trial opened through the
registry so the handle is real. Every read is compared with the as-of
function it stands for, called directly on the same store with the same
frozen `Settings`. Probe times are the fixture README's documented ones.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
from conftest import load_universe_fixtures

from tradepartner import gap as gap_module
from tradepartner.backtest.provider import DataProvider, GapReading
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.config import Settings
from tradepartner.store import registry, schema
from tradepartner.store.asof import adjusted_prices_as_of, dropped_dividends_as_of, prices_as_of
from tradepartner.store.db import (
    StoreLockedError,
    configure_connection,
    insert_row,
    open_for_write,
    open_read_only,
)
from tradepartner.store.delistings import listing_ends_as_of
from tradepartner.universe import universe_as_of

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
BACKTEST_DIR = Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "backtest"

T_DUAL = datetime(2018, 12, 17, 21, 0, tzinfo=UTC)
T_TRANSFER = datetime(2018, 10, 25, 20, 0, tzinfo=UTC)
T_AFTER_TRANSFER = datetime(2018, 11, 30, 21, 0, tzinfo=UTC)
T_WINDOW_DELIST = datetime(2019, 6, 24, 20, 0, tzinfo=UTC)
T_AFTER_DELIST = datetime(2019, 7, 31, 20, 0, tzinfo=UTC)
T_JAN = datetime(2019, 1, 31, 21, 0, tzinfo=UTC)
T_FEB = datetime(2019, 2, 28, 21, 0, tzinfo=UTC)
T_MAR = datetime(2019, 3, 29, 20, 0, tzinfo=UTC)


def _settings(path: Path, **universe: Any) -> Settings:
    return Settings(_env_file=None, store={"path": str(path)}, universe=universe)


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The provider compares the frozen calendar with the live settings; keep
    # the live ones free of any `.env`.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


class Store:
    def __init__(
        self,
        path: Path,
        handle: registry.TrialHandle,
        settings: Settings,
        closed: registry.TrialHandle,
    ) -> None:
        self.path = path
        self.handle = handle
        self.settings = settings
        self.closed = closed
        self.opened = 0
        self.open_now = 0

    def connect(self) -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        """A recording read-only connection factory."""

        @contextmanager
        def _connect() -> Iterator[duckdb.DuckDBPyConnection]:
            with open_read_only(self.settings) as conn:
                self.opened += 1
                self.open_now += 1
                try:
                    yield conn
                finally:
                    self.open_now -= 1

        return _connect()

    def provider(self, frozen: Settings | None = None) -> StoreProvider:
        return StoreProvider(self.connect, self.handle, frozen or self.settings)

    @contextmanager
    def direct(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with open_read_only(self.settings) as conn:
            yield conn


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    tmp = tmp_path_factory.mktemp("store_provider")
    path = tmp / "fixture.duckdb"
    settings = _settings(path)
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        load_universe_fixtures(conn, UNIVERSE_DIR)
        # A late dividend for this test only: ex-date 2019-01-15, first known
        # 2019-03-05, after the 2019-02-28 rebalance.
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": "SEC_DUAL_A",
                "action_type": "dividend",
                "ex_date": date(2019, 1, 15),
                "ratio_or_amount": 0.05,
                "announced_at": None,
                "known_at": datetime(2019, 3, 5, 21, 0, tzinfo=UTC),
                "ingested_at": datetime(2019, 3, 5, 21, 10, tzinfo=UTC),
                "source": "alpaca",
                "provenance": "action",
            },
        )
        # And one that is not late: first known 2019-03-04, ex-date after
        # the 2019-02-28 rebalance.
        insert_row(
            conn,
            "corporate_actions",
            {
                "security_id": "SEC_DUAL_B",
                "action_type": "dividend",
                "ex_date": date(2019, 3, 5),
                "ratio_or_amount": 0.04,
                "announced_at": None,
                "known_at": datetime(2019, 3, 4, 21, 0, tzinfo=UTC),
                "ingested_at": datetime(2019, 3, 4, 21, 10, tzinfo=UTC),
                "source": "alpaca",
                "provenance": "action",
            },
        )
    finally:
        conn.close()
    # Registry calls get another "real store" path, so this temp store may
    # hold a synthetic trial.
    registry_settings = _settings(tmp / "real.duckdb")
    with open_for_write(settings) as conn:
        hypothesis = registry.register_hypothesis(
            conn,
            slug="h-test",
            family="momentum",
            title="test",
            doc_path="docs/hypotheses/h-test.md",
            doc_sha256="0" * 64,
            params={"costs.per_side_bps": 15.0},
            in_sample_start=date(2017, 1, 31),
            holdout_start=date(2023, 1, 1),
            holdout_end=date(2025, 12, 31),
            registered_by="owner",
            settings=registry_settings,
        )
        handle = registry.open_trial(
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
        closed = registry.open_trial(
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
        registry.close_trial(conn, closed, "failed", "closed for the test")
    return Store(path, handle, settings, closed)


def _ids(frame: pl.DataFrame) -> set[str]:
    return set(frame["security_id"].to_list())


# --- the protocol and the reads ------------------------------------------------


def test_is_a_data_provider(store: Store) -> None:
    with store.provider() as provider:
        assert isinstance(provider, DataProvider)


@pytest.mark.parametrize("t", [T_DUAL, T_TRANSFER, T_WINDOW_DELIST, T_JAN])
def test_universe_equals_universe_as_of(store: Store, t: datetime) -> None:
    with store.provider() as provider:
        got = provider.universe(t)
    with store.direct() as conn:
        want = universe_as_of(conn, t, store.settings)
    assert got.members.equals(want.members)
    assert got.exclusions.equals(want.exclusions)
    assert got.session == want.session


def test_universe_at_readme_probes(store: Store) -> None:
    with store.provider() as provider:
        assert {"SEC_DUAL_A", "SEC_DUAL_B"} <= _ids(provider.universe(T_DUAL).members)
        assert "SEC_WINDOW_DELIST" in _ids(provider.universe(T_WINDOW_DELIST).members)
        # Delisted between the Form 25 and the new listing becoming known.
        assert "SEC_TRANSFER" not in _ids(provider.universe(T_TRANSFER).members)


def test_listing_end_of_the_delisted_name(store: Store) -> None:
    with store.provider() as provider:
        ends = provider.listing_ends(T_AFTER_DELIST, ["SEC_WINDOW_DELIST"])
    [row] = ends.iter_rows(named=True)
    assert (row["status"], row["end_session"]) == ("delisted", date(2019, 6, 24))
    with store.direct() as conn:
        want = listing_ends_as_of(conn, T_AFTER_DELIST, store.settings, ["SEC_WINDOW_DELIST"])
    assert ends.equals(want)


def test_transfer_is_delisted_until_the_new_listing_is_known(store: Store) -> None:
    with store.provider() as provider:
        before = provider.listing_ends(T_TRANSFER, ["SEC_TRANSFER"])
        after = provider.listing_ends(T_AFTER_TRANSFER, ["SEC_TRANSFER"])
    assert before["status"].to_list() == ["delisted"]
    assert "transferred" in after["status"].to_list()


def test_benchmark_ids_from_securities_benchmark(store: Store) -> None:
    with store.provider() as provider:
        assert provider.benchmark_ids(T_JAN) == {"MTUM": "SEC_MTUM", "SPY": "SEC_SPY"}
        assert provider.benchmark_ids(datetime(2017, 6, 30, 20, tzinfo=UTC)) == {}
    only_spy = Settings(_env_file=None, store={"path": str(store.path)}, benchmarks=["SPY"])
    with store.provider(only_spy) as provider:
        assert provider.benchmark_ids(T_JAN) == {"SPY": "SEC_SPY"}


def test_gap_equals_survivorship_gap(store: Store) -> None:
    with store.provider() as provider:
        got = provider.survivorship_gap(T_WINDOW_DELIST)
    with store.direct() as conn:
        want = gap_module.survivorship_gap(conn, T_WINDOW_DELIST, store.settings)
    assert got == GapReading(count_share=want.count_share, size_share=want.size_share)


@pytest.mark.parametrize("include_dividends", [False, True])
def test_adjusted_prices_equal_the_as_of_read(store: Store, include_dividends: bool) -> None:
    ids = ["SEC_SPLIT_BETWEEN", "SEC_DIV_REVISED", "SEC_SPY"]
    with store.provider() as provider:
        got = provider.adjusted_prices(T_MAR, ids, include_dividends)
    with store.direct() as conn:
        want = adjusted_prices_as_of(
            conn, T_MAR, ids, include_dividends=include_dividends, settings=store.settings
        )
    assert got.equals(want)
    assert _ids(got) == set(ids)


# SEC_SPLIT_BACKFILLED's 2018-06-01 bar is revised at 2018-06-15T20:00Z.
T_BEFORE_REVISION = datetime(2018, 6, 8, 20, 0, tzinfo=UTC)
T_AFTER_REVISION = datetime(2018, 6, 29, 20, 0, tzinfo=UTC)


@pytest.mark.parametrize("t", [T_BEFORE_REVISION, T_AFTER_REVISION, T_MAR])
def test_raw_prices_equal_the_as_of_read(store: Store, t: datetime) -> None:
    ids = ["SEC_SPLIT_BACKFILLED", "SEC_SPLIT_BETWEEN", "SEC_SPY"]
    with store.provider() as provider:
        got = provider.raw_prices(t, ids)
    with store.direct() as conn:
        want = prices_as_of(conn, t, ids)
    assert got.equals(want)
    assert _ids(got) == set(ids)
    assert (got["known_at"] <= t).all()


def test_raw_prices_take_the_bar_revision_at_its_known_at(store: Store) -> None:
    def close(t: datetime) -> float:
        with store.provider() as provider:
            frame = provider.raw_prices(t, ["SEC_SPLIT_BACKFILLED"])
        return float(frame.filter(pl.col("session") == date(2018, 6, 1))["close"].item())

    assert close(T_BEFORE_REVISION) == 38.45
    assert close(T_AFTER_REVISION) == 40.37


def test_dropped_dividends_equal_the_as_of_read(store: Store) -> None:
    ids = ["SEC_DIV_REVISED", "SEC_SPY", "SEC_MTUM"]
    with store.provider() as provider:
        got = provider.dropped_dividends(T_MAR, ids)
    with store.direct() as conn:
        want = dropped_dividends_as_of(conn, T_MAR, ids, settings=store.settings)
    assert got.equals(want)


def test_late_dividends(store: Store) -> None:
    ids = ["SEC_DUAL_A", "SEC_DUAL_B", "SEC_DIV_REVISED"]
    with store.provider() as provider:
        late = provider.late_dividends(T_FEB, T_MAR, ids)
        not_yet = provider.late_dividends(T_JAN, T_FEB, ids)
        other_ids = provider.late_dividends(T_FEB, T_MAR, ["SEC_DIV_REVISED"])
    # First known 2019-03-05 for an ex-date before 2019-02-28. The revised
    # dividend was first known before T_FEB, so its revision is not late,
    # and SEC_DUAL_B's goes ex after 2019-02-28, so it is not late either.
    assert late.select("security_id", "ex_date", "ratio_or_amount").rows() == [
        ("SEC_DUAL_A", date(2019, 1, 15), 0.05)
    ]
    assert late["known_at"].to_list() == [datetime(2019, 3, 5, 21, 0, tzinfo=UTC)]
    assert not_yet.is_empty() and other_ids.is_empty()


def test_static_listing_count(store: Store) -> None:
    ids = ["SEC_STATIC_PRE2019", "SEC_DUAL_A", "SEC_SPY"]
    with store.provider() as provider:
        # SPY's listing is snapshot_static; PRE9's is known only from 2020-01-15.
        assert provider.static_listing_count(datetime(2019, 12, 31, 21, tzinfo=UTC), ids) == 1
        assert provider.static_listing_count(datetime(2020, 1, 31, 21, tzinfo=UTC), ids) == 2


def test_frozen_settings_govern_the_universe(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    # The live environment asks for one company; the frozen settings do not.
    monkeypatch.setenv("UNIVERSE__TOP_N_BY_CAP", "1")
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store.path.parent / "none.env"))
    with store.provider() as provider:
        full = provider.universe(T_DUAL)
    with store.provider(_settings(store.path, top_n_by_cap=1)) as provider:
        top_one = provider.universe(T_DUAL)
    assert len(set(full.members["cik"].to_list())) > 1
    assert len(set(top_one.members["cik"].to_list())) == 1


# --- connections and the handle ------------------------------------------------------


def test_one_connection_per_step_closed_before_the_next(store: Store) -> None:
    opened_before = store.opened
    provider = store.provider()
    after_check = store.opened  # the handle check opened and closed one
    assert after_check == opened_before + 1 and store.open_now == 0
    ids = ["SEC_DUAL_A"]
    provider.universe(T_JAN)
    provider.adjusted_prices(T_JAN, ids, True)
    provider.listing_ends(T_JAN, ids)
    provider.survivorship_gap(T_JAN)
    assert store.opened == after_check + 1 and store.open_now == 1
    provider.universe(T_FEB)
    provider.late_dividends(T_JAN, T_FEB, ids)
    assert store.opened == after_check + 2 and store.open_now == 1
    provider.close()
    assert store.open_now == 0


def test_write_lock_between_steps_is_harmless(store: Store) -> None:
    with store.provider() as provider:
        provider.universe(T_JAN)
        # Released at the step boundary, the store takes a writer between
        # steps, and the next step opens a fresh connection afterwards.
        provider.end_step()
        with open_for_write(store.settings) as writer:
            writer.execute("SELECT 1")
        assert _ids(provider.universe(T_FEB).members)


def test_step_opening_under_a_write_lock_retries(store: Store) -> None:
    attempts: list[str] = []

    def locked_twice() -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        attempts.append("open")
        if len(attempts) in (2, 3):  # the first open is the handle check
            raise StoreLockedError("locked for writing by another process")
        return open_read_only(store.settings)

    with StoreProvider(locked_twice, store.handle, store.settings) as provider:
        assert _ids(provider.universe(T_JAN).members)
    assert len(attempts) == 4


def test_lock_held_past_the_retry_window_raises(store: Store) -> None:
    state = {"calls": 0}

    def always_locked() -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        state["calls"] += 1
        if state["calls"] > 1:
            raise StoreLockedError("locked for writing by another process")
        return open_read_only(store.settings)

    settings = Settings(_env_file=None, store={"path": str(store.path), "lock_retry_seconds": 0})
    with (
        StoreProvider(always_locked, store.handle, settings) as provider,
        pytest.raises(StoreLockedError),
    ):
        provider.universe(T_JAN)


def test_unknown_handle_refused(store: Store, tmp_path: Path) -> None:
    other_path = tmp_path / "other.duckdb"
    other = _settings(other_path)
    with open_for_write(other) as conn:
        schema.init_schema(conn)
    with pytest.raises(registry.RegistryError):
        StoreProvider(
            store.connect,
            store.handle,
            store.settings,
            registry_connect=lambda: open_read_only(other),
        )


def test_closed_trial_refused(store: Store) -> None:
    with pytest.raises(registry.TrialAlreadyClosed):
        StoreProvider(store.connect, store.closed, store.settings)


def test_calendar_differing_from_the_live_one_refused(store: Store) -> None:
    frozen = Settings(
        _env_file=None, store={"path": str(store.path)}, calendar={"start": date(2012, 1, 3)}
    )
    with pytest.raises(ValueError, match="calendar"):
        StoreProvider(store.connect, store.handle, frozen)


def test_handle_check_retries_under_a_write_lock(store: Store) -> None:
    attempts: list[str] = []

    def locked_once() -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        attempts.append("open")
        if len(attempts) == 1:
            raise StoreLockedError("locked for writing by another process")
        return open_read_only(store.settings)

    with StoreProvider(locked_once, store.handle, store.settings):
        assert len(attempts) == 2


def test_plain_integer_is_not_a_handle(store: Store) -> None:
    with pytest.raises(TypeError, match="TrialHandle"):
        StoreProvider(store.connect, store.handle.trial_id, store.settings)  # type: ignore[arg-type]


def test_registry_connect_is_used_once_at_construction(store: Store) -> None:
    calls: list[str] = []

    def registry_connect() -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        calls.append("registry")
        return open_read_only(store.settings)

    opened = store.opened
    with StoreProvider(
        store.connect, store.handle, store.settings, registry_connect=registry_connect
    ) as provider:
        assert calls == ["registry"] and store.opened == opened
        provider.universe(T_JAN)
    assert calls == ["registry"] and store.opened == opened + 1


def test_bare_date_and_string_ids_raise(store: Store) -> None:
    with store.provider() as provider:
        with pytest.raises(TypeError):
            provider.universe(date(2019, 1, 31))  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            provider.adjusted_prices(T_JAN, "SEC_DUAL_A", False)


def test_nothing_under_backtest_imports_adapters() -> None:
    offenders = []
    for path in sorted(BACKTEST_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        package = ["tradepartner", *path.relative_to(BACKTEST_DIR.parent).parent.parts]
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Resolve a relative import (`from ..adapters import x`).
                base = package[: len(package) - node.level + 1] if node.level else []
                module = ".".join([*base, *([node.module] if node.module else [])])
                names = [module, *(f"{module}.{a.name}" for a in node.names)]
            if any(
                n == "tradepartner.adapters" or n.startswith("tradepartner.adapters.")
                for n in names
            ):
                offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == []
