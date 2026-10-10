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
records `ok` with its statistics, unless the data vintage at the trial's
cutoff differs from the one `open_trial` captured (spec req 9 as amended,
#1232; the store's latest `ingested_at` for a trial opened without one): then it
records `failed` with `STORE_CHANGED_MESSAGE` instead and returns `"failed"`.
It returns rather than raises so the row survives the caller's commit.
Detail rows (`write_metrics`, `write_equity`, `write_weights`,
`write_rebalances`) are refused once a trial has its result row.

**Deflated Sharpe inputs** (spec req 8; strategy-lab spec reqs 3, 9).
`family_sharpes` takes the `ok`, non-synthetic, `in_sample` trials of a
family and returns, per basis, the **annualised** Sharpe (`sharpe_period`
or `sharpe_period_excess_spy` times the square root of the trial's own
`periods_per_year`) of the latest such trial (highest id) per distinct
(canonical frozen set hash, window) pair, read from the base-level
`strategy` rows of `trial_metrics` in one set-based query per basis. The
pair key hashes `frozen.canonical_frozen_set` of the stored parameters, so
two registrations whose stored sets differ only in a default-valued table
key (`schedule.*`, say) are one pair. The window is the **resolved** one:
the first rebalance session at the hypothesis's frozen cadence (read through
`frozen.frozen_values`: the last XNYS session of a month, ADR 0006, of an ISO
week, or every session; strategy-lab plan T110) on or after the requested
start, and `data_cutoff`, so requested dates that
resolve to the same sessions are one pair and cannot shrink V. Only the
period keys and `periods_per_year` are read, never a `*_monthly` key. A
trial still being written can be counted as the latest of its pair with
`pending=`, so a run's own V includes it. N itself is
`backtest.results.family_n`, the one place it is computed.

