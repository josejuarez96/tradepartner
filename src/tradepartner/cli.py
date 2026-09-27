"""The `tradepartner` command (Phase 2 plan T19; spec reqs 9, 11, 12 and "CLI").

- `tradepartner ingest [--source alpaca|edgar|all] [--backfill --since DATE]
  [--dry-run]` runs `ingest.ingest_session`, or `backfill.backfill` with
  `--backfill`, over the real sources built from settings: `EdgarFilingSource`
  and an `AlpacaPriceSource` whose ticker resolver is read from the store when
  the price side first fetches (`StorePriceSource`), so it sees the listings the
  EDGAR chunk has just committed. It prints one line per source and exits with
  the result's code: 0 when every source is `ok`, 1 otherwise.
- `tradepartner health [--check]` prints `health.health_report` at the current
  time. `--check` exits 1 when any integrity rule fails and names the rules. It
  also warns, without failing, when the latest EDGAR run row reports
  quarantined accessions (the T11h failure policy): those filings get no
  further request until the owner clears them.
- `tradepartner dashboard` runs the Streamlit shell (`dashboard/app.py`) bound to
  localhost with usage telemetry off (ADR 0011), and exits with Streamlit's code.
- `tradepartner export OUT_DIR` writes every table in the store to
  `OUT_DIR/<table>.parquet` through a read-only connection, and refuses to
  overwrite a file already there.

**Exit codes.** 0 success; 1 a source not `ok`, a failed health check, or no
store; 2 a usage or configuration error found before any work: inconsistent
flags, or a secret the chosen sources need is missing. The missing secret is
named, never shown, and nothing is written.

**Secrets.** The command prints run messages, which `ingest` stores with every
configured secret redacted, and names of variables. It never prints a setting's
value.

`make_app` takes every edge a test replaces (settings, clock, the EDGAR HTTP
client, the price source, the dashboard launcher); `main` is the console script
over the real ones.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

import duckdb
import httpx
import typer

from tradepartner.adapters import alpaca_raw
from tradepartner.adapters.alpaca_prices import AlpacaPriceSource, ListingResolver
from tradepartner.adapters.edgar_source import EdgarFilingSource
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.backfill import backfill
from tradepartner.config import Settings, get_settings
from tradepartner.health import HealthReport, health_report
from tradepartner.ingest import SOURCES, IngestResult, _read, ingest_session
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import StoreLockedError, open_read_only, utc_now
from tradepartner.timeutil import ensure_tz_aware_utc

USAGE_ERROR = 2
DASHBOARD_APP = Path(__file__).resolve().parent / "dashboard" / "app.py"

#: The T11h count `ingest` appends to an EDGAR run message.
_QUARANTINED = re.compile(r"\bquarantined: (\d+)\b")

Clock = Callable[[], datetime]
Launcher = Callable[[list[str]], int]


class StorePriceSource(PriceSource):
    """An `AlpacaPriceSource` whose `ListingResolver` is built from the
    store's listings known at the first fetch, not when the run starts.

    `ingest` fetches prices only after the EDGAR chunk has committed, so on a
    first run the listings the resolver needs do not exist until then. The
    resolver is built once and kept for the rest of the run (a backfill's
    months reuse it). `fetch_bars` and `fetch_actions` default to
    `alpaca_raw` with this command's settings."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock = utc_now,
        fetch_bars: Callable[[list[str], date, date], Any] | None = None,
        fetch_actions: Callable[[list[str], date, date], Any] | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._fetch_bars = fetch_bars or (
            lambda symbols, start, end: alpaca_raw.daily_bars(
                symbols, start, end, settings=settings
            )
        )
        self._fetch_actions = fetch_actions or (
            lambda symbols, start, end: alpaca_raw.corporate_actions(
                symbols, start, end, settings=settings
            )
        )
        self._inner: AlpacaPriceSource | None = None

    def _source(self) -> AlpacaPriceSource:
        if self._inner is None:
            at = ensure_tz_aware_utc(self._clock(), field_name="clock()")
            with _read(self._settings) as conn:  # waits out a writer like ingest's reads
                listings = listings_as_of(conn, at)
            self._inner = AlpacaPriceSource(
                ListingResolver(listings.iter_rows(named=True)),
                fetch_bars=self._fetch_bars,
                fetch_actions=self._fetch_actions,
                settings=self._settings,
            )
        return self._inner

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        """`AlpacaPriceSource.bars` over the store's listings."""
        return self._source().bars(security_ids, start, end)

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        """`AlpacaPriceSource.corporate_actions` over the store's listings."""
        return self._source().corporate_actions(security_ids, start, end)


