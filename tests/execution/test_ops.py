"""Tests for `execution.ops.page_data` (Phase 4 plan T66b, spec req 12): the pure
read that feeds the operations page and `paper status` (T67)."""

from __future__ import annotations

import re
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

from tradepartner.calendar import next_session, previous_session
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

# `ops._required_run_session` itself, computed from the real clock at import
# time so every test stays correct whenever it runs, on a session day or not
# (the behavior finding 3 of the quant-auditor review on PR #433 covers).
_S_MINUS_1 = ops._required_run_session(utc_now())
_S = next_session(_S_MINUS_1)
_T0 = datetime.combine(_S_MINUS_1, datetime.min.time(), tzinfo=UTC) + timedelta(hours=14)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # a Tuesday session: S-1 is the session strictly before it (Monday),
        # because Tuesday's own run may not have happened yet.
        (datetime(2026, 9, 29, 18, 0, tzinfo=UTC), date(2026, 9, 28)),
        # a Saturday: there is no "today's run" to wait for, so S-1 is the
        # latest session at or before it (Friday) -- not the one before that.
        (datetime(2026, 10, 3, 18, 0, tzinfo=UTC), date(2026, 10, 2)),
        # Thanksgiving (a holiday, not a session): same rule as the weekend.
        (datetime(2026, 11, 26, 18, 0, tzinfo=UTC), date(2026, 11, 25)),
    ],
)
def test_required_run_session_does_not_double_step_on_a_non_session_day(
    now: datetime, expected: date
) -> None:
    assert ops._required_run_session(now) == expected


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

        # AAA: a held name that left the universe, big enough to trade
        # (plan.py's real shape: decision="trade", reason="left_universe").
        decision_sell = DecisionRow(
            run_id=run_id,
            rebalance_session=_S_MINUS_1,
            security_id="AAA",
            side="sell",
            whole_share=True,
            decision="trade",
            reason="left_universe",
            **_stamp(1),
        )
        decision_sell_id = append(conn, decision_sell)
        assert decision_sell_id is not None
        ids["decision_sell_id"] = decision_sell_id

        # BBB: an ordinary buy, no override and no exit reason.
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

        # EEE: an `exclude_name` override (plan.py: decision="override",
        # reason=override.kind) -- req 12 names "override" as a ranking
        # reason, never produced by a signal row.
        append(
            conn,
            DecisionRow(
                run_id=run_id,
                rebalance_session=_S_MINUS_1,
                security_id="EEE",
                whole_share=True,
                decision="override",
                reason="exclude_name",
                **_stamp(1),
            ),
        )

        # FFF: a left_universe exit too small to trade (plan.py: decision=
        # "dust", reason="left_universe") -- must read differently from AAA's
        # traded exit, even though both carry reason="left_universe".
        append(
            conn,
            DecisionRow(
                run_id=run_id,
                rebalance_session=_S_MINUS_1,
                security_id="FFF",
                whole_share=True,
                decision="dust",
                reason="left_universe",
                **_stamp(1),
            ),
        )

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
    # the latest known_at among every row page_data reads, the alert being the
    # latest of this fixture's own rows (window.known_at predates the window).
    assert data.as_of == max(open_window.known_at, _at(12))
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
    assert set(by_security) == {"AAA", "BBB", "CCC", "DDD", "EEE", "FFF"}

    assert by_security["BBB"].signal_reason == "selected"
    assert by_security["BBB"].selected is True
    assert by_security["BBB"].decision == "trade"
    assert by_security["BBB"].decision_reason is None

    assert by_security["CCC"].signal_reason == "below_cut"
    assert by_security["CCC"].selected is False

    assert by_security["DDD"].signal_reason == "excluded_no_history"

    # AAA, EEE and FFF have decisions but no signal row (the plan's universe
    # pass never scored them), so they must still show up in the ranking --
    # finding 1 of the quant-auditor review on PR #433 -- with their decision
    # kind and reason kept separate (finding 2).
    assert by_security["AAA"].signal_reason is None
    assert by_security["AAA"].rank is None
    assert by_security["AAA"].decision == "trade"
    assert by_security["AAA"].decision_reason == "left_universe"

    assert by_security["EEE"].decision == "override"
    assert by_security["EEE"].decision_reason == "exclude_name"

    assert by_security["FFF"].decision == "dust"
    assert by_security["FFF"].decision_reason == "left_universe"

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


