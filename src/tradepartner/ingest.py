"""Single-session ingest (spec reqs 1, 9, 10; plan T16).

`ingest_session(settings, prices=..., filings=...)` brings the store up to
the **expected session**: the calendar's last completed session at run time
minus `ingest.settle_delay_minutes` (`expected_session`). Sources run in
order, `edgar` then `alpaca` (the price side fetches the names the master
lists), and the run **halts** at the first source that is not `ok`.

**One atomic chunk per source.** Each source is fetched before its write
transaction: EDGAR with no store connection open (a recording pass), the
price side after reading its names and action replay plan on short
read-only connections (#173). Then each source's rows are written inside one
`store.db.open_for_write` transaction, which retries the lock for
`store.lock_retry_seconds` and releases it when the chunk ends. A failure
anywhere in the chunk rolls all of it back; an earlier source's committed
chunk stands. A stale or failed source then gets **only** its
`ingestion_runs` row, written in a chunk of its own. A locked store gets no
row at all (there is no way to write one) and status `locked`.

**Staleness** (price side): stale if the bar for `ingest.reference_symbol`
is missing at the expected session, or if more than
`ingest.max_missing_share` of listed names lack one. Listed names are the
benchmarks and the common names on one of `universe.exchanges` whose
current listing at the session is `listed`, or `transferred` and not yet
ended. Delisted names whose `effective_on` is not past are still fetched
(their bars fix the listing's end) but not counted. A common name whose
current listing is `snapshot_static` and that has no bar is named in the
run message with its own count, not counted (#784); so is any other name
with no bar now or at the previous session in the store, unless it is a
benchmark or first listed this session, or the store has no bar at all at
the previous session.

**Idempotent.** Builders and sources return full views; a row is written
only if it changes what an as-of read returns:

- *Current-value* rows (bars, corporate actions: the source reports its
  value now) follow `adapters.prices.revision_of`: first seen as stamped;
  unchanged is skipped; different is a new row at `known_at = ingested_at`,
  never back-dated, and an action keeps the stored `announced_at`.
  Actions are matched on their identity (#108, #181, `_add_actions`): the
  source's id when it gives one, so a re-dated action is a revision of one
  event, found across ex-dates. An id-less re-date (one stored key of a
  security and type gone from the window, one new key in it) and an id-less
  key gaining an id are each written as a cancel of the old key plus the new
  row, both at `ingested_at`. A key merely absent is never cancelled.
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
skipped-filer counts when the source exposes them (#172). A `failed` run's
message also names where the error was raised: `_with_frames` appends
` | at: <frames>` (file:line in function, innermost first, across the
`raise ... from` chain, paths relative to the package -- never a source
line or a local/argument value), itself bounded by `ingest.max_where_frames`
and `ingest.max_where_chars` so a long trail is cut, never the error text,
before the redaction and `max_message_chars` cut above (#573).
"""

from __future__ import annotations

import re
import time
import traceback
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
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
    StatementFactRecord,
)
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.calendar import last_completed_session, previous_session
from tradepartner.config import Settings, clean_message
from tradepartner.store.asof import _validate_t
from tradepartner.store.classify import (
    ClassificationBuild,
    build_classifications,
    classifications_as_of,
)
from tradepartner.store.db import (
    StoreLockedError,
    insert_row,
    open_for_write,
    open_read_only,
    utc_now,
)
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
}

