"""The feasibility design file, its counts object and the block renderer (T442, #3702).

The research-registry spec's 2026-11-21 amendment ("a counts-only feasibility
command") is the contract. This module is the pure half of it:

* `parse_design_text` / `parse_design_file`: the closed TOML design file. Unknown keys
  are refused with the key named; the SHA-256 is of the canonical JSON of the parsed
  design, so key order, whitespace and comments never change it (the pattern of
  `experiment.experiment_sha256`).
* `FeasibilityCounts`: the typed counts the reader (T443) fills.
* `render_block`: a pure function from the design and the counts to the block's text,
  the same bytes for the same inputs, ending with the one ledger line.

No clock, no store and no config is read here, and no return is read anywhere. The
arithmetic of the `[shape]` lines is `tradepartner.research.shape`'s, reached through
one adapter (`_shape_figures`).
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Any, Final

from tradepartner.research import shape
from tradepartner.research.shape import EffectiveCount

__all__ = [
    "ENTRY_RULES",
    "SOURCES",
    "BandTotal",
    "CountCell",
    "DelisterCoverage",
    "FeasibilityCounts",
    "FeasibilityDesign",
    "FeasibilityDesignError",
    "ShapeInputs",
    "SourceSpec",
    "StoreFacts",
    "TimestampCounts",
    "band_label",
    "design_sha256",
    "parse_design_file",
    "parse_design_text",
    "render_block",
]

#: The event-study runner's entry rules (`event_study.run.entry_session`).
ENTRY_RULES: Final[tuple[str, ...]] = ("next_session_after_date", "next_open_after_instant")

LEDGER_PREFIX: Final = "feasibility:"

Scalar = str | bool | int


class FeasibilityDesignError(ValueError):
    """The design file is not a valid design; the message names the key."""


@dataclass(frozen=True)
class SourceSpec:
    """One of the four closed sources: its table and the columns a filter may test.

    The filed columns are the table's own data columns; the four fact-table columns
    every fact carries (`known_at`, `ingested_at`, `source`, `provenance`) are not
    filterable, so a filter cannot reach a stamp.
    """

    name: str
    table: str
    issuer_column: str
    columns: tuple[str, ...]


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
    ),
    "form25": SourceSpec(
        "form25",
        "delistings",
        "security_id",
        ("security_id", "form", "class_title", "exchange", "filed_at", "effective_on"),
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
    """The `[shape]` table, copied from the card (units: basis points)."""

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
    return out


def _positive_int(key: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _fail(key, "must be a positive integer")
    return value


def _date(key: str, value: object) -> date:
    # A TOML datetime is a `datetime`, which is also a `date`: only a calendar date is a window end.
    if isinstance(value, datetime) or not isinstance(value, date):
        raise _fail(key, "must be a date (YYYY-MM-DD)")
    return value


def _filter_values(key: str, value: object) -> tuple[Scalar, ...]:
    items = value if isinstance(value, list) else [value]
    if not items:
        raise _fail(key, "membership list must not be empty")
    kinds = {type(v) for v in items}
    if not all(isinstance(v, (str, bool, int)) for v in items) or len(kinds) != 1:
        raise _fail(key, "must be a string, boolean or integer, or a list of one such type")
    return tuple(sorted(set(items), key=lambda v: (str(type(v)), v)))


def _filters(
    doc: Mapping[str, Any], spec: SourceSpec
) -> tuple[tuple[str, tuple[Scalar, ...]], ...]:
    raw = doc.get("filter", {})
    if not isinstance(raw, dict):
        raise _fail("filter", "must be a table")
    out = []
    for column in sorted(raw):
        if column not in spec.columns:
            raise _fail(
                f"filter.{column}",
                f"not a filed column of {spec.table} (columns: {', '.join(spec.columns)})",
            )
        out.append((column, _filter_values(f"filter.{column}", raw[column])))
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
        normalised["shape"] = {k: float(doc["shape"][k]) for k in _SHAPE_KEYS}
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


# --- the counts the reader fills ---


@dataclass(frozen=True)
class StoreFacts:
    """Facts about the counted file, for the header (the reader reads them from the copy)."""

    store_path: str
    size_bytes: int
    modified_at: datetime
    schema_version: int
    newest_ingest_run_id: str | None
    newest_ingest_at: datetime | None


@dataclass(frozen=True)
class TimestampCounts:
    """(a): rows matched, rows left out for a late `known_at`, events with no entry session."""

    rows_matched: int
    rows_after_window: int
    events_without_entry: int


@dataclass(frozen=True)
class CountCell:
    """(b), one cell: `band` is an index into the design's bands, `None` for "no bar"."""

    year: int
    band: int | None
    raw_events: int
    entry_sessions: int
    issuers: int


