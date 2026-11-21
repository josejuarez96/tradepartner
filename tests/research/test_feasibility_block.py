"""The feasibility block (T453, #3742): the renderer and the counts object."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tradepartner.research import feasibility_block as fb
from tradepartner.research import feasibility_design as fd
from tradepartner.research import shape
from tradepartner.research.shape import EffectiveCount

VALID = """
source = "form4"
entry = "next_session_after_date"
hold_sessions = 20
[filter]
transaction_code = "P"
is_officer = true
[window]
start = 2018-01-02
end = 2019-03-29
[band]
lookback_sessions = 20
edges = [200000000, 50000000]
"""


SHAPE_200 = (
    "[shape]\ngross_bp = 200\nsd_bp = 866\ndecision_cost_bps = 50\nfloor_bp = 30\nt_floor = 1\n"
)
RHO = "rho = 0.4\n"
RHO_SRC = 'rho_source = "a paper"\n'
RHO_BOTH = RHO + RHO_SRC


def _parse(text: str) -> fd.FeasibilityDesign:
    return fd.parse_design_text(text)


def _effective(n_eff: float, n: int = 10) -> EffectiveCount:
    return EffectiveCount(
        n=n,
        n_eff=n_eff,
        n_eff_rho0=n_eff * 2,
        n_eff_rho1=n_eff / 2,
        same_date_factor=1.25,
        overlap_factor=1.5,
        rho=None,
    )


def _counts() -> fb.FeasibilityCounts:
    return fb.FeasibilityCounts(
        store=fb.StoreFacts(
            store_path="/tmp/copy.duckdb",
            size_bytes=4096,
            modified_at=datetime(2026, 11, 20, 9, 30, tzinfo=UTC),
            schema_version=49,
            newest_ingest_run_id="run-7",
            newest_ingest_at=datetime(2026, 11, 19, 22, 0, tzinfo=UTC),
        ),
        boundary=date(2019, 12, 31),
        timestamp=fb.TimestampCounts(rows_matched=14, rows_after_window=2, events_without_entry=1),
        cells=(
            fb.CountCell(2019, None, 1, 1, 1),
            fb.CountCell(2018, 1, 3, 2, 3),
            fb.CountCell(2018, 0, 4, 3, 2),
        ),
        totals=(
            fb.BandTotal(None, 1, 1, 1, None),
            fb.BandTotal(1, 3, 2, 3, _effective(2.5, 3)),
            fb.BandTotal(0, 4, 3, 2, _effective(3.5, 4)),
        ),
        events_hold_past_window=2,
        coverage=fb.DelisterCoverage(3, 2, 1, 1),
    )


def test_band_labels_come_from_the_edges() -> None:
    design = _parse(VALID)
    assert [fb.band_label(design, b) for b in (0, 1, 2, None)] == [
        ">= $200M",
        "$50M to $200M",
        "< $50M",
        "no bar",
    ]
    with pytest.raises(ValueError):
        fb.band_label(design, 3)


def test_block_is_byte_identical_and_order_independent() -> None:
    design = _parse(VALID)
    counts = _counts()
    reordered = fb.FeasibilityCounts(
        **{
            **counts.__dict__,
            "cells": tuple(reversed(counts.cells)),
            "totals": tuple(reversed(counts.totals)),
        }
    )
    block = fb.render_block(design, counts)
    assert fb.render_block(design, counts) == block
    assert fb.render_block(design, reordered) == block


def test_block_ends_with_exactly_one_ledger_line() -> None:
    block = fb.render_block(_parse(VALID), _counts())
    lines = block.split("\n")
    assert [i for i, ln in enumerate(lines) if ln.startswith("feasibility:")] == [len(lines) - 1]
    assert not block.endswith("\n")
    ledger = lines[-1]
    assert ledger.startswith("feasibility: (a) ")
    assert "; (b) " in ledger and "; n_eff " in ledger and "(default clusters)" in ledger
    assert ledger.rindex("; (f) ") > ledger.index("; n_eff ")


def test_header_carries_hash_and_the_four_store_facts() -> None:
    design = _parse(VALID)
    block = fb.render_block(design, _counts())
    assert f"design sha256: {design.sha256}" in block
    for fact in ("/tmp/copy.duckdb", "size 4096 bytes", "modified 2026-11-20T09:30:00+00:00"):
        assert fact in block
    assert "newest ingest run-7 at 2026-11-19T22:00:00+00:00" in block
    assert "development boundary: 2019-12-31" in block


def test_ledger_names_the_cited_rho_and_its_source() -> None:
    design = _parse('rho = 0.3\nrho_source = "a paper"\n' + VALID)
    assert "(rho 0.3, a paper)" in fb.render_block(design, _counts()).split("\n")[-1]


def test_a_value_cannot_forge_a_ledger_line() -> None:
    counts = _counts()
    forged = fb.FeasibilityCounts(
        **{
            **counts.__dict__,
            "store": fb.StoreFacts(**{**counts.store.__dict__, "store_path": "a\nfeasibility: x"}),
        }
    )
    lines = fb.render_block(_parse(VALID), forged).split("\n")
    assert sum(ln.startswith("feasibility:") for ln in lines) == 1


def test_no_shape_table_prints_no_shape_lines() -> None:
    assert "pass chance" not in fb.render_block(_parse(VALID), _counts())


def test_shape_table_prints_the_power_line_and_pass_chance_from_t441() -> None:
    text = VALID + SHAPE_200
    design = _parse(text)
    block = fb.render_block(design, _counts())
    n_min = shape.event_min_count(200.0, 100.0, 866.0, floor_bp=30.0, t_floor=1.0)
    chance = shape.event_pass_probability(200.0, 100.0, 866.0, 3.5, floor_bp=30.0, t_floor=1.0)
    assert f">= $200M: n_eff 3.5 against n_min {n_min}; pass chance {chance * 100:.2f}%" in block
    assert "$50M to $200M: n_eff 2.5" in block
    assert "no bar: n_eff" not in block


def test_shape_with_net_at_the_floor_says_none() -> None:
    text = VALID + SHAPE_200.replace("200", "120", 1)
    assert "against n_min none (net at or under the floor)" in fb.render_block(
        _parse(text), _counts()
    )


def test_module_reads_no_clock_store_or_config() -> None:
    source = Path(fd.__file__).read_text(encoding="utf-8")
    for needle in (
        "duckdb",
        "datetime.now",
        "date.today",
        "tradepartner.config",
        "tradepartner.store",
    ):
        assert needle not in source
