"""The Book page view model (plan T279, #2170).

Hand values (scenario `_seed`): the window starts Tue 2026-09-29 10:00 ET, so the
base session B is Mon 09-28 at 100,000; marks (cash only) 09-29 100,500, 09-30
101,500, 10-01 99,200; SPY 5,000 / 5,050 / 5,150 / 5,050. `NOW` is Fri 10-02 after
the close, so the as-of session is 10-01 (the last marked), the since-start return
is -0.8% against SPY +1.0%, the best day is 09-30 (+1,000), the worst 10-01
(-2,300), the maximum drawdown 101,500 to 99,200 (-2,300, -2.266%), and three
daily rebalances (09-30, 10-01, 10-02) have closed: "3 of 5 rebalances".
"""

from __future__ import annotations

import ast
import json
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tradepartner.calendar import is_session, next_session
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution import book_view
from tradepartner.store.db import open_for_write, open_read_only
from tradepartner.store.journal import (
    DecisionRow,
    FillRow,
    KillSwitchRow,
    OrderRow,
    PaperReportRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    ReconciliationRow,
    append,
)

NOW = datetime(2026, 10, 2, 22, 0, tzinfo=UTC)
KNOWN = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(hours=3)
B = date(2026, 9, 28)
T0 = date(2026, 9, 30)
T1 = date(2026, 10, 1)
STARTED = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)
MARKS = {date(2026, 9, 29): 100_500.0, T0: 101_500.0, T1: 99_200.0}
SPY = {B: 5_000.0, date(2026, 9, 29): 5_050.0, T0: 5_150.0, T1: 5_050.0}
FROZEN = json.dumps(
    {
        **{f"risk.{k}": v for k, v in RiskConfig().model_dump(mode="json").items()},
        "paper.min_rebalances": 5,
    }
)
HYPOTHESIS_PARAMS = '{"costs.per_side_bps": 5.0, "schedule.rebalance_cadence": "daily"}'


def _stamp(at: datetime = KNOWN) -> dict[str, datetime]:
    return {"known_at": at, "ingested_at": at}


def _seed(
    settings: Settings,
    *,
    marks: dict[date, float] | None = None,
    spy: dict[date, float] | None = None,
    started: datetime = STARTED,
    names_per_mark: int = 0,
) -> None:
    """Hypothesis 1 (daily), window 1, one finished run, the marks, SPY bars and
    the tracking trial 7 (no result row)."""
    marks = MARKS if marks is None else marks
    spy = SPY if spy is None else spy
    with open_for_write(settings) as conn:
        conn.execute(
            "INSERT INTO hypotheses (hypothesis_id, slug, family, title, doc_path, doc_sha256, "
            "params_json, params_sha256, in_sample_start, holdout_start, holdout_end, "
            "registered_at, registered_by) VALUES (1, 'h-view', 'momentum', 'view', "
            "'docs/hypotheses/h-view.md', repeat('0', 64), ?, repeat('0', 64), ?, ?, ?, ?, 'test')",
            [HYPOTHESIS_PARAMS, B, B, B, KNOWN],
        )
        append(
            conn,
            PaperWindowRow(
                window_id=1,
                hypothesis_id=1,
                first_rebalance_session=T0,
                account_id="PA1",
                starting_cash=100_000.0,
                starting_equity=100_000.0,
                code_version="test",
                started_at=started,
                frozen_json=FROZEN,
                frozen_sha256="0" * 64,
                **_stamp(started),
            ),
        )
        run_id = append(
            conn,
            PaperRunRow(
                window_id=1,
                session=date(2026, 10, 2),
                kind="rebalance",
                started_at=KNOWN,
                invoked_by="scheduler",
                code_version="abc",
                **_stamp(),
            ),
        )
        assert run_id is not None
        append(
            conn,
            PaperRunResultRow(
                run_id=run_id,
                finished_at=KNOWN + timedelta(minutes=5),
                status="ok",
                clock_fault=False,
                **_stamp(KNOWN + timedelta(minutes=5)),
            ),
        )
        for session, equity in marks.items():
            names = max(names_per_mark, 0)
            held = equity * 0.5
            for i in range(names):
                append(
                    conn,
                    PositionDailyRow(
                        run_id=run_id,
                        session=session,
                        security_id=f"SEC{i}",
                        quantity=10.0,
                        mark_price=100.0,
                        value=held / names,
                        cash=equity - held,
                        **_stamp(),
                    ),
                )
            if names == 0:
                append(
                    conn,
                    PositionDailyRow(
                        run_id=run_id,
                        session=session,
                        security_id=None,
                        quantity=0.0,
                        cash=equity,
                        **_stamp(),
                    ),
                )
        for session, close in spy.items():
            stamp = datetime.combine(session, datetime.min.time(), tzinfo=UTC) + timedelta(hours=21)
            conn.execute(
                "INSERT INTO prices_daily (security_id, session, open, high, low, close, "
                "volume, known_at, ingested_at, source, provenance) "
                "VALUES ('SEC_SPY', ?, ?, ?, ?, ?, 1, ?, ?, 'alpaca', 'bar')",
                [session, close, close, close, close, stamp, stamp],
            )
        conn.execute(
            "INSERT INTO trials (trial_id, hypothesis_id, kind, started_at, start_session, "
            "end_session, code_version, synthetic, run_by) VALUES "
            "(7, 1, 'tracking', ?, ?, ?, 'test', FALSE, 'test')",
            [KNOWN, T0, T1],
        )


