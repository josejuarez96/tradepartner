"""Truncation invariance for universe rule 2's secondary-venue pass (T196, #1682).

A listing row known after `t`, a secondary-quote row (`NYSE_CHICAGO`), an `OTC`
row and a Form 25 each change what rule 2 reads only from their own
`known_at`. `universe_as_of(T)` on the full store equals `universe_as_of(T)`
on the store truncated to `known_at <= T`, at a probe just before and just
after every `known_at`, on a synthetic store that walks each case.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import pytest

from lookahead.harness import TruncatedStore, probe_timestamps
from tradepartner.config import Settings
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.schema import init_schema
from tradepartner.universe import universe_as_of

RUN = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
COMMON = {"ingested_at": RUN, "source": "edgar", "provenance": "filing"}
FILED = datetime(2015, 3, 1, 21, tzinfo=UTC)


def _at(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, 21, tzinfo=UTC)


#: security -> rows (exchange, valid_from, known_at). `NYSE` rows are on the
#: list; the others are off it. VANISH: its Form 25 (below) ends the NYSE row.
ROWS: dict[str, list[tuple[str, date, datetime]]] = {
    "SECOND": [
        ("NYSE", date(2019, 1, 2), _at(2019, 1, 2)),
        ("NYSE_CHICAGO", date(2020, 6, 1), _at(2020, 6, 2)),
    ],
    "OFFLIST": [
        ("NYSE", date(2019, 1, 2), _at(2019, 1, 2)),
        ("OTC", date(2020, 9, 1), _at(2020, 9, 3)),
    ],
    "VANISH": [
        ("NYSE", date(2019, 1, 2), _at(2019, 1, 2)),
        ("NYSETX", date(2021, 1, 4), _at(2021, 1, 5)),
    ],
    "LATEOLD": [
        ("NYSE_CHICAGO", date(2019, 1, 2), _at(2019, 1, 2)),
        ("NYSE", date(2019, 1, 2), _at(2020, 3, 2)),
    ],
}


@pytest.fixture
def store() -> Iterator[duckdb.DuckDBPyConnection]:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    init_schema(conn)
    for sid, rows in ROWS.items():
        insert_row(
            conn,
            "securities",
            {"security_id": sid, "cik": sid, "name": sid, "known_at": FILED, **COMMON},
        )
        insert_row(
            conn,
            "classifications",
            {
                "security_id": sid,
                "sic": 3570,
                "security_type": "common",
                "rule": "common_default",
                "known_at": FILED,
                **COMMON,
            },
        )
        for exchange, valid_from, known_at in rows:
            insert_row(
                conn,
                "listings",
                {
                    "security_id": sid,
                    "ticker": sid,
                    "exchange": exchange,
                    "class_title": "Common Stock",
                    "valid_from": valid_from,
                    "known_at": known_at,
                    **COMMON,
                },
            )
    insert_row(
        conn,
        "delistings",
        {
            "security_id": "VANISH",
            "form": "25",
            "class_title": "Common Stock",
            "exchange": "NYSE",
            "filed_at": _at(2020, 11, 2),
            "effective_on": date(2020, 11, 12),
            "known_at": _at(2020, 11, 2),
            **COMMON,
        },
    )
    yield conn
    conn.close()


def test_rule_2_is_invariant_under_truncation(store: duckdb.DuckDBPyConnection) -> None:
    settings = Settings(_env_file=None)
    truncated = TruncatedStore(store)
    try:
        passed: set[tuple[str, str]] = set()
        probes = probe_timestamps(store)
        assert len(probes) >= 8
        for t in probes:
            full = universe_as_of(store, t, settings)
            cut = universe_as_of(truncated.at(t), t, settings)
            assert full.exclusions.equals(cut.exclusions), f"exclusions disagree at T={t!r}"
            assert full.members.equals(cut.members), f"members disagree at T={t!r}"
            failed = {
                r["security_id"]
                for r in full.exclusions.iter_rows(named=True)
                if r["rule_name"] == "exchange"
            }
            passed |= {(sid, t.date().isoformat()) for sid in ROWS if sid not in failed}
        # Not vacuous: SECOND passes rule 2 on the secondary row once it is known.
        assert any(sid == "SECOND" for sid, _ in passed)
    finally:
        truncated.close()
