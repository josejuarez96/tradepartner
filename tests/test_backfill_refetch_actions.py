"""#3054: `refetch_actions`, the splits-and-dividends refetch of committed
backfill months, with no bars, under a data release.

A backfill month keeps the actions its first fetch got; an action the
resolver of that day could not receive (a pre-rename symbol it never asked)
arrives only by asking the month again. Same store, `_History` stub and
synthetic filings as `test_backfill`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import open_paper_window
from test_backfill import SINCE, _backfill, _History
from test_ingest import ACME, NOW

from tradepartner.adapters.prices import ActionType, CorporateAction, action_first_seen_known_at
from tradepartner.backfill import ACTIONS_REFETCH, FILLED, refetch_actions
from tradepartner.config import Settings
from tradepartner.ingest import FAILED
from tradepartner.store import registry
from tradepartner.store.db import open_for_write
from tradepartner.store.schema import init_schema

REL = "refetch-test"
LATER = NOW + timedelta(days=3)
EX = date(2019, 5, 15)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def _dividend(ex_date: date = EX, amount: float = 0.25, action_id: str = "acme-q2") -> Any:
    return CorporateAction(
        ACME,
        ActionType.DIVIDEND,
        ex_date,
        amount,
        action_first_seen_known_at(ex_date),
        "alpaca",
        source_action_id=action_id,
    )


def _open_release(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        init_schema(conn)
        registry.open_data_release(conn, conn, name=REL, backup_path="b.duckdb", reason="#3054")


def _rows(settings: Settings, sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        return conn.execute(sql).fetchall()


def _committed(settings: Settings) -> _History:
    """A backfill whose source served no actions: the months are committed."""
    prices = _History()
    _backfill(settings, prices)
    assert _rows(settings, "SELECT count(*) FROM corporate_actions") == [(0,)]
    prices.actions = [_dividend()]
    prices.calls.clear()
    return prices


def _refetch(settings: Settings, prices: _History, **kwargs: Any) -> Any:
    options: dict[str, Any] = {
        "since": date(2019, 5, 1),
        "through": date(2019, 5, 31),
        "release": REL,
        "clock": lambda: LATER,
    }
    options.update(kwargs)
    return refetch_actions(settings, prices=prices, **options)


def test_a_dry_run_counts_the_missing_action_and_writes_nothing(settings: Settings) -> None:
    prices = _committed(settings)
    runs_before = _rows(settings, "SELECT count(*) FROM ingestion_runs")
    result = _refetch(settings, prices, dry_run=True)  # no release needed to look
    assert result.dry_run and result.ok
    assert result.added == (("dividend", 1),)
    assert "would add 1 dividend" in result.summary()
    assert [c for c in prices.calls if c[0] == "bars"] == []  # actions only
    assert _rows(settings, "SELECT count(*) FROM corporate_actions") == [(0,)]
    assert _rows(settings, "SELECT count(*) FROM ingestion_runs") == runs_before


def test_a_real_run_writes_only_inside_the_open_release(settings: Settings) -> None:
    prices = _committed(settings)
    refused = _refetch(settings, prices)
    assert [r.status for r in refused.runs] == [FAILED]
    assert "no open data release" in refused.runs[0].message
    assert prices.calls == []  # refused before any fetch
    assert _rows(settings, "SELECT count(*) FROM corporate_actions") == [(0,)]
    _open_release(settings)
    result = _refetch(settings, prices)
    assert result.ok and result.added == (("dividend", 1),)
    [(sid, ex, amount, known_at, ingested_at)] = _rows(
        settings,
        "SELECT security_id, ex_date, ratio_or_amount, known_at, ingested_at "
        "FROM corporate_actions",
    )
    assert (sid, ex, amount) == (ACME, EX, 0.25)
    # The source's timing rule, never the refetch's clock: no look-ahead, and
    # no later than an as-of read before the ex-date could have seen it.
    assert known_at == action_first_seen_known_at(EX)
    assert ingested_at == LATER
    # The refused attempt left its own `failed` row; the run is `filled`, never `ok`.
    statuses = _rows(
        settings, f"SELECT status FROM ingestion_runs WHERE mode = '{ACTIONS_REFETCH}'"
    )
    assert sorted(s for (s,) in statuses) == sorted([FAILED, FILLED])
    [(status, mode, cursor, message)] = _rows(
        settings,
        "SELECT status, mode, chunk_cursor, message FROM ingestion_runs "
        f"WHERE mode = '{ACTIONS_REFETCH}' AND status = '{FILLED}'",
    )
    assert (status, mode) == (FILLED, ACTIONS_REFETCH)
    assert cursor == f"{ACTIONS_REFETCH};since=2019-05-01;through=2019-05-31"
    assert "added dividend=1" in message and f"release {REL}" in message


def test_a_second_run_adds_nothing(settings: Settings) -> None:
    prices = _committed(settings)
    _open_release(settings)
    _refetch(settings, prices)
    again = _refetch(settings, prices)
    assert again.ok and again.added == ()
    assert _rows(settings, "SELECT count(*) FROM corporate_actions") == [(1,)]


def test_securities_limit_the_request(settings: Settings) -> None:
    prices = _committed(settings)
    result = _refetch(settings, prices, dry_run=True, securities=["0000000099"])
    assert result.added == ()
    with pytest.raises(ValueError, match="at least one id"):
        _refetch(settings, prices, dry_run=True, securities=[])


def test_months_are_asked_one_by_one(settings: Settings) -> None:
    prices = _committed(settings)
    result = _refetch(settings, prices, since=SINCE, through=date(2019, 6, 28), dry_run=True)
    assert [c[1:] for c in prices.calls if c[0] == "actions"] == [
        (SINCE, date(2019, 4, 30)),
        (date(2019, 5, 1), date(2019, 5, 31)),
        (date(2019, 6, 1), date(2019, 6, 28)),
    ]
    assert result.added == (("dividend", 1),)


def test_a_bad_range_is_refused(settings: Settings) -> None:
    prices = _History()
    with pytest.raises(ValueError, match="on or after"):
        _refetch(settings, prices, since=date(2019, 5, 2), through=date(2019, 5, 1))
    with pytest.raises(TypeError):
        _refetch(settings, prices, since=datetime(2019, 5, 1, tzinfo=UTC))


def test_the_cli_dry_run_prints_the_counts(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from tradepartner import cli

    prices = _committed(settings)
    monkeypatch.setattr(cli, "_missing_secrets", lambda *_: [])
    app = cli.make_app(
        settings=lambda: settings, clock=lambda: LATER, price_source=lambda _: prices
    )
    args = ["refetch-actions", "--since", "2019-05-01", "--through", "2019-05-31"]
    done = CliRunner().invoke(app, [*args, "--release", REL, "--dry-run"])
    assert done.exit_code == 0, done.output
    assert "would add 1 dividend" in done.output
    blank = CliRunner().invoke(app, [*args, "--release", " "])
    assert blank.exit_code != 0 and "data release" in blank.output
    refused = CliRunner().invoke(app, [*args, "--release", REL])
    assert refused.exit_code == 1 and "no open data release" in refused.output
    assert _rows(settings, "SELECT count(*) FROM corporate_actions") == [(0,)]


def test_name_changes_only_needs_the_actions_backfill(settings: Settings) -> None:
    from typer.testing import CliRunner

    from tradepartner import cli

    app = cli.make_app(settings=lambda: settings)
    done = CliRunner().invoke(app, ["ingest", "--source", "alpaca", "--name-changes-only"])
    assert done.exit_code != 0
    assert "--name-changes-only needs --actions-backfill" in done.output


def test_spin_offs_are_left_to_their_own_path(settings: Settings) -> None:
    prices = _committed(settings)
    prices.actions.append(
        CorporateAction(
            ACME,
            ActionType.SPINOFF,
            EX,
            0.5,
            action_first_seen_known_at(EX),
            "alpaca",
            source_action_id="0000000099",
        )
    )
    result = _refetch(settings, prices, dry_run=True)
    assert result.added == (("dividend", 1),)


def test_a_real_run_refuses_inside_an_open_paper_window_and_writes_nothing(
    settings: Settings,
) -> None:
    prices = _committed(settings)
    _open_release(settings)
    open_paper_window(settings, datetime(2019, 5, 14, 14, tzinfo=UTC))
    result = _refetch(settings, prices)
    assert not result.ok
    assert "open paper window" in " ".join(r.message for r in result.runs)
    assert _rows(settings, "SELECT count(*) FROM corporate_actions") == [(0,)]
    assert _rows(settings, f"SELECT count(*) FROM ingestion_runs WHERE status = '{FILLED}'") == [
        (0,)
    ]
