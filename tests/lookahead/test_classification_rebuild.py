"""No look-ahead in the classification rebuild (#1541, plan T182).

`ingest --source edgar --rebuild-classifications` deletes the covered
securities' `classifications` rows and writes the build's own, each at the
instant its classification first became knowable (`store.classify`'s timing
rule), never at the run. The fixture issuer is a domestic filer (a 10-K and
a titled cover page at T1, SIC 3571 on its header) that files an N-CSR at
T3 and so becomes a `fund`; a stale row an older rule set wrote sits at T1.

- No rebuilt row is known after the run or before the evidence it used: each
  `known_at` is one of the issuer's acceptance instants.
- The rebuild never stamps a row at the run, and the read at T1 is the
  build's, where a plain re-ingest keeps the stale row.
- Truncation: a rebuild over a cache cut at T gives the same read at T as a
  rebuild over the full cache.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from tradepartner.adapters.filings import (
    CoverListing,
    CoverPage,
    FilingHeader,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.config import Settings
from tradepartner.ingest import OK, ingest_session
from tradepartner.store.classify import classifications_as_of
from tradepartner.store.db import insert_row, open_for_write

CIK = "0000000077"
T1 = datetime(2019, 3, 1, 21, 30, tzinfo=UTC)  # 10-K and cover page accepted
T3 = datetime(2019, 9, 3, 20, 30, tzinfo=UTC)  # N-CSR accepted: a fund
FIRST_RUN = datetime(2019, 10, 1, 2, 0, tzinfo=UTC)
REBUILD_RUN = datetime(2019, 10, 8, 2, 0, tzinfo=UTC)


class _NoPrices(PriceSource):
    def bars(self, security_ids: object, start: object, end: object) -> list[Bar]:
        return []

    def corporate_actions(
        self, security_ids: object, start: object, end: object
    ) -> list[CorporateAction]:
        return []


def _source(until: datetime | None = None) -> FixtureFilingSource:
    """The issuer's evidence accepted at or before `until` (all by default)."""
    index = [
        FilingIndexEntry(CIK, "Acme Co", "10-K", "k1", T1),
        FilingIndexEntry(CIK, "Acme Co", "N-CSR", "n1", T3),
    ]
    page = CoverPage(CIK, "k1", T1, (CoverListing("Common Stock", "ACME", "NYSE"),))
    return FixtureFilingSource(
        index=[e for e in index if until is None or e.accepted_at <= until],
        cover_pages=[page],
        headers=[FilingHeader(CIK, "k1", "10-K", 3571, T1)],
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def _ingest(settings: Settings, now: datetime, source: FixtureFilingSource, **kw: bool) -> None:
    result = ingest_session(
        settings,
        prices=_NoPrices(),
        filings=source,
        source="edgar",
        clock=lambda: now,
        **kw,
    )
    assert [r.status for r in result.runs] == [OK], [r.message for r in result.runs]


def _rows(settings: Settings) -> list[tuple[str, str, datetime]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return [
            (r[0], r[1], r[2])
            for r in conn.execute(
                "SELECT security_id, security_type, known_at FROM classifications "
                "WHERE security_id = ? ORDER BY known_at",
                [CIK],
            ).fetchall()
        ]


def _read(settings: Settings, t: datetime) -> list[str]:
    """The security types the as-of read gives at `t` (empty before any row)."""
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return classifications_as_of(conn, t, [CIK])["security_type"].to_list()


def _stale(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        conn.execute("DELETE FROM classifications WHERE security_id = ?", [CIK])
        insert_row(
            conn,
            "classifications",
            {
                "security_id": CIK,
                "sic": None,
                "security_type": "unclassifiable",
                "rule": "title_unrecognized",
                "known_at": T1,
                "ingested_at": T1,
                "source": "edgar",
                "provenance": "filing",
            },
        )


def test_rebuilt_rows_are_known_at_their_evidence_never_at_the_run(settings: Settings) -> None:
    _ingest(settings, FIRST_RUN, _source())
    _stale(settings)
    _ingest(settings, REBUILD_RUN, _source())  # a plain re-ingest keeps the stale read at T1
    assert _read(settings, T1) == ["unclassifiable"]  # forward-only without the flag
    _ingest(settings, REBUILD_RUN, _source(), rebuild_classifications=True)
    rows = _rows(settings)
    assert rows
    for _, _, known_at in rows:
        assert known_at <= REBUILD_RUN
        assert known_at in {T1, T3}  # an acceptance of the issuer's own evidence
    assert REBUILD_RUN not in {r[2] for r in rows}
    assert [r[1] for r in rows] == ["common", "fund"]
    assert _read(settings, T1) == ["common"]
    assert _read(settings, T1 - timedelta(microseconds=1)) == []
    assert _read(settings, T3 - timedelta(microseconds=1)) == ["common"]
    assert _read(settings, T3) == ["fund"]


def test_a_rebuild_over_a_cache_cut_at_t_reads_the_same_at_t(
    settings: Settings, tmp_path: Path
) -> None:
    cut = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "cut.duckdb"), "lock_retry_seconds": 1},
    )
    _ingest(settings, FIRST_RUN, _source())
    _ingest(settings, REBUILD_RUN, _source(), rebuild_classifications=True)
    _ingest(cut, FIRST_RUN, _source(until=T1))
    _ingest(cut, REBUILD_RUN, _source(until=T1), rebuild_classifications=True)
    probes = [T1 - timedelta(microseconds=1), T1, T3 - timedelta(microseconds=1)]
    for t in probes:
        assert _read(settings, t) == _read(cut, t)
