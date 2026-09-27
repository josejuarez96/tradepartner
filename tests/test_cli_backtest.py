"""The Phase 3 commands: `backtest`, `hypothesis register`, `trials`, `decision`
(backtest plan T42; spec reqs 10-12 and 16).

The live `settings.store.path` (from `STORE__PATH`, no `.env`) is a temp copy of
the fixture universe: the CLI has no store-path option, so these tests point the
environment at it, the way the owner's `.env` points it at the real store. The
hypothesis file is the one `tests/backtest/test_run.py` uses: in-sample from
2018-01-31, holdout 2019-06-03..2020-06-30, gap threshold 0.05, which the
fixture's June 2019 gap (0.0769) breaches.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.backtest import engine
from tradepartner.cli_record import scrub_text
from tradepartner.config import Settings, get_settings
from tradepartner.store import registry
from tradepartner.store.db import open_for_write

SLUG = "h-cli"
HOLDOUT_WINDOW = ["--start", "2019-05-31", "--end", "2019-08-30"]
USER_AGENT = "Test Owner cli-owner@example.com"
ALPACA_KEY = "PK" + "T" * 20  # key-shaped for the scrub, not a real key
ALPACA_SECRET = "not-a-real-secret-" + "s" * 12

HYPOTHESIS = f"""# Hypothesis: CLI test (fixture, not a real hypothesis)

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
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_store_path: Path) -> Path:
    """The live store is the fixture copy; secrets are set so a leak is detectable."""
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(fixture_store_path))
    monkeypatch.setenv("SEC_EDGAR_USER_AGENT", USER_AGENT)
    monkeypatch.setenv("ALPACA_API_KEY", ALPACA_KEY)
    monkeypatch.setenv("ALPACA_API_SECRET", ALPACA_SECRET)
    return fixture_store_path


@pytest.fixture
def hypothesis_file(tmp_path: Path) -> Path:
    path = tmp_path / "hypotheses" / f"{SLUG}.md"
    path.parent.mkdir()
    path.write_text(HYPOTHESIS)
    return path


class Out:
    def __init__(self, result: Any) -> None:
        self.exit_code: int = result.exit_code
        self.stdout: str = result.stdout
        self.stderr: str = result.stderr
        self.output: str = result.output
        self.exception = result.exception


def _cli(*args: str) -> Out:
    return Out(CliRunner().invoke(cli.make_app(), list(args)))


def _assert_scrubbed(out: Out) -> None:
    """Nothing in the output matches a `cli_record` scrub pattern or a secret."""
    secrets = [USER_AGENT, ALPACA_KEY, ALPACA_SECRET]
    _, hits = scrub_text(out.output, secrets=secrets)
    assert hits == 0, out.output


@pytest.fixture
def registered(hypothesis_file: Path) -> Path:
    out = _cli("hypothesis", "register", str(hypothesis_file))
    assert out.exit_code == 0, out.output
    return hypothesis_file


@pytest.fixture
def read(live: Path) -> Iterator[Any]:
    """Opens lazily: no read-only connection may be open while the CLI writes."""
    conns: list[duckdb.DuckDBPyConnection] = []

    def _open() -> duckdb.DuckDBPyConnection:
        conns.append(duckdb.connect(str(live), read_only=True))
        return conns[-1]

    yield _open
    for conn in conns:
        conn.close()


def _trial_id(out: Out) -> int:
    for line in out.stdout.splitlines():
        if line.startswith("trial "):
            return int(line.split()[1].rstrip(":"))
    raise AssertionError(f"no trial id printed:\n{out.output}")


def _status(read: Any, trial_id: int) -> str:
    """The trial's result status, over a connection closed before returning, so
    a later command can still write. (`read` is kept for the fixture's setup.)"""
    with duckdb.connect(get_settings().store.path, read_only=True) as conn:
        row = conn.execute(
            "SELECT status FROM trial_results WHERE trial_id = ?", [trial_id]
        ).fetchone()
    assert row is not None
    return str(row[0])


# --- hypothesis register ---------------------------------------------------------


def test_register_prints_the_frozen_set_and_its_hash(hypothesis_file: Path, read: Any) -> None:
    out = _cli("hypothesis", "register", str(hypothesis_file))
    assert out.exit_code == 0, out.output
    record = registry.get_hypothesis(read(), SLUG)
    assert record.params_sha256 in out.stdout
    assert f"{SLUG}" in out.stdout
    for key, value in record.params.items():
        assert f"{key} = {json.dumps(value, default=str)}" in out.stdout, key
    assert record.registered_by == "owner"
    _assert_scrubbed(out)


