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

from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.config import Settings
from tradepartner.dashboard import override_page
from tradepartner.store import registry, schema
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
            # The override writer reads the window's cadence from its hypothesis (T136).
            holdout = (date(2025, 1, 2), date(2026, 8, 31))
            params = Settings(
                _env_file=None,
                holdout={"start": holdout[0].isoformat(), "end": holdout[1].isoformat()},
            )
            hypothesis = registry.register_hypothesis(
                conn,
                slug="h-override-page",
                family="momentum",
                title="override page test",
                doc_path="docs/hypotheses/h-override-page.md",
                doc_sha256="0" * 64,
                params=frozen_params_of(params, family="momentum"),
                in_sample_start=date(2017, 1, 3),
                holdout_start=holdout[0],
                holdout_end=holdout[1],
                registered_by="test",
                settings=params,
            )
            append(
                conn,
                PaperWindowRow(
                    hypothesis_id=hypothesis.hypothesis_id,
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


def test_the_page_never_offers_settle_order(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    """Spec req 17 (#571): the page has no broker and the settlement gate needs one,
    so its kinds stay req 9's three although the schema allows `settle_order`."""
    assert schema.SETTLE_ORDER_KIND in schema.JOURNAL_ENUMS["overrides", "kind"]
    assert schema.SETTLE_ORDER_KIND not in override_page.KINDS
    at = _app(monkeypatch, store)
    assert not at.exception
    assert schema.SETTLE_ORDER_KIND not in at.selectbox(key=override_page.KIND_KEY).options


def test_a_settle_order_submit_is_refused_and_writes_nothing(store: Path) -> None:
    """Even a submit that bypasses the form's options is refused by the writer the
    page shares with `paper override`."""
    outcome = override_page.submit(
        _settings(store), schema.SETTLE_ORDER_KIND, _REBALANCE, _NAME, _REASON
    )
    assert outcome.status is override_page.OutcomeStatus.REFUSED
    assert outcome.refusal == "override"
    assert schema.SETTLE_ORDER_KIND in outcome.message
    assert _rows(store) == []


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


def test_a_version_16_store_warns_to_migrate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """#1261: `_render_window_state` catches `SchemaVersionError` into an
    `st.warning` carrying its message, so the override page says "migrate
    first" instead of a false "no paper window is open"."""
    store_path = _init(tmp_path / "v16.duckdb", with_window=False)
    with duckdb.connect(str(store_path)) as conn:
        conn.execute("UPDATE schema_version SET version = 16")
        conn.execute("ALTER TABLE orders DROP COLUMN book_id")
    at = _app(monkeypatch, store_path)
    assert not at.exception
    text = _text(at)
    assert "open it for writing once" in text
    assert "no paper window is open" not in text.lower()


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


def test_a_same_content_resubmit_that_arrives_stale_writes_no_second_row(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """A fast double-click: the second click's own message can carry the
    browser's pre-clear widget values, so the reason field is not actually
    empty when the second rerun's `on_click` reads it (module docstring, "A
    fast double-click"). Simulated here by restoring the reason after the
    first write, as the stale second click would. The duplicate guard
    refuses the second submit by content, not by the (now unreliable) empty
    reason."""
    at = _app(monkeypatch, store)
    _fill(at)
    _submit(at)
    assert len(_rows(store)) == 1

    # The stale second click's message still carries the pre-clear reason.
    at.text_area(key=override_page.REASON_KEY).set_value(_REASON)
    _submit(at)

    assert not at.exception
    assert len(_rows(store)) == 1
    assert "identical" in _text(at).lower()


def test_a_resubmit_after_changing_a_field_writes_a_second_row(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """The duplicate guard compares content, so a genuinely different submit
    (even right after a write) is not refused."""
    at = _app(monkeypatch, store)
    _fill(at, name=_NAME)
    _submit(at)
    assert len(_rows(store)) == 1

    _fill(at, name="SEC_QQQ")
    _submit(at)

    assert not at.exception
    assert len(_rows(store)) == 2


def test_a_resubmit_differing_only_by_surrounding_whitespace_is_still_a_duplicate(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """The signature trims the reason and name before comparing, so padding
    either with whitespace does not evade the guard."""
    at = _app(monkeypatch, store)
    _fill(at, name=_NAME, reason=_REASON)
    _submit(at)
    assert len(_rows(store)) == 1

    _fill(at, name=f"  {_NAME} ", reason=f" {_REASON}\n")
    _submit(at)

    assert not at.exception
    assert len(_rows(store)) == 1
    assert "identical" in _text(at).lower()


def test_a_deliberate_unmodified_resubmit_later_is_still_a_duplicate(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """`LAST_WRITTEN_KEY` only changes when a *different* submit is itself
    written, not on every edit: editing a field and then retyping exactly
    what was last written is still refused, since nothing resets the guard
    on a field change alone (module docstring)."""
    at = _app(monkeypatch, store)
    _fill(at, name=_NAME, reason=_REASON)
    _submit(at)
    assert len(_rows(store)) == 1

    _fill(at, name="SEC_QQQ")  # an intervening edit, not submitted
    _fill(at, name=_NAME, reason=_REASON)  # back to exactly what was written
    _submit(at)

    assert not at.exception
    assert len(_rows(store)) == 1
    assert "identical" in _text(at).lower()


def test_a_repeat_kill_switch_engagement_is_not_treated_as_a_duplicate(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """`engage_kill_switch` takes neither a session nor a name, so a repeat
    engagement would otherwise be flagged a duplicate on reason text alone;
    the guard exempts it since a second logged engagement is harmless
    (module docstring)."""
    at = _app(monkeypatch, store)
    _fill(at, kind="engage_kill_switch", session=None, name="", reason=_REASON)
    _submit(at)
    assert len(_rows(store)) == 1

    _fill(at, kind="engage_kill_switch", session=None, name="", reason=_REASON)
    _submit(at)

    assert not at.exception
    assert len(_rows(store)) == 2
    assert "identical" not in _text(at).lower()


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
    # Only a write empties the reason: a busy submit keeps it to resubmit.
    assert at.text_area(key=override_page.REASON_KEY).value == _REASON


# --- theme -----------------------------------------------------------------------------


def test_no_colour_literal_in_page_code() -> None:
    source = Path(override_page.__file__).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)


# --- shared constants -------------------------------------------------------------


def test_duplicate_guard_exemption_is_the_shared_kind_constant() -> None:
    """#636: the exemption must reference `schema.ENGAGE_KILL_SWITCH_KIND`, not a
    hardcoded literal, and must stay a subset of the `overrides.kind` enum so drift
    between the two fails this test instead of silently exempting a stale kind."""
    assert schema.ENGAGE_KILL_SWITCH_KIND in override_page._DUPLICATE_GUARD_EXEMPT_KINDS
    assert (
        set(schema.JOURNAL_ENUMS["overrides", "kind"])
        >= override_page._DUPLICATE_GUARD_EXEMPT_KINDS
    )


# --- the book (ADR 0017 B.7; plan T156) -------------------------------------------


def _add_book_b(store_path: Path) -> int:
    """A second open window, book `b` on its own account, on the same hypothesis."""
    with open_for_write(_settings(store_path)) as conn:
        main = conn.execute("SELECT hypothesis_id FROM paper_windows").fetchone()
        assert main is not None
        window_id = append(
            conn,
            PaperWindowRow(
                hypothesis_id=main[0],
                first_rebalance_session=_T0,
                account_id="PB1",
                starting_cash=50_000.0,
                starting_equity=50_000.0,
                code_version="test",
                started_at=_STARTED,
                frozen_json=json.dumps({"paper.min_override_reason_chars": _MIN_REASON}),
                frozen_sha256="0" * 64,
                book_id="b",
                known_at=_STARTED,
                ingested_at=_STARTED,
            ),
        )
    assert window_id is not None
    return window_id


def _override_windows(store_path: Path) -> list[tuple[Any, ...]]:
    with open_read_only(_settings(store_path)) as conn:
        return conn.execute("SELECT window_id, kind FROM overrides ORDER BY override_id").fetchall()


def test_one_book_shows_no_book_selector(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    at = _app(monkeypatch, store)
    assert not at.exception
    assert all(sb.key != override_page.BOOK_KEY for sb in at.selectbox)


def test_an_override_written_from_the_page_attaches_to_the_selected_books_window(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    b_window = _add_book_b(store)
    at = _app(monkeypatch, store)
    assert not at.exception
    selector = at.selectbox(key=override_page.BOOK_KEY)
    assert selector.options == ["b", "main"]
    assert selector.value == "main"

    _fill(at, kind="keep_name")
    _submit(at)
    assert not at.exception
    at.selectbox(key=override_page.BOOK_KEY).set_value("b").run()
    _fill(at, kind="keep_name")  # the same fields, another book: not a duplicate
    _submit(at)
    assert not at.exception

    with open_read_only(_settings(store)) as conn:
        main_window = conn.execute(
            "SELECT window_id FROM paper_windows WHERE book_id = 'main'"
        ).fetchone()
    assert main_window is not None
    assert _override_windows(store) == [(main_window[0], "keep_name"), (b_window, "keep_name")]


def test_the_selected_books_window_state_is_shown(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    """A book with no open window shows the `no_window` hint for that book only."""
    _add_book_b(store)
    with open_for_write(_settings(store)) as conn:
        conn.execute(
            'INSERT INTO paper_window_stops (window_id, "at", state, known_at, ingested_at) '
            "SELECT window_id, ?, 'closed', ?, ? FROM paper_windows WHERE book_id = 'b'",
            [_STARTED, _STARTED, _STARTED],
        )
    at = _app(monkeypatch, store)
    assert "No paper window is open" not in _text(at)
    at.selectbox(key=override_page.BOOK_KEY).set_value("b").run()
    assert not at.exception
    assert "No paper window is open for book `b`" in _text(at)
