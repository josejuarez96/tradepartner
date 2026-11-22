"""The feasibility design file: its closed parser and canonical hash (T453, #3702).

The research-registry spec's 2026-11-21 amendment ("a counts-only feasibility
command") is the contract. This module is the design half of it:

* `parse_design_text` / `parse_design_file`: the closed TOML design file. Unknown keys
  are refused with the key named; the SHA-256 is of the canonical JSON of the parsed
  design, so key order, whitespace and comments never change it (the pattern of
  `experiment.experiment_sha256`).
* `SOURCES`: the four sources, their tables and the columns a filter may name.

The counts object and the block renderer are in `feasibility_block`. No clock, no store
and no config is read here, and no return is read anywhere.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Any, Final

__all__ = [
    "ENTRY_RULES",
    "SOURCES",
    "FeasibilityDesign",
    "FeasibilityDesignError",
    "ShapeInputs",
    "SourceSpec",
    "design_sha256",
    "parse_design_file",
    "parse_design_text",
]

#: The event-study runner's entry rules (`event_study.run.entry_session`).
ENTRY_RULES: Final[tuple[str, ...]] = ("next_session_after_date", "next_open_after_instant")

Scalar = str | bool | int


class FeasibilityDesignError(ValueError):
    """The design file is not a valid design; the message names the key."""


@dataclass(frozen=True)
class SourceSpec:
    """One of the four closed sources: its table and the columns a filter may test.

    `columns` is every data column of the table (the four fact stamps `known_at`,
    `ingested_at`, `source` and `provenance` excluded). `filter_columns` is the subset a
    filter may name: columns that describe the filed event itself. An identifier
    (security, issuer, owner or filer ids, accession numbers, names) is never one, and
    no date or stamp is: a filter on an identifier would let a design pick names after
    the fact. Nor is a continuous or free-text column (an exact-value list would act as a
    row id) or a revision flag (`cancelled`: filtering on it resolves an event by a later
    fact).

    The rule the reader (T454) follows for every source: an event is the **first-known**
    row of an issuer's identity, at its own `known_at`; a later revision, cancellation or
    restatement never removes or re-dates it.
    """

    name: str
    table: str
    issuer_column: str
    columns: tuple[str, ...]
    filter_columns: tuple[str, ...]


#: The four held tables with a `known_at`, as the spec lists them. The 8-K items table is
#: not one (the guard of #1382). Columns are those of `store/schema.py`.
SOURCES: Final[dict[str, SourceSpec]] = {
    "form4": SourceSpec(
        "form4",
        "sec_form4_transactions",
        "issuer_cik",
        (
            "accession",
            "issuer_cik",
            "filer_cik",
            "line_no",
            "is_director",
            "is_officer",
            "is_ten_percent_owner",
            "is_other",
            "transaction_date",
            "deemed_execution_date",
            "transaction_code",
            "acquired_disposed",
            "shares",
            "price",
            "shares_owned_after",
            "ownership_form",
            "security_title",
            "date_of_original_submission",
            "accepted_at",
        ),
        (
            "transaction_code",
            "acquired_disposed",
            "is_director",
            "is_officer",
            "is_ten_percent_owner",
            "is_other",
            "ownership_form",
        ),
    ),
    "statement_fact": SourceSpec(
        "statement_fact",
        "statement_facts",
        "cik",
        (
            "cik",
            "fact_name",
            "xbrl_tag",
            "period_start",
            "period_end",
            "period_days",
            "value",
            "unit",
            "form",
            "filing_accession",
            "basis",
            "comparative",
        ),
        ("fact_name", "xbrl_tag", "form", "period_days", "unit", "basis", "comparative"),
    ),
    "form25": SourceSpec(
        "form25",
        "delistings",
        "security_id",
        ("security_id", "form", "class_title", "exchange", "filed_at", "effective_on"),
        ("form", "exchange"),
    ),
    "corporate_action": SourceSpec(
        "corporate_action",
        "corporate_actions",
        "security_id",
        (
            "security_id",
            "action_type",
            "ex_date",
            "ratio_or_amount",
            "announced_at",
            "source_action_id",
            "cancelled",
        ),
        ("action_type",),
    ),
}

_TOP_KEYS: Final = frozenset(
    {"source", "filter", "entry", "window", "hold_sessions", "band", "rho", "rho_source", "shape"}
)
_WINDOW_KEYS: Final = frozenset({"start", "end"})
_BAND_KEYS: Final = frozenset({"lookback_sessions", "edges"})
_SHAPE_KEYS: Final = ("gross_bp", "sd_bp", "decision_cost_bps", "floor_bp", "t_floor")


@dataclass(frozen=True)
class ShapeInputs:
    """The `[shape]` table, copied from the card (event ideas only; units: basis points)."""

    gross_bp: float
    sd_bp: float
    decision_cost_bps: float
    floor_bp: float
    t_floor: float


@dataclass(frozen=True)
class FeasibilityDesign:
    """A parsed design. `filters` is `(column, allowed values)`, values sorted."""

    source: str
    filters: tuple[tuple[str, tuple[Scalar, ...]], ...]
    entry: str
    window_start: date
    window_end: date
    hold_sessions: int
    lookback_sessions: int
    edges: tuple[float, ...]
    rho: float | None
    rho_source: str | None
    shape: ShapeInputs | None
    sha256: str

    @property
    def spec(self) -> SourceSpec:
        """The source's table, issuer column and filed columns."""
        return SOURCES[self.source]

    @property
    def n_bands(self) -> int:
        """Number of liquidity bands: one more than the edges."""
        return len(self.edges) + 1


