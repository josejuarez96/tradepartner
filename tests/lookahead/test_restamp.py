"""No look-ahead in the store restamp from a stamps-repair manifest (#1382, T166b).

A fixture store is ingested from an "early" cache, whose stamps sit one New
York UTC offset before the true acceptance (the #1382 defect). The repaired
cache carries the true stamps, and a manifest lists each change. The restamp
must move exactly the rows written from those accessions, and the as-of
reads must change only inside each change's [old, new) span.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    DelistingFiling,
    FactRecord,
    FilingHeader,
    FilingIndexEntry,
    StatementFactRecord,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.config import Settings
from tradepartner.ingest import (
    MANIFEST_FIELDS,
    OK,
    RESTAMP_TABLES,
    ManifestChange,
    RestampResult,
    ingest_session,
    read_manifest,
)
from tradepartner.store.db import insert_row, open_for_write

ACME = "0000000001"  # restamped: every filing early by the offset
DUAL = "0000000002"  # unchanged (outside the manifest), or date-crossing
EXCHANGE = "0000876661"  # the exchange that files ACME's Form 25-NSE
SPY_TRUST = "0000884394"

FIRST_RUN = datetime(2019, 6, 29, 2, 0, tzinfo=UTC)
RESTAMP_RUN = datetime(2019, 7, 2, 2, 0, tzinfo=UTC)


def _true(year: int, month: int, day: int) -> datetime:
    """A true acceptance: 16:30 New York in EST months, 16:30 in EDT months."""
    est = month in (1, 2) or (month == 3 and day < 10)
    return datetime(year, month, day, 21 if est else 20, 30, tzinfo=UTC)


def _early(true: datetime) -> datetime:
    """The #1382 defect: the stamp one New York UTC offset early."""
    return true - (timedelta(hours=5) if true.hour == 21 else timedelta(hours=4))


# ACME's accessions and true stamps.
A_INDEX = (f"{ACME}-18-000001", _true(2018, 3, 1))
A_COVER = (f"{ACME}-19-000001", _true(2019, 3, 1))
A_25NSE = (f"{EXCHANGE}-19-000007", _true(2019, 5, 1))
# An 8-K12B before the 25-NSE: master relists ACME at the 25-NSE's stamp.
A_8K12B = (f"{ACME}-19-000050", _true(2019, 4, 29))
# DUAL's.
D_INDEX = (f"{DUAL}-18-000001", _true(2018, 3, 2))
D_COVER = (f"{DUAL}-19-000001", _true(2019, 3, 4))
# A statement fact accepted inside the paper window (never restamped).
S_STATEMENT = (f"{DUAL}-19-000009", _true(2019, 6, 10))


