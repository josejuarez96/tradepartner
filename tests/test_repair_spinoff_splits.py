"""`repair.retract_spinoff_splits` and `repair-spinoff-splits` (#3058).

PAR's Alpaca split row `s1` on EX (ratio 0.25) is a spin-off reviewed as one:
the repair deletes every revision of that identity and nothing else. OTHER's
split `g` is a real split and stays. PAR's flat 10.0 closes around EX show what
the fake split did to the parent's history: x4 before EX.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tradepartner import cli, repair
from tradepartner.adapters.alpaca_prices import AlpacaPriceSource, ListingResolver
from tradepartner.calendar import previous_session, session_close
from tradepartner.config import Settings
from tradepartner.ingest import _add_actions
from tradepartner.repair import REPAIR, REPAIRED, RepairRefused, retract_spinoff_splits
from tradepartner.store import registry
from tradepartner.store.asof import adjusted_prices_as_of
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema
from tradepartner.store.spinoff_splits import SpinoffSplit

RUN = datetime(2026, 11, 6, 23, tzinfo=UTC)
PAR, OTHER = "0000000101", "0000000102"
EX = date(2020, 4, 1)
BEFORE = (date(2020, 3, 27), date(2020, 3, 30), date(2020, 3, 31))
REL = "spinoff-splits-test"


def _split(
    sid: str,
    action_id: str,
    *,
    ratio: float = 0.25,
    ex_date: date = EX,
    known: datetime | None = None,
    action_type: str = "split",
    source: str = "alpaca",
) -> dict[str, Any]:
    stamp = known or session_close(previous_session(ex_date))
    return {
        "security_id": sid,
        "action_type": action_type,
        "ex_date": ex_date,
        "ratio_or_amount": ratio,
        "announced_at": None,
        "source_action_id": action_id,
        "cancelled": False,
        "known_at": stamp,
        "ingested_at": max(stamp, RUN - timedelta(days=30)),
        "source": source,
        "provenance": "action",
    }


def _bar(sid: str, session: date, close: float = 10.0) -> dict[str, Any]:
    return {
        "security_id": sid,
        "session": session,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000,
        "known_at": session_close(session),
        "ingested_at": RUN - timedelta(days=30),
        "source": "alpaca_sip",
        "provenance": "bar",
    }


def _entry(
    sid: str = PAR, action_id: str = "s1", *, ratio: float = 0.25, ex_date: date = EX
) -> SpinoffSplit:
    return SpinoffSplit(sid, action_id, ex_date, ratio, "PAR", "Child Co", "test")


LIST = (_entry(),)


@pytest.fixture
def splits(settings: Settings) -> Settings:
    with open_for_write(settings) as conn:
        init_schema(conn)
        for row in (_split(PAR, "s1"), _split(OTHER, "g", ratio=2.0)):
            insert_row(conn, "corporate_actions", row)
        for day in (*BEFORE, EX):
            insert_row(conn, "prices_daily", _bar(PAR, day))
        registry.open_data_release(conn, conn, name=REL, backup_path="b.duckdb", reason="#3058")
    return settings


def _retract(settings: Settings, **kwargs: Any) -> repair.SpinoffSplitsRetracted:
    args: dict[str, Any] = {"reviewed": LIST}
    return retract_spinoff_splits(settings, clock=lambda: RUN, **(args | kwargs))


def _rows(settings: Settings, sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(sql).fetchall()


def _actions(settings: Settings) -> list[tuple[Any, ...]]:
    return _rows(
        settings,
        "SELECT security_id, action_type, source_action_id FROM corporate_actions "
        "ORDER BY security_id, source_action_id, known_at",
    )


ALL = [(PAR, "split", "s1"), (OTHER, "split", "g")]


def test_the_dry_run_counts_and_changes_nothing(splits: Settings) -> None:
    result = _retract(splits, dry_run=True)
    assert (result.entries, result.identities, result.action_rows) == (1, 1, 1)
    assert (result.already_repaired, result.stale, result.ex_dates) == (0, (), (EX,))
    assert result.summary() == (
        f"would delete 1 spin-off split rows of 1 identities on 1 ex-dates {EX}..{EX}; "
        "entries 1, already repaired 0, stale 0"
    )
    assert _actions(splits) == ALL
    assert _rows(splits, "SELECT count(*) FROM ingestion_runs") == [(0,)]


def test_the_repair_deletes_every_revision_of_the_listed_identity_only(
    splits: Settings,
) -> None:
    with open_for_write(splits) as conn:  # a later revision of the same event
        insert_row(conn, "corporate_actions", _split(PAR, "s1", known=RUN - timedelta(days=2)))
    assert _retract(splits, dry_run=True).action_rows == 2
    result = _retract(splits, expect=2, release=REL)
    assert _actions(splits) == [(OTHER, "split", "g")]
    [(source, status, mode, message)] = _rows(
        splits, "SELECT source, status, mode, message FROM ingestion_runs"
    )
    assert (source, status, mode) == ("alpaca", REPAIRED, REPAIR)
    assert message == f"{result.summary()}; release {REL}"
    assert message.startswith("deleted 2 spin-off split rows of 1 identities")
    again = _retract(splits, dry_run=True)
    assert (again.identities, again.action_rows, again.already_repaired) == (0, 0, 1)


def test_after_the_repair_no_read_at_any_t_applies_the_fake_split(splits: Settings) -> None:
    """The delete is back-dated on purpose: the split never happened, so the
    parent's history is its raw closes at every `t`, before and after the
    row's `known_at`."""

    def before_ex(t: datetime) -> list[float]:
        with duckdb.connect(splits.store.path, read_only=True) as conn:
            frame = adjusted_prices_as_of(conn, t, [PAR], include_dividends=True)
        return frame.filter(pl.col("session") < EX)["close"].to_list()

    stamps = [session_close(day) for day in (*BEFORE, EX)] + [RUN]
    assert before_ex(RUN) == [40.0, 40.0, 40.0]  # the 0.25 "split" multiplies by 4
    _retract(splits, expect=1, release=REL)
    for t in stamps:
        assert before_ex(t) == [10.0] * len([d for d in BEFORE if session_close(d) <= t]), t


