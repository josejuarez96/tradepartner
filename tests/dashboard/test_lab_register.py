"""A Lab registration uses the desk command and never touches a live Run log."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest
from backtest.test_sweep_register import SWEEP_SOURCE, _copy, _counts, _twin
from conftest import load_universe_fixtures
from pydantic import SecretStr

from tradepartner.config import Settings
from tradepartner.dashboard import lab_child, lab_files, lab_register
from tradepartner.dashboard.lab_files import FileKind, GitState
from tradepartner.store import lab_schema, schema


@pytest.fixture
def ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Settings, Path, Path]:
    docs = tmp_path / "docs"
    folder = docs / "sweeps"
    folder.mkdir(parents=True)
    file = folder / "safe.md"
    file.write_text("sweep fixture")
    store = tmp_path / "store.duckdb"
    store.touch()
    settings = Settings(_env_file=None, store={"path": str(store)})
    monkeypatch.setattr(lab_child.book_actions, "live_settings", lambda: settings)
    monkeypatch.setattr(lab_files, "git_state", lambda *a, **k: GitState.MERGED)
    monkeypatch.setattr(lab_child, "_entry_point", lambda: tmp_path / "tradepartner")
    (tmp_path / "tradepartner").touch()
    return settings, docs, store


class FakeChild:
    def __init__(self, code: int = 0, output: str = "created") -> None:
        self.returncode = code
        self.output = output
        self.calls: list[float | None] = []

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.calls.append(timeout)
        return self.output, ""


def test_exact_argv_own_pipes_and_live_log_untouched(
    ready: tuple[Settings, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, docs, store = ready
    log = Path(f"{store}.lab-run.log")
    log.write_text("running sweep output")
    child = FakeChild()
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def popen(argv: list[str], **kwargs: Any) -> FakeChild:
        calls.append((argv, kwargs))
        return child

    monkeypatch.setattr(subprocess, "Popen", popen)
    result = lab_register.register(settings, docs, FileKind.SWEEP, "safe.md")
    argv, opts = calls[0]
    assert argv == [
        sys.executable,
        str(docs.parent / "tradepartner"),
        "sweep",
        "register",
        "--",
        str(docs / "sweeps" / "safe.md"),
    ]
    assert opts["stdout"] == subprocess.PIPE and opts["stderr"] == subprocess.PIPE
    assert opts["stdin"] == subprocess.DEVNULL and opts["shell"] is False
    assert opts["cwd"] == lab_child.book_actions.PROJECT_ROOT
    assert opts["env"]["STORE__PATH"] == str(store)
    assert "TRADEPARTNER_ENV_FILE" not in opts["env"]
    assert child.calls == [settings.store.lock_retry_seconds + settings.ui.kill_wait_margin_seconds]
    assert result.status == "created"
    assert log.read_text() == "running sweep output"


@pytest.mark.parametrize(
    "kind,name",
    [(FileKind.HYPOTHESIS, "safe.md"), (FileKind.SWEEP, "../safe.md"), (FileKind.SWEEP, "Bad.md")],
)
def test_refused_inputs_start_nothing(
    ready: tuple[Settings, Path, Path], monkeypatch: pytest.MonkeyPatch, kind: FileKind, name: str
) -> None:
    settings, docs, _ = ready
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("started"))
    with pytest.raises(lab_register.RegisterRefused):
        lab_register.register(settings, docs, kind, name)


def test_rechecks_merged_and_live_at_click(
    ready: tuple[Settings, Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings, docs, _ = ready
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("started"))
    monkeypatch.setattr(lab_files, "git_state", lambda *a, **k: GitState.NOT_MERGED)
    with pytest.raises(lab_register.RegisterRefused, match="not merged"):
        lab_register.register(settings, docs, FileKind.SWEEP, "safe.md")
    monkeypatch.setattr(lab_files, "git_state", lambda *a, **k: GitState.MERGED)
    other = Settings(_env_file=None, store={"path": str(tmp_path / "other.duckdb")})
    with pytest.raises(lab_register.RegisterRefused, match="not the live store"):
        lab_register.register(other, docs, FileKind.SWEEP, "safe.md")


@pytest.mark.parametrize(
    "code,output,status",
    [
        (0, "sweep safe (registration 1)", "created"),
        (0, "unchanged, nothing written", "unchanged"),
        (2, "family refused", "refused"),
        (1, "store busy: wait", "store_busy"),
        (1, "boom", "failed"),
    ],
)
def test_command_outcome_and_scrub(
    ready: tuple[Settings, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    code: int,
    output: str,
    status: str,
) -> None:
    settings, docs, _ = ready
    child = FakeChild(code, output)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: child)
    result = lab_register.register(settings, docs, FileKind.SWEEP, "safe.md")
    assert result.status == status and result.output == output


def test_timeout_never_retries_or_confirms(
    ready: tuple[Settings, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, docs, _ = ready
    calls = 0

    class Slow(FakeChild):
        def communicate(self, timeout: float | None = None) -> tuple[str, str]:
            if timeout is not None:
                raise subprocess.TimeoutExpired("register", timeout)
            return "late", ""

    def popen(*a: Any, **k: Any) -> Slow:
        nonlocal calls
        calls += 1
        return Slow()

    monkeypatch.setattr(subprocess, "Popen", popen)
    result = lab_register.register(settings, docs, FileKind.SWEEP, "safe.md")
    assert result.status == "not_confirmed" and calls == 1


def test_secret_from_child_never_returned(
    ready: tuple[Settings, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, docs, _ = ready
    settings = Settings(
        _env_file=None,
        store={"path": settings.store.path},
        alpaca_api_secret=SecretStr("sentinel-secret-123"),
    )
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: FakeChild(1, "trace sentinel-secret-123")
    )
    result = lab_register.register(settings, docs, FileKind.SWEEP, "safe.md")
    assert "sentinel-secret-123" not in result.output


def test_real_cli_register_writes_the_engine_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "fixture.duckdb"
    settings = Settings(_env_file=None, store={"path": str(store)})
    conn = duckdb.connect(str(store))
    schema.init_schema(conn)
    load_universe_fixtures(conn, Path(__file__).resolve().parents[1] / "fixtures" / "universe")
    lab_schema.apply_lab_schema(conn)
    _twin(conn, settings, tmp_path)
    before = _counts(conn)
    before_trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
    conn.close()
    docs = tmp_path / "docs"
    (docs / "sweeps").mkdir(parents=True)
    (docs / "sweeps" / "fixture-sweep.md").write_text(_copy(tmp_path, SWEEP_SOURCE).read_text())
    monkeypatch.setattr(lab_files, "git_state", lambda *a, **k: GitState.MERGED)
    monkeypatch.setattr(lab_child.book_actions, "live_settings", lambda: settings)
    # Point this fixture child at an empty test config, never the owner's .env.
    empty_config = tmp_path / "empty-config"
    empty_config.touch()
    monkeypatch.setattr(
        lab_child,
        "_child_env",
        lambda s: {
            **os.environ,
            "STORE__PATH": str(store),
            "TRADEPARTNER_ENV_FILE": str(empty_config),
        },
    )
    result = lab_register.register(settings, docs, FileKind.SWEEP, "fixture-sweep.md")
    assert result.status == "created", result.output
    conn = duckdb.connect(str(store), read_only=True)
    after = _counts(conn)
    assert after["sweeps"] == before["sweeps"] + 1
    assert after["sweep_variants"] == before["sweep_variants"] + 4
    assert after["hypotheses"] == before["hypotheses"] + 4
    assert after["hypothesis_fingerprints"] == before["hypothesis_fingerprints"] + 4
    assert after["family_rules"] == before["family_rules"]
    assert conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0] == before_trials
    conn.close()
    again = lab_register.register(settings, docs, FileKind.SWEEP, "fixture-sweep.md")
    assert again.status == "unchanged"
    assert "unchanged, nothing written" in again.output
    conn = duckdb.connect(str(store), read_only=True)
    assert _counts(conn) == after
    conn.close()
