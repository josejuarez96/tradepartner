"""The `tradepartner` command (Phase 2 plan T19; spec reqs 9, 11, 12 and "CLI").

`make_app` takes every edge the tests need to replace (settings, clock, the
EDGAR HTTP client, the price source, the dashboard launcher), so nothing here
touches the network, the owner's store or a browser.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.adapters import edgar_raw
from tradepartner.adapters.edgar_source import EdgarFilingSource
from tradepartner.adapters.prices import PriceSource
from tradepartner.backfill import FILLED, NO_HOLE, UNASSIGNED, Hole, HoleFill, NamedSecurity
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.ingest import FAILED, OK, STALE, IngestResult, SourceRun
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

USER_AGENT = "Test Owner test-owner-ua@example.com"
T_END = session_close(date(2020, 6, 30))
NOW = datetime(2020, 7, 1, 23, 0, tzinfo=UTC)


def _settings(tmp_path: Path, store: Path | None = None, **overrides: Any) -> Settings:
    settings = Settings(
        _env_file=None,
        store={"path": str(store or tmp_path / "store.duckdb"), "lock_retry_seconds": 0},
        edgar={"cache_dir": str(tmp_path / "edgar-cache")},
        **overrides,
    )
    # 1000 req/s (no real sleeps) is past the config's `le=10` (#1108): set unvalidated.
    fast = settings.edgar.model_copy(update={"requests_per_second": 1000.0})
    return settings.model_copy(update={"edgar": fast})


def _invoke(settings: Settings, args: Sequence[str], **kwargs: Any) -> Any:  # click.testing.Result
    app = cli.make_app(settings=lambda: settings, clock=kwargs.pop("clock", lambda: NOW), **kwargs)
    return CliRunner().invoke(app, list(args))


def _run_row(
    settings: Settings,
    run_id: str,
    source: str,
    status: str,
    started: datetime,
    message: str | None,
) -> None:
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(
            conn,
            "ingestion_runs",
            {
                "run_id": run_id,
                "started_at": started,
                "finished_at": started + timedelta(minutes=5),
                "status": status,
                "source": source,
                "mode": "session",
                "rows_added": 0,
                "chunk_cursor": "2020-06-30",
                "message": message,
            },
        )


# --- ingest --------------------------------------------------------------------


class _Recorder:
    """Stands in for `ingest_session`/`backfill`, recording its arguments."""

    def __init__(self, result: IngestResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def __call__(self, settings: Settings, **kwargs: Any) -> IngestResult:
        self.calls.append({"settings": settings, **kwargs})
        return self.result


def _ok(*sources: str) -> IngestResult:
    return IngestResult(tuple(SourceRun(s, OK, 3, "2020-06-30", "fine") for s in sources))


def _patched(monkeypatch: pytest.MonkeyPatch, name: str, result: IngestResult) -> _Recorder:
    recorder = _Recorder(result)
    monkeypatch.setattr(cli, name, recorder)
    return recorder


@pytest.fixture
def secrets_set(tmp_path: Path) -> Settings:
    return _settings(
        tmp_path,
        sec_edgar_user_agent=USER_AGENT,
        alpaca_api_key="test-key-id",
        alpaca_api_secret="test-key-secret",
    )


def test_ingest_runs_the_session_and_exits_zero_when_every_source_is_ok(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    session = _patched(monkeypatch, "ingest_session", _ok("edgar", "alpaca"))
    result = _invoke(secrets_set, ["ingest"])
    assert result.exit_code == 0, result.output
    (call,) = session.calls
    assert call["source"] == "all"
    assert call["dry_run"] is False
    assert isinstance(call["filings"], EdgarFilingSource)
    assert isinstance(call["prices"], PriceSource)
    assert "edgar" in result.output and "alpaca" in result.output


def test_ingest_exits_non_zero_when_a_source_is_stale(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    stale = IngestResult(
        (
            SourceRun("edgar", OK, 1, "2020-06-30", "fine"),
            SourceRun("alpaca", STALE, 0, "2020-06-30", "reference symbol SPY has no bar"),
        )
    )
    _patched(monkeypatch, "ingest_session", stale)
    result = _invoke(secrets_set, ["ingest"])
    assert result.exit_code == 1
    assert "stale" in result.output
    assert "reference symbol SPY has no bar" in result.output


def test_ingest_prints_a_failed_run_messages_where_clause(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    # #573: the frame trail `_with_frames` appends to a failed run's message
    # is already redacted and capped by `_clean` before it reaches the CLI,
    # so `_print_result` needs no scrub of its own -- just check it prints.
    failed = IngestResult(
        (
            SourceRun(
                "alpaca",
                FAILED,
                0,
                "2020-06-30",
                "KeyError: 'cik' | at: adapters/edgar_source.py:1632 in _holds_accession",
            ),
        )
    )
    _patched(monkeypatch, "ingest_session", failed)
    result = _invoke(secrets_set, ["ingest"])
    assert result.exit_code == 1
    assert (
        "KeyError: 'cik' | at: adapters/edgar_source.py:1632 in _holds_accession" in result.output
    )


def test_ingest_passes_source_and_dry_run(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    result = _invoke(secrets_set, ["ingest", "--source", "edgar", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert session.calls[0]["source"] == "edgar"
    assert session.calls[0]["dry_run"] is True


class _SourceRecorder:
    """Stands in for `EdgarFilingSource`, recording its keyword arguments."""

    def __init__(self) -> None:
        self.kwargs: list[dict[str, Any]] = []

    def __call__(self, settings: Settings, **kwargs: Any) -> object:
        self.kwargs.append(kwargs)
        return object()


def _statements_on(settings: Settings, on: bool = True) -> Settings:
    edgar = settings.edgar.model_copy(update={"statement_facts_enabled": on})
    return settings.model_copy(update={"edgar": edgar})


def test_ingest_statement_flags_reach_the_adapter_and_are_off_by_default(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    """#660: `--bulk-from-cache` builds the EDGAR source with `reuse_cached`
    and `--rebuild-statement-facts` reaches `ingest_session`; T78 runs
    `ingest --source edgar --bulk-from-cache` with the switch on."""
    built = _SourceRecorder()
    monkeypatch.setattr(cli, "EdgarFilingSource", built)
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    assert _invoke(secrets_set, ["ingest", "--source", "edgar"]).exit_code == 0
    assert built.kwargs[0]["reuse_cached"] is False
    assert session.calls[0]["rebuild_statement_facts"] is False

    on = _statements_on(secrets_set)
    args = ["ingest", "--source", "edgar", "--bulk-from-cache", "--rebuild-statement-facts"]
    result = _invoke(on, args)
    assert result.exit_code == 0, result.output
    assert built.kwargs[1]["reuse_cached"] is True
    assert session.calls[1]["rebuild_statement_facts"] is True
    assert session.calls[1]["filings"] is not None


@pytest.mark.parametrize(
    "args",
    [
        ["--rebuild-statement-facts"],  # the switch is off
        ["--source", "alpaca", "--bulk-from-cache"],
        ["--source", "alpaca", "--rebuild-statement-facts"],
        ["--backfill", "--since", "2016-01-04", "--bulk-from-cache"],
        ["--backfill", "--since", "2016-01-04", "--rebuild-statement-facts"],
    ],
)
def test_ingest_refuses_statement_flags_where_they_cannot_apply(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings, args: list[str]
) -> None:
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    filled = _patched(monkeypatch, "backfill", _ok("edgar"))
    off = args == ["--rebuild-statement-facts"]  # the switch is set off: on by default (T78)
    settings = _statements_on(secrets_set, on=not off)
    result = _invoke(settings, ["ingest", *args])
    assert result.exit_code == 2, result.output
    assert session.calls == [] and filled.calls == []


def test_ingest_refuses_an_unknown_source(secrets_set: Settings) -> None:
    result = _invoke(secrets_set, ["ingest", "--source", "yahoo"])
    assert result.exit_code == 2


def test_backfill_passes_since_and_source(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    filled = _patched(monkeypatch, "backfill", _ok("alpaca"))
    session = _patched(monkeypatch, "ingest_session", _ok("alpaca"))
    result = _invoke(
        secrets_set, ["ingest", "--backfill", "--since", "2016-01-04", "--source", "alpaca"]
    )
    assert result.exit_code == 0, result.output
    assert session.calls == []
    (call,) = filled.calls
    assert call["since"] == date(2016, 1, 4)
    assert call["source"] == "alpaca"


@pytest.mark.parametrize(
    "args",
    [
        ["ingest", "--backfill"],
        ["ingest", "--since", "2016-01-04"],
        ["ingest", "--backfill", "--since", "2016-01-04", "--dry-run"],
        ["ingest", "--backfill", "--since", "not-a-date"],
    ],
)
def test_inconsistent_backfill_flags_are_refused_before_any_work(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings, args: list[str]
) -> None:
    filled = _patched(monkeypatch, "backfill", _ok("alpaca"))
    session = _patched(monkeypatch, "ingest_session", _ok("alpaca"))
    result = _invoke(secrets_set, args)
    assert result.exit_code == 2
    assert filled.calls == [] and session.calls == []


# --- ingest --backfill --fill-holes (#831) -------------------------------------

HOLE = Hole("0001404912", "KKR", (date(2018, 8, 1), date(2018, 8, 31)), True)


class _FillRecorder:
    """Stands in for `fill_holes`, recording its arguments."""

    def __init__(self, runs: tuple[SourceRun, ...] = ()) -> None:
        self.runs = runs
        self.calls: list[dict[str, Any]] = []

    def __call__(self, settings: Settings, **kwargs: Any) -> HoleFill:
        self.calls.append(kwargs)
        dry = bool(kwargs.get("dry_run", False))
        return HoleFill((HOLE,) if dry else (), () if dry else self.runs, dry)


def _with_store(settings: Settings) -> Settings:
    Path(settings.store.path).touch()
    return settings


def test_a_fill_holes_dry_run_lists_the_holes_and_needs_no_secret(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fill = _FillRecorder()
    monkeypatch.setattr(cli, "fill_holes", fill)
    filled = _patched(monkeypatch, "backfill", _ok("alpaca"))
    args = ["ingest", "--backfill", "--since", "2016-01-01", "--source", "alpaca"]
    result = _invoke(_with_store(_settings(tmp_path)), [*args, "--fill-holes", "--dry-run"])
    assert result.exit_code == 0, result.output
    (call,) = fill.calls
    assert call["since"] == date(2016, 1, 1) and call["dry_run"] is True
    assert filled.calls == []
    assert "1 holes (security, month) over 1 securities" in result.output
    assert "0001404912 KKR: 2018-08 (1 months, 1 between its stored bars)" in result.output


def test_a_fill_holes_run_prints_each_month_and_exits_on_its_status(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    cursor = "holes;since=2016-01-01;through=2018-08-31"
    fill = _FillRecorder((SourceRun("alpaca", STALE, 0, cursor, "reference gap"),))
    monkeypatch.setattr(cli, "fill_holes", fill)
    args = ["ingest", "--backfill", "--since", "2016-01-01", "--source", "alpaca", "--fill-holes"]
    result = _invoke(_with_store(secrets_set), args)
    assert result.exit_code == 1
    (call,) = fill.calls
    assert isinstance(call["prices"], PriceSource) and "dry_run" not in call
    assert f"alpaca: stale, 0 rows, cursor {cursor}: reference gap" in result.output
    fill.runs = (SourceRun("alpaca", FILLED, 40, cursor, "holes of 1 names"),)
    assert _invoke(secrets_set, args).exit_code == 0


def test_a_fill_holes_dry_run_on_a_locked_store_says_so_and_lists_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    locked = SourceRun("alpaca", "locked", 0, "holes;since=2016-01-01", "store is locked")
    monkeypatch.setattr(cli, "fill_holes", lambda *_, **__: HoleFill((), (locked,), True))
    args = ["ingest", "--backfill", "--since", "2016-01-01", "--source", "alpaca"]
    result = _invoke(_with_store(_settings(tmp_path)), [*args, "--fill-holes", "--dry-run"])
    assert result.exit_code == 1
    assert "alpaca: locked" in result.output and "store is locked" in result.output
    assert "holes as of now" not in result.output


def test_a_real_fill_holes_run_needs_the_alpaca_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fill = _FillRecorder()
    monkeypatch.setattr(cli, "fill_holes", fill)
    args = ["ingest", "--backfill", "--since", "2016-01-01", "--source", "alpaca", "--fill-holes"]
    result = _invoke(_with_store(_settings(tmp_path)), args)
    assert result.exit_code == 2 and "ALPACA_API_KEY" in result.output
    assert fill.calls == []


@pytest.mark.parametrize(
    "args",
    [
        ["ingest", "--fill-holes", "--source", "alpaca"],
        ["ingest", "--backfill", "--since", "2016-01-01", "--fill-holes"],
        ["ingest", "--backfill", "--since", "2016-01-01", "--fill-holes", "--source", "edgar"],
    ],
)
def test_inconsistent_fill_holes_flags_are_refused_before_any_work(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings, args: list[str]
) -> None:
    fill = _FillRecorder()
    monkeypatch.setattr(cli, "fill_holes", fill)
    result = _invoke(_with_store(secrets_set), args)
    assert result.exit_code == 2
    assert fill.calls == []


def test_named_securities_reach_the_fill_and_its_drops_are_printed(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    # #876: --security repeats or takes ids joined by commas.
    cursor = "holes;since=2016-01-01;through=2018-08-31"
    calls: list[dict[str, Any]] = []

    def fill(settings: Settings, **kwargs: Any) -> HoleFill:
        calls.append(kwargs)
        dry = bool(kwargs.get("dry_run", False))
        runs = () if dry else (SourceRun("alpaca", FILLED, 40, cursor, "holes of 1 names"),)
        named = (NamedSecurity("0000000002", NO_HOLE),)
        return HoleFill((HOLE,) if dry else (), runs, dry, ((UNASSIGNED, 3),), named)

    monkeypatch.setattr(cli, "fill_holes", fill)
    args = [
        *["ingest", "--backfill", "--since", "2016-01-01", "--source", "alpaca", "--fill-holes"],
        *["--security", "0001404912,0000000002", "--security", " 0000000003 "],
    ]
    dry = _invoke(_with_store(secrets_set), [*args, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    real = _invoke(secrets_set, args)
    assert real.exit_code == 0, real.output
    assert [c["securities"] for c in calls] == [["0001404912", "0000000002", "0000000003"]] * 2
    note = f"3 holes the resolver cannot assign, not fetched (3 {UNASSIGNED})"
    assert f"; {note}" in dry.output
    assert f"alpaca: {note}" in real.output
    for output in (dry.output, real.output):
        assert f"  0000000002: not fetched, {NO_HOLE}" in output


@pytest.mark.parametrize(
    "extra",
    [
        ["--security", "0000000002"],  # no --fill-holes
        ["--fill-holes", "--source", "alpaca", "--security", "0000000002,"],  # a blank id
    ],
)
def test_bad_security_flags_are_refused_before_any_work(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings, extra: list[str]
) -> None:
    fill = _FillRecorder()
    monkeypatch.setattr(cli, "fill_holes", fill)
    filled = _patched(monkeypatch, "backfill", _ok("alpaca"))
    result = _invoke(
        _with_store(secrets_set), ["ingest", "--backfill", "--since", "2016-01-01", *extra]
    )
    assert result.exit_code == 2
    assert fill.calls == [] and filled.calls == []


def test_a_fill_holes_run_without_a_store_fails(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    fill = _FillRecorder()
    monkeypatch.setattr(cli, "fill_holes", fill)
    args = ["ingest", "--backfill", "--since", "2016-01-01", "--source", "alpaca", "--fill-holes"]
    result = _invoke(secrets_set, [*args, "--dry-run"])
    assert result.exit_code == 1 and "no store" in result.output
    assert fill.calls == []


def test_missing_edgar_user_agent_exits_non_zero_with_no_secret_in_the_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(
        tmp_path, alpaca_api_key="alpaca-key-value", alpaca_api_secret="alpaca-secret-value"
    )
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    result = _invoke(settings, ["ingest"])
    assert result.exit_code == 2
    assert "SEC_EDGAR_USER_AGENT" in result.output
    assert "alpaca-key-value" not in result.output
    assert "alpaca-secret-value" not in result.output
    assert session.calls == []
    assert not Path(settings.store.path).exists()


def test_missing_alpaca_keys_refuse_a_price_run_but_not_an_edgar_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path, sec_edgar_user_agent=USER_AGENT)
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    refused = _invoke(settings, ["ingest", "--source", "alpaca"])
    assert refused.exit_code == 2
    assert "ALPACA_API_KEY" in refused.output
    assert USER_AGENT not in refused.output
    assert session.calls == []
    assert _invoke(settings, ["ingest", "--source", "edgar"]).exit_code == 0


def test_ingest_builds_the_edgar_source_from_settings_with_the_injected_client(
    secrets_set: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real `ingest_session` over a real `EdgarFilingSource`: every request
    goes through the injected transport with the declared User-Agent; SEC
    refusing it fails the EDGAR chunk, which writes only its failed run row,
    and the command exits non-zero without echoing the User-Agent. The one
    `403` wait (`edgar.rate_limit_wait_seconds`, #554) is recorded, not slept."""
    slept: list[float] = []
    monkeypatch.setattr(edgar_raw.time, "sleep", slept.append)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(403, text="Forbidden")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = _invoke(secrets_set, ["ingest", "--source", "edgar"], edgar_client=client)
    assert result.exit_code == 1, result.output
    assert seen, "the EDGAR source made no request through the injected client"
    assert {r.headers["User-Agent"] for r in seen} == {USER_AGENT}
    assert all(r.url.host.endswith("sec.gov") for r in seen)
    assert USER_AGENT not in result.output
    assert "failed" in result.output
    with duckdb.connect(secrets_set.store.path, read_only=True) as conn:
        rows = conn.execute("SELECT source, status FROM ingestion_runs").fetchall()
        assert rows == [("edgar", "failed")]
        assert conn.execute("SELECT count(*) FROM securities").fetchone() == (0,)
    assert secrets_set.edgar.rate_limit_wait_seconds in slept