**Vintages** (strategy-lab spec, Definitions "Vintage"). `open_trial`
records on every trial its `data_vintage` (`data_vintage(conn, cutoff)`,
the latest `ingested_at` over fact rows known at its data cutoff),
`code_tree_sha256` (`code_tree_sha256(repo_dir)`, over the git-tracked
`*.py` files under `src/tradepartner/` and `uv.lock`) and its `detail_level`
(`full` unless the caller opens a sweep variant at `summary`, #1197); every
result row records `sharpe_unit = annual` (V and SR* in annual units, req 9;
NULL on rows written before schema version 15 means `monthly`). These two
functions are the vintages' one home.

**Named data releases** (#1319, data-foundation plan T140b; runbook
`docs/runbooks/data-releases.md`). A release is `owner_decisions` rows of kind
`data_release` (schema version 18) whose `values_json` carries the runbook's
fields: a `before` row (`open_data_release`), its `after` row
(`close_data_release`, with the sessions the repair touched), or a closed
`record` row naming the backup that holds a trial's state
(`record_trial_state`). At most one release is open (`open_release`); a name is
used once. `data_vintage` also takes the `made_at` of every command-written
`after` row whose `sessions_from` is on or before the cutoff, so a release that
only deletes rows stales the trials whose window it touched. `plan_release_import`
and `import_release` read the hand-written `data/releases.toml` once; imported
`after` rows move no vintage (`_release_vintage`).

**The development boundary** (ADR 0016 points 1, 2 and 6; data-foundation plan
T142b). `development_boundary` reads the newest `development_boundary` row by
`made_at`; `write_development_boundary` is its one writer (`record_decision`
refuses the kind), and refuses a date on or after the `holdout.start` of any
registered non-oracle family (spent, unspent or forward) or on or before any such
family's `in_sample_start` (`BoundaryRefused`). `open_trial` records the boundary
in force on every trial (`trials.development_boundary`, NULL when there is none).
`family_registered_on` is the day a family was first registered, from which a
holdout is forward (`holdout.is_forward`).

**The shakedown decisions** (ADR 0017 part E; paper-trading plan T157; schema
version 20). The owner opens the shakedown span with a `shakedown_span` row whose
`values_json` holds the two thresholds, `sessions` (N) and `order_sessions` (M),
so the bar is read from the row, never from live settings; a new row restarts the
span, and `shakedown_span` reads the newest by `made_at` (then by id). A
`shakedown_note` row names one existing `alerts` row (`alert_id`) the owner has
explained. Both go through `record_decision`, which checks their values.
"""

from __future__ import annotations

import json
import math
import os
import re
import statistics
import subprocess
import tomllib
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import astuple, dataclass, field, fields
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Final, Literal

import duckdb
import pyarrow as pa

from tradepartner.backtest.frozen import canonical_frozen_set, frozen_values
from tradepartner.calendar import (
    all_sessions,
    is_session,
    last_session_of_month,
    last_session_of_week,
    session_close,
)
from tradepartner.config import Cadence, Settings, get_settings
from tradepartner.store.db import configure_connection, insert_row, utc_now
from tradepartner.store.schema import (
    REBALANCE_COUNT_COLUMNS,
    REBALANCE_COUNTS_TABLE_NAME,
    TABLE_PROVENANCE_VALUES,
)

BASE_COST_KEY: Final = "costs.per_side_bps"
ORACLE_FAMILY: Final = "oracle"
STORE_CHANGED_MESSAGE: Final = "store changed during run"
UNFINISHED: Final = "unfinished"

TrialKind = Literal["in_sample", "holdout", "tracking"]

#: `owner_decisions.kind` values `record_decision` writes: the Phase 3 three, then
#: version 18's two (#1319, T140b; `lab_schema.RELEASE_DECISION_KINDS`) and version
#: 20's two (#1388, T157; `lab_schema.SHAKEDOWN_DECISION_KINDS`). The lab's
#: `promotion` and `sweep_retired` are written by `store.lab_registry`.
DecisionKind = Literal[
    "gap_signoff",
    "gap_override",
    "holdout_spend",
    "data_release",
    "development_boundary",
    "shakedown_span",
    "shakedown_note",
    "operations_book",
]
Basis = Literal["raw", "excess_spy"]

#: Outcomes `close_trial` records; `ok` goes through `write_result`.
#: `refused_variant` (strategy-lab spec req 5(a)) is accepted only by a store whose
#: `trial_results.status` CHECK the lab schema rebuilt; no other store has a variant.
CLOSE_STATUSES: Final = frozenset(
    {"failed", "refused_window", "refused_holdout", "refused_gap", "refused_variant"}
)

_BASIS_METRICS: Final[dict[Basis, str]] = {
    "raw": "sharpe_period",
    "excess_spy": "sharpe_period_excess_spy",
}

#: The frozen key the window key's rebalance schedule comes from (through `frozen_values`).
CADENCE_KEY: Final = "schedule.rebalance_cadence"

#: The metric row each trial's annualisation reads (strategy-lab spec req 8).
PERIODS_PER_YEAR_METRIC: Final = "periods_per_year"

#: `trials.detail_level` (strategy-lab spec, "Detail level"): `full` for a standalone
#: hypothesis, the sweep's `lab.sweep_detail_level` for a sweep variant (#1197).
DetailLevel = Literal["full", "summary"]
DETAIL_LEVEL_FULL: Final = "full"
DETAIL_LEVELS: Final[tuple[DetailLevel, ...]] = ("full", "summary")

#: `trial_results.sharpe_unit` on every row written from schema version 15 on.
SHARPE_UNIT_ANNUAL: Final = "annual"

#: What `code_tree_sha256` hashes besides the package's `*.py` files.
_CODE_TREE_PACKAGE: Final = "src/tradepartner/"
_CODE_TREE_LOCK: Final = "uv.lock"


class RegistryError(RuntimeError):
    """A registry call that would break a registry rule."""


class RealStoreRefused(RegistryError):
    """An oracle hypothesis or synthetic trial aimed at `settings.store.path`."""


class UnmarkedStoreRefused(RegistryError):
    """A `store_path` run aimed at a store that is neither `settings.store.path` nor
    carries the `store_markers` `fixture` row: a copy of the real store, which could
    host an uncounted run (strategy-lab spec, Definitions, Fixture marker)."""


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
        "data_vintage",
        "database",
        "detail_level",
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
    detail_level: DetailLevel
    data_vintage: datetime | None
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
    """One `trial_rebalances` row (spec req 5 counts) and its plan's counts.

    `counts` is the plan's count mapping, name to value, whole (ADR 0014
    point 3; #1153, T127): `write_rebalances` writes it to
    `trial_rebalance_counts` once per rebalance, at the trial's base cost
    level, and fills each fixed count column (`schema.REBALANCE_COUNT_COLUMNS`:
    `n_excluded_no_history` and T85d's six `profitability` columns) from it by
    name on every level's row, NULL when absent, so both reads agree
    (`columns`)."""

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
    n_dropped_dividends: int
    n_late_dividends: int
    counts: Mapping[str, int] = field(default_factory=dict)

    def columns(self) -> dict[str, Any]:
        """The `trial_rebalances` row this writes: every field but `counts`, then
        each `schema.REBALANCE_COUNT_COLUMNS` column from `counts` by name, None
        (NULL) when the plan does not report it."""
        values = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "counts"}
        return values | {name: self.counts.get(name) for name in REBALANCE_COUNT_COLUMNS}


@dataclass(frozen=True)
class FamilySharpes:
    """The count of the family's counted trials and the per-basis annualised Sharpes
    V is taken over (strategy-lab spec req 9). The DSR's N is `results.family_n`."""

    n_trials: int
    raw: tuple[float, ...]
    excess_spy: tuple[float, ...]

    def variance(self, basis: Basis) -> float | None:
        """Sample variance of `basis`'s Sharpes; None with fewer than two pairs."""
        values = self.raw if basis == "raw" else self.excess_spy
        return statistics.variance(values) if len(values) >= 2 else None


@dataclass(frozen=True)
class HoldoutSpend:
    """A `holdout` trial of the family, whatever its outcome, or (`source =
    "research_run"`) a research run of the family that spent one of its
    hypotheses' holdouts: then `trial_id` is the run id, `hypothesis_id` is
    None, `slug` is the experiment's and `status` its outcome."""

    trial_id: int
    hypothesis_id: int | None
    slug: str
    started_at: datetime
    synthetic: bool
    holdout_reason: str | None
    status: str
    source: Literal["trial", "research_run"] = "trial"


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


def code_tree_sha256(repo_dir: Path | None = None) -> str | None:
    """The code vintage (strategy-lab spec, Definitions "Vintage"): SHA-256 over the
    git-tracked `*.py` files under `src/tradepartner/` plus `uv.lock` of the checkout
    holding `repo_dir` (default: this module's own), each as its path and the SHA-256
    of its working-tree bytes, in path order. A docs or test change, or an untracked
    `__pycache__` file, leaves it unchanged; any change to a tracked source file under
    the package, committed or not, or a dependency bump changes it. An untracked `.py`
    file is outside the hash (the spec's git-tracked set); `code_dirty` flags it. None
    outside a checkout."""
    cwd = repo_dir if repo_dir is not None else Path(__file__).resolve().parent
    try:
        root = Path(_git(cwd, "rev-parse", "--show-toplevel").strip())
        listed = _git(root, "ls-files", "-z", "--", _CODE_TREE_PACKAGE, _CODE_TREE_LOCK)
    except (OSError, subprocess.CalledProcessError):
        return None
    paths = sorted(
        path
        for path in listed.split("\0")
        if path == _CODE_TREE_LOCK or (path.startswith(_CODE_TREE_PACKAGE) and path.endswith(".py"))
    )
    lines = []
    for path in paths:
        file = root / path
        content = sha256(file.read_bytes()).hexdigest() if file.is_file() else "missing"
        lines.append(f"{path}\0{content}\n")
    return sha256("".join(lines).encode()).hexdigest()


def data_vintage(conn: duckdb.DuckDBPyConnection, cutoff: datetime) -> datetime | None:
    """The data vintage at `cutoff` (strategy-lab spec, Definitions "Vintage"): the
    latest of the `ingested_at` over every fact table's rows with `known_at <=
    cutoff` and the `made_at` of every closed data release whose touched sessions
    start on or before `cutoff` (`_release_vintage`, #1319 T140b); None when there
    is neither. A nightly ingest that only adds later sessions leaves it
    unchanged; a late fact for an in-window session changes it, and so does a
    release that only deletes rows. Guarded against an absent table as
    `store_max_ingested_at` is."""
    if cutoff.tzinfo is None:
        raise ValueError(f"cutoff must be timezone-aware, got {cutoff!r}")
    tables = _present_fact_tables(conn)
    facts: datetime | None = None
    if tables:
        union = " UNION ALL ".join(
            f"SELECT MAX(ingested_at) AS m FROM {table} WHERE known_at <= $cutoff"
            for table in tables
        )
        row = conn.execute(f"SELECT MAX(m) FROM ({union})", {"cutoff": cutoff}).fetchone()
        facts = row[0] if row is not None else None
    release = _release_vintage(conn, cutoff)
    if facts is None or (release is not None and release > facts):
        return release
    return facts


#: The columns schema version 15 adds (`schema._PERIOD_COLUMNS`), by table.
#: `development_boundary` is version 18's (#1319, T140b), absent on the same stores.
_VERSION_15_COLUMNS: Final[dict[str, frozenset[str]]] = {
    "trials": frozenset(
        {"detail_level", "data_vintage", "code_tree_sha256", "development_boundary"}
    ),
    "trial_results": frozenset({"sharpe_unit"}),
}


def _version_15_present(
    conn: duckdb.DuckDBPyConnection, table: str, row: dict[str, Any]
) -> dict[str, Any]:
    """`row` without the version-15 columns `table` does not have yet. Every writing
    command migrates the store first (`schema.init_schema`), so the real store always
    has them; only a store written through the registry without that step (a test's
    version-4 store) lacks them, and its rows then carry none."""
    present = {r[1] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
    absent = _VERSION_15_COLUMNS[table] - present
    return {key: value for key, value in row.items() if key not in absent}


def _present_fact_tables(conn: duckdb.DuckDBPyConnection) -> list[str]:
    present = {
        name for (name,) in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    }
    return [table for table in TABLE_PROVENANCE_VALUES if table in present]


def store_max_ingested_at(conn: duckdb.DuckDBPyConnection) -> datetime | None:
    """The latest `ingested_at` over every fact table; None on an empty store.

    Every caller here opens a writable connection first (a trial needs one
    to record itself), which migrates the store, so every
    `TABLE_PROVENANCE_VALUES` table always exists by the time this runs
    today. Still guarded against a table absent from `conn` (the same
    defensive check `health._count_per_table`/`_bad_provenance` added for
    #660's `statement_facts`), rather than relying on every future caller
    continuing to migrate first.
    """
    tables = _present_fact_tables(conn)
    if not tables:
        return None
    union = " UNION ALL ".join(f"SELECT MAX(ingested_at) AS m FROM {table}" for table in tables)
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


def is_real_store(conn: duckdb.DuckDBPyConnection, settings: Settings) -> bool:
    """Whether `conn`'s database file is `settings.store.path` (module docstring,
    "Real store": file identity, so a hard link is still the real store)."""
    return _is_real_store(conn, settings)


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


def _first_rebalance_on_or_after(day: date, cadence: Cadence) -> date:
    """The first rebalance session at `cadence` on or after `day`: the last session of
    its month (`month_end`, ADR 0006, the Phase 3 rule unchanged), of its ISO week
    (`week_end`) or the session itself (`daily`), all read from the XNYS calendar
    (strategy-lab spec req 6). Raises `ValueError` past the configured calendar."""
    if cadence == "month_end":
        month_end = last_session_of_month(day.year, day.month)
        if month_end >= day:
            return month_end
        year, month = (day.year + 1, 1) if day.month == 12 else (day.year, day.month + 1)
        return last_session_of_month(year, month)
    sessions = all_sessions()
    i = bisect_left(sessions, day)
    if i == len(sessions):
        raise ValueError(f"no XNYS session on or after {day} in the configured calendar")
    first = sessions[i]
    if cadence == "daily":
        return first
    iso_year, iso_week, _ = first.isocalendar()
    return last_session_of_week(iso_year, iso_week)


def _fits_boundary(in_sample_start: date, cadence: Cadence, boundary: date) -> bool:
    """Whether a default in-sample window from `in_sample_start` under `boundary` is
    non-empty: `in_sample_start` is before the boundary and the first rebalance session
    at `cadence` on or after it is on or before the boundary (ADR 0016 points 2, 6)."""
    if in_sample_start >= boundary:
        return False
    try:
        return _first_rebalance_on_or_after(in_sample_start, cadence) <= boundary
    except ValueError:
        return False


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
    arguments, and `family="oracle"` on the real store. With a development boundary
    in force (ADR 0016 point 2), refuses (`BoundaryRefused`) an `in_sample_start` on
    or after it, or one with no rebalance session at the frozen cadence on or before
    it, so no registration's default window is ever empty.
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
    boundary = boundary_date(conn)
    if boundary is not None:
        cadence = frozen_values(_Stored(params, family))[CADENCE_KEY]
        if not _fits_boundary(in_sample_start, cadence, boundary):
            raise BoundaryRefused(
                f"{slug!r}: in_sample_start {in_sample_start} leaves no {cadence} rebalance "
                f"session on or before the development boundary {boundary}"
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
    detail_level: DetailLevel = DETAIL_LEVEL_FULL,
) -> TrialHandle:
    """Insert a `trials` row for hypothesis `hypothesis_id` and return its
    handle. This is the moment a run becomes a trial (spec "Definitions").
    The caller names the exact registration (the one matching the file it
    loaded), never "the latest for a slug", so a run is recorded under the
    parameters it runs; the handle carries their `params_sha256`.

    `start_session`/`end_session` are the requested window as given, and
    `data_cutoff` may be None when the requested end has no session close;
    the window rules are checked afterwards (spec req 11), so a refusal is
    still a trial. Captures the code version and the code vintage
    (`repo_dir`, default this checkout), the store's latest `ingested_at`
    and the data vintage at `data_cutoff`, and records `detail_level`
    (`full` by default; a sweep variant's is the sweep's level, which
    `backtest.results.write_results` then must write; module docstring,
    "Vintages"), and the development boundary in force (`development_boundary`,
    NULL when none; module docstring, "The development boundary"). Refuses an
    unknown detail level and `synthetic=True` on the real store. Commit it in its own write chunk
    (module docstring, "Trial handle").
    """
    if detail_level not in DETAIL_LEVELS:
        raise ValueError(f"detail level must be one of {DETAIL_LEVELS}, got {detail_level!r}")
    settings = settings if settings is not None else get_settings()
    hypothesis = get_hypothesis_by_id(conn, hypothesis_id)
    if synthetic and _is_real_store(conn, settings):
        raise RealStoreRefused("a synthetic trial is refused on the real store")
    version, dirty = code_version(repo_dir)
    max_ingested = store_max_ingested_at(conn)
    vintage = data_vintage(conn, data_cutoff) if data_cutoff is not None else None
    boundary = development_boundary(conn)
    trial_id = max(
        _next_id(conn, "trials", "trial_id"), _next_id(conn, "trial_results", "trial_id")
    )
    started_at = utc_now()
    insert_row(
        conn,
        "trials",
        _version_15_present(
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
                "detail_level": detail_level,
                "data_vintage": vintage,
                "code_tree_sha256": code_tree_sha256(repo_dir),
                "development_boundary": boundary.boundary if boundary is not None else None,
            },
        ),
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
        detail_level=detail_level,
        data_vintage=vintage,
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
    store changed under the run (`_store_changed`), record `failed` with
    `STORE_CHANGED_MESSAGE` and no statistics and return `"failed"`.
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
    if _store_changed(conn, handle, cutoff[0]):
        _insert_result(conn, handle, "failed", STORE_CHANGED_MESSAGE, ResultStatistics())
        return "failed"
    _insert_result(conn, handle, "ok", message, stats)
    return "ok"


def _store_changed(conn: duckdb.DuckDBPyConnection, handle: TrialHandle, cutoff: datetime) -> bool:
    """Backtest spec req 9 as amended (strategy-lab spec amendment 7, #1232): the
    data vintage at the trial's own `cutoff` differs from the one `open_trial`
    captured on `handle`, so a late fact known at or before the cutoff fails the
    run and a row for a session after it fails nothing. A trial with no vintage at
    its open (no fact known at its cutoff then) keeps the Phase 3 rule, the
    stricter one: the store's latest `ingested_at` at open and write."""
    if handle.data_vintage is None:
        return store_max_ingested_at(conn) != handle.store_max_ingested_at
    return data_vintage(conn, cutoff) != handle.data_vintage


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
        "sharpe_unit": SHARPE_UNIT_ANNUAL,
        **{f.name: getattr(stats, f.name) for f in fields(stats)},
    }
    insert_row(conn, "trial_results", _version_15_present(conn, "trial_results", row))


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
    """Append `trial_rebalances` rows for an open trial (`RebalanceRow.columns`
    each), and, for the rows at the trial's base cost level (its hypothesis's
    `costs.per_side_bps`), one `trial_rebalance_counts` row per count name: a
    plan's counts are the same at every level, so they are stored once per
    rebalance, as `trial_weights` is (#1153, T127). Refuses a count that is
    not an integer."""
    rows = list(rows)
    for row in rows:
        for name, value in row.counts.items():
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"rebalance count {name!r} must be an int, got {value!r}")
    _write_values(
        conn,
        handle,
        "trial_rebalances",
        _REBALANCE_COLUMNS,
        _date_fields(RebalanceRow),
        (tuple(row.columns().values()) for row in rows),
    )
    base = _base_level(conn, handle.hypothesis_id)
    _write_values(
        conn,
        handle,
        REBALANCE_COUNTS_TABLE_NAME,
        ("session", "name", "value"),
        {"session"},
        (
            (row.session, name, value)
            for row in rows
            if row.cost_per_side_bps == base
            for name, value in sorted(row.counts.items())
        ),
    )


def rebalance_counts(conn: duckdb.DuckDBPyConnection, trial_id: int) -> dict[date, dict[str, int]]:
    """`trial_id`'s `trial_rebalance_counts`, rebalance session to count name to
    value, sessions ascending and names sorted; empty for a trial with none
    (version 14, #1153, T127)."""
    counts: dict[date, dict[str, int]] = {}
    for session, name, value in conn.execute(
        f"SELECT session, name, value FROM {REBALANCE_COUNTS_TABLE_NAME} "
        "WHERE trial_id = ? ORDER BY session, name",
        [trial_id],
    ).fetchall():
        counts.setdefault(session, {})[name] = value
    return counts


def _base_level(conn: duckdb.DuckDBPyConnection, hypothesis_id: int) -> float:
    """The hypothesis's base cost level, read as `family_sharpes` reads it."""
    row = conn.execute(
        "SELECT params_json FROM hypotheses WHERE hypothesis_id = ?", [hypothesis_id]
    ).fetchone()
    if row is None:
        raise RegistryError(f"hypothesis {hypothesis_id} does not exist")
    return float(json.loads(row[0])[BASE_COST_KEY])


#: `trial_rebalances`' columns in `RebalanceRow.columns` order.
_REBALANCE_COLUMNS: Final = (
    *(f.name for f in fields(RebalanceRow) if f.name != "counts"),
    *REBALANCE_COUNT_COLUMNS,
)


def _date_fields(row_type: type[Any]) -> set[str]:
    return {f.name for f in fields(row_type) if f.type == "date"}


def _write_rows(
    conn: duckdb.DuckDBPyConnection,
    handle: TrialHandle,
    table: str,
    row_type: type[Any],
    rows: Iterable[Any],
) -> None:
    """Bulk-append typed rows (`_write_values`, one tuple per dataclass row)."""
    names = tuple(f.name for f in fields(row_type))
    _write_values(conn, handle, table, names, _date_fields(row_type), map(astuple, rows))


def _write_values(
    conn: duckdb.DuckDBPyConnection,
    handle: TrialHandle,
    table: str,
    names: Sequence[str],
    date_names: set[str],
    rows: Iterable[tuple[Any, ...]],
) -> None:
    """Bulk-append rows of `names`' values through one Arrow table (thousands
    of equity rows per trial). Date columns must be dates, not datetimes,
    which DuckDB would silently truncate."""
    _check_open(conn, handle)
    columns: dict[str, list[Any]] = {name: [] for name in ["trial_id", *names]}
    for row in rows:
        for name, value in zip(names, row, strict=True):
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
    kind: DecisionKind,
    reason: str,
    values: Mapping[str, Any],
    hypothesis_id: int | None = None,
    trial_id: int | None = None,
) -> int:
    """Append an `owner_decisions` row (spec req 12) and return its id.
    `values` (the gap values at the time, say) are stored as canonical JSON.
    Refuses a blank reason and a trial or hypothesis id that does not exist.
    A `data_release` row is refused here: the release writers below are its only
    writers, so every such row has the shape `data_vintage` reads. So is a
    `development_boundary` row: `write_development_boundary` is its one writer, so
    every boundary passes ADR 0016 point 6's refusals. A `shakedown_span` row's
    `values` must be exactly `sessions` and `order_sessions`, whole numbers with
    1 <= order_sessions <= sessions (`ValueError` otherwise); a `shakedown_note`
    row's exactly `alert_id`, naming an existing `alerts` row
    (`ShakedownRefused` for an unknown one; ADR 0017 part E)."""
    if kind == DATA_RELEASE_KIND:
        raise ValueError("a data_release row is written by the release writers only")
    if kind == DEVELOPMENT_BOUNDARY_KIND:
        raise ValueError("a development_boundary row is written by write_development_boundary only")
    if kind == SHAKEDOWN_SPAN_KIND:
        _check_span_values(values)
    if kind == SHAKEDOWN_NOTE_KIND:
        _check_note_values(conn, values)
    return _insert_decision(
        conn,
        kind=kind,
        reason=reason,
        values=values,
        hypothesis_id=hypothesis_id,
        trial_id=trial_id,
        made_at=utc_now(),
    )


