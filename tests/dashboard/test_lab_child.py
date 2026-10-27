"""The Lab's child process (plan T290, #2320): argv, spawn and log, never waited on."""

from __future__ import annotations

import ast
import os
import stat
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest
from pydantic import SecretStr

from tradepartner.config import Settings
from tradepartner.dashboard import book_actions, lab_child
from tradepartner.dashboard.lab_child import LabKind, LabRefused
from tradepartner.store import lab_registry, registry

DESK_ONLY = [
    "--spend-holdout",
    "--holdout-reason",
    "--holdout-repeat",
    "--override-gap",
    "--gap-reason",
    "--rerun",
    "--measured-cost-bps",
]
SENTINEL = "sk-SENTINEL-0123456789"


class _FakeChild:
    """A child that never exits: any wait or poll fails the test."""

    pid = 4242

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"spawn must not touch the child ({name})")


class _Popen:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, argv: list[str], **kwargs: Any) -> _FakeChild:
        self.calls.append((argv, kwargs))
        return _FakeChild()


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "data" / "live.duckdb").write_bytes(b"")
    (root / ".env").write_text("STORE__PATH=data/live.duckdb\n")
    entry = tmp_path / "tradepartner"
    entry.write_text("")
    monkeypatch.setattr(book_actions, "PROJECT_ROOT", root)
    monkeypatch.setattr(lab_child, "_entry_point", lambda: entry)
    monkeypatch.delenv("STORE__PATH", raising=False)
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "x.env"))
    return Settings(_env_file=None, store={"path": str(root / "data" / "live.duckdb")})


@pytest.fixture
def popen(monkeypatch: pytest.MonkeyPatch) -> _Popen:
    fake = _Popen()
    monkeypatch.setattr(subprocess, "Popen", fake)
    return fake


@pytest.fixture
def conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(":memory:")


@pytest.fixture(autouse=True)
def known(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lab_registry, "sweep_by_slug", lambda c, slug: object())
    monkeypatch.setattr(registry, "get_hypothesis", lambda c, slug: object())


def _flags(argv: list[str]) -> list[str]:
    return argv[: argv.index("--")]


def test_sweep_run_argv_is_exact(live: Settings) -> None:
    argv = lab_child.sweep_run_argv("b10", "go", live)
    assert argv[0] == sys.executable
    assert argv[2:] == ["sweep", "run", "--note=go", "--", "b10"]
    assert lab_child.sweep_run_argv("b10", None, live)[2:] == ["sweep", "run", "--", "b10"]


def test_backtest_argv_is_exact(live: Settings) -> None:
    argv = lab_child.backtest_argv("h1", date(2020, 1, 2), date(2021, 3, 4), "n", live)
    assert argv[2:] == [
        "backtest",
        "--start=2020-01-02",
        "--end=2021-03-04",
        "--note=n",
        "--",
        "h1",
    ]


def test_register_argv_is_exact_and_absolute(live: Settings, tmp_path: Path) -> None:
    path = tmp_path / "docs" / "sweeps" / "b10.md"
    assert lab_child.register_argv(path)[2:] == ["sweep", "register", "--", str(path)]
    with pytest.raises(LabRefused):
        lab_child.register_argv(Path("docs/sweeps/b10.md"))


@pytest.mark.parametrize("flag", DESK_ONLY)
def test_no_desk_only_flag_in_any_argv(live: Settings, tmp_path: Path, flag: str) -> None:
    argvs = [
        lab_child.sweep_run_argv("b10", "n", live),
        lab_child.backtest_argv("h1", date(2020, 1, 1), date(2020, 2, 1), "n", live),
        lab_child.register_argv(tmp_path / "s.md"),
    ]
    for argv in argvs:
        assert not any(a.split("=")[0] == flag for a in argv)
    # an option-looking slug or note cannot smuggle one in: the grammar refuses it
    with pytest.raises(LabRefused):
        lab_child.sweep_run_argv(flag, None, live)
    assert "--note=" + flag in lab_child.sweep_run_argv("b10", flag, live)  # one argv item