def test_register_again_unchanged_returns_the_same_hypothesis(
    hypothesis_file: Path, read: Any
) -> None:
    first = _cli("hypothesis", "register", str(hypothesis_file))
    second = _cli("hypothesis", "register", str(hypothesis_file))
    assert (first.exit_code, second.exit_code) == (0, 0)
    count = read().execute("SELECT count(*) FROM hypotheses WHERE slug = ?", [SLUG]).fetchone()
    assert count == (1,)


def test_register_refuses_an_incomplete_file(tmp_path: Path, read: Any) -> None:
    path = tmp_path / "broken.md"
    path.write_text(HYPOTHESIS.replace("top_fraction = 0.5\n", ""))
    out = _cli("hypothesis", "register", str(path))
    assert out.exit_code == 2
    assert "strategy.top_fraction" in out.output
    assert read().execute("SELECT count(*) FROM hypotheses").fetchone() == (0,)


def test_register_refuses_a_missing_file(tmp_path: Path) -> None:
    assert _cli("hypothesis", "register", str(tmp_path / "nope.md")).exit_code == 2


# --- backtest ------------------------------------------------------------------


def test_ok_run_exits_zero_and_prints_trial_metrics_gap_and_flags(
    registered: Path, read: Any
) -> None:
    out = _cli("backtest", SLUG, "--note", "cli test")
    assert out.exit_code == 0, out.output
    trial_id = _trial_id(out)
    assert _status(read, trial_id) == "ok"
    for word in ("cagr", "sharpe_annual", "max_drawdown", "strategy", "SPY", "MTUM"):
        assert word in out.stdout, word
    assert "gap max" in out.stdout
    assert "red flag" in out.stdout
    assert "dsr" in out.stdout
    for level in ("0 bps", "15 bps", "30 bps", "60 bps", "100 bps"):
        assert level in out.stdout, level
    note = read().execute("SELECT note FROM trials WHERE trial_id = ?", [trial_id]).fetchone()
    assert note == ("cli test",)
    _assert_scrubbed(out)


def test_the_metrics_table_uses_the_frozen_base_cost(
    registered: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COSTS__PER_SIDE_BPS", "99")
    out = _cli("backtest", SLUG)
    assert out.exit_code == 0, out.output
    assert "metrics at the base cost, 15 bps per side" in out.stdout
    assert "99 bps" not in out.stdout


def test_failed_run_exits_one_with_the_trial_id_and_the_error(
    registered: Path, read: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise RuntimeError(f"engine exploded near {USER_AGENT}")

    monkeypatch.setattr(engine, "run", boom)
    out = _cli("backtest", SLUG)
    assert out.exit_code == 1, out.output
    assert _status(read, _trial_id(out)) == "failed"
    assert "RuntimeError" in out.output
    _assert_scrubbed(out)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (ValueError(f"in_sample_start after the holdout, {USER_AGENT}"), 2),
        (registry.RegistryError(f"bad registration {ALPACA_KEY}"), 2),
        (OSError(f"disk trouble {ALPACA_SECRET}"), 1),
    ],
)
def test_errors_before_a_trial_exit_cleanly_and_scrubbed(
    registered: Path, monkeypatch: pytest.MonkeyPatch, error: Exception, code: int
) -> None:
    def raises(*_: object, **__: object) -> None:
        raise error

    monkeypatch.setattr(cli, "run_hypothesis", raises)
    out = _cli("backtest", SLUG)
    assert out.exit_code == code, out.output
    assert type(error).__name__ in out.output
    assert "Traceback" not in out.output
    _assert_scrubbed(out)


def test_backtest_with_no_store_exits_one_and_creates_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "absent.duckdb"))
    out = _cli("backtest", SLUG)
    assert out.exit_code == 1
    assert not (tmp_path / "absent.duckdb").exists()


def test_backtest_refuses_an_injected_settings_store_other_than_the_loaded_one(
    registered: Path, tmp_path: Path, live: Path
) -> None:
    elsewhere = Settings(_env_file=None, store={"path": str(tmp_path / "elsewhere.duckdb")})
    duckdb.connect(elsewhere.store.path).close()
    app = cli.make_app(settings=lambda: elsewhere)
    out = CliRunner().invoke(app, ["backtest", SLUG])
    assert out.exit_code == 2
    assert "loaded settings' store" in out.output
    assert get_settings().store.path == str(live)


def test_refused_window_exits_two(registered: Path, read: Any) -> None:
    out = _cli("backtest", SLUG, "--start", "2017-06-30")
    assert out.exit_code == 2, out.output
    assert _status(read, _trial_id(out)) == "refused_window"


def test_refused_holdout_exits_two(registered: Path, read: Any) -> None:
    out = _cli("backtest", SLUG, *HOLDOUT_WINDOW)
    assert out.exit_code == 2, out.output
    assert _status(read, _trial_id(out)) == "refused_holdout"


