"""Headless tests for the Book page (T276, #2170): it draws `book_view.build` and computes
nothing, so the view model is replaced by a hand-built one and the page's text, widgets
and routing are checked."""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import streamlit as st
from dashboard_nav import nav_calls_fixture  # noqa: F401
from streamlit.testing.v1 import AppTest

from tradepartner.config import RiskConfig, Settings
from tradepartner.dashboard import book_actions, book_page
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
    safety: book_view.Safety | None = None,
    backtest: book_view.Backtest | None = None,
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
        safety or _safety(),
        backtest or _backtest(),
    )


_CHECKED = datetime(2026, 10, 2, 21, 0, tzinfo=UTC)  # 5:00 PM ET


def _safety(
    *,
    matches: bool = True,
    running: bool = True,
    since: datetime | None = None,
    text: str | None = None,
) -> book_view.Safety:
    recon_text = text or (
        f"Matches Alpaca as of {_CHECKED.isoformat()}: cash and holdings agree within the "
        "reconciliation's tolerance."
    )
    switch = (
        book_view.KillSwitchView(True, None, "", "Running: the kill switch is off.")
        if running
        else book_view.KillSwitchView(False, since, "stopped by the owner: checking", "x")
    )
    limit = book_view.LimitLine(
        "position",
        "Largest position: 4.8% against a limit of 5.0%, 0.2 points to spare. "
        "Past it, the order is cut.",
        "within",
    )
    return book_view.Safety(
        book_view.ReconciliationView(matches, recon_text, _CHECKED, 1.0), switch, (limit,)
    )


def _backtest(report: bool = True) -> book_view.Backtest:
    trial = book_view.TrialResult(12, "ok", None, False, "trial 12: ok")
    line = book_view.ReportLine(_D1, trial, "Latest report runs through 2026-10-01; trial 12: ok.")
    return book_view.Backtest(
        book_view.ShakedownProgress(5, 10, "5 of 10 sessions"),
        line if report else None,
        "tradepartner paper report --book main",
    )


def _live(monkeypatch: pytest.MonkeyPatch, store: Path) -> None:
    """Make `store` the live store: a project root whose `.env` names it."""
    root = store.parent / "root"
    root.mkdir(exist_ok=True)
    (root / ".env").write_text(f"STORE__PATH={store}\n")
    monkeypatch.setattr(book_actions, "PROJECT_ROOT", root)


