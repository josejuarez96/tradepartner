"""Results, metrics and deflated Sharpe writes (backtest spec reqs 7, 8, 15; plan T39b).

Each test registers a hypothesis on `fixture_store_path`, opens a trial, runs the engine
on the fake provider at every cost level and hands the results to `write_results`.
Prices are a seeded random walk: constant growth would make every monthly return equal,
and `series_metrics` refuses a zero-variance series.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from datetime import UTC, date, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import polars as pl
import pytest

from backtest.fake_provider import FakeProvider
from tradepartner.backtest import results as results_module
from tradepartner.backtest.engine import BacktestResult, run
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.backtest.metrics import (
    EXCESS_SPY_KEYS,
    METRIC_KEYS,
    deflated_sharpe,
    red_flag,
    series_metrics,
)
from tradepartner.backtest.provider import GapReading
from tradepartner.backtest.results import write_results
from tradepartner.backtest.schedule import read_time, rebalance_sessions
from tradepartner.calendar import all_sessions, session_close
from tradepartner.config import Settings
from tradepartner.store import registry
from tradepartner.store.db import insert_row

HISTORY_START, HISTORY_END = date(2022, 12, 1), date(2024, 10, 31)
SESSIONS = [s for s in all_sessions() if HISTORY_START <= s <= HISTORY_END]
START, END = date(2024, 1, 31), date(2024, 9, 30)
#: A second window: a different (parameter hash, window) pair for V.
LATER_START = date(2024, 2, 29)
BASE, LEVELS = 15.0, (0.0, 15.0, 30.0)
BENCHMARKS = {"SPY": "S", "MTUM": "M"}
NAMES = ("A", "B", "C", "D", "E")
_CUTOFF = datetime(2024, 10, 1, tzinfo=UTC)
HOLDOUT = (date(2025, 1, 2), date(2025, 12, 31))


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "store": {"path": str(tmp_path / "real_store.duckdb")},
        "strategy": {"top_fraction": 0.4},
        "costs": {"per_side_bps": BASE, "sensitivity_per_side_bps": [0.0, 30.0]},
        "holdout": {"start": HOLDOUT[0], "end": HOLDOUT[1]},
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _prices(seed: int) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for sid in (*NAMES, *BENCHMARKS.values()):
        close = 100.0
        for session in SESSIONS:
            previous = close
            close *= 1 + float(rng.normal(0.0004, 0.012))
            rows.append((sid, session, (previous + close) / 2, close, session_close(session)))
    return pl.DataFrame(
        rows,
        schema={
            "security_id": pl.Utf8,
            "session": pl.Date,
            "open": pl.Float64,
            "close": pl.Float64,
            "known_at": pl.Datetime("us", "UTC"),
        },
        orient="row",
    )


#: Gap readings at two rebalances: the maxima are 0.02 (count) and 0.05 (size).
GAPS = {
    read_time(date(2024, 3, 28)): GapReading(count_share=0.02, size_share=0.01),
    read_time(date(2024, 6, 28)): GapReading(count_share=0.01, size_share=0.05),
}


def _provider(seed: int = 7) -> FakeProvider:
    members = {s: list(NAMES) for s in rebalance_sessions(START, HISTORY_END)}
    return FakeProvider(prices=_prices(seed), members=members, benchmarks=BENCHMARKS, gaps=GAPS)


@pytest.fixture
def conn(fixture_store_path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect(str(fixture_store_path))
    try:
        yield connection
    finally:
        connection.close()


def _register(
    conn: duckdb.DuckDBPyConnection, settings: Settings, slug: str = "h1"
) -> registry.HypothesisRecord:
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family="momentum",
        title=f"{slug} title",
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="d" * 64,
        params=frozen_params_of(settings),
        in_sample_start=START,
        holdout_start=HOLDOUT[0],
        holdout_end=HOLDOUT[1],
        registered_by="owner",
        settings=settings,
    )


def _open(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    *,
    kind: registry.TrialKind = "in_sample",
    synthetic: bool = False,
    start: date = START,
    slug: str = "h1",
) -> registry.TrialHandle:
    return registry.open_trial(
        conn,
        hypothesis_id=registry.get_hypothesis(conn, slug).hypothesis_id,
        kind=kind,
        start_session=start,
        end_session=END,
        data_cutoff=_CUTOFF,
        synthetic=synthetic,
        run_by="test",
        settings=settings,
        repo_dir=tmp_path,
    )


def _run(
    settings: Settings,
    handle: registry.TrialHandle,
    *,
    seed: int = 7,
    start: date = START,
    levels: tuple[float, ...] = LEVELS,
) -> dict[float, BacktestResult]:
    return run(settings, _provider(seed), start, END, handle, levels)


def _trial(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    *,
    seed: int = 7,
    start: date = START,
    **kwargs: Any,
) -> tuple[registry.TrialHandle, dict[float, BacktestResult], str]:
    handle = _open(conn, settings, tmp_path, start=start, **kwargs)
    results = _run(settings, handle, seed=seed, start=start)
    status = write_results(conn, handle, results, settings)
    return handle, results, status


def _result_row(conn: duckdb.DuckDBPyConnection, trial_id: int) -> dict[str, Any]:
    cursor = conn.execute("SELECT * FROM trial_results WHERE trial_id = ?", [trial_id])
    names = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    assert row is not None
    return dict(zip(names, row, strict=True))


def _metrics(
    conn: duckdb.DuckDBPyConnection, trial_id: int, series: str, level: float
) -> dict[str, float | None]:
    return dict(
        conn.execute(
            "SELECT metric, value FROM trial_metrics "
            "WHERE trial_id = ? AND series = ? AND cost_per_side_bps = ?",
            [trial_id, series, level],
        ).fetchall()
    )


def _monthly(result: BacktestResult, series: str) -> list[float]:
    equity = {row.session: row.equity for row in result.equity if row.series == series}
    ends = rebalance_sessions(START, END)
    return [equity[b] / equity[a] - 1 for a, b in pairwise(ends)]


def _count(conn: duckdb.DuckDBPyConnection, table: str, trial_id: int) -> int:
    (n,) = conn.execute(  # type: ignore[misc]
        f"SELECT COUNT(*) FROM {table} WHERE trial_id = ?", [trial_id]
    ).fetchone()
    return int(n)


class TestTablesWritten:
    def test_every_table_is_written(self, conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle, results, status = _trial(conn, settings, tmp_path)
        assert status == "ok"
        trial = handle.trial_id
        assert _count(conn, "trial_metrics", trial) == 3 * len(LEVELS) * len(METRIC_KEYS)
        assert _count(conn, "trial_equity", trial) == sum(len(r.equity) for r in results.values())
        assert _count(conn, "trial_rebalances", trial) == sum(
            len(r.rebalances) for r in results.values()
        )
        assert _count(conn, "trial_results", trial) == 1

    def test_weights_are_written_at_the_base_level_only(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle, results, _ = _trial(conn, settings, tmp_path)
        stored = conn.execute(
            "SELECT fill_session, security_id, target_weight, fill_price, shares "
            "FROM trial_weights WHERE trial_id = ? ORDER BY ALL",
            [handle.trial_id],
        ).fetchall()
        expected = sorted(
            (w.fill_session, w.security_id, w.target_weight, w.fill_price, w.shares)
            for w in results[BASE].weights
        )
        assert stored == expected
        assert results[0.0].weights != results[BASE].weights  # the levels differ

    def test_rebalances_are_written_per_level(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle, results, _ = _trial(conn, settings, tmp_path)
        per_level = dict(
            conn.execute(
                "SELECT cost_per_side_bps, SUM(cost_paid) FROM trial_rebalances "
                "WHERE trial_id = ? GROUP BY ALL",
                [handle.trial_id],
            ).fetchall()
        )
        assert per_level == pytest.approx(
            {
                level: sum(r.cost_paid for r in result.rebalances)
                for level, result in results.items()
            }
        )
        assert per_level[0.0] == 0.0


class TestMetrics:
    def test_every_series_and_level_has_the_req_7_keys(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle, _, _ = _trial(conn, settings, tmp_path)
        for series in ("strategy", "SPY", "MTUM"):
            for level in LEVELS:
                metrics = _metrics(conn, handle.trial_id, series, level)
                assert set(metrics) == set(METRIC_KEYS), (series, level)
                for key, value in metrics.items():
                    if series == "SPY" and key in EXCESS_SPY_KEYS:
                        assert value is None, key
                    else:
                        assert value is not None and math.isfinite(value), (series, level, key)

    def test_strategy_metrics_come_from_month_end_equity(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        """Month i return = equity at close(T_{i+1}) / equity at close(T_i) - 1, each
        level against the benchmarks at that level, `cost_drag` against the 0 bp run."""
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle, results, _ = _trial(conn, settings, tmp_path)
        base = results[BASE]
        expected = series_metrics(
            "strategy",
            monthly=_monthly(base, "strategy"),
            gross_monthly=_monthly(results[0.0], "strategy"),
            daily_equity=[r.equity for r in base.equity if r.series == "strategy"],
            turnover=[r.turnover for r in base.rebalances],
            spy_monthly=_monthly(base, "SPY"),
            mtum_monthly=_monthly(base, "MTUM"),
            risk_free_rate=settings.metrics.risk_free_rate,
        )
        assert _metrics(conn, handle.trial_id, "strategy", BASE) == pytest.approx(expected)

    def test_cost_drag_is_zero_at_zero_cost_and_grows_with_the_level(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle, _, _ = _trial(conn, settings, tmp_path)
        drag = {
            level: _metrics(conn, handle.trial_id, "strategy", level)["cost_drag"]
            for level in LEVELS
        }
        assert drag[0.0] == pytest.approx(0.0, abs=1e-15)
        assert 0 < drag[BASE] < drag[30.0]  # type: ignore[operator]

    def test_benchmark_turnover_is_zero(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        """A benchmark is bought once at F_0 and never rebalanced."""
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle, _, _ = _trial(conn, settings, tmp_path)
        for series in ("SPY", "MTUM"):
            assert _metrics(conn, handle.trial_id, series, BASE)["turnover_monthly"] == 0.0


class TestResultRow:
    def test_single_trial_takes_the_psr_basis_on_both_bases(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle, _, _ = _trial(conn, settings, tmp_path)
        row = _result_row(conn, handle.trial_id)
        base = _metrics(conn, handle.trial_id, "strategy", BASE)
        raw = deflated_sharpe(base, "raw", n_trials=1, pair_sharpes=[base["sharpe_monthly"]])  # type: ignore[list-item]
        excess = deflated_sharpe(
            base,
            "excess_spy",
            n_trials=1,
            pair_sharpes=[base["sharpe_monthly_excess_spy"]],  # type: ignore[list-item]
        )
        assert row["status"] == "ok"
        assert row["n_trials"] == 1
        assert row["dsr_basis"] == "psr"
        assert row["sharpe_variance"] is None and row["sharpe_variance_excess"] is None
        assert row["sr_star"] == 0.0 and row["sr_star_excess"] == 0.0
        assert row["psr_zero"] == pytest.approx(raw.psr_zero)
        assert row["dsr"] == pytest.approx(raw.psr_zero)
        assert row["psr_zero_excess"] == pytest.approx(excess.psr_zero)
        assert row["dsr_excess"] == pytest.approx(excess.psr_zero)
        assert row["psr_zero"] != pytest.approx(row["psr_zero_excess"])  # own inputs

    def test_gap_maxima_over_the_base_rebalances(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle, _, _ = _trial(conn, settings, tmp_path)
        row = _result_row(conn, handle.trial_id)
        assert row["gap_max_count_share"] == pytest.approx(0.02)
        assert row["gap_max_size_share"] == pytest.approx(0.05)

    @pytest.mark.parametrize("threshold_pp", [-100.0, 100.0])
    def test_red_flag_follows_base_excess_cagr(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path, threshold_pp: float
    ) -> None:
        settings = _settings(tmp_path)
        flagged = settings.model_copy(
            update={
                "metrics": settings.metrics.model_copy(
                    update={"red_flag_excess_cagr_pp": threshold_pp}
                )
            }
        )
        _register(conn, flagged)
        handle = _open(conn, flagged, tmp_path)
        write_results(conn, handle, _run(flagged, handle), flagged)
        base = _metrics(conn, handle.trial_id, "strategy", BASE)
        assert _result_row(conn, handle.trial_id)["red_flag"] is red_flag(base, flagged)
        assert _result_row(conn, handle.trial_id)["red_flag"] is (threshold_pp < 0)

    def test_store_change_during_the_run_records_failed(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle = _open(conn, settings, tmp_path)
        results = _run(settings, handle)
        now = datetime(2030, 1, 2, 21, 0, tzinfo=UTC)  # an ingest lands mid-run
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": "LATE",
                "session": date(2030, 1, 2),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "known_at": now,
                "ingested_at": now,
                "source": "test",
                "provenance": "bar",
            },
        )
        assert write_results(conn, handle, results, settings) == "failed"
        row = _result_row(conn, handle.trial_id)
        assert row["status"] == "failed"
        assert row["message"] == registry.STORE_CHANGED_MESSAGE


class TestTrialCount:
    """N counts `ok`, non-synthetic, in-sample trials of the family, this one included;
    V is over the latest trial per (parameter hash, window) pair (spec req 8)."""

    def test_rerun_raises_n_and_keeps_one_pair(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        _trial(conn, settings, tmp_path)
        handle, _, _ = _trial(conn, settings, tmp_path)
        row = _result_row(conn, handle.trial_id)
        assert row["n_trials"] == 2
        assert row["dsr_basis"] == "psr"
        assert row["sharpe_variance"] is None

    def test_a_second_window_is_a_second_pair(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        first, _, _ = _trial(conn, settings, tmp_path)
        second, _, _ = _trial(conn, settings, tmp_path, start=LATER_START)
        row = _result_row(conn, second.trial_id)
        sharpes = {
            basis: [_metrics(conn, h.trial_id, "strategy", BASE)[key] for h in (first, second)]
            for basis, key in (
                ("raw", "sharpe_monthly"),
                ("excess_spy", "sharpe_monthly_excess_spy"),
            )
        }
        base = _metrics(conn, second.trial_id, "strategy", BASE)
        raw = deflated_sharpe(base, "raw", n_trials=2, pair_sharpes=sharpes["raw"])  # type: ignore[arg-type]
        excess = deflated_sharpe(base, "excess_spy", n_trials=2, pair_sharpes=sharpes["excess_spy"])  # type: ignore[arg-type]
        assert row["n_trials"] == 2
        assert row["dsr_basis"] == "dsr"
        assert row["sharpe_variance"] == pytest.approx(raw.sharpe_variance)
        assert row["sr_star"] == pytest.approx(raw.sr_star)
        assert row["dsr"] == pytest.approx(raw.dsr)
        assert row["sharpe_variance_excess"] == pytest.approx(excess.sharpe_variance)
        assert row["sr_star_excess"] == pytest.approx(excess.sr_star)
        assert row["dsr_excess"] == pytest.approx(excess.dsr)

    @pytest.mark.parametrize(
        "kwargs",
        [{"synthetic": True}, {"kind": "holdout"}, {"kind": "tracking"}],
        ids=["synthetic", "holdout", "tracking"],
    )
    def test_uncounted_run_stores_the_family_n_and_changes_nothing(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path, kwargs: dict[str, Any]
    ) -> None:
        """A synthetic or holdout run stores the family's N at run time without itself,
        and leaves the next in-sample run's N and V as if it never ran."""
        settings = _settings(tmp_path)
        _register(conn, settings)
        _trial(conn, settings, tmp_path)
        other, _, _ = _trial(conn, settings, tmp_path, seed=11, **kwargs)
        assert _result_row(conn, other.trial_id)["n_trials"] == 1
        after, _, _ = _trial(conn, settings, tmp_path)
        row = _result_row(conn, after.trial_id)
        assert row["n_trials"] == 2
        assert row["dsr_basis"] == "psr"

    def test_first_run_of_an_empty_family_when_uncounted(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        """No counted trial yet: N = 0, nothing to deflate, DSR = PSR(0)."""
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle, _, _ = _trial(conn, settings, tmp_path, synthetic=True)
        row = _result_row(conn, handle.trial_id)
        assert row["n_trials"] == 0
        assert row["dsr_basis"] == "psr"
        assert row["dsr"] == pytest.approx(row["psr_zero"])

    def test_holdout_run_is_deflated_by_the_in_sample_pairs(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        """With two in-sample pairs, a holdout run stores N = 2 and takes SR* from their
        V, without adding its own Sharpe to V (quant-auditor on #206, note 4)."""
        settings = _settings(tmp_path)
        _register(conn, settings)
        first, _, _ = _trial(conn, settings, tmp_path)
        second, _, _ = _trial(conn, settings, tmp_path, start=LATER_START)
        holdout, _, _ = _trial(conn, settings, tmp_path, seed=11, kind="holdout")
        pairs = [
            _metrics(conn, h.trial_id, "strategy", BASE)["sharpe_monthly"] for h in (first, second)
        ]
        base = _metrics(conn, holdout.trial_id, "strategy", BASE)
        expected = deflated_sharpe(base, "raw", n_trials=2, pair_sharpes=pairs)  # type: ignore[arg-type]
        row = _result_row(conn, holdout.trial_id)
        assert row["n_trials"] == 2
        assert row["dsr_basis"] == "dsr"
        assert row["sr_star"] == pytest.approx(expected.sr_star)
        assert row["dsr"] == pytest.approx(expected.dsr)


class TestRefusals:
    """Bad inputs are refused before any row is written."""

    @pytest.mark.parametrize("levels", [(15.0, 30.0), (0.0, 30.0)], ids=["no-zero", "no-base"])
    def test_missing_level_is_refused(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path, levels: tuple[float, ...]
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle = _open(conn, settings, tmp_path)
        results = _run(settings, handle, levels=levels)
        with pytest.raises(ValueError, match="level"):
            write_results(conn, handle, results, settings)
        for table in ("trial_metrics", "trial_equity", "trial_weights", "trial_rebalances"):
            assert _count(conn, table, handle.trial_id) == 0

    def test_settings_other_than_the_trials_frozen_ones_are_refused(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        """The base level, risk-free rate and red-flag threshold must be the frozen ones
        `family_sharpes` reads (quant-auditor on #206, finding 1)."""
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle = _open(conn, settings, tmp_path)
        results = _run(settings, handle)
        live = settings.model_copy(
            update={"metrics": settings.metrics.model_copy(update={"risk_free_rate": 0.02})}
        )
        with pytest.raises(ValueError, match="frozen"):
            write_results(conn, handle, results, live)
        assert _count(conn, "trial_metrics", handle.trial_id) == 0

    def test_missing_benchmark_is_refused(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle = _open(conn, settings, tmp_path)
        members = {s: list(NAMES) for s in rebalance_sessions(START, HISTORY_END)}
        provider = FakeProvider(prices=_prices(7), members=members, benchmarks={"SPY": "S"})
        results = run(settings, provider, START, END, handle, LEVELS)
        with pytest.raises(ValueError, match="MTUM"):
            write_results(conn, handle, results, settings)
        assert _count(conn, "trial_metrics", handle.trial_id) == 0

    def test_mismatched_level_key_is_refused(
        self, conn: duckdb.DuckDBPyConnection, tmp_path: Path
    ) -> None:
        settings = _settings(tmp_path)
        _register(conn, settings)
        handle = _open(conn, settings, tmp_path)
        results = _run(settings, handle)
        swapped = {0.0: results[BASE], BASE: results[0.0], 30.0: results[30.0]}
        with pytest.raises(ValueError, match="level"):
            write_results(conn, handle, swapped, settings)


def test_module_writes_only_through_the_registry() -> None:
    """Append-only by construction: every write goes through `store.registry`."""
    source = Path(results_module.__file__).read_text(encoding="utf-8")
    assert "INSERT" not in source.upper().replace("INSERTS", "")
    assert "insert_row" not in source
