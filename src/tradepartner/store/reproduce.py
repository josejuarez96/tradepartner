"""Reproduce a registered trial after a repair, on a temporary copy (ADR 0029, #3428, T398).

A repair (T390, T392) moves rows out of a fact table into `retracted_rows`, so every
reader sees a delete and a plain re-run of a trial after the repair no longer matches
the trial's stored numbers. A reproduction asks "what did the store hold when the trial
*started*", and the trial stores that (`started_at`, `store_max_ingested_at`,
`data_vintage`, `code_tree_sha256`). The readers carry no such axis and are not
changed: one path rebuilds that state on a throwaway **copy** of the store and replays
the trial there.

`reproduce_trial(trial_id, ...)`:

1. Reads the trial and, under one read-only connection to the real store (which is
   never written; the file hash is the same after), copies the store file and its WAL
   into a temporary directory.
2. On the copy: migrates it, `restore_for_trial`, and writes the `reproduction` marker
   (`store_markers`, widened on the copy only). The marker is what lets
   `backtest.run.replay_trial` run on a copy of the real store; `run._marked_copy` is
   unchanged, so an unmarked copy is still refused for every other caller.
3. Compares the copy's `registered_data_vintage` at the trial's cutoff with the stored
   `data_vintage`, and the current `code_tree_sha256` with the stored one. Either
   differing is "not reproducible: <which>" and the trial is not run (a changed result
   would be read as a data effect when it is not).
4. Replays the trial (`replay_trial`: its own registration, window, kind and frozen
   parameters), writing no trial, metric or lab row, and compares every stored
   base-level `strategy` metric, in full, with the replay's.
5. Deletes the copy.

A reproduction is the same trial run again, not a new trial: it adds nothing to N, and
the registry's trial and metric rows are those of the real store, untouched.

**`restore_for_trial` (on the copy, by the trial's `started_at` S).** For each
`retracted_rows` row:

1. Retracted after S and not restored (`restored_at` NULL): re-insert it with its
   original `known_at` (its own insert, not `retracted_rows.restore`, which refuses a held
   key by design). Refuses, naming the row, when the key is held by a revision ingested
   no later than the trial's `store_max_ingested_at`. A row ingested after that was not
   in the store at S and is not re-inserted.
2. Retracted at or before S and not restored: leave it out; the trial did not see it.
3. Retracted at or before S and restored after S: the live table now holds a row the
   trial never saw; delete it from the copy.
4. Retracted and restored both after S, or restored at or before S: the row is in the
   live table and was at S; nothing to do.

**Rows ingested after S.** A backfill or restamp can write a row with an old `known_at`
after the trial (`known_at <= ingested_at`) and a re-run would see it, so every fact row
with `ingested_at` after the trial's `store_max_ingested_at` is deleted from the copy. A
data release whose `made_at` is after S is left out of the copy's vintage (the repair's
own closed release is the change being undone, so counting it would refuse every real
reproduction). Revisions that withdraw a `securities` or `listings` row
(`retraction.py`) are already reproducible by `known_at` and are out of this module. A
store changed by a means that keeps no row (a raw delete outside T390 and T392) reports a
vintage mismatch.

This module reads `retracted_rows`, so it is a check-only module: no module under
`backtest/` other than `run.py`'s `replay_trial` seam, `research/`, `universe.py` or the
signals may import it (a source scan in `tests/lookahead/test_check_only_inputs.py`).
"""

from __future__ import annotations

import math
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import duckdb

from tradepartner.config import Settings, get_settings
from tradepartner.store import lab_schema, registry, retracted_rows, schema
from tradepartner.store.db import open_for_write, open_read_only, utc_now

#: `store_markers.kind` of a store made only to reproduce a trial. The kind is not in
#: `lab_schema.STORE_MARKER_KINDS` (a fixture store has no use for it): the copy's
#: table is widened to hold it, on the copy alone.
REPRODUCTION_MARKER_KIND = "reproduction"

Verdict = Literal["reproduced", "differs", "not_reproducible"]


