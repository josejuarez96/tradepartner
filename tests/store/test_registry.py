"""Tests for the trial registry API (Phase 3 T31b, `store/registry.py`).

Spec `docs/specs/backtest.md` reqs 8, 9, 11, 12 and the "Registry and
holdout" acceptance lines: inserts and reads only; ids increase; closing
twice raises; a handle is built only by `open_trial`; the oracle family
and synthetic trials are refused on `settings.store.path`; `family_sharpes`
counts N over every `ok`, non-synthetic, `in_sample` trial and takes V per
basis over the latest trial per (parameter hash, window) pair, never
reading an annualized value; holdout spends; `unfinished` listing;
decision rows; code version and dirty flag.
"""

from __future__ import annotations

import inspect
import os
import re
import statistics
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.config import Settings
from tradepartner.store import registry, schema
from tradepartner.store.db import insert_row

_T0 = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
_W1 = (date(2018, 1, 31), date(2022, 12, 30))
_W2 = (date(2019, 1, 31), date(2022, 12, 30))


def _params(per_side_bps: float = 15.0, **extra: Any) -> dict[str, Any]:
    return {"costs.per_side_bps": per_side_bps, "strategy.top_fraction": 0.1, **extra}


def _connect(path: Path) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    return conn


@pytest.fixture
def conn(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    """A registry on a temp file that is not `settings.store.path`."""
    return _connect(tmp_path / "scratch.duckdb")


@pytest.fixture
def real_conn(settings: Settings) -> duckdb.DuckDBPyConnection:
    """A registry on the file `settings.store.path` names."""
    return _connect(Path(settings.store.path))


def _register(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    slug: str = "h1",
    family: str = "momentum",
    params: dict[str, Any] | None = None,
    doc_sha256: str = "d" * 64,
) -> registry.HypothesisRecord:
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family=family,
        title=f"{slug} title",
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256=doc_sha256,
        params=params if params is not None else _params(),
        in_sample_start=date(2016, 1, 29),
        holdout_start=date(2023, 1, 3),
        holdout_end=date(2025, 12, 31),
        registered_by="owner",
        settings=settings,
    )


def _open(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    slug: str = "h1",
    kind: registry.TrialKind = "in_sample",
    window: tuple[date, date] = _W1,
    synthetic: bool = False,
    **kwargs: Any,
) -> registry.TrialHandle:
    return registry.open_trial(
        conn,
        hypothesis_id=registry.get_hypothesis(conn, slug).hypothesis_id,
        kind=kind,
        start_session=window[0],
        end_session=window[1],
        data_cutoff=_T0,
        synthetic=synthetic,
        run_by="test",
        settings=settings,
        repo_dir=tmp_path,
        **kwargs,
    )


def _metrics(
    conn: duckdb.DuckDBPyConnection,
    handle: registry.TrialHandle,
    raw: float,
    excess: float,
    level: float = 15.0,
) -> None:
    registry.write_metrics(
        conn,
        handle,
        [
            registry.MetricRow("strategy", level, "sharpe_monthly", raw),
            registry.MetricRow("strategy", level, "sharpe_monthly_excess_spy", excess),
            registry.MetricRow("strategy", level, "sharpe_annual", 99.0),
            registry.MetricRow("SPY", level, "sharpe_monthly", 5.0),
        ],
    )


def _ok_trial(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    raw: float,
    excess: float,
    **kwargs: Any,
) -> registry.TrialHandle:
    handle = _open(conn, settings, tmp_path, **kwargs)
    _metrics(conn, handle, raw, excess)
    _metrics(conn, handle, raw + 7.0, excess + 7.0, level=50.0)
    assert registry.write_result(conn, handle, registry.ResultStatistics()) == "ok"
    return handle


# --- module shape --------------------------------------------------------


def test_module_text_has_no_update_or_delete() -> None:
    text = inspect.getsource(registry)
    assert not re.search(r"\b(UPDATE|DELETE)\b", text, flags=re.IGNORECASE)


