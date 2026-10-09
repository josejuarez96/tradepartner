"""Run orchestration and refusals (backtest spec reqs 9-11, 14; plan T39).

`run_hypothesis` turns one request into one trial, in this order:

1. **Open** (one write chunk, committed on its own): migrate the store
   (`schema.init_schema`, as every writing command does), load the slug's frozen
   `Settings` (`hypothesis.load_frozen`), resolve the window (a missing end is
   the in-sample default's), read the family's holdout spends and ask
   `holdout.decide` with no gap series, then `registry.open_trial` with the kind,
   repeat mark and reasons that answer gives. The decision is pure and reads no
   market data, so it can precede the insert: the `trials` row is append-only,
   so its kind must be known when it is written. A refusal at this point
   (`refused_window`, `refused_holdout`, or `refused_gap` for `--override-gap`
   without a reason, before any gap read) is opened as `in_sample` with no
   holdout reason, so it never counts as a holdout spend. A holdout run also
   appends a `holdout_spend` owner decision.
2. **Refuse** those at once: `close_trial` in its own chunk, no `StoreProvider`
   built.
3. **Gap gate** (holdout runs): `survivorship_gap` at the window's rebalance
   closes and nothing else, then `decide` again; `refused_gap` closes the trial,
   an override the gate needed appends a `gap_override` owner decision.
4. **Run** the engine over a `StoreProvider` whose factory opens one short-lived
   read-only connection per step, at the base level and every
   `costs.sensitivity_per_side_bps` level, from the frozen `Settings`.
5. **Write** through `results.write_results` in one chunk; it records `failed`
   (`store changed during run`) when the data vintage at the trial's cutoff
   moved since the open (`registry.write_result`, #1232).

Any exception after the open closes the trial `failed` with the exception's type
and text as the message, and returns the formatted traceback in
`RunOutcome.error` for the caller to print, so every trial ends with a result
row unless the process dies (then it lists as `unfinished`). An exception before
the open (an unregistered slug, a synthetic trial on the real store) raises:
there is no trial yet.

Only `write_results` compares the data vintage at the cutoff with the one recorded
at the open. A `refused_gap`, and the gap values stored with a `gap_override`, are
not compared: an ingest after the open can at worst leave a refusal that newer
data would have passed (a refusal spends nothing) or override values read from
the newer data, whose run then fails at the write. Conservative, not exact.

DuckDB allows one connection mode per file per process, so no read-only
connection is open while a write chunk runs: the provider releases its step
connection before each write.

`store_path` defaults to `settings.store.path` and the CLI never sets it; tests,
the oracle and `backtest-runner` pass a temp-file store. Registry checks always
compare against the live `settings.store.path`, so `synthetic=True` is refused on
the real store whatever path reaches it.

**Fixture marker** (strategy-lab spec, Definitions, Fixture marker, amendment
2026-10-05; plan T110). A `store_path` that is the live `settings.store.path` (by
file identity, `registry.is_real_store`) is the real store, not a copy: it keeps
the Phase 3 behaviour for its kind (the paper window's tracking trial passes it).
Any other `store_path` must carry the `store_markers` `fixture` row
(`lab_schema.has_fixture_marker`), checked before the store is migrated or any
trial opened (`registry.UnmarkedStoreRefused` otherwise), and every trial on it
is opened `synthetic=True`, so a copy of the real store can never host an
uncounted run.

**Strategy-lab rules** (strategy-lab spec req 5; plan T110). Only when the store
is lab-initialised (`lab_schema.is_lab_initialised`) does the open read the
hypothesis's lab state (a sweep variant, pre-lab, promoted) and the family rules'
`max_family_holdout_spends` into a `holdout.LabState` for `decide`; otherwise
`decide` gets `lab=None` and the Phase 3 rules apply unchanged. A variant is
`refused_variant`: a trial with that status, no provider call. A family without a
rules row (none exists once the lab migration has run) is capped at the live
`lab.max_family_holdout_spends`.

**The development boundary** (ADR 0016 point 2; data-foundation plan T142b). The open
reads `registry.development_boundary` once and passes it to the default window
(`holdout.default_in_sample_window`) and to both `decide` calls, so no in-sample run
reads a session after it and a spend lies inside the holdout; with no boundary row
every rule is as before. `Frozen.registered_on` is the family's first registration day
(`registry.family_registered_on`), so a forward holdout's tracking window may start
inside it (`holdout.tracking_start`).
"""

