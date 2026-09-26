"""Run orchestration and refusals (backtest spec reqs 9-11, 14; plan T39).

Every test registers a hypothesis on `fixture_store_path` and calls `run_hypothesis`
with `store_path` set to it, while the live `settings.store.path` names another
temp file. `StoreProvider` is replaced by a subclass that records every
`DataProvider` call (and its construction) and can inject an error or a write.

The frozen window: in-sample from 2018-01-31 (the benchmarks are known from
2018-01-02), holdout 2019-06-03..2020-06-30. The fixture's survivorship-gap count
share is 0.0769 at 2019-06-28 and 0 at the other sessions of the holdout window
used here, so the frozen threshold 0.05 refuses it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.backtest import engine
from tradepartner.backtest import run as run_module
from tradepartner.backtest.holdout import Flags, Reasons
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.backtest.run import run_hypothesis
from tradepartner.backtest.schedule import read_time
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.config import Settings
from tradepartner.store import registry
from tradepartner.store.db import insert_row, open_for_write, utc_now

SLUG = "h-run"
IN_SAMPLE_START = date(2018, 1, 31)
HOLDOUT = (date(2019, 6, 3), date(2020, 6, 30))
#: The last rebalance session before holdout.start: the default window's end.
DEFAULT_END = date(2019, 5, 31)
HOLDOUT_WINDOW = (date(2019, 5, 31), date(2019, 8, 30))
HOLDOUT_SESSIONS = (date(2019, 5, 31), date(2019, 6, 28), date(2019, 7, 31), date(2019, 8, 30))
LEVELS = {0.0, 15.0, 30.0, 60.0, 100.0}
SPEND = Flags(spend_holdout=True)
SPEND_REASON = Reasons(holdout_reason="owner spends the holdout")

_METHODS = (
    "universe",
    "adjusted_prices",
    "raw_prices",
    "listing_ends",
    "benchmark_ids",
    "survivorship_gap",
    "dropped_dividends",
    "late_dividends",
    "static_listing_count",
)


def _frozen() -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        holdout={"start": HOLDOUT[0], "end": HOLDOUT[1]},
        gap={"count_share_threshold": 0.05},
    )


def _store(path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(path)})


@pytest.fixture(autouse=True)
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Live settings without a `.env`, whose store is another temp file."""
    real = tmp_path / "real_store.duckdb"
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(real))
    return real


@pytest.fixture
def store(fixture_store_path: Path) -> Path:
    frozen = _frozen()
    with open_for_write(_store(fixture_store_path)) as conn:
        registry.register_hypothesis(
            conn,
            slug=SLUG,
            family="momentum",
            title="run orchestration",
            doc_path=f"docs/hypotheses/{SLUG}.md",
            doc_sha256="0" * 64,
            params=frozen_params_of(frozen),
            in_sample_start=IN_SAMPLE_START,
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            registered_by="test",
            settings=frozen,
        )
    return fixture_store_path


class Calls(list[tuple[str, Any]]):
    """`(method, t)` per provider call; `("__init__", None)` per construction."""


def _spy(
    monkeypatch: pytest.MonkeyPatch,
    fail: str | None = None,
    write_to: Path | None = None,
) -> Calls:
    calls = Calls()

    class Spy(StoreProvider):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            calls.append(("__init__", None))
            super().__init__(*args, **kwargs)

    def wrap(name: str) -> Any:
        def method(self: StoreProvider, *args: Any, **kwargs: Any) -> Any:
            calls.append((name, args[0]))
            if name == fail:
                raise RuntimeError("injected provider error")
            if name == "adjusted_prices" and write_to is not None and len(calls) > 1:
                _insert_mid_run(self, write_to)
            return getattr(StoreProvider, name)(self, *args, **kwargs)

        return method

    for name in _METHODS:
        setattr(Spy, name, wrap(name))
    monkeypatch.setattr(run_module, "StoreProvider", Spy)
    return calls


_written: set[Path] = set()


def _insert_mid_run(provider: StoreProvider, path: Path) -> None:
    """Once per store: a new bar after the window, ingested now, between two reads."""
    if path in _written:
        return
    _written.add(path)
    provider.end_step()
    with open_for_write(_store(path)) as conn:
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": "SEC_SPY",
                "session": date(2020, 7, 1),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "known_at": datetime(2020, 7, 1, 20, 0, tzinfo=UTC),
                "ingested_at": utc_now(),
                "source": "alpaca",
                "provenance": "bar",
            },
        )


