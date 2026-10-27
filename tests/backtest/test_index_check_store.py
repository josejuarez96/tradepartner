"""The index check's store reads (backtest spec amendment #1598, T245).

Every test runs on a temp copy of the fixture universe with a check-only RSP series
planted (`BENCH:RSP`, SPY's bars at half the price). The fixture's months: at
2019-05-31 the members are DUALA, DUALB, SPFT, SPBT and WNDX (WNDX's last bar is
2019-06-24), at 2019-06-28 DUALA, DUALB and SPBT, so June 2019 holds a mid-month
delisting and July 2019 three plain members. The check writes nothing, so a "bad month"
is planted into the temp copy before the run.
"""

from __future__ import annotations

import ast
import math
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
from conftest import load_universe_fixtures

import tradepartner.backtest.index_check as ic
import tradepartner.backtest.index_check_store as ics
from tradepartner.calendar import last_session_of_month, session_close
from tradepartner.config import Settings
from tradepartner.store import registry, schema
from tradepartner.store.db import configure_connection, insert_row, open_read_only
from tradepartner.universe import universe_as_of

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
NOW = datetime(2026, 10, 20, 15, tzinfo=UTC)
JUNE, JULY = "2019-06", "2019-07"
T_JULY = session_close(last_session_of_month(2019, 7))


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def settings_for(path: Path, **extra: Any) -> Settings:
    return Settings(_env_file=None, store={"path": str(path)}, **extra)


def build_store(path: Path, *, boundary: date | None = None) -> Settings:
    """The fixture universe plus `BENCH:RSP` (check-only), and an optional
    development boundary; closed before it returns."""
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        load_universe_fixtures(conn, UNIVERSE_DIR)
        conn.execute(
            "INSERT INTO securities SELECT * REPLACE ('BENCH:RSP' AS security_id, "
            "'Invesco S&P 500 Equal Weight ETF' AS name) FROM securities "
            "WHERE security_id = 'SEC_SPY'"
        )
        conn.execute(
            "INSERT INTO listings SELECT * REPLACE ('BENCH:RSP' AS security_id, "
            "'RSP' AS ticker) FROM listings WHERE security_id = 'SEC_SPY'"
        )
        conn.execute(
            "INSERT INTO classifications SELECT * REPLACE ('BENCH:RSP' AS security_id) "
            "FROM classifications WHERE security_id = 'SEC_SPY'"
        )
        conn.execute(
            "INSERT INTO prices_daily SELECT * REPLACE ('BENCH:RSP' AS security_id, "
            "open * 0.5 AS open, high * 0.5 AS high, low * 0.5 AS low, "
            "close * 0.5 AS close) FROM prices_daily WHERE security_id = 'SEC_SPY'"
        )
        if boundary is not None:
            registry.write_development_boundary(conn, boundary=boundary, reason="index-check test")
    finally:
        conn.close()
    return settings_for(path)


@pytest.fixture
def store(tmp_path: Path) -> Settings:
    """Development through 2019-06-28: June 2019 is a development month, July protected."""
    return build_store(tmp_path / "s.duckdb", boundary=date(2019, 6, 28))


@pytest.fixture
def open_store(tmp_path: Path) -> Settings:
    """Development through 2019-12-31: both test months show levels."""
    return build_store(tmp_path / "o.duckdb", boundary=date(2019, 12, 31))


