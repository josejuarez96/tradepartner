"""No look-ahead in the feasibility counts reader (T454, #3743).

The spec amendment's "Nothing after the development boundary": the block is the same
bytes when the store is cut at the close of the window's last session, when rows known
after it are added, and when a bar is restated after an event's own decision time.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from fixtures.research import feasibility_store as fx

from tradepartner.calendar import session_close
from tradepartner.research import feasibility as feas
from tradepartner.research import feasibility_block as fb
from tradepartner.research import feasibility_design as fd
from tradepartner.store.db import insert_row

FACTS = fb.StoreFacts("copy.duckdb", 1, datetime(2026, 1, 1, tzinfo=UTC), 49, "run-1", None)
NAMES = ("form4", "form25", "statement_fact", "corporate_action")
CUT = session_close(fx.WINDOW_END)  # the window's last session is a Friday


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> fx.FeasibilityStore:
    return fx.build_feasibility_store(tmp_path_factory.mktemp("la") / "store.duckdb")


def _block(path: Path, name: str) -> str:
    design = fd.parse_design_file(fx.DESIGN_DIR / f"{name}.toml")
    conn = duckdb.connect(str(path), read_only=True)
    try:
        return fb.render_block(design, feas.count_feasibility(conn, design, store=FACTS))
    finally:
        conn.close()


def _copy(built: fx.FeasibilityStore, tmp_path: Path) -> Path:
    target = tmp_path / "copy.duckdb"
    shutil.copy2(built.path, target)
    return target


def _known_tables(conn: duckdb.DuckDBPyConnection) -> list[str]:
    rows = conn.execute(
        "SELECT table_name FROM duckdb_columns() WHERE column_name = 'known_at' "
        "AND table_name NOT IN ('owner_decisions')"
    ).fetchall()
    return [str(r[0]) for r in rows]


@pytest.mark.parametrize("name", NAMES)
def test_cutting_the_store_at_the_window_end_leaves_the_block_byte_identical(
    built: fx.FeasibilityStore, tmp_path: Path, name: str
) -> None:
    path = _copy(built, tmp_path)
    conn = duckdb.connect(str(path))
    try:
        dropped = 0
        for table in _known_tables(conn):
            before = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            conn.execute(f"DELETE FROM {table} WHERE known_at > ?", [CUT])
            after = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert before is not None and after is not None
            dropped += before[0] - after[0]
    finally:
        conn.close()
    assert dropped > 0  # the fixture does hold rows known after the cut
    assert _block(path, name) == _block(built.path, name)


def test_rows_and_bars_added_after_the_cut_leave_the_block_byte_identical(
    built: fx.FeasibilityStore, tmp_path: Path
) -> None:
    path = _copy(built, tmp_path)
    late = datetime(2019, 4, 10, 15, tzinfo=UTC)
    conn = duckdb.connect(str(path))
    try:
        insert_row(
            conn,
            "sec_form4_transactions",
            {
                "accession": "0000000009-19-000001",
                "issuer_cik": fx.CIK["CCC"],
                "filer_cik": "99F4",
                "line_no": 0,
                "is_director": False,
                "is_officer": True,
                "is_ten_percent_owner": False,
                "is_other": False,
                "transaction_date": late.date(),
                "transaction_code": "P",
                "acquired_disposed": "A",
                "shares": 100,
                "ownership_form": "D",
                "security_title": "Common Stock",
                "accepted_at": late,
                "known_at": late,
                "ingested_at": late,
                "source": "test",
                "provenance": "filing",
            },
        )
        # A new session after the window, and a restated bar for a session inside it.
        conn.execute(
            "INSERT INTO prices_daily (security_id, session, open, high, low, close, volume, "
            "known_at, ingested_at, source, provenance) VALUES "
            "('SEC_BBB', DATE '2019-04-08', 1, 1, 1, 1, 1, ?, ?, 'test', 'bar'), "
            "('SEC_BBB', DATE '2019-03-20', 1, 1, 1, 1, 1, ?, ?, 'test', 'bar')",
            [late, late, late, late],
        )
    finally:
        conn.close()
    for name in NAMES:
        assert _block(path, name) == _block(built.path, name)


def test_a_bar_restated_after_the_events_own_decision_time_does_not_move_its_band(
    built: fx.FeasibilityStore, tmp_path: Path
) -> None:
    """AAA's 2018-03-12 event reads bars known then, not the later restatement of them."""
    path = _copy(built, tmp_path)
    restated = datetime(2018, 12, 1, 15, tzinfo=UTC)  # inside the window, after the event
    conn = duckdb.connect(str(path))
    try:
        conn.execute(
            "INSERT INTO prices_daily SELECT security_id, session, open, high, low, 1.0, 1, ?, ?, "
            "source, provenance FROM prices_daily WHERE security_id = 'SEC_AAA' "
            "AND session >= DATE '2018-02-09' AND session <= DATE '2018-03-12' "
            "AND known_at < TIMESTAMPTZ '2019-01-01'",
            [restated, restated],
        )
        n = conn.execute("SELECT count(*) FROM prices_daily WHERE known_at = ?", [restated])
        row = n.fetchone()
        assert row is not None and row[0] > 20  # the restatement is there
    finally:
        conn.close()
    assert _block(path, "form4") == _block(built.path, "form4")
