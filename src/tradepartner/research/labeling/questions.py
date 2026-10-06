"""The question and the versioned option set (req 4), pure.

Research-labeling spec req 4 (docs/specs/research-labeling.md): one `choice`
question per packet, with an **ordered** option set whose hash changes with its
order or any description, so a run's `option_set_version` and hash fix exactly
what the model was asked (the `prompt_version` of the prospective feature spec
§3). No I/O, no store, no model client.

**Test (c)** (ADR 0013 point 3 (c); req 14) scans this module for any store table
name as a whole token and for the owner's gold/review dataset names, with an empty
allowlist: `DEFAULT_OPTIONS`' descriptions are written to pass it, and any edit here
must keep that true (`uv run pytest tests/test_llm_boundary.py -k test_c`).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from tradepartner.research.labeling import crosswalk

#: The one option every question offers besides the eight substantive reasons
#: (req 4): the filings shown do not state the reason. Has no class (req 5).
UNRESOLVED = "unresolved"

#: The task in one sentence, the evidence rule, and the fallback (req 4, last
#: sentence): "The instructions name the task in one sentence, say the reason must
#: be stated in the text shown, and say to choose `unresolved` otherwise."
DEFAULT_INSTRUCTIONS = (
    "Read the filing text shown and say why the listed instrument stopped trading "
    "on its exchange. Choose the option only if the reason is stated in the text "
    "shown; otherwise choose 'unresolved'."
)

#: The pilot's nine options, in req 4's fixed order (the model leans toward the
#: first option it sees; the perturbation probe permutes this order to measure
#: that bias, never this module). Descriptions avoid every store table name as a
#: whole token (req 14; test (c)) and name no model field, gold column or review
#: column.
DEFAULT_OPTIONS: tuple[tuple[str, str], ...] = (
    (
        "merger_or_acquisition",
        "the company was acquired by or merged into another company; holders "
        "received cash or the other company's shares",
    ),
    (
        "going_private",
        "the company was bought out by insiders, a sponsor or through a cash-out "
        "reverse split and stopped being publicly traded, without a separate "
        "acquirer absorbing it",
    ),
    (
        "instrument_retirement",
        "the instrument reached maturity, was redeemed, called, converted or "
        "expired (notes, warrants, units, rights, a SPAC trust liquidation)",
    ),
    (
        "redomicile_or_reorganisation",
        "the same business continued under a new holding company, domicile, "
        "legal form or a separation, and holders of the old class hold the new one",
    ),
    (
        "bankruptcy",
        "the class was cancelled, or trading ended, because of a bankruptcy or "
        "insolvency proceeding",
    ),
    (
        "compliance_delisting",
        "the exchange removed the class for failing a listing standard (bid "
        "price, market value, filing delinquency, other), not because of any of "
        "the above",
    ),
    (
        "voluntary_withdrawal",
        "the issuer withdrew the class from the exchange by its own decision, to "
        "trade over the counter or to deregister, with none of the above as the "
        "reason",
    ),
    (
        "exchange_transfer",
        "the class moved to another national stock exchange and kept trading",
    ),
    (UNRESOLVED, "the filings shown do not state the reason"),
)


def _canonical_json(version: str, instructions: str, options: Sequence[tuple[str, str]]) -> str:
    """A deterministic encoding of everything the hash covers: `version`,
    `instructions` and the options in their given order. Order-sensitive (a list,
    not a sorted mapping): swapping two options changes this string."""
    return json.dumps(
        {"version": version, "instructions": instructions, "options": [list(o) for o in options]},
        ensure_ascii=False,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class OptionSet:
    """One versioned `choice` question (req 4): `version`, `instructions`, and an
    **ordered** sequence of `(option, description)` pairs. `unresolved` must be
    present and every other option must map to exactly one class (`crosswalk.
    class_of`); violating either raises at construction, never silently.
    """

    version: str
    instructions: str
    options: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        names = [name for name, _ in self.options]
        if len(set(names)) != len(names):
            raise ValueError(f"OptionSet options must not repeat: {names}")
        if UNRESOLVED not in names:
            raise ValueError(f"OptionSet must include {UNRESOLVED!r}")
        for name in names:
            if name == UNRESOLVED:
                continue
            crosswalk.class_of(name)  # raises KeyError for a name with no class

    @property
    def criteria(self) -> Mapping[str, str]:
        """`option -> description`, in this object's fixed order (req 4; the
        vendor's `questions.<name>.criteria` body and `ModelRequest.criteria`,
        which leans toward the first key it sees -- the same order as `options`,
        since `dict` preserves insertion order)."""
        return MappingProxyType(dict(self.options))

    @property
    def hash(self) -> str:
        """SHA-256 over `version`, `instructions` and the ordered options (req 4):
        changes on a reordering or any description edit, never on anything else."""
        payload = _canonical_json(self.version, self.instructions, self.options)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


#: The pilot's option set (owner question 1 on #1015; req 4). Any change is a new
#: version with a new hash (req 4: "versions tried on `dev` count against the
#: registration's `budget.configurations`").
DEFAULT_OPTION_SET = OptionSet(
    version="1",
    instructions=DEFAULT_INSTRUCTIONS,
    options=DEFAULT_OPTIONS,
)
