"""The feasibility block: the counts object the reader fills and the renderer (T453, #3702).

`render_block` is a pure function from a `FeasibilityDesign` and a `FeasibilityCounts`
to the block's text: the same bytes for the same inputs, ending with the one ledger
line. No clock, no store and no config is read here. The arithmetic of the `[shape]`
lines is `tradepartner.research.shape`'s, reached through one adapter (`_shape_figures`).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from tradepartner.research import shape
from tradepartner.research.feasibility_design import FeasibilityDesign, ShapeInputs
from tradepartner.research.shape import EffectiveCount

__all__ = [
    "BandTotal",
    "CountCell",
    "DelisterCoverage",
    "FeasibilityCounts",
    "StoreFacts",
    "TimestampCounts",
    "band_label",
    "render_block",
]

LEDGER_PREFIX: Final = "feasibility:"


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

    Everything else here is independent of T452's signatures; if its event form changes
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
