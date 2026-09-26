"""`tradepartner` CLI (plan T19, spike `spike/t19-cli`).

Every command is driven through `build_app(CliDeps(...))`, so settings, the
clock, the EDGAR HTTP client, the price source and the dashboard launcher are
injected: no test touches the network, the real store or `.env`.

T11d/T11e/T11f are not merged when this spike was written: the EDGAR ingest
test stubs `cover_pages`, `filing_headers`, `facts` and `delistings` on
`EdgarFilingSource` and exercises the real `filing_index` and
`companies_snapshot` over the recorded fixtures.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pyarrow.parquet as pq
import pytest
from adapters.edgar_transport import (
    FIXTURES,
    USER_AGENT,
    EdgarRouter,
    edgar_settings,
    index_header,
)
from pydantic import SecretStr
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.adapters.edgar_source import EdgarFilingSource
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.calendar import session_close
from tradepartner.config import Settings
from tradepartner.store.db import insert_row
from tradepartner.store.schema import REGISTRY_TABLE_NAMES, TABLE_NAMES

CLOCK = datetime(2026, 9, 25, tzinfo=UTC)
T_END = session_close(date(2020, 6, 30))
SECRET_AGENT = "Secret Person secret.person@example.com"


@dataclass
class _NoPrices(PriceSource):
    """A price source that must not be called (EDGAR-only runs)."""

    calls: list[str] = field(default_factory=list)

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        self.calls.append("bars")
        raise AssertionError("prices must not be fetched")

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        self.calls.append("actions")
        raise AssertionError("prices must not be fetched")


def _deps(
    settings: Settings,
    *,
    client: httpx.Client | None = None,
    clock: datetime = CLOCK,
    prices: PriceSource | None = None,
    launched: list[list[str]] | None = None,
) -> cli.CliDeps:
    def launch(argv: list[str]) -> int:
        if launched is not None:
            launched.append(argv)
        return 0

    return cli.CliDeps(
        settings=lambda: settings,
        http_client=lambda _settings: client,
        clock=lambda: clock,
        price_source=lambda _settings: prices if prices is not None else _NoPrices(),
        launch=launch,
    )


def _invoke(deps: cli.CliDeps, *args: str) -> Any:
    return CliRunner().invoke(cli.build_app(deps), list(args))


def _output(result: Any) -> str:
    return str(result.output) + str(getattr(result, "stderr", "") or "")


# --- ingest -------------------------------------------------------------------


def _edgar_router() -> EdgarRouter:
    """Index quarters 2024 Q1 to 2026 Q3: the recorded 2024 Q1 index (Apple,
    Alphabet and KLX rows only), header-only elsewhere."""
    router = EdgarRouter()
    for year in (2024, 2025, 2026):
        for qtr in range(1, 5):
            if (year, qtr) <= (2026, 3):
                router.add_index(year, qtr, index_header())
    router.add_index(2024, 1, (FIXTURES / "filing_index_2024_qtr1.txt").read_text())
    return router


@pytest.fixture
def stub_t11def(monkeypatch: pytest.MonkeyPatch) -> None:
    """T11d/T11e/T11f placeholders answer empty until those tasks merge."""
    for method in ("cover_pages", "filing_headers", "facts"):
        monkeypatch.setattr(EdgarFilingSource, method, lambda self, *a, **k: [])
    monkeypatch.setattr(EdgarFilingSource, "delistings", lambda self, since=None: [])


def _edgar_cli_settings(tmp_path: Path, **overrides: Any) -> Settings:
    base = edgar_settings(tmp_path / "cache")
    return base.model_copy(
        update={
            "store": base.store.model_copy(
                update={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 1}
            ),
            **overrides,
        }
    )


def test_ingest_edgar_builds_the_edgar_source_from_settings(
    tmp_path: Path, stub_t11def: None
) -> None:
    settings = _edgar_cli_settings(tmp_path)
    router = _edgar_router()
    prices = _NoPrices()
    result = _invoke(
        _deps(settings, client=router.client(), prices=prices), "ingest", "--source", "edgar"
    )
    assert result.exit_code == 0, _output(result)
    assert router.index_urls(), "the EDGAR index was never fetched"
    assert any("submissions/CIK0000320193" in url for url in router.urls)
    assert prices.calls == []
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        rows = conn.execute("SELECT source, status FROM ingestion_runs").fetchall()
        securities = conn.execute("SELECT count(*) FROM securities").fetchone()
    assert rows == [("edgar", "ok")]
    assert securities is not None and securities[0] >= 1
    assert "edgar" in result.output and "ok" in result.output


def test_ingest_dry_run_writes_no_run_row(tmp_path: Path, stub_t11def: None) -> None:
    settings = _edgar_cli_settings(tmp_path)
    result = _invoke(
        _deps(settings, client=_edgar_router().client()),
        "ingest",
        "--source",
        "edgar",
        "--dry-run",
    )
    assert result.exit_code == 0, _output(result)
    assert "dry run" in result.output
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        # The dry run rolls back the schema too: no table, so no run row.
        tables = conn.execute("SELECT table_name FROM information_schema.tables").fetchall()
    assert ("ingestion_runs",) not in tables


def test_a_failed_source_exits_non_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(self: EdgarFilingSource, since: object = None) -> list[object]:
        raise RuntimeError("EDGAR down")

    monkeypatch.setattr(EdgarFilingSource, "filing_index", down)
    settings = _edgar_cli_settings(tmp_path)
    result = _invoke(
        _deps(settings, client=_edgar_router().client()), "ingest", "--source", "edgar"
    )
    assert result.exit_code == 1
    assert "failed" in result.output and "EDGAR down" in result.output


@pytest.mark.parametrize("source", ["edgar", "all"])
def test_a_missing_user_agent_exits_non_zero_with_no_secret(tmp_path: Path, source: str) -> None:
    settings = Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb")},
        alpaca_api_key="alpaca-key-value-xyz",
        alpaca_api_secret="alpaca-secret-value-xyz",
        edgar={"cache_dir": str(tmp_path / "cache")},
    )
    router = EdgarRouter()
    result = _invoke(_deps(settings, client=router.client()), "ingest", "--source", source)
    assert result.exit_code == cli.EXIT_USAGE
    out = _output(result)
    assert "SEC_EDGAR_USER_AGENT" in out
    assert "alpaca-key-value-xyz" not in out and "alpaca-secret-value-xyz" not in out
    assert router.urls == []
    assert not Path(settings.store.path).exists()


def test_a_blank_user_agent_counts_as_missing(tmp_path: Path) -> None:
    settings = _edgar_cli_settings(tmp_path, sec_edgar_user_agent=SecretStr("   "))
    result = _invoke(_deps(settings, client=EdgarRouter().client()), "ingest", "--source", "edgar")
    assert result.exit_code == cli.EXIT_USAGE


def test_missing_alpaca_keys_exit_non_zero_with_no_secret(tmp_path: Path) -> None:
    settings = _edgar_cli_settings(tmp_path)
    result = _invoke(_deps(settings), "ingest", "--source", "alpaca")
    assert result.exit_code == cli.EXIT_USAGE
    out = _output(result)
    assert "ALPACA_API_KEY" in out and USER_AGENT not in out


def test_an_error_message_names_no_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def leak(self: EdgarFilingSource, since: object = None) -> list[object]:
        raise RuntimeError(f"server echoed {SECRET_AGENT}")

    monkeypatch.setattr(EdgarFilingSource, "filing_index", leak)
    settings = _edgar_cli_settings(tmp_path, sec_edgar_user_agent=SecretStr(SECRET_AGENT))
    result = _invoke(_deps(settings, client=EdgarRouter().client()), "ingest", "--source", "edgar")
    assert result.exit_code == 1
    assert SECRET_AGENT not in _output(result)
    assert "secret.person" not in _output(result)


@pytest.mark.parametrize(
    "args",
    [
        ("ingest", "--since", "2024-01-01"),  # --since needs --backfill
        ("ingest", "--backfill"),  # --backfill needs --since
        ("ingest", "--backfill", "--since", "2024-01-01", "--dry-run"),
        ("ingest", "--source", "iex"),
        ("ingest", "--backfill", "--since", "not-a-date"),
    ],
)
def test_bad_ingest_options_are_usage_errors(tmp_path: Path, args: tuple[str, ...]) -> None:
    settings = _edgar_cli_settings(tmp_path)
    result = _invoke(_deps(settings, client=EdgarRouter().client()), *args)
    assert result.exit_code == cli.EXIT_USAGE, _output(result)
    assert not Path(settings.store.path).exists()


def test_backfill_passes_since_and_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_backfill(settings: Settings, **kwargs: Any) -> Any:
        seen.update(kwargs)
        from tradepartner.ingest import IngestResult, SourceRun

        return IngestResult((SourceRun("edgar", "ok", 3, "since=2024-01-01", "fine"),))

    monkeypatch.setattr(cli, "backfill", fake_backfill)
    settings = _edgar_cli_settings(tmp_path)
    result = _invoke(
        _deps(settings, client=EdgarRouter().client()),
        "ingest",
        "--source",
        "edgar",
        "--backfill",
        "--since",
        "2024-01-01",
    )
    assert result.exit_code == 0, _output(result)
    assert seen["since"] == date(2024, 1, 1) and seen["source"] == "edgar"
    assert isinstance(seen["filings"], EdgarFilingSource)


# --- health -------------------------------------------------------------------


def _store_settings(path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(path)})


def test_health_check_passes_on_the_fixture_store(fixture_store_path: Path) -> None:
    result = _invoke(_deps(_store_settings(fixture_store_path), clock=T_END), "health", "--check")
    assert result.exit_code == 0, _output(result)
    assert "integrity: ok" in result.output


def test_health_check_fails_naming_the_rule(fixture_store_path: Path) -> None:
    known = datetime(2017, 1, 3, 21, 0, tzinfo=UTC)
    with duckdb.connect(str(fixture_store_path)) as conn:
        insert_row(
            conn,
            "listings",
            {
                "security_id": "SEC_DUAL_A",
                "ticker": "DUALA",
                "exchange": "NASDAQ",
                "class_title": "Class A Common Stock",
                "valid_from": date(2017, 1, 3),
                "known_at": known,
                "ingested_at": known,
                "source": "fixture",
                "provenance": "filing",
            },
        )
    deps = _deps(_store_settings(fixture_store_path), clock=T_END)
    checked = _invoke(deps, "health", "--check")
    assert checked.exit_code == cli.EXIT_CHECK_FAILED
    assert "non_overlapping_listings" in _output(checked)
    # Without --check the report prints and exits 0 whatever the rules say.
    assert _invoke(deps, "health").exit_code == 0


def test_health_on_a_missing_store_exits_non_zero(tmp_path: Path) -> None:
    result = _invoke(_deps(_store_settings(tmp_path / "absent.duckdb")), "health", "--check")
    assert result.exit_code == cli.EXIT_STORE_UNAVAILABLE
    assert not (tmp_path / "absent.duckdb").exists()


def test_health_on_a_locked_store_exits_non_zero(fixture_store_path: Path) -> None:
    holder = duckdb.connect(str(fixture_store_path))  # same process: DuckDB refuses a second
    try:
        result = _invoke(_deps(_store_settings(fixture_store_path), clock=T_END), "health")
    finally:
        holder.close()
    assert result.exit_code == cli.EXIT_STORE_UNAVAILABLE
    assert "busy" in _output(result)


def _edgar_run(path: Path, message: str, at: datetime) -> None:
    with duckdb.connect(str(path)) as conn:
        conn.execute(
            "INSERT INTO ingestion_runs (run_id, source, mode, started_at, finished_at, "
            "status, rows_added, chunk_cursor, message) VALUES (?, 'edgar', 'daily', ?, ?, "
            "'ok', 0, '2020-06-30', ?)",
            [message[:8], at, at, message],
        )


def test_health_warns_when_the_last_edgar_run_quarantined_filings(
    fixture_store_path: Path,
) -> None:
    _edgar_run(
        fixture_store_path,
        "9 securities; failed filings: 2; quarantined: 3; pre-XML delistings: 0",
        datetime(2020, 6, 30, 19, tzinfo=UTC),
    )
    result = _invoke(_deps(_store_settings(fixture_store_path), clock=T_END), "health", "--check")
    assert result.exit_code == 0, _output(result)
    assert "warning" in _output(result) and "3 quarantined" in _output(result)


def test_health_does_not_warn_on_zero_quarantined(fixture_store_path: Path) -> None:
    _edgar_run(
        fixture_store_path,
        "9 securities; failed filings: 0; quarantined: 0; pre-XML delistings: 0",
        datetime(2020, 6, 30, 19, tzinfo=UTC),
    )
    result = _invoke(_deps(_store_settings(fixture_store_path), clock=T_END), "health", "--check")
    assert "quarantined" not in _output(result)


# --- dashboard ----------------------------------------------------------------


def test_dashboard_launches_streamlit_on_the_app(tmp_path: Path) -> None:
    launched: list[list[str]] = []
    result = _invoke(_deps(_store_settings(tmp_path / "s.duckdb"), launched=launched), "dashboard")
    assert result.exit_code == 0, _output(result)
    [argv] = launched
    assert argv[1:4] == ["-m", "streamlit", "run"]
    assert Path(argv[4]).name == "app.py" and Path(argv[4]).is_file()


def test_dashboard_passes_the_launcher_exit_code(tmp_path: Path) -> None:
    deps = _deps(_store_settings(tmp_path / "s.duckdb"))
    deps.launch = lambda argv: 7
    assert _invoke(deps, "dashboard").exit_code == 7


# --- export -------------------------------------------------------------------


def test_export_writes_parquet_per_table(fixture_store_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "export"
    result = _invoke(_deps(_store_settings(fixture_store_path)), "export", str(out))
    assert result.exit_code == 0, _output(result)
    written = {p.stem for p in out.glob("*.parquet")}
    assert written == set(TABLE_NAMES) | set(REGISTRY_TABLE_NAMES)
    with duckdb.connect(str(fixture_store_path), read_only=True) as conn:
        for table in ("prices_daily", "listings"):
            count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            assert count is not None
            assert pq.read_metadata(out / f"{table}.parquet").num_rows == count[0]
    known_at = pq.read_schema(out / "prices_daily.parquet").field("known_at")
    assert str(known_at.type).startswith("timestamp") and "UTC" in str(known_at.type)


def test_export_refuses_a_non_empty_directory(fixture_store_path: Path, tmp_path: Path) -> None:
    out = tmp_path / "export"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    result = _invoke(_deps(_store_settings(fixture_store_path)), "export", str(out))
    assert result.exit_code == cli.EXIT_USAGE
    assert [p.name for p in out.iterdir()] == ["keep.txt"]


def test_export_on_a_missing_store_exits_non_zero(tmp_path: Path) -> None:
    result = _invoke(
        _deps(_store_settings(tmp_path / "absent.duckdb")), "export", str(tmp_path / "o")
    )
    assert result.exit_code == cli.EXIT_STORE_UNAVAILABLE


# --- the default price source --------------------------------------------------


class _Asked(Exception):
    pass


def test_the_default_price_source_resolves_symbols_from_the_store(
    fixture_store_path: Path,
) -> None:
    asked: list[list[str]] = []

    def fetch(symbols: list[str], start: date, end: date) -> dict[str, Any]:
        asked.append(sorted(symbols))
        raise _Asked

    settings = _store_settings(fixture_store_path)
    source = cli.StoreResolvedAlpacaSource(settings, clock=lambda: T_END, fetch_bars=fetch)
    with pytest.raises(_Asked):
        source.bars(["SEC_DUAL_A"], date(2020, 6, 30), date(2020, 6, 30))
    assert asked == [["DUALA"]]
