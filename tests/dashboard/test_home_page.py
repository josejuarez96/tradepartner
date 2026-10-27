"""Tests for the Home page (plan T275, #2170).

Driven headless through `streamlit.testing.v1.AppTest` on the shell over a temp
store. `home_view.build` is T273's own module with its own tests; here it is
replaced by a hand-built `HomeView` so each state of the page is exercised as the
thin draw it is. A full-page text test keeps developer words off every state.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from dashboard_nav import nav_calls_fixture  # noqa: F401
from streamlit.testing.v1 import AppTest

from tradepartner.config import Settings
from tradepartner.dashboard import home_page
from tradepartner.execution import home_view, ops
from tradepartner.execution.home_view import (
    BookCard,
    DataStatus,
    HomeView,
    NeedsYou,
    ResearchStrip,
    StatusLine,
)
from tradepartner.execution.ops import PerformanceData
from tradepartner.store import schema
from tradepartner.store.db import open_for_write

_APP_PATH = str(
    Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "dashboard" / "app.py"
)
_SESSION = date(2026, 10, 2)
_B, _D1, _D2 = date(2026, 9, 30), date(2026, 10, 1), _SESSION
_AT = datetime(2026, 10, 2, 22, 30, tzinfo=UTC)  # 6:30 PM ET

_QUIET = HomeView(
    status=StatusLine(
        "ok", "Everything is running normally", "Next run at 08:05 ET", date(2026, 10, 5)
    ),
    needs_you=(),
    books=(
        BookCard(
            "main",
            "Trading",
            "Buys steady winners.",
            _SESSION,
            101_000.0,
            1.0,
            2.0,
            "Sold 2 names for $4,000 at 9:31 AM ET.",
            7,
            12_345.0,
            ("AAPL", "MSFT"),
            False,
        ),
    ),
    data=DataStatus("Data is up to date through 2026-10-02", 0, False),
    research=ResearchStrip("A research decision was made", "Decision: keep", _AT, "research"),
)
_LOUD = replace(
    _QUIET,
    status=StatusLine("red", "h is halted", "Next run at 08:05 ET", None),
    needs_you=(
        NeedsYou(
            "h",
            "kill_switch_engaged",
            "Trading is stopped (h)",
            "Read the reason.",
            "tradepartner paper resume --book h",
        ),
        NeedsYou(
            "main",
            "overdue_delisting",
            "Cash for a delisted name has not arrived (main, ZZZ)",
            "Check Alpaca.",
            "Check Alpaca's activity",
        ),
    ),
    books=(*_QUIET.books, replace(_QUIET.books[0], book_id="h", pill="Halted")),
)


def _perf() -> PerformanceData:
    return PerformanceData(
        book_id="main",
        as_of=_AT,
        last_updated=_AT,
        base_session=_B,
        base_value=100_000.0,
        equity=((_B, 100_000.0), (_D1, 99_200.0), (_D2, 101_000.0)),
        equity_index=((_B, 100.0), (_D1, 99.2), (_D2, 101.0)),
        daily_pnl=((_D1, -800.0), (_D2, 1_800.0)),
        cumulative_return_pct=1.0,
        benchmark_index=((_B, 100.0), (_D1, 101.0), (_D2, 102.0)),
        excess_pp=((_B, 0.0), (_D1, -1.8), (_D2, -1.0)),
        tracking_state="not_yet",
        tracking_n=1,
    )


def _store(tmp_path: Path) -> Path:
    path = tmp_path / "home.duckdb"
    with open_for_write(Settings(_env_file=None, store={"path": str(path)})) as conn:
        schema.init_schema(conn)
    return path


def _app(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, view: HomeView | None = _QUIET
) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(_store(tmp_path)))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "does-not-exist.env"))
    monkeypatch.setenv("DASHBOARD_TEST_PAGE", "Home")
    if view is not None:
        monkeypatch.setattr(home_view, "build", lambda conn, settings, now: view)
    monkeypatch.setattr(ops, "performance_data", lambda conn, settings, book_id=None: _perf())
    at = AppTest.from_file(_APP_PATH, default_timeout=30)
    at.run()
    assert not at.exception
    return at


def _text(at: AppTest) -> str:
    """Every drawn text, in page order."""
    parts: list[str] = []

    def walk(node: Any) -> None:
        for child in getattr(node, "children", {}).values():
            if child.type == "metric":
                parts.append(f"{child.label} {child.value}")
            elif child.type in ("markdown", "caption", "subheader", "header", "code"):
                parts.append(str(child.value))
            elif child.type == "page_link":
                parts.append(str(child.proto.label))
            walk(child)

    walk(at.main)
    return "\n".join(parts)


def _positions(at: AppTest, *needles: str) -> list[int]:
    text = _text(at)
    return [text.index(n) for n in needles]


def test_sections_come_in_the_mockups_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, tmp_path)
    order = _positions(
        at, "Everything is running normally", "Needs you", "main", "Data is up to date", "Research"
    )
    assert order == sorted(order)
    assert [s.value for s in at.subheader] == ["Needs you", "Books", "Data", "Research"]


def test_quiet_page_says_nothing_needs_you_and_uses_no_orange(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, tmp_path)
    assert "Nothing needs you." in [m.value for m in at.markdown]
    assert not at.code
    assert "orange" not in _text(at)


def test_loud_page_shows_each_item_in_orange_with_its_command_as_copyable_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, tmp_path, _LOUD)
    orange = [m.value for m in at.markdown if ":orange[" in m.value]
    assert len(orange) == 2
    assert "Trading is stopped (h)" in orange[0] and "delisted" in orange[1]
    assert [c.value for c in at.code] == [
        "tradepartner paper resume --book h",
        "Check Alpaca's activity",
    ]
    assert "Nothing needs you." not in _text(at)
    assert "h is halted" in _text(at)


def test_a_card_shows_value_returns_trades_and_holdings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, tmp_path)
    text = _text(at)
    assert "Value as of Fri Oct 2 $101,000.00" in text
    assert "+$1,000.00 (+1.00%)" in text  # dollars first, then percent
    assert "+$2,000.00 (+2.00%)" in text
    assert "Sold 2 names for $4,000 at 9:31 AM ET." in text
    assert "7 holdings · $12,345.00 cash" in text
    assert "Largest: AAPL, MSFT" in text
    assert "Buys steady winners." in text
    assert ":green-badge[Trading]" in text


def test_a_card_has_a_book_against_spy_line_chart_on_one_axis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, tmp_path)
    [chart] = at.get("vega_lite_chart")
    spec = json.loads(chart.proto.spec)
    assert spec["mark"]["type"] == "line"
    enc = spec["encoding"]
    assert "y2" not in enc and "independent" not in json.dumps(spec)
    colour = enc["color"]["scale"]
    assert colour["domain"] == ["This book", "SPY"] and colour["range"] == ["#2a78d6", "#5a6470"]
    assert enc["strokeDash"]["scale"]["range"] == [[], [4, 4]]  # book solid, SPY dashed


def test_each_card_links_to_the_book_page_with_the_book_in_the_query(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, tmp_path, _LOUD)
    links = {
        link.proto.label: (link.proto.page, link.proto.query_string)
        for link in at.get("page_link")
        if link.proto.label.startswith("Open ") and link.proto.label != "Open Research"
    }
    assert links == {
        "Open main": (home_page.BOOKS_URL_PATH, "book=main"),
        "Open h": (home_page.BOOKS_URL_PATH, "book=h"),
    }


def test_the_research_strip_links_to_research_and_shows_eastern_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, tmp_path)
    assert "A research decision was made: Decision: keep (Oct 2, 6:30 PM ET)" in _text(at)
    assert [k.proto.page for k in at.get("page_link") if k.proto.label == "Open Research"] == [
        "research"
    ]


def test_a_store_with_no_window_shows_one_sentence_and_the_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, tmp_path, view=None)  # the real view model over an empty store
    text = _text(at)
    assert "tradepartner paper start --hypothesis" in text
    assert not at.get("vega_lite_chart")
    assert "Everything is running normally" not in text


def test_a_card_without_marks_says_so_instead_of_drawing_an_empty_chart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bare = BookCard("b", "Starts Monday", "x.", None, None, None, None, None, None, None, (), False)
    at = _app(monkeypatch, tmp_path, replace(_QUIET, books=(bare,)))
    assert "No value yet" in _text(at)
    assert not at.get("vega_lite_chart")
    assert ":blue-badge[Starts Monday]" in _text(at)


_FORBIDDEN = re.compile(
    r"\bops\b|reconciliation|known_at|\bUTC\b|run_in_progress|kill_switch|order_lagging"
    r"|overdue_delisting|shakedown_note|report_due|\bunreadable\b|\bok\b",
    re.IGNORECASE,
)


@pytest.mark.parametrize("view", [_QUIET, _LOUD], ids=["quiet", "loud"])
def test_no_developer_words_ids_or_utc_anywhere_on_the_page(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, view: HomeView
) -> None:
    at = _app(monkeypatch, tmp_path, view)
    text = _text(at)
    assert _FORBIDDEN.findall(text) == []
    assert not re.search(r"\b\d{1,2}:\d{2}(:\d{2})?\s*(Z|\+00:00)", text)
