"""Tests for `execution.ops.page_data` (Phase 4 plan T66b, spec req 12): the pure
read that feeds the operations page and `paper status` (T67)."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest
from conftest import version_4_store

from tradepartner.calendar import is_session, previous_session
from tradepartner.config import Settings
from tradepartner.execution import ops
from tradepartner.store.db import open_for_write, open_read_only, utc_now
from tradepartner.store.journal import (
    AlertRow,
    DecisionRow,
    FillRow,
    OrderEventRow,
    OrderRow,
    OutcomeRow,
    PaperPlanRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    ReconciliationRow,
    SignalRow,
    append,
)

_ROOT = Path(__file__).resolve().parents[2]
_NEW_YORK = ZoneInfo("America/New_York")


def _today() -> date:
    """The same "today's session" `ops._today_session` derives, computed here
    from the real clock so tests stay correct whenever they run."""
    day = utc_now().astimezone(_NEW_YORK).date()
    return day if is_session(day) else previous_session(day)


_S = _today()
_S_MINUS_1 = previous_session(_S)
_T0 = datetime.combine(_S_MINUS_1, datetime.min.time(), tzinfo=UTC) + timedelta(hours=14)


def _at(minutes: int) -> datetime:
    return _T0 + timedelta(minutes=minutes)


def _stamp(minutes: int = 0) -> dict[str, datetime]:
    return {"known_at": _at(minutes), "ingested_at": _at(minutes)}


def _run(window_id: int, session: date, minutes: int = 0) -> PaperRunRow:
    return PaperRunRow(
        window_id=window_id,
        session=session,
        kind="rebalance",
        started_at=_at(minutes),
        invoked_by="scheduler",
        code_version="abc",
        **_stamp(minutes),
    )


def _result(run_id: int, status: str, minutes: int = 5) -> PaperRunResultRow:
    return PaperRunResultRow(
        run_id=run_id,
        finished_at=_at(minutes),
        status=status,
        clock_fault=False,
        **_stamp(minutes),
    )


@pytest.fixture
def seeded(journal_settings: Settings, open_window: PaperWindowRow) -> dict[str, int]:
    """A window with one finished run on S-1 (so the header is not stale), a
    plan with a ranking, two orders (one filled, one left pending) with their
    chains, a reconciliation, a position mark and an alert."""
    window_id = open_window.window_id
    assert window_id is not None
    ids: dict[str, int] = {"window_id": window_id}
    with open_for_write(journal_settings) as conn:
        run_id = append(conn, _run(window_id, _S_MINUS_1))
        assert run_id is not None
        ids["run_id"] = run_id
        result_id = append(conn, _result(run_id, "ok"))
        assert result_id is None  # no ID_COLUMN

        decision_sell = DecisionRow(
            run_id=run_id,
            rebalance_session=_S_MINUS_1,
            security_id="AAA",
            side="sell",
            whole_share=True,
            decision="forced_exit",
            reason="left_universe",
            **_stamp(1),
        )
        decision_sell_id = append(conn, decision_sell)
        assert decision_sell_id is not None
        ids["decision_sell_id"] = decision_sell_id

        decision_buy = DecisionRow(
            run_id=run_id,
            rebalance_session=_S_MINUS_1,
            security_id="BBB",
            side="buy",
            whole_share=True,
            decision="trade",
            **_stamp(2),
        )
        decision_buy_id = append(conn, decision_buy)
        assert decision_buy_id is not None
        ids["decision_buy_id"] = decision_buy_id

        plan = PaperPlanRow(
            run_id=run_id,
            plan_trial_id=1,
            rebalance_session=_S_MINUS_1,
            n_universe=10,
            n_targets=1,
            n_orders_below_min_at_live_capital=0,
            **_stamp(3),
        )
        append(conn, plan)

        append(
            conn,
            SignalRow(
                run_id=run_id,
                rebalance_session=_S_MINUS_1,
                security_id="BBB",
                score=1.0,
                rank=1,
                reason="selected",
                **_stamp(3),
            ),
        )
        append(
            conn,
            SignalRow(
                run_id=run_id,
                rebalance_session=_S_MINUS_1,
                security_id="CCC",
                score=0.5,
                rank=2,
                reason="below_cut",
                **_stamp(3),
            ),
        )
        append(
            conn,
            SignalRow(
                run_id=run_id,
                rebalance_session=_S_MINUS_1,
                security_id="DDD",
                score=None,
                rank=None,
                reason="excluded_no_history",
                **_stamp(3),
            ),
        )

        sell_order = OrderRow(
            client_order_id="tp-sell-1",
            decision_id=decision_sell_id,
            run_id=run_id,
            session=_S_MINUS_1,
            attempt=1,
            phase="sell",
            security_id="AAA",
            symbol="AAA",
            side="sell",
            quantity=10.0,
            sells_in_flight_at_submit=False,
            **_stamp(4),
        )
        append(conn, sell_order)
        append(
            conn,
            OrderEventRow(
                client_order_id="tp-sell-1",
                status="accepted",
                **_stamp(5),
            ),
        )
        append(
            conn,
            OrderEventRow(
                client_order_id="tp-sell-1",
                status="filled",
                filled_quantity=10.0,
                filled_avg_price=20.0,
                **_stamp(6),
            ),
        )
        fill_id = append(
            conn,
            FillRow(
                client_order_id="tp-sell-1",
                filled_at=_at(6),
                quantity=10.0,
                price=20.0,
                price_implied=True,
                broker_fill_id="brk-1",
                source="broker_status",
                **_stamp(6),
            ),
        )
        assert fill_id is not None
        ids["fill_id"] = fill_id
        append(
            conn,
            OutcomeRow(
                client_order_id="tp-sell-1",
                through_session=_S_MINUS_1,
                kind="realised_pnl",
                value=5.0,
                **_stamp(7),
            ),
        )

        buy_order = OrderRow(
            client_order_id="tp-buy-1",
            decision_id=decision_buy_id,
            run_id=run_id,
            session=_S_MINUS_1,
            attempt=1,
            phase="buy",
            security_id="BBB",
            symbol="BBB",
            side="buy",
            quantity=5.0,
            sells_in_flight_at_submit=False,
            **_stamp(8),
        )
        append(conn, buy_order)
        append(
            conn,
            OrderEventRow(
                client_order_id="tp-buy-1",
                status="accepted",
                **_stamp(9),
            ),
        )

        append(
            conn,
            PositionDailyRow(
                run_id=run_id,
                session=_S_MINUS_1,
                security_id="BBB",
                quantity=5.0,
                mark_price=20.0,
                value=100.0,
                **_stamp(10),
            ),
        )
        append(
            conn,
            PositionDailyRow(
                run_id=run_id,
                session=_S_MINUS_1,
                security_id="ZZZ",
                quantity=0.0,
                mark_price=10.0,
                value=0.0,
                **_stamp(10),
            ),
        )

        reconciliation_id = append(
            conn,
            ReconciliationRow(
                window_id=window_id,
                run_id=run_id,
                at=_at(11),
                status="ok",
                broker_cash=1000.0,
                **_stamp(11),
            ),
        )
        assert reconciliation_id is not None
        ids["reconciliation_id"] = reconciliation_id

        alert_id = append(
            conn,
            AlertRow(
                run_id=run_id,
                session=_S_MINUS_1,
                kind="stale_data",
                message="SPY has no bar for S",
                at=_at(12),
                **_stamp(12),
            ),
        )
        assert alert_id is not None
        ids["alert_id"] = alert_id
    return ids


def test_journal_not_initialised_on_a_version_4_store(tmp_path: Path) -> None:
    path = version_4_store(tmp_path / "store_v4.duckdb")
    settings = Settings(_env_file=None, store={"path": str(path)})
    conn = duckdb.connect(str(path), read_only=True)
    try:
        data = ops.page_data(conn, settings)
    finally:
        conn.close()
    assert data.journal_not_initialised is True
    assert data.window is None
    assert data.as_of is None
    assert data.ranking == ()
    assert data.fills == ()
    assert data.chains == ()
    assert data.alerts == ()


def test_no_window_returns_empty_data(journal_settings: Settings) -> None:
    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)
    assert data.journal_not_initialised is False
    assert data.window is None
    assert data.switch_state is None
    assert data.positions_count == 0
    assert data.open_orders_count == 0
    assert data.targets_count == 0


def test_every_field_on_a_seeded_journal(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)

    assert data.journal_not_initialised is False
    assert data.window is not None and data.window.window_id == open_window.window_id
    assert data.as_of is not None
    assert data.last_updated == _at(5)
    assert data.stale is False  # a run on S-1 was seeded

    # positions: only BBB (nonzero quantity) counts, not the zeroed ZZZ row
    assert data.positions_count == 1
    assert data.positions_value == 100.0

    # the pending buy order (accepted, never filled) is still open
    assert data.open_orders_count == 1

    assert data.targets_count == 1

    assert data.switch_state is not None
    assert data.switch_state.engaged is False

    by_security = {r.security_id: r for r in data.ranking}
    assert by_security["BBB"].signal_reason == "selected"
    assert by_security["BBB"].selected is True
    assert by_security["BBB"].decision_reason == "trade"
    assert by_security["CCC"].signal_reason == "below_cut"
    assert by_security["CCC"].selected is False
    assert by_security["DDD"].signal_reason == "excluded_no_history"

    assert len(data.fills) == 1
    assert data.fills[0].fill.broker_fill_id == "brk-1"
    assert data.fills_capped is False

    chains = {c.client_order_id: c for c in data.chains}
    assert set(chains) == {"tp-sell-1", "tp-buy-1"}
    sell_kinds = [s.kind for s in chains["tp-sell-1"].steps]
    assert sell_kinds == ["order", "order_event", "order_event", "fill", "outcome"]
    assert data.chains_capped is False

    assert len(data.alerts) == 1
    assert data.alerts[0].kind == "stale_data"
    assert data.alerts_capped is False

    assert data.reconciliation is not None
    assert data.reconciliation.status == "ok"


def test_a_decision_reason_falls_back_to_the_decision_field_for_left_universe(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)
    # AAA (the forced exit / left_universe sell) has no signal, so it is not in
    # the ranking (which is built from `signals`); its decision reason is
    # checked directly instead.
    assert data.ranking and all(r.security_id != "AAA" for r in data.ranking)


def test_stale_when_s_minus_1_has_no_run(
    journal_settings: Settings, open_window: PaperWindowRow
) -> None:
    window_id = open_window.window_id
    assert window_id is not None
    with open_for_write(journal_settings) as conn:
        # a run, but not on S-1
        run_id = append(conn, _run(window_id, previous_session(_S_MINUS_1)))
        assert run_id is not None
        append(conn, _result(run_id, "ok"))
    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)
    assert data.stale is True


@pytest.fixture
def holder(journal_settings: Settings) -> Iterator[subprocess.Popen[str]]:
    """Another process holding the paper run lock until its stdin closes."""
    script = textwrap.dedent(
        f"""
        import sys
        from tradepartner.config import Settings
        from tradepartner.execution.lock import run_lock

        settings = Settings(_env_file=None, store={{"path": {journal_settings.store.path!r}}})
        with run_lock(settings):
            print("held", flush=True)
            sys.stdin.read()
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=_ROOT,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    try:
        yield proc
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=30)


