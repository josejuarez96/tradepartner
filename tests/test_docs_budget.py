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