@pytest.mark.parametrize("slug", ["", "B10", "-x", "a b", "a/b", "../x", "a\n", "--rerun"])
def test_bad_slug_refused_with_nothing_started(
    live: Settings, popen: _Popen, conn: duckdb.DuckDBPyConnection, slug: str
) -> None:
    with pytest.raises(LabRefused):
        lab_child.spawn(live, conn, LabKind.SWEEP_RUN, slug)
    assert popen.calls == []
    assert not lab_child.log_path(live).exists()


@pytest.mark.parametrize(
    "note", ["", "   ", "x" * 501, "a\x00b", "a\nb", "a\x1bb", "a\tb", "a\u202eb", "a\u200bb"]
)
def test_bad_note_refused_with_nothing_started(
    live: Settings, popen: _Popen, conn: duckdb.DuckDBPyConnection, note: str
) -> None:
    with pytest.raises(LabRefused):
        lab_child.spawn(live, conn, LabKind.SWEEP_RUN, "b10", note=note)
    assert popen.calls == []
    assert not lab_child.log_path(live).exists()


def test_unregistered_slug_refused(
    live: Settings,
    popen: _Popen,
    conn: duckdb.DuckDBPyConnection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lab_registry, "sweep_by_slug", lambda c, slug: None)
    with pytest.raises(LabRefused, match="not registered"):
        lab_child.spawn(live, conn, LabKind.SWEEP_RUN, "b10")

    def unknown(c: Any, slug: str) -> None:
        raise registry.UnknownHypothesis("no")

    monkeypatch.setattr(registry, "get_hypothesis", unknown)
    with pytest.raises(LabRefused, match="not registered"):
        lab_child.spawn(
            live, conn, LabKind.BACKTEST, "h1", start=date(2020, 1, 1), end=date(2020, 2, 1)
        )
    assert popen.calls == []


def test_non_live_store_and_manifest_refused(
    live: Settings, popen: _Popen, conn: duckdb.DuckDBPyConnection, tmp_path: Path
) -> None:
    copy = tmp_path / "copy.duckdb"
    copy.write_bytes(b"")
    page = Settings(_env_file=None, store={"path": str(copy)})
    with pytest.raises(LabRefused, match="not the live store"):
        lab_child.spawn(page, conn, LabKind.SWEEP_RUN, "b10")
    store = Path(live.store.path)
    store.with_name(f"{store.name}.manifest.json").write_text("{}")
    with pytest.raises(LabRefused, match="backup manifest"):
        lab_child.spawn(live, conn, LabKind.SWEEP_RUN, "b10")
    assert popen.calls == []


def test_popen_call_shape(live: Settings, popen: _Popen, conn: duckdb.DuckDBPyConnection) -> None:
    spawned = lab_child.spawn(live, conn, LabKind.SWEEP_RUN, "b10", note="n")
    (argv, kw) = popen.calls[0]
    assert argv == list(spawned.argv)
    assert kw["shell"] is False
    assert kw["start_new_session"] is True
    assert kw["stdin"] == subprocess.DEVNULL
    assert kw["stdout"] == kw["stderr"] and isinstance(kw["stdout"], int)
    assert subprocess.PIPE not in (kw["stdout"], kw["stderr"], kw["stdin"])
    assert kw["env"]["STORE__PATH"] == str(Path(live.store.path).resolve())
    assert "TRADEPARTNER_ENV_FILE" not in kw["env"]
    assert kw["cwd"] == book_actions.PROJECT_ROOT
    assert spawned.pid == 4242 and spawned.log_path == lab_child.log_path(live)
    assert spawned.log_path.name == "live.duckdb.lab-run.log"


def test_spawn_returns_at_once_without_touching_a_child_that_never_exits(
    live: Settings, popen: _Popen, conn: duckdb.DuckDBPyConnection
) -> None:
    spawned = lab_child.spawn(
        live, conn, LabKind.BACKTEST, "h1", start=date(2020, 1, 1), end=date(2020, 2, 1)
    )
    assert isinstance(spawned.child, _FakeChild)  # no wait/poll/communicate was called