from __future__ import annotations

import math
import traceback
from dataclasses import dataclass
from datetime import date
from functools import partial
from pathlib import Path
from typing import Literal, cast

import duckdb

from tradepartner.backtest import engine
from tradepartner.backtest.engine import BacktestResult
from tradepartner.backtest.holdout import (
    Decision,
    Flags,
    Frozen,
    LabState,
    Reasons,
    Window,
    decide,
    default_in_sample_window,
    gap_sessions,
)
from tradepartner.backtest.hypothesis import load_frozen
from tradepartner.backtest.results import write_results
from tradepartner.backtest.schedule import read_time
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.cli_record import _configured_secrets, scrub_text
from tradepartner.config import ENGINE_FAMILIES, Cadence, Settings, get_settings
from tradepartner.store import lab_registry, lab_schema, registry, schema
from tradepartner.store.db import open_for_write, open_read_only

Results = dict[float, BacktestResult]
Status = Literal[
    "ok", "failed", "refused_window", "refused_holdout", "refused_gap", "refused_variant"
]

#: The owner, unless a caller (`backtest-runner`, a test) names itself.
DEFAULT_RUN_BY = "owner"


@dataclass(frozen=True)
class RunOutcome:
    """One trial's outcome. `results` are the per-level results written with an
    `ok` row, else None; `error` is the formatted traceback of the exception that
    made the trial `failed` (None for a store change, which raises nothing), for
    the caller to print. The one-line message is in `trial_results`."""

    trial_id: int
    status: Status
    results: Results | None = None
    error: str | None = None


def _on_store(live: Settings, store_path: Path | str | None) -> Settings:
    """`live` with `store.path` set to `store_path` (default: unchanged), for
    the connection factories only."""
    if store_path is None:
        return live
    store = live.store.model_copy(update={"path": str(store_path)})
    return live.model_copy(update={"store": store})


def _marked_copy(conn: duckdb.DuckDBPyConnection, live: Settings) -> bool:
    """Whether a `store_path` run goes on a marked fixture store (module docstring,
    "Fixture marker"): False for the real store, True for a marked store, and
    `registry.UnmarkedStoreRefused` for any other store."""
    if registry.is_real_store(conn, live):
        return False
    if not lab_schema.has_fixture_marker(conn):
        raise registry.UnmarkedStoreRefused(
            "store_path names a store that is neither settings.store.path nor a fixture "
            "store (no store_markers fixture row): a copy of the real store is refused"
        )
    return True


def _lab_state(
    conn: duckdb.DuckDBPyConnection, hypothesis: registry.HypothesisRecord, live: Settings
) -> LabState | None:
    """The hypothesis's strategy-lab inputs to `decide`, or None on a store without
    the lab tables, which keeps the Phase 3 rules (module docstring)."""
    if not lab_schema.is_lab_initialised(conn):
        return None
    hypothesis_id = hypothesis.hypothesis_id
    variant = conn.execute(
        "SELECT 1 FROM sweep_variants WHERE hypothesis_id = ? LIMIT 1", [hypothesis_id]
    ).fetchone()
    rules = lab_registry.family_rules(conn, hypothesis.family)
    return LabState(
        is_variant=variant is not None,
        is_pre_lab=lab_registry.is_pre_lab(conn, hypothesis_id),
        promoted=lab_registry.promotion_for(conn, hypothesis_id) is not None,
        max_family_holdout_spends=(
            rules.max_family_holdout_spends
            if rules is not None
            else live.lab.max_family_holdout_spends
        ),
    )


