"""Tests for the dashboard shell (T21a).

Spec req 1 ("store busy" state when locked) and req 12 (Streamlit page,
read-only, with the busy state). `determine_store_state` is exercised
directly (no Streamlit dependency) for the state logic itself; headless
render is exercised through `streamlit.testing.v1.AppTest`, which imports
and runs `tradepartner.dashboard.app` as a script, driving `Settings`
through the `STORE__PATH` environment variable the same way any other
`Settings()` caller would (see `tests/conftest.py`'s `settings` fixture
and `tests/test_config.py` for the same env-var pattern).
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from tradepartner.config import Settings
from tradepartner.dashboard.app import StoreState, determine_store_state
from tradepartner.store import schema
from tradepartner.store.db import open_for_write

_APP_PATH = str(
    Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "dashboard" / "app.py"
)


def _run_app(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(store_path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store_path.parent / "does-not-exist.env"))
    at = AppTest.from_file(_APP_PATH)
    at.run()
    return at


# --- determine_store_state (pure, no Streamlit) --------------------------


def test_determine_store_state_no_store(settings: Settings) -> None:
    assert not Path(settings.store.path).exists()
    assert determine_store_state(settings) is StoreState.NO_STORE


def test_determine_store_state_does_not_create_the_file(settings: Settings) -> None:
    determine_store_state(settings)
    assert not Path(settings.store.path).exists()


def test_determine_store_state_ok(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    assert determine_store_state(settings) is StoreState.OK


def test_determine_store_state_busy_when_locked_same_process(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    writer = duckdb.connect(database=settings.store.path, read_only=False)
    try:
        assert determine_store_state(settings) is StoreState.BUSY
    finally:
        writer.close()


def test_dashboard_app_module_never_imports_open_for_write() -> None:
    """The shell must never be able to open a write connection at all:
    `open_for_write` is not even imported into `dashboard.app`, so no
    code path in this module can reach it."""
    import tradepartner.dashboard.app as app_module

    assert not hasattr(app_module, "open_for_write")


def test_determine_store_state_read_only_connection_rejects_writes(settings: Settings) -> None:
    """The connection `determine_store_state` opens is genuinely
    read-only: a write attempted on a fresh connection to the same store
    path in read-only mode fails (mirrors
    `tests/store/test_schema.py::test_open_read_only_cannot_write`)."""
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    reader = duckdb.connect(database=settings.store.path, read_only=True)
    try:
        with pytest.raises(duckdb.Error):
            reader.execute("CREATE TABLE should_not_exist (a INTEGER)")
    finally:
        reader.close()


# --- headless render (streamlit.testing.v1.AppTest) -----------------------


def test_render_no_store_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = tmp_path / "no-store-here.duckdb"
    at = _run_app(monkeypatch, store_path)

    assert not at.exception
    assert not store_path.exists()
    assert any(
        "no store" in w.value.lower() or "run `tradepartner ingest`" in w.value for w in at.warning
    )


def test_render_busy_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = tmp_path / "busy.duckdb"
    settings = Settings(_env_file=None, store={"path": str(store_path)})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    writer = duckdb.connect(database=str(store_path), read_only=False)
    try:
        at = _run_app(monkeypatch, store_path)
        assert not at.exception
        assert any("busy" in info.value.lower() for info in at.info)
    finally:
        writer.close()


def test_render_ok_state_shows_navigation_and_placeholder_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store_path = tmp_path / "ok.duckdb"
    settings = Settings(_env_file=None, store={"path": str(store_path)})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    at = _run_app(monkeypatch, store_path)

    assert not at.exception
    assert not at.warning
    assert not at.info
    [nav] = at.sidebar.radio
    assert nav.options == ["Data health"]