#: What makes an action row a new revision of its identity (`CorporateAction.same_values`).
_ACTION_VALUES: tuple[str, ...] = ("action_type", "ex_date", "ratio_or_amount", "cancelled")


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
    recorded = _Recorded(filings)
    work: dict[str, Callable[[duckdb.DuckDBPyConnection], tuple[int, str]]] = {
        "edgar": lambda conn: _ingest_filings(conn, settings, recorded, clock),
        "alpaca": lambda conn: _write_prices(conn, fetched["alpaca"]),
    }
    fetched: dict[str, _PriceFetch] = {}
    prepare: dict[str, Callable[[], object] | None] = {
        "edgar": lambda: _prefetch(recorded, settings, dry_run=dry_run),
        "alpaca": lambda: fetched.update(alpaca=_fetch_prices(settings, prices, now, clock)),
    }
    # T11h: `record_failures` (the failure policy's after-commit hook), edgar
    # only, read from the adapter under `_Recorded` -- a fixture source and
    # the alpaca chunk never have one.
    after_commit: dict[str, Callable[[], None] | None] = {
        "edgar": getattr(_unwrap(filings), "record_failures", None),
        "alpaca": None,
    }
    runs: list[SourceRun] = []
    for name in SOURCES if source == "all" else (source,):
        run = _run_source(
            name,
            work[name],
            settings,
            now,
            clock,
            cursor,
            dry_run,
            prepare=prepare[name],
            after_commit=after_commit[name],
        )
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
    mode: str = MODE,
    prepare: Callable[[], object] | None = None,
    after_commit: Callable[[], None] | None = None,
) -> SourceRun:
    """One chunk: `prepare` (fetching, no store connection open), then
    `work` inside one write transaction with the run row, then -- only for a
    committed `ok`, non-dry run -- `after_commit` (T11h's `record_failures`),
    called outside the write transaction; an exception from it is appended
    to the returned message only, the committed run row never rewritten."""
    run_id = uuid.uuid4().hex

    def outcome(status: str, rows: int, message: str) -> SourceRun:
        return SourceRun(name, status, rows, cursor, _clean(message, settings))

    try:
        if prepare is not None:
            prepare()
        with open_for_write(settings) as conn:
            init_schema(conn)
            rows, message = work(conn)
            if dry_run:
                raise _DryRun(rows, message)
            run = outcome(OK, rows, message)
            _write_run(conn, run_id, now, clock(), run, mode)
    except _DryRun as dry:
        return outcome(OK, dry.rows, f"dry run: {dry}")
    except StoreLockedError as exc:
        return outcome(LOCKED, 0, str(exc))
    except _Stale as exc:
        run = outcome(STALE, 0, str(exc))
        return run if dry_run else _record_only(settings, run_id, now, clock, run, mode)
    except Exception as exc:  # any source or parse failure halts with a run row
        message = _with_frames(f"{type(exc).__name__}: {exc}", exc, settings)
        run = outcome(FAILED, 0, message)
        # A dry run writes no run row, failed or not.
        return run if dry_run else _record_only(settings, run_id, now, clock, run, mode)
    if after_commit is not None:
        try:
            after_commit()
        except Exception as exc:  # never rewrites the already-committed run row
            message = _clean(f"{run.message}; after_commit: {type(exc).__name__}: {exc}", settings)
            run = SourceRun(run.source, run.status, run.rows_added, run.chunk_cursor, message)
    return run


def _record_only(
    settings: Settings,
    run_id: str,
    now: datetime,
    clock: Callable[[], datetime],
    run: SourceRun,
    mode: str,
) -> SourceRun:
    """Write only `run`'s `ingestion_runs` row, in a chunk of its own (a
    stale or failed source); note in the message if the store is locked."""
    try:
        with open_for_write(settings) as conn:
            init_schema(conn)
            _write_run(conn, run_id, now, clock(), run, mode)
    except StoreLockedError as exc:
        message = _clean(f"{run.message}; run row: {exc}", settings)
        return SourceRun(run.source, run.status, 0, run.chunk_cursor, message)
    return run


_PACKAGE_ROOT = Path(__file__).resolve().parent


def _relative_path(filename: str) -> str:
    """`filename` relative to the `tradepartner` package root, or just its
    name when it falls outside the package (stdlib, a dependency, a test)."""
    try:
        return str(Path(filename).resolve().relative_to(_PACKAGE_ROOT))
    except ValueError:
        return Path(filename).name


def _next_in_chain(exc: BaseException) -> BaseException | None:
    """The next exception in `exc`'s chain for `_where`: the explicit
    `raise ... from cause`, else the implicit `__context__` unless a bare
    `raise ... from None` suppressed it."""
    if exc.__cause__ is not None:
        return exc.__cause__
    if exc.__suppress_context__:
        return None
    return exc.__context__


def _where(exc: BaseException, settings: Settings) -> str:
    """`file:line in function` for the innermost `ingest.max_where_frames`
    frames of `exc`'s traceback, continuing into its `__cause__`/
    `__context__` chain so a `raise ... from` keeps the original error's
    frames, joined by ` < `. Reads only a frame's filename, line number and
    function name -- never a source line, a local or an argument value."""
    limit = settings.ingest.max_where_frames
    frames: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(frames) < limit:
        seen.add(id(current))
        for summary in reversed(traceback.extract_tb(current.__traceback__)):
            frames.append(f"{_relative_path(summary.filename)}:{summary.lineno} in {summary.name}")
            if len(frames) >= limit:
                break
        current = _next_in_chain(current)
    return " < ".join(frames)


def _with_frames(message: str, exc: BaseException, settings: Settings) -> str:
    """`message` with ` | at: <frames>` appended for where `exc` was raised,
    the combined text bounded to `ingest.max_where_chars` by shortening the
    frame list -- never `message`, the error text -- before `_clean` applies
    its own, separate `max_message_chars` cut."""
    frames = _where(exc, settings)
    if not frames:
        return message
    budget = settings.ingest.max_where_chars - len(message) - len(" | at: ")
    if budget <= 0:
        return message
    return f"{message} | at: {frames[:budget]}"


#: The run row's message cleaning, shared with the EDGAR adapter's stored
#: failure messages so the two cannot drift (#629).
_clean = clean_message


