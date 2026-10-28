"""Sweep registration and run controls for the Lab (T294, B10 path).

Render uses the shell's read-only connection. Write actions are Streamlit button
callbacks, after that connection closes; Run reopens one short read-only connection
to recheck the registry before taking the slot and spawning the desk command.
"""

from __future__ import annotations

import functools
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo

import duckdb
import streamlit as st

from tradepartner import calendar
from tradepartner.backtest import holdout, quiet, results, sweep_report
from tradepartner.backtest.frozen import frozen_values
from tradepartner.config import ENGINE_FAMILIES, Cadence, Settings
from tradepartner.dashboard import (
    backtest_page,
    book_actions,
    lab_child,
    lab_files,
    lab_register,
    lab_run,
    lab_verdict,
)
from tradepartner.dashboard.lab_files import FileKind, GitState, LabFile, RegistryState
from tradepartner.store import lab_queries, lab_registry, lab_schema, registry

DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs"
_ET = ZoneInfo("America/New_York")
_OUTCOME = "lab_action_outcome"
_NOTE = "lab_run_note"
_BUDGET = "lab_sweep_time_budget"
_POLL_ENDED = "lab_poll_ended"


def _safe_scrub(text: str, settings: Settings) -> str:
    """Show text only after both page and live secrets are removed."""
    try:
        live = book_actions.live_settings()
    except (ValueError, OSError):
        return "Text hidden because the live configuration could not be read."
    return lab_child.scrub(text, settings, live)


def _slot_path(settings: Settings) -> Path:
    return Path(f"{settings.store.path}.lab-run.json")


def register_refusal(file: LabFile) -> str | None:
    """Why this file has no Register action."""
    if file.kind is FileKind.HYPOTHESIS:
        return "Hypothesis registration is desk only, through sweep promote."
    if file.git_state is GitState.NOT_MERGED:
        return "This file is not merged, so it cannot be registered here."
    if file.registry_state is RegistryState.UNREADABLE:
        return file.message or "The file cannot be read."
    if file.message and "not initialised" in file.message:
        return file.message
    return None


def busy_sentence(busy: lab_run.Busy) -> str:
    """One card sentence naming all slot blockers."""
    return "Run busy: " + "; ".join(busy.reasons)


def status_sentence(status: lab_run.SlotStatus) -> str:
    """The slot's state in owner-facing words."""
    if status.state == "running" and status.record is not None:
        return f"The {status.record.kind} {status.record.slug} is running."
    return {
        "idle": "No Lab run is active.",
        "starting": "Another tab is starting a run; check again.",
        "running": "The run is running.",
        "ended": "The run ended.",
        "stale": "A prior run slot is stale; check the registry before another run.",
    }[status.state]


def _on_register(settings: Settings, name: str) -> None:
    """Register in a callback, after the render connection has closed."""
    try:
        st.session_state[_OUTCOME] = lab_register.register(
            settings, DOCS_ROOT, FileKind.SWEEP, name
        )
    except (lab_register.RegisterRefused, OSError, duckdb.Error) as exc:
        st.session_state[_OUTCOME] = _safe_scrub(str(exc), settings)


