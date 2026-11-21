"""The feasibility counts reader (T454, #3743): the spec amendment's hard rules on the fixture.

Each test names the rule it pins. Header facts are fixed (`FACTS`) so that a block is
byte-comparable across a store and its altered copy.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fixtures.research import feasibility_store as fx

from tradepartner.calendar import last_completed_session
from tradepartner.research import feasibility as feas
from tradepartner.research import feasibility_block as fb
from tradepartner.research import feasibility_design as fd
from tradepartner.store.db import insert_row

FACTS = fb.StoreFacts("copy.duckdb", 1, datetime(2026, 1, 1, tzinfo=UTC), 49, "run-1", None)
SRC = Path(feas.__file__)
PRICE_COLUMNS = {"security_id", "session", "close", "volume", "known_at"}


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> fx.FeasibilityStore:
    return fx.build_feasibility_store(tmp_path_factory.mktemp("feas") / "store.duckdb")


def _design(name: str = "form4", extra: str = "") -> fd.FeasibilityDesign:
    text = (fx.DESIGN_DIR / f"{name}.toml").read_text()
    return fd.parse_design_text(extra + text)


def _counts(path: Path, design: fd.FeasibilityDesign) -> fb.FeasibilityCounts:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        return feas.count_feasibility(conn, design, store=FACTS)
    finally:
        conn.close()


def _block(path: Path, design: fd.FeasibilityDesign) -> str:
    return fb.render_block(design, _counts(path, design))


def _copy(built: fx.FeasibilityStore, tmp_path: Path) -> Path:
    target = tmp_path / "copy.duckdb"
    shutil.copy2(built.path, target)
    return target


def _edit(path: Path, *statements: str) -> None:
    conn = duckdb.connect(str(path))
    try:
        for statement in statements:
            conn.execute(statement)
    finally:
        conn.close()


class Recorder:
    """A connection wrapper that records every SQL string and forwards it unchanged."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn
        self.sql: list[str] = []

    def execute(self, sql: str, params: Any = None) -> Any:
        self.sql.append(sql)
        return self.conn.execute(sql, params) if params is not None else self.conn.execute(sql)


def _recorded(path: Path, design: fd.FeasibilityDesign) -> list[str]:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        rec = Recorder(conn)
        feas.count_feasibility(rec, design, store=FACTS)  # type: ignore[arg-type]
        return rec.sql
    finally:
        conn.close()


# --- Read-only ---


def test_read_only_hard_rule_file_unchanged_and_no_write_possible(
    built: fx.FeasibilityStore,
) -> None:
    before = hashlib.sha256(built.path.read_bytes()).hexdigest()
    conn = built.connect(read_only=True)
    try:
        counts = feas.count_feasibility(conn, _design())  # reads the header facts itself
        with pytest.raises(duckdb.Error):
            conn.execute("DELETE FROM prices_daily")
    finally:
        conn.close()
    assert hashlib.sha256(built.path.read_bytes()).hexdigest() == before
    assert Path(counts.store.store_path).resolve() == built.path.resolve()
    assert counts.store.size_bytes == built.path.stat().st_size
    assert counts.store.newest_ingest_run_id == "feasibility-fixture-1"
    assert counts.store.schema_version == 49


# --- Levels only, from five columns ---


def test_prices_daily_is_read_through_exactly_five_columns(built: fx.FeasibilityStore) -> None:
    seen = 0
    for name in ("form4", "form25", "statement_fact", "corporate_action"):
        for sql in _recorded(built.path, _design(name)):
            if "prices_daily" not in sql:
                continue
            seen += 1
            select = re.match(r"\s*SELECT\s+(DISTINCT\s+)?(.*?)\s+FROM\s", sql, re.S)
            assert select is not None
            columns = {c.strip() for c in select.group(2).split(",")}
            assert columns <= PRICE_COLUMNS
            assert "*" not in sql
            assert not re.search(r"\b(open|high|low|adj\w*)\b", sql, re.I)
    assert seen >= 4
    assert not re.search(r"SELECT\s+\*|\.\*", SRC.read_text())