class UnknownTrial(LookupError):
    """The store has no trial with that id."""


class HoldoutReplayRefused(ValueError):
    """A holdout trial replayed without `--holdout-repeat`."""


class RestoreForTrialRefused(ValueError):
    """A retracted row whose key a revision ingested no later than the trial already
    holds; the message names each. Nothing is reproduced."""


@dataclass(frozen=True)
class TrialFacts:
    """The `trials` row's fields a reproduction needs, and the trial's result status."""

    trial_id: int
    hypothesis_id: int
    kind: str
    started_at: datetime
    start_session: Any
    end_session: Any
    data_cutoff: datetime | None
    store_max_ingested_at: datetime | None
    data_vintage: datetime | None
    code_tree_sha256: str | None
    synthetic: bool
    status: str | None


@dataclass(frozen=True)
class RestoreSummary:
    """What `restore_for_trial` did to the copy."""

    reinserted: int = 0
    left_out: int = 0
    deleted_unseen: int = 0
    deleted_after_ingest: int = 0
    releases_left_out: int = 0


@dataclass(frozen=True)
class Reproduction:
    """The outcome of `reproduce_trial`. `verdict` is `reproduced` (every compared
    metric equal), `differs` (a metric moved; `differences` names each) or
    `not_reproducible` (`reasons` say which of the vintage, the code tree or the
    restoration stopped it; nothing was run)."""

    trial_id: int
    verdict: Verdict
    kind: str
    holdout_replay: bool
    compared: int = 0
    reasons: tuple[str, ...] = ()
    differences: tuple[str, ...] = ()
    restored: RestoreSummary = field(default_factory=RestoreSummary)


# --- the marker --------------------------------------------------------------------


def write_reproduction_marker(conn: duckdb.DuckDBPyConnection, written_by: str) -> None:
    """Mark `conn`'s store as a reproduction copy. Widens `store_markers.kind` to take
    the marker (the table is created if absent), so call it on a throwaway copy only."""
    lab_schema.create_store_markers(conn)
    lab_schema.widen_enum(conn, "store_markers", "kind", (REPRODUCTION_MARKER_KIND,))
    conn.execute(
        "INSERT INTO store_markers (kind, written_at, written_by) VALUES (?, ?, ?) "
        "ON CONFLICT (kind) DO NOTHING",
        [REPRODUCTION_MARKER_KIND, utc_now(), written_by],
    )


def has_reproduction_marker(conn: duckdb.DuckDBPyConnection) -> bool:
    """Whether the store carries the `reproduction` marker row (false when
    `store_markers` is absent)."""
    present = conn.execute(
        "SELECT 1 FROM duckdb_tables() WHERE database_name = current_database() "
        "AND schema_name = current_schema() AND table_name = 'store_markers'"
    ).fetchone()
    if present is None:
        return False
    (count,) = conn.execute(  # type: ignore[misc]
        "SELECT COUNT(*) FROM store_markers WHERE kind = ?", [REPRODUCTION_MARKER_KIND]
    ).fetchone()
    return bool(count)


# --- reading the trial -------------------------------------------------------------


def read_trial(conn: duckdb.DuckDBPyConnection, trial_id: int) -> TrialFacts:
    """The facts of trial `trial_id`; `UnknownTrial` if the store has none."""
    row = conn.execute(
        "SELECT t.trial_id, t.hypothesis_id, t.kind, t.started_at, t.start_session, "
        "t.end_session, t.data_cutoff, t.store_max_ingested_at, t.data_vintage, "
        "t.code_tree_sha256, t.synthetic, r.status "
        "FROM trials t LEFT JOIN trial_results r ON r.trial_id = t.trial_id "
        "WHERE t.trial_id = ?",
        [trial_id],
    ).fetchone()
    if row is None:
        raise UnknownTrial(f"no trial has id {trial_id}")
    return TrialFacts(*row)


