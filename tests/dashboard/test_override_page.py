"""Tests for the override page (Phase 4 T69b, spec req 9; ADR 0011 Decision 2;
ADR 0008 point 4).

Driven headless through `streamlit.testing.v1.AppTest` on the shell, as
`test_ops_page.py` does, over a temp store seeded only through
`store.journal`. The writer itself (`execution.window.override`, T64b) has its
own tests (`tests/execution/test_window_stop.py`); here the page is exercised
as the form that calls it, from an `on_click` callback that runs before the
shell opens its read-only connection.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from tradepartner.config import Settings
from tradepartner.dashboard import override_page
from tradepartner.store import schema
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import PaperWindowRow, append

_ROOT = Path(__file__).resolve().parents[2]
_APP_PATH = str(_ROOT / "src" / "tradepartner" / "dashboard" / "app.py")
_PAGE = "Override"
_MIN_REASON = 20
_T0 = date(2026, 9, 30)  # a month's last session: the window's first rebalance
_REBALANCE = date(2026, 10, 30)  # the next month's last session
_NAME = "SEC_SPY"
_REASON = "the owner excludes this name for a known reason"
_STARTED = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)


def _settings(store_path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(store_path)})


def _init(store_path: Path, *, with_window: bool) -> Path:
    with open_for_write(_settings(store_path)) as conn:
        schema.init_schema(conn)
        if with_window:
            append(
                conn,
                PaperWindowRow(
                    hypothesis_id=1,
                    first_rebalance_session=_T0,
                    account_id="PA1",
                    starting_cash=100_000.0,
                    starting_equity=100_000.0,
                    code_version="test",
                    started_at=_STARTED,
                    frozen_json=json.dumps({"paper.min_override_reason_chars": _MIN_REASON}),
                    frozen_sha256="0" * 64,
                    known_at=_STARTED,
                    ingested_at=_STARTED,
                ),
            )
    return store_path


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return _init(tmp_path / "override.duckdb", with_window=True)


def _app(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(store_path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store_path.parent / "does-not-exist.env"))
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value(_PAGE).run()
    return at


def _fill(
    at: AppTest,
    *,
    kind: str = "exclude_name",
    session: date | None = _REBALANCE,
    name: str = _NAME,
    reason: str = _REASON,
) -> None:
    at.selectbox(key=override_page.KIND_KEY).set_value(kind)
    at.date_input(key=override_page.SESSION_KEY).set_value(session)
    at.text_input(key=override_page.NAME_KEY).set_value(name)
    at.text_area(key=override_page.REASON_KEY).set_value(reason)


def _submit(at: AppTest) -> None:
    at.button(key=override_page.SUBMIT_KEY).click().run()


def _rows(store_path: Path) -> list[tuple[Any, ...]]:
    with open_read_only(_settings(store_path)) as conn:
        return conn.execute(
            "SELECT kind, rebalance_session, security_id, reason FROM overrides "
            "ORDER BY override_id"
        ).fetchall()


def _text(at: AppTest) -> str:
    parts: list[Any] = [m.value for m in at.markdown]
    parts += [c.value for c in at.caption]
    parts += [h.value for h in at.header]
    parts += [e.value for kind in (at.info, at.warning, at.error, at.success) for e in kind]
    return "\n".join(str(p) for p in parts)


# --- navigation and render -----------------------------------------------------


def test_page_is_in_the_shell_navigation(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    at = _app(monkeypatch, store)
    assert not at.exception
    assert _PAGE in at.sidebar.radio[0].options


def test_the_form_offers_exactly_the_schemas_kinds(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    at = _app(monkeypatch, store)
    assert not at.exception
    assert at.selectbox(key=override_page.KIND_KEY).options == [
        "exclude_name",
        "keep_name",
        "engage_kill_switch",
    ]


def test_the_page_renders_with_no_memo_table(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    """ADR 0008 point 4: the form is never blocked by the memo layer. Phase 4
    has no memo table at all, and the page renders its form without one."""
    with open_read_only(_settings(store)) as conn:
        tables = {r[0] for r in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()}
    assert not any("memo" in t for t in tables)

    at = _app(monkeypatch, store)

    assert not at.exception
    assert at.button(key=override_page.SUBMIT_KEY)


def test_rendering_the_page_writes_nothing(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    at = _app(monkeypatch, store)
    at.run()
    assert not at.exception
    assert _rows(store) == []


# --- the write -------------------------------------------------------------------


def test_a_submit_writes_exactly_one_row_as_entered(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    at = _app(monkeypatch, store)
    _fill(at, kind="keep_name", session=_REBALANCE, name=_NAME, reason=_REASON)
    _submit(at)

    assert not at.exception
    assert _rows(store) == [("keep_name", _REBALANCE, _NAME, _REASON)]
    assert "written" in _text(at).lower()


def test_engage_kill_switch_takes_no_session_and_no_name(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    at = _app(monkeypatch, store)
    _fill(at, kind="engage_kill_switch", session=None, name="", reason=_REASON)
    _submit(at)

    assert not at.exception
    assert _rows(store) == [("engage_kill_switch", None, None, _REASON)]


def test_the_write_runs_before_the_shells_read_connection(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """ADR 0011 Decision 2: the submit's `on_click` callback runs at the start
    of the rerun, before the shell opens its read-only connection, so the
    rerun opens exactly one write connection and then exactly one read one."""
    at = _app(monkeypatch, store)
    _fill(at)
    opened: list[object] = []
    real_connect = duckdb.connect

    def _counting_connect(*args: Any, **kwargs: Any) -> Any:
        opened.append(kwargs.get("read_only"))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", _counting_connect)
    _submit(at)

    assert not at.exception
    assert opened == [False, True]


def test_a_submit_after_a_render_writes_one_row_and_a_further_rerun_none(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """The shell's read connection was in scope for the render that drew the
    form; the submit still writes exactly one row, and the reruns after it
    (a plain rerun, or another interaction) write none: no flag left set in
    `session_state` can replay the write."""
    at = _app(monkeypatch, store)
    _fill(at)
    _submit(at)
    assert len(_rows(store)) == 1

    at.run()
    at.sidebar.radio[0].set_value(_PAGE).run()

    assert not at.exception
    assert len(_rows(store)) == 1


# --- refusals ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "reason",
    ["", "   \n\t ", "x" * (_MIN_REASON - 1)],
    ids=["empty", "whitespace_only", "too_short"],
)
def test_a_reason_the_writer_refuses_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, store: Path, reason: str
) -> None:
    at = _app(monkeypatch, store)
    _fill(at, reason=reason)
    _submit(at)

    assert not at.exception
    assert _rows(store) == []
    assert "refused" in _text(at).lower()
    assert str(_MIN_REASON) in _text(at)


def test_no_window_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = _init(tmp_path / "no-window.duckdb", with_window=False)
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert "no paper window is open" in _text(at).lower()

    _fill(at)
    _submit(at)

    assert not at.exception
    assert _rows(store_path) == []
    assert "no_window" in _text(at)


def test_a_field_the_kind_does_not_take_is_refused(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    at = _app(monkeypatch, store)
    _fill(at, kind="engage_kill_switch", session=_REBALANCE, name=_NAME)
    _submit(at)

    assert not at.exception
    assert _rows(store) == []
    assert "refused" in _text(at).lower()


def test_a_second_click_after_a_write_writes_no_second_row(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """The form clears on submit, so a second click (or a double click) finds
    an empty reason and is refused rather than writing the same row twice."""
    at = _app(monkeypatch, store)
    _fill(at)
    _submit(at)
    _submit(at)

    assert not at.exception
    assert len(_rows(store)) == 1
    assert at.text_area(key=override_page.REASON_KEY).value in (None, "")


def test_a_write_followed_by_a_busy_render_still_shows_it_was_written(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """The write commits in the callback; if the shell's read then finds the
    store busy (a scheduled run took the lock in between), the owner still
    sees that the override was written, once, so they do not resubmit."""
    import tradepartner.store.db as db

    at = _app(monkeypatch, store)
    _fill(at)

    @contextmanager
    def _busy(_settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
        raise db.StoreLockedError("held by a run")
        yield  # pragma: no cover

    with monkeypatch.context() as patch:
        patch.setattr(db, "open_read_only", _busy)
        _submit(at)
        assert not at.exception
        assert len(_rows(store)) == 1
        assert "written" in _text(at).lower()
        assert "busy" in _text(at).lower()

    at.run()
    assert not at.exception
    assert "written" not in _text(at).lower()


def test_the_page_writes_to_the_shells_settings_not_the_environments(
    monkeypatch: pytest.MonkeyPatch, store: Path, tmp_path: Path
) -> None:
    """`render_app(settings)` hands its own `Settings` to the page, so the
    write lands in the store the shell reads, whatever the environment says."""
    elsewhere = tmp_path / "elsewhere.duckdb"
    monkeypatch.setenv("STORE__PATH", str(elsewhere))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "does-not-exist.env"))

    def _script(path: str) -> None:
        from tradepartner.config import Settings
        from tradepartner.dashboard.app import render_app

        render_app(Settings(_env_file=None, store={"path": path}))

    at = AppTest.from_function(_script, args=(str(store),), default_timeout=30)
    at.run()
    at.sidebar.radio[0].set_value(_PAGE).run()
    _fill(at)
    _submit(at)

    assert not at.exception
    assert len(_rows(store)) == 1
    assert not elsewhere.exists()


def test_store_busy_writes_nothing_and_shows_the_busy_state(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """Another tab mid-render holds an in-process read connection: the writer
    raises `StoreLockedError` at once, and the page shows "store busy" for the
    owner to resubmit."""
    at = _app(monkeypatch, store)
    _fill(at)
    other_tab = duckdb.connect(database=str(store), read_only=True)
    try:
        _submit(at)
    finally:
        other_tab.close()

    assert not at.exception
    assert _rows(store) == []
    assert "busy" in _text(at).lower()
    assert "nothing was written" in _text(at).lower()


# --- theme -----------------------------------------------------------------------------


def test_no_colour_literal_in_page_code() -> None:
    source = Path(override_page.__file__).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)
