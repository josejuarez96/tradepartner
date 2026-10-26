"""The pure "waiting on me" builder (plan T256): every kind from hand inputs."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from tradepartner.calendar import session_close
from tradepartner.config import RiskConfig
from tradepartner.execution import waiting
from tradepartner.execution.switch import derive
from tradepartner.execution.waiting import (
    BookFacts,
    LagFacts,
    ReportFacts,
    RunFacts,
    SpanAlert,
    SpanFacts,
    SwitchFacts,
    WaitingItem,
    build,
)
from tradepartner.store.journal import (
    KillSwitchRow,
    OrderRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    ReconciliationRow,
    RunWithResult,
)

THU, FRI, MON, TUE = date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)
STARTED = datetime(2026, 9, 29, 14, 0, tzinfo=UTC)


def _at(day: date, hour: int = 14) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=UTC)


def _window(window_id: int = 1, book_id: str = "main") -> PaperWindowRow:
    return PaperWindowRow(
        window_id=window_id,
        hypothesis_id=1,
        first_rebalance_session=date(2026, 9, 30),
        account_id="PA1",
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=STARTED,
        frozen_json="{}",
        frozen_sha256="0" * 64,
        book_id=book_id,
        known_at=STARTED,
        ingested_at=STARTED,
    )


def _run(run_id: int, started_at: datetime, window_id: int = 1) -> PaperRunRow:
    return PaperRunRow(
        run_id=run_id,
        window_id=window_id,
        session=started_at.date(),
        kind="rebalance",
        started_at=started_at,
        invoked_by="scheduler",
        code_version="abc",
        known_at=started_at,
        ingested_at=started_at,
    )


def _result(run_id: int, status: str, finished_at: datetime) -> PaperRunResultRow:
    return PaperRunResultRow(
        run_id=run_id,
        finished_at=finished_at,
        status=status,
        clock_fault=False,
        known_at=finished_at,
        ingested_at=finished_at,
    )


def _switch_row(
    event_id: int, state: str, at: datetime, source: str = "fault", reason: str | None = None
) -> KillSwitchRow:
    return KillSwitchRow(
        reason=reason,
        event_id=event_id,
        window_id=1,
        at=at,
        state=state,
        source=source,
        known_at=at,
        ingested_at=at,
    )


def _switch(
    rows: Sequence[KillSwitchRow], runs: Sequence[RunWithResult], *, lock_free: bool = True
) -> SwitchFacts:
    window = _window()
    state = derive(
        window,
        rows,
        [rw.run for rw in runs],
        [rw.result for rw in runs if rw.result is not None],
        reading_run=None,
        lock_free=lock_free,
    )
    return SwitchFacts(window=window, state=state, kill_switch_rows=tuple(rows), runs=tuple(runs))


def _order(client_order_id: str = "tp-a") -> OrderRow:
    at = _at(THU, 14)
    return OrderRow(
        client_order_id=client_order_id,
        decision_id=1,
        run_id=1,
        session=THU,
        attempt=1,
        phase="buy",
        security_id="AAA",
        symbol="AAA",
        side="buy",
        quantity=5.0,
        sells_in_flight_at_submit=False,
        known_at=at,
        ingested_at=at,
    )


def _reconciliation(at: datetime, lagging: Sequence[str], rid: int) -> ReconciliationRow:
    return ReconciliationRow(
        reconciliation_id=rid,
        window_id=1,
        at=at,
        status="fills_lagging" if lagging else "ok",
        mismatches_json=json.dumps({"lagging": list(lagging), "mismatches": []}),
        known_at=at,
        ingested_at=at,
    )


def _lag(session: date, rows: Sequence[ReconciliationRow], bound: int = 2) -> LagFacts:
    return LagFacts(
        orders=(_order(),),
        reconciliations=tuple(rows),
        session=session,
        frozen=RiskConfig(max_fill_lag_sessions=bound),
    )


def _kinds(items: Sequence[WaitingItem]) -> list[tuple[str, str]]:
    return [(i.book_id, i.kind) for i in items]


# --- kill_switch_engaged ------------------------------------------------------------


def test_a_switch_engaged_by_a_halt_is_an_item_since_the_engaged_row() -> None:
    halted = RunWithResult(_run(1, _at(THU)), _result(1, "halted", _at(THU, 15)))
    facts = _switch([_switch_row(1, "engaged", _at(THU, 15))], [halted])
    (item,) = build([BookFacts(book_id="main", switch=facts)], None)
    assert item == WaitingItem(
        book_id="main",
        kind="kill_switch_engaged",
        since=_at(THU, 15),
        detail="kill switch engaged: a fault switched it on "
        "(a failed check or broker problem) (event 1); "
        "run 1 halted itself on a failed safety check",
        command='tradepartner paper resume --book main --reason "<why>"',
    )


def test_the_reason_is_one_line_and_scrubbed() -> None:
    row = _switch_row(
        1, "engaged", _at(THU, 15), "owner", reason="stop\n  now, mail me@example.com"
    )
    (item,) = build([BookFacts(book_id="main", switch=_switch([row], []))], None)
    assert "\n" not in item.detail
    assert "reason given: stop now," in item.detail
    assert "me@example.com" not in item.detail


def test_a_switch_with_only_a_run_in_progress_is_no_item() -> None:
    running = RunWithResult(_run(1, _at(THU)), None)
    in_progress = _switch([], [running], lock_free=False)
    assert in_progress.state.run_in_progress and not in_progress.state.engaged
    assert build([BookFacts(book_id="main", switch=in_progress)], None) == ()
    # The same run with the lock free is unfinished, not running: it engages.
    unfinished = _switch([], [running], lock_free=True)
    (item,) = build([BookFacts(book_id="main", switch=unfinished)], None)
    assert item.kind == "kill_switch_engaged" and item.since == _at(THU)
    assert item.detail == "kill switch engaged: run 1 never finished (the process stopped part way)"


def test_a_crashed_run_with_no_kill_switch_row_is_since_the_earliest_engaging_run() -> None:
    runs = [
        RunWithResult(_run(1, _at(THU)), _result(1, "ok", _at(THU, 15))),
        RunWithResult(_run(2, _at(FRI)), _result(2, "crashed", _at(FRI, 15))),
        RunWithResult(_run(3, _at(MON)), _result(3, "failed", _at(MON, 15))),
    ]
    facts = _switch([], runs)
    assert facts.state.engaged_row is None
    (item,) = build([BookFacts(book_id="main", switch=facts)], None)
    assert item.since == _at(FRI)
    assert item.detail == (
        "kill switch engaged: run 2 crashed with an error; run 3 failed with an error"
    )


def test_a_released_switch_is_no_item() -> None:
    halted = RunWithResult(_run(1, _at(THU)), _result(1, "halted", _at(THU, 15)))
    rows = [_switch_row(1, "engaged", _at(THU, 15)), _switch_row(2, "released", _at(THU, 16))]
    assert build([BookFacts(book_id="main", switch=_switch(rows, [halted]))], None) == ()


# --- order_lagging ------------------------------------------------------------------


def test_an_order_is_lagging_exactly_at_the_bound_and_not_one_session_before() -> None:
    rows = [_reconciliation(_at(THU, 13), [], 1), _reconciliation(_at(THU, 15), ["tp-a"], 2)]
    # Bound 2: the anchor is THU 15:00; FRI is one session past, MON two.
    assert build([BookFacts(book_id="main", lag=_lag(FRI, rows))], None) == ()
    (item,) = build([BookFacts(book_id="main", lag=_lag(MON, rows))], None)
    assert item == WaitingItem(
        book_id="main",
        kind="order_lagging",
        since=_at(THU, 15),
        detail=(
            "order tp-a (buy AAA) still lagging its fills 2 session(s) after it was first "
            "listed, at 2026-10-05 (bound 2): check the order in Alpaca; settle it only if "
            "Alpaca no longer knows it"
        ),
        command='tradepartner paper settle --book main --order tp-a --reason "<why>"',
    )


def test_an_order_never_listed_lagging_is_no_item() -> None:
    rows = [_reconciliation(_at(THU, 15), ["tp-other"], 1)]
    assert build([BookFacts(book_id="main", lag=_lag(TUE, rows))], None) == ()


# --- missed_run ---------------------------------------------------------------------


def test_missed_run_follows_stale() -> None:
    stale = RunFacts(stale=True, session=MON, window_started_at=STARTED)
    (item,) = build([BookFacts(book_id="b", run=stale)], None)
    assert item == WaitingItem(
        book_id="b",
        kind="missed_run",
        since=MON,
        detail=(
            "no run for session 2026-10-05: check the Mac was awake and the job's last "
            "exit code (docs/runbooks/scheduling.md)"
        ),
        command=None,
    )
    ran = RunFacts(stale=False, session=MON, window_started_at=STARTED)
    assert build([BookFacts(book_id="b", run=ran)], None) == ()


def test_missed_run_is_absent_for_a_window_opened_after_s_minus_1s_close() -> None:
    close = session_close(MON)
    at_close = RunFacts(stale=True, session=MON, window_started_at=close)
    assert _kinds(build([BookFacts(book_id="b", run=at_close)], None)) == [("b", "missed_run")]
    after = RunFacts(stale=True, session=MON, window_started_at=close + timedelta(seconds=1))
    assert build([BookFacts(book_id="b", run=after)], None) == ()


# --- shakedown_note -----------------------------------------------------------------


def _span(noted: frozenset[int] = frozenset()) -> SpanFacts:
    alerts = (
        SpanAlert(alert_id=1, book_id="main", kind="stale_data", session=FRI, at=_at(FRI)),
        SpanAlert(alert_id=2, book_id="b", kind="missed_run", session=MON, at=_at(MON)),
        SpanAlert(alert_id=3, book_id="main", kind="run_failed", session=MON, at=_at(MON)),
        # before the span's start_at, and after `now`: neither is read
        SpanAlert(alert_id=4, book_id="main", kind="stale_data", session=THU, at=_at(THU, 3)),
        SpanAlert(alert_id=5, book_id="main", kind="stale_data", session=TUE, at=_at(TUE, 20)),
    )
    return SpanFacts(start_at=_at(THU, 4), alerts=alerts, noted=noted, now=_at(TUE, 19))


def test_shakedown_note_items_are_the_unnoted_span_alerts() -> None:
    items = build([], _span())
    assert [(i.book_id, i.since) for i in items] == [("b", _at(MON)), ("main", _at(FRI))]
    assert items[1] == WaitingItem(
        book_id="main",
        kind="shakedown_note",
        since=_at(FRI),
        detail=(
            "stale_data alert 1 (session 2026-10-02) is inside the shakedown span and has "
            "no shakedown note"
        ),
        command='tradepartner decision shakedown-note --alert 1 --reason "<why>"',
    )


def test_a_noted_alert_is_no_item_and_no_span_is_no_item() -> None:
    assert [i.since for i in build([], _span(frozenset({2})))] == [_at(FRI)]
    assert build([], None) == ()


# --- report_due ---------------------------------------------------------------------


def _report(count: int | None, has_report: bool = False) -> ReportFacts:
    return ReportFacts(tracking_count=count, has_report=has_report, minimum=2, second_rebalance=THU)


def test_report_due_at_two_rebalances_and_gone_once_a_report_exists() -> None:
    assert build([BookFacts(book_id="main", report=_report(1))], None) == ()
    assert build([BookFacts(book_id="main", report=_report(None))], None) == ()
    (item,) = build([BookFacts(book_id="main", report=_report(2))], None)
    assert item == WaitingItem(
        book_id="main",
        kind="report_due",
        since=THU,
        detail=(
            "2 rebalances tracked and no tracking report yet; `paper report` opens a "
            "counted tracking trial"
        ),
        command="tradepartner paper report --book main",
    )
    assert build([BookFacts(book_id="main", report=_report(5, has_report=True))], None) == ()


# --- unreadable and the order -------------------------------------------------------


def test_an_unreadable_reconciliation_is_one_item_and_other_kinds_still_come_out() -> None:
    bad = ReconciliationRow(
        reconciliation_id=1,
        window_id=1,
        at=_at(THU),
        status="fills_lagging",
        mismatches_json="{not json",
        known_at=_at(THU),
        ingested_at=_at(THU),
    )
    stale = RunFacts(stale=True, session=MON, window_started_at=STARTED)
    books = [
        BookFacts(book_id="a", lag=_lag(MON, [bad]), run=stale, errors=("frozen: boom",)),
        BookFacts(book_id="b", run=stale),
    ]
    items = build(books, None)
    assert _kinds(items) == [("a", "missed_run"), ("b", "missed_run"), ("a", "unreadable")]
    assert items[-1] == WaitingItem(
        book_id="a",
        kind="unreadable",
        since=None,
        detail="frozen: boom; reconciliation 1 mismatches_json is unreadable",
        command=None,
    )


def test_two_books_items_come_out_by_kind_then_book_then_since() -> None:
    halted = RunWithResult(_run(1, _at(THU)), _result(1, "halted", _at(THU, 15)))
    switch = _switch([_switch_row(1, "engaged", _at(THU, 15))], [halted])
    rows = [_reconciliation(_at(THU, 15), ["tp-a", "tp-b"], 1)]
    lag = LagFacts(
        orders=(_order("tp-b"), _order("tp-a")),
        reconciliations=tuple(rows),
        session=MON,
        frozen=RiskConfig(max_fill_lag_sessions=1),
    )
    stale = RunFacts(stale=True, session=MON, window_started_at=STARTED)
    books = [
        BookFacts(book_id="main", switch=switch, run=stale, report=_report(3)),
        BookFacts(book_id="b", lag=lag, run=stale, errors=("unreadable mark",)),
    ]
    items = build(books, _span())
    assert _kinds(items) == [
        ("main", "kill_switch_engaged"),
        ("b", "order_lagging"),
        ("b", "order_lagging"),
        ("b", "missed_run"),
        ("main", "missed_run"),
        ("b", "shakedown_note"),
        ("main", "shakedown_note"),
        ("main", "report_due"),
        ("b", "unreadable"),
    ]
    # the two lagging orders share their anchor: the detail breaks the tie
    assert [i.command for i in items[1:3]] == [
        'tradepartner paper settle --book b --order tp-a --reason "<why>"',
        'tradepartner paper settle --book b --order tp-b --reason "<why>"',
    ]


def test_nothing_waiting_is_an_empty_tuple() -> None:
    ran = RunFacts(stale=False, session=MON, window_started_at=STARTED)
    assert build([BookFacts(book_id="main", run=ran, report=_report(0))], None) == ()
    assert build([], None) == ()


def test_the_module_does_no_io() -> None:
    text = Path(waiting.__file__).read_text()
    for banned in ("duckdb", "open(", "utc_now", "datetime.now", "subprocess", "requests"):
        assert banned not in text