def _source(
    *,
    acme_early: bool,
    dual_early: bool = True,
    dual_cover_at: datetime | None = None,
    statements: bool = False,
    relisted: bool = False,
) -> FixtureFilingSource:
    """The cache: ACME's stamps early or repaired, DUAL's early by default."""

    def acme(stamp: datetime) -> datetime:
        return _early(stamp) if acme_early else stamp

    def dual(stamp: datetime) -> datetime:
        return _early(stamp) if dual_early else stamp

    dual_cover = dual_cover_at or dual(D_COVER[1])
    return FixtureFilingSource(
        index=[
            FilingIndexEntry(ACME, "Acme Corp", "10-K", A_INDEX[0], acme(A_INDEX[1])),
            FilingIndexEntry(DUAL, "Dual Corp", "10-K", D_INDEX[0], dual(D_INDEX[1])),
            *(
                [FilingIndexEntry(ACME, "Acme Corp", "8-K12B", A_8K12B[0], acme(A_8K12B[1]))]
                if relisted
                else []
            ),
        ],
        cover_pages=[
            CoverPage(
                ACME, A_COVER[0], acme(A_COVER[1]), (CoverListing("Common Stock", "ACME", "NYSE"),)
            ),
            CoverPage(
                DUAL,
                D_COVER[0],
                dual_cover,
                (
                    CoverListing("Class A Common Stock", "DUA", "NASDAQ"),
                    CoverListing("Class B Common Stock", "DUB", "NASDAQ"),
                ),
            ),
        ],
        headers=[
            FilingHeader(ACME, A_COVER[0], "10-K", 3571, acme(A_COVER[1])),
            FilingHeader(DUAL, D_COVER[0], "10-K", 7372, dual_cover),
        ],
        facts=[
            FactRecord(
                ACME,
                "EntityCommonStockSharesOutstanding",
                date(2019, 2, 15),
                "",
                5_000_000,
                A_COVER[0],
                acme(A_COVER[1]),
            ),
            FactRecord(
                DUAL,
                "EntityCommonStockSharesOutstanding",
                date(2019, 2, 15),
                "us-gaap:CommonClassAMember",
                9_000_000,
                D_COVER[0],
                dual_cover,
            ),
        ],
        delistings=[
            # The subject company is ACME; the stamp is read from the exchange's
            # stamps file (an exchange-only 25-NSE).
            DelistingFiling(ACME, "25-NSE", "Common Stock", "NYSE", A_25NSE[0], acme(A_25NSE[1])),
        ],
        snapshot=[
            CompanySnapshotEntry(
                SPY_TRUST, "SPDR S&P 500", "SPY", "NYSE_ARCA", datetime(2019, 6, 28, 23, tzinfo=UTC)
            )
        ],
        statement_facts=(
            [
                StatementFactRecord(
                    cik=DUAL,
                    fact_name="revenue",
                    xbrl_tag="Revenues",
                    period_start=date(2018, 4, 1),
                    period_end=date(2019, 3, 31),
                    value=1_000.0,
                    unit="USD",
                    form="10-K",
                    accession=S_STATEMENT[0],
                    accepted_at=S_STATEMENT[1],
                    filed=date(2019, 6, 10),
                    comparative=False,
                )
            ]
            if statements
            else []
        ),
    )


def _acme_manifest() -> list[ManifestChange]:
    return [
        ManifestChange(ACME, accession, _early(true), true, "shift")
        for accession, true in (A_INDEX, A_COVER)
    ] + [ManifestChange(EXCHANGE, A_25NSE[0], _early(A_25NSE[1]), A_25NSE[1], "sgml")]


class _NoPrices(PriceSource):
    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        raise AssertionError("an edgar-only run asks for no bars")

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        raise AssertionError("an edgar-only run asks for no actions")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
        edgar={"statement_facts_enabled": True},
    )


def _ingest(
    settings: Settings,
    source: FixtureFilingSource,
    *,
    now: datetime,
    restamp: Sequence[ManifestChange] | None = None,
    **kwargs: Any,
) -> tuple[str, str, list[RestampResult]]:
    results: list[RestampResult] = []
    outcome = ingest_session(
        settings,
        prices=_NoPrices(),
        filings=source,
        source="edgar",
        clock=lambda: now,
        restamp=restamp,
        on_restamp=results.append,
        **kwargs,
    )
    (run,) = outcome.runs
    return run.status, run.message, results


@dataclass
class _Store:
    path: str

    def rows(self, table: str, *, without: tuple[str, ...] = ()) -> list[tuple[Any, ...]]:
        exclude = f" EXCLUDE ({', '.join(without)})" if without else ""
        with duckdb.connect(self.path, read_only=True) as conn:
            conn.execute("SET TimeZone='UTC'")
            return sorted(conn.execute(f"SELECT *{exclude} FROM {table}").fetchall(), key=repr)

    def as_of(self, table: str, t: datetime) -> list[tuple[Any, ...]]:
        """Every row an as-of read at `t` can see, with `ingested_at` left out
        (the restamp's vintage clock, never a read input)."""
        with duckdb.connect(self.path, read_only=True) as conn:
            conn.execute("SET TimeZone='UTC'")
            return sorted(
                conn.execute(
                    f"SELECT * EXCLUDE (ingested_at) FROM {table} WHERE known_at <= ?", [t]
                ).fetchall(),
                key=repr,
            )

    def as_of_values(self, table: str, t: datetime) -> list[tuple[Any, ...]]:
        """`as_of` without the stamp columns the restamp moves: what a read
        at `t` sees, compared across a restamp."""
        stamps = "ingested_at, known_at" + (", filed_at" if table == "delistings" else "")
        with duckdb.connect(self.path, read_only=True) as conn:
            conn.execute("SET TimeZone='UTC'")
            return sorted(
                conn.execute(
                    f"SELECT * EXCLUDE ({stamps}) FROM {table} WHERE known_at <= ?", [t]
                ).fetchall(),
                key=repr,
            )

    def known_ats(self, table: str, cik: str) -> set[datetime]:
        with duckdb.connect(self.path, read_only=True) as conn:
            conn.execute("SET TimeZone='UTC'")
            return {
                r[0]
                for r in conn.execute(
                    f"SELECT t.known_at FROM {table} t JOIN securities s USING (security_id) "
                    "WHERE s.cik = ?",
                    [cik],
                ).fetchall()
            }