def _app(
    monkeypatch: pytest.MonkeyPatch,
    path: Path,
    view: book_view.BookView | None = None,
    seen: list[str] | None = None,
    *,
    live: bool = True,
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
    if live:
        _live(monkeypatch, path)
    monkeypatch.setenv("STORE__PATH", str(path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(path.parent / "missing.env"))
    at = AppTest.from_file(_APP).run()
    monkeypatch.setenv("DASHBOARD_TEST_PAGE", "Books")
    at.run(timeout=30)
    return at


def _text(at: AppTest) -> str:
    parts: list[str] = []
    groups = (at.markdown, at.caption, at.header, at.subheader, at.warning, at.info, at.error)
    for group in (*groups, at.success, at.code):
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
    text = _text(at).lower().replace("running a new report", "")  # T277's quoted note
    assert status not in text.replace("trading", "") or status == "closed"


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


# --- T277 (#2270): the safety card, "Stop this book now" and the backtest card ---

_REASON = "the drawdown looks wrong, stopping to check"
_ENGAGED_LINE = "paper kill: engaged (kill_switch event 4)"


class _Child:
    """A fake `paper kill` child: exits `code`, or is still running at the bound."""

    def __init__(self, code: int | None, stdout: str, stderr: str) -> None:
        self.returncode, self.out, self.err = code, stdout, stderr
        self.terminated = False

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]:
        if self.returncode is None and timeout is not None:
            raise subprocess.TimeoutExpired("paper kill", timeout)
        return self.out, self.err

    def terminate(self) -> None:
        self.terminated = True

    kill = terminate


class _Starter:
    """Replaces `book_actions._start`; `check` runs when the child would start."""

    def __init__(
        self,
        code: int | None = 0,
        stdout: str = _ENGAGED_LINE,
        stderr: str = "",
        check: Callable[[], None] | None = None,
    ) -> None:
        self.code, self.stdout, self.stderr, self.check = code, stdout, stderr, check
        self.argvs: list[list[str]] = []
        self.children: list[_Child] = []

    def __call__(self, argv: list[str], *, env: dict[str, str], cwd: Path) -> _Child:
        if self.check is not None:
            self.check()
        self.argvs.append(argv)
        self.children.append(_Child(self.code, self.stdout, self.stderr))
        return self.children[-1]


def _starter(monkeypatch: pytest.MonkeyPatch, starter: _Starter | None = None) -> _Starter:
    starter = starter or _Starter()
    monkeypatch.setattr(book_actions, "_start", starter)
    return starter


def _open_dialog(at: AppTest) -> None:
    at.button(key=f"{book_page.STOP_BUTTON_KEY}_main").click().run(timeout=30)


def _confirm(at: AppTest, reason: str | None = _REASON) -> None:
    if reason is not None:
        at.text_area(key=book_page.STOP_REASON_KEY).input(reason).run(timeout=30)
    at.button(key=book_page.STOP_CONFIRM_KEY).click().run(timeout=30)


def test_the_safety_card_draws_the_view_and_computes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    text = _text(at)
    assert "Matches Alpaca to the cent." in text
    assert "Checked Friday, October 2, 5:00 PM ET." in text
    assert "Trading: the kill switch is off." in text
    assert "Largest position: 4.8% against a limit of 5.0%" in text


def test_a_reconciliation_that_did_not_match_says_what_differs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    words = (
        f"Does not match Alpaca as of {_CHECKED.isoformat()}: the books do not match "
        "Alpaca: cash differs."
    )
    view = _view(safety=_safety(matches=False, text=words))
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"), view)
    text = _text(at)
    assert "to the cent" not in text
    assert "Does not match Alpaca as of Friday, October 2, 5:00 PM ET" in text
    assert "cash differs" in text and "+00:00" not in text


def test_a_stopped_book_says_since_when_and_why_and_the_desk_resume(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    view = _view(status="stopped", safety=_safety(running=False, since=_CHECKED))
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"), view)
    text = _text(at)
    assert "Halted since Friday, October 2, 5:00 PM ET: stopped by the owner: checking." in text
    assert "tradepartner paper resume --book=main" in text
    labels = [b.label for b in at.button]
    assert not [b for b in labels if "esume" in b or "stop all" in b.lower() or "every" in b]


def test_the_stop_dialog_names_the_book_says_what_happens_and_starts_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    starter = _starter(monkeypatch)
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    _open_dialog(at)
    text = _text(at)
    assert "Stop main now?" in text
    assert "no run places an order for main" in text
    box = at.text_area(key=book_page.STOP_REASON_KEY)
    assert box.value in (None, "")
    assert starter.argvs == []


@pytest.mark.parametrize("reason", [None, "", "   \n "])
def test_an_empty_or_blank_reason_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, reason: str | None
) -> None:
    starter = _starter(monkeypatch)
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    _open_dialog(at)
    _confirm(at, reason)
    assert not at.exception
    assert starter.argvs == []
    assert "empty or blank reason stops nothing" in _text(at)


def test_the_reason_box_is_capped_by_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The box caps at `ui.max_reason_chars`; `book_actions.kill` refuses longer
    text before anything runs (its own test)."""
    monkeypatch.setenv("UI__MAX_REASON_CHARS", "40")
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    _open_dialog(at)
    assert at.text_area(key=book_page.STOP_REASON_KEY).proto.max_chars == 40


@pytest.mark.parametrize(
    ("code", "engaged"),
    [(0, True), (2, False), (3, False), (4, False), (5, False), (6, False), (77, False)],
)
def test_the_outcome_is_the_exit_code_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int, engaged: bool
) -> None:
    starter = _starter(monkeypatch, _Starter(code))
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    _open_dialog(at)
    _confirm(at)
    assert not at.exception
    assert len(starter.argvs) == 1
    assert starter.argvs[0][-2:] == ["--book=main", f"--reason={_REASON}"]
    text = _text(at)
    if engaged:
        assert [s.value for s in at.success] == ["The kill switch is engaged for main."]
        assert "NOT engaged" not in text
    else:
        assert "The kill switch is NOT engaged for main" in text and not at.success
    assert _ENGAGED_LINE in text  # the command's own text, shown, never read


def test_exit_0_after_the_store_busy_line_is_engaged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    waiting = "paper kill: kill switch NOT engaged yet: store busy (held by x), waiting up to 300 s"
    _starter(monkeypatch, _Starter(0, stderr=waiting))
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    _open_dialog(at)
    _confirm(at)
    assert [s.value for s in at.success] == ["The kill switch is engaged for main."]


def test_past_the_bound_it_says_not_confirmed_and_neither_kills_nor_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    starter = _starter(monkeypatch, _Starter(None))
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    _open_dialog(at)
    _confirm(at)
    assert "NOT confirmed" in _text(at)
    assert len(starter.argvs) == 1 and not starter.children[0].terminated
    at.run(timeout=30)
    assert len(starter.argvs) == 1


def test_a_stale_second_click_with_the_same_reason_runs_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The Override page's guard: a second click whose message still carries the
    pre-clear reason is refused by content, not timing."""
    starter = _starter(monkeypatch)
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    _open_dialog(at)
    _confirm(at)
    assert len(starter.argvs) == 1
    assert at.session_state[book_page.STOP_REASON_KEY] == ""
    del at.session_state[book_page.STOP_OUTCOME_KEY]  # the form shows again
    at.run(timeout=30)
    at.session_state[book_page.STOP_REASON_KEY] = f"  {_REASON} "
    at.button(key=book_page.STOP_CONFIRM_KEY).click().run(timeout=30)
    assert len(starter.argvs) == 1
    assert "already" in _text(at)


def test_no_store_connection_is_open_when_the_child_starts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(tmp_path / "s.duckdb")
    opened: list[bool] = []

    def no_connection_open() -> None:
        # A write open in this process fails at once while any read-only one is open.
        probe = Settings(_env_file=None, store={"path": str(store), "lock_retry_seconds": 0})
        with open_for_write(probe):
            opened.append(True)

    _starter(monkeypatch, _Starter(0, check=no_connection_open))
    at = _app(monkeypatch, store)
    _open_dialog(at)
    _confirm(at)
    assert not at.exception and opened == [True]


def test_a_copy_named_by_the_page_s_own_env_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    live = _store(tmp_path / "live.duckdb")
    _live(monkeypatch, live)
    copy = tmp_path / "copy" / "s.duckdb"
    copy.parent.mkdir()
    shutil.copy(live, copy)
    env = tmp_path / "copy" / ".env"
    env.write_text(f"STORE__PATH={copy}\n")
    starter = _starter(monkeypatch)
    at = _app(monkeypatch, copy, live=False)
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(env))
    at.run(timeout=30)
    assert "not the live store" in _text(at)
    assert f"{book_page.STOP_BUTTON_KEY}_main" not in [b.key for b in at.button]
    assert starter.argvs == []


