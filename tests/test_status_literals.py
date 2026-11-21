"""#3643: a journal status is spelt once, in `store.journal_enums`.

The execution modules still hold private module constants that re-spell a member of
those enums (`_FILLED = "filled"`); a typo in one is a silent never-equal that mypy
cannot see. `BASELINE` lists the ones that exist today. It may only shrink: a new
private copy fails, and so does a `BASELINE` entry whose constant is gone, so the
list is deleted line by line as each module moves to the enums.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tradepartner.store import journal_enums

_EXECUTION = Path(__file__).resolve().parents[1] / "src" / "tradepartner" / "execution"
_STATUS_VALUES = frozenset(
    member.value
    for enum in (
        journal_enums.OrderEventStatus,
        journal_enums.DecisionKind,
        journal_enums.DecisionEventStatus,
    )
    for member in enum
)
_ADVICE = (
    "use the member of `tradepartner.store.journal_enums` (OrderEventStatus, DecisionKind, "
    "DecisionEventStatus) instead of a private string"
)

#: (file, constant) pairs in `src/tradepartner/execution/` on main at #3643, less
#: `outcomes.py`'s `_FILLED`, which that PR moved to the enum.
BASELINE: frozenset[tuple[str, str]] = frozenset(
    {
        ("check.py", "_FILLED"),
        ("collect.py", "_PENDING"),
        ("collect.py", "_REJECTED"),
        ("collect.py", "_WRITTEN_OFF"),
        ("exam_ledger.py", "PENDING"),
        ("exits.py", "_DUST"),
        ("exits.py", "_FORCED_EXIT"),
        ("exits.py", "_SKIPPED"),
        ("phases.py", "_DUST"),
        ("phases.py", "_FORCED_EXIT"),
        ("phases.py", "_SKIP_BELOW_MINIMUM"),
        ("phases.py", "_SKIP_BELOW_ONE_SHARE"),
        ("phases.py", "_SKIP_DELISTED"),
        ("phases.py", "_SKIP_NOT_BORROWABLE"),
        ("phases.py", "_SKIP_SHORT_PRICE"),
        ("phases.py", "_SKIP_UNTRADABLE"),
        ("plan.py", "FORCED_EXIT"),
        ("plan.py", "_CANCELLED"),
        ("plan.py", "_DUST"),
        ("plan.py", "_FILLED_STATUS"),
        ("plan.py", "_OVERRIDE"),
        ("plan.py", "_SKIPPED"),
        ("plan.py", "_SKIP_BELOW_MINIMUM"),
        ("plan.py", "_SKIP_BELOW_ONE_SHARE"),
        ("plan.py", "_SKIP_DELISTED"),
        ("plan.py", "_SKIP_NOT_BORROWABLE"),
        ("plan.py", "_SKIP_RESTRICTED"),
        ("plan.py", "_SKIP_SHORT_PRICE"),
        ("plan.py", "_SKIP_ZERO"),
        ("plan.py", "_TRADE"),
        ("plan.py", "_WRITTEN_OFF"),
        ("planning.py", "_FORCED_EXIT"),
        ("reconcile.py", "PENDING_UNRESOLVED"),
        ("report.py", "_OVERRIDE"),
        ("resume.py", "_ACCEPTED"),
        ("resume.py", "_CANCELLED"),
        ("risk.py", "_FORCED_EXIT"),
        ("run.py", "_FORCED_EXIT"),
        ("true_up.py", "REPLAY"),
        ("window.py", "OVERRIDE"),
        ("window.py", "_CANCELLED"),
        ("window.py", "_DUST"),
        ("window.py", "_FILLED"),
        ("window.py", "_PENDING"),
        ("wrapper.py", "_ACCEPTED"),
        ("wrapper.py", "_CANCEL_FAILED"),
        ("wrapper.py", "_CANCEL_NOOP"),
        ("wrapper.py", "_CANCEL_REQUESTED"),
        ("wrapper.py", "_FORCED_EXIT"),
        ("wrapper.py", "_PENDING"),
        ("wrapper.py", "_PENDING_UNRESOLVED"),
        ("wrapper.py", "_REPLAY"),
        ("wrapper.py", "_SKIPPED"),
        ("wrapper.py", "_SKIP_RESTRICTED"),
        ("wrapper.py", "_WRITTEN_OFF"),
    }
)


def _private_copies(source: str) -> set[str]:
    """Names bound at module level, by `NAME = "<value>"` or `NAME: T = "<value>"`,
    to a string literal that is a journal status value."""
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        else:
            continue
        if (
            isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and value.value in _STATUS_VALUES
        ):
            names.update(target.id for target in targets if isinstance(target, ast.Name))
    return names


def _scan() -> set[tuple[str, str]]:
    return {
        (path.name, name)
        for path in sorted(_EXECUTION.glob("*.py"))
        for name in _private_copies(path.read_text(encoding="utf-8"))
    }


def test_no_new_private_copy_of_a_status_value() -> None:
    found = _scan()
    new = sorted(found - BASELINE)
    assert not new, f"new private copies of a journal status value: {new}; {_ADVICE}"
    gone = sorted(BASELINE - found)
    assert not gone, f"these constants are gone; delete them from BASELINE: {gone}"


def test_the_scanner_on_planted_sources() -> None:
    assert _private_copies('_FILLED = "filled"\n') == {"_FILLED"}
    assert _private_copies('PENDING: Final = "pending"\n') == {"PENDING"}
    assert _private_copies('_A = _B = "forced_exit"\n') == {"_A", "_B"}
    assert _private_copies('_SIDE = "buy"\n') == set()
    assert _private_copies("_FILLED = OrderEventStatus.FILLED\n") == set()
    assert _private_copies('def f() -> str:\n    status = "filled"\n    return status\n') == set()
