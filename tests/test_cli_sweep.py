"""The strategy-lab commands: `sweep register | run | status | report | promote |
retire`, `lab status`, and `backtest <variant-slug>` (strategy-lab spec req 15; plan
task T111).

Every case runs the CLI on a temp-file store that is the live `settings.store.path`
(`STORE__PATH`, no `.env`): the CLI has no store-path option. The lab store is built
once per module the way the lab leaves a family before its first sweep: the fixture
universe, H1's fixture twin registered before the lab tables exist, marked pre-lab,
fingerprinted, with the family rules written from it, and run once (an `ok` trial, so
the family is ready for a sweep). The sweep file is `tests/fixtures/sweeps/
fixture-sweep.md` at a window the fixture bars cover (four variants in two read
groups), with `promote_at_least = 0.0` (and `lab.promotion_min_dsr_excess = 0`) and a
`retire_below` far below any statistic, so its argmax can be promoted. A second module
store, `ran`, holds that sweep registered and run to completion through the CLI.

The un-migrated store is `fixture_store_path` (no lab tables). Secrets are set in the
environment so a leak into any output is detectable; the checkout reads clean
(`code_version` patched) and the sweep runner's clock is a fake one on a Saturday,
outside every configured quiet interval.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
import typer
from conftest import load_universe_fixtures, mark_pre_lab
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.backtest import frozen, hypothesis, lab, sweep, sweep_report
from tradepartner.backtest.holdout import Flags
from tradepartner.backtest.run import run_hypothesis
from tradepartner.cli_record import scrub_text
from tradepartner.config import Settings
from tradepartner.store import lab_registry, lab_schema, registry, schema
from tradepartner.store.db import configure_connection, open_for_write

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SWEEP_SOURCE = FIXTURES / "sweeps" / "fixture-sweep.md"
TWIN_SOURCE = FIXTURES / "hypotheses" / "fixture-momentum.md"
SLUG = "fixture-sweep"
TWIN = "fixture-momentum"
PROMOTED = "h-promoted"
WINDOW_EDITS = (
    ("in_sample_start = 2017-01-31", "in_sample_start = 2018-01-31"),
    ("start = 2023-01-03", "start = 2019-05-01"),
    ("end = 2025-12-31", "end = 2020-06-30"),
)
LAB_EDITS = (
    ("promote_at_least = 0.5", "promote_at_least = 0.0"),
    ("retire_below = 0.0", "retire_below = -100.0"),
)
#: A Saturday: no configured quiet interval (weekdays only) and none within a day.
SATURDAY = datetime(2026, 10, 10, 16, 0, tzinfo=UTC)
_CLEAN: tuple[str, bool] = ("test-clean", False)
USER_AGENT = "Test Owner lab-owner@example.com"
ALPACA_KEY = "PK" + "L" * 20  # key-shaped for the scrub, not a real key
ALPACA_SECRET = "not-a-real-secret-" + "l" * 12
MIGRATION = "run the lab migration first"
#: The registry tables a refused command must leave unchanged.
REGISTRY_TABLES = (
    "hypotheses",
    "trials",
    "trial_results",
    "owner_decisions",
    "sweeps",
    "sweep_variants",
    "sweep_runs",
    "sweep_trials",
)


class FakeClock:
    """The sweep runner's clock: moves only when the runner sleeps."""

    def __init__(self) -> None:
        self.t = SATURDAY

    def now(self) -> datetime:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += timedelta(seconds=seconds)


class Out:
    def __init__(self, result: Any) -> None:
        self.exit_code: int = result.exit_code
        self.stdout: str = result.stdout
        self.stderr: str = result.stderr
        self.output: str = result.output
        self.exception = result.exception


def _cli(*args: str) -> Out:
    """Run the CLI; every output is checked for secret-shaped text."""
    app = cli.make_app(sweep_clock=FakeClock())
    out = Out(CliRunner().invoke(app, list(args)))
    _, hits = scrub_text(out.output, secrets=[USER_AGENT, ALPACA_KEY, ALPACA_SECRET])
    assert hits == 0, out.output
    return out


def _edited(source: Path, directory: Path, edits: tuple[tuple[str, str], ...]) -> Path:
    text = source.read_text()
    for old, new in edits:
        assert old in text, old
        text = text.replace(old, new, 1)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / source.name
    path.write_text(text)
    return path


