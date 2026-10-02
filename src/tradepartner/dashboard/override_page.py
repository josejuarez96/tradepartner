"""Override page (Phase 4 T69b, spec req 9; ADR 0011 Decision 2; ADR 0008 point 4).

The dashboard's **only write**: a form whose submit appends one `overrides`
row through T64b's `execution.window.override`, the same writer `paper
override` uses. The page never opens a write connection itself and never
restates the writer's checks (kind and fields, `no_window`, the trimmed
reason against the window's frozen `paper.min_override_reason_chars`); it
passes what the owner entered and shows what the writer answered.

**Submit before render.** A DuckDB write connection cannot open in-process
while the shell's read-only one is held, so the write never runs in the
script body. The form's submit button carries an `on_click` callback
(`on_submit`), which Streamlit runs at the start of the rerun, before the
script body and so before the shell opens its read-only connection; the
previous render's connection has already closed. The callback stores the
writer's answer (`Outcome`) in `session_state`, and the page body pops it to
show it once. That stored value is a result to display, never a trigger: a
rerun that finds it writes nothing, and only a click runs the callback.

**Store busy.** `StoreLockedError` (another process past
`store.lock_retry_seconds`, or at once another tab of this server
mid-render) becomes the busy outcome: nothing was written, resubmit.

What the page draws comes from the shell's read-only connection: whether a
window is open, so the owner sees before submitting that the writer would
refuse with `no_window`. The form renders whatever else the store holds or
lacks; no other table is read.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

import duckdb
import streamlit as st

from tradepartner.config import Settings, get_settings
from tradepartner.execution import window
from tradepartner.store.db import StoreLockedError, utc_now
from tradepartner.store.journal import JournalIntegrityError, JournalNotInitialised, open_window
from tradepartner.store.schema import JOURNAL_ENUMS

#: The kinds the schema allows, in its order (spec req 9).
KINDS: tuple[str, ...] = JOURNAL_ENUMS[("overrides", "kind")]

FORM_KEY = "override_form"
KIND_KEY = "override_kind"
SESSION_KEY = "override_rebalance_session"
NAME_KEY = "override_name"
REASON_KEY = "override_reason"
SUBMIT_KEY = "override_submit"
OUTCOME_KEY = "override_outcome"


class OutcomeStatus(StrEnum):
    """What a submit came to."""

    WRITTEN = "written"
    REFUSED = "refused"
    BUSY = "busy"


@dataclass(frozen=True)
class Outcome:
    """The writer's answer to one submit. `refusal` is the writer's
    machine-readable code (`WindowCommandRefused.reason`) when refused;
    `override_id` is the new row's id when written."""

    status: OutcomeStatus
    message: str
    override_id: int | None = None
    refusal: str | None = None


def submit(
    settings: Settings,
    kind: str,
    rebalance_session: date | None,
    name: str | None,
    reason: str | None,
    clock: Callable[[], datetime] = utc_now,
) -> Outcome:
    """Call T64b's `override` writer once with what the form holds and map its
    answer to an `Outcome`. A blank name is no name (`engage_kill_switch`
    takes none); the reason goes to the writer untrimmed, which trims it and
    checks it. Any other exception (a clock fault, a window whose
    `frozen_json` lacks the minimum) propagates: it is not an answer to
    show, it is a fault."""
    security_id = (name or "").strip() or None
    try:
        override_id = window.override(
            settings, clock, kind, rebalance_session, security_id, reason or ""
        )
    except window.WindowCommandRefused as exc:
        return Outcome(OutcomeStatus.REFUSED, str(exc), refusal=exc.reason)
    except StoreLockedError as exc:
        return Outcome(OutcomeStatus.BUSY, str(exc))
    target = ", ".join(str(v) for v in (rebalance_session, security_id) if v is not None)
    detail = f" ({target})" if target else ""
    return Outcome(
        OutcomeStatus.WRITTEN,
        f"Override {override_id} written: {kind}{detail}.",
        override_id=override_id,
    )


def on_submit(settings: Settings) -> None:
    """The submit button's `on_click` callback: Streamlit runs it before the
    script body, so before the shell opens its read-only connection (module
    docstring). Reads the form's widgets from `session_state`, writes through
    `submit`, and leaves the answer for the page body to show once."""
    state = st.session_state
    st.session_state[OUTCOME_KEY] = submit(
        settings,
        state[KIND_KEY],
        state[SESSION_KEY],
        state[NAME_KEY],
        state[REASON_KEY],
    )


def _render_outcome(outcome: Outcome, settings: Settings) -> None:
    if outcome.status is OutcomeStatus.WRITTEN:
        st.success(outcome.message)
    elif outcome.status is OutcomeStatus.BUSY:
        st.info(
            f"The store at `{settings.store.path}` is busy (locked by another "
            "process, or read by another dashboard tab); nothing was written. "
            "Submit again in a moment."
        )
    else:
        st.error(f"Refused ({outcome.refusal}): {outcome.message}. Nothing was written.")


def _render_window_state(conn: duckdb.DuckDBPyConnection) -> None:
    try:
        current = open_window(conn)
    except JournalNotInitialised:
        current = None
    except JournalIntegrityError as exc:
        st.error(f"{exc}; the writer refuses every override until this is resolved.")
        return
    if current is None:
        st.info("No paper window is open: the writer refuses an override with `no_window`.")


def render(conn: duckdb.DuckDBPyConnection, settings: Settings | None = None) -> None:
    """Draw the override form from the shell's read-only connection. Writes
    nothing itself: the write is `on_submit`'s, before the next render."""
    if settings is None:
        settings = get_settings()

    st.header("Override")
    st.caption(
        "The dashboard's only write: one logged override with a reason, consumed by "
        "the run that plans the rebalance it names (the kill switch takes neither a "
        "session nor a name). The reason must meet the window's frozen minimum length "
        "once trimmed."
    )

    outcome = st.session_state.pop(OUTCOME_KEY, None)
    if isinstance(outcome, Outcome):
        _render_outcome(outcome, settings)

    _render_window_state(conn)

    with st.form(FORM_KEY):
        st.selectbox("Kind", KINDS, key=KIND_KEY)
        st.date_input(
            "Rebalance session",
            value=None,
            key=SESSION_KEY,
            help="A month's last session; leave empty for engage_kill_switch.",
        )
        st.text_input(
            "Name (security id)", key=NAME_KEY, help="Leave empty for engage_kill_switch."
        )
        st.text_area("Reason", key=REASON_KEY)
        st.form_submit_button(
            "Submit override", key=SUBMIT_KEY, on_click=on_submit, args=(settings,)
        )
