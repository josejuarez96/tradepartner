"""Incremental EDGAR equals `--rebuild-index`, row for row (T323b, #2629, #2703).

A simulated SEC (`_Sec`) serves the daily-index listings, the daily form
files and every quarter's `form.idx` over an `httpx.MockTransport`, as of
each night's clock, from the generated scenario in
`tests/fixtures/edgar/incremental/scenario.json`. Two stores read it under
the same clocks, each with its own local EDGAR cache (T319a): the
incremental store, seeded once by `--rebuild-index` and then run nightly
with `edgar.incremental_index` on; and the rebuild store, run with
`--rebuild-index` every night. The real `EdgarFilingSource` index path runs
unchanged (daily-file parsing, `form.idx` reconcile, the new-issuer walk,
the bound-index builds); `_SimSource` replaces only the per-CIK answers that
would need SEC's submissions, companyfacts and FSN payloads, keeping the
adapter's cache semantics where T320's touched set reads them.

Every store lives under `tmp_path`: an autouse guard fails a test that opens
DuckDB anywhere else.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
from collections.abc import Iterator, Mapping, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from email.utils import format_datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import httpx
import pytest

from tradepartner.adapters import edgar_source as edgar_source_module
from tradepartner.adapters.edgar_source import EdgarFilingSource, SubmissionRecord
from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    DelistingFiling,
    FactRecord,
    FilenameListing,
    FilingEvent,
    FilingHeader,
    SecFormFiling,
    StatementFactRecord,
)
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.calendar import all_sessions
from tradepartner.config import Settings
from tradepartner.ingest import IngestResult, ingest_session
from tradepartner.store.db import open_for_write
from tradepartner.store.schema import init_schema

EASTERN = ZoneInfo("America/New_York")
USER_AGENT = "TradePartner test-agent test@example.com"
SCENARIO = Path(__file__).resolve().parents[1] / "fixtures/edgar/incremental/scenario.json"
HEADER = "".join(
    line
    for line in (Path(__file__).resolve().parents[1] / "fixtures/edgar/filing_index_2024_qtr1.txt")
    .read_text()
    .splitlines(keepends=True)
    if "edgar/data/" not in line
)


@pytest.fixture(autouse=True, scope="module")
def _store_under_tmp(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """Fail any DuckDB file opened outside pytest's temp root (T323's guard).

    Module-scoped, so it is in place before the shared 100-night run starts."""
    root = tmp_path_factory.getbasetemp().resolve()
    connect = duckdb.connect

    def guarded(database: Any = ":memory:", *args: Any, **kwargs: Any) -> Any:
        name = str(database)
        in_memory = name == "" or name.startswith(":memory:")
        if not in_memory and not Path(name).resolve().is_relative_to(root):
            raise AssertionError(f"test opened a store outside tmp_path: {name}")
        return connect(database, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(duckdb, "connect", guarded)
        yield


# --- the simulated SEC ---------------------------------------------------------


def _eastern(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), EASTERN).astimezone(UTC)


def _quarter(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3 + 1


def _quarter_end(quarter: tuple[int, int]) -> date:
    year, number = quarter
    return date(year + number // 4, number % 4 * 3 + 1, 1) - timedelta(days=1)


@dataclass
class _Filing:
    """One accession as SEC lists it, with the scenario's later edits."""

    cik: str
    name: str
    form: str
    accession: str
    accepted: datetime
    filed_on: date
    #: The daily file that first lists it; None: only a quarter's `form.idx`.
    listed: date | None
    #: When SEC's quarterly walk first carries a reconcile-only row.
    appears: datetime | None = None
    #: Further CIKs the same accession is listed under (co-registrant, exchange).
    also: tuple[str, ...] = ()
    #: When the submissions payload first stamps it (pending before).
    stamp_from: datetime | None = None
    #: Daily re-listings: (daily file day, filed_on it carries).
    relist: tuple[tuple[date, date], ...] = ()
    #: Corrections only `form.idx` carries: (from, filed_on).
    quarterly: tuple[tuple[datetime, date], ...] = ()
    ticker: str | None = None
    exchange: str | None = None
    shares: float | None = None
    #: Days companyfacts trails the acceptance before it carries this filing.
    facts_lag_days: int = 0
    #: The FSN period that first carries this accession's cover shares.
    fsn_period: str | None = None

    def stamped(self, now: datetime) -> bool:
        return self.accepted <= now and (self.stamp_from or self.accepted) <= now

    def ciks(self) -> tuple[str, ...]:
        return (self.cik, *self.also)

    def filed_as_of(self, at: datetime) -> date:
        """The filed_on SEC's quarterly walk shows at `at`."""
        dated = [(_eastern(day, 22), filed) for day, filed in self.relist]
        dated.extend(self.quarterly)
        current = self.filed_on
        for when, filed in sorted(dated):
            if when <= at:
                current = filed
        return current


def _line(form: str, name: str, cik: str, filed: date, accession: str) -> str:
    return (
        f"{form:<17}{name:<62}{int(cik):<12}{filed:%Y%m%d}    "
        f"edgar/data/{int(cik)}/{accession}.txt\n"
    )


def _quarterly_line(form: str, name: str, cik: str, filed: date, accession: str) -> str:
    path = f"edgar/data/{int(cik)}/{accession}.txt"
    return f"{form:<17}{name:<62}{int(cik):<12}{filed.isoformat():<12}{path}\n"


