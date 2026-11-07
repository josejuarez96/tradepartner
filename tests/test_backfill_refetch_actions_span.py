"""#3074: `refetch_actions(span=True)`, one request over the whole span for
named securities, with a process-date window that reaches the run's day.

Alpaca filters corporate actions on `process_date` (#101). A month request
asks for actions processed within `alpaca.actions_process_lag_days` of the
month, so a dividend Alpaca processed later (a re-processed or late-loaded
record) is in no month's answer and a month refetch cannot recover it. The
span mode asks once for `[since, through]`, every symbol each named security
held in it, with the lag widened by `span_settings` so the process window
covers every day up to the run. Same store and `_History` stub as
`test_backfill_refetch_actions`.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from test_backfill import SINCE, _History
from test_backfill_refetch_actions import (
    EX,
    LATER,
    REL,
    _committed,
    _dividend,
    _open_release,
    _refetch,
    _rows,
)
from test_ingest import ACME

from tradepartner.adapters.alpaca_prices import AlpacaPriceSource, ListingResolver
from tradepartner.adapters.prices import action_first_seen_known_at
from tradepartner.backfill import span_settings
from tradepartner.config import Settings

THROUGH = date(2019, 6, 28)
STAMP = action_first_seen_known_at(EX).isoformat()
AGAIN = LATER + timedelta(days=1)  # a second run's clock


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1},
    )


def test_span_asks_one_window_for_the_named_securities(settings: Settings) -> None:
    prices = _committed(settings)
    result = _refetch(
        settings, prices, since=SINCE, through=THROUGH, span=True, securities=[ACME], dry_run=True
    )
    assert [c[1:] for c in prices.calls if c[0] == "actions"] == [(SINCE, THROUGH)]
    assert result.ok and result.added == (("dividend", 1),)
    assert len(result.runs) == 1
    assert "over 1 span" in result.summary()


def test_span_lists_each_row_it_would_add(settings: Settings) -> None:
    prices = _committed(settings)
    result = _refetch(
        settings, prices, since=SINCE, through=THROUGH, span=True, securities=[ACME], dry_run=True
    )
    assert result.rows == (f"new {ACME} dividend {EX} 0.25 known_at={STAMP}",)
    assert _rows(settings, "SELECT count(*) FROM corporate_actions") == [(0,)]


def test_month_mode_also_lists_its_rows(settings: Settings) -> None:
    prices = _committed(settings)
    result = _refetch(settings, prices, dry_run=True)
    assert result.rows == (f"new {ACME} dividend {EX} 0.25 known_at={STAMP}",)


def _stored_then(settings: Settings, again: Any) -> Any:
    """ACME's dividend stored by a first span run; the source then answers `again`."""
    prices = _committed(settings)
    _open_release(settings)
    _refetch(settings, prices, since=SINCE, through=THROUGH, span=True, securities=[ACME])
    prices.actions = [again]
    return _refetch(
        settings,
        prices,
        since=SINCE,
        through=THROUGH,
        span=True,
        securities=[ACME],
        dry_run=True,
        clock=lambda: AGAIN,
    )


def test_a_changed_amount_is_listed_as_a_revision_at_the_clock(settings: Settings) -> None:
    result = _stored_then(settings, _dividend(amount=0.2501))
    [line] = result.rows
    assert line.startswith(f"revision {ACME} dividend {EX} 0.2501 ")
    assert line.endswith(f"known_at={AGAIN.isoformat()}")  # never back-dated


def test_a_reissued_id_for_a_stored_event_is_listed_as_a_collision(settings: Settings) -> None:
    result = _stored_then(settings, _dividend(action_id="acme-q2-reissued"))
    assert result.rows == (f"collision {ACME} dividend {EX} 0.25 known_at={STAMP}",)


def test_span_needs_named_securities(settings: Settings) -> None:
    prices = _History()
    with pytest.raises(ValueError, match="span needs --security"):
        _refetch(settings, prices, since=SINCE, through=THROUGH, span=True)
    assert prices.calls == []


