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
OVERRIDE = Flags(spend_holdout=True, override_gap=True)
OVERRIDE_REASONS = Reasons(holdout_reason="spend", gap_reason="owner accepts the June gap")

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
    """`(method, t)` per provider call (`t` the read time, `late_dividends`' second
    argument); `("__init__", None)` per construction. `inserted` is set once the
    mid-run write has happened."""

    inserted = False

    def read_times(self) -> list[datetime]:
        return [t for name, t in self if name != "__init__"]


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
            calls.append((name, args[1] if name == "late_dividends" else args[0]))
            if name == fail:
                raise RuntimeError("injected provider error")
            if name == "adjusted_prices" and write_to is not None and not calls.inserted:
                calls.inserted = True
                _insert_mid_run(self, write_to)
            return getattr(StoreProvider, name)(self, *args, **kwargs)

        return method

    for name in _METHODS:
        setattr(Spy, name, wrap(name))
    monkeypatch.setattr(run_module, "StoreProvider", Spy)
    return calls


def _insert_mid_run(provider: StoreProvider, path: Path) -> None:
    """A new bar after the window, ingested now, between two reads."""
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
    outcome = run_hypothesis(SLUG, None, None, Flags(), synthetic=True, store_path=store)

    assert (outcome.status, outcome.error) == ("ok", None)
    results = outcome.results
    assert results is not None and set(results) == LEVELS
    assert max(row.n_targets for row in results[15.0].rebalances) >= 2
    conn = read()
    trial = _row(conn, "trials", outcome.trial_id)
    assert (trial["kind"], trial["synthetic"]) == ("in_sample", True)
    assert (trial["start_session"], trial["end_session"]) == (IN_SAMPLE_START, DEFAULT_END)
    assert trial["data_cutoff"] == read_time(DEFAULT_END)
    assert _row(conn, "trial_results", outcome.trial_id)["status"] == "ok"
    levels = conn.execute(
        "SELECT DISTINCT cost_per_side_bps FROM trial_equity WHERE trial_id = ?",
        [outcome.trial_id],
    ).fetchall()
    assert {level for (level,) in levels} == LEVELS
    assert calls.count(("__init__", None)) == 1
    # Every read is at or before the default window's last rebalance close.
    assert calls.read_times() and max(calls.read_times()) <= read_time(DEFAULT_END)


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
    outcome = run_hypothesis(SLUG, None, None, Flags(), store_path=store)

    assert outcome.status == "ok" and outcome.results is not None
    [(params, start, end, levels)] = seen
    assert params.strategy.top_fraction == 0.5
    assert params.costs.per_side_bps == 15.0
    assert (params.holdout.start, params.holdout.end) == HOLDOUT
    assert params.gap.count_share_threshold == 0.05
    assert (start, end) == (IN_SAMPLE_START, DEFAULT_END)
    assert set(levels) == LEVELS
    assert _row(read(), "trial_results", outcome.trial_id)["status"] == "ok"