@pytest.fixture
def early_store(settings: Settings) -> _Store:
    status, message, _ = _ingest(settings, _source(acme_early=True), now=FIRST_RUN)
    assert status == OK, message
    return _Store(settings.store.path)


def _snapshot(store: _Store) -> dict[str, list[tuple[Any, ...]]]:
    return {t: store.rows(t) for t in (*RESTAMP_TABLES, "statement_facts")}


def test_the_moved_rows_carry_the_new_stamp_and_none_keeps_the_old(
    settings: Settings, early_store: _Store
) -> None:
    olds = {_early(t) for _, t in (A_INDEX, A_COVER, A_25NSE)}
    news = {t for _, t in (A_INDEX, A_COVER, A_25NSE)}
    assert any(early_store.known_ats(t, ACME) & olds for t in RESTAMP_TABLES)

    status, message, (result,) = _ingest(
        settings, _source(acme_early=False), now=RESTAMP_RUN, restamp=_acme_manifest()
    )

    assert status == OK, message
    assert result.refused == {}
    for table in RESTAMP_TABLES:
        assert not early_store.known_ats(table, ACME) & olds, table
        assert early_store.known_ats(table, ACME) <= news, table
    assert result.moved["delistings"] == 1
    assert all(result.moved[t] >= 1 for t in RESTAMP_TABLES)
    # The delisting's stamp is its filed_at too; its ingested_at is the restamp's clock.
    ((filed_at, known_at, ingested_at),) = early_store.rows(
        "delistings",
        without=(
            "security_id",
            "form",
            "class_title",
            "exchange",
            "effective_on",
            "source",
            "provenance",
        ),
    )
    assert filed_at == known_at == A_25NSE[1]
    assert ingested_at == RESTAMP_RUN


def test_the_form_25_nse_moves_by_the_exchange_files_cik_not_the_subjects(
    settings: Settings, early_store: _Store
) -> None:
    """The 25-NSE's stamp is in the exchange's stamps file: a manifest row under
    ACME's own CIK for that accession moves no delisting; the exchange's does."""
    wrong = [ManifestChange(ACME, f"{ACME}-19-000077", _early(A_25NSE[1]), A_25NSE[1], "sgml")]
    _, _, (result,) = _ingest(settings, _source(acme_early=True), now=RESTAMP_RUN, restamp=wrong)
    assert result.moved["delistings"] == 0
    assert early_store.known_ats("delistings", ACME) == {_early(A_25NSE[1])}

    right = [ManifestChange(EXCHANGE, A_25NSE[0], _early(A_25NSE[1]), A_25NSE[1], "sgml")]
    _, _, (result,) = _ingest(settings, _source(acme_early=False), now=RESTAMP_RUN, restamp=right)
    assert result.moved["delistings"] == 1
    assert early_store.known_ats("delistings", ACME) == {A_25NSE[1]}
    # Only the delisting: ACME's other rows keep their (early) stamps.
    assert result.moved["securities"] == result.moved["facts"] == 0


