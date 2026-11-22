"""The feasibility design file and block (T453, #3742): parsing, hashing, refusals, rendering."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fixtures.research import feasibility_store

from tradepartner.research import feasibility_design as fd
from tradepartner.research import shape
from tradepartner.research.event_study.run import entry_session
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


# --- the fixture store builder (used by T454 and T455) ---


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


# --- the filter allow-lists, typed values and the event-only shape ---

ALLOW_LISTS = {
    "form4": {
        "transaction_code",
        "acquired_disposed",
        "is_director",
        "is_officer",
        "is_ten_percent_owner",
        "is_other",
        "ownership_form",
    },
    "statement_fact": {
        "fact_name",
        "xbrl_tag",
        "form",
        "period_days",
        "unit",
        "basis",
        "comparative",
    },
    "form25": {"form", "exchange"},
    "corporate_action": {"action_type"},
}
IDENTIFIERS = {
    "form4": (
        "issuer_cik",
        "filer_cik",
        "accession",
        "line_no",
        "transaction_date",
        "accepted_at",
        "shares",
        "price",
        "shares_owned_after",
        "security_title",
    ),
    "statement_fact": ("cik", "filing_accession", "period_end", "value"),
    "form25": ("security_id", "class_title", "filed_at", "effective_on"),
    "corporate_action": (
        "security_id",
        "source_action_id",
        "ex_date",
        "announced_at",
        "ratio_or_amount",
        "cancelled",
    ),
}


def _design_for(source: str, filter_line: str) -> str:
    return VALID.replace('source = "form4"', f'source = "{source}"').replace(
        '[filter]\ntransaction_code = "P"\nis_officer = true\n', f"[filter]\n{filter_line}\n"
    )


@pytest.mark.parametrize("source", sorted(ALLOW_LISTS))
def test_filter_allow_lists_exclude_identifiers_dates_and_stamps(source: str) -> None:
    assert set(fd.SOURCES[source].filter_columns) == ALLOW_LISTS[source]
    assert set(fd.SOURCES[source].filter_columns) <= set(fd.SOURCES[source].columns)
    for column, kind in fd.FILTER_KINDS[source].items():
        value = {str: '"A"', bool: "true", int: "1"}[kind]
        if (source, column) == ("statement_fact", "basis"):
            value = '"reported"'
        assert _parse(_design_for(source, f"{column} = {value}")).filters[0][0] == column
    for column in (*IDENTIFIERS[source], "known_at", "ingested_at", "provenance"):
        _refused(_design_for(source, f'{column} = "x"'), f"filter.{column}")


def test_shape_is_event_only_and_a_trial_form_or_key_is_unknown() -> None:
    for extra in ('form = "trial"', "borrow_bp_per_month = 1.25", "months = 84", "book = 1"):
        _refused(VALID + SHAPE_200 + extra + "\n", "shape." + extra.split(" ")[0])


def test_filter_values_are_typed_by_their_column() -> None:
    _refused(_design_for("form4", "transaction_code = 1"), "filter.transaction_code")
    _refused(_design_for("form4", "is_officer = 1"), "filter.is_officer")
    _refused(_design_for("form4", 'is_officer = "true"'), "filter.is_officer")
    _refused(_design_for("form4", 'acquired_disposed = "X"'), "filter.acquired_disposed")
    _refused(_design_for("statement_fact", 'period_days = "90"'), "filter.period_days")
    _refused(_design_for("statement_fact", "period_days = true"), "filter.period_days")
    _refused(_design_for("statement_fact", 'basis = "guessed"'), "filter.basis")
    assert _parse(_design_for("form4", 'transaction_code = ["P", "S"]')).filters
    assert _parse(_design_for("statement_fact", "period_days = 90")).filters


def test_negative_zero_hashes_like_zero() -> None:
    a = _parse(VALID + SHAPE_200.replace("floor_bp = 30", "floor_bp = 0"))
    b = _parse(VALID + SHAPE_200.replace("floor_bp = 30", "floor_bp = -0.0"))
    assert a.sha256 == b.sha256


def _mid_band_events(built: feasibility_store.FeasibilityStore) -> list[tuple[str, int]]:
    """(issuer, entry session position) of the matching 2018 Form 4 events of the mid band."""
    position = {s: i for i, s in enumerate(built.sessions)}
    return [
        (issuer, position[entry_session(accepted, "next_session_after_date")])
        for issuer, accepted, code, officer, _filer, _why in feasibility_store.FORM4_EVENTS
        if issuer in {"BBB", "DDD", "FFF"} and code == "P" and officer and accepted.year == 2018
    ]


def test_the_mid_band_tells_the_default_from_rho_zero_from_a_cited_rho(tmp_path: Path) -> None:
    built = feasibility_store.build_feasibility_store(tmp_path / "f.duckdb")
    events = _mid_band_events(built)
    assert len(events) == 5
    default = shape.effective_count(events, 20)
    rho0 = shape.effective_count(events, 20, rho=0.0)
    cited = shape.effective_count(events, 20, rho=0.3)
    # Events: A = (BBB, 3-13), B = (DDD, 3-13), C = (BBB, 5-14), D = (FFF, 5-17), E = (DDD, 8-21).
    # Only A-B (one session, o = 1) and C-D (3 sessions apart, o = 17/20 = 0.85) are pairs of
    # different issuers whose holds overlap; no two events of one issuer overlap. N = 5.
    #   default: 5 + 2 * (1 + 0.05 * 0.85)  = 7.085  -> 25 / 7.085 = 3.5286
    #   rho 0:   5                          = 5      -> 25 / 5     = 5.0
    #   rho 0.3: 5 + 2 * 0.3 * (1 + 0.85)   = 6.11   -> 25 / 6.11  = 4.0917
    assert default.n_eff == pytest.approx(25 / 7.085)
    assert rho0.n_eff == pytest.approx(5.0)
    assert cited.n_eff == pytest.approx(25 / 6.11)
    assert len({round(v, 6) for v in (default.n_eff, rho0.n_eff, cited.n_eff)}) == 3
    assert default.same_date_factor > 1.0  # B > 1: two events on one session


def test_late_restated_bars_would_move_aaa_across_the_band_edge(tmp_path: Path) -> None:
    built = feasibility_store.build_feasibility_store(tmp_path / "f.duckdb")
    window_close = datetime(2019, 3, 29, 20, tzinfo=UTC)
    entry_no = built.sessions.index(date(2018, 3, 13))
    lookback = built.sessions[entry_no - 20 : entry_no]
    conn = built.connect()
    try:

        def median(cutoff: datetime) -> float:
            row = conn.execute(
                "SELECT median(dv) FROM (SELECT arg_max(close * volume, known_at) AS dv "
                "FROM prices_daily WHERE security_id = 'SEC_AAA' "
                "AND session >= ? AND session <= ? AND known_at <= ? GROUP BY session)",
                [lookback[0], lookback[-1], cutoff],
            ).fetchone()
            assert row is not None
            return float(row[0])

        assert median(window_close) > 200e6  # as of the window's end: top band
        assert median(feasibility_store.LATE_RESTATED_AT) < 50e6  # read late: low band
        late = conn.execute(
            "SELECT count(*) FROM prices_daily WHERE known_at > ? AND known_at < ?",
            [window_close, datetime(2019, 12, 31, tzinfo=UTC)],
        ).fetchone()
        assert late is not None and late[0] >= 12
    finally:
        conn.close()


def test_revisions_cancellations_and_a_copy_with_no_boundary(tmp_path: Path) -> None:
    built = feasibility_store.build_feasibility_store(tmp_path / "f.duckdb")
    conn = built.connect()
    try:
        cancelled = conn.execute(
            "SELECT security_id, known_at FROM corporate_actions WHERE cancelled ORDER BY known_at"
        ).fetchall()
        assert [c[0] for c in cancelled] == ["SEC_AAA", "SEC_CCC"]
        assert cancelled[0][1] < datetime(2019, 3, 29, tzinfo=UTC) < cancelled[1][1]
        revised = conn.execute(
            "SELECT security_id, count(*) FROM delistings GROUP BY 1 HAVING count(*) > 1 ORDER BY 1"
        ).fetchall()
        assert [r[0] for r in revised] == ["SEC_DDD", "SEC_FFF"]
        filed_inside = conn.execute(
            "SELECT count(*) FROM delistings WHERE security_id = 'SEC_CCC' "
            "AND filed_at <= ? AND effective_on > ?",
            [datetime(2019, 3, 29, 23, tzinfo=UTC), date(2019, 3, 29)],
        ).fetchone()
        assert filed_inside == (1,)
    finally:
        conn.close()
    bare = feasibility_store.build_feasibility_store(tmp_path / "bare.duckdb", with_boundary=False)
    assert bare.boundary is None
    conn = bare.connect()
    try:
        assert registry.boundary_date(conn) is None
    finally:
        conn.close()


def test_a_filing_at_1345_utc_enters_by_the_new_york_offset() -> None:
    january = datetime(2018, 1, 16, 13, 45, tzinfo=UTC)  # 8:45 EST: before the open
    march = datetime(2018, 3, 14, 13, 45, tzinfo=UTC)  # 9:45 EDT: after the open
    assert entry_session(january, "next_open_after_instant") == date(2018, 1, 16)
    assert entry_session(march, "next_open_after_instant") == date(2018, 3, 15)