def _on_run(
    settings: Settings,
    slug: str,
    expected_sweep_id: int,
    expected_k: int,
    expected_excess: results.NextTrialCost,
    expected_raw: results.NextTrialCost,
) -> None:
    """Recheck and start one sweep, with one short callback-only store read."""
    from tradepartner.dashboard.app import StoreUnavailable, open_store_connection

    now = datetime.now(UTC)
    with open_store_connection(settings) as store:
        if isinstance(store, StoreUnavailable):
            st.session_state[_OUTCOME] = f"Store {store.state.value}; nothing ran."
            return
        try:
            sweep = lab_registry.sweep_by_slug(store, slug)
            if sweep is None:
                st.session_state[_OUTCOME] = f"{slug} is not registered; nothing ran."
                return
            if sweep.sweep_id != expected_sweep_id:
                st.session_state[_OUTCOME] = (
                    "Registration changed since this dialog opened; nothing ran."
                )
                return
            plan = lab_queries.plan_run(store, sweep.sweep_id)
            if not plan.variants:
                st.session_state[_OUTCOME] = "There are no variants to run; nothing ran."
                return
            k = len(plan.variants)
            rules = lab_registry.family_rules(store, sweep.family)
            floor = rules.min_sharpe_variance_annual if rules else 0.0
            excess = results.next_trial_cost(
                store, sweep.family, basis="excess_spy", added=k, floor=floor
            )
            raw = results.next_trial_cost(store, sweep.family, basis="raw", added=k, floor=floor)
            if (k, excess, raw) != (expected_k, expected_excess, expected_raw):
                st.session_state[_OUTCOME] = (
                    "Trial cost changed since this dialog opened; nothing ran. "
                    "Review the new quote."
                )
                return
            flying = lab_verdict.in_flight(store, now, settings)
            row = store.execute(
                "SELECT COALESCE(MAX(sweep_run_id), 0) FROM sweep_runs WHERE sweep_id = ?",
                [sweep.sweep_id],
            ).fetchone()
            baseline = int(row[0]) if row is not None else 0
        except (
            duckdb.Error,
            ValueError,
            lab_schema.LabNotInitialised,
            lab_registry.LabRegistryError,
        ) as exc:
            st.session_state[_OUTCOME] = f"The run check failed: {_safe_scrub(str(exc), settings)}"
            return
        note = st.session_state.get(_NOTE) or None
        budget = st.session_state.get(_BUDGET)
        _start_run(
            settings,
            slug,
            note,
            now,
            tuple(i.slug for i in flying.running),
            baseline,
            store,
            time_budget_minutes=budget,
        )


def _start_run(
    settings: Settings,
    slug: str,
    note: str | None,
    now: datetime,
    in_flight: tuple[str, ...],
    baseline: int,
    conn: duckdb.DuckDBPyConnection | None = None,
    *,
    kind: lab_child.LabKind = lab_child.LabKind.SWEEP_RUN,
    start: date | None = None,
    end: date | None = None,
    time_budget_minutes: float | None = None,
) -> None:
    """Take the slot and spawn; abandon the reservation on any pre-spawn refusal."""
    slot = _slot_path(settings)
    start_time_of = functools.partial(
        lab_run.process_start_time, timeout_seconds=settings.ui.lab_ps_timeout_seconds
    )
    taken = lab_run.take(
        slot,
        in_flight=in_flight,
        now=now,
        grace_seconds=settings.ui.lab_slot_grace_seconds,
        start_time_of=start_time_of,
    )
    if isinstance(taken, lab_run.Busy):
        st.session_state[_OUTCOME] = busy_sentence(taken)
        return
    if conn is None:
        lab_run.abandon(slot, taken)
        st.session_state[_OUTCOME] = "The registry read is unavailable; nothing ran."
        return
    try:
        if kind is lab_child.LabKind.BACKTEST:
            spawned = lab_child.spawn(settings, conn, kind, slug, note=note, start=start, end=end)
        else:
            spawned = lab_child.spawn(
                settings, conn, kind, slug, note=note, time_budget_minutes=time_budget_minutes
            )
    except (lab_child.LabRefused, OSError, duckdb.Error, ValueError) as exc:
        lab_run.abandon(slot, taken)
        st.session_state[_OUTCOME] = _safe_scrub(str(exc), settings)
        return
    try:
        lab_run.record(
            slot,
            spawned.child,
            kind=kind.value,
            slug=slug,
            baseline=baseline,
            started_at=now,
            log_path=spawned.log_path,
            taken=taken,
            start_time_of=start_time_of,
        )
        lab_run.watch(slot, spawned.child)
    except (lab_run.SlotLost, OSError, ValueError) as exc:
        st.session_state[_OUTCOME] = (
            "The child may be running, but its slot could not be recorded. "
            "Do not start another run; inspect the registry and desk log. "
            + _safe_scrub(str(exc), settings)
        )
        return
    st.session_state[_OUTCOME] = f"Started {slug}; check the status card for progress."


def _show_outcome() -> None:
    outcome = st.session_state.pop(_OUTCOME, None)
    if isinstance(outcome, str):
        st.warning(outcome)
    elif isinstance(outcome, lab_register.RegisterResult):
        (st.success if outcome.status in {"created", "unchanged"} else st.warning)(
            f"Registration {outcome.status}: {outcome.output}"
        )


