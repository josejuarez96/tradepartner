"""Reproduce a registered trial after a repair, on a temporary copy (ADR 0029, #3428,
plan T398).

The end-to-end tests run a trial on the fixture store (which is also the live store, as
the owner's is), retract a row the trial read through T389's `retract`, show a plain
re-run now differs, and show `reproduce_trial` returns every stored base-level `strategy`
metric equal. The `restore_for_trial` cases (module docstring of `store.reproduce`) each
have a unit test on the in-memory fixture store.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

import duckdb
import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.backtest.holdout import Flags, Reasons
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.backtest.run import replay_trial, run_hypothesis
from tradepartner.config import Settings
from tradepartner.store import registry, reproduce, retracted_rows
from tradepartner.store.db import open_for_write, open_read_only

SLUG = "h-repro"
RULE = "resolver: bar is not a trading day"
BAR = ("SEC_SPLIT_FUTURE", date(2018, 6, 1))
SPY_DIVIDEND = ("SEC_SPY", "", "dividend", date(2018, 6, 25))


def _frozen() -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        holdout={"start": date(2019, 6, 3), "end": date(2020, 6, 30)},
        gap={"count_share_threshold": 0.05},
    )


def _settings(path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(path)})


@pytest.fixture(autouse=True)
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_store_path: Path) -> Path:
    """The live store is the fixture store, as the owner's is the live one."""
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(fixture_store_path))
    return fixture_store_path


@pytest.fixture
def store(live: Path) -> Path:
    frozen = _frozen()
    with open_for_write(_settings(live)) as conn:
        registry.register_hypothesis(
            conn,
            slug=SLUG,
            family="momentum",
            title="reproduction",
            doc_path=f"docs/hypotheses/{SLUG}.md",
            doc_sha256="0" * 64,
            params=frozen_params_of(frozen, family="momentum"),
            in_sample_start=date(2018, 1, 31),
            holdout_start=date(2019, 6, 3),
            holdout_end=date(2020, 6, 30),
            registered_by="test",
            settings=frozen,
        )
    return live


def _metrics(path: Path, trial_id: int) -> dict[str, float | None]:
    with open_read_only(_settings(path)) as conn:
        return reproduce.stored_base_strategy_metrics(conn, trial_id)


def _counts(path: Path) -> dict[str, int]:
    tables = ("trials", "trial_results", "trial_metrics", "trial_equity", "owner_decisions")
    with open_read_only(_settings(path)) as conn:
        return {
            t: int(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0])  # type: ignore[index]
            for t in tables
        }


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _retract(path: Path, table: str, key: tuple[Any, ...]) -> None:
    with open_for_write(_settings(path)) as conn:
        retracted_rows.retract(conn, table, [key], "not a real row", "rel-1", RULE)


def _run() -> int:
    outcome = run_hypothesis(SLUG, None, None, Flags())
    assert outcome.status == "ok", outcome.error
    return outcome.trial_id


# --- the end-to-end reproduction ---------------------------------------------------


def test_a_retracted_bar_is_reproduced_where_a_plain_rerun_differs(
    store: Path, tmp_path: Path
) -> None:
    trial = _run()
    stored = _metrics(store, trial)
    assert stored  # the base-level strategy metrics, all of them
    _retract(store, "prices_daily", BAR)
    assert _metrics(store, _run()) != stored  # a plain re-run now differs
    before, counts = _hash(store), _counts(store)
    work = tmp_path / "work"
    work.mkdir()

    result = reproduce.reproduce_trial(trial, work_dir=work)

    assert result.verdict == "reproduced", result
    assert result.compared == len(stored)
    assert result.differences == ()
    assert result.restored.reinserted == 1
    assert _hash(store) == before  # the real store is untouched
    assert _counts(store) == counts  # no trial, result or metric row, no decision
    assert list(work.iterdir()) == []  # the copy is deleted
    with open_read_only(_settings(store)) as conn:
        assert not reproduce.has_reproduction_marker(conn)  # the marker is on the copy alone


def test_a_retracted_dividend_is_reproduced(store: Path) -> None:
    trial = _run()
    stored = _metrics(store, trial)
    _retract(store, "corporate_actions", SPY_DIVIDEND)
    assert _metrics(store, _run()) != stored
    before = _hash(store)

    result = reproduce.reproduce_trial(trial)

    assert result.verdict == "reproduced", result
    assert result.compared == len(stored)
    assert _hash(store) == before