def _blank(value: Any) -> bool:
    return value is None or not value.get_secret_value().strip()


def _missing_secrets(settings: Settings, source: str) -> list[str]:
    """Names of the secrets the chosen sources need and do not have."""
    missing: list[str] = []
    if source in ("all", "edgar") and _blank(settings.sec_edgar_user_agent):
        missing.append("SEC_EDGAR_USER_AGENT")
    if source in ("all", "alpaca"):
        if _blank(settings.alpaca_api_key):
            missing.append("ALPACA_API_KEY")
        if _blank(settings.alpaca_api_secret):
            missing.append("ALPACA_API_SECRET")
    return missing


def _fail(message: str, code: int) -> typer.Exit:
    typer.echo(message, err=True)
    return typer.Exit(code)


def _print_result(result: IngestResult) -> None:
    for run in result.runs:
        typer.echo(
            f"{run.source}: {run.status}, {run.rows_added} rows, "
            f"cursor {run.chunk_cursor}: {run.message}"
        )


def _store_missing(settings: Settings) -> typer.Exit | None:
    if Path(settings.store.path).exists():
        return None
    return _fail(f"no store at {settings.store.path}; run `tradepartner ingest` first", 1)


def _quarantined(report: HealthReport) -> int:
    """The quarantined count on the latest EDGAR run row, 0 if it names none."""
    for status in report.ingests:
        if status.source == "edgar" and status.latest_message:
            found = _QUARANTINED.findall(status.latest_message)
            return int(found[-1]) if found else 0
    return 0


def _print_report(report: HealthReport) -> None:
    echo = typer.echo
    echo(f"health at {report.t.isoformat()} (session {report.session.isoformat()})")
    echo("last ingest per source:")
    for s in report.ingests:
        last_ok = s.last_ok_finished_at.isoformat() if s.last_ok_finished_at else "never"
        echo(f"  {s.source}: last ok {last_ok} (cursor {s.last_ok_cursor})")
        if s.latest_status is not None:
            echo(f"    latest: {s.latest_status}: {s.latest_message}")
    c = report.coverage
    echo(
        f"coverage: {len(c.live) - len(c.missing)} of {len(c.live)} live names "
        f"have a bar at {c.session.isoformat()} ({c.share:.1%}); bars from {c.first_bar} "
        f"to {c.last_bar} for {c.names_with_bars} names"
    )
    if c.missing:
        echo(f"  missing: {', '.join(c.missing)}")
    echo(
        f"gaps: {report.gaps.names_with_gaps} names, "
        f"{report.gaps.missing_sessions} missing sessions"
    )
    gap = report.survivorship
    echo(
        f"survivorship gap: {gap.missing.height} names missing, "
        f"{gap.count_share:.1%} by count, {gap.size_share:.1%} by size; "
        f"{len(gap.unclassifiable)} unclassifiable, "
        f"{len(gap.truncated_history)} truncated history, "
        f"{len(gap.stale_shares)} stale shares"
    )
    u = report.unclassifiable
    echo(f"unclassifiable: {len(u.unclassifiable)}; unclassified: {len(u.unclassified)}")
    echo(
        f"snapshot_static reliance: {report.static_reliance.count} "
        f"{report.static_reliance.by_table}"
    )
    echo(f"delisted names: {report.delisted.count}")
    for row in report.delisted.frame.iter_rows(named=True):
        echo(f"  {row['security_id']} {row['ticker']} {row['exchange']} ended {row['end_session']}")
    echo(f"settings: {report.settings}")
    echo("integrity:")
    for check in report.integrity:
        verdict = "pass" if check.passed else f"FAIL ({check.violations.height} rows)"
        echo(f"  {check.rule}: {verdict}")


def _export(conn: duckdb.DuckDBPyConnection, out_dir: Path) -> list[str]:
    tables = [
        row[0]
        for row in conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'BASE TABLE' ORDER BY table_name"
        ).fetchall()
    ]
    if out_dir.exists() and not out_dir.is_dir():
        raise _fail(f"{out_dir} exists and is not a directory", 1)
    targets = {table: out_dir / f"{table}.parquet" for table in tables}
    clash = sorted(str(p) for p in targets.values() if p.exists() or p.is_symlink())
    if clash:
        raise _fail(f"refusing to overwrite: {', '.join(clash)}", 1)
    out_dir.mkdir(parents=True, exist_ok=True)
    for table, path in targets.items():
        name = table.replace('"', '""')
        literal = str(path).replace("'", "''")
        conn.execute(f"COPY (SELECT * FROM \"{name}\") TO '{literal}' (FORMAT parquet)")
    return tables