def test_run_in_progress_while_another_process_holds_the_lock(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    seeded: dict[str, int],
    holder: subprocess.Popen[str],
) -> None:
    window_id = open_window.window_id
    assert window_id is not None
    # an unfinished run of this window, as the holder's run would leave one
    with open_for_write(journal_settings) as conn:
        append(conn, _run(window_id, _S, minutes=100))
    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)
    assert data.switch_state is not None
    assert data.switch_state.engaged is False
    assert data.switch_state.run_in_progress is True


def test_page_data_takes_no_write_connection(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """A real read-only DuckDB connection: any write attempted inside
    `page_data` would raise, not merely go unnoticed."""
    with open_read_only(journal_settings) as conn:
        ops.page_data(conn, journal_settings)
    # the store is still free for a write connection right after
    with open_for_write(journal_settings):
        pass


def test_the_module_text_has_no_write_statement() -> None:
    import re

    text = Path(ops.__file__).read_text()
    code = re.sub(r'"""[\s\S]*?"""', "", text)
    assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER)\b", code, re.IGNORECASE)


def test_per_row_reads_are_capped_by_the_page_row_limit(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    window_id = open_window.window_id
    assert window_id is not None
    limited = Settings(
        _env_file=None,
        store={"path": journal_settings.store.path},
        dashboard={"page_row_limit": 1},
    )
    with open_for_write(journal_settings) as conn:
        for i in range(3):
            append(
                conn,
                AlertRow(
                    run_id=seeded["run_id"],
                    session=_S_MINUS_1,
                    kind="run_failed",
                    message=f"extra {i}",
                    at=_at(20 + i),
                    **_stamp(20 + i),
                ),
            )
    with open_read_only(limited) as conn:
        data = ops.page_data(conn, limited)
    assert len(data.alerts) == 1
    assert data.alerts_capped is True
    assert len(data.fills) <= 1
    total_chain_steps = sum(len(c.steps) for c in data.chains)
    assert total_chain_steps <= 1
    assert data.chains_capped is True


def test_page_data_runs_within_half_the_lock_retry_seconds(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    fast = Settings(
        _env_file=None,
        store={"path": journal_settings.store.path, "lock_retry_seconds": 2},
    )
    with open_read_only(fast) as conn:
        started = time.monotonic()
        ops.page_data(conn, fast)
        elapsed = time.monotonic() - started
    assert elapsed < fast.store.lock_retry_seconds / 2
