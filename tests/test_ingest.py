"""Tests for single-session ingest (spec reqs 1, 9, 10; plan T16).

Scenarios run `ingest_session` against a real DuckDB file under `tmp_path`,
with a `FixtureFilingSource` of synthetic filings and `_Prices`, a stub
`PriceSource` whose bars, omissions and failures each test states.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest

from tradepartner import ingest as ingest_module
from tradepartner.adapters.edgar import StatementConflict
from tradepartner.adapters.edgar_source import EdgarFilingSource, SubmissionRecord
from tradepartner.adapters.edgar_validation import InputValidationError, ValidationFailures
from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    DelistingFiling,
    FactRecord,
    FilingHeader,
    FilingIndexEntry,
    FilingSource,
    StatementFactRecord,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.adapters.prices import (
    ActionType,
    Bar,
    CorporateAction,
    PriceSource,
    action_first_seen_known_at,
    bar_known_at,
)
from tradepartner.config import Settings
from tradepartner.health import statement_counts
from tradepartner.ingest import (
    FACT_NAMES,
    FAILED,
    LOCKED,
    OK,
    STALE,
    ConflictKey,
    IngestResult,
    SourceRun,
    StatementCounts,
    StatementKey,
    StatementVintage,
    _add_rows,
    _build_filings,
    _fetched,
    _ingest_filings,
    _may_count,
    _prefetch,
    _Recorded,
    _types_known,
    expected_session,
    fact_rows,
    ingest_session,
    insert_statement_frame,
    statement_fact_rows,
    statement_frame,
)
from tradepartner.store.asof import (
    facts_as_of,
    live_actions_as_of,
    prices_as_of,
    statement_facts_as_of,
)
from tradepartner.store.classify import build_classifications
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.master import build_master
from tradepartner.store.schema import init_schema

ACME = "0000000001"  # one class, NYSE
DUAL = "0000000002"  # Class A and Class B, NASDAQ
SPY_TRUST = "0000884394"
DUAL_B = f"{DUAL}:class-b-common-stock"
SPY = "BENCH:SPY"

SESSION = date(2019, 6, 28)  # a Friday
NOW = datetime(2019, 6, 29, 2, 0, tzinfo=UTC)  # Friday 22:00 ET, after close + settle
FETCHED_AT = datetime(2019, 6, 28, 23, 0, tzinfo=UTC)


def _at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 20, 30, tzinfo=UTC)


def _filings(
    fetched_at: datetime = FETCHED_AT,
    cls: type[FixtureFilingSource] = FixtureFilingSource,
    *,
    acme_title: str = "Common Stock",
    acme_extra: tuple[CoverListing, ...] = (),
    dual_listings: tuple[CoverListing, ...] | None = None,
    delistings: Sequence[DelistingFiling] = (),
    extra_facts: Sequence[FactRecord] = (),
    extra_index: Sequence[FilingIndexEntry] = (),
    extra_headers: Sequence[FilingHeader] = (),
    extra_snapshot: Sequence[CompanySnapshotEntry] = (),
    extra_covers: Sequence[CoverPage] = (),
) -> FixtureFilingSource:
    dual = dual_listings or (
        CoverListing("Class A Common Stock", "DUA", "NASDAQ"),
        CoverListing("Class B Common Stock", "DUB", "NASDAQ"),
    )
    return cls(
        delistings=delistings,
        index=[
            FilingIndexEntry(ACME, "Acme Corp", "10-K", f"{ACME}-18-000001", _at(2018, 3, 1)),
            FilingIndexEntry(DUAL, "Dual Corp", "10-K", f"{DUAL}-18-000001", _at(2018, 3, 2)),
            *extra_index,
        ],
        cover_pages=[
            CoverPage(
                ACME,
                f"{ACME}-19-000001",
                _at(2019, 3, 1),
                (CoverListing(acme_title, "ACME", "NYSE"), *acme_extra),
            ),
            CoverPage(
                DUAL,
                f"{DUAL}-19-000001",
                _at(2019, 3, 4),
                dual,
            ),
            *extra_covers,
        ],
        headers=[
            FilingHeader(ACME, f"{ACME}-19-000001", "10-K", 3571, _at(2019, 3, 1)),
            FilingHeader(DUAL, f"{DUAL}-19-000001", "10-K", 7372, _at(2019, 3, 4)),
            *extra_headers,
        ],
        facts=[
            _fact(ACME, "", 5_000_000, f"{ACME}-19-000001", _at(2019, 3, 1)),
            _fact(DUAL, "us-gaap:CommonClassAMember", 9_000_000, f"{DUAL}-19-1", _at(2019, 3, 4)),
            _fact(DUAL, "us-gaap:CommonClassBMember", 1_000_000, f"{DUAL}-19-1", _at(2019, 3, 4)),
            *extra_facts,
        ],
        snapshot=[
            CompanySnapshotEntry(SPY_TRUST, "SPDR S&P 500", "SPY", "NYSE_ARCA", fetched_at),
            *extra_snapshot,
        ],
    )


def _fact(cik: str, member: str, value: float, accession: str, at: datetime) -> FactRecord:
    return FactRecord(
        cik, "EntityCommonStockSharesOutstanding", date(2019, 2, 15), member, value, accession, at
    )


@dataclass
class _Prices(PriceSource):
    """Bars at `close` for every requested id and session, except `missing`;
    `fail` names a method that raises after the other has run."""

    close: float = 20.0
    missing: set[str] = field(default_factory=set)
    fail: str | None = None
    actions: list[CorporateAction] = field(default_factory=list)
    calls: list[tuple[str, tuple[str, ...], date, date]] = field(default_factory=list)

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        self.calls.append(("bars", tuple(security_ids), start, end))
        if self.fail == "bars":
            raise RuntimeError("bars endpoint down")
        c = self.close
        return [
            Bar(sid, start, c, c + 1, c - 1, c, 1_000_000, bar_known_at(start), "alpaca")
            for sid in sorted(security_ids)
            if sid not in self.missing
        ]

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        self.calls.append(("actions", tuple(security_ids), start, end))
        if self.fail == "actions":
            raise RuntimeError("actions endpoint down")
        return [a for a in self.actions if a.security_id in security_ids]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def _run(
    settings: Settings,
    prices: PriceSource | None = None,
    *,
    now: datetime = NOW,
    filings: FixtureFilingSource | None = None,
    **kwargs: Any,
) -> IngestResult:
    return ingest_session(
        settings,
        prices=prices if prices is not None else _Prices(),
        filings=filings if filings is not None else _filings(),
        clock=lambda: now,
        **kwargs,
    )


@pytest.fixture
def read(settings: Settings) -> Iterator[Callable[[str], list[tuple[Any, ...]]]]:
    def query(sql: str) -> list[tuple[Any, ...]]:
        with duckdb.connect(settings.store.path, read_only=True) as conn:
            conn.execute("SET TimeZone='UTC'")
            return conn.execute(sql).fetchall()

    yield query


def _counts(read: Callable[[str], list[tuple[Any, ...]]]) -> dict[str, int]:
    tables = ("securities", "listings", "classifications", "delistings", "facts")
    tables += ("prices_daily", "corporate_actions")
    return {t: read(f"SELECT count(*) FROM {t}")[0][0] for t in tables}


# --- expected session (spec req 10) -----------------------------------------


def _et(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    # June/July and November dates below: EDT (UTC-4) and EST (UTC-5).
    offset = 5 if month in (11, 12, 1, 2) else 4
    return datetime(year, month, day, hour + offset, minute, tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_et(2019, 6, 29, 12), date(2019, 6, 28)),  # weekend: Saturday -> Friday
        (_et(2019, 7, 4, 18), date(2019, 7, 3)),  # holiday: Independence Day -> the half day
        (_et(2019, 7, 2, 15), date(2019, 7, 1)),  # pre-close: Tuesday 15:00 -> Monday
        (_et(2019, 7, 2, 16, 30), date(2019, 7, 1)),  # inside the settle delay after the close
        (_et(2019, 7, 2, 17, 1), date(2019, 7, 2)),  # past close + 60 minutes
        (_et(2018, 11, 23, 13, 30), date(2018, 11, 21)),  # half day, 13:00 close + 30 min
        (_et(2018, 11, 23, 14, 1), date(2018, 11, 23)),  # half day, past its early close + 60
    ],
)
def test_expected_session_is_the_last_completed_one(
    settings: Settings, now: datetime, expected: date
) -> None:
    assert expected_session(now, settings) == expected


def test_settle_delay_comes_from_config() -> None:
    now = _et(2019, 7, 2, 16, 30)
    short = Settings(_env_file=None, ingest={"settle_delay_minutes": 15})
    assert expected_session(now, short) == date(2019, 7, 2)


# --- a normal run and a re-run ----------------------------------------------


def test_first_run_writes_both_sources_and_their_run_rows(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    prices = _Prices()
    result = _run(settings, prices)
    assert result.ok and result.exit_code == 0
    assert [(r.source, r.status) for r in result.runs] == [("edgar", OK), ("alpaca", OK)]
    rows = read("SELECT source, status, mode, rows_added, chunk_cursor FROM ingestion_runs")
    by_source = {r[0]: r[1:] for r in rows}
    assert by_source["alpaca"] == (OK, "session", 4, "2019-06-28")
    assert by_source["edgar"][:2] == (OK, "session") and by_source["edgar"][2] > 0
    fetched = {sid for call in prices.calls for sid in call[1]}
    assert fetched == {ACME, DUAL, DUAL_B, SPY}
    assert ("bars", tuple(sorted(fetched)), SESSION, SESSION) in prices.calls
    assert ("actions", tuple(sorted(fetched)), date(2019, 6, 1), SESSION) in prices.calls


def test_every_row_is_known_by_its_ingested_at_which_is_the_run_clock(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    _run(settings)
    for table in ("securities", "listings", "classifications", "facts", "prices_daily"):
        for known_at, ingested_at in read(f"SELECT known_at, ingested_at FROM {table}"):
            assert known_at <= ingested_at == NOW


def test_rerun_of_a_completed_session_adds_no_fact_rows(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    _run(settings)
    before = _counts(read)
    result = _run(settings, now=NOW + timedelta(hours=1))
    assert result.ok
    assert [r.rows_added for r in result.runs] == [0, 0]
    assert _counts(read) == before
    assert read("SELECT count(*) FROM ingestion_runs")[0][0] == 4


def test_a_later_snapshot_with_the_same_values_adds_nothing(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    _run(settings)
    before = _counts(read)
    later = FETCHED_AT + timedelta(days=3)
    _run(settings, filings=_filings(fetched_at=later), now=NOW + timedelta(days=3))
    after = _counts(read)
    assert after["securities"] == before["securities"]
    assert after["listings"] == before["listings"]


def test_a_re_fetched_bar_with_a_new_close_is_a_revision_at_ingested_at(
    settings: Settings,
) -> None:
    _run(settings, _Prices(close=20.0))
    later = NOW + timedelta(days=1)
    result = _run(settings, _Prices(close=21.0), now=later)
    assert result.runs[1].rows_added == 4
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        first = prices_as_of(conn, NOW, [ACME])
        second = prices_as_of(conn, later, [ACME])
    assert first["close"].to_list() == [20.0]
    assert second["close"].to_list() == [21.0]
    assert second["known_at"].to_list() == [later]


def test_a_revised_dividend_keeps_its_announcement_and_is_stamped_at_ingest(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    ex = date(2019, 6, 14)

    def dividend(amount: float) -> CorporateAction:
        known = action_first_seen_known_at(ex)
        return CorporateAction(ACME, ActionType.DIVIDEND, ex, amount, known, "alpaca")

    _run(settings, _Prices(actions=[dividend(0.10)]))
    later = NOW + timedelta(days=1)
    _run(settings, _Prices(actions=[dividend(0.12)]), now=later)
    rows = read("SELECT ratio_or_amount, known_at FROM corporate_actions ORDER BY known_at")
    assert rows == [(0.10, action_first_seen_known_at(ex)), (0.12, later)]


def test_a_re_dated_action_is_one_event_after_ingest(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    # #181: the same Alpaca id on a new ex-date is a revision, not a second split.
    def split(ex: date) -> CorporateAction:
        known = action_first_seen_known_at(ex)
        return CorporateAction(
            ACME, ActionType.SPLIT, ex, 2.0, known, "alpaca", source_action_id="a1"
        )

    _run(settings, _Prices(actions=[split(date(2019, 6, 14))]))
    later = NOW + timedelta(days=1)
    _run(settings, _Prices(actions=[split(date(2019, 6, 21))]), now=later)
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        live = live_actions_as_of(conn, later)
    assert live.select("ex_date", "source_action_id").rows() == [(date(2019, 6, 21), "a1")]


# --- staleness (spec req 10) ------------------------------------------------


def test_stale_reference_symbol_writes_only_the_run_row(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    result = _run(settings, _Prices(missing={SPY}))
    assert not result.ok and result.exit_code != 0
    alpaca = result.runs[-1]
    assert (alpaca.source, alpaca.status) == ("alpaca", STALE)
    assert "SPY" in alpaca.message
    assert _counts(read)["prices_daily"] == 0
    assert read("SELECT status, rows_added FROM ingestion_runs WHERE source = 'alpaca'") == [
        (STALE, 0)
    ]


def test_stale_leaves_earlier_bars_unchanged(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    _run(settings)
    before = read("SELECT * FROM prices_daily ORDER BY security_id")
    result = _run(settings, _Prices(close=30.0, missing={SPY}), now=NOW + timedelta(days=1))
    assert result.runs[-1].status == STALE
    assert read("SELECT * FROM prices_daily ORDER BY security_id") == before


@pytest.mark.parametrize(("share", "status"), [(0.2, STALE), (0.5, OK)])
def test_too_many_listed_names_missing_is_stale(
    settings: Settings, share: float, status: str
) -> None:
    # One of four listed names (ACME, DUAL, DUAL_B, SPY) missing: 25%.
    tuned = settings.model_copy(
        update={"ingest": settings.ingest.model_copy(update={"max_missing_share": share})}
    )
    assert _run(tuned, _Prices(missing={ACME})).runs[-1].status == status


def test_reference_symbol_comes_from_config(settings: Settings) -> None:
    tuned = settings.model_copy(
        update={
            "ingest": settings.ingest.model_copy(
                update={"reference_symbol": "ACME", "max_missing_share": 0.5}
            )
        }
    )
    assert _run(tuned, _Prices(missing={SPY})).runs[-1].status == OK
    assert _run(tuned, _Prices(missing={ACME})).runs[-1].status == STALE


# --- failures and atomic chunks (spec req 9) --------------------------------


def test_a_mid_chunk_failure_commits_nothing_from_that_chunk(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    # Bars are fetched, then actions raise: no bar of this chunk is stored,
    # while the earlier (EDGAR) chunk stands.
    result = _run(settings, _Prices(fail="actions"))
    assert [(r.source, r.status) for r in result.runs] == [("edgar", OK), ("alpaca", FAILED)]
    assert "actions endpoint down" in result.runs[-1].message
    counts = _counts(read)
    assert counts["prices_daily"] == 0 and counts["securities"] > 0
    assert read("SELECT status FROM ingestion_runs WHERE source = 'alpaca'") == [(FAILED,)]


def test_a_failed_source_halts_the_run(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    class Broken(FixtureFilingSource):
        def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
            raise RuntimeError("companyfacts 503")

    prices = _Prices()
    result = _run(settings, prices, filings=_filings(cls=Broken))
    assert [(r.source, r.status) for r in result.runs] == [("edgar", FAILED)]
    assert prices.calls == []
    assert _counts(read)["securities"] == 0
    assert read("SELECT source, status FROM ingestion_runs") == [("edgar", FAILED)]


def test_one_source_only(settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]) -> None:
    result = _run(settings, source="edgar")
    assert [r.source for r in result.runs] == ["edgar"]
    assert _counts(read)["prices_daily"] == 0


class _Counted(FixtureFilingSource):
    """Sets the attributes the real EDGAR adapter exposes (T11b, T11c) only
    inside its calls, as the adapter does: `filing_index` resets its two,
    `facts` adds one unstamped fact per CIK asked (#172)."""

    skipped: Any = 3

    def filing_index(self, since: datetime | None = None) -> list[FilingIndexEntry]:
        self.unstamped_filings = ["a", "b"]
        self.skipped_filers = type(self).skipped
        return super().filing_index(since)

    def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
        self.unstamped_facts = [*getattr(self, "unstamped_facts", []), cik]
        return super().facts(cik, names)


@pytest.mark.parametrize("skipped", [("d", "e", "f"), 3])
def test_edgar_run_message_carries_the_adapter_counts(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]], skipped: Any
) -> None:
    class Counted(_Counted):
        pass

    Counted.skipped = skipped
    result = _run(settings, filings=_filings(cls=Counted), source="edgar")
    counts = "; unstamped: 2 filings, 2 facts; skipped filers: 3; missing benchmarks:"
    assert result.runs[0].status == OK
    assert counts in result.runs[0].message
    assert counts in read("SELECT message FROM ingestion_runs")[0][0]