def _insert_decision(
    conn: duckdb.DuckDBPyConnection,
    *,
    kind: DecisionKind,
    reason: str,
    values: Mapping[str, Any],
    hypothesis_id: int | None,
    trial_id: int | None,
    made_at: datetime,
) -> int:
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
            "made_at": made_at,
            "kind": kind,
            "hypothesis_id": hypothesis_id,
            "trial_id": trial_id,
            "values_json": canonical_params_json(values),
            "reason": reason,
        },
    )
    return decision_id


# --- named data releases (#1319, data-foundation plan T140b) -----------------

DATA_RELEASE_KIND: Final = "data_release"
DEVELOPMENT_BOUNDARY_KIND: Final = "development_boundary"

#: A release entry's stage (runbook `docs/runbooks/data-releases.md`, "The record").
ReleaseStage = Literal["before", "after", "record"]
RELEASE_STAGES: Final[tuple[ReleaseStage, ...]] = ("before", "after", "record")

#: A release name: lowercase letters, digits and single hyphens (runbook).
RELEASE_NAME: Final = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")

#: The `values_json` key every imported row carries (the time of the import).
IMPORTED_AT_KEY: Final = "imported_at"

#: `ingestion_runs.mode` of a repair run (`tradepartner.repair.REPAIR`).
_REPAIR_MODE: Final = "repair"

