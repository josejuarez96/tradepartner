"""Trial registry API (Phase 3 spec reqs 8, 9, 11, 12; plan T31b).

The only way code reads or writes the registry tables that `store.schema`
creates at schema version 3. **Inserts and reads only**: a trial's outcome
is its one `trial_results` row, never a change to its `trials` row, and a
trial with no result row is listed as `unfinished`. Every function takes an
open connection and leaves the transaction to the caller (`store.db`'s
`open_for_write` commits a chunk); ids are `MAX + 1` inside that write
transaction, so they increase in the order trials were opened.

**Trial handle.** `open_trial` is the only constructor of `TrialHandle`
(ADR 0005: no id, no run). A handle remembers the database file it was
opened on and its trial's `started_at`, and every write refuses a handle
whose `trials` row is not in this store. Commit `open_trial` in its own
write chunk before any other work: a rolled-back open leaves a handle
with no row, which every later write refuses.

**Real store.** The connection's database file is the real store when it
is the same file as `settings.store.path` (file identity, so a hard link,
symlink, relative path or case variant is still the real store). There,
`register_hypothesis` refuses `family="oracle"` and `open_trial` refuses
`synthetic=True`, so no test or oracle trial can reach the owner's
registry (spec "Definitions" > Trial).

**Parameters.** A hypothesis's frozen parameters are a flat mapping of
dotted config keys to JSON values (`{"costs.per_side_bps": 15.0, ...}`).
The registry stores them as `canonical_params_json` and hashes that text
(`params_sha256`), so one parameter set has one hash whoever computes it.
Values hash as written: `15` and `15.0` differ, so the caller normalizes
types (floats for float keys, ISO strings for dates) before registering.
`costs.per_side_bps` is required: it is the trial's base cost level, the
level N, V and DSR use (spec req 6). A slug stays in the family it was
first registered in, so moving it cannot reset N or hide holdout spends.

**Result rows.** `close_trial` records any outcome other than `ok`
(`failed` and the three refusals), with no statistics. `write_result`
records `ok` with its statistics, unless the store's latest `ingested_at`
differs from the value captured at `open_trial`: then it records `failed`
with `STORE_CHANGED_MESSAGE` instead and returns `"failed"` (spec req 9).
It returns rather than raises so the row survives the caller's commit.
Detail rows (`write_metrics`, `write_equity`, `write_weights`,
`write_rebalances`) are refused once a trial has its result row.

**Deflated Sharpe inputs** (spec req 8). `family_sharpes` counts N over the
`ok`, non-synthetic, `in_sample` trials of a family and returns, per basis,
the monthly Sharpe of the latest such trial (highest id) per distinct
(parameter hash, window) pair, read from the base-level `strategy` rows of
`trial_metrics`. The window is the **resolved** one: the first rebalance
session (last XNYS session of a month, ADR 0006) on or after the requested
start, and `data_cutoff`, so requested dates that resolve to the same
sessions are one pair and cannot shrink V. Only `sharpe_monthly` and `sharpe_monthly_excess_spy` are
read. A trial still being written can be counted as the latest of its pair
with `pending=`, so a run's own N and V include it.
"""

from __future__ import annotations

import json
import math
import os
import statistics
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import astuple, dataclass, fields
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Final, Literal

import duckdb
import pyarrow as pa

from tradepartner.calendar import last_session_of_month
from tradepartner.config import Settings, get_settings
from tradepartner.store.db import insert_row, utc_now
from tradepartner.store.schema import TABLE_PROVENANCE_VALUES

BASE_COST_KEY: Final = "costs.per_side_bps"
ORACLE_FAMILY: Final = "oracle"
STORE_CHANGED_MESSAGE: Final = "store changed during run"
UNFINISHED: Final = "unfinished"

TrialKind = Literal["in_sample", "holdout", "tracking"]
Basis = Literal["raw", "excess_spy"]

#: Outcomes `close_trial` records; `ok` goes through `write_result`.
CLOSE_STATUSES: Final = frozenset({"failed", "refused_window", "refused_holdout", "refused_gap"})