@contextmanager
def _env(mp: pytest.MonkeyPatch, path: Path, tmp: Path) -> Iterator[None]:
    mp.setenv("TRADEPARTNER_ENV_FILE", str(tmp / "none.env"))
    mp.setenv("STORE__PATH", str(path))
    mp.setenv("LAB__PROMOTION_MIN_DSR_EXCESS", "0")
    mp.setenv("SEC_EDGAR_USER_AGENT", USER_AGENT)
    mp.setenv("ALPACA_API_KEY", ALPACA_KEY)
    mp.setenv("ALPACA_API_SECRET", ALPACA_SECRET)
    mp.setattr(registry, "code_version", lambda repo_dir=None: _CLEAN)
    mp.setattr(lab_registry, "code_version", lambda repo_dir=None: _CLEAN)
    yield


def _sweep_file(directory: Path) -> Path:
    return _edited(SWEEP_SOURCE, directory, WINDOW_EDITS + LAB_EDITS)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The lab store before its first sweep (module docstring)."""
    directory = tmp_path_factory.mktemp("cli-lab-template")
    path = directory / "lab.duckdb"
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        load_universe_fixtures(conn, FIXTURES / "universe")
    finally:
        conn.close()
    with pytest.MonkeyPatch.context() as mp, _env(mp, path, directory):
        settings = Settings(_env_file=None)
        with open_for_write(Settings()) as conn:
            twin = hypothesis.register(
                conn,
                _edited(TWIN_SOURCE, directory / "files", WINDOW_EDITS),
                registered_by="test",
                settings=settings,
            )
            lab_schema.apply_lab_schema(conn)
            mark_pre_lab(conn, twin.hypothesis_id)
            values = frozen.frozen_values(twin)
            lab_registry.write_fingerprint(
                conn,
                twin.hypothesis_id,
                frozen.fingerprint(twin.family, values, twin.in_sample_start),
            )
            lab_registry.write_family_rules(
                conn,
                family=twin.family,
                first_hypothesis_id=twin.hypothesis_id,
                parent_family=None,
                holdout_start=twin.holdout_start,
                holdout_end=twin.holdout_end,
                in_sample_start=twin.in_sample_start,
                fixed_params=sweep.family_rule_params(values),
                sr_star_seed_annual=None,
                settings=settings,
            )
        assert run_hypothesis(twin.slug, None, None, Flags(), run_by="test").status == "ok"
    return path


@pytest.fixture(scope="module")
def ran(template: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The lab store with the sweep registered and run to completion by the CLI."""
    directory = tmp_path_factory.mktemp("cli-lab-ran")
    path = directory / "lab.duckdb"
    shutil.copy(template, path)
    with pytest.MonkeyPatch.context() as mp, _env(mp, path, directory):
        assert _cli("sweep", "register", str(_sweep_file(directory / "files"))).exit_code == 0
        out = _cli("sweep", "run", SLUG)
        assert out.exit_code == 0, out.output
    return path