#: Every key a `data/releases.toml` entry may carry, and the keys each stage
#: requires (runbook, "The fields").
_ENTRY_KEYS: Final = frozenset(
    {
        "name",
        "stage",
        "made_at",
        "backup_path",
        "store_max_ingested_at",
        "data_vintage",
        "cutoff",
        "sessions_from",
        "sessions_to",
        "trial",
        "reason",
    }
)
_COMMON_KEYS: Final = frozenset(_ENTRY_KEYS - {"backup_path", "trial"})
_STAGE_KEYS: Final[dict[str, frozenset[str]]] = {
    "before": _COMMON_KEYS | {"backup_path"},
    "after": _COMMON_KEYS,
    "record": _COMMON_KEYS | {"backup_path", "trial"},
}
_DATETIME_KEYS: Final = ("made_at", "store_max_ingested_at", "data_vintage", "cutoff")
_DATE_KEYS: Final = ("sessions_from", "sessions_to")


class ReleaseRefused(RegistryError):
    """A data-release write the rules refuse: a bad name, a name already used, a
    second open release, a close of a release that is not the open one, a backup
    that is not the store's state, or an import entry that breaks the runbook."""


class BackupRefused(ReleaseRefused):
    """A `--backup` path that is not a file, is the store itself, or cannot be
    opened read-only."""


class ReleaseFileError(ValueError):
    """`data/releases.toml` cannot be read, is not TOML, or is not one or more
    `[[release]]` tables and nothing else."""


def open_backup(path: Path, store_path: Path) -> duckdb.DuckDBPyConnection:
    """A read-only connection to a release's backup file (the caller closes it).
    Refuses a path that is not a file or that is the store itself (file identity,
    so a link to the store is refused too), so no release names the store as its
    own backup."""
    if not path.is_file():
        raise BackupRefused(f"--backup {path} is not a file")
    store = store_path.expanduser()
    if store.exists() and os.path.samefile(path, store):
        raise BackupRefused(f"--backup {path} is the store itself, not a backup")
    try:
        conn = duckdb.connect(str(path), read_only=True)
    except duckdb.Error as exc:
        raise BackupRefused(f"cannot open --backup {path} read-only: {exc}") from None
    configure_connection(conn)
    return conn


def read_release_file(path: Path) -> list[Any]:
    """The `[[release]]` tables of the hand-written `data/releases.toml`, in file
    order, unchecked (`plan_release_import` checks them). Raises
    `ReleaseFileError` for an unreadable or non-TOML file, any other top-level
    key, no entry, or an entry that is not a table."""
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReleaseFileError(f"cannot read {path}: {exc.strerror}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ReleaseFileError(f"{path} is not valid TOML: {exc}") from None
    entries = doc.get("release", [])
    if set(doc) - {"release"} or not isinstance(entries, list) or not entries:
        raise ReleaseFileError(f"{path} must hold one or more [[release]] tables only")
    if not all(isinstance(entry, dict) for entry in entries):
        raise ReleaseFileError(f"{path}: every [[release]] entry must be a table")
    return entries


@dataclass(frozen=True)
class DataRelease:
    """One `data_release` row of `owner_decisions`. `made_at` is the entry's own
    time (the row's write time for a command, the file's `made_at` for an import,
    whose write time is `imported_at`); `values` is the whole `values_json`."""

    decision_id: int
    name: str
    stage: ReleaseStage
    made_at: datetime
    backup_path: str | None
    store_max_ingested_at: datetime | None
    data_vintage: datetime | None
    cutoff: datetime | None
    sessions_from: date | None
    sessions_to: date | None
    trial: int | None
    reason: str
    imported_at: datetime | None
    values: Mapping[str, Any]


@dataclass(frozen=True)
class DevelopmentBoundary:
    """The newest `development_boundary` row (ADR 0016 points 1 and 6)."""

    decision_id: int
    made_at: datetime
    boundary: date
    reason: str


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _optional_datetime(value: Any) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _optional_date(value: Any) -> date | None:
    return None if value is None else date.fromisoformat(value)


def _release(decision_id: int, values_json: str, reason: str) -> DataRelease:
    values = json.loads(values_json)
    return DataRelease(
        decision_id=decision_id,
        name=values["name"],
        stage=values["stage"],
        made_at=datetime.fromisoformat(values["made_at"]),
        backup_path=values.get("backup_path"),
        store_max_ingested_at=_optional_datetime(values.get("store_max_ingested_at")),
        data_vintage=_optional_datetime(values.get("data_vintage")),
        cutoff=_optional_datetime(values.get("cutoff")),
        sessions_from=_optional_date(values.get("sessions_from")),
        sessions_to=_optional_date(values.get("sessions_to")),
        trial=values.get("trial"),
        reason=reason,
        imported_at=_optional_datetime(values.get(IMPORTED_AT_KEY)),
        values=values,
    )


def _has_table(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM duckdb_tables() WHERE database_name = current_database() "
            "AND schema_name = current_schema() AND table_name = ?",
            [table],
        ).fetchone()
        is not None
    )