@dataclass(frozen=True)
class BandTotal:
    """(b), a band's totals and its effective count (`None` when it has no event or no bar)."""

    band: int | None
    raw_events: int
    entry_sessions: int
    issuers: int
    effective: EffectiveCount | None


@dataclass(frozen=True)
class DelisterCoverage:
    """(f): issuer-level only, over the window. Never per event or per hold."""

    delisted: int
    delisted_with_bars: int
    delisted_with_events: int
    delisted_with_events_and_bars: int


@dataclass(frozen=True)
class FeasibilityCounts:
    """Everything the block prints that is not the design: filled by the counts reader."""

    store: StoreFacts
    boundary: date
    timestamp: TimestampCounts
    cells: tuple[CountCell, ...]
    totals: tuple[BandTotal, ...]
    events_hold_past_window: int
    coverage: DelisterCoverage


# --- rendering ---


def _money(value: float) -> str:
    for scale, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if value >= scale:
            return f"${value / scale:g}{suffix}"
    return f"${value:g}"


def band_label(design: FeasibilityDesign, band: int | None) -> str:
    """A band's label from the design's edges; `None` is the "no bar" row."""
    if band is None:
        return "no bar"
    edges = design.edges
    if not 0 <= band < design.n_bands:
        raise ValueError(f"band {band} is outside 0..{design.n_bands - 1}")
    if band == 0:
        return f">= {_money(edges[0])}"
    if band == len(edges):
        return f"< {_money(edges[-1])}"
    return f"{_money(edges[band])} to {_money(edges[band - 1])}"


def _one_line(text: str) -> str:
    """Escape line breaks so no value can forge a block line."""
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


def _iso(value: datetime) -> str:
    return value.isoformat()


def _band_key(band: int | None) -> tuple[int, int]:
    return (1, 0) if band is None else (0, band)


def _shape_figures(inputs: ShapeInputs, n_eff: float) -> tuple[int | None, float]:
    """The one call into `shape`: `(n_min, pass chance)` for the card's numbers at `n_eff`.

    Everything else here is independent of T441's signatures; if its event form changes
    (for example a count of calendar quarters), only this function changes.
    """
    round_trip = shape.event_round_trip_bp(inputs.decision_cost_bps)
    n_min = shape.event_min_count(
        inputs.gross_bp,
        round_trip,
        inputs.sd_bp,
        floor_bp=inputs.floor_bp,
        t_floor=inputs.t_floor,
    )
    chance = shape.event_pass_probability(
        inputs.gross_bp,
        round_trip,
        inputs.sd_bp,
        n_eff,
        floor_bp=inputs.floor_bp,
        t_floor=inputs.t_floor,
    )
    return n_min, chance


def _rule_text(design: FeasibilityDesign) -> str:
    if design.rho is None:
        return "default clusters"
    assert design.rho_source is not None
    return f"rho {design.rho:g}, {_one_line(design.rho_source)}"


def _filter_text(design: FeasibilityDesign) -> str:
    if not design.filters:
        return "none"
    parts = []
    for column, values in design.filters:
        shown = ", ".join(json.dumps(v) for v in values)
        parts.append(f"{column} = {shown}" if len(values) == 1 else f"{column} in [{shown}]")
    return "; ".join(parts)


def _ordered(items: Sequence[BandTotal]) -> list[BandTotal]:
    return sorted(items, key=lambda t: _band_key(t.band))


