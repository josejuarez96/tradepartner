"""Single-session ingest (spec reqs 1, 9, 10; plan T16).

`ingest_session(settings, prices=..., filings=...)` brings the store up to
the **expected session**: the calendar's last completed session at run time
minus `ingest.settle_delay_minutes` (`expected_session`). Sources run in
order, `edgar` then `alpaca` (the price side fetches the names the master
lists), and the run **halts** at the first source that is not `ok`.

**One atomic chunk per source.** Each source's rows are written inside one
`store.db.open_for_write` transaction, which retries the lock for
`store.lock_retry_seconds` and releases it when the chunk ends. A failure
anywhere in the chunk rolls all of it back; an earlier source's committed
chunk stands. A stale or failed source then gets **only** its
`ingestion_runs` row, written in a chunk of its own. A locked store gets no
row at all (there is no way to write one) and status `locked`.

**Staleness** (price side): stale if the bar for `ingest.reference_symbol`
is missing at the expected session, or if more than
`ingest.max_missing_share` of listed names lack one. Listed names are the
securities whose current listing at the session is `listed`, or
`transferred` and not yet ended. Delisted names whose `effective_on` is
not past are still fetched (their bars fix the listing's end) but not
counted.

**Idempotent.** Builders and sources return full views; a row is written
only if it changes what an as-of read returns:

- *Current-value* rows (bars, corporate actions: the source reports its
  value now) follow `adapters.prices.revision_of`: first seen as stamped;
  unchanged is skipped; different is a new row at `known_at = ingested_at`,
  never back-dated, and an action keeps the stored `announced_at`.
- *Filed* rows (master, delistings, classifications, facts: `known_at` is a
  filing acceptance or fetch time) keep their `known_at` only when it is
  later than every stored row of their key and changes the view. Stored
  history is never rewritten: if the key's latest row still differs from
  the builder's latest (a restatement, a late filing, A -> B -> A), one row
  with the builder's latest values is stamped at `ingested_at`.

The price side fetches bars for the expected session and actions with
`ex_date` from the first of its month to it, so revisions within the month
are seen; the chunk cursor is the session. A revision to an earlier month's
action, or an action announced before its ex-date, is stored only once its
ex-date falls in a fetched window (or by the backfill, T17): never early,
but a live read can see less than a later backfill stamped at the same T.
The staleness denominator counts only common and benchmark listings, so a
preferred or note with no bar never makes a run stale.

**`ingested_at`** for the EDGAR chunk is read from `clock` after every
filing call has returned (the builders run twice over one recorded set of
answers: once to fetch, once to stamp), so a filing accepted while the run
is fetching is stored, not refused as look-ahead.

**Run messages** are stored with every configured secret value redacted,
control characters replaced and the length capped at
`ingest.max_message_chars`: an exception's text comes from a server. The
EDGAR message carries the adapter's unstamped-filing, unstamped-fact and
skipped-filer counts when the source exposes them (#172).
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb

from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverPage,
    DelistingFiling,
    FactRecord,
    FilingHeader,
    FilingIndexEntry,
    FilingSource,
)
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.calendar import last_completed_session
from tradepartner.config import Settings
from tradepartner.store.classify import (
    ClassificationBuild,
    build_classifications,
    classifications_as_of,
)
from tradepartner.store.db import StoreLockedError, insert_row, open_for_write, utc_now
from tradepartner.store.delistings import (
    DELISTED,
    LISTED,
    TRANSFERRED,
    build_delistings,
    listing_ends_as_of,
)
from tradepartner.store.master import MasterBuild, build_master, securities_as_of
from tradepartner.store.schema import init_schema
from tradepartner.timeutil import ensure_tz_aware_utc
from tradepartner.universe import SHARES_FACT

OK, STALE, FAILED, LOCKED = "ok", "stale", "failed", "locked"
MODE = "session"
SOURCES: tuple[str, ...] = ("edgar", "alpaca")

#: XBRL concept requested from `FilingSource.facts` -> the store's `fact_name`.
FACT_NAMES: dict[str, str] = {"EntityCommonStockSharesOutstanding": SHARES_FACT}

Row = dict[str, Any]

#: Per table: the natural key an as-of read collapses on, and the value
#: columns whose change makes a new row.
_TABLES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "securities": (("security_id",), ("cik", "name", "benchmark")),
    "listings": (("security_id", "ticker", "exchange", "valid_from"), ("class_title",)),
    "classifications": (("security_id",), ("sic", "security_type", "rule")),
    "delistings": (
        ("security_id", "form", "class_title", "exchange", "filed_at"),
        ("effective_on",),
    ),
    "facts": (("security_id", "fact_name", "as_of_date", "class_member"), ("value",)),
    "prices_daily": (("security_id", "session"), ("open", "high", "low", "close", "volume")),
    "corporate_actions": (("security_id", "action_type", "ex_date"), ("ratio_or_amount",)),
}


@dataclass(frozen=True)
class SourceRun:
    """One source's outcome; `rows_added` counts fact rows, not the run row."""

    source: str
    status: str
    rows_added: int
    chunk_cursor: str
    message: str