def _write_run(
    conn: duckdb.DuckDBPyConnection,
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    run: SourceRun,
    mode: str = MODE,
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
            "mode": mode,
            "rows_added": run.rows_added,
            "chunk_cursor": run.chunk_cursor,
            "message": run.message,
        },
    )


# --- the EDGAR chunk --------------------------------------------------------


#: `ingested_at` for the fetch pass: later than any record, so no builder refuses one.
_FETCH_PASS = datetime(9000, 1, 1, tzinfo=UTC)


class _Recorded(FilingSource):
    """`source`, with each answer recorded so a second build reads the same.
    Once `frozen` (after the fetch pass), a question not already answered
    raises instead of reaching the source, so no fetch runs under the lock."""

    def __init__(self, source: FilingSource) -> None:
        self._source = source
        self._answers: dict[tuple[Any, ...], Any] = {}
        self.frozen = False

    def _ask(self, method: str, *args: Any) -> Any:
        key = (method, *(tuple(a) if isinstance(a, list) else a for a in args))
        if key not in self._answers:
            if self.frozen:
                raise RuntimeError(f"filing source asked {key!r} after the fetch pass")
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

    def statement_facts(self, cik: str) -> list[StatementFactRecord]:
        """Memoized like every other question above, but not yet asked
        anywhere: `#660`'s switch defaults off, and no caller reaches this
        until T77b wires a per-CIK call into `_build_filings`'s fetch
        pass. That wiring is not free of this class's own rules, despite
        the shared `_ask` cache: the proxy's memo would otherwise hold
        every record in memory (the spec's "Ingest" section says the
        fetch pass must record only *which* CIKs were filled, never the
        records themselves, for a payload the shares path already reads
        once per CIK). T77b's wiring therefore cannot simply call this
        method and keep the answer; it needs a dedicated memo keyed on
        "CIK filled: yes/no", not the generic `_ask` cache used here."""
        return list(self._ask("statement_facts", cik))


def _unwrap(filings: FilingSource) -> FilingSource:
    """The adapter under any number of `_Recorded` wrappers (T17's fetch
    pass, T11h's `after_commit`/`check_failures` reads)."""
    while isinstance(filings, _Recorded):
        filings = filings._source
    return filings


def _prefetch(recorded: _Recorded, settings: Settings, *, dry_run: bool) -> None:
    """The fetch pass: every filing question, with no store connection open;
    then `check_failures()` (T11h), if the source under `recorded` has one,
    before the lock; then `recorded` answers only from what it holds.

    When `check_failures()` raises on a non-dry run, the source's
    `record_failed_check()` (if it has one) records the run's failures
    first, so they can be quarantined and `accepted` although the chunk
    never commits (#610 policy 2); the check's error is re-raised either
    way, and a dry run records nothing. `dry_run` has no default (#629), so
    a caller cannot record failures by leaving it out.

    Before `check_failures()`, the input-validation gate (#578): if the
    source exposes `validation_failures` (`edgar_validation`) and the pass
    recorded any parse failure, the run fails here with one bounded message
    naming the full list's file, before any store write, on a dry run too.
    `check_failures()` and `record_failed_check()` are then not called: a
    pass with absent inputs must not advance the per-document failure
    counts. If the pass itself raises after recording a parse failure (a
    later step can fail because an input was treated as absent), the gate
    still fails the run with the full list and names that error, so the
    error never hides the list."""
    _fetch_and_gate(recorded, settings)
    source = _unwrap(recorded)
    check_failures = getattr(source, "check_failures", None)
    if check_failures is not None:
        try:
            check_failures()
        except Exception as check_error:
            record = getattr(source, "record_failed_check", None)
            if record is not None and not dry_run:
                try:
                    record()
                except Exception as record_error:
                    raise RuntimeError(
                        f"{check_error}; recording the failures also failed: "
                        f"{type(record_error).__name__}: {record_error}"
                    ) from record_error
            raise
    recorded.frozen = True


def _fetch_and_gate(recorded: _Recorded, settings: Settings) -> None:
    """The fetch pass into `recorded`, then the input-validation gate (#578)
    on the source under it: when the source exposes `validation_failures`
    and anything was recorded, `InputValidationError` names them all, also
    when the pass itself raised afterwards (named and chained). With
    nothing recorded, the pass's own error propagates unchanged."""
    source = _unwrap(recorded)
    validation = getattr(source, "validation_failures", None)

    def gate(stopped_by: BaseException | None = None) -> None:
        if validation is not None:
            counted = {label: getattr(source, attribute, 0) for attribute, label in _EMPTY_COUNTS}
            validation.raise_if_any(settings, counted, stopped_by=stopped_by)

    try:
        _build_filings(recorded, settings, _FETCH_PASS)
    except Exception as error:
        gate(stopped_by=error)  # raises when anything was recorded
        raise
    gate()


