"""Tests for the Performance page (plan T252, paper spec amendment 2026-10-20, #2006).

Driven headless through `streamlit.testing.v1.AppTest` on the shell over a temp
store with only paper windows. `ops.performance_data` is T251's own module with its
own tests (`tests/execution/test_ops.py`); here it is replaced by a hand-built
`PerformanceData` so each state of the page is exercised as the thin read-only draw.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from tradepartner.config import Settings
from tradepartner.dashboard import performance_page
from tradepartner.execution import ops
from tradepartner.execution.marks import UnreadableMarkError
from tradepartner.execution.ops import PerformanceData
from tradepartner.store import schema
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import PaperWindowRow, append

_ROOT = Path(__file__).resolve().parents[2]
_APP_PATH = str(_ROOT / "src" / "tradepartner" / "dashboard" / "app.py")
_PAGE = "Performance"
_AS_OF = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
_UPDATED = datetime(2026, 10, 2, 11, 0, tzinfo=UTC)
_B, _D1, _D2 = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30)


def _window(book: str, account: str) -> PaperWindowRow:
    return PaperWindowRow(
        hypothesis_id=1,
        first_rebalance_session=_D2,
        account_id=account,
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=_UPDATED,
        frozen_json="{}",
        frozen_sha256="0" * 64,
        book_id=book,
        known_at=_UPDATED,
        ingested_at=_UPDATED,
    )


def _store(path: Path, *books: str) -> Path:
    settings = Settings(_env_file=None, store={"path": str(path)})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        for i, book in enumerate(books):
            append(conn, _window(book, f"PA{i}"))
    return path


@pytest.fixture
def store(tmp_path: Path) -> Path:
    return _store(tmp_path / "perf.duckdb", "main")


def _data(book: str = "main", **changes: Any) -> PerformanceData:
    """Equity 100,000 / 100,500 / 101,500 at B, 09-29, 09-30; SPY index 100 / 101 / 103."""
    base = PerformanceData(
        book_id=book,
        as_of=_AS_OF,
        last_updated=_UPDATED,
        base_session=_B,
        base_value=100_000.0,
        equity=((_B, 100_000.0), (_D1, 100_500.0), (_D2, 101_500.0)),
        equity_index=((_B, 100.0), (_D1, 100.5), (_D2, 101.5)),
        daily_pnl=((_D1, 500.0), (_D2, 1_000.0)),
        cumulative_return_pct=1.5,
        benchmark_index=((_B, 100.0), (_D1, 101.0), (_D2, 103.0)),
        excess_pp=((_B, 0.0), (_D1, -0.5), (_D2, -1.5)),
        tracking_state="not_yet",
        tracking_n=1,
    )
    return replace(base, **changes)


def _app(
    monkeypatch: pytest.MonkeyPatch,
    store_path: Path,
    read: Callable[[str], PerformanceData | None] | None = None,
) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(store_path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store_path.parent / "does-not-exist.env"))
    if read is not None:
        monkeypatch.setattr(
            ops, "performance_data", lambda conn, settings, book_id=None: read(str(book_id))
        )
    at = AppTest.from_file(_APP_PATH)
    at.run()
    at.sidebar.radio[0].set_value(_PAGE).run(timeout=30)
    return at


def _text(at: AppTest) -> str:
    parts: list[Any] = [m.value for m in at.markdown]
    parts += [c.value for c in at.caption]
    parts += [h.value for h in at.header]
    parts += [h.value for h in at.subheader]
    parts += [f"{m.label} {m.value}" for m in at.metric]
    parts += [e.value for kind in (at.info, at.warning, at.error, at.success) for e in kind]
    return "\n".join(str(p) for p in parts)


def _metrics(at: AppTest) -> dict[str, str]:
    return {m.label: str(m.value) for m in at.metric}


def test_page_is_in_the_shell_navigation(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    at = _app(monkeypatch, store, lambda book: _data(book))
    assert not at.exception
    assert _PAGE in at.sidebar.radio[0].options


def test_renders_header_kpis_and_charts(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    at = _app(monkeypatch, store, lambda book: _data(book))
    assert not at.exception
    text = _text(at)
    assert "as of 2026-10-02 12:00 UTC · last updated 2026-10-02 11:00 UTC" in text
    assert _metrics(at) == {
        "Equity": "101,500.00",
        "Cumulative return": "+1.50%",
        "Daily P&L": "+1,000.00",
        "Versus SPY": "-1.50 pp",
    }
    assert len(at.get("vega_lite_chart")) == 2  # the equity curve and the daily-P&L bars
    assert "benchmark unavailable" not in text


def test_stale_chip(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    at = _app(monkeypatch, store, lambda book: _data(book, stale=True))
    assert not at.exception
    assert ":orange-badge[" in _text(at) and "stale" in _text(at)


def test_tracking_state_not_yet(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    at = _app(monkeypatch, store, lambda book: _data(book, tracking_n=1))
    assert not at.exception
    assert "not yet — a report needs two rebalances (1 of 2)" in _text(at)


def test_tracking_state_report_due(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    at = _app(
        monkeypatch,
        store,
        lambda book: _data(book, tracking_state="report_due", tracking_n=2),
    )
    assert not at.exception
    assert "two rebalances done — run paper report --book main" in _text(at)


def test_tracking_state_drawn_adds_the_trial_line(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    data = _data(
        tracking_state="drawn",
        tracking_n=2,
        tracking_trial_id=7,
        trial_span=(_D1, _D2),
        trial_line=((_D1, 100_500.0), (_D2, 102_000.0)),
        trial_index=((_D1, 100.5), (_D2, 102.0)),
    )
    at = _app(monkeypatch, store, lambda book: data)
    assert not at.exception
    text = _text(at)
    assert "Tracking trial 7 drawn" in text
    assert "its own span, 2026-09-29 to 2026-09-30" in text
    assert "not yet" not in text and "run paper report" not in text
    hero = at.get("vega_lite_chart")[0]
    assert "Tracking trial" in str(hero.proto.spec)


def test_trial_error_is_a_warning_not_a_crash(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    data = _data(tracking_state="drawn", tracking_trial_id=7, trial_error="trial 7 is missing")
    at = _app(monkeypatch, store, lambda book: data)
    assert not at.exception
    assert "Tracking trial 7 not drawn: trial 7 is missing" in _text(at)


def test_missing_spy_session_shows_the_benchmark_panel(
    monkeypatch: pytest.MonkeyPatch, store: Path
) -> None:
    data = _data(
        benchmark_index=(),
        excess_pp=(),
        benchmark_error="benchmark unavailable for 2026-09-30",
    )
    at = _app(monkeypatch, store, lambda book: data)
    assert not at.exception
    assert _metrics(at)["Versus SPY"] == "n/a"
    assert "SPY" not in str(at.get("vega_lite_chart")[0].proto.spec).replace("Versus SPY", "")
    assert "benchmark unavailable for 2026-09-30" in _text(at)
    assert ":orange-badge[" in _text(at)


def test_no_marks_yet_draws_no_chart(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    data = _data(
        equity=(), equity_index=(), daily_pnl=(), benchmark_index=(), excess_pp=(),
        cumulative_return_pct=None,
    )  # fmt: skip
    at = _app(monkeypatch, store, lambda book: data)
    assert not at.exception
    assert not at.get("vega_lite_chart")
    assert "No marks yet in this window" in _text(at)


def test_no_window_and_unreadable_mark_panels(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    at = _app(monkeypatch, store, lambda book: None)
    assert not at.exception
    assert "No paper window yet" in _text(at)

    def boom(book: str) -> PerformanceData:
        raise UnreadableMarkError("session 2026-09-30 has no cash")

    at = _app(monkeypatch, store, boom)
    assert not at.exception
    assert "A position mark could not be read" in _text(at)


def test_journal_flags_render_their_panels(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    at = _app(
        monkeypatch,
        store,
        lambda book: PerformanceData(book_id=book, journal_outdated="migrate first"),
    )
    assert not at.exception
    assert "Journal outdated: migrate first" in _text(at)


def test_one_book_has_no_selector_two_books_pick_as_ops_page_does(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    one = _store(tmp_path / "one.duckdb", "main")
    at = _app(monkeypatch, one, lambda book: _data(book))
    assert all(sb.key != performance_page.BOOK_KEY for sb in at.selectbox)

    two = _store(tmp_path / "two.duckdb", "main", "b")
    pnl = {"main": 1_000.0, "b": -250.0}
    at = _app(
        monkeypatch,
        two,
        lambda book: _data(book, daily_pnl=((_D2, pnl[book]),)),
    )
    assert not at.exception
    [selector] = [sb for sb in at.selectbox if sb.key == performance_page.BOOK_KEY]
    assert selector.options == ["b", "main"]
    assert selector.value == "main"  # `paper.book_id`, not the first token
    assert _metrics(at)["Daily P&L"] == "+1,000.00"
    selector.set_value("b").run(timeout=30)
    assert not at.exception
    assert _metrics(at)["Daily P&L"] == "-250.00"


def _row_counts(path: Path) -> dict[str, int]:
    with duckdb.connect(str(path), read_only=True) as conn:
        tables = [
            str(r[0])
            for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'main' AND table_type = 'BASE TABLE'"
            ).fetchall()
        ]
        return {
            t: int(conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0])  # type: ignore[index]
            for t in tables
        }


def test_the_page_writes_nothing(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    before = _row_counts(store)
    at = _app(monkeypatch, store, lambda book: _data(book))
    assert not at.exception
    assert _row_counts(store) == before


def test_no_colour_literal_in_page_code() -> None:
    source = Path(performance_page.__file__).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)