_BASIS_METRICS: Final[dict[Basis, str]] = {
    "raw": "sharpe_monthly",
    "excess_spy": "sharpe_monthly_excess_spy",
}


class RegistryError(RuntimeError):
    """A registry call that would break a registry rule."""


class RealStoreRefused(RegistryError):
    """An oracle hypothesis or synthetic trial aimed at `settings.store.path`."""


class UnknownHypothesis(RegistryError):
    """No hypothesis is registered under the slug or id."""


class TrialAlreadyClosed(RegistryError):
    """The trial already has its result row; nothing more may be written."""


@dataclass(frozen=True)
class HypothesisRecord:
    """One `hypotheses` row, with `params` decoded."""

    hypothesis_id: int
    slug: str
    family: str
    title: str
    doc_path: str
    doc_sha256: str
    params: dict[str, Any]
    params_sha256: str
    in_sample_start: date
    holdout_start: date
    holdout_end: date
    registered_at: datetime
    registered_by: str


class TrialHandle:
    """Proof that a `trials` row exists. Built only by `open_trial`."""

    __slots__ = (
        "database",
        "family",
        "hypothesis_id",
        "kind",
        "params_sha256",
        "started_at",
        "store_max_ingested_at",
        "synthetic",
        "trial_id",
    )

    trial_id: int
    hypothesis_id: int
    family: str
    params_sha256: str
    kind: str
    synthetic: bool
    started_at: datetime
    store_max_ingested_at: datetime | None
    database: str | None

    def __init__(self) -> None:
        raise TypeError("a TrialHandle is issued only by registry.open_trial")

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("a TrialHandle is immutable")

    def __repr__(self) -> str:
        return f"TrialHandle(trial_id={self.trial_id}, family={self.family!r}, kind={self.kind!r})"


def _issue_handle(**values: Any) -> TrialHandle:
    handle = object.__new__(TrialHandle)
    for name, value in values.items():
        object.__setattr__(handle, name, value)
    return handle


@dataclass(frozen=True)
class ResultStatistics:
    """The statistics columns of an `ok` `trial_results` row (spec req 8)."""

    n_trials: int | None = None
    sharpe_variance: float | None = None
    sr_star: float | None = None
    psr_zero: float | None = None
    dsr: float | None = None
    sharpe_variance_excess: float | None = None
    sr_star_excess: float | None = None
    psr_zero_excess: float | None = None
    dsr_excess: float | None = None
    dsr_basis: str | None = None
    red_flag: bool | None = None
    gap_max_count_share: float | None = None
    gap_max_size_share: float | None = None


@dataclass(frozen=True)
class MetricRow:
    """One `trial_metrics` row; `value` is None where a metric does not apply."""

    series: str
    cost_per_side_bps: float
    metric: str
    value: float | None


@dataclass(frozen=True)
class EquityRow:
    """One `trial_equity` row; `cash` is None for a benchmark series."""

    series: str
    cost_per_side_bps: float
    session: date
    equity: float
    cash: float | None


@dataclass(frozen=True)
class WeightRow:
    """One base-level `trial_weights` row; price and shares None on a missing fill."""

    fill_session: date
    security_id: str
    target_weight: float
    fill_price: float | None
    shares: float | None


@dataclass(frozen=True)
class RebalanceRow:
    """One `trial_rebalances` row (spec req 5 counts)."""

    cost_per_side_bps: float
    session: date
    fill_session: date
    n_universe: int
    n_static_listings: int
    n_targets: int
    turnover: float
    cost_paid: float
    gap_count_share: float | None
    gap_size_share: float | None
    n_missing_fill: int
    n_delisting_exits: int
    n_stale_exits: int
    n_excluded_no_history: int
    n_dropped_dividends: int
    n_late_dividends: int


