"""The Home view model (plan T273, #2170): words for every item kind, the status
line's three levels, a card per book, Data status, the Research strip, and the
read's bound and its cut at `now`."""

from __future__ import annotations

import json
import time
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pytest

from tradepartner import health
from tradepartner.config import RiskConfig, Settings
from tradepartner.dashboard import research_page
from tradepartner.execution import home_view, ops
from tradepartner.execution.waiting import KIND_ORDER, WaitingItem
from tradepartner.store import schema
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    PaperReportRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    append,
)

NOW = datetime(2026, 10, 2, 22, 0, tzinfo=UTC)  # Friday, after the close
KNOWN = datetime(2026, 10, 2, 21, 30, tzinfo=UTC)
FROZEN = json.dumps(
    {f"risk.{k}": v for k, v in RiskConfig(max_fill_lag_sessions=1).model_dump(mode="json").items()}
)
MARKS = {
    date(2026, 9, 30): 101_500.0,
    date(2026, 10, 1): 99_200.0,
    date(2026, 10, 2): 101_000.0,
}
SPY = {date(2026, 9, 28): 5_000.0, date(2026, 9, 29): 5_050.0, **{d: 5_100.0 for d in MARKS}}


# --- words -------------------------------------------------------------------------


def _item(kind: str, command: str | None, book: str = "main") -> WaitingItem:
    return WaitingItem(
        book_id=book,
        kind=kind,  # type: ignore[arg-type]
        since=date(2026, 10, 1),
        detail="RAW DETAIL that must not reach the page",
        command=command,
    )


@pytest.mark.parametrize("kind", KIND_ORDER)
def test_every_waiting_kind_has_a_title_a_sentence_and_an_action(kind: str) -> None:
    # `waiting.build` gives every kind a command except these two.
    command = None if kind in ("missed_run", "unreadable") else f"tradepartner x {kind}"
    (out,) = home_view._needs_you((_item(kind, command),), ())
    assert out.title and out.sentence.endswith(".") and "RAW DETAIL" not in out.sentence
    assert out.action, "an item always says what to do or where to look"
    if command:
        assert out.action == command  # the exact command text
    assert out.kind == kind and "main" in out.title


def test_an_overdue_delisting_points_at_alpacas_activity_and_runs_nothing() -> None:
    late = ops.OverdueDelisting("b", "SEC9", "ZZZ", date(2026, 10, 1))
    (out,) = home_view._needs_you((), (late,))
    assert out.action == "Check Alpaca's activity" and "ZZZ" in out.title and "b" in out.title


def test_items_come_out_most_urgent_first() -> None:
    late = ops.OverdueDelisting("main", "S", "ZZZ", date(2026, 10, 1))
    items = tuple(_item(k, None) for k in reversed(KIND_ORDER))
    kinds = [n.kind for n in home_view._needs_you(items, (late,))]
    assert kinds == [
        "kill_switch_engaged",
        "order_lagging",
        "overdue_delisting",
        "missed_run",
        "shakedown_note",
        "report_due",
        "unreadable",
    ]


# --- the status line ---------------------------------------------------------------


def _summary(book: str = "main", *, engaged: bool = False, **over: Any) -> Any:
    fields: dict[str, Any] = {
        "book_id": book,
        "is_open": True,
        "switch_state": SimpleNamespace(engaged=engaged),
        "next_rebalance_session": date(2026, 10, 5),
        "last_run_session": date(2026, 10, 2),
        "last_run_status": "ok",
        "window": SimpleNamespace(first_rebalance_session=date(2026, 9, 30)),
    }
    return SimpleNamespace(**{**fields, **over})


def _need(kind: str) -> home_view.NeedsYou:
    return home_view.NeedsYou("main", kind, "t", "s.", "a")


def test_the_status_line_is_normal_with_nothing_waiting() -> None:
    line = home_view._status(Settings(_env_file=None), (_summary(),), ())
    assert (line.level, line.text) == ("ok", "Everything is running normally")
    assert line.next_run == "Next run at 08:05 ET" and line.next_rebalance == date(2026, 10, 5)


def test_the_status_line_is_amber_for_items_that_leave_trading_alone() -> None:
    line = home_view._status(Settings(_env_file=None), (_summary(),), (_need("report_due"),))
    assert line.level == "amber" and "not affected" in line.text