def test_a_store_with_a_backup_manifest_beside_it_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = _store(tmp_path / "s.duckdb")
    store.with_name(f"{store.name}.manifest.json").write_text("{}")
    starter = _starter(monkeypatch)
    at = _app(monkeypatch, store)
    assert "backup manifest" in _text(at)
    assert f"{book_page.STOP_BUTTON_KEY}_main" not in [b.key for b in at.button]
    assert starter.argvs == []


def test_a_store_outside_the_configured_one_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    live = _store(tmp_path / "live.duckdb")
    _live(monkeypatch, live)
    starter = _starter(monkeypatch)
    at = _app(monkeypatch, _store(tmp_path / "elsewhere.duckdb"), live=False)
    assert "not the live store" in _text(at)
    assert "tradepartner paper kill --book=main" in _text(at)
    assert starter.argvs == []


def test_a_closed_book_offers_no_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"), _view(status="closed"))
    assert f"{book_page.STOP_BUTTON_KEY}_main" not in [b.key for b in at.button]


def test_the_backtest_card(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"))
    text = _text(at)
    assert "5 of 10 sessions with no problem" in text
    assert "The latest tracking report runs through Thursday, October 1: it finished." in text
    assert "tradepartner paper report --book main" in text
    assert "Running a new report counts as one test." in text


def test_no_report_yet_is_plain(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    at = _app(monkeypatch, _store(tmp_path / "s.duckdb"), _view(backtest=_backtest(False)))
    assert "No tracking report yet." in _text(at)


def test_a_real_render_adds_no_trial(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The real view model over a window with frozen risk keys: the page never runs
    `paper report`, so the `trials` row count is the same before and after."""
    path = tmp_path / "s.duckdb"
    frozen = json.dumps({f"risk.{k}": v for k, v in RiskConfig().model_dump(mode="json").items()})
    settings = Settings(_env_file=None, store={"path": str(path)})
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        conn.execute(
            "INSERT INTO hypotheses (hypothesis_id, slug, family, title, doc_path, doc_sha256, "
            "params_json, params_sha256, in_sample_start, holdout_start, holdout_end, "
            "registered_at, registered_by) VALUES (1, 'h-view', 'momentum', 'view', "
            "'docs/hypotheses/h-view.md', repeat('0', 64), '{}', repeat('0', 64), ?, ?, ?, ?, "
            "'test')",
            [_D0, _D0, _D0, _NOW],
        )
        append(
            conn,
            PaperWindowRow(
                hypothesis_id=1,
                first_rebalance_session=_D0,
                account_id="PA0",
                starting_cash=100_000.0,
                starting_equity=100_000.0,
                code_version="test",
                started_at=_NOW,
                frozen_json=frozen,
                frozen_sha256="0" * 64,
                book_id="main",
                known_at=_NOW,
                ingested_at=_NOW,
            ),
        )
    count = "SELECT count(*) FROM trials"
    with open_for_write(settings) as conn:
        before = conn.execute(count).fetchone()
    _live(monkeypatch, path)
    monkeypatch.setattr(book_page, "_now", lambda: _NOW)
    monkeypatch.setenv("STORE__PATH", str(path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "missing.env"))
    at = AppTest.from_file(_APP).run()
    monkeypatch.setenv("DASHBOARD_TEST_PAGE", "Books")
    at.run(timeout=30)
    assert not at.exception, at.exception
    assert "Running a new report counts as one test." in _text(at)
    with open_for_write(settings) as conn:
        assert conn.execute(count).fetchone() == before


_BANNED_MODULES = (
    "tradepartner.dashboard.override_page",
    "tradepartner.execution.switch",
    "tradepartner.execution.window",
    "tradepartner.execution.wrapper",
    "tradepartner.execution.run",
    "tradepartner.execution.phases",
    "tradepartner.execution.brokers",
    "tradepartner.adapters",
)
_BANNED_NAMES = {"submit", "engage", "open_for_write", "override_page", "switch", "window"}


@pytest.mark.parametrize("module", [book_page, book_actions])
def test_no_second_kill_path_is_imported(module: object) -> None:
    tree = ast.parse(Path(str(getattr(module, "__file__", ""))).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            names = [base, *(f"{base}.{a.name}" for a in node.names)]
            assert not {a.name for a in node.names} & _BANNED_NAMES, ast.dump(node)
        else:
            continue
        for name in names:
            assert not name.startswith(_BANNED_MODULES), name
