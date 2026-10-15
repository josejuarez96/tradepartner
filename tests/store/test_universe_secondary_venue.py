"""Universe rule 2: a newer row on a real venue off the list (T196, #1682 lever A).

The latest listing row of a class can sit on a secondary quote venue
(`NYSE_CHICAGO`, `NYSETX`) while an older row on `universe.exchanges` is
still live; the name was then excluded as `exchange:NYSE_CHICAGO`. Rule 2
now passes it while any row at or before the session is live on the list,
and still refuses a newer row on `OTC`, `NONE` or no name, a name whose list
row a Form 25 ended, and a name with no row on the list at all.

Also pinned, as reads at `t` (no look-ahead): a row known after `t` moves
nothing at `t`. #1574's same-day tie cases live in `test_universe_exchange_tie.py`.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pytest

from tradepartner.calendar import last_completed_session
from tradepartner.config import Settings
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.schema import init_schema
from tradepartner.universe import _current_listings, universe_as_of

RUN = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
COMMON = {"ingested_at": RUN, "source": "edgar", "provenance": "filing"}
FILED = datetime(2015, 3, 1, 21, tzinfo=UTC)
T = datetime(2021, 6, 30, 21, tzinfo=UTC)

#: security -> its listing rows (exchange, valid_from, known_at).
OLD = (date(2019, 1, 2), datetime(2019, 1, 2, 21, tzinfo=UTC))
NEW = (date(2020, 6, 1), datetime(2020, 6, 1, 21, tzinfo=UTC))
LATE_KNOWN = datetime(2021, 9, 1, 21, tzinfo=UTC)

CASES: dict[str, list[tuple[str, date, datetime]]] = {
    "CHI": [("NYSE", *OLD), ("NYSE_CHICAGO", *NEW)],
    "TX": [("NASDAQ", *OLD), ("NYSETX", *NEW)],
    "OTC": [("NYSE", *OLD), ("OTC", *NEW)],
    "NONE": [("NYSE", *OLD), ("NONE", *NEW)],
    "ENDED": [("NYSE", *OLD), ("NYSE_CHICAGO", *NEW)],
    "ONLYCHI": [("NYSE_CHICAGO", *OLD)],
    "ARCA": [("NYSE_ARCA", *OLD), ("NYSE_CHICAGO", *NEW)],
    "PLAIN": [("NYSE", *OLD)],
    # The older NYSE row is known only after T: at T the name has one row.
    "LATEOLD": [("NYSE", OLD[0], LATE_KNOWN), ("NYSE_CHICAGO", *NEW)],
    # An OTC row known after T moves nothing at T.
    "LATEOTC": [("NYSE", *OLD), ("OTC", date(2021, 9, 1), LATE_KNOWN)],
}


def _build(conn: duckdb.DuckDBPyConnection, cases: dict[str, Any]) -> None:
    for sid, rows in cases.items():
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
    # ENDED: a Form 25 for its NYSE class, filed 2020-02, long before the
    # Chicago row (outside any transfer window): the NYSE listing is ended.
    insert_row(
        conn,
        "delistings",
        {
            "security_id": "ENDED",
            "form": "25",
            "class_title": "Common Stock",
            "exchange": "NYSE",
            "filed_at": datetime(2020, 2, 3, 21, tzinfo=UTC),
            "effective_on": date(2020, 2, 13),
            "known_at": datetime(2020, 2, 3, 21, tzinfo=UTC),
            **COMMON,
        },
    )


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    configure_connection(c)
    init_schema(c)
    _build(c, CASES)
    yield c
    c.close()


def _rule_2(conn: duckdb.DuckDBPyConnection, t: datetime = T) -> dict[str, str]:
    """Per security, the rule 2 reason (`""` if it passes rule 2)."""
    u = universe_as_of(conn, t, Settings(_env_file=None))
    out = {sid: "" for sid in CASES}
    for row in u.exclusions.iter_rows(named=True):
        out[row["security_id"]] = row["reason"] if row["rule_name"] == "exchange" else ""
    return out


def test_a_secondary_quote_row_does_not_hide_a_live_listing_on_the_list(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    reasons = _rule_2(conn)
    assert reasons["CHI"] == ""
    assert reasons["TX"] == ""
    assert reasons["PLAIN"] == ""


def test_the_newer_row_on_no_venue_is_a_move_off_the_exchange(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    reasons = _rule_2(conn)
    assert reasons["OTC"] == "exchange:OTC"
    assert reasons["NONE"] == "exchange:NONE"


def test_a_listing_ended_by_its_own_form_25_is_not_live(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    assert _rule_2(conn)["ENDED"] == "exchange:NYSE_CHICAGO"


def test_no_row_on_the_list_still_fails(conn: duckdb.DuckDBPyConnection) -> None:
    reasons = _rule_2(conn)
    assert reasons["ONLYCHI"] == "exchange:NYSE_CHICAGO"
    assert reasons["ARCA"] == "exchange:NYSE_CHICAGO"


def test_the_current_listing_is_still_the_newest_row(conn: duckdb.DuckDBPyConnection) -> None:
    """Rule 2's pass does not change which row is current: members report the
    newer row's exchange, and the flag is set only on the securities it lifts."""
    current = _current_listings(conn, T, last_completed_session(T), Settings(_env_file=None))
    assert current["CHI"]["exchange"] == "NYSE_CHICAGO"
    assert current["CHI"]["on_listed_exchange"] is True
    assert current["ONLYCHI"]["on_listed_exchange"] is False
    assert current["ARCA"]["on_listed_exchange"] is False


def test_a_row_known_after_t_moves_nothing_at_t(conn: duckdb.DuckDBPyConnection) -> None:
    """No look-ahead: LATEOLD's NYSE row and LATEOTC's OTC row are known after
    T. At T the first has only its Chicago row (fails) and the second only
    its NYSE row (passes); once known (and valid) both read as their rows say."""
    before = _rule_2(conn)
    assert before["LATEOLD"] == "exchange:NYSE_CHICAGO"
    assert before["LATEOTC"] == ""
    after = _rule_2(conn, datetime(2021, 10, 1, 21, tzinfo=UTC))
    assert after["LATEOLD"] == ""
    assert after["LATEOTC"] == "exchange:OTC"