@st.dialog("Register sweep")
def _register_dialog(settings: Settings, file: LabFile, preview: lab_files.SweepPreview) -> None:
    st.write(f"{preview.title} · {preview.family} · {len(preview.variants)} variants")
    st.write(
        f"In sample from {preview.in_sample_start}; holdout {preview.holdout_start} to "
        f"{preview.holdout_end} ({preview.holdout_kind})."
    )
    st.write(
        f"Selection statistic ⓘ {preview.selection_statistic}; promote at least "
        f"{preview.promote_at_least}; retire below {preview.retire_below}."
    )
    for variant in preview.variants:
        st.write(f"Variant {variant.index}: {variant.variant_params}")
    st.info(preview.registration_effect)
    st.write(
        f"From here these parameters cannot change (grid hash {preview.grid_sha256}). "
        "A change is a new registration with new variants, each counted against "
        "the family's budget."
    )
    st.button("Confirm Register", on_click=_on_register, args=(settings, file.name), type="primary")


def _cost_line(cost: results.NextTrialCost) -> str:
    basis = "excess" if cost.basis == "excess_spy" else "raw"
    note = f" {cost.note}." if cost.note else ""
    floor = " Variance is floored at the family's minimum." if cost.floored else ""
    return f"{basis} basis ⓘ: SR* ⓘ {cost.sr_star_now:.3f} to {cost.sr_star_next:.3f}.{floor}{note}"


@st.dialog("Run sweep")
def _run_dialog(
    settings: Settings,
    slug: str,
    sweep_id: int,
    family: str,
    k: int,
    excess: results.NextTrialCost,
    raw: results.NextTrialCost,
    predicted_seconds: float,
) -> None:
    st.write(
        f"This runs up to {k} variants, each a counted trial ⓘ of the `{family}` family: "
        f"N ⓘ goes from {excess.n_now} to at most {excess.n_next}."
    )
    st.write(
        "SR* ⓘ, the expected maximum Sharpe of N unskilled trials, rises at today's "
        "variance; every later deflated Sharpe ratio ⓘ in this family is deflated "
        "against it. A refused trial is logged and does not count."
    )
    st.write(_cost_line(excess))
    st.write(_cost_line(raw))
    st.caption(f"Predicted duration: about {predicted_seconds / 60:.1f} minutes.")
    st.text_area("Optional note", key=_NOTE, max_chars=settings.ui.max_reason_chars)
    if st.checkbox("Set a time budget for this sweep"):
        st.number_input("Time budget (minutes)", min_value=0.01, value=60.0, key=_BUDGET)
    else:
        st.session_state[_BUDGET] = None
    st.button(
        "Confirm Run",
        on_click=_on_run,
        args=(settings, slug, sweep_id, k, excess, raw),
        type="primary",
    )


def _hypothesis_window(conn: duckdb.DuckDBPyConnection, slug: str) -> holdout.Window:
    hypothesis = registry.get_hypothesis(conn, slug)
    if hypothesis.family not in ENGINE_FAMILIES or registry.is_sweep_variant(
        conn, hypothesis.hypothesis_id
    ):
        raise ValueError("Only a promoted, non-variant engine hypothesis can run here.")
    if lab_registry.promotion_for(conn, hypothesis.hypothesis_id) is None:
        raise ValueError("Only a promoted hypothesis can run here.")
    values = frozen_values(hypothesis)
    cadence = cast(Cadence, values["schedule.rebalance_cadence"])
    frozen = holdout.Frozen(
        hypothesis.hypothesis_id,
        hypothesis.in_sample_start,
        hypothesis.holdout_start,
        hypothesis.holdout_end,
        float(values[holdout.GAP_THRESHOLD_KEY]),
    )
    return holdout.default_in_sample_window(frozen, cadence, registry.boundary_date(conn))


def _hypothesis_start_decision(now: datetime, settings: Settings, cadence: Cadence) -> str:
    intervals = quiet.quiet_intervals_around(
        now,
        settings,
        paper_window_open=True,
        session_open=lambda day: calendar.session_open(day) if calendar.is_session(day) else None,
    )
    return quiet.start_decision(now, settings.lab.seconds_per_variant_default[cadence], intervals)


