"""The lint ratchet (T437, #3632): blind excepts, prints in ``src`` and function size.

``pyproject.toml`` selects six ruff rules at limits measured at about the 99th percentile of
``src/``. Every offender that existed when they were switched on carries a per-line
``# noqa``; ``RUF100`` (already selected) fails lint when one stops being needed, so the
list can only shrink. These tests pin the selection and prove a new offender fails.
"""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"

RATCHET_RULES = frozenset({"BLE001", "T20", "C901", "PLR0912", "PLR0913", "PLR0915"})

PROBE = '''\
"""A probe module with one new offender per ratchet rule family."""


def swallow() -> None:
    """Catch everything and print."""
    try:
        pass
    except Exception:
        pass
    print("x")


def wide(
    *, a: int, b: int, c: int, d: int, e: int, f: int, g: int, h: int, i: int, j: int,
    k: int, m: int, n: int,
) -> None:
    """Take thirteen keyword arguments."""
'''


def _ruff_lint_config() -> dict[str, Any]:
    with PYPROJECT.open("rb") as handle:
        config: dict[str, Any] = tomllib.load(handle)
    lint: dict[str, Any] = config["tool"]["ruff"]["lint"]
    return lint


def test_the_ratchet_rules_are_selected_with_the_measured_limits() -> None:
    lint = _ruff_lint_config()

    assert set(lint["select"]) >= RATCHET_RULES
    assert lint.get("mccabe", {}).get("max-complexity") == 25
    pylint = lint.get("pylint", {})
    assert pylint.get("max-branches") == 25
    assert pylint.get("max-statements") == 100
    assert pylint.get("max-args") == 12
    # The only per-file ignore is `print` outside `src/`; every other offender is per line.
    assert lint.get("per-file-ignores") == {"scripts/**": ["T20"], "tests/**": ["T20"]}


def test_a_new_blind_except_and_an_oversized_function_fail(tmp_path: Path) -> None:
    probe = tmp_path / "src" / "tradepartner" / "probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_text(PROBE, encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            str(PYPROJECT),
            "--no-cache",
            "--output-format",
            "concise",
            str(probe),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    for code in ("BLE001", "T201", "PLR0913"):
        assert code in result.stdout, f"{code} not reported:\n{result.stdout}{result.stderr}"