def _live(source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "lab.duckdb"
    shutil.copy(source, path)
    with _env(monkeypatch, path, tmp_path):
        pass
    return path


@pytest.fixture
def store(template: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh copy of the template, the live store."""
    return _live(template, tmp_path, monkeypatch)


@pytest.fixture
def done(ran: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh copy of the store whose sweep is complete, the live store."""
    return _live(ran, tmp_path, monkeypatch)


@pytest.fixture
def plain(fixture_store_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The un-migrated fixture store (no lab tables), the live store."""
    with _env(monkeypatch, fixture_store_path, tmp_path):
        pass
    return fixture_store_path


@contextmanager
def _read(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        yield conn
    finally:
        conn.close()


def _counts(path: Path) -> dict[str, int]:
    with _read(path) as conn:
        present = {t for (t,) in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
        return {
            t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])  # type: ignore[index]
            for t in REGISTRY_TABLES
            if t in present
        }


def _variant_slugs(path: Path) -> list[str]:
    with _read(path) as conn:
        return [
            str(s)
            for (s,) in conn.execute(
                "SELECT h.slug FROM sweep_variants v JOIN hypotheses h USING (hypothesis_id) "
                "ORDER BY v.sweep_id, v.variant_index"
            ).fetchall()
        ]


# --- sweep register ---------------------------------------------------------------


def test_sweep_register_writes_the_registration_and_an_unchanged_file_writes_nothing(
    store: Path, tmp_path: Path
) -> None:
    file = _sweep_file(tmp_path / "files")
    out = _cli("sweep", "register", str(file))
    assert out.exit_code == 0, out.output
    slugs = _variant_slugs(store)
    assert len(slugs) == 4
    assert all(s in out.stdout for s in slugs)
    assert f"sweep {SLUG}" in out.stdout
    before = _counts(store)
    again = _cli("sweep", "register", str(file))
    assert again.exit_code == 0, again.output
    assert "unchanged" in again.stdout
    assert _counts(store) == before


def test_sweep_register_refuses_a_bad_file_and_a_missing_one(store: Path, tmp_path: Path) -> None:
    before = _counts(store)
    bad = _sweep_file(tmp_path / "files")
    bad.write_text(bad.read_text().replace('selection_statistic = "sharpe', 'x = "sharpe'))
    out = _cli("sweep", "register", str(bad))
    assert out.exit_code == 2, out.output
    assert "refused" in out.output
    assert _cli("sweep", "register", str(tmp_path / "nope.md")).exit_code == 2
    assert _counts(store) == before


# --- sweep run ----------------------------------------------------------------------


def test_sweep_run_runs_every_variant_and_records_the_run(done: Path) -> None:
    with _read(done) as conn:
        statuses = conn.execute(
            "SELECT r.status FROM sweep_trials s JOIN trial_results r USING (trial_id)"
        ).fetchall()
        runs = conn.execute("SELECT note, completed FROM sweep_runs").fetchall()
    assert sorted(s for (s,) in statuses) == ["ok"] * 4
    assert runs == [(None, True)]


def test_sweep_run_takes_a_budget_and_a_note_and_rerun_after_completion(done: Path) -> None:
    out = _cli("sweep", "run", SLUG, "--rerun", "--time-budget-minutes", "30", "--note", "again")
    assert out.exit_code == 0, out.output
    assert "4 planned" in out.stdout
    with _read(done) as conn:
        runs = conn.execute(
            "SELECT time_budget_minutes, note FROM sweep_runs ORDER BY sweep_run_id"
        ).fetchall()
    assert runs[-1] == (30, "again")


def test_sweep_run_refuses_rerun_on_an_incomplete_sweep_and_an_unknown_slug(
    store: Path, tmp_path: Path
) -> None:
    assert _cli("sweep", "register", str(_sweep_file(tmp_path / "files"))).exit_code == 0
    before = _counts(store)
    out = _cli("sweep", "run", SLUG, "--rerun")
    assert out.exit_code == 2, out.output
    assert _cli("sweep", "run", "no-such-sweep").exit_code == 2
    assert _cli("sweep", "run", SLUG, "--time-budget-minutes", "0").exit_code == 2
    assert _counts(store) == before


def test_an_error_after_the_run_opened_exits_1_not_as_a_refusal(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _cli("sweep", "register", str(_sweep_file(tmp_path / "files"))).exit_code == 0

    def broken(*_args: object, **_kwargs: object) -> None:
        raise ValueError("a mid-run defect")

    monkeypatch.setattr(lab, "_run_group", broken)
    out = _cli("sweep", "run", SLUG)
    assert out.exit_code == 1, out.output
    assert "refused" not in out.output
    assert "rows written so far stay recorded" in out.output
    with _read(store) as conn:
        assert conn.execute("SELECT completed FROM sweep_runs").fetchall() == [(False,)]


def test_sweep_run_refuses_above_the_registry_size_limit(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _cli("sweep", "register", str(_sweep_file(tmp_path / "files"))).exit_code == 0
    before = _counts(store)
    monkeypatch.setenv("LAB__REGISTRY_SIZE_WARN_GB", "0.000001")
    monkeypatch.setenv("LAB__REGISTRY_SIZE_REFUSE_GB", "0.000002")
    out = _cli("sweep", "run", SLUG)
    assert out.exit_code == 2, out.output
    assert "registry_size_refuse_gb" in out.output
    assert _counts(store) == before


# --- sweep status, report ---------------------------------------------------------------


def test_sweep_status_lists_every_sweep_and_one_sweeps_variants(
    store: Path, tmp_path: Path
) -> None:
    assert _cli("sweep", "register", str(_sweep_file(tmp_path / "files"))).exit_code == 0
    every = _cli("sweep", "status")
    assert every.exit_code == 0, every.output
    assert SLUG in every.stdout
    assert "incomplete (unrun)" in every.stdout
    one = _cli("sweep", "status", SLUG)
    assert one.exit_code == 0, one.output
    assert all(s in one.stdout for s in _variant_slugs(store))
    assert _cli("sweep", "status", "no-such-sweep").exit_code == 2


def test_sweep_status_of_a_complete_sweep(done: Path) -> None:
    out = _cli("sweep", "status", SLUG)
    assert out.exit_code == 0, out.output
    assert "complete" in out.stdout
    assert "incomplete" not in out.stdout


def test_sweep_report_prints_the_report_and_refuses_an_unknown_slug(done: Path) -> None:
    out = _cli("sweep", "report", SLUG)
    assert out.exit_code == 0, out.output
    assert f"sweep {SLUG} (registration" in out.stdout
    assert "argmax:" in out.stdout
    assert _cli("sweep", "report", "no-such-sweep").exit_code == 2


# --- sweep promote, retire --------------------------------------------------------------


def _promotion_file(path: Path, directory: Path) -> Path:
    """The momentum fixture at the argmax variant's values, with its provenance."""
    with _read(path) as conn:
        report = sweep_report.sweep_report(conn, SLUG)
        assert report.verdicts is not None
        argmax = report.verdicts.argmax
        record = registry.get_hypothesis(conn, argmax.slug)
    values = frozen.frozen_values(record)
    text = TWIN_SOURCE.read_text()
    for old, new in (
        *WINDOW_EDITS,
        (f'slug = "{TWIN}"', f'slug = "{PROMOTED}"'),
        ("top_fraction = 0.10", f"top_fraction = {values['strategy.top_fraction']}"),
        (
            "[gap]",
            f'[schedule]\nrebalance_cadence = "{values["schedule.rebalance_cadence"]}"\n'
            'signal_anchor = "month_end"\n\n[gap]',
        ),
    ):
        assert old in text, old
        text = text.replace(old, new, 1)
    text += (
        "\n## Sweep provenance\n\n"
        f"- **Sweep:** `{SLUG}`, registration id {report.sweep_id}; the family's declared "
        f"count n = {report.declared_count} at promotion\n"
        f"- **Variant:** `{argmax.slug}`, rank 1 (the argmax) of the registration's "
        f"{report.n_declared} variants by {report.selection_statistic}\n"
    )
    directory.mkdir(parents=True, exist_ok=True)
    out = directory / f"{PROMOTED}.md"
    out.write_text(text)
    return out


def test_sweep_promote_registers_the_argmax_file_with_its_decision(
    done: Path, tmp_path: Path
) -> None:
    file = _promotion_file(done, tmp_path / "promote")
    out = _cli("sweep", "promote", SLUG, "--file", str(file), "--reason", "as registered")
    assert out.exit_code == 0, out.output
    assert PROMOTED in out.stdout
    with _read(done) as conn:
        kinds = conn.execute("SELECT kind FROM owner_decisions").fetchall()
        assert registry.get_hypothesis(conn, PROMOTED).slug == PROMOTED
    assert ("promotion",) in kinds


def test_sweep_promote_refuses_a_blank_reason_and_a_missing_file(
    done: Path, tmp_path: Path
) -> None:
    file = _promotion_file(done, tmp_path / "promote")
    before = _counts(done)
    blank = _cli("sweep", "promote", SLUG, "--file", str(file), "--reason", " ")
    assert blank.exit_code == 2, blank.output
    missing = _cli("sweep", "promote", SLUG, "--file", str(tmp_path / "x.md"), "--reason", "r")
    assert missing.exit_code == 2, missing.output
    assert _counts(done) == before


def test_sweep_promote_refuses_while_the_sweep_is_unrun(store: Path, tmp_path: Path) -> None:
    assert _cli("sweep", "register", str(_sweep_file(tmp_path / "files"))).exit_code == 0
    file = tmp_path / "anything.md"
    file.write_text(TWIN_SOURCE.read_text())
    before = _counts(store)
    out = _cli("sweep", "promote", SLUG, "--file", str(file), "--reason", "too early")
    assert out.exit_code == 2, out.output
    assert "refused" in out.output
    assert _counts(store) == before


def test_sweep_retire_records_the_decision_once(done: Path) -> None:
    out = _cli("sweep", "retire", SLUG, "--reason", "nothing to promote")
    assert out.exit_code == 0, out.output
    with _read(done) as conn:
        assert conn.execute("SELECT kind FROM owner_decisions").fetchall() == [("sweep_retired",)]
    assert _cli("sweep", "retire", SLUG, "--reason", "again").exit_code == 2
    assert _cli("sweep", "retire", SLUG, "--reason", "  ").exit_code == 2
    assert _cli("sweep", "retire", "no-such-sweep", "--reason", "r").exit_code == 2


# --- lab status -------------------------------------------------------------------------


def test_lab_status_prints_the_registry(done: Path) -> None:
    out = _cli("lab", "status")
    assert out.exit_code == 0, out.output
    for text in ("store size:", "families:", "momentum: N", "last 10 sweep runs:", SLUG):
        assert text in out.stdout


# --- backtest of a variant --------------------------------------------------------------


def test_backtest_of_a_variant_slug_exits_2_refused_variant(done: Path) -> None:
    assert cli.STATUS_EXIT["refused_variant"] == 2
    variant = _variant_slugs(done)[0]
    with _read(done) as conn:
        record = registry.get_hypothesis(conn, variant)
        before = conn.execute(
            "SELECT COUNT(*) FROM trials WHERE hypothesis_id = ?", [record.hypothesis_id]
        ).fetchone()
    out = _cli("backtest", variant)
    assert out.exit_code == 2, out.output
    assert "refused_variant" in out.stdout
    with _read(done) as conn:
        rows = conn.execute(
            "SELECT r.status FROM trials t JOIN trial_results r USING (trial_id) "
            "WHERE t.hypothesis_id = ? ORDER BY t.trial_id",
            [record.hypothesis_id],
        ).fetchall()
    assert before is not None
    assert len(rows) == before[0] + 1
    assert rows[-1] == ("refused_variant",)


# --- the un-migrated store -------------------------------------------------------------

LAB_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("sweep", "register", "<file>"),
    ("sweep", "run", SLUG),
    ("sweep", "status"),
    ("sweep", "status", SLUG),
    ("sweep", "report", SLUG),
    ("sweep", "promote", SLUG, "--file", "<file>", "--reason", "r"),
    ("sweep", "retire", SLUG, "--reason", "r"),
    ("lab", "status"),
)


@pytest.mark.parametrize("command", LAB_COMMANDS, ids=lambda c: " ".join(c[:2]))
def test_every_lab_command_on_an_unmigrated_store_exits_2_naming_the_migration(
    plain: Path, tmp_path: Path, command: tuple[str, ...]
) -> None:
    file = _sweep_file(tmp_path / "files")
    before = _counts(plain)
    out = _cli(*(str(file) if part == "<file>" else part for part in command))
    assert out.exit_code == 2, out.output
    assert MIGRATION in out.output
    assert _counts(plain) == before


def test_hypothesis_register_and_backtest_on_an_unmigrated_store_are_phase_3(
    plain: Path, tmp_path: Path
) -> None:
    file = _edited(TWIN_SOURCE, tmp_path / "files", WINDOW_EDITS)
    assert _cli("hypothesis", "register", str(file)).exit_code == 0
    out = _cli("backtest", TWIN)
    assert out.exit_code == 0, out.output
    assert ": ok" in out.stdout


# --- the parser ----------------------------------------------------------------------------

FORBIDDEN_OPTION = re.compile(r"store|path|start|end|window|holdout|gap|synthetic")


def _commands(group: Any) -> Iterator[Any]:
    """Every command under a Typer group, nested groups included (Typer's own Click)."""
    for command in group.commands.values():
        yield command
        if hasattr(command, "commands"):
            yield from _commands(command)


def test_no_lab_command_takes_a_store_path_window_holdout_gap_or_synthetic_option() -> None:
    root: Any = typer.main.get_command(cli.make_app())
    run = root.commands["sweep"].commands["run"]
    run_options = {o for p in run.params for o in p.opts}
    for flag in (
        "--start",
        "--end",
        "--spend-holdout",
        "--holdout-repeat",
        "--override-gap",
        "--synthetic",
        "--store",
        "--store-path",
    ):
        assert flag not in run_options
    for name in ("sweep", "lab"):
        group = root.commands[name]
        assert not group.params
        commands = list(_commands(group))
        assert commands
        for command in commands:
            for param in command.params:
                for opt in param.opts:
                    if opt.startswith("-"):
                        assert not FORBIDDEN_OPTION.search(opt), (command.name, opt)
    # and no option anywhere in the app names a store path
    for command in _commands(root):
        for param in command.params:
            assert not any(re.search(r"store", o) for o in param.opts), param.opts