def test_a_repair_that_closes_a_release_is_reproduced(store: Path, tmp_path: Path) -> None:
    """The repair's own closed release is a change being undone: counting it would make
    the copy's vintage differ from the trial's and refuse every real reproduction."""
    trial = _run()
    with open_read_only(_settings(store)) as conn:
        trial_vintage = conn.execute(
            "SELECT data_vintage FROM trials WHERE trial_id = ?", [trial]
        ).fetchone()[0]  # type: ignore[index]
    backup = tmp_path / "backup.duckdb"
    shutil.copyfile(store, backup)
    with open_for_write(_settings(store)) as conn, registry.open_backup(backup, store) as bak:
        registry.open_data_release(
            conn, bak, name="bar-repair", backup_path=str(backup), reason="drop a bad bar"
        )
    _retract(store, "prices_daily", BAR)
    with open_for_write(_settings(store)) as conn:
        registry.close_data_release(
            conn, name="bar-repair", sessions_from=date(2018, 6, 1), sessions_to=date(2018, 6, 29)
        )
    with open_read_only(_settings(store)) as conn:  # the live vintage moved
        live_vintage = registry.registered_data_vintage(
            conn, datetime(2019, 5, 31, 20, tzinfo=UTC), 1
        )
    assert live_vintage != trial_vintage
    before = _hash(store)

    result = reproduce.reproduce_trial(trial)

    assert result.verdict == "reproduced", result
    assert result.restored.releases_left_out == 2  # its before and after rows
    assert result.restored.reinserted == 1
    assert _hash(store) == before


def test_a_vintage_that_differs_after_restoration_is_not_reproducible(store: Path) -> None:
    trial = _run()
    with open_for_write(_settings(store)) as conn:
        # A store changed by a means that keeps no row: here the stored vintage no longer
        # matches what the store holds at the trial's cutoff.
        conn.execute("UPDATE trials SET data_vintage = data_vintage - INTERVAL 1 DAY")
    result = reproduce.reproduce_trial(trial)
    assert result.verdict == "not_reproducible"
    assert any(r.startswith("data_vintage") for r in result.reasons)
    assert result.compared == 0