def test_edgar_run_message_names_only_the_counts_the_source_exposes(settings: Settings) -> None:
    class OnlyFacts(FixtureFilingSource):
        unstamped_facts = ("c",)

    off = _switched(settings, False)  # the statement counts are their own tests' job
    message = _run(off, filings=_filings(cls=OnlyFacts), source="edgar").runs[0].message
    assert " facts; unstamped: 1 facts; missing benchmarks:" in message


def test_edgar_run_message_carries_the_fsn_counts(settings: Settings) -> None:
    """T11c: FSN re-issues, duplicates and periods whose re-issue could not be
    checked, before the benchmarks list so the length cap never cuts them.
    T11d adds `.fsn_missing`: older cover-form accessions absent from FSN."""

    class Fsn(FixtureFilingSource):
        fsn_reissued = 2
        fsn_duplicates = 1
        fsn_reissue_undetected = 3
        fsn_incomplete_listings = 4
        cover_incomplete_listings = 6  # #612: per-document cover parses
        fsn_missing = 5

    message = _run(settings, filings=_filings(cls=Fsn), source="edgar").runs[0].message
    counts = (
        "; FSN re-issued: 2; FSN duplicates: 1; FSN re-issues unchecked: 3; "
        "FSN incomplete listings: 4; cover incomplete listings: 6; FSN missing: 5; missing"
    )
    assert counts in message


def test_edgar_run_message_carries_the_pre_xml_delistings_count(settings: Settings) -> None:
    """T11f: a Form 25/25-NSE skipped pre-fetch (not XML)."""

    class Delistings(FixtureFilingSource):
        pre_xml_delistings = 7
        unstamped_delistings = 2

    message = _run(settings, filings=_filings(cls=Delistings), source="edgar").runs[0].message
    assert "; pre-XML delistings: 7; unstamped delistings: 2; missing" in message


def test_edgar_run_message_carries_the_failure_policy_counts(settings: Settings) -> None:
    """T11h: failed filings, quarantined accessions and facts missing; #566:
    empty bulk zip members; #576: empty per-CIK API answers; #599: payloads
    with `facts` but no `cik`; #610: company values dropped after capping."""

    class Failing(FixtureFilingSource):
        failed_filings = 2
        quarantined = 1
        facts_missing = 4
        facts_bulk_empty = 3  # #566
        submissions_bulk_empty = 5
        facts_api_empty = 6  # #576
        submissions_api_empty = 7
        facts_bulk_keyless = 8  # #599
        facts_api_keyless = 9
        facts_capped_dropped = frozenset({("acc", "name", "day")})  # #610 X2: dropped keys

    message = _run(settings, filings=_filings(cls=Failing), source="edgar").runs[0].message
    counts = (
        "; failed filings: 2; quarantined: 1; facts missing: 4"
        "; empty bulk facts: 3; empty bulk submissions: 5"
        "; empty API facts: 6; empty API submissions: 7"
        "; keyless bulk facts: 8; keyless API facts: 9; capped facts dropped: 1; missing"
    )
    assert counts in message