def test_open_high_low_are_never_read(built: fx.FeasibilityStore, tmp_path: Path) -> None:
    design = _design()
    base = _block(built.path, design)
    path = _copy(built, tmp_path)
    _edit(path, "UPDATE prices_daily SET open = 1.0, high = 999999.0, low = 0.5")
    assert _block(path, design) == base


# --- No return and no price change ---

_FACTOR = "(1 << CAST(hash(security_id, session) % 4 AS INTEGER))"


def _factor_store(built: fx.FeasibilityStore, tmp_path: Path) -> Path:
    """Every session's close divided by its own power of two, volume multiplied by it.

    `close * volume` is unchanged bit for bit (the factors are powers of two); every
    close-to-close change differs.
    """
    path = _copy(built, tmp_path)
    _edit(
        path,
        f"UPDATE prices_daily SET close = close / {_FACTOR}, volume = volume * {_FACTOR}",
    )
    return path


def test_factor_case_changes_every_price_change_and_no_level(
    built: fx.FeasibilityStore, tmp_path: Path
) -> None:
    path = _factor_store(built, tmp_path)
    q = "SELECT security_id, session, close, volume FROM prices_daily ORDER BY 1, 2"
    a = built.connect().execute(q).fetchall()
    b = duckdb.connect(str(path), read_only=True).execute(q).fetchall()
    assert [r[2] * r[3] for r in a] == [r[2] * r[3] for r in b]
    changed = [x[2] / y[2] for x, y in zip(a, b, strict=True)]
    assert len({round(c, 9) for c in changed}) > 3
    assert [r[2] for r in a] != [r[2] for r in b]


@pytest.mark.parametrize("name", ["form4", "form25", "statement_fact", "corporate_action"])
def test_factor_case_block_is_byte_identical(
    built: fx.FeasibilityStore, tmp_path: Path, name: str
) -> None:
    design = _design(name)
    assert _block(_factor_store(built, tmp_path), design) == _block(built.path, design)


