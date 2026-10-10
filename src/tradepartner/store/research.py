"""Research-experiment registry API (research-registry spec reqs 1 to 9; plan T81).

The only way code writes the research tables `store.schema` creates at version 12.
**Inserts and reads only** (req 1): a run's outcome is its one `research_results`
row, never a change to its `research_runs` row, and a run with no result row lists
as `unfinished`. Every function takes an open connection and leaves the
transaction to the caller (`store.db.open_for_write` commits a chunk); ids are
`MAX + 1` inside that transaction, as in `store.registry`. Every function calls
`schema.require_research` first, so a pre-migration store raises
`ResearchNotInitialised`.

**Registration** (req 2). `register_experiment` takes a file-level-valid
`ParsedExperiment` (T82's parser did every refusal that needs no store) and adds
the store-level ones: a registered slug needs `amends_sha256` equal to its latest
registration's `params_sha256`, a new slug may not carry one, and a
`multiplicity.family_id` keeps its first `method` and `family_size`. An
amendment is a new row pointing at the old one plus a `budget_amend` decision.

**Datasets** (req 11). `register_dataset` records what the caller computed (T83's
CLI hashes the export and reads the event column with T82's helpers) and applies
the store rules itself, so every caller is protected, not only the CLI: sealing
never shrinks under a name, the same export, split file and sealed set (and the
same path, `locked` and seed) return the existing row, a split file needs an
event column and labels no row `full` or `none`, a sealed split name must be one
of `RESEARCH_SPLITS`, and a sealed split needs an event column and sealed
periods holding its event span (`full` and `none` against the whole declared
`[event_start, event_end]`, since a split file never labels a row `full` or
`none`; #1149). The two span endpoints can hide a row in a gap between two sealed
periods, so when the periods do not cover the whole span the store also checks
the split's per-row event dates (`split_row_dates`, every row for `full`/`none`,
the same rows the CLI already reads); #1174. It can only check the rows it is
given: for `full`/`none` their count must equal `n_rows`, while a labelled
split's completeness stays the CLI's `check_sealed_split_has_period`.

**Runs** (reqs 3 to 7). `open_run` and `attach_run` are the only constructors of
`RunHandle`. `open_run` reads the rows the gates need, asks `research.gates` in
spec order (window, split, holdout, budget, confirmatory basis), and inserts the
run row whatever the answer: a refusal is a run row plus its refusal result, and
the handle carries `refusal` so no data are read. Holdout spends, flags and
reasons are recorded only on a run that opens. Commit `open_run` in its own write
chunk before any other work, including `load_dataset`: a rolled-back open has
no durable run record, even if its handle remains in memory. `synthetic=True`
is refused on `settings.store.path` (file identity, `store.registry`'s check).

**The development boundary** (ADR 0016 point 2; data-foundation plan T142b). For a
registration that names a backtest family (one with registered hypotheses), `open_run` reads
`store.registry.development_boundary` once and treats it as one more protected edge,
checked right after the window gate: a registration window that reads a session after
the boundary outside every one of the family's holdouts (a dead month, or a session
past the last `holdout.end`) is `refused_window`, with no flag to override it. A
window past the boundary inside a holdout is the holdout gate's, as before (it needs
`--spend-holdout`). With no boundary row, or no family, nothing changes.

**Results** (reqs 6, 8). `close_run` records any non-`ok` outcome; `write_result`
records `ok` with a verdict computed from the interval, or `failed` when more
configurations were evaluated than declared, the export's hash moved, or the
runtime store's latest `ingested_at` moved for an `economic` or `return` run. It
returns rather than raises so the row survives the commit.

**Counts** (req 9). `family_run_count` is the research share of a family's N.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Final, Literal

import duckdb

from tradepartner.calendar import all_sessions
from tradepartner.config import Settings, get_settings
from tradepartner.research import (
    EVERY_ROW_SPLITS,
    DatasetChanged,
    RunHandle,
    _issue_run_handle,
    load_dataset,
)
from tradepartner.research.experiment import ParsedExperiment
from tradepartner.research.gates import (
    Flags,
    PriorSpend,
    Reasons,
    Span,
    WindowDecision,
    check_budget,
    check_confirmatory,
    check_holdout,
    check_split,
    check_window,
    compute_verdict,
    is_split_sealed,
)
from tradepartner.store.db import insert_row, utc_now
from tradepartner.store.registry import (
    RealStoreRefused,
    RegistryError,
    _database_path,
    _is_real_store,
    _next_id,
    boundary_date,
    canonical_params_json,
    code_version,
    store_max_ingested_at,
)
from tradepartner.store.schema import RESEARCH_OUTCOMES, RESEARCH_SPLITS, require_research

UNFINISHED: Final = "unfinished"
DATASET_CHANGED_MESSAGE: Final = "dataset changed during run"
STORE_CHANGED_MESSAGE: Final = "store changed during run"
CONFIGURATIONS_EXCEEDED_MESSAGE: Final = "configurations exceeded declaration"

#: Kinds whose runs read the runtime store (req 4), so `store_max_ingested_at`
#: is captured at open and compared at close (req 8).
STORE_READING_KINDS: Final = frozenset({"economic", "return"})
#: Outcomes `close_run` records; `ok` goes through `write_result`.
CLOSE_OUTCOMES: Final = frozenset(RESEARCH_OUTCOMES) - {"ok"}

DecisionKind = Literal["holdout_spend", "budget_amend"]


class ResearchError(RegistryError):
    """A research-registry call that would break a registry rule."""


class UnknownRegistration(ResearchError):
    """No registration or run matches the slug or id."""


class RunAlreadyClosed(ResearchError):
    """The run already has its result row; nothing more may be written."""


@dataclass(frozen=True)
class RegistrationRecord:
    """One `research_registrations` row, JSON lists decoded."""

    registration_id: int
    slug: str
    kind: str
    stage: int
    title: str
    confirmatory: bool
    provenance: str
    touches_returns: bool
    family: str | None
    claims: tuple[str, ...]
    hypothesis_ref: str | None
    dataset_name: str
    dataset_sha256_pin: str | None
    window_start: date
    window_end: date
    splits: tuple[str, ...]
    primary_metric: str
    primary_direction: str
    primary_threshold: float | None
    primary_ci_level: float
    primary_min_clusters: int
    primary_inference: str
    secondary: tuple[str, ...]
    comparison_set: str
    multiplicity_method: str
    multiplicity_family_id: str | None
    multiplicity_family_size: int | None
    budget_runs: int
    budget_configurations: int
    stop_rule: str
    expected_effect: str
    seed: int
    doc_path: str
    doc_sha256: str
    params_json: str
    params_sha256: str
    amends_registration_id: int | None
    registered_by: str
    known_at: datetime


@dataclass(frozen=True)
class DatasetRecord:
    """One `research_datasets` row, JSON decoded. `path` is read only by
    `research.load_dataset` (req 3, req 13)."""

    dataset_id: int
    name: str
    version: str
    path: str
    sha256: str
    n_rows: int | None
    event_start: date
    event_end: date
    event_column: str | None
    split_path: str | None
    split_sha256: str | None
    split_spans: Mapping[str, tuple[date, date]]
    sealed_splits: tuple[str, ...]
    sealed_periods: tuple[tuple[date, date], ...]
    locked: bool
    seed: int | None
    code_version: str
    code_dirty: bool | None
    note: str | None
    known_at: datetime


@dataclass(frozen=True)
class RunSummary:
    """One listed run; `outcome` is `unfinished` when it has no result row."""

    run_id: int
    registration_id: int
    slug: str
    kind: str
    family: str | None
    provenance: str
    dataset_id: int
    split: str
    confirmatory: bool
    confirmatory_basis: str
    n_configurations_declared: int
    synthetic: bool
    holdout_spent: bool
    holdout_repeat: bool
    run_by: str
    note: str | None
    known_at: datetime
    outcome: str
    verdict: str | None
    message: str | None
    finished_at: datetime | None


# --- helpers -----------------------------------------------------------------


def _sha256_text(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _span_list(spans: Sequence[tuple[date, date]]) -> list[list[str]]:
    return [[start.isoformat(), end.isoformat()] for start, end in spans]


def _decode_spans(raw: Sequence[Sequence[str]]) -> tuple[tuple[date, date], ...]:
    return tuple((date.fromisoformat(s), date.fromisoformat(e)) for s, e in raw)


def _valid_spend_span(start: str, end: str) -> bool:
    try:
        return date.fromisoformat(start) <= date.fromisoformat(end)
    except ValueError:
        return False


def _covers_span(span: tuple[date, date], periods: Sequence[tuple[date, date]]) -> bool:
    """Whether the merged `periods` cover every day of `[span[0], span[1]]` with no gap.

    A split whose rows all lie inside its span is fully held as soon as the periods cover
    the whole interval, so the store needs no per-row dates then. A gap means the span's
    two endpoints can hide a row in it (#1174): the store cannot tell from the endpoints
    alone and must read the rows."""
    start, end = span
    cursor = start
    for p_start, p_end in sorted(periods):
        if p_end < cursor:
            continue
        if p_start > cursor:
            return False
        cursor = max(cursor, p_end)
        if cursor >= end:
            return True
    return cursor >= end


def _next_run_id(conn: duckdb.DuckDBPyConnection) -> int:
    return max(
        _next_id(conn, "research_runs", "run_id"), _next_id(conn, "research_results", "run_id")
    )


def _has_result(conn: duckdb.DuckDBPyConnection, run_id: int) -> bool:
    return (
        conn.execute("SELECT 1 FROM research_results WHERE run_id = ?", [run_id]).fetchone()
        is not None
    )


def _insert_result(
    conn: duckdb.DuckDBPyConnection, run_id: int, outcome: str, message: str | None, **stats: Any
) -> None:
    insert_row(
        conn,
        "research_results",
        {"run_id": run_id, "outcome": outcome, "message": message, "known_at": utc_now(), **stats},
    )


def _check_open(conn: duckdb.DuckDBPyConnection, handle: RunHandle) -> None:
    """Refuse a handle from another store file, one whose `research_runs` row is
    not in this store (a rolled-back open), and one whose run is closed."""
    require_research(conn)
    if handle.database != _database_path(conn):
        raise ResearchError(
            f"run {handle.run_id} was opened on another store ({handle.database!r})"
        )
    row = conn.execute(
        "SELECT known_at FROM research_runs WHERE run_id = ?", [handle.run_id]
    ).fetchone()
    if row is None or row[0] != handle.known_at:
        raise ResearchError(
            f"run {handle.run_id} has no matching research_runs row in this store "
            "(was its open_run rolled back?)"
        )
    if _has_result(conn, handle.run_id):
        raise RunAlreadyClosed(f"run {handle.run_id} already has its result row")


# --- registrations -----------------------------------------------------------

_REGISTRATION_COLUMNS: Final = (
    "registration_id, slug, kind, stage, title, confirmatory, provenance, touches_returns, "
    "family, claims_json, hypothesis_ref, dataset_name, dataset_sha256_pin, window_start, "
    "window_end, splits_json, primary_metric, primary_direction, primary_threshold, "
    "primary_ci_level, primary_min_clusters, primary_inference, secondary_json, "
    "comparison_set, multiplicity_method, multiplicity_family_id, multiplicity_family_size, "
    "budget_runs, budget_configurations, stop_rule, expected_effect, seed, doc_path, "
    "doc_sha256, params_json, params_sha256, amends_registration_id, registered_by, known_at"
)
_JSON_LIST_COLUMNS: Final = {9: "claims", 15: "splits", 22: "secondary"}


def _registration_where(
    conn: duckdb.DuckDBPyConnection, where: str, value: object
) -> RegistrationRecord | None:
    row = conn.execute(
        f"SELECT {_REGISTRATION_COLUMNS} FROM research_registrations WHERE {where}", [value]
    ).fetchone()
    if row is None:
        return None
    values = list(row)
    for index in _JSON_LIST_COLUMNS:
        values[index] = tuple(json.loads(values[index]))
    return RegistrationRecord(*values)


def _latest_registration(conn: duckdb.DuckDBPyConnection, slug: str) -> RegistrationRecord | None:
    return _registration_where(
        conn,
        "registration_id = (SELECT MAX(registration_id) FROM research_registrations "
        "WHERE slug = ?)",
        slug,
    )


def get_registration(conn: duckdb.DuckDBPyConnection, slug: str) -> RegistrationRecord:
    """The latest registration of `slug`'s amendment chain; `UnknownRegistration`
    if the slug was never registered."""
    require_research(conn)
    record = _latest_registration(conn, slug)
    if record is None:
        raise UnknownRegistration(f"no experiment is registered as {slug!r}")
    return record


def register_experiment(
    conn: duckdb.DuckDBPyConnection,
    parsed: ParsedExperiment,
    registered_by: str,
    *,
    amend_reason: str | None = None,
) -> RegistrationRecord:
    """Register `parsed` (module docstring, "Registration") and return its row.

    Refuses a registered slug without `amends_sha256`, an `amends_sha256` that is
    not the latest registration's `params_sha256` (a stale amendment), an
    `amends_sha256` on a new slug, and a `multiplicity.family_id` already
    registered with another method or size. An amendment also appends a
    `budget_amend` decision with the old and new budgets and `amend_reason`
    (default: which registration it supersedes)."""
    require_research(conn)
    latest = _latest_registration(conn, parsed.slug)
    if latest is None and parsed.amends_sha256 is not None:
        raise ResearchError(
            f"{parsed.slug!r} is not registered yet; amends_sha256 is only for an amendment"
        )
    if latest is not None and parsed.amends_sha256 is None:
        raise ResearchError(
            f"{parsed.slug!r} is already registered (registration {latest.registration_id}); "
            f"a re-registration is an amendment with amends_sha256 = {latest.params_sha256!r}"
        )
    if latest is not None and parsed.amends_sha256 != latest.params_sha256:
        raise ResearchError(
            f"stale amendment: amends_sha256 {parsed.amends_sha256!r} is not the latest "
            f"registration's params_sha256 {latest.params_sha256!r}"
        )
    if parsed.multiplicity_family_id is not None:
        clash = conn.execute(
            "SELECT registration_id, multiplicity_method, multiplicity_family_size "
            "FROM research_registrations WHERE multiplicity_family_id = ? "
            "AND (multiplicity_method <> ? OR multiplicity_family_size IS DISTINCT FROM ?) "
            "ORDER BY registration_id LIMIT 1",
            [
                parsed.multiplicity_family_id,
                parsed.multiplicity_method,
                parsed.multiplicity_family_size,
            ],
        ).fetchone()
        if clash is not None:
            raise ResearchError(
                f"multiplicity family {parsed.multiplicity_family_id!r} is registered with "
                f"method {clash[1]!r} and size {clash[2]} (registration {clash[0]})"
            )
    registration_id = _next_id(conn, "research_registrations", "registration_id")
    insert_row(
        conn,
        "research_registrations",
        {
            "registration_id": registration_id,
            "slug": parsed.slug,
            "kind": parsed.kind,
            "stage": parsed.stage,
            "title": parsed.title,
            "confirmatory": parsed.confirmatory,
            "provenance": parsed.provenance,
            "touches_returns": parsed.touches_returns,
            "family": parsed.family,
            "claims_json": json.dumps(list(parsed.claims)),
            "hypothesis_ref": parsed.hypothesis_ref,
            "dataset_name": parsed.dataset_name,
            "dataset_sha256_pin": parsed.dataset_sha256_pin,
            "window_start": parsed.window_start,
            "window_end": parsed.window_end,
            "splits_json": json.dumps(list(parsed.splits)),
            "primary_metric": parsed.primary_metric,
            "primary_direction": parsed.primary_direction,
            "primary_threshold": parsed.primary_threshold,
            "primary_ci_level": parsed.primary_ci_level,
            "primary_min_clusters": parsed.primary_min_clusters,
            "primary_inference": parsed.primary_inference,
            "secondary_json": json.dumps(list(parsed.secondary)),
            "comparison_set": parsed.comparison_set,
            "multiplicity_method": parsed.multiplicity_method,
            "multiplicity_family_id": parsed.multiplicity_family_id,
            "multiplicity_family_size": parsed.multiplicity_family_size,
            "budget_runs": parsed.budget_runs,
            "budget_configurations": parsed.budget_configurations,
            "stop_rule": parsed.stop_rule,
            "expected_effect": parsed.expected_effect,
            "seed": parsed.seed,
            "doc_path": parsed.doc_path,
            "doc_sha256": parsed.doc_sha256,
            "params_json": parsed.params_json,
            "params_sha256": parsed.params_sha256,
            "amends_registration_id": latest.registration_id if latest is not None else None,
            "registered_by": registered_by,
            "known_at": utc_now(),
        },
    )
    if latest is not None:
        record_decision(
            conn,
            kind="budget_amend",
            registration_id=registration_id,
            values={
                "amends_registration_id": latest.registration_id,
                "old": {"runs": latest.budget_runs, "configurations": latest.budget_configurations},
                "new": {"runs": parsed.budget_runs, "configurations": parsed.budget_configurations},
            },
            reason=amend_reason
            if amend_reason is not None and amend_reason.strip()
            else f"amends registration {latest.registration_id}",
            made_by=registered_by,
        )
    record = _registration_where(conn, "registration_id = ?", registration_id)
    assert record is not None
    return record


# --- datasets ----------------------------------------------------------------

_DATASET_COLUMNS: Final = (
    "dataset_id, name, version, path, sha256, n_rows, event_start, event_end, event_column, "
    "split_path, split_sha256, split_spans_json, sealed_splits_json, sealed_periods_json, "
    "locked, seed, code_version, code_dirty, note, known_at"
)


def _dataset_from_row(row: Sequence[Any]) -> DatasetRecord:
    values = list(row)
    values[11] = {k: _decode_spans([v])[0] for k, v in json.loads(values[11]).items()}
    values[12] = tuple(json.loads(values[12]))
    values[13] = _decode_spans(json.loads(values[13]))
    return DatasetRecord(*values)


def get_dataset(conn: duckdb.DuckDBPyConnection, dataset_id: int) -> DatasetRecord:
    """The dataset version `dataset_id`; `UnknownRegistration` if none."""
    require_research(conn)
    row = conn.execute(
        f"SELECT {_DATASET_COLUMNS} FROM research_datasets WHERE dataset_id = ?", [dataset_id]
    ).fetchone()
    if row is None:
        raise UnknownRegistration(f"no dataset version has id {dataset_id}")
    return _dataset_from_row(row)


def _name_sealing(
    conn: duckdb.DuckDBPyConnection, name: str
) -> tuple[frozenset[str], frozenset[tuple[date, date]]]:
    """Every split name and period ever sealed under dataset `name` (sealing
    persists across versions, req 11)."""
    splits: set[str] = set()
    periods: set[tuple[date, date]] = set()
    for raw_splits, raw_periods in conn.execute(
        "SELECT sealed_splits_json, sealed_periods_json FROM research_datasets WHERE name = ?",
        [name],
    ).fetchall():
        splits |= set(json.loads(raw_splits))
        periods |= set(_decode_spans(json.loads(raw_periods)))
    return frozenset(splits), frozenset(periods)


def register_dataset(
    conn: duckdb.DuckDBPyConnection,
    *,
    name: str,
    version: str,
    path: str,
    sha256: str,
    event_start: date,
    event_end: date,
    n_rows: int | None = None,
    event_column: str | None = None,
    split_path: str | None = None,
    split_sha256: str | None = None,
    split_spans: Mapping[str, tuple[date, date]] | None = None,
    sealed_splits: Sequence[str] = (),
    sealed_periods: Sequence[tuple[date, date]] = (),
    split_row_dates: Mapping[str, Sequence[date]] | None = None,
    locked: bool = False,
    seed: int | None = None,
    note: str | None = None,
    repo_dir: Path | None = None,
) -> DatasetRecord:
    """Register a dataset version (module docstring, "Datasets") and return it.

    `split_spans` are the per-split event spans the caller computed from the
    event column and the split file; without a split file the one split `full`
    spans `[event_start, event_end]`. `test` in `split_spans` seals `test` by
    implication. `split_row_dates` maps a split to the event dates of its rows
    (`full` and `none` are every row, `n_rows` of them when given); both span
    endpoints must lie in a sealed period, and when the periods do not cover a
    sealed split's whole span the store also requires a non-empty list and checks
    every one of those rows is inside the span and a period, since the two
    endpoints can hide a row between them (#1174). Refuses `split without event
    column`, `sealed split without period`, `sealed set shrinks` and a split file
    labelling rows `full` or `none`; returns the existing row for the same `(name,
    sha256, split_sha256)` and sealed set when the path, split path, `locked` and
    seed match too (a moved export, or one locked after protocol §10 step 5, is a new
    version rather than the stale row)."""
    require_research(conn)
    if event_end < event_start:
        raise ResearchError(f"event span end {event_end} is before its start {event_start}")
    if split_path is not None and event_column is None:
        raise ResearchError("split without event column: a split file needs --event-column")
    if (split_path is None) != (split_sha256 is None) or (split_path is None) != (
        split_spans is None
    ):
        raise ResearchError("split_path, split_sha256 and split_spans go together")
    if split_spans is not None and EVERY_ROW_SPLITS & set(split_spans):
        raise ResearchError(
            "a split file cannot label rows `full` or `none`: those splits bind every row"
        )
    unknown = sorted(set(sealed_splits) - set(RESEARCH_SPLITS))
    if unknown:
        raise ResearchError(f"--sealed {unknown}: not among the splits {RESEARCH_SPLITS}")
    spans = dict(split_spans) if split_spans is not None else {"full": (event_start, event_end)}
    sealed = set(sealed_splits) | ({"test"} if "test" in spans else set())
    periods = sorted(set(sealed_periods))
    row_dates = {split: tuple(values) for split, values in (split_row_dates or {}).items()}
    for split in sorted(sealed):
        if event_column is None or not periods:
            raise ResearchError(
                f"sealed split without period: {split!r} is sealed, which needs an event "
                "column and a sealed period holding its rows"
            )
        # `full` and `none` bind every row whatever the split file labels them
        # (req 3): a split file never labels a row `full`/`none`, so sealing
        # `full` with one has no entry in `spans`, and `none` never has one
        # (with or without a split file). Check the dataset's whole declared
        # event span instead (#1149).
        span = (event_start, event_end) if split in EVERY_ROW_SPLITS else spans.get(split)
        if span is None:
            continue
        if not all(any(start <= day <= end for start, end in periods) for day in span):
            raise ResearchError(
                f"sealed split without period: {split!r} spans [{span[0]}, {span[1]}], "
                f"outside the sealed periods {periods}"
            )
        if _covers_span(span, periods):
            continue
        # The span's two endpoints can each sit in a different sealed period while a row
        # between them is held by none (#1174). Where the periods do not cover the whole
        # span, only the split's row dates can show it, so require and check them. An
        # empty list cannot stand in for the rows.
        rows = row_dates.get(split)
        if not rows:
            raise ResearchError(
                f"sealed split without period: {split!r} spans [{span[0]}, {span[1]}], which "
                f"the sealed periods {periods} do not cover; sealing it needs split_row_dates "
                f"for {split!r} to check every row"
            )
        if split in EVERY_ROW_SPLITS and n_rows is not None and len(rows) != n_rows:
            raise ResearchError(
                f"sealed split without period: {split!r} has {len(rows)} row dates for "
                f"n_rows {n_rows}; every row is needed to check the gaps between periods"
            )
        outside_span = [day for day in rows if not span[0] <= day <= span[1]]
        if outside_span:
            raise ResearchError(
                f"sealed split without period: {split!r} row with event date "
                f"{outside_span[0]} is outside the declared span [{span[0]}, {span[1]}]"
            )
        uncovered = [day for day in rows if not any(start <= day <= end for start, end in periods)]
        if uncovered:
            raise ResearchError(
                f"sealed split without period: {split!r} row with event date {uncovered[0]} "
                f"falls outside every sealed period {periods}"
            )
    old_splits, old_periods = _name_sealing(conn, name)
    if not old_splits <= sealed or not old_periods <= set(periods):
        raise ResearchError(
            f"sealed set shrinks: {name!r} has sealed splits {sorted(old_splits)} and periods "
            f"{sorted(old_periods)}; a new version keeps every one"
        )
    sealed_json = json.dumps(sorted(sealed))
    periods_json = json.dumps(_span_list(periods))
    existing = conn.execute(
        f"SELECT {_DATASET_COLUMNS} FROM research_datasets WHERE name = ? AND sha256 = ? "
        "AND split_sha256 IS NOT DISTINCT FROM ? AND sealed_splits_json = ? "
        "AND sealed_periods_json = ? AND path = ? AND split_path IS NOT DISTINCT FROM ? "
        "AND locked = ? AND seed IS NOT DISTINCT FROM ? ORDER BY dataset_id LIMIT 1",
        [name, sha256, split_sha256, sealed_json, periods_json, path, split_path, locked, seed],
    ).fetchone()
    if existing is not None:
        return _dataset_from_row(existing)
    commit, dirty = code_version(repo_dir)
    dataset_id = _next_id(conn, "research_datasets", "dataset_id")
    insert_row(
        conn,
        "research_datasets",
        {
            "dataset_id": dataset_id,
            "name": name,
            "version": version,
            "path": path,
            "sha256": sha256,
            "n_rows": n_rows,
            "event_start": event_start,
            "event_end": event_end,
            "event_column": event_column,
            "split_path": split_path,
            "split_sha256": split_sha256,
            "split_spans_json": json.dumps(
                {k: [s.isoformat(), e.isoformat()] for k, (s, e) in sorted(spans.items())}
            ),
            "sealed_splits_json": sealed_json,
            "sealed_periods_json": periods_json,
            "locked": locked,
            "seed": seed,
            "code_version": commit,
            "code_dirty": dirty,
            "note": note,
            "known_at": utc_now(),
        },
    )
    return get_dataset(conn, dataset_id)


# --- runs --------------------------------------------------------------------


def _family_holdouts(conn: duckdb.DuckDBPyConnection, family: str | None) -> tuple[Span, ...]:
    if family is None:
        return ()
    rows = conn.execute(
        "SELECT DISTINCT holdout_start, holdout_end FROM hypotheses WHERE family = ? ORDER BY 1, 2",
        [family],
    ).fetchall()
    return tuple(Span(start, end) for start, end in rows)


def _boundary_decision(
    window: Span, family_holdouts: Sequence[Span], boundary: date | None
) -> WindowDecision:
    """The development boundary as a protected edge (module docstring): `refused_window`
    when `window` reads a session after `boundary` that no family holdout covers. A day
    past the calendar's last session counts as a session, so a window beyond the
    configured calendar is refused rather than let through."""
    if boundary is None or window.end <= boundary:
        return WindowDecision("ok", "no session after the development boundary")
    sessions = frozenset(all_sessions())
    last = max(sessions)
    day = max(window.start, boundary + timedelta(days=1))
    dead: list[date] = []
    while day <= window.end:
        if (day in sessions or day > last) and not any(
            h.start <= day <= h.end for h in family_holdouts
        ):
            dead.append(day)
        day += timedelta(days=1)
    if not dead:
        return WindowDecision("ok", "sessions after the development boundary are holdout")
    return WindowDecision(
        "refused_window",
        f"window [{window.start}, {window.end}] reads sessions after the development "
        f"boundary {boundary} outside the family's holdouts ({dead[0]}..{dead[-1]}); "
        "no flag reads them",
    )


def _prior_spends(
    conn: duckdb.DuckDBPyConnection, family: str | None, dataset_name: str, split: str
) -> tuple[tuple[PriorSpend, ...], bool]:
    """The dated spends this run's repeat check reads, scoped as the gates
    require (Definitions, Protected window), and whether `split` was already
    spent as a sealed split under `dataset_name`. Synthetic rows are excluded."""
    spends: list[PriorSpend] = []
    if family is not None:
        for trial_id, start, end in conn.execute(
            "SELECT t.trial_id, h.holdout_start, h.holdout_end FROM trials t "
            "JOIN hypotheses h USING (hypothesis_id) "
            "WHERE h.family = ? AND t.kind = 'holdout' AND NOT t.synthetic ORDER BY 1",
            [family],
        ).fetchall():
            spends.append(PriorSpend(f"backtest trial {trial_id}", Span(start, end)))
    sealed_split_spent = False
    for run_id, run_family, run_dataset, raw in conn.execute(
        "SELECT r.run_id, g.family, d.name, x.values_json FROM research_decisions x "
        "JOIN research_runs r ON r.run_id = x.run_id "
        "JOIN research_registrations g ON g.registration_id = r.registration_id "
        "JOIN research_datasets d ON d.dataset_id = r.dataset_id "
        "WHERE x.kind = 'holdout_spend' AND NOT r.synthetic ORDER BY r.run_id"
    ).fetchall():
        values = json.loads(raw)
        if family is not None and run_family == family:
            for start, end in _decode_spans(values["family_holdouts"]):
                spends.append(PriorSpend(f"research run {run_id}", Span(start, end)))
        if run_dataset == dataset_name:
            for start, end in _decode_spans(values["sealed_periods"]):
                spends.append(PriorSpend(f"research run {run_id}", Span(start, end)))
            sealed_split_spent = sealed_split_spent or values["sealed_split"] == split
    return tuple(spends), sealed_split_spent


def _chain_usage(conn: duckdb.DuckDBPyConnection, slug: str) -> tuple[int, int]:
    """`(runs, configurations declared)` over every non-synthetic run of `slug`'s
    amendment chain, whatever its outcome (req 6)."""
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(r.n_configurations_declared), 0) FROM research_runs r "
        "JOIN research_registrations g USING (registration_id) "
        "WHERE g.slug = ? AND NOT r.synthetic",
        [slug],
    ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1])


def open_run(
    conn: duckdb.DuckDBPyConnection,
    slug: str,
    dataset_id: int,
    split: str,
    config: Mapping[str, Any],
    run_by: str,
    note: str | None = None,
    synthetic: bool = False,
    flags: Flags | None = None,
    reasons: Reasons | None = None,
    *,
    configurations: int = 1,
    as_of: datetime | None = None,
    registration_id: int | None = None,
    settings: Settings | None = None,
    repo_dir: Path | None = None,
) -> RunHandle:
    """Insert a `research_runs` row for `slug`'s latest registration and return
    its handle (module docstring, "Runs"). A gate's refusal is recorded as the
    run's result and named in `handle.refusal`; it does not raise.

    `configurations` is `n_configurations_declared` (req 6, default 1); `as_of`
    is the latest as-of bound an `economic` or `return` analysis will read
    (req 4); `registration_id`, when given, must be the chain's latest (an
    amended registration opens no further runs). Raises, writing nothing, on
    `synthetic=True` on the real store, an unknown slug or dataset, a dataset of
    another name than the registration's, a dataset other than its pinned
    `sha256`, an unknown split, and a split the dataset has no rows of (`full`
    and `none` always bind every row, so their span is the dataset's)."""
    require_research(conn)
    settings = settings if settings is not None else get_settings()
    flags = flags if flags is not None else Flags()
    reasons = reasons if reasons is not None else Reasons()
    if synthetic and _is_real_store(conn, settings):
        raise RealStoreRefused("a synthetic research run is refused on the real store")
    registration = get_registration(conn, slug)
    if registration_id is not None and registration_id != registration.registration_id:
        raise ResearchError(
            f"registration {registration_id} is superseded by "
            f"{registration.registration_id}; it opens no further runs"
        )
    dataset = get_dataset(conn, dataset_id)
    if dataset.name != registration.dataset_name:
        raise ResearchError(
            f"dataset {dataset_id} is {dataset.name!r}; {slug!r} binds "
            f"{registration.dataset_name!r}"
        )
    pin = registration.dataset_sha256_pin
    if pin is not None and dataset.sha256 != pin:
        raise ResearchError(f"dataset {dataset_id} is not the pinned sha256 {pin!r}")
    if split not in RESEARCH_SPLITS:
        raise ResearchError(f"split {split!r} is not one of {RESEARCH_SPLITS}")
    if configurations < 1:
        raise ResearchError("a run declares at least one configuration")

    window = Span(registration.window_start, registration.window_end)
    dataset_span = Span(dataset.event_start, dataset.event_end)
    if split in dataset.split_spans and split not in EVERY_ROW_SPLITS:
        bound_split_span = Span(*dataset.split_spans[split])
    elif split in EVERY_ROW_SPLITS or split not in registration.splits:
        # `full` and `none` bind every row (req 5); an unlisted split is
        # `refused_split` by the gate before its span matters.
        bound_split_span = dataset_span
    else:
        raise ResearchError(f"dataset {dataset_id} has no {split!r} rows to bind")
    sealed_names, sealed_dates = _name_sealing(conn, dataset.name)
    sealed_splits = tuple(sorted(sealed_names))
    sealed_periods = tuple(Span(s, e) for s, e in sorted(sealed_dates))
    family_holdouts = _family_holdouts(conn, registration.family)
    prior, sealed_split_spent = _prior_spends(conn, registration.family, dataset.name, split)
    chain_runs, chain_configurations = _chain_usage(conn, slug)
    first_known = conn.execute(
        "SELECT MIN(known_at) FROM research_datasets WHERE name = ?", [dataset.name]
    ).fetchone()
    assert first_known is not None
    binds_sealed = is_split_sealed(split, sealed_splits) or any(
        bound_split_span.overlaps(p) for p in sealed_periods
    )

    window_decision = check_window(dataset_span, window, as_of)
    if window_decision.outcome == "ok" and family_holdouts:
        window_decision = _boundary_decision(window, family_holdouts, boundary_date(conn))
    split_decision = check_split(split, registration.splits)
    holdout = check_holdout(
        window,
        split,
        bound_split_span,
        family_holdouts,
        sealed_splits,
        sealed_periods,
        flags,
        reasons,
        prior,
        sealed_split_spent,
    )
    budget = check_budget(
        chain_runs,
        registration.budget_runs,
        chain_configurations,
        configurations,
        registration.budget_configurations,
    )
    basis = check_confirmatory(
        registration.kind,
        registration.confirmatory,
        registration.known_at,
        first_known[0],
        binds_sealed and dataset.seed == registration.seed,
        dataset.locked,
    )
    refusal: str | None = None
    message: str | None = None
    for decision in (window_decision, split_decision, holdout, budget, basis):
        if decision.outcome != "ok":
            refusal, message = decision.outcome, decision.message
            break
    spent = refusal is None and holdout.holdout_spent

    config_json = canonical_params_json(config)
    commit, dirty = code_version(repo_dir)
    max_ingested = store_max_ingested_at(conn) if registration.kind in STORE_READING_KINDS else None
    run_id = _next_run_id(conn)
    known_at = utc_now()
    insert_row(
        conn,
        "research_runs",
        {
            "run_id": run_id,
            "registration_id": registration.registration_id,
            "dataset_id": dataset.dataset_id,
            "dataset_sha256": dataset.sha256,
            "split": split,
            "config_json": config_json,
            "config_sha256": _sha256_text(config_json),
            "n_configurations_declared": configurations,
            "confirmatory": registration.confirmatory,
            "confirmatory_basis": basis.basis if refusal is None else "none",
            "code_version": commit,
            "code_dirty": dirty,
            "store_max_ingested_at": max_ingested,
            "synthetic": synthetic,
            "holdout_spent": spent,
            "holdout_repeat": spent and holdout.holdout_repeat,
            "holdout_reason": holdout.holdout_reason if spent else None,
            "run_by": run_by,
            "note": note,
            "known_at": known_at,
        },
    )
    if refusal is not None:
        _insert_result(conn, run_id, refusal, message)
    elif spent:
        record_decision(
            conn,
            kind="holdout_spend",
            run_id=run_id,
            values={
                "dataset_id": dataset.dataset_id,
                "dataset_name": dataset.name,
                "split": split,
                "family": registration.family,
                "family_holdouts": _span_list(
                    [(h.start, h.end) for h in family_holdouts if window.overlaps(h)]
                ),
                "sealed_periods": _span_list(
                    [(p.start, p.end) for p in sealed_periods if bound_split_span.overlaps(p)]
                ),
                "sealed_split": split if holdout.touches_sealed_split else None,
                "holdout_repeat": holdout.holdout_repeat,
            },
            reason=str(holdout.holdout_reason),
            made_by=run_by,
        )
    return _issue_run_handle(
        run_id=run_id,
        registration_id=registration.registration_id,
        slug=registration.slug,
        kind=registration.kind,
        family=registration.family,
        touches_returns=registration.touches_returns,
        dataset=dataset,
        split=split,
        n_configurations_declared=configurations,
        synthetic=synthetic,
        known_at=known_at,
        store_max_ingested_at=max_ingested,
        database=_database_path(conn),
        refusal=refusal,
        message=message,
    )


def attach_run(
    conn: duckdb.DuckDBPyConnection, run_id: int, settings: Settings | None = None
) -> RunHandle:
    """A handle for the unfinished run `run_id` (req 3, req 14's `--run`).
    Refuses an id absent from the store, a run with a result row, a synthetic
    run on the real store and a non-synthetic run on any other file."""
    require_research(conn)
    settings = settings if settings is not None else get_settings()
    row = conn.execute(
        "SELECT r.registration_id, r.dataset_id, r.split, r.n_configurations_declared, "
        "r.synthetic, r.known_at, r.store_max_ingested_at, g.slug, g.kind, g.family, "
        "g.touches_returns FROM research_runs r "
        "JOIN research_registrations g USING (registration_id) WHERE r.run_id = ?",
        [run_id],
    ).fetchone()
    if row is None:
        raise UnknownRegistration(f"no research run has id {run_id}")
    if _has_result(conn, run_id):
        raise RunAlreadyClosed(f"run {run_id} already has its result row")
    synthetic = bool(row[4])
    real = _is_real_store(conn, settings)
    if synthetic and real:
        raise RealStoreRefused(f"run {run_id} is synthetic; it is refused on the real store")
    if not synthetic and not real:
        raise ResearchError(f"run {run_id} is not synthetic; it attaches only on the real store")
    return _issue_run_handle(
        run_id=run_id,
        registration_id=row[0],
        slug=row[7],
        kind=row[8],
        family=row[9],
        touches_returns=row[10],
        dataset=get_dataset(conn, row[1]),
        split=row[2],
        n_configurations_declared=row[3],
        synthetic=synthetic,
        known_at=row[5],
        store_max_ingested_at=row[6],
        database=_database_path(conn),
        refusal=None,
        message=None,
    )


def close_run(
    conn: duckdb.DuckDBPyConnection, handle: RunHandle, outcome: str, message: str | None = None
) -> None:
    """Record a non-`ok` outcome (`failed`, a refusal or `abandoned`) with no
    statistics (req 8)."""
    if outcome not in CLOSE_OUTCOMES:
        raise ValueError(f"outcome {outcome!r} is not one of {sorted(CLOSE_OUTCOMES)}")
    _check_open(conn, handle)
    _insert_result(conn, handle.run_id, outcome, message)


def write_result(
    conn: duckdb.DuckDBPyConnection,
    handle: RunHandle,
    *,
    primary_value: float,
    primary_ci_low: float,
    primary_ci_high: float,
    n_observations: int,
    n_clusters: int,
    n_configurations: int,
    artifact_sha256: str,
    artifact_path: str,
    secondary: Mapping[str, Any] | None = None,
    exploratory: Mapping[str, Any] | None = None,
    message: str | None = None,
) -> Literal["ok", "failed"]:
    """Record `ok` with a verdict from the interval and return `"ok"`; or record
    `failed` with no statistics and return `"failed"` when `n_configurations`
    exceeds the declaration, the export's hash moved, or the store's latest
    `ingested_at` moved for an `economic` or `return` run (req 8). `secondary`
    holds the declared secondary metrics only."""
    _check_open(conn, handle)
    registration = _registration_where(conn, "registration_id = ?", handle.registration_id)
    assert registration is not None
    undeclared = sorted(set(secondary or {}) - set(registration.secondary))
    if undeclared:
        raise ValueError(f"undeclared secondary metrics {undeclared}; they are exploratory")
    failure: str | None = None
    if n_configurations > handle.n_configurations_declared:
        failure = CONFIGURATIONS_EXCEEDED_MESSAGE
    else:
        try:
            load_dataset(handle, verify_only=True)
        except DatasetChanged:
            failure = DATASET_CHANGED_MESSAGE
        if (
            failure is None
            and registration.kind in STORE_READING_KINDS
            and store_max_ingested_at(conn) != handle.store_max_ingested_at
        ):
            failure = STORE_CHANGED_MESSAGE
    if failure is not None:
        _insert_result(conn, handle.run_id, "failed", failure)
        return "failed"
    verdict = compute_verdict(
        n_clusters,
        registration.primary_min_clusters,
        "less" if registration.primary_direction == "less" else "greater",
        registration.primary_threshold,
        primary_ci_low,
        primary_ci_high,
    )
    _insert_result(
        conn,
        handle.run_id,
        "ok",
        message,
        primary_value=primary_value,
        primary_ci_low=primary_ci_low,
        primary_ci_high=primary_ci_high,
        n_observations=n_observations,
        n_clusters=n_clusters,
        n_configurations=n_configurations,
        secondary_json=canonical_params_json(secondary or {}),
        exploratory_json=canonical_params_json(exploratory or {}),
        verdict=verdict,
        artifact_sha256=artifact_sha256,
        artifact_path=artifact_path,
    )
    return "ok"


# --- decisions, counts and listing -------------------------------------------


def record_decision(
    conn: duckdb.DuckDBPyConnection,
    *,
    kind: DecisionKind,
    reason: str,
    values: Mapping[str, Any],
    made_by: str,
    registration_id: int | None = None,
    run_id: int | None = None,
) -> int:
    """Append a `research_decisions` row and return its id. Refuses a blank
    reason and a registration or run id that does not exist."""
    require_research(conn)
    if kind == "holdout_spend":
        for key in ("family_holdouts", "sealed_periods"):
            spans = values.get(key)
            if not isinstance(spans, (list, tuple)) or any(
                not isinstance(span, (list, tuple))
                or len(span) != 2
                or not all(isinstance(day, str) for day in span)
                or not _valid_spend_span(span[0], span[1])
                for span in spans
            ):
                raise ValueError(f"holdout_spend needs valid {key} date spans")
        if "sealed_split" not in values or (
            values["sealed_split"] is not None and not isinstance(values["sealed_split"], str)
        ):
            raise ValueError("holdout_spend needs a sealed_split string or null")
    if not reason.strip():
        raise ValueError("a research decision needs a reason")
    for table, column, value in (
        ("research_registrations", "registration_id", registration_id),
        ("research_runs", "run_id", run_id),
    ):
        if value is not None and (
            conn.execute(f"SELECT 1 FROM {table} WHERE {column} = ?", [value]).fetchone() is None
        ):
            raise UnknownRegistration(f"{column} {value} does not exist")
    decision_id = _next_id(conn, "research_decisions", "decision_id")
    insert_row(
        conn,
        "research_decisions",
        {
            "decision_id": decision_id,
            "kind": kind,
            "registration_id": registration_id,
            "run_id": run_id,
            "values_json": canonical_params_json(values),
            "reason": reason,
            "made_by": made_by,
            "known_at": utc_now(),
        },
    )
    return decision_id


def family_run_count(conn: duckdb.DuckDBPyConnection, family: str) -> int:
    """Σ `n_configurations` over `ok`, non-synthetic, non-holdout-spending runs
    of `family` whose registration touches returns, exploratory included: the
    research share of the family's N (req 9)."""
    require_research(conn)
    row = conn.execute(
        "SELECT COALESCE(SUM(x.n_configurations), 0) FROM research_results x "
        "JOIN research_runs r USING (run_id) "
        "JOIN research_registrations g ON g.registration_id = r.registration_id "
        "WHERE x.outcome = 'ok' AND NOT r.synthetic AND NOT r.holdout_spent "
        "AND g.touches_returns AND g.family = ?",
        [family],
    ).fetchone()
    assert row is not None
    return int(row[0])


def list_runs(
    conn: duckdb.DuckDBPyConnection,
    registration: str | None = None,
    family: str | None = None,
    kind: str | None = None,
    include_synthetic: bool = False,
) -> list[RunSummary]:
    """Runs newest first, optionally for one slug, family or kind; synthetic
    hidden unless asked for; a run without a result row is `unfinished`."""
    require_research(conn)
    rows = conn.execute(
        "SELECT r.run_id, r.registration_id, g.slug, g.kind, g.family, g.provenance, "
        "r.dataset_id, r.split, r.confirmatory, r.confirmatory_basis, "
        "r.n_configurations_declared, r.synthetic, r.holdout_spent, r.holdout_repeat, "
        f"r.run_by, r.note, r.known_at, COALESCE(x.outcome, '{UNFINISHED}'), x.verdict, "
        "x.message, x.known_at FROM research_runs r "
        "JOIN research_registrations g USING (registration_id) "
        "LEFT JOIN research_results x USING (run_id) "
        "WHERE (? IS NULL OR g.slug = ?) AND (? IS NULL OR g.family = ?) "
        "AND (? IS NULL OR g.kind = ?) AND (? OR NOT r.synthetic) ORDER BY r.run_id DESC",
        [registration, registration, family, family, kind, kind, include_synthetic],
    ).fetchall()
    return [RunSummary(*row) for row in rows]
