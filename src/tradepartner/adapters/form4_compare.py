"""Rule 5 per-field source comparison for Form 4 (plan T349, #2714, spec amendment
2026-10-31 item 6).

Pure: it reads two row lists (bulk data set and XML) and a declaration, and returns what
disagrees. It touches no store, makes no request, reads no clock and writes no fact; the
driver decides what to store and when to fail the run. The declaration names, per shared
field, the trusted source and (for price, shares and shares-owned-after only) the
tolerance. Identity, owner, line number and every other field compare exactly, and the
row keys must match as a set with none repeated, so a tolerance can never hide an
ordering, identity or owner difference.

The unit of the share is the accession: numerator = XML-fetched accessions with at least
one disagreement, denominator = all accessions whose XML was fetched and compared in the
run. Per-field disagreements are counted apart, for diagnosis only.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from decimal import Decimal
from enum import Enum
from typing import Literal

from tradepartner.adapters.edgar import Form4Transaction
from tradepartner.adapters.form4_route import DERA_ROUNDING
from tradepartner.config_data import (
    FORM4_COMPARE_FIELDS,
    FORM4_TOLERANCE_FIELDS,
    EventsConfig,
)

Source = Literal["xml", "bulk"]
KEY_FIELD = "row_key"  # the `(filer_cik, line_no)` key set differs, repeated or missing


@dataclass(frozen=True)
class FieldRule:
    """The trusted source and tolerance of one field. `tolerance` is an absolute amount
    applied after rounding to the declaration's decimals; exact fields hold 0."""

    trusted: Source = "xml"
    tolerance: Decimal = Decimal(0)


@dataclass(frozen=True)
class Declaration:
    """Per-field comparison declaration: one `FieldRule` per `FORM4_COMPARE_FIELDS` name."""

    rules: Mapping[str, FieldRule]
    decimals: int = 2
    threshold: Decimal | None = None

    def __post_init__(self) -> None:
        if set(self.rules) != set(FORM4_COMPARE_FIELDS):
            raise ValueError("a declaration must name every compared field, once")
        if {f.name for f in fields(Form4Transaction)} != set(FORM4_COMPARE_FIELDS):
            raise ValueError("the compared fields differ from Form4Transaction")
        for name, rule in self.rules.items():
            if rule.tolerance < 0 or (rule.tolerance != 0 and name not in FORM4_TOLERANCE_FIELDS):
                raise ValueError(f"{name} takes no tolerance of {rule.tolerance}")
        if self.decimals < 0:
            raise ValueError("decimals cannot be negative")
        if self.threshold is not None and not Decimal(0) < self.threshold <= Decimal(1):
            raise ValueError("the threshold is a share in (0, 1]")


def declaration_from(events: EventsConfig) -> Declaration:
    """Build the declaration the `events.form4_compare_*` keys describe."""
    rules = {
        name: FieldRule(
            events.form4_compare_trusted.get(name, "xml"),
            events.form4_compare_tolerance.get(name, Decimal(0)),
        )
        for name in FORM4_COMPARE_FIELDS
    }
    return Declaration(rules, events.form4_compare_decimals, events.form4_compare_threshold)


@dataclass(frozen=True)
class Disagreement:
    """One field of one row (or the key set, `KEY_FIELD`) on which the sources differ."""

    field: str
    row_key: tuple[str, int] | None
    bulk: object
    xml: object
    trusted: Source


def _round(value: Decimal | None, decimals: int) -> Decimal | None:
    return None if value is None else value.quantize(Decimal(1).scaleb(-decimals), DERA_ROUNDING)


def _differs(name: str, bulk: object, xml: object, decl: Declaration) -> bool:
    if name in FORM4_TOLERANCE_FIELDS:
        a = _round(bulk, decl.decimals)  # type: ignore[arg-type]
        b = _round(xml, decl.decimals)  # type: ignore[arg-type]
        if a is None or b is None:
            return a is not b
        return abs(a - b) > decl.rules[name].tolerance
    if name == "security_title":
        return " ".join(str(bulk).split()) != " ".join(str(xml).split())
    return bulk != xml


def compare_rows(
    bulk_rows: Sequence[Form4Transaction],
    xml_rows: Sequence[Form4Transaction],
    decl: Declaration,
) -> tuple[Disagreement, ...]:
    """Every disagreement between the two sources' rows of one accession. Rows match by
    `(filer_cik, line_no)`; a missing, extra or repeated key is one `KEY_FIELD`
    disagreement (trusted: the `line_no` rule's source) and compares nothing further, so
    reordered or reattributed rows cannot pass on tolerance."""
    bulk_by_key = {(r.filer_cik, r.line_no): r for r in bulk_rows}
    xml_by_key = {(r.filer_cik, r.line_no): r for r in xml_rows}
    if (
        len(bulk_by_key) != len(bulk_rows)
        or len(xml_by_key) != len(xml_rows)
        or bulk_by_key.keys() != xml_by_key.keys()
    ):
        return (
            Disagreement(
                KEY_FIELD,
                None,
                tuple(sorted(bulk_by_key)),
                tuple(sorted(xml_by_key)),
                decl.rules["line_no"].trusted,
            ),
        )
    found: list[Disagreement] = []
    for key in sorted(bulk_by_key):
        for name in FORM4_COMPARE_FIELDS:
            b, x = getattr(bulk_by_key[key], name), getattr(xml_by_key[key], name)
            if _differs(name, b, x, decl):
                found.append(Disagreement(name, key, b, x, decl.rules[name].trusted))
    return tuple(found)


class Outcome(Enum):
    """What a run's tally means against the threshold."""

    UNCHECKED = "unchecked"  # zero denominator
    REPORT_ONLY = "report_only"  # threshold unset: report, write nothing differently
    BELOW = "below"  # share under the threshold
    AT_OR_ABOVE = "at_or_above"  # share at or over it: the source run fails


@dataclass
class RunTally:
    """The run's counted comparison: accessions compared and those with a disagreement,
    plus per-field counts that never enter the share."""

    compared: int = 0
    disagreeing: int = 0
    by_field: Counter[str] = field(default_factory=Counter)

    def add(self, disagreements: Sequence[Disagreement]) -> None:
        """Count one XML-fetched accession compared in this run."""
        self.compared += 1
        if disagreements:
            self.disagreeing += 1
        self.by_field.update(d.field for d in disagreements)

    def share(self) -> Decimal | None:
        """Disagreeing over compared accessions; None when nothing was compared."""
        return Decimal(self.disagreeing) / Decimal(self.compared) if self.compared else None

    def outcome(self, threshold: Decimal | None) -> Outcome:
        """Zero denominator is unchecked; an unset threshold only reports."""
        share = self.share()
        if share is None:
            return Outcome.UNCHECKED
        if threshold is None:
            return Outcome.REPORT_ONLY
        return Outcome.AT_OR_ABOVE if share >= threshold else Outcome.BELOW

    def message(self, threshold: Decimal | None) -> str:
        """`key=value` text for the run message: numerator, denominator, threshold."""
        shown = "unset" if threshold is None else str(threshold)
        return (
            f"form4_compare_disagree={self.disagreeing}; form4_compare_compared={self.compared}; "
            f"form4_compare_threshold={shown}"
        )
