"""Token budgets for the always-loaded docs (#351).

Every session reads `docs/STATUS.md` and `CLAUDE.md` on every turn, so their size is paid
over and over. STATUS drifted to about 10k tokens (93% an ever-growing Done list) before
#351 cut it to a board and made `fragments.py fold` keep only the last few Done lines.
These budgets stop the drift from returning: a PR that pushes either file over fails CI
and has to move detail to where it belongs (CHANGELOG, a runbook, ways-of-working).

Tokens are estimated at 4 characters each (no tokenizer dependency).
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHARS_PER_TOKEN = 4
#: Budgets in estimated tokens, set by #351.
BUDGETS = {
    "docs/STATUS.md": 2_000,
    "CLAUDE.md": 3_000,
}


def estimated_tokens(text: str) -> int:
    """Characters / 4, rounded up."""
    return -(-len(text) // CHARS_PER_TOKEN)


@pytest.mark.parametrize(("path", "budget"), sorted(BUDGETS.items()))
def test_always_loaded_doc_stays_within_its_token_budget(path: str, budget: int) -> None:
    tokens = estimated_tokens((ROOT / path).read_text(encoding="utf-8"))
    assert tokens <= budget, (
        f"{path} is about {tokens} tokens, over its {budget}-token budget (#351): move "
        "detail out (CHANGELOG, runbooks, ways-of-working) instead of raising the budget"
    )


def test_the_estimate_rounds_up() -> None:
    assert estimated_tokens("") == 0
    assert estimated_tokens("abcd") == 1
    assert estimated_tokens("abcde") == 2


#: The longest a finished (`- [x]`) plan line may be, in characters (#353). A finished
#: task collapses to `**Tn: Title** (#issue, PR #n) · Files: <paths> · Depends on: …`;
#: tests, review and prose live in the PR and git history.
FINISHED_LINE_MAX_CHARS = 400
PLANS = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / "docs" / "plans").glob("*.md"))


def long_finished_lines(text: str) -> list[str]:
    """The task ids of finished plan lines over `FINISHED_LINE_MAX_CHARS`."""
    return [
        line.split("**")[1].split(":")[0]
        for line in text.splitlines()
        if line.startswith("- [x] ") and len(line) > FINISHED_LINE_MAX_CHARS
    ]


@pytest.mark.parametrize("path", PLANS)
def test_finished_plan_lines_are_collapsed(path: str) -> None:
    long = long_finished_lines((ROOT / path).read_text(encoding="utf-8"))
    assert not long, (
        f"{path}: finished lines over {FINISHED_LINE_MAX_CHARS} characters: {long}. Collapse "
        "each to `- [x] **Tn: Title** (#issue, PR #n) · Files: <paths> · Depends on: …` (#353)"
    )


def test_the_finished_line_check_ignores_open_lines() -> None:
    long_open = "- [ ] **T9: Open.** " + "x" * 500
    long_done = "- [x] **T8 (owner): Done.** " + "x" * 500
    assert long_finished_lines(f"{long_open}\n{long_done}\n- [x] **T7: Short.**") == ["T8 (owner)"]
