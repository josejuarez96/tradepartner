"""Tests for single-session ingest (spec reqs 1, 9, 10; plan T16).

Scenarios run `ingest_session` against a real DuckDB file under `tmp_path`,
with a `FixtureFilingSource` of synthetic filings and `_Prices`, a stub
`PriceSource` whose bars, omissions and failures each test states.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
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
from tradepartner.ingest import (
    FACT_NAMES,
    FAILED,
    LOCKED,
    OK,
    STALE,
    IngestResult,
    _add_rows,
    expected_session,
    fact_rows,
    ingest_session,
)
from tradepartner.store.asof import facts_as_of, live_actions_as_of, prices_as_of
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
        ],
        headers=[
            FilingHeader(ACME, f"{ACME}-19-000001", "10-K", 3571, _at(2019, 3, 1)),
            FilingHeader(DUAL, f"{DUAL}-19-000001", "10-K", 7372, _at(2019, 3, 4)),
        ],
        facts=[
            _fact(ACME, "", 5_000_000, f"{ACME}-19-000001", _at(2019, 3, 1)),
            _fact(DUAL, "us-gaap:CommonClassAMember", 9_000_000, f"{DUAL}-19-1", _at(2019, 3, 4)),
            _fact(DUAL, "us-gaap:CommonClassBMember", 1_000_000, f"{DUAL}-19-1", _at(2019, 3, 4)),
            *extra_facts,
        ],
        snapshot=[CompanySnapshotEntry(SPY_TRUST, "SPDR S&P 500", "SPY", "NYSE_ARCA", fetched_at)],
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

    message = _run(settings, filings=_filings(cls=OnlyFacts), source="edgar").runs[0].message
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
        fsn_missing = 5

    message = _run(settings, filings=_filings(cls=Fsn), source="edgar").runs[0].message
    counts = (
        "; FSN re-issued: 2; FSN duplicates: 1; FSN re-issues unchecked: 3; "
        "FSN incomplete listings: 4; FSN missing: 5; missing"
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
    """T11h: failed filings, quarantined accessions and facts missing."""

    class Failing(FixtureFilingSource):
        failed_filings = 2
        quarantined = 1
        facts_missing = 4

    message = _run(settings, filings=_filings(cls=Failing), source="edgar").runs[0].message
    counts = "; failed filings: 2; quarantined: 1; facts missing: 4; missing"
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


def test_fact_redated_a_to_b_to_a_reinserts_latest_accession_revision() -> None:
    """#258: a returned date must win without rewriting the old A row."""
    conn = _store()
    accession = f"{ACME}-18-000001"
    base = {
        "security_id": ACME,
        "fact_name": "shares_outstanding",
        "class_member": "",
        "value": 5_000_000.0,
        "filing_accession": accession,
        "known_at": T1,
        "source": "edgar",
        "provenance": "filing",
    }

    def ingest(as_of_date: date, at: datetime) -> int:
        row = {**base, "as_of_date": as_of_date, "ingested_at": at}
        return _add_rows(conn, "facts", [row], ingested_at=at, current=False)

    a, b = date(2018, 12, 31), date(2018, 12, 15)
    first, second, third = NOW, NOW + timedelta(days=1), NOW + timedelta(days=2)
    assert ingest(a, first) == 1
    assert ingest(b, second) == 1
    assert ingest(a, third) == 1
    assert ingest(a, third + timedelta(hours=1)) == 0

    def dates(t: datetime) -> list[date]:
        rows = facts_as_of(conn, t, [ACME])
        return rows.filter(rows["filing_accession"] == accession)["as_of_date"].to_list()

    assert dates(second - timedelta(microseconds=1)) == [a]
    assert dates(third - timedelta(microseconds=1)) == [b]
    assert dates(third) == [a]
    stored = conn.execute(
        "SELECT as_of_date, known_at FROM facts WHERE filing_accession = ? ORDER BY known_at",
        [accession],
    ).fetchall()
    assert stored == [(a, T1), (b, second), (a, third)]


def test_fact_redating_keeps_every_date_in_the_latest_accession_ingest() -> None:
    """A complete source snapshot can remove a date, then restore it later."""
    conn = _store()
    accession = f"{ACME}-18-000001"
    a, b = date(2018, 12, 31), date(2018, 12, 15)
    first, second, third = NOW, NOW + timedelta(days=1), NOW + timedelta(days=2)

    def ingest(dates: tuple[date, ...], at: datetime) -> int:
        rows = [
            {
                "security_id": ACME,
                "fact_name": "shares_outstanding",
                "as_of_date": d,
                "class_member": "",
                "value": 5_000_000.0,
                "filing_accession": accession,
                "known_at": T1,
                "ingested_at": at,
                "source": "edgar",
                "provenance": "filing",
            }
            for d in dates
        ]
        return _add_rows(conn, "facts", rows, ingested_at=at, current=False)

    assert ingest((a, b), first) == 2
    assert ingest((b,), second) == 1
    assert ingest((a, b), third) == 2
    assert ingest((a, b), third + timedelta(hours=1)) == 0

    def served(t: datetime) -> list[date]:
        rows = facts_as_of(conn, t, [ACME])
        return sorted(rows.filter(rows["filing_accession"] == accession)["as_of_date"].to_list())

    assert served(second - timedelta(microseconds=1)) == [b, a]
    # The one-date source correction hides A only from the second ingest onward.
    assert served(third - timedelta(microseconds=1)) == [b]
    assert served(third) == [b, a]


def test_fact_redating_refuses_a_future_acceptance() -> None:
    """Restamping a changed accession must not hide a future filing stamp."""
    conn = _store()
    base = {
        "security_id": ACME,
        "fact_name": "shares_outstanding",
        "class_member": "",
        "value": 5_000_000.0,
        "filing_accession": f"{ACME}-18-000001",
        "source": "edgar",
        "provenance": "filing",
    }
    first = {**base, "as_of_date": date(2018, 12, 31), "known_at": T1, "ingested_at": NOW}
    assert _add_rows(conn, "facts", [first], ingested_at=NOW, current=False) == 1
    future = {
        **base,
        "as_of_date": date(2018, 12, 15),
        "known_at": NOW + timedelta(days=2),
        "ingested_at": NOW + timedelta(days=1),
    }
    with pytest.raises(ValueError, match="not knowable yet"):
        _add_rows(conn, "facts", [future], ingested_at=NOW + timedelta(days=1), current=False)
    assert conn.execute("SELECT count(*) FROM facts").fetchone() == (1,)


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
