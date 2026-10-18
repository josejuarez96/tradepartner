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