#: The source's counts of empty `{}` payloads (#566, #576): counted, never
#: failed, and shown next to a failed validation's list (#578).
_EMPTY_COUNTS: tuple[tuple[str, str], ...] = (
    ("facts_bulk_empty", "empty bulk facts"),
    ("submissions_bulk_empty", "empty bulk submissions"),
    ("facts_api_empty", "empty API facts"),
    ("submissions_api_empty", "empty API submissions"),
)


def _ingest_filings(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    filings: FilingSource,
    clock: Callable[[], datetime],
) -> tuple[int, str]:
    recorded = _Recorded(filings)
    # The fetch pass, so `now` is read only after every source answer. When
    # `filings` is already a frozen fetch pass (`_prefetch`, as both callers
    # do) nothing is left to fetch and repeating its build would only
    # recompute it (#564). A caller that hands an unfrozen source still
    # meets the input-validation gate before any row is written (#578).
    if not (isinstance(filings, _Recorded) and filings.frozen):
        _fetch_and_gate(recorded, settings)
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
    `ingest.max_message_chars` never cuts them off. `filings` arrives
    wrapped in `_Recorded` (T17's fetch pass), so the counts are read from
    the adapter underneath, never from the proxy (#194). The adapter must
    hold them as plain attributes set during the fetch pass, never as
    properties that fetch: this read runs after the pass is frozen, inside
    the write transaction."""
    filings = _unwrap(filings)

    def count(attribute: str) -> int | None:
        value = getattr(filings, attribute, None)
        return None if value is None else int(value if isinstance(value, int) else len(value))

    unstamped = [
        f"{n} {what}"
        for what in ("filings", "facts")
        if (n := count(f"unstamped_{what}")) is not None
    ]
    parts = [f"unstamped: {', '.join(unstamped)}"] if unstamped else []
    if (skipped := count("skipped_filers")) is not None:
        parts.append(f"skipped filers: {skipped}")
    for attribute, label in (
        ("fsn_reissued", "FSN re-issued"),  # T11c: re-issued or rolled-up periods
        ("fsn_duplicates", "FSN duplicates"),
        ("fsn_reissue_undetected", "FSN re-issues unchecked"),
        ("fsn_incomplete_listings", "FSN incomplete listings"),
        ("cover_incomplete_listings", "cover incomplete listings"),  # #612: per-document
        ("fsn_missing", "FSN missing"),  # T11d: older cover-form accessions not in FSN
        ("pre_xml_delistings", "pre-XML delistings"),
        ("unstamped_delistings", "unstamped delistings"),  # T11f
        ("failed_filings", "failed filings"),  # T11h: the failure policy
        ("quarantined", "quarantined"),
        ("facts_missing", "facts missing"),  # T11h: T11e's company-facts-404 leftover
        ("facts_bulk_empty", "empty bulk facts"),  # #566: `{}` companyfacts.zip members
        ("submissions_bulk_empty", "empty bulk submissions"),
        ("facts_api_empty", "empty API facts"),  # #576: per-CIK API 200 `{}`
        ("submissions_api_empty", "empty API submissions"),
        ("facts_bulk_keyless", "keyless bulk facts"),  # #599: facts but no `cik`
        ("facts_api_keyless", "keyless API facts"),
        ("facts_capped_dropped", "capped facts dropped"),  # #610 X2
    ):
        if (n := count(attribute)) is not None:
            parts.append(f"{label}: {n}")
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
    force at the fact's acceptance (no later knowledge picks the class),
    without a class whose successor is known by then (`MasterBuild.successions`);
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
    succeeded = {s.predecessor_id: s.known_at for s in master.successions}

    def common_at(cik: str, t: datetime) -> list[str]:
        """The CIK's classes classified `common` by the latest row known at
        `t`, less any whose successor (new equity, #820) is known at `t`."""
        out = []
        for sid in by_cik.get(cik, []):
            if sid in succeeded and succeeded[sid] <= t:
                continue
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


@dataclass(frozen=True)
class _PriceFetch:
    """The daily price chunk's fetch pass, ready to write."""

    session: date
    bars: tuple[Bar, ...]
    actions: tuple[CorporateAction, ...]
    action_window: tuple[date, date]
    covered: tuple[date, date]
    ingested_at: datetime
    message: str


def _fetch_prices(
    settings: Settings,
    prices: PriceSource,
    now: datetime,
    clock: Callable[[], datetime],
) -> _PriceFetch:
    """The price side's fetch pass (#173): names and the action replay plan
    from short read-only connections, bars and actions fetched with no store
    connection open, then the staleness checks. Raises `LookupError` with no
    live reference listing and `_Stale` as in the module docstring."""
    session = expected_session(now, settings)
    symbol = settings.ingest.reference_symbol
    reference: str | None = None
    fetch: set[str] = set()
    listed: set[str] = set()
    static_only: set[str] = set()
    may_count: set[str] | None = None
    if Path(settings.store.path).exists():
        with _price_read(settings) as conn:
            # Read the store as of now, after the EDGAR chunk committed (its snapshot
            # rows are stamped at their fetch time, which can be after the run began).
            read_at = ensure_tz_aware_utc(clock(), field_name="clock()")
            fetch, listed, static_only, may_count, reference = _price_names(
                conn, read_at, session, settings
            )
    if reference is None:
        raise LookupError(f"reference symbol {symbol} has no live listing at {session}")
    ids = sorted(fetch)
    bars = [bar for bar in prices.bars(ids, session, session) if bar.session == session]
    action_window = (session.replace(day=1), session)
    actions = prices.corporate_actions(ids, *action_window)
    with _price_read(settings) as conn:
        plan = _replay_plan(conn, actions, action_window)
    actions, covered = _replay_actions(prices, actions, action_window, plan)
    ingested_at = ensure_tz_aware_utc(clock(), field_name="clock()")  # revisions: when fetched
    have = {bar.security_id for bar in bars}
    if reference not in have:
        raise _Stale(f"reference symbol {symbol} ({reference}) has no bar for {session}")
    counted, missing, reported = _staleness(listed, have, static_only, may_count)
    share, limit = len(missing) / len(counted), settings.ingest.max_missing_share
    if share > limit:
        raise _Stale(
            f"{len(missing)} of {len(counted)} listed names ({share:.1%}, over {limit:.1%}) "
            f"have no bar for {session}: {', '.join(missing[:10])}{_reported_note(reported)}"
        )
    message = (
        f"{len(bars)} bars and {len(actions)} actions for {len(ids)} names; "
        f"{len(missing)} of {len(counted)} listed names missing{_reported_note(reported)}"
    )
    if resolution := prices.resolution_summary():
        message += f"; {resolution}"
    return _PriceFetch(
        session, tuple(bars), tuple(actions), action_window, covered, ingested_at, message
    )


@contextmanager
def _price_read(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    """`_read` for the price side's fetch pass. It reads before any
    `init_schema` (only a write migrates), so a store with no tables or an
    older schema fails plainly: run `edgar` or `all` first."""
    try:
        with _read(settings) as conn:
            yield conn
    except (duckdb.CatalogException, duckdb.BinderException) as exc:
        raise LookupError(
            f"the store's schema is not ready for the price side; run edgar or all first: {exc}"
        ) from exc


def _write_prices(conn: duckdb.DuckDBPyConnection, fetched: _PriceFetch) -> tuple[int, str]:
    """The price chunk's write: the fetched bars and actions, nothing fetched."""
    at, session = fetched.ingested_at, fetched.session
    added = _add_rows(
        conn,
        "prices_daily",
        [_bar_row(bar, at) for bar in fetched.bars],
        ingested_at=at,
        current=True,
        where="AND session BETWEEN ? AND ?",
        params=[session, session],
    )
    added += _add_actions(
        conn, fetched.actions, fetched.action_window, ingested_at=at, covered=fetched.covered
    )
    return added, fetched.message


@contextmanager
def _read(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    """A short-lived read-only connection, retrying a writer's lock for
    `store.lock_retry_seconds` with `open_for_write`'s backoff (spec req 9),
    then `StoreLockedError`."""
    cfg = settings.store
    deadline = time.monotonic() + cfg.lock_retry_seconds
    delay = cfg.lock_retry_initial_delay_seconds
    while True:
        try:
            reader = open_read_only(settings)
            conn = reader.__enter__()
        except StoreLockedError as exc:
            if isinstance(exc.__cause__, duckdb.ConnectionException):
                raise  # this process holds the file: waiting never helps
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, cfg.lock_retry_max_delay_seconds)
            continue
        try:
            yield conn
        finally:
            reader.__exit__(None, None, None)
        return


STATIC = "snapshot_static"


def _counted(
    sid: str, row: Row, benchmarks: set[str], kinds: dict[str, str], settings: Settings
) -> bool:
    """Whether a live listing `row` puts `sid` in the staleness denominator:
    a benchmark, or a common name on one of `universe.exchanges` (OTC is not
    one, and the SIP feed has no OTC bars; #784)."""
    return sid in benchmarks or (
        kinds.get(sid) == "common" and row["exchange"] in settings.universe.exchanges
    )


def _types_known(conn: duckdb.DuckDBPyConnection, t: datetime) -> dict[str, set[str]]:
    """Every `security_type` of each security in a `classifications` row
    known at `t`: every revision, not only the latest, so a window before a
    later reclassification still fetches the name (#794). A bare date
    raises `TypeError`, a naive datetime `ValueError`."""
    t = _validate_t(t)
    types: dict[str, set[str]] = defaultdict(set)
    for sid, kind in conn.execute(
        "SELECT DISTINCT security_id, security_type FROM classifications WHERE known_at <= ?",
        [t],
    ).fetchall():
        types[sid].add(kind)
    return types


#: Classifier labels never fetched unless `universe.security_types` admits
#: them: debt (notes, debentures), preferreds, warrants, units and rights.
#: Everything else on `universe.exchanges` is fetched (owner, #802).
NOT_EQUITY = frozenset({"debt", "preferred", "warrant", "unit", "right"})


def _fetched(
    sid: str,
    row: Row,
    benchmarks: set[str],
    types: Mapping[str, set[str]],
    settings: Settings,
) -> bool:
    """Whether a listing `row` puts `sid` in the price fetch (#794): a
    benchmark, or a listing on one of `universe.exchanges` whose security
    has no classification yet or a `types` entry outside `NOT_EQUITY` (or
    in `universe.security_types`). Common, unclassifiable, spac, foreign,
    fund and depositary names are fetched; no OTC listing. Wider than
    `_counted`, so every counted name is fetched."""
    if sid in benchmarks:
        return True
    if row["exchange"] not in settings.universe.exchanges:
        return False
    known = types.get(sid)
    skipped = NOT_EQUITY - set(settings.universe.security_types)
    return not known or not known <= skipped


def _may_count(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    previous: tuple[date, date],
    earliest: Mapping[str, date],
    benchmarks: set[str],
) -> set[str] | None:
    """The names whose miss may count (#784, dark names): those with a bar
    in the `previous` chunk window already in the store at `t` (nothing this
    chunk fetched), those first listed after it, and the benchmarks. `None`
    when the store holds no bar at all in `previous` (no previous chunk):
    then every miss counts."""
    first, last = previous
    had = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT security_id FROM prices_daily "
            "WHERE session BETWEEN ? AND ? AND known_at <= ?",
            [first, last, t],
        ).fetchall()
    }
    if not had:
        return None
    return had | benchmarks | {sid for sid, start in earliest.items() if start > last}


SNAPSHOT_ONLY = "snapshot-only names with no rows"
DARK = "names with no bar in the previous chunk"


def _staleness(
    listed: Iterable[str], have: set[str], static_only: set[str], may_count: set[str] | None
) -> tuple[list[str], list[str], dict[str, list[str]]]:
    """`(counted, missing, reported)`. A listed name with no bar is reported
    by cause, and left out of both sides of the share, if it is in
    `static_only` (listed only by `snapshot_static` spans, never a
    benchmark: its back-dated ticker may not be the one it traded under
    then; #784, owner option a), or else if `may_count` is not `None` and
    lacks it (dark since before this chunk: it counted once, in the chunk
    it went dark). Every other listed name counts and is missing if it has
    no bar."""
    absent = sorted(sid for sid in set(listed) if sid not in have)
    static = [sid for sid in absent if sid in static_only]
    dark = [
        sid
        for sid in absent
        if sid not in static_only and may_count is not None and sid not in may_count
    ]
    out = {*static, *dark}
    counted = sorted(set(listed) - out)
    missing = [sid for sid in absent if sid not in out]
    return counted, missing, {SNAPSHOT_ONLY: static, DARK: dark}


def _reported_note(reported: Mapping[str, list[str]]) -> str:
    """The run-message clauses naming `_staleness`'s reported names by
    cause (count and up to 10 names each), or `""`."""
    return "".join(
        f"; {len(names)} {cause} (not counted): {', '.join(names[:10])}"
        for cause, names in reported.items()
        if names
    )


def _price_names(
    conn: duckdb.DuckDBPyConnection, now: datetime, session: date, settings: Settings
) -> tuple[set[str], set[str], set[str], set[str] | None, str | None]:
    """Names to fetch (`_fetched` listings live or delisted from `session`
    on, and the reference), listed common and benchmark names (the staleness
    denominator), the listed names whose current listing is
    `snapshot_static` (benchmarks never), `_may_count` over the previous
    session, and the reference symbol's `security_id`, from each
    security's current listing."""
    current: dict[str, Row] = {}
    earliest: dict[str, date] = {}
    for row in listing_ends_as_of(conn, now, settings).iter_rows(named=True):
        sid = row["security_id"]
        earliest[sid] = min(row["valid_from"], earliest.get(sid, row["valid_from"]))
        held = current.get(sid)
        if row["valid_from"] <= session and (
            held is None or row["valid_from"] > held["valid_from"]
        ):
            current[sid] = row
    kinds = {
        row["security_id"]: row["security_type"]
        for row in classifications_as_of(conn, now).iter_rows(named=True)
    }
    benchmarks = {
        row["security_id"]
        for row in securities_as_of(conn, now).iter_rows(named=True)
        if row["benchmark"]
    }
    types = _types_known(conn, now)
    fetch: set[str] = set()
    listed: set[str] = set()
    static_only: set[str] = set()
    reference = None
    for sid, row in current.items():
        status, end = row["status"], row["end_session"]
        live = status == LISTED or (status == TRANSFERRED and (end is None or end >= session))
        if live and _counted(sid, row, benchmarks, kinds, settings):
            listed.add(sid)
            if row["provenance"] == STATIC and sid not in benchmarks:
                static_only.add(sid)
        if live and row["ticker"] == settings.ingest.reference_symbol:
            reference = sid
        effective = row["effective_on"]
        ending = status == DELISTED and effective is not None and effective >= session
        if (live or ending) and _fetched(sid, row, benchmarks, types, settings):
            fetch.add(sid)
    if reference is not None:
        fetch.add(reference)
    before = previous_session(session)
    may_count = _may_count(conn, now, (before, before), earliest, benchmarks)
    return fetch, listed, static_only, may_count, reference


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
        "source_action_id": action.source_action_id or "",
        "cancelled": action.cancelled,
        "known_at": action.known_at,
        "ingested_at": ingested_at,
        "source": action.source,
        "provenance": "action",
    }


# --- corporate actions by identity (#108, #181) ------------------------------

_Identity = tuple[Any, ...]


def _identity(row: Mapping[str, Any]) -> _Identity:
    """`CorporateAction.key` over a row: the source id when set ('' is none)."""
    if row["source_action_id"]:
        return (row["security_id"], "source_action_id", row["source_action_id"])
    return (row["security_id"], row["action_type"], row["ex_date"])


def _same_action(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return all(a[c] == b[c] for c in _ACTION_VALUES)


def _stored_actions(
    conn: duckdb.DuckDBPyConnection, rows: Sequence[Row], window: tuple[date, date]
) -> dict[_Identity, list[Row]]:
    """Stored rows per identity, oldest first: those with an ex-date in
    `window`, and every row of an incoming source id whatever its ex-date."""
    ids = sorted({row["security_id"] for row in rows})
    source_ids = sorted({row["source_action_id"] for row in rows if row["source_action_id"]})
    cursor = conn.execute(
        "SELECT * FROM corporate_actions WHERE list_contains(?, security_id) "
        "AND (ex_date BETWEEN ? AND ? OR list_contains(?, source_action_id))",
        [ids, *window, source_ids],
    )
    names = [d[0] for d in cursor.description]
    history: dict[_Identity, list[Row]] = defaultdict(list)
    for values in cursor.fetchall():
        stored = dict(zip(names, values, strict=True))
        history[_identity(stored)].append(stored)
    for stored_rows in history.values():
        stored_rows.sort(key=lambda r: r["known_at"])
    return history


def _cancel(latest: Row, ingested_at: datetime) -> Row:
    if ingested_at <= latest["known_at"]:
        raise ValueError(f"cancel at {ingested_at.isoformat()} would be back-dated")
    return {**latest, "cancelled": True, "known_at": ingested_at, "ingested_at": ingested_at}


def _outside(
    history: Mapping[_Identity, list[Row]], rows: Sequence[Row], window: tuple[date, date]
) -> list[date]:
    """Stored ex-dates outside `window` of the source ids in `rows`."""
    first, last = window
    ids = {_identity(row) for row in rows if row["source_action_id"]}
    return [
        stored["ex_date"]
        for identity in ids
        for stored in history.get(identity, [])
        if not first <= stored["ex_date"] <= last
    ]


_ReplayPlan = tuple[frozenset[_Identity], tuple[date, date]]


def _replay_plan(
    conn: duckdb.DuckDBPyConnection,
    actions: Sequence[CorporateAction],
    window: tuple[date, date],
) -> _ReplayPlan | None:
    """When a source id in `actions` (the source's answer for ex-dates in
    `window`) has stored rows with an ex-date outside `window`: those ids
    and the window widened to cover them; else `None`. Only reads `conn`.

    A replaying source filters each revision on its own ex-date, so the
    narrow answer could hold an older revision of a re-dated event and
    revert it; `_replay_actions` asks again over the widened window.
    """
    rows = [_action_row(action, action.known_at) for action in actions]  # only ids are read
    outside = _outside(_stored_actions(conn, rows, window), rows, window)
    if not outside:
        return None
    first, last = window
    ids = frozenset(_identity(row) for row in rows if row["source_action_id"])
    return ids, (min(first, *outside), max(last, *outside))


def _replay_actions(
    prices: PriceSource,
    actions: Sequence[CorporateAction],
    window: tuple[date, date],
    plan: _ReplayPlan | None,
) -> tuple[list[CorporateAction], tuple[date, date]]:
    """`actions` plus the source's answer for the plan's ids over its widened
    window, and the window the list covers (`covered` for `_add_actions`).
    Needs no store connection: backfill calls it with none open, so the
    extra request never holds the store."""
    if plan is None:
        return list(actions), window
    ids, wide = plan
    again = prices.corporate_actions(sorted({identity[0] for identity in ids}), *wide)
    extra = [a for a in again if a.key in ids]
    return [*actions, *extra], wide


def _add_actions(
    conn: duckdb.DuckDBPyConnection,
    actions: Sequence[CorporateAction],
    window: tuple[date, date],
    *,
    ingested_at: datetime,
    covered: tuple[date, date] | None = None,
) -> int:
    """Write `actions` (from `_replay_actions`, covering ex-dates in
    `covered`, default `window`) by identity; return the rows added.

    Per identity the source's latest record known by `ingested_at` is its
    value now, under the revision rule of `_current` (a replayed revision
    recorded later waits for a later run; a stored identity with no such
    record is left as it is). If a source id has stored rows
    with an ex-date outside `covered` (the store changed after the replay
    was planned), nothing is written: `ValueError`, re-run. A cancel of an
    identity never stored adds nothing.

    Replacements are stamped at `ingested_at`, never at a first-seen proxy
    (look-ahead). A first-seen id row whose `(security_id, action_type,
    ex_date)` is a live stored id-less key cancels that key (once, however
    many ids share it). When exactly one live id-less key of a security and
    type in `window` is gone from the answer and exactly one new key of that
    security and type appears (id-less, or an id that cancelled nothing),
    the old key is cancelled: an id-less re-date, or an id-less key gaining
    an id and a new ex-date. Any first-seen row of a security and type with
    a cancel in this run is stamped at `ingested_at`, as the fixture
    contract requires. A stored key that is only absent stays live: absence
    is not a withdrawal. An id-less key re-dated out of `window` is not
    seen, so both keys stay live; sources with ids (Alpaca) avoid that.
    """
    rows = [_action_row(action, ingested_at) for action in actions]
    history = _stored_actions(conn, rows, window)
    first, last = window
    lo, hi = covered or window
    stray = [ex for ex in _outside(history, rows, window) if not lo <= ex <= hi]
    if stray:
        raise ValueError(
            f"stored revisions with ex-dates {sorted(set(stray))} fall outside the replayed "
            f"window {lo}..{hi}; the store changed after the replay was planned, re-run"
        )

    records: dict[_Identity, list[Row]] = defaultdict(list)
    for row in sorted(rows, key=lambda r: r["known_at"]):
        records[_identity(row)].append(row)
    incoming: dict[_Identity, Row] = {}
    for identity, recs in records.items():
        known = [r for r in recs if r["known_at"] <= ingested_at]
        if known:
            incoming[identity] = known[-1]
        elif identity not in history:
            incoming[identity] = recs[-1]  # first seen but not knowable yet: refused below

    def live(identity: _Identity) -> Row | None:
        past = history.get(identity)
        return past[-1] if past and not past[-1]["cancelled"] else None

    new_rows: list[Row] = []
    cancelled: set[tuple[str, str]] = set()
    retired: set[_Identity] = set()
    first_seen: list[Row] = []
    for identity, row in incoming.items():
        past = history.get(identity)
        if past:
            revision = _current(row, past, ingested_at, _same_action)
            new_rows += revision
            if revision and row["cancelled"]:
                cancelled.add((row["security_id"], row["action_type"]))
                retired.add(identity)
        elif not row["cancelled"]:
            first_seen.append(row)

    unpaired: list[Row] = []
    for row in first_seen:
        group = (row["security_id"], row["action_type"])
        old = (*group, row["ex_date"])
        if not row["source_action_id"]:
            unpaired.append(row)
        elif old in retired:  # its id-less key is already cancelled in this run
            continue
        elif (latest := live(old)) is not None and old not in incoming:
            new_rows.append(_cancel(latest, ingested_at))  # an id-less key gaining an id
            cancelled.add(group)
            retired.add(old)
        else:
            unpaired.append(row)

    gone: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for identity in history:
        latest = live(identity)
        if (
            latest is not None
            and not latest["source_action_id"]
            and first <= latest["ex_date"] <= last
            and identity not in incoming
            and identity not in retired
        ):
            gone[(latest["security_id"], latest["action_type"])].append(latest)
    appeared: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for row in unpaired:
        appeared[(row["security_id"], row["action_type"])].append(row)
    for group, old_rows in gone.items():
        if len(old_rows) == 1 and len(appeared.get(group, [])) == 1:
            new_rows.append(_cancel(old_rows[0], ingested_at))
            cancelled.add(group)

    for row in first_seen:
        replaces = (row["security_id"], row["action_type"]) in cancelled
        new_rows.append({**row, "known_at": ingested_at} if replaces else dict(row))

    for new in new_rows:
        if new["known_at"] > ingested_at:
            raise ValueError(
                f"corporate_actions {_identity(new)}: known_at {new['known_at'].isoformat()} "
                f"is after ingested_at {ingested_at.isoformat()}; it is not knowable yet"
            )
        insert_row(conn, "corporate_actions", new)
    return len(new_rows)


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