def data_releases(conn: duckdb.DuckDBPyConnection) -> list[DataRelease]:
    """Every `data_release` row, newest first by the entry's `made_at` (then by
    id); empty on a store without `owner_decisions`."""
    if not _has_table(conn, "owner_decisions"):
        return []
    rows = conn.execute(
        "SELECT decision_id, values_json, reason FROM owner_decisions WHERE kind = ?",
        [DATA_RELEASE_KIND],
    ).fetchall()
    releases = [_release(int(i), str(v), str(r)) for i, v, r in rows]
    return sorted(releases, key=lambda r: (r.made_at, r.decision_id), reverse=True)


def _open_releases(releases: Sequence[DataRelease]) -> list[DataRelease]:
    closed = {r.name for r in releases if r.stage == "after"}
    return [r for r in releases if r.stage == "before" and r.name not in closed]


def open_release(conn: duckdb.DuckDBPyConnection) -> DataRelease | None:
    """The open release's `before` row (a `before` with no `after` of its name), or
    None. `record` rows are never open. The writers keep at most one open."""
    found = _open_releases(data_releases(conn))
    return found[0] if found else None


class BoundaryRefused(RegistryError):
    """A development boundary ADR 0016 point 6 refuses: on or after a registered
    non-oracle family's `holdout.start`, or on or before its `in_sample_start`."""


def development_boundary(conn: duckdb.DuckDBPyConnection) -> DevelopmentBoundary | None:
    """The newest `development_boundary` row by `made_at` (then by id), or None
    (`write_development_boundary` writes them)."""
    if not _has_table(conn, "owner_decisions"):
        return None
    row = conn.execute(
        "SELECT decision_id, made_at, values_json, reason FROM owner_decisions "
        "WHERE kind = ? ORDER BY made_at DESC, decision_id DESC LIMIT 1",
        [DEVELOPMENT_BOUNDARY_KIND],
    ).fetchone()
    if row is None:
        return None
    return DevelopmentBoundary(
        decision_id=int(row[0]),
        made_at=row[1],
        boundary=date.fromisoformat(json.loads(row[2])["date"]),
        reason=str(row[3]),
    )


def boundary_date(conn: duckdb.DuckDBPyConnection) -> date | None:
    """The newest development boundary's date (`development_boundary`), or None."""
    found = development_boundary(conn)
    return found.boundary if found is not None else None


# --- the shakedown decisions (#1388, paper-trading plan T157; ADR 0017 part E) ---

SHAKEDOWN_SPAN_KIND: Final = "shakedown_span"
SHAKEDOWN_NOTE_KIND: Final = "shakedown_note"
_SPAN_KEYS: Final = ("sessions", "order_sessions")
_NOTE_KEYS: Final = ("alert_id",)


class ShakedownRefused(RegistryError):
    """A shakedown decision that names something the store does not hold (an
    unknown alert); nothing was written."""


@dataclass(frozen=True)
class ShakedownSpan:
    """The newest `shakedown_span` row: when the span opened and the two
    thresholds it froze (ADR 0017 part E, "The span")."""

    decision_id: int
    made_at: datetime
    sessions: int
    order_sessions: int
    reason: str


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _check_keys(kind: str, values: Mapping[str, Any], keys: tuple[str, ...]) -> None:
    if set(values) != set(keys):
        raise ValueError(f"a {kind} row's values are exactly {list(keys)}, got {sorted(values)}")


def _check_span_values(values: Mapping[str, Any]) -> None:
    _check_keys(SHAKEDOWN_SPAN_KIND, values, _SPAN_KEYS)
    sessions, order_sessions = values["sessions"], values["order_sessions"]
    if not (_whole(sessions) and _whole(order_sessions)):
        raise ValueError("sessions and order_sessions are whole numbers")
    if not 1 <= order_sessions <= sessions:
        raise ValueError(
            f"order_sessions must be at least 1 and at most sessions "
            f"(got sessions={sessions}, order_sessions={order_sessions})"
        )


def _check_note_values(conn: duckdb.DuckDBPyConnection, values: Mapping[str, Any]) -> None:
    _check_keys(SHAKEDOWN_NOTE_KIND, values, _NOTE_KEYS)
    alert_id = values["alert_id"]
    if not _whole(alert_id):
        raise ValueError("alert_id is a whole number")
    if (
        not _has_table(conn, "alerts")
        or conn.execute("SELECT 1 FROM alerts WHERE alert_id = ?", [alert_id]).fetchone() is None
    ):
        raise ShakedownRefused(f"alert {alert_id} does not exist")


def shakedown_span(conn: duckdb.DuckDBPyConnection) -> ShakedownSpan | None:
    """The newest `shakedown_span` row by `made_at` (then by id), or None. The
    thresholds come from the row, never from settings (ADR 0017 part E)."""
    if not _has_table(conn, "owner_decisions"):
        return None
    row = conn.execute(
        "SELECT decision_id, made_at, values_json, reason FROM owner_decisions "
        "WHERE kind = ? ORDER BY made_at DESC, decision_id DESC LIMIT 1",
        [SHAKEDOWN_SPAN_KIND],
    ).fetchone()
    if row is None:
        return None
    values = json.loads(row[2])
    return ShakedownSpan(
        decision_id=int(row[0]),
        made_at=row[1],
        sessions=int(values["sessions"]),
        order_sessions=int(values["order_sessions"]),
        reason=str(row[3]),
    )


class Unread:
    """The default of a caller's `boundary` argument it did not read from the store:
    the callee reads `boundary_date` itself."""


#: The one `Unread` value.
UNREAD: Final = Unread()


def write_development_boundary(
    conn: duckdb.DuckDBPyConnection, *, boundary: date, reason: str
) -> int:
    """Append a `development_boundary` row (ADR 0016 points 1 and 6) and return its id.

    A new row is the only way the boundary moves; it never edits an older one. Refuses
    (`BoundaryRefused`, nothing written) a boundary on or after the `holdout.start` of
    any registered non-oracle family, spent, unspent or forward, so no exam month can
    become a development month, and a boundary on or before any such family's
    `in_sample_start` or before the first rebalance session at any registration's
    frozen cadence, so every default window keeps a session (the same line
    `register_hypothesis` and `backtest.sweep.register` hold for a new registration).
    A blank reason is refused as for every owner decision. Families are read from
    `hypotheses`, so a store without the lab tables is checked too."""
    rows = conn.execute(
        "SELECT family, MIN(holdout_start), MAX(in_sample_start) FROM hypotheses "
        "WHERE family <> ? GROUP BY family ORDER BY family",
        [ORACLE_FAMILY],
    ).fetchall()
    registered = conn.execute(
        "SELECT slug, family, params_json, in_sample_start FROM hypotheses "
        "WHERE family <> ? ORDER BY hypothesis_id",
        [ORACLE_FAMILY],
    ).fetchall()
    exams = [f"{family} ({start})" for family, start, _ in rows if boundary >= start]
    if exams:
        raise BoundaryRefused(
            f"development boundary {boundary} is on or after the holdout.start of "
            f"{', '.join(exams)}: exam months never become development months"
        )
    early = [f"{family} ({start})" for family, _, start in rows if boundary <= start]
    if early:
        raise BoundaryRefused(
            f"development boundary {boundary} is on or before the in_sample_start of "
            f"{', '.join(early)}: no in-sample session would remain"
        )
    empty = []
    for slug, family, params_json, start in registered:
        cadence = frozen_values(_Stored(json.loads(params_json), family))[CADENCE_KEY]
        if not _fits_boundary(start, cadence, boundary):
            empty.append(f"{slug} ({cadence} from {start})")
    if empty:
        raise BoundaryRefused(
            f"development boundary {boundary} leaves no rebalance session in the default "
            f"window of {', '.join(sorted(set(empty)))}"
        )
    return _insert_decision(
        conn,
        kind=DEVELOPMENT_BOUNDARY_KIND,
        reason=reason,
        values={"date": boundary.isoformat()},
        hypothesis_id=None,
        trial_id=None,
        made_at=utc_now(),
    )


