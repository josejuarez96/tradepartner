"""`cli.main` sets a private umask (#3652): files and directories created by the
console script, and by every child it starts, are owner-only. `make_app` and the
import leave the umask alone, so only `main` is called here.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tradepartner import cli
from tradepartner.config import Settings
from tradepartner.store import db, schema

#: The usual macOS and Linux default, set for the test so the result does not
#: depend on the umask of the shell that started pytest.
USUAL_UMASK = 0o022


@pytest.fixture(autouse=True)
def _restored_umask() -> Iterator[None]:
    """The umask is process-wide: start each test at 022 and put the old one back."""
    old = os.umask(USUAL_UMASK)
    try:
        yield
    finally:
        os.umask(old)


def _main_running(monkeypatch: pytest.MonkeyPatch, body: Callable[[], None]) -> None:
    """Run `cli.main()` with `body` in place of the Typer app."""
    monkeypatch.setattr(cli, "make_app", lambda: body)
    cli.main()


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_main_makes_new_store_files_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = tmp_path / "data" / "store.duckdb"
    settings = Settings(_env_file=None, store={"path": str(store), "lock_retry_seconds": 0})

    def write_store() -> None:
        with db.open_for_write(settings) as conn:
            schema.init_schema(conn)

    _main_running(monkeypatch, write_store)
    assert _mode(store) == 0o600
    assert _mode(store.parent) == 0o700


def test_main_does_not_change_an_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / "old.txt"
    old.write_text("made before main ran")
    old.chmod(0o644)
    _main_running(monkeypatch, lambda: None)
    assert _mode(old) == 0o644


def test_a_child_started_after_main_inherits_the_umask(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def start_child() -> None:
        child = subprocess.run(
            [sys.executable, "-c", "import os; m = os.umask(0); print(oct(m))"],
            capture_output=True,
            text=True,
            check=True,
        )
        seen.append(child.stdout.strip())

    _main_running(monkeypatch, start_child)
    assert seen == ["0o77"]


def test_importing_the_cli_and_building_an_app_leave_the_umask_alone() -> None:
    cli.make_app()
    current = os.umask(USUAL_UMASK)
    assert current == USUAL_UMASK
