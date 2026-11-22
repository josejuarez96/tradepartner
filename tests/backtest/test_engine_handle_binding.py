"""A `TrialHandle` binds one run to its trial (code review 2026-11-20, #3533).

The handle carries the trial row's window and holdout bound; `engine.run` refuses a
second use, another window, params that do not hash to the registration and an in-sample
handle reaching the holdout, all before any provider call.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest

from backtest.fake_provider import FakeProvider
from backtest.test_results import BENCHMARKS, NAMES, SESSIONS, _prices
from tradepartner.backtest import engine
from tradepartner.backtest.engine import run, run_many
from tradepartner.backtest.holdout import Flags, Reasons
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.backtest.run import run_hypothesis
from tradepartner.backtest.schedule import read_time, rebalance_sessions
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.config import Settings
from tradepartner.store import registry
from tradepartner.store.db import open_for_write

START, END = date(2019, 1, 4), date(2019, 3, 29)
HOLDOUT = (date(2019, 6, 3), date(2020, 6, 30))
SLUG = "h-binding"


def _settings(top: float = 0.5, holdout: tuple[date, date] = HOLDOUT) -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": top},
        costs={"per_side_bps": 0.0},
        holdout={"start": holdout[0], "end": holdout[1]},
    )


def _lend(conn: duckdb.DuckDBPyConnection) -> Callable[[], Any]:
    @contextmanager
    def _connect() -> Iterator[duckdb.DuckDBPyConnection]:
        yield conn

    return _connect


def _register(
    conn: duckdb.DuckDBPyConnection, slug: str = SLUG, holdout_start: date = HOLDOUT[0]
) -> int:
    settings = _settings(holdout=(holdout_start, HOLDOUT[1]))
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family="momentum",
        title=slug,
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="0" * 64,
        params=frozen_params_of(settings, family="momentum"),
        in_sample_start=START,
        holdout_start=holdout_start,
        holdout_end=HOLDOUT[1],
        registered_by="test",
        settings=settings,
    ).hypothesis_id


def _open(
    conn: duckdb.DuckDBPyConnection,
    hypothesis_id: int,
    *,
    start: date = START,
    end: date = END,
    kind: Any = "in_sample",
) -> registry.TrialHandle:
    return registry.open_trial(
        conn,
        hypothesis_id=hypothesis_id,
        kind=kind,
        start_session=start,
        end_session=end,
        data_cutoff=read_time(rebalance_sessions(start, end, "month_end")[-1], "month_end"),
        synthetic=True,
        run_by="test",
        settings=_settings(),
    )


class Recorder:
    """A provider that records any use: a refused run touches none."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Any:
        if name == "settings":
            raise AttributeError(name)
        self.calls.append(name)
        raise AssertionError(f"provider used: {name}")


@pytest.fixture
def hypothesis_id(fixture_store: duckdb.DuckDBPyConnection) -> int:
    return _register(fixture_store)


def _run(conn: duckdb.DuckDBPyConnection, handle: registry.TrialHandle, **over: Any) -> Any:
    params = over.pop("params", _settings())
    start, end = over.pop("start", START), over.pop("end", END)
    connect = _lend(conn)
    with StoreProvider(connect, handle, params, registry_connect=connect) as provider:
        return run(params, provider, start, end, handle, (0.0,), family="momentum")


def test_second_run_on_one_handle_is_refused(
    fixture_store: duckdb.DuckDBPyConnection, hypothesis_id: int
) -> None:
    handle = _open(fixture_store, hypothesis_id)
    assert _run(fixture_store, handle)
    with pytest.raises(ValueError, match="refused_handle"):
        _run(fixture_store, handle)


def test_run_over_another_window_than_the_trial_row_is_refused(
    fixture_store: duckdb.DuckDBPyConnection, hypothesis_id: int
) -> None:
    handle = _open(fixture_store, hypothesis_id)
    recorder = Recorder()
    with pytest.raises(ValueError, match="refused_handle"):
        run(_settings(), recorder, START, date(2019, 2, 28), handle, (0.0,), family="momentum")  # type: ignore[arg-type]
    assert recorder.calls == []


def test_in_sample_handle_over_holdout_months_is_refused(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    hid = _register(fixture_store, "h-early-holdout", holdout_start=date(2019, 3, 1))
    handle = _open(fixture_store, hid)
    recorder = Recorder()
    with pytest.raises(ValueError, match=r"refused_handle.*holdout"):
        run(
            _settings(holdout=(date(2019, 3, 1), HOLDOUT[1])),
            recorder,
            START,
            END,
            handle,
            (0.0,),
            family="momentum",
        )  # type: ignore[arg-type]
    assert recorder.calls == []


def test_params_not_hashing_to_the_trial_are_refused_before_any_provider_call(
    fixture_store: duckdb.DuckDBPyConnection, hypothesis_id: int
) -> None:
    handle = _open(fixture_store, hypothesis_id)
    other = _settings(top=0.25)
    recorder = Recorder()
    with pytest.raises(ValueError, match=r"refused_handle.*hash"):
        run(other, recorder, START, END, handle, (0.0,), family="momentum")  # type: ignore[arg-type]
    assert recorder.calls == []


def test_holdout_kind_handle_over_holdout_window_runs(
    fixture_store_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "real_store.duckdb"))
    settings = _settings().model_copy(
        update={"store": _settings().store.model_copy(update={"path": str(fixture_store_path)})}
    )
    with open_for_write(settings) as conn:
        registry.register_hypothesis(
            conn,
            slug=SLUG,
            family="momentum",
            title=SLUG,
            doc_path=f"docs/hypotheses/{SLUG}.md",
            doc_sha256="0" * 64,
            params=frozen_params_of(_settings(), family="momentum"),
            in_sample_start=date(2018, 1, 2),
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            registered_by="test",
            settings=settings,
        )
    window = (date(2019, 6, 3), date(2019, 8, 30))
    outcome = run_hypothesis(
        SLUG,
        *window,
        Flags(spend_holdout=True, override_gap=True),
        reasons=Reasons(holdout_reason="spend", gap_reason="owner accepts the gap"),
        synthetic=True,
        store_path=fixture_store_path,
    )
    assert outcome.status == "ok"


def test_sweep_read_group_with_shared_window_runs(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """Four variants over one window run through `run_many`: the binding is per handle."""
    sessions = [s for s in SESSIONS if s]
    start, end = sessions[0], sessions[-1]
    variants = []
    for i in range(4):
        params = _settings(top=0.2 + 0.1 * i, holdout=(date(2030, 1, 1), date(2030, 12, 31)))
        hid = registry.register_hypothesis(
            fixture_store,
            slug=f"h-sweep-{i}",
            family="momentum",
            title="sweep",
            doc_path=f"docs/hypotheses/h-sweep-{i}.md",
            doc_sha256="0" * 64,
            params=frozen_params_of(params, family="momentum"),
            in_sample_start=start,
            holdout_start=date(2030, 1, 1),
            holdout_end=date(2030, 12, 31),
            registered_by="test",
            settings=params,
        ).hypothesis_id
        handle = _open(fixture_store, hid, start=start, end=end)
        variants.append((params, handle))
    provider = FakeProvider(
        prices=_prices(7), members={s: list(NAMES) for s in SESSIONS}, benchmarks=BENCHMARKS
    )
    out = run_many(variants, provider, start, end, (0.0,), family="momentum")
    assert sorted(out) == [h.trial_id for _, h in variants] and not out.failures
    assert engine._CONSUMED_HANDLES