def test_log_is_truncated_and_0600_over_a_wider_file(live: Settings) -> None:
    path = lab_child.log_path(live)
    path.write_text("old text")
    path.chmod(0o666)
    fd = lab_child.open_log(path)
    os.close(fd)
    assert path.read_bytes() == b""
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_log_symlink_refused(live: Settings, tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("keep")
    path = lab_child.log_path(live)
    path.symlink_to(target)
    with pytest.raises(LabRefused):
        lab_child.open_log(path)
    assert target.read_text() == "keep"


def test_log_fifo_refused(live: Settings) -> None:
    path = lab_child.log_path(live)
    os.mkfifo(path)
    with pytest.raises(LabRefused):
        lab_child.open_log(path)


def test_died_child_secret_never_in_tail(
    live: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = book_actions.PROJECT_ROOT
    (root / ".env").write_text(f"STORE__PATH=data/live.duckdb\nALPACA_API_KEY={SENTINEL}\n")
    page = Settings(
        _env_file=None,
        store={"path": live.store.path},
        alpaca_api_secret=SecretStr("PAGE-SECRET-98765"),
    )
    path = lab_child.log_path(page)
    fd = lab_child.open_log(path)
    code = (
        "raise ValueError('settings invalid: input_value=' + "
        f"{SENTINEL!r} + ' and ' + 'PAGE-SECRET-98765')"
    )
    subprocess.run([sys.executable, "-c", code], stdout=fd, stderr=fd, check=False)
    os.close(fd)
    assert SENTINEL in path.read_text()  # the raw log does hold it
    tail = lab_child.scrubbed_tail(path, page)
    assert "ValueError" in tail
    assert SENTINEL not in tail and "PAGE-SECRET-98765" not in tail
    assert "[redacted]" in tail


def test_secret_cut_by_the_window_start_is_not_shown(live: Settings) -> None:
    root = book_actions.PROJECT_ROOT
    (root / ".env").write_text(f"STORE__PATH=data/live.duckdb\nALPACA_API_KEY={SENTINEL}\n")
    path = lab_child.log_path(live)
    path.write_text("x" * 100 + SENTINEL + "tail-end")
    tail = lab_child.scrubbed_tail(path, live, max_bytes=len("tail-end") + 6)
    assert SENTINEL[-6:] not in tail


def test_tail_hidden_when_live_config_unreadable(
    live: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = lab_child.log_path(live)
    path.write_text(SENTINEL)

    def boom() -> Settings:
        raise ValueError("bad")

    monkeypatch.setattr(book_actions, "live_settings", boom)
    assert SENTINEL not in lab_child.scrubbed_tail(path, live)


def test_book_actions_imports_no_lab_module() -> None:
    tree = ast.parse(Path(book_actions.__file__).read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names += [node.module or ""] + [a.name for a in node.names]
    assert not [n for n in names if "lab" in n.lower()]


def _no_fragment(secret: str, tail: str) -> None:
    for i in range(len(secret) - 3):
        assert secret[i : i + 4] not in tail


def test_cut_secret_also_present_whole_in_the_window(live: Settings) -> None:
    root = book_actions.PROJECT_ROOT
    (root / ".env").write_text(f"STORE__PATH=data/live.duckdb\nALPACA_API_KEY={SENTINEL}\n")
    path = lab_child.log_path(live)
    # the window starts inside the first copy; a whole copy follows and shrinks on redaction
    path.write_text("A" * 50 + SENTINEL + " mid " + SENTINEL + " end")
    max_bytes = len(" mid ") + len(SENTINEL) + len(" end") + 8
    tail = lab_child.scrubbed_tail(path, live, max_bytes=max_bytes)
    _no_fragment(SENTINEL, tail)
    assert "[redacted]" in tail


def test_cut_secret_followed_by_a_multibyte_tail(live: Settings) -> None:
    root = book_actions.PROJECT_ROOT
    (root / ".env").write_text(f"STORE__PATH=data/live.duckdb\nALPACA_API_KEY={SENTINEL}\n")
    path = lab_child.log_path(live)
    path.write_bytes(("B" * 50 + SENTINEL + "\u2014" * 12).encode())
    tail = lab_child.scrubbed_tail(path, live, max_bytes=36)
    _no_fragment(SENTINEL, tail)


def test_log_hard_link_refused_and_not_truncated(live: Settings, tmp_path: Path) -> None:
    other = tmp_path / "other.txt"
    other.write_text("keep me")
    other.chmod(0o644)
    path = lab_child.log_path(live)
    os.link(other, path)
    with pytest.raises(LabRefused):
        lab_child.open_log(path)
    assert other.read_text() == "keep me"
    assert stat.S_IMODE(other.stat().st_mode) == 0o644