def test_a_real_span_run_writes_inside_the_release_at_the_source_stamp(
    settings: Settings,
) -> None:
    prices = _committed(settings)
    _open_release(settings)
    result = _refetch(settings, prices, since=SINCE, through=THROUGH, span=True, securities=[ACME])
    assert result.ok and result.added == (("dividend", 1),)
    [(known_at, ingested_at)] = _rows(
        settings, "SELECT known_at, ingested_at FROM corporate_actions"
    )
    assert known_at == action_first_seen_known_at(EX)  # the source's rule, not the clock
    assert ingested_at == LATER
    [(cursor,)] = _rows(
        settings, "SELECT chunk_cursor FROM ingestion_runs WHERE mode = 'actions_refetch'"
    )
    assert cursor == f"actions_refetch;span;since={SINCE};through={THROUGH}"


def test_span_settings_widen_the_lag_to_the_run_day(settings: Settings) -> None:
    today = date(2026, 11, 6)
    wide = span_settings(settings, through=date(2016, 12, 31), today=today)
    lag = wide.alpaca.actions_process_lag_days
    assert date(2016, 12, 31) + timedelta(days=lag) >= today
    assert wide.alpaca.actions_backfill_floor == settings.alpaca.actions_backfill_floor
    # Never narrower than the configured lag.
    near = span_settings(settings, through=today, today=today)
    assert near.alpaca.actions_process_lag_days == settings.alpaca.actions_process_lag_days


def _late_processed(symbols: list[str], start: date, end: date) -> dict[str, Any]:
    """Alpaca's rule: a record is returned when its `process_date` is in
    `[start, end]`, whatever its ex-date. SPY's 2016-03-18 dividend here was
    processed three years late."""
    rows = [
        {
            "id": "spy-2016-03",
            "symbol": "SPY",
            "ex_date": "2016-03-18",
            "process_date": "2019-03-01",
            "rate": 1.05,
        },
        {
            "id": "spy-2016-06",
            "symbol": "SPY",
            "ex_date": "2016-06-17",
            "process_date": "2016-07-29",
            "rate": 1.08,
        },
    ]
    kept = [
        r
        for r in rows
        if r["symbol"] in symbols and start <= date.fromisoformat(r["process_date"]) <= end
    ]
    return {"cash_dividends": kept}


def _spy_source(config: Settings) -> AlpacaPriceSource:
    resolver = ListingResolver(
        [
            {
                "security_id": "BENCH:SPY",
                "ticker": "SPY",
                "exchange": "NYSEARCA",
                "valid_from": date(1993, 1, 29),
            }
        ]
    )
    return AlpacaPriceSource(resolver, fetch_actions=_late_processed, settings=config)


def test_a_late_processed_dividend_is_in_no_month_but_in_the_span() -> None:
    base = Settings(_env_file=None)
    month = _spy_source(base).corporate_actions(["BENCH:SPY"], date(2016, 3, 1), date(2016, 3, 31))
    assert month == []  # processed 2019-03-01, beyond the 90-day lag
    wide = span_settings(base, through=date(2016, 12, 31), today=date(2026, 11, 6))
    span = _spy_source(wide).corporate_actions(["BENCH:SPY"], date(2016, 1, 1), date(2016, 12, 31))
    assert [(a.ex_date, a.ratio_or_amount) for a in span] == [
        (date(2016, 3, 18), 1.05),
        (date(2016, 6, 17), 1.08),
    ]
    # Timing is the first-seen proxy of the ex-date, never the process date.
    assert span[0].known_at == action_first_seen_known_at(date(2016, 3, 18))


def test_the_cli_span_builds_the_source_with_the_wide_lag(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from tradepartner import cli

    prices = _committed(settings)
    seen: list[Settings] = []

    def factory(s: Settings) -> _History:
        seen.append(s)
        return prices

    monkeypatch.setattr(cli, "_missing_secrets", lambda *_: [])
    app = cli.make_app(settings=lambda: settings, clock=lambda: LATER, price_source=factory)
    args = ["refetch-actions", "--since", str(SINCE), "--through", str(THROUGH)]
    done = CliRunner().invoke(
        app, [*args, "--release", REL, "--span", "--security", ACME, "--dry-run"]
    )
    assert done.exit_code == 0, done.output
    assert "would add 1 dividend" in done.output
    assert f"would write new {ACME} dividend {EX} 0.25" in done.output
    [used] = seen
    assert THROUGH + timedelta(days=used.alpaca.actions_process_lag_days) >= LATER.date()
    nameless = CliRunner().invoke(app, [*args, "--release", REL, "--span", "--dry-run"])
    assert nameless.exit_code != 0 and "span needs --security" in nameless.output
