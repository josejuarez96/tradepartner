"""`ingest.ingest_spinoff_backfill` and `spinoff-backfill` (#3058).

The symbol-less actions scan finds nothing before `alpaca.actions_backfill_floor`;
Alpaca's per-symbol request does (#101). The backfill asks it, month by month,
for the `spin_off` type alone, and writes the spin-offs it returns by the usual
writer rules, only inside an open data release.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.adapters import alpaca_raw
from tradepartner.calendar import previous_session, session_close
from tradepartner.config import Settings
from tradepartner.ingest import ACTIONS_FILLED, ACTIONS_MODE, ingest_spinoff_backfill
from tradepartner.store import registry
from tradepartner.store.db import open_for_write

NOW = datetime(2026, 11, 6, 23, tzinfo=UTC)
REL = "spinoff-backfill-test"
JUNE = (date(2019, 6, 1), date(2019, 6, 30))
EX = date(2019, 6, 17)


def _payload(symbols: list[str], start: date, end: date) -> dict[str, Any]:
    """Alpaca's answer: one spin-off of SPY into MTUM, and a split row that a
    types-filtered request would never carry (the backfill must not store it)."""
    rows: dict[str, list[dict[str, Any]]] = {
        "spin_offs": [
            {
                "id": "spin-2019",
                "source_symbol": "SPY",
                "new_symbol": "MTUM",
                "ex_date": EX.isoformat(),
                "process_date": EX.isoformat(),
                "source_rate": 4,
                "new_rate": 1,
            }
        ],
        "forward_splits": [
            {
                "id": "split-2019",
                "symbol": "SPY",
                "ex_date": EX.isoformat(),
                "process_date": EX.isoformat(),
                "old_rate": 1,
                "new_rate": 2,
            }
        ],
    }
    return {
        category: [
            row
            for row in category_rows
            if start <= date.fromisoformat(row["process_date"]) <= end
            and (row.get("source_symbol") or row.get("symbol")) in symbols
        ]
        for category, category_rows in rows.items()
    }


@pytest.fixture
def store(fixture_store_path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(fixture_store_path)})


def _open(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        registry.open_data_release(conn, conn, name=REL, backup_path="b.duckdb", reason="#3058")


def _actions(settings: Settings, kind: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(
            "SELECT security_id, source_action_id, ex_date, ratio_or_amount, known_at "
            "FROM corporate_actions WHERE action_type = ? AND ex_date BETWEEN ? AND ? "
            "ORDER BY security_id",
            [kind, *JUNE],
        ).fetchall()


def _runs(settings: Settings) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(
            "SELECT mode, status, chunk_cursor FROM ingestion_runs "
            "WHERE chunk_cursor LIKE 'spinoffs;%' ORDER BY started_at, chunk_cursor"
        ).fetchall()


def test_a_dry_run_fetches_counts_and_writes_nothing(store: Settings) -> None:
    calls: list[tuple[int, date, date]] = []

    def fetch(symbols: list[str], start: date, end: date) -> dict[str, Any]:
        calls.append((len(symbols), start, end))
        return _payload(symbols, start, end)

    result = ingest_spinoff_backfill(
        store, since=JUNE[0], through=JUNE[1], clock=lambda: NOW, dry_run=True, fetch=fetch
    )
    [run] = result.runs
    assert run.status == ACTIONS_FILLED, run.message
    assert run.message.startswith(f"dry run: {JUNE[0]}..{JUNE[1]}: 1 spin-off action(s) of ")
    assert run.rows_added == 1
    assert calls and all(n > 0 for n, _, _ in calls)
    assert _actions(store, "spinoff") == []
    assert _runs(store) == []


def test_a_real_run_writes_the_spin_off_only_inside_the_release(store: Settings) -> None:
    with pytest.raises(ValueError, match="named data release"):
        ingest_spinoff_backfill(
            store, since=JUNE[0], through=JUNE[1], clock=lambda: NOW, fetch=_payload
        )
    with pytest.raises(ValueError, match="no release is open"):
        ingest_spinoff_backfill(
            store, since=JUNE[0], through=JUNE[1], release=REL, clock=lambda: NOW, fetch=_payload
        )
    assert _actions(store, "spinoff") == []
    _open(store)
    result = ingest_spinoff_backfill(
        store, since=JUNE[0], through=JUNE[1], release=REL, clock=lambda: NOW, fetch=_payload
    )
    [run] = result.runs
    assert run.status == ACTIONS_FILLED, run.message
    [(child, parent, ex_date, ratio, known_at)] = _actions(store, "spinoff")
    assert (ex_date, ratio) == (EX, 0.25)
    assert child != parent
    # stamped no earlier than the close before the ex-date (#1712), at the
    # close of the first session after the process date (req 5)
    assert known_at >= session_close(previous_session(EX))
    assert known_at <= NOW
    assert _runs(store) == [
        (ACTIONS_MODE, ACTIONS_FILLED, f"spinoffs;since={JUNE[0]};through={JUNE[1]}")
    ]
    # the split row in the answer is not the backfill's to store
    assert [row for row in _actions(store, "split") if row[1] == "split-2019"] == []
    again = ingest_spinoff_backfill(
        store, since=JUNE[0], through=JUNE[1], release=REL, clock=lambda: NOW, fetch=_payload
    )
    assert again.runs[0].rows_added == 0  # idempotent
    assert len(_actions(store, "spinoff")) == 1


def test_the_default_request_asks_for_spin_offs_only(
    store: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alpaca.data.enums import CorporateActionsType

    seen: list[Any] = []

    def fake(symbols: list[str], start: date, end: date, **kwargs: Any) -> dict[str, Any]:
        seen.append(kwargs.get("types"))
        return {}

    monkeypatch.setattr(alpaca_raw, "corporate_actions", fake)
    result = ingest_spinoff_backfill(
        store, since=JUNE[0], through=JUNE[1], clock=lambda: NOW, dry_run=True
    )
    assert result.runs[0].status == ACTIONS_FILLED
    assert seen and all(types == [CorporateActionsType.SPIN_OFF] for types in seen)


def test_through_defaults_to_the_day_before_the_floor(store: Settings) -> None:
    result = ingest_spinoff_backfill(
        store, since=date(2020, 5, 1), clock=lambda: NOW, dry_run=True, fetch=_payload
    )
    [run] = result.runs
    assert run.chunk_cursor == "spinoffs;since=2020-05-01;through=2020-05-31"
    with pytest.raises(ValueError, match="on or after since"):
        ingest_spinoff_backfill(
            store, since=date(2020, 6, 1), clock=lambda: NOW, dry_run=True, fetch=_payload
        )


def test_the_spinoff_backfill_command(store: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        alpaca_raw,
        "corporate_actions",
        lambda symbols, start, end, **_: _payload(symbols, start, end),
    )
    keyed = Settings(
        _env_file=None,  # type: ignore[call-arg]
        store={"path": store.store.path},
        alpaca_api_key="test-key-id",
        alpaca_api_secret="test-secret-value",
    )
    app = cli.make_app(settings=lambda: keyed, clock=lambda: NOW)
    runner = CliRunner()
    base = ["spinoff-backfill", "--since", str(JUNE[0]), "--through", str(JUNE[1])]
    dry = runner.invoke(app, [*base, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "1 spin-off action(s)" in dry.output
    assert runner.invoke(app, base).exit_code == cli.USAGE_ERROR  # no release
    closed = runner.invoke(app, [*base, "--release", REL])
    assert closed.exit_code == cli.USAGE_ERROR
    assert "no release is open" in closed.output
    _open(store)
    done = runner.invoke(app, [*base, "--release", REL])
    assert done.exit_code == 0, done.output
    assert len(_actions(store, "spinoff")) == 1
    unkeyed = cli.make_app(settings=lambda: store, clock=lambda: NOW)
    missing = runner.invoke(unkeyed, [*base, "--dry-run"])
    assert missing.exit_code == cli.USAGE_ERROR
    assert "ALPACA_API_KEY" in missing.output