# --- parsing ---


def _fail(key: str, why: str) -> FeasibilityDesignError:
    return FeasibilityDesignError(f"{key}: {why}")


def _table(doc: Mapping[str, Any], key: str, allowed: frozenset[str]) -> dict[str, Any]:
    value = doc.get(key)
    if not isinstance(value, dict):
        raise _fail(key, "missing table" if value is None else "must be a table")
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _fail(f"{key}.{unknown[0]}", "unknown key")
    return value


def _number(key: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _fail(key, "must be a number")
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):
        raise _fail(key, "must be finite")
    return out + 0.0


def _positive_int(key: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail(key, "must be a positive integer")
    return value


def _date(key: str, value: object) -> date:
    # A TOML datetime is a `datetime`, which is also a `date`: only a calendar date is a window end.
    if isinstance(value, datetime) or not isinstance(value, date):
        raise _fail(key, "must be a date (YYYY-MM-DD)")
    return value


#: The value type of each filterable column; a mismatch is refused with the key named.
FILTER_KINDS: Final[dict[str, dict[str, type]]] = {
    "form4": {
        "transaction_code": str,
        "acquired_disposed": str,
        "is_director": bool,
        "is_officer": bool,
        "is_ten_percent_owner": bool,
        "is_other": bool,
        "ownership_form": str,
    },
    "statement_fact": {
        "fact_name": str,
        "xbrl_tag": str,
        "form": str,
        "period_days": int,
        "unit": str,
        "basis": str,
        "comparative": bool,
    },
    "form25": {"form": str, "exchange": str},
    "corporate_action": {"action_type": str},
}
#: Closed vocabularies the schema defines (`CHECK` constraints).
FILTER_VOCABULARIES: Final[dict[tuple[str, str], tuple[str, ...]]] = {
    ("form4", "acquired_disposed"): ("A", "D"),
    ("statement_fact", "basis"): ("reported", "derived"),
}


def _filter_values(
    key: str, value: object, kind: type, vocabulary: tuple[str, ...] | None = None
) -> tuple[Scalar, ...]:
    items = value if isinstance(value, list) else [value]
    if not items:
        raise _fail(key, "membership list must not be empty")
    if not all(type(v) is kind for v in items):
        raise _fail(key, f"must be a {kind.__name__}, or a list of them")
    if vocabulary is not None:
        bad = sorted(str(v) for v in items if v not in vocabulary)
        if bad:
            raise _fail(key, f"{bad[0]!r} is not one of {', '.join(vocabulary)}")
    return tuple(sorted(set(items), key=lambda v: (str(type(v)), v)))


def _filters(
    doc: Mapping[str, Any], spec: SourceSpec
) -> tuple[tuple[str, tuple[Scalar, ...]], ...]:
    raw = doc.get("filter", {})
    if not isinstance(raw, dict):
        raise _fail("filter", "must be a table")
    out = []
    for column in sorted(raw):
        if column not in spec.filter_columns:
            why = (
                "an identifier, date, stamp or unsuitable column"
                if column in spec.columns
                else "not a column"
            )
            raise _fail(
                f"filter.{column}",
                f"{why} of {spec.table}, not filterable "
                f"(filterable: {', '.join(spec.filter_columns)})",
            )
        kind = FILTER_KINDS[spec.name][column]
        vocab = FILTER_VOCABULARIES.get((spec.name, column))
        out.append((column, _filter_values(f"filter.{column}", raw[column], kind, vocab)))
    return tuple(out)


def _shape_inputs(doc: Mapping[str, Any]) -> ShapeInputs | None:
    if "shape" not in doc:
        return None
    raw = doc["shape"]
    if not isinstance(raw, dict):
        raise _fail("shape", "must be a table")
    unknown = sorted(set(raw) - set(_SHAPE_KEYS))
    if unknown:
        raise _fail(f"shape.{unknown[0]}", "unknown key")
    for key in _SHAPE_KEYS:
        if key not in raw:
            raise _fail(f"shape.{key}", "missing key")
    values = {k: _number(f"shape.{k}", raw[k]) for k in _SHAPE_KEYS}
    if values["sd_bp"] <= 0:
        raise _fail("shape.sd_bp", "must be positive")
    for key in ("decision_cost_bps", "floor_bp", "t_floor"):
        if values[key] < 0:
            raise _fail(f"shape.{key}", "must not be negative")
    return ShapeInputs(**values)


def _canonical(design: Mapping[str, Any]) -> str:
    return json.dumps(design, sort_keys=True, separators=(",", ":"), allow_nan=False)


def design_sha256(design: Mapping[str, Any]) -> str:
    """SHA-256 of a normalised design's canonical (key-sorted, compact) JSON."""
    return sha256(_canonical(design).encode("utf-8")).hexdigest()


def parse_design_text(text: str, *, name: str = "design") -> FeasibilityDesign:
    """Parse and validate a design file's text. Raises `FeasibilityDesignError` naming the key.

    The hash is of the normalised values, not of the text: key order, whitespace,
    comments, `1` against `1.0` and the order of a membership list do not change it.
    """
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise FeasibilityDesignError(f"{name}: not valid TOML: {exc}") from exc
    unknown = sorted(set(doc) - _TOP_KEYS)
    if unknown:
        raise _fail(unknown[0], "unknown key")

    source = doc.get("source")
    if source is None:
        raise _fail("source", "missing key")
    if not isinstance(source, str) or source not in SOURCES:
        raise _fail("source", f"must be one of {', '.join(SOURCES)}, got {source!r}")
    spec = SOURCES[source]
    filters = _filters(doc, spec)

    entry = doc.get("entry")
    if entry is None:
        raise _fail("entry", "missing key")
    if entry not in ENTRY_RULES:
        raise _fail("entry", f"must be one of {', '.join(ENTRY_RULES)}")

    window = _table(doc, "window", _WINDOW_KEYS)
    for key in sorted(_WINDOW_KEYS):
        if key not in window:
            raise _fail(f"window.{key}", "missing key")
    start = _date("window.start", window["start"])
    end = _date("window.end", window["end"])
    if end < start:
        raise _fail("window.end", f"{end} is before window.start {start}")

    if "hold_sessions" not in doc:
        raise _fail("hold_sessions", "missing key")
    hold = _positive_int("hold_sessions", doc["hold_sessions"])

    band = _table(doc, "band", _BAND_KEYS)
    for key in sorted(_BAND_KEYS):
        if key not in band:
            raise _fail(f"band.{key}", "missing key")
    lookback = _positive_int("band.lookback_sessions", band["lookback_sessions"])
    raw_edges = band["edges"]
    if not isinstance(raw_edges, list) or not raw_edges:
        raise _fail("band.edges", "must be a non-empty list of numbers")
    edges = tuple(_number(f"band.edges[{i}]", v) for i, v in enumerate(raw_edges))
    if edges[0] <= 0 or any(b >= a for a, b in pairwise(edges)):
        raise _fail("band.edges", "must be positive and strictly descending")

    rho: float | None = None
    rho_source: str | None = None
    if "rho" in doc:
        rho = _number("rho", doc["rho"])
        if not 0.0 <= rho <= 1.0:
            raise _fail("rho", "must be in [0, 1]")
    if "rho_source" in doc:
        raw_src = doc["rho_source"]
        if (
            not isinstance(raw_src, str)
            or not raw_src.strip()
            or "\n" in raw_src
            or "\r" in raw_src
        ):
            raise _fail("rho_source", "must be a non-empty single-line string")
        rho_source = raw_src.strip()
    if rho is not None and rho_source is None:
        raise _fail("rho_source", "missing: a rho needs the source it is cited from")
    if rho_source is not None and rho is None:
        raise _fail("rho", "missing: a rho_source without a rho")

    shape_inputs = _shape_inputs(doc)
    normalised: dict[str, Any] = {
        "source": source,
        "filter": {column: list(values) for column, values in filters},
        "entry": entry,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "hold_sessions": hold,
        "band": {"lookback_sessions": lookback, "edges": list(edges)},
    }
    if rho is not None:
        normalised["rho"] = rho
        normalised["rho_source"] = rho_source
    if shape_inputs is not None:
        normalised["shape"] = asdict(shape_inputs)
    return FeasibilityDesign(
        source=source,
        filters=filters,
        entry=entry,
        window_start=start,
        window_end=end,
        hold_sessions=hold,
        lookback_sessions=lookback,
        edges=edges,
        rho=rho,
        rho_source=rho_source,
        shape=shape_inputs,
        sha256=design_sha256(normalised),
    )


def parse_design_file(path: Path) -> FeasibilityDesign:
    """Read and parse a design file (UTF-8 TOML). A missing file raises `FeasibilityDesignError`."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FeasibilityDesignError(f"{path}: cannot read the design: {exc.strerror}") from exc
    return parse_design_text(text, name=str(path))