def render_block(design: FeasibilityDesign, counts: FeasibilityCounts) -> str:
    """The block, as text: the same bytes for the same design and counts.

    Cells and totals are sorted here, so the order the reader produced them in does not
    matter. The text has no trailing newline and ends with the one ledger line.
    """
    store = counts.store
    ts = counts.timestamp
    cov = counts.coverage
    totals = _ordered(counts.totals)
    cells = sorted(counts.cells, key=lambda c: (c.year, *_band_key(c.band)))
    lines = [
        "feasibility block (counts only: no return is read)",
        f"design sha256: {design.sha256}",
        f"source: {design.source} ({design.spec.table}); filter: {_filter_text(design)}; "
        f"entry: {design.entry}; hold: {design.hold_sessions} sessions",
        f"window: {design.window_start} to {design.window_end}; "
        f"development boundary: {counts.boundary}",
        f"store: {_one_line(store.store_path)}; size {store.size_bytes} bytes; "
        f"modified {_iso(store.modified_at)}; schema version {store.schema_version}; "
        f"newest ingest {store.newest_ingest_run_id or 'none'}"
        f"{' at ' + _iso(store.newest_ingest_at) if store.newest_ingest_at else ''}",
        f"(a) timestamp: rows matched {ts.rows_matched}; "
        f"left out, known after the close of the window's last session {ts.rows_after_window}; "
        f"events with no entry session inside the window {ts.events_without_entry}",
        "(b) counts (raw events, distinct entry sessions, distinct issuers):",
    ]
    for cell in cells:
        lines.append(
            f"  {cell.year}  {band_label(design, cell.band)}: "
            f"{cell.raw_events}, {cell.entry_sessions}, {cell.issuers}"
        )
    lines.append("  totals per band:")
    for total in totals:
        lines.append(
            f"  {band_label(design, total.band)}: "
            f"{total.raw_events}, {total.entry_sessions}, {total.issuers}"
        )
    lines.append(
        f"  events whose hold runs past the window's end (counted, exit not read): "
        f"{counts.events_hold_past_window}"
    )
    lines.append(f"effective count, hold {design.hold_sessions} sessions ({_rule_text(design)}):")
    n_eff_by_band: dict[int | None, float] = {}
    for total in totals:
        eff = total.effective
        if eff is None:
            lines.append(f"  {band_label(design, total.band)}: none")
            continue
        n_eff_by_band[total.band] = eff.n_eff
        lines.append(
            f"  {band_label(design, total.band)}: N {eff.n}; n_eff {eff.n_eff:.1f}; "
            f"bounds rho 0 {eff.n_eff_rho0:.1f}, rho 1 {eff.n_eff_rho1:.1f}; "
            f"B {eff.same_date_factor:.3f}; O {eff.overlap_factor:.3f}"
        )
    lines += [
        "(f) delister coverage (by issuer, listing ends inside the window only):",
        f"  issuers delisted {cov.delisted}, with bars {cov.delisted_with_bars}",
        f"  of those with at least one event {cov.delisted_with_events}, "
        f"with bars {cov.delisted_with_events_and_bars}",
    ]
    if design.shape is not None:
        lines.append("shape (from the card; power and pass chance per band):")
        for band in sorted(n_eff_by_band, key=_band_key):
            n_min, chance = _shape_figures(design.shape, n_eff_by_band[band])
            n_min_text = "none (net at or under the floor)" if n_min is None else str(n_min)
            lines.append(
                f"  {band_label(design, band)}: n_eff {n_eff_by_band[band]:.1f} "
                f"against n_min {n_min_text}; pass chance {chance * 100:.2f}%"
            )
    lines.append(_ledger_line(design, counts, totals))
    return "\n".join(lines)


def _ledger_line(
    design: FeasibilityDesign, counts: FeasibilityCounts, totals: Sequence[BandTotal]
) -> str:
    ts = counts.timestamp
    cov = counts.coverage
    events = ", ".join(f"{band_label(design, t.band)} {t.raw_events}" for t in totals)
    n_eff = ", ".join(
        f"{band_label(design, t.band)} {t.effective.n_eff:.1f}"
        for t in totals
        if t.effective is not None
    )
    return (
        f"{LEDGER_PREFIX} (a) {ts.rows_matched} rows, {ts.rows_after_window} after the window, "
        f"{ts.events_without_entry} with no entry session; "
        f"(b) events per band: {events or 'none'}; "
        f"n_eff {n_eff or 'none'} ({_rule_text(design)}); "
        f"(f) delisters {cov.delisted}, with bars {cov.delisted_with_bars}; "
        f"with events {cov.delisted_with_events}, with bars {cov.delisted_with_events_and_bars}"
    )
