"""`action_retract.retract_action` and the ingest keep rule (#2092, plan T262;
data-foundation spec amendment 2026-10-20 (#2092)).

One stored 2:1 split of `SEC_X` under Alpaca id `ca-split-1`, ex-date
2026-10-02, stamped at the first-seen proxy close(2026-10-01) and ingested that
evening. The issuer never split: the raw closes are 100 on both sessions. Every
number below is computed by hand from those rows.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.action_retract import retract_action
from tradepartner.adapters.prices import ActionType, CorporateAction
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.ingest import ACTION_RETRACT_MODE, _add_actions
from tradepartner.repair import RepairRefused
from tradepartner.store.asof import adjusted_prices_as_of, live_actions_as_of
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

SID, AID = "SEC_X", "ca-split-1"
PRIOR, EX = date(2026, 10, 1), date(2026, 10, 2)
#: The first-seen proxy for ex-date EX: close(PRIOR), 2026-10-01 20:00 UTC.
KNOWN = session_close(PRIOR)
INGESTED = KNOWN + timedelta(hours=2, minutes=30)  # the 18:30 ET ingest
#: The owner's retraction: 10:00 ET on the ex-date, after the 08:05 halt.
AT = datetime(2026, 10, 2, 14, 0, tzinfo=UTC)
#: The next evening's ingest, after the retraction.
NEXT_INGEST = session_close(EX) + timedelta(hours=2, minutes=30)
WINDOW = (date(2026, 10, 1), EX)
TICK = timedelta(microseconds=1)


def _split_row(**override: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "security_id": SID,
        "action_type": "split",
        "ex_date": EX,
        "ratio_or_amount": 2.0,
        "announced_at": None,
        "source_action_id": AID,
        "cancelled": False,
        "known_at": KNOWN,
        "ingested_at": INGESTED,
        "source": "alpaca",
        "provenance": "action",
    }
    return {**row, **override}


def _bar(session: date) -> dict[str, Any]:
    return {
        "security_id": SID,
        "session": session,
        "open": 100.0,
        "high": 100.0,
        "low": 100.0,
        "close": 100.0,
        "volume": 1_000,
        "known_at": session_close(session),
        "ingested_at": session_close(session) + timedelta(hours=2),
        "source": "alpaca_sip",
        "provenance": "bar",
    }


@pytest.fixture
def store(settings: Settings) -> Settings:
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(conn, "corporate_actions", _split_row())
        for session in (PRIOR, EX):
            insert_row(conn, "prices_daily", _bar(session))
    return settings


def _actions(settings: Settings) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(
            "SELECT action_type, ex_date, ratio_or_amount, cancelled, known_at, ingested_at "
            "FROM corporate_actions ORDER BY known_at"
        ).fetchall()


def _runs(settings: Settings) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(
            "SELECT source, mode, status, started_at, rows_added, message FROM ingestion_runs "
            "ORDER BY started_at"
        ).fetchall()


def _retract(settings: Settings, **kwargs: Any) -> Any:
    args: dict[str, Any] = {
        "security_id": SID,
        "source_action_id": AID,
        "reason": "Alpaca withdrew the split (#2092)",
        "clock": lambda: AT,
        "expect_known_at": KNOWN,
    }
    return retract_action(settings, **{**args, **kwargs})


def _read(settings: Settings, t: datetime) -> tuple[pl.DataFrame, pl.DataFrame]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        prices = adjusted_prices_as_of(conn, t, [SID], settings=settings)
        return live_actions_as_of(conn, t), prices


# --- the command --------------------------------------------------------------


def test_a_dry_run_names_the_latest_revision_and_writes_nothing(store: Settings) -> None:
    result = _retract(store, dry_run=True, expect_known_at=None)

    assert result.dry_run
    assert (result.action_type, result.ex_date, result.ratio_or_amount) == ("split", EX, 2.0)
    assert result.latest_known_at == KNOWN
    assert "cancelled False" in result.summary()
    assert result.summary().startswith(f"would withdraw {SID} split {AID} ex-date 2026-10-02")
    assert len(_actions(store)) == 1
    assert _runs(store) == []


def test_a_retraction_appends_one_cancel_at_the_clock_and_its_run_row(store: Settings) -> None:
    _retract(store)

    assert _actions(store) == [
        ("split", EX, 2.0, False, KNOWN, INGESTED),
        ("split", EX, 2.0, True, AT, AT),  # the values carried, stamped at the clock
    ]
    ((source, mode, status, started, added, message),) = _runs(store)
    assert (source, mode, status, started, added) == (
        "alpaca",
        ACTION_RETRACT_MODE,
        "repaired",
        AT,
        1,
    )
    assert f"withdrew {SID} split {AID}" in message
    assert "Alpaca withdrew the split (#2092)" in message


def test_no_look_ahead_the_split_stands_before_the_cancel_and_is_gone_from_it(
    store: Settings,
) -> None:
    """Before the cancel every read is the one taken before the command ran: the
    2:1 split, effective on 2026-10-02, halves the 2026-10-01 close (100 -> 50).
    From the cancel's `known_at` on the split is gone and the close is the raw 100."""
    before_actions, before_prices = _read(store, AT - TICK)
    assert before_actions["source_action_id"].to_list() == [AID]
    assert before_prices["close"].to_list() == [50.0]

    _retract(store)

    actions, prices = _read(store, AT - TICK)
    assert actions.equals(before_actions)
    assert prices.equals(before_prices)
    at_cancel, prices_at_cancel = _read(store, AT)
    assert at_cancel.height == 0
    assert prices_at_cancel["close"].to_list() == [100.0]
    after_close, prices_after_close = _read(store, session_close(EX))
    assert after_close.height == 0
    assert prices_after_close["close"].to_list() == [100.0, 100.0]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"source_action_id": "ca-other"}, "no stored corporate action"),
        ({"expect_known_at": KNOWN - TICK}, "not the dry run's"),
        ({"clock": lambda: KNOWN}, "back-dated"),
        ({"reinstate": True}, "is live"),
    ],
)
def test_a_refused_run_writes_nothing(
    store: Settings, kwargs: dict[str, Any], message: str
) -> None:
    with pytest.raises(RepairRefused, match=message):
        _retract(store, **kwargs)
    assert len(_actions(store)) == 1
    assert _runs(store) == []