def _on_hypothesis_run(
    settings: Settings,
    slug: str,
    window: holdout.Window,
    expected_id: int,
    expected_excess: results.NextTrialCost,
    expected_raw: results.NextTrialCost,
) -> None:
    """Recheck the registered window, cost, quiet interval and slot on click."""
    from tradepartner.dashboard.app import StoreUnavailable, open_store_connection

    now = datetime.now(UTC)
    with open_store_connection(settings) as store:
        if isinstance(store, StoreUnavailable):
            st.session_state[_OUTCOME] = f"Store {store.state.value}; nothing ran."
            return
        try:
            hypothesis = registry.get_hypothesis(store, slug)
            allowed = _hypothesis_window(store, slug)
            if (
                hypothesis.hypothesis_id != expected_id
                or window.start < allowed.start
                or window.end != allowed.end
            ):
                st.session_state[_OUTCOME] = "Registration or window changed; nothing ran."
                return
            cadence = cast(Cadence, frozen_values(hypothesis)["schedule.rebalance_cadence"])
            if _hypothesis_start_decision(now, settings, cadence) != "start":
                st.session_state[_OUTCOME] = (
                    "The quiet schedule says wait; nothing ran. Check when it opens."
                )
                return
            rules = lab_registry.family_rules(store, hypothesis.family)
            floor = rules.min_sharpe_variance_annual if rules else 0.0
            excess = results.next_trial_cost(
                store, hypothesis.family, basis="excess_spy", added=1, floor=floor
            )
            raw = results.next_trial_cost(
                store, hypothesis.family, basis="raw", added=1, floor=floor
            )
            if (excess, raw) != (expected_excess, expected_raw):
                st.session_state[_OUTCOME] = (
                    "Trial cost changed; nothing ran. Review the new quote."
                )
                return
            flying = lab_verdict.in_flight(store, now, settings)
            row = store.execute("SELECT COALESCE(MAX(trial_id), 0) FROM trials").fetchone()
            baseline = int(row[0]) if row else 0
        except (
            duckdb.Error,
            ValueError,
            KeyError,
            registry.RegistryError,
            lab_schema.LabNotInitialised,
        ) as exc:
            st.session_state[_OUTCOME] = _safe_scrub(f"The run check failed: {exc}", settings)
            return
        _start_run(
            settings,
            slug,
            st.session_state.get(_NOTE) or None,
            now,
            tuple(i.slug for i in flying.running),
            baseline,
            store,
            kind=lab_child.LabKind.BACKTEST,
            start=window.start,
            end=window.end,
        )


@st.dialog("Run hypothesis")
def _hypothesis_dialog(
    settings: Settings,
    hypothesis: registry.HypothesisRecord,
    window: holdout.Window,
    excess: results.NextTrialCost,
    raw: results.NextTrialCost,
) -> None:
    start = st.date_input(
        "First in-sample session", value=window.start, min_value=window.start, max_value=window.end
    )
    st.write(f"Last in-sample session: {window.end}. The holdout stays reserved.")
    st.write(f"One counted trial ⓘ of {hypothesis.family}: N ⓘ {excess.n_now} to {excess.n_next}.")
    st.write(_cost_line(excess))
    st.write(_cost_line(raw))
    st.text_area("Optional note", key=_NOTE, max_chars=settings.ui.max_reason_chars)
    st.button(
        "Confirm Run",
        on_click=_on_hypothesis_run,
        args=(
            settings,
            hypothesis.slug,
            holdout.Window(start, window.end),
            hypothesis.hypothesis_id,
            excess,
            raw,
        ),
        type="primary",
    )