def test_locked_alerts_with_no_run_still_show_in_the_window(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """`locked` is emitted before any run row exists (`execution.alerts`), so it
    carries no `run_id` and would be missed by a `run_id IN (window's runs)`
    filter alone; it belongs to this window once its session is on or after
    the window's first rebalance (finding 4 of the quant-auditor review on
    PR #433)."""
    with open_for_write(journal_settings) as conn:
        alert_id = append(
            conn,
            AlertRow(
                run_id=None,
                session=_S,
                kind="locked",
                message="the paper run lock was held",
                at=_at(50),
                **_stamp(50),
            ),
        )
        assert alert_id is not None
    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)
    kinds = {a.kind for a in data.alerts}
    assert "locked" in kinds
    assert len(data.alerts) == 2  # the seeded stale_data alert plus this one


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


def test_per_row_reads_are_capped_and_keep_the_newest_rows(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """An oversized fixture: more alerts, fills and orders than the (lowered)
    `dashboard.page_row_limit`. Each capped section truncates to the limit and
    says so, and keeps the newest rows, not an arbitrary subset (findings 5
    and 6 of the quant-auditor review on PR #433)."""
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
        # Two more orders, each with its own fill, both newer (by known_at)
        # than the two orders `seeded` already wrote.
        extra_fill_ids = []
        for i in range(2):
            minutes = 30 + i
            append(
                conn,
                OrderRow(
                    client_order_id=f"tp-extra-{i}",
                    decision_id=seeded["decision_buy_id"],
                    run_id=seeded["run_id"],
                    session=_S_MINUS_1,
                    attempt=1,
                    phase="buy",
                    security_id="BBB",
                    symbol="BBB",
                    side="buy",
                    quantity=1.0,
                    sells_in_flight_at_submit=False,
                    **_stamp(minutes),
                ),
            )
            fill_id = append(
                conn,
                FillRow(
                    client_order_id=f"tp-extra-{i}",
                    filled_at=_at(minutes),
                    quantity=1.0,
                    price=20.0,
                    price_implied=True,
                    broker_fill_id=f"brk-extra-{i}",
                    source="broker_status",
                    **_stamp(minutes),
                ),
            )
            assert fill_id is not None
            extra_fill_ids.append(fill_id)

    with open_read_only(limited) as conn:
        data = ops.page_data(conn, limited)

    assert len(data.alerts) == 1
    assert data.alerts_capped is True

    assert len(data.fills) == 1
    assert data.fills_capped is True
    assert data.fills[0].fill.fill_id == max(extra_fill_ids)  # the newest fill

    assert sum(len(c.steps) for c in data.chains) == 1
    assert data.chains_capped is True
    # the newest order (tp-extra-1, known_at _at(31)), not tp-sell-1 or
    # tp-buy-1 (both older) and not tp-extra-0 (older than tp-extra-1).
    (chain,) = data.chains
    assert chain.client_order_id == "tp-extra-1"


def test_page_data_runs_within_half_the_lock_retry_seconds(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """A fixture much larger than `seeded` alone (many orders, each with an
    event, a fill and an outcome, and many alerts), so this check could fail
    if a read were not bounded."""
    window_id = open_window.window_id
    assert window_id is not None
    with open_for_write(journal_settings) as conn:
        for i in range(200):
            minutes = 100 + i
            client_order_id = f"tp-bulk-{i}"
            append(
                conn,
                OrderRow(
                    client_order_id=client_order_id,
                    decision_id=seeded["decision_buy_id"],
                    run_id=seeded["run_id"],
                    session=_S_MINUS_1,
                    attempt=1,
                    phase="buy",
                    security_id="BBB",
                    symbol="BBB",
                    side="buy",
                    quantity=1.0,
                    sells_in_flight_at_submit=False,
                    **_stamp(minutes),
                ),
            )
            append(
                conn,
                OrderEventRow(
                    client_order_id=client_order_id,
                    status="accepted",
                    **_stamp(minutes),
                ),
            )
            append(
                conn,
                FillRow(
                    client_order_id=client_order_id,
                    filled_at=_at(minutes),
                    quantity=1.0,
                    price=20.0,
                    price_implied=True,
                    broker_fill_id=f"brk-bulk-{i}",
                    source="broker_status",
                    **_stamp(minutes),
                ),
            )
            append(
                conn,
                OutcomeRow(
                    client_order_id=client_order_id,
                    through_session=_S_MINUS_1,
                    kind="realised_pnl",
                    value=1.0,
                    **_stamp(minutes),
                ),
            )
            append(
                conn,
                AlertRow(
                    run_id=seeded["run_id"],
                    session=_S_MINUS_1,
                    kind="run_failed",
                    message=f"bulk {i}",
                    at=_at(minutes),
                    **_stamp(minutes),
                ),
            )
    fast = Settings(
        _env_file=None,
        store={"path": journal_settings.store.path, "lock_retry_seconds": 2},
    )
    with open_read_only(fast) as conn:
        started = time.monotonic()
        data = ops.page_data(conn, fast)
        elapsed = time.monotonic() - started
    assert len(data.alerts) == 201  # the seeded one plus the 200 bulk ones
    assert data.alerts_capped is False  # well under the default page_row_limit (500)
    assert elapsed < fast.store.lock_retry_seconds / 2


# --- bounded reads (#435) ---------------------------------------------------------


class _Result:
    """A DuckDB result that records how many rows each fetch hands back."""

    def __init__(self, result: duckdb.DuckDBPyConnection, sql: str, reads: list[tuple[str, int]]):
        self._result, self._sql, self._reads = result, sql, reads

    def fetchall(self) -> list[tuple[object, ...]]:
        rows = self._result.fetchall()
        self._reads.append((self._sql, len(rows)))
        return rows

    def fetchone(self) -> tuple[object, ...] | None:
        row = self._result.fetchone()
        self._reads.append((self._sql, 0 if row is None else 1))
        return row


class _CountingConnection:
    """`page_data`'s connection with every fetched row counted per query."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self._conn = conn
        self.reads: list[tuple[str, int]] = []

    def execute(self, sql: str, params: object = None) -> _Result:
        result = self._conn.execute(sql) if params is None else self._conn.execute(sql, params)
        return _Result(result, sql, self.reads)

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


def _bulk_orders(
    journal_settings: Settings,
    seeded: dict[str, int],
    count: int,
    start: int = 30,
    prefix: str = "bulk",
) -> None:
    """`count` terminal orders newer than `seeded`'s, each with two events, a
    fill and an outcome, all stamped at the order's own minute."""
    with open_for_write(journal_settings) as conn:
        for i in range(count):
            minutes = start + i
            coid = f"tp-{prefix}-{i:03d}"
            append(
                conn,
                OrderRow(
                    client_order_id=coid,
                    decision_id=seeded["decision_buy_id"],
                    run_id=seeded["run_id"],
                    session=_S_MINUS_1,
                    attempt=1,
                    phase="buy",
                    security_id="BBB",
                    symbol="BBB",
                    side="buy",
                    quantity=1.0,
                    sells_in_flight_at_submit=False,
                    **_stamp(minutes),
                ),
            )
            for status in ("accepted", "filled"):
                append(conn, OrderEventRow(client_order_id=coid, status=status, **_stamp(minutes)))
            append(
                conn,
                FillRow(
                    client_order_id=coid,
                    filled_at=_at(minutes),
                    quantity=1.0,
                    price=20.0,
                    price_implied=True,
                    broker_fill_id=f"brk-{prefix}-{i}",
                    source="broker_status",
                    **_stamp(minutes),
                ),
            )
            append(
                conn,
                OutcomeRow(
                    client_order_id=coid,
                    through_session=_S_MINUS_1,
                    kind="realised_pnl",
                    value=1.0,
                    **_stamp(minutes),
                ),
            )


def _limited(journal_settings: Settings, limit: int) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": journal_settings.store.path},
        dashboard={"page_row_limit": limit},
    )


def _chain_reads(settings: Settings) -> tuple[ops.OpsData, list[tuple[str, int]]]:
    """`page_data` on a counting connection, and the rows each query over the
    order-chain tables handed back."""
    with open_read_only(settings) as conn:
        counting = _CountingConnection(conn)
        data = ops.page_data(counting, settings)  # type: ignore[arg-type]
    reads = [
        (sql, rows)
        for sql, rows in counting.reads
        if re.search(r"\bFROM\s+(orders|order_events|outcomes|fills)\b", sql)
    ]
    return data, reads


def test_the_chain_and_fills_reads_are_bounded_in_sql(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """A window with many more orders, events, fills and outcomes than the
    limit, counted on the connection itself, not on the page's output: no
    query over those tables hands back more than the kept orders' rows (each
    bulk order has two events, so `2 * limit`), and doubling the window
    changes no query's count."""
    _bulk_orders(journal_settings, seeded, 40)
    limited = _limited(journal_settings, 3)
    data, reads = _chain_reads(limited)

    assert reads
    assert max(rows for _, rows in reads) <= 2 * 3, reads
    assert data.chains_capped is True
    assert data.fills_capped is True
    assert [c.client_order_id for c in data.chains] == ["tp-bulk-039"]
    assert [f.fill.broker_fill_id for f in data.fills] == [f"brk-bulk-{i}" for i in (39, 38, 37)]

    _bulk_orders(journal_settings, seeded, 40, start=200, prefix="more")  # newer still
    doubled, doubled_reads = _chain_reads(limited)
    assert [rows for _, rows in doubled_reads] == [rows for _, rows in reads]
    assert doubled.chains_capped is True


def test_the_chain_views_fills_come_only_from_the_kept_orders(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    seeded: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every `fills_for` call is bounded: by `limit` (the fills table) or by the
    orders the chain view can keep (never the whole window)."""
    _bulk_orders(journal_settings, seeded, 10)
    limited = _limited(journal_settings, 4)
    calls: list[dict[str, object]] = []
    real = ops.journal.fills_for

    def spy(conn: duckdb.DuckDBPyConnection, **kwargs: object) -> object:
        calls.append(kwargs)
        return real(conn, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(ops.journal, "fills_for", spy)
    with open_read_only(limited) as conn:
        data = ops.page_data(conn, limited)

    assert all(c.get("limit") is not None or c.get("client_order_ids") is not None for c in calls)
    (chain_call,) = [c for c in calls if c.get("client_order_ids") is not None]
    assert sorted(chain_call["client_order_ids"]) == [  # type: ignore[call-overload]
        f"tp-bulk-{i:03d}" for i in range(6, 10)
    ]
    kept = {c.client_order_id for c in data.chains}
    assert kept <= set(chain_call["client_order_ids"])  # type: ignore[call-overload]
    fill_steps = [s for c in data.chains for s in c.steps if s.kind == "fill"]
    assert fill_steps and all("brk-bulk-" in s.detail for s in fill_steps)


def test_as_of_covers_a_late_event_on_an_order_past_the_cap(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """The oldest order drops out of a capped chain view, but its late event
    is still the newest row of the window, so "as of" still shows it."""
    _bulk_orders(journal_settings, seeded, 3)
    with open_for_write(journal_settings) as conn:
        append(conn, OrderEventRow(client_order_id="tp-sell-1", status="cancel_noop", **_stamp(90)))
    limited = _limited(journal_settings, 1)
    with open_read_only(limited) as conn:
        data = ops.page_data(conn, limited)
    assert [c.client_order_id for c in data.chains] == ["tp-bulk-002"]
    assert data.as_of == _at(90)


def test_under_the_limit_the_bounded_reads_show_every_row(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """Under the cap, the chain view, the fills table and "as of" are what an
    unbounded read of the whole window gives."""
    _bulk_orders(journal_settings, seeded, 5)
    window_id = open_window.window_id
    assert window_id is not None
    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)
        orders = ops.journal.orders_for(conn, window_id=window_id)
        events = ops.journal.order_events_for(conn, window_id=window_id)
        fills = ops.journal.fills_for(conn, window_id=window_id)
        outcomes = ops.journal.outcomes_for(conn, window_id)
    expected_chains, capped = ops._build_chains(orders, events, fills, outcomes, limit=500)
    assert capped is False and data.chains_capped is False
    assert data.chains == expected_chains
    assert [f.fill.fill_id for f in data.fills] == sorted(
        (f.fill.fill_id for f in fills), reverse=True
    )
    assert data.as_of == max(
        [o.known_at for o in orders]
        + [e.known_at for e in events]
        + [f.fill.known_at for f in fills]
        + [o.known_at for o in outcomes]
        + [_at(12)]  # the seeded alert
    )
