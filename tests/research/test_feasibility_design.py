"""The feasibility design file and block (T442, #3742): parsing, hashing, refusals, rendering."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fixtures.research import feasibility_store

from tradepartner.research import feasibility_design as fd
from tradepartner.research import shape
from tradepartner.research.event_study.run import entry_session
from tradepartner.research.shape import EffectiveCount
from tradepartner.store import registry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "research" / "feasibility"
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


def _refused(text: str, key: str) -> None:
    with pytest.raises(fd.FeasibilityDesignError) as err:
        _parse(text)
    assert key in str(err.value)


# --- the fixtures and the hash ---


@pytest.mark.parametrize("source", sorted(fd.SOURCES))
def test_one_valid_fixture_design_per_source(source: str) -> None:
    design = fd.parse_design_file(FIXTURES / f"{source}.toml")
    assert design.source == source
    assert re.fullmatch(r"[0-9a-f]{64}", design.sha256)


def test_hash_ignores_key_order_whitespace_comments_and_number_spelling() -> None:
    reordered = """
entry = "next_session_after_date"
source = "form4"
hold_sessions = 20
# a comment
[band]
edges = [200000000.0,    50000000]
lookback_sessions = 20
[window]
end = 2019-03-29
start = 2018-01-02
[filter]
is_officer = true
transaction_code = ["P"]
"""
    assert _parse(reordered).sha256 == _parse(VALID).sha256


def test_hash_changes_with_the_design() -> None:
    assert _parse(VALID.replace("= 20\n[filter]", "= 21\n[filter]")).sha256 != _parse(VALID).sha256


def test_membership_order_does_not_change_the_hash() -> None:
    a = _parse(VALID.replace('"P"', '["P", "S"]'))
    b = _parse(VALID.replace('"P"', '["S", "P"]'))
    assert a.sha256 == b.sha256
    assert a.filters[1][1] == ("P", "S") or a.filters[0][1] == ("P", "S")


def test_parsed_values() -> None:
    design = fd.parse_design_file(FIXTURES / "form4.toml")
    assert design.window_start == date(2018, 1, 2)
    assert design.window_end == date(2019, 3, 29)
    assert design.edges == (200_000_000.0, 50_000_000.0)
    assert design.n_bands == 3
    assert design.rho is None
    assert design.shape is not None and design.shape.sd_bp == 866.0
    assert fd.parse_design_file(FIXTURES / "statement_fact.toml").rho == 0.3


def test_the_four_sources_and_not_filing_events() -> None:
    assert set(fd.SOURCES) == {"form4", "statement_fact", "form25", "corporate_action"}
    assert {s.table for s in fd.SOURCES.values()} == {
        "sec_form4_transactions",
        "statement_facts",
        "delistings",
        "corporate_actions",
    }
    assert "filing" + "_events" not in Path(fd.__file__).read_text(encoding="utf-8")


def test_entry_rules_are_the_runners() -> None:
    stamp = datetime(2018, 7, 3, 21, tzinfo=UTC)
    for rule in fd.ENTRY_RULES:
        entry_session(stamp, rule)  # raises on a rule the runner does not know


# --- refusals, each naming the key ---


def test_unknown_top_level_and_nested_keys_are_refused() -> None:
    _refused("colour = 1\n" + VALID, "colour")
    _refused(VALID.replace("[band]", "[band]\nextra = 1"), "band.extra")
    _refused(VALID.replace("[window]", "[window]\nextra = 1"), "window.extra")
    _refused(VALID + "[shape]\nbogus = 1\n", "shape.bogus")


@pytest.mark.parametrize("bad", ["filing_events", "prices_daily", "form5", ""])
def test_source_outside_the_four_is_refused(bad: str) -> None:
    _refused(VALID.replace('"form4"', f'"{bad}"'), "source")


def test_missing_source_is_refused() -> None:
    _refused(VALID.replace('source = "form4"\n', ""), "source")


def test_filter_on_a_column_outside_the_sources_filed_columns_is_refused() -> None:
    _refused(VALID.replace("[window]", "action_type = 1\n[window]"), "filter.action_type")
    # a column of another table, and a stamp column, are not filed columns of form4
    _refused(VALID.replace("[window]", "known_at = 1\n[window]"), "filter.known_at")
    _refused(VALID.replace("[window]", "close = 1\n[window]"), "filter.close")


def test_filter_value_must_be_a_scalar_or_a_uniform_list() -> None:
    _refused(VALID.replace('"P"', '["P", 1]'), "filter.transaction_code")
    _refused(VALID.replace('"P"', "[]"), "filter.transaction_code")
    _refused(VALID.replace('"P"', "1.5"), "filter.transaction_code")


def test_rho_needs_a_rho_source_and_the_reverse() -> None:
    _refused(RHO + VALID, "rho_source")
    _refused(RHO_SRC + VALID, "rho")
    _refused('rho = 1.4\nrho_source = "a paper"\n' + VALID, "rho")
    _refused('rho = 0.4\nrho_source = "  "\n' + VALID, "rho_source")
    assert _parse(RHO_BOTH + VALID).rho_source == "a paper"


def test_missing_band_or_hold_is_refused() -> None:
    _refused(VALID.replace("hold_sessions = 20\n", ""), "hold_sessions")
    _refused(
        VALID.replace("[band]\nlookback_sessions = 20\n", "[band]\n"), "band.lookback_sessions"
    )
    _refused(VALID.replace("edges = [200000000, 50000000]\n", ""), "band.edges")
    _refused(VALID.split("[band]")[0], "band")


def test_band_edges_must_be_positive_and_descending() -> None:
    _refused(VALID.replace("[200000000, 50000000]", "[50000000, 200000000]"), "band.edges")
    _refused(VALID.replace("[200000000, 50000000]", "[]"), "band.edges")
    _refused(VALID.replace("[200000000, 50000000]", "[0]"), "band.edges")


def test_window_that_ends_before_it_starts_is_refused() -> None:
    _refused(VALID.replace("end = 2019-03-29", "end = 2017-12-29"), "window.end")
    assert _parse(VALID.replace("end = 2019-03-29", "end = 2018-01-02")).window_end == date(
        2018, 1, 2
    )


def test_window_and_hold_types_are_checked() -> None:
    _refused(VALID.replace("start = 2018-01-02", 'start = "2018-01-02"'), "window.start")
    _refused(VALID.replace("start = 2018-01-02", "start = 2018-01-02T10:00:00Z"), "window.start")
    _refused(VALID.replace("hold_sessions = 20", "hold_sessions = 0"), "hold_sessions")
    _refused(VALID.replace("hold_sessions = 20", "hold_sessions = true"), "hold_sessions")
    _refused(VALID.replace("next_session_after_date", "same_day"), "entry")


def test_shape_table_needs_all_five_keys() -> None:
    full = SHAPE_200
    assert _parse(VALID + full).shape is not None
    _refused(VALID + full.replace("floor_bp = 30\n", ""), "shape.floor_bp")
    _refused(VALID + full.replace("sd_bp = 866", "sd_bp = 0"), "shape.sd_bp")


def test_invalid_toml_and_missing_file_are_refused(tmp_path: Path) -> None:
    with pytest.raises(fd.FeasibilityDesignError):
        _parse("source = ")
    with pytest.raises(fd.FeasibilityDesignError):
        fd.parse_design_file(tmp_path / "absent.toml")


# --- the block ---


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


def _counts() -> fd.FeasibilityCounts:
    return fd.FeasibilityCounts(
        store=fd.StoreFacts(
            store_path="/tmp/copy.duckdb",
            size_bytes=4096,
            modified_at=datetime(2026, 11, 20, 9, 30, tzinfo=UTC),
            schema_version=49,
            newest_ingest_run_id="run-7",
            newest_ingest_at=datetime(2026, 11, 19, 22, 0, tzinfo=UTC),
        ),
        boundary=date(2019, 12, 31),
        timestamp=fd.TimestampCounts(rows_matched=14, rows_after_window=2, events_without_entry=1),
        cells=(
            fd.CountCell(2019, None, 1, 1, 1),
            fd.CountCell(2018, 1, 3, 2, 3),
            fd.CountCell(2018, 0, 4, 3, 2),
        ),
        totals=(
            fd.BandTotal(None, 1, 1, 1, None),
            fd.BandTotal(1, 3, 2, 3, _effective(2.5, 3)),
            fd.BandTotal(0, 4, 3, 2, _effective(3.5, 4)),
        ),
        events_hold_past_window=2,
        coverage=fd.DelisterCoverage(3, 2, 1, 1),
    )


def test_band_labels_come_from_the_edges() -> None:
    design = _parse(VALID)
    assert [fd.band_label(design, b) for b in (0, 1, 2, None)] == [
        ">= $200M",
        "$50M to $200M",
        "< $50M",
        "no bar",
    ]
    with pytest.raises(ValueError):
        fd.band_label(design, 3)


def test_block_is_byte_identical_and_order_independent() -> None:
    design = _parse(VALID)
    counts = _counts()
    reordered = fd.FeasibilityCounts(
        **{
            **counts.__dict__,
            "cells": tuple(reversed(counts.cells)),
            "totals": tuple(reversed(counts.totals)),
        }
    )
    block = fd.render_block(design, counts)
    assert fd.render_block(design, counts) == block
    assert fd.render_block(design, reordered) == block


def test_block_ends_with_exactly_one_ledger_line() -> None:
    block = fd.render_block(_parse(VALID), _counts())
    lines = block.split("\n")
    assert [i for i, ln in enumerate(lines) if ln.startswith("feasibility:")] == [len(lines) - 1]
    assert not block.endswith("\n")
    ledger = lines[-1]
    assert ledger.startswith("feasibility: (a) ")
    assert "; (b) " in ledger and "; n_eff " in ledger and "(default clusters)" in ledger
    assert ledger.rindex("; (f) ") > ledger.index("; n_eff ")


def test_header_carries_hash_and_the_four_store_facts() -> None:
    design = _parse(VALID)
    block = fd.render_block(design, _counts())
    assert f"design sha256: {design.sha256}" in block
    for fact in ("/tmp/copy.duckdb", "size 4096 bytes", "modified 2026-11-20T09:30:00+00:00"):
        assert fact in block
    assert "newest ingest run-7 at 2026-11-19T22:00:00+00:00" in block
    assert "development boundary: 2019-12-31" in block


def test_ledger_names_the_cited_rho_and_its_source() -> None:
    design = _parse('rho = 0.3\nrho_source = "a paper"\n' + VALID)
    assert "(rho 0.3, a paper)" in fd.render_block(design, _counts()).split("\n")[-1]


def test_a_value_cannot_forge_a_ledger_line() -> None:
    counts = _counts()
    forged = fd.FeasibilityCounts(
        **{
            **counts.__dict__,
            "store": fd.StoreFacts(**{**counts.store.__dict__, "store_path": "a\nfeasibility: x"}),
        }
    )
    lines = fd.render_block(_parse(VALID), forged).split("\n")
    assert sum(ln.startswith("feasibility:") for ln in lines) == 1


def test_no_shape_table_prints_no_shape_lines() -> None:
    assert "pass chance" not in fd.render_block(_parse(VALID), _counts())


def test_shape_table_prints_the_power_line_and_pass_chance_from_t441() -> None:
    text = VALID + SHAPE_200
    design = _parse(text)
    block = fd.render_block(design, _counts())
    n_min = shape.event_min_count(200.0, 100.0, 866.0, floor_bp=30.0, t_floor=1.0)
    chance = shape.event_pass_probability(200.0, 100.0, 866.0, 3.5, floor_bp=30.0, t_floor=1.0)
    assert f">= $200M: n_eff 3.5 against n_min {n_min}; pass chance {chance * 100:.2f}%" in block
    assert "$50M to $200M: n_eff 2.5" in block
    assert "no bar: n_eff" not in block


def test_shape_with_net_at_the_floor_says_none() -> None:
    text = VALID + SHAPE_200.replace("200", "120", 1)
    assert "against n_min none (net at or under the floor)" in fd.render_block(
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


# --- the fixture store builder (used by T443 and T444) ---


def _digest(path: Path) -> list[tuple[object, ...]]:
    store = feasibility_store.FeasibilityStore(
        path, date(1, 1, 1), date(1, 1, 1), date(1, 1, 1), (), {}
    )
    conn = store.connect()
    try:
        out: list[tuple[object, ...]] = []
        for table in ("prices_daily", "sec_form4_transactions", "delistings"):
            out += conn.execute(f"SELECT * FROM {table} ORDER BY ALL").fetchall()
        return out
    finally:
        conn.close()


def test_fixture_store_builds_with_every_planted_case(tmp_path: Path) -> None:
    built = feasibility_store.build_feasibility_store(tmp_path / "f.duckdb")
    assert built.path.is_file()
    assert all(count > 0 for count in built.rows.values())
    conn = built.connect()
    try:
        # the four sources are the four tables, each with rows after the window and the boundary
        for table in feasibility_store.SOURCE_TABLES.values():
            late = conn.execute(
                f"SELECT count(*) FROM {table} WHERE known_at > ?",
                [datetime(2019, 3, 29, 23, tzinfo=UTC)],
            ).fetchone()
            assert late is not None and late[0] >= 1, table
        assert registry.boundary_date(conn) == date(2019, 12, 31)
        # a row known after the boundary exists, in prices too
        after_boundary = conn.execute(
            "SELECT count(*) FROM prices_daily WHERE known_at > ?",
            [datetime(2019, 12, 31, 23, tzinfo=UTC)],
        ).fetchone()
        assert after_boundary == (1,)
        # no bar: EEE; delisted inside the hold: DDD's bars end before its hold would
        bars = dict(
            conn.execute("SELECT security_id, count(*) FROM prices_daily GROUP BY 1").fetchall()
        )
        assert "SEC_EEE" not in bars and bars["SEC_AAA"] > bars["SEC_DDD"] > 0
        # a bar outside every lookback window: the last sessions of the year, after all holds
        assert built.sessions[-1] == date(2019, 4, 5)
        # non-constant closes for every issuer with bars: a close-to-close read would show
        for security in bars:
            distinct = conn.execute(
                "SELECT count(DISTINCT close) FROM prices_daily WHERE security_id = ?", [security]
            ).fetchone()
            assert distinct is not None and distinct[0] > 5
        # the bands: the median close * volume of each issuer falls in the planned band
        medians = dict(
            conn.execute(
                "SELECT security_id, median(close * volume) FROM prices_daily "
                "WHERE known_at < ? GROUP BY 1",
                [datetime(2019, 12, 31, tzinfo=UTC)],
            ).fetchall()
        )
        assert medians["SEC_AAA"] > 200e6 > medians["SEC_BBB"] > 50e6 > medians["SEC_CCC"]
    finally:
        conn.close()


def test_fixture_store_is_deterministic_and_fixture_designs_fit_it(tmp_path: Path) -> None:
    a = feasibility_store.build_feasibility_store(tmp_path / "a.duckdb")
    b = feasibility_store.build_feasibility_store(tmp_path / "b.duckdb")
    assert _digest(a.path) == _digest(b.path)
    assert a.rows == b.rows
    for source in fd.SOURCES:
        design = fd.parse_design_file(feasibility_store.DESIGN_DIR / f"{source}.toml")
        assert design.window_end <= a.boundary
        assert (design.window_start, design.window_end) == (a.window_start, a.window_end)