@dataclass(frozen=True)
class IngestResult:
    """Every source attempted, in order; the run halted after a non-`ok` one."""

    runs: tuple[SourceRun, ...]

    @property
    def ok(self) -> bool:
        return all(run.status == OK for run in self.runs)

    @property
    def exit_code(self) -> int:
        """0 when every source is `ok`, else 1 (spec req 9: halts non-zero)."""
        return 0 if self.ok else 1


class _Stale(Exception):
    """The source is stale (spec req 10); its chunk is rolled back."""


class _DryRun(Exception):
    """Raised to roll a dry-run chunk back after counting its rows."""

    def __init__(self, rows: int, message: str) -> None:
        super().__init__(message)
        self.rows = rows


def expected_session(now: datetime, settings: Settings) -> date:
    """The last session completed by `now` minus `ingest.settle_delay_minutes`."""
    now = ensure_tz_aware_utc(now, field_name="now")
    return last_completed_session(now - timedelta(minutes=settings.ingest.settle_delay_minutes))


def ingest_session(
    settings: Settings,
    *,
    prices: PriceSource,
    filings: FilingSource,
    source: str = "all",
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = False,
) -> IngestResult:
    """Ingest the expected session from `source` (`edgar`, `alpaca` or
    `all`); see the module docstring. `clock` gives the run's instant, used
    for the expected session, `ingested_at` and the run row's times. A dry
    run rolls every chunk back and writes no run row."""
    if source not in ("all", *SOURCES):
        raise ValueError(f"source must be 'all' or one of {SOURCES}, got {source!r}")
    now = ensure_tz_aware_utc(clock(), field_name="clock()")
    cursor = expected_session(now, settings).isoformat()
    work: dict[str, Callable[[duckdb.DuckDBPyConnection], tuple[int, str]]] = {
        "edgar": lambda conn: _ingest_filings(conn, settings, filings, clock),
        "alpaca": lambda conn: _ingest_prices(conn, settings, prices, now),
    }
    runs: list[SourceRun] = []
    for name in SOURCES if source == "all" else (source,):
        run = _run_source(name, work[name], settings, now, clock, cursor, dry_run)
        runs.append(run)
        if run.status != OK:
            break
    return IngestResult(tuple(runs))


def _run_source(
    name: str,
    work: Callable[[duckdb.DuckDBPyConnection], tuple[int, str]],
    settings: Settings,
    now: datetime,
    clock: Callable[[], datetime],
    cursor: str,
    dry_run: bool,
) -> SourceRun:
    run_id = uuid.uuid4().hex

    def outcome(status: str, rows: int, message: str) -> SourceRun:
        return SourceRun(name, status, rows, cursor, _clean(message, settings))

    try:
        with open_for_write(settings) as conn:
            init_schema(conn)
            rows, message = work(conn)
            if dry_run:
                raise _DryRun(rows, message)
            run = outcome(OK, rows, message)
            _write_run(conn, run_id, now, clock(), run)
        return run
    except _DryRun as dry:
        return outcome(OK, dry.rows, f"dry run: {dry}")
    except StoreLockedError as exc:
        return outcome(LOCKED, 0, str(exc))
    except _Stale as exc:
        run = outcome(STALE, 0, str(exc))
    except Exception as exc:  # any source or parse failure halts with a run row
        run = outcome(FAILED, 0, f"{type(exc).__name__}: {exc}")
    if not dry_run:
        try:
            with open_for_write(settings) as conn:
                init_schema(conn)
                _write_run(conn, run_id, now, clock(), run)
        except StoreLockedError as exc:
            return outcome(run.status, 0, f"{run.message}; run row: {exc}")
    return run


