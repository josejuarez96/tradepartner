"""`questions.OptionSet` (plan T121; spec req 4, docs/specs/research-labeling.md)."""

from __future__ import annotations

import pytest

from tradepartner.research.labeling import questions
from tradepartner.research.labeling.crosswalk import UNRESOLVED, class_of


def test_default_option_set_has_unresolved_and_one_class_each() -> None:
    names = [name for name, _ in questions.DEFAULT_OPTION_SET.options]
    assert UNRESOLVED in names
    assert len(names) == 9
    for name in names:
        if name == UNRESOLVED:
            assert class_of(name) is None
        else:
            # Raises KeyError if `name` has no class; a plain call checks it maps
            # to exactly one (the five classes are a partition, so membership in
            # more than one is impossible by construction of `CLASSES`).
            assert class_of(name) is not None


def test_unresolved_is_required() -> None:
    options = tuple(o for o in questions.DEFAULT_OPTIONS if o[0] != UNRESOLVED)
    with pytest.raises(ValueError, match="unresolved"):
        questions.OptionSet(version="x", instructions="i", options=options)


def test_an_option_with_no_class_raises() -> None:
    options = (*questions.DEFAULT_OPTIONS, ("not_a_real_option", "nonsense"))
    with pytest.raises(KeyError):
        questions.OptionSet(version="x", instructions="i", options=options)


def test_repeated_option_name_raises() -> None:
    options = (*questions.DEFAULT_OPTIONS, questions.DEFAULT_OPTIONS[0])
    with pytest.raises(ValueError, match="repeat"):
        questions.OptionSet(version="x", instructions="i", options=options)


def test_hash_is_stable_for_the_same_object() -> None:
    a = questions.OptionSet(version="1", instructions="i", options=questions.DEFAULT_OPTIONS)
    b = questions.OptionSet(version="1", instructions="i", options=questions.DEFAULT_OPTIONS)
    assert a.hash == b.hash
    assert len(a.hash) == 64  # SHA-256 hex digest


def test_hash_changes_on_reorder() -> None:
    base = questions.OptionSet(version="1", instructions="i", options=questions.DEFAULT_OPTIONS)
    swapped = (
        questions.DEFAULT_OPTIONS[1],
        questions.DEFAULT_OPTIONS[0],
        *questions.DEFAULT_OPTIONS[2:],
    )
    reordered = questions.OptionSet(version="1", instructions="i", options=swapped)
    assert base.hash != reordered.hash


def test_hash_changes_on_a_description_edit() -> None:
    base = questions.OptionSet(version="1", instructions="i", options=questions.DEFAULT_OPTIONS)
    name, description = questions.DEFAULT_OPTIONS[0]
    edited_first = (name, description + " edited")
    edited = questions.OptionSet(
        version="1", instructions="i", options=(edited_first, *questions.DEFAULT_OPTIONS[1:])
    )
    assert base.hash != edited.hash


def test_hash_unchanged_by_anything_else() -> None:
    a = questions.OptionSet(version="1", instructions="i", options=questions.DEFAULT_OPTIONS)
    b = questions.OptionSet(version="1", instructions="i", options=questions.DEFAULT_OPTIONS)
    assert a.hash == b.hash


def test_criteria_is_option_to_description_in_order() -> None:
    option_set = questions.DEFAULT_OPTION_SET
    assert list(option_set.criteria.items()) == list(option_set.options)


def test_criteria_is_read_only() -> None:
    option_set = questions.DEFAULT_OPTION_SET
    with pytest.raises(TypeError):
        option_set.criteria["unresolved"] = "nope"  # type: ignore[index]


def test_default_option_set_descriptions_name_no_store_table() -> None:
    # A light duplicate of tests/test_llm_boundary.py's test (c), scoped to this
    # module's data rather than its source text, so a future edit to the literal
    # strings is caught here too, close to what it changes.
    forbidden = {
        "securities",
        "listings",
        "classifications",
        "delistings",
        "corporate_actions",
        "statement_facts",
        "research_runs",
        "research_results",
        "decisions",
        "orders",
        "signals",
    }
    for _name, description in questions.DEFAULT_OPTIONS:
        words = set(description.replace(",", " ").replace("(", " ").replace(")", " ").split())
        assert words.isdisjoint(forbidden), description
