"""The `slow` speed tier (#3647): registered, strict, and marked from the shard weights.

`tests/conftest.py` already holds an estimated CPU-seconds weight per heavy shard unit
for CI sharding. The same table marks the slow tier, so `uv run pytest -m "not slow"`
is the quick part of the suite without a second list of slow files.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace

import conftest
import pytest

_ROOT_DIR = Path(__file__).parent.parent


class _FakeItem:
    """The attributes `pytest_itemcollected` reads, with a recording `add_marker`."""

    def __init__(self, relpath: str, nodeid: str) -> None:
        self.path = _ROOT_DIR / relpath
        self.nodeid = nodeid
        self.config = SimpleNamespace(rootpath=_ROOT_DIR)
        self.marks: list[str] = []

    def add_marker(self, marker: pytest.MarkDecorator) -> None:
        self.marks.append(marker.name)


def test_slow_is_registered_and_markers_are_strict() -> None:
    options = tomllib.loads((_ROOT_DIR / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "pytest"
    ]["ini_options"]
    assert [m for m in options["markers"] if m.startswith("slow:")], options["markers"]
    assert "--strict-markers" in options["addopts"].split()


def test_a_listed_unit_is_slow_and_an_unlisted_one_is_not() -> None:
    assert conftest.is_slow_unit("tests/test_health.py") is True
    assert conftest.is_slow_unit("tests/unlisted.py") is False
    split_file = next(iter(conftest._SPLIT_BY_TEST))
    node_keys = [k for k in conftest._HEAVY_TEST_FILE_WEIGHTS if k.startswith(f"{split_file}::")]
    assert node_keys
    assert conftest.is_slow_unit(node_keys[0]) is True
    # The split file as a whole is not a unit: an unlisted node id of it is fast.
    assert conftest.is_slow_unit(f"{split_file}::test_not_in_the_table") is False


def test_collection_marks_an_item_of_a_heavy_file() -> None:
    heavy = _FakeItem("tests/test_health.py", "tests/test_health.py::test_one")
    light = _FakeItem("tests/test_calendar.py", "tests/test_calendar.py::test_one")
    conftest.pytest_itemcollected(heavy)  # type: ignore[arg-type]
    conftest.pytest_itemcollected(light)  # type: ignore[arg-type]
    assert heavy.marks == ["slow"]
    assert light.marks == []
