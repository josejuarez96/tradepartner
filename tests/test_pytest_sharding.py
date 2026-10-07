"""Pure test-file bucketing used by CI's pytest shards (#1112).

`shard_assignment` (tests/conftest.py) is what `pytest_collection_modifyitems`
uses to keep only one shard's items per CI job. These tests exercise the
pure function directly; the collection behavior is covered by CI itself
running all N shards and the workflow comparing counts (see the PR body).
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest
from conftest import (
    _DEFAULT_TEST_FILE_WEIGHT,
    _HEAVY_TEST_FILE_WEIGHTS,
    _SPLIT_BY_TEST,
    _shard_unit,
    shard_assignment,
)

_TESTS_DIR = Path(__file__).parent
_ROOT_DIR = _TESTS_DIR.parent


def _collected_test_files() -> list[str]:
    """Every file pytest's default `test_*.py` pattern collects, repo-relative posix paths."""
    return [p.relative_to(_ROOT_DIR).as_posix() for p in sorted(_TESTS_DIR.rglob("test_*.py"))]


def test_every_file_lands_in_exactly_one_shard() -> None:
    files = _collected_test_files()
    assignment = shard_assignment(files, shard_count=4)
    # total: every input path appears as a key, nothing dropped or duplicated.
    assert set(assignment) == set(files)
    # exactly one: each value is a single valid shard index.
    assert all(0 <= shard < 4 for shard in assignment.values())


def test_union_of_shards_is_the_full_set() -> None:
    files = _collected_test_files()
    assignment = shard_assignment(files, shard_count=4)
    shards: list[set[str]] = [set() for _ in range(4)]
    for path, shard in assignment.items():
        shards[shard].add(path)
    union: set[str] = set()
    for shard in shards:
        union |= shard
    assert union == set(files)
    # disjoint: no file is kept by more than one shard.
    assert sum(len(shard) for shard in shards) == len(union)


def test_assignment_is_deterministic_regardless_of_input_order() -> None:
    files = _collected_test_files()
    forward = shard_assignment(files, shard_count=4)
    backward = shard_assignment(list(reversed(files)), shard_count=4)
    assert forward == backward


def test_heavy_files_are_balanced_by_weight_not_piled_on_one_shard() -> None:
    """The whole point of weighting known-heavy files: total weight per shard stays
    close, even though the files themselves vary a lot in individual weight."""
    assignment = shard_assignment(_HEAVY_TEST_FILE_WEIGHTS, shard_count=4)
    loads = Counter()
    for path, shard in assignment.items():
        loads[shard] += _HEAVY_TEST_FILE_WEIGHTS[path]
    average = sum(loads.values()) / 4
    # no shard is more than ~25% off the average load, across heavy files alone.
    assert all(abs(load - average) <= 0.25 * average for load in loads.values()), loads


def test_single_shard_count_keeps_everything() -> None:
    files = ["tests/a.py", "tests/b.py", "tests/c.py"]
    assignment = shard_assignment(files, shard_count=1)
    assert set(assignment.values()) == {0}


def test_rejects_non_positive_shard_count() -> None:
    with pytest.raises(ValueError):
        shard_assignment(["tests/a.py"], shard_count=0)


def test_default_weight_applies_to_unlisted_files() -> None:
    assert "tests/test_pytest_sharding.py" not in _HEAVY_TEST_FILE_WEIGHTS
    # a file not in the heavy table gets the default weight implicitly: two
    # unlisted files of equal (default) weight go to two different shards.
    assignment = shard_assignment(["tests/unlisted_a.py", "tests/unlisted_b.py"], shard_count=2)
    assert assignment["tests/unlisted_a.py"] != assignment["tests/unlisted_b.py"]
    assert _DEFAULT_TEST_FILE_WEIGHT > 0


class _FakeItem:
    """Stands in for `pytest.Item`: `_shard_unit` only reads `.path` and `.nodeid`."""

    def __init__(self, path: Path, nodeid: str) -> None:
        self.path = path
        self.nodeid = nodeid


def test_shard_unit_is_the_file_for_an_ordinary_file() -> None:
    item = _FakeItem(_ROOT_DIR / "tests" / "test_health.py", "tests/test_health.py::test_x")
    assert _shard_unit(item, _ROOT_DIR) == "tests/test_health.py"


def test_shard_unit_is_the_nodeid_for_a_split_by_test_file() -> None:
    (split_file,) = _SPLIT_BY_TEST
    nodeid = f"{split_file}::test_some_case[month_end]"
    item = _FakeItem(_ROOT_DIR / split_file, nodeid)
    assert _shard_unit(item, _ROOT_DIR) == nodeid


def test_two_tests_in_the_same_split_by_test_file_can_land_in_different_shards() -> None:
    """The whole point of `_SPLIT_BY_TEST`: unlike an ordinary file, its own tests are
    independent shard units and don't have to travel together."""
    (split_file,) = _SPLIT_BY_TEST
    a = f"{split_file}::test_a[month_end]"
    b = f"{split_file}::test_b[month_end]"
    assignment = shard_assignment([a, b], shard_count=2)
    assert assignment[a] != assignment[b]


def test_split_by_test_files_have_only_nodeid_keys_in_the_weight_table() -> None:
    """A bare file-path key for a `_SPLIT_BY_TEST` file would never be looked up (its
    tests are always keyed by nodeid instead) and would silently rot."""
    for split_file in _SPLIT_BY_TEST:
        assert split_file not in _HEAVY_TEST_FILE_WEIGHTS
        assert any(key.startswith(f"{split_file}::") for key in _HEAVY_TEST_FILE_WEIGHTS)