@pytest.mark.parametrize(
    ("summary", "needs"),
    [
        (_summary(engaged=True), ()),
        (_summary(), (_need("missed_run"),)),
    ],
)
def test_the_status_line_is_red_for_a_halted_book_or_a_missed_run(
    summary: Any, needs: tuple[home_view.NeedsYou, ...]
) -> None:
    settings = Settings(_env_file=None, ui={"next_run_time_et": "09:30"})
    line = home_view._status(settings, (summary,), needs)
    assert line.level == "red" and line.next_run == "Next run at 09:30 ET"


# --- the card's pill and strategy sentence -----------------------------------------


def test_the_pill() -> None:
    assert home_view._pill(_summary()) == "Trading"
    assert home_view._pill(_summary(last_run_session=None)) == "Starts Wednesday"
    assert home_view._pill(_summary(engaged=True)) == "Halted"
    assert home_view._pill(_summary(is_open=False)) == "Closed"


def _hypothesis(conn: duckdb.DuckDBPyConnection, slug: str = "h1-momentum-12-1") -> None:
    conn.execute(
        "INSERT INTO hypotheses (hypothesis_id, slug, family, title, doc_path, doc_sha256, "
        "params_json, params_sha256, in_sample_start, holdout_start, holdout_end, "
        "registered_at, registered_by) VALUES (1, ?, 'momentum', 'Registry title', "
        "'docs/hypotheses/x.md', repeat('0', 64), ?, repeat('0', 64), ?, ?, ?, ?, 'test')",
        [
            slug,
            '{"costs.per_side_bps": 5.0, "schedule.rebalance_cadence": "daily"}',
            date(2026, 9, 1),
            date(2026, 9, 1),
            date(2026, 9, 1),
            KNOWN,
        ],
    )


def _window(conn: duckdb.DuckDBPyConnection, book: str, **over: Any) -> int:
    started = over.pop("started_at", datetime(2026, 9, 29, 14, 0, tzinfo=UTC))
    row = PaperWindowRow(
        hypothesis_id=1,
        first_rebalance_session=over.pop("first", date(2026, 9, 30)),
        account_id=f"PA-{book}",
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=started,
        frozen_json=FROZEN,
        frozen_sha256="0" * 64,
        book_id=book,
        known_at=started,
        ingested_at=started,
    )
    window_id = append(conn, replace(row, **over))  # type: ignore[arg-type]
    assert window_id is not None
    return window_id


def _run(conn: duckdb.DuckDBPyConnection, window_id: int, session: date, status: str) -> int:
    started = datetime.combine(session, datetime.min.time(), tzinfo=UTC) + timedelta(hours=14)
    stamp = {"known_at": started, "ingested_at": started}
    run_id = append(
        conn,
        PaperRunRow(
            window_id=window_id,
            session=session,
            kind="rebalance",
            started_at=started,
            invoked_by="scheduler",
            code_version="abc",
            **stamp,
        ),
    )
    assert run_id is not None
    done = started + timedelta(minutes=5)
    stamp = {"known_at": done, "ingested_at": done}
    append(
        conn,
        PaperRunResultRow(
            run_id=run_id, finished_at=done, status=status, clock_fault=False, **stamp
        ),
    )
    return run_id


def _mark(conn: duckdb.DuckDBPyConnection, run_id: int, session: date, cash: float) -> None:
    append(
        conn,
        PositionDailyRow(
            run_id=run_id,
            session=session,
            security_id=None,
            quantity=0.0,
            cash=cash,
            known_at=KNOWN,
            ingested_at=KNOWN,
        ),
    )


@pytest.fixture
def store(journal_settings: Settings) -> Settings:
    """`main`: trading, three cash-only marks, SPY bars. `b`: opened after the close,
    no run yet. `h`: its latest run halted (an engaged switch)."""
    with open_for_write(journal_settings) as conn:
        _hypothesis(conn)
        main = _window(conn, "main")
        _window(conn, "b", first=date(2026, 10, 5), started_at=NOW)
        halted = _window(conn, "h")
        _run(conn, halted, date(2026, 10, 1), "halted")
        run_id = _run(conn, main, date(2026, 10, 1), "ok")
        _run(conn, main, date(2026, 10, 2), "ok")
        for session, cash in MARKS.items():
            _mark(conn, run_id, session, cash)
        for session, close in SPY.items():
            stamp = datetime.combine(session, datetime.min.time(), tzinfo=UTC) + timedelta(hours=21)
            conn.execute(
                "INSERT INTO prices_daily (security_id, session, open, high, low, close, "
                "volume, known_at, ingested_at, source, provenance) "
                "VALUES ('SEC_SPY', ?, ?, ?, ?, ?, 1, ?, ?, 'alpaca', 'bar')",
                [session, close, close, close, close, stamp, stamp],
            )
    return journal_settings


