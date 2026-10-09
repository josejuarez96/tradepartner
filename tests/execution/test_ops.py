"""Tests for `execution.ops.page_data` (Phase 4 plan T66b, spec req 12): the pure
read that feeds the operations page and `paper status` (T67)."""

from __future__ import annotations

import re
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from dataclasses import fields as dataclass_fields
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import pytest
from conftest import version_4_store

from tradepartner.calendar import next_session, previous_session
from tradepartner.config import Settings
from tradepartner.execution import ops
from tradepartner.store import schema
from tradepartner.store.db import open_for_write, open_read_only, utc_now
from tradepartner.store.journal import (
    AlertRow,
    DecisionRow,
    FillRow,
    KillSwitchRow,
    OrderEventRow,
    OrderRow,
    OutcomeRow,
    PaperPlanRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PaperWindowStopRow,
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


def test_reconciliation_fields_match_the_row_type_and_include_book_id() -> None:
    """Version 17 (ADR 0015 seam 1, plan T132) adds `book_id` to
    `reconciliations`; `_RECONCILIATION_FIELDS` is the select list
    `_latest_reconciliation` zips into `ReconciliationRow`, so it must name
    every row-type field in order."""
    names = tuple(f.name for f in dataclass_fields(ReconciliationRow))
    assert names == ops._RECONCILIATION_FIELDS
    fields_list = ops._RECONCILIATION_FIELDS
    assert fields_list.index("book_id") + 1 == fields_list.index("known_at")


def test_journal_outdated_on_a_read_only_version_16_store(tmp_path: Path) -> None:
    """#1261: `require_journal` raises `SchemaVersionError` on a version-16 store
    (the eight expanded tables lack `book_id`); `page_data` catches it into
    `journal_outdated`, whose message names the fix, not a false "no journal"."""
    path = tmp_path / "store_v16.duckdb"
    conn = duckdb.connect(str(path))
    try:
        schema.init_schema(conn)
        conn.execute("UPDATE schema_version SET version = 16")
        conn.execute("ALTER TABLE paper_windows DROP COLUMN book_id")
    finally:
        conn.close()
    settings = Settings(_env_file=None, store={"path": str(path)})
    with duckdb.connect(str(path), read_only=True) as conn:
        data = ops.page_data(conn, settings)
    assert data.journal_not_initialised is False
    assert data.journal_outdated is not None
    assert "open it for writing once" in data.journal_outdated
    assert data.window is None


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


# --- bounded reads, the remaining whole-window ones (#613) -----------------------


def _rows(settings: Settings, pattern: str) -> list[tuple[str, int]]:
    """`page_data`'s row counts for every query whose SQL matches `pattern`."""
    with open_read_only(settings) as conn:
        counting = _CountingConnection(conn)
        ops.page_data(counting, settings)  # type: ignore[arg-type]
    return [(sql, rows) for sql, rows in counting.reads if re.search(pattern, sql)]


def test_marks_read_only_the_latest_marked_session(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """A window with marks on several sessions: the positions KPI matches a
    full read of the latest session's rows, and the `positions_daily` query
    never hands back more than that session's own rows, however many older
    sessions the window has."""
    window_id = open_window.window_id
    assert window_id is not None
    sessions = [previous_session(_S_MINUS_1), _S_MINUS_1, _S]
    with open_for_write(journal_settings) as conn:
        for i, session in enumerate(sessions):
            for security_id, quantity, value in (("BBB", 5.0, 100.0 + i), ("CCC", 2.0, 40.0 + i)):
                append(
                    conn,
                    PositionDailyRow(
                        run_id=seeded["run_id"],
                        session=session,
                        security_id=security_id,
                        quantity=quantity,
                        mark_price=20.0,
                        value=value,
                        **_stamp(60 + i),
                    ),
                )
    with open_read_only(journal_settings) as conn:
        full_marks = [
            m
            for m in ops.journal.positions_daily_for(conn, window_id)
            if m.session == _S and m.security_id is not None
        ]
    expected_count = sum(1 for m in full_marks if m.quantity != 0)
    expected_value = sum(m.value or 0.0 for m in full_marks if m.quantity != 0)

    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)
    assert data.positions_count == expected_count
    assert data.positions_value == expected_value

    reads = _rows(journal_settings, r"\bFROM\s+positions_daily\b")
    assert reads
    assert max(rows for _, rows in reads) == len(full_marks)


def test_marks_filter_does_not_depend_on_the_trading_calendar(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """The latest marked session is the first session after the seeded
    marks' `_S_MINUS_1` that follows a non-session date (a weekend or a
    holiday); a stray mark row sits on the calendar day before it, between
    `previous_session(last_session)` and `last_session` itself. The old
    filter, `after=previous_session(last_session)`, reads every row with
    `session > previous_session(last_session)`, which catches the stray row
    too, since the calendar has no session that day to keep the two apart
    (#652); the fix must still read only the last session's row, whatever
    mark the calendar never scheduled sits in between. Dates derive from
    `_S_MINUS_1` so the test does not depend on the day it runs."""
    window_id = open_window.window_id
    assert window_id is not None
    last_session = next_session(_S_MINUS_1)
    while last_session - previous_session(last_session) <= timedelta(days=1):
        last_session = next_session(last_session)
    stray_session = last_session - timedelta(days=1)  # not a session
    assert previous_session(last_session) < stray_session < last_session

    with open_for_write(journal_settings) as conn:
        append(
            conn,
            PositionDailyRow(
                run_id=seeded["run_id"],
                session=stray_session,
                security_id="QQQ",
                quantity=99.0,
                mark_price=1.0,
                value=999.0,
                **_stamp(90),
            ),
        )
        append(
            conn,
            PositionDailyRow(
                run_id=seeded["run_id"],
                session=last_session,
                security_id="BBB",
                quantity=3.0,
                mark_price=10.0,
                value=30.0,
                **_stamp(91),
            ),
        )

    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)

    assert data.positions_count == 1
    assert data.positions_value == 30.0


def test_kill_switch_read_is_at_most_two_rows_and_matches_derive(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """Many `kill_switch` rows, but `_kill_switch_rows_for` reads at most two
    (the last by `event_id` and the greatest-`at` `released` row), and
    `switch.derive` over that pair alone equals `derive` over the whole
    window's rows."""
    window_id = open_window.window_id
    assert window_id is not None
    with open_for_write(journal_settings) as conn:
        for i in range(10):
            append(
                conn,
                KillSwitchRow(
                    window_id=window_id,
                    at=_at(100 + i),
                    state="engaged" if i % 2 == 0 else "released",
                    source="owner",
                    **_stamp(100 + i),
                ),
            )
    with open_read_only(journal_settings) as conn:
        window = ops.journal.latest_window(conn)
        assert window is not None
        full_rows = ops.journal.kill_switch_events_for(conn, window_id)
        runs_with_results = ops.journal.runs_for(conn, window_id)
        runs = [rw.run for rw in runs_with_results]
        results = [rw.result for rw in runs_with_results if rw.result is not None]
        expected = ops.derive(window, full_rows, runs, results, reading_run=None, lock_free=True)

        bounded_rows = ops._kill_switch_rows_for(conn, window_id)
        assert len(bounded_rows) <= 2
        actual = ops.derive(window, bounded_rows, runs, results, reading_run=None, lock_free=True)
    assert actual == expected


@pytest.mark.parametrize("lock_free", [True, False])
def test_bounded_run_reads_match_derive_across_fault_scenarios(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    seeded: dict[str, int],
    lock_free: bool,
) -> None:
    """One fixture covering every scenario `switch.derive` branches on: a
    faulted run a release clears (its own stamps are both before the
    release), a faulted run the SAME release does not clear (stamped after
    it), an old unfinished run, the window's latest run left unfinished (the
    in-progress rule differs by `lock_free`), and an `engaged` row with no
    run_id (the "latest kill-switch row" cause). `_kill_switch_rows_for` and
    `_runs_for_switch`'s bounded reads must derive the identical
    `SwitchState` a full, unbounded read of every row gives."""
    window_id = open_window.window_id
    assert window_id is not None
    with open_for_write(journal_settings) as conn:
        cleared_run_id = append(conn, _run(window_id, _S, minutes=200))
        append(conn, _result(cleared_run_id, "crashed", minutes=205))

        uncleared_run_id = append(conn, _run(window_id, _S, minutes=300))
        append(conn, _result(uncleared_run_id, "failed", minutes=305))

        # The one release in the fixture: after both of the first run's
        # stamps (clears it), but before the second run's finished_at (does
        # not clear it) -- the exact release-between-stamps case.
        append(
            conn,
            KillSwitchRow(
                window_id=window_id,
                at=_at(302),
                state="released",
                source="owner",
                **_stamp(302),
            ),
        )

        old_unfinished_id = append(conn, _run(window_id, _S, minutes=400))
        assert old_unfinished_id is not None
        latest_unfinished_id = append(conn, _run(window_id, _S, minutes=500))
        assert latest_unfinished_id is not None

        # The window's last kill_switch row by event_id: an owner engagement
        # tied to no run, so it is only ever found through "the last row".
        append(
            conn,
            KillSwitchRow(
                window_id=window_id,
                at=_at(310),
                state="engaged",
                source="owner",
                **_stamp(310),
            ),
        )

    with open_read_only(journal_settings) as conn:
        window = ops.journal.latest_window(conn)
        assert window is not None
        full_rows = ops.journal.kill_switch_events_for(conn, window_id)
        full_runs_with_results = ops.journal.runs_for(conn, window_id)
        full_runs = [rw.run for rw in full_runs_with_results]
        full_results = [rw.result for rw in full_runs_with_results if rw.result is not None]
        expected = ops.derive(
            window, full_rows, full_runs, full_results, reading_run=None, lock_free=lock_free
        )

        bounded_rows = ops._kill_switch_rows_for(conn, window_id)
        assert len(bounded_rows) <= 2
        released_at = max((r.at for r in bounded_rows if r.state == "released"), default=None)
        bounded_runs_with_results = ops._runs_for_switch(conn, window_id, released_at=released_at)
        bounded_runs = [rw.run for rw in bounded_runs_with_results]
        bounded_results = [rw.result for rw in bounded_runs_with_results if rw.result is not None]
        actual = ops.derive(
            window,
            bounded_rows,
            bounded_runs,
            bounded_results,
            reading_run=None,
            lock_free=lock_free,
        )

    assert actual == expected
    assert expected.engaged is True
    assert any(f"run {uncleared_run_id} failed" in c for c in expected.causes)
    assert not any(f"run {cleared_run_id} " in c for c in expected.causes)  # cleared
    assert any(f"run {old_unfinished_id} unfinished" in c for c in expected.causes)
    if lock_free:
        assert expected.run_in_progress is False
        assert any(f"run {latest_unfinished_id} unfinished" in c for c in expected.causes)
    else:
        assert expected.run_in_progress is True
        assert not any(f"run {latest_unfinished_id} " in c for c in expected.causes)


def test_reconciliation_tie_on_at_picks_the_same_row_as_max(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """Two reconciliations sharing the same (latest) `at`: `_latest_reconciliation`
    must pick the one Python's `max(reconciliations, key=lambda r: r.at)`
    would, which keeps the *first* maximal element it scans in
    `reconciliations_for`'s order (known_at, ingested_at, rowid) -- the
    earlier-known one, not an arbitrary tied row."""
    window_id = open_window.window_id
    assert window_id is not None
    tied_at = _at(500)
    with open_for_write(journal_settings) as conn:
        earlier_id = append(
            conn,
            ReconciliationRow(
                window_id=window_id,
                run_id=seeded["run_id"],
                at=tied_at,
                status="ok",
                broker_cash=1234.0,
                **_stamp(50),
            ),
        )
        later_id = append(
            conn,
            ReconciliationRow(
                window_id=window_id,
                run_id=seeded["run_id"],
                at=tied_at,
                status="mismatch",
                broker_cash=9999.0,
                **_stamp(51),
            ),
        )
    with open_read_only(journal_settings) as conn:
        full = ops.journal.reconciliations_for(conn, window_id)
        expected = max(full, key=lambda r: r.at)
        actual = ops._latest_reconciliation(conn, window_id)
    assert actual is not None
    assert actual.reconciliation_id == expected.reconciliation_id == earlier_id
    assert actual.reconciliation_id != later_id

    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)
    assert data.reconciliation is not None
    assert data.reconciliation.reconciliation_id == earlier_id


def test_open_orders_count_matches_non_terminal_orders_with_many_terminal_orders(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """Many terminal (filled) orders plus a few open ones: the bounded
    `COUNT(*)` matches `len(journal.non_terminal_orders(...))` and reads one
    row, whatever the window's order count."""
    window_id = open_window.window_id
    assert window_id is not None
    _bulk_orders(journal_settings, seeded, 25)  # all terminal (filled)
    with open_read_only(journal_settings) as conn:
        expected = len(ops.journal.non_terminal_orders(conn, window_id=window_id))
        actual = ops._open_orders_count(conn, window_id)
    assert actual == expected

    reads = _rows(journal_settings, r"SELECT\s+COUNT\(\*\)\s+FROM\s+orders\b")
    assert reads
    assert all(rows == 1 for _, rows in reads)

    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)
    assert data.open_orders_count == expected


def test_as_of_covers_rows_the_bounded_reads_no_longer_fetch(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """A late `known_at` on an old-session mark, an old (superseded)
    kill-switch row and an old reconciliation -- none of them read by the
    bounded reads above -- must still be picked up by `_bounded_known_at`'s
    aggregate, so "as of" never predates a row the page no longer reads row
    by row."""
    window_id = open_window.window_id
    assert window_id is not None
    late = _at(999)
    with open_for_write(journal_settings) as conn:
        # an old-session mark, journaled late (known_at far after its session)
        append(
            conn,
            PositionDailyRow(
                run_id=seeded["run_id"],
                session=previous_session(_S_MINUS_1),
                security_id="BBB",
                quantity=1.0,
                mark_price=1.0,
                value=1.0,
                known_at=late,
                ingested_at=late,
            ),
        )
        # the latest mark, on a later session, known well before `late`
        append(
            conn,
            PositionDailyRow(
                run_id=seeded["run_id"],
                session=_S,
                security_id="BBB",
                quantity=1.0,
                mark_price=1.0,
                value=1.0,
                **_stamp(13),
            ),
        )
        # an old kill_switch row, superseded by nothing newer by event_id,
        # but not the last row and not the max release either
        append(
            conn,
            KillSwitchRow(
                window_id=window_id,
                at=_at(14),
                state="engaged",
                source="owner",
                known_at=late,
                ingested_at=late,
            ),
        )
        append(
            conn,
            KillSwitchRow(
                window_id=window_id,
                at=_at(15),
                state="released",
                source="owner",
                **_stamp(15),
            ),
        )
        # an old reconciliation, not the one `_latest_reconciliation` returns
        append(
            conn,
            ReconciliationRow(
                window_id=window_id,
                run_id=seeded["run_id"],
                at=_at(16),
                status="ok",
                known_at=late,
                ingested_at=late,
            ),
        )
    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings)
    assert data.as_of == late


def test_a_long_window_reads_at_most_the_bound_of_every_remaining_section(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """An oversized fixture across every section this task bounds -- many
    sessions of marks, many kill-switch cycles, many finished runs, many
    reconciliations and many terminal orders -- so a window that has run for
    a long time never makes any of those queries hand back more than its
    bound, and `page_data`'s output still matches a full, unbounded read."""
    window_id = open_window.window_id
    assert window_id is not None
    # Build 20 extra sessions of marks, 20 kill-switch engage/release cycles,
    # 20 extra finished (non-faulted) runs, and 20 reconciliations: none of
    # them a cause `switch.derive` can draw on, so the bounded reads must
    # leave every one of them out.
    with open_for_write(journal_settings) as conn:
        session = _S_MINUS_1
        for i in range(20):
            session = next_session(session)
            append(
                conn,
                PositionDailyRow(
                    run_id=seeded["run_id"],
                    session=session,
                    security_id="BBB",
                    quantity=1.0,
                    mark_price=1.0,
                    value=float(i),
                    **_stamp(600 + i),
                ),
            )
            append(
                conn,
                KillSwitchRow(
                    window_id=window_id,
                    at=_at(600 + i),
                    state="engaged" if i % 2 == 0 else "released",
                    source="owner",
                    **_stamp(600 + i),
                ),
            )
            extra_run_id = append(conn, _run(window_id, _S_MINUS_1, minutes=700 + i))
            append(conn, _result(extra_run_id, "ok", minutes=701 + i))
            append(
                conn,
                ReconciliationRow(
                    window_id=window_id,
                    run_id=seeded["run_id"],
                    at=_at(600 + i),
                    status="ok",
                    **_stamp(600 + i),
                ),
            )
        last_session = session
    _bulk_orders(journal_settings, seeded, 20, start=800)  # all terminal (filled)

    with open_read_only(journal_settings) as conn:
        window = ops.journal.latest_window(conn)
        assert window is not None

        # the reference: a full, unbounded read of every table
        full_marks = [
            m
            for m in ops.journal.positions_daily_for(conn, window_id)
            if m.session == last_session and m.security_id is not None
        ]
        expected_positions_count = sum(1 for m in full_marks if m.quantity != 0)
        expected_positions_value = sum(m.value or 0.0 for m in full_marks if m.quantity != 0)
        full_kill_switch_rows = ops.journal.kill_switch_events_for(conn, window_id)
        full_runs_with_results = ops.journal.runs_for(conn, window_id)
        full_runs = [rw.run for rw in full_runs_with_results]
        full_results = [rw.result for rw in full_runs_with_results if rw.result is not None]
        expected_switch_state = ops.derive(
            window, full_kill_switch_rows, full_runs, full_results, reading_run=None, lock_free=True
        )
        expected_reconciliation = max(
            ops.journal.reconciliations_for(conn, window_id), key=lambda r: r.at
        )
        expected_open_orders_count = len(ops.journal.non_terminal_orders(conn, window_id=window_id))

        settings = journal_settings
        counting = _CountingConnection(conn)
        data = ops.page_data(counting, settings)  # type: ignore[arg-type]

    assert data.positions_count == expected_positions_count
    assert data.positions_value == expected_positions_value
    assert data.switch_state == expected_switch_state
    assert data.reconciliation is not None
    assert data.reconciliation.reconciliation_id == expected_reconciliation.reconciliation_id
    assert data.open_orders_count == expected_open_orders_count

    marks_reads = [
        rows for sql, rows in counting.reads if re.search(r"\bFROM\s+positions_daily\b", sql)
    ]
    assert marks_reads and max(marks_reads) == len(full_marks)

    kill_switch_reads = [
        rows for sql, rows in counting.reads if re.search(r"\bFROM\s+kill_switch\b", sql)
    ]
    # the "as of" aggregate also reads from kill_switch (one row); the
    # bounded-rows query is the other two (at most one row each).
    assert kill_switch_reads
    assert max(kill_switch_reads) <= 2

    reconciliation_reads = [
        rows for sql, rows in counting.reads if re.search(r"\bFROM\s+reconciliations\b", sql)
    ]
    assert reconciliation_reads
    assert max(reconciliation_reads) == 1

    orders_count_reads = [
        rows
        for sql, rows in counting.reads
        if re.search(r"SELECT\s+COUNT\(\*\)\s+FROM\s+orders\b", sql)
    ]
    assert orders_count_reads == [1]

    # `_runs_for_switch` is the one read this task leaves uncapped at a fixed
    # row count (module docstring, item 3), but it must still read far fewer
    # than the window's 21 runs: none of the 20 extra runs is unfinished,
    # faulted or uncleared, so only the window's latest run (the
    # in-progress-rule row) should come back.
    with open_read_only(journal_settings) as conn:
        released_at = max(
            (r.at for r in ops._kill_switch_rows_for(conn, window_id) if r.state == "released"),
            default=None,
        )
        bounded_runs = ops._runs_for_switch(conn, window_id, released_at=released_at)
    assert len(bounded_runs) < len(full_runs_with_results)
    assert len(bounded_runs) <= 2


def _derive_bounded_and_full(
    conn: duckdb.DuckDBPyConnection, window: PaperWindowRow, *, lock_free: bool = True
) -> tuple[ops.SwitchState, ops.SwitchState]:
    """`switch.derive` over `_kill_switch_rows_for`/`_runs_for_switch`'s bounded
    reads, and over a full, unbounded read of every row of the window --
    paired, so a test can assert they are the same `SwitchState` and inspect
    either one."""
    window_id = window.window_id
    assert window_id is not None
    full_rows = ops.journal.kill_switch_events_for(conn, window_id)
    full_runs_with_results = ops.journal.runs_for(conn, window_id)
    full_runs = [rw.run for rw in full_runs_with_results]
    full_results = [rw.result for rw in full_runs_with_results if rw.result is not None]
    expected = ops.derive(
        window, full_rows, full_runs, full_results, reading_run=None, lock_free=lock_free
    )

    bounded_rows = ops._kill_switch_rows_for(conn, window_id)
    released_at = max((r.at for r in bounded_rows if r.state == "released"), default=None)
    bounded_runs_with_results = ops._runs_for_switch(conn, window_id, released_at=released_at)
    bounded_runs = [rw.run for rw in bounded_runs_with_results]
    bounded_results = [rw.result for rw in bounded_runs_with_results if rw.result is not None]
    actual = ops.derive(
        window, bounded_rows, bounded_runs, bounded_results, reading_run=None, lock_free=lock_free
    )
    return actual, expected


@pytest.mark.parametrize(
    ("started_minutes", "finished_minutes", "release_minutes"),
    [
        (200, 205, 205),  # a release stamped exactly at finished_at
        # a release stamped exactly at started_at, with finished_at BEFORE
        # started_at (a clock-skewed run, nothing in the schema rules it
        # out) so the finished_at clause alone could not already keep the
        # run: only the started_at clause does, isolating it from the first
        # case (safety-reviewer's verification pass on #651 found the
        # original (200, 205, 200) case vacuous, since finished_at=205 >
        # release=200 kept the run through the finished_at clause alone).
        (205, 200, 205),
    ],
)
def test_release_exactly_at_a_runs_stamp_does_not_clear_it(
    journal_settings: Settings,
    open_window: PaperWindowRow,
    seeded: dict[str, int],
    started_minutes: int,
    finished_minutes: int,
    release_minutes: int,
) -> None:
    """`switch._faulted_uncleared` clears a run only on a *strict* `at >
    started_at and at > finished_at`; a release stamped at exactly one of
    those two instants must not clear it. `_runs_for_switch`'s SQL expresses
    "not cleared" as `released_at <= started_at OR released_at <= finished_at`
    -- the equality case is the one a `<=` -> `<` slip would silently drop
    from the bounded read while `derive` still calls the run engaged
    (reviewed in #651: this is the fail-open direction, so it needs its own
    boundary test rather than relying on the release-strictly-between-stamps
    case already covered elsewhere in this file)."""
    window_id = open_window.window_id
    assert window_id is not None
    with open_for_write(journal_settings) as conn:
        run_id = append(conn, _run(window_id, _S, minutes=started_minutes))
        append(conn, _result(run_id, "crashed", minutes=finished_minutes))
        append(
            conn,
            KillSwitchRow(
                window_id=window_id,
                at=_at(release_minutes),
                state="released",
                source="owner",
                **_stamp(release_minutes + 1),
            ),
        )
    with open_read_only(journal_settings) as conn:
        window = ops.journal.latest_window(conn)
        assert window is not None
        actual, expected = _derive_bounded_and_full(conn, window)
    assert actual == expected
    assert expected.engaged is True
    assert any(f"run {run_id} crashed" in c for c in expected.causes)


def test_the_greatest_release_at_may_not_be_the_latest_by_event_id(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """Two `released` rows written out of `at` order (the halt path's
    `utc_now()` stamp can run behind an earlier write's clock reading): the
    first-written row carries the *later* `at` and clears a faulted run the
    second-written (later `event_id`, earlier `at`) row would not. Neither
    row is `engaged`, so if this case comes out `engaged=False`, that is only
    because the switch correctly picked the greatest `at` to clear the run --
    not because some row's state happened to be `engaged` (every other
    scenario in this file ends on one)."""
    window_id = open_window.window_id
    assert window_id is not None
    with open_for_write(journal_settings) as conn:
        run_id = append(conn, _run(window_id, _S, minutes=500))
        append(conn, _result(run_id, "failed", minutes=550))
        # written first, carries the later `at` -- the one that clears the run
        append(
            conn,
            KillSwitchRow(
                window_id=window_id,
                at=_at(600),
                state="released",
                source="owner",
                **_stamp(600),
            ),
        )
        # written second (higher event_id), carries the earlier `at` -- does
        # not clear the run on its own, and is the "last row by event_id"
        append(
            conn,
            KillSwitchRow(
                window_id=window_id,
                at=_at(510),
                state="released",
                source="owner",
                **_stamp(601),
            ),
        )
    with open_read_only(journal_settings) as conn:
        window = ops.journal.latest_window(conn)
        assert window is not None
        actual, expected = _derive_bounded_and_full(conn, window)
    assert actual == expected
    assert expected.engaged is False  # the greatest `at` (600) clears the run
    assert expected.causes == ()


def test_switch_engages_from_an_unfinished_run_alone(
    journal_settings: Settings, open_window: PaperWindowRow, seeded: dict[str, int]
) -> None:
    """No `kill_switch` row exists at all for this window: `engaged` must
    still be True from the unfinished run alone (every other scenario in
    this file has at least one kill-switch row; this is the one where
    `_kill_switch_rows_for` legitimately returns zero rows)."""
    window_id = open_window.window_id
    assert window_id is not None
    with open_for_write(journal_settings) as conn:
        run_id = append(conn, _run(window_id, _S, minutes=900))  # no result: unfinished
    with open_read_only(journal_settings) as conn:
        bounded_rows = ops._kill_switch_rows_for(conn, window_id)
        assert bounded_rows == ()
        data = ops.page_data(conn, journal_settings)
    assert data.switch_state is not None
    assert data.switch_state.engaged is True
    assert data.switch_state.causes == (f"run {run_id} unfinished",)


# --- per book (ADR 0017 B.7; plan T156) -------------------------------------------


def _locked_alert(book_id: str, minutes: int) -> AlertRow:
    return AlertRow(
        session=_S_MINUS_1,
        kind="locked",
        message=f"{book_id}'s run lock was held",
        at=_at(minutes),
        book_id=book_id,
        **_stamp(minutes),
    )


@pytest.fixture
def two_books(journal_settings: Settings, open_window: PaperWindowRow) -> dict[str, int]:
    """`main`'s open window (the shared fixture's, first rebalance before S-1) with
    one `ok` run and a mark, and a newer window for book `b` with one unfinished
    run, two marked positions and its own `locked` alert; `main` has a `locked`
    alert of its own too."""
    main_id = open_window.window_id
    assert main_id is not None
    with open_for_write(journal_settings) as conn:
        main_run = append(conn, _run(main_id, _S_MINUS_1))
        assert main_run is not None
        append(conn, _result(main_run, "ok"))
        append(
            conn,
            PositionDailyRow(
                run_id=main_run,
                session=_S_MINUS_1,
                security_id="AAA",
                quantity=1.0,
                mark_price=10.0,
                value=10.0,
                **_stamp(6),
            ),
        )
        b_id = append(
            conn,
            replace(open_window, window_id=None, account_id="PB1", book_id="b"),
        )
        assert b_id is not None
        b_run = append(conn, _run(b_id, _S_MINUS_1, minutes=10))
        assert b_run is not None
        for security_id in ("BBB", "CCC"):
            append(
                conn,
                PositionDailyRow(
                    run_id=b_run,
                    session=_S_MINUS_1,
                    security_id=security_id,
                    quantity=2.0,
                    mark_price=5.0,
                    value=10.0,
                    **_stamp(11),
                ),
            )
        append(conn, _locked_alert("main", 20))
        append(conn, _locked_alert("b", 21))
    return {"main": main_id, "b": b_id, "main_run": main_run, "b_run": b_run}


def test_page_data_reads_only_the_named_books_window(
    journal_settings: Settings, two_books: dict[str, int]
) -> None:
    """With no book `page_data` reads `paper.book_id`'s (`main`'s) window, though
    `b`'s is newer; with `b` it reads `b`'s; each sees its own `locked` alert only."""
    with open_read_only(journal_settings) as conn:
        default = ops.page_data(conn, journal_settings)
        main = ops.page_data(conn, journal_settings, "main")
        other = ops.page_data(conn, journal_settings, "b")
    assert default == main
    assert main.book_id == "main" and other.book_id == "b"
    assert main.window is not None and main.window.window_id == two_books["main"]
    assert other.window is not None and other.window.window_id == two_books["b"]
    assert [a.message for a in main.alerts] == ["main's run lock was held"]
    assert [a.message for a in other.alerts] == ["b's run lock was held"]
    assert (main.positions_count, other.positions_count) == (1, 2)


def test_page_data_for_a_book_with_no_window_is_the_no_window_state(
    journal_settings: Settings, two_books: dict[str, int]
) -> None:
    with open_read_only(journal_settings) as conn:
        data = ops.page_data(conn, journal_settings, "c")
        with pytest.raises(ValueError, match="book_id must match"):
            ops.page_data(conn, journal_settings, "b-1")
    assert data == ops.OpsData(book_id="c")


def test_book_summaries_lists_one_row_per_book_in_token_order(
    journal_settings: Settings, two_books: dict[str, int]
) -> None:
    with open_read_only(journal_settings) as conn:
        rows = ops.book_summaries(conn, journal_settings)
    assert [(r.book_id, r.window.window_id) for r in rows] == [
        ("b", two_books["b"]),
        ("main", two_books["main"]),
    ]
    b, main = rows
    assert (b.positions_count, main.positions_count) == (2, 1)
    assert (b.last_run_status, main.last_run_status) == ("unfinished", "ok")
    assert b.is_open and main.is_open
    assert main.open_orders_count == 0
    assert not main.switch_state.engaged


def test_book_summaries_closed_window_has_no_next_rebalance(
    journal_settings: Settings, two_books: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A closed book's row says so and names no next rebalance; an open one names
    the first rebalance session at its window's cadence whose close is after now."""
    monkeypatch.setattr(ops, "window_cadence", lambda _conn, _window: "month_end")
    monkeypatch.setattr(ops, "utc_now", lambda: datetime(2026, 10, 9, 18, tzinfo=UTC))
    with open_for_write(journal_settings) as conn:
        append(
            conn,
            PaperWindowStopRow(window_id=two_books["b"], at=_at(30), state="closed", **_stamp(30)),
        )
    with open_read_only(journal_settings) as conn:
        b, main = ops.book_summaries(conn, journal_settings)
    assert not b.is_open and b.next_rebalance_session is None
    assert main.is_open and main.next_rebalance_session == date(2026, 10, 30)


def test_book_summaries_is_empty_before_the_first_window(journal_settings: Settings) -> None:
    with open_read_only(journal_settings) as conn:
        assert ops.book_summaries(conn, journal_settings) == ()
