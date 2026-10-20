"""Pure test-file bucketing used by CI's pytest shards (#1112).

`shard_assignment` (tests/conftest.py) is what `pytest_collection_modifyitems`
uses to keep only one shard's items per CI job. These tests check the
assignment against one real collection and cover the shard item order.
"""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import (
    _DEFAULT_TEST_FILE_WEIGHT,
    _HEAVY_TEST_FILE_WEIGHTS,
    _SPLIT_BY_TEST,
    _order_shard_items,
    _shard_unit,
    pytest_collection_modifyitems,
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
    assert "tests/unlisted_a.py" not in _HEAVY_TEST_FILE_WEIGHTS
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


def test_shard_order_is_heaviest_first_with_stable_unit_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Weights 5/1/3 run in that order; equal weights sort by unit nodeid."""
    for unit, weight in {"a": 5.0, "b": 1.0, "c": 3.0, "d": 3.0}.items():
        monkeypatch.setitem(_HEAVY_TEST_FILE_WEIGHTS, f"tests/{unit}.py", weight)
    items = [
        _FakeItem(_ROOT_DIR / "tests/d.py", "tests/d.py::one"),
        _FakeItem(_ROOT_DIR / "tests/a.py", "tests/a.py::second"),
        _FakeItem(_ROOT_DIR / "tests/b.py", "tests/b.py::one"),
        _FakeItem(_ROOT_DIR / "tests/a.py", "tests/a.py::first"),
        _FakeItem(_ROOT_DIR / "tests/c.py", "tests/c.py::one"),
    ]
    _order_shard_items(items, _ROOT_DIR)
    assert [item.nodeid for item in items] == [
        "tests/a.py::second",
        "tests/a.py::first",
        "tests/c.py::one",
        "tests/d.py::one",
        "tests/b.py::one",
    ]


def test_local_collection_keeps_its_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_SHARD_INDEX", raising=False)
    monkeypatch.delenv("PYTEST_SHARD_COUNT", raising=False)
    items = [
        _FakeItem(_ROOT_DIR / "tests/b.py", "tests/b.py::one"),
        _FakeItem(_ROOT_DIR / "tests/a.py", "tests/a.py::one"),
    ]
    pytest_collection_modifyitems(None, items)  # type: ignore[arg-type]
    assert [item.nodeid for item in items] == ["tests/b.py::one", "tests/a.py::one"]


def test_shard_collection_orders_kept_items(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_SHARD_INDEX", "0")
    monkeypatch.setenv("PYTEST_SHARD_COUNT", "1")
    monkeypatch.setitem(_HEAVY_TEST_FILE_WEIGHTS, "tests/a.py", 5.0)
    monkeypatch.setitem(_HEAVY_TEST_FILE_WEIGHTS, "tests/b.py", 1.0)
    items = [
        _FakeItem(_ROOT_DIR / "tests/b.py", "tests/b.py::one"),
        _FakeItem(_ROOT_DIR / "tests/a.py", "tests/a.py::one"),
    ]
    pytest_collection_modifyitems(SimpleNamespace(rootpath=_ROOT_DIR), items)  # type: ignore[arg-type]
    assert [item.nodeid for item in items] == ["tests/a.py::one", "tests/b.py::one"]


def test_real_collection_shards_partition_the_full_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    """One real collection, then each CI shard's actual deselection hook (#2056)."""
    workflow = (_ROOT_DIR / ".github" / "workflows" / "ci.yml").read_text()
    count_match = re.search(r"(?m)^  PYTEST_SHARD_COUNT:\s*(\d+)\s*$", workflow)
    assert count_match, "CI workflow has no PYTEST_SHARD_COUNT"
    count = int(count_match.group(1))
    monkeypatch.delenv("PYTEST_SHARD_INDEX", raising=False)
    monkeypatch.delenv("PYTEST_SHARD_COUNT", raising=False)
    monkeypatch.delenv("PYTEST_ADDOPTS", raising=False)

    class Collector:
        def __init__(self) -> None:
            self.session: pytest.Session | None = None

        def pytest_collection_finish(self, session: pytest.Session) -> None:
            self.session = session

    collector = Collector()
    result = pytest.main(
        ["--collect-only", "-q", "-p", "no:cacheprovider", "-o", "addopts=", "tests"],
        plugins=[collector],
    )
    assert result == pytest.ExitCode.OK
    assert collector.session is not None
    session = collector.session
    full_items = list(session.items)
    full = [item.nodeid for item in full_items]
    units = {_shard_unit(item, session.config.rootpath) for item in full_items}
    shards: list[list[str]] = []
    monkeypatch.setenv("PYTEST_SHARD_COUNT", str(count))
    for index in range(count):
        monkeypatch.setenv("PYTEST_SHARD_INDEX", str(index))
        kept = full_items.copy()
        pytest_collection_modifyitems(session.config, kept)
        shards.append([item.nodeid for item in kept])
    shard_sets = [set(shard) for shard in shards]
    assert all(
        shard_sets[i].isdisjoint(shard_sets[j]) for i in range(count) for j in range(i + 1, count)
    ), "a test is collected by more than one shard"
    assert sum(map(len, shards)) == sum(map(len, shard_sets)), "a shard has duplicate tests"
    assert set().union(*shard_sets) == set(full)
    assert len(full) == len(set(full))
    assert len(full) > 1000, "collection looks empty or truncated"
    # no stale weight keys: every listed unit is a real file or nodeid (a renamed test or
    # parameter would otherwise fall back to the default weight and unbalance a shard).
    stale = sorted(set(_HEAVY_TEST_FILE_WEIGHTS) - units)
    assert not stale, f"weight table keys that match no collected unit: {stale}"
