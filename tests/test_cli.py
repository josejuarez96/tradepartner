"""The `tradepartner` command (Phase 2 plan T19; spec reqs 9, 11, 12 and "CLI").

`make_app` takes every edge the tests need to replace (settings, clock, the
EDGAR HTTP client, the price source, the dashboard launcher), so nothing here
touches the network, the owner's store or a browser.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pyarrow.parquet as pq
import pytest
from typer.testing import CliRunner

from tradepartner import cli, health
from tradepartner.adapters import alpaca_raw, edgar_raw
from tradepartner.adapters.edgar_source import EdgarFilingSource
from tradepartner.adapters.fixture_rates import FixtureRateSource
from tradepartner.adapters.prices import PriceSource
from tradepartner.adapters.rates import RateObservation
from tradepartner.backfill import FILLED, NO_HOLE, UNASSIGNED, Hole, HoleFill, NamedSecurity
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.ingest import FAILED, OK, STALE, IngestResult, RestampResult, SourceRun
from tradepartner.store import journal, registry
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.dividend_reviews import ReviewedGroup
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


def test_fred_cli_refuses_missing_key_without_fetch(tmp_path: Path) -> None:
    """The key is checked before a FRED request or store write."""
    settings = _settings(tmp_path)
    result = _invoke(
        settings, ["ingest", "--source", "fred", "--backfill", "--since", "2020-06-29"]
    )
    assert result.exit_code == 2
    assert "FRED_API_KEY" in result.output
    assert not Path(settings.store.path).exists()


def test_fred_cli_backfill_uses_injected_source(tmp_path: Path) -> None:
    """The CLI routes FRED backfill through the rates ingest only."""
    settings = _settings(tmp_path, fred_api_key="fixture-secret")
    rates = FixtureRateSource([RateObservation("DTB3", date(2020, 6, 29), 0.1, date(2020, 6, 30))])
    result = _invoke(
        settings,
        ["ingest", "--source", "fred", "--backfill", "--since", "2020-06-29"],
        rate_source=lambda _settings: rates,
        clock=lambda: datetime(2020, 7, 3, 22, tzinfo=UTC),
    )
    assert result.exit_code == 0, result.output
    with duckdb.connect(str(settings.store.path), read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM rates").fetchone() == (1,)


def test_all_with_blank_fred_key_runs_legacy_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unset FRED key cannot stop the scheduled price ingest."""
    settings = _settings(
        tmp_path,
        sec_edgar_user_agent=USER_AGENT,
        alpaca_api_key="test-key-id",
        alpaca_api_secret="test-key-secret",
    )
    session = _patched(monkeypatch, "ingest_session", _ok("edgar", "alpaca"))
    result = _invoke(settings, ["ingest"])
    assert result.exit_code == 0, result.output
    assert session.calls and session.calls[0]["rates"] is None
    assert "FRED_API_KEY" in result.output