@dataclass
class _Sec:
    """SEC as of a clock: daily files, listings, `form.idx`, snapshot, FSN.

    `form.idx` of a quarter is regenerated every night until two days after
    the quarter ends, then only on the scenario's `regens`; an open quarter's
    file carries a correction a night after the daily file does. Its ETag
    names the quarter and the regeneration, so an unchanged file is a 304.
    """

    filings: list[_Filing]
    issuers: dict[str, tuple[str, str, str]]  # cik -> (name, ticker, exchange)
    snapshot_from: dict[str, date] = field(default_factory=dict)  # cik -> first day listed
    snapshot_until: dict[str, date] = field(default_factory=dict)  # cik -> last day listed
    reposts: dict[date, datetime] = field(default_factory=dict)  # daily file -> re-posted at
    regens: dict[tuple[int, int], tuple[datetime, ...]] = field(default_factory=dict)
    fsn: dict[str, datetime] = field(default_factory=dict)  # period -> published at
    failures: dict[str, tuple[datetime, datetime]] = field(default_factory=dict)
    settle_days: int = 3
    now: datetime = datetime(2000, 1, 1, tzinfo=UTC)

    def __post_init__(self) -> None:
        self._accessions = {f.accession: f for f in self.filings}
        self._by_cik: dict[str, list[_Filing]] = {}
        self._by_day: dict[date, list[_Filing]] = {}
        self._by_quarter: dict[tuple[int, int], list[_Filing]] = {}
        for f in self.filings:
            for cik in f.ciks():
                self._by_cik.setdefault(cik, []).append(f)
            for day in (f.listed, *(d for d, _ in f.relist)):
                if day is not None:
                    self._by_day.setdefault(day, []).append(f)
            dates = {f.filed_on, *(d for _, d in f.relist), *(d for _, d in f.quarterly)}
            for quarter in {_quarter(d) for d in dates}:
                self._by_quarter.setdefault(quarter, []).append(f)

    def filing(self, accession: str) -> _Filing:
        return self._accessions[accession]

    def of_cik(self, cik: str) -> list[_Filing]:
        return self._by_cik.get(cik, [])

    def of_day(self, day: date) -> list[_Filing]:
        return self._by_day.get(day, [])

    def of_quarter(self, quarter: tuple[int, int]) -> list[_Filing]:
        return self._by_quarter.get(quarter, [])

    # daily files
    def _daily_rows(self, day: date) -> list[tuple[str, str, str, date, str]]:
        rows: list[tuple[str, str, str, date, str]] = []
        for f in self.of_day(day):
            for cik in f.ciks():
                if f.listed == day:
                    rows.append((f.form, f.name, cik, f.filed_on, f.accession))
                rows.extend(
                    (f.form, f.name, cik, filed, f.accession) for d, filed in f.relist if d == day
                )
        return sorted(rows, key=lambda r: (r[0], r[2], r[4]))

    def daily_days(self, quarter: tuple[int, int]) -> list[date]:
        """Every session's file, an empty one too, once posted."""
        return [d for d in all_sessions() if _quarter(d) == quarter and self._posted(d) <= self.now]

    def _posted(self, day: date) -> datetime:
        repost = self.reposts.get(day)
        first = _eastern(day, 22, 5)
        return repost if repost is not None and repost <= self.now else first

    def daily_body(self, day: date) -> bytes:
        text = HEADER + "".join(_line(*row) for row in self._daily_rows(day))
        return text.encode()

    def daily_listing(self, quarter: tuple[int, int]) -> dict[str, Any]:
        items = []
        for day in self.daily_days(quarter):
            stamp = self._posted(day).astimezone(EASTERN)
            items.append(
                {
                    "name": f"form.{day:%Y%m%d}.idx",
                    "last-modified": stamp.strftime("%m/%d/%Y %I:%M:%S %p"),
                    "size": f"{len(self.daily_body(day))} B",
                    "type": "file",
                }
            )
        return {"directory": {"item": items}}

    # the quarterly walk
    def _final(self, quarter: tuple[int, int]) -> datetime:
        return _eastern(_quarter_end(quarter) + timedelta(days=self.settle_days - 1), 22)

    def generated(self, quarter: tuple[int, int]) -> datetime:
        """When SEC last regenerated the quarter's `form.idx` before `now`."""
        final = self._final(quarter)
        if self.now < final:  # regenerated every night with the daily file
            at = _eastern(self.now.astimezone(EASTERN).date(), 22)
            return at if at <= self.now else at - timedelta(days=1)
        return max(t for t in (final, *self.regens.get(quarter, ())) if t <= self.now)

    def form_idx(self, quarter: tuple[int, int]) -> bytes:
        at = self.generated(quarter)
        dated_at = at if at >= self._final(quarter) else at - timedelta(days=1)
        lines: list[tuple[str, str, str, date, str]] = []
        for f in self.of_quarter(quarter):
            seen = f.appears if f.listed is None else _eastern(f.listed, 22)
            if seen is None or seen > at:
                continue
            filed = f.filed_as_of(dated_at)
            if _quarter(filed) == quarter:
                lines.extend((f.form, f.name, cik, filed, f.accession) for cik in f.ciks())
        lines.sort(key=lambda r: (r[0], r[2], r[4]))
        return (HEADER + "".join(_quarterly_line(*row) for row in lines)).encode()

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        assert request.headers["User-Agent"] == USER_AGENT
        prefix = "https://www.sec.gov/Archives/edgar/"
        if url.startswith(prefix + "daily-index/"):
            parts = url.removeprefix(prefix + "daily-index/").split("/")
            year, number = int(parts[0]), int(parts[1].removeprefix("QTR"))
            if parts[2] == "index.json":
                return httpx.Response(200, json=self.daily_listing((year, number)))
            day = date.fromisoformat(parts[2].removeprefix("form.").removesuffix(".idx"))
            if self._posted(day) > self.now:
                return httpx.Response(404)
            return httpx.Response(200, content=self.daily_body(day))
        if url.startswith(prefix + "full-index/"):
            parts = url.removeprefix(prefix + "full-index/").split("/")
            quarter = int(parts[0]), int(parts[1].removeprefix("QTR"))
            at = self.generated(quarter)
            etag = f'"{quarter[0]}Q{quarter[1]}-{at:%Y%m%d%H%M}"'
            if request.headers.get("If-None-Match") == etag:
                return httpx.Response(304, headers={"ETag": etag})
            headers = {"ETag": etag, "Last-Modified": format_datetime(at, usegmt=True)}
            return httpx.Response(200, content=self.form_idx(quarter), headers=headers)
        if url.endswith("financial-statement-notes-data-sets"):
            periods = sorted(p for p, at in self.fsn.items() if at <= self.now)
            links = "".join(
                f'<a href="/files/dera/data/financial-statement-notes-data-sets/{p}_notes.zip">'
                f"{p}</a>"
                for p in periods
            )
            return httpx.Response(200, text=f"<html>{links}</html>")
        pytest.fail(f"unrouted EDGAR request: {url}")

    # per-CIK answers
    def stamped(self, cik: str) -> dict[str, SubmissionRecord]:
        return {
            f.accession: SubmissionRecord(f.accession, f.form, "doc.htm", True, f.accepted)
            for f in self.of_cik(cik)
            if f.stamped(self.now)
        }

    def failing(self, accession: str) -> bool:
        window = self.failures.get(accession)
        return window is not None and window[0] <= self.now < window[1]


