"""Plain-words rendering of paper halt reasons and unclassified failures (#2203).

Display only: what the journal stores (kill-switch causes, alert text, exception
names) and every exit code are unchanged. The owner reads the result in `paper
status`, the waiting list, the operations page and the CLI's `failed:` lines.
"""

from __future__ import annotations

import re
from typing import Final

from tradepartner.adapters.alpaca_trading_raw import AlpacaTradingError

#: Where the scheduled paper run's errors land (docs/runbooks/shakedown.md).
PAPER_ERR_LOG: Final = "~/Library/Logs/tradepartner/paper.err.log"

_SOURCE_WORDS: Final = {
    "owner": "you switched it on (paper kill)",
    "fault": "a fault switched it on (a failed check or broker problem)",
    "drawdown": "the drawdown limit switched it on",
}
_RUN_WORDS: Final = {
    "unfinished": "never finished (the process stopped part way)",
    "halted": "halted itself on a failed safety check",
    "crashed": "crashed with an error",
    "failed": "failed with an error",
}
_ENGAGED = re.compile(r"^kill_switch event (\d+) engaged \((\w+)\)$")
_RUN = re.compile(r"^run (\d+) (unfinished|halted|crashed|failed)$")


def plain_cause(cause: str) -> str:
    """One stored kill-switch cause in words; an unrecognised cause is returned as is."""
    engaged = _ENGAGED.match(cause)
    if engaged:
        who = _SOURCE_WORDS.get(engaged.group(2), f"source {engaged.group(2)}")
        return f"{who} (event {engaged.group(1)})"
    run = _RUN.match(cause)
    if run:
        return f"run {run.group(1)} {_RUN_WORDS[run.group(2)]}"
    return cause


def plain_causes(causes: tuple[str, ...] | list[str]) -> str:
    """Every cause in words, joined with `; `."""
    return "; ".join(plain_cause(c) for c in causes)


def failure_hint(exc: BaseException) -> str:
    """The plain next step for an unclassified failure."""
    if isinstance(exc, AlpacaTradingError):
        code = exc.status_code
        if code in (401, 403):
            what = "Alpaca refused the credentials; check the paper keys in .env"
        elif code == 404:
            what = "Alpaca does not know what was asked for (an order or symbol); check it exists"
        elif code == 429:
            what = "Alpaca is rate-limiting; wait a few minutes and run it again"
        elif code is None or code >= 500:
            what = "Alpaca or the network failed; check your connection and run it again"
        else:
            what = f"Alpaca rejected the request ({code})"
    elif isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        what = "a network or file problem; check your connection and disk, then run it again"
    else:
        what = "an unexpected error; nothing was changed by this message"
    return (
        f"  What happened: {what}.\n"
        f"  Next: read {PAPER_ERR_LOG}, run `tradepartner paper status`, "
        "and tell your agent if it repeats."
    )
