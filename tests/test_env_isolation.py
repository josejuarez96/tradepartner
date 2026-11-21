"""The test process never loads the real `.env` (#3623).

`tests/conftest.py` points `TRADEPARTNER_ENV_FILE`, the supported override
`config._default_env_file` reads on every `Settings()` construction, at a path
that does not exist. These tests pin that guard. None of them touches a real
`.env`: every stand-in file is written under `tmp_path`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import NO_ENV_FILE

from tradepartner import config
from tradepartner.config import Settings, get_settings

_PLANTED = "/planted/leak.duckdb"
_TESTS_DIR = Path(__file__).resolve().parent

# Test files allowed to remove the override, as paths relative to `tests/`.
# Empty: `monkeypatch.setenv` to a file the test wrote itself is the way to
# read an env file in a test.
_MAY_REMOVE_THE_OVERRIDE: frozenset[str] = frozenset()

# Built from two halves so this file does not match its own scan.
_VARIABLE = "TRADEPARTNER_" + "ENV_FILE"
_REMOVALS = (f'delenv("{_VARIABLE}"', f'os.environ.pop("{_VARIABLE}"')


def _plant_default_env_file(root: Path) -> Path:
    """Write a stand-in project root whose `.env` names a planted store path;
    return the `config.py` path that makes it the default location."""
    (root / "src" / "tradepartner").mkdir(parents=True)
    (root / ".env").write_text(f"STORE__PATH={_PLANTED}\n")
    return root / "src" / "tradepartner" / "config.py"


def test_a_bare_settings_does_not_read_the_default_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STORE__PATH", raising=False)
    # the default is Path(config.__file__).resolve().parents[2] / ".env"
    monkeypatch.setattr(config, "__file__", str(_plant_default_env_file(tmp_path)))
    assert Settings().store.path != _PLANTED
    assert get_settings().store.path != _PLANTED


def test_the_override_points_at_a_missing_file() -> None:
    assert os.environ[_VARIABLE] == str(NO_ENV_FILE)
    assert not NO_ENV_FILE.exists()
    assert config._default_env_file() == NO_ENV_FILE


def test_a_child_process_inherits_the_override() -> None:
    child = subprocess.run(
        [sys.executable, "-c", f"import os; print(os.environ['{_VARIABLE}'])"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stdout.strip() == str(NO_ENV_FILE)


def test_a_child_settings_does_not_read_a_planted_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("STORE__PATH", raising=False)
    stand_in = _plant_default_env_file(tmp_path)
    code = (
        "import sys\n"
        "import tradepartner.config as config\n"
        "config.__file__ = sys.argv[1]\n"
        "print(config.Settings().store.path)\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", code, str(stand_in)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stdout.strip()
    assert child.stdout.strip() != _PLANTED


def test_an_explicit_override_still_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / "x.env"
    env_file.write_text("STORE__PATH=/explicit.duckdb\n")
    monkeypatch.delenv("STORE__PATH", raising=False)
    monkeypatch.setenv(_VARIABLE, str(env_file))
    assert Settings().store.path == "/explicit.duckdb"


def test_no_test_removes_the_override() -> None:
    removers = {
        path.relative_to(_TESTS_DIR).as_posix()
        for path in _TESTS_DIR.rglob("*.py")
        if any(removal in path.read_text(encoding="utf-8") for removal in _REMOVALS)
    }
    assert removers == _MAY_REMOVE_THE_OVERRIDE