def test_the_repair_refuses_and_deletes_nothing(splits: Settings) -> None:
    with pytest.raises(ValueError, match="dry run"):
        _retract(splits, release=REL)
    with pytest.raises(ValueError, match="named data release"):
        _retract(splits, expect=1)
    with pytest.raises(RepairRefused, match="not the expected 2"):
        _retract(splits, expect=2, release=REL)
    with pytest.raises(RepairRefused, match="named 'other'"):
        _retract(splits, expect=1, release="other")
    with open_for_write(splits) as conn:
        registry.close_data_release(conn, name=REL, sessions_from=date(2020, 3, 27), sessions_to=EX)
    with pytest.raises(RepairRefused, match="no release is open"):
        _retract(splits, expect=1, release=REL)
    assert _actions(splits) == ALL
    assert _rows(splits, "SELECT count(*) FROM ingestion_runs") == [(0,)]


@pytest.mark.parametrize(
    ("row", "why"),
    [
        (_split(PAR, "s1", ratio=0.5, known=RUN - timedelta(days=2)), "not the reviewed"),
        (
            _split(PAR, "s1", ex_date=date(2020, 4, 2), known=RUN - timedelta(days=2)),
            "not the reviewed",
        ),
        (_split(PAR, "s1", action_type="dividend", ex_date=date(2020, 5, 1)), "alpaca dividend"),
    ],
    ids=["ratio", "ex_date", "type"],
)
def test_a_stale_entry_refuses_a_real_run(splits: Settings, row: dict[str, Any], why: str) -> None:
    with open_for_write(splits) as conn:
        insert_row(conn, "corporate_actions", row)
    result = _retract(splits, dry_run=True)
    assert (result.identities, result.action_rows) == (0, 0)
    [line] = result.stale
    assert why in line and PAR in line
    with pytest.raises(RepairRefused, match="1 stale list entries"):
        _retract(splits, expect=0, release=REL)
    assert len(_actions(splits)) == len(ALL) + 1


def test_a_repaired_identity_stays_out_through_a_backfill(splits: Settings) -> None:
    """After the delete, the same Alpaca row is refused by ingest (the list is
    read by the source), so the repair is never undone by a refetch."""
    _retract(splits, expect=1, release=REL)
    source = AlpacaPriceSource(
        ListingResolver([{"security_id": PAR, "ticker": "PAR", "valid_from": date(2016, 1, 4)}]),
        fetch_actions=lambda symbols, start, end: {
            "reverse_splits": [
                {
                    "id": "s1",
                    "symbol": "PAR",
                    "ex_date": EX.isoformat(),
                    "process_date": EX.isoformat(),
                    "old_rate": 4,
                    "new_rate": 1,
                }
            ]
        },
        settings=Settings(_env_file=None),  # type: ignore[call-arg]
        spinoff_splits=LIST,
    )
    window = (date(2020, 4, 1), date(2020, 4, 30))
    again = source.corporate_actions([PAR], *window)
    assert again == []
    assert source.last_actions_report is not None
    assert source.last_actions_report.reviewed_spinoff_splits_refused == 1
    with open_for_write(splits) as conn:
        _add_actions(conn, again, window, ingested_at=RUN + timedelta(hours=1))
    assert _actions(splits) == [(OTHER, "split", "g")]


def test_the_repair_spinoff_splits_command(
    splits: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repair, "spinoff_splits", lambda: LIST)
    app = cli.make_app(settings=lambda: splits, clock=lambda: RUN)
    runner = CliRunner()
    base = ["repair-spinoff-splits", "--release", REL]
    dry = runner.invoke(app, [*base, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert dry.output.startswith("would delete 1 spin-off split rows")
    assert runner.invoke(app, ["repair-spinoff-splits", "--dry-run"]).exit_code == (cli.USAGE_ERROR)
    assert runner.invoke(app, base).exit_code == cli.USAGE_ERROR  # no count
    other = runner.invoke(
        app, ["repair-spinoff-splits", "--release", "other", "--expect-action-rows", "1"]
    )
    assert other.exit_code == 1
    assert "repair refused: no open data release named 'other'" in other.output
    assert _actions(splits) == ALL
    done = runner.invoke(app, [*base, "--expect-action-rows", "1"])
    assert done.exit_code == 0, done.output
    assert done.output.startswith("deleted 1 spin-off split rows")
    assert _actions(splits) == [(OTHER, "split", "g")]


def test_the_command_exits_1_on_a_stale_dry_run(
    splits: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repair, "spinoff_splits", lambda: (_entry(ratio=0.5),))
    app = cli.make_app(settings=lambda: splits, clock=lambda: RUN)
    dry = CliRunner().invoke(app, ["repair-spinoff-splits", "--release", REL, "--dry-run"])
    assert dry.exit_code == 1
    assert "stale 1" in dry.output and "not the reviewed" in dry.output


def test_a_repair_that_misses_its_count_rolls_back(
    splits: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = repair._delete_split_identities
    monkeypatch.setattr(repair, "_delete_split_identities", lambda *a, **k: real(*a, **k) - 1)
    with pytest.raises(RuntimeError, match="rolled back"):
        _retract(splits, expect=1, release=REL)
    assert _actions(splits) == ALL
    assert _rows(splits, "SELECT count(*) FROM ingestion_runs") == [(0,)]