def _view(settings: Settings, now: datetime = NOW) -> book_view.BookView:
    with open_read_only(settings) as conn:
        return book_view.build(conn, settings, "main", now)


def _append(settings: Settings, *rows: object) -> None:
    with open_for_write(settings) as conn:
        for row in rows:
            append(conn, row)  # type: ignore[arg-type]


def _reconciliation(
    status: str = "ok",
    *,
    payload: dict[str, object] | None = None,
    at: datetime = KNOWN,
    known: datetime | None = None,
) -> ReconciliationRow:
    body = {"mismatches": [], "lagging": [], "pending": [], "allowed": []}
    body.update(payload or {})
    return ReconciliationRow(
        window_id=1,
        run_id=1,
        at=at,
        status=status,
        broker_cash=99_200.0,
        mismatches_json=json.dumps(body),
        **_stamp(known or at),
    )


def _counts(settings: Settings) -> dict[str, int]:
    with open_read_only(settings) as conn:
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


def test_the_header_and_performance_hand_values(journal_settings: Settings) -> None:
    _seed(journal_settings)
    view = _view(journal_settings)
    assert view.window_id == 1
    header = view.header
    assert header is not None
    assert header.status == "running"
    assert header.as_of_session == T1
    assert header.value == pytest.approx(99_200.0)
    assert header.since_start_return_pct == pytest.approx(-0.8)
    assert header.spy_return_pct == pytest.approx(1.0)
    assert header.benchmark_error is None
    perf = view.performance
    assert perf is not None
    assert [r.range_key for r in perf.ranges] == ["since_start", "1M", "3M"]
    assert perf.ranges[0].start_session == B
    assert perf.ranges[1].note is not None  # the book is younger than a month
    assert perf.best_day is not None and perf.best_day.session == T0
    assert perf.best_day.dollars == pytest.approx(1_000.0)
    assert perf.worst_day is not None and perf.worst_day.session == T1
    assert perf.worst_day.dollars == pytest.approx(-2_300.0)
    assert perf.max_drawdown is not None
    assert perf.max_drawdown.pct == pytest.approx(100 * (99_200 / 101_500 - 1))
    assert perf.tracking is not None
    assert perf.tracking.text == "3 of 5 rebalances"


def test_today_and_holdings_are_read(journal_settings: Settings) -> None:
    _seed(journal_settings)
    view = _view(journal_settings)
    assert view.today_session == date(2026, 10, 2)
    assert view.today is not None and view.today.digest.startswith("No trades")
    assert view.holdings is not None and view.holdings.rows == ()
    assert view.holdings.summary.cash == pytest.approx(99_200.0)


def test_a_book_with_no_window_is_empty(journal_settings: Settings) -> None:
    view = _view(journal_settings)
    assert view.window_id is None
    assert view.header is None and view.safety is None and view.backtest is None