def _window(
    frozen: Frozen,
    start: date | None,
    end: date | None,
    cadence: Cadence,
    boundary: date | None = None,
) -> Window:
    default = default_in_sample_window(frozen, cadence, boundary)
    return Window(
        start if start is not None else default.start,
        end if end is not None else default.end,
    )


def _close(
    store: Settings,
    handle: registry.TrialHandle,
    status: Status,
    message: str,
    error: str | None = None,
) -> RunOutcome:
    """Close the trial with `message`, scrubbed of every configured secret at
    write time (an exception's words can carry one; #342), as `ingest._clean`
    does for run rows. `error`, the traceback, is returned, not stored; the CLI
    scrubs it when it prints it."""
    message = scrub_text(message, secrets=_configured_secrets(store))[0]
    with open_for_write(store) as conn:
        registry.close_trial(conn, handle, status, message)
    return RunOutcome(handle.trial_id, status, error=error)


def _gap_values(decision: Decision, frozen: Frozen, series: dict[date, float]) -> dict[str, object]:
    """The gap at the time of an override, JSON-safe (a NaN share is stored as null)."""
    return {
        "threshold": frozen.gap_count_share_threshold,
        "count_share": {
            s.isoformat(): None if math.isnan(share) else share for s, share in series.items()
        },
        "breaches": [s.isoformat() for s, _ in decision.gap_breaches],
    }