class _RecordFailures(FixtureFilingSource):
    """Records every `record_failures()` call on a list shared with the test."""

    def __init__(self, calls: list[str], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._calls = calls

    def record_failures(self) -> None:
        self._calls.append("called")


def test_after_commit_is_called_only_after_a_committed_ok_edgar_chunk(
    settings: Settings,
) -> None:
    calls: list[str] = []
    filings = _filings(cls=lambda **kw: _RecordFailures(calls, **kw))
    result = _run(settings, filings=filings, source="all")
    assert result.ok
    assert calls == ["called"]  # once for edgar; alpaca has no `record_failures`


def test_after_commit_is_not_called_for_a_dry_run(settings: Settings) -> None:
    calls: list[str] = []
    filings = _filings(cls=lambda **kw: _RecordFailures(calls, **kw))
    result = _run(settings, filings=filings, source="edgar", dry_run=True)
    assert result.runs[0].status == OK
    assert calls == []


def test_after_commit_is_not_called_for_a_failed_chunk(settings: Settings) -> None:
    calls: list[str] = []

    class Broken(_RecordFailures):
        def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
            raise RuntimeError("companyfacts 503")

    filings = _filings(cls=lambda **kw: Broken(calls, **kw))
    result = _run(settings, filings=filings, source="edgar")
    assert result.runs[0].status == FAILED
    assert calls == []


def test_after_commit_exception_leaves_status_ok_and_the_committed_row_untouched(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    class Boom(FixtureFilingSource):
        def record_failures(self) -> None:
            raise RuntimeError("disk full")

    result = _run(settings, filings=_filings(cls=Boom), source="edgar")
    run = result.runs[0]
    assert run.status == OK
    assert "after_commit: RuntimeError: disk full" in run.message
    stored_message = read("SELECT message FROM ingestion_runs WHERE source = 'edgar'")[0][0]
    assert "after_commit" not in stored_message  # the committed row was never rewritten


def test_check_failures_raising_fails_the_chunk_with_one_failed_run_row(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    class Unhealthy(FixtureFilingSource):
        def check_failures(self) -> None:
            raise RuntimeError("too many failures")

    result = _run(settings, filings=_filings(cls=Unhealthy), source="edgar")
    assert result.runs[0].status == FAILED
    assert "too many failures" in result.runs[0].message
    assert read("SELECT source, status FROM ingestion_runs") == [("edgar", FAILED)]


class _RecordsFailedCheck(FixtureFilingSource):
    """A source whose `check_failures` raises and that counts the
    `record_failed_check` calls (#610 policy 2)."""

    recorded = 0
    fails = True

    def check_failures(self) -> None:
        if self.fails:
            raise RuntimeError("too many failures")

    def record_failed_check(self) -> None:
        type(self).recorded += 1


def test_a_failed_check_records_the_failures_before_the_chunk_fails(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    """#610 policy 2: the failures reach disk although nothing commits, so the
    owner can accept them; the check's own message is the run's message."""
    _RecordsFailedCheck.recorded = 0
    result = _run(settings, filings=_filings(cls=_RecordsFailedCheck), source="edgar")
    assert result.runs[0].status == FAILED
    assert "too many failures" in result.runs[0].message
    assert _RecordsFailedCheck.recorded == 1
    assert read("SELECT source, status FROM ingestion_runs") == [("edgar", FAILED)]


def test_a_dry_run_or_a_passing_check_records_no_failed_check(settings: Settings) -> None:
    _RecordsFailedCheck.recorded = 0
    _run(settings, source="edgar")  # a real run first, so the store exists
    result = _run(settings, filings=_filings(cls=_RecordsFailedCheck), source="edgar", dry_run=True)
    assert result.runs[0].status == FAILED
    assert _RecordsFailedCheck.recorded == 0

    class Passing(_RecordsFailedCheck):
        fails = False

    assert _run(settings, filings=_filings(cls=Passing), source="edgar").runs[0].status == OK
    assert _RecordsFailedCheck.recorded == 0


def test_a_failing_record_keeps_the_check_message(settings: Settings) -> None:
    class RecordBoom(_RecordsFailedCheck):
        def record_failed_check(self) -> None:
            raise OSError("disk full")

    run = _run(settings, filings=_filings(cls=RecordBoom), source="edgar").runs[0]
    assert run.status == FAILED
    assert "too many failures" in run.message and "disk full" in run.message


class _Validating(_RecordsFailedCheck):
    """A source that records `bad` parse failures on its validation
    collector during the fetch pass (#578), as the EDGAR adapter does. Its
    `check_failures` raises, so a test sees whether the gate ran first."""

    bad: tuple[tuple[str, str, Exception], ...] = ()
    facts_bulk_empty = 62  # #566: counted, never failed
    validation_failures: ValidationFailures

    def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
        for input, key, error in self.bad:
            self.validation_failures.record(input, key, error)
        self.bad = ()  # once per pass, like a cached payload
        return super().facts(cik, names)


#: Parse failures that crashed past backfills (#599, #609 C3 and F3).
_CRASHES = (
    ("companyfacts.zip member", "0001786835", KeyError("cik")),
    ("cover page", "0002124122-26-000017", ValueError("cover page names 0 entities")),
    ("FSN period", "2026q1", TypeError("conversion from NoneType to Decimal")),
)


def _validating(
    tmp_path: Path, bad: Sequence[tuple[str, str, Exception]], *, fails: bool = True
) -> FixtureFilingSource:
    def make(**kwargs: Any) -> _Validating:
        source = _Validating(**kwargs)
        source.validation_failures = ValidationFailures(tmp_path / "validation", lambda: NOW)
        source.bad, source.fails = tuple(bad), fails
        return source

    return _filings(cls=make)


def test_input_validation_fails_the_run_before_any_store_write(
    settings: Settings, tmp_path: Path, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    """#578: three different bad inputs fail the run once, listing all three,
    with no data row written and the failure policy's check never reached."""
    _RecordsFailedCheck.recorded = 0
    run = _run(settings, filings=_validating(tmp_path, _CRASHES), source="edgar").runs[0]
    assert run.status == FAILED
    assert "InputValidationError: EDGAR input validation: 3 input(s)" in run.message
    assert "counted, not failed: empty bulk facts 62" in run.message
    (listed,) = (tmp_path / "validation").glob("failures-*.json")
    assert f"full list: {listed.resolve()}" in run.message
    assert len(json.loads(listed.read_text())["failures"]) == 3
    assert _RecordsFailedCheck.recorded == 0  # check_failures/record_failed_check not called
    assert set(_counts(read).values()) == {0}
    assert read("SELECT source, status FROM ingestion_runs") == [("edgar", FAILED)]


def test_input_validation_runs_on_a_dry_run_and_writes_the_list(
    settings: Settings, tmp_path: Path, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    _run(settings, source="edgar")  # a real run first, so the store exists
    before = (_counts(read), read("SELECT count(*) FROM ingestion_runs"))
    filings = _validating(tmp_path, _CRASHES[:1])
    run = _run(settings, filings=filings, source="edgar", dry_run=True).runs[0]
    assert run.status == FAILED and "1 input(s) failed to parse" in run.message
    assert len(list((tmp_path / "validation").glob("failures-*.json"))) == 1
    assert (_counts(read), read("SELECT count(*) FROM ingestion_runs")) == before


def test_a_pass_that_stops_after_a_recorded_failure_still_lists_it(
    settings: Settings, tmp_path: Path
) -> None:
    """#578: an input treated as absent can make a later step of the pass
    raise; the gate still fails the run with the full list, naming that
    error, so the error never hides the list."""

    class Stops(_Validating):
        def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
            super().facts(cik, names)
            raise RuntimeError("no cached FSN period at or after edgar.fsn_first_year")

    def make(**kwargs: Any) -> Stops:
        source = Stops(**kwargs)
        source.validation_failures = ValidationFailures(tmp_path / "validation", lambda: NOW)
        source.bad = _CRASHES[2:]
        return source

    run = _run(settings, filings=_filings(cls=make), source="edgar").runs[0]
    assert run.status == FAILED
    assert "InputValidationError: EDGAR input validation: 1 input(s)" in run.message
    assert "the pass then stopped: RuntimeError: no cached FSN period" in run.message
    assert "FSN period 2026q1" in run.message


def test_an_unfrozen_source_handed_to_the_write_meets_the_gate(
    settings: Settings, tmp_path: Path
) -> None:
    """#578 (safety-reviewer on #864): `_ingest_filings` given a source no
    `_prefetch` froze runs the fetch pass itself, then the same gate, so a
    recorded input never becomes silently absent rows."""
    conn = duckdb.connect(":memory:")
    conn.execute("SET TimeZone='UTC'")
    init_schema(conn)
    with pytest.raises(InputValidationError, match="3 input"):
        _ingest_filings(conn, settings, _validating(tmp_path, _CRASHES), lambda: NOW, "run-1")
    assert conn.execute("SELECT count(*) FROM securities").fetchone() == (0,)
    conn.close()


def test_a_pass_that_stops_with_nothing_recorded_raises_its_own_error(
    settings: Settings, tmp_path: Path
) -> None:
    class Stops(_Validating):
        def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
            raise RuntimeError("unrelated")

    def make(**kwargs: Any) -> Stops:
        source = Stops(**kwargs)
        source.validation_failures = ValidationFailures(tmp_path / "validation", lambda: NOW)
        return source

    run = _run(settings, filings=_filings(cls=make), source="edgar").runs[0]
    assert run.status == FAILED
    assert "RuntimeError: unrelated" in run.message and "InputValidationError" not in run.message
    assert not (tmp_path / "validation").exists()


def test_a_clean_validation_passes_straight_through(settings: Settings, tmp_path: Path) -> None:
    run = _run(settings, filings=_validating(tmp_path, (), fails=False), source="edgar").runs[0]
    assert run.status == OK  # 62 empty bulk facts are counted, never failed
    assert not (tmp_path / "validation").exists()


def test_input_validation_fails_before_a_write_time_collision(
    settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#687 failed at the write (a listings PRIMARY KEY collision); with
    parse failures recorded, the gate fails the run before that write runs."""

    def collide(*args: Any) -> tuple[int, str]:
        raise duckdb.ConstraintException('duplicate key "0000864270:0-750pct-medium-term-notes"')

    monkeypatch.setattr("tradepartner.ingest._ingest_filings", collide)
    run = _run(settings, filings=_validating(tmp_path, _CRASHES), source="edgar").runs[0]
    assert run.status == FAILED
    assert "InputValidationError" in run.message and "ConstraintException" not in run.message


def test_a_fixture_source_leaves_the_edgar_message_unchanged(settings: Settings) -> None:
    message = _run(settings, source="edgar").runs[0].message
    assert "unstamped" not in message and "skipped" not in message


def test_prices_before_any_master_fails_rather_than_fetching_nothing(settings: Settings) -> None:
    result = _run(settings, source="alpaca")
    assert result.runs[-1].status == FAILED
    assert "SPY" in result.runs[-1].message


def test_dry_run_counts_rows_and_writes_nothing(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    _run(settings)
    before = (_counts(read), read("SELECT count(*) FROM ingestion_runs"))
    result = _run(settings, _Prices(close=25.0), now=NOW + timedelta(days=1), dry_run=True)
    assert result.ok
    assert result.runs[-1].rows_added == 4
    assert all(r.message.startswith("dry run") for r in result.runs)
    assert (_counts(read), read("SELECT count(*) FROM ingestion_runs")) == before


# --- the single-writer lock (spec reqs 1, 9) --------------------------------


def _hold(path: str, *, read_only: bool, seconds: float) -> subprocess.Popen[str]:
    code = textwrap.dedent(f"""
        import duckdb, time
        conn = duckdb.connect(database={path!r}, read_only={read_only})
        print("HELD", flush=True)
        time.sleep({seconds})
        conn.close()
        """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "HELD"
    return proc


def test_a_short_lived_reader_is_waited_for(settings: Settings) -> None:
    _run(settings)
    patient = settings.model_copy(
        update={"store": settings.store.model_copy(update={"lock_retry_seconds": 10})}
    )
    proc = _hold(settings.store.path, read_only=True, seconds=0.5)
    try:
        assert _run(patient, now=NOW + timedelta(days=1)).ok
    finally:
        proc.wait(timeout=10)


def _open_from_another_process(path: str, *, read_only: bool) -> int:
    code = f"import duckdb; duckdb.connect(database={path!r}, read_only={read_only}).close()"
    return subprocess.run([sys.executable, "-c", code], check=False).returncode


@dataclass
class _Slow(_Prices):
    """`_Prices` that runs `during` while it fetches bars."""

    during: Callable[[], None] | None = None

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        if self.during is not None:
            self.during()
        return super().bars(security_ids, start, end)


def test_the_store_is_free_while_the_price_side_fetches(settings: Settings) -> None:
    # #173: the daily Alpaca chunk reads its names on a short read and fetches
    # with no connection open; only the write is a transaction.
    _run(settings, source="edgar")
    opened: list[int] = []

    def others() -> None:
        path = settings.store.path
        opened.append(_open_from_another_process(path, read_only=True))
        opened.append(_open_from_another_process(path, read_only=False))

    result = _run(settings, _Slow(during=others), source="alpaca")
    assert result.ok
    assert opened == [0, 0]


def test_a_writer_holding_the_store_during_the_name_read_is_locked(settings: Settings) -> None:
    _run(settings, source="edgar")
    proc = _hold(settings.store.path, read_only=False, seconds=30)
    try:
        result = _run(settings, source="alpaca")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    assert [(r.source, r.status) for r in result.runs] == [("alpaca", LOCKED)]
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM prices_daily").fetchone() == (0,)


def test_a_newer_revision_written_during_the_fetch_fails_the_chunk(settings: Settings) -> None:
    # Another writer commits a later revision of the reference bar while the
    # price side fetches: the write would be back-dated behind it, so it fails.
    _run(settings, source="edgar")
    later = NOW + timedelta(hours=1)

    def other_writer() -> None:
        with open_for_write(settings) as conn:
            insert_row(
                conn,
                "prices_daily",
                {
                    "security_id": SPY,
                    "session": SESSION,
                    "open": 99.0,
                    "high": 99.0,
                    "low": 99.0,
                    "close": 99.0,
                    "volume": 1,
                    "known_at": later,
                    "ingested_at": later,
                    "source": "alpaca",
                    "provenance": "bar",
                },
            )

    result = _run(settings, _Slow(during=other_writer), source="alpaca")
    assert [(r.source, r.status) for r in result.runs] == [("alpaca", FAILED)]
    assert "back-dated" in result.runs[0].message
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        assert conn.execute("SELECT close FROM prices_daily").fetchall() == [(99.0,)]


def test_a_store_without_a_schema_fails_the_price_side_plainly(settings: Settings) -> None:
    duckdb.connect(settings.store.path).close()  # a file with no tables
    result = _run(settings, source="alpaca")
    assert [(r.source, r.status) for r in result.runs] == [("alpaca", FAILED)]
    assert "schema is not ready" in result.runs[0].message


def test_a_writer_that_never_closes_fails_after_the_retry_window(settings: Settings) -> None:
    _run(settings)
    proc = _hold(settings.store.path, read_only=False, seconds=30)
    try:
        result = _run(settings, now=NOW + timedelta(days=1))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    assert result.exit_code != 0
    assert [(r.source, r.status) for r in result.runs] == [("edgar", LOCKED)]


# --- facts: XBRL concept to store fact, class member to security ------------


def _fact_rows(
    settings: Settings, source: FixtureFilingSource
) -> tuple[tuple[dict[str, Any], ...], tuple[FactRecord, ...]]:
    master = build_master(source, settings, ingested_at=NOW)
    classes = build_classifications(source, master, settings, ingested_at=NOW)
    records = [f for cik in (ACME, DUAL) for f in source.facts(cik, list(FACT_NAMES))]
    return fact_rows(records, master, classes, ingested_at=NOW)


def test_fact_rows_map_the_concept_and_each_class_member(settings: Settings) -> None:
    rows, unmatched = _fact_rows(settings, _filings())
    got = {(r["security_id"], r["class_member"], r["value"]) for r in rows}
    assert got == {
        (ACME, "", 5_000_000),
        (DUAL, "us-gaap:CommonClassAMember", 9_000_000),
        (DUAL_B, "us-gaap:CommonClassBMember", 1_000_000),
    }
    assert {r["fact_name"] for r in rows} == {"shares_outstanding"}
    assert all(r["provenance"] == "filing" and r["source"] == "edgar" for r in rows)
    assert unmatched == ()


def test_an_undimensioned_fact_of_a_multi_class_company_is_unmatched(settings: Settings) -> None:
    total = _fact(DUAL, "", 10_000_000, f"{DUAL}-19-1", _at(2019, 3, 4))
    rows, unmatched = _fact_rows(settings, _filings(extra_facts=[total]))
    assert DUAL not in {r["security_id"] for r in rows if r["class_member"] == ""}
    assert unmatched == (total,)


def test_a_listed_preferred_never_takes_the_common_shares(settings: Settings) -> None:
    # quant-auditor on #164: a bank-style CIK, common plus a listed preferred.
    pref = CoverListing("6.00% Series A Preferred Stock", "ACMEP", "NYSE")
    rows, unmatched = _fact_rows(settings, _filings(acme_extra=(pref,)))
    assert [(r["security_id"], r["value"]) for r in rows if r["class_member"] == ""] == [
        (ACME, 5_000_000)
    ]
    assert unmatched == ()


def test_a_fact_for_an_unlisted_class_is_unmatched(settings: Settings) -> None:
    # quant-auditor on #164: only Class B is listed (Nike-style); Class A's
    # shares must not land on it.
    only_b = (CoverListing("Class B Common Stock", "DUB", "NASDAQ"),)
    rows, unmatched = _fact_rows(settings, _filings(dual_listings=only_b))
    dual = [(r["security_id"], r["class_member"]) for r in rows if r["security_id"] != ACME]
    assert dual == [(DUAL, "us-gaap:CommonClassBMember")]
    assert [u.class_member for u in unmatched] == ["us-gaap:CommonClassAMember"]


def test_a_class_member_needs_a_matching_title_even_for_one_class(settings: Settings) -> None:
    member = _fact(ACME, "us-gaap:CommonClassAMember", 7, f"{ACME}-19-2", _at(2019, 3, 1))
    _, unmatched = _fact_rows(settings, _filings(extra_facts=[member]))
    assert member in unmatched
    matched = _fact_rows(
        settings, _filings(acme_title="Class A Common Stock", extra_facts=[member])
    )
    assert (ACME, 7) in {(r["security_id"], r["value"]) for r in matched[0]}


def test_a_series_letter_in_the_title_names_the_class(settings: Settings) -> None:
    # #1166: Liberty Broadband (LBRDA/LBRDK) and Liberty TripAdvisor title
    # their classes "Series A/C common stock" and tag CommonClassA/CMember.
    series = (
        CoverListing("Series A common stock", "DUA", "NASDAQ"),
        CoverListing("Series C common stock", "DUC", "NASDAQ"),
    )
    class_c = _fact(DUAL, "us-gaap:CommonClassCMember", 3_000_000, f"{DUAL}-19-1", _at(2019, 3, 4))
    rows, unmatched = _fact_rows(settings, _filings(dual_listings=series, extra_facts=[class_c]))
    dual = {(r["security_id"], r["value"]) for r in rows if r["security_id"] != ACME}
    assert dual == {(DUAL, 9_000_000), (f"{DUAL}:series-c-common-stock", 3_000_000)}
    assert [u.class_member for u in unmatched] == ["us-gaap:CommonClassBMember"]  # unlisted


def test_a_series_in_a_rights_clause_names_no_class(settings: Settings) -> None:
    # code-review on #1168: a plain common title mentioning a preferred
    # series' purchase rights must not let an unlisted Class A take its place.
    title = "Common Stock, and Series A Junior Participating Preferred Stock Purchase Rights"
    member = _fact(ACME, "us-gaap:CommonClassAMember", 7, f"{ACME}-19-2", _at(2019, 3, 1))
    _, unmatched = _fact_rows(settings, _filings(acme_title=title, extra_facts=[member]))
    assert member in unmatched


#: A later cover page retitles ACME's one listed class "Class A" (#1166: SAM,
#: PBF, OPY, GOCO, WEBR): same pair, so no new listing row.
_RETITLED_AT = _at(2019, 5, 1)
_RETITLE = CoverPage(
    ACME, f"{ACME}-19-000005", _RETITLED_AT, (CoverListing("Class A Common Stock", "ACME", "NYSE"),)
)


def test_a_class_letter_from_a_later_cover_title_names_the_class(settings: Settings) -> None:
    after = _RETITLED_AT + timedelta(days=1)
    member = _fact(ACME, "us-gaap:CommonClassAMember", 7, f"{ACME}-19-000006", after)
    other = _fact(ACME, "us-gaap:CommonClassBMember", 2, f"{ACME}-19-000006", after)
    rows, unmatched = _fact_rows(settings, _filings(extra_covers=[_RETITLE], extra_facts=[member]))
    assert (ACME, "us-gaap:CommonClassAMember", 7) in {
        (r["security_id"], r["class_member"], r["value"]) for r in rows
    }
    assert member not in unmatched
    _, unmatched = _fact_rows(settings, _filings(extra_covers=[_RETITLE], extra_facts=[other]))
    assert other in unmatched  # Class B is not the listed class


def test_a_later_cover_title_never_names_an_earlier_facts_class(settings: Settings) -> None:
    # No look-ahead (#1166): a fact accepted before the cover page that first
    # titles the class "Class A" is matched as if that page did not exist.
    before = _RETITLED_AT - timedelta(days=1)
    early = _fact(ACME, "us-gaap:CommonClassAMember", 7, f"{ACME}-19-000004", before)
    with_later = _fact_rows(settings, _filings(extra_covers=[_RETITLE], extra_facts=[early]))
    without = _fact_rows(settings, _filings(extra_facts=[early]))
    assert early in with_later[1] and early in without[1]
    assert with_later == without


def test_an_owner_override_sends_the_named_member_to_the_sole_common_class(
    settings: Settings,
) -> None:
    # #1169: DKS/TR/VMEO title their one listed class "Common Stock" but tag
    # its shares CommonClassA (Class B unlisted). Only an owner-reviewed
    # per-CIK override matches them; Class B stays unmatched.
    class_a = _fact(ACME, "CommonClassA", 7, f"{ACME}-19-2", _at(2019, 3, 1))
    class_b = _fact(ACME, "CommonClassB", 2, f"{ACME}-19-2", _at(2019, 3, 1))
    source = _filings(extra_facts=[class_a, class_b])
    master = build_master(source, settings, ingested_at=NOW)
    classes = build_classifications(source, master, settings, ingested_at=NOW)
    records = list(source.facts(ACME, list(FACT_NAMES)))
    _, unmatched = fact_rows(records, master, classes, ingested_at=NOW)
    assert class_a in unmatched and class_b in unmatched
    rows, unmatched = fact_rows(
        records,
        master,
        classes,
        ingested_at=NOW,
        class_member_overrides={ACME: "CommonClassA"},
    )
    assert (ACME, "CommonClassA", 7) in {
        (r["security_id"], r["class_member"], r["value"]) for r in rows
    }
    assert unmatched == (class_b,)


def test_an_override_never_picks_between_several_common_classes(settings: Settings) -> None:
    # The override acts like an undimensioned fact: a CIK with two common
    # classes and no title naming the letter leaves the fact unmatched.
    both = (
        CoverListing("Common Stock", "DUA", "NASDAQ"),
        CoverListing("Common Stock, Series 2", "DUB", "NASDAQ"),
    )
    member = _fact(DUAL, "CommonClassC", 3, f"{DUAL}-19-2", _at(2019, 3, 5))
    source = _filings(dual_listings=both, extra_facts=[member])
    master = build_master(source, settings, ingested_at=NOW)
    classes = build_classifications(source, master, settings, ingested_at=NOW)
    _, unmatched = fact_rows(
        [member], master, classes, ingested_at=NOW, class_member_overrides={DUAL: "CommonClassC"}
    )
    assert unmatched == (member,)


def test_the_edgar_run_applies_the_configured_overrides(settings: Settings) -> None:
    # The ingest path passes `edgar.class_member_overrides` to fact_rows.
    class_a = _fact(ACME, "CommonClassA", 7, f"{ACME}-19-2", _at(2019, 3, 1))
    configured = settings.model_copy(
        update={
            "edgar": settings.edgar.model_copy(
                update={"class_member_overrides": {ACME: "CommonClassA"}}
            )
        }
    )
    *_, facts, unmatched = _build_filings(_filings(extra_facts=[class_a]), configured, NOW)
    assert (ACME, 7) in {(r["security_id"], r["value"]) for r in facts}
    assert class_a not in unmatched


def test_shares_after_new_equity_go_to_the_successor(settings: Settings) -> None:
    # #820: post-bankruptcy equity is a new security; the old common no
    # longer takes the company's share count once the successor is known.
    cik = "0000000099"
    ingested_at = datetime(2021, 6, 1, tzinfo=UTC)

    def shares(value: float, accession: str, at: datetime) -> FactRecord:
        return FactRecord(
            cik, "EntityCommonStockSharesOutstanding", at.date(), "", value, accession, at
        )

    before = shares(49_000_000, "a1", datetime(2020, 8, 6, 21, tzinfo=UTC))
    after = shares(83_000_000, "a2", datetime(2020, 11, 5, 22, tzinfo=UTC))
    source = FixtureFilingSource(
        index=[
            FilingIndexEntry(cik, "Crc Co", "10-K", "k1", datetime(2018, 3, 1, tzinfo=UTC)),
            FilingIndexEntry(cik, "Crc Co", "8-A12B", "r1", datetime(2020, 10, 27, tzinfo=UTC)),
        ],
        cover_pages=[
            CoverPage(cik, "c1", datetime(2019, 8, 1, 21, tzinfo=UTC), (_common("CRC"),)),
            CoverPage(cik, "a1", before.accepted_at, ()),
            CoverPage(cik, "a2", after.accepted_at, (_common("CRC"),)),
        ],
        delistings=[
            DelistingFiling(
                cik, "25-NSE", "Common Stock", "NYSE", "d1", datetime(2020, 7, 31, 18, tzinfo=UTC)
            )
        ],
        facts=[before, after],
    )
    master = build_master(source, settings, ingested_at=ingested_at)
    classes = build_classifications(source, master, settings, ingested_at=ingested_at)
    rows, unmatched = fact_rows([before, after], master, classes, ingested_at=ingested_at)
    assert [(r["security_id"], r["value"]) for r in rows] == [
        (cik, 49_000_000),
        (f"{cik}@2020-11-05", 83_000_000),
    ]
    assert unmatched == ()


def _common(ticker: str) -> CoverListing:
    return CoverListing("Common Stock", ticker, "NYSE")


def test_shares_reach_the_as_of_read(settings: Settings) -> None:
    _run(settings)
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        facts = facts_as_of(conn, NOW, [DUAL_B])
    assert facts["value"].to_list() == [1_000_000]


def test_a_filing_accepted_after_the_run_clock_fails_the_chunk(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    # No look-ahead: a record not yet knowable at the run's instant is never stored.
    early = NOW - timedelta(days=200)
    result = _run(settings, now=early)
    assert [(r.source, r.status) for r in result.runs] == [("edgar", FAILED)]
    assert "after" in result.runs[0].message
    assert _counts(read)["securities"] == 0


def test_a_filing_accepted_while_the_run_fetches_is_stored(settings: Settings) -> None:
    # quant-auditor on #164: ingested_at is read after the sources return.
    start = datetime(2019, 3, 4, 20, 0, tzinfo=UTC)  # before DUAL's cover page (20:30)
    ticks = iter([start, NOW, NOW, NOW])
    result = ingest_session(
        settings, prices=_Prices(), filings=_filings(), source="edgar", clock=lambda: next(ticks)
    )
    assert result.ok


def _filing_tables(conn: duckdb.DuckDBPyConnection) -> dict[str, list[tuple[Any, ...]]]:
    tables = ("securities", "listings", "delistings", "classifications", "facts")
    return {t: sorted(conn.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in tables}


def test_prefetch_requires_dry_run_by_keyword(settings: Settings) -> None:
    """#629: `dry_run` has no default, so a future dry caller cannot record
    failures by leaving it out."""
    with pytest.raises(TypeError, match="dry_run"):
        _prefetch(_Recorded(_filings()), settings)  # type: ignore[call-arg]


def test_a_prefetched_source_is_built_once_more_with_the_same_rows(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#564: after `_prefetch`, `_ingest_filings` skips its own fetch pass (a
    frozen source has nothing left to fetch) and writes exactly the rows the
    unprefetched path writes, stamped at the same clock."""
    import tradepartner.ingest as ingest

    builds: list[datetime] = []

    def counting(*args: Any, **kwargs: Any) -> Any:
        builds.append(kwargs["ingested_at"])
        return build_classifications(*args, **kwargs)

    monkeypatch.setattr(ingest, "build_classifications", counting)
    written = []
    for prefetch in (False, True):
        builds.clear()
        source: FixtureFilingSource | _Recorded = _filings()
        if prefetch:
            source = _Recorded(source)
            _prefetch(source, settings, dry_run=False)
        conn = duckdb.connect(":memory:")
        conn.execute("SET TimeZone='UTC'")
        init_schema(conn)
        added, _ = _ingest_filings(conn, settings, source, lambda: NOW, "run-1")
        assert added > 0
        written.append(_filing_tables(conn))
        conn.close()
        assert builds[-1] == NOW
        assert len(builds) == 2  # one fetch pass, then the build at the clock
    assert written[0] == written[1]


def test_a_filed_value_that_reverts_is_a_third_row(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    # quant-auditor on #164: A -> B -> A at one known_at stays visible as A.
    def name() -> list[tuple[Any, ...]]:
        return read(
            f"SELECT name, known_at FROM securities WHERE security_id = '{ACME}' ORDER BY known_at"
        )

    def renamed(to: str) -> FixtureFilingSource:
        source = _filings()
        source._index = [
            FilingIndexEntry(
                e.cik, to if e.cik == ACME else e.company_name, e.form, e.accession, e.accepted_at
            )
            for e in source._index
        ]
        return source

    _run(settings, source="edgar")
    one, two = NOW + timedelta(days=1), NOW + timedelta(days=2)
    _run(settings, filings=renamed("Acme Renamed"), now=one, source="edgar")
    _run(settings, filings=renamed("Acme Corp"), now=two, source="edgar")
    _run(settings, filings=renamed("Acme Corp"), now=two + timedelta(hours=1), source="edgar")
    assert [n for n, _ in name()] == ["Acme Corp", "Acme Renamed", "Acme Corp"]
    assert [k for _, k in name()][1:] == [one, two]


def test_a_revised_action_keeps_the_stored_announcement(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    ex = date(2019, 6, 14)
    announced = datetime(2019, 5, 1, 20, 0, tzinfo=UTC)
    first = CorporateAction(
        ACME, ActionType.DIVIDEND, ex, 0.10, announced, "alpaca", announced_at=announced
    )
    revised = CorporateAction(
        ACME, ActionType.DIVIDEND, ex, 0.12, action_first_seen_known_at(ex), "alpaca"
    )
    _run(settings, _Prices(actions=[first]))
    _run(settings, _Prices(actions=[revised]), now=NOW + timedelta(days=1))
    assert read("SELECT announced_at FROM corporate_actions ORDER BY known_at") == [
        (announced,),
        (announced,),
    ]


def _delisting(effective_on: date) -> DelistingFiling:
    return DelistingFiling(
        ACME, "25", "Common Stock", "NYSE", f"{ACME}-19-000025", _at(2019, 6, 20), effective_on
    )


@pytest.mark.parametrize(
    ("effective_on", "fetched"), [(date(2019, 6, 30), True), (date(2019, 6, 27), False)]
)
def test_a_delisted_name_is_fetched_until_effective_but_never_counted(
    settings: Settings, effective_on: date, fetched: bool
) -> None:
    prices = _Prices(missing={ACME})
    result = _run(settings, prices, filings=_filings(delistings=[_delisting(effective_on)]))
    assert result.ok  # ACME's missing bar does not count toward staleness
    bars_call = next(c for c in prices.calls if c[0] == "bars")
    assert (ACME in bars_call[1]) is fetched
    assert "0 of 3 listed names missing" in result.runs[-1].message


def test_a_listed_preferred_is_not_in_the_staleness_denominator(settings: Settings) -> None:
    pref = CoverListing("6.00% Series A Preferred Stock", "ACMEP", "NYSE")
    prices = _Prices(missing={f"{ACME}:6-00pct-series-a-preferred-stock"})
    result = _run(settings, prices, filings=_filings(acme_extra=(pref,)))
    assert result.ok, result.runs[-1].message


OTC_B = (
    CoverListing("Class A Common Stock", "DUA", "NASDAQ"),
    CoverListing("Class B Common Stock", "DUB", "OTC"),
)
STAT = "0000000003"  # no cover page: its only listing is snapshot_static


def _with_stat(*, cover: bool = False, **kwargs: Any) -> FixtureFilingSource:
    """`_filings` plus STAT, a common name listed only by a `snapshot_static`
    span, or also by a cover page (a filing-based span) if `cover`."""
    accession = f"{STAT}-18-000001"
    page = CoverPage(
        STAT, f"{STAT}-19-000001", _at(2019, 3, 6), (CoverListing("Common Stock", "STAT", "NYSE"),)
    )
    return _filings(
        extra_index=[FilingIndexEntry(STAT, "Stat Corp", "10-K", accession, _at(2018, 3, 5))],
        extra_headers=[FilingHeader(STAT, accession, "10-K", 3571, _at(2018, 3, 5))],
        extra_facts=[_fact(STAT, "", 3_000_000, accession, _at(2018, 3, 5))],
        extra_snapshot=[CompanySnapshotEntry(STAT, "Stat Corp", "STAT", "NYSE", FETCHED_AT)],
        extra_covers=[page] if cover else [],
        **kwargs,
    )


@pytest.mark.parametrize(("missing", "ok"), [(DUAL_B, True), (ACME, False)])
def test_an_otc_common_name_is_not_in_the_staleness_denominator(
    settings: Settings, missing: str, ok: bool
) -> None:
    # #784: OTC is not one of `universe.exchanges` and the SIP feed has no
    # OTC bars; a NYSE name with no bar still counts (1 of 3 is stale).
    result = _run(settings, _Prices(missing={missing}), filings=_filings(dual_listings=OTC_B))
    assert result.ok is ok, result.runs[-1].message
    if ok:
        assert "0 of 3 listed names missing" in result.runs[-1].message


NOT_COMMON = (
    CoverListing("6.00% Series A Preferred Stock", "ACMEP", "NYSE"),
    CoverListing("5.25% Notes due 2030", "ACME30", "NYSE"),
)


def test_notes_preferreds_and_otc_listings_are_not_fetched(
    settings: Settings,
) -> None:
    # #794: a note, a preferred and an OTC listing are not fetched; a
    # common NYSE name and a benchmark are, and every counted name is.
    prices = _Prices()
    result = _run(settings, prices, filings=_filings(acme_extra=NOT_COMMON, dual_listings=OTC_B))
    assert result.ok, result.runs[-1].message
    fetched = set(next(c for c in prices.calls if c[0] == "bars")[1])
    assert {ACME, SPY} <= fetched
    assert DUAL_B not in fetched
    assert not any(sid.startswith(f"{ACME}:") for sid in fetched)  # preferred, note
    assert "0 of 3 listed names missing" in result.runs[-1].message


def test_the_fetched_security_types_come_from_config(settings: Settings) -> None:
    # #794: a type the universe admits is fetched; a note still is not.
    types = [*settings.universe.security_types, "preferred"]
    tuned = settings.model_copy(
        update={"universe": settings.universe.model_copy(update={"security_types": types})}
    )
    prices = _Prices()
    _run(tuned, prices, filings=_filings(acme_extra=NOT_COMMON))
    fetched = set(next(c for c in prices.calls if c[0] == "bars")[1])
    assert f"{ACME}:6-00pct-series-a-preferred-stock" in fetched
    assert f"{ACME}:5-25pct-notes-due-2030" not in fetched


@pytest.mark.parametrize(
    ("sid", "exchange", "types", "fetched"),
    [
        ("X", "NYSE", {}, True),  # no classification row yet
        ("X", "NYSE", {"X": {"unclassifiable"}}, True),  # e.g. before a spin-off's first 10-Q
        ("X", "NYSE", {"X": {"debt", "common"}}, True),  # common in one revision
        ("X", "NYSE", {"X": {"spac"}}, True),  # #802 owner: blocklist, not allowlist
        ("X", "NYSE", {"X": {"foreign"}}, True),
        ("X", "NYSE", {"X": {"fund"}}, True),
        ("X", "NYSE", {"X": {"depositary"}}, True),
        ("X", "NYSE", {"X": {"debt"}}, False),
        ("X", "NYSE", {"X": {"preferred"}}, False),
        ("X", "NYSE", {"X": {"warrant"}}, False),
        ("X", "NYSE", {"X": {"unit"}}, False),
        ("X", "NYSE", {"X": {"right"}}, False),
        ("X", "OTC", {"X": {"common"}}, False),
        (SPY, "OTC", {}, True),  # a benchmark whatever its listing
    ],
)
def test_the_fetch_predicate(
    settings: Settings, sid: str, exchange: str, types: dict[str, set[str]], fetched: bool
) -> None:
    assert _fetched(sid, {"exchange": exchange}, {SPY}, types, settings) is fetched


def test_types_known_has_every_revision_known_at_t_and_none_after(settings: Settings) -> None:
    _run(settings, source="edgar")
    later = NOW + timedelta(days=1)
    with open_for_write(settings) as conn:
        cursor = conn.execute("SELECT * FROM classifications WHERE security_id = ?", [ACME])
        names = [d[0] for d in cursor.description]
        row = dict(zip(names, cursor.fetchone() or (), strict=True))
        insert_row(
            conn,
            "classifications",
            {**row, "security_type": "foreign", "known_at": later, "ingested_at": later},
        )
        assert _types_known(conn, NOW)[ACME] == {"common"}  # no look-ahead
        assert _types_known(conn, later)[ACME] == {"common", "foreign"}


def test_the_staleness_exchange_filter_comes_from_config(settings: Settings) -> None:
    exchanges = [*settings.universe.exchanges, "OTC"]
    tuned = settings.model_copy(
        update={"universe": settings.universe.model_copy(update={"exchanges": exchanges})}
    )
    result = _run(tuned, _Prices(missing={DUAL_B}), filings=_filings(dual_listings=OTC_B))
    assert result.runs[-1].status == STALE


def test_a_snapshot_static_only_name_with_no_bar_is_reported_not_counted(
    settings: Settings,
) -> None:
    # #784 (owner option a): its back-dated ticker may not be the one it traded under.
    result = _run(settings, _Prices(missing={STAT}), filings=_with_stat())
    assert result.ok, result.runs[-1].message
    message = result.runs[-1].message
    assert "0 of 4 listed names missing" in message
    assert f"1 snapshot-only names with no rows (not counted): {STAT}" in message


def test_a_name_with_a_filing_based_span_and_no_bar_still_counts(settings: Settings) -> None:
    result = _run(settings, _Prices(missing={STAT}), filings=_with_stat(cover=True))
    assert result.runs[-1].status == STALE and STAT in result.runs[-1].message
    assert "snapshot-only" not in result.runs[-1].message


def test_a_missing_benchmark_still_counts_though_snapshot_static(settings: Settings) -> None:
    # SPY's listing is snapshot_static on NYSE_ARCA (not a universe exchange);
    # with ACME as the reference, only the benchmark rule keeps SPY counted.
    tuned = settings.model_copy(
        update={"ingest": settings.ingest.model_copy(update={"reference_symbol": "ACME"})}
    )
    result = _run(tuned, _Prices(missing={SPY}))
    assert result.runs[-1].status == STALE and SPY in result.runs[-1].message
    assert "snapshot-only" not in result.runs[-1].message


NEWCO = "0000000004"  # first listed by a cover page accepted on `accepted`


def _with_newco(accepted: datetime, **kwargs: Any) -> FixtureFilingSource:
    """`_filings` plus NEWCO, a NYSE common name first listed at `accepted`."""
    accession = f"{NEWCO}-19-000001"
    page = CoverPage(NEWCO, accession, accepted, (CoverListing("Common Stock", "NEWC", "NYSE"),))
    return _filings(
        extra_index=[FilingIndexEntry(NEWCO, "Newco Inc", "10-K", accession, accepted)],
        extra_headers=[FilingHeader(NEWCO, accession, "10-K", 3571, accepted)],
        extra_facts=[_fact(NEWCO, "", 2_000_000, accession, accepted)],
        extra_covers=[page],
        **kwargs,
    )


def _loose(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"ingest": settings.ingest.model_copy(update={"max_missing_share": 0.3})}
    )


def _early() -> FixtureFilingSource:
    """`_filings` with SPY's snapshot known before the previous session."""
    return _filings(fetched_at=_at(2019, 6, 3))


PREVIOUS = NOW - timedelta(days=1)  # expected session 2019-06-27


def test_a_name_dark_since_before_the_previous_session_is_reported_not_counted(
    settings: Settings,
) -> None:
    # #784 (dark names): no bar at 06-27 either, so 06-28's miss is not counted.
    assert _run(_loose(settings), _Prices(missing={ACME}), now=PREVIOUS, filings=_early()).ok
    result = _run(settings, _Prices(missing={ACME}), filings=_early())
    assert result.ok, result.runs[-1].message
    message = result.runs[-1].message
    assert "0 of 3 listed names missing" in message
    assert f"1 names with no bar in the previous chunk (not counted): {ACME}" in message


def test_a_name_with_a_bar_at_the_previous_session_and_none_now_counts(
    settings: Settings,
) -> None:
    assert _run(settings, now=PREVIOUS, filings=_early()).ok
    result = _run(settings, _Prices(missing={ACME}), filings=_early())
    assert result.runs[-1].status == STALE and ACME in result.runs[-1].message


def test_a_name_first_listed_this_session_with_no_bar_counts(settings: Settings) -> None:
    assert _run(settings, now=PREVIOUS, filings=_early()).ok
    filings = _with_newco(_at(2019, 6, 28), fetched_at=_at(2019, 6, 3))
    result = _run(settings, _Prices(missing={NEWCO}), filings=filings)
    assert result.runs[-1].status == STALE and NEWCO in result.runs[-1].message


def _dark_limited(settings: Settings, share: float) -> Settings:
    return settings.model_copy(
        update={"ingest": settings.ingest.model_copy(update={"max_dark_share": share})}
    )


def test_too_many_dark_names_make_the_run_stale(settings: Settings) -> None:
    # #796 (i a): dark names leave the missing share, so a gradual dropout is
    # bounded by their own share of the listed set (here 1 of 4).
    assert _run(_loose(settings), _Prices(missing={ACME}), now=PREVIOUS, filings=_early()).ok
    result = _run(_dark_limited(settings, 0.2), _Prices(missing={ACME}), filings=_early())
    assert result.runs[-1].status == STALE, result.runs[-1].message
    assert "1 of 4 listed names (25.0%, over 20.0%) are dark or snapshot-only" in (
        result.runs[-1].message
    )
    assert ACME in result.runs[-1].message


def test_dark_names_within_the_limit_leave_the_run_ok(settings: Settings) -> None:
    assert _run(_loose(settings), _Prices(missing={ACME}), now=PREVIOUS, filings=_early()).ok
    result = _run(_dark_limited(settings, 0.25), _Prices(missing={ACME}), filings=_early())
    assert result.ok, result.runs[-1].message


def test_snapshot_only_names_count_toward_the_dark_share(settings: Settings) -> None:
    # 1 of 5 listed names is snapshot-only with no bar.
    tuned = _dark_limited(settings, 0.15)
    result = _run(tuned, _Prices(missing={STAT}), filings=_with_stat())
    assert result.runs[-1].status == STALE, result.runs[-1].message
    assert "1 of 5 listed names (20.0%, over 15.0%) are dark or snapshot-only" in (
        result.runs[-1].message
    )


def test_a_name_up_listed_from_otc_this_session_with_no_bar_counts(settings: Settings) -> None:
    # #796: DUB moves from OTC to NASDAQ on 06-28. Its first listing on a
    # universe exchange starts this session, so it is a new listing, not dark.
    otc = _filings(fetched_at=_at(2019, 6, 3), dual_listings=OTC_B)
    assert _run(settings, now=PREVIOUS, filings=otc).ok
    up = CoverPage(
        DUAL,
        f"{DUAL}-19-000002",
        _at(2019, 6, 28),
        (
            CoverListing("Class A Common Stock", "DUA", "NASDAQ"),
            CoverListing("Class B Common Stock", "DUB", "NASDAQ"),
        ),
    )
    filings = _filings(fetched_at=_at(2019, 6, 3), dual_listings=OTC_B, extra_covers=[up])
    result = _run(settings, _Prices(missing={DUAL_B}), filings=filings)
    assert result.runs[-1].status == STALE, result.runs[-1].message
    assert DUAL_B in result.runs[-1].message


def _bar(sid: str, session: date, ingested_at: datetime) -> dict[str, Any]:
    known = datetime.combine(session, datetime.min.time(), UTC) + timedelta(hours=21)
    return {
        "security_id": sid,
        "session": session,
        "open": 10.0,
        "high": 10.0,
        "low": 10.0,
        "close": 10.0,
        "volume": 100,
        "known_at": known,
        "ingested_at": ingested_at,
        "source": "alpaca",
        "provenance": "bar",
    }


def test_may_count_reads_bars_ingested_by_t_and_always_the_benchmarks(
    settings: Settings,
) -> None:
    # #796: "already in the store" means ingested by t, not known by t: a
    # backfilled bar of the previous session stamped after t is not there yet.
    # A benchmark may always count, with or without a bar.
    t = NOW
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(conn, "prices_daily", _bar(ACME, date(2019, 6, 27), t - timedelta(hours=1)))
        insert_row(conn, "prices_daily", _bar(DUAL_B, date(2019, 6, 27), t + timedelta(hours=1)))
        window = (date(2019, 6, 27), date(2019, 6, 27))
        may = _may_count(conn, t, window, {}, {SPY})
    assert may == {ACME, SPY}


def test_run_messages_are_redacted_cleaned_and_capped(
    tmp_path: Path, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    secret = "sk-sentinel-4f2a"
    settings = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
        ingest={"max_message_chars": 200},
        alpaca_api_secret=secret,
        sec_edgar_user_agent="Owner owner@example.com",
    )

    class Leaky(_Prices):
        def corporate_actions(
            self, security_ids: Sequence[str], start: date, end: date
        ) -> list[CorporateAction]:
            raise RuntimeError(f"auth {secret} ua Owner owner@example.com\x1b[31m" + "x" * 5000)

    result = ingest_session(settings, prices=Leaky(), filings=_filings(), clock=lambda: NOW)
    stored = read("SELECT message FROM ingestion_runs WHERE source = 'alpaca'")[0][0]
    for message in (stored, result.runs[-1].message):
        assert secret not in message and "owner@example.com" not in message
        assert "[redacted]" in message and "\x1b" not in message
        assert len(message) == 200


# --- #573: a failed run row names where the error was raised --------------


def test_failed_run_message_names_the_raising_file_line_and_function(
    settings: Settings,
) -> None:
    result = _run(settings, _Prices(fail="bars"))
    assert result.runs[-1].status == FAILED
    message = result.runs[-1].message
    assert "RuntimeError: bars endpoint down" in message
    assert " | at: " in message
    where = message.split(" | at: ", 1)[1]
    assert "test_ingest.py" in where
    assert " in bars" in where


def test_failed_run_message_has_no_local_or_argument_values(settings: Settings) -> None:
    # The traceback is read for file/line/function only; `_where` never reads
    # a frame's locals or arguments, so a secret-looking one of each must not
    # leak even though neither is a configured secret `_clean` would catch.
    def _inner(argument: str) -> None:
        local_secret = "sk-local-9f3c21"
        assert local_secret  # kept "in scope" for the frame, never read back
        raise ValueError("boom")

    class Boom(_Prices):
        def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
            _inner("sk-argument-7e21aa")

    result = _run(settings, Boom())
    message = result.runs[-1].message
    assert result.runs[-1].status == FAILED
    assert "ValueError: boom" in message
    assert "sk-local-9f3c21" not in message
    assert "sk-argument-7e21aa" not in message
    assert "_inner" in message


def test_failed_run_message_includes_the_chained_causes_frame(settings: Settings) -> None:
    def _root_cause() -> None:
        raise KeyError("cik")

    class Boom(_Prices):
        def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
            try:
                _root_cause()
            except KeyError as exc:
                raise RuntimeError("wrapped") from exc

    result = _run(settings, Boom())
    message = result.runs[-1].message
    assert result.runs[-1].status == FAILED
    assert "RuntimeError: wrapped" in message
    where = message.split(" | at: ", 1)[1]
    assert "_root_cause" in where
    assert "bars" in where


def test_failed_run_message_where_is_length_bounded(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
        ingest={"max_where_chars": 60},
    )

    def _deep(n: int) -> None:
        if n == 0:
            raise RuntimeError("deep failure")
        _deep(n - 1)

    class Boom(_Prices):
        def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
            _deep(20)

    result = _run(settings, Boom())
    message = result.runs[-1].message
    assert result.runs[-1].status == FAILED
    # The error text is never cut to make room for frames.
    assert "RuntimeError: deep failure" in message
    assert len(message) <= 60


# --- filed-row revisions, decided per key (quant-auditor re-audit on #164) --


def _store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    init_schema(conn)
    return conn


def _classification(sic: int, known_at: datetime, ingested_at: datetime) -> dict[str, Any]:
    return {
        "security_id": ACME,
        "sic": sic,
        "security_type": "common",
        "rule": "common_default",
        "known_at": known_at,
        "ingested_at": ingested_at,
        "source": "edgar",
        "provenance": "filing",
    }


def _security(name: str, known_at: datetime, ingested_at: datetime) -> dict[str, Any]:
    return {
        "security_id": ACME,
        "cik": ACME,
        "name": name,
        "benchmark": False,
        "known_at": known_at,
        "ingested_at": ingested_at,
        "source": "edgar",
        "provenance": "filing",
    }


T1, T2, T3 = _at(2019, 1, 2), _at(2019, 2, 1), _at(2019, 3, 1)


@pytest.mark.parametrize(
    ("table", "make", "old", "restated", "kept"),
    [
        ("classifications", _classification, 3571, 3572, 7372),
        ("securities", _security, "Acme", "Acme Restated", "Acme Two"),
    ],
)
def test_a_restated_older_row_neither_ties_nor_fails(
    table: str, make: Callable[..., dict[str, Any]], old: Any, restated: Any, kept: Any
) -> None:
    conn = _store()
    first = [make(old, T1, NOW), make(kept, T2, NOW)]
    assert _add_rows(conn, table, first, ingested_at=NOW, current=False) == 2
    later = NOW + timedelta(days=1)
    again = [make(restated, T1, later), make(kept, T2, later)]
    for _ in range(2):  # the restated history adds nothing, now and on re-run
        assert _add_rows(conn, table, again, ingested_at=later, current=False) == 0
    column = "sic" if table == "classifications" else "name"
    read = conn.execute(f"SELECT {column} FROM {table} ORDER BY known_at").fetchall()
    assert read[-1] == (kept,)


def test_a_late_filing_behind_a_stored_revision_is_one_row_at_ingested_at() -> None:
    conn = _store()
    _add_rows(conn, "securities", [_security("A", T1, NOW)], ingested_at=NOW, current=False)
    one = NOW + timedelta(days=1)
    _add_rows(conn, "securities", [_security("B", T1, one)], ingested_at=one, current=False)
    two = NOW + timedelta(days=2)
    late = [_security("B", T1, two), _security("C", T3, two)]  # C@T3 < the stored B@one
    assert _add_rows(conn, "securities", late, ingested_at=two, current=False) == 1
    assert (
        _add_rows(conn, "securities", late, ingested_at=two + timedelta(hours=1), current=False)
        == 0
    )
    rows = conn.execute("SELECT name, known_at FROM securities ORDER BY known_at").fetchall()
    assert rows == [("A", T1), ("B", one), ("C", two)]


def test_cover_page_duplicate_pair_does_not_abort_the_listings_write() -> None:
    """#687: `build_master` must never hand `_add_rows` two `listings` rows
    with the same (security_id, ticker, exchange, valid_from, known_at)
    key, or the store's UNIQUE constraint aborts the whole EDGAR write
    (the failing path of the 2026-10-03 backfill rerun). Real shapes:
    Honda (CIK 0000864270) 10-Q accepted 2021-11-09, two 0.750%
    medium-term notes both tagged HMC/26A on one cover page; Moatable
    (CIK 0001509223) 10-Q accepted 2023-08-14, Class A ordinary shares and
    their ADS both retickered to MTBL on one cover page."""
    notes_cik = "0000900001"
    ads_cik = "0000900002"
    ads_title = "American depositary shares, each representing 45 Class A ordinary shares"
    class_a_title = "Class A ordinary shares, par value $0.001 per share*"
    source = FixtureFilingSource(
        index=[
            FilingIndexEntry(notes_cik, "Honda-like Co", "10-K", f"{notes_cik}-1", _at(2015, 3, 1)),
            FilingIndexEntry(ads_cik, "Moatable-like Inc", "10-K", f"{ads_cik}-1", _at(2015, 3, 1)),
        ],
        cover_pages=[
            CoverPage(
                notes_cik,
                f"{notes_cik}-2",
                _at(2021, 3, 1),
                (CoverListing("Common Stock, par value $0.50 per share", "HMC", "NYSE"),),
            ),
            CoverPage(
                notes_cik,
                f"{notes_cik}-3",
                datetime(2021, 11, 9, 17, 59, 52, tzinfo=UTC),
                (
                    CoverListing("Common Stock, par value $0.50 per share", "HMC", "NYSE"),
                    CoverListing(
                        "0.750% Medium-Term Notes, Series ADue November 25, 2026",
                        "HMC/26A",
                        "NYSE",
                    ),
                    CoverListing(
                        "0.750% Medium-Term Notes, Series ADue January 17, 2024",
                        "HMC/26A",
                        "NYSE",
                    ),
                    CoverListing(
                        "1.100% Medium-Term Notes, Series BDue October 1, 2025",
                        "HMC/25B",
                        "NYSE",
                    ),
                ),
            ),
            CoverPage(
                ads_cik, f"{ads_cik}-2", _at(2020, 3, 1), (CoverListing(ads_title, "RENN", "NYSE"),)
            ),
            CoverPage(
                ads_cik,
                f"{ads_cik}-3",
                _at(2023, 3, 31),
                (
                    CoverListing(class_a_title, "RENN", "NYSE"),
                    CoverListing(ads_title, "RENN", "NYSE"),
                ),
            ),
            CoverPage(
                ads_cik,
                f"{ads_cik}-4",
                datetime(2023, 8, 14, 20, 56, 14, tzinfo=UTC),
                (
                    CoverListing(class_a_title, "MTBL", "NYSE"),
                    CoverListing(ads_title, "MTBL", "NYSE"),
                ),
            ),
        ],
    )
    ingested_at = datetime(2023, 8, 15, tzinfo=UTC)
    master = build_master(source, Settings(_env_file=None), ingested_at=ingested_at)
    conn = _store()
    added = _add_rows(conn, "listings", master.listings, ingested_at=ingested_at, current=False)
    assert added == len(master.listings)
    rows = conn.execute(
        "SELECT security_id, ticker, exchange, valid_from, known_at, COUNT(*) AS n "
        "FROM listings GROUP BY 1, 2, 3, 4, 5 HAVING COUNT(*) > 1"
    ).fetchall()
    assert rows == []


def test_fact_class_uses_the_classification_known_at_acceptance(settings: Settings) -> None:
    # A class classified common only after the fact was accepted cannot take it.
    source = _filings()
    master = build_master(source, settings, ingested_at=NOW)
    classes = build_classifications(source, master, settings, ingested_at=NOW)
    early = _fact(ACME, "", 1, f"{ACME}-18-000009", _at(2018, 3, 1) - timedelta(days=1))
    rows, unmatched = fact_rows([early], master, classes, ingested_at=NOW)
    assert rows == () and unmatched == (early,)


def test_a_dry_run_whose_check_failures_raises_writes_no_run_row(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    """A dry run never writes a run row, failed or not (#275 safety review)."""

    class Unhealthy(FixtureFilingSource):
        def check_failures(self) -> None:
            raise RuntimeError("too many failures")

    _run(settings, source="edgar")  # a real run first, so the store exists
    before = read("SELECT count(*) FROM ingestion_runs")
    result = _run(settings, filings=_filings(cls=Unhealthy), source="edgar", dry_run=True)
    assert result.runs[0].status == FAILED
    assert read("SELECT count(*) FROM ingestion_runs") == before


# --- #334: every SecretStr field is redacted from run messages ---------------


def _secret_fields() -> list[str]:
    from pydantic import SecretStr

    return sorted(
        name
        for name, info in Settings.model_fields.items()
        if "SecretStr" in str(info.annotation) or info.annotation is SecretStr
    )


def test_every_secret_field_is_redacted_from_run_messages() -> None:
    from tradepartner.ingest import _clean

    fields = _secret_fields()
    # The eight known today; a new SecretStr field joins this loop by itself.
    assert {
        "alpaca_api_key",
        "alpaca_api_secret",
        "alpaca_paper_api_key",
        "alpaca_paper_api_secret",
        "sec_edgar_user_agent",
        "alert_smtp_user",
        "alert_smtp_password",
        "alert_email_to",
    } <= set(fields)
    values = {name: f"value-of-{name}-7c1" for name in fields}
    settings = Settings(_env_file=None, **values)
    message = "; ".join(f"{name} leaked {value}" for name, value in values.items())
    cleaned = _clean(message, settings)
    for value in values.values():
        assert value not in cleaned
    assert cleaned.count("[redacted]") == len(values)


def _secret_offenders(model: type[Any]) -> list[str]:
    """Fields of `model` holding a secret that `config.secret_values` would
    miss: anything but a bare or Optional `SecretStr` at the top level, and any
    `SecretStr`/`SecretBytes` inside a nested model or container."""
    import types
    import typing

    from pydantic import BaseModel, SecretBytes, SecretStr

    def mentions_secret(annotation: object) -> bool:
        if annotation in (SecretStr, SecretBytes):
            return True
        return any(mentions_secret(arg) for arg in typing.get_args(annotation))

    def models_in(annotation: object) -> list[type[BaseModel]]:
        found = []
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            found.append(annotation)
        for arg in typing.get_args(annotation):
            found += models_in(arg)
        return found

    offenders = []
    for name, info in model.model_fields.items():
        annotation = info.annotation
        plain = annotation is SecretStr or (
            typing.get_origin(annotation) in (typing.Union, types.UnionType)
            and set(typing.get_args(annotation)) == {SecretStr, type(None)}
        )
        if mentions_secret(annotation) and not plain:
            offenders.append(name)
        seen: set[type[BaseModel]] = set()
        stack = models_in(annotation)
        while stack:
            sub_model = stack.pop()
            if sub_model in seen:
                continue
            seen.add(sub_model)
            for sub_name, sub in sub_model.model_fields.items():
                if mentions_secret(sub.annotation):
                    offenders.append(f"{name}.{sub_name}")
                stack += models_in(sub.annotation)
    return offenders


def test_secrets_live_only_in_top_level_secretstr_fields() -> None:
    """`config.secret_values` finds secrets among `Settings`' own fields by type;
    a secret nested in a sub-model, a container or `SecretBytes` would be missed,
    so this pins that none exists (#334 review)."""
    assert _secret_offenders(Settings) == []


def test_the_secret_guard_flags_what_the_scrub_would_miss() -> None:
    from pydantic import BaseModel, SecretBytes, SecretStr

    class _Sub(BaseModel):
        token: SecretStr | None = None

    class _Probe(BaseModel):
        fine: SecretStr | None = None
        also_fine: SecretStr = SecretStr("x")
        listed: list[SecretStr] | None = None
        tupled: tuple[SecretStr, ...] = ()
        mapped: dict[str, SecretStr] = {}
        raw: SecretBytes | None = None
        nested: _Sub | None = None

    assert sorted(_secret_offenders(_Probe)) == [
        "listed",
        "mapped",
        "nested.token",
        "raw",
        "tupled",
    ]


# --- statement facts: first vintages (#660, T77b) ----------------------------

F1 = f"{ACME}-19-000101"  # FY2018 10-K
F2 = f"{ACME}-19-000102"  # a later filing carrying FY2018 as a comparative
F3 = f"{ACME}-19-000103"
UNSTAMPED = f"{ACME}-19-000199"  # no stamp record yet
AT1, AT2, AT3 = _at(2019, 3, 1), _at(2019, 5, 1), _at(2019, 6, 3)
FY18 = (date(2018, 1, 1), date(2018, 12, 31))
FY17 = (date(2017, 1, 1), date(2017, 12, 31))
_TAG = {
    "revenue": "us-gaap:Revenues",
    "cost_of_revenue": "us-gaap:CostOfRevenue",
    "gross_profit": "us-gaap:GrossProfit",
    "total_assets": "us-gaap:Assets",
}


def _sf(
    fact: str,
    value: float,
    accession: str,
    accepted_at: datetime | None,
    *,
    period: tuple[date | None, date] = FY18,
    filed: date | None = None,
    cik: str = ACME,
    unit: str = "USD",
    comparative: bool = False,
) -> StatementFactRecord:
    """One statement record; `filed` defaults to the acceptance date."""
    if filed is None:
        assert accepted_at is not None, "an unstamped record needs its filed date"
        filed = accepted_at.date()
    return StatementFactRecord(
        cik=cik,
        fact_name=fact,
        xbrl_tag=_TAG[fact],
        period_start=period[0],
        period_end=period[1],
        value=value,
        unit=unit,
        form="" if accepted_at is None else "10-K",
        accession=accession,
        accepted_at=accepted_at,
        filed=filed,
        comparative=comparative,
    )


def _rows(
    records: Sequence[StatementFactRecord],
    stored: dict[StatementKey, StatementVintage] | None = None,
    conflicts: set[ConflictKey] | None = None,
) -> tuple[dict[tuple[str, date], dict[str, Any]], StatementCounts]:
    rows, counts = statement_fact_rows(records, stored or {}, conflicts or set(), ingested_at=NOW)
    return {(r["fact_name"], r["period_end"]): r for r in rows}, counts


def _gp_key(period: tuple[date | None, date] = FY18) -> StatementKey:
    start, end = period
    return ("gross_profit", end, 0 if start is None else (end - start).days)


def test_first_vintage_is_kept_across_a_restating_filing() -> None:
    rows, counts = _rows(
        [
            _sf("revenue", 110, F2, AT2, comparative=True),  # restated a year later
            _sf("revenue", 100, F1, AT1),
            _sf("total_assets", 500, F1, AT1, period=(None, date(2018, 12, 31))),
            _sf("total_assets", 500, F2, AT2, period=(None, date(2018, 12, 31))),  # identical
        ]
    )
    revenue = rows[("revenue", FY18[1])]
    assert (revenue["value"], revenue["known_at"], revenue["filing_accession"]) == (100, AT1, F1)
    assert revenue["basis"] == "reported" and revenue["xbrl_tag"] == "us-gaap:Revenues"
    assessed = rows[("total_assets", FY18[1])]
    assert (assessed["period_start"], assessed["period_days"]) == (None, 0)
    assert (counts.added, counts.restated, counts.vintage_late) == (2, 1, 0)


def test_a_stored_key_skips_every_carrier_and_its_own_accession_silently() -> None:
    stored = {("revenue", FY18[1], 364): StatementVintage(AT1, 100.0, F1)}
    rows, counts = _rows([_sf("revenue", 100, F1, AT1), _sf("revenue", 120, F2, AT2)], stored)
    assert rows == {}
    assert (counts.restated, counts.vintage_late, counts.added) == (1, 0, 0)


@pytest.mark.parametrize(
    ("filed", "held"),
    [(date(2019, 2, 27), True), (AT1.date(), True), (date(2019, 3, 2), False)],
    ids=["earlier", "same-day tie", "later"],
)
def test_an_unstamped_carrier_filed_on_or_before_holds_the_key(filed: date, held: bool) -> None:
    records = [
        _sf("revenue", 100, F1, AT1),
        _sf("revenue", 90, UNSTAMPED, None, filed=filed),
        _sf("revenue", 100, F2, AT2, comparative=True),  # a later carrier stays held too
    ]
    rows, counts = _rows(records)
    assert (("revenue", FY18[1]) in rows) is not held
    assert counts.held == int(held)


def test_held_counts_an_entry_that_holds_only_a_later_carrier() -> None:
    records = [
        _sf("revenue", 100, F1, AT1),
        _sf("revenue", 90, UNSTAMPED, None, filed=AT1.date()),
        _sf("revenue", 95, f"{ACME}-19-000198", None, filed=date(2019, 4, 1)),
        _sf("revenue", 100, F2, AT2, comparative=True),
    ]
    _, counts = _rows(records)
    assert counts.held == 2


def test_once_stamped_the_earlier_carrier_is_the_vintage() -> None:
    stamped = _at(2019, 2, 27)
    rows, counts = _rows([_sf("revenue", 100, F1, AT1), _sf("revenue", 90, UNSTAMPED, stamped)])
    assert rows[("revenue", FY18[1])]["filing_accession"] == UNSTAMPED
    assert (counts.held, counts.restated) == (0, 1)


def test_a_record_accepted_before_its_stored_vintage_is_late_not_written() -> None:
    stored = {("revenue", FY18[1], 364): StatementVintage(AT2, 110.0, F2)}
    rows, counts = _rows([_sf("revenue", 100, F1, AT1)], stored)
    assert rows == {}
    assert (counts.vintage_late, counts.restated) == (1, 0)


def test_a_record_accepted_after_the_run_raises() -> None:
    with pytest.raises(ValueError, match="after ingested_at"):
        _rows([_sf("revenue", 100, F1, NOW + timedelta(seconds=1))])


def _components(
    accession: str, accepted_at: datetime, *, period: tuple[date | None, date] = FY18
) -> list[StatementFactRecord]:
    return [
        _sf("revenue", 100, accession, accepted_at, period=period),
        _sf("cost_of_revenue", 60, accession, accepted_at, period=period),
    ]


def test_one_accessions_components_derive_gross_profit_at_its_acceptance() -> None:
    """Also the case of a `GrossProfit` the parser skipped as non-USD or
    malformed: it reaches the ingest as no record, so the candidate stands."""
    rows, counts = _rows(
        [*_components(F1, AT1), *_components(F1, AT1, period=FY17)]  # FY17: a comparative
    )
    derived = rows[("gross_profit", FY18[1])]
    assert derived["value"] == 40
    assert (derived["known_at"], derived["filing_accession"], derived["basis"]) == (
        AT1,
        F1,
        "derived",
    )
    assert derived["xbrl_tag"] == "us-gaap:Revenues-us-gaap:CostOfRevenue"
    assert derived["comparative"] is False
    assert rows[("gross_profit", FY17[1])]["comparative"] is True
    assert counts.derived == 2


def test_components_from_two_accessions_derive_nothing() -> None:
    rows, counts = _rows(
        [_sf("revenue", 100, F1, AT1), _sf("cost_of_revenue", 60, F2, AT2, comparative=True)]
    )
    assert ("gross_profit", FY18[1]) not in rows and counts.derived == 0


def test_a_withheld_component_derives_nothing_from_either_filing() -> None:
    """F1's cost of revenue was withheld (a conflict: no record), so its
    vintage is F2's and neither filing holds both vintages."""
    rows, counts = _rows([_sf("revenue", 100, F1, AT1), *_components(F2, AT2)])
    assert rows[("cost_of_revenue", FY18[1])]["filing_accession"] == F2
    assert ("gross_profit", FY18[1]) not in rows and counts.derived == 0


def test_a_filed_gross_profit_in_the_first_carrier_is_reported_nothing_derived() -> None:
    rows, counts = _rows([*_components(F1, AT1), _sf("gross_profit", 41, F1, AT1)])
    assert rows[("gross_profit", FY18[1])]["basis"] == "reported"
    assert rows[("gross_profit", FY18[1])]["value"] == 41
    assert counts.derived == 0


def test_a_later_filed_gross_profit_is_restated_also_in_one_run() -> None:
    rows, counts = _rows(
        [*_components(F1, AT1), _sf("gross_profit", 45, F2, AT2, comparative=True)]
    )
    derived = rows[("gross_profit", FY18[1])]
    assert (derived["basis"], derived["known_at"], derived["value"]) == ("derived", AT1, 40)
    assert (counts.derived, counts.restated) == (1, 1)


def test_a_held_gross_profit_key_derives_nothing_and_counts_held_once() -> None:
    rows, counts = _rows(
        [
            *_components(F1, AT1),
            _sf("gross_profit", 41, UNSTAMPED, None, filed=AT1.date()),
            _sf("gross_profit", 41, F2, AT2, comparative=True),
        ]
    )
    assert ("gross_profit", FY18[1]) not in rows
    assert ("revenue", FY18[1]) in rows and ("cost_of_revenue", FY18[1]) in rows
    assert (counts.held, counts.derived) == (1, 0)


def test_a_conflict_withheld_gross_profit_derives_nothing_until_a_clean_filing() -> None:
    conflict = {(F1, "gross_profit", FY18[1], 364)}
    rows, counts = _rows(_components(F1, AT1), conflicts=conflict)
    assert ("gross_profit", FY18[1]) not in rows and counts.derived == 0

    later = _sf("gross_profit", 42, F2, AT2, comparative=True)
    rows, counts = _rows([*_components(F1, AT1), later], conflicts=conflict)
    vintage = rows[("gross_profit", FY18[1])]
    assert (vintage["basis"], vintage["filing_accession"], vintage["comparative"]) == (
        "reported",
        F2,
        True,
    )


def test_components_in_two_units_derive_nothing() -> None:
    rows, _ = _rows([_sf("revenue", 100, F1, AT1), _sf("cost_of_revenue", 60, F1, AT1, unit="EUR")])
    assert ("gross_profit", FY18[1]) not in rows


# --- statement facts through the EDGAR chunk ---------------------------------


class _StatementSource(FixtureFilingSource):
    """A fixture source answering `statement_facts` as the adapter does:
    `conflicts` restored onto `statement_conflict_keys` on every read, the
    adapter's counts as plain attributes, and every call logged."""

    def __init__(
        self,
        records: Sequence[StatementFactRecord],
        *,
        conflicts: Sequence[StatementConflict] = (),
        unstampable: int = 0,
    ) -> None:
        base = _filings()
        super().__init__(
            index=base._index,
            snapshot=base._snapshot,
            facts=base._facts,
            headers=base._headers,
            cover_pages=base._cover_pages,
            delistings=base._delistings,
            statement_facts=records,
        )
        self._conflicts = list(conflicts)
        self.statement_conflict_keys: list[StatementConflict] = []
        self.statement_conflicts = len(conflicts)
        self.statement_unstampable = unstampable
        self.calls: list[str] = []

    def statement_facts(self, cik: str) -> list[StatementFactRecord]:
        self.calls.append(cik)
        listed = set(self.statement_conflict_keys)
        self.statement_conflict_keys.extend(c for c in self._conflicts if c not in listed)
        return super().statement_facts(cik)


def _switched(settings: Settings, on: bool = True) -> Settings:
    edgar = settings.edgar.model_copy(update={"statement_facts_enabled": on})
    return settings.model_copy(update={"edgar": edgar})


def _edgar(settings: Settings, source: FilingSource, **kwargs: Any) -> SourceRun:
    result = _run(settings, filings=source, source="edgar", **kwargs)  # type: ignore[arg-type]
    (run,) = result.runs
    assert run.status == OK, run.message
    return run


def _statement_rows(read: Callable[[str], list[tuple[Any, ...]]]) -> list[tuple[Any, ...]]:
    return read(
        "SELECT fact_name, period_end, value, filing_accession, basis, known_at "
        "FROM statement_facts ORDER BY fact_name, period_end"
    )


def test_ingest_keeps_the_first_vintage_and_a_rerun_adds_nothing(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    on = _switched(settings)
    source = _StatementSource(
        [_sf("revenue", 100, F1, AT1), _sf("revenue", 110, F2, AT2, comparative=True)]
    )
    first = _edgar(on, source)
    assert statement_counts(first.message) == {
        "added": 1,
        "held": 0,
        "vintage_late": 0,
        "restated": 1,
        "derived": 0,
        "unstampable": 0,
        "conflicts": 0,
    }
    assert _statement_rows(read) == [("revenue", FY18[1], 100, F1, "reported", AT1)]
    with duckdb.connect(on.store.path, read_only=True) as conn:
        for t in (AT1, AT2, NOW):
            frame = statement_facts_as_of(conn, t)
            assert frame["value"].to_list() == [100.0], t
        assert statement_facts_as_of(conn, AT1 - timedelta(microseconds=1)).is_empty()
    rows_before = _counts(read)
    again = _edgar(on, source)
    assert statement_counts(again.message)["added"] == 0
    assert _counts(read) == rows_before
    assert len(_statement_rows(read)) == 1


def test_known_at_is_the_acceptance_never_the_filed_date(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    """No look-ahead: an after-hours filing (accepted 18:30 New York, `filed`
    the next day) is visible from the acceptance instant, not before."""
    accepted = datetime(2019, 3, 1, 23, 30, tzinfo=UTC)
    _edgar(
        _switched(settings),
        _StatementSource([_sf("revenue", 100, F1, accepted, filed=date(2019, 3, 4))]),
    )
    assert _statement_rows(read)[0][-1] == accepted
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        assert statement_facts_as_of(conn, accepted - timedelta(microseconds=1)).is_empty()
        assert len(statement_facts_as_of(conn, accepted)) == 1


def test_a_late_record_is_counted_and_the_rebuild_makes_it_the_vintage(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    on = _switched(settings)
    _edgar(on, _StatementSource([_sf("revenue", 110, F2, AT2)]))
    both = _StatementSource([_sf("revenue", 100, F1, AT1), _sf("revenue", 110, F2, AT2)])
    late = _edgar(on, both)
    assert statement_counts(late.message)["vintage_late"] == 1
    assert [r[3] for r in _statement_rows(read)] == [F2]

    rebuilt = _edgar(on, both, rebuild_statement_facts=True)
    counts = statement_counts(rebuilt.message)
    assert (counts["deleted"], counts["added"], counts["vintage_late"]) == (1, 1, 0)
    assert counts["restated"] == 1
    assert [r[3] for r in _statement_rows(read)] == [F1]


def test_an_unstampable_accession_holds_nothing_and_its_count_is_reported(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    """The adapter drops a settled-unstampable accession's entries and counts
    them; nothing reaches the hold rule, so the stamped carrier is stored."""
    run = _edgar(
        _switched(settings), _StatementSource([_sf("revenue", 100, F1, AT1)], unstampable=3)
    )
    counts = statement_counts(run.message)
    assert (counts["unstampable"], counts["held"], counts["added"]) == (3, 0, 1)


def test_a_conflict_withheld_gross_profit_stays_empty_across_runs_until_clean(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    """The second run's source restores the conflict list from its cache on
    every read, so the candidate stays withheld and no row is added."""
    on = _switched(settings)
    conflict = StatementConflict(F1, "gross_profit", *FY18, (40.0, 41.0))
    for _ in range(2):
        run = _edgar(on, _StatementSource(_components(F1, AT1), conflicts=[conflict]))
        assert statement_counts(run.message)["derived"] == 0
        assert [r[0] for r in _statement_rows(read)] == ["cost_of_revenue", "revenue"]
    clean = [*_components(F1, AT1), _sf("gross_profit", 41, F2, AT2, comparative=True)]
    _edgar(on, _StatementSource(clean, conflicts=[conflict]))
    gross = [r for r in _statement_rows(read) if r[0] == "gross_profit"]
    assert gross == [("gross_profit", FY18[1], 41, F2, "reported", AT2)]


def test_switch_off_asks_nothing_and_adds_no_row(
    settings: Settings, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    settings = _switched(settings, False)  # on by default since T78 (#1127)
    source = _StatementSource(_components(F1, AT1))
    run = _edgar(settings, source)
    assert source.calls == []
    assert "statement_" not in run.message
    assert read("SELECT count(*) FROM statement_facts") == [(0,)]
    with pytest.raises(ValueError, match="statement_facts_enabled"):
        _run(settings, filings=source, source="edgar", rebuild_statement_facts=True)
    assert read("SELECT count(*) FROM statement_facts") == [(0,)]


def test_each_cik_is_one_bulk_insert(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    read: Callable[[str], list[tuple[Any, ...]]],
) -> None:
    inserts: list[set[str]] = []
    real = ingest_module.insert_statement_frame

    def counted(conn: duckdb.DuckDBPyConnection, frame: Any) -> None:
        inserts.append(set(frame.column("cik").to_pylist()))
        real(conn, frame)

    monkeypatch.setattr(ingest_module, "insert_statement_frame", counted)
    records = [
        *_components(F1, AT1),
        _sf("revenue", 7, f"{DUAL}-19-000101", AT1, cik=DUAL),
        _sf("total_assets", 9, f"{DUAL}-19-000101", AT1, cik=DUAL, period=(None, FY18[1])),
    ]
    _edgar(_switched(settings), _StatementSource(records))
    assert inserts == [{ACME}, {DUAL}]
    assert read("SELECT cik, count(*) FROM statement_facts GROUP BY cik ORDER BY cik") == [
        (ACME, 3),
        (DUAL, 2),
    ]


def test_a_frame_with_a_naive_timestamp_is_refused_before_the_insert(settings: Settings) -> None:
    row = _statement_row_for_test()
    with open_for_write(settings) as conn:
        init_schema(conn)
        with pytest.raises(TypeError, match="known_at must be tz-aware"):
            insert_statement_frame(
                conn, statement_frame([{**row, "known_at": AT1.replace(tzinfo=None)}])
            )
        assert conn.execute("SELECT count(*) FROM statement_facts").fetchone() == (0,)
        insert_statement_frame(conn, statement_frame([row]))
        assert conn.execute("SELECT count(*) FROM statement_facts").fetchone() == (1,)


def _statement_row_for_test() -> dict[str, Any]:
    rows, _ = statement_fact_rows([_sf("revenue", 100, F1, AT1)], {}, set(), ingested_at=NOW)
    return rows[0]


# --- statement facts: the fetch pass and the lock, over the EDGAR adapter ------


class _AdapterStatements(FixtureFilingSource):
    """The fixture master's answers, with `statement_facts` and its counts
    served by a real `EdgarFilingSource` over a fake transport; `calls`
    logs each CIK asked and whether the write phase was running."""

    def __init__(self, adapter: EdgarFilingSource) -> None:
        base = _filings()
        super().__init__(
            index=base._index,
            snapshot=base._snapshot,
            facts=base._facts,
            headers=base._headers,
            cover_pages=base._cover_pages,
            delistings=base._delistings,
        )
        self.adapter = adapter
        self.writing = False
        self.calls: list[tuple[str, bool]] = []

    def statement_facts(self, cik: str) -> list[StatementFactRecord]:
        self.calls.append((cik, self.writing))
        return self.adapter.statement_facts(cik)

    def __getattr__(self, name: str) -> Any:  # the adapter's plain statement_* attributes
        if name.startswith("statement_"):
            return getattr(self.adapter, name)
        raise AttributeError(name)


def _companyfacts_entry(accession: str, period: tuple[date, date] | date, value: float) -> Any:
    row: dict[str, Any] = {"accn": accession, "val": value, "fy": 2018, "fp": "FY"}
    row.update(form="10-K", filed="2019-03-01")
    if isinstance(period, tuple):
        row["start"], row["end"] = period[0].isoformat(), period[1].isoformat()
    else:
        row["end"] = period.isoformat()
    return row


def test_under_the_lock_each_cik_is_one_cache_read_and_the_proxy_holds_no_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, read: Callable[[str], list[tuple[Any, ...]]]
) -> None:
    settings = Settings(
        _env_file=None,
        sec_edgar_user_agent="TradePartner test-agent test@example.com",
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
        edgar={"cache_dir": str(tmp_path / "cache"), "statement_facts_enabled": True},
    )
    settings = settings.model_copy(
        update={"edgar": settings.edgar.model_copy(update={"requests_per_second": 1e6})}
    )
    accession = f"{ACME}-19-000001"  # the fixture's cover-page 10-K
    body = {
        "cik": int(ACME),
        "facts": {
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {"shares": [_companyfacts_entry(accession, date(2019, 2, 15), 5)]}
                }
            },
            "us-gaap": {
                "Revenues": {"units": {"USD": [_companyfacts_entry(accession, FY18, 100)]}},
                "CostOfRevenue": {"units": {"USD": [_companyfacts_entry(accession, FY18, 60)]}},
                "GrossProfit": {
                    "units": {
                        "USD": [  # a same-tag conflict for FY2017: withheld, listed
                            _companyfacts_entry(accession, FY17, 30),
                            _companyfacts_entry(accession, FY17, 31),
                        ]
                    }
                },
            },
        },
    }
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        assert str(request.url) == f"https://data.sec.gov/api/xbrl/companyfacts/CIK{ACME}.json"
        return httpx.Response(200, json=body)

    adapter = EdgarFilingSource(
        settings, client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    adapter._filing_index_ran = True
    adapter._fsn_ready = True
    adapter._fsn_loaded_periods = ("2099_01",)
    adapter._save_stamps(
        ACME, {accession: SubmissionRecord(accession, "10-K", "doc.htm", True, _at(2019, 3, 1))}
    )  # DUAL has no stamped filing: answered with no request, as `facts` is
    source = _AdapterStatements(adapter)
    recorded = _Recorded(source)
    _prefetch(recorded, settings, dry_run=False)
    assert source.calls == [(ACME, False), (DUAL, False)]
    assert not any(key[0] == "statement_facts" for key in recorded._answers)
    fetched = (adapter.requests, adapter.statement_conflicts, adapter.statement_none)
    assert fetched == (1, 1, 0)

    real = ingest_module._write_statement_facts

    def locked(*args: Any, **kwargs: Any) -> Any:
        source.writing = True
        return real(*args, **kwargs)

    monkeypatch.setattr(ingest_module, "_write_statement_facts", locked)
    with open_for_write(settings) as conn:
        init_schema(conn)
        _, message = _ingest_filings(conn, settings, recorded, lambda: NOW, "run-1")
    assert [cik for cik, writing in source.calls if writing] == [ACME, DUAL]
    assert (adapter.requests, adapter.statement_conflicts, adapter.statement_none) == fetched
    assert urls == [f"https://data.sec.gov/api/xbrl/companyfacts/CIK{ACME}.json"]
    assert not any(key[0] == "statement_facts" for key in recorded._answers)
    assert statement_counts(message)["derived"] == 1
    assert ("gross_profit", FY17[1], 0.0) not in [(r[0], r[1], r[2]) for r in _statement_rows(read)]
    assert [r[:3] for r in _statement_rows(read)] == [
        ("cost_of_revenue", FY18[1], 60),
        ("gross_profit", FY18[1], 40),
        ("revenue", FY18[1], 100),
    ]
