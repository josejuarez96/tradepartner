"""Look-ahead for `successor_chains` (#821, plan T187).

The row's `known_at` is the latest of the four evidence stamps (the fixture
carries one row per chainable pair), so a read before it must see two
securities; the truncation harness places a probe just before and just after
it. There is no `successor_chains` reader yet (T187b), so this pins the
harness membership and the row's `known_at` boundaries: the stored row is
absent strictly before its `known_at` and present from it.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from lookahead.harness import PROBE_EPSILON, TruncatedStore
from tradepartner.store import schema
from tradepartner.store.master import Chain, MasterBuild, write_master

#: The fixture's chainable pairs and their `successor_chains` rows, from
#: `chain_cases.json` (the spin-off pair has none).
_CASES = {
    "0001000011": ("0001000010", datetime.fromisoformat("2020-02-07T20:30:00+00:00")),
    "0001000013": ("0001000012", datetime.fromisoformat("2020-03-30T20:30:00+00:00")),
    "0001000015": ("0001000014", datetime.fromisoformat("2020-05-11T20:30:00+00:00")),
}


@pytest.fixture
def truncated(chain_fixture_store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    store = TruncatedStore(chain_fixture_store)
    try:
        yield store
    finally:
        store.close()


def test_the_fixture_stores_a_chain_row_per_chainable_pair(
    chain_fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    rows = chain_fixture_store.execute(
        "SELECT successor_id, predecessor_id, known_at FROM successor_chains ORDER BY successor_id"
    ).fetchall()
    assert rows == [
        (successor, predecessor, known_at)
        for successor, (predecessor, known_at) in sorted(_CASES.items())
    ]
    assert chain_fixture_store.execute(
        "SELECT count(*) FROM successor_chains WHERE successor_id = '0001000017'"
    ).fetchone() == (0,)


def test_the_row_is_absent_before_its_known_at_and_present_from_it(
    truncated: TruncatedStore,
) -> None:
    assert "successor_chains" in truncated.tables
    for successor, (_predecessor, known_at) in _CASES.items():
        before = truncated.at(known_at - PROBE_EPSILON)
        assert before.execute(
            "SELECT count(*) FROM successor_chains WHERE successor_id = ?", [successor]
        ).fetchone() == (0,), successor
        at = truncated.at(known_at)
        assert at.execute(
            "SELECT count(*) FROM successor_chains WHERE successor_id = ?", [successor]
        ).fetchone() == (1,), successor


def test_truncation_sees_exactly_the_rows_known_by_each_probe(
    chain_fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore
) -> None:
    """Every probe sees the fixture rows with `known_at <= T` and no others."""
    probes = [known_at_ for _predecessor, known_at_ in _CASES.values()]
    for known_at in sorted(probes):
        for t in (known_at - PROBE_EPSILON, known_at, known_at + PROBE_EPSILON):
            expected = chain_fixture_store.execute(
                "SELECT successor_id FROM successor_chains "
                "WHERE known_at <= ? ORDER BY successor_id",
                [t],
            ).fetchall()
            seen = (
                truncated.at(t)
                .execute("SELECT successor_id FROM successor_chains ORDER BY successor_id")
                .fetchall()
            )
            assert seen == expected, t


#: The read rule `schema.successor_chains` documents: the successor's
#: unretracted row with the greatest `ingested_at`, unless a retraction was
#: written after it (every revision applied, the corrected history the
#: UPDATE-moved listing rows give, T166b), shown at `t` only when its
#: `known_at <= t`.
_LIVE_AT_SQL = """
    SELECT evidence_accession, known_at FROM successor_chains c
    WHERE successor_id = '0001000011' AND NOT retracted AND known_at <= ?
      AND NOT EXISTS (
          SELECT 1 FROM successor_chains n
          WHERE n.successor_id = c.successor_id AND n.ingested_at > c.ingested_at
      )
