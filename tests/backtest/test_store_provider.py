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

from backtest.fake_provider import Call, FakeProvider
from tradepartner import gap as gap_module
from tradepartner.backtest import store_provider as store_provider_module
from tradepartner.backtest.provider import STATEMENT_FACT_NAMES, DataProvider, GapReading
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import last_completed_session, session_close
from tradepartner.config import Settings
from tradepartner.store import registry, schema
from tradepartner.store.asof import (
    adjusted_prices_as_of,
    dropped_dividends_as_of,
    live_actions_as_of,
    prices_as_of,
    statement_facts_as_of,
)
from tradepartner.store.classify import classifications_as_of
from tradepartner.store.db import (
    StoreLockedError,
    configure_connection,
    insert_row,
    open_for_write,
    open_read_only,
)
from tradepartner.store.delistings import listing_ends_as_of
from tradepartner.universe import shares_as_of, universe_as_of

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
ZERO_VOLUME_ID = "SEC_DIV_REVISED"
ZERO_VOLUME_SESSION = date(2019, 3, 5)


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
        # A zero-volume revision (#787): SEC_DIV_REVISED's 2019-03-05 bar is
        # re-fetched on 2019-03-06 with no volume, so the provider drops it.
        (bar,) = (
            prices_as_of(conn, T_MAR, [ZERO_VOLUME_ID])
            .filter(pl.col("session") == ZERO_VOLUME_SESSION)
            .iter_rows(named=True)
        )
        revised = datetime(2019, 3, 6, 21, 0, tzinfo=UTC)
        insert_row(
            conn, "prices_daily", bar | {"volume": 0, "known_at": revised, "ingested_at": revised}
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
        # By symbol (#840): before the master rows are known too; the bars stay as-of.
        early = datetime(2017, 6, 30, 20, tzinfo=UTC)
        assert provider.benchmark_ids(early) == {"MTUM": "SEC_MTUM", "SPY": "SEC_SPY"}
    only_spy = Settings(_env_file=None, store={"path": str(store.path)}, benchmarks=["SPY"])
    with store.provider(only_spy) as provider:
        assert provider.benchmark_ids(T_JAN) == {"SPY": "SEC_SPY"}


def test_gap_equals_survivorship_gap(store: Store) -> None:
    with store.provider() as provider:
        got = provider.survivorship_gap(T_WINDOW_DELIST)
    with store.direct() as conn:
        want = gap_module.survivorship_gap(conn, T_WINDOW_DELIST, store.settings)
    assert got == GapReading(count_share=want.count_share, size_share=want.size_share)


def test_a_step_builds_its_universe_once(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    # #1305: the gap counts the universe's missing-data exclusions; within one step
    # (one connection, one t) it reuses the universe the step built instead of
    # building it a second time. A new step builds its own.
    built: list[datetime] = []

    def counting(conn: duckdb.DuckDBPyConnection, t: datetime, settings: Settings) -> Any:
        built.append(t)
        return universe_as_of(conn, t, settings)

    monkeypatch.setattr(store_provider_module, "universe_as_of", counting)
    monkeypatch.setattr(gap_module, "universe_as_of", counting)
    with store.provider() as provider:
        universe = provider.universe(T_WINDOW_DELIST)
        gap = provider.survivorship_gap(T_WINDOW_DELIST)
        assert provider.universe(T_WINDOW_DELIST) is universe
        assert built == [T_WINDOW_DELIST]
        gap_first = provider.survivorship_gap(T_AFTER_DELIST)  # a new step, gap first
        provider.universe(T_AFTER_DELIST)
        assert built == [T_WINDOW_DELIST, T_AFTER_DELIST]
    with store.direct() as conn:
        for t, got in ((T_WINDOW_DELIST, gap), (T_AFTER_DELIST, gap_first)):
            want = gap_module.survivorship_gap(conn, t, store.settings)
            assert got == GapReading(count_share=want.count_share, size_share=want.size_share)


@pytest.mark.parametrize("include_dividends", [False, True])
def test_adjusted_prices_equal_the_as_of_read(store: Store, include_dividends: bool) -> None:
    ids = ["SEC_SPLIT_BETWEEN", "SEC_DIV_REVISED", "SEC_SPY"]
    with store.provider() as provider:
        got = provider.adjusted_prices(T_MAR, ids, include_dividends)
    with store.direct() as conn:
        want = adjusted_prices_as_of(
            conn,
            T_MAR,
            ids,
            include_dividends=include_dividends,
            settings=store.settings,
            traded_only=True,
        )
    assert got.equals(want)
    assert _ids(got) == set(ids)


def _spy_on_adjusted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record the keyword arguments of every `adjusted_prices_as_of` call the provider
    makes, passing each through to the real read."""
    seen: list[dict[str, Any]] = []

    def spy(*args: Any, **kwargs: Any) -> pl.DataFrame:
        seen.append(kwargs)
        return adjusted_prices_as_of(*args, **kwargs)

    monkeypatch.setattr(store_provider_module, "adjusted_prices_as_of", spy)
    return seen


@pytest.mark.parametrize("include_dividends", [False, True])
def test_sessions_from_reaches_the_as_of_read(
    store: Store, monkeypatch: pytest.MonkeyPatch, include_dividends: bool
) -> None:
    """T99: the bound is passed through to `adjusted_prices_as_of`, and the frame is
    the as-of read's bounded frame."""
    ids = ["SEC_SPLIT_BETWEEN", "SEC_DIV_REVISED", "SEC_SPY"]
    bound = date(2019, 2, 22)
    seen = _spy_on_adjusted(monkeypatch)
    with store.provider() as provider:
        got = provider.adjusted_prices(T_MAR, ids, include_dividends, sessions_from=bound)
    assert [call["sessions_from"] for call in seen] == [bound]
    with store.direct() as conn:
        want = adjusted_prices_as_of(
            conn,
            T_MAR,
            ids,
            include_dividends=include_dividends,
            settings=store.settings,
            traded_only=True,
            sessions_from=bound,
        )
    assert got.equals(want)
    assert got["session"].min() == bound


@pytest.mark.parametrize("include_dividends", [False, True])
def test_the_default_bounds_nothing(
    store: Store, monkeypatch: pytest.MonkeyPatch, include_dividends: bool
) -> None:
    """T99: without the keyword the provider passes no bound, so every existing call
    reads exactly the frame it read before."""
    ids = ["SEC_SPLIT_BETWEEN", "SEC_DIV_REVISED", "SEC_SPY"]
    seen = _spy_on_adjusted(monkeypatch)
    with store.provider() as provider:
        got = provider.adjusted_prices(T_MAR, ids, include_dividends)
    assert [call["sessions_from"] for call in seen] == [None]
    with store.direct() as conn:
        want = adjusted_prices_as_of(
            conn,
            T_MAR,
            ids,
            include_dividends=include_dividends,
            settings=store.settings,
            traded_only=True,
        )
    assert got.equals(want)


def test_raw_prices_equal_the_as_of_read(store: Store) -> None:
    """`raw_prices` is `prices_as_of`: unadjusted bars known at `t`, latest revision,
    restricted to `ids` (#199; the method T37b added to the protocol)."""
    ids = ["SEC_SPLIT_BETWEEN", "SEC_DIV_REVISED", "SEC_SPY"]
    with store.provider() as provider:
        got = provider.raw_prices(T_MAR, ids)
    with store.direct() as conn:
        want = prices_as_of(conn, T_MAR, ids, traded_only=True)
    assert got.equals(want)
    assert _ids(got) == set(ids)
    # Unadjusted: the 3:1 split on SEC_SPLIT_BETWEEN (ex-date 2019-01-11) leaves the
    # raw close of the session before it untouched, where the adjusted frame divides it.
    split_eve = (pl.col("security_id") == "SEC_SPLIT_BETWEEN") & (
        pl.col("session") == date(2019, 1, 10)
    )
    assert got.filter(split_eve)["close"].item() == 58.39


def test_a_zero_volume_bar_is_missing_from_both_frames(store: Store) -> None:
    """#787: a bar whose latest revision has no volume is neither a mark, a fill
    nor a signal anchor; before the revision is known it is an ordinary bar."""
    ids = [ZERO_VOLUME_ID]
    bar = pl.col("session") == ZERO_VOLUME_SESSION
    with store.provider() as provider:
        frames = [
            provider.raw_prices(T_MAR, ids),
            provider.adjusted_prices(T_MAR, ids, True),
            provider.adjusted_prices(T_MAR, ids, False),
        ]
        before = provider.raw_prices(datetime(2019, 3, 5, 21, 0, tzinfo=UTC), ids)
    with store.direct() as conn:
        stored = prices_as_of(conn, T_MAR, ids).filter(bar)
    assert stored["volume"].to_list() == [0]
    for frame in frames:
        assert frame.filter(bar).is_empty()
        assert frame.filter(pl.col("session") > ZERO_VOLUME_SESSION).height > 0
    assert before.filter(bar).height == 1


def test_raw_prices_exclude_a_revision_not_yet_known(store: Store) -> None:
    """No look-ahead: SEC_SPLIT_BACKFILLED's 2018-06-01 bar is revised on
    2018-06-15T20:00Z; a read before that instant sees the original close."""
    ids = ["SEC_SPLIT_BACKFILLED"]
    revised_at = datetime(2018, 6, 15, 20, 0, tzinfo=UTC)
    bar = pl.col("session") == date(2018, 6, 1)
    with store.provider() as provider:
        before = provider.raw_prices(datetime(2018, 6, 8, 20, 0, tzinfo=UTC), ids)
        after = provider.raw_prices(revised_at, ids)
    assert before.filter(bar)["close"].item() == 38.45
    assert after.filter(bar)["close"].item() == 40.37
    assert before.filter(bar).height == after.filter(bar).height == 1


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


# --- the statement reads (backtest spec amendment #720, plan T85c) -------------------

#: Reads at these closes, around the fixture's statement-fact cases (README "Statement
#: facts"): the five acceptance stamps, the 10-K accepted after close(2019-02-28), and
#: the issuer whose total_assets is accepted after its gross-profit filing.
STATEMENT_TS = [
    session_close(date(2017, 6, 30)),
    T_DUAL,
    session_close(date(2019, 1, 31)),
    T_FEB,
    T_MAR,
    session_close(date(2019, 11, 29)),
    session_close(date(2019, 12, 31)),
    session_close(date(2020, 1, 31)),
    session_close(date(2020, 6, 30)),
]
STATEMENT_IDS = [
    "SEC_DUAL_A",
    "SEC_DUAL_B",
    "SEC_DUAL_PFD",
    "SEC_TRANSFER",
    "SEC_WINDOW_DELIST",
    "SEC_SPLIT_FUTURE",
    "SEC_SPLIT_BETWEEN",
    "SEC_SPLIT_REDATED",
    "SEC_FACTS_RESTATED",
    "SEC_BOUNDARY_DELIST",
    "SEC_TRUNC_DELIST",
    "SEC_CLEAN_MERGER",
    "SEC_REUSE_2",
    "SEC_SPY",
]


def _fiscal_years(frame: pl.DataFrame, sid: str, fact_name: str) -> set[int]:
    rows = frame.filter(pl.col("security_id") == sid, pl.col("fact_name") == fact_name)
    return {period_end.year for period_end in rows["period_end"].to_list()}


@pytest.mark.parametrize("t", STATEMENT_TS)
def test_statement_facts_are_the_as_of_read_of_the_family_names(store: Store, t: datetime) -> None:
    with store.provider() as provider:
        got = provider.statement_facts(t, STATEMENT_IDS)
    with store.direct() as conn:
        full = statement_facts_as_of(conn, t, STATEMENT_IDS)
    assert got.equals(full.filter(pl.col("fact_name").is_in(STATEMENT_FACT_NAMES)))
    assert set(got["fact_name"].to_list()) <= set(STATEMENT_FACT_NAMES)
    assert got.filter(pl.col("known_at") > t).is_empty()


def test_statement_facts_leave_out_every_other_fact_name(store: Store) -> None:
    t = session_close(date(2020, 6, 30))
    with store.provider() as provider:
        got = provider.statement_facts(t, STATEMENT_IDS)
    with store.direct() as conn:
        full = statement_facts_as_of(conn, t, STATEMENT_IDS)
    # The fixture's revenue and cost_of_revenue rows are known at t.
    assert {"revenue", "cost_of_revenue"} <= set(full["fact_name"])
    assert set(got["fact_name"].to_list()) == set(STATEMENT_FACT_NAMES)


def test_statement_facts_one_row_per_listed_class(store: Store) -> None:
    ids = ["SEC_DUAL_A", "SEC_DUAL_B", "SEC_DUAL_PFD"]
    with store.provider() as provider:
        got = provider.statement_facts(T_FEB, ids)
    by_class = got.partition_by("security_id", as_dict=True, include_key=False)
    assert sorted(key for (key,) in by_class) == ids
    frames = list(by_class.values())
    assert frames[0].height == 2  # FY2018 gross_profit and total_assets, once per class
    assert all(frame.equals(frames[0]) for frame in frames)


def test_statement_facts_nothing_for_a_cik_with_no_securities_row_at_t(store: Store) -> None:
    # CIK0001000001's FY2016 10-K is accepted 2017-06-15; its securities row
    # (SEC_TRUNC_DELIST) is known only from 2017-12-01.
    with store.provider() as provider:
        before = provider.statement_facts(session_close(date(2017, 6, 30)), ["SEC_TRUNC_DELIST"])
        after = provider.statement_facts(session_close(date(2017, 12, 29)), ["SEC_TRUNC_DELIST"])
    assert before.is_empty()
    assert _fiscal_years(after, "SEC_TRUNC_DELIST", "gross_profit") == {2015, 2016}


def test_statement_facts_restricted_to_the_ids(store: Store) -> None:
    with store.provider() as provider:
        got = provider.statement_facts(T_MAR, ["SEC_TRANSFER"])
        none = provider.statement_facts(T_MAR, [])
    assert _ids(got) == {"SEC_TRANSFER"}
    assert none.is_empty() and "fact_name" in none.columns


def test_ordinary_month_end_stamps(store: Store) -> None:
    # 2019-01-31 closes at 16:00 New York: accepted 15:00 (SEC_WINDOW_DELIST's
    # issuer) and exactly 16:00 (SEC_SPLIT_FUTURE's) are visible at the close,
    # 17:30 (the dual-class issuer's) only at the next rebalance.
    close = session_close(date(2019, 1, 31))
    ids = ["SEC_WINDOW_DELIST", "SEC_SPLIT_FUTURE", "SEC_DUAL_A"]
    with store.provider() as provider:
        at_close = provider.statement_facts(close, ids)
        next_close = provider.statement_facts(T_FEB, ids)
    stamp = at_close.filter(
        pl.col("security_id") == "SEC_SPLIT_FUTURE", pl.col("period_end") == date(2018, 12, 31)
    )
    assert set(stamp["known_at"].to_list()) == {close}
    for sid in ("SEC_WINDOW_DELIST", "SEC_SPLIT_FUTURE"):
        assert 2018 in _fiscal_years(at_close, sid, "gross_profit")
        assert 2018 in _fiscal_years(at_close, sid, "total_assets")
    assert _fiscal_years(at_close, "SEC_DUAL_A", "gross_profit") == set()
    assert _fiscal_years(next_close, "SEC_DUAL_A", "gross_profit") == {2018}


def test_half_day_month_end_stamps(store: Store) -> None:
    # 2019-11-29 closes at 13:00 New York: SEC_SPLIT_BETWEEN's FY2019 10-K,
    # accepted 12:30, is visible at that close; its 10-K/A with the FY2019
    # total_assets, accepted 14:00, is not.
    close = session_close(date(2019, 11, 29))
    sid = "SEC_SPLIT_BETWEEN"
    with store.provider() as provider:
        at_close = provider.statement_facts(close, [sid])
        next_close = provider.statement_facts(session_close(date(2019, 12, 31)), [sid])
    assert 2019 in _fiscal_years(at_close, sid, "gross_profit")
    assert 2019 not in _fiscal_years(at_close, sid, "total_assets")
    assert 2019 in _fiscal_years(next_close, sid, "total_assets")


def test_ten_k_accepted_after_the_close_is_read_at_the_next_rebalance(store: Store) -> None:
    with store.provider() as provider:
        at_t_i = provider.statement_facts(T_FEB, ["SEC_TRANSFER"])
        at_next = provider.statement_facts(T_MAR, ["SEC_TRANSFER"])
    assert max(_fiscal_years(at_t_i, "SEC_TRANSFER", "gross_profit")) == 2017
    assert max(_fiscal_years(at_next, "SEC_TRANSFER", "gross_profit")) == 2018


def test_total_assets_accepted_after_the_gross_profit_filing(store: Store) -> None:
    sid = "SEC_FACTS_RESTATED"
    with store.provider() as provider:
        december = provider.statement_facts(session_close(date(2019, 12, 31)), [sid])
        january = provider.statement_facts(session_close(date(2020, 1, 31)), [sid])
    fy2019 = pl.col("period_end") == date(2019, 9, 30)
    assert set(december.filter(fy2019)["fact_name"].to_list()) == {"gross_profit"}
    assert set(january.filter(fy2019)["fact_name"].to_list()) == {"gross_profit", "total_assets"}


def test_restated_issuer_keeps_its_original_fy2018_values(store: Store) -> None:
    sid = "SEC_BOUNDARY_DELIST"
    with store.provider() as provider:
        first = provider.statement_facts(session_close(date(2019, 2, 28)), [sid])
        later = provider.statement_facts(session_close(date(2020, 6, 30)), [sid])
    fy2018 = pl.col("period_end") == date(2018, 12, 31)
    assert first.filter(fy2018).equals(later.filter(fy2018))
    assert first.filter(fy2018).height == 2


@pytest.mark.parametrize("t", [T_DUAL, T_FEB, session_close(date(2020, 6, 30))])
def test_sics_equal_classifications_as_of(store: Store, t: datetime) -> None:
    with store.provider() as provider:
        got = provider.sics(t, STATEMENT_IDS)
    with store.direct() as conn:
        rows = classifications_as_of(conn, t, STATEMENT_IDS)
    want = dict(rows.select("security_id", "sic").iter_rows())
    assert got == {sid: want.get(sid) for sid in STATEMENT_IDS}


def test_sics_none_for_no_row_at_t_and_for_no_sic(store: Store) -> None:
    # SEC_REUSE_2's classification is known only from 2019-07-03; SPY has no SIC.
    with store.provider() as provider:
        got = provider.sics(T_DUAL, ["SEC_REUSE_2", "SEC_SPY", "SEC_DUAL_A"])
        later = provider.sics(session_close(date(2019, 7, 31)), ["SEC_REUSE_2"])
    assert got == {"SEC_REUSE_2": None, "SEC_SPY": None, "SEC_DUAL_A": 7372}
    assert later == {"SEC_REUSE_2": 7372}


def test_statement_reads_share_the_step_connection(store: Store) -> None:
    provider = store.provider()
    after_check = store.opened
    provider.universe(T_FEB)
    provider.statement_facts(T_FEB, ["SEC_DUAL_A"])
    provider.sics(T_FEB, ["SEC_DUAL_A"])
    assert store.opened == after_check + 1 and store.open_now == 1
    provider.statement_facts(T_MAR, ["SEC_DUAL_A"])
    assert store.opened == after_check + 2 and store.open_now == 1
    provider.close()
    assert store.open_now == 0


def test_statement_reads_refuse_a_bare_date_and_string_ids(store: Store) -> None:
    with store.provider() as provider:
        with pytest.raises(TypeError):
            provider.statement_facts(date(2019, 1, 31), ["SEC_DUAL_A"])  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            provider.sics(datetime(2019, 1, 31, 21), ["SEC_DUAL_A"])  # noqa: DTZ001
        with pytest.raises(TypeError):
            provider.statement_facts(T_JAN, "SEC_DUAL_A")
        with pytest.raises(TypeError):
            provider.sics(T_JAN, "SEC_DUAL_A")


# --- the turnover read (backtest spec amendment #1358, plan T165b) ----------------------

T_APR = session_close(date(2019, 4, 30))
TURNOVER_IDS = [
    "SEC_FACTS_RESTATED",
    "SEC_FACTS_STALE",
    "SEC_SPLIT_BETWEEN",
    "SEC_SPLIT_PLAIN",
    "SEC_SPLIT_FUTURE",
    "SEC_SPLIT_REDATED",
    "SEC_SPLIT_REDATED_NOID",
    "SEC_DIV_REVISED",
    "SEC_WINDOW_DELIST",
    "SEC_DUAL_A",
]
TURNOVER_TS = [
    T_DUAL,
    T_JAN,
    T_FEB,
    T_MAR,
    T_APR,
    session_close(date(2019, 6, 28)),
    session_close(date(2019, 10, 17)),
    session_close(date(2019, 10, 31)),
]


def _month_start(t: datetime) -> date:
    return last_completed_session(t).replace(day=1)


@pytest.mark.parametrize("t", TURNOVER_TS)
def test_turnover_inputs_are_the_three_as_of_reads(store: Store, t: datetime) -> None:
    """The bars are `prices_as_of(traded_only=True)` from `sessions_from` through
    `t`'s session, the shares are rule 7's raw picks, and the splits are the live
    actions known at `t` with `ex_date` at or before its session."""
    start = _month_start(t)
    with store.provider() as provider:
        got = provider.turnover_inputs(t, TURNOVER_IDS, start)
    session = last_completed_session(t)
    with store.direct() as conn:
        bars = prices_as_of(conn, t, TURNOVER_IDS, traded_only=True).filter(
            pl.col("session").is_between(start, session)
        )
        pick = shares_as_of(conn, t, TURNOVER_IDS, store.settings)
        actions = live_actions_as_of(conn, t, TURNOVER_IDS).filter(
            (pl.col("action_type") == "split") & (pl.col("ex_date") <= session)
        )
    assert got.t == t
    assert got.bars.columns == ["security_id", "session", "volume", "known_at"]
    assert got.bars.equals(bars.select(got.bars.columns).sort("security_id", "session"))
    assert got.bars.height > 0
    assert got.bars["known_at"].max() <= t  # type: ignore[operator]
    assert dict(got.shares) == pick.shares
    want_splits: dict[str, list[tuple[date, float]]] = {}
    for sid, ex_date, ratio in actions.select(
        "security_id", "ex_date", "ratio_or_amount"
    ).iter_rows():
        want_splits.setdefault(sid, []).append((ex_date, ratio))
    assert {sid: list(v) for sid, v in got.splits.items()} == {
        sid: sorted(v) for sid, v in want_splits.items()
    }


def test_a_shares_fact_accepted_after_the_close_is_read_at_the_next_rebalance(
    store: Store,
) -> None:
    """SEC_FACTS_RESTATED's restated 2019-01-16 fact is accepted 2019-03-29T20:30Z,
    after that session's close: absent at close(2019-03-29), read at close(2019-04-30).
    SEC_FACTS_STALE's first fact (accepted 2019-02-08) is absent at January's close."""
    with store.provider() as provider:
        jan = provider.turnover_inputs(T_JAN, TURNOVER_IDS, _month_start(T_JAN)).shares
        feb = provider.turnover_inputs(T_FEB, TURNOVER_IDS, _month_start(T_FEB)).shares
        mar = provider.turnover_inputs(T_MAR, TURNOVER_IDS, _month_start(T_MAR)).shares
        apr = provider.turnover_inputs(T_APR, TURNOVER_IDS, _month_start(T_APR)).shares
    assert "SEC_FACTS_STALE" not in jan
    assert feb["SEC_FACTS_STALE"] == (date(2019, 2, 1), 2_000_000.0)
    assert mar["SEC_FACTS_RESTATED"] == (date(2019, 1, 16), 1_000_000.0)
    assert apr["SEC_FACTS_RESTATED"] == (date(2019, 1, 16), 1_050_000.0)
    # Raw, not split-moved: SEC_SPLIT_BETWEEN's fact predates its 3-for-1.
    assert feb["SEC_SPLIT_BETWEEN"] == (date(2018, 10, 29), 5_000_000.0)


def test_a_split_is_absent_before_its_known_at_and_counts_once(store: Store) -> None:
    """SEC_SPLIT_REDATED_NOID's 2019-10-14 split is first known 2019-10-18T22:00Z
    (after its ex-date) and its 2019-10-21 date is cancelled: absent at
    close(2019-10-17), once at close(2019-10-31). SEC_SPLIT_REDATED's re-dated split
    counts once, at its latest ex-date; SEC_SPLIT_FUTURE's known split with a later
    ex-date is not yet a split of the read."""
    ids = ["SEC_SPLIT_REDATED", "SEC_SPLIT_REDATED_NOID", "SEC_SPLIT_FUTURE"]
    before_t, after_t = session_close(date(2019, 10, 17)), session_close(date(2019, 10, 31))
    with store.provider() as provider:
        jan = provider.turnover_inputs(T_JAN, ids, _month_start(T_JAN)).splits
        before = provider.turnover_inputs(before_t, ids, _month_start(before_t)).splits
        after = provider.turnover_inputs(after_t, ids, _month_start(after_t)).splits
    assert "SEC_SPLIT_FUTURE" not in jan
    assert "SEC_SPLIT_REDATED_NOID" not in before
    assert before["SEC_SPLIT_REDATED"] == ((date(2019, 6, 17), 2.0),)
    assert after["SEC_SPLIT_REDATED_NOID"] == ((date(2019, 10, 14), 2.0),)
    assert after["SEC_SPLIT_FUTURE"] == ((date(2019, 2, 14), 4.0),)


def test_a_missing_formation_bar_is_a_missing_row_never_a_zero(store: Store) -> None:
    """#787: SEC_DIV_REVISED's 2019-03-05 bar is re-fetched with no volume on
    2019-03-06: at March's close it is absent, not a zero-volume row; read before the
    revision is known it is an ordinary bar."""
    ids = [ZERO_VOLUME_ID]
    start = date(2019, 3, 1)
    with store.provider() as provider:
        mar = provider.turnover_inputs(T_MAR, ids, start).bars
        early = provider.turnover_inputs(datetime(2019, 3, 5, 21, 0, tzinfo=UTC), ids, start).bars
    assert ZERO_VOLUME_SESSION not in mar["session"].to_list()
    assert mar.height > 0 and mar["volume"].min() > 0  # type: ignore[operator]
    assert early.filter(pl.col("session") == ZERO_VOLUME_SESSION)["volume"].item() > 0


def test_the_bars_run_from_sessions_from_through_the_session_of_t(store: Store) -> None:
    ids = ["SEC_SPLIT_BETWEEN"]
    with store.provider() as provider:
        got = provider.turnover_inputs(T_JAN, ids, date(2019, 1, 2)).bars
        with pytest.raises(TypeError, match="sessions_from"):
            provider.turnover_inputs(T_JAN, ids, T_DUAL)  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            provider.turnover_inputs(T_JAN, "SEC_SPLIT_BETWEEN", date(2019, 1, 2))
    sessions = got["session"].to_list()
    assert sessions[0] == date(2019, 1, 2) and sessions[-1] == date(2019, 1, 31)
    assert len(sessions) == 21  # every January 2019 session, the split's ex-date among them


def test_the_turnover_read_shares_the_step_connection(store: Store) -> None:
    provider = store.provider()
    opened = store.opened
    provider.universe(T_JAN)
    provider.turnover_inputs(T_JAN, ["SEC_DUAL_A"], _month_start(T_JAN))
    assert store.opened == opened + 1 and store.open_now == 1
    provider.close()


def _fixture_fake(store: Store) -> FakeProvider:
    """A `FakeProvider` over the fixture store's own rows: raw bars, shares facts and
    split actions (identity: `source_action_id`, else the ex-date, as in #108)."""
    with store.direct() as conn:
        raw = conn.execute("SELECT * FROM prices_daily").pl()
        facts = conn.execute(
            "SELECT security_id, as_of_date, value, known_at FROM facts "
            "WHERE fact_name = 'shares_outstanding'"
        ).pl()
        splits = conn.execute(
            "SELECT security_id, "
            "CASE WHEN source_action_id = '' THEN CAST(ex_date AS VARCHAR) "
            "ELSE source_action_id END AS action_id, "
            "ex_date, ratio_or_amount AS ratio, cancelled, known_at "
            "FROM corporate_actions WHERE action_type = 'split'"
        ).pl()
    return FakeProvider(prices=raw, members={}, benchmarks={}, shares_rows=facts, split_rows=splits)


@pytest.mark.parametrize("t", TURNOVER_TS)
def test_the_fake_and_store_providers_answer_alike_on_the_fixture(
    store: Store, t: datetime
) -> None:
    fake = _fixture_fake(store)
    start = _month_start(t)
    with store.provider() as provider:
        want = provider.turnover_inputs(t, TURNOVER_IDS, start)
    got = fake.turnover_inputs(t, TURNOVER_IDS, start)
    assert got.bars.equals(want.bars)
    assert dict(got.shares) == dict(want.shares)
    assert dict(got.splits) == dict(want.splits)
    assert fake.calls == [Call("turnover_inputs", t, ids=tuple(TURNOVER_IDS), sessions_from=start)]