Read = Callable[[], duckdb.DuckDBPyConnection]


@pytest.fixture
def read(store: Path) -> Iterator[Read]:
    """Opens lazily: no read-only connection may be open while a run writes."""
    conns: list[duckdb.DuckDBPyConnection] = []

    def _open() -> duckdb.DuckDBPyConnection:
        conns.append(duckdb.connect(str(store), read_only=True))
        return conns[-1]

    yield _open
    for conn in conns:
        conn.close()


def _row(conn: duckdb.DuckDBPyConnection, table: str, trial_id: int) -> dict[str, Any]:
    cursor = conn.execute(f"SELECT * FROM {table} WHERE trial_id = ?", [trial_id])
    names = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    assert len(rows) == 1, f"{table} rows for trial {trial_id}: {rows}"
    return dict(zip(names, rows[0], strict=True))


def _decisions(conn: duckdb.DuckDBPyConnection, trial_id: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT kind, reason, values_json FROM owner_decisions WHERE trial_id = ?", [trial_id]
    ).fetchall()
    return {kind: {"reason": reason, "values": json.loads(values)} for kind, reason, values in rows}


# --- ok, failed ---------------------------------------------------------------------


def test_ok_run_returns_results_and_leaves_an_ok_row(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch)
    trial_id, results = run_hypothesis(SLUG, None, None, Flags(), synthetic=True, store_path=store)

    assert results is not None and set(results) == LEVELS
    assert max(row.n_targets for row in results[15.0].rebalances) >= 2
    conn = read()
    trial = _row(conn, "trials", trial_id)
    assert (trial["kind"], trial["synthetic"]) == ("in_sample", True)
    assert (trial["start_session"], trial["end_session"]) == (IN_SAMPLE_START, DEFAULT_END)
    assert trial["data_cutoff"] == read_time(DEFAULT_END)
    assert _row(conn, "trial_results", trial_id)["status"] == "ok"
    levels = conn.execute(
        "SELECT DISTINCT cost_per_side_bps FROM trial_equity WHERE trial_id = ?", [trial_id]
    ).fetchall()
    assert {level for (level,) in levels} == LEVELS
    assert calls.count(("__init__", None)) == 1
    # The engine read only at rebalance closes of the default window.
    assert max(t for name, t in calls if name == "universe") <= read_time(DEFAULT_END)


def test_frozen_params_used(store: Path, read: Read, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "STRATEGY__TOP_FRACTION": "0.9",
        "COSTS__PER_SIDE_BPS": "40",
        "HOLDOUT__START": "2018-06-01",
        "HOLDOUT__END": "2018-12-31",
        "GAP__COUNT_SHARE_THRESHOLD": "0.5",
    }.items():
        monkeypatch.setenv(key, value)
    seen: list[tuple[Settings, date, date, list[float]]] = []
    real_run = engine.run

    def spy_run(params: Settings, provider: Any, start: date, end: date, *rest: Any) -> Any:
        seen.append((params, start, end, list(rest[1])))
        return real_run(params, provider, start, end, *rest)

    monkeypatch.setattr(engine, "run", spy_run)
    trial_id, results = run_hypothesis(SLUG, None, None, Flags(), store_path=store)

    assert results is not None
    [(params, start, end, levels)] = seen
    assert params.strategy.top_fraction == 0.5
    assert params.costs.per_side_bps == 15.0
    assert (params.holdout.start, params.holdout.end) == HOLDOUT
    assert params.gap.count_share_threshold == 0.05
    assert (start, end) == (IN_SAMPLE_START, DEFAULT_END)
    assert set(levels) == LEVELS
    assert _row(read(), "trial_results", trial_id)["status"] == "ok"