@dataclass(frozen=True)
class FamilySharpes:
    """N and the per-basis monthly Sharpes V is taken over (spec req 8)."""

    n_trials: int
    raw: tuple[float, ...]
    excess_spy: tuple[float, ...]

    def variance(self, basis: Basis) -> float | None:
        """Sample variance of `basis`'s Sharpes; None with fewer than two pairs."""
        values = self.raw if basis == "raw" else self.excess_spy
        return statistics.variance(values) if len(values) >= 2 else None


@dataclass(frozen=True)
class HoldoutSpend:
    """A `holdout` trial of the family, whatever its outcome."""

    trial_id: int
    hypothesis_id: int
    slug: str
    started_at: datetime
    synthetic: bool
    holdout_reason: str | None
    status: str


@dataclass(frozen=True)
class TrialSummary:
    """One listed trial; `status` is `unfinished` when it has no result row."""

    trial_id: int
    hypothesis_id: int
    slug: str
    family: str
    kind: str
    started_at: datetime
    start_session: date
    end_session: date
    synthetic: bool
    holdout_repeat: bool
    run_by: str
    note: str | None
    status: str
    message: str | None
    finished_at: datetime | None


# --- helpers ---------------------------------------------------------------


def canonical_params_json(params: Mapping[str, Any]) -> str:
    """The one text form of a parameter mapping: sorted keys, no spaces."""
    return json.dumps(dict(params), sort_keys=True, separators=(",", ":"), allow_nan=False)


def params_sha256(params: Mapping[str, Any]) -> str:
    """SHA-256 hex digest of `canonical_params_json(params)`."""
    return sha256(canonical_params_json(params).encode()).hexdigest()


def code_version(repo_dir: Path | None = None) -> tuple[str, bool | None]:
    """`(commit, dirty)` of the git checkout holding `repo_dir` (default: this
    module's own checkout); `("unknown", None)` outside a checkout. Untracked
    files count as dirty: an uncommitted module changes what runs. Assumes
    the editable install `uv sync` makes; a non-editable install inside a
    checkout would report that checkout's commit, not the installed code."""
    cwd = repo_dir if repo_dir is not None else Path(__file__).resolve().parent
    try:
        head = _git(cwd, "rev-parse", "HEAD")
        status = _git(cwd, "status", "--porcelain")
    except (OSError, subprocess.CalledProcessError):
        return "unknown", None
    return head.strip(), bool(status.strip())


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


