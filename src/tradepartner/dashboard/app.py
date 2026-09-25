"""Streamlit dashboard shell (T21a).

Spec req 1: "every other process uses short-lived `read_only=True`
connections and shows a 'store busy' state when locked." Spec req 12:
"Page. Streamlit, rendering `health.py` output, read-only, with the busy
state."

This module is deliberately split into two layers:

- `determine_store_state` is a small, pure function (`Settings ->
  StoreState`) with no Streamlit dependency, so the "no store yet" /
  "store busy" / "ok" decision is unit-testable without rendering
  anything. It opens at most one short-lived `store.db.open_read_only`
  connection (closed before it returns) and never opens a write
  connection.
- `render_app` is the thin Streamlit layer: it calls `determine_store_state`
  once per render and renders the matching state, or the selected page
  when the store is available.

Navigation is a placeholder for now: a single "Data health" entry that
renders nothing (`render_health_placeholder`). T21 adds
`health_page.py` and changes only `_PAGES`' "Data health" entry to point
at it; nothing else here needs to change.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

import streamlit as st

from tradepartner.config import Settings, get_settings
from tradepartner.store.db import StoreLockedError, open_read_only


class StoreState(StrEnum):
    """The three states the shell renders (spec req 1, req 12)."""

    NO_STORE = "no_store"
    """`settings.store.path` does not exist on disk yet."""

    BUSY = "busy"
    """The store file exists but is locked for writing by another process
    (or, in-process, by a write connection already open)."""

    OK = "ok"
    """The store file exists and a short-lived read-only connection to it
    succeeded."""


def determine_store_state(settings: Settings) -> StoreState:
    """Return the current `StoreState` for `settings.store.path`.

    Checks file existence *before* attempting to connect: DuckDB's
    `read_only=True` connect on a missing file raises, and opening it
    even transiently is not "showing a message", so the file's presence
    is checked with `pathlib.Path.exists` first and no connection is
    attempted (and none is left behind) when it is absent. When the file
    exists, this opens exactly one short-lived `store.db.open_read_only`
    connection, does nothing with it, and closes it before returning —
    it never opens a write connection.
    """
    if not Path(settings.store.path).exists():
        return StoreState.NO_STORE
    try:
        with open_read_only(settings):
            pass
    except StoreLockedError:
        return StoreState.BUSY
    return StoreState.OK


def render_no_store(settings: Settings) -> None:
    """Render the "no store yet" message."""
    st.warning(
        f"No store found at `{settings.store.path}`. Run `tradepartner ingest` "
        "to create it, then reload this page."
    )


def render_busy(settings: Settings) -> None:
    """Render the "store busy" message."""
    st.info(
        f"The store at `{settings.store.path}` is busy (locked for writing "
        "by another process). Reload this page in a moment."
    )


def render_health_placeholder() -> None:
    """Empty placeholder for the data-health page.

    T21 replaces the body of this function with a call into
    `health_page.render(conn)`; the navigation entry itself
    (`_PAGES["Data health"]`) is the only other line T21 needs to touch.
    """


_PAGES: dict[str, Callable[[], None]] = {
    "Data health": render_health_placeholder,
}


def render_app(settings: Settings | None = None) -> None:
    """Render the full dashboard shell for one Streamlit script run.

    `settings` defaults to `get_settings()` (real environment); tests pass
    an explicit `Settings` instead, or set `STORE__PATH` in the
    environment before invoking `streamlit.testing.v1.AppTest`, since a
    module run as a Streamlit script takes no arguments.
    """
    if settings is None:
        settings = get_settings()

    st.set_page_config(page_title="TradePartner", layout="wide")
    st.title("TradePartner")

    page_name = st.sidebar.radio("Navigate", list(_PAGES))

    state = determine_store_state(settings)
    if state is StoreState.NO_STORE:
        render_no_store(settings)
        return
    if state is StoreState.BUSY:
        render_busy(settings)
        return

    _PAGES[page_name]()


render_app()