def test_a_code_tree_that_differs_is_not_reproducible(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trial = _run()
    monkeypatch.setattr(registry, "code_tree_sha256", lambda repo_dir=None: "f" * 64)
    result = reproduce.reproduce_trial(trial)
    assert result.verdict == "not_reproducible"
    assert [r.split(":")[0] for r in result.reasons] == ["code_tree_sha256"]


def test_a_trial_without_an_ok_result_is_not_reproducible(store: Path) -> None:
    with open_for_write(_settings(store)) as conn:
        handle = registry.open_trial(
            conn,
            hypothesis_id=1,
            kind="in_sample",
            start_session=date(2018, 1, 31),
            end_session=date(2019, 5, 31),
            data_cutoff=None,
            synthetic=False,
            run_by="test",
        )
        registry.close_trial(conn, handle, "failed", "boom")
    result = reproduce.reproduce_trial(handle.trial_id)
    assert result.verdict == "not_reproducible"
    assert "status failed" in result.reasons[0]


def test_an_unknown_trial_is_named(store: Path) -> None:
    with pytest.raises(reproduce.UnknownTrial):
        reproduce.reproduce_trial(99)


# --- in sample and holdout ---------------------------------------------------------


def _holdout_trial() -> int:
    outcome = run_hypothesis(
        SLUG,
        date(2019, 5, 31),
        date(2019, 8, 30),
        Flags(spend_holdout=True, override_gap=True),
        reasons=Reasons(holdout_reason="spend", gap_reason="owner accepts the June gap"),
    )
    assert outcome.status == "ok", outcome.error
    return outcome.trial_id


def test_a_holdout_trial_refuses_without_the_flag_and_spends_nothing(store: Path) -> None:
    trial = _holdout_trial()
    counts, before = _counts(store), _hash(store)
    with pytest.raises(reproduce.HoldoutReplayRefused, match="--holdout-repeat"):
        reproduce.reproduce_trial(trial)
    result = reproduce.reproduce_trial(trial, holdout_repeat=True)
    assert result.verdict == "reproduced", result
    assert result.holdout_replay
    assert _counts(store) == counts  # the replay spent no holdout and added no trial to N
    assert _hash(store) == before


def test_an_in_sample_trial_needs_no_flag(store: Path) -> None:
    result = reproduce.reproduce_trial(_run())
    assert result.verdict == "reproduced"
    assert not result.holdout_replay


def test_replay_trial_refuses_a_copy_without_the_marker(store: Path, tmp_path: Path) -> None:
    trial = _run()
    copy = tmp_path / "unmarked.duckdb"
    shutil.copyfile(store, copy)
    before = _hash(copy)
    with pytest.raises(registry.UnmarkedStoreRefused, match="reproduction marker"):
        replay_trial(trial, copy)
    assert _hash(copy) == before


# --- the command -------------------------------------------------------------------


def _cli(*args: str) -> Any:
    return CliRunner().invoke(cli.make_app(), list(args))


def test_the_command_exits_zero_on_a_reproduction_and_one_otherwise(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trial = _run()
    _retract(store, "prices_daily", BAR)
    ok = _cli("trial-reproduce", str(trial))
    assert ok.exit_code == 0, ok.output
    assert "reproduced: all" in ok.output
    monkeypatch.setattr(registry, "code_tree_sha256", lambda repo_dir=None: "f" * 64)
    bad = _cli("trial-reproduce", str(trial))
    assert bad.exit_code == 1
    assert "not reproducible: code_tree_sha256" in bad.output


def test_the_command_names_a_holdout_replay(store: Path) -> None:
    trial = _holdout_trial()
    refused = _cli("trial-reproduce", str(trial))
    assert refused.exit_code != 0
    assert "--holdout-repeat" in refused.output
    ok = _cli("trial-reproduce", str(trial), "--holdout-repeat")
    assert ok.exit_code == 0, ok.output
    assert "holdout replay" in ok.output


# --- restore_for_trial -------------------------------------------------------------

S = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
MAX_INGESTED = datetime(2026, 1, 20, 9, 0, tzinfo=UTC)
DAY = timedelta(days=1)


def _facts(**over: Any) -> reproduce.TrialFacts:
    base = reproduce.TrialFacts(
        trial_id=1,
        hypothesis_id=1,
        kind="in_sample",
        started_at=S,
        start_session=date(2018, 1, 31),
        end_session=date(2019, 5, 31),
        data_cutoff=datetime(2019, 5, 31, 20, tzinfo=UTC),
        store_max_ingested_at=MAX_INGESTED,
        data_vintage=None,
        code_tree_sha256=None,
        synthetic=True,
        status="ok",
    )
    return replace(base, **over)


def _bars(conn: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    return conn.execute("SELECT * FROM prices_daily ORDER BY ALL").fetchall()


def _bar(conn: duckdb.DuckDBPyConnection) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT * FROM prices_daily WHERE security_id = ? AND session = ? ORDER BY ALL", list(BAR)
    ).fetchall()


def _retract_bar(conn: duckdb.DuckDBPyConnection, at: datetime) -> None:
    retracted_rows.retract(conn, "prices_daily", [BAR], "not a real bar", "rel-1", RULE, at=at)


def _insert_bar(conn: duckdb.DuckDBPyConnection, base: tuple[Any, ...], **over: Any) -> None:
    columns = [r[1] for r in conn.execute("PRAGMA table_info('prices_daily')").fetchall()]
    values = {**dict(zip(columns, base, strict=True)), **over}
    conn.execute(
        f"INSERT INTO prices_daily ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})",
        list(values.values()),
    )


def test_case_1_retracted_after_the_start_is_put_back_with_its_known_at(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    original = _bars(fixture_store)
    _retract_bar(fixture_store, S + DAY)
    assert _bars(fixture_store) != original
    summary = reproduce.restore_for_trial(fixture_store, _facts())
    assert summary.reinserted == 1
    assert _bars(fixture_store) == original  # every column, `known_at` included


def test_case_2_retracted_before_the_start_stays_out(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _retract_bar(fixture_store, S - DAY)
    after_retract = _bars(fixture_store)
    summary = reproduce.restore_for_trial(fixture_store, _facts())
    assert summary.left_out == 1
    assert summary.reinserted == 0
    assert _bars(fixture_store) == after_retract


def test_case_3_restored_after_the_start_is_deleted_from_the_copy(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    original = _bars(fixture_store)
    _retract_bar(fixture_store, S - 2 * DAY)
    retracted_rows.restore(fixture_store, table="prices_daily", key=BAR, at=S + DAY)
    assert _bars(fixture_store) == original  # the live table holds the row again
    summary = reproduce.restore_for_trial(fixture_store, _facts())
    assert summary.deleted_unseen == 1
    assert _bar(fixture_store) == []  # the trial never saw it
    assert len(_bars(fixture_store)) == len(original) - 1


def test_case_3_restored_before_the_start_is_in_the_copy(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    original = _bars(fixture_store)
    _retract_bar(fixture_store, S - 3 * DAY)
    retracted_rows.restore(fixture_store, table="prices_daily", key=BAR, at=S - DAY)
    reproduce.restore_for_trial(fixture_store, _facts())
    assert _bars(fixture_store) == original


def test_case_4_retracted_and_restored_after_the_start_is_nothing_to_do(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    original = _bars(fixture_store)
    _retract_bar(fixture_store, S + DAY)
    retracted_rows.restore(fixture_store, table="prices_daily", key=BAR, at=S + 2 * DAY)
    summary = reproduce.restore_for_trial(fixture_store, _facts())
    assert summary == reproduce.RestoreSummary()
    assert _bars(fixture_store) == original


def test_a_held_key_ingested_no_later_than_the_start_state_refuses_and_names_the_row(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    [row] = _bar(fixture_store)
    _retract_bar(fixture_store, S + DAY)
    # A later revision of the same bar, ingested before the trial started.
    _insert_bar(
        fixture_store,
        row,
        known_at=row[7] + timedelta(hours=1),
        ingested_at=row[8] + timedelta(hours=1),
    )
    held = _bars(fixture_store)
    with pytest.raises(reproduce.RestoreForTrialRefused, match="SEC_SPLIT_FUTURE"):
        reproduce.restore_for_trial(fixture_store, _facts())
    assert _bars(fixture_store) == held  # nothing re-inserted


def test_rows_ingested_after_the_start_state_are_deleted(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """A backfill with an old `known_at` written after the trial is not in the copy."""
    original = _bars(fixture_store)
    [row] = _bar(fixture_store)
    _insert_bar(fixture_store, row, session=date(2018, 6, 2), ingested_at=MAX_INGESTED + DAY)
    summary = reproduce.restore_for_trial(fixture_store, _facts())
    assert summary.deleted_after_ingest == 1
    assert _bars(fixture_store) == original


def test_a_later_backfill_of_a_retracted_key_is_deleted_then_the_original_put_back(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """The key a repair retracted was re-added by an ingest after the trial: that
    revision is not the trial's, so it goes and the retracted original returns."""
    original = _bars(fixture_store)
    [row] = _bar(fixture_store)
    _retract_bar(fixture_store, S + DAY)
    _insert_bar(
        fixture_store, row, known_at=row[7] + timedelta(hours=1), ingested_at=MAX_INGESTED + DAY
    )
    summary = reproduce.restore_for_trial(fixture_store, _facts())
    assert (summary.deleted_after_ingest, summary.reinserted) == (1, 1)
    assert _bars(fixture_store) == original


def test_releases_made_after_the_start_are_left_out_of_the_copy(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    for name, made in (("early", S - DAY), ("late", S + DAY)):
        registry._write_release(
            fixture_store,
            {"name": name, "stage": "after", "made_at": made, "sessions_from": date(2018, 6, 1)},
            "drop a bad bar",
            made_at=made,
        )
    summary = reproduce.restore_for_trial(fixture_store, _facts())
    assert summary.releases_left_out == 1
    assert [r.name for r in registry.data_releases(fixture_store)] == ["early"]


def test_the_marker_is_written_to_a_copy_and_read_back(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    assert not reproduce.has_reproduction_marker(fixture_store)
    reproduce.write_reproduction_marker(fixture_store, "test")
    assert reproduce.has_reproduction_marker(fixture_store)


def test_metrics_compare_in_full() -> None:
    nan = float("nan")
    same = reproduce.compare_metrics(
        {"a": 1.0, "b": None, "c": nan}, {"a": 1.0, "b": nan, "c": None}
    )
    assert same == []
    lines = reproduce.compare_metrics({"a": 1.0, "b": 2.0}, {"a": 1.0 + 1e-12, "c": 3.0})
    assert len(lines) == 3 and lines[0].startswith("a:")


def test_a_key_retracted_again_after_being_restored_is_not_put_back(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """Out of the table at the start (retracted, restored after, retracted again): the
    trial never saw it, so the copy must not hold it."""
    _retract_bar(fixture_store, S - 2 * DAY)
    retracted_rows.restore(fixture_store, table="prices_daily", key=BAR, at=S + DAY)
    _retract_bar(fixture_store, S + 2 * DAY)
    summary = reproduce.restore_for_trial(fixture_store, _facts())
    assert summary.reinserted == 0
    assert _bar(fixture_store) == []


def test_a_trial_with_no_stored_metrics_is_not_a_pass(
    store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trial = _run()
    with open_for_write(_settings(store)) as conn:
        conn.execute("DELETE FROM trial_metrics WHERE trial_id = ?", [trial])
    result = reproduce.reproduce_trial(trial)
    assert result.verdict == "not_reproducible"
    assert "no stored base-level" in result.reasons[0]