def stored_base_strategy_metrics(
    conn: duckdb.DuckDBPyConnection, trial_id: int
) -> dict[str, float | None]:
    """Every stored `strategy` metric of the trial at its base cost level."""
    rows = conn.execute(
        "SELECT metric, value FROM trial_metrics WHERE trial_id = ? AND series = 'strategy' "
        "AND cost_level_kind = 'base' ORDER BY metric",
        [trial_id],
    ).fetchall()
    return {str(m): v for m, v in rows}


def stored_measured_levels(conn: duckdb.DuckDBPyConnection, trial_id: int) -> list[float]:
    """The trial's `measured` cost levels (an in-sample extra level), so a replay runs
    the same level set."""
    rows = conn.execute(
        "SELECT DISTINCT cost_per_side_bps FROM trial_metrics "
        "WHERE trial_id = ? AND cost_level_kind = 'measured' ORDER BY 1",
        [trial_id],
    ).fetchall()
    return [float(r[0]) for r in rows]


def _same(a: float | None, b: float | None) -> bool:
    """Equal in full: exactly, a missing value (NULL or NaN) equal to a missing one."""
    a_missing = a is None or math.isnan(a)
    b_missing = b is None or math.isnan(b)
    if a_missing or b_missing:
        return a_missing and b_missing
    return a == b


def compare_metrics(
    stored: dict[str, float | None], replayed: dict[str, float | None]
) -> list[str]:
    """One line per metric that is missing on a side or unequal; empty when the two
    agree in full."""
    out = []
    for name in sorted(set(stored) | set(replayed)):
        if name not in replayed:
            out.append(f"{name}: stored {stored[name]!r}, not produced by the replay")
        elif name not in stored:
            out.append(f"{name}: replayed {replayed[name]!r}, not stored")
        elif not _same(stored[name], replayed[name]):
            out.append(f"{name}: stored {stored[name]!r}, replayed {replayed[name]!r}")
    return out


# --- restore_for_trial -------------------------------------------------------------


def _ids(conn: duckdb.DuckDBPyConnection, where: str, params: list[Any]) -> list[tuple[int, str]]:
    rows = conn.execute(
        f"SELECT retraction_id, table_name FROM retracted_rows WHERE {where} "
        "ORDER BY retraction_id",
        params,
    ).fetchall()
    return [(int(i), str(t)) for i, t in rows]


def _match_ids(conn: duckdb.DuckDBPyConnection, ids: Sequence[int]) -> None:
    conn.execute("CREATE OR REPLACE TEMP TABLE _restore_ids (retraction_id BIGINT)")
    conn.executemany("INSERT INTO _restore_ids VALUES (?)", [(i,) for i in ids])


