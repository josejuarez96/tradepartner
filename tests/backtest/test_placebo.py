"""The kill ladder's rung-5 placebos and the shock note (backtest spec amendment #1820,
items 1 to 3; plan T217).

`shuffle` and `random_entry` take a `SignalResult.scores` mapping and the seed string
`f"{placebo_seed}:{rebalance_session.toordinal()}"` given to `random.Random`; the
scored names are sorted by security id first. Every expected value below is derived by
hand from the generator pin, so a stdlib change fails `test_generator_pin` by name.
"""

from __future__ import annotations

import ast
import random
import sys
from datetime import date
from pathlib import Path

import pytest

from tradepartner.backtest import placebo

SRC = Path(__file__).resolve().parents[2] / "src" / "tradepartner"
PLACEBO_PY = SRC / "backtest" / "placebo.py"

#: Five names given out of id order; sorted by id they are A1 < B2 < C3 < D4 < E5.
SCORES = {"C3": 0.3, "A1": 0.1, "E5": 0.5, "B2": 0.2, "D4": 0.4}
IDS = ("A1", "B2", "C3", "D4", "E5")

#: `random.Random(seed).shuffle(list(range(5)))` on Python 3.12, the generator pin.
PERMUTATIONS = {
    "0:1": [3, 0, 2, 4, 1],
    "7:1": [2, 1, 3, 4, 0],
    "0:2": [1, 4, 0, 2, 3],
}


@pytest.mark.parametrize(("seed", "expected"), PERMUTATIONS.items())
def test_generator_pin(seed: str, expected: list[int]) -> None:
    """The stdlib generator the placebos rest on, pinned against Python 3.12."""
    assert sys.version_info[:2] == (3, 12)
    values = list(range(5))
    random.Random(seed).shuffle(values)
    assert values == expected


# --- shuffle ----------------------------------------------------------------------


def test_shuffle_equals_the_hand_permutation_of_the_id_sorted_scores() -> None:
    """Id-sorted vector [0.1, 0.2, 0.3, 0.4, 0.5] shuffled by "0:1" is
    [0.4, 0.1, 0.3, 0.5, 0.2], assigned back to A1..E5 in id order."""
    assert dict(placebo.shuffle(SCORES, "0:1")) == {
        "A1": 0.4,
        "B2": 0.1,
        "C3": 0.3,
        "D4": 0.5,
        "E5": 0.2,
    }


@pytest.mark.parametrize("seed", PERMUTATIONS)
def test_shuffle_follows_the_pin_for_every_seed(seed: str) -> None:
    vector = [SCORES[i] for i in IDS]
    expected = {name: vector[j] for name, j in zip(IDS, PERMUTATIONS[seed], strict=True)}
    assert dict(placebo.shuffle(SCORES, seed)) == expected


def test_shuffle_ignores_the_mapping_order() -> None:
    """The names are sorted by security id before the draw, so insertion order is moot."""
    in_id_order = {name: SCORES[name] for name in IDS}
    reversed_order = {name: SCORES[name] for name in reversed(IDS)}
    assert dict(placebo.shuffle(in_id_order, "0:1")) == dict(placebo.shuffle(SCORES, "0:1"))
    assert dict(placebo.shuffle(reversed_order, "0:1")) == dict(placebo.shuffle(SCORES, "0:1"))


def test_shuffle_is_identical_across_two_calls_with_one_seed() -> None:
    assert dict(placebo.shuffle(SCORES, "7:1")) == dict(placebo.shuffle(SCORES, "7:1"))


def test_shuffle_differs_across_seeds_and_across_sessions() -> None:
    base = dict(placebo.shuffle(SCORES, "0:1"))
    assert dict(placebo.shuffle(SCORES, "7:1")) != base  # another placebo_seed
    assert dict(placebo.shuffle(SCORES, "0:2")) != base  # another rebalance session


def test_shuffle_keeps_the_names_and_the_score_multiset() -> None:
    out = placebo.shuffle(SCORES, "0:1")
    assert set(out) == set(SCORES)
    assert sorted(out.values()) == sorted(SCORES.values())


def test_shuffle_leaves_its_input_untouched() -> None:
    scores = dict(SCORES)
    placebo.shuffle(scores, "0:1")
    assert scores == SCORES


