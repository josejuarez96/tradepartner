"""Look-ahead for `successor_chains` (#821, plan T187).

The row's `known_at` is the latest of the four evidence stamps (the fixture
carries one row per chainable pair), so a read before it must see two
securities; the truncation harness places a probe just before and just after
it. There is no `successor_chains` reader yet (T187b), so this pins the
harness membership and the row's `known_at` boundaries: the stored row is
absent strictly before its `known_at` and present from it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime

import duckdb
import pytest

from lookahead.harness import PROBE_EPSILON, TruncatedStore

#: The fixture's chainable pairs and their `successor_chains` rows, from
#: `chain_cases.json` (the spin-off pair has none).
_CASES = {
    "0001000011": ("0001000010", datetime.fromisoformat("2020-02-07T20:30:00+00:00")),
    "0001000013": ("0001000012", datetime.fromisoformat("2020-03-30T20:30:00+00:00")),
    "0001000015": ("0001000014", datetime.fromisoformat("2020-05-11T20:30:00+00:00")),
}


@pytest.fixture
def truncated(fixture_store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    store = TruncatedStore(fixture_store)
    try:
        yield store
    finally:
        store.close()


def test_the_fixture_stores_a_chain_row_per_chainable_pair(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    rows = fixture_store.execute(
        "SELECT successor_id, predecessor_id, known_at FROM successor_chains ORDER BY successor_id"
    ).fetchall()
    assert rows == [
        (successor, predecessor, known_at)
        for successor, (predecessor, known_at) in sorted(_CASES.items())
    ]
    assert fixture_store.execute(
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
    fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore
) -> None:
    """Every probe sees the fixture rows with `known_at <= T` and no others."""
    probes = [known_at_ for _predecessor, known_at_ in _CASES.values()]
    for known_at in sorted(probes):
        for t in (known_at - PROBE_EPSILON, known_at, known_at + PROBE_EPSILON):
            expected = fixture_store.execute(
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


#: The read rule `schema.successor_chains` documents: the live row with the
#: greatest `evidence_known_at <= t` that no retraction *known at t* names.
_LIVE_AT_SQL = """
    SELECT evidence_known_at FROM successor_chains
    WHERE successor_id = '0001000011' AND NOT retracted
      AND known_at <= ? AND evidence_known_at <= ?
      AND evidence_known_at NOT IN (
          SELECT withdrawn_known_at FROM successor_chains
          WHERE retracted AND known_at <= ?
      )
    ORDER BY evidence_known_at DESC LIMIT 1
"""


def test_a_moved_chain_is_absent_from_the_old_stamp_once_the_run_is_known(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """#821: after a move the chain never reads from the withdrawn stamp: the
    old stamp is live only before the run that moved it is known, and the new
    stamp is live from its own `known_at` (the truncation harness holds)."""
    from tradepartner.store.db import insert_row

    successor = "0001000011"
    old = datetime.fromisoformat("2020-02-07T20:30:00+00:00")
    new = datetime.fromisoformat("2020-03-08T20:30:00+00:00")
    clock = datetime.fromisoformat("2020-04-01T04:00:00+00:00")

    def at(t: datetime) -> list[datetime]:
        return [row[0] for row in fixture_store.execute(_LIVE_AT_SQL, [t, t, t]).fetchall()]

    assert at(old) == [old]
    assert at(new) == [old]  # the fixture is unmoved: the old stamp, no new row yet
    insert_row(
        fixture_store,
        "successor_chains",
        {
            "predecessor_id": "0001000010",
            "successor_id": successor,
            "evidence_accession": "0001000011-24-000001",
            "evidence_form": "8-K12B",
            "retracted": True,
            "withdrawn_known_at": old,
            "evidence_known_at": old,
            "known_at": clock,
            "ingested_at": clock,
            "source": "edgar",
            "provenance": "filing",
        },
    )
    insert_row(
        fixture_store,
        "successor_chains",
        {
            "predecessor_id": "0001000010",
            "successor_id": successor,
            "evidence_accession": "0001000011-24-000001",
            "evidence_form": "8-K12B",
            "retracted": False,
            "withdrawn_known_at": None,
            "evidence_known_at": new,
            "known_at": new,
            "ingested_at": clock,
            "source": "edgar",
            "provenance": "filing",
        },
    )
    assert at(new) == [new]  # the old stamp is still live before the run is known
    assert at(clock) == [new]  # and gone from it once the retraction is known
    assert at(clock.replace(year=2020, month=5)) == [new]