def test_mutant_reader_with_one_close_change_fails_the_factor_case(
    built: fx.FeasibilityStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fixture is sensitive: a reader that reads one close-to-close change is caught."""
    design = _design()
    factor = _factor_store(built, tmp_path)
    # An "outlier filter": drop a bar whose close moved more than 5% from the one before.
    mutant_sql = (
        "SELECT security_id, session, close, volume, known_at FROM ("
        "SELECT security_id, session, close, volume, known_at, "
        "close / lag(close) OVER w AS step FROM prices_daily "
        "WHERE list_contains(?, security_id) AND session >= ? AND session <= ? AND known_at <= ? "
        "WINDOW w AS (PARTITION BY security_id ORDER BY session)"
        ") WHERE step IS NULL OR abs(step - 1) <= 0.05"
    )
    # The honest reader passes on the factor store ...
    assert _block(factor, design) == _block(built.path, design)
    # ... and the mutant does not.
    monkeypatch.setattr(feas, "_BARS_SQL", mutant_sql)
    assert _block(factor, design) != _block(built.path, design)


def _lookback_keep(
    design: fd.FeasibilityDesign, calendar: tuple[date, ...]
) -> set[tuple[str, date]]:
    """(security, session) pairs inside any event's lookback window, worked out here."""
    keep: set[tuple[str, date]] = set()
    sessions = list(calendar)
    for issuer, accepted, code, officer, _filer, _why in fx.FORM4_EVENTS:
        if (
            code != "P"
            or not officer
            or not design.window_start <= accepted.date() <= design.window_end
        ):
            continue
        done = sessions.index(last_completed_session(accepted))
        for s in sessions[max(0, done - design.lookback_sessions + 1) : done + 1]:
            keep.add((f"SEC_{issuer}", s))
    return keep


def test_overwrite_outside_every_lookback_window_is_byte_identical(
    built: fx.FeasibilityStore, tmp_path: Path
) -> None:
    design = _design()
    base = _block(built.path, design)
    keep = _lookback_keep(design, built.sessions)
    path = _copy(built, tmp_path)
    conn = duckdb.connect(str(path))
    try:
        conn.execute("CREATE TEMP TABLE keep (security_id VARCHAR, session DATE)")
        conn.executemany("INSERT INTO keep VALUES (?, ?)", sorted(keep))
        conn.execute(
            "UPDATE prices_daily SET close = 0.01, volume = 3 WHERE NOT EXISTS "
            "(SELECT 1 FROM keep k WHERE k.security_id = prices_daily.security_id "
            "AND k.session = prices_daily.session)"
        )
        rows = conn.execute("SELECT count(*) FROM prices_daily").fetchone()
        overwritten = conn.execute("SELECT count(*) FROM prices_daily WHERE volume = 3").fetchone()
    finally:
        conn.close()
    assert rows is not None and overwritten is not None
    assert rows[0] == built.rows["prices_daily"]  # rows kept
    assert overwritten[0] > rows[0] // 2  # most of the table: entry, holds, the last session
    assert _block(path, design) == base


# --- Nothing after the development boundary ---


def test_window_after_the_boundary_is_refused_before_any_event_or_price_read(
    built: fx.FeasibilityStore,
) -> None:
    late = fd.parse_design_text(
        (fx.DESIGN_DIR / "form4.toml").read_text().replace("2019-03-29", "2020-01-02")
    )
    conn = built.connect()
    rec = Recorder(conn)
    with pytest.raises(feas.FeasibilityRefused, match="development boundary"):
        feas.count_feasibility(rec, late, store=FACTS)  # type: ignore[arg-type]
    assert not [s for s in rec.sql if "prices_daily" in s or "sec_form4" in s]


def test_a_copy_with_no_boundary_is_refused(built: fx.FeasibilityStore, tmp_path: Path) -> None:
    path = _copy(built, tmp_path)
    _edit(path, "DELETE FROM owner_decisions WHERE kind = 'development_boundary'")
    conn = duckdb.connect(str(path), read_only=True)
    rec = Recorder(conn)
    with pytest.raises(feas.FeasibilityRefused, match="no development boundary"):
        feas.count_feasibility(rec, _design(), store=FACTS)  # type: ignore[arg-type]
    assert not [s for s in rec.sql if "prices_daily" in s or "sec_form4" in s]


def test_every_read_is_cut_at_the_close_of_the_last_session(built: fx.FeasibilityStore) -> None:
    sql = _recorded(built.path, _design())
    reads = [s for s in sql if re.search(r"FROM (prices_daily|sec_form4|securities|delistings)", s)]
    assert reads and all("known_at <= ?" in s for s in reads)


# --- No store row, and no research-registry table ---


def _row_counts(path: Path) -> dict[str, int]:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()]
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}  # type: ignore[index]
    finally:
        conn.close()