def restore_for_trial(copy_conn: duckdb.DuckDBPyConnection, trial: TrialFacts) -> RestoreSummary:
    """Rebuild, on the throwaway copy `copy_conn`, the store as `trial` saw it
    (module docstring). Never call it on the real store. Raises
    `RestoreForTrialRefused` (and re-inserts nothing) when a retracted row's key is held
    by a revision ingested no later than the trial's `store_max_ingested_at`."""
    started, max_ingested = trial.started_at, trial.store_max_ingested_at
    ingested = "CAST(row_values['ingested_at'] AS TIMESTAMPTZ)"

    # Rows the trial never saw: restored after S having been retracted at or before S.
    unseen = _ids(
        copy_conn,
        "retracted_at <= ? AND restored_at IS NOT NULL AND restored_at > ?",
        [started, started],
    )
    left_out = len(_ids(copy_conn, "retracted_at <= ? AND restored_at IS NULL", [started]))
    deleted_unseen = 0
    deleted_after = 0
    with schema.atomic(copy_conn):
        for table in retracted_rows.KEY_COLUMNS:
            ids = [i for i, t in unseen if t == table]
            if not ids:
                continue
            _match_ids(copy_conn, ids)
            try:
                [n] = copy_conn.execute(
                    f"SELECT count(*) FROM {table} t JOIN retracted_rows r "
                    f"ON {retracted_rows._key_expr(table, 't')} = r.row_key "
                    "AND t.known_at = CAST(r.row_values['known_at'] AS TIMESTAMPTZ) "
                    "WHERE r.retraction_id IN (SELECT retraction_id FROM _restore_ids)"
                ).fetchone() or (0,)
                copy_conn.execute(
                    f"DELETE FROM {table} AS t USING retracted_rows r "
                    f"WHERE {retracted_rows._key_expr(table, 't')} = r.row_key "
                    "AND t.known_at = CAST(r.row_values['known_at'] AS TIMESTAMPTZ) "
                    "AND r.retraction_id IN (SELECT retraction_id FROM _restore_ids)"
                )
                deleted_unseen += int(n)
            finally:
                copy_conn.execute("DROP TABLE IF EXISTS _restore_ids")
        # Rows ingested after the trial: a re-run would see an old-`known_at` backfill.
        for table in registry._present_fact_tables(copy_conn):
            if max_ingested is None:
                [n] = copy_conn.execute(f"SELECT count(*) FROM {table}").fetchone() or (0,)
                copy_conn.execute(f"DELETE FROM {table}")
            else:
                [n] = copy_conn.execute(
                    f"SELECT count(*) FROM {table} WHERE ingested_at > ?", [max_ingested]
                ).fetchone() or (0,)
                copy_conn.execute(f"DELETE FROM {table} WHERE ingested_at > ?", [max_ingested])
            deleted_after += int(n)
        released = _leave_out_later_releases(copy_conn, started)

    # Rows retracted after S: put back, in their own transaction (DuckDB may refuse to
    # re-insert a key it deleted in the same one).
    again_where = "retracted_at > ? AND restored_at IS NULL"
    again_params: list[Any] = [started]
    if max_ingested is not None:
        again_where += f" AND {ingested} <= ?"
        again_params.append(max_ingested)
    else:
        again_where += " AND FALSE"
    again = _ids(copy_conn, again_where, again_params)
    reinserted = 0
    with schema.atomic(copy_conn):
        if again:
            _match_ids(copy_conn, [i for i, _ in again])
            try:
                problems = _held_by_earlier_revision(copy_conn, sorted({t for _, t in again}))
                if problems:
                    raise RestoreForTrialRefused("; ".join(problems))
                for table in sorted({t for _, t in again}, key=lambda n: n != "listings"):
                    retracted_rows._insert_back(copy_conn, table)
                reinserted = len(again)
            finally:
                copy_conn.execute("DROP TABLE IF EXISTS _restore_ids")
    return RestoreSummary(reinserted, left_out, deleted_unseen, deleted_after, released)


def _held_by_earlier_revision(conn: duckdb.DuckDBPyConnection, tables: Sequence[str]) -> list[str]:
    out: list[str] = []
    for table in tables:
        rows = conn.execute(
            "SELECT DISTINCT r.row_key, r.retracted_at, t.ingested_at "
            f"FROM retracted_rows r JOIN {table} t "
            f"ON {retracted_rows._key_expr(table, 't')} = r.row_key "
            "WHERE r.table_name = ? "
            "AND r.retraction_id IN (SELECT retraction_id FROM _restore_ids) ORDER BY 1",
            [table],
        ).fetchall()
        out += [
            f"{table} key {tuple(key)!r} retracted at {at} is held by a revision "
            f"ingested at {held}, no later than the trial's store state"
            for key, at, held in rows
        ]
    return out


def _leave_out_later_releases(conn: duckdb.DuckDBPyConnection, started: datetime) -> int:
    """Delete from the copy every `data_release` decision whose `made_at` is after the
    trial's start, so the copy's vintage is the one the trial could have seen."""
    later = [r.decision_id for r in registry.data_releases(conn) if r.made_at > started]
    for decision_id in later:
        conn.execute("DELETE FROM owner_decisions WHERE decision_id = ?", [decision_id])
    return len(later)


# --- reproduce_trial ---------------------------------------------------------------