def test_all_without_seeded_rate_runs_legacy_sources(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    """An unseeded rate series asks for backfill without failing all."""
    session = _patched(monkeypatch, "ingest_session", _ok("edgar", "alpaca"))
    result = _invoke(secrets_set, ["ingest"])
    assert result.exit_code == 0, result.output
    assert session.calls and session.calls[0]["rates"] is None
    assert "--source fred --backfill" in result.output


def test_all_backfill_appends_fred_run(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    """The all-source backfill routes the configured FRED series too."""
    legacy = _patched(monkeypatch, "backfill", _ok("edgar", "alpaca"))
    rates = FixtureRateSource([RateObservation("DTB3", date(2020, 6, 29), 0.1, date(2020, 6, 30))])
    result = _invoke(
        secrets_set,
        ["ingest", "--backfill", "--since", "2020-06-29"],
        rate_source=lambda _settings: rates,
        clock=lambda: datetime(2020, 7, 3, 22, tzinfo=UTC),
    )
    assert result.exit_code == 0, result.output
    assert legacy.calls
    assert "fred" in result.output
    with duckdb.connect(str(secrets_set.store.path), read_only=True) as conn:
        assert conn.execute(
            "SELECT count(*) FROM ingestion_runs WHERE source='fred'"
        ).fetchone() == (1,)


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
        fred_api_key="test-fred-key",
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


def test_openfigi_is_explicit_only(monkeypatch: pytest.MonkeyPatch, secrets_set: Settings) -> None:
    secrets_set.openfigi.enabled = True
    figi = _patched(monkeypatch, "ingest_openfigi", _ok("openfigi"))
    session = _patched(monkeypatch, "ingest_session", _ok("edgar", "alpaca"))
    explicit = _invoke(secrets_set, ["ingest", "--source", "openfigi"])
    assert explicit.exit_code == 0, explicit.output
    assert len(figi.calls) == 1
    all_run = _invoke(secrets_set, ["ingest", "--source", "all"])
    assert all_run.exit_code == 0, all_run.output
    assert len(figi.calls) == 1
    assert session.calls[-1]["source"] == "all"


def test_openfigi_disabled_by_default(tmp_path: Path) -> None:
    result = _invoke(_settings(tmp_path), ["ingest", "--source", "openfigi"])
    assert result.exit_code == 2
    assert "openfigi.enabled" in result.output


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


def test_ingest_filing_event_flag_and_configured_forms(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    built = _SourceRecorder()
    monkeypatch.setattr(cli, "EdgarFilingSource", built)
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    edgar = secrets_set.edgar.model_copy(
        update={"filing_events_enabled": True, "event_forms": ["8-K", "10-Q"]}
    )
    settings = secrets_set.model_copy(update={"edgar": edgar})
    result = _invoke(settings, ["ingest", "--source", "edgar", "--rebuild-filing-events"])
    assert result.exit_code == 0, result.output
    assert built.kwargs[0]["event_forms"] == ["8-K", "10-Q"]
    assert session.calls[0]["rebuild_filing_events"] is True


@pytest.mark.parametrize(
    "args",
    [
        ["--rebuild-filing-events"],
        ["--source", "alpaca", "--rebuild-filing-events"],
        ["--backfill", "--since", "2016-01-04", "--rebuild-filing-events"],
    ],
)
def test_ingest_refuses_filing_event_rebuild_outside_enabled_session(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings, args: list[str]
) -> None:
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    result = _invoke(secrets_set, ["ingest", *args])
    assert result.exit_code == 2, result.output
    assert session.calls == []


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


def test_ingest_rebuild_classifications_reaches_the_session_and_is_off_by_default(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings
) -> None:
    """#1541 (T182): needs no switch, only the edgar source."""
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    assert _invoke(secrets_set, ["ingest", "--source", "edgar"]).exit_code == 0
    assert session.calls[0]["rebuild_classifications"] is False
    args = ["ingest", "--source", "edgar", "--rebuild-classifications"]
    assert _invoke(secrets_set, args).exit_code == 0
    assert session.calls[1]["rebuild_classifications"] is True


@pytest.mark.parametrize(
    "args",
    [
        ["--source", "alpaca", "--rebuild-classifications"],
        ["--backfill", "--since", "2016-01-04", "--rebuild-classifications"],
    ],
)
def test_ingest_refuses_rebuild_classifications_where_it_cannot_apply(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings, args: list[str]
) -> None:
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    filled = _patched(monkeypatch, "backfill", _ok("edgar"))
    result = _invoke(secrets_set, ["ingest", *args])
    assert result.exit_code == 2, result.output
    assert session.calls == [] and filled.calls == []


def _manifest(
    tmp_path: Path,
    header: str = "cik,accession,old_stamp,new_stamp,method,evidence",
    name: str = "manifest.csv",
) -> Path:
    path = tmp_path / name
    path.write_text(
        f"{header}\n0000000001,0000000001-19-000001,2019-03-01T16:30:00+00:00,"
        "2019-03-01T21:30:00+00:00,shift,class early\n"
    )
    return path


def test_ingest_restamp_from_reads_the_manifest_and_prints_refused_ciks(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings, tmp_path: Path
) -> None:
    """#1382 (T166b): the manifest reaches `ingest_session` as changes, and
    the restamp's counts and every refused CIK are printed."""
    calls: list[dict[str, Any]] = []

    def session(settings: Settings, **kwargs: Any) -> IngestResult:
        calls.append(kwargs)
        kwargs["on_restamp"](
            RestampResult(moved={"securities": 2}, refused={"0000000002": "listings"}, changes=1)
        )
        return _ok("edgar")

    monkeypatch.setattr(cli, "ingest_session", session)
    path = _manifest(tmp_path)
    result = _invoke(secrets_set, ["ingest", "--source", "edgar", "--restamp-from", str(path)])
    assert result.exit_code == 0, result.output
    (change,) = calls[0]["restamp"]
    assert change.cik == "0000000001" and change.changed
    assert "moved securities 2; refused 1" in result.output
    assert "restamp refused 0000000002: listings" in result.output


@pytest.mark.parametrize(
    "args",
    [
        [],  # --source all
        ["--source", "alpaca"],
        ["--source", "edgar", "--backfill", "--since", "2016-01-04"],
    ],
)
def test_ingest_refuses_restamp_from_outside_an_edgar_session(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings, tmp_path: Path, args: list[str]
) -> None:
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    filled = _patched(monkeypatch, "backfill", _ok("edgar"))
    path = _manifest(tmp_path)
    result = _invoke(secrets_set, ["ingest", *args, "--restamp-from", str(path)])
    assert result.exit_code == 2, result.output
    assert session.calls == [] and filled.calls == []


def test_ingest_refuses_a_manifest_missing_a_column(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings, tmp_path: Path
) -> None:
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    path = _manifest(tmp_path, header="cik,accession,old_stamp,stamp,method,evidence")
    result = _invoke(secrets_set, ["ingest", "--source", "edgar", "--restamp-from", str(path)])
    assert result.exit_code == 2, result.output
    assert "new_stamp" in result.output
    assert session.calls == []


#: The cache driver's manifest name for release `restamp-x` (T166a).
_RELEASED = "v1.restamp-x.manifest.csv"


def _precheck_file(tmp_path: Path, **fields: Any) -> Path:
    path = tmp_path / "precheck.restamp-x.json"
    data: dict[str, Any] = {
        "release": "restamp-x",
        "window_first_session": "2026-10-12",
        "pass": True,
        "instants": ["2026-10-09T20:00:00+00:00"],
        "journal_high_water": "2026-10-09T21:00:00+00:00",
        "made_at": "2026-10-12T13:00:00+00:00",
    }
    data.update(fields)
    path.write_text(json.dumps({k: v for k, v in data.items() if v is not ...}))
    return path


def test_ingest_restamp_window_earlier_ok_hands_the_precheck_and_release_on(
    monkeypatch: pytest.MonkeyPatch, secrets_set: Settings, tmp_path: Path
) -> None:
    """#1497 (T166e): the pre-check file reaches `ingest_session` parsed,
    with the release read from the manifest's name; whether it allows the
    restamp is decided in the store transaction, not here."""
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    manifest = _manifest(tmp_path, name=_RELEASED)
    precheck = _precheck_file(tmp_path)
    args = ["ingest", "--source", "edgar", "--restamp-from", str(manifest)]
    result = _invoke(secrets_set, [*args, "--restamp-window-earlier-ok", str(precheck)])
    assert result.exit_code == 0, result.output
    ok = session.calls[0]["restamp_window_ok"]
    assert ok.release == ok.precheck.release == "restamp-x" and ok.precheck.passed
    assert ok.precheck.window_first_session == date(2026, 10, 12)
    assert ok.precheck.instants == (datetime(2026, 10, 9, 20, tzinfo=UTC),)
    assert ok.precheck.journal_high_water == datetime(2026, 10, 9, 21, tzinfo=UTC)

    result = _invoke(secrets_set, args)
    assert result.exit_code == 0, result.output
    assert session.calls[1]["restamp_window_ok"] is None


@pytest.mark.parametrize(
    ("manifest_name", "fields", "with_manifest", "why"),
    [
        (_RELEASED, {}, False, "needs --restamp-from"),
        ("manifest.csv", {}, True, "v1.<release>.manifest.csv"),
        (_RELEASED, {"instants": ...}, True, "instants"),
        (_RELEASED, {"pass": "yes"}, True, "pass is not a JSON boolean"),
        (_RELEASED, {"instants": ["2026-10-09T20:00:00"]}, True, "tz-aware"),
    ],
    ids=["no-manifest", "unnamed-manifest", "missing-field", "pass-not-bool", "naive-instant"],
)
def test_ingest_refuses_an_unusable_restamp_window_earlier_ok(
    monkeypatch: pytest.MonkeyPatch,
    secrets_set: Settings,
    tmp_path: Path,
    manifest_name: str,
    fields: dict[str, Any],
    with_manifest: bool,
    why: str,
) -> None:
    session = _patched(monkeypatch, "ingest_session", _ok("edgar"))
    manifest = _manifest(tmp_path, name=manifest_name)
    precheck = _precheck_file(tmp_path, **fields)
    args = ["ingest", "--source", "edgar", "--restamp-window-earlier-ok", str(precheck)]
    if with_manifest:
        args += ["--restamp-from", str(manifest)]
    result = _invoke(secrets_set, args)
    assert result.exit_code == 2, result.output
    assert why in result.output
    assert session.calls == []


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
    assert "listed with no bars after a form 25: 0" in plain.output.lower()
    checked = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert checked.exit_code == 0, checked.output


def test_health_prints_the_figi_types_block_and_fails_only_with_the_threshold(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    """#1713, T189c: the block names a `common` class OpenFIGI calls a note;
    unset `openfigi.max_disagreements` reports it and `--check` passes."""
    settings = _settings(tmp_path, store=fixture_store_path)
    with open_for_write(settings) as conn:
        insert_row(
            conn,
            "openfigi_types",
            {
                "security_id": "SEC_FACTS_RESTATED",
                "ticker": "FRST",
                "figi": "BBG1",
                "composite_figi": "BBG1",
                "share_class_figi": "BBG2",
                "security_type": "Medium Term Note",
                "security_type2": "Note",
                "market_sector": "Corp",
                "name": "FRST",
                "exch_code": "US",
                "mapped": True,
                "known_at": T_END - timedelta(days=1),
                "ingested_at": T_END - timedelta(days=1),
                "provenance": "openfigi",
                "source": "openfigi",
            },
        )
    plain = _invoke(settings, ["health"], clock=lambda: T_END)
    assert plain.exit_code == 0, plain.output
    out = plain.output.lower()
    assert "figi types:" in out
    assert "medium term note" in out
    assert "figi_types_disagreements" in out
    assert _invoke(settings, ["health", "--check"], clock=lambda: T_END).exit_code == 0
    gated = _settings(tmp_path, store=fixture_store_path, openfigi={"max_disagreements": 0})
    failed = _invoke(gated, ["health", "--check"], clock=lambda: T_END)
    assert failed.exit_code == 1
    assert "figi_types_disagreements" in failed.output


def test_health_check_enforces_rate_stamp_and_passes_fixture(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    """The rates stamp rule is enforced by the CLI's --check exit code."""
    settings = _settings(tmp_path, store=fixture_store_path)
    passing = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert passing.exit_code == 0, passing.output
    with open_for_write(settings) as conn:
        conn.execute(
            "UPDATE rates SET known_at = known_at - INTERVAL '1 hour' "
            "WHERE observation_date = '2020-06-26'"
        )
    failed = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert failed.exit_code == 1
    assert "rates_known_at_from_vintage" in failed.output


def test_health_reports_the_coverage_block_not_computed_by_default(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    """#1541: without `--coverage` and with `coverage.check_enabled` off the
    block is not computed, and its rule says so, never "pass"."""
    settings = _settings(tmp_path, store=fixture_store_path)
    result = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert result.exit_code == 0, result.output
    assert "coverage reference: not computed (run with --coverage)" in result.output
    assert "  coverage_reference_names: not computed (run with --coverage)" in result.output


def test_health_coverage_is_report_only_with_the_default_off(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    """The default list names real CIKs the fixture store lacks: `--coverage`
    lists them and `--check` still exits 0 while `check_enabled` is off."""
    settings = _settings(tmp_path, store=fixture_store_path)
    result = _invoke(settings, ["health", "--coverage", "--check"], clock=lambda: T_END)
    assert result.exit_code == 0, result.output
    assert "coverage reference (report-only): 2 sample sessions" in result.output
    assert "  2019-12-31: 1 members; 30 of 30 reference names missing;" in result.output
    assert "  missing 2019-12-31 0000051143:IBM (-): no_security" in result.output
    assert (
        "  coverage_reference_names: 60 missing (report-only): coverage.check_enabled is off"
        in (result.output)
    )


def test_health_check_fails_coverage_reference_names_only_with_check_enabled(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    pre9 = "CIK0001000019:PRE9"
    enforced = _settings(
        tmp_path,
        store=fixture_store_path,
        coverage={"reference_names": [pre9], "check_enabled": True},
    )
    failed = _invoke(enforced, ["health", "--check"], clock=lambda: T_END)
    assert failed.exit_code == 1
    assert "health check failed: coverage_reference_names" in failed.output
    assert "  coverage_reference_names: fail (2)" in failed.output
    refused = _invoke(enforced, ["health", "--no-coverage", "--check"], clock=lambda: T_END)
    assert refused.exit_code == 1
    assert refused.output.strip() == (
        "--no-coverage cannot be used with --check while coverage.check_enabled is on "
        "or coverage.index_max_missing is set"
    )
    skipped = _invoke(enforced, ["health", "--no-coverage"], clock=lambda: T_END)
    assert skipped.exit_code == 0, skipped.output
    assert "coverage reference: not computed" in skipped.output
    assert (
        "  missing 2019-12-31 CIK0001000019:PRE9 (SEC_STATIC_PRE2019): "
        "rule exchange, not_listed" in failed.output
    )
    report_only = _settings(
        tmp_path, store=fixture_store_path, coverage={"reference_names": [pre9]}
    )
    plain = _invoke(report_only, ["health", "--coverage", "--check"], clock=lambda: T_END)
    assert plain.exit_code == 0, plain.output
    off_skipped_check = _invoke(
        report_only, ["health", "--no-coverage", "--check"], clock=lambda: T_END
    )
    assert off_skipped_check.exit_code == 0, off_skipped_check.output
    assert "coverage reference: not computed" in off_skipped_check.output
    off_skipped_plain = _invoke(report_only, ["health", "--no-coverage"], clock=lambda: T_END)
    assert off_skipped_plain.exit_code == 0, off_skipped_plain.output
    listed = _settings(
        tmp_path,
        store=fixture_store_path,
        coverage={"reference_names": ["CIK0001000016:FRST::2019-12-31"], "check_enabled": True},
    )
    passed = _invoke(listed, ["health", "--check"], clock=lambda: T_END)
    assert passed.exit_code == 0, passed.output
    assert "  coverage_reference_names: pass" in passed.output


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


def test_health_check_fails_naming_the_missing_session(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    """#1575, #1550 part c: a mid-window session with no bars at all is named
    with its row count, `health` reports it and `--check` fails on it."""
    settings = _settings(tmp_path, store=fixture_store_path)
    missing = date(2020, 2, 14)
    with open_for_write(settings) as conn:
        conn.execute("DELETE FROM prices_daily WHERE session = ?", [missing])
    plain = _invoke(settings, ["health"], clock=lambda: T_END)
    assert plain.exit_code == 0, plain.output
    assert "session holes: 1 in the universe history window" in plain.output
    assert "  2020-02-14: 0 rows (previous 2020-02-13: 19)" in plain.output
    checked = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert checked.exit_code == 1, checked.output
    assert "health check failed: session_holes" in checked.output
    assert "  session_holes: FAIL (1 rows)" in checked.output


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


def _equal_cash(conn: duckdb.DuckDBPyConnection, sid: str, action_id: str) -> None:
    known = session_close(date(2020, 3, 13))
    insert_row(
        conn,
        "corporate_actions",
        {
            "security_id": sid,
            "action_type": "dividend",
            "ex_date": date(2020, 3, 16),
            "ratio_or_amount": 0.37,
            "announced_at": None,
            "source_action_id": action_id,
            "cancelled": False,
            "known_at": known,
            "ingested_at": known,
            "source": "alpaca",
            "provenance": "action",
        },
    )


def test_health_lists_equal_amount_dividends_and_warns_on_pending(
    tmp_path: Path, fixture_store_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1613 (T186b): accepted and unresolved pairs are listed, a pending
    duplicate is a warning and not a failure, an unlisted pair fails."""
    settings = _settings(tmp_path, store=fixture_store_path)
    plain = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert plain.exit_code == 0, plain.output
    assert "accepted equal-amount dividend pairs: 0" in plain.output
    assert "unresolved equal-amount dividend pairs: 0" in plain.output
    assert "pending repair" not in plain.output
    with open_for_write(settings) as conn:
        for sid in ("SEC_DUAL_A", "SEC_DUAL_B", "SEC_TICKCHANGE", "SEC_TRANSFER"):
            for action_id in ("a", "b"):
                _equal_cash(conn, sid, f"{sid}-{action_id}")

    def listed(sid: str, verdict: str) -> ReviewedGroup:
        retract = f"{sid}-b" if verdict == "duplicate" else ""
        return ReviewedGroup(sid, date(2020, 3, 16), 0.37, verdict, retract, "TCK", "test")  # type: ignore[arg-type]

    reviewed = [
        listed("SEC_DUAL_A", "distinct"),
        listed("SEC_DUAL_B", "unresolved"),
        listed("SEC_TICKCHANGE", "duplicate"),
    ]
    monkeypatch.setattr(health, "reviewed_groups", lambda: reviewed)
    failing = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert failing.exit_code == 1  # SEC_TRANSFER's pair is listed nowhere
    assert "health check failed: duplicate_dividends" in failing.output
    assert "duplicate_dividends: FAIL (2 rows)" in failing.output
    assert "accepted equal-amount dividend pairs: 1" in failing.output
    assert "  SEC_DUAL_A@2020-03-16 TCK 0.37 (SEC_DUAL_A-a, SEC_DUAL_A-b)" in failing.output
    assert "unresolved equal-amount dividend pairs: 1" in failing.output
    assert "  SEC_DUAL_B@2020-03-16 TCK 0.37" in failing.output
    assert "warning: 1 listed duplicate dividend keys are pending repair" in failing.output
    monkeypatch.setattr(
        health, "reviewed_groups", lambda: [*reviewed, listed("SEC_TRANSFER", "distinct")]
    )
    checked = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert checked.exit_code == 0, checked.output  # pending is a warning, not a failure
    assert "warning: 1 listed duplicate dividend keys are pending repair" in checked.output


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


# --- decision development-boundary (#1319, ADR 0016 point 6, plan T142b) -------------

#: Today's families (#1333): momentum's spent holdout, profitability's unspent one on
#: the same months, combined's forward one.
_SPENT = ("momentum", date(2024, 1, 1), date(2026, 9, 30))
_UNSPENT = ("profitability", date(2024, 1, 1), date(2026, 9, 30))
_FORWARD = ("combined", date(2027, 1, 1), date(2027, 6, 30))


def _boundary_store(settings: Settings, *families: tuple[str, date, date]) -> None:
    """A migrated store with one hypothesis per family (in-sample from 2020-08-31);
    momentum's holdout is spent by a holdout trial."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        for family, start, end in families:
            record = registry.register_hypothesis(
                conn,
                slug=f"{family}-h",
                family=family,
                title=family,
                doc_path=f"docs/hypotheses/{family}-h.md",
                doc_sha256="d" * 64,
                params={"costs.per_side_bps": 15.0},
                in_sample_start=date(2020, 8, 31),
                holdout_start=start,
                holdout_end=end,
                registered_by="owner",
                settings=settings,
            )
            if family == "momentum":
                registry.open_trial(
                    conn,
                    hypothesis_id=record.hypothesis_id,
                    kind="holdout",
                    start_session=start,
                    end_session=end,
                    data_cutoff=None,
                    synthetic=False,
                    run_by="test",
                    holdout_reason="spent",
                    settings=settings,
                )


def _set_boundary(settings: Settings, day: str, reason: str = "ADR 0016") -> Any:
    return _invoke(
        settings, ["decision", "development-boundary", "--date", day, "--reason", reason]
    )


def _boundary_rows(settings: Settings) -> int:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        (n,) = conn.execute(  # type: ignore[misc]
            "SELECT COUNT(*) FROM owner_decisions WHERE kind = 'development_boundary'"
        ).fetchone()
    return int(n)


@pytest.mark.parametrize(
    ("family", "day"),
    [
        (_SPENT, "2024-01-01"),
        (_SPENT, "2025-03-31"),
        (_UNSPENT, "2024-01-01"),
        (_FORWARD, "2027-01-01"),
        (_FORWARD, "2027-03-31"),
    ],
    ids=["spent-start", "spent-inside", "unspent-start", "forward-start", "forward-inside"],
)
def test_development_boundary_is_refused_on_or_after_any_holdout_start(
    tmp_path: Path, family: tuple[str, date, date], day: str
) -> None:
    settings = _settings(tmp_path)
    _boundary_store(settings, family)
    result = _set_boundary(settings, day)
    assert result.exit_code == 1, result.output
    assert f"boundary refused: development boundary {day} is on or after" in result.output
    assert f"{family[0]} ({family[1]})" in result.output
    assert _boundary_rows(settings) == 0


@pytest.mark.parametrize("day", ["2020-08-31", "2019-12-31"])
def test_development_boundary_is_refused_on_or_before_an_in_sample_start(
    tmp_path: Path, day: str
) -> None:
    settings = _settings(tmp_path)
    _boundary_store(settings, _SPENT, _UNSPENT, _FORWARD)
    result = _set_boundary(settings, day)
    assert result.exit_code == 1, result.output
    assert "in_sample_start of combined (2020-08-31), momentum" in result.output
    assert _boundary_rows(settings) == 0


def test_development_boundary_writes_a_new_row_each_time(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _boundary_store(settings, _SPENT, _UNSPENT, _FORWARD)
    first = _set_boundary(settings, "2023-12-29", "ADR 0016, #1320")
    assert first.exit_code == 0, first.output
    assert "development_boundary 2023-12-29" in first.output
    moved = _set_boundary(settings, "2022-12-30", "moved; momentum, profitability ran")
    assert moved.exit_code == 0, moved.output
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        found = registry.development_boundary(conn)
    assert found is not None
    assert (found.boundary, found.reason) == (
        date(2022, 12, 30),
        "moved; momentum, profitability ran",
    )
    assert _boundary_rows(settings) == 2


def test_development_boundary_usage_errors_write_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _boundary_store(settings, _SPENT)
    assert _set_boundary(settings, "2023-12-29", " ").exit_code == 2
    assert _set_boundary(settings, "2023-12-1").exit_code == 2
    assert _boundary_rows(settings) == 0


# --- decision shakedown-span / shakedown-note (#1388, paper-trading plan T157) --------


_SPAN = ("decision", "shakedown-span")


def _shakedown_store(settings: Settings) -> int:
    """A migrated store with one `missed_run` alert on book `main`; its id."""
    at = datetime(2026, 10, 12, 21, 0, tzinfo=UTC)
    with open_for_write(settings) as conn:
        init_schema(conn)
        alert_id = journal.append(
            conn,
            journal.AlertRow(
                session=date(2026, 10, 12),
                kind="missed_run",
                message="no run",
                at=at,
                known_at=at,
                ingested_at=at,
            ),
        )
    assert alert_id is not None
    return alert_id


def _shakedown_rows(settings: Settings) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(
            "SELECT kind, values_json, reason FROM owner_decisions "
            "WHERE kind LIKE 'shakedown%' ORDER BY decision_id"
        ).fetchall()


def test_shakedown_span_writes_both_thresholds_and_a_new_row_restarts(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _shakedown_store(settings)
    args = [*_SPAN, "--sessions", "10", "--order-sessions", "5"]
    first = _invoke(settings, [*args, "--reason", "H1 goes live first"])
    assert first.exit_code == 0, first.output
    assert "shakedown_span (sessions 10, order sessions 5)" in first.output
    second = _invoke(
        settings,
        [*_SPAN, "--sessions", "20", "--order-sessions", "10", "--reason", "restart after"],
    )
    assert second.exit_code == 0, second.output
    assert _shakedown_rows(settings) == [
        ("shakedown_span", '{"order_sessions":5,"sessions":10}', "H1 goes live first"),
        ("shakedown_span", '{"order_sessions":10,"sessions":20}', "restart after"),
    ]
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        span = registry.shakedown_span(conn)
    assert span is not None and (span.sessions, span.order_sessions) == (20, 10)


@pytest.mark.parametrize(
    ("sessions", "order_sessions", "reason"),
    [("10", "5", " "), ("10", "11", "r"), ("10", "0", "r"), ("0", "0", "r"), ("ten", "5", "r")],
    ids=["blank-reason", "m-over-n", "m-zero", "n-zero", "not-a-number"],
)
def test_shakedown_span_usage_errors_write_nothing(
    tmp_path: Path, sessions: str, order_sessions: str, reason: str
) -> None:
    settings = _settings(tmp_path)
    _shakedown_store(settings)
    result = _invoke(
        settings,
        [*_SPAN, "--sessions", sessions, "--order-sessions", order_sessions, "--reason", reason],
    )
    assert result.exit_code == 2, result.output
    assert _shakedown_rows(settings) == []


def test_shakedown_note_names_an_existing_alert_and_refuses_an_unknown_one(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    alert_id = _shakedown_store(settings)
    unknown = _invoke(
        settings,
        ["decision", "shakedown-note", "--alert", str(alert_id + 1), "--reason", "late"],
    )
    assert unknown.exit_code == 1, unknown.output
    assert f"shakedown refused: alert {alert_id + 1} does not exist" in unknown.output
    blank = _invoke(
        settings, ["decision", "shakedown-note", "--alert", str(alert_id), "--reason", " "]
    )
    assert blank.exit_code == 2, blank.output
    assert _shakedown_rows(settings) == []
    ok = _invoke(
        settings,
        ["decision", "shakedown-note", "--alert", str(alert_id), "--reason", "feed late"],
    )
    assert ok.exit_code == 0, ok.output
    assert f"shakedown_note for alert {alert_id}" in ok.output
    assert _shakedown_rows(settings) == [
        ("shakedown_note", f'{{"alert_id":{alert_id}}}', "feed late")
    ]


def test_shakedown_commands_without_a_store_write_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    result = _invoke(
        settings,
        [*_SPAN, "--sessions", "10", "--order-sessions", "5", "--reason", "r"],
    )
    assert result.exit_code != 0
    assert not Path(settings.store.path).exists()


# --- decision data-release (#1319, data-foundation plan T140b) ------------------------


def _release_store(settings: Settings, *, with_trial: bool = False) -> None:
    """A migrated store at `settings.store.path` with one price row and, when asked,
    one registered hypothesis and its trial 1."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": "A",
                "session": date(2020, 6, 30),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "known_at": T_END,
                "ingested_at": NOW,
                "source": "test",
                "provenance": "bar",
            },
        )
        if with_trial:
            record = registry.register_hypothesis(
                conn,
                slug="h1",
                family="momentum",
                title="h1",
                doc_path="docs/hypotheses/h1.md",
                doc_sha256="d" * 64,
                params={"costs.per_side_bps": 15.0},
                in_sample_start=date(2016, 1, 29),
                holdout_start=date(2023, 1, 3),
                holdout_end=date(2025, 12, 31),
                registered_by="owner",
                settings=settings,
            )
            registry.open_trial(
                conn,
                hypothesis_id=record.hypothesis_id,
                kind="in_sample",
                start_session=date(2018, 1, 31),
                end_session=date(2020, 6, 30),
                data_cutoff=T_END,
                synthetic=False,
                run_by="test",
                settings=settings,
            )


def _backup(settings: Settings, tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"tradepartner.{name}.duckdb"
    path.write_bytes(Path(settings.store.path).read_bytes())
    return path


def _releases(settings: Settings) -> list[Any]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return registry.data_releases(conn)


def test_data_release_open_then_close(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _release_store(settings)
    backup = _backup(settings, tmp_path, "repair-a")
    opened = _invoke(
        settings,
        [
            "decision",
            "data-release",
            "open",
            "--name",
            "repair-a",
            "--backup",
            str(backup),
            "--reason",
            "#1314 repair",
        ],
    )
    assert opened.exit_code == 0, opened.output
    assert "data_release repair-a before (open)" in opened.output
    again = _invoke(
        settings,
        [
            "decision",
            "data-release",
            "open",
            "--name",
            "repair-b",
            "--backup",
            str(backup),
            "--reason",
            "r",
        ],
    )
    assert again.exit_code == 1 and "'repair-a' is open" in again.output
    closed = _invoke(
        settings,
        [
            "decision",
            "data-release",
            "close",
            "--name",
            "repair-a",
            "--from",
            "2020-06-01",
            "--to",
            "2020-06-30",
        ],
    )
    assert closed.exit_code == 0, closed.output
    after, before = _releases(settings)
    assert (before.stage, before.backup_path, before.reason) == (
        "before",
        str(backup),
        "#1314 repair",
    )
    assert before.store_max_ingested_at == NOW
    assert (after.stage, after.sessions_from, after.cutoff) == ("after", date(2020, 6, 1), T_END)
    bad_day = _invoke(
        settings,
        [
            "decision",
            "data-release",
            "close",
            "--name",
            "repair-a",
            "--from",
            "2020-6-1",
            "--to",
            "2020-06-30",
        ],
    )
    assert bad_day.exit_code == 2


def test_data_release_open_refuses_a_missing_or_self_backup(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _release_store(settings)
    for backup, code, text in (
        (tmp_path / "absent.duckdb", 2, "is not a file"),
        (Path(settings.store.path), 2, "is the store itself"),
    ):
        result = _invoke(
            settings,
            [
                "decision",
                "data-release",
                "open",
                "--name",
                "repair-a",
                "--backup",
                str(backup),
                "--reason",
                "r",
            ],
        )
        assert result.exit_code == code and text in result.output
    stale = _backup(settings, tmp_path, "stale")
    with open_for_write(settings) as conn:
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": "B",
                "session": date(2020, 6, 30),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "known_at": T_END,
                "ingested_at": NOW + timedelta(hours=1),
                "source": "test",
                "provenance": "bar",
            },
        )
    result = _invoke(
        settings,
        [
            "decision",
            "data-release",
            "open",
            "--name",
            "repair-a",
            "--backup",
            str(stale),
            "--reason",
            "r",
        ],
    )
    assert result.exit_code == 1 and "not the store's state" in result.output
    assert _releases(settings) == []


def test_data_release_record_names_the_backup_of_a_trial(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _release_store(settings, with_trial=True)
    backup = _backup(settings, tmp_path, "pre-sweep")
    result = _invoke(
        settings,
        [
            "decision",
            "data-release",
            "record",
            "--name",
            "pre-sweep",
            "--backup",
            str(backup),
            "--trial",
            "1",
            "--reason",
            "back-fill",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "equals the trial's" in result.output and "0 repair run(s)" in result.output
    (row,) = _releases(settings)
    assert (row.stage, row.trial, row.data_vintage) == ("record", 1, NOW)
    assert row.values["vintage_equals_trial"] is True


_RELEASES_TOML = """
[[release]]
name = "repair-13-tickers"
stage = "before"
made_at = 2026-10-10T14:05:00Z
backup_path = "data/tradepartner.repair-13-tickers.duckdb"
store_max_ingested_at = 2026-10-09T05:48:12.523886Z
data_vintage = 2026-10-09T05:48:12.523886Z
cutoff = 2026-04-29T20:00:00Z
sessions_from = 2016-01-04
sessions_to = 2026-04-29
reason = "#1314: 13 names hold another company's prices"

[[release]]
name = "pre-sweep-20261008"
stage = "record"
made_at = 2026-10-08T21:50:00Z
backup_path = "data/tradepartner.pre-sweep-20261008.duckdb"
store_max_ingested_at = 2026-10-08T05:48:12.523886Z
data_vintage = 2026-10-08T02:48:17.200099Z
cutoff = 2026-09-30T20:00:00Z
sessions_from = 2023-12-29
sessions_to = 2026-09-30
trial = 1
reason = "Back-fill (#1303 item 1)"
"""


def test_data_release_import_reads_the_file_once(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _release_store(settings, with_trial=True)
    path = tmp_path / "releases.toml"
    path.write_text(_RELEASES_TOML, encoding="utf-8")
    result = _invoke(settings, ["decision", "data-release", "import-file", str(path)])
    assert result.exit_code == 0, result.output
    assert "repair-13-tickers: imported before" in result.output
    assert "pre-sweep-20261008: imported record" in result.output
    rows = _releases(settings)
    assert [(r.name, r.stage) for r in rows] == [
        ("repair-13-tickers", "before"),
        ("pre-sweep-20261008", "record"),
    ]
    assert rows[1].data_vintage == datetime(2026, 10, 8, 2, 48, 17, 200099, tzinfo=UTC)
    again = _invoke(settings, ["decision", "data-release", "import-file", str(path)])
    assert again.exit_code == 1 and "already stored" in again.output
    assert len(_releases(settings)) == 2
    path.write_text("release = [1, 2]\n", encoding="utf-8")
    not_tables = _invoke(settings, ["decision", "data-release", "import-file", str(path)])
    assert not_tables.exit_code == 2 and "must be a table" in not_tables.output
    path.write_text("[[release]\n", encoding="utf-8")
    assert _invoke(settings, ["decision", "data-release", "import-file", str(path)]).exit_code == 2


def test_actions_backfill_cli_validates_and_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tradepartner.ingest import ACTIONS_FILLED

    settings = _settings(tmp_path, alpaca_api_key="fake-key", alpaca_api_secret="fake-secret")
    for args in (
        ["ingest", "--source", "edgar", "--actions-backfill", "--since", "2020-06-01"],
        ["ingest", "--source", "alpaca", "--actions-backfill", "--since", "2020-05-01"],
        [
            "ingest",
            "--source",
            "alpaca",
            "--actions-backfill",
            "--since",
            "2020-06-01",
            "--through",
            "bad",
        ],
    ):
        assert _invoke(settings, args).exit_code == 2
    seen: dict[str, Any] = {}

    def fake_actions(settings: Settings, **kwargs: Any) -> IngestResult:
        seen.update(kwargs)
        return IngestResult(
            (SourceRun("alpaca", ACTIONS_FILLED, 0, "actions;through=2020-06-30", "ok"),)
        )

    monkeypatch.setattr(cli, "ingest_actions_backfill", fake_actions)
    result = _invoke(
        settings,
        [
            "ingest",
            "--source",
            "alpaca",
            "--actions-backfill",
            "--since",
            "2020-06-01",
            "--through",
            "2020-06-30",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0, result.output
    assert seen["since"] == date(2020, 6, 1)
    assert seen["through"] == date(2020, 6, 30)
    assert seen["dry_run"] is True


# --- the borrow-status snapshot (#1506, T171c, T171e) --------------------------


_ASSET = {
    "id": "00000000-0000-4000-8000-000000000001",
    "class": "us_equity",
    "symbol": "AAA",
    "exchange": "NYSE",
    "status": "active",
    "tradable": True,
    "marginable": True,
    "shortable": True,
    "easy_to_borrow": True,
    "borrow_status": "easy_to_borrow",
    "fractionable": True,
}


class _AssetsEdge:
    """Stands in for `alpaca_raw.assets_all`, recording each call."""

    def __init__(self) -> None:
        self.calls: list[tuple[Settings, Any]] = []

    def __call__(self, settings: Settings, clock: Any) -> alpaca_raw.AssetsFetch:
        self.calls.append((settings, clock))
        return alpaca_raw.AssetsFetch([_ASSET], NOW - timedelta(seconds=2), NOW)


def _with_keep(settings: Settings, tmp_path: Path) -> Settings:
    borrow = settings.borrow.model_copy(update={"raw_dir": str(tmp_path / "raw")})
    return settings.model_copy(update={"borrow": borrow})


def test_ingest_source_alpaca_assets_takes_one_snapshot_through_the_edge(
    tmp_path: Path, secrets_set: Settings
) -> None:
    settings = _with_keep(secrets_set, tmp_path)
    edge = _AssetsEdge()
    result = _invoke(settings, ["ingest", "--source", "alpaca_assets"], assets_source=edge)
    assert result.exit_code == 0, result.output
    assert len(edge.calls) == 1 and edge.calls[0][0] is settings
    assert "alpaca_assets: ok, 1 rows, cursor 2020-07-01T23:00:00+00:00" in result.output
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM borrow_status").fetchone() == (1,)


def test_ingest_source_alpaca_assets_needs_the_alpaca_keys(tmp_path: Path) -> None:
    settings = _with_keep(_settings(tmp_path), tmp_path)
    edge = _AssetsEdge()
    result = _invoke(settings, ["ingest", "--source", "alpaca_assets"], assets_source=edge)
    assert result.exit_code == 2
    assert "ALPACA_API_KEY" in result.output
    assert edge.calls == []


def test_ingest_all_hands_the_session_the_assets_edge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, secrets_set: Settings
) -> None:
    """`--source all` passes the snapshot's fetch, bound to the settings and
    clock; `ingest_session` decides from `borrow.enabled` whether to call it."""
    session = _patched(monkeypatch, "ingest_session", _ok("edgar", "alpaca"))
    edge = _AssetsEdge()
    result = _invoke(secrets_set, ["ingest"], assets_source=edge)
    assert result.exit_code == 0, result.output
    assert edge.calls == []
    fetched = session.calls[0]["assets"]()
    assert fetched.assets == [_ASSET]
    assert edge.calls[0][0] is secrets_set


def test_ingest_import_dir_applies_the_files_with_no_request_and_no_secret(
    tmp_path: Path,
) -> None:
    rec = tmp_path / "rec"
    rec.mkdir()
    for name, finished in (
        ("assets-1.json.gz", NOW - timedelta(days=2)),
        ("assets-2.json.gz", NOW + timedelta(days=1)),
    ):
        with gzip.open(rec / name, "wt") as handle:
            json.dump(
                {
                    "fetch_started_utc": (finished - timedelta(seconds=2)).isoformat(),
                    "fetch_finished_utc": finished.isoformat(),
                    "endpoint": "paper GET /v2/assets",
                    "assets": [_ASSET],
                },
                handle,
            )
    settings = _with_keep(_settings(tmp_path), tmp_path)  # no Alpaca key at all
    edge = _AssetsEdge()
    args = ["ingest", "--source", "alpaca_assets", "--import-dir", str(rec)]
    result = _invoke(settings, args, assets_source=edge)
    assert edge.calls == []
    assert result.exit_code == 1, result.output  # one file is from the future
    assert "alpaca_assets import: 1 applied, 0 skipped" in result.output
    assert "1 refused" in result.output
    assert "refused assets-2.json.gz: SnapshotClockError" in result.output
    again = _invoke(settings, args, assets_source=edge)
    assert "0 applied, 1 skipped" in again.output
    assert "skipped assets-1.json.gz" in again.output


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (["--import-dir", "."], "--import-dir needs --source alpaca_assets"),
        (
            ["--source", "alpaca_assets", "--backfill", "--since", "2020-06-29"],
            "has no backfill",
        ),
        (["--source", "alpaca_assets", "--rebuild-classifications"], "edgar source"),
    ],
)
def test_ingest_refuses_borrow_flags_where_they_cannot_apply(
    secrets_set: Settings, args: list[str], why: str
) -> None:
    edge = _AssetsEdge()
    result = _invoke(secrets_set, ["ingest", *args], assets_source=edge)
    assert result.exit_code == 2
    assert why in result.output
    assert edge.calls == []


def test_ingest_import_dir_must_be_a_directory(tmp_path: Path) -> None:
    settings = _with_keep(_settings(tmp_path), tmp_path)
    args = ["ingest", "--source", "alpaca_assets", "--import-dir", str(tmp_path / "missing")]
    result = _invoke(settings, args)
    assert result.exit_code == 2
    assert "is not a directory" in result.output


def test_health_prints_the_borrow_block_and_its_warnings(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    settings = _settings(tmp_path, store=fixture_store_path)
    result = _invoke(settings, ["health"], clock=lambda: T_END)
    assert result.exit_code == 0, result.output
    assert "borrow status: 10 rows, 6 assets; latest observation none" in result.output
    assert "last alpaca_assets run: never; none" in result.output
    assert "None shortable" not in result.output
    assert "warning: borrow.enabled is off" in result.output


def test_health_coverage_prints_the_index_lines_with_the_reports_sha(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    """#1682, T189b: two lines under the coverage block (the key's SHA, the
    counts, then the misses by bucket) and the rule's verdict, report-only."""
    settings = _settings(tmp_path, store=fixture_store_path)
    result = _invoke(settings, ["health", "--coverage", "--check"], clock=lambda: T_END)
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    [summary] = [line for line in lines if line.startswith("  index members (key ")]
    assert summary.startswith(
        "  index members (key a2430f2af0c79ddf0748e91de11bdeb1616ab5a7 through 2026-08-18): "
        "2 sessions checked, 0 beyond the key; 0 of 1010 hits (0.0%); "
    )
    assert summary.endswith("max missing per session (report-only)")
    [buckets] = [line for line in lines if line.startswith("  index misses by bucket: ")]
    assert (
        "unresolved 1010 member-sessions, 517 names (" in buckets
        and " member-sessions, " in buckets
        and "AAPL" in buckets
    )
    assert "  index_members_missing: " in result.output
    plain = _invoke(settings, ["health", "--check"], clock=lambda: T_END)
    assert "  index_members_missing: not computed (run with --coverage)" in plain.output


def test_health_check_fails_index_members_missing_once_the_threshold_is_set(
    tmp_path: Path, fixture_store_path: Path
) -> None:
    gated = _settings(tmp_path, store=fixture_store_path, coverage={"index_max_missing": 0})
    failed = _invoke(gated, ["health", "--check"], clock=lambda: T_END)
    assert failed.exit_code == 1
    assert "health check failed: index_members_missing" in failed.output
    assert ": fail (max 0)" in failed.output
    refused = _invoke(gated, ["health", "--no-coverage", "--check"], clock=lambda: T_END)
    assert refused.exit_code == 1
    assert "coverage.index_max_missing is set" in refused.output
    off = _settings(tmp_path, store=fixture_store_path, coverage={"index_members": False})
    result = _invoke(off, ["health", "--coverage"], clock=lambda: T_END)
    assert "  index members: off (coverage.index_members)" in result.output
    assert "  index_members_missing: off (coverage.index_members)" in result.output


# --- T191 (#1682 lever B): delete-master-rows ---

_VZ = "0000732712"
_NOTE = "2.375% Notes due 2022"
_NOTE_KNOWN = datetime(2019, 10, 30, 16, 18, 19, tzinfo=UTC)
_TENQ = "0000732712-19-000063"
_MASTER_RELEASE = "restate-test"


def _sgml(accession: str, accepted_eastern: str, cik: str) -> str:
    return (
        f"<SEC-DOCUMENT>{accession}.txt : 20191030\n<SEC-HEADER>{accession}.hdr.sgml\n"
        f"<ACCEPTANCE-DATETIME>{accepted_eastern}\nACCESSION NUMBER:\t\t{accession}\n"
        "CONFORMED SUBMISSION TYPE:\t10-Q\nFILER:\n\tCOMPANY DATA:\n"
        f"\t\tCENTRAL INDEX KEY:\t\t\t{cik}\n"
        "\t\tSTANDARD INDUSTRIAL CLASSIFICATION:\tTELEPHONE COMMUNICATIONS [4813]\n"
        "</SEC-HEADER>\n"
    )


@pytest.fixture
def master_store(secrets_set: Settings, tmp_path: Path) -> tuple[Settings, Path]:
    """Verizon's primary with the common and the note's listing; a release
    open; a manifest naming the note's row with its 10-Q."""
    with open_for_write(secrets_set) as conn:
        init_schema(conn)
        common = datetime(2019, 8, 8, 17, 4, 24, tzinfo=UTC)
        insert_row(
            conn,
            "securities",
            {
                "security_id": _VZ,
                "cik": _VZ,
                "name": "VZ",
                "known_at": common,
                "ingested_at": NOW,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        for ticker, title, known in (("VZ", "Common Stock", common), ("VZ22A", _NOTE, _NOTE_KNOWN)):
            insert_row(
                conn,
                "listings",
                {
                    "security_id": _VZ,
                    "ticker": ticker,
                    "exchange": "NYSE",
                    "class_title": title,
                    "valid_from": known.date(),
                    "known_at": known,
                    "ingested_at": datetime(2026, 10, 4, tzinfo=UTC),
                    "source": "edgar",
                    "provenance": "filing",
                },
            )
        registry.open_data_release(
            conn, conn, name=_MASTER_RELEASE, backup_path="b.duckdb", reason="#1682"
        )
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(
        "table,security_id,ticker,exchange,valid_from,form,class_title,filed_at,known_at,"
        f"accession\nlistings,{_VZ},VZ22A,NYSE,2019-10-30,,{_NOTE},,{_NOTE_KNOWN.isoformat()},"
        f"{_TENQ}\n"
    )
    return secrets_set, manifest


def _edgar_headers(accepted_eastern: str, cik: str = _VZ) -> tuple[httpx.Client, list[str]]:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if (
            request.url.path
            == f"/Archives/edgar/data/{int(_VZ)}/{_TENQ.replace('-', '')}/{_TENQ}.txt"
        ):
            return httpx.Response(206, text=_sgml(_TENQ, accepted_eastern, cik))
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), asked


def _stored_tickers(settings: Settings) -> list[str]:
    with duckdb.connect(str(settings.store.path), read_only=True) as conn:
        return [
            r[0] for r in conn.execute("SELECT ticker FROM listings ORDER BY ticker").fetchall()
        ]


def test_delete_master_rows_dry_run_then_delete(master_store: tuple[Settings, Path]) -> None:
    settings, manifest = master_store
    client, asked = _edgar_headers("20191030121819")  # 12:18:19 New York = the row's stamp
    args = ["delete-master-rows", "--manifest", str(manifest), "--release", _MASTER_RELEASE]
    dry = _invoke(settings, [*args, "--dry-run"], edgar_client=client)
    assert dry.exit_code == 0, dry.output
    assert dry.output.startswith("would delete 1 listings and 0 delistings rows of 1 manifest")
    assert f"listings {_VZ} VZ22A NYSE 2019-10-30" in dry.output
    assert asked and _stored_tickers(settings) == ["VZ", "VZ22A"]
    done = _invoke(settings, args, edgar_client=client)
    assert done.exit_code == 0, done.output
    assert done.output.startswith("deleted 1 listings")
    assert _stored_tickers(settings) == ["VZ"]


def test_delete_master_rows_refuses_later_evidence_and_another_cik(
    master_store: tuple[Settings, Path],
) -> None:
    settings, manifest = master_store
    args = ["delete-master-rows", "--manifest", str(manifest), "--release", _MASTER_RELEASE]
    for client, reason in (
        (_edgar_headers("20191030121820")[0], "after the row's known_at"),
        (
            _edgar_headers("20191030121819", cik="0000000001")[0],
            f"does not read as CIK {_VZ}'s filing",
        ),
    ):
        for extra in (["--dry-run"], []):
            result = _invoke(settings, [*args, *extra], edgar_client=client)
            assert result.exit_code == 1, result.output
            assert reason in result.output
        assert _stored_tickers(settings) == ["VZ", "VZ22A"]
    other = manifest.parent / "other.csv"
    other.write_text(manifest.read_text().replace(_TENQ, "0000732712-19-000064"))
    gone = _invoke(
        settings,
        ["delete-master-rows", "--manifest", str(other), "--release", _MASTER_RELEASE],
        edgar_client=_edgar_headers("20191030121819")[0],
    )
    assert gone.exit_code == 1 and "EDGAR has no 0000732712-19-000064" in gone.output


def test_delete_master_rows_usage_errors(
    master_store: tuple[Settings, Path], tmp_path: Path
) -> None:
    settings, manifest = master_store
    client, _ = _edgar_headers("20191030121819")
    base = ["delete-master-rows", "--manifest", str(manifest)]
    assert _invoke(settings, base, edgar_client=client).exit_code == cli.USAGE_ERROR
    blank = _invoke(settings, [*base, "--release", " "], edgar_client=client)
    assert blank.exit_code == cli.USAGE_ERROR and "--release <name>" in blank.output
    other = _invoke(settings, [*base, "--release", "other"], edgar_client=client)
    assert other.exit_code == 1 and "no open data release named 'other'" in other.output
    empty = tmp_path / "empty.csv"
    empty.write_text(manifest.read_text().splitlines()[0] + "\n")
    no_rows = _invoke(settings, [*base[:2], str(empty), "--release", _MASTER_RELEASE])
    assert no_rows.exit_code == cli.USAGE_ERROR and "lists no rows" in no_rows.output
    no_agent = settings.model_copy(update={"sec_edgar_user_agent": None})
    missing = _invoke(no_agent, [*base, "--release", _MASTER_RELEASE, "--dry-run"])
    assert missing.exit_code == cli.USAGE_ERROR and "SEC_EDGAR_USER_AGENT" in missing.output
    assert _stored_tickers(settings) == ["VZ", "VZ22A"]