def test_shuffle_with_a_real_session_seed() -> None:
    """The seed a run builds, `f"{placebo_seed}:{session.toordinal()}"`, is a plain
    string to `random.Random`; the result is that generator's shuffle of the vector."""
    seed = f"7:{date(2024, 1, 31).toordinal()}"
    order = list(range(5))
    random.Random(seed).shuffle(order)
    vector = [SCORES[i] for i in IDS]
    assert dict(placebo.shuffle(SCORES, seed)) == {
        name: vector[j] for name, j in zip(IDS, order, strict=True)
    }


# --- random_entry -----------------------------------------------------------------


def test_random_entry_equals_the_hand_ranking() -> None:
    """ "0:1" shuffles A1..E5 to [D4, A1, C3, E5, B2], scored 5 down to 1 in that order;
    the signal's own scores play no part."""
    assert dict(placebo.random_entry(SCORES, "0:1")) == {
        "D4": 5.0,
        "A1": 4.0,
        "C3": 3.0,
        "E5": 2.0,
        "B2": 1.0,
    }


def test_random_entry_ranks_by_the_pin_for_another_seed() -> None:
    """ "7:1" shuffles A1..E5 to [C3, B2, D4, E5, A1]."""
    assert dict(placebo.random_entry(SCORES, "7:1")) == {
        "C3": 5.0,
        "B2": 4.0,
        "D4": 3.0,
        "E5": 2.0,
        "A1": 1.0,
    }


def test_random_entry_is_a_full_ranking_with_no_tie() -> None:
    """Every name gets a distinct score n..1, so `hold` fills open entries from the same
    ranking rather than from a tie among zeros in id order (spec-critic on #1821)."""
    out = placebo.random_entry(SCORES, "0:2")
    assert set(out) == set(SCORES)
    assert sorted(out.values()) == [1.0, 2.0, 3.0, 4.0, 5.0]


def test_random_entry_ignores_the_mapping_order_and_the_scores() -> None:
    other = {name: -float(k) for k, name in enumerate(reversed(IDS))}
    assert dict(placebo.random_entry(other, "0:1")) == dict(placebo.random_entry(SCORES, "0:1"))


def test_random_entry_every_name_ranks_first_across_200_seeds() -> None:
    session = date(2024, 1, 31).toordinal()
    firsts = set()
    for placebo_seed in range(200):
        out = placebo.random_entry(SCORES, f"{placebo_seed}:{session}")
        firsts.add(max(out, key=out.__getitem__))
    assert firsts == set(IDS)


def test_placebos_on_no_scores_are_empty() -> None:
    assert dict(placebo.shuffle({}, "0:1")) == {}
    assert dict(placebo.random_entry({}, "0:1")) == {}


# --- shock_note -------------------------------------------------------------------

DEFAULTS = {
    "strategy.top_fraction": 0.10,
    "strategy.signal_delay_sessions": 0,
    "strategy.placebo": "none",
    "strategy.placebo_seed": 0,
}


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"strategy.placebo": "shuffle", "strategy.placebo_seed": 7}, "placebo=shuffle seed=7"),
        ({"strategy.signal_delay_sessions": 1}, "signal_delay=1"),
        (
            {
                "strategy.placebo": "shuffle",
                "strategy.placebo_seed": 7,
                "strategy.signal_delay_sessions": 1,
            },
            "placebo=shuffle seed=7 signal_delay=1",
        ),
        ({"strategy.placebo": "random_entry"}, "placebo=random_entry seed=0"),
        ({}, None),
        ({"strategy.placebo_seed": 7}, None),  # a seed without a placebo is no shock
    ],
)
def test_shock_note(overrides: dict[str, object], expected: str | None) -> None:
    assert placebo.shock_note({**DEFAULTS, **overrides}) == expected


# --- purity -----------------------------------------------------------------------


def test_placebo_module_imports_only_the_stdlib() -> None:
    """Pure and stdlib-only (no new dependency, no store or engine import)."""
    tree = ast.parse(PLACEBO_PY.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {alias.name.partition(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "no relative import"
            roots.add((node.module or "").partition(".")[0])
    assert roots <= set(sys.stdlib_module_names) | {"__future__"}
