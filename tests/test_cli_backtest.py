"""Backtest CLI (backtest spec reqs 11, 12, 16; plan T42). SPIKE.

The live `settings.store.path` is the temp-file fixture store, as the owner's store is
for the real CLI: the commands have no store-path option. The hypothesis file mirrors
`tests/backtest/test_run.py`'s frozen window: in-sample from 2018-01-31, holdout
2019-06-03..2020-06-30, gap threshold 0.05, which the fixture's June 2019 count share
(1/13) breaks.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner, Result

from tradepartner.backtest import engine
from tradepartner.backtest.holdout import Flags
from tradepartner.backtest.run import run_hypothesis
from tradepartner.cli_backtest import app
from tradepartner.cli_record import scrub_text
from tradepartner.config import Settings
from tradepartner.store import registry
from tradepartner.store.db import open_for_write

SLUG = "h-cli"
EDGAR_UA = "Placid Tester placid.tester@example.com"
HOLDOUT_WINDOW = ["--start", "2019-05-31", "--end", "2019-08-30"]
SPEND = ["--spend-holdout", "--holdout-reason", "owner spends the holdout"]
OVERRIDE = ["--override-gap", "--gap-reason", "owner accepts the June gap"]

HYPOTHESIS = f"""# Hypothesis: CLI test

