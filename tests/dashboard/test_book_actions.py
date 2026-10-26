"""The Book page's one write, "Stop this book now" (plan T277, #2268; ADR 0018 section 2
and its 2026-10-26 amendment): `paper kill` as a subprocess, judged by its exit code
alone, against the live store only, waited on for a bound and never retried."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from tradepartner.config import Settings
from tradepartner.dashboard import book_actions
from tradepartner.dashboard.book_actions import KillRefused, KillStatus
from tradepartner.store import schema
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import PaperWindowRow, append

_NOW = datetime(2026, 10, 2, 22, 0, tzinfo=UTC)
_REASON = "drawdown looks wrong, stopping to check"


def _store(path: Path) -> Path:
    settings = Settings(_env_file=None, store={"path": str(path)})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        append(
            conn,
            PaperWindowRow(
                hypothesis_id=1,
                first_rebalance_session=date(2026, 9, 30),
                account_id="PA0",
                starting_cash=100_000.0,
                starting_equity=100_000.0,
                code_version="test",
                started_at=_NOW,
                frozen_json="{}",
                frozen_sha256="0" * 64,
                book_id="main",
                known_at=_NOW,
                ingested_at=_NOW,
            ),
        )
    return path


class _Fake:
    """A stand-in for the `paper kill` child: records how it was started, answers
    `communicate` with `code` and the text, or times out when `code` is None."""

    def __init__(
        self,
        code: int | None,
        stdout: str = "",
        stderr: str = "",
        check: Callable[[], None] | None = None,
    ) -> None:
        self.code, self.stdout, self.stderr, self.check = code, stdout, stderr, check
        self.calls: list[dict[str, Any]] = []
        self.timeouts: list[float | None] = []
        self.terminated = False

    def __call__(self, argv: list[str], *, env: dict[str, str], cwd: Path) -> _Fake:
        if self.check is not None:
            self.check()
        self.calls.append({"argv": argv, "env": env, "cwd": cwd})
        return self

    @property
    def returncode(self) -> int | None:
        return self.code

    def communicate(self, timeout: float | None = None) -> tuple[str, str]:
        self.timeouts.append(timeout)
        if self.code is None and timeout is not None:
            raise subprocess.TimeoutExpired("paper kill", timeout)
        return self.stdout, self.stderr

    def terminate(self) -> None:
        self.terminated = True

    kill = terminate


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """A project root whose `.env` names the store by a relative path, as the owner's
    does, and page settings that read that same file."""
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    _store(root / "data" / "live.duckdb")
    (root / ".env").write_text("STORE__PATH=data/live.duckdb\n")
    monkeypatch.setattr(book_actions, "PROJECT_ROOT", root)
    monkeypatch.delenv("STORE__PATH", raising=False)
    monkeypatch.delenv("TRADEPARTNER_ENV_FILE", raising=False)
    return Settings(_env_file=None, store={"path": str(root / "data" / "live.duckdb")})


def _fake(monkeypatch: pytest.MonkeyPatch, fake: _Fake) -> _Fake:
    monkeypatch.setattr(book_actions, "_start", fake)
    return fake


def test_the_live_store_ignores_the_page_s_store_path_and_env_file(
    monkeypatch: pytest.MonkeyPatch, live: Settings, tmp_path: Path
) -> None:
    other = tmp_path / "other.env"
    other.write_text("STORE__PATH=/somewhere/else.duckdb\n")
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "copy.duckdb"))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(other))
    assert book_actions.live_store_path() == book_actions.PROJECT_ROOT / "data" / "live.duckdb"
    assert os.environ["STORE__PATH"] == str(tmp_path / "copy.duckdb")  # put back untouched


def test_the_live_store_passes(live: Settings) -> None:
    assert book_actions.store_refusal(live) is None


def test_a_copy_named_by_the_page_s_env_refuses(live: Settings, tmp_path: Path) -> None:
    copy = tmp_path / "copy.duckdb"
    shutil.copy(live.store.path, copy)
    page = Settings(_env_file=None, store={"path": str(copy)})
    refusal = book_actions.store_refusal(page)
    assert refusal is not None and "not the live store" in refusal
    assert "tradepartner paper kill" in refusal


def test_the_live_file_with_a_backup_manifest_beside_it_refuses(live: Settings) -> None:
    store = Path(live.store.path)
    store.with_name(f"{store.name}.manifest.json").write_text("{}")
    refusal = book_actions.store_refusal(live)
    assert refusal is not None and "backup" in refusal


def test_an_outside_or_missing_path_refuses(live: Settings, tmp_path: Path) -> None:
    for path in (tmp_path / "nowhere.duckdb", Path("/dev/null")):
        page = Settings(_env_file=None, store={"path": str(path)})
        assert book_actions.store_refusal(page) is not None


@pytest.mark.parametrize("reason", [None, "", "   \n\t"])
def test_an_empty_or_blank_reason_is_refused_before_anything_runs(
    monkeypatch: pytest.MonkeyPatch, live: Settings, reason: str | None
) -> None:
    fake = _fake(monkeypatch, _Fake(0))
    assert book_actions.reason_refusal(reason, live) is not None
    with pytest.raises(KillRefused):
        book_actions.kill(live, "main", reason)  # type: ignore[arg-type]
    assert fake.calls == []


def test_a_reason_over_the_cap_is_refused_and_one_at_it_runs(
    monkeypatch: pytest.MonkeyPatch, live: Settings
) -> None:
    fake = _fake(monkeypatch, _Fake(0))
    cap = live.ui.max_reason_chars
    with pytest.raises(KillRefused, match=str(cap)):
        book_actions.kill(live, "main", "x" * (cap + 1))
    assert fake.calls == []
    assert book_actions.kill(live, "main", "x" * cap).status is KillStatus.ENGAGED


@pytest.mark.parametrize("book", ["", "ma in", "main;rm", "--all", "../main"])
def test_a_book_outside_the_token_grammar_is_refused(
    monkeypatch: pytest.MonkeyPatch, live: Settings, book: str
) -> None:
    fake = _fake(monkeypatch, _Fake(0))
    with pytest.raises(KillRefused):
        book_actions.kill(live, book, _REASON)
    assert fake.calls == []


def test_a_store_that_is_not_live_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, live: Settings, tmp_path: Path
) -> None:
    fake = _fake(monkeypatch, _Fake(0))
    copy = tmp_path / "copy.duckdb"
    shutil.copy(live.store.path, copy)
    with pytest.raises(KillRefused):
        book_actions.kill(Settings(_env_file=None, store={"path": str(copy)}), "main", _REASON)
    assert fake.calls == []


def test_the_call_is_the_desk_command_in_list_form_on_the_same_store(
    monkeypatch: pytest.MonkeyPatch, live: Settings
) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", "/elsewhere/.env")
    fake = _fake(monkeypatch, _Fake(0))
    book_actions.kill(live, "main", _REASON)
    [call] = fake.calls
    assert call["argv"] == [
        sys.executable,
        "-m",
        "tradepartner.cli",
        "paper",
        "kill",
        "--book=main",
        f"--reason={_REASON}",
    ]
    assert call["env"]["STORE__PATH"] == str(Path(live.store.path).resolve())
    assert "TRADEPARTNER_ENV_FILE" not in call["env"]
    assert call["cwd"] == book_actions.PROJECT_ROOT
    assert fake.timeouts == [330]


def test_start_uses_no_shell() -> None:
    tree = ast.parse(Path(book_actions.__file__).read_text())
    popens = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "Popen"
    ]
    assert len(popens) == 1
    shell = [k for k in popens[0].keywords if k.arg == "shell"]
    assert [ast.literal_eval(k.value) for k in shell] == [False]


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (0, KillStatus.ENGAGED),
        (2, KillStatus.NOT_ENGAGED),
        (3, KillStatus.NOT_ENGAGED),
        (4, KillStatus.NOT_ENGAGED),
        (5, KillStatus.NOT_ENGAGED),
        (6, KillStatus.NOT_ENGAGED),
        (77, KillStatus.NOT_ENGAGED),
        (-9, KillStatus.NOT_ENGAGED),
    ],
)
def test_the_result_is_the_exit_code_alone(
    monkeypatch: pytest.MonkeyPatch, live: Settings, code: int, status: KillStatus
) -> None:
    # The text says "engaged" whatever the code: it is never read for the outcome.
    _fake(monkeypatch, _Fake(code, stdout="paper kill: engaged (kill_switch event 4)"))
    result = book_actions.kill(live, "main", _REASON)
    assert (result.status, result.exit_code) == (status, code)
    assert "paper kill: engaged" in result.output


def test_exit_0_after_the_waiting_line_is_engaged(
    monkeypatch: pytest.MonkeyPatch, live: Settings
) -> None:
    waiting = (
        "paper kill: kill switch NOT engaged yet: store busy (held by duckdb, pid 1), "
        "waiting up to 300 s"
    )
    _fake(monkeypatch, _Fake(0, stdout="paper kill: engaged (kill_switch event 9)", stderr=waiting))
    result = book_actions.kill(live, "main", _REASON)
    assert result.status is KillStatus.ENGAGED
    assert "NOT engaged yet" in result.output


def test_the_output_is_scrubbed(monkeypatch: pytest.MonkeyPatch, live: Settings) -> None:
    secret = "PKSECRETVALUE123456"
    settings = live.model_copy(update={"alpaca_paper_api_key": SecretStr(secret)})
    _fake(monkeypatch, _Fake(3, stderr=f"failed with {secret}\x1b[31m"))
    result = book_actions.kill(settings, "main", _REASON)
    assert secret not in result.output and "\x1b" not in result.output
    assert "[31m" not in result.output
    assert "[redacted]" in result.output


def test_past_the_bound_it_is_not_confirmed_never_terminated_never_retried(
    monkeypatch: pytest.MonkeyPatch, live: Settings
) -> None:
    fake = _fake(monkeypatch, _Fake(None))
    result = book_actions.kill(live, "main", _REASON)
    assert result.status is KillStatus.NOT_CONFIRMED and result.exit_code is None
    assert len(fake.calls) == 1 and not fake.terminated


def test_the_wait_comes_from_the_live_config_the_child_reads(
    monkeypatch: pytest.MonkeyPatch, live: Settings
) -> None:
    (book_actions.PROJECT_ROOT / ".env").write_text(
        "STORE__PATH=data/live.duckdb\nUI__KILL_WAIT_SECONDS=999\n"
    )
    fake = _fake(monkeypatch, _Fake(0))
    book_actions.kill(live, "main", _REASON)
    assert fake.timeouts == [999]


def test_a_page_with_a_shorter_writer_wait_still_waits_the_child_s_bound(
    monkeypatch: pytest.MonkeyPatch, live: Settings
) -> None:
    """The page launched with its own `.env` (same live store, smaller writer wait):
    the bound follows the child's config, not the page's."""
    page = Settings(
        _env_file=None,
        store={"path": live.store.path, "writer_lock_retry_seconds": 5},
    )
    fake = _fake(monkeypatch, _Fake(0))
    book_actions.kill(page, "main", _REASON)
    assert fake.timeouts == [330]


def test_an_unloadable_live_config_refuses_in_words(
    monkeypatch: pytest.MonkeyPatch, live: Settings
) -> None:
    (book_actions.PROJECT_ROOT / ".env").write_text("UI__KILL_WAIT_SECONDS=1\n")
    fake = _fake(monkeypatch, _Fake(0))
    refusal = book_actions.store_refusal(live)
    assert refusal is not None and "could not be read" in refusal
    with pytest.raises(KillRefused, match="could not be read"):
        book_actions.kill(live, "main", _REASON)
    assert fake.calls == []


def test_a_store_section_variable_does_not_reach_the_child(
    monkeypatch: pytest.MonkeyPatch, live: Settings
) -> None:
    monkeypatch.setenv("STORE", '{"path": "/elsewhere.duckdb"}')
    fake = _fake(monkeypatch, _Fake(0))
    book_actions.kill(live, "main", _REASON)
    assert "STORE" not in fake.calls[0]["env"]


def test_paper_kill_runs_for_real_against_a_temp_store(
    monkeypatch: pytest.MonkeyPatch, live: Settings
) -> None:
    """A kill needs no broker: the real command, as a child, engages the switch."""
    result = book_actions.kill(live, "main", _REASON)
    assert result.status is KillStatus.ENGAGED, result.output
    with open_read_only(live) as conn:
        rows = conn.execute("SELECT state, reason FROM kill_switch").fetchall()
    assert len(rows) == 1 and rows[0][0] == "engaged" and _REASON in rows[0][1]