def test_a_second_withdrawal_is_refused(store: Settings) -> None:
    _retract(store)
    with pytest.raises(RepairRefused, match="already withdrawn"):
        _retract(store, clock=lambda: AT + timedelta(hours=1), expect_known_at=AT)
    assert len(_actions(store)) == 2


def test_a_cancel_ingest_wrote_is_not_reinstated(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(conn, "corporate_actions", _split_row())
        insert_row(
            conn,
            "corporate_actions",
            _split_row(cancelled=True, known_at=INGESTED, ingested_at=INGESTED),
        )
    with pytest.raises(RepairRefused, match="not written by action-retract"):
        _retract(settings, reinstate=True, expect_known_at=INGESTED)
    assert len(_actions(settings)) == 2


def test_reinstate_makes_the_split_live_again_from_its_own_clock(store: Settings) -> None:
    _retract(store)
    later = AT + timedelta(hours=1)

    _retract(store, reinstate=True, clock=lambda: later, expect_known_at=AT)

    assert _actions(store)[-1] == ("split", EX, 2.0, False, later, later)
    assert _read(store, later - TICK)[0].height == 0
    assert _read(store, later)[0]["source_action_id"].to_list() == [AID]


def test_the_identity_and_the_reason_are_required(store: Settings) -> None:
    with pytest.raises(ValueError, match="reason"):
        _retract(store, reason=" ")
    with pytest.raises(ValueError, match="action id"):
        _retract(store, source_action_id="")
    with pytest.raises(ValueError, match="expect_known_at"):
        _retract(store, expect_known_at=None)


# --- ingest keeps the withdrawal ---------------------------------------------


def _served(ratio: float = 2.0) -> CorporateAction:
    """What Alpaca's answer still holds for the id: the first-seen record."""
    return CorporateAction(
        security_id=SID,
        action_type=ActionType.SPLIT,
        ex_date=EX,
        ratio_or_amount=ratio,
        known_at=KNOWN,
        source="alpaca",
        source_action_id=AID,
    )


def _ingest(
    settings: Settings, action: CorporateAction, at: datetime
) -> tuple[int, dict[str, int]]:
    stats: dict[str, int] = {}
    with open_for_write(settings) as conn:
        added = _add_actions(conn, [action], WINDOW, ingested_at=at, stats=stats)
    return added, stats


def test_ingest_never_revives_a_withdrawal_the_source_still_serves(store: Settings) -> None:
    _retract(store)

    added, stats = _ingest(store, _served(), NEXT_INGEST)

    assert added == 0
    assert stats == {"actions_retracted_kept": 1}
    assert len(_actions(store)) == 2
    assert _read(store, NEXT_INGEST)[0].height == 0


def test_ingest_writes_a_re_announcement_with_new_values(store: Settings) -> None:
    _retract(store)

    added, stats = _ingest(store, _served(ratio=3.0), NEXT_INGEST)

    assert (added, stats) == (1, {})
    assert _actions(store)[-1] == ("split", EX, 3.0, False, NEXT_INGEST, NEXT_INGEST)


def test_ingest_after_a_reinstatement_is_a_no_op(store: Settings) -> None:
    _retract(store)
    later = AT + timedelta(hours=1)
    _retract(store, reinstate=True, clock=lambda: later, expect_known_at=AT)

    assert _ingest(store, _served(), NEXT_INGEST) == (0, {})


def test_ingest_still_revives_its_own_cancel(settings: Settings) -> None:
    """Unchanged behaviour: a cancel no `action_retract` run wrote is revised by
    the source's live record, as req 5's revision rule says."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(conn, "corporate_actions", _split_row())
        insert_row(
            conn,
            "corporate_actions",
            _split_row(cancelled=True, known_at=INGESTED, ingested_at=INGESTED),
        )

    added, stats = _ingest(settings, _served(), NEXT_INGEST)

    assert (added, stats) == (1, {})
    assert _actions(settings)[-1] == ("split", EX, 2.0, False, NEXT_INGEST, NEXT_INGEST)


# --- the CLI --------------------------------------------------------------------


def test_the_command_dry_run_then_withdraws(store: Settings) -> None:
    app = cli.make_app(settings=lambda: store, clock=lambda: AT)
    runner = CliRunner()
    base = ["action-retract", "--security", SID, "--action-id", AID]
    reason = ["--reason", "Alpaca withdrew it"]

    dry = runner.invoke(app, [*base, *reason, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert dry.output.startswith(f"would withdraw {SID} split {AID}")
    assert f"--expect-known-at {KNOWN.isoformat()}" in dry.output

    both = runner.invoke(app, [*base, *reason, "--dry-run", "--expect-known-at", KNOWN.isoformat()])
    assert both.exit_code == cli.USAGE_ERROR
    unguarded = runner.invoke(app, [*base, *reason])
    assert unguarded.exit_code == cli.USAGE_ERROR
    assert "--expect-known-at" in unguarded.output
    no_reason = runner.invoke(app, [*base, "--expect-known-at", KNOWN.isoformat()])
    assert no_reason.exit_code == cli.USAGE_ERROR
    stale = runner.invoke(app, [*base, *reason, "--expect-known-at", AT.isoformat()])
    assert stale.exit_code == 1
    assert "refused" in stale.output
    assert len(_actions(store)) == 1

    done = runner.invoke(app, [*base, *reason, "--expect-known-at", KNOWN.isoformat()])
    assert done.exit_code == 0, done.output
    assert done.output.startswith(f"withdrew {SID} split {AID}")
    assert _actions(store)[-1][3] is True


def test_the_command_refuses_a_missing_store(settings: Settings) -> None:
    app = cli.make_app(settings=lambda: settings, clock=lambda: AT)
    result = CliRunner().invoke(
        app,
        ["action-retract", "--security", SID, "--action-id", AID, "--reason", "x", "--dry-run"],
    )
    assert result.exit_code == 1
    assert "no store" in result.output