class _SimSource(EdgarFilingSource):
    """The real adapter over `_Sec`, with per-CIK payload answers simulated.

    The index path (`incremental_index`, `form.idx`, the walk, the stored
    index binding, `_read_stamps` and the stamps cache) is the adapter's own.
    The submissions payload comes from the scenario's clock; companyfacts keep the adapter's
    per-CIK cache file keyed by the latest cover-form accession, written only
    once the payload carries it; FSN cover shares appear with their period.
    """

    def __init__(self, settings: Settings, sec: _Sec, **kwargs: Any) -> None:
        client = httpx.Client(transport=httpx.MockTransport(sec.handle))
        super().__init__(settings, client=client, clock=lambda: sec.now, **kwargs)
        self._sec = sec

    # stamping: the adapter's own stamps cache, unstampable rule and SGML-free
    # path (`stamp_sgml_check` off) run; only the submissions payload is simulated.
    def _fetch_submissions(self, cik: str, wanted: set[str]) -> Any:
        return edgar_source_module._Submissions(self._sec.stamped(cik), [], self._sec.now)

    # payloads
    def companies_snapshot(self) -> list[CompanySnapshotEntry]:
        today = self._sec.now.astimezone(EASTERN).date()
        entries = [
            CompanySnapshotEntry(cik, name, ticker, exchange, self._sec.now)
            for cik, (name, ticker, exchange) in sorted(self._sec.issuers.items())
            if self._sec.snapshot_from.get(cik, date.min)
            <= today
            <= self._sec.snapshot_until.get(cik, date.max)
        ]
        entries.append(
            CompanySnapshotEntry("0000884394", "SPDR S&P 500", "SPY", "NYSE_ARCA", self._sec.now)
        )
        return entries

    def _ensure_fsn(self) -> None:
        return None

    def _cached_fsn_periods(self) -> list[str]:
        return sorted(p for p, at in self._sec.fsn.items() if at <= self._sec.now)

    def _load_fsn_manifest(self, period: str) -> dict[str, Any] | None:
        if period not in self._cached_fsn_periods():
            return None
        return {
            "accessions_extracted": sorted(
                f.accession for f in self._sec.filings if f.fsn_period == period
            )
        }

    def _failure_store(self) -> dict[str, dict[str, Any]]:
        return {
            accession: {
                "error_class": "HTTPStatusError",
                "base_form": self._sec.filing(accession).form,
                "message": "503 from the simulated SEC",
                "count": 1,
                "accepted": False,
            }
            for accession in self._sec.failures
            if self._sec.failing(accession)
        }

    def _unread_runs(self) -> dict[str, int]:
        return {}

    def _records(self, cik: str) -> list[tuple[_Filing, SubmissionRecord]]:
        stamps = self._read_stamps(cik)
        filings = {f.accession: f for f in self._sec.of_cik(cik)}
        out = []
        for accession, record in sorted(stamps.items()):
            if record.accepted_at is None:
                continue
            if self._sec.failing(accession):  # skipped this run, as T11h's policy does
                form = filings[accession].form
                self._record_failure(accession, "HTTPStatusError", form, "503")
                continue
            out.append((filings[accession], record))
        return out

    def cover_pages(self, cik: str) -> list[CoverPage]:
        return [
            CoverPage(
                cik,
                f.accession,
                r.accepted_at,
                (CoverListing("Common Stock", f.ticker, f.exchange),),
            )
            for f, r in self._records(cik)
            if f.ticker is not None and f.exchange is not None and r.accepted_at is not None
        ]

    def filing_headers(self, cik: str, forms: Sequence[str]) -> list[FilingHeader]:
        return [
            FilingHeader(cik, f.accession, f.form, 3571, r.accepted_at)
            for f, r in self._records(cik)
            if f.form in forms and r.accepted_at is not None
        ]

    def delistings(self, since: datetime | None = None) -> list[DelistingFiling]:
        out = []
        for f in self._sec.filings:  # the scenario's handful, never the memory history
            if f.form not in ("25", "25-NSE") or not f.stamped(self._sec.now):
                continue
            if f.exchange is None or (since is not None and f.accepted < since):
                continue
            out.append(
                DelistingFiling(f.cik, f.form, "Common Stock", f.exchange, f.accession, f.accepted)
            )
        return sorted(out, key=lambda d: (d.accepted_at, d.accession))

    def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
        records = self._records(cik)
        cover_forms = set(self._settings.edgar.cover_page_forms)
        latest = self._facts_cache_key(dict((f.accession, r) for f, r in records), cover_forms)
        if latest is None:
            return []
        path = self._cache / "facts" / f"v{edgar_source_module.PARSER_VERSION}" / f"{cik}.json"
        now = self._sec.now
        cached = None
        if path.is_file():
            data = json.loads(path.read_text())
            if data["key"] == latest:
                cached = data["facts"]
        if cached is None:
            carried = [
                f
                for f, _ in records
                if f.shares is not None
                and f.fsn_period is None
                and f.accepted + timedelta(days=f.facts_lag_days) <= now
            ]
            cached = [[f.accession, f.filed_on.isoformat(), f.shares] for f in carried]
            # The payload is complete once it carries the latest cover-form filing,
            # or that filing has no companyfacts row to wait for (FSN-only shares).
            if any(f.accession == latest for f in carried) or not any(
                f.accession == latest and f.shares is not None and f.fsn_period is None
                for f, _ in records
            ):
                # The one payload read fills both caches (T77a), as the adapter's does.
                for target, payload in (
                    (
                        path,
                        {"version": edgar_source_module.PARSER_VERSION, "key": latest},
                    ),
                    (
                        self._statement_cache_path(cik),
                        {
                            "version": edgar_source_module.STATEMENT_VERSION,
                            "key": self._statement_key(latest),
                        },
                    ),
                ):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps({**payload, "cik": cik, "facts": cached}))
        stamps = {f.accession: r.accepted_at for f, r in records if r.accepted_at is not None}
        out = [
            FactRecord(
                cik,
                "EntityCommonStockSharesOutstanding",
                date.fromisoformat(as_of),
                "",
                value,
                accession,
                stamps[accession],
            )
            for accession, as_of, value in cached
            if accession in stamps
        ]
        periods = set(self._cached_fsn_periods())
        out.extend(
            FactRecord(
                cik,
                "EntityCommonStockSharesOutstanding",
                f.filed_on,
                "",
                f.shares,
                f.accession,
                r.accepted_at,
            )
            for f, r in records
            if f.fsn_period in periods and f.shares is not None and r.accepted_at is not None
        )
        return sorted(out, key=lambda x: (x.accepted_at, x.accession))

    def statement_facts(self, cik: str) -> list[StatementFactRecord]:
        return []

    def filing_events(self, cik: str) -> list[FilingEvent]:
        return []

    def filename_listings(self, cik: str) -> list[FilenameListing]:
        return []

    def sec_forms_13d(self, subjects: frozenset[str] | None = None) -> list[SecFormFiling]:
        return []


class _NoPrices(PriceSource):
    """The proof runs `--source edgar` only; a price request is a test bug."""

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        raise AssertionError("the EDGAR proof asks no prices")

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        raise AssertionError("the EDGAR proof asks no prices")


# --- the scenario --------------------------------------------------------------


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text).astimezone(UTC)


def _quarter_key(text: str) -> tuple[int, int]:
    year, number = text.split("Q")
    return int(year), int(number)


@dataclass(frozen=True)
class _Scenario:
    sec: _Sec
    first_year: int
    nights: tuple[date, ...]
    key_off: tuple[date, date]
    chain_night: date
    chain_entry: str


def _load(payload: Mapping[str, Any]) -> _Scenario:
    filings = [
        _Filing(
            cik=f["cik"],
            name=f["name"],
            form=f["form"],
            accession=f["accession"],
            accepted=_instant(f["accepted"]),
            filed_on=date.fromisoformat(f["filed_on"]),
            listed=None if f["listed"] is None else date.fromisoformat(f["listed"]),
            appears=_instant(f["appears"]) if "appears" in f else None,
            also=tuple(f.get("also", ())),
            stamp_from=_instant(f["stamp_from"]) if "stamp_from" in f else None,
            relist=tuple(
                (date.fromisoformat(day), date.fromisoformat(filed))
                for day, filed in f.get("relist", ())
            ),
            quarterly=tuple(
                (_instant(at), date.fromisoformat(filed)) for at, filed in f.get("quarterly", ())
            ),
            ticker=f.get("ticker"),
            exchange=f.get("exchange"),
            shares=f.get("shares"),
            facts_lag_days=f.get("facts_lag_days", 0),
            fsn_period=f.get("fsn_period"),
        )
        for f in payload["filings"]
    ]
    sec = _Sec(
        filings,
        {
            cik: (name, ticker, exchange)
            for cik, (name, ticker, exchange) in payload["issuers"].items()
        },
        snapshot_from={k: date.fromisoformat(v) for k, v in payload["snapshot_from"].items()},
        snapshot_until={k: date.fromisoformat(v) for k, v in payload["snapshot_until"].items()},
        reposts={date.fromisoformat(k): _instant(v) for k, v in payload["reposts"].items()},
        regens={_quarter_key(k): tuple(map(_instant, v)) for k, v in payload["regens"].items()},
        fsn={k: _instant(v) for k, v in payload["fsn"].items()},
        failures={k: (_instant(a), _instant(b)) for k, (a, b) in payload["failures"].items()},
    )
    seed = date.fromisoformat(payload["seed_night"])
    off = tuple(date.fromisoformat(day) for day in payload["key_off"])
    return _Scenario(
        sec,
        payload["first_year"],
        tuple(seed + timedelta(days=n) for n in range(payload["nights"])),
        (off[0], off[1]),
        date.fromisoformat(payload["chain_edit"]["night"]),
        payload["chain_edit"]["entry"],
    )


def _night(day: date) -> datetime:
    """The nightly run's clock: 23:30 US/Eastern, after the day's daily file."""
    return _eastern(day, 23, 30)


# --- the two stores ------------------------------------------------------------


def _settings(root: Path, name: str, first_year: int, **edgar: Any) -> Settings:
    settings = Settings(
        _env_file=None,
        sec_edgar_user_agent=USER_AGENT,
        store={"path": str(root / name / "store.duckdb"), "lock_retry_seconds": 1},
        edgar={
            "index_first_year": first_year,
            "cache_dir": str(root / name / "edgar-cache"),
            "stamp_sgml_check": False,
            "incremental_index": True,
            **edgar,
        },
    )
    # A negligible throttle past the config's cap of 10 (#1108): the transport is fake.
    fast = settings.edgar.model_copy(update={"requests_per_second": 1e6})
    settings = settings.model_copy(update={"edgar": fast})
    Path(settings.store.path).parent.mkdir(parents=True, exist_ok=True)
    with open_for_write(settings) as conn:
        init_schema(conn)
    return settings


def _with(settings: Settings, **edgar: Any) -> Settings:
    return settings.model_copy(update={"edgar": settings.edgar.model_copy(update=edgar)})


def _with_chain(settings: Settings, entry: str) -> Settings:
    master = settings.master.model_copy(update={"chains": [entry]})
    return settings.model_copy(update={"master": master})