def _build(settings: Settings, now: datetime = NOW) -> home_view.HomeView:
    with open_read_only(settings) as conn:
        return home_view.build(conn, settings, now)


def test_strategy_sentence_uses_the_blurb_then_the_registry_title(
    store: Settings, tmp_path: Path
) -> None:
    blurbs = tmp_path / "b.toml"
    blurbs.write_text('[blurbs]\n"h1-momentum-12-1" = "Holds last year\'s winners."\n')
    with open_read_only(store) as conn:
        (main,) = (s for s in ops.book_summaries(conn, store) if s.book_id == "main")
        assert home_view.strategy_sentence(conn, main, blurbs) == "Holds last year's winners."
        blurbs.write_text('[blurbs]\n"another-slug" = "Not this one."\n')
        assert home_view.strategy_sentence(conn, main, blurbs) == "Registry title"
        assert home_view.strategy_sentence(conn, main, tmp_path / "gone.toml") == "Registry title"
        blurbs.write_text("not toml [")
        assert home_view.strategy_sentence(conn, main, blurbs) == "Registry title"


def test_the_checked_in_blurbs_cover_every_registered_hypothesis_file() -> None:
    blurbs = home_view._blurbs()
    slugs = {
        line.split('"')[1]
        for path in (home_view.BLURBS_PATH.parents[1] / "hypotheses").glob("*.md")
        for line in path.read_text().splitlines()
        if line.startswith("slug = ")
    }
    assert slugs and slugs <= set(blurbs)
    assert all(text.endswith(".") for text in blurbs.values())


# --- the whole read ----------------------------------------------------------------


def test_build_cards_status_and_needs_on_a_seeded_store(store: Settings) -> None:
    view = _build(store)
    cards = {c.book_id: c for c in view.books}
    assert [c.book_id for c in view.books] == ["b", "h", "main"]
    assert cards["main"].pill == "Trading" and cards["b"].pill == "Starts Monday"
    assert cards["h"].pill == "Halted"
    main = cards["main"]
    assert main.session == date(2026, 10, 2) and main.equity == 101_000.0
    # Since the first mark (10-01 is the base for the book's own series): SPY on the same days.
    assert main.since_start_pct is not None and main.spy_since_start_pct is not None
    assert main.holdings_count == 0 and main.cash == 101_000.0 and not main.unreadable
    assert main.digest
    assert cards["b"].equity is None and cards["b"].digest is None
    assert main.strategy and "Holds" in main.strategy
    assert view.status.level == "red" and "h is halted" in view.status.text
    assert next(n.kind for n in view.needs_you) == "kill_switch_engaged"
    assert view.research == home_view.ResearchStrip("Nothing new yet", None, None, "research")


def test_a_book_with_only_a_normal_run_shows_everything_running_normally(
    journal_settings: Settings,
) -> None:
    with open_for_write(journal_settings) as conn:
        _hypothesis(conn)
        window = _window(conn, "main")
        _run(conn, window, date(2026, 10, 1), "ok")
        _run(conn, window, date(2026, 10, 2), "ok")
        # The first tracking report is already on file, so none is due.
        append(
            conn,
            PaperReportRow(
                window_id=window,
                trial_id=99,
                through_session=date(2026, 10, 1),
                run_at=KNOWN,
                known_at=KNOWN,
                ingested_at=KNOWN,
            ),
        )
    assert _build(journal_settings).status.text == "Everything is running normally"


def test_a_fact_known_after_now_moves_nothing(store: Settings) -> None:
    """A later session's mark, stamped after `now`, changes no card and no line."""
    before = _build(store)
    with open_for_write(store) as conn:
        (run_id,) = conn.execute(
            "SELECT run_id FROM paper_runs WHERE session = ?", [date(2026, 10, 1)]
        ).fetchone()  # type: ignore[misc]
        # `h`'s run is also on 10-01; mark under the lowest id, `main`'s window is checked.
        later = datetime(2026, 10, 5, 21, 30, tzinfo=UTC)
        append(
            conn,
            PositionDailyRow(
                run_id=run_id,
                session=date(2026, 10, 5),
                security_id=None,
                quantity=0.0,
                cash=1.0,
                known_at=later,
                ingested_at=later,
            ),
        )
    assert _build(store) == before


