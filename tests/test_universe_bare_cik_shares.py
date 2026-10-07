"""Rule 7 reads the bare-CIK shares facts for an issuer's one listed common
class (#1165, part of #1018).

EDGAR files `EntityCommonStockSharesOutstanding` under the issuer, so the
store keeps it on the bare-CIK security (`security_id == cik`), while the
traded class may be a sub-class security (`<cik>:<class-slug>`: de-SPACs,
VRT, FWONK). When the issuer has exactly one class at `t` that passes rules
1-2 (a common class with a live listing on a universe exchange) and that
class has no shares fact of its own known at `t`, rule 7 reads the bare-CIK
facts for it. Two such classes: never a guess, the class stays `no_shares`.
Only facts known at `t` are read.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import pytest
from lookahead.harness import TruncatedStore

from tradepartner.config import Settings
from tradepartner.store.db import insert_row
from tradepartner.universe import Universe, shares_as_of, universe_as_of

SUB = "SEC_SPLIT_PLAIN"  # CIK0001000011's only security; no shares fact of its own
CIK = "CIK0001000011"  # the bare-CIK security id the issuer's facts sit on
T = datetime(2019, 12, 31, 22, 0, tzinfo=UTC)
AS_OF = date(2019, 10, 31)  # after SUB's 2-for-1 split (ex 2018-12-13)
EARLY = datetime(2018, 8, 6, 20, 30, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(_env_file=None)


def _known(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, 21, 0, tzinfo=UTC)


def _fact(
    conn: duckdb.DuckDBPyConnection,
    security_id: str,
    as_of: date,
    value: float,
    known: datetime,
    class_member: str = "",
) -> None:
    insert_row(
        conn,
        "facts",
        {
            "security_id": security_id,
            "fact_name": "shares_outstanding",
            "as_of_date": as_of,
            "class_member": class_member,
            "value": value,
            "filing_accession": f"0000000000-{as_of:%y%m%d}-{class_member or 0}",
            "known_at": known,
            "ingested_at": known,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def _listed_class(
    conn: duckdb.DuckDBPyConnection, security_id: str, security_type: str, ticker: str
) -> None:
    """A class of CIK listed on NYSE since 2018-09-04, classified as
    `security_type`, with no bars."""
    stamps = {"known_at": EARLY, "ingested_at": EARLY, "source": "edgar", "provenance": "filing"}
    insert_row(
        conn,
        "securities",
        {"security_id": security_id, "cik": CIK, "name": "Plain Split Co", **stamps},
    )
    insert_row(
        conn,
        "classifications",
        {
            "security_id": security_id,
            "sic": 7372,
            "security_type": security_type,
            "rule": "common_default",
            **stamps,
        },
    )
    insert_row(
        conn,
        "listings",
        {
            "security_id": security_id,
            "ticker": ticker,
            "exchange": "NYSE",
            "class_title": "Common Stock",
            "valid_from": date(2018, 9, 4),
            **stamps,
        },
    )


def _excluded(u: Universe) -> dict[str, tuple[int, str]]:
    return {r["security_id"]: (r["rule"], r["reason"]) for r in u.exclusions.iter_rows(named=True)}


def _member(u: Universe, security_id: str) -> dict[str, Any]:
    (row,) = u.members.filter(u.members["security_id"] == security_id).iter_rows(named=True)
    return row


def test_without_bare_cik_facts_the_sub_class_has_no_shares(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    assert _excluded(universe_as_of(fixture_store, T, _settings()))[SUB] == (7, "no_shares")


def test_the_one_listed_class_reads_the_bare_cik_facts(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, CIK, AS_OF, 8_000_000, _known(date(2019, 11, 5)))
    u = universe_as_of(fixture_store, T, _settings())
    row = _member(u, SUB)
    assert row["shares"] == pytest.approx(8_000_000)
    assert row["market_cap"] == pytest.approx(8_000_000 * row["close"])
    assert SUB not in _excluded(u)


def test_a_bare_cik_fact_before_a_split_is_moved_by_the_class_split(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # Rule 8 moves the fact by the listed class's own splits (2-for-1 ex
    # 2018-12-13), as it would a fact filed on the class itself.
    _fact(fixture_store, CIK, date(2018, 11, 30), 4_000_000, _known(date(2018, 12, 3)))
    _fact(fixture_store, CIK, date(2019, 2, 28), 8_100_000, _known(date(2019, 3, 4)))
    pick = shares_as_of(fixture_store, T, [SUB], _settings(), bare_sources={SUB: CIK})
    assert pick.shares[SUB] == (date(2019, 2, 28), 8_100_000)
    assert pick.outliers.is_empty()  # 4M x2 split -> 8.1M is in line
    assert pick.mapped == {SUB: CIK}


def test_a_non_common_sibling_does_not_make_the_class_ambiguous(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # VRT: the warrants and units trade too, but rule 1 keeps only common.
    _listed_class(fixture_store, f"{CIK}:warrants", "warrant", "SPLT WS")
    _fact(fixture_store, CIK, AS_OF, 8_000_000, _known(date(2019, 11, 5)))
    u = universe_as_of(fixture_store, T, _settings())
    assert _member(u, SUB)["shares"] == pytest.approx(8_000_000)


def test_two_listed_common_classes_never_borrow_the_bare_cik_facts(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # The bare-CIK security is itself a listed common class: which class the
    # issuer total belongs to is a guess, so SUB stays `no_shares`.
    _listed_class(fixture_store, CIK, "common", "SPLT.V")
    _fact(fixture_store, CIK, AS_OF, 8_000_000, _known(date(2019, 11, 5)))
    assert _excluded(universe_as_of(fixture_store, T, _settings()))[SUB] == (7, "no_shares")


def test_two_listed_sub_classes_never_borrow_the_bare_cik_facts(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _listed_class(fixture_store, f"{CIK}:class-b-common-stock", "common", "SPLT.B")
    _fact(fixture_store, CIK, AS_OF, 8_000_000, _known(date(2019, 11, 5)))
    assert _excluded(universe_as_of(fixture_store, T, _settings()))[SUB] == (7, "no_shares")


def test_class_member_rows_on_the_bare_cik_stay_ambiguous(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    # MKC-style: the bare-CIK fact carries one row per class; never a guess.
    _fact(fixture_store, CIK, AS_OF, 1_000_000, _known(date(2019, 11, 5)), "CommonStock")
    _fact(fixture_store, CIK, AS_OF, 9_000_000, _known(date(2019, 11, 5)), "NonvotingCommonStock")
    assert _excluded(universe_as_of(fixture_store, T, _settings()))[SUB] == (
        7,
        "ambiguous_shares",
    )


def test_the_class_own_facts_win_over_the_bare_cik_facts(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, SUB, AS_OF, 7_000_000, _known(date(2019, 11, 5)))
    _fact(fixture_store, CIK, AS_OF, 8_000_000, _known(date(2019, 11, 5)))
    u = universe_as_of(fixture_store, T, _settings())
    assert _member(u, SUB)["shares"] == pytest.approx(7_000_000)


def test_a_stale_bare_cik_fact_is_stale_shares(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, CIK, date(2018, 10, 31), 4_000_000, _known(date(2018, 11, 5)))
    assert _excluded(universe_as_of(fixture_store, T, _settings()))[SUB] == (7, "stale_shares")


def test_without_sources_shares_as_of_reads_only_the_class_own_facts(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    _fact(fixture_store, CIK, AS_OF, 8_000_000, _known(date(2019, 11, 5)))
    pick = shares_as_of(fixture_store, T, [SUB], _settings())
    assert SUB not in pick.shares
    assert CIK not in pick.shares
    assert pick.mapped == {}


# --- No look-ahead ------------------------------------------------------------


LATE = datetime(2020, 2, 3, 20, 30, tzinfo=UTC)
CLASS_B = f"{CIK}:class-b-common-stock"


@pytest.fixture
def _later_rows(fixture_store: duckdb.DuckDBPyConnection) -> None:
    """The 2019-10-31 count filed only on 2020-01-03, and a second listed
    common class known only from 2020-02-03 (LATE)."""
    _fact(fixture_store, CIK, AS_OF, 8_000_000, _known(date(2020, 1, 3)))
    stamps = {"known_at": LATE, "ingested_at": LATE, "source": "edgar", "provenance": "filing"}
    insert_row(
        fixture_store,
        "securities",
        {"security_id": CLASS_B, "cik": CIK, "name": "Plain Split Co", **stamps},
    )
    insert_row(
        fixture_store,
        "classifications",
        {
            "security_id": CLASS_B,
            "sic": 7372,
            "security_type": "common",
            "rule": "common_default",
            **stamps,
        },
    )
    insert_row(
        fixture_store,
        "listings",
        {
            "security_id": CLASS_B,
            "ticker": "SPLT.B",
            "exchange": "NYSE",
            "class_title": "Class B Common Stock",
            "valid_from": date(2020, 2, 3),
            **stamps,
        },
    )


@pytest.fixture
def truncated(
    fixture_store: duckdb.DuckDBPyConnection, _later_rows: None
) -> Iterator[TruncatedStore]:
    store = TruncatedStore(fixture_store)
    try:
        yield store
    finally:
        store.close()


def test_a_bare_cik_fact_known_after_t_is_never_read(
    fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore
) -> None:
    # The fact is out at T and in once known; the second listed common class
    # takes the mapping away from LATE on, never before.
    settings = _settings()
    assert _excluded(universe_as_of(fixture_store, T, settings))[SUB] == (7, "no_shares")
    known = _known(date(2020, 1, 3))
    assert _member(universe_as_of(fixture_store, known, settings), SUB)["shares"] == 8_000_000
    after = datetime(2020, 2, 4, 22, 0, tzinfo=UTC)
    assert _excluded(universe_as_of(fixture_store, after, settings))[SUB] == (7, "no_shares")
    probes = [T, _known(date(2020, 1, 2)), known, LATE - timedelta(seconds=1), LATE, after]
    for t in probes:
        full = universe_as_of(fixture_store, t, settings)
        cut = universe_as_of(truncated.at(t), t, settings)
        assert full.members.equals(cut.members), t
        assert full.exclusions.equals(cut.exclusions), t