def _ingest(settings: Settings, sec: _Sec, *, rebuild: bool) -> IngestResult:
    source = _SimSource(settings, sec)
    try:
        result = ingest_session(
            settings,
            prices=_NoPrices(),
            filings=source,
            source="edgar",
            clock=lambda: sec.now,
            rebuild_index=rebuild,
        )
    finally:
        source.close()
    assert result.ok, [(run.source, run.status, run.message) for run in result.runs]
    return result


#: Columns that name a run or the schema migration's wall clock, never a fact.
_RUN_COLUMNS = frozenset({"run_id"})


def _tables(path: str) -> dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]]:
    """Every table but the run log, row for row, without run ids."""
    with duckdb.connect(path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        names = [
            name
            for (name,) in conn.execute(
                "SELECT table_name FROM information_schema.tables ORDER BY 1"
            ).fetchall()
            if name not in ("ingestion_runs", "schema_version")
        ]
        out: dict[str, tuple[tuple[str, ...], list[tuple[Any, ...]]]] = {}
        for name in names:
            columns = tuple(
                column
                for (column,) in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = ? ORDER BY ordinal_position",
                    [name],
                ).fetchall()
                if column not in _RUN_COLUMNS
            )
            select = ", ".join(f'"{c}"' for c in columns)
            rows = conn.execute(f'SELECT {select} FROM "{name}"').fetchall()
            out[name] = columns, sorted(rows, key=repr)
        return out