def test_the_read_stays_within_half_the_lock_retry_seconds(store: Settings) -> None:
    fast = Settings(_env_file=None, store={"path": store.store.path, "lock_retry_seconds": 2})
    started = time.monotonic()
    _build(fast)
    assert time.monotonic() - started < fast.store.lock_retry_seconds / 2


def test_the_read_writes_nothing(store: Settings) -> None:
    def counts() -> list[int]:
        with open_read_only(store) as conn:
            return [
                conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # type: ignore[index]
                for t in ("paper_runs", "positions_daily", "trials", "alerts")
            ]

    before = counts()
    _build(store)
    assert counts() == before


# --- Data status -------------------------------------------------------------------


def _ingest(message: str | None) -> health.IngestStatus:
    return health.IngestStatus("edgar", KNOWN, None, "ok", KNOWN, message)


def _data(
    monkeypatch: pytest.MonkeyPatch,
    *,
    message: str | None,
    gaps: int,
    summaries: tuple[Any, ...],
    as_of: datetime | None = KNOWN,
) -> home_view.DataStatus:
    monkeypatch.setattr(health, "last_ingests", lambda _c, _t: (_ingest(message),))
    monkeypatch.setattr(health, "bar_gaps", lambda _c, _t: SimpleNamespace(names_with_gaps=gaps))
    monkeypatch.setattr(
        home_view.header, "store_freshness", lambda _c, _t: SimpleNamespace(as_of=as_of)
    )
    return home_view._data_status(None, summaries, NOW)  # type: ignore[arg-type]


def test_data_is_up_to_date_through_the_last_session(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _data(monkeypatch, message="ok", gaps=0, summaries=(_summary(),))
    assert out.text == "Data is up to date through 2026-10-02" and out.records == 0


def test_open_quarantined_filings_and_bar_holes_need_tidying_not_affecting_trading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counts `quarantined: N` on the latest EDGAR ingest row (the count the `health`
    command warns on) plus `health.bar_gaps` securities with a hole."""
    out = _data(monkeypatch, message="filings 9, quarantined: 3", gaps=2, summaries=(_summary(),))
    assert out.text == "5 records need tidying; trading not affected" and not out.trading_affected


def test_records_say_trading_affected_when_a_halted_run_is_the_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    halted = _summary(last_run_status="halted")
    out = _data(monkeypatch, message="quarantined: 1", gaps=0, summaries=(halted,))
    assert out.text == "1 records need tidying; trading affected" and out.trading_affected


# --- the Research strip ------------------------------------------------------------


def _view(**kind: Any) -> Any:
    return SimpleNamespace(**{"registrations": (), "runs": (), "decisions": (), **kind})


def _strip(monkeypatch: pytest.MonkeyPatch, view: Any) -> home_view.ResearchStrip:
    monkeypatch.setattr(research_page, "load_research_view", lambda _c: view)
    return home_view._research_strip(None)  # type: ignore[arg-type]


def test_the_strip_shows_the_latest_decision_or_run_with_its_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = datetime(2026, 9, 1, tzinfo=UTC)
    new = datetime(2026, 9, 20, tzinfo=UTC)
    reg = SimpleNamespace(slug="s1", title="Does X work?")
    decision = SimpleNamespace(known_at=old, reason="spent\nthe holdout")
    run = SimpleNamespace(slug="s1", known_at=new)
    out = _strip(monkeypatch, _view(registrations=(reg,), runs=(run,), decisions=(decision,)))
    assert (out.title, out.at, out.link) == ("Does X work?", new, "research")
    newer = SimpleNamespace(known_at=new + timedelta(days=1), reason="amended budget")
    out = _strip(monkeypatch, _view(registrations=(reg,), runs=(run,), decisions=(decision, newer)))
    assert out.title == "Decision: amended budget"


def test_the_strip_says_nothing_new_without_research(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _strip(monkeypatch, _view()).text == "Nothing new yet"

    def missing(_c: object) -> None:
        raise schema.ResearchNotInitialised("no tables")

    monkeypatch.setattr(research_page, "load_research_view", missing)
    assert home_view._research_strip(None).text == "Nothing new yet"  # type: ignore[arg-type]