def family_registered_on(conn: duckdb.DuckDBPyConnection, family: str) -> date | None:
    """The UTC day `family` was first registered (its earliest `hypotheses` row), or
    None for a family with no registration: the day a holdout is forward from (ADR
    0016 point 4, `holdout.Frozen.registered_on`)."""
    row = conn.execute(
        "SELECT MIN(registered_at) FROM hypotheses WHERE family = ?", [family]
    ).fetchone()
    first = row[0] if row is not None else None
    return None if first is None else first.astimezone(UTC).date()


def _release_vintage(conn: duckdb.DuckDBPyConnection, cutoff: datetime) -> datetime | None:
    """The latest `made_at` over the `after` rows written by `close_data_release`
    whose `sessions_from` is on or before `cutoff`'s UTC date (a session's close
    falls on its own UTC date), or None. A release that only deletes rows moves no
    `ingested_at`, so this is what stales the trials whose window it touched; a
    trial opened after the close read the new state and captured this value, so
    it stays current. An imported `after` row (`IMPORTED_AT_KEY`) is left out: its
    trials were rerun by hand under the runbook's rule before T140b, and counting
    it now would stale every trial opened since its `made_at`, which read the
    repaired store, and rerun them into N (#1321 pass-2 follow-up)."""
    day = cutoff.astimezone(UTC).date()
    latest: datetime | None = None
    for release in data_releases(conn):
        if (
            release.stage == "after"
            and release.imported_at is None
            and release.sessions_from is not None
            and release.sessions_from <= day
            and (latest is None or release.made_at > latest)
        ):
            latest = release.made_at
    return latest


def _check_new_name(conn: duckdb.DuckDBPyConnection, name: str) -> None:
    if RELEASE_NAME.fullmatch(name) is None:
        raise ReleaseRefused(
            f"release name {name!r} must be lowercase letters, digits and single hyphens"
        )
    if any(r.name == name for r in data_releases(conn)):
        raise ReleaseRefused(f"release name {name!r} is already recorded; pick a new name")


def _write_release(
    conn: duckdb.DuckDBPyConnection,
    values: Mapping[str, Any],
    reason: str,
    trial_id: int | None = None,
    made_at: datetime | None = None,
) -> int:
    stored = {key: _json_value(value) for key, value in values.items() if value is not None}
    return _insert_decision(
        conn,
        kind=DATA_RELEASE_KIND,
        reason=reason,
        values=stored,
        hypothesis_id=None,
        trial_id=trial_id,
        made_at=made_at if made_at is not None else utc_now(),
    )


def open_data_release(
    conn: duckdb.DuckDBPyConnection,
    backup: duckdb.DuckDBPyConnection,
    *,
    name: str,
    backup_path: str,
    reason: str,
) -> int:
    """Write a release's `before` row and return its id (runbook, steps 3 and 4).
    `backup` is a read-only connection to the backup file `backup_path` names. The
    row records the store's latest `ingested_at` and the data vintage now (no
    sessions are named yet, so the cutoff is the row's own time). Refuses a bad or
    used name, a blank reason, a second open release, and a backup whose latest
    `ingested_at` is not the store's (it would not hold the state before the
    repair)."""
    if not reason.strip():
        raise ReleaseRefused("a release needs a reason")
    _check_new_name(conn, name)
    if (current := open_release(conn)) is not None:
        raise ReleaseRefused(f"release {current.name!r} is open; close it before opening another")
    live = store_max_ingested_at(conn)
    copied = store_max_ingested_at(backup)
    if copied != live:
        raise ReleaseRefused(
            f"backup {backup_path} is not the store's state: its latest ingested_at is "
            f"{copied}, the store's is {live}; take the backup again"
        )
    now = utc_now()
    values = {
        "name": name,
        "stage": "before",
        "made_at": now,
        "backup_path": backup_path,
        "store_max_ingested_at": live,
        "data_vintage": data_vintage(conn, now),
        "cutoff": now,
    }
    return _write_release(conn, values, reason, made_at=now)


def close_data_release(
    conn: duckdb.DuckDBPyConnection,
    *,
    name: str,
    sessions_from: date,
    sessions_to: date,
    reason: str | None = None,
) -> int:
    """Write the open release's `after` row with the sessions the repair touched
    and return its id (runbook, step 7). The row records the store's latest
    `ingested_at` and the data vintage at the close of `sessions_to`, read before
    the row is written. Its `made_at` then enters `data_vintage` for every cutoff
    on or after `sessions_from` (`_release_vintage`). Refuses a name that is not
    the open release, a session that is not an XNYS session and a reversed range.
    `reason` defaults to the `before` row's."""
    current = open_release(conn)
    if current is None or current.name != name:
        found = "no release is open" if current is None else f"{current.name!r} is open"
        raise ReleaseRefused(f"cannot close {name!r}: {found}")
    for flag, day in (("--from", sessions_from), ("--to", sessions_to)):
        if not is_session(day):
            raise ReleaseRefused(f"{flag} {day.isoformat()} is not an XNYS session")
    if sessions_from > sessions_to:
        raise ReleaseRefused(f"--from {sessions_from} is after --to {sessions_to}")
    if reason is not None and not reason.strip():
        raise ReleaseRefused("--reason must not be blank")
    cutoff = session_close(sessions_to)
    now = utc_now()
    values = {
        "name": name,
        "stage": "after",
        "made_at": now,
        "store_max_ingested_at": store_max_ingested_at(conn),
        "data_vintage": data_vintage(conn, cutoff),
        "cutoff": cutoff,
        "sessions_from": sessions_from,
        "sessions_to": sessions_to,
    }
    return _write_release(conn, values, reason or current.reason, made_at=now)


def _repair_runs(backup: duckdb.DuckDBPyConnection, since: datetime) -> list[dict[str, Any]]:
    """The backup's `ingestion_runs` rows of mode `repair` started at or after
    `since`, oldest first: the deletions `data_vintage` cannot see."""
    if not _has_table(backup, "ingestion_runs"):
        return []
    rows = backup.execute(
        "SELECT run_id, started_at, finished_at, source, status, rows_added "
        "FROM ingestion_runs WHERE mode = ? AND started_at >= ? ORDER BY started_at, run_id",
        [_REPAIR_MODE, since],
    ).fetchall()
    keys = ("run_id", "started_at", "finished_at", "source", "status", "rows_added")
    return [{k: _json_value(v) for k, v in zip(keys, row, strict=True)} for row in rows]