_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def _clean(message: str, settings: Settings) -> str:
    """`message` with configured secrets redacted, control characters
    replaced by a space, and cut to `ingest.max_message_chars`."""
    for secret in (
        settings.alpaca_api_key,
        settings.alpaca_api_secret,
        settings.sec_edgar_user_agent,
    ):
        value = secret.get_secret_value().strip() if secret is not None else ""
        if value:
            message = message.replace(value, "[redacted]")
    return _CONTROL.sub(" ", message)[: settings.ingest.max_message_chars]


def _write_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    run: SourceRun,
) -> None:
    insert_row(
        conn,
        "ingestion_runs",
        {
            "run_id": run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "status": run.status,
            "source": run.source,
            "mode": MODE,
            "rows_added": run.rows_added,
            "chunk_cursor": run.chunk_cursor,
            "message": run.message,
        },
    )


# --- the EDGAR chunk --------------------------------------------------------


#: `ingested_at` for the fetch pass: later than any record, so no builder refuses one.
_FETCH_PASS = datetime(9000, 1, 1, tzinfo=UTC)


class _Recorded(FilingSource):
    """`source`, with each answer recorded so a second build reads the same."""

    def __init__(self, source: FilingSource) -> None:
        self._source = source
        self._answers: dict[tuple[Any, ...], Any] = {}

    def _ask(self, method: str, *args: Any) -> Any:
        key = (method, *(tuple(a) if isinstance(a, list) else a for a in args))
        if key not in self._answers:
            self._answers[key] = getattr(self._source, method)(*args)
        return self._answers[key]

    def filing_index(self, since: datetime | None = None) -> list[FilingIndexEntry]:
        return list(self._ask("filing_index", since))

    def companies_snapshot(self) -> list[CompanySnapshotEntry]:
        return list(self._ask("companies_snapshot"))

    def facts(self, cik: str, names: Sequence[str]) -> list[FactRecord]:
        return list(self._ask("facts", cik, list(names)))

    def filing_headers(self, cik: str, forms: Sequence[str]) -> list[FilingHeader]:
        return list(self._ask("filing_headers", cik, list(forms)))

    def cover_pages(self, cik: str) -> list[CoverPage]:
        return list(self._ask("cover_pages", cik))

    def delistings(self, since: datetime | None = None) -> list[DelistingFiling]:
        return list(self._ask("delistings", since))


def _ingest_filings(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    filings: FilingSource,
    clock: Callable[[], datetime],
) -> tuple[int, str]:
    recorded = _Recorded(filings)
    _build_filings(recorded, settings, _FETCH_PASS)
    now = ensure_tz_aware_utc(clock(), field_name="clock()")
    master, delistings, classes, facts, unmatched = _build_filings(recorded, settings, now)
    added = 0
    for table, rows in (
        ("securities", master.securities),
        ("listings", master.listings),
        ("delistings", delistings.delistings),
        ("classifications", classes.classifications),
        ("facts", facts),
    ):
        added += _add_rows(conn, table, rows, ingested_at=now, current=False)
    message = (
        f"{len(master.securities)} securities; unmatched: {len(master.unmatched_snapshot)} "
        f"snapshot, {len(delistings.unmatched)} delistings, {len(unmatched)} facts"
        f"{_source_counts(filings)}; "
        f"missing benchmarks: {', '.join(master.missing_benchmarks) or 'none'}"
    )
    return added, message