def make_app(
    *,
    settings: Callable[[], Settings] = get_settings,
    clock: Clock = utc_now,
    edgar_client: httpx.Client | None = None,
    price_source: Callable[[Settings], PriceSource] | None = None,
    launcher: Launcher = subprocess.call,
) -> typer.Typer:
    """The `tradepartner` Typer app over the given edges (module docstring)."""
    app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)

    @app.command()
    def ingest(
        source: Annotated[
            str, typer.Option(help="alpaca, edgar or all", show_default=True)
        ] = "all",
        backfill_: Annotated[
            bool, typer.Option("--backfill", help="backfill from --since, resuming")
        ] = False,
        since: Annotated[str | None, typer.Option(help="backfill start, YYYY-MM-DD")] = None,
        dry_run: Annotated[bool, typer.Option(help="roll every chunk back")] = False,
    ) -> None:
        """Bring the store up to the expected session, or backfill it."""
        if source not in ("all", *SOURCES):
            raise _fail(
                f"--source must be all, {' or '.join(SOURCES)}; got {source!r}", USAGE_ERROR
            )
        if backfill_ != (since is not None):
            raise _fail("--backfill and --since go together", USAGE_ERROR)
        if backfill_ and dry_run:
            raise _fail("--dry-run is not available with --backfill", USAGE_ERROR)
        start: date | None = None
        if since is not None:
            try:
                start = date.fromisoformat(since)
            except ValueError:
                raise _fail(f"--since must be YYYY-MM-DD, got {since!r}", USAGE_ERROR) from None
        s = settings()
        missing = _missing_secrets(s, source)
        if missing:
            raise _fail(
                f"missing required secret(s): {', '.join(missing)}. "
                "Set them in .env (see .env.example).",
                USAGE_ERROR,
            )
        filings = EdgarFilingSource(s, client=edgar_client, clock=clock)
        prices = price_source(s) if price_source else StorePriceSource(s, clock=clock)
        if start is not None:
            result = backfill(
                s, prices=prices, filings=filings, since=start, source=source, clock=clock
            )
        else:
            result = ingest_session(
                s, prices=prices, filings=filings, source=source, clock=clock, dry_run=dry_run
            )
        _print_result(result)
        raise typer.Exit(result.exit_code)

    @app.command()
    def health(
        check: Annotated[
            bool, typer.Option(help="exit non-zero if any integrity rule fails")
        ] = False,
    ) -> None:
        """Print the data-health report."""
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        t = ensure_tz_aware_utc(clock(), field_name="clock()")
        try:
            with open_read_only(s) as conn:
                report = health_report(conn, t, s)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except (duckdb.CatalogException, duckdb.BinderException) as exc:
            raise _fail(
                f"the store has no usable schema; run `tradepartner ingest`: {exc}", 1
            ) from None
        _print_report(report)
        if (count := _quarantined(report)) > 0:
            typer.echo(
                f"warning: the last EDGAR run reports {count} quarantined accessions; "
                "they get no further request until their failed_filings.json entries "
                "are cleared",
                err=True,
            )
        if check and not report.ok:
            raise _fail(f"health check failed: {', '.join(report.failures)}", 1)

    @app.command()
    def dashboard() -> None:
        """Run the Streamlit dashboard on localhost."""
        argv = [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            str(DASHBOARD_APP),
            "--server.address",
            "localhost",
            "--browser.gatherUsageStats",
            "false",
        ]
        raise typer.Exit(launcher(argv))

    @app.command()
    def export(
        out_dir: Annotated[Path, typer.Argument(help="directory for <table>.parquet files")],
    ) -> None:
        """Write every store table to OUT_DIR as Parquet."""
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_read_only(s) as conn:
                tables = _export(conn, out_dir)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        typer.echo(f"exported {len(tables)} tables to {out_dir}")

    return app


def main() -> None:
    """Console-script entry point (`[project.scripts] tradepartner`)."""
    make_app()()


if __name__ == "__main__":
    main()