def _edgar_message(path: str) -> str:
    """The night's EDGAR run message."""
    with duckdb.connect(path, read_only=True) as conn:
        row = conn.execute(
            "SELECT message FROM ingestion_runs WHERE source = 'edgar' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    return str(row[0])


def _health(settings: Settings, at: datetime) -> list[str]:
    """`tradepartner health`'s report, less the per-source run lines (their
    modes and cursors name the run type, which differs by construction)."""
    from tradepartner import cli
    from tradepartner.health import health_report

    buffer = io.StringIO()
    with duckdb.connect(settings.store.path, read_only=True) as conn, redirect_stdout(buffer):
        cli._print_report(health_report(conn, at, settings, with_coverage_reference=False))
    lines = buffer.getvalue().splitlines()
    start = lines.index("last ingest per source:")
    end = next(i for i in range(start + 1, len(lines)) if not lines[i].startswith(" "))
    kept = lines[:start] + lines[end:]
    return [line for line in kept if not line.startswith("settings: ")]


# --- known divergences ---------------------------------------------------------


def _utc(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


#: What each table's divergence is pinned by: enough columns to name the row
#: and every value a tracked issue lets differ.
PINNED_COLUMNS: dict[str, tuple[str, ...]] = {
    "filing_index": ("cik", "accession", "filed_on", "listed_on", "list_position", "known_at"),
    "edgar_index_files": ("path",),
}

#: Divergences found by this proof, each tracked by its issue: the rows only
#: the incremental store holds and the rows only the rebuild store holds, in
#: `PINNED_COLUMNS`. A night's difference passes only when every differing row
#: is one of these; and each issue's rows must still occur (strict), so a fix
#: has to remove its entry.
KNOWN_DIVERGENCES: dict[
    tuple[int, str], tuple[frozenset[tuple[Any, ...]], frozenset[tuple[Any, ...]]]
] = {
    # One night's daily re-listing and settled-quarter reconcile of one accession:
    # the nightly keeps the daily listed_on, the rebuild's quarter chunk the quarterly.
    (2859, "filing_index"): (
        frozenset(
            {
                (
                    "0000000105",
                    "0000000105-26-000001",
                    date(2025, 12, 31),
                    date(2026, 1, 21),
                    1,
                    _utc(date(2026, 1, 22), 4, 30),
                ),
            }
        ),
        frozenset(
            {
                (
                    "0000000105",
                    "0000000105-26-000001",
                    date(2025, 12, 31),
                    date(2026, 1, 20),
                    1,
                    _utc(date(2026, 1, 22), 4, 30),
                ),
            }
        ),
    ),
}

#: Run-message clauses a touched-only nightly counts over tonight's CIKs (#2860):
#: the securities count, and the unstamped filings count (its facts count must agree).
MESSAGE_ISSUE = 2860


def _message_clauses(message: str) -> dict[str, str]:
    """The EDGAR message by clause name (`8 securities` -> `securities: 8`),
    up to the index driver's own counts."""
    out: dict[str, str] = {}
    for clause in message.split("; index_", 1)[0].split("; "):
        head, colon, rest = clause.partition(": ")
        if not colon and head[:1].isdigit():
            count, _, name = head.partition(" ")
            out[name] = count
        else:
            out[head] = rest
    return out


def _message_diff(ours: str, theirs: str) -> tuple[bool, list[str]]:
    """Whether #2860's clauses differ, and every other differing clause."""
    left, right = _message_clauses(ours), _message_clauses(theirs)
    known = False
    odd = []
    for name in sorted(left.keys() | right.keys()):
        a, b = left.get(name), right.get(name)
        if a == b:
            continue
        unstamped_facts_agree = bool(a and b and a.split(", ")[1:] == b.split(", ")[1:])
        if name == "securities" or (name == "unstamped" and unstamped_facts_agree):
            known = True
        else:
            odd.append(f"{name}: {a!r} != {b!r}")
    return known, odd


@dataclass
class _Run:
    """The 100 nights' outcome, shared by the tests below."""

    scenario: _Scenario
    incremental: Settings
    rebuild: Settings
    diffs: list[str] = field(default_factory=list)
    known: set[int] = field(default_factory=set)
    touched: dict[date, frozenset[str]] = field(default_factory=dict)
    compared: int = 0
    #: The incremental store's first key-on night after the gap: rows it first
    #: writes then were written by the rebuild store on a gap night, so their
    #: `ingested_at` may differ (the line's "different runs").
    catch_up: datetime | None = None


def _without(columns: tuple[str, ...], row: tuple[Any, ...], name: str) -> tuple[Any, ...]:
    return tuple(v for c, v in zip(columns, row, strict=True) if c != name)


def _compare(run: _Run, night: date, incremental: Settings, rebuild: Settings) -> None:
    """Record every difference the known list does not explain."""
    left, right = _tables(incremental.store.path), _tables(rebuild.store.path)
    for name in sorted(left.keys() | right.keys()):
        columns, ours = left[name]
        _, theirs = right[name]
        if ours == theirs:
            continue
        only_ours = [r for r in ours if r not in theirs]
        only_theirs = [r for r in theirs if r not in ours]
        if run.catch_up is not None and "ingested_at" in columns:
            at = columns.index("ingested_at")

            def key(row: tuple[Any, ...], columns: tuple[str, ...] = columns) -> tuple[Any, ...]:
                return _without(columns, row, "ingested_at")

            late = {key(r) for r in only_ours if r[at] == run.catch_up}
            matched = late & {key(r) for r in only_theirs}
            only_ours = [r for r in only_ours if not (r[at] == run.catch_up and key(r) in matched)]
            only_theirs = [r for r in only_theirs if key(r) not in matched]
        if not only_ours and not only_theirs:
            continue
        pinned = PINNED_COLUMNS.get(name)
        if pinned is not None:
            index = [columns.index(c) for c in pinned]
            ours_pins = {tuple(r[i] for i in index) for r in only_ours}
            theirs_pins = {tuple(r[i] for i in index) for r in only_theirs}
            keys = {
                key
                for key, (mine, other) in KNOWN_DIVERGENCES.items()
                if key[1] == name and (ours_pins & mine or theirs_pins & other)
            }
            covered_ours = {pin for key in keys for pin in KNOWN_DIVERGENCES[key][0]}
            covered_theirs = {pin for key in keys for pin in KNOWN_DIVERGENCES[key][1]}
            if ours_pins <= covered_ours and theirs_pins <= covered_theirs:
                run.known.update(issue for issue, _ in keys)
                continue
        run.diffs.append(
            f"{night}: {name}\n  incremental only: {only_ours}\n  rebuild only: {only_theirs}"
        )
    ours_message = _edgar_message(incremental.store.path)
    known, odd = _message_diff(ours_message, _edgar_message(rebuild.store.path))
    if known:
        run.known.add(MESSAGE_ISSUE)
    if odd:
        run.diffs.append(f"{night}: edgar run message: {odd}")
    # T321's rotation check rebuilt a share of the untouched issuers and found no drift.
    if "incremental_drift: 0" not in ours_message:
        run.diffs.append(f"{night}: rotation drift: {ours_message.rsplit('; ', 1)[-1]}")
    ours_health, theirs_health = (
        _health(incremental, run.scenario.sec.now),
        _health(rebuild, run.scenario.sec.now),
    )
    if ours_health != theirs_health:
        run.diffs.append(f"{night}: health\n  {ours_health}\n  {theirs_health}")


@pytest.fixture(scope="module")
def hundred_nights(tmp_path_factory: pytest.TempPathFactory) -> Iterator[_Run]:
    """Seed both stores on the first night, then run 99 more and compare
    them after each night outside the incremental store's key-off gap."""
    from tradepartner import ingest as ingest_module
    from tradepartner.store import raw_archive

    scenario = _load(json.loads(SCENARIO.read_text()))
    sec = scenario.sec
    root = tmp_path_factory.mktemp("hundred-nights")
    run = _Run(
        scenario,
        _settings(root, "incremental", scenario.first_year),
        _settings(root, "rebuild", scenario.first_year),
    )
    touched = ingest_module._touched_for_index

    def record(*args: Any, **kwargs: Any) -> Any:
        result = touched(*args, **kwargs)
        run.touched[sec.now.astimezone(EASTERN).date()] = result[0]
        return result

    def never(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a nightly read reached the raw archive")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ingest_module, "_touched_for_index", record)
        # The archive stays outside the nightly read path (#2714 item 4).
        for reader in ("read_body", "read_manifest"):
            patch.setattr(raw_archive, reader, never)
        first, *rest = scenario.nights
        sec.now = _night(first)
        _ingest(run.incremental, sec, rebuild=True)
        _ingest(run.rebuild, sec, rebuild=True)
        for night in rest:
            sec.now = _night(night)
            incremental, rebuild = run.incremental, run.rebuild
            if night >= scenario.chain_night:
                incremental = _with_chain(incremental, scenario.chain_entry)
                rebuild = _with_chain(rebuild, scenario.chain_entry)
            gap = scenario.key_off[0] <= night <= scenario.key_off[1]
            _ingest(_with(incremental, incremental_index=not gap), sec, rebuild=False)
            _ingest(rebuild, sec, rebuild=True)
            if gap:
                continue
            if run.catch_up is None and night > scenario.key_off[1]:
                run.catch_up = sec.now
            run.compared += 1
            _compare(run, night, incremental, rebuild)
        yield run


def _rows(settings: Settings, sql: str, params: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        return conn.execute(sql, list(params)).fetchall()


def test_incremental_store_equals_rebuild_store_every_night(hundred_nights: _Run) -> None:
    """Equality: every table row for row, the EDGAR run message and `health`,
    on every night outside the key-off gap, less the divergences each tracked
    by its issue; each of those must still occur (strict)."""
    assert hundred_nights.compared == 100 - 1 - 9  # the seed night and the gap
    assert hundred_nights.diffs == []
    assert hundred_nights.known == {issue for issue, _ in KNOWN_DIVERGENCES} | {MESSAGE_ISSUE}


def _index(settings: Settings, accession: str) -> list[tuple[Any, ...]]:
    return _rows(
        settings,
        "SELECT cik, filed_on, listed_on, known_at, ingested_at FROM filing_index "
        "WHERE accession = ? ORDER BY known_at, cik",
        [accession],
    )


def test_the_nights_reach_every_case_the_line_lists(hundred_nights: _Run) -> None:
    """The scenario does what its `case` notes say, so the equality covers them."""
    run, store = hundred_nights, hundred_nights.rebuild

    def night(day: date) -> datetime:
        return _night(day)

    def at(day: date, hour: int = 17, minute: int = 0) -> datetime:
        return _eastern(day, hour, minute)

    # New issuers, one with earlier rows: the S-1's night walks back to 2025Q2's Form D.
    assert _index(store, "0000000103-25-000001") == [
        (
            "0000000103",
            date(2025, 5, 12),
            date(2025, 7, 1),
            at(date(2025, 5, 12)),
            night(date(2025, 12, 10)),
        )
    ]
    # A row pending, then stamped: written the night its stamp arrived.
    assert _index(store, "0000000102-25-000002")[0][4] == night(date(2025, 12, 11))
    # A reconcile insert: only the settled quarter's regenerated form.idx lists it.
    assert _index(store, "0000000101-25-000004") == [
        (
            "0000000101",
            date(2025, 12, 22),
            date(2026, 1, 13),
            at(date(2025, 12, 22)),
            night(date(2026, 1, 14)),
        )
    ]
    # A correction found by a settled quarter's reconcile.
    assert [r[1:3] for r in _index(store, "0000000106-25-000002")] == [
        (date(2025, 11, 12), date(2025, 11, 12)),
        (date(2025, 11, 11), date(2026, 1, 13)),
    ]
    # A settled quarter's form.idx that predates a daily re-listing: the later
    # regenerations carry the corrected date, the older one never undoes it.
    assert [r[1:3] for r in _index(store, "0000000101-25-000003")] == [
        (date(2025, 12, 3), date(2025, 12, 3)),
        (date(2025, 12, 2), date(2026, 1, 12)),
    ]
    # A corrected filed_on re-listing; the first date's file re-posted on 01-13 adds none.
    assert [r[1:] for r in _index(store, "0000000102-26-000001")] == [
        (date(2026, 1, 8), date(2026, 1, 8), at(date(2026, 1, 8)), night(date(2026, 1, 8))),
        (date(2026, 1, 6), date(2026, 1, 9), night(date(2026, 1, 9)), night(date(2026, 1, 9))),
    ]
    # A new issuer with a corrected accession in an unsettled quarter: both rows
    # on the 10-K's night.
    assert [(*r[1:3], r[4]) for r in _index(store, "0000000104-26-000001")] == [
        (date(2026, 1, 20), date(2026, 1, 20), night(date(2026, 2, 2))),
        (date(2026, 1, 16), date(2026, 1, 23), night(date(2026, 2, 2))),
    ]
    # A correction that moves filed_on across a quarter end.
    assert _index(store, "0000000105-26-000001")[-1][1] == date(2025, 12, 31)
    # A filing listed inside the key-off gap in the open quarter: the first key-on
    # night catches it up from its daily file, the same row on a later run.
    gap_row = _index(store, "0000000102-26-000003")
    assert [r[:4] for r in _index(run.incremental, "0000000102-26-000003")] == [
        r[:4] for r in gap_row
    ]
    assert gap_row[0][4] == night(date(2026, 1, 5))
    assert _index(run.incremental, "0000000102-26-000003")[0][4] == night(date(2026, 1, 8))
    # A stamp that arrives only after its quarter settled (#2865): inside
    # `edgar.unstampable_retry_days` the accession is asked again, so both stores
    # index it on the first night SEC serves the stamp, known at its acceptance
    # like every pending-then-stamped row, ingested that night and never before.
    for settings in (store, run.incremental):
        assert _index(settings, "0000000106-25-000003") == [
            (
                "0000000106",
                date(2025, 12, 23),
                date(2025, 12, 23),
                at(date(2025, 12, 23)),
                night(date(2026, 1, 12)),
            )
        ]
        assert (
            _rows(
                settings,
                "SELECT accession FROM filing_index_pending "
                "WHERE accession = '0000000106-25-000003'",
            )
            == []
        )
    # The 25-NSEs: the subject keeps a paired one; an exchange-only one stays the exchange's.
    assert [r[0] for r in _index(store, "0000876661-26-000001")] == ["0000000105"]
    assert [r[0] for r in _index(store, "0000876661-26-000002")] == ["0000876661"]
    # A 13D row filed by the filer.
    assert [r[0] for r in _index(store, "0000950123-25-000001")] == ["0000950123"]
    # The master.chains edit derives the chain.
    assert _rows(store, "SELECT predecessor_id, successor_id FROM successor_chains") == [
        ("0000000107", "0000000108")
    ]
    # Companyfacts lagging nine days: the fact arrives the night the payload carries it.
    assert _rows(
        store, "SELECT ingested_at FROM facts WHERE filing_accession = '0000000101-26-000001'"
    ) == [(night(date(2026, 2, 19)),)]
    # An FSN period with a co-registrant: both CIKs' shares arrive with the period.
    assert _rows(
        store,
        "SELECT substr(security_id, 1, 10), ingested_at FROM facts "
        "WHERE filing_accession = '0000000109-26-000001' ORDER BY 1",
    ) == [("0000000109", night(date(2026, 2, 20))), ("0000000110", night(date(2026, 2, 20)))]
    # The touched set (T320) is what the nightly rebuilt: narrow on a quiet
    # night, and naming each trigger's CIKs on its night.
    touched = run.touched
    assert sum(1 for ciks in touched.values() if not ciks) >= 30
    assert touched[date(2025, 11, 29)] == frozenset()
    expected = {
        date(2026, 1, 8): {"0000000101", "0000000102", "0000000103", "0000000106"},  # Q4 settled
        date(2026, 1, 21): {"0000000106"},  # the snapshot entry disappeared
        date(2026, 1, 29): {"0000000102"},  # the failure passed on retry
        date(2026, 2, 15): {"0000000101"},  # companyfacts still trails
        date(2026, 2, 16): {"0000000107", "0000000108"},  # the master.chains edit
        date(2026, 2, 20): {"0000000109", "0000000110"},  # the new FSN period
    }
    for day, ciks in expected.items():
        assert ciks <= touched[day], day
    assert "0000000101" not in touched[date(2026, 2, 22)]


def test_an_as_of_read_before_a_revision_returns_the_first_known_filed_on(
    hundred_nights: _Run,
) -> None:
    """Revisions (#2703): just before each revision's `known_at` both stores
    read the earlier `filed_on`, from it the revised one."""
    from tradepartner.store.filing_index import by_cik

    for settings in (hundred_nights.incremental, hundred_nights.rebuild):
        revised = _rows(
            settings,
            "SELECT cik, accession FROM filing_index GROUP BY cik, accession "
            "HAVING count(*) > 1 ORDER BY 1, 2",
        )
        assert len(revised) >= 6
        with duckdb.connect(settings.store.path, read_only=True) as conn:
            for cik, accession in revised:
                history = conn.execute(
                    "SELECT filed_on, known_at FROM filing_index "
                    "WHERE cik = ? AND accession = ? ORDER BY known_at",
                    [cik, accession],
                ).fetchall()
                for (earlier, _), (filed_on, stored_at) in pairwise(history):
                    known_at = stored_at.astimezone(UTC)

                    def read(t: datetime, cik: str = cik, accession: str = accession) -> list[date]:
                        return [
                            r.filed_on for r in by_cik(conn, cik, t) if r.accession == accession
                        ]

                    assert read(known_at - timedelta(microseconds=1)) == [earlier]
                    assert read(known_at) == [filed_on]


def test_rebuild_index_over_a_copy_of_the_incremental_store_writes_no_row(
    hundred_nights: _Run, tmp_path: Path
) -> None:
    """The stored revisions are the rebuild's source: over the incremental
    store, on its last night, it writes no row."""
    run = hundred_nights
    copy = tmp_path / "copy"
    shutil.copytree(Path(run.incremental.store.path).parent, copy)
    settings = _with_chain(run.incremental, run.scenario.chain_entry)
    settings = settings.model_copy(
        update={
            "store": settings.store.model_copy(update={"path": str(copy / "store.duckdb")}),
            "edgar": settings.edgar.model_copy(update={"cache_dir": str(copy / "edgar-cache")}),
        }
    )
    before = _tables(settings.store.path)
    _ingest(settings, run.scenario.sec, rebuild=True)
    assert _tables(settings.store.path) == before


def test_master_retract_proposes_the_same_rows_on_both_stores(hundred_nights: _Run) -> None:
    from tradepartner.retract import master_retract

    run = hundred_nights
    sec = run.scenario.sec
    proposals = []
    for store in (run.incremental, run.rebuild):
        settings = _with_chain(store, run.scenario.chain_entry)
        source = _SimSource(settings, sec, prefer_stored_index=True)
        try:
            result = master_retract(settings, filings=source, clock=lambda: sec.now, dry_run=True)
        finally:
            source.close()
        proposals.append((result.rows, result.digest, result.summary(), tuple(result.lines())))
    assert proposals[0] == proposals[1]


def test_a_correction_inside_the_key_off_gap_is_dated_no_earlier_incrementally(
    tmp_path: Path,
) -> None:
    """The 100 nights hold no correction inside the key-off gap, since there
    the stores date it on different runs. Here one is: the incremental store's
    revision is known no earlier than the rebuild store's, and both keep the
    same first-known row."""
    cik = "0000000201"
    accession = f"{cik}-26-000001"
    filing = _Filing(
        cik,
        "Kappa Corp",
        "10-K",
        accession,
        _eastern(date(2026, 3, 2), 17),
        date(2026, 3, 2),
        date(2026, 3, 2),
        relist=((date(2026, 3, 4), date(2026, 2, 27)),),
        ticker="KAP",
        exchange="NYSE",
        shares=100_000,
    )
    sec = _Sec(
        [filing],
        {cik: ("Kappa Corp", "KAP", "NYSE")},
        fsn={"2026_01": _instant("2026-02-20T12:00:00-05:00")},
    )
    incremental = _settings(tmp_path, "incremental", 2026)
    rebuild = _settings(tmp_path, "rebuild", 2026)
    sec.now = _night(date(2026, 3, 2))
    _ingest(incremental, sec, rebuild=True)
    _ingest(rebuild, sec, rebuild=True)
    for day, key_on in ((3, False), (4, False), (5, False), (6, True)):
        sec.now = _night(date(2026, 3, day))
        _ingest(_with(incremental, incremental_index=key_on), sec, rebuild=False)
        _ingest(rebuild, sec, rebuild=True)
    ours, theirs = _index(incremental, accession), _index(rebuild, accession)
    assert ours[0] == theirs[0]
    assert [r[1] for r in ours] == [r[1] for r in theirs] == [date(2026, 3, 2), date(2026, 2, 27)]
    assert theirs[1][3] == _night(date(2026, 3, 4))
    assert ours[1][3] == _night(date(2026, 3, 6))
    assert ours[1][3] >= theirs[1][3]


# --- the raw archive (plan T347, #2714) -------------------------------------------

LAMBDA = "0000000301"
FIRST = f"{LAMBDA}-26-000001"  # a 10-K listed 03-02
SECOND = f"{LAMBDA}-26-000002"  # a 10-Q listed 03-03, whose file SEC re-posts on 03-04
REPOST = date(2026, 3, 3)


@dataclass
class _ArchiveCase:
    sec: _Sec
    settings: Settings
    root: Path

    def night(self, day: int, *, rebuild: bool = False) -> None:
        """Ingest March `day`, 2026. On the 4th SEC re-posts the 3rd's file with
        the 10-Q's corrected `filed_on`."""
        self.sec.now = _night(date(2026, 3, day))
        if day == 4:
            self.sec.filing(SECOND).filed_on = date(2026, 3, 1)
        _ingest(self.settings, self.sec, rebuild=rebuild)

    def bodies(self) -> set[str]:
        return {path.name for path in (self.root / "raw").rglob("*") if path.is_file()}

    def runs(self) -> list[tuple[Any, ...]]:
        return _rows(
            self.settings,
            "SELECT run_id, mode, started_at, message FROM ingestion_runs "
            "WHERE source = 'edgar_index' ORDER BY rowid",
        )

    def manifests(self, runs: Sequence[tuple[Any, ...]]) -> list[dict[str, Any]]:
        from tradepartner.adapters.daily_index import parse_index_manifest
        from tradepartner.adapters.form4_archive import message_digest
        from tradepartner.store.raw_archive import manifest_path

        out = []
        for run_id, _mode, _started, message in runs:
            if (digest := message_digest(message)) is not None:
                # read from disk: `hundred_nights` keeps `read_manifest` patched
                body = manifest_path(self.root, digest).read_bytes()
                assert _sha(body) == digest
                manifest = parse_index_manifest(body)
                assert manifest["writer"]["run_id"] == run_id
                out.append(manifest)
        return out


def _archive_case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **ingest: Any) -> _ArchiveCase:
    """One issuer over March 2026 nights (Q1 open) with the raw archive on."""
    from pydantic import SecretStr

    from tradepartner import diskfloor

    monkeypatch.setattr(diskfloor, "free_bytes", lambda _path: 10**15)
    root = (tmp_path / "archive").resolve()
    root.mkdir()
    settings = _settings(tmp_path, "store", 2026)
    settings = settings.model_copy(
        update={
            "tradepartner_raw_archive": SecretStr(str(root)),
            "ingest": settings.ingest.model_copy(update=ingest),
        }
    )
    accepted = {day: _eastern(date(2026, 3, day), 17) for day in (2, 3)}
    filings = [
        _Filing(
            LAMBDA,
            "Lambda Corp",
            "10-K",
            FIRST,
            accepted[2],
            date(2026, 3, 2),
            date(2026, 3, 2),
            ticker="LAM",
            exchange="NYSE",
            shares=100_000,
        ),
        _Filing(LAMBDA, "Lambda Corp", "10-Q", SECOND, accepted[3], REPOST, REPOST),
    ]
    sec = _Sec(
        filings,
        {LAMBDA: ("Lambda Corp", "LAM", "NYSE")},
        fsn={"2026_01": _instant("2026-02-20T12:00:00-05:00")},
        reposts={REPOST: _eastern(date(2026, 3, 4), 21)},
    )
    return _ArchiveCase(sec, settings, root)


def _sha(body: bytes) -> str:
    import hashlib

    return hashlib.sha256(body).hexdigest()


def test_daily_bodies_are_kept_by_hash_and_rows_map_to_their_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reused and changed bodies, and the source/mode/file-date mapping: each
    daily body read is kept once by hash, a re-posted file keeps both bodies, a
    rebuild that re-reads them keeps none anew, and each stored row maps to
    exactly one writer entry, a revision (its `known_at` the run's commit clock)
    to its night's run. No ingest run reads the archive."""
    from tradepartner.adapters.daily_index import find_index_entry, index_key
    from tradepartner.adapters.form4_archive import ProvenanceError, row_digest
    from tradepartner.store import raw_archive

    case = _archive_case(tmp_path, monkeypatch)
    first_body = case.sec.daily_body(REPOST)

    def never(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an ingest run read the raw archive")

    with pytest.MonkeyPatch.context() as patch:
        for reader in ("read_body", "read_manifest"):
            patch.setattr(raw_archive, reader, never)
        for day in (2, 3, 4, 5):
            case.night(day, rebuild=day == 2)
        kept = case.bodies()
        case.night(5, rebuild=True)
    assert case.bodies() == kept
    assert {_sha(first_body), _sha(case.sec.daily_body(REPOST))} <= kept
    assert _sha(first_body) != _sha(case.sec.daily_body(REPOST))
    runs = case.runs()
    manifests = case.manifests(runs)
    entries = [entry for manifest in manifests for entry in manifest["entries"]]
    assert {entry["raw_sha256"] for entry in entries} == kept
    for entry in entries:
        assert entry["url"] == "https://www.sec.gov/Archives/edgar/" + entry["path"]
        assert entry["fetched_at"] is not None and entry["parsed"]
    modes = {run_id: mode for run_id, mode, *_ in runs}
    with duckdb.connect(case.settings.store.path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        cursor = conn.execute("SELECT * FROM filing_index ORDER BY known_at")
        columns = [item[0] for item in cursor.description]
        stored = [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]
    writers = []
    for row in stored:
        found = []
        for mode in ("rebuild_index", "session"):
            with contextlib.suppress(ProvenanceError):
                found.append(
                    find_index_entry(
                        manifests,
                        mode=mode,
                        listed_on=row["listed_on"],
                        key=index_key(row),
                        digest=row_digest(row),
                    )
                )
        assert len(found) == 1, row
        writer, entry = found[0]
        writers.append((row["accession"], row["filed_on"], modes[writer], entry["file_date"]))
    assert writers == [
        (FIRST, date(2026, 3, 2), "rebuild_index", "2026-03-02"),
        (SECOND, REPOST, "session", "2026-03-03"),
        (SECOND, date(2026, 3, 1), "session", "2026-03-03"),
    ]
    assert stored[-1]["known_at"] == stored[-1]["ingested_at"] == _night(date(2026, 3, 4))


def test_a_failed_daily_archive_write_writes_no_derived_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tradepartner.store import raw_archive
    from tradepartner.store.raw_archive_config import RawArchiveFloorError

    case = _archive_case(tmp_path, monkeypatch)
    case.night(2, rebuild=True)
    before = _tables(case.settings.store.path)

    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise RawArchiveFloorError("raw archive write refused")

    monkeypatch.setattr(raw_archive, "archive_body", refuse)
    case.sec.now = _night(date(2026, 3, 3))
    source = _SimSource(case.settings, case.sec)
    try:
        result = ingest_session(
            case.settings,
            prices=_NoPrices(),
            filings=source,
            source="edgar",
            clock=lambda: case.sec.now,
        )
    finally:
        source.close()
    assert not result.ok
    assert "RawArchiveFloorError" in " ".join(run.message for run in result.runs)
    assert _tables(case.settings.store.path) == before


def test_a_small_message_cap_keeps_the_manifest_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The digest sits before any text `ingest.max_message_chars` can cut."""
    from tradepartner.adapters.form4_archive import message_digest

    case = _archive_case(tmp_path, monkeypatch, max_message_chars=200)
    case.night(2, rebuild=True)
    case.night(3)
    runs = case.runs()
    assert all(len(message) <= 200 for *_, message in runs)
    written = [run for run in runs if message_digest(run[3]) is not None]
    assert {run[1] for run in written} == {"rebuild_index", "session"}
    assert len(case.manifests(written)) == len(written)


def test_a_later_fetch_is_invisible_to_a_store_truncated_before_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No look-ahead: the runs and rows known by night 03-03 cite no file fetched
    later (the 4th's, the re-posted body of the 3rd's) and hold no later row."""
    case = _archive_case(tmp_path, monkeypatch)
    for day in (2, 3, 4):
        case.night(day, rebuild=day == 2)
    cut = _night(date(2026, 3, 3))
    early = [run for run in case.runs() if run[2] <= cut]
    entries = [e for m in case.manifests(early) for e in m["entries"]]
    assert entries
    assert _sha(case.sec.daily_body(REPOST)) not in {e["raw_sha256"] for e in entries}
    assert "daily-index/2026/QTR1/form.20260304.idx" not in {e["path"] for e in entries}
    rows = _rows(
        case.settings,
        "SELECT filed_on FROM filing_index WHERE accession = ? AND known_at <= ?",
        [SECOND, cut],
    )
    assert rows == [(REPOST,)]


def test_quarterly_and_pending_quarterly_rows_are_left_out_of_every_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The snapshot exception: a settled quarter's `form.idx` row, and one still
    pending from it, map to no daily entry. A daily row pending one night maps to
    its own file's entry once stamped. A dry run
    keeps nothing and names no digest."""
    from tradepartner.adapters.daily_index import find_index_entry, index_key
    from tradepartner.adapters.form4_archive import ProvenanceError, message_digest, row_digest

    case = _archive_case(tmp_path, monkeypatch)
    old, late, held = f"{LAMBDA}-25-000009", f"{LAMBDA}-25-000010", f"{LAMBDA}-26-000003"
    stamp = _eastern(date(2026, 3, 3), 12)
    extra = [
        _Filing(
            LAMBDA,
            "Lambda Corp",
            "10-Q",
            old,
            _eastern(date(2025, 11, 3), 17),
            date(2025, 11, 3),
            None,
            appears=_eastern(date(2025, 11, 3), 22),
        ),
        _Filing(
            LAMBDA,
            "Lambda Corp",
            "8-K",
            late,
            _eastern(date(2025, 12, 1), 17),
            date(2025, 12, 1),
            None,
            appears=_eastern(date(2025, 12, 1), 22),
            stamp_from=stamp,
        ),
        _Filing(
            LAMBDA,
            "Lambda Corp",
            "8-K",
            held,
            _eastern(date(2026, 3, 2), 18),
            date(2026, 3, 2),
            date(2026, 3, 2),
            stamp_from=stamp,
        ),
    ]
    case.sec = _Sec(
        [*case.sec.filings, *extra],
        case.sec.issuers,
        fsn=case.sec.fsn,
        reposts=case.sec.reposts,
    )
    case.settings = _with(case.settings, index_first_year=2025)
    case.sec.now = _night(date(2026, 3, 2))
    source = _SimSource(case.settings, case.sec)
    try:
        dry = ingest_session(
            case.settings,
            prices=_NoPrices(),
            filings=source,
            source="edgar",
            clock=lambda: case.sec.now,
            rebuild_index=True,
            dry_run=True,
        )
    finally:
        source.close()
    assert dry.ok and all(message_digest(run.message) is None for run in dry.runs)
    assert case.bodies() == set() and not (case.root / "manifests").exists()
    case.night(2, rebuild=True)
    pending = _rows(case.settings, "SELECT accession FROM filing_index_pending ORDER BY 1")
    assert pending == [(late,), (held,)]
    case.night(3)
    runs = case.runs()
    assert "index_unattributed: 0" in runs[-1][3]
    manifests = case.manifests(runs)
    with duckdb.connect(case.settings.store.path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        cursor = conn.execute("SELECT * FROM filing_index")
        columns = [item[0] for item in cursor.description]
        stored = {
            r["accession"]: r
            for r in (dict(zip(columns, v, strict=True)) for v in cursor.fetchall())
        }

    def entry(accession: str) -> Mapping[str, Any]:
        row = stored[accession]
        return find_index_entry(
            manifests,
            mode="session",
            listed_on=row["listed_on"],
            key=index_key(row),
            digest=row_digest(row),
        )[1]

    assert late not in stored
    for accession in (old,):
        for mode in ("session", "rebuild_index"):
            row = stored[accession]
            with pytest.raises(ProvenanceError):
                find_index_entry(
                    manifests,
                    mode=mode,
                    listed_on=row["listed_on"],
                    key=index_key(row),
                    digest=row_digest(row),
                )
    assert entry(held)["file_date"] == "2026-03-02"


# --- the memory bound ----------------------------------------------------------

MEMORY_ISSUERS = 20
#: Form 4 rows per issuer per quarter: 20 issuers x 61 rows x 4 quarters is
#: about 9,800 index rows a year, so ~19,500 for 2 years and ~195,000 for 20.
MEMORY_FORM4S = 60
MEMORY_SEED = date(2026, 1, 5)  # Q4 2025 settled on 01-04; Q1 2026 open
MEMORY_NIGHT = date(2026, 1, 6)


def _issuer_history(index: int, years: int) -> list[_Filing]:
    """One synthetic issuer's history: a 10-Q (10-K in Q1) and its Form 4s
    each settled quarter from `2026 - years` to 2025, then an 8-K in the seed
    night's daily file; the first issuer files another on the measured night."""
    cik = f"{300 + index:010d}"
    name = f"History {index} Corp"
    out: list[_Filing] = []
    for year in range(2026 - years, 2026):
        for number in range(1, 5):
            day = date(year, 3 * number - 1, 10)
            out.append(
                _Filing(
                    cik,
                    name,
                    "10-K" if number == 1 else "10-Q",
                    f"{cik}-{year % 100:02d}-{number:06d}",
                    _eastern(day, 17),
                    day,
                    day,
                    ticker=f"H{index:03d}",
                    exchange="NYSE",
                    shares=1e6,
                )
            )
            for n in range(MEMORY_FORM4S):
                filer = f"{2000000 + n:010d}"
                out.append(
                    _Filing(
                        cik,
                        name,
                        "4",
                        f"{filer}-{year % 100:02d}-{1000 * number + index:06d}",
                        _eastern(day, 18),
                        day,
                        day,
                    )
                )
    out.append(
        _Filing(
            cik,
            name,
            "8-K",
            f"{cik}-26-000001",
            _eastern(MEMORY_SEED, 17),
            MEMORY_SEED,
            MEMORY_SEED,
        )
    )
    if index == 0:  # tonight's one new filing
        out.append(
            _Filing(
                cik,
                name,
                "8-K",
                f"{cik}-26-000002",
                _eastern(MEMORY_NIGHT, 17),
                MEMORY_NIGHT,
                MEMORY_NIGHT,
            )
        )
    return out


class _HistorySec(_Sec):
    """`_Sec` over a long synthetic history, built per CIK or per quarter on
    demand: the measured process never holds the whole history, so its peak
    RSS is the driver's."""

    def __init__(self, years: int) -> None:
        self.years = years
        ciks = {
            f"{300 + i:010d}": (f"History {i} Corp", f"H{i:03d}", "NYSE")
            for i in range(MEMORY_ISSUERS)
        }
        open_quarter = [f for i in range(MEMORY_ISSUERS) for f in _issuer_history(i, 0)]
        super().__init__(open_quarter, ciks, fsn={"2025_12": _instant("2026-01-02T12:00:00-05:00")})

    def of_cik(self, cik: str) -> list[_Filing]:
        index = int(cik) - 300
        return _issuer_history(index, self.years) if 0 <= index < MEMORY_ISSUERS else []

    def of_quarter(self, quarter: tuple[int, int]) -> list[_Filing]:
        return [
            f
            for i in range(MEMORY_ISSUERS)
            for f in _issuer_history(i, self.years)
            if _quarter(f.filed_on) == quarter
        ]

    def filing(self, accession: str) -> _Filing:
        return next(f for f in self.of_cik(accession[:10]) if f.accession == accession)


def _memory_settings(root: Path, years: int) -> Settings:
    return _settings(root, f"years-{years}", 2026 - years)


def _measure_night(root: str, years: int) -> None:
    """The measured process: one incremental night over the seeded store."""
    sec = _HistorySec(years)
    sec.now = _night(MEMORY_NIGHT)
    _ingest(_memory_settings(Path(root), years), sec, rebuild=False)


def test_driver_peak_rss_is_flat_from_2_to_20_years_of_history(tmp_path: Path) -> None:
    """Memory: the nightly driver's `getrusage` peak RSS, measured in its own
    process (the run row's `peak_rss_bytes`), is within 10% between a
    synthetic 2-year and a 20-year history."""
    import subprocess
    import sys

    tests = Path(__file__).resolve().parents[1]
    peaks = {}
    for years in (2, 20):
        settings = _memory_settings(tmp_path, years)
        sec = _HistorySec(years)
        sec.now = _night(MEMORY_SEED)
        _ingest(settings, sec, rebuild=True)
        stored = _rows(settings, "SELECT count(*) FROM filing_index")[0][0]
        assert stored == MEMORY_ISSUERS * ((MEMORY_FORM4S + 1) * 4 * years + 1)
        del sec
        code = (
            f"import sys; sys.path.insert(0, {str(tests)!r}); "
            "from lookahead.test_incremental_edgar import _measure_night; "
            f"_measure_night({str(tmp_path)!r}, {years})"
        )
        subprocess.run([sys.executable, "-c", code], check=True, timeout=600)
        message = _rows(
            settings,
            "SELECT message FROM ingestion_runs WHERE source = 'edgar_index' "
            "AND mode = 'session' ORDER BY rowid DESC LIMIT 1",
        )[0][0]
        assert "index_added: " in message
        peaks[years] = int(message.split("peak_rss_bytes: ", 1)[1].split(";", 1)[0])
    print(f"driver peak RSS by years of history: {peaks}")
    assert abs(peaks[20] - peaks[2]) <= 0.10 * peaks[2], peaks