def test_refused_gap_exits_two(registered: Path, read: Any) -> None:
    out = _cli("backtest", SLUG, *HOLDOUT_WINDOW, "--spend-holdout", "--holdout-reason", "spend")
    assert out.exit_code == 2, out.output
    assert _status(read, _trial_id(out)) == "refused_gap"


def test_gap_override_with_a_reason_runs(registered: Path, read: Any) -> None:
    out = _cli(
        "backtest",
        SLUG,
        *HOLDOUT_WINDOW,
        "--spend-holdout",
        "--holdout-reason",
        "spend",
        "--override-gap",
        "--gap-reason",
        "owner accepts the June gap",
    )
    assert out.exit_code == 0, out.output
    trial_id = _trial_id(out)
    kinds = (
        read()
        .execute("SELECT kind FROM owner_decisions WHERE trial_id = ? ORDER BY kind", [trial_id])
        .fetchall()
    )
    assert kinds == [("gap_override",), ("holdout_spend",)]
    assert "holdout" in out.stdout


def test_a_second_spend_needs_holdout_repeat(registered: Path, read: Any) -> None:
    spend = ["--spend-holdout", "--holdout-reason", "spend", "--override-gap", "--gap-reason", "ok"]
    assert _cli("backtest", SLUG, *HOLDOUT_WINDOW, *spend).exit_code == 0
    again = _cli("backtest", SLUG, *HOLDOUT_WINDOW, *spend)
    assert again.exit_code == 2
    assert _status(read, _trial_id(again)) == "refused_holdout"
    repeat = _cli("backtest", SLUG, *HOLDOUT_WINDOW, *spend, "--holdout-repeat")
    assert repeat.exit_code == 0, repeat.output
    assert "holdout repeat" in repeat.stdout


@pytest.mark.parametrize(
    "args",
    [
        ["--spend-holdout"],
        ["--spend-holdout", "--holdout-reason", "   "],
        ["--holdout-reason", "spend"],
        ["--gap-reason", "why"],
        ["--start", "2019-13-01"],
        ["--end", "yesterday"],
    ],
)
def test_flag_refusals_exit_two_before_any_trial(
    registered: Path, read: Any, args: list[str]
) -> None:
    out = _cli("backtest", SLUG, *args)
    assert out.exit_code == 2, out.output
    assert read().execute("SELECT count(*) FROM trials").fetchone() == (0,)


def test_unregistered_hypothesis_exits_two_with_no_trial(registered: Path, read: Any) -> None:
    out = _cli("backtest", "no-such-slug")
    assert out.exit_code == 2
    assert "no-such-slug" in out.output
    assert read().execute("SELECT count(*) FROM trials").fetchone() == (0,)


@pytest.mark.parametrize("flag", ["--synthetic", "--store", "--store-path", "--db"])
def test_there_is_no_synthetic_or_store_path_option(registered: Path, flag: str) -> None:
    out = _cli("backtest", SLUG, flag, "x")
    assert out.exit_code == 2
    assert "No such option" in out.output


# --- trials --------------------------------------------------------------------


def _synthetic_trial(live: Path) -> int:
    """A synthetic trial on the live store: opened with settings naming another
    store, the only way the registry lets one be written there."""
    elsewhere = Settings(_env_file=None, store={"path": str(live.parent / "other.duckdb")})
    with open_for_write(get_settings()) as conn:
        record = registry.get_hypothesis(conn, SLUG)
        handle = registry.open_trial(
            conn,
            hypothesis_id=record.hypothesis_id,
            kind="in_sample",
            start_session=date(2018, 1, 31),
            end_session=date(2019, 5, 31),
            data_cutoff=None,
            synthetic=True,
            run_by="test",
            settings=elsewhere,
        )
        registry.close_trial(conn, handle, "failed", "synthetic marker")
    return handle.trial_id


def test_trials_hides_synthetic_by_default_and_lists_unfinished(
    registered: Path, live: Path
) -> None:
    ok = _trial_id(_cli("backtest", SLUG))
    synthetic = _synthetic_trial(live)
    with open_for_write(get_settings()) as conn:  # a trial whose process died: no result row
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
        ).trial_id

    out = _cli("trials")
    assert out.exit_code == 0, out.output
    ids = _listed(out)
    assert ids == [unfinished, ok]  # newest first, synthetic hidden
    assert "unfinished" in out.stdout
    assert "synthetic marker" not in out.stdout

    everything = _cli("trials", "--include-synthetic")
    assert _listed(everything) == [unfinished, synthetic, ok]
    assert "synthetic" in everything.stdout

    assert _listed(_cli("trials", "--hypothesis", "other-slug")) == []
    assert _listed(_cli("trials", "--hypothesis", SLUG)) == [unfinished, ok]


