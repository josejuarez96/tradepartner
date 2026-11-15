"""T391 (#3411): `repair_restore`, the owner's way back for retracted rows (ADR 0029).

A restore of one row, of one release, and of a row whose key is held (refused and
named); the dry run writes nothing; a restore is recorded in
`retracted_rows.restored_at`; a restore inside an open paper window refuses; a
restore with no open release refuses; the close names the restored sessions. Fixture
stores only.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import open_paper_window
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.config import Settings
from tradepartner.repair import RepairRefused
from tradepartner.repair_restore import repair_restore
from tradepartner.store import registry
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.retracted_rows import retract
from tradepartner.store.schema import init_schema

SID, OTHER = "0000000001", "0000000002"
OLD, NEW = "repair-old", "restore-new"
RUN = datetime(2026, 11, 14, 15, tzinfo=UTC)
THEN = datetime(2026, 11, 12, 15, tzinfo=UTC)
KNOWN = datetime(2021, 6, 1, 21, tzinfo=UTC)
D1, D2, D3 = date(2021, 6, 1), date(2021, 6, 2), date(2021, 6, 3)


def _bar(sid: str, session: date, known: datetime = KNOWN) -> dict[str, Any]:
    return {
        "security_id": sid,
        "session": session,
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 1_000,
        "known_at": known,
        "ingested_at": known,
        "source": "alpaca_sip",
        "provenance": "bar",
    }


def _split(sid: str, ex: date) -> dict[str, Any]:
    return {
        "security_id": sid,
        "action_type": "split",
        "ex_date": ex,
        "ratio_or_amount": 2.0,
        "announced_at": None,
        "source_action_id": f"{sid}-{ex}",
        "cancelled": False,
        "known_at": KNOWN,
        "ingested_at": KNOWN,
        "source": "alpaca",
        "provenance": "action",
    }


@pytest.fixture
def store(settings: Settings) -> Settings:
    """Three bars and a split of SID retracted in release OLD (closed), and release NEW open."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        for day in (D1, D2, D3):
            insert_row(conn, "prices_daily", _bar(SID, day))
        insert_row(conn, "prices_daily", _bar(OTHER, D1))
        insert_row(conn, "corporate_actions", _split(SID, D2))
    with open_for_write(settings) as conn:
        retract(
            conn, "prices_daily", [(SID, D1), (SID, D2), (SID, D3)], "bad", OLD, "rule A", at=THEN
        )
        retract(
            conn,
            "corporate_actions",
            [(SID, f"{SID}-{D2}", "", None)],
            "bad",
            OLD,
            "rule B",
            at=THEN,
        )
    with open_for_write(settings) as conn:
        registry.open_data_release(conn, conn, name=NEW, backup_path="b.duckdb", reason="#3411")
    return settings