# --- the price source the CLI builds -------------------------------------------


def test_store_price_source_resolves_tickers_from_the_listings_known_when_first_called(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    """The listings come from the store at the first fetch, i.e. after the
    EDGAR chunk committed, never from before the run."""
    settings = _settings(tmp_path, store=fixture_store_path)
    fetched: list[tuple[list[str], date, date]] = []

    def fetch_bars(symbols: list[str], start: date, end: date) -> dict[str, Any]:
        fetched.append((symbols, start, end))
        return {"feed": "sip", "bars": {}}

    source = cli.StorePriceSource(
        settings, clock=lambda: T_END, fetch_bars=fetch_bars, fetch_actions=lambda *a: {}
    )
    assert source.bars(["SEC_SPY"], date(2020, 6, 30), date(2020, 6, 30)) == []
    ((symbols, _, _),) = fetched
    assert symbols == ["SPY"]


def test_store_price_source_default_fetch_passes_asof_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1314: the default bars fetcher hands `asof` to `alpaca_raw`."""
    settings = _settings(tmp_path)
    sent: list[date | None] = []

    def daily_bars(
        symbols: list[str], start: date, end: date, *, asof: date | None = None, **_: Any
    ) -> dict[str, Any]:
        sent.append(asof)
        return {"feed": "sip", "bars": {}}

    monkeypatch.setattr(cli.alpaca_raw, "daily_bars", daily_bars)
    source = cli.StorePriceSource(settings, clock=lambda: T_END)
    day = date(2017, 6, 1)
    source._fetch_bars(["VAL"], day, day, asof=day)
    source._fetch_bars(["VAL"], day, day)
    assert sent == [day, None]


def test_store_price_source_reports_what_its_resolver_left_out(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    settings = _settings(tmp_path, store=fixture_store_path)
    source = cli.StorePriceSource(
        settings,
        clock=lambda: T_END,
        fetch_bars=lambda *a: {"feed": "sip", "bars": {}},
        fetch_actions=lambda *a: {},
    )
    assert source.resolution_summary() == ""
    source.bars(["SEC_SPY"], date(2020, 6, 30), date(2020, 6, 30))
    summary = source.resolution_summary()
    assert summary.startswith("resolver left out ")
    assert "; 0 co-registrant and 0 disputed claims on another" in summary  # #793: evidence read
    assert summary.endswith("; 0 bar rows unresolved")


def test_store_price_source_reads_nothing_until_called(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    cli.StorePriceSource(settings, clock=lambda: NOW)
    assert not Path(settings.store.path).exists()


# --- health --------------------------------------------------------------------


def test_health_prints_the_report_and_check_passes_on_the_fixture_store(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    settings = _settings(tmp_path, store=fixture_store_path)
    plain = _invoke(settings, ["health"], clock=lambda: T_END)
    assert plain.exit_code == 0, plain.output
    for heading in ("edgar", "alpaca", "coverage", "delisted", "integrity"):
        assert heading in plain.output.lower()
    checked = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert checked.exit_code == 0, checked.output


def test_health_check_exits_non_zero_naming_the_failed_rule(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    settings = _settings(tmp_path, store=fixture_store_path)
    saturday = date(2020, 6, 27)
    with open_for_write(settings) as conn:
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": "SEC_SPY",
                "session": saturday,
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "known_at": T_END - timedelta(days=1),
                "ingested_at": T_END - timedelta(days=1),
                "source": "fixture",
                "provenance": "bar",
            },
        )
    plain = _invoke(settings, ["health"], clock=lambda: T_END)
    assert plain.exit_code == 0  # without --check the report is informational
    checked = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert checked.exit_code == 1
    assert "bars_on_sessions" in checked.output


def test_health_prints_the_price_jump_review_list(tmp_path: Path, fixture_store_path: Path) -> None:
    """#787: an unexplained jump is listed for the owner; it is no integrity failure."""
    settings = _settings(tmp_path, store=fixture_store_path)
    plain = _invoke(settings, ["health"], clock=lambda: T_END)
    assert "price jumps: 0 to review, 0 in all" in plain.output
    session, revised = date(2019, 3, 15), datetime(2019, 3, 18, 12, 0, tzinfo=UTC)
    with open_for_write(settings) as conn:
        (bar,) = (
            conn.execute(
                "SELECT * FROM prices_daily WHERE security_id = 'SEC_DUAL_A' AND session = ?",
                [session],
            )
            .pl()
            .iter_rows(named=True)
        )
        insert_row(
            conn,
            "prices_daily",
            bar
            | {
                "close": bar["close"] * 3,
                "high": bar["close"] * 3,
                "known_at": revised,
                "ingested_at": revised,
            },
        )
    checked = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert checked.exit_code == 0, checked.output
    assert "price jumps: 2 to review, 2 in all" in checked.output
    assert "SEC_DUAL_A@2019-03-15" in checked.output
    assert "SEC_DUAL_A@2019-03-18" in checked.output
    capped = _invoke(settings, ["health", "--jumps-before", "2019-03-18"], clock=lambda: T_END)
    assert "price jumps before 2019-03-18: 1 to review, 1 in all" in capped.output
    assert "SEC_DUAL_A@2019-03-18" not in capped.output
    bad = _invoke(settings, ["health", "--jumps-before", "2019-13-01"], clock=lambda: T_END)
    assert bad.exit_code == 2


def test_health_lists_accepted_same_day_pairs_and_check_passes(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    """#855: an owner-accepted same-day pair passes `--check` and is listed."""
    sid, day = "0002079013:share-rights", date(2019, 6, 3)
    known = datetime(2019, 6, 3, 21, 0, tzinfo=UTC)
    plain_settings = _settings(tmp_path, store=fixture_store_path)
    with open_for_write(plain_settings) as conn:
        for ticker in ("HACAR", "HCACR"):
            insert_row(
                conn,
                "listings",
                {
                    "security_id": sid,
                    "ticker": ticker,
                    "exchange": "NASDAQ",
                    "class_title": "Rights",
                    "valid_from": day,
                    "known_at": known,
                    "ingested_at": known,
                    "source": "fixture",
                    "provenance": "filing",
                },
            )
    failing = _invoke(plain_settings, ["health", "--check"], clock=lambda: T_END)
    assert failing.exit_code == 1
    assert "non_overlapping_listings" in failing.output
    assert "accepted same-day pairs: 0" in failing.output
    accepted = _settings(
        tmp_path,
        store=fixture_store_path,
        universe={"accepted_same_day_pairs": [f"{sid}@{day.isoformat()}"]},
    )
    checked = _invoke(accepted, ["health", "--check"], clock=lambda: T_END)
    assert checked.exit_code == 0, checked.output
    assert "accepted same-day pairs: 1" in checked.output
    assert f"  {sid}@2019-06-03 HACAR/HCACR" in checked.output


def _shares_fact(
    conn: duckdb.DuckDBPyConnection, security_id: str, as_of: date, value: float
) -> None:
    known = datetime(as_of.year, as_of.month, as_of.day, 21, 0, tzinfo=UTC) + timedelta(days=1)
    insert_row(
        conn,
        "facts",
        {
            "security_id": security_id,
            "fact_name": "shares_outstanding",
            "as_of_date": as_of,
            "class_member": "",
            "value": value,
            "filing_accession": f"cli-{security_id}-{as_of.isoformat()}",
            "known_at": known,
            "ingested_at": known,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def test_health_prints_the_shares_outlier_review_list(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    """#845: out-of-line shares facts are listed, a zero one too (no ratio)."""
    settings = _settings(tmp_path, store=fixture_store_path)
    plain = _invoke(settings, ["health"], clock=lambda: T_END)
    assert "shares outliers: 0 to review, 0 in all" in plain.output
    with open_for_write(settings) as conn:
        _shares_fact(conn, "SEC_SPLIT_BETWEEN", date(2019, 2, 28), 15e9)
        _shares_fact(conn, "SEC_SPLIT_BETWEEN", date(2019, 5, 31), 0)
        _shares_fact(conn, "SEC_ZERO_FIRST", date(2019, 1, 31), 0)
    checked = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert checked.exit_code == 0, checked.output
    assert "shares outliers: 3 to review, 3 in all" in checked.output
    assert "SEC_SPLIT_BETWEEN@2019-02-28 15000000000 (x1000 over 5000000" in checked.output
    assert "SEC_SPLIT_BETWEEN@2019-05-31 0 (not a share count" in checked.output
    assert "SEC_ZERO_FIRST@2019-01-31 0 (not a share count" in checked.output
    assert "integrity:" in checked.output
    capped = _invoke(settings, ["health", "--jumps-before", "2019-03-01"], clock=lambda: T_END)
    assert "shares outliers before 2019-03-01: 2 to review, 2 in all" in capped.output


def test_health_warns_when_the_last_edgar_run_reports_quarantined_accessions(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    settings = _settings(tmp_path, store=fixture_store_path)
    _run_row(settings, "r1", "edgar", "ok", T_END - timedelta(days=2), "x; quarantined: 0")
    _run_row(settings, "r2", "edgar", "ok", T_END - timedelta(days=1), "x; quarantined: 2")
    result = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert result.exit_code == 0, result.output  # a warning, not a failed rule
    assert "warning" in result.output.lower()
    assert "2 quarantined" in result.output


def test_health_does_not_warn_when_the_last_edgar_run_quarantined_nothing(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    settings = _settings(tmp_path, store=fixture_store_path)
    _run_row(settings, "r1", "edgar", "ok", T_END - timedelta(days=2), "x; quarantined: 4")
    _run_row(settings, "r2", "edgar", "ok", T_END - timedelta(days=1), "x; quarantined: 0")
    result = _invoke(settings, ["health"], clock=lambda: T_END)
    assert result.exit_code == 0
    assert "quarantined" not in result.output.lower().replace("quarantined: 0", "")


def test_health_with_no_store_exits_non_zero(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    result = _invoke(settings, ["health", "--check"])
    assert result.exit_code == 1
    assert "no store" in result.output.lower()
    assert not Path(settings.store.path).exists()


def test_health_on_a_store_with_no_schema_exits_non_zero(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    duckdb.connect(settings.store.path).close()
    result = _invoke(settings, ["health"])
    assert result.exit_code == 1
    assert "no usable schema" in result.output


def test_health_on_a_locked_store_reports_busy(tmp_path: Path, fixture_store_path: Path) -> None:
    settings = _settings(tmp_path, store=fixture_store_path)
    with open_for_write(settings):
        result = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert result.exit_code == 1
    assert "busy" in result.output.lower()


# --- dashboard -----------------------------------------------------------------


def test_dashboard_launches_streamlit_on_the_app_bound_to_localhost(tmp_path: Path) -> None:
    launched: list[list[str]] = []

    def launcher(argv: list[str]) -> int:
        launched.append(argv)
        return 0

    result = _invoke(_settings(tmp_path), ["dashboard"], launcher=launcher)
    assert result.exit_code == 0, result.output
    ((argv),) = launched
    assert argv[1:4] == ["-m", "streamlit", "run"]
    app_path = Path(argv[4])
    assert app_path.name == "app.py" and app_path.parent.name == "dashboard"
    assert app_path.is_file()
    flags = dict(zip(argv[5::2], argv[6::2], strict=True))
    assert flags["--server.address"] == "localhost"
    assert flags["--browser.gatherUsageStats"] == "false"


def test_dashboard_exit_code_is_streamlits(tmp_path: Path) -> None:
    result = _invoke(_settings(tmp_path), ["dashboard"], launcher=lambda argv: 3)
    assert result.exit_code == 3


# --- export --------------------------------------------------------------------


def _tables(path: Path) -> list[str]:
    with duckdb.connect(str(path), read_only=True) as conn:
        return sorted(
            row[0]
            for row in conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        )


def test_export_writes_one_parquet_file_per_table(tmp_path: Path, fixture_store_path: Path) -> None:
    settings = _settings(tmp_path, store=fixture_store_path)
    out = tmp_path / "export"
    result = _invoke(settings, ["export", str(out)])
    assert result.exit_code == 0, result.output
    tables = _tables(fixture_store_path)
    assert {"prices_daily", "listings", "ingestion_runs", "schema_version"} <= set(tables)
    assert sorted(p.stem for p in out.glob("*.parquet")) == tables
    with duckdb.connect(str(fixture_store_path), read_only=True) as conn:
        for table in tables:
            (count,) = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone() or (0,)
            assert pq.read_table(out / f"{table}.parquet").num_rows == count, table


def test_export_keeps_known_at_as_utc_instants(tmp_path: Path, fixture_store_path: Path) -> None:
    settings = _settings(tmp_path, store=fixture_store_path)
    out = tmp_path / "export"
    assert _invoke(settings, ["export", str(out)]).exit_code == 0
    column = pq.read_table(out / "prices_daily.parquet").schema.field("known_at")
    assert str(column.type.tz).upper() in {"UTC", "+00:00", "ETC/UTC"}


def test_export_refuses_to_overwrite(tmp_path: Path, fixture_store_path: Path) -> None:
    settings = _settings(tmp_path, store=fixture_store_path)
    out = tmp_path / "export"
    out.mkdir()
    (out / "listings.parquet").write_text("keep me")
    result = _invoke(settings, ["export", str(out)])
    assert result.exit_code == 1
    assert (out / "listings.parquet").read_text() == "keep me"


def test_export_refuses_a_dangling_symlink_and_a_file_as_the_directory(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    settings = _settings(tmp_path, store=fixture_store_path)
    out = tmp_path / "export"
    out.mkdir()
    (out / "listings.parquet").symlink_to(tmp_path / "elsewhere.parquet")
    assert _invoke(settings, ["export", str(out)]).exit_code == 1
    assert not (tmp_path / "elsewhere.parquet").exists()
    a_file = tmp_path / "a_file"
    a_file.write_text("x")
    result = _invoke(settings, ["export", str(a_file)])
    assert result.exit_code == 1
    assert "not a directory" in result.output


def test_export_with_no_store_exits_non_zero(tmp_path: Path) -> None:
    result = _invoke(_settings(tmp_path), ["export", str(tmp_path / "export")])
    assert result.exit_code == 1
    assert "no store" in result.output.lower()


# --- the console script --------------------------------------------------------


def test_console_script_points_at_main() -> None:
    import tomllib

    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    scripts = tomllib.loads(pyproject.read_text())["project"]["scripts"]
    assert scripts["tradepartner"] == "tradepartner.cli:main"
    assert callable(cli.main)