def test_it_imports_no_streamlit() -> None:
    tree = ast.parse(Path(book_view.__file__).read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    assert "streamlit" not in names


# --- the safety part ----------------------------------------------------------------


def test_no_reconciliation_is_never_a_match(journal_settings: Settings) -> None:
    _seed(journal_settings)
    safety = _view(journal_settings).safety
    assert safety is not None
    assert not safety.reconciliation.matches
    assert safety.reconciliation.text == book_view.NO_RECONCILIATION


def test_an_ok_row_with_nothing_off_matches_to_the_cent(journal_settings: Settings) -> None:
    _seed(journal_settings)
    _append(journal_settings, _reconciliation())
    safety = _view(journal_settings).safety
    assert safety is not None
    assert safety.reconciliation.matches
    assert "Matches Alpaca to the cent" in safety.reconciliation.text
    assert "risk.reconcile_cash_tolerance" in safety.reconciliation.fields_read


def test_an_ok_row_whose_cash_differs_by_more_than_the_tolerance_is_no_match(
    journal_settings: Settings,
) -> None:
    allowed = [
        {"reason": "cash within the open allowances", "difference": 0.5, "security_id": None}
    ]
    _seed(journal_settings)
    _append(journal_settings, _reconciliation(payload={"allowed": allowed}))
    recon = _view(journal_settings).safety.reconciliation  # type: ignore[union-attr]
    assert not recon.matches
    assert "$0.50" in recon.text and "not to the cent" in recon.text


def test_a_cash_difference_inside_the_tolerance_still_matches(journal_settings: Settings) -> None:
    allowed = [{"reason": "cash within the open allowances", "difference": 0.004}]
    _seed(journal_settings)
    _append(journal_settings, _reconciliation(payload={"allowed": allowed}))
    assert _view(journal_settings).safety.reconciliation.matches  # type: ignore[union-attr]


def test_a_mismatch_row_says_what_differs(journal_settings: Settings) -> None:
    mismatch = {"kind": "cash", "detail": "broker 99200.0, ledger 99000.0"}
    _seed(journal_settings)
    _append(journal_settings, _reconciliation("mismatch", payload={"mismatches": [mismatch]}))
    recon = _view(journal_settings).safety.reconciliation  # type: ignore[union-attr]
    assert not recon.matches
    assert "broker 99200.0, ledger 99000.0" in recon.text


def test_an_unreadable_or_open_ok_row_is_no_match(journal_settings: Settings) -> None:
    _seed(journal_settings)
    unreadable = _reconciliation()
    _append(journal_settings, unreadable.__class__(**{**vars(unreadable), "mismatches_json": "{"}))
    assert not _view(journal_settings).safety.reconciliation.matches  # type: ignore[union-attr]
    _append(
        journal_settings,
        _reconciliation(payload={"lagging": ["c1"]}, at=KNOWN + timedelta(minutes=1)),
    )
    assert not _view(journal_settings).safety.reconciliation.matches  # type: ignore[union-attr]


def test_the_kill_switch_reads_running_or_stopped_with_a_reason(
    journal_settings: Settings,
) -> None:
    _seed(journal_settings)
    safety = _view(journal_settings).safety
    assert safety is not None and safety.kill_switch.running
    engaged_at = KNOWN + timedelta(hours=1)
    _append(
        journal_settings,
        KillSwitchRow(
            window_id=1,
            at=engaged_at,
            state="engaged",
            source="owner",
            reason="checking a fill",
            **_stamp(engaged_at),
        ),
    )
    view = _view(journal_settings)
    assert view.header is not None and view.header.status == "stopped"
    assert view.safety is not None
    kill = view.safety.kill_switch
    assert not kill.running
    assert kill.since == engaged_at
    assert "checking a fill" in kill.reason and "Stopped since" in kill.text


def test_the_limits_come_out_in_plain_words(journal_settings: Settings) -> None:
    _seed(journal_settings)
    limits = _view(journal_settings).safety.limits  # type: ignore[union-attr]
    assert [line.key for line in limits] == ["cash", "gross", "position", "drawdown"]
    assert all("points" in line.sentence and "Past it" in line.sentence for line in limits)


# --- the backtest part --------------------------------------------------------------


def test_the_backtest_part_reads_the_report_trial_and_the_command(
    journal_settings: Settings,
) -> None:
    _seed(journal_settings)
    backtest = _view(journal_settings).backtest
    assert backtest is not None
    assert backtest.report is None and backtest.shakedown is None
    assert backtest.command == "tradepartner paper report --book main"
    _append(
        journal_settings,
        PaperReportRow(window_id=1, trial_id=7, through_session=T1, run_at=KNOWN, **_stamp()),
    )
    with open_for_write(journal_settings) as conn:
        conn.execute(
            "INSERT INTO trial_results (trial_id, finished_at, status, message) "
            "VALUES (7, ?, 'ok', NULL)",
            [KNOWN],
        )
        conn.execute(
            "INSERT INTO owner_decisions (decision_id, made_at, kind, values_json, reason) "
            "VALUES (1, ?, 'shakedown_span', ?, 'test')",
            [STARTED, json.dumps({"sessions": 10, "order_sessions": 3})],
        )
    backtest = _view(journal_settings).backtest
    assert backtest is not None and backtest.report is not None
    assert backtest.report.through_session == T1
    assert backtest.report.trial.status == "ok"
    assert backtest.shakedown is not None
    assert backtest.shakedown.sessions_needed == 10


# --- no look-ahead, no writes, time -------------------------------------------------


def test_rows_known_or_dated_after_now_move_nothing(journal_settings: Settings) -> None:
    _seed(journal_settings)
    before = _view(journal_settings)
    with open_for_write(journal_settings) as conn:
        decision = append(
            conn,
            DecisionRow(
                run_id=1,
                security_id="SEC9",
                whole_share=True,
                decision="trade",
                **_stamp(LATER),
            ),
        )
        assert decision is not None
        append(
            conn,
            OrderRow(
                client_order_id="late-1",
                decision_id=decision,
                run_id=1,
                session=date(2026, 10, 2),
                attempt=1,
                phase="buy",
                security_id="SEC9",
                symbol="LATE",
                side="buy",
                quantity=10.0,
                sells_in_flight_at_submit=False,
                **_stamp(LATER),
            ),
        )
        append(
            conn,
            FillRow(
                client_order_id="late-1",
                filled_at=LATER,
                quantity=10.0,
                price=50.0,
                price_implied=False,
                broker_fill_id="f-late",
                source="broker_feed",
                **_stamp(LATER),
            ),
        )
        # A mark known after now, and a mark dated after the last completed session.
        for session, known in ((T1, LATER), (date(2026, 10, 5), KNOWN)):
            append(
                conn,
                PositionDailyRow(
                    run_id=1,
                    session=session,
                    security_id=None,
                    quantity=0.0,
                    cash=1.0,
                    **_stamp(known),
                ),
            )
        append(conn, _reconciliation("mismatch", at=NOW - timedelta(hours=1), known=LATER))
        append(conn, _reconciliation("mismatch", at=LATER, known=LATER))
        append(
            conn,
            KillSwitchRow(window_id=1, at=LATER, state="engaged", source="owner", **_stamp(LATER)),
        )
        conn.execute(
            "INSERT INTO trials (trial_id, hypothesis_id, kind, started_at, start_session, "
            "end_session, code_version, synthetic, run_by) VALUES "
            "(8, 1, 'tracking', ?, ?, ?, 'test', FALSE, 'test')",
            [LATER, T0, T1],
        )
        append(
            conn,
            PaperReportRow(
                window_id=1, trial_id=8, through_session=T1, run_at=LATER, **_stamp(LATER)
            ),
        )
    assert _view(journal_settings) == before


def test_a_build_changes_no_row_count_and_adds_no_trial(journal_settings: Settings) -> None:
    _seed(journal_settings)
    _append(journal_settings, _reconciliation())
    before = _counts(journal_settings)
    _view(journal_settings)
    assert _counts(journal_settings) == before
    assert before["trials"] == 1


def _sessions(count: int) -> list[date]:
    out: list[date] = []
    day = date(2026, 5, 1)
    while len(out) < count:
        if is_session(day):
            out.append(day)
        day = next_session(day)
    return out


def test_the_whole_read_fits_in_half_the_lock_retry(journal_settings: Settings) -> None:
    sessions = _sessions(110)
    marks = {s: 100_000.0 + 10 * i for i, s in enumerate(sessions)}
    base = date(2026, 4, 30)
    spy = {s: 5_000.0 + i for i, s in enumerate([base, *sessions])}
    started = datetime(2026, 5, 1, 14, 0, tzinfo=UTC)
    _seed(journal_settings, marks=marks, spy=spy, started=started, names_per_mark=25)
    _append(journal_settings, _reconciliation(at=NOW - timedelta(days=30)))
    now = datetime.combine(sessions[-1], datetime.min.time(), tzinfo=UTC) + timedelta(hours=23)
    began = time.perf_counter()
    view = _view(journal_settings, now)
    elapsed = time.perf_counter() - began
    assert view.header is not None and view.header.as_of_session == sessions[-1]
    assert view.holdings is not None and view.holdings.summary.count == 25
    assert elapsed < journal_settings.store.lock_retry_seconds / 2


def test_a_naive_now_is_refused(journal_settings: Settings) -> None:
    _seed(journal_settings)
    with open_read_only(journal_settings) as conn, pytest.raises(ValueError):
        book_view.build(conn, journal_settings, "main", NOW.replace(tzinfo=None))