```toml hypothesis
slug = "{SLUG}"
family = "momentum"
title = "CLI test momentum"
in_sample_start = 2018-01-31

[holdout]
start = 2019-06-03
end = 2020-06-30

[strategy]
formation_months = 12
skip_months = 1
top_fraction = 0.5
weighting = "equal"
signal_total_return = true

[costs]
per_side_bps = 15.0
commission_per_share = 0.0
commission_per_order = 0.0
sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]

[gap]
count_share_threshold = 0.05
```
"""


@pytest.fixture(autouse=True)
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_store_path: Path) -> Path:
    """The fixture store as the live store; a configured EDGAR User-Agent (a secret)."""
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(fixture_store_path))
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", EDGAR_UA)
    return fixture_store_path


@pytest.fixture
def hypothesis_file(tmp_path: Path) -> Path:
    path = tmp_path / f"{SLUG}.md"
    path.write_text(HYPOTHESIS)
    return path


def invoke(*args: str) -> Result:
    """Run the CLI; every line it prints must already pass the `cli_record` scrub."""
    result = CliRunner().invoke(app, list(args))
    for stream in (result.stdout, result.stderr):
        assert scrub_text(stream, secrets=[EDGAR_UA])[1] == 0, stream
    return result


@pytest.fixture
def registered(hypothesis_file: Path) -> None:
    result = invoke("hypothesis", "register", str(hypothesis_file))
    assert result.exit_code == 0, result.output


def _rows(store: Path, sql: str, *params: object) -> list[tuple[object, ...]]:
    with duckdb.connect(str(store), read_only=True) as conn:
        return conn.execute(sql, list(params)).fetchall()


# --- hypothesis register ---------------------------------------------------------------


def test_register_prints_the_frozen_set_and_its_hash(hypothesis_file: Path) -> None:
    result = invoke("hypothesis", "register", str(hypothesis_file))

    assert result.exit_code == 0
    assert f"{SLUG} (momentum)" in result.stdout
    assert "strategy.top_fraction = 0.5" in result.stdout
    assert "params_sha256 " in result.stdout


def test_register_refuses_a_file_missing_a_required_key(tmp_path: Path) -> None:
    path = tmp_path / f"{SLUG}.md"
    path.write_text(HYPOTHESIS.replace("per_side_bps = 15.0\n", ""))

    result = invoke("hypothesis", "register", str(path))

    assert result.exit_code == 1
    assert "costs.per_side_bps" in result.stderr


# --- backtest: exit codes ----------------------------------------------------------------


@pytest.mark.usefixtures("registered")
def test_ok_run_exits_0_and_prints_trial_metrics_gap_and_flags(store: Path) -> None:
    result = invoke("backtest", SLUG)

    assert result.exit_code == 0, result.output
    [(trial_id, _status)] = _rows(
        store, "SELECT trial_id, status FROM trial_results WHERE status = 'ok'"
    )
    assert f"trial {trial_id}: ok (in_sample)" in result.stdout
    assert "metrics at 15 bp per side" in result.stdout
    for text in ("sharpe_annual", "strategy", "SPY", "MTUM", "gap maxima:", "flags:"):
        assert text in result.stdout
    assert _rows(store, "SELECT synthetic FROM trials") == [(False,)]


@pytest.mark.usefixtures("registered")
def test_failed_run_exits_1_with_the_traceback_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: object) -> None:
        raise RuntimeError(f"injected failure for {EDGAR_UA}")

    monkeypatch.setattr(engine, "run", broken)
    result = invoke("backtest", SLUG)

    assert result.exit_code == 1
    assert ": failed (in_sample)" in result.stdout
    assert "Traceback (most recent call last):" in result.stderr
    assert "placid.tester" not in result.stdout + result.stderr  # the secret is scrubbed


@pytest.mark.usefixtures("registered")
@pytest.mark.parametrize(
    ("args", "status"),
    [
        (["--start", "2017-12-29"], "refused_window"),
        (HOLDOUT_WINDOW, "refused_holdout"),
        ([*HOLDOUT_WINDOW, "--spend-holdout"], "refused_holdout"),
        ([*HOLDOUT_WINDOW, *SPEND], "refused_gap"),
        ([*HOLDOUT_WINDOW, *SPEND, "--override-gap"], "refused_gap"),
    ],
    ids=["window", "holdout", "spend-without-reason", "gap", "override-without-reason"],
)
def test_each_refusal_exits_2_and_is_a_trial(store: Path, args: list[str], status: str) -> None:
    result = invoke("backtest", SLUG, *args)

    assert result.exit_code == 2, result.output
    assert f": {status} (" in result.stdout
    assert _rows(store, "SELECT status FROM trial_results") == [(status,)]


@pytest.mark.usefixtures("registered")
def test_holdout_repeat_needs_its_flag_and_is_reported() -> None:
    assert invoke("backtest", SLUG, *HOLDOUT_WINDOW, *SPEND, *OVERRIDE).exit_code == 0
    again = invoke("backtest", SLUG, *HOLDOUT_WINDOW, *SPEND, *OVERRIDE)
    assert again.exit_code == 2
    assert "--holdout-repeat" in again.stdout

    repeat = invoke("backtest", SLUG, *HOLDOUT_WINDOW, *SPEND, *OVERRIDE, "--holdout-repeat")
    assert repeat.exit_code == 0
    assert "flags: " in repeat.stdout and "holdout_repeat" in repeat.stdout


def test_unregistered_hypothesis_exits_1_before_any_trial(store: Path) -> None:
    result = invoke("backtest", "nope")

    assert result.exit_code == 1
    assert "error:" in result.stderr


def test_there_is_no_synthetic_or_store_path_option() -> None:
    help_text = CliRunner().invoke(app, ["backtest", "--help"]).stdout
    assert "--synthetic" not in help_text
    assert "--store" not in help_text and "--path" not in help_text


# --- trials ---------------------------------------------------------------------------------


@pytest.mark.usefixtures("registered")
def test_trials_hides_synthetic_by_default_and_lists_unfinished(
    store: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A synthetic trial needs a store that is not the live one while it is written.
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "elsewhere.duckdb"))
    synthetic = run_hypothesis(SLUG, None, None, Flags(), synthetic=True, store_path=store)
    monkeypatch.setenv("STORE__PATH", str(store))
    with open_for_write(Settings(_env_file=None, store={"path": str(store)})) as conn:
        record = registry.get_hypothesis(conn, SLUG)
        unfinished = registry.open_trial(
            conn,
            hypothesis_id=record.hypothesis_id,
            kind="in_sample",
            start_session=date(2018, 1, 31),
            end_session=date(2019, 5, 31),
            data_cutoff=None,
            synthetic=False,
            run_by="test",
        )

    shown = invoke("trials")
    assert shown.exit_code == 0
    lines = shown.stdout.splitlines()
    assert [line.split()[0] for line in lines] == [str(unfinished.trial_id)]
    assert lines[0].endswith("unfinished")

    everything = invoke("trials", "--include-synthetic").stdout.splitlines()
    assert [line.split()[0] for line in everything] == [
        str(unfinished.trial_id),
        str(synthetic.trial_id),
    ]
    assert " synthetic " in everything[1]


def test_trials_on_a_store_without_the_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    empty = tmp_path / "empty.duckdb"
    duckdb.connect(str(empty)).close()
    monkeypatch.setenv("STORE__PATH", str(empty))

    result = invoke("trials")

    assert result.exit_code == 0
    assert "registry not initialised" in result.stdout


# --- decision gap-signoff -----------------------------------------------------------------


@pytest.mark.usefixtures("registered")
def test_gap_signoff_appends_a_row_with_the_gap_values(store: Path) -> None:
    assert invoke("backtest", SLUG).exit_code == 0
    [(trial_id,)] = _rows(store, "SELECT trial_id FROM trials")

    result = invoke("decision", "gap-signoff", "--trial", str(trial_id), "--reason", "gap ok")

    assert result.exit_code == 0, result.output
    [(kind, reason, values, row_trial)] = _rows(
        store, "SELECT kind, reason, values_json, trial_id FROM owner_decisions"
    )
    assert (kind, reason, row_trial) == ("gap_signoff", "gap ok", trial_id)
    assert '"gap_max_count_share"' in str(values)
    # Every planned rebalance; the last (2019-05-31) makes no plan, so it has no row.
    assert '"2019-04-30":0.0' in str(values)


@pytest.mark.usefixtures("registered")
def test_gap_signoff_refuses_a_trial_that_is_not_ok(store: Path) -> None:
    assert invoke("backtest", SLUG, *HOLDOUT_WINDOW).exit_code == 2
    [(trial_id,)] = _rows(store, "SELECT trial_id FROM trials")

    refused = invoke("decision", "gap-signoff", "--trial", str(trial_id), "--reason", "x")
    missing = invoke("decision", "gap-signoff", "--trial", "999", "--reason", "x")
    blank = invoke("decision", "gap-signoff", "--trial", str(trial_id), "--reason", " ")

    assert (refused.exit_code, missing.exit_code, blank.exit_code) == (2, 2, 2)
    assert "refused_holdout, not ok" in refused.stderr
    assert _rows(store, "SELECT COUNT(*) FROM owner_decisions") == [(0,)]


@pytest.mark.usefixtures("registered")
def test_gap_signoff_refuses_a_holdout_trial(store: Path) -> None:
    assert invoke("backtest", SLUG, *HOLDOUT_WINDOW, *SPEND, *OVERRIDE).exit_code == 0
    [(trial_id,)] = _rows(store, "SELECT trial_id FROM trials")

    result = invoke("decision", "gap-signoff", "--trial", str(trial_id), "--reason", "x")

    assert result.exit_code == 2
    assert "holdout trial" in result.stderr
    kinds = _rows(store, "SELECT kind FROM owner_decisions ORDER BY decision_id")
    assert kinds == [("holdout_spend",), ("gap_override",)]