def _poll_status(settings: Settings) -> None:
    """Fragment-only slot poll; no store connection or registry read."""
    now = datetime.now(UTC)
    start_time_of = functools.partial(
        lab_run.process_start_time, timeout_seconds=settings.ui.lab_ps_timeout_seconds
    )
    state = lab_run.status(
        _slot_path(settings),
        scrub=lambda value: _safe_scrub(value, settings),
        now=now,
        grace_seconds=settings.ui.lab_slot_grace_seconds,
        start_time_of=start_time_of,
        tail_bytes=settings.ui.lab_tail_bytes,
    )
    st.subheader("Run status")
    st.write(status_sentence(state))
    if state.state == "running":
        intervals = quiet.quiet_intervals_around(
            now,
            settings,
            paper_window_open=True,
            session_open=lambda day: (
                calendar.session_open(day) if calendar.is_session(day) else None
            ),
        )
        waiting = next((i for i in intervals if i.start <= now < i.end), None)
        if waiting is not None:
            st.info(
                "Queued: run is waiting for the quiet interval to end at "
                f"{waiting.end.astimezone(_ET):%I:%M %p} ET."
            )
    if state.log_tail:
        st.code(state.log_tail, language=None)
    st.button("Check again")
    if state.state in {"running", "starting"}:
        st.session_state[_POLL_ENDED] = False
    elif state.state == "ended" and not st.session_state.get(_POLL_ENDED, False):
        st.session_state[_POLL_ENDED] = True
        st.rerun()


def _status(conn: duckdb.DuckDBPyConnection, settings: Settings, now: datetime) -> bool:
    slot = _slot_path(settings)
    start_time_of = functools.partial(
        lab_run.process_start_time, timeout_seconds=settings.ui.lab_ps_timeout_seconds
    )
    state = lab_run.status(
        slot,
        scrub=lambda text: _safe_scrub(text, settings),
        now=now,
        grace_seconds=settings.ui.lab_slot_grace_seconds,
        start_time_of=start_time_of,
        tail_bytes=settings.ui.lab_tail_bytes,
    )
    if state.state == "ended":
        lab_run.release(slot, start_time_of=start_time_of)
    last = state.record if state.state == "ended" else lab_run.read_last(slot)
    if last is not None and last.baseline is not None:
        verdict = lab_verdict.verdict(
            conn,
            kind=cast(lab_verdict.RunKind, last.kind),
            slug=last.slug,
            baseline_id=last.baseline,
            exit_code=last.exit_code,
            output=state.log_tail,
            now=now,
        )
        st.write(_safe_scrub(f"Verdict ⓘ: {verdict.outcome}. {verdict.text}", settings))
        if last.kind == "sweep_run":
            st.caption(
                "The engine's pre-registered sweep rule compares the selected variant's "
                "deflated Sharpe ratio with its promote and retire thresholds."
            )
        if verdict.message:
            st.write(_safe_scrub(verdict.message, settings))
        for variant in verdict.variants:
            line = f"Variant {variant.variant_index} ({variant.slug}): {variant.status}"
            if variant.message:
                line += f" — {_safe_scrub(variant.message, settings)}"
            st.write(line)
        if verdict.verdicts is not None:
            rules = verdict.verdicts
            st.write(
                f"promote at least {rules.promote_at_least}: "
                f"{'met' if rules.promote_at_least_met else 'not met'}; "
                f"retire below {rules.retire_below}: "
                f"{'met' if rules.retire_below_met else 'not met'}"
            )
        if verdict.verdicts_error:
            st.warning(_safe_scrub(verdict.verdicts_error, settings))
    try:
        flying = lab_verdict.in_flight(conn, now, settings)
    except (duckdb.Error, ValueError, lab_schema.LabNotInitialised):
        st.warning("The registry's in-flight state could not be read.")
        return True
    for item in flying.orphans:
        st.warning(f"Orphan {item.slug}: {item.remedy}")
    if flying.running:
        st.info("Run busy: " + ", ".join(item.slug for item in flying.running))
    return state.state in {"starting", "running"} or flying.busy


def _number(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.3f}"