def _source_counts(filings: FilingSource) -> str:
    """`"; unstamped: N filings, M facts; skipped filers: K"`, naming only the
    attributes `filings` exposes, so rows the EDGAR adapter (T11b, T11c) left
    out stay visible in the run row (#172). Duck-typed: each attribute is a
    count or a collection; a fixture source has none and adds nothing.

    Read once, after both builds, from the unwrapped source. The contract
    this relies on: `unstamped_filings` and `skipped_filers` hold the result
    of the run's one `filing_index` call, and `unstamped_facts` accumulates
    over every `facts` call on the source instance, never reset per CIK.
    The counts sit before the variable-length benchmarks list so
    `ingest.max_message_chars` never cuts them off."""

    def count(attribute: str) -> int | None:
        value = getattr(filings, attribute, None)
        return value if value is None or isinstance(value, int) else len(value)

    unstamped = [
        f"{n} {what}"
        for what in ("filings", "facts")
        if (n := count(f"unstamped_{what}")) is not None
    ]
    parts = [f"unstamped: {', '.join(unstamped)}"] if unstamped else []
    if (skipped := count("skipped_filers")) is not None:
        parts.append(f"skipped filers: {skipped}")
    return "".join(f"; {part}" for part in parts)


def _build_filings(
    filings: FilingSource, settings: Settings, ingested_at: datetime
) -> tuple[MasterBuild, Any, ClassificationBuild, tuple[Row, ...], tuple[FactRecord, ...]]:
    master = build_master(filings, settings, ingested_at=ingested_at)
    delistings = build_delistings(filings.delistings(), master, ingested_at=ingested_at)
    classes = build_classifications(filings, master, settings, ingested_at=ingested_at)
    ciks = sorted({row["cik"] for row in master.securities if not row["benchmark"]})
    records = [record for cik in ciks for record in filings.facts(cik, list(FACT_NAMES))]
    facts, unmatched = fact_rows(records, master, classes, ingested_at=ingested_at)
    return master, delistings, classes, facts, unmatched


_MEMBER_CLASS = re.compile(r"Class([A-Z])(?![a-z])")
_TITLE_CLASS = re.compile(r"\bclass ([a-z])\b")


def fact_rows(
    records: Iterable[FactRecord],
    master: MasterBuild,
    classes: ClassificationBuild,
    *,
    ingested_at: datetime,
) -> tuple[tuple[Row, ...], tuple[FactRecord, ...]]:
    """`facts` rows for `records`, and the records no security could take.

    Candidates are the CIK's **common** classes, by the classification in
    force at the fact's acceptance (no later knowledge picks the class);
    a listed preferred, warrant or note never takes shares. A fact whose
    member names a class letter (`us-gaap:CommonClassBMember`) goes to the
    one common class whose listing title names that letter ("Class B Common
    Stock"), and is unmatched if none does (an unlisted class). Any other
    fact (undimensioned, or a member with no letter) goes to the sole common
    class, and is unmatched when there are several: a total is no one
    class's shares. Raises `ValueError` for a record accepted after
    `ingested_at`.
    """
    kinds: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for row in sorted(classes.classifications, key=lambda r: r["known_at"]):
        kinds[row["security_id"]].append((row["known_at"], row["security_type"]))
    by_cik: dict[str, list[str]] = defaultdict(list)
    for row in master.securities:
        if not row["benchmark"]:
            by_cik[row["cik"]].append(row["security_id"])

    def common_at(cik: str, t: datetime) -> list[str]:
        """The CIK's classes classified `common` by the latest row known at `t`."""
        out = []
        for sid in by_cik.get(cik, []):
            known = [kind for at, kind in kinds[sid] if at <= t]
            if known and known[-1] == "common":
                out.append(sid)
        return out

    letters: dict[str, set[str]] = defaultdict(set)
    for row in master.listings:
        match = _TITLE_CLASS.search((row["class_title"] or "").lower())
        if match:
            letters[row["security_id"]].add(match.group(1).upper())

    rows: list[Row] = []
    unmatched: list[FactRecord] = []
    for record in records:
        if record.accepted_at > ingested_at:
            raise ValueError(
                f"{record.accession}: accepted_at {record.accepted_at.isoformat()} is after "
                f"ingested_at {ingested_at.isoformat()}"
            )
        ids = common_at(record.cik, record.accepted_at)
        member = _MEMBER_CLASS.search(record.class_member)
        if member:
            ids = [sid for sid in ids if member.group(1) in letters[sid]]
        if record.fact_name not in FACT_NAMES or len(ids) != 1:
            unmatched.append(record)
            continue
        rows.append(
            {
                "security_id": ids[0],
                "fact_name": FACT_NAMES[record.fact_name],
                "as_of_date": record.as_of_date,
                "class_member": record.class_member,
                "value": record.value,
                "filing_accession": record.accession,
                "known_at": record.accepted_at,
                "ingested_at": ingested_at,
                "source": "edgar",
                "provenance": "filing",
            }
        )
    return tuple(rows), tuple(unmatched)


