"""Headless tests for the Book page (T276, #2170): it draws `book_view.build` and computes
nothing, so the view model is replaced by a hand-built one and the page's text, widgets
and routing are checked."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import streamlit as st
from dashboard_nav import nav_calls_fixture  # noqa: F401
from streamlit.testing.v1 import AppTest

from tradepartner.config import Settings
from tradepartner.dashboard import book_page
from tradepartner.execution import book_activity, book_holdings, book_stats, book_view
from tradepartner.store import schema
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import PaperWindowRow, PaperWindowStopRow, append

_APP = str(Path(__file__).resolve().parents[2] / "src/tradepartner/dashboard/app.py")
_NOW = datetime(2026, 10, 2, 22, 0, tzinfo=UTC)
_D0, _D1, _D2 = date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)


def _store(path: Path, books: tuple[str, ...] = ("main",), closed: tuple[str, ...] = ()) -> Path:
    settings = Settings(_env_file=None, store={"path": str(path)})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        for i, book in enumerate(books):
            wid = append(
                conn,
                PaperWindowRow(
                    hypothesis_id=1,
                    first_rebalance_session=_D0,
                    account_id=f"PA{i}",
                    starting_cash=100_000.0,
                    starting_equity=100_000.0,
                    code_version="test",
                    started_at=_NOW,
                    frozen_json="{}",
                    frozen_sha256="0" * 64,
                    book_id=book,
                    known_at=_NOW,
                    ingested_at=_NOW,
                ),
            )
            if book in closed:
                append(
                    conn,
                    PaperWindowStopRow(
                        window_id=wid, state="closed", at=_NOW, known_at=_NOW, ingested_at=_NOW
                    ),
                )
    return path


def _slice(key: str, note: str | None = None, n: int = 3) -> book_stats.RangeSlice:
    days = [_D0, _D1, _D2][:n]
    rows = [(d, 100.0 + i) for i, d in enumerate(days)]
    spy = [(d, 100.0 + i / 2) for i, d in enumerate(days)]
    return book_stats.RangeSlice(key, days[0] if days else None, rows, spy, 2.0, 1.0, note)


def _holding(ticker: str, gain: float) -> book_holdings.HoldingRow:
    return book_holdings.HoldingRow(
        ticker, "long", 10.0, 1000.0, 1000.0 + gain, gain, gain / 10, 5.0, 0.5
    )


def _side(side: str, sentence: str | None) -> book_activity.SideActivity:
    return book_activity.SideActivity(side, 2 if sentence else 0, 0.0, None, (), (), sentence)


def _view(
    book: str = "main",
    *,
    status: str = "running",
    sells: str | None = "Sold 2 names for $1,000, 9:35 AM to 9:40 AM.",
    buys: str | None = "Bought 3 names for $1,200, 9:35 AM to 9:41 AM.",
    problems: tuple[book_activity.OrderProblem, ...] = (),
    digest: str = "Nothing filled",
    notes: dict[str, str] | None = None,
    tracking: book_stats.TrackingProgress | None = None,
    spy: float | None = 1.0,
) -> book_view.BookView:
    notes = notes or {}
    ranges = tuple(_slice(k, notes.get(k)) for k in ("since_start", "1M", "3M"))
    perf = book_view.Performance(
        ranges,
        book_stats.DayStat(_D1, 1000.0, 1.0),
        book_stats.DayStat(_D2, -2300.0, -2.2),
        book_stats.MaxDrawdown(-2300.0, -2.27, _D1, _D2),
        tracking or book_stats.TrackingProgress(3, 5, False, "3 of 5 rebalances"),
    )
    held = book_holdings.Holdings(
        _D2,
        (),
        book_holdings.HoldingsSummary(
            7, 50_000.0, 4, 3, (), (_holding("AAPL", 300.0),), (_holding("MSFT", -120.0),)
        ),
    )
    return book_view.BookView(
        book,
        _NOW,
        1,
        book_view.Header(status, _D2, 99_200.0, -0.8, spy, None),  # type: ignore[arg-type]
        perf,
        book_activity.TodayActivity(digest, _side("sell", sells), _side("buy", buys), problems),
        _D2,
        held,
        None,
        None,
    )


def _app(
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
    view: book_view.BookView | None = None,
    seen: list[str] | None = None,
) -> AppTest:
    def build(
        conn: object, settings: Settings, book: str, now: datetime, *, top_n: int = 5
    ) -> book_view.BookView:
        if seen is not None:
            seen.append(book)
        base = view or _view()
        return book_view.BookView(book, *[getattr(base, f) for f in base.__dataclass_fields__][1:])

    monkeypatch.setattr(book_view, "build", build)
    monkeypatch.setattr(book_page, "_now", lambda: _NOW)
    monkeypatch.setenv("STORE__PATH", str(path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(path.parent / "missing.env"))
    at = AppTest.from_file(_APP).run()
    monkeypatch.setenv("DASHBOARD_TEST_PAGE", "Books")
    at.run(timeout=30)
    return at


def _text(at: AppTest) -> str:
    parts: list[str] = []
    for group in (at.markdown, at.caption, at.header, at.subheader, at.warning, at.info):
        parts += [str(e.value) for e in group]
    for m in at.metric:
        parts += [str(m.label), str(m.value), str(m.help or "")]
    return "\n".join(parts)


def test_header_shows_name_pill_sentence_value_and_returns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    assert not at.exception
    assert at.header[0].value == "main"
    text = _text(at)
    assert "Book main" in text  # strategy sentence fallback for an unregistered hypothesis
    assert "Trading" in text
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Value"] == "$99,200.00"
    assert metrics["Return since start"] == "-0.80%"
    assert metrics["SPY over the same days"] == "+1.00%"
    assert "Friday, October 2" in text


@pytest.mark.parametrize(
    ("status", "word"), [("running", "Trading"), ("stopped", "Halted"), ("closed", "Closed")]
)
def test_status_pill_words(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str, word: str
) -> None:
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"), _view(status=status))
    assert word in _text(at)
    assert status not in _text(at).lower().replace("trading", "") or status == "closed"


def test_range_buttons_default_since_start_and_switch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    control = at.get("button_group")[0]
    assert control.proto.options and [o.content for o in control.proto.options] == [
        "Since start",
        "1M",
        "3M",
    ]
    assert len(at.get("vega_lite_chart")) == 1


def test_a_range_longer_than_history_says_so_not_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    note = book_stats.TOO_YOUNG_NOTE
    at = _app(
        monkeypatch,
        _store(tmp_path / "s.duckdb"),
        _view(notes={"since_start": note, "1M": note, "3M": note}),
    )
    assert "longer than the book's history" in _text(at)
    assert len(at.get("vega_lite_chart")) == 1


def test_stats_each_carry_a_one_line_explanation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Best day"] == "+$1,000.00 (+1.00%)"
    assert metrics["Worst day"] == "-$2,300.00 (-2.20%)"
    assert metrics["Maximum drawdown"] == "-$2,300.00 (-2.27%)"
    assert metrics["Track record"] == "too early to judge: 3 of 5 rebalances"
    text = _text(at)
    assert "The biggest fall from a high." in text
    assert "rose the most" in text and "fell the most" in text


def test_today_lists_sells_then_buys_and_problems(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    problem = book_activity.OrderProblem(
        "AAPL", "buy", "Buying AAPL did not go through.", "rejected"
    )
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"), _view(problems=(problem,)))
    text = _text(at)
    assert text.index("Sold 2 names") < text.index("Bought 3 names")
    assert [w.value for w in at.warning] == ["Buying AAPL did not go through."]
    assert "rejected" not in text


def test_today_empty_state_is_plain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    view = _view(sells=None, buys=None, digest=book_activity.NO_TRADES)
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"), view)
    assert book_activity.NO_TRADES in _text(at)


def test_holdings_block(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    metrics = {m.label: str(m.value) for m in at.metric}
    assert metrics["Names held"] == "7"
    assert metrics["Cash"] == "$50,000.00"
    assert metrics["Up today"] == "4" and metrics["Down today"] == "3"
    frames = [d.value for d in at.dataframe]
    assert [list(f["Ticker"]) for f in frames] == [["AAPL"], ["MSFT"]]
    assert list(frames[0]["Gain"]) == ["+$300.00"]
    assert list(frames[1]["Gain"]) == ["-$120.00"]


def test_see_all_trades_opens_trades_for_the_book(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(st, "switch_page", lambda page: opened.append(str(page)))
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    [button] = [b for b in at.button if b.label == "See all trades"]
    button.click().run(timeout=30)
    assert opened == ["trades"]
    assert at.session_state[book_page.TRADES_BOOK_KEY] == "main"


def test_default_is_the_one_open_book_and_picker_switches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[str] = []
    path = _store(tmp_path / "s.duckdb", ("alpha", "main"), closed=("main",))
    at = _app(monkeypatch, path, seen=seen)
    assert seen[-1] == "alpha"
    picker = next(b for b in at.selectbox if b.key == book_page.BOOK_KEY)
    picker.set_value("main").run(timeout=30)
    assert seen[-1] == "main"
    assert at.header[0].value == "main"


def test_a_link_chooses_the_book(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    seen: list[str] = []
    path = _store(tmp_path / "s.duckdb", ("alpha", "beta"))
    monkeypatch.setattr(book_page, "_now", lambda: _NOW)
    monkeypatch.setenv("STORE__PATH", str(path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setattr(
        book_view,
        "build",
        lambda conn, settings, book, now, *, top_n=5: (seen.append(book), _view(book))[1],
    )
    at = AppTest.from_file(_APP).run()
    monkeypatch.setenv("DASHBOARD_TEST_PAGE", "Books")
    at.query_params["book"] = "beta"
    at.run(timeout=30)
    assert seen[-1] == "beta"


def test_no_id_raw_status_or_utc_in_the_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    text = _text(at)
    assert not re.search(r"\b(window|trial|run|order) ?(id|#)?\s*\d+\b", text, re.I)
    assert "UTC" not in text and "+00:00" not in text and "T22:00" not in text
    for raw in ("pending_unresolved", "fills_lagging", "mismatch", "rejected", "engaged"):
        assert raw not in text


def test_no_book_and_no_window_are_plain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "s.duckdb"
    with open_for_write(Settings(_env_file=None, store={"path": str(path)})) as conn:
        schema.init_schema(conn)
    at = _app(monkeypatch, path)
    assert not at.exception
    assert "No book has started yet" in _text(at)


def test_the_page_writes_nothing_and_opens_no_connection() -> None:
    source = Path(book_page.__file__).read_text()
    assert "open_for_write" not in source and "open_read_only" not in source
    assert "INSERT" not in source and "subprocess" not in source


def test_choosing_a_range_draws_that_range(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    view = _view(notes={"3M": book_stats.TOO_YOUNG_NOTE})
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"), view)
    assert "longer than the book's history" not in _text(at)
    at.get("button_group")[0].set_value("3M").run(timeout=30)
    assert not at.exception
    assert "longer than the book's history" in _text(at)