"""


def _chain_row(known_at: datetime, ingested_at: datetime, **extra: object) -> dict[str, object]:
    return {
        "predecessor_id": "0001000010",
        "successor_id": "0001000011",
        "evidence_accession": "0001000011-24-000001",
        "evidence_form": "8-K12B",
        "retracted": False,
        "withdrawn_known_at": None,
        "known_at": known_at,
        "ingested_at": ingested_at,
        "source": "edgar",
        "provenance": "filing",
    } | extra


def test_a_moved_chain_is_absent_before_its_new_stamp_at_every_read(
    chain_fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """#821: after a move to a later stamp the chain reads absent at every
    `t` in [old, new), as the successor's listing row the move follows (moved
    by UPDATE, T166b) does, so `chain_known_at_ge_successor_rows` holds at
    every read; it reads the new stamp in [new, the run's clock) and after."""
    from tradepartner.store.db import insert_row

    old = datetime.fromisoformat("2020-02-07T20:30:00+00:00")
    new = datetime.fromisoformat("2020-03-08T20:30:00+00:00")
    clock = datetime.fromisoformat("2020-04-01T04:00:00+00:00")
    accession = "0001000011-24-000001"

    def at(t: datetime) -> list[tuple[str, datetime]]:
        return [tuple(row) for row in chain_fixture_store.execute(_LIVE_AT_SQL, [t]).fetchall()]

    assert at(old - PROBE_EPSILON) == []
    assert at(old) == [(accession, old)]
    assert at(new) == [(accession, old)]  # unmoved: the old stamp, no new row yet
    withdrawn = _chain_row(clock, clock, retracted=True, withdrawn_known_at=old)
    insert_row(chain_fixture_store, "successor_chains", withdrawn)
    insert_row(chain_fixture_store, "successor_chains", _chain_row(new, clock))
    for t in (old, old + PROBE_EPSILON, new - PROBE_EPSILON):  # [old, new)
        assert at(t) == [], t
    for t in (new, new + PROBE_EPSILON, clock - PROBE_EPSILON):  # [new, clock)
        assert at(t) == [(accession, new)], t
    for t in (clock, clock.replace(month=5)):  # from the run on
        assert at(t) == [(accession, new)], t


def test_write_master_stores_a_chain_no_earlier_than_its_evidence() -> None:
    """#1838: `write_master`'s chain path stores the row at its evidence
    `known_at`, never the run clock, so a read before that stamp sees no
    chain and a read from it does."""
    known_at = datetime.fromisoformat("2020-02-07T20:30:00+00:00")
    clock = datetime.fromisoformat("2020-04-01T04:00:00+00:00")
    accession = "0001000011-24-000001"
    conn = duckdb.connect(":memory:")
    try:
        schema.init_schema(conn)
        write_master(
            conn,
            MasterBuild(
                (),
                (),
                (),
                (),
                chains=(Chain("0001000010", "0001000011", accession, "8-K12B", known_at, clock),),
            ),
            now=clock,
        )
        truncated = TruncatedStore(conn)
        try:

            def at(t: datetime) -> list[tuple[str, datetime]]:
                return [tuple(row) for row in truncated.at(t).execute(_LIVE_AT_SQL, [t]).fetchall()]

            assert at(known_at - PROBE_EPSILON) == []
            assert at(known_at) == [(accession, known_at)]
            assert at(clock) == [(accession, known_at)]
        finally:
            truncated.close()
    finally:
        conn.close()


# --- the overlay stays out of the shared fixture (#821, PR #1836) -----------------------

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
_GENERATOR_PATH = Path(__file__).resolve().parents[2] / "scripts" / "make_fixture_universe.py"
_CHAIN_IDS = tuple(f"00010000{n}" for n in range(10, 18))


def test_the_shared_fixture_store_holds_no_chain_pair(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """The four pairs live only in the `chains/` overlay: the store every other test
    reads (health, universe, the H1/TWIN/B3 pins) has none of their rows."""
    for table in ("securities", "listings", "prices_daily", "facts", "classifications"):
        count = fixture_store.execute(
            f"SELECT count(*) FROM {table} WHERE security_id = ANY(?)",
            [list(_CHAIN_IDS)],
        ).fetchone()
        assert count == (0,), table
    assert fixture_store.execute("SELECT count(*) FROM successor_chains").fetchone() == (0,)


def test_the_chain_overlay_regenerates_byte_identically(tmp_path: Path) -> None:
    """`make_fixture_universe.py` writes the overlay beside the universe, byte for byte
    what is committed under `tests/fixtures/universe/chains/`."""
    spec = importlib.util.spec_from_file_location("_chain_overlay_gen", _GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.write_fixtures(tmp_path)
    committed = sorted(p.name for p in (_FIXTURES_DIR / "chains").iterdir())
    assert committed == sorted(p.name for p in (tmp_path / "chains").iterdir())
    for name in committed:
        assert (_FIXTURES_DIR / "chains" / name).read_bytes() == (
            tmp_path / "chains" / name
        ).read_bytes(), name
    assert not (tmp_path / "successor_chains.csv").exists()