def store_max_ingested_at(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    """The latest `ingested_at` over every fact table; None on an empty store."""
    union = " UNION ALL ".join(
        f"SELECT MAX(ingested_at) AS m FROM {table}" for table in TABLE_PROVENANCE_VALUES
    )
    row = conn.execute(f"SELECT MAX(m) FROM ({union})").fetchone()
    return row[0] if row is not None else None


def _database_path(conn: duckdb.DuckDBPyConnection) -> str | None:
    row = conn.execute(
        "SELECT path FROM duckdb_databases() WHERE database_name = current_database()"
    ).fetchone()
    return str(Path(row[0]).resolve()) if row is not None and row[0] else None


def _is_real_store(conn: duckdb.DuckDBPyConnection, settings: Settings) -> bool:
    """Whether `conn`'s file is `settings.store.path`, by file identity when
    both exist (a hard link resolves to its own path), else by path."""
    path = _database_path(conn)
    if path is None:
        return False
    real = Path(settings.store.path).expanduser()
    if real.exists() and Path(path).exists():
        return os.path.samefile(path, real)
    return path == str(real.resolve())


def _next_id(conn: duckdb.DuckDBPyConnection, table: str, column: str) -> int:
    row = conn.execute(f"SELECT COALESCE(MAX({column}), 0) + 1 FROM {table}").fetchone()
    assert row is not None
    return int(row[0])


def _has_result(conn: duckdb.DuckDBPyConnection, trial_id: int) -> bool:
    return (
        conn.execute("SELECT 1 FROM trial_results WHERE trial_id = ?", [trial_id]).fetchone()
        is not None
    )


def _check_trial_row(conn: duckdb.DuckDBPyConnection, handle: TrialHandle) -> None:
    """Refuse a handle whose `trials` row is not in this store: another
    store's handle, or one whose open was rolled back (its id may since
    belong to another trial, so the row must match `started_at` too)."""
    if handle.database != _database_path(conn):
        raise RegistryError(
            f"trial {handle.trial_id} was opened on another store ({handle.database!r})"
        )
    row = conn.execute(
        "SELECT started_at FROM trials WHERE trial_id = ?", [handle.trial_id]
    ).fetchone()
    if row is None or row[0] != handle.started_at:
        raise RegistryError(
            f"trial {handle.trial_id} has no matching trials row in this store "
            "(was its open_trial rolled back?)"
        )


def _check_open(conn: duckdb.DuckDBPyConnection, handle: TrialHandle) -> None:
    """Refuse a handle without its `trials` row, or whose trial is closed."""
    _check_trial_row(conn, handle)
    if _has_result(conn, handle.trial_id):
        raise TrialAlreadyClosed(f"trial {handle.trial_id} already has its result row")


def _first_rebalance_on_or_after(day: date) -> date:
    """The first last-session-of-month on or after `day` (ADR 0006)."""
    month_end = last_session_of_month(day.year, day.month)
    if month_end >= day:
        return month_end
    year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
    return last_session_of_month(year, month)


# --- hypotheses ------------------------------------------------------------


def register_hypothesis(
    conn: duckdb.DuckDBPyConnection,
    *,
    slug: str,
    family: str,
    title: str,
    doc_path: str,
    doc_sha256: str,
    params: Mapping[str, Any],
    in_sample_start: date,
    holdout_start: date,
    holdout_end: date,
    registered_by: str,
    settings: Settings | None = None,
) -> HypothesisRecord:
    """Register a hypothesis and return its record.

    The same slug, file hash and parameter hash return the existing record
    instead of a second row, so re-registering an unchanged file cannot
    make a new hypothesis; its family and window must then match the stored
    row. A changed file or parameter set is a new one, in the slug's
    original family. Refuses a family outside `settings.hypotheses.families`,
    parameters without a numeric `costs.per_side_bps`, `holdout.start`,
    `holdout.end` or `in_sample_start` params that disagree with the window
    arguments, and `family="oracle"` on the real store.
    """
    settings = settings if settings is not None else get_settings()
    if family not in settings.hypotheses.families:
        raise RegistryError(
            f"family {family!r} is not in hypotheses.families {settings.hypotheses.families}"
        )
    if family == ORACLE_FAMILY and _is_real_store(conn, settings):
        raise RealStoreRefused(f"family {ORACLE_FAMILY!r} is refused on the real store")
    base = params.get(BASE_COST_KEY)
    if isinstance(base, bool) or not isinstance(base, int | float) or not math.isfinite(base):
        raise RegistryError(f"params must name a finite numeric {BASE_COST_KEY!r}")
    window = {
        "in_sample_start": in_sample_start,
        "holdout_start": holdout_start,
        "holdout_end": holdout_end,
    }
    for key, column in _WINDOW_PARAM_KEYS.items():
        if key in params and params[key] != window[column].isoformat():
            raise RegistryError(
                f"params {key}={params[key]!r} disagrees with {column}={window[column]}"
            )
    families = conn.execute(
        "SELECT DISTINCT family FROM hypotheses WHERE slug = ?", [slug]
    ).fetchall()
    if families and families != [(family,)]:
        raise RegistryError(
            f"slug {slug!r} is registered in family {families[0][0]!r}; "
            f"it cannot move to {family!r}"
        )
    params_json = canonical_params_json(params)
    params_hash = params_sha256(params)
    existing = conn.execute(
        "SELECT hypothesis_id FROM hypotheses "
        "WHERE slug = ? AND doc_sha256 = ? AND params_sha256 = ? ORDER BY hypothesis_id",
        [slug, doc_sha256, params_hash],
    ).fetchone()
    if existing is not None:
        record = get_hypothesis_by_id(conn, existing[0])
        for column, value in window.items():
            if getattr(record, column) != value:
                raise RegistryError(
                    f"{slug!r} is registered with {column}={getattr(record, column)}, "
                    f"not {value}, for the same file and parameters"
                )
        return record
    hypothesis_id = _next_id(conn, "hypotheses", "hypothesis_id")
    insert_row(
        conn,
        "hypotheses",
        {
            "hypothesis_id": hypothesis_id,
            "slug": slug,
            "family": family,
            "title": title,
            "doc_path": doc_path,
            "doc_sha256": doc_sha256,
            "params_json": params_json,
            "params_sha256": params_hash,
            "in_sample_start": in_sample_start,
            "holdout_start": holdout_start,
            "holdout_end": holdout_end,
            "registered_at": utc_now(),
            "registered_by": registered_by,
        },
    )
    return get_hypothesis_by_id(conn, hypothesis_id)


def get_hypothesis(conn: duckdb.DuckDBPyConnection, slug: str) -> HypothesisRecord:
    """The latest registration of `slug`; `UnknownHypothesis` if there is none."""
    return _hypothesis_where(
        conn,
        "hypothesis_id = (SELECT MAX(hypothesis_id) FROM hypotheses WHERE slug = ?)",
        slug,
        missing=f"no hypothesis is registered as {slug!r}",
    )


def get_hypothesis_by_id(conn: duckdb.DuckDBPyConnection, hypothesis_id: int) -> HypothesisRecord:
    """The registration with `hypothesis_id`; `UnknownHypothesis` if none."""
    return _hypothesis_where(
        conn,
        "hypothesis_id = ?",
        hypothesis_id,
        missing=f"no hypothesis has id {hypothesis_id}",
    )


_HYPOTHESIS_COLUMNS: Final = tuple(f.name for f in fields(HypothesisRecord))

#: Params keys that restate a window column; when present they must agree.
_WINDOW_PARAM_KEYS: Final = {
    "in_sample_start": "in_sample_start",
    "holdout.start": "holdout_start",
    "holdout.end": "holdout_end",
}


def _hypothesis_where(
    conn: duckdb.DuckDBPyConnection, where: str, value: object, missing: str = ""
) -> HypothesisRecord:
    columns = ", ".join("params_json" if c == "params" else c for c in _HYPOTHESIS_COLUMNS)
    row = conn.execute(f"SELECT {columns} FROM hypotheses WHERE {where}", [value]).fetchone()
    if row is None:
        raise UnknownHypothesis(missing)
    values = dict(zip(_HYPOTHESIS_COLUMNS, row, strict=True))
    values["params"] = json.loads(values["params"])
    return HypothesisRecord(**values)


# --- trials ----------------------------------------------------------------


def open_trial(
    conn: duckdb.DuckDBPyConnection,
    *,
    hypothesis_id: int,
    kind: TrialKind,
    start_session: date,
    end_session: date,
    data_cutoff: datetime | None,
    synthetic: bool,
    run_by: str,
    holdout_repeat: bool = False,
    holdout_reason: str | None = None,
    gap_override_reason: str | None = None,
    note: str | None = None,
    settings: Settings | None = None,
    repo_dir: Path | None = None,
) -> TrialHandle:
    """Insert a `trials` row for hypothesis `hypothesis_id` and return its
    handle. This is the moment a run becomes a trial (spec "Definitions").
    The caller names the exact registration (the one matching the file it
    loaded), never "the latest for a slug", so a run is recorded under the
    parameters it runs; the handle carries their `params_sha256`.

    `start_session`/`end_session` are the requested window as given, and
    `data_cutoff` may be None when the requested end has no session close;
    the window rules are checked afterwards (spec req 11), so a refusal is
    still a trial. Captures the code version (`repo_dir`, default this
    checkout) and the store's latest `ingested_at`. Refuses
    `synthetic=True` on the real store. Commit it in its own write chunk
    (module docstring, "Trial handle").
    """
    settings = settings if settings is not None else get_settings()
    hypothesis = get_hypothesis_by_id(conn, hypothesis_id)
    if synthetic and _is_real_store(conn, settings):
        raise RealStoreRefused("a synthetic trial is refused on the real store")
    version, dirty = code_version(repo_dir)
    max_ingested = store_max_ingested_at(conn)
    trial_id = max(
        _next_id(conn, "trials", "trial_id"), _next_id(conn, "trial_results", "trial_id")
    )
    started_at = utc_now()
    insert_row(
        conn,
        "trials",
        {
            "trial_id": trial_id,
            "hypothesis_id": hypothesis.hypothesis_id,
            "kind": kind,
            "started_at": started_at,
            "start_session": start_session,
            "end_session": end_session,
            "data_cutoff": data_cutoff,
            "store_max_ingested_at": max_ingested,
            "code_version": version,
            "code_dirty": dirty,
            "synthetic": synthetic,
            "holdout_repeat": holdout_repeat,
            "holdout_reason": holdout_reason,
            "gap_override_reason": gap_override_reason,
            "run_by": run_by,
            "note": note,
        },
    )
    return _issue_handle(
        trial_id=trial_id,
        hypothesis_id=hypothesis.hypothesis_id,
        family=hypothesis.family,
        params_sha256=hypothesis.params_sha256,
        kind=kind,
        synthetic=synthetic,
        started_at=started_at,
        store_max_ingested_at=max_ingested,
        database=_database_path(conn),
    )


def close_trial(
    conn: duckdb.DuckDBPyConnection,
    handle: TrialHandle,
    status: str,
    message: str | None = None,
) -> None:
    """Record a non-`ok` outcome (`failed` or a refusal) with no statistics."""
    if status == "ok":
        raise ValueError("an ok outcome is recorded by write_result, with its statistics")
    if status not in CLOSE_STATUSES:
        raise ValueError(f"status {status!r} is not one of {sorted(CLOSE_STATUSES)}")
    _check_open(conn, handle)
    _insert_result(conn, handle, status, message, ResultStatistics())


def write_result(
    conn: duckdb.DuckDBPyConnection,
    handle: TrialHandle,
    stats: ResultStatistics,
    message: str | None = None,
) -> str:
    """Record an `ok` outcome with `stats` and return `"ok"`; or, when the
    store's latest `ingested_at` moved since `open_trial`, record `failed`
    with `STORE_CHANGED_MESSAGE` and no statistics and return `"failed"`.
    Refuses a trial opened without a `data_cutoff`: only refusals lack one."""
    _check_open(conn, handle)
    cutoff = conn.execute(
        "SELECT data_cutoff FROM trials WHERE trial_id = ?", [handle.trial_id]
    ).fetchone()
    if cutoff is None or cutoff[0] is None:
        raise RegistryError(
            f"trial {handle.trial_id} has no data_cutoff; an ok run needs its resolved end "
            "(it keys the trial's V pair)"
        )
    if store_max_ingested_at(conn) != handle.store_max_ingested_at:
        _insert_result(conn, handle, "failed", STORE_CHANGED_MESSAGE, ResultStatistics())
        return "failed"
    _insert_result(conn, handle, "ok", message, stats)
    return "ok"


def _insert_result(
    conn: duckdb.DuckDBPyConnection,
    handle: TrialHandle,
    status: str,
    message: str | None,
    stats: ResultStatistics,
) -> None:
    row: dict[str, Any] = {
        "trial_id": handle.trial_id,
        "finished_at": utc_now(),
        "status": status,
        "message": message,
        **{f.name: getattr(stats, f.name) for f in fields(stats)},
    }
    insert_row(conn, "trial_results", row)


# --- detail rows -----------------------------------------------------------


def write_metrics(
    conn: duckdb.DuckDBPyConnection, handle: TrialHandle, rows: Iterable[MetricRow]
) -> None:
    """Append `trial_metrics` rows for an open trial."""
    _write_rows(conn, handle, "trial_metrics", MetricRow, rows)


def write_equity(
    conn: duckdb.DuckDBPyConnection, handle: TrialHandle, rows: Iterable[EquityRow]
) -> None:
    """Append `trial_equity` rows for an open trial."""
    _write_rows(conn, handle, "trial_equity", EquityRow, rows)


def write_weights(
    conn: duckdb.DuckDBPyConnection, handle: TrialHandle, rows: Iterable[WeightRow]
) -> None:
    """Append base-level `trial_weights` rows for an open trial."""
    _write_rows(conn, handle, "trial_weights", WeightRow, rows)


def write_rebalances(
    conn: duckdb.DuckDBPyConnection, handle: TrialHandle, rows: Iterable[RebalanceRow]
) -> None:
    """Append `trial_rebalances` rows for an open trial."""
    _write_rows(conn, handle, "trial_rebalances", RebalanceRow, rows)


def _write_rows(
    conn: duckdb.DuckDBPyConnection,
    handle: TrialHandle,
    table: str,
    row_type: type[Any],
    rows: Iterable[Any],
) -> None:
    """Bulk-append typed rows through one Arrow table (thousands of equity
    rows per trial). Date fields must be dates, not datetimes, which DuckDB
    would silently truncate."""
    _check_open(conn, handle)
    names = [f.name for f in fields(row_type)]
    date_names = {f.name for f in fields(row_type) if f.type == "date"}
    columns: dict[str, list[Any]] = {name: [] for name in ["trial_id", *names]}
    for row in rows:
        for name, value in zip(names, astuple(row), strict=True):
            if name in date_names and (not isinstance(value, date) or isinstance(value, datetime)):
                raise TypeError(f"{table}.{name} must be a date, got {value!r}")
            columns[name].append(value)
        columns["trial_id"].append(handle.trial_id)
    if not columns["trial_id"]:
        return
    conn.register("_registry_rows", pa.table(columns))
    try:
        conn.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _registry_rows")
    finally:
        conn.unregister("_registry_rows")


# --- owner decisions -------------------------------------------------------


def record_decision(
    conn: duckdb.DuckDBPyConnection,
    *,
    kind: Literal["gap_signoff", "gap_override", "holdout_spend"],
    reason: str,
    values: Mapping[str, Any],
    hypothesis_id: int | None = None,
    trial_id: int | None = None,
) -> int:
    """Append an `owner_decisions` row (spec req 12) and return its id.
    `values` (the gap values at the time, say) are stored as canonical JSON.
    Refuses a blank reason and a trial or hypothesis id that does not exist."""
    if not reason.strip():
        raise ValueError("an owner decision needs a reason")
    for table, column, value in (
        ("trials", "trial_id", trial_id),
        ("hypotheses", "hypothesis_id", hypothesis_id),
    ):
        if value is not None and (
            conn.execute(f"SELECT 1 FROM {table} WHERE {column} = ?", [value]).fetchone() is None
        ):
            raise RegistryError(f"{column.split('_')[0]} {value} does not exist")
    decision_id = _next_id(conn, "owner_decisions", "decision_id")
    insert_row(
        conn,
        "owner_decisions",
        {
            "decision_id": decision_id,
            "made_at": utc_now(),
            "kind": kind,
            "hypothesis_id": hypothesis_id,
            "trial_id": trial_id,
            "values_json": canonical_params_json(values),
            "reason": reason,
        },
    )
    return decision_id


# --- reads -----------------------------------------------------------------


def family_sharpes(
    conn: duckdb.DuckDBPyConnection, family: str, pending: TrialHandle | None = None
) -> FamilySharpes:
    """N and the per-basis Sharpes for V over `family` (module docstring).

    `pending` counts a trial that has its metrics but no result row yet as
    `ok`, when it is itself a non-synthetic `in_sample` trial of `family`
    and its `trials` row is in this store. Raises `RegistryError` when a
    counted trial lacks a finite base-level Sharpe.
    """
    if pending is not None:
        _check_trial_row(conn, pending)
    counted = conn.execute(
        "SELECT t.trial_id, h.params_json, h.params_sha256, t.start_session, t.data_cutoff "
        "FROM trials t JOIN hypotheses h USING (hypothesis_id) "
        "LEFT JOIN trial_results r USING (trial_id) "
        "WHERE h.family = ? AND t.kind = 'in_sample' AND NOT t.synthetic "
        "AND (r.status = 'ok' OR (r.trial_id IS NULL AND t.trial_id = ?)) "
        "ORDER BY t.trial_id",
        [family, pending.trial_id if pending is not None else None],
    ).fetchall()
    latest: dict[tuple[str, date, datetime | None], tuple[int, float]] = {}
    for trial_id, params_json, params_hash, start, cutoff in counted:
        base = float(json.loads(params_json)[BASE_COST_KEY])
        latest[(params_hash, _first_rebalance_on_or_after(start), cutoff)] = (trial_id, base)
    chosen = sorted(latest.values())
    return FamilySharpes(
        n_trials=len(counted),
        raw=_base_sharpes(conn, chosen, "raw"),
        excess_spy=_base_sharpes(conn, chosen, "excess_spy"),
    )


def _base_sharpes(
    conn: duckdb.DuckDBPyConnection, trials: Sequence[tuple[int, float]], basis: Basis
) -> tuple[float, ...]:
    metric = _BASIS_METRICS[basis]
    values: list[float] = []
    for trial_id, base in trials:
        row = conn.execute(
            "SELECT value FROM trial_metrics WHERE trial_id = ? AND series = 'strategy' "
            "AND metric = ? AND cost_per_side_bps = ?",
            [trial_id, metric, base],
        ).fetchone()
        if row is None or row[0] is None or not math.isfinite(row[0]):
            raise RegistryError(f"trial {trial_id} has no finite base-level {metric} for strategy")
        values.append(float(row[0]))
    return tuple(values)


def family_holdout_spends(conn: duckdb.DuckDBPyConnection, family: str) -> list[HoldoutSpend]:
    """Every `holdout` trial in `family`, oldest first, whatever its outcome:
    a holdout run that failed, crashed or was refused still counts as a
    spend (conservative; domain rule 3). A caller that reads spends to set
    `holdout_repeat` does so in the same write chunk as its `open_trial`."""
    rows = conn.execute(
        "SELECT t.trial_id, t.hypothesis_id, h.slug, t.started_at, t.synthetic, "
        f"t.holdout_reason, COALESCE(r.status, '{UNFINISHED}') "
        "FROM trials t JOIN hypotheses h USING (hypothesis_id) "
        "LEFT JOIN trial_results r USING (trial_id) "
        "WHERE h.family = ? AND t.kind = 'holdout' ORDER BY t.trial_id",
        [family],
    ).fetchall()
    return [HoldoutSpend(*row) for row in rows]


def list_trials(
    conn: duckdb.DuckDBPyConnection,
    slug: str | None = None,
    include_synthetic: bool = False,
) -> list[TrialSummary]:
    """Trials newest first, optionally for one slug; synthetic hidden unless
    asked for. A trial without a result row has status `unfinished`."""
    rows = conn.execute(
        "SELECT t.trial_id, t.hypothesis_id, h.slug, h.family, t.kind, t.started_at, "
        "t.start_session, t.end_session, t.synthetic, t.holdout_repeat, t.run_by, t.note, "
        f"COALESCE(r.status, '{UNFINISHED}'), r.message, r.finished_at "
        "FROM trials t JOIN hypotheses h USING (hypothesis_id) "
        "LEFT JOIN trial_results r USING (trial_id) "
        "WHERE (? IS NULL OR h.slug = ?) AND (? OR NOT t.synthetic) "
        "ORDER BY t.trial_id DESC",
        [slug, slug, include_synthetic],
    ).fetchall()
    return [TrialSummary(*row) for row in rows]