def test_no_row_no_file_and_only_selects(
    built: fx.FeasibilityStore, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = _row_counts(built.path)
    folder = set(built.path.parent.iterdir())
    for name in ("form4", "form25", "statement_fact", "corporate_action"):
        sql = _recorded(built.path, _design(name))
        assert all(
            re.match(r"\s*SELECT\b", s)
            and not re.search(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP)\b", s)
            for s in sql
            if "owner_decisions" not in s and "duckdb_tables" not in s
        )
    assert _row_counts(built.path) == before
    assert set(built.path.parent.iterdir()) == folder
    assert capsys.readouterr().out == ""


def test_module_names_no_research_table_no_store_research_and_no_filing_events() -> None:
    source = SRC.read_text()
    assert "store.research" not in source and "store import research" not in source
    assert not re.search(r"\bresearch_(registrations|datasets|runs|results|decisions)\b", source)
    assert "filing_events" not in source
    assert "registry.boundary_date" in source


# --- Counts are right (hand-computed on the fixture) ---


def _cells(counts: fb.FeasibilityCounts) -> dict[tuple[int, int | None], tuple[int, int, int]]:
    return {(c.year, c.band): (c.raw_events, c.entry_sessions, c.issuers) for c in counts.cells}


def _totals(counts: fb.FeasibilityCounts) -> dict[int | None, tuple[int, int, int]]:
    return {t.band: (t.raw_events, t.entry_sessions, t.issuers) for t in counts.totals}


def test_form4_counts_match_the_hand_count(built: fx.FeasibilityStore) -> None:
    counts = _counts(built.path, _design())
    # Rows: 12 matched (both filers of AAA on 2018-03-12 are rows); events 11 (they are one).
    assert counts.timestamp == fb.TimestampCounts(12, 0, 0)
    assert counts.boundary == fx.BOUNDARY
    assert _cells(counts) == {
        (2018, 0): (2, 2, 1),  # AAA 03-13 and 03-20
        (2018, 1): (3, 3, 2),  # BBB 03-13, BBB 05-14, DDD 08-21
        (2018, 2): (2, 2, 1),  # CCC 03-13 and 07-05
        (2018, None): (2, 2, 2),  # AAA 01-11 (short lookback) and EEE (no bars)
        (2019, 0): (1, 1, 1),  # AAA 02-05
        (2019, 1): (1, 1, 1),  # BBB 03-28
    }
    assert _totals(counts) == {0: (3, 3, 1), 1: (4, 4, 2), 2: (2, 2, 1), None: (2, 2, 2)}
    assert counts.events_hold_past_window == 1  # BBB 03-28 + 20 sessions
    n_sessions = len([s for s in built.sessions if built.window_start <= s <= built.window_end])
    assert counts.window_sessions == n_sessions
    # (f): DDD, EEE, FFF end in the window (GGG after it); DDD and FFF have bars;
    # DDD and EEE have events; only DDD has both.
    assert counts.coverage == fb.DelisterCoverage(3, 2, 2, 1)


def test_effective_count_one_issuer_with_overlapping_holds(built: fx.FeasibilityStore) -> None:
    top = next(t for t in _counts(built.path, _design()).totals if t.band == 0)
    assert top.effective is not None
    # AAA entries 03-13 and 03-20 (5 sessions apart, o = 15/20) and 2019-02-05, apart:
    # sum w = 3 + 2 * 0.75, n_eff = 9 / 4.5. Same issuer, so the default and any rho agree.
    assert top.effective.n_eff == pytest.approx(9 / 4.5)
    assert top.effective.same_date_factor == pytest.approx(1.0)
    assert top.effective.overlap_factor == pytest.approx(3 / (9 / 4.5))


def _brute(events: list[tuple[str, int]], hold: int, rho: float | None) -> tuple[float, ...]:
    """n_eff by the spec's pairwise double sum: default, a cited rho, rho 0 and rho 1."""

    def over(a: int, b: int) -> float:
        return max(0, hold - abs(a - b)) / hold

    def n_eff(weight: Callable[[tuple[str, int], tuple[str, int]], float]) -> float:
        total = sum(weight(x, y) for x in events for y in events)
        return len(events) ** 2 / total

    def w(x: tuple[str, int], y: tuple[str, int], cross: Callable[[int, int], float]) -> float:
        if x is y:
            return 1.0
        if x[0] == y[0]:
            return over(x[1], y[1])
        return cross(x[1], y[1])

    default = n_eff(lambda x, y: w(x, y, lambda a, b: 1.0 if a == b else 0.05 * over(a, b)))
    cited = n_eff(lambda x, y: w(x, y, lambda a, b: (rho or 0.0) * over(a, b)))
    zero = n_eff(lambda x, y: w(x, y, lambda a, b: 0.0))
    one = n_eff(lambda x, y: w(x, y, lambda a, b: over(a, b)))
    return default, cited, zero, one


_ONE_BAND = "[band]\nlookback_sessions = 20\nedges = [1e15]\n"


def _single_band_design(extra: str = "") -> fd.FeasibilityDesign:
    text = (fx.DESIGN_DIR / "form4.toml").read_text()
    text = re.sub(r"\[band\]\nlookback_sessions = 20\nedges = .*\n", _ONE_BAND, text)
    return fd.parse_design_text(extra + text)


def _entry(built: fx.FeasibilityStore, day: date) -> int:
    return list(built.sessions).index(day)


def test_effective_count_default_cited_rho_and_bounds_equal_the_double_sum(
    built: fx.FeasibilityStore,
) -> None:
    d = date
    # The nine events with bars (AAA, BBB and CCC share 2018-03-13), by hand; entry = next session.
    events = [
        ("AAA", d(2018, 3, 13)),
        ("BBB", d(2018, 3, 13)),
        ("CCC", d(2018, 3, 13)),
        ("AAA", d(2018, 3, 20)),
        ("BBB", d(2018, 5, 14)),
        ("CCC", d(2018, 7, 5)),
        ("DDD", d(2018, 8, 21)),
        ("AAA", d(2019, 2, 5)),
        ("BBB", d(2019, 3, 28)),
    ]
    indexed = [(i, _entry(built, s)) for i, s in events]
    for design, rho in (
        (_single_band_design(), None),
        (_single_band_design('rho = 0.4\nrho_source = "test"\n'), 0.4),
    ):
        counts = _counts(built.path, design)
        low = next(t for t in counts.totals if t.band == 1)  # every banded event is under 1e15
        assert low.raw_events == 9 and low.effective is not None
        default, cited, zero, one = _brute(indexed, 20, rho)
        eff = low.effective
        assert eff.n_eff == pytest.approx(default if rho is None else cited)
        assert eff.n_eff_rho0 == pytest.approx(zero)
        assert eff.n_eff_rho1 == pytest.approx(one)
        assert eff.n / eff.same_date_factor / eff.overlap_factor == pytest.approx(eff.n_eff)
        # One session holds three events: sum m_d^2 = 3^2 + 6 * 1^2 = 15, B = 15 / 9.
        assert eff.same_date_factor == pytest.approx(15 / 9)


def test_form25_source_counts_issuers_and_coverage(built: fx.FeasibilityStore) -> None:
    counts = _counts(built.path, _design("form25"))
    # DDD (mid), EEE (no bars), FFF (mid) end in the window; GGG (2019-05) does not.
    assert _totals(counts) == {0: (0, 0, 0), 1: (2, 2, 2), 2: (0, 0, 0), None: (1, 1, 1)}
    assert counts.coverage == fb.DelisterCoverage(3, 2, 3, 2)


def test_statement_fact_and_corporate_action_filters(built: fx.FeasibilityStore) -> None:
    facts = _counts(built.path, _design("statement_fact"))
    # AAA 10-K and BBB 10-Q only: comparative, 8-K, after-window and after-boundary rows go.
    assert facts.timestamp.rows_matched == 2
    assert _totals(facts) == {0: (1, 1, 1), 1: (1, 1, 1), 2: (0, 0, 0), None: (0, 0, 0)}
    actions = _counts(built.path, _design("corporate_action"))
    assert actions.timestamp.rows_matched == 2  # AAA and CCC splits; BBB's is after the window
    assert _totals(actions) == {0: (1, 1, 1), 1: (0, 0, 0), 2: (1, 1, 1), None: (0, 0, 0)}


def test_a_row_first_known_after_the_window_is_not_read(
    built: fx.FeasibilityStore, tmp_path: Path
) -> None:
    # AAA's form 4 of 2019-04-02 is in the fixture; one more, known after the boundary, too.
    path = _copy(built, tmp_path)
    _insert_form4(path, "AAA", datetime(2019, 6, 3, 15, tzinfo=UTC), "Z9")
    assert _block(path, _design()) == _block(built.path, _design())


def test_events_with_no_entry_session_in_the_window(
    built: fx.FeasibilityStore, tmp_path: Path
) -> None:
    path = _copy(built, tmp_path)
    # Known on the window's last session, before the close: its entry (next session) is outside.
    _insert_form4(path, "AAA", datetime(2019, 3, 29, 15, tzinfo=UTC), "Z8")
    counts = _counts(path, _design())
    assert counts.timestamp == fb.TimestampCounts(13, 0, 1)
    assert _totals(counts) == _totals(_counts(built.path, _design()))


# --- The trading calendar ---


def _insert_form4(path: Path, issuer: str, accepted: datetime, filer: str) -> None:
    conn = duckdb.connect(str(path))
    try:
        insert_row(
            conn,
            "sec_form4_transactions",
            {
                "accession": f"0000000009-18-{filer}",
                "issuer_cik": fx.CIK[issuer],
                "filer_cik": f"99{filer}",
                "line_no": 0,
                "is_director": False,
                "is_officer": True,
                "is_ten_percent_owner": False,
                "is_other": False,
                "transaction_date": accepted.date(),
                "transaction_code": "P",
                "acquired_disposed": "A",
                "shares": 100,
                "ownership_form": "D",
                "security_title": "Common Stock",
                "accepted_at": accepted,
                "known_at": accepted,
                "ingested_at": accepted,
                "source": "test",
                "provenance": "filing",
            },
        )
    finally:
        conn.close()


def _only_aaa(built: fx.FeasibilityStore, tmp_path: Path, times: list[datetime]) -> Path:
    path = _copy(built, tmp_path)
    _edit(path, "DELETE FROM sec_form4_transactions")
    for number, accepted in enumerate(times):
        _insert_form4(path, "AAA", accepted, f"{number:06d}")
    return path


def test_weekend_and_after_close_filings_enter_the_next_session_not_the_next_weekday(
    built: fx.FeasibilityStore, tmp_path: Path
) -> None:
    # Friday after the close, Saturday and Sunday all enter Monday 2018-06-04: one event.
    times = [
        datetime(2018, 6, 1, 21, tzinfo=UTC),
        datetime(2018, 6, 2, 15, tzinfo=UTC),
        datetime(2018, 6, 3, 15, tzinfo=UTC),
    ]
    counts = _counts(_only_aaa(built, tmp_path, times), _design())
    assert counts.timestamp.rows_matched == 3
    assert _totals(counts)[0] == (1, 1, 1)


def test_a_hold_counts_sessions_across_a_holiday(
    built: fx.FeasibilityStore, tmp_path: Path
) -> None:
    # 2018-07-04 is a holiday. Entries 07-03 and 07-06 are two sessions apart (3 weekdays).
    times = [datetime(2018, 7, 2, 15, tzinfo=UTC), datetime(2018, 7, 5, 15, tzinfo=UTC)]
    path = _only_aaa(built, tmp_path, times)
    text = (
        (fx.DESIGN_DIR / "form4.toml")
        .read_text()
        .replace("hold_sessions = 20", "hold_sessions = 3")
    )
    design = fd.parse_design_text(
        re.sub(r"\[band\]\nlookback_sessions = 20\nedges = .*\n", _ONE_BAND, text)
    )
    eff = next(t for t in _counts(path, design).totals if t.band == 1).effective
    assert eff is not None
    # Two sessions apart in a 3-session hold: o = 1/3, n_eff = 4 / (2 + 2/3) = 1.5.
    # By weekdays o would be 0 and n_eff 2.0.
    assert eff.n_eff == pytest.approx(1.5)