@contextmanager
def writer(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(settings.store.path)
    try:
        configure_connection(conn)
        yield conn
    finally:
        conn.close()


def run(settings: Settings, start: str = JUNE, end: str = JULY, **kw: Any) -> ics.IndexCheckReport:
    return ics.run_index_check(settings, now=NOW, start=start, end=end, **kw)


def names(report: ics.IndexCheckReport, month: str, book: str = ics.EQUAL) -> dict[str, Any]:
    (worst,) = [w for w in report.worst[book] if w.month == month]
    return {n.row.ticker: n for n in worst.names}


def action(
    sid: str, kind: str, ex: date, amount: float, source: str, known: datetime
) -> dict[str, Any]:
    return {
        "security_id": sid,
        "action_type": kind,
        "ex_date": ex,
        "ratio_or_amount": amount,
        "announced_at": None,
        "source_action_id": source,
        "cancelled": False,
        "known_at": known,
        "ingested_at": known,
        "source": "alpaca",
        "provenance": "action",
    }


def raw_close(settings: Settings, sid: str, day: date) -> float:
    with writer(settings) as conn:
        row = conn.execute(
            "SELECT close FROM prices_daily WHERE security_id = ? AND session = ?", [sid, day]
        ).fetchone()
    assert row is not None
    return float(row[0])


# ---- flags ------------------------------------------------------------------------------


def test_a_member_with_two_missing_bars_is_attributed_missing_bars_2(open_store: Settings) -> None:
    with writer(open_store) as conn:
        conn.execute(
            "DELETE FROM prices_daily WHERE security_id = 'SEC_SPLIT_BETWEEN' "
            "AND session IN ('2019-07-10', '2019-07-11')"
        )
    report = run(open_store, JULY, JULY)
    assert "missing_bars=2" in names(report, JULY)["SPBT"].flags
    assert report.census[JULY]["missing_bars"] == 1
    assert "missing_bars=2" not in names(run(open_store, JUNE, JUNE), JUNE)["SPBT"].flags


def test_a_planted_duplicate_dividend_is_dup_dividend(open_store: Settings) -> None:
    known = datetime(2019, 7, 12, 20, tzinfo=UTC)
    with writer(open_store) as conn:
        for source in ("div-a", "div-b"):
            insert_row(
                conn,
                "corporate_actions",
                action("SEC_DUAL_A", "dividend", date(2019, 7, 15), 0.1, source, known),
            )
    flags = names(run(open_store, JULY, JULY), JULY)["DUALA"].flags
    assert "dup_dividend" in flags
    assert "dividend=0.1" in flags


def test_a_single_dividend_is_not_a_duplicate(open_store: Settings) -> None:
    with writer(open_store) as conn:
        insert_row(
            conn,
            "corporate_actions",
            action(
                "SEC_DUAL_A", "dividend", date(2019, 7, 15), 0.1, "only", T_JULY.replace(day=12)
            ),
        )
    flags = names(run(open_store, JULY, JULY), JULY)["DUALA"].flags
    assert "dividend=0.1" in flags
    assert "dup_dividend" not in flags


def _form_25(settings: Settings, filed: datetime) -> None:
    """A Form 25 for WNDX filed (and known) at `filed`, with the bars as the fixture has them."""
    with writer(settings) as conn:
        insert_row(
            conn,
            "delistings",
            {
                "security_id": "SEC_WINDOW_DELIST",
                "form": "25",
                "class_title": "Common Stock",
                "exchange": "NASDAQ",
                "filed_at": filed,
                "effective_on": date(2019, 7, 8),
                "known_at": filed,
                "ingested_at": filed,
                "source": "edgar",
                "provenance": "filing",
            },
        )


def test_a_mid_month_delisting_ends_at_its_last_close_and_is_delisted(open_store: Settings) -> None:
    _form_25(open_store, datetime(2019, 6, 26, 20, 30, tzinfo=UTC))
    report = run(open_store, JUNE, JUNE)
    wndx = names(report, JUNE)["WNDX"]
    assert "delisted" in wndx.flags
    assert not any(f.startswith("missing_bars") for f in wndx.flags)
    assert report.census[JUNE]["delisted"] == 1
    # the last traded close (2019-06-24) over the close of 2019-05-31; no actions between
    expected = (
        raw_close(open_store, "SEC_WINDOW_DELIST", date(2019, 6, 24))
        / raw_close(open_store, "SEC_WINDOW_DELIST", date(2019, 5, 31))
        - 1.0
    )
    assert isinstance(wndx.row, ic.DevNameRow)
    assert wndx.row.name_return == pytest.approx(expected, abs=1e-12)


def test_a_form_25_filed_after_the_month_end_close_is_not_known_for_the_month(
    open_store: Settings,
) -> None:
    """The fixture's own Form 25 is filed at 20:30 UTC on 2019-06-28, after t_m (20:00):
    at t_m the name has four sessions without a bar and is not yet delisted."""
    wndx = names(run(open_store, JUNE, JUNE), JUNE)["WNDX"]
    assert wndx.flags == ("missing_bars=4",)


def test_a_split_in_the_month_is_flagged_with_its_ratio(open_store: Settings) -> None:
    with writer(open_store) as conn:
        insert_row(
            conn,
            "corporate_actions",
            action(
                "SEC_DUAL_B",
                "split",
                date(2019, 7, 15),
                2.0,
                "",
                datetime(2019, 7, 12, 20, tzinfo=UTC),
            ),
        )
    assert "split=2" in names(run(open_store, JULY, JULY), JULY)["DUALB"].flags


def test_a_member_without_a_cap_is_left_out_of_the_cap_book_and_counted(
    open_store: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = ics.universe_as_of

    def no_cap_for_b(conn: duckdb.DuckDBPyConnection, t: datetime, s: Settings) -> Any:
        universe = real(conn, t, s)
        members = universe.members.with_columns(
            pl.when(pl.col("security_id") == "SEC_DUAL_B")
            .then(None)
            .otherwise(pl.col("market_cap"))
            .alias("market_cap")
        )
        return type(universe)(**{**universe.__dict__, "members": members})

    monkeypatch.setattr(ics, "universe_as_of", no_cap_for_b)
    report = run(open_store, JULY, JULY)
    (month,) = report.months
    assert month.n_members == 3 and month.n_no_cap == 1
    cap_names = names(report, JULY, ics.CAP)
    assert "DUALB" not in cap_names
    assert "DUALB" in names(report, JULY, ics.EQUAL)


def test_extreme_no_cap_member_is_listed_from_start_of_month_members(
    open_store: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = ics.universe_as_of
    seen: list[datetime] = []

    def no_cap_for_b(conn: duckdb.DuckDBPyConnection, t: datetime, s: Settings) -> Any:
        seen.append(t)
        universe = real(conn, t, s)
        members = universe.members.with_columns(
            pl.when(pl.col("security_id") == "SEC_DUAL_B")
            .then(None)
            .otherwise(pl.col("market_cap"))
            .alias("market_cap")
        )
        return type(universe)(**{**universe.__dict__, "members": members})

    monkeypatch.setattr(ics, "universe_as_of", no_cap_for_b)
    cfg = open_store.model_copy(
        update={
            "index_check": open_store.index_check.model_copy(update={"extreme_name_return": 0.0})
        }
    )
    report = run(cfg, JULY, JULY)
    assert seen == [session_close(date(2019, 6, 28))]
    dual_b = names(report, JULY, ics.CAP)["DUALB"]
    assert dual_b.row.contribution == 0.0
    assert "extreme" in dual_b.flags


# ---- the arithmetic on the store ---------------------------------------------------------


def test_a_development_month_matches_the_hand_computed_returns(open_store: Settings) -> None:
    prev, end = date(2019, 6, 28), date(2019, 7, 31)
    sids = ["SEC_DUAL_A", "SEC_DUAL_B", "SEC_SPLIT_BETWEEN"]
    r = {s: raw_close(open_store, s, end) / raw_close(open_store, s, prev) - 1.0 for s in sids}
    with open_read_only(open_store) as conn:
        caps = dict(
            universe_as_of(conn, session_close(prev), open_store)
            .members.select("security_id", "market_cap")
            .iter_rows()
        )
    assert set(caps) == set(sids)
    total = sum(caps.values())
    r_spy = raw_close(open_store, "SEC_SPY", end) / raw_close(open_store, "SEC_SPY", prev) - 1.0
    # RSP is SPY's bars at half the price, so both benchmarks return the same
    expected_eq = sum(1 + r[s] for s in sids) / 3 - 1
    expected_cap = sum(caps[s] / total * (1 + r[s]) for s in sids) - 1
    (row,) = run(open_store, JULY, JULY).months
    assert isinstance(row, ic.DevMonthRow)
    assert row.equal_return == pytest.approx(expected_eq, abs=1e-12)
    assert row.equal_bench_return == pytest.approx(r_spy, abs=1e-12)
    assert row.equal_gap == pytest.approx(expected_eq - r_spy, abs=1e-12)
    assert row.cap_return == pytest.approx(expected_cap, abs=1e-12)
    assert row.cap_gap == pytest.approx(expected_cap - r_spy, abs=1e-12)


def test_the_listed_names_contributions_sum_to_the_gap(open_store: Settings) -> None:
    report = run(open_store, JULY, JULY)
    (month,) = report.months
    listed = names(report, JULY)  # three members, five names per month: all listed
    assert len(listed) == 3
    assert math.fsum(n.row.contribution for n in listed.values()) == pytest.approx(
        month.equal_gap, abs=1e-12
    )


# ---- protected months --------------------------------------------------------------------


def test_a_protected_month_carries_no_level_and_a_development_month_does(store: Settings) -> None:
    report = run(store)
    june, july = report.months
    assert isinstance(june, ic.DevMonthRow) and isinstance(july, ic.ProtectedMonthRow)
    payload = ics.report_dict(report)
    june_json, july_json = payload["months"]
    assert {"equal_return", "equal_bench_return", "cap_return", "cap_bench_return"} <= set(
        june_json
    )
    assert not any("return" in key for key in july_json)
    assert report.header.edge == date(2019, 6, 28)
    assert report.header.edge_source == "development_boundary"
    protected_names = [
        n for w in payload["worst"]["equal"] if w["month"] == JULY for n in w["names"]
    ]
    assert protected_names and not any("name_return" in n for n in protected_names)
    assert any(
        "name_return" in n
        for w in payload["worst"]["equal"]
        if w["month"] == JUNE
        for n in w["names"]
    )


def test_with_no_edge_in_the_store_every_month_is_protected(tmp_path: Path) -> None:
    report = run(build_store(tmp_path / "n.duckdb"))
    assert report.header.edge is None
    assert all(isinstance(m, ic.ProtectedMonthRow) for m in report.months)
    assert all(isinstance(y, ic.ProtectedYearRow) for y in report.years)


def test_the_year_row_and_zone_totals_follow_the_months(store: Settings) -> None:
    report = run(store)
    (year,) = report.years
    assert isinstance(year, ic.ProtectedYearRow) and year.months == 2
    assert report.year_members == {2019: report.months[0].n_members}
    dev, prot = report.zones
    assert isinstance(dev, ic.DevYearRow) and dev.months == 1
    assert isinstance(prot, ic.ProtectedYearRow) and prot.months == 1


# ---- failures and refusals ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "sid", "day"),
    [("SPY", "SEC_SPY", "2019-07-31"), ("RSP", "BENCH:RSP", "2019-06-28")],
)
def test_a_benchmark_bar_missing_on_a_boundary_session_stops_the_run(
    open_store: Settings, symbol: str, sid: str, day: str
) -> None:
    with writer(open_store) as conn:
        conn.execute("DELETE FROM prices_daily WHERE security_id = ? AND session = ?", [sid, day])
    with pytest.raises(ics.IndexCheckFailed) as caught:
        run(open_store, JULY, JULY)
    assert str(caught.value) == f"missing_benchmark_bar {symbol} {day}"


def test_a_zero_volume_benchmark_bar_is_missing(open_store: Settings) -> None:
    with writer(open_store) as conn:
        conn.execute(
            "UPDATE prices_daily SET volume = 0 WHERE security_id = 'BENCH:RSP' "
            "AND session = '2019-07-31'"
        )
    with pytest.raises(ics.IndexCheckFailed, match=r"missing_benchmark_bar RSP 2019-07-31"):
        run(open_store, JULY, JULY)


class Reads:
    """Counts connections and fails any read of a month."""

    def __init__(self) -> None:
        self.opened = 0

    def connect(self, settings: Settings) -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        self.opened += 1
        return open_read_only(settings)


@pytest.fixture
def no_month_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("a month was read")

    monkeypatch.setattr(ics, "universe_as_of", refuse)
    monkeypatch.setattr(ics, "adjusted_prices_as_of", refuse)


@pytest.mark.parametrize(
    ("start", "end", "now"),
    [
        ("1990-01", "1990-03", NOW),  # T_(m-1) before the calendar first session
        ("1989-12", "1989-12", NOW),
        ("2019-07", "2019-06", NOW),  # end before start
        ("2019-06", "2019-07", datetime(2019, 7, 31, 15, tzinfo=UTC)),  # July not closed
        ("2019-6", "2019-07", NOW),  # not YYYY-MM
        ("2019-06", "July", NOW),
    ],
)
def test_a_bad_window_is_refused_before_any_month_is_read(
    store: Settings, no_month_read: None, start: str, end: str, now: datetime
) -> None:
    reads = Reads()
    with pytest.raises(ics.IndexCheckRefused) as caught:
        ics.run_index_check(store, now=now, start=start, end=end, connect=reads.connect)
    assert caught.value.code == "refused_window"
    assert reads.opened == 0


def test_a_rank_by_other_than_cap_is_refused_before_any_month_is_read(
    tmp_path: Path, no_month_read: None
) -> None:
    path = tmp_path / "r.duckdb"
    build_store(path)
    reads = Reads()
    other = settings_for(path, universe={"rank_by": "dollar_volume"})
    with pytest.raises(ics.IndexCheckRefused) as caught:
        ics.run_index_check(other, now=NOW, start=JUNE, end=JULY, connect=reads.connect)
    assert caught.value.code == "refused_rank_by"
    assert reads.opened == 0


def test_a_benchmark_identity_error_is_a_refusal_before_any_month(
    open_store: Settings, no_month_read: None
) -> None:
    with writer(open_store) as conn:
        conn.execute("UPDATE securities SET benchmark = FALSE WHERE security_id = 'BENCH:RSP'")
    reads = Reads()
    with pytest.raises(ics.IndexCheckRefused) as caught:
        ics.run_index_check(open_store, now=NOW, start=JUNE, end=JULY, connect=reads.connect)
    assert caught.value.code == "benchmark_identity"
    assert reads.opened == 1  # the preflight's identity read only


def test_the_default_window_comes_from_config(open_store: Settings) -> None:
    cfg = settings_for(
        Path(open_store.store.path), index_check={"start_month": JULY, "end_month": JULY}
    )
    report = ics.run_index_check(cfg, now=NOW)
    assert [m.month for m in report.months] == [JULY]


# ---- it writes nothing ---------------------------------------------------------------------


def _counts(path: str) -> dict[str, int]:
    conn = duckdb.connect(path, read_only=True)
    try:
        tables = [r[0] for r in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()]
        counts = {t: int(conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]) for t in tables}  # type: ignore[index]
    finally:
        conn.close()
    return counts


def test_a_run_changes_no_table_and_every_connection_is_read_only(
    store: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _counts(store.store.path)
    assert "schema_version" in before
    modes: list[Any] = []
    real = duckdb.connect

    def spy(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        modes.append(kwargs.get("read_only"))
        return real(*args, **kwargs)

    monkeypatch.setattr("tradepartner.store.db.duckdb.connect", spy)
    run(store)
    monkeypatch.undo()
    assert modes == [True] * 3  # the preflight and one per month
    assert _counts(store.store.path) == before
    conn = duckdb.connect(store.store.path, read_only=True)
    try:
        assert conn.execute("SELECT max(version) FROM schema_version").fetchone() == (
            schema.CURRENT_SCHEMA_VERSION,
        )
    finally:
        conn.close()


def test_the_report_header_names_the_run(store: Settings) -> None:
    head = run(store).header
    assert head.store_path == store.store.path
    assert head.schema_version == schema.CURRENT_SCHEMA_VERSION
    assert head.data_vintage is not None
    assert head.universe["rank_by"] == "cap"
    assert head.apply_spin_offs is store.adjust.apply_spin_offs
    assert (head.equal_benchmark, head.cap_benchmark) == ("RSP", "SPY")


# ---- the module's imports (AST) ---------------------------------------------------------------

MODULES = [
    Path(ics.__file__),
    Path(ic.__file__),
]
ALLOWED_REGISTRY_READS = {"development_boundary", "data_vintage", "family_rules"}


def _imports(path: Path) -> list[tuple[str, str | None]]:
    """(module, name) for every import; `name` is None for a plain `import`."""
    out: list[tuple[str, str | None]] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out += [(a.name, None) for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import"
            out += [(node.module or "", a.name) for a in node.names]
    return out


BANNED_BACKTEST = ("engine", "holdout")


def _violations(path: Path) -> list[str]:
    bad: list[str] = []
    for module, name in _imports(path):
        full = f"{module}.{name}" if name else module
        if module in ("tradepartner.store.registry", "tradepartner.store.lab_registry"):
            if name not in ALLOWED_REGISTRY_READS:
                bad.append(f"{full} is not a pinned read")
        elif full in ("tradepartner.store.registry", "tradepartner.store.lab_registry"):
            bad.append(f"the module {full} is imported whole")
        parts = full.split(".")
        if (
            parts[:2] == ["tradepartner", "backtest"]
            and len(parts) > 2
            and parts[2] in BANNED_BACKTEST
        ):
            bad.append(f"{full} is an engine or holdout import")
    return bad


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_the_modules_import_only_the_pinned_registry_reads(path: Path) -> None:
    assert _violations(path) == []


def test_the_registry_reads_it_imports_exist_and_the_oracle_name_matches() -> None:
    assert ics.ORACLE_FAMILY == registry.ORACLE_FAMILY
    assert {n for m, n in _imports(MODULES[0]) if m.endswith("registry") and n} == (
        ALLOWED_REGISTRY_READS
    )


@pytest.mark.parametrize(
    "source",
    [
        "from tradepartner.store.registry import open_trial\n",
        "from tradepartner.store import registry\n",
        "from tradepartner.store.lab_registry import write_family_rules\n",
        "from tradepartner.backtest import engine\n",
        "from tradepartner.backtest.holdout import Flags\n",
        "import tradepartner.backtest.engine\n",
    ],
)
def test_the_checker_catches_a_banned_import(tmp_path: Path, source: str) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text(source)
    assert _violations(bad)


def test_the_edge_is_the_earlier_of_the_boundary_and_the_day_before_a_holdout_start(
    tmp_path: Path,
) -> None:
    from tradepartner.store import lab_registry, lab_schema

    settings = build_store(tmp_path / "e.duckdb", boundary=date(2019, 6, 28))
    with writer(settings) as conn:
        lab_schema.apply_lab_schema(conn)
        for slug, family, start in (
            ("h-edge", "momentum", date(2019, 7, 1)),
            ("h-oracle", registry.ORACLE_FAMILY, date(2019, 1, 7)),
        ):
            hypothesis = registry.register_hypothesis(
                conn,
                slug=slug,
                family=family,
                title="edge test",
                doc_path=f"docs/hypotheses/{slug}.md",
                doc_sha256="0" * 64,
                params={"costs.per_side_bps": 15.0},
                in_sample_start=date(2017, 1, 31),
                holdout_start=start,
                holdout_end=date(2019, 12, 31),
                registered_by="owner",
                settings=settings_for(
                    tmp_path / "elsewhere.duckdb"
                ),  # not this store: no real-store refusal
            )
            lab_registry.write_family_rules(
                conn,
                family=family,
                first_hypothesis_id=hypothesis.hypothesis_id,
                parent_family=None,
                holdout_start=start,
                holdout_end=date(2019, 12, 31),
                in_sample_start=date(2017, 1, 31),
                fixed_params={},
                sr_star_seed_annual=None,
                settings=settings,
            )
    head = run(settings, JULY, JULY).header
    # momentum's holdout starts 2019-07-01, so its day before (06-30) is after the boundary
    # (06-28), and the oracle family's holdout is ignored
    assert (head.edge, head.edge_source) == (date(2019, 6, 28), "development_boundary")
    with writer(settings) as conn:
        conn.execute(
            "UPDATE family_rules SET holdout_start = '2019-06-15' WHERE family = 'momentum'"
        )
    head = run(settings, JULY, JULY).header
    assert (head.edge, head.edge_source) == (date(2019, 6, 14), "holdout_start")