# --- the price chunk --------------------------------------------------------


def _ingest_prices(
    conn: duckdb.DuckDBPyConnection, settings: Settings, prices: PriceSource, now: datetime
) -> tuple[int, str]:
    session = expected_session(now, settings)
    symbol = settings.ingest.reference_symbol
    fetch, listed, reference = _price_names(conn, now, session, settings)
    if reference is None:
        raise LookupError(f"reference symbol {symbol} has no live listing at {session}")
    ids = sorted(fetch)
    bars = [bar for bar in prices.bars(ids, session, session) if bar.session == session]
    actions = prices.corporate_actions(ids, session.replace(day=1), session)
    have = {bar.security_id for bar in bars}
    if reference not in have:
        raise _Stale(f"reference symbol {symbol} ({reference}) has no bar for {session}")
    missing = sorted(listed - have)
    share, limit = len(missing) / len(listed), settings.ingest.max_missing_share
    if share > limit:
        raise _Stale(
            f"{len(missing)} of {len(listed)} listed names ({share:.1%}, over {limit:.1%}) "
            f"have no bar for {session}: {', '.join(missing[:10])}"
        )
    window = [session, session]
    added = _add_rows(
        conn,
        "prices_daily",
        [_bar_row(bar, now) for bar in bars],
        ingested_at=now,
        current=True,
        where="AND session BETWEEN ? AND ?",
        params=window,
    )
    added += _add_rows(
        conn,
        "corporate_actions",
        [_action_row(action, now) for action in actions],
        ingested_at=now,
        current=True,
        where="AND ex_date BETWEEN ? AND ?",
        params=[session.replace(day=1), session],
    )
    message = (
        f"{len(bars)} bars and {len(actions)} actions for {len(ids)} names; "
        f"{len(missing)} of {len(listed)} listed names missing"
    )
    return added, message


def _price_names(
    conn: duckdb.DuckDBPyConnection, now: datetime, session: date, settings: Settings
) -> tuple[set[str], set[str], str | None]:
    """Names to fetch, listed common and benchmark names (the staleness
    denominator) and the reference symbol's `security_id`, from each
    security's current listing."""
    current: dict[str, Row] = {}
    for row in listing_ends_as_of(conn, now, settings).iter_rows(named=True):
        held = current.get(row["security_id"])
        if row["valid_from"] <= session and (
            held is None or row["valid_from"] > held["valid_from"]
        ):
            current[row["security_id"]] = row
    kinds = {
        row["security_id"]: row["security_type"]
        for row in classifications_as_of(conn, now).iter_rows(named=True)
    }
    benchmarks = {
        row["security_id"]
        for row in securities_as_of(conn, now).iter_rows(named=True)
        if row["benchmark"]
    }
    fetch: set[str] = set()
    listed: set[str] = set()
    reference = None
    for sid, row in current.items():
        status, end = row["status"], row["end_session"]
        live = status == LISTED or (status == TRANSFERRED and (end is None or end >= session))
        if live and (sid in benchmarks or kinds.get(sid) == "common"):
            listed.add(sid)
        if live and row["ticker"] == settings.ingest.reference_symbol:
            reference = sid
        effective = row["effective_on"]
        if live or (status == DELISTED and effective is not None and effective >= session):
            fetch.add(sid)
    return fetch, listed, reference


def _bar_row(bar: Bar, ingested_at: datetime) -> Row:
    return {
        "security_id": bar.security_id,
        "session": bar.session,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
        "known_at": bar.known_at,
        "ingested_at": ingested_at,
        "source": bar.source,
        "provenance": "bar",
    }