def _listed(out: Out) -> list[int]:
    assert out.exit_code == 0, out.output
    return [
        int(line.split()[0])
        for line in out.stdout.splitlines()
        if line.strip() and line.split()[0].isdigit()
    ]


def test_trials_shows_failed_and_refused_rows_with_their_message(registered: Path) -> None:
    _cli("backtest", SLUG, *HOLDOUT_WINDOW)
    out = _cli("trials")
    assert "refused_holdout" in out.stdout
    assert "--spend-holdout" in out.stdout or "holdout" in out.stdout
    _assert_scrubbed(out)


def test_trials_with_no_store_exits_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "absent.duckdb"))
    out = _cli("trials")
    assert out.exit_code == 1
    assert "no store" in out.output.lower()
    assert not (tmp_path / "absent.duckdb").exists()


# --- decision gap-signoff ------------------------------------------------------


def test_gap_signoff_appends_an_owner_decision_with_the_gap_values(
    registered: Path, read: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    trial_id = _trial_id(_cli("backtest", SLUG))
    monkeypatch.setenv("GAP__COUNT_SHARE_THRESHOLD", "0.5")  # live value: must not be used
    out = _cli("decision", "gap-signoff", "--trial", str(trial_id), "--reason", "gap accepted")
    assert out.exit_code == 0, out.output
    rows = (
        read()
        .execute(
            "SELECT kind, reason, values_json, hypothesis_id FROM owner_decisions "
            "WHERE trial_id = ?",
            [trial_id],
        )
        .fetchall()
    )
    assert len(rows) == 1
    kind, reason, values_json, hypothesis_id = rows[0]
    assert (kind, reason) == ("gap_signoff", "gap accepted")
    assert hypothesis_id == registry.get_hypothesis(read(), SLUG).hypothesis_id
    values = json.loads(values_json)
    stored = (
        read()
        .execute(
            "SELECT gap_max_count_share, gap_max_size_share FROM trial_results WHERE trial_id = ?",
            [trial_id],
        )
        .fetchone()
    )
    assert stored is not None and stored[0] is not None
    assert (values["gap_max_count_share"], values["gap_max_size_share"]) == stored
    assert values["count_share_threshold"] == 0.05  # the frozen value, not the live 0.5
    _assert_scrubbed(out)


def test_a_second_signoff_appends_another_row(registered: Path, read: Any) -> None:
    trial_id = _trial_id(_cli("backtest", SLUG))
    for reason in ("first", "second"):
        out = _cli("decision", "gap-signoff", "--trial", str(trial_id), "--reason", reason)
        assert out.exit_code == 0
    count = (
        read().execute("SELECT count(*) FROM owner_decisions WHERE kind = 'gap_signoff'").fetchone()
    )
    assert count == (2,)


@pytest.mark.parametrize(
    ("trial", "reason"),
    [
        ("999", "why"),
        (None, "   "),
        (None, None),
        ("refused", "why"),
        ("failed", "why"),
        ("unfinished", "why"),
    ],
)
def test_gap_signoff_refusals_exit_two_and_write_nothing(
    registered: Path,
    read: Any,
    monkeypatch: pytest.MonkeyPatch,
    trial: str | None,
    reason: str | None,
) -> None:
    if trial is None:
        trial = str(_trial_id(_cli("backtest", SLUG)))
    elif trial == "refused":
        trial = str(_trial_id(_cli("backtest", SLUG, *HOLDOUT_WINDOW)))
    elif trial == "failed":
        with monkeypatch.context() as patch:
            patch.setattr(engine, "run", _boom)
            trial = str(_trial_id(_cli("backtest", SLUG)))
    elif trial == "unfinished":
        trial = str(_unfinished_trial())
    args = ["decision", "gap-signoff", "--trial", trial]
    if reason is not None:
        args += ["--reason", reason]
    out = _cli(*args)
    assert out.exit_code == 2, out.output
    count = (
        read().execute("SELECT count(*) FROM owner_decisions WHERE kind = 'gap_signoff'").fetchone()
    )
    assert count == (0,)


def _boom(*_: object, **__: object) -> None:
    raise RuntimeError("engine exploded")


def _unfinished_trial() -> int:
    """A trial whose process died: a `trials` row and no result row."""
    with open_for_write(get_settings()) as conn:
        record = registry.get_hypothesis(conn, SLUG)
        return registry.open_trial(
            conn,
            hypothesis_id=record.hypothesis_id,
            kind="in_sample",
            start_session=date(2018, 1, 31),
            end_session=date(2019, 5, 31),
            data_cutoff=None,
            synthetic=False,
            run_by="test",
        ).trial_id
