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
- `render_app` is the thin Streamlit layer. Most pages render inside one
  read-only connection. Data health probes availability, closes that
  connection, then opens one read-only connection per query and closes it
  before Python aggregation or drawing.

Navigation is `st.navigation(position="top")` (Streamlit 1.64; an empty-string
section header puts its pages first, and each other section is a drop-down):
Home (`home_page`), Research (`research_page`, T83c), Books (`book_page`) and a
"Details" group holding "Data health" (`health_page`, T21), "Backtest" (T43),
"Trial registry" (T44), "Operations" (`ops_page`, T69), "Trades", "Performance"
(`performance_page`, T252) and "Override" (`override_page`, T69b). Each
`st.Page` runs one closure that does the store flow below for its page.
Data health releases its probe before drawing; other pages draw inside the
read connection. Home and Books take `(conn, settings)` (T274 routes them
once; T275 and T276 fill in the bodies without editing this module).

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

`show_outcome` is only called when the Override page is the one selected
this rerun: a write can only be queued by that page's own submit button, so
that page is already showing when `on_click` runs, but a render that leaves
it pending (store busy, store unreadable, no store, or an exception) does
not reach this point, and if the owner navigates to another page before the
next successful render, showing the old answer under a page that never
wrote it would mislead. `show_outcome` pops, not peeks, so the stored
answer is untouched when this skips it, and is still shown, once, the next
time the owner selects the Override page.

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

import html
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

import duckdb
import polars as pl
import streamlit as st

from tradepartner.config import Settings, get_settings
from tradepartner.dashboard import (
    backtest_page,
    book_page,
    health_page,
    home_page,
    lab_page,
    ops_page,
    override_page,
    performance_page,
    research_page,
    theme,
    trades_page,
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

_PageRenderer = Callable[[duckdb.DuckDBPyConnection, Settings], None]

#: Section header for the pages that sit in the top nav's "Details" drop-down.
_DETAILS = "Details"

#: Pages shown straight in the top bar, in order: title -> renderer.
_TOP_PAGES: dict[str, _PageRenderer] = {
    "Home": home_page.render,
    "Research": lambda conn, _settings: research_page.render(conn),
    "Lab": lab_page.render,
    "Books": book_page.render,
}

#: The Details drop-down, in order: title -> renderer.
_DETAIL_PAGES: dict[str, _PageRenderer] = {
    "Data health": lambda conn, _settings: health_page.render(conn),
    "Backtest": lambda conn, _settings: backtest_page.render(conn),
    "Trial registry": lambda conn, _settings: trials_page.render(conn),
    "Operations": lambda conn, _settings: ops_page.render(conn),
    "Trades": lambda conn, _settings: trades_page.render(conn),
    "Performance": lambda conn, _settings: performance_page.render(conn),
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


def _page_path(title: str, *, default: bool = False) -> str:
    """The URL path of a page: `/` for the default page, else `/<title-slug>`."""
    return "/" if default else "/" + title.lower().replace(" ", "-")


def _phone_bar_html() -> str:
    """The phone's fixed bottom bar: one link per top-nav page, in order, by page URL.
    CSS (`theme.phone_css`) hides it above the phone breakpoint; the top nav stays."""
    links = "".join(
        f'<a href="{_page_path(title, default=(i == 0))}" target="_self">{html.escape(title)}</a>'
        for i, title in enumerate(_TOP_PAGES)
    )
    return f'<nav class="{theme.PHONE_NAV_CLASS}" aria-label="Pages">{links}</nav>'


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

    st.html(theme.phone_css(theme.palette()) + _phone_bar_html())

    def _page(title: str, renderer: _PageRenderer, *, default: bool = False) -> st.Page:
        def _run() -> None:
            _render_page(settings, title, renderer)

        return st.Page(
            _run,
            title=title,
            url_path="" if default else _page_path(title)[1:],
            default=default,
        )

    top = [_page(t, r, default=(i == 0)) for i, (t, r) in enumerate(_TOP_PAGES.items())]
    details = [_page(t, r) for t, r in _DETAIL_PAGES.items()]
    st.navigation({"": top, _DETAILS: details}, position="top").run()


def _render_page(settings: Settings, page_name: str, renderer: _PageRenderer) -> None:
    """Run the selected page, releasing Data health's probe before its reads."""
    if page_name == _OVERRIDE_PAGE:
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

        if page_name != "Data health":
            renderer(store, settings)
            return

    steps = _ReadSteps(settings)
    try:
        renderer(cast(duckdb.DuckDBPyConnection, steps), settings)
    except StoreLockedError:
        render_busy(settings)
    except _ReadStepUnreadable as exc:
        render_unreadable(settings, str(exc))
    finally:
        steps._close()


class _ReadStepUnreadable(Exception):
    """A Data health read failed while opening the store, before its query."""


class _ReadSteps:
    """DuckDB read cursor that releases the file after each fetched result.

    Registered temporary views are replayed on each connection; the health
    readers use them for their one-query joins. No connection spans Python
    aggregation or Streamlit drawing.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._views: dict[str, Any] = {}
        self._context: Any = None
        self._conn: duckdb.DuckDBPyConnection | None = None

    def register(self, name: str, value: Any) -> _ReadSteps:
        self._views[name] = value
        return self

    def unregister(self, name: str) -> _ReadSteps:
        self._views.pop(name)
        return self

    def execute(self, query: str, parameters: Any = None) -> _ReadSteps:
        self._close()
        context = open_read_only(self._settings)
        try:
            conn = context.__enter__()
        except StoreLockedError:
            raise
        except duckdb.Error as exc:
            raise _ReadStepUnreadable(str(exc)) from exc
        self._context, self._conn = context, conn
        try:
            for name, value in self._views.items():
                conn.register(name, value)
            if parameters is None:
                conn.execute(query)
            else:
                conn.execute(query, parameters)
        except BaseException:
            self._close()
            raise
        return self

    @property
    def description(self) -> Any:
        assert self._conn is not None
        return self._conn.description

    def fetchall(self) -> list[tuple[Any, ...]]:
        assert self._conn is not None
        try:
            return self._conn.fetchall()
        finally:
            self._close()

    def fetchone(self) -> tuple[Any, ...] | None:
        assert self._conn is not None
        try:
            return self._conn.fetchone()
        finally:
            self._close()

    def pl(self) -> pl.DataFrame:
        assert self._conn is not None
        try:
            return self._conn.pl()
        finally:
            self._close()

    def _close(self) -> None:
        if self._context is not None:
            self._context.__exit__(None, None, None)
            self._context = None
            self._conn = None


if __name__ == "__main__":
    render_app()