def test_a_relisting_stamped_by_the_exchange_filed_25_nse_moves_with_it(
    settings: Settings,
) -> None:
    """quant-auditor on #1487: with an 8-K12B before the Form 25, master writes
    the subject's relisting row at the 25-NSE's stamp, which the exchange's
    stamps file holds. It moves with the 25-NSE's change, to where a fresh
    build from the repaired cache puts it."""
    store = _Store(settings.store.path)
    status, message, _ = _ingest(settings, _source(acme_early=True, relisted=True), now=FIRST_RUN)
    assert status == OK, message
    assert _early(A_25NSE[1]) in store.known_ats("listings", ACME)
    manifest = [
        *_acme_manifest(),
        ManifestChange(ACME, A_8K12B[0], _early(A_8K12B[1]), A_8K12B[1], "shift"),
    ]

    status, message, (result,) = _ingest(
        settings, _source(acme_early=False, relisted=True), now=RESTAMP_RUN, restamp=manifest
    )

    assert status == OK, message
    assert result.refused == {}
    fresh_settings = settings.model_copy(
        update={
            "store": settings.store.model_copy(
                update={"path": str(Path(settings.store.path).with_name("fresh.duckdb"))}
            )
        }
    )
    _ingest(fresh_settings, _source(acme_early=False, relisted=True), now=RESTAMP_RUN)
    fresh = _Store(fresh_settings.store.path)
    for table in RESTAMP_TABLES:
        assert store.known_ats(table, ACME) == fresh.known_ats(table, ACME), table


