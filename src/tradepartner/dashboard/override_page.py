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
writer's answer (`Outcome`) in `session_state`, and the shell pops it with
`show_outcome` before it opens its connection, so the answer is shown once
even when the read that follows finds the store busy (a written override
never looks unwritten). That stored value is a result to display, never a
trigger: a rerun that finds it writes nothing, and only a click runs the
callback. After a write (only then: a busy or refused submit keeps what was
typed, to resubmit) the reason is emptied, so a second click once the page
has re-rendered finds no reason and is refused rather than writing the same
row twice.

**Store busy.** `StoreLockedError` (another process past
`store.lock_retry_seconds`, or at once another tab of this server
mid-render) becomes the busy outcome: nothing was written, resubmit.

**A fast double-click.** Two clicks close enough together can both reach the
server carrying the *same* pre-clear widget values: Streamlit queues each
click as its own rerun, and a rerun's `on_click` callback is handed whatever
the browser last sent for every widget, including the reason, before this
rerun's script body runs. The first rerun writes and empties
`REASON_KEY` in `session_state`, but the second click's own message still
carries the browser's pre-clear reason, which overwrites the just-emptied
value before that rerun's callback reads it — so "empty the reason after a
write" alone does not stop a same-content resubmit arriving this way. The
guard is content, not timing: `on_submit` also remembers the exact
`(kind, rebalance_session, name, reason)` (trimmed, so surrounding
whitespace alone cannot evade it) it last wrote in `session_state`
(`LAST_WRITTEN_KEY`), and refuses, without calling the writer, a later
submit whose fields match that tuple exactly, however long after the write
it arrives — not just the very next rerun. `LAST_WRITTEN_KEY` only ever
changes when a *different* submit is itself written, so a deliberate,
unmodified resubmit of exactly what was last written is refused even after
other edits in between; nothing resets it on a field change alone (an
`on_change` reset would also fire on the stale second click's own message
and disarm the guard it exists to be). `engage_kill_switch` takes neither a
session nor a name, so a repeat engagement differs only in the reason text,
if at all; a second logged engagement is harmless (the writer, not this
guard, is what would make re-engaging consequential), so the duplicate
check does not apply to it.

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
from tradepartner.store.schema import ENGAGE_KILL_SWITCH_KIND, JOURNAL_ENUMS

#: The kinds the schema allows, in its order (spec req 9).
KINDS: tuple[str, ...] = JOURNAL_ENUMS[("overrides", "kind")]

FORM_KEY = "override_form"
KIND_KEY = "override_kind"
SESSION_KEY = "override_rebalance_session"
NAME_KEY = "override_name"
REASON_KEY = "override_reason"
SUBMIT_KEY = "override_submit"
OUTCOME_KEY = "override_outcome"
#: The trimmed `(kind, rebalance_session, name, reason)` last written this
#: session (module docstring, "A fast double-click"); `None` until the first
#: write. Changes only when a *different* submit is itself written — never
#: on a field edit alone — so an exact resubmit of it stays refused until
#: something else is written in its place.
LAST_WRITTEN_KEY = "override_last_written"
#: Kinds the duplicate guard does not apply to: re-engaging the kill switch
#: a second time is harmless, and the kind takes no session or name to vary
#: the signature with (module docstring, "A fast double-click").
_DUPLICATE_GUARD_EXEMPT_KINDS = frozenset({ENGAGE_KILL_SWITCH_KIND})


class OutcomeStatus(StrEnum):
    """What a submit came to."""

    WRITTEN = "written"
    REFUSED = "refused"
    BUSY = "busy"
    DUPLICATE = "duplicate"
    """Refused by this page, before calling the writer, because its fields
    exactly match the override `on_submit` just wrote this session (module
    docstring, "A fast double-click")."""


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
    docstring). Reads the form's widgets from `session_state`, and, unless
    `kind` is exempt (`_DUPLICATE_GUARD_EXEMPT_KINDS`), first compares the
    trimmed fields against `LAST_WRITTEN_KEY`: an exact repeat of the
    override just written this session is refused as a `DUPLICATE` without
    calling the writer (module docstring, "A fast double-click"). Otherwise
    it writes through `submit` and leaves the answer for `show_outcome` to
    show once. After a write, and only then, it records this submit's
    (trimmed) fields as `LAST_WRITTEN_KEY` and empties the reason, so a
    second click once the page has re-rendered is refused for a blank
    reason instead of writing the same row again, and a same-content click
    that arrives first is refused by the duplicate check instead."""
    state = st.session_state
    kind, rebalance_session, name, reason = (
        state[KIND_KEY],
        state[SESSION_KEY],
        state[NAME_KEY],
        state[REASON_KEY],
    )
    signature = (kind, rebalance_session, (name or "").strip(), (reason or "").strip())
    if kind not in _DUPLICATE_GUARD_EXEMPT_KINDS and signature == state.get(LAST_WRITTEN_KEY):
        state[OUTCOME_KEY] = Outcome(
            OutcomeStatus.DUPLICATE,
            "Identical to the override just written in this session; nothing "
            "was written. Change a field to submit again.",
        )
        return
    outcome = submit(settings, kind, rebalance_session, name, reason)
    if outcome.status is OutcomeStatus.WRITTEN:
        state[LAST_WRITTEN_KEY] = signature
        state[REASON_KEY] = ""
    state[OUTCOME_KEY] = outcome


def show_outcome(settings: Settings) -> None:
    """Show, once, the answer `on_submit` left in `session_state`, if any.
    The shell calls this before it opens its read-only connection (module
    docstring); it reads no store."""
    outcome = st.session_state.pop(OUTCOME_KEY, None)
    if not isinstance(outcome, Outcome):
        return
    if outcome.status is OutcomeStatus.WRITTEN:
        st.success(outcome.message)
    elif outcome.status is OutcomeStatus.BUSY:
        st.info(
            f"The store at `{settings.store.path}` is busy (locked by another "
            "process, or read by another dashboard tab); nothing was written. "
            "Submit again in a moment."
        )
    elif outcome.status is OutcomeStatus.DUPLICATE:
        st.warning(outcome.message)
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
    nothing itself: the write is `on_submit`'s, before the next render, and
    its answer is `show_outcome`'s. `settings` is the shell's, so the write
    lands in the store the shell reads."""
    if settings is None:
        settings = get_settings()

    st.header("Override")
    st.caption(
        "The dashboard's only write: one logged override with a reason, consumed by "
        "the run that plans the rebalance it names (the kill switch takes neither a "
        "session nor a name). The reason must meet the window's frozen minimum length "
        "once trimmed."
    )

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