def record_trial_state(
    conn: duckdb.DuckDBPyConnection,
    backup: duckdb.DuckDBPyConnection,
    *,
    name: str,
    backup_path: str,
    trial_id: int,
    reason: str,
) -> int:
    """Write a closed `record` row naming the backup that holds the store state
    trial `trial_id` read, and return its id (runbook, "The back-fill"). The
    vintage and latest `ingested_at` are read on `backup` (read-only) at the
    trial's `data_cutoff`; the row says whether that vintage equals the trial's
    stored one (`vintage_equals_trial`) and lists the backup's repair runs started
    since the trial (`repair_runs`, #1321 pass-2 follow-up: a deletion between the
    trial and the backup moves no vintage). A `record` row is never open. Refuses a
    bad or used name, a blank reason, and a trial without a `data_cutoff`."""
    if not reason.strip():
        raise ReleaseRefused("a release needs a reason")
    _check_new_name(conn, name)
    row = conn.execute(
        "SELECT data_cutoff, data_vintage, store_max_ingested_at, start_session, "
        "end_session, started_at FROM trials WHERE trial_id = ?",
        [trial_id],
    ).fetchone()
    if row is None:
        raise ReleaseRefused(f"trial {trial_id} does not exist")
    cutoff, trial_vintage, trial_max, start, end, started_at = row
    if cutoff is None:
        raise ReleaseRefused(f"trial {trial_id} has no data_cutoff (a refused window)")
    vintage = data_vintage(backup, cutoff)
    now = utc_now()
    values = {
        "name": name,
        "stage": "record",
        "made_at": now,
        "backup_path": backup_path,
        "store_max_ingested_at": store_max_ingested_at(backup),
        "data_vintage": vintage,
        "cutoff": cutoff,
        "sessions_from": start,
        "sessions_to": end,
        "trial": trial_id,
        "trial_data_vintage": trial_vintage,
        "trial_store_max_ingested_at": trial_max,
        "vintage_equals_trial": vintage == trial_vintage,
        "repair_runs": _repair_runs(backup, started_at),
    }
    return _write_release(conn, values, reason, trial_id=trial_id, made_at=now)


def _entry_problems(index: int, entry: Mapping[str, Any]) -> list[str]:
    where = f"entry {index}"
    stage = entry.get("stage")
    if stage not in _STAGE_KEYS:
        return [f"{where}: stage {stage!r} is not one of {RELEASE_STAGES}"]
    problems: list[str] = []
    keys = set(entry)
    if missing := sorted(_STAGE_KEYS[stage] - keys):
        problems.append(f"{where}: {stage} entry lacks {missing}")
    if extra := sorted(keys - _STAGE_KEYS[stage]):
        problems.append(f"{where}: {stage} entry has unknown keys {extra}")
    name = entry.get("name")
    if not isinstance(name, str) or RELEASE_NAME.fullmatch(name) is None:
        problems.append(f"{where}: name {name!r} is not lowercase letters, digits and hyphens")
    for key in _DATETIME_KEYS:
        value = entry.get(key)
        if key in entry and (not isinstance(value, datetime) or value.utcoffset() != timedelta(0)):
            problems.append(f"{where}: {key} must be a UTC datetime with Z, got {value!r}")
    for key in _DATE_KEYS:
        value = entry.get(key)
        if key in entry and (isinstance(value, datetime) or not isinstance(value, date)):
            problems.append(f"{where}: {key} must be a date, got {value!r}")
    if "trial" in entry and (
        isinstance(entry["trial"], bool) or not isinstance(entry["trial"], int)
    ):
        problems.append(f"{where}: trial must be an integer, got {entry['trial']!r}")
    for key in ("reason", "backup_path"):
        if key in entry and (not isinstance(entry[key], str) or not entry[key].strip()):
            problems.append(f"{where}: {key} must be a non-blank string")
    return problems


def plan_release_import(
    conn: duckdb.DuckDBPyConnection, entries: Sequence[Mapping[str, Any]]
) -> list[list[Mapping[str, Any]]]:
    """Check every entry of `data/releases.toml` (its `[[release]]` tables, in file
    order) against the runbook and the store, and return them grouped by release
    name in file order, ready for `import_release`, one release each. Refuses, all
    problems listed at once and nothing written: a malformed entry, a (name,
    stage) pair the file repeats or the store holds, a name whose `record` is
    mixed with a `before` or `after`, an `after` with no `before` before it or
    made on or after the version-18 migration (the commands record those), a
    `record` whose trial does not exist, and a file that would leave more than one
    release open (the store's open release counted)."""
    problems: list[str] = []
    for index, entry in enumerate(entries, start=1):
        if not isinstance(entry, Mapping):
            problems.append(f"entry {index} is not a table: {entry!r}")
            continue
        problems.extend(_entry_problems(index, entry))
    if problems:
        raise ReleaseRefused("; ".join(problems))
    migrated = conn.execute(
        "SELECT MIN(applied_at) FROM schema_version WHERE version >= 18"
    ).fetchone()
    since = migrated[0] if migrated is not None else None
    for entry in entries:
        if entry["stage"] == "after" and since is not None and entry["made_at"] >= since:
            problems.append(
                f"{entry['name']}: an after made at {entry['made_at']} is after the store "
                f"gained the release commands ({since}); record it with `close`, since an "
                "imported after moves no vintage"
            )
    stored = data_releases(conn)
    pairs = {(r.name, r.stage): r for r in stored}
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for entry in entries:
        groups.setdefault(entry["name"], []).append(entry)
    for name, group in groups.items():
        stages = [e["stage"] for e in group]
        for stage in sorted(set(stages)):
            if stages.count(stage) > 1:
                problems.append(f"{name}: the file has {stages.count(stage)} {stage} entries")
            if (name, stage) in pairs:
                problems.append(f"{name}: a {stage} row is already stored")
        all_stages = set(stages) | {s for (n, s) in pairs if n == name}
        if "record" in all_stages and all_stages != {"record"}:
            problems.append(f"{name}: a record entry shares its name with a before or after")
        stored_before = pairs.get((name, "before"))
        if "after" in stages and stored_before is not None and stored_before.imported_at is None:
            problems.append(
                f"{name}: its before row was written by `open`; close it with `close`, "
                "not an imported after (an imported after moves no vintage)"
            )
        if "after" in stages:
            before = next((e for e in group if e["stage"] == "before"), None)
            before_at = (
                before["made_at"]
                if before
                else getattr(pairs.get((name, "before")), "made_at", None)
            )
            after_at = next(e["made_at"] for e in group if e["stage"] == "after")
            if before_at is None:
                problems.append(f"{name}: an after entry with no before")
            elif after_at < before_at:
                problems.append(f"{name}: the after entry is made before its before entry")
        for entry in group:
            if entry["stage"] == "record" and (
                conn.execute("SELECT 1 FROM trials WHERE trial_id = ?", [entry["trial"]]).fetchone()
                is None
            ):
                problems.append(f"{name}: trial {entry['trial']} does not exist")
    closed = {n for (n, s) in pairs if s == "after"} | {
        e["name"] for e in entries if e["stage"] == "after"
    }
    still_open = sorted(
        {n for (n, s) in pairs if s == "before"}
        | {e["name"] for e in entries if e["stage"] == "before"}
    )
    still_open = [n for n in still_open if n not in closed]
    if len(still_open) > 1:
        problems.append(f"the import would leave {len(still_open)} releases open: {still_open}")
    if problems:
        raise ReleaseRefused("; ".join(problems))
    return list(groups.values())


def import_release(
    conn: duckdb.DuckDBPyConnection, entries: Sequence[Mapping[str, Any]]
) -> list[int]:
    """Write one release's entries (one group of `plan_release_import`) as
    `data_release` rows in file order and return their ids; the caller holds one
    transaction per release. Each row's `values_json` is the entry as written, its
    times in ISO form, plus `imported_at` (the write time, also the row's
    `made_at` column; the entry's `made_at` stays in `values_json`). Refuses again
    a (name, stage) pair already stored, so a second import of the file writes
    nothing."""
    stored = {(r.name, r.stage) for r in data_releases(conn)}
    if clash := sorted({(e["name"], e["stage"]) for e in entries} & stored):
        raise ReleaseRefused(f"already stored: {clash}")
    now = utc_now()
    ids = []
    for entry in entries:
        values = {
            key: value.astimezone(UTC) if isinstance(value, datetime) else value
            for key, value in entry.items()
            if key != "reason"
        }
        values[IMPORTED_AT_KEY] = now
        ids.append(
            _write_release(conn, values, entry["reason"], trial_id=entry.get("trial"), made_at=now)
        )
    return ids