def _rows(settings: Settings, sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(sql).fetchall()


def _bars(settings: Settings) -> list[tuple[Any, ...]]:
    return _rows(
        settings, "SELECT security_id, session FROM prices_daily ORDER BY security_id, session"
    )


def _restored(settings: Settings) -> int:
    return int(
        _rows(settings, "SELECT count(*) FROM retracted_rows WHERE restored_at IS NOT NULL")[0][0]
    )


def test_a_dry_run_lists_the_rows_and_writes_nothing(store: Settings) -> None:
    before = _rows(store, "SELECT * FROM retracted_rows ORDER BY ALL")
    result = repair_restore(store, release=NEW, from_release=OLD, dry_run=True, clock=lambda: RUN)
    assert result.dry_run is True
    assert len(result.rows) == 4
    assert result.summary().startswith("would restore 4 retracted rows of release repair-old")
    assert _bars(store) == [(OTHER, D1)]
    assert _rows(store, "SELECT * FROM retracted_rows ORDER BY ALL") == before
    assert _rows(store, "SELECT count(*) FROM ingestion_runs") == [(0,)]


def test_a_restore_of_one_row_puts_back_that_key_only(store: Settings) -> None:
    result = repair_restore(
        store, release=NEW, table="prices_daily", key=(SID, D2), expect=1, clock=lambda: RUN
    )
    assert [(r.table, r.session) for r in result.rows] == [("prices_daily", D2)]
    assert _bars(store) == [(SID, D2), (OTHER, D1)]
    assert _restored(store) == 1
    [(stamp,)] = _rows(
        store, "SELECT restored_at FROM retracted_rows WHERE restored_at IS NOT NULL"
    )
    assert stamp == RUN


def test_a_restore_of_one_release_puts_back_every_row_with_its_known_at(store: Settings) -> None:
    result = repair_restore(store, release=NEW, from_release=OLD, expect=4, clock=lambda: RUN)
    assert len(result.rows) == 4
    assert _bars(store) == [(SID, D1), (SID, D2), (SID, D3), (OTHER, D1)]
    assert _rows(store, "SELECT known_at FROM corporate_actions") == [(KNOWN,)]
    assert _rows(store, "SELECT DISTINCT known_at FROM prices_daily") == [(KNOWN,)]
    assert _restored(store) == 4
    [(run,)] = _rows(store, "SELECT message FROM ingestion_runs WHERE mode = 'repair'")
    assert "restored 4 retracted rows" in run
    assert NEW in run


def test_the_close_names_the_restored_sessions(store: Settings) -> None:
    result = repair_restore(store, release=NEW, from_release=OLD, expect=4, clock=lambda: RUN)
    assert (result.sessions_from, result.sessions_to) == (D1, D3)
    assert f"--name {NEW} --from 2021-06-01 --to 2021-06-03" in result.close_command()
    assert "decision data-release close" in result.close_command()


def test_a_held_key_refuses_and_names_it_and_nothing_comes_back(store: Settings) -> None:
    with open_for_write(store) as conn:
        insert_row(conn, "prices_daily", _bar(SID, D2, known=datetime(2021, 7, 1, tzinfo=UTC)))
    with pytest.raises(RepairRefused, match=r"prices_daily key .*2021-06-02.* is held"):
        repair_restore(store, release=NEW, from_release=OLD, expect=4, clock=lambda: RUN)
    assert _bars(store) == [(SID, D2), (OTHER, D1)]
    assert _restored(store) == 0
    assert _rows(store, "SELECT count(*) FROM ingestion_runs") == [(0,)]


def test_the_dry_run_names_a_held_key_too(store: Settings) -> None:
    with open_for_write(store) as conn:
        insert_row(conn, "prices_daily", _bar(SID, D1))
    with pytest.raises(RepairRefused, match="is held"):
        repair_restore(store, release=NEW, from_release=OLD, dry_run=True, clock=lambda: RUN)


def test_a_real_run_writes_only_the_dry_runs_count(store: Settings) -> None:
    with pytest.raises(RepairRefused, match="found 4 rows, not the expected 3"):
        repair_restore(store, release=NEW, from_release=OLD, expect=3, clock=lambda: RUN)
    assert _bars(store) == [(OTHER, D1)]
    assert _restored(store) == 0
    with pytest.raises(ValueError, match="expect"):
        repair_restore(store, release=NEW, from_release=OLD, clock=lambda: RUN)


def test_no_open_release_refuses(store: Settings) -> None:
    with pytest.raises(RepairRefused, match="no open data release named 'other'"):
        repair_restore(store, release="other", from_release=OLD, expect=4, clock=lambda: RUN)
    with open_for_write(store) as conn:
        registry.close_data_release(conn, name=NEW, sessions_from=D1, sessions_to=D3)
    with pytest.raises(RepairRefused, match="no release is open"):
        repair_restore(store, release=NEW, from_release=OLD, expect=4, clock=lambda: RUN)
    assert _restored(store) == 0


def test_it_runs_only_in_its_own_release_not_the_one_that_retracted(store: Settings) -> None:
    with open_for_write(store) as conn:
        registry.close_data_release(conn, name=NEW, sessions_from=D1, sessions_to=D3)
        registry.open_data_release(conn, conn, name=OLD, backup_path="b.duckdb", reason="again")
    with pytest.raises(RepairRefused, match="its own release"):
        repair_restore(store, release=OLD, from_release=OLD, expect=4, clock=lambda: RUN)
    assert _restored(store) == 0


def test_an_open_paper_window_refuses_a_real_run_and_a_dry_run(store: Settings) -> None:
    open_paper_window(store, datetime(2026, 11, 10, 14, tzinfo=UTC))
    for dry in (True, False):
        with pytest.raises(RepairRefused, match="an open paper window"):
            repair_restore(
                store,
                release=NEW,
                from_release=OLD,
                dry_run=dry,
                expect=None if dry else 4,
                clock=lambda: RUN,
            )
    assert _bars(store) == [(OTHER, D1)]
    assert _restored(store) == 0


def test_a_selection_that_matches_nothing_refuses(store: Settings) -> None:
    with pytest.raises(RepairRefused, match="no unrestored retracted row"):
        repair_restore(store, release=NEW, from_release="never", expect=1, clock=lambda: RUN)
    with pytest.raises(ValueError, match="name a release"):
        repair_restore(store, release=NEW, expect=1, clock=lambda: RUN)


def test_a_second_restore_of_a_restored_release_finds_nothing(store: Settings) -> None:
    repair_restore(store, release=NEW, from_release=OLD, expect=4, clock=lambda: RUN)
    with pytest.raises(RepairRefused, match="no unrestored retracted row"):
        repair_restore(store, release=NEW, from_release=OLD, expect=4, clock=lambda: RUN)


# The command.


def _invoke(settings: Settings, *args: str) -> tuple[int, str]:
    app = cli.make_app(settings=lambda: settings, clock=lambda: RUN)
    result = CliRunner().invoke(app, ["repair-restore", *args])
    return result.exit_code, result.output


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def test_the_command_dry_runs_then_restores_and_prints_the_close(store: Settings) -> None:
    code, out = _invoke(store, "--release", NEW, "--from-release", OLD, "--dry-run")
    assert code == 0, out
    assert "would restore 4" in out
    assert _bars(store) == [(OTHER, D1)]
    code, out = _invoke(store, "--release", NEW, "--from-release", OLD)
    assert code != 0 and "--dry-run first" in out
    code, out = _invoke(store, "--release", NEW, "--from-release", OLD, "--expect-rows", "4")
    assert code == 0, out
    assert "restored 4" in out
    assert "--from 2021-06-01 --to 2021-06-03" in out
    assert len(_bars(store)) == 4


def test_the_command_restores_one_row_by_table_and_key(store: Settings) -> None:
    code, out = _invoke(
        store, "--release", NEW, "--table", "prices_daily", "--key", SID, "--key", "2021-06-03",
        "--expect-rows", "1",
    )  # fmt: skip
    assert code == 0, out
    assert _bars(store) == [(SID, D3), (OTHER, D1)]


def test_the_command_exits_one_on_a_refusal(store: Settings) -> None:
    open_paper_window(store, datetime(2026, 11, 10, 14, tzinfo=UTC))
    code, out = _invoke(store, "--release", NEW, "--from-release", OLD, "--dry-run")
    assert code == 1
    assert "repair refused" in out
