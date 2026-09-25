"""Tests for the dashboard shell (T21a).

Spec req 1 ("store busy" state when locked) and req 12 (Streamlit page,
read-only, with the busy state). `open_store_connection` is exercised
directly (no Streamlit dependency) for the connection/state logic itself;
headless render is exercised through `streamlit.testing.v1.AppTest`, which
imports and runs `tradepartner.dashboard.app` as a script, driving
`Settings` through the `STORE__PATH` environment variable the same way any
other `Settings()` caller would (see `tests/conftest.py`'s `settings`
fixture and `tests/test_config.py` for the same env-var pattern).
"""

from __future__ import annotations

import runpy
from pathlib import Path

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from tradepartner.config import Settings
from tradepartner.dashboard.app import StoreState, StoreUnavailable, open_store_connection
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


# --- open_store_connection (pure, no Streamlit) ---------------------------


def test_open_store_connection_no_store(settings: Settings) -> None:
    assert not Path(settings.store.path).exists()
    with open_store_connection(settings) as store:
        assert store == StoreUnavailable(StoreState.NO_STORE)


def test_open_store_connection_does_not_create_the_file(settings: Settings) -> None:
    with open_store_connection(settings):
        pass
    assert not Path(settings.store.path).exists()


def test_open_store_connection_ok_yields_a_usable_connection(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    with open_store_connection(settings) as store:
        assert isinstance(store, duckdb.DuckDBPyConnection)
        (count,) = store.execute("SELECT COUNT(*) FROM ingestion_runs").fetchone()  # type: ignore[misc]
        assert count == 0


def test_open_store_connection_busy_when_locked_same_process(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    writer = duckdb.connect(database=settings.store.path, read_only=False)
    try:
        with open_store_connection(settings) as store:
            assert store == StoreUnavailable(StoreState.BUSY)
    finally:
        writer.close()


def test_open_store_connection_unreadable_for_a_non_duckdb_file(settings: Settings) -> None:
    Path(settings.store.path).write_text("not a duckdb file, just plain text")

    with open_store_connection(settings) as store:
        assert isinstance(store, StoreUnavailable)
        assert store.state is StoreState.UNREADABLE
        assert store.detail


def test_open_store_connection_read_only_connection_rejects_writes(settings: Settings) -> None:
    """The connection `open_store_connection` yields is genuinely
    read-only (mirrors
    `tests/store/test_schema.py::test_open_read_only_cannot_write`)."""
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    with open_store_connection(settings) as store:
        assert isinstance(store, duckdb.DuckDBPyConnection)
        with pytest.raises(duckdb.Error):
            store.execute("CREATE TABLE should_not_exist (a INTEGER)")


def test_open_store_connection_propagates_page_body_errors(settings: Settings) -> None:
    """An exception raised while *using* the yielded connection must
    propagate unchanged, not be swallowed or turned into an `UNREADABLE`
    state — only an error raised while *opening* the connection is
    caught."""
    with open_for_write(settings) as conn:
        schema.init_schema(conn)

    class _PageBoom(Exception):
        pass

    with pytest.raises(_PageBoom), open_store_connection(settings) as store:
        assert isinstance(store, duckdb.DuckDBPyConnection)
        raise _PageBoom("simulated page-body failure")


def test_dashboard_app_module_never_imports_open_for_write() -> None:
    """The shell must never be able to open a write connection at all:
    `open_for_write` is not even imported into `dashboard.app`, so no
    code path in this module can reach it."""
    import tradepartner.dashboard.app as app_module

    assert not hasattr(app_module, "open_for_write")


# --- import has no side effect --------------------------------------------


def test_importing_the_module_does_not_call_get_settings_or_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`render_app()` is guarded by `if __name__ == "__main__":`, so
    executing this file under any other module name — a plain `import`,
    or `runpy.run_path` with a non-`"__main__"` `run_name`, exactly as
    happens here — must not call `get_settings` (and so must not read the
    real `.env`) or touch the real store."""
    calls: list[None] = []

    import tradepartner.config as config_module

    def _spy_get_settings() -> Settings:
        calls.append(None)
        return config_module.Settings(_env_file=None)

    monkeypatch.setattr(config_module, "get_settings", _spy_get_settings)

    namespace = runpy.run_path(_APP_PATH, run_name="tradepartner.dashboard.app_reimport_probe")

    assert calls == []
    assert "render_app" in namespace  # the module body itself did execute


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


def test_render_unreadable_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = tmp_path / "corrupt.duckdb"
    store_path.write_text("not a duckdb file, just plain text")

    at = _run_app(monkeypatch, store_path)

    assert not at.exception
    assert any("could not be read" in e.value.lower() for e in at.error)


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
    assert not at.error
    [nav] = at.sidebar.radio
    assert nav.options == ["Data health", "Backtest"]