def test_unchanged_ciks_snapshot_rows_retractions_and_ingest_stamped_rows_stay_byte_identical(
    settings: Settings, early_store: _Store
) -> None:
    acme_cover_old = _early(A_COVER[1])
    with open_for_write(settings) as conn:
        # A retraction at ACME's old stamp, and a row stamped at its own ingest
        # at that stamp: neither is a filing's stamp, so neither moves.
        insert_row(
            conn,
            "securities",
            {
                "security_id": f"{ACME}:retracted",
                "cik": ACME,
                "name": "Acme Corp",
                "benchmark": False,
                "retracted": True,
                "known_at": acme_cover_old,
                "ingested_at": FIRST_RUN,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        insert_row(
            conn,
            "classifications",
            {
                "security_id": f"{ACME}:retracted",
                "sic": 3571,
                "security_type": "common",
                "rule": "revision",
                "known_at": acme_cover_old,
                "ingested_at": acme_cover_old,
                "source": "edgar",
                "provenance": "filing",
            },
        )
    before = _snapshot(early_store)

    status, message, _ = _ingest(
        settings, _source(acme_early=False), now=RESTAMP_RUN, restamp=_acme_manifest()
    )

    assert status == OK, message
    after = _snapshot(early_store)
    for table in (*RESTAMP_TABLES, "statement_facts"):
        kept = [r for r in before[table] if ACME not in repr(r) or ":retracted" in repr(r)]
        assert set(kept) <= set(after[table]), table


def test_every_as_of_read_changes_only_inside_old_to_new(
    settings: Settings, early_store: _Store
) -> None:
    probes = sorted(
        {
            t + d
            for _, true in (A_INDEX, A_COVER, A_25NSE, D_INDEX, D_COVER)
            for t in (_early(true), true)
            for d in (-timedelta(seconds=1), timedelta(0), timedelta(hours=1))
        }
    )
    before = {
        (table, t): early_store.as_of_values(table, t) for table in RESTAMP_TABLES for t in probes
    }

    _ingest(settings, _source(acme_early=False), now=RESTAMP_RUN, restamp=_acme_manifest())

    spans = [(_early(true), true) for _, true in (A_INDEX, A_COVER, A_25NSE)]
    dropped = 0
    for table in RESTAMP_TABLES:
        for t in probes:
            now = early_store.as_of_values(table, t)
            inside = [old for old, new in spans if old <= t < new]
            if not inside:
                # Outside every [old, new): the same rows.
                assert now == before[(table, t)], (table, t)
            else:
                # Inside: a row moved out of the read, none added.
                assert set(now) <= set(before[(table, t)]), (table, t)
                dropped += len(before[(table, t)]) - len(now)
                assert not any(old in r for r in early_store.as_of(table, t) for old in inside), (
                    table,
                    t,
                )
    assert dropped > 0  # some read inside a span no longer sees its row


def test_a_date_crossing_change_is_refused_and_listed_and_moves_none_of_its_cik(
    settings: Settings, early_store: _Store
) -> None:
    old = _early(D_COVER[1])
    crossing = datetime(2019, 3, 5, 5, 30, tzinfo=UTC)  # 00:30 New York the next day
    manifest = [*_acme_manifest(), ManifestChange(DUAL, D_COVER[0], old, crossing, "sgml")]
    dual_before = {t: early_store.known_ats(t, DUAL) for t in RESTAMP_TABLES}

    # The cache holds DUAL's repaired stamp: the day's build writes none of
    # the refused CIK's rows beside the stored ones.
    status, message, (result,) = _ingest(
        settings,
        _source(acme_early=False, dual_cover_at=crossing),
        now=RESTAMP_RUN,
        restamp=manifest,
    )

    assert status == OK, message
    assert set(result.refused) == {DUAL}
    assert "listings" in result.refused[DUAL]
    assert DUAL in message
    assert {t: early_store.known_ats(t, DUAL) for t in RESTAMP_TABLES} == dual_before
    assert early_store.known_ats("facts", ACME) == {A_COVER[1]}


def test_two_new_stamps_for_one_old_stamp_refuse_the_cik(
    settings: Settings, early_store: _Store
) -> None:
    old = _early(A_COVER[1])
    manifest = [
        ManifestChange(ACME, A_COVER[0], old, A_COVER[1], "sgml"),
        ManifestChange(ACME, f"{ACME}-19-000002", old, A_COVER[1] + timedelta(minutes=1), "sgml"),
    ]
    _, _, (result,) = _ingest(settings, _source(acme_early=True), now=RESTAMP_RUN, restamp=manifest)
    assert set(result.refused) == {ACME}
    assert sum(result.moved.values()) == 0


def _open_window(settings: Settings, started_at: datetime) -> None:
    with open_for_write(settings) as conn:
        insert_row(
            conn,
            "paper_windows",
            {
                "window_id": 1,
                "hypothesis_id": 1,
                "first_rebalance_session": date(2019, 6, 28),
                "account_id": "paper",
                "starting_cash": 100_000.0,
                "starting_equity": 100_000.0,
                "code_version": "test",
                "started_at": started_at,
                "frozen_json": "{}",
                "frozen_sha256": "0" * 64,
                "known_at": started_at,
                "ingested_at": started_at,
            },
        )


def test_a_change_inside_an_open_paper_window_aborts_and_writes_nothing(
    settings: Settings, early_store: _Store
) -> None:
    # Window opened on Sunday 2019-04-28: its first session is Monday 04-29,
    # before the 25-NSE's stamps (05-01).
    _open_window(settings, datetime(2019, 4, 28, 15, tzinfo=UTC))
    before = _snapshot(early_store)

    status, message, results = _ingest(
        settings, _source(acme_early=False), now=RESTAMP_RUN, restamp=_acme_manifest()
    )

    assert status != OK
    assert "2019-04-29" in message and A_25NSE[0] in message
    assert results == []
    assert _snapshot(early_store) == before


def test_statement_facts_from_the_window_start_are_identical_after_a_rebuild(
    settings: Settings,
) -> None:
    """The spec-critic follow-up on #1470: the rebuild re-inserts every row at
    its own clock, so `ingested_at` is left out of the comparison."""
    store = _Store(settings.store.path)
    status, message, _ = _ingest(settings, _source(acme_early=True, statements=True), now=FIRST_RUN)
    assert status == OK, message
    window_start = datetime(2019, 6, 3, 4, tzinfo=UTC)  # Monday 2019-06-03, New York midnight
    _open_window(settings, window_start)

    def from_window(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
        return [r for r in rows if any(isinstance(v, datetime) and v >= window_start for v in r)]

    before = from_window(store.rows("statement_facts", without=("ingested_at",)))
    assert before

    status, message, _ = _ingest(
        settings,
        _source(acme_early=False, statements=True),
        now=RESTAMP_RUN,
        restamp=_acme_manifest(),
        rebuild_statement_facts=True,
    )

    assert status == OK, message
    assert from_window(store.rows("statement_facts", without=("ingested_at",))) == before


def test_restamping_a_cache_cut_at_t_gives_the_same_read_at_t(
    settings: Settings, tmp_path: Path
) -> None:
    t = _early(A_COVER[1]) + timedelta(hours=1)  # inside the cover page's [old, new)
    full = _Store(settings.store.path)
    _ingest(settings, _source(acme_early=True), now=FIRST_RUN)
    _ingest(settings, _source(acme_early=False), now=RESTAMP_RUN, restamp=_acme_manifest())

    cut_settings = settings.model_copy(
        update={"store": settings.store.model_copy(update={"path": str(tmp_path / "cut.duckdb")})}
    )
    cut = _Store(cut_settings.store.path)
    _ingest(cut_settings, _source(acme_early=True).known_by(t), now=FIRST_RUN)
    _ingest(
        cut_settings,
        _source(acme_early=False).known_by(t),
        now=RESTAMP_RUN,
        restamp=_acme_manifest(),
    )

    for table in RESTAMP_TABLES:
        assert cut.as_of(table, t) == full.as_of(table, t), table


def test_a_dry_run_writes_nothing(settings: Settings, early_store: _Store) -> None:
    before = _snapshot(early_store)
    _, _, (result,) = _ingest(
        settings,
        _source(acme_early=False),
        now=RESTAMP_RUN,
        restamp=_acme_manifest(),
        dry_run=True,
    )
    assert sum(result.moved.values()) > 0
    assert _snapshot(early_store) == before


def test_a_failure_after_the_writes_rolls_the_restamp_back(
    settings: Settings, early_store: _Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any security_id difference raises, and the chunk commits nothing."""
    from tradepartner import ingest

    calls = iter(range(100))
    real = ingest._security_ids

    def drifting(conn: duckdb.DuckDBPyConnection, table: str) -> frozenset[str]:
        ids = real(conn, table)
        return ids | {"minted"} if next(calls) >= len(RESTAMP_TABLES) else ids

    monkeypatch.setattr(ingest, "_security_ids", drifting)
    before = _snapshot(early_store)

    status, message, _ = _ingest(
        settings, _source(acme_early=False), now=RESTAMP_RUN, restamp=_acme_manifest()
    )

    assert status != OK
    assert "security_ids" in message
    assert _snapshot(early_store) == before


def test_a_subset_manifest_then_the_rest_and_a_rerun_moves_nothing_more(
    settings: Settings, early_store: _Store
) -> None:
    """The owner's two-phase release: one CIK's changes, then another's; a
    repeated manifest finds no row at its old stamps."""
    _, _, (first,) = _ingest(
        settings, _source(acme_early=False), now=RESTAMP_RUN, restamp=_acme_manifest()
    )
    assert early_store.known_ats("securities", DUAL) == {_early(D_INDEX[1]), _early(D_COVER[1])}

    dual = [
        ManifestChange(DUAL, accession, _early(true), true, "shift")
        for accession, true in (D_INDEX, D_COVER)
    ]
    later = RESTAMP_RUN + timedelta(days=1)
    status, message, (second,) = _ingest(
        settings, _source(acme_early=False, dual_early=False), now=later, restamp=dual
    )
    assert status == OK, message
    assert second.refused == {}, second.refused
    assert second.moved["securities"] >= 1, second.moved
    assert early_store.known_ats("securities", DUAL) == {D_INDEX[1], D_COVER[1]}

    _, _, (again,) = _ingest(
        settings,
        _source(acme_early=False, dual_early=False),
        now=later + timedelta(days=1),
        restamp=[*_acme_manifest(), *dual],
    )
    assert sum(again.moved.values()) == 0
    assert first.refused == second.refused == again.refused == {}


def test_read_manifest_reads_t166s_columns(tmp_path: Path) -> None:
    path = tmp_path / "manifest.csv"
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(MANIFEST_FIELDS))
        writer.writeheader()
        writer.writerow(
            {
                "cik": ACME,
                "accession": A_COVER[0],
                "old_stamp": _early(A_COVER[1]).isoformat(),
                "new_stamp": A_COVER[1].isoformat(),
                "method": "shift",
                "evidence": "class early",
            }
        )
    (row,) = read_manifest(path)
    assert (row.cik, row.accession, row.new_stamp, row.changed) == (
        ACME,
        A_COVER[0],
        A_COVER[1],
        True,
    )
    path.write_text("cik,accession\n")
    with pytest.raises(ValueError, match="old_stamp"):
        read_manifest(path)
    header = ",".join(MANIFEST_FIELDS)
    path.write_text(f"{header}\n{ACME},{A_COVER[0]}\n")
    with pytest.raises(ValueError, match="too few fields"):
        read_manifest(path)
    path.write_text(
        f"{header}\n1,{A_COVER[0]},{A_COVER[1].isoformat()},{A_COVER[1].isoformat()},kept,x\n"
    )
    with pytest.raises(ValueError, match="10 digits"):
        read_manifest(path)