def _result_reading(
    conn: duckdb.DuckDBPyConnection, settings: Settings, sweep: lab_registry.SweepRecord
) -> None:
    """Read a registered sweep in the owner's threshold, result, rule order."""
    report = sweep_report.sweep_report(conn, sweep.slug)
    spends = registry.family_holdout_spends(conn, sweep.family)
    boundary = registry.development_boundary(conn)
    st.subheader(f"Result: {sweep.slug}")
    st.write(
        "Pre-registration ⓘ fixes the test and thresholds before its trials run. "
        f"selection_statistic {sweep.selection_statistic}; promote_at_least "
        f"{sweep.promote_at_least}; retire_below {sweep.retire_below}."
    )
    st.write(
        f"Holdout ⓘ is reserved data for the final check: {sweep.holdout_start} to "
        f"{sweep.holdout_end}; {'spent' if spends else 'unspent'} "
        f"({len(spends)} family spends). registered_at {sweep.registered_at}; "
        f"grid hash {sweep.grid_sha256}."
    )
    st.caption(
        "Development boundary ⓘ is the latest owner-set date that limits new "
        f"in-sample work: {boundary.boundary if boundary else 'none recorded'}."
    )
    st.write(
        f"N ⓘ counts the family's trials: {report.family_n}. "
        "SR* ⓘ is the expected maximum Sharpe of N unskilled trials: "
        f"{_number(report.sr_star_annual)} annual today."
    )
    st.caption(
        "Deflated Sharpe ratio ⓘ adjusts a trial's Sharpe for repeated testing. "
        "dsr_excess ⓘ is that ratio on the excess-over-SPY basis."
    )
    for row in report.rows:
        st.write(
            f"Trial ⓘ is one recorded test: variant {row.variant_index} ({row.slug}), "
            f"{row.status}; today's dsr_excess {_number(row.dsr_excess)} at "
            f"today's family N {report.family_n} and SR* "
            f"{_number(report.sr_star_annual)}."
        )
    if report.verdicts is None:
        st.write(f"Sweep {report.state}; the rule has no decision until it is complete.")
    else:
        verdict = report.verdicts
        st.write(
            f"Rule verdict: argmax {verdict.argmax.slug} (variant "
            f"{verdict.argmax.variant_index}); dsr_excess recomputed at the family's "
            f"SR* high-water mark is {_number(verdict.dsr_excess_at_high_water)} "
            f"(high-water SR* {_number(verdict.sr_star_high_water_annual)}), "
            "which can differ from today's value above. "
            f"promote_at_least {sweep.promote_at_least}: "
            f"{'met' if verdict.promote_at_least_met else 'not met'}; "
            f"retire_below {sweep.retire_below}: "
            f"{'met' if verdict.retire_below_met else 'not met'}."
        )
        next_step = (
            "sweep promote"
            if verdict.promote_at_least_met
            else "sweep retire"
            if verdict.retire_below_met
            else "review without promotion"
        )
        st.write(
            "Owner decision ⓘ is the owner's explicit next step, not an automatic "
            f"action: {next_step} at the desk."
        )
    with st.expander("How it was computed"):
        if report.distribution is not None:
            d = report.distribution
            st.write(
                "Distribution of the selection statistic: "
                f"min {_number(d.minimum)}, Q1 {_number(d.q1)}, "
                f"median {_number(d.median)}, Q3 {_number(d.q3)}, "
                f"max {_number(d.maximum)}."
            )
        st.write(f"Red flags: {report.red_flag_count} variants.")
        for row in report.rows:
            message = _safe_scrub(row.message, settings) if row.message else row.status
            st.write(f"{row.slug}: red_flag {row.red_flag}; {message}.")
        choices = [row.trial_id for row in report.rows if row.trial_id is not None]
        picked = st.selectbox("Selected trial", choices, index=None, key=f"trial_{sweep.sweep_id}")
        if picked is not None:
            view = backtest_page.load_trial_view(conn, picked, settings)
            for dsr in view.dsr_rows:
                st.write(
                    f"{dsr.basis}: stored DSR {_number(dsr.stored_dsr)} "
                    f"(N at run time); today's DSR "
                    f"{_number(dsr.today.dsr if dsr.today else None)}."
                )
                if dsr.today_error:
                    st.caption(_safe_scrub(dsr.today_error, settings))
            gaps = [
                r["gap_count_share"] for r in view.rebalances if r["gap_count_share"] is not None
            ]
            st.write(
                "Survivorship gap ⓘ is the share of names missing from the "
                "point-in-time universe; maximum count share "
                f"{_number(max(gaps) if gaps else None)}."
            )


