"""Streamlit dashboard shell (T21a).

Spec req 1: "every other process uses short-lived `read_only=True`
connections and shows a 'store busy' state when locked." Spec req 12:
"Page. Streamlit, rendering `health.py` output, read-only, with the busy
state."

This module is deliberately split into two layers:

- `open_store_connection` is a small, pure context manager (`Settings ->
  Iterator[StoreUnavailable | duckdb.DuckDBPyConnection]`) with no
  Streamlit dependency, so the "no store yet" / "store busy" / "store
  unreadable" / "ok" decision is unit-testable without rendering
  anything. It opens **at most one** short-lived read-only connection per
  call, for the whole lifetime of the `with` block, and never opens a
  write connection. Only errors raised while *opening* that connection
  are turned into a `StoreUnavailable`; an error raised by whatever the
  caller does with the yielded connection propagates unchanged.
- `render_app` is the thin Streamlit layer: it opens exactly one
  connection per render and, if the store is available, renders the
  selected page *inside* that connection's `with` block, passing the
  connection to the page so a page never has to open its own.

Navigation maps each entry to a page's `render(conn)`: "Data health"
(`health_page`, T21), "Backtest" (T43), "Trial registry" (T44) and
"Operations" (`ops_page`, T69) and "Override" (`override_page`, T69b).

**Submit before render (ADR 0011 Decision 2, #273; T69b).** The override page
is the dashboard's only write, and a DuckDB write connection cannot open
in-process while this shell's read-only one is held. So the write never runs
inside `render_app`'s `with` block: the page's submit button carries an
`on_click` callback (`override_page.on_submit`), which Streamlit runs at the
start of the rerun, before this script body, so the previous render's
connection has closed and none is open while T64b's `override` writer runs.
`render_app` then shows the writer's answer (`override_page.show_outcome`)
*before* it opens its one read-only connection, so a write that committed is
reported even when that read finds the store busy, and hands its own
`Settings` to the override page so the write lands in the store it reads.
A `session_state` flag consumed by this body is not the mechanism (a flag
left set would write on every later rerun); the stored answer is only
displayed. `StoreLockedError` from the writer (another process
past `store.lock_retry_seconds`, or at once another tab mid-render) is the
busy state; this module still never imports `open_for_write`.

**Server options (ADR 0011, 2026-09-26, #273).** Streamlit is itself an HTTP
and websocket server, bound to every interface unless told otherwise, and
`.streamlit/config.toml` is only a default: `STREAMLIT_SERVER_ADDRESS` or a
`--server.address` flag would override it. So `render_app` refuses to render
anything else unless the *running* `server.address` is `localhost` or
`127.0.0.1` and `browser.gatherUsageStats` is false, and says what to fix
rather than silently opening a network boundary the owner never chose.
`_server_options_ok` is the pure check (`Settings` never enters it: these are
Streamlit's own options, read through `st.get_option`), so the refusal is
unit-testable without a page render.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import duckdb
import streamlit as st

from tradepartner.config import Settings, get_settings
from tradepartner.dashboard import (
    backtest_page,
    health_page,
    ops_page,
    override_page,
    trials_page,
)
from tradepartner.store.db import StoreLockedError, open_read_only

#: ADR 0011 point 3: the only addresses `render_app` accepts for `server.address`.
_ALLOWED_SERVER_ADDRESSES = frozenset({"localhost", "127.0.0.1"})


class StoreState(StrEnum):
    """The non-connection outcomes `open_store_connection` can yield
    (spec req 1, req 12)."""

    NO_STORE = "no_store"
    """`settings.store.path` does not exist on disk yet."""

    BUSY = "busy"
    """The store file exists but is locked for writing by another process
    (or, in-process, by a write connection already open)."""

    UNREADABLE = "unreadable"
    """The store file exists and is not locked, but connecting to it
    failed for some other reason (e.g. it is not a valid DuckDB file)."""


@dataclass(frozen=True)
class StoreUnavailable:
    """What `open_store_connection` yields when there is no connection to
    hand to the caller this render. `detail` is a human-readable reason,
    set only for `StoreState.UNREADABLE`."""

    state: StoreState
    detail: str = ""


@contextmanager
def open_store_connection(
    settings: Settings,
) -> Iterator[StoreUnavailable | duckdb.DuckDBPyConnection]:
    """Yield either a `StoreUnavailable` or a single short-lived,
    read-only connection to the store, opened once for the lifetime of
    this `with` block and closed when it exits.

    Checks file existence *before* attempting to connect: DuckDB's
    `read_only=True` connect on a missing file raises, and opening it
    even transiently is not "showing a message", so the file's presence
    is checked with `pathlib.Path.exists` first and no connection is
    attempted (and none is left behind) when it is absent.

    When the file exists, this drives `store.db.open_read_only`'s context
    manager by hand (`__enter__`/`__exit__`) rather than with a plain
    `with` statement, so only an exception raised while *opening* the
    connection is caught here: `StoreLockedError` yields `BUSY`, and any
    other `duckdb.Error` (e.g. the file exists but is not a valid DuckDB
    database) yields `UNREADABLE` with the error text. An exception
    raised by whatever the caller does with the yielded connection is
    re-raised through `open_read_only`'s own `__exit__` (which still
    closes the connection) and propagates to the caller unchanged — this
    function never swallows a page's own errors.
    """
    if not Path(settings.store.path).exists():
        yield StoreUnavailable(StoreState.NO_STORE)
        return

    read_only_ctx = open_read_only(settings)
    try:
        conn = read_only_ctx.__enter__()
    except StoreLockedError:
        yield StoreUnavailable(StoreState.BUSY)
        return
    except duckdb.Error as exc:
        yield StoreUnavailable(StoreState.UNREADABLE, detail=str(exc))
        return

    try:
        yield conn
    except BaseException:
        if not read_only_ctx.__exit__(*sys.exc_info()):
            raise
    else:
        read_only_ctx.__exit__(None, None, None)


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


def render_unreadable(settings: Settings, detail: str) -> None:
    """Render the "store unreadable" message."""
    st.error(f"The store at `{settings.store.path}` could not be read: {detail}")


_OVERRIDE_PAGE = "Override"

_PAGES: dict[str, Callable[[duckdb.DuckDBPyConnection], None]] = {
    "Data health": health_page.render,
    "Backtest": backtest_page.render,
    "Trial registry": trials_page.render,
    "Operations": ops_page.render,
    _OVERRIDE_PAGE: override_page.render,
}


def _server_options_ok(address: str | None, gather_usage_stats: bool | None) -> tuple[bool, str]:
    """Whether the *running* Streamlit options satisfy ADR 0011 point 3, and if
    not, a message naming what to fix. Pure, so the refusal is testable without
    a Streamlit script run (module docstring)."""
    if address in _ALLOWED_SERVER_ADDRESSES and gather_usage_stats is False:
        return True, ""
    return False, (
        f"Refusing to render: server.address is {address!r} and "
        f"browser.gatherUsageStats is {gather_usage_stats!r}. ADR 0011 requires "
        f"server.address to be one of {sorted(_ALLOWED_SERVER_ADDRESSES)} and "
        "browser.gatherUsageStats to be false, so the dashboard's one write "
        "(the override form) is never reachable from another machine. Fix "
        "`.streamlit/config.toml` (or an overriding STREAMLIT_SERVER_ADDRESS / "
        "--server.address) and reload."
    )


def render_app(settings: Settings | None = None) -> None:
    """Render the full dashboard shell for one Streamlit script run.

    Opens exactly one store connection for the whole render (via
    `open_store_connection`) and, when the store is available, renders
    the selected page inside that connection's `with` block, passing the
    connection to it — a page never opens the store itself.

    `settings` defaults to `get_settings()` (real environment); tests pass
    an explicit `Settings` instead, or set `STORE__PATH` in the
    environment before invoking `streamlit.testing.v1.AppTest`, since a
    module run as a Streamlit script takes no arguments.
    """
    if settings is None:
        settings = get_settings()

    st.set_page_config(page_title="TradePartner", layout="wide")
    st.title("TradePartner")

    ok, message = _server_options_ok(
        st.get_option("server.address"), st.get_option("browser.gatherUsageStats")
    )
    if not ok:
        st.error(message)
        return

    page_name = st.sidebar.radio("Navigate", list(_PAGES))
    override_page.show_outcome(settings)

    with open_store_connection(settings) as store:
        if isinstance(store, StoreUnavailable):
            if store.state is StoreState.NO_STORE:
                render_no_store(settings)
            elif store.state is StoreState.BUSY:
                render_busy(settings)
            else:
                render_unreadable(settings, store.detail)
            return

        if page_name == _OVERRIDE_PAGE:
            override_page.render(store, settings)
        else:
            _PAGES[page_name](store)


if __name__ == "__main__":
    render_app()