def test_handle_cannot_be_built_outside_open_trial() -> None:
    with pytest.raises(TypeError, match="open_trial"):
        registry.TrialHandle()


def test_handle_is_immutable(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    handle = _open(conn, settings, tmp_path)
    with pytest.raises(AttributeError):
        handle.trial_id = 99


# --- hypotheses ----------------------------------------------------------


def test_register_stores_canonical_params_and_their_hash(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    record = _register(conn, settings, params={"b": 2, "costs.per_side_bps": 15.0})
    (params_json, params_sha) = conn.execute(  # type: ignore[misc]
        "SELECT params_json, params_sha256 FROM hypotheses"
    ).fetchone()
    assert params_json == registry.canonical_params_json({"costs.per_side_bps": 15.0, "b": 2})
    assert params_sha == registry.params_sha256({"costs.per_side_bps": 15.0, "b": 2})
    assert record.params == {"b": 2, "costs.per_side_bps": 15.0}
    assert record.params_sha256 == params_sha


def test_register_identical_file_returns_the_existing_hypothesis(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    first = _register(conn, settings)
    again = _register(conn, settings)
    assert again.hypothesis_id == first.hypothesis_id
    (count,) = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()  # type: ignore[misc]
    assert count == 1


def test_changed_file_or_params_is_a_new_hypothesis_and_ids_increase(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    first = _register(conn, settings)
    changed_params = _register(conn, settings, params=_params(20.0))
    changed_file = _register(conn, settings, doc_sha256="e" * 64)
    ids = [first.hypothesis_id, changed_params.hypothesis_id, changed_file.hypothesis_id]
    assert ids == sorted(ids) and len(set(ids)) == 3
    assert registry.get_hypothesis(conn, "h1").hypothesis_id == changed_file.hypothesis_id


def test_unregistered_slug_is_refused(conn: duckdb.DuckDBPyConnection) -> None:
    with pytest.raises(registry.UnknownHypothesis, match="nope"):
        registry.get_hypothesis(conn, "nope")


def test_register_refuses_family_outside_the_enumerated_list(
    conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    only_momentum = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "real.duckdb")},
        hypotheses={"families": ["momentum"]},
    )
    with pytest.raises(registry.RegistryError, match="family"):
        _register(conn, only_momentum, family="oracle")
    with pytest.raises(registry.RegistryError, match="family"):
        _register(conn, only_momentum, family="value")


def test_register_refuses_params_without_the_base_cost_level(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    with pytest.raises(registry.RegistryError, match=r"costs\.per_side_bps"):
        _register(conn, settings, params={"strategy.top_fraction": 0.1})


def test_oracle_family_refused_on_the_real_store_and_accepted_on_a_temp_file(
    conn: duckdb.DuckDBPyConnection,
    real_conn: duckdb.DuckDBPyConnection,
    settings: Settings,
) -> None:
    with pytest.raises(registry.RealStoreRefused, match="oracle"):
        _register(real_conn, settings, family="oracle")
    assert _register(conn, settings, family="oracle").family == "oracle"


# --- trials --------------------------------------------------------------


def test_open_trial_records_every_field_and_ids_increase(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    hypothesis = _register(conn, settings)
    first = _open(conn, settings, tmp_path, note="n", holdout_reason=None)
    second = _open(conn, settings, tmp_path, kind="holdout", holdout_reason="spend")
    assert second.trial_id > first.trial_id
    assert first.hypothesis_id == hypothesis.hypothesis_id
    assert (first.family, first.kind, first.synthetic) == ("momentum", "in_sample", False)
    row = conn.execute(
        "SELECT hypothesis_id, kind, start_session, end_session, data_cutoff, "
        "code_version, code_dirty, synthetic, holdout_repeat, run_by, note "
        "FROM trials WHERE trial_id = ?",
        [first.trial_id],
    ).fetchone()
    assert row == (
        hypothesis.hypothesis_id,
        "in_sample",
        _W1[0],
        _W1[1],
        _T0,
        "unknown",
        None,
        False,
        False,
        "test",
        "n",
    )


def test_open_trial_refuses_an_unknown_hypothesis_id(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    with pytest.raises(registry.UnknownHypothesis, match="42"):
        registry.open_trial(
            conn,
            hypothesis_id=42,
            kind="in_sample",
            start_session=_W1[0],
            end_session=_W1[1],
            data_cutoff=_T0,
            synthetic=False,
            run_by="test",
            settings=settings,
            repo_dir=tmp_path,
        )
    (count,) = conn.execute("SELECT COUNT(*) FROM trials").fetchone()  # type: ignore[misc]
    assert count == 0


def test_open_trial_runs_the_named_registration_not_the_latest_for_the_slug(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """File A, edited to B, reverted to A: the run is recorded under A."""
    a = _register(conn, settings)
    b = _register(conn, settings, params=_params(20.0))
    again = _register(conn, settings)
    assert again.hypothesis_id == a.hypothesis_id
    assert registry.get_hypothesis(conn, "h1").hypothesis_id == b.hypothesis_id
    handle = registry.open_trial(
        conn,
        hypothesis_id=again.hypothesis_id,
        kind="in_sample",
        start_session=_W1[0],
        end_session=_W1[1],
        data_cutoff=_T0,
        synthetic=False,
        run_by="test",
        settings=settings,
        repo_dir=tmp_path,
    )
    assert (handle.hypothesis_id, handle.params_sha256) == (a.hypothesis_id, a.params_sha256)
    assert registry.get_hypothesis_by_id(conn, a.hypothesis_id).params == _params()


def test_a_slug_cannot_move_to_another_family(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _register(conn, settings)
    with pytest.raises(registry.RegistryError, match="cannot move"):
        _register(conn, settings, family="oracle", params=_params(20.0))


def test_identical_file_with_a_different_window_is_refused(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _register(conn, settings)
    with pytest.raises(registry.RegistryError, match="holdout_end"):
        registry.register_hypothesis(
            conn,
            slug="h1",
            family="momentum",
            title="h1 title",
            doc_path="docs/hypotheses/h1.md",
            doc_sha256="d" * 64,
            params=_params(),
            in_sample_start=date(2016, 1, 29),
            holdout_start=date(2023, 1, 3),
            holdout_end=date(2026, 12, 31),
            registered_by="owner",
            settings=settings,
        )


def test_window_params_must_agree_with_the_window_columns(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    _register(conn, settings, params={**_params(), "holdout.start": "2023-01-03"})
    with pytest.raises(registry.RegistryError, match=r"holdout\.start"):
        _register(conn, settings, slug="h2", params={**_params(), "holdout.start": "2024-01-02"})


def test_open_trial_accepts_a_missing_data_cutoff(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """A requested end off the trading calendar has no close to cut at; the
    refusal must still be a trial."""
    _register(conn, settings)
    handle = registry.open_trial(
        conn,
        hypothesis_id=registry.get_hypothesis(conn, "h1").hypothesis_id,
        kind="in_sample",
        start_session=_W1[0],
        end_session=date(2099, 12, 31),
        data_cutoff=None,
        synthetic=False,
        run_by="test",
        settings=settings,
        repo_dir=tmp_path,
    )
    registry.close_trial(conn, handle, "refused_window", "end after holdout.end")


def test_an_ok_result_needs_a_data_cutoff(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """A NULL cutoff would merge unrelated trials into one V pair."""
    _register(conn, settings)
    handle = registry.open_trial(
        conn,
        hypothesis_id=registry.get_hypothesis(conn, "h1").hypothesis_id,
        kind="in_sample",
        start_session=_W1[0],
        end_session=_W1[1],
        data_cutoff=None,
        synthetic=False,
        run_by="test",
        settings=settings,
        repo_dir=tmp_path,
    )
    with pytest.raises(registry.RegistryError, match="data_cutoff"):
        registry.write_result(conn, handle, registry.ResultStatistics())


def test_a_hard_link_to_the_real_store_is_still_the_real_store(
    real_conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(real_conn, settings)
    real_conn.close()
    link = tmp_path / "alias.duckdb"
    os.link(settings.store.path, link)
    aliased = duckdb.connect(str(link))
    try:
        with pytest.raises(registry.RealStoreRefused):
            _open(aliased, settings, tmp_path, synthetic=True)
        with pytest.raises(registry.RealStoreRefused):
            _register(aliased, settings, slug="o", family="oracle")
    finally:
        aliased.close()


def test_synthetic_refused_on_the_real_store_and_accepted_on_a_temp_file(
    conn: duckdb.DuckDBPyConnection,
    real_conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
) -> None:
    _register(real_conn, settings)
    with pytest.raises(registry.RealStoreRefused, match="synthetic"):
        _open(real_conn, settings, tmp_path, synthetic=True)
    _register(conn, settings)
    assert _open(conn, settings, tmp_path, synthetic=True).synthetic is True


def test_open_trial_captures_the_store_max_ingested_at(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    assert _open(conn, settings, tmp_path).store_max_ingested_at is None
    _insert_fact(conn, _T0)
    handle = _open(conn, settings, tmp_path)
    assert handle.store_max_ingested_at == _T0
    (stored,) = conn.execute(  # type: ignore[misc]
        "SELECT store_max_ingested_at FROM trials WHERE trial_id = ?", [handle.trial_id]
    ).fetchone()
    assert stored == _T0


def _insert_fact(conn: duckdb.DuckDBPyConnection, ingested_at: datetime) -> None:
    insert_row(
        conn,
        "securities",
        {
            "security_id": f"S{ingested_at.timestamp():.0f}",
            "cik": "1",
            "name": "X",
            "known_at": ingested_at,
            "ingested_at": ingested_at,
            "source": "fixture",
            "provenance": "filing",
        },
    )


# --- results -------------------------------------------------------------


def test_closing_twice_raises(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    handle = _open(conn, settings, tmp_path)
    registry.close_trial(conn, handle, "failed", "boom")
    with pytest.raises(registry.TrialAlreadyClosed):
        registry.close_trial(conn, handle, "failed", "again")
    with pytest.raises(registry.TrialAlreadyClosed):
        registry.write_result(conn, handle, registry.ResultStatistics())


def test_close_trial_refuses_ok_and_unknown_statuses(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    handle = _open(conn, settings, tmp_path)
    with pytest.raises(ValueError, match="write_result"):
        registry.close_trial(conn, handle, "ok")
    with pytest.raises(ValueError, match="done"):
        registry.close_trial(conn, handle, "done")


def test_write_result_stores_the_statistics(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    handle = _open(conn, settings, tmp_path)
    stats = registry.ResultStatistics(n_trials=3, dsr=0.4, dsr_basis="dsr", red_flag=False)
    assert registry.write_result(conn, handle, stats) == "ok"
    row = conn.execute(
        "SELECT status, message, n_trials, dsr, dsr_basis, red_flag, sr_star FROM trial_results"
    ).fetchone()
    assert row == ("ok", None, 3, 0.4, "dsr", False, None)


def test_write_result_records_failed_when_the_store_changed_during_the_run(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    handle = _open(conn, settings, tmp_path)
    _insert_fact(conn, _T0)
    assert registry.write_result(conn, handle, registry.ResultStatistics(dsr=0.9)) == "failed"
    row = conn.execute("SELECT status, message, dsr FROM trial_results").fetchone()
    assert row == ("failed", registry.STORE_CHANGED_MESSAGE, None)


# --- detail rows ---------------------------------------------------------


def test_detail_rows_are_written(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    handle = _open(conn, settings, tmp_path)
    _metrics(conn, handle, 0.1, 0.05)
    registry.write_equity(
        conn,
        handle,
        [
            registry.EquityRow("strategy", 15.0, date(2018, 2, 1), 1.0, 0.1),
            registry.EquityRow("SPY", 15.0, date(2018, 2, 1), 1.0, None),
        ],
    )
    registry.write_weights(
        conn,
        handle,
        [
            registry.WeightRow(date(2018, 2, 1), "SEC_A", 0.5, 10.0, 5.0),
            registry.WeightRow(date(2018, 2, 1), "SEC_B", 0.5, None, None),
        ],
    )
    registry.write_rebalances(conn, handle, [_rebalance_row()])
    counts = {
        table: conn.execute(  # type: ignore[index]
            f"SELECT COUNT(*) FROM {table} WHERE trial_id = ?", [handle.trial_id]
        ).fetchone()[0]
        for table in ("trial_metrics", "trial_equity", "trial_weights", "trial_rebalances")
    }
    assert counts == {
        "trial_metrics": 4,
        "trial_equity": 2,
        "trial_weights": 2,
        "trial_rebalances": 1,
    }
    assert conn.execute(
        "SELECT fill_price FROM trial_weights WHERE security_id = 'SEC_B'"
    ).fetchone() == (None,)


def test_detail_rows_refused_after_the_trial_is_closed(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    handle = _open(conn, settings, tmp_path)
    registry.close_trial(conn, handle, "failed", "boom")
    with pytest.raises(registry.TrialAlreadyClosed):
        _metrics(conn, handle, 0.1, 0.05)


def test_detail_rows_reject_a_datetime_session(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    handle = _open(conn, settings, tmp_path)
    with pytest.raises(TypeError, match="session"):
        registry.write_equity(conn, handle, [registry.EquityRow("strategy", 15.0, _T0, 1.0, 0.0)])


def test_a_rolled_back_handle_is_refused_even_after_its_id_is_reused(
    tmp_path: Path, settings: Settings
) -> None:
    conn = _connect(tmp_path / "rollback.duckdb")
    _register(conn, settings)
    conn.begin()
    stale = _open(conn, settings, tmp_path)
    conn.rollback()
    with pytest.raises(registry.RegistryError, match="rolled back"):
        registry.close_trial(conn, stale, "failed")
    fresh = _open(conn, settings, tmp_path)
    assert fresh.trial_id == stale.trial_id
    with pytest.raises(registry.RegistryError, match="rolled back"):
        registry.close_trial(conn, stale, "failed")
    registry.close_trial(conn, fresh, "failed", "its own outcome")


def test_a_handle_from_another_store_is_refused(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    handle = _open(conn, settings, tmp_path)
    other = _connect(tmp_path / "other.duckdb")
    _register(other, settings)
    _open(other, settings, tmp_path)
    with pytest.raises(registry.RegistryError, match="another store"):
        registry.close_trial(other, handle, "failed")


def _rebalance_row() -> registry.RebalanceRow:
    return registry.RebalanceRow(
        cost_per_side_bps=15.0,
        session=date(2018, 1, 31),
        fill_session=date(2018, 2, 1),
        n_universe=10,
        n_static_listings=1,
        n_targets=2,
        turnover=0.5,
        cost_paid=0.001,
        gap_count_share=0.01,
        gap_size_share=0.001,
        n_missing_fill=0,
        n_delisting_exits=0,
        n_stale_exits=0,
        n_excluded_no_history=1,
        n_dropped_dividends=0,
        n_late_dividends=0,
    )


# --- owner decisions -----------------------------------------------------


def test_record_decision_appends_rows_with_increasing_ids(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    hypothesis = _register(conn, settings)
    handle = _open(conn, settings, tmp_path)
    first = registry.record_decision(
        conn,
        kind="gap_signoff",
        reason="gap under threshold",
        values={"count_share": 0.01, "size_share": 0.002},
        hypothesis_id=hypothesis.hypothesis_id,
        trial_id=handle.trial_id,
    )
    second = registry.record_decision(conn, kind="holdout_spend", reason="final test", values={})
    assert second > first
    row = conn.execute(
        "SELECT kind, hypothesis_id, trial_id, values_json, reason FROM owner_decisions "
        "WHERE decision_id = ?",
        [first],
    ).fetchone()
    assert row == (
        "gap_signoff",
        hypothesis.hypothesis_id,
        handle.trial_id,
        '{"count_share":0.01,"size_share":0.002}',
        "gap under threshold",
    )


def test_record_decision_refuses_a_blank_reason_or_unknown_trial(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    with pytest.raises(ValueError, match="reason"):
        registry.record_decision(conn, kind="gap_signoff", reason="  ", values={})
    with pytest.raises(registry.RegistryError, match="trial 7"):
        registry.record_decision(conn, kind="gap_signoff", reason="r", values={}, trial_id=7)


# --- family_sharpes ------------------------------------------------------


def test_family_sharpes_counts_n_over_ok_in_sample_and_v_over_latest_per_pair(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    _register(conn, settings, slug="other", family="oracle")
    _ok_trial(conn, settings, tmp_path, 0.10, 0.01)  # pair (h1, W1), replaced below
    _ok_trial(conn, settings, tmp_path, 0.30, 0.03)  # rerun of (h1, W1): latest
    _ok_trial(conn, settings, tmp_path, 0.20, 0.02, window=_W2)
    failed = _open(conn, settings, tmp_path)
    registry.close_trial(conn, failed, "failed", "boom")
    _ok_trial(conn, settings, tmp_path, 9.0, 9.0, synthetic=True)
    _ok_trial(conn, settings, tmp_path, 9.0, 9.0, kind="holdout", holdout_reason="r")
    _open(conn, settings, tmp_path)  # unfinished
    _ok_trial(conn, settings, tmp_path, 9.0, 9.0, slug="other")

    result = registry.family_sharpes(conn, "momentum")

    assert result.n_trials == 3
    assert result.raw == (0.30, 0.20)
    assert result.excess_spy == (0.03, 0.02)
    assert result.variance("raw") == pytest.approx(statistics.variance([0.30, 0.20]))
    assert result.variance("excess_spy") == pytest.approx(statistics.variance([0.03, 0.02]))


def test_family_sharpes_with_one_pair_has_no_variance(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    _ok_trial(conn, settings, tmp_path, 0.1, 0.01)
    _ok_trial(conn, settings, tmp_path, 0.2, 0.02)
    result = registry.family_sharpes(conn, "momentum")
    assert (result.n_trials, result.raw) == (2, (0.2,))
    assert result.variance("raw") is None


def test_family_sharpes_counts_a_pending_trial_as_the_latest_of_its_pair(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    _ok_trial(conn, settings, tmp_path, 0.1, 0.01)
    _ok_trial(conn, settings, tmp_path, 0.2, 0.02, window=_W2)
    pending = _open(conn, settings, tmp_path)
    _metrics(conn, pending, 0.5, 0.05)
    assert registry.family_sharpes(conn, "momentum").n_trials == 2
    result = registry.family_sharpes(conn, "momentum", pending=pending)
    assert result.n_trials == 3
    assert result.raw == (0.2, 0.5)


def test_requested_windows_that_resolve_alike_are_one_pair(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """Starts on 2018-01-02 and 2018-01-31 both resolve to the January
    month-end session; with one data_cutoff they are reruns of one pair."""
    _register(conn, settings)
    _ok_trial(conn, settings, tmp_path, 0.1, 0.01, window=(date(2018, 1, 2), _W1[1]))
    _ok_trial(conn, settings, tmp_path, 0.3, 0.03, window=(date(2018, 1, 31), date(2022, 12, 31)))
    _ok_trial(conn, settings, tmp_path, 0.2, 0.02, window=_W2)
    result = registry.family_sharpes(conn, "momentum")
    assert (result.n_trials, result.raw) == (3, (0.3, 0.2))


def test_family_sharpes_refuses_a_nan_sharpe(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    _ok_trial(conn, settings, tmp_path, float("nan"), 0.01)
    with pytest.raises(registry.RegistryError, match="finite"):
        registry.family_sharpes(conn, "momentum")


def test_family_sharpes_refuses_a_pending_handle_from_another_store(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    other = _connect(tmp_path / "other.duckdb")
    _register(other, settings)
    foreign = _open(other, settings, tmp_path)
    _register(conn, settings)
    _open(conn, settings, tmp_path)
    with pytest.raises(registry.RegistryError, match="another store"):
        registry.family_sharpes(conn, "momentum", pending=foreign)


def test_family_sharpes_reads_the_base_level_of_each_hypothesis(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings, slug="cheap", params=_params(5.0))
    handle = _open(conn, settings, tmp_path, slug="cheap")
    _metrics(conn, handle, 0.4, 0.04, level=5.0)
    _metrics(conn, handle, 0.9, 0.09, level=15.0)
    registry.write_result(conn, handle, registry.ResultStatistics())
    assert registry.family_sharpes(conn, "momentum").raw == (0.4,)


def test_family_sharpes_raises_when_an_ok_trial_lacks_its_base_sharpe(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    handle = _open(conn, settings, tmp_path)
    registry.write_result(conn, handle, registry.ResultStatistics())
    with pytest.raises(registry.RegistryError, match="sharpe_monthly"):
        registry.family_sharpes(conn, "momentum")


def test_family_sharpes_never_reads_an_annualized_value() -> None:
    source = inspect.getsource(registry.family_sharpes) + inspect.getsource(registry._base_sharpes)
    assert "annual" not in source


# --- holdout spends and listing ------------------------------------------


def test_family_holdout_spends_lists_every_holdout_trial_in_the_family(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    _register(conn, settings, slug="h2")
    _register(conn, settings, slug="other", family="oracle")
    _ok_trial(conn, settings, tmp_path, 0.1, 0.01)
    spent = _ok_trial(conn, settings, tmp_path, 0.1, 0.0, kind="holdout", holdout_reason="a")
    failed = _open(conn, settings, tmp_path, slug="h2", kind="holdout", holdout_reason="b")
    registry.close_trial(conn, failed, "failed", "boom")
    _open(conn, settings, tmp_path, slug="other", kind="holdout", holdout_reason="c")

    spends = registry.family_holdout_spends(conn, "momentum")

    assert [(s.trial_id, s.slug, s.status, s.holdout_reason) for s in spends] == [
        (spent.trial_id, "h1", "ok", "a"),
        (failed.trial_id, "h2", "failed", "b"),
    ]


def test_list_trials_newest_first_with_unfinished_and_synthetic_hidden(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _register(conn, settings)
    _register(conn, settings, slug="h2")
    done = _ok_trial(conn, settings, tmp_path, 0.1, 0.01)
    synthetic = _open(conn, settings, tmp_path, synthetic=True)
    refused = _open(conn, settings, tmp_path, slug="h2")
    registry.close_trial(conn, refused, "refused_gap", "gap 0.09 > 0.05")
    open_one = _open(conn, settings, tmp_path)

    listed = registry.list_trials(conn)
    assert [(t.trial_id, t.status) for t in listed] == [
        (open_one.trial_id, "unfinished"),
        (refused.trial_id, "refused_gap"),
        (done.trial_id, "ok"),
    ]
    assert listed[1].message == "gap 0.09 > 0.05"
    with_synthetic = registry.list_trials(conn, include_synthetic=True)
    assert synthetic.trial_id in [t.trial_id for t in with_synthetic]
    assert [t.trial_id for t in registry.list_trials(conn, slug="h2")] == [refused.trial_id]


# --- code version --------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_code_version_reads_the_commit_and_dirty_flag(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "f.txt").write_text("a")
    _git(repo, "add", "f.txt")
    _git(repo, "commit", "-q", "-m", "c")
    sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert registry.code_version(repo) == (sha, False)
    (repo / "f.txt").write_text("b")
    assert registry.code_version(repo) == (sha, True)


def test_code_version_is_unknown_outside_a_checkout(tmp_path: Path) -> None:
    assert registry.code_version(tmp_path) == ("unknown", None)


def test_code_version_defaults_to_this_checkout() -> None:
    version, dirty = registry.code_version()
    assert re.fullmatch(r"[0-9a-f]{40}", version)
    assert isinstance(dirty, bool)