def _action_row(action: CorporateAction, ingested_at: datetime) -> Row:
    return {
        "security_id": action.security_id,
        "action_type": str(action.action_type),
        "ex_date": action.ex_date,
        "ratio_or_amount": action.ratio_or_amount,
        "announced_at": action.announced_at,
        "known_at": action.known_at,
        "ingested_at": ingested_at,
        "source": action.source,
        "provenance": "action",
    }


# --- writing only what changes an as-of read --------------------------------


def _add_rows(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    ingested_at: datetime,
    current: bool,
    where: str = "",
    params: Sequence[Any] = (),
) -> int:
    """Insert each row of `rows` that changes an as-of read (module
    docstring); return how many were inserted."""
    key_cols, value_cols = _TABLES[table]

    def key(row: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(row[c] for c in key_cols)

    def same(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
        return all(a[c] == b[c] for c in value_cols)

    ids = sorted({row["security_id"] for row in rows})
    cursor = conn.execute(
        f"SELECT * FROM {table} WHERE list_contains(?, security_id) {where}", [ids, *params]
    )
    names = [d[0] for d in cursor.description]
    history: dict[tuple[Any, ...], list[Row]] = defaultdict(list)
    for values in cursor.fetchall():
        stored = dict(zip(names, values, strict=True))
        history[key(stored)].append(stored)
    for stored_rows in history.values():
        stored_rows.sort(key=lambda r: r["known_at"])

    incoming: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in sorted(rows, key=lambda r: (key(r), r["known_at"])):
        incoming[key(row)].append(row)
    added = 0
    for row_key, built in incoming.items():
        past = history[row_key]
        if current:  # the source's latest record per key is its value now
            new_rows = _current(built[-1], past, ingested_at, same)
        else:
            new_rows = _filed(built, past, ingested_at, same)
        for new in new_rows:
            if new["known_at"] > ingested_at:
                raise ValueError(
                    f"{table} {row_key}: known_at {new['known_at'].isoformat()} is after "
                    f"ingested_at {ingested_at.isoformat()}; it is not knowable yet"
                )
            insert_row(conn, table, new)
            added += 1
    return added


_Same = Callable[[Mapping[str, Any], Mapping[str, Any]], bool]


def _current(
    row: Mapping[str, Any], past: list[Row], ingested_at: datetime, same: _Same
) -> list[Row]:
    """The revision rule of `adapters.prices.revision_of`, over rows."""
    if not past:
        return [dict(row)]
    latest = past[-1]
    if same(row, latest):
        return []
    if ingested_at <= latest["known_at"]:
        raise ValueError(f"revision at {ingested_at.isoformat()} would be back-dated")
    revised = {**row, "known_at": ingested_at}
    if "announced_at" in latest:
        revised["announced_at"] = latest["announced_at"]
    return [revised]


def _filed(
    built: Sequence[Mapping[str, Any]], past: list[Row], ingested_at: datetime, same: _Same
) -> list[Row]:
    """The rows to add for one key, given the builder's rows for it
    (oldest first) and the stored ones.

    A builder row keeps its own `known_at` only if that is later than every
    stored row of the key and the view just before it differs: history
    already stored is never rewritten or back-dated. Then, if the latest
    row still differs from the builder's latest, one row with the builder's
    latest values is stamped at `ingested_at` (a restatement, a late filing
    behind a stored revision, or A -> B -> A). At most one row per key is
    stamped per run, so no two rows tie on a `known_at`, and a re-run over
    the same builder output adds nothing.
    """
    added: list[Row] = []
    newest = past[-1]["known_at"] if past else None
    for row in built:
        if newest is not None and row["known_at"] <= newest:
            continue
        view = (past + added)[-1] if past or added else None
        if view is None or not same(row, view):
            added.append(dict(row))
    final, latest = built[-1], (past + added)[-1]
    if not same(final, latest):
        if ingested_at <= latest["known_at"]:
            raise ValueError(f"revision at {ingested_at.isoformat()} would be back-dated")
        added.append({**final, "known_at": ingested_at, "ingested_at": ingested_at})
    return added