# --- reads -----------------------------------------------------------------


def family_sharpes(
    conn: duckdb.DuckDBPyConnection, family: str, pending: TrialHandle | None = None
) -> FamilySharpes:
    """The counted trials and the per-basis annualised Sharpes for V over `family`
    (module docstring, "Deflated Sharpe inputs").

    `pending` counts a trial that has its metrics but no result row yet as
    `ok`, when it is itself a non-synthetic `in_sample` trial of `family`
    and its `trials` row is in this store. Raises `RegistryError` when a
    pair's latest trial lacks a finite base-level period Sharpe or a positive
    `periods_per_year`.
    """
    if pending is not None:
        _check_trial_row(conn, pending)
    counted = conn.execute(
        f"SELECT t.trial_id, h.params_json, t.start_session, t.data_cutoff {_COUNTED_FROM} "
        "ORDER BY t.trial_id",
        [family, pending.trial_id if pending is not None else None],
    ).fetchall()
    latest: dict[tuple[str, date, datetime | None], tuple[int, float]] = {}
    for trial_id, params_json, start, cutoff in counted:
        params = json.loads(params_json)
        cadence = frozen_values(_Stored(params, family))[CADENCE_KEY]
        pair = (
            _canonical_set_hash(params, family),
            _first_rebalance_on_or_after(start, cadence),
            cutoff,
        )
        latest[pair] = (trial_id, float(params[BASE_COST_KEY]))
    chosen = sorted(latest.values())
    return FamilySharpes(
        n_trials=len(counted),
        raw=_annual_sharpes(conn, chosen, "raw"),
        excess_spy=_annual_sharpes(conn, chosen, "excess_spy"),
    )


#: The trials N counts and V draws from: `ok` (or the pending trial), in-sample,
#: non-synthetic, of one family. Parameters: the family, then the pending trial id.
_COUNTED_FROM: Final = (
    "FROM trials t JOIN hypotheses h USING (hypothesis_id) "
    "LEFT JOIN trial_results r USING (trial_id) "
    "WHERE h.family = ? AND t.kind = 'in_sample' AND NOT t.synthetic "
    "AND (r.status = 'ok' OR (r.trial_id IS NULL AND t.trial_id = ?))"
)


def count_counted_trials(
    conn: duckdb.DuckDBPyConnection, family: str, pending: TrialHandle | None = None
) -> int:
    """How many trials `family_sharpes` counts (same rows, same `pending` rule). The
    DSR's N is `backtest.results.family_n`, which calls this; nothing else should."""
    if pending is not None:
        _check_trial_row(conn, pending)
    row = conn.execute(
        f"SELECT COUNT(*) {_COUNTED_FROM}",
        [family, pending.trial_id if pending is not None else None],
    ).fetchone()
    return int(row[0]) if row is not None else 0


@dataclass(frozen=True)
class _Stored:
    """A stored parameter set and its family, the shape `frozen.frozen_values` reads,
    so the window key reads a row's cadence through the one accessor."""

    params: Mapping[str, Any]
    family: str


def _canonical_set_hash(params: Mapping[str, Any], family: str) -> str:
    """The V pair's parameter key: the hash of the canonical frozen set (strategy-lab
    spec req 3, amendment 6), never the raw `params_sha256`."""
    return params_sha256(canonical_frozen_set(params, family))


def _annual_sharpes(
    conn: duckdb.DuckDBPyConnection, trials: Sequence[tuple[int, float]], basis: Basis
) -> tuple[float, ...]:
    """Each trial's base-level `strategy` period Sharpe on `basis` times the square
    root of its own `periods_per_year`, in `trials` order, in one query."""
    metric = _BASIS_METRICS[basis]
    if not trials:
        return ()
    rows = conn.execute(
        "SELECT c.trial_id, s.value, p.value "
        "FROM (SELECT UNNEST($ids::BIGINT[]) AS trial_id, UNNEST($bases::DOUBLE[]) AS base) c "
        "LEFT JOIN trial_metrics s ON s.trial_id = c.trial_id AND s.series = 'strategy' "
        "AND s.cost_per_side_bps = c.base AND s.metric = $metric "
        "LEFT JOIN trial_metrics p ON p.trial_id = c.trial_id AND p.series = 'strategy' "
        "AND p.cost_per_side_bps = c.base AND p.metric = $ppy",
        {
            "ids": [trial_id for trial_id, _ in trials],
            "bases": [base for _, base in trials],
            "metric": metric,
            "ppy": PERIODS_PER_YEAR_METRIC,
        },
    ).fetchall()
    found = {trial_id: (sharpe, ppy) for trial_id, sharpe, ppy in rows}
    values: list[float] = []
    for trial_id, _base in trials:
        sharpe, ppy = found.get(trial_id, (None, None))
        if sharpe is None or not math.isfinite(sharpe):
            raise RegistryError(f"trial {trial_id} has no finite base-level {metric} for strategy")
        if ppy is None or not math.isfinite(ppy) or ppy <= 0:
            raise RegistryError(
                f"trial {trial_id} has no positive base-level {PERIODS_PER_YEAR_METRIC} "
                "for strategy"
            )
        values.append(float(sharpe) * math.sqrt(ppy))
    return tuple(values)


def family_holdout_spends(conn: duckdb.DuckDBPyConnection, family: str) -> list[HoldoutSpend]:
    """Every `holdout` trial in `family`, whatever its outcome, and every
    research run of the family that spent one of its hypotheses' holdouts
    (research-registry spec req 5), oldest first: a holdout run that failed,
    crashed or was refused still counts as a spend (conservative; domain rule
    3). A store before the research tables (a read-only open of version 11)
    has trial spends only. A caller that reads spends to set `holdout_repeat`
    does so in the same write chunk as its `open_trial`."""
    rows = conn.execute(
        "SELECT t.trial_id, t.hypothesis_id, h.slug, t.started_at, t.synthetic, "
        f"t.holdout_reason, COALESCE(r.status, '{UNFINISHED}') "
        "FROM trials t JOIN hypotheses h USING (hypothesis_id) "
        "LEFT JOIN trial_results r USING (trial_id) "
        "WHERE h.family = ? AND t.kind = 'holdout' ORDER BY t.trial_id",
        [family],
    ).fetchall()
    spends = [HoldoutSpend(*row) for row in rows]
    has_research = conn.execute(
        "SELECT COUNT(*) FROM duckdb_tables() WHERE database_name = current_database() "
        "AND table_name IN ('research_runs', 'research_decisions', 'research_registrations', "
        "'research_results')"
    ).fetchone()
    if has_research is None or has_research[0] < 4:
        return spends
    research = conn.execute(
        "SELECT r.run_id, g.slug, r.known_at, r.synthetic, r.holdout_reason, "
        f"COALESCE(x.outcome, '{UNFINISHED}'), d.values_json "
        "FROM research_decisions d JOIN research_runs r ON r.run_id = d.run_id "
        "JOIN research_registrations g ON g.registration_id = r.registration_id "
        "LEFT JOIN research_results x ON x.run_id = r.run_id "
        "WHERE d.kind = 'holdout_spend' AND g.family = ? ORDER BY r.run_id",
        [family],
    ).fetchall()
    spends += [
        HoldoutSpend(run_id, None, slug, at, synthetic, reason, status, "research_run")
        for run_id, slug, at, synthetic, reason, status, values in research
        if json.loads(values)["family_holdouts"]
    ]
    return sorted(spends, key=lambda spend: spend.started_at)


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