def render(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Draw the Lab from the shell's read-only connection."""
    st.header("Lab")
    _show_outcome()
    now = datetime.now(UTC)
    st.fragment(run_every=settings.ui.lab_poll_seconds)(_poll_status)(settings)
    busy = _status(conn, settings, now)
    files = lab_files.list_files(conn, settings, DOCS_ROOT)
    if not files:
        st.info("No sweep or hypothesis files are available.")
    for file in files:
        with st.container(border=True):
            st.write(
                f"{file.name} · {file.kind.value} · {file.registry_state.value} · "
                f"{file.git_state.value}"
            )
            refusal = register_refusal(file)
            if refusal:
                st.caption(refusal)
            elif file.kind is FileKind.SWEEP:
                try:
                    preview = lab_files.preview(file.path, settings, conn)
                except lab_files.LabFileRefused as exc:
                    st.warning(_safe_scrub(str(exc), settings))
                    continue
                if st.button("Register", key=f"register_{file.name}"):
                    _register_dialog(settings, file, preview)
            if file.kind is FileKind.HYPOTHESIS and file.registry_state is RegistryState.REGISTERED:
                try:
                    hypothesis = registry.get_hypothesis(conn, file.slug)
                    window = _hypothesis_window(conn, file.slug)
                    cadence = cast(Cadence, frozen_values(hypothesis)["schedule.rebalance_cadence"])
                    decision = _hypothesis_start_decision(now, settings, cadence)
                    if busy:
                        st.caption("Run is unavailable while the Lab slot is busy.")
                    elif decision != "start":
                        interval = quiet.next_quiet_interval(
                            now,
                            settings,
                            paper_window_open=True,
                            session_open=lambda day: (
                                calendar.session_open(day) if calendar.is_session(day) else None
                            ),
                        )
                        opening = (
                            interval.end.astimezone(_ET).strftime("%Y-%m-%d %I:%M %p ET")
                            if interval
                            else "after the next quiet interval"
                        )
                        st.caption(f"Run waits for the quiet schedule; opens {opening}.")
                    else:
                        rules = lab_registry.family_rules(conn, hypothesis.family)
                        floor = rules.min_sharpe_variance_annual if rules else 0.0
                        excess = results.next_trial_cost(
                            conn, hypothesis.family, basis="excess_spy", added=1, floor=floor
                        )
                        raw = results.next_trial_cost(
                            conn, hypothesis.family, basis="raw", added=1, floor=floor
                        )
                        if st.button("Run", key=f"run_{file.name}"):
                            _hypothesis_dialog(settings, hypothesis, window, excess, raw)
                except (
                    registry.RegistryError,
                    duckdb.Error,
                    ValueError,
                    KeyError,
                    lab_schema.LabNotInitialised,
                ) as exc:
                    st.caption(_safe_scrub(f"Hypothesis Run unavailable: {exc}", settings))
            if (
                file.kind is not FileKind.SWEEP
                or file.registry_state is not RegistryState.REGISTERED
            ):
                continue
            sweep = lab_registry.sweep_by_slug(conn, file.slug)
            if sweep is None:
                continue
            try:
                _result_reading(conn, settings, sweep)
            except (duckdb.Error, ValueError, lab_schema.LabNotInitialised) as exc:
                st.warning(_safe_scrub(f"Result unavailable: {exc}", settings))
            if busy:
                st.caption("Run is unavailable while the Lab slot is busy.")
                continue
            plan = lab_queries.plan_run(conn, sweep.sweep_id)
            k = len(plan.variants)
            if not k:
                st.caption("No variants are awaiting a plain run.")
                continue
            rules = lab_registry.family_rules(conn, sweep.family)
            floor = rules.min_sharpe_variance_annual if rules else 0.0
            excess = results.next_trial_cost(
                conn, sweep.family, basis="excess_spy", added=k, floor=floor
            )
            raw = results.next_trial_cost(conn, sweep.family, basis="raw", added=k, floor=floor)
            predicted = sum(
                settings.lab.seconds_per_variant_default[p.cadence] for p in plan.variants
            )
            if st.button("Run", key=f"run_{file.name}"):
                _run_dialog(
                    settings,
                    sweep.slug,
                    sweep.sweep_id,
                    sweep.family,
                    k,
                    excess,
                    raw,
                    predicted,
                )