def reproduce_trial(
    trial_id: int,
    *,
    settings: Settings | None = None,
    holdout_repeat: bool = False,
    work_dir: Path | None = None,
) -> Reproduction:
    """Reproduce trial `trial_id` of `settings.store.path` (default the live store) on a
    temporary copy (module docstring) and return the `Reproduction`.

    Raises `UnknownTrial`, `HoldoutReplayRefused` (a holdout trial without
    `holdout_repeat`; a replay spends no holdout, but it is refused unless asked for) and
    `StoreLockedError` (the store is being written). A reproduction that cannot be run or
    does not match is a returned `not_reproducible` / `differs`, never a pass.
    `work_dir` is where the temporary copy is made (default the system temp)."""
    from tradepartner.backtest.run import replay_trial

    settings = settings if settings is not None else get_settings()
    source = Path(settings.store.path)
    tmp = Path(tempfile.mkdtemp(prefix="reproduce-", dir=work_dir))
    try:
        # One read-only connection for the read and the copy: no writer can start
        # meanwhile, and the real store is never opened for write.
        with open_read_only(settings) as conn:
            facts = read_trial(conn, trial_id)
            if facts.kind == "holdout" and not holdout_repeat:
                raise HoldoutReplayRefused(
                    f"trial {trial_id} is a holdout trial; pass --holdout-repeat to replay it "
                    "(the replay reproduces numbers the registry holds and writes nothing, so "
                    "it spends no holdout)"
                )
            holdout = facts.kind == "holdout"
            if facts.status != "ok":
                return Reproduction(
                    trial_id,
                    "not_reproducible",
                    facts.kind,
                    holdout,
                    reasons=(f"trial has no ok result (status {facts.status})",),
                )
            copy = tmp / source.name
            shutil.copyfile(source, copy)
            wal = source.with_name(source.name + ".wal")
            if wal.is_file():
                shutil.copyfile(wal, copy.with_name(copy.name + ".wal"))
        on_copy = settings.model_copy(
            update={"store": settings.store.model_copy(update={"path": str(copy)})}
        )
        with open_for_write(on_copy) as conn:
            schema.init_schema(conn)
            restored = restore_for_trial(conn, facts)
            write_reproduction_marker(conn, "trial-reproduce")
        reasons = _not_reproducible(on_copy, facts)
        if reasons:
            return Reproduction(
                trial_id,
                "not_reproducible",
                facts.kind,
                holdout,
                reasons=tuple(reasons),
                restored=restored,
            )
        rows = replay_trial(trial_id, copy, holdout_replay=holdout_repeat)
        replayed: dict[str, float | None] = {
            r.metric: r.value
            for r in rows
            if r.series == "strategy" and r.cost_level_kind == "base"
        }
        with open_read_only(on_copy) as conn:
            stored = stored_base_strategy_metrics(conn, trial_id)
        differences = compare_metrics(stored, replayed)
        return Reproduction(
            trial_id,
            "differs" if differences else "reproduced",
            facts.kind,
            holdout,
            compared=len(stored),
            differences=tuple(differences),
            restored=restored,
        )
    except RestoreForTrialRefused as exc:
        return Reproduction(
            trial_id,
            "not_reproducible",
            facts.kind,
            facts.kind == "holdout",
            reasons=(f"restoration refused: {exc}",),
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _not_reproducible(copy_settings: Settings, facts: TrialFacts) -> list[str]:
    """Why the trial cannot be reproduced on the restored copy: its stored data vintage
    differs from the copy's at the trial's cutoff, or its stored code tree differs from
    the current one. Empty when it can."""
    reasons: list[str] = []
    if facts.data_cutoff is None:
        return ["data_vintage: the trial has no data cutoff"]
    with open_read_only(copy_settings) as conn:
        vintage = registry.registered_data_vintage(conn, facts.data_cutoff, facts.hypothesis_id)
    if vintage != facts.data_vintage:
        reasons.append(
            f"data_vintage: stored {facts.data_vintage}, the restored copy has {vintage}"
        )
    current = registry.code_tree_sha256()
    if current is None or current != facts.code_tree_sha256:
        reasons.append(f"code_tree_sha256: stored {facts.code_tree_sha256}, current {current}")
    return reasons