def test_injected_provider_error_leaves_failed_and_returns_the_traceback(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spy(monkeypatch, fail="universe")
    outcome = run_hypothesis(SLUG, None, None, Flags(), store_path=store)

    assert (outcome.status, outcome.results) == ("failed", None)
    assert outcome.error is not None
    assert outcome.error.startswith("Traceback (most recent call last):")
    assert "in method" in outcome.error  # the spy's frame: the whole stack is kept
    assert outcome.error.rstrip().endswith("RuntimeError: injected provider error")
    result = _row(read(), "trial_results", outcome.trial_id)
    assert result["status"] == "failed"
    assert result["message"] == "RuntimeError: injected provider error"


def test_row_inserted_mid_run_leaves_failed(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, write_to=store)
    outcome = run_hypothesis(SLUG, None, None, Flags(), store_path=store)

    assert (outcome.status, outcome.results, outcome.error) == ("failed", None, None)
    assert calls.inserted and ("adjusted_prices", read_time(DEFAULT_END)) in calls
    result = _row(read(), "trial_results", outcome.trial_id)
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
    outcome = run_hypothesis(SLUG, start, end, SPEND, store_path=store)

    assert (outcome.status, outcome.results) == ("refused_window", None)
    assert calls == []
    conn = read()
    assert _row(conn, "trial_results", outcome.trial_id)["status"] == "refused_window"
    trial = _row(conn, "trials", outcome.trial_id)
    assert (trial["start_session"], trial["end_session"]) == (start, end)


def test_refused_holdout_makes_no_provider_call(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch)
    outcome = run_hypothesis(SLUG, *HOLDOUT_WINDOW, Flags(), store_path=store)

    assert (outcome.status, outcome.results) == ("refused_holdout", None)
    assert calls == []
    conn = read()
    assert _row(conn, "trial_results", outcome.trial_id)["status"] == "refused_holdout"
    assert _row(conn, "trials", outcome.trial_id)["kind"] == "in_sample"
    assert _decisions(conn, outcome.trial_id) == {}


def test_refused_gap_after_gap_reads_only(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch)
    outcome = run_hypothesis(SLUG, *HOLDOUT_WINDOW, SPEND, reasons=SPEND_REASON, store_path=store)

    assert (outcome.status, outcome.results) == ("refused_gap", None)
    assert calls == [
        ("__init__", None),
        *(("survivorship_gap", read_time(s)) for s in HOLDOUT_SESSIONS),
    ]
    conn = read()
    result = _row(conn, "trial_results", outcome.trial_id)
    assert result["status"] == "refused_gap"
    assert "2019-06-28" in result["message"]
    trial = _row(conn, "trials", outcome.trial_id)
    assert (trial["kind"], trial["holdout_reason"]) == ("holdout", SPEND_REASON.holdout_reason)
    assert trial["gap_override_reason"] is None
    decisions = _decisions(conn, outcome.trial_id)
    assert set(decisions) == {"holdout_spend"}
    assert decisions["holdout_spend"]["reason"] == SPEND_REASON.holdout_reason


def test_override_without_a_gap_reason_spends_nothing(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch)
    blank = Reasons(holdout_reason="spend", gap_reason="  ")
    refused = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, OVERRIDE, reasons=blank, synthetic=True, store_path=store
    )

    assert (refused.status, refused.results) == ("refused_gap", None)
    assert calls == []
    conn = read()
    trial = _row(conn, "trials", refused.trial_id)
    assert (trial["kind"], trial["holdout_reason"], trial["gap_override_reason"]) == (
        "in_sample",
        None,
        None,
    )
    assert _decisions(conn, refused.trial_id) == {}
    conn.close()

    spend = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, OVERRIDE, reasons=OVERRIDE_REASONS, synthetic=True, store_path=store
    )
    assert spend.status == "ok"
    assert _row(read(), "trials", spend.trial_id)["holdout_repeat"] is False


def test_gap_override_runs_and_records_the_owner_decision(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, OVERRIDE, reasons=OVERRIDE_REASONS, synthetic=True, store_path=store
    )

    assert outcome.status == "ok" and outcome.results is not None
    conn = read()
    assert _row(conn, "trial_results", outcome.trial_id)["status"] == "ok"
    trial = _row(conn, "trials", outcome.trial_id)
    assert (trial["kind"], trial["gap_override_reason"]) == ("holdout", OVERRIDE_REASONS.gap_reason)
    decisions = _decisions(conn, outcome.trial_id)
    assert set(decisions) == {"holdout_spend", "gap_override"}
    override = decisions["gap_override"]
    assert override["reason"] == OVERRIDE_REASONS.gap_reason
    assert override["values"]["count_share"]["2019-06-28"] == pytest.approx(1 / 13)
    assert override["values"]["threshold"] == 0.05


def test_a_second_spend_by_the_same_hypothesis_needs_holdout_repeat(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, OVERRIDE, reasons=OVERRIDE_REASONS, synthetic=True, store_path=store
    )
    assert first.status == "ok"

    calls = _spy(monkeypatch)
    second = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, OVERRIDE, reasons=OVERRIDE_REASONS, synthetic=True, store_path=store
    )
    assert (second.status, second.results) == ("refused_holdout", None)
    assert calls == []

    repeat_flags = Flags(spend_holdout=True, override_gap=True, holdout_repeat=True)
    third = run_hypothesis(
        SLUG,
        *HOLDOUT_WINDOW,
        repeat_flags,
        reasons=OVERRIDE_REASONS,
        synthetic=True,
        store_path=store,
    )
    assert third.status == "ok" and third.results is not None

    conn = read()
    marks = {
        o.trial_id: (
            _row(conn, "trials", o.trial_id)["kind"],
            _row(conn, "trials", o.trial_id)["holdout_repeat"],
        )
        for o in (first, second, third)
    }
    assert marks == {
        first.trial_id: ("holdout", False),
        second.trial_id: ("in_sample", False),
        third.trial_id: ("holdout", True),
    }


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
    outcome = run_hypothesis(SLUG, None, None, Flags(), synthetic=True, store_path=store)

    assert not live.exists()
    trial = _row(read(), "trials", outcome.trial_id)
    assert trial["synthetic"] is True