def test_injected_provider_error_leaves_failed(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spy(monkeypatch, fail="universe")
    trial_id, results = run_hypothesis(SLUG, None, None, Flags(), store_path=store)

    assert results is None
    result = _row(read(), "trial_results", trial_id)
    assert result["status"] == "failed"
    assert result["message"] == "RuntimeError: injected provider error"


def test_row_inserted_mid_run_leaves_failed(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, write_to=store)
    trial_id, results = run_hypothesis(SLUG, None, None, Flags(), store_path=store)

    assert results is None
    assert store in _written and ("adjusted_prices", read_time(DEFAULT_END)) in calls
    result = _row(read(), "trial_results", trial_id)
    assert (result["status"], result["message"]) == ("failed", registry.STORE_CHANGED_MESSAGE)


# --- refusals -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [(date(2017, 12, 29), DEFAULT_END), (IN_SAMPLE_START, date(2020, 7, 31))],
    ids=["start-before-in-sample", "end-after-holdout"],
)
def test_refused_window_makes_no_provider_call(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch, start: date, end: date
) -> None:
    calls = _spy(monkeypatch)
    trial_id, results = run_hypothesis(SLUG, start, end, SPEND, store_path=store)

    assert results is None and calls == []
    conn = read()
    assert _row(conn, "trial_results", trial_id)["status"] == "refused_window"
    trial = _row(conn, "trials", trial_id)
    assert (trial["start_session"], trial["end_session"]) == (start, end)


def test_refused_holdout_makes_no_provider_call(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch)
    trial_id, results = run_hypothesis(SLUG, *HOLDOUT_WINDOW, Flags(), store_path=store)

    assert results is None and calls == []
    conn = read()
    assert _row(conn, "trial_results", trial_id)["status"] == "refused_holdout"
    assert _row(conn, "trials", trial_id)["kind"] == "in_sample"
    assert _decisions(conn, trial_id) == {}


def test_refused_gap_after_gap_reads_only(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch)
    trial_id, results = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, SPEND, reasons=SPEND_REASON, store_path=store
    )

    assert results is None
    assert calls == [
        ("__init__", None),
        *(("survivorship_gap", read_time(s)) for s in HOLDOUT_SESSIONS),
    ]
    conn = read()
    result = _row(conn, "trial_results", trial_id)
    assert result["status"] == "refused_gap"
    assert "2019-06-28" in result["message"]
    trial = _row(conn, "trials", trial_id)
    assert (trial["kind"], trial["holdout_reason"]) == ("holdout", SPEND_REASON.holdout_reason)
    assert trial["gap_override_reason"] is None
    decisions = _decisions(conn, trial_id)
    assert set(decisions) == {"holdout_spend"}
    assert decisions["holdout_spend"]["reason"] == SPEND_REASON.holdout_reason


def test_gap_override_runs_and_records_the_owner_decision(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    reasons = Reasons(holdout_reason="spend", gap_reason="owner accepts the June gap")
    flags = Flags(spend_holdout=True, override_gap=True)
    trial_id, results = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, flags, reasons=reasons, synthetic=True, store_path=store
    )

    assert results is not None
    conn = read()
    assert _row(conn, "trial_results", trial_id)["status"] == "ok"
    trial = _row(conn, "trials", trial_id)
    assert (trial["kind"], trial["gap_override_reason"]) == ("holdout", reasons.gap_reason)
    decisions = _decisions(conn, trial_id)
    assert set(decisions) == {"holdout_spend", "gap_override"}
    override = decisions["gap_override"]
    assert override["reason"] == reasons.gap_reason
    assert override["values"]["count_share"]["2019-06-28"] == pytest.approx(1 / 13)
    assert override["values"]["threshold"] == 0.05


# --- synthetic --------------------------------------------------------------------------


def test_synthetic_refused_on_the_real_store(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORE__PATH", str(store))
    calls = _spy(monkeypatch)
    with pytest.raises(registry.RealStoreRefused):
        run_hypothesis(SLUG, None, None, Flags(), synthetic=True)

    assert calls == []
    assert read().execute("SELECT COUNT(*) FROM trials").fetchone() == (0,)


def test_synthetic_accepted_on_a_temp_file(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch, live: Path
) -> None:
    _spy(monkeypatch, fail="universe")
    trial_id, _ = run_hypothesis(SLUG, None, None, Flags(), synthetic=True, store_path=store)

    assert not live.exists()
    trial = _row(read(), "trials", trial_id)
    assert trial["synthetic"] is True