def run_hypothesis(
    slug: str,
    start: date | None,
    end: date | None,
    flags: Flags,
    synthetic: bool = False,
    store_path: Path | str | None = None,
    *,
    reasons: Reasons | None = None,
    note: str | None = None,
    run_by: str = DEFAULT_RUN_BY,
    kind: Literal["tracking"] | None = None,
) -> RunOutcome:
    """Run `slug`'s latest registration over `[start, end]` as one trial and return
    its `RunOutcome` (module docstring).

    `start` and `end` default to the in-sample window's, unless `kind="tracking"`
    (Phase 4 spec req 10; plan T65b), in which case both are required (there is no
    default tracking window: `paper report` names `[T_0, last completed T]` itself)
    and `holdout.decide`'s tracking rule (T53) replaces the window, holdout and gap
    rules; `flags` and `reasons` are not read, and the trial is opened `kind=tracking`,
    so it is never counted in a family's `N`, `V` or holdout spends
    (`registry.family_sharpes` and `family_holdout_spends` both filter on `trials.kind`).
    The outcome carries the recorded status, the results written with an `ok` row
    and, on `failed`, the formatted traceback; the one-line message is in
    `trial_results`. Raises `registry.UnknownHypothesis` for an unregistered slug,
    `registry.RealStoreRefused` for `synthetic=True` on `settings.store.path`,
    `registry.UnmarkedStoreRefused` for a `store_path` that is neither
    `settings.store.path` nor a marked fixture store, `ValueError` for a family
    outside `config.ENGINE_FAMILIES` (#1053), and
    `ValueError` for `kind="tracking"` with a missing `start` or `end`, all before
    any trial exists.
    """
    tracking = kind == "tracking"
    if tracking:
        if start is None or end is None:
            raise ValueError("a tracking run needs an explicit start and end")
        tracking_window = Window(start, end)
    reasons = reasons if reasons is not None else Reasons()
    live = get_settings()
    store = _on_store(live, store_path)
    with open_for_write(store) as conn:
        if store_path is not None and _marked_copy(conn, live):
            synthetic = True
        schema.init_schema(conn)
        hypothesis = registry.get_hypothesis(conn, slug)
        if hypothesis.family not in ENGINE_FAMILIES:
            raise ValueError(
                f"{slug!r} is in family {hypothesis.family!r}, which the engine cannot run "
                f"yet (engine families: {', '.join(ENGINE_FAMILIES)})"
            )
        params = load_frozen(conn, slug, settings=live)
        frozen = Frozen.from_hypothesis(
            hypothesis, registered_on=registry.family_registered_on(conn, hypothesis.family)
        )
        cadence = params.schedule.rebalance_cadence
        boundary = registry.boundary_date(conn)
        window = tracking_window if tracking else _window(frozen, start, end, cadence, boundary)
        spends = registry.family_holdout_spends(conn, hypothesis.family)
        lab = _lab_state(conn, hypothesis, live)
        decision = decide(
            window,
            frozen,
            flags,
            reasons,
            None,
            spends,
            tracking=tracking,
            cadence=cadence,
            lab=lab,
            boundary=boundary,
        )
        sessions = gap_sessions(window, cadence)
        refused = decision.outcome not in ("run", "needs_gap")
        holdout = decision.kind == "holdout" and not refused
        # A refusal is always recorded `in_sample` (it spent nothing), whatever
        # `decision.kind` says: `refused_gap` keeps the holdout `Decision` its gate
        # started from (`holdout.decide`'s `replace(spend, outcome="refused_gap", ...)`),
        # so `decision.kind == "holdout"` there even though the run never happened.
        trial_kind = decision.kind if not refused and decision.kind is not None else "in_sample"
        handle = registry.open_trial(
            conn,
            hypothesis_id=hypothesis.hypothesis_id,
            kind=trial_kind,
            start_session=window.start,
            end_session=window.end,
            data_cutoff=read_time(sessions[-1], cadence) if sessions else None,
            synthetic=synthetic,
            run_by=run_by,
            holdout_repeat=holdout and decision.holdout_repeat,
            holdout_reason=decision.holdout_reason if holdout else None,
            gap_override_reason=reasons.gap_reason if holdout and flags.override_gap else None,
            note=note,
            settings=live,
        )
        if holdout and decision.holdout_reason is not None:
            registry.record_decision(
                conn,
                kind="holdout_spend",
                reason=decision.holdout_reason,
                values={
                    "start": window.start.isoformat(),
                    "end": window.end.isoformat(),
                    "holdout_repeat": decision.holdout_repeat,
                },
                hypothesis_id=hypothesis.hypothesis_id,
                trial_id=handle.trial_id,
            )

    if refused:
        return _close(store, handle, cast(Status, decision.outcome), decision.message)
    try:
        with StoreProvider(partial(open_read_only, store), handle, params) as provider:
            if decision.outcome == "needs_gap":
                series = {
                    s: provider.survivorship_gap(read_time(s, cadence)).count_share
                    for s in decision.gap_sessions
                }
                provider.end_step()
                decision = decide(
                    window,
                    frozen,
                    flags,
                    reasons,
                    series,
                    spends,
                    cadence=cadence,
                    lab=lab,
                    boundary=boundary,
                )
                if decision.outcome != "run":
                    return _close(store, handle, cast(Status, decision.outcome), decision.message)
                if decision.gap_override_reason is not None:
                    with open_for_write(store) as conn:
                        registry.record_decision(
                            conn,
                            kind="gap_override",
                            reason=decision.gap_override_reason,
                            values=_gap_values(decision, frozen, series),
                            hypothesis_id=hypothesis.hypothesis_id,
                            trial_id=handle.trial_id,
                        )
            levels = sorted({params.costs.per_side_bps, *params.costs.sensitivity_per_side_bps})
            results = engine.run(
                params, provider, window.start, window.end, handle, levels, family=hypothesis.family
            )
        with open_for_write(store) as conn:
            status = write_results(conn, handle, results, params)
    except Exception as exc:
        error = "".join(traceback.format_exception(exc))
        return _close(store, handle, "failed", f"{type(exc).__name__}: {exc}", error)
    if status == "ok":
        return RunOutcome(handle.trial_id, "ok", results)
    return RunOutcome(handle.trial_id, "failed")
