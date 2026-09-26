"""The `tradepartner` command line (spec reqs 9, 11, 12; plan T19).

Commands: `ingest`, `health [--check]`, `dashboard` and `export`. Each is a
thin layer over the library: `ingest.ingest_session` / `backfill.backfill`,
`health.health_report`, the Streamlit app in `dashboard/app.py`, and a
read-only DuckDB `COPY` per table. Nothing here computes a number.

**Exit codes.** `0` success. `1` an ingest source ended non-`ok`
(`IngestResult.exit_code`, spec req 9). `2` a usage error: bad options, or a
credential the chosen sources need is missing, found before any request or
store write. `3` `health --check` found a failing integrity rule (spec req
11). `4` the store is missing, locked or unreadable for a read-only command.
`dashboard` returns Streamlit's own exit code.

**Secrets.** Nothing printed here includes a secret: ingest run messages are
already redacted by `ingest._clean`, and every other message this module
prints passes through `_redact` with the configured secrets.

**Injection.** `build_app(CliDeps(...))` takes the settings loader, the
clock, the EDGAR HTTP client, the price source and the dashboard launcher,
so tests run every command offline; `app` is the production instance.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

import duckdb
import httpx
import typer

from tradepartner.adapters.alpaca_prices import (
    AlpacaPriceSource,
    FetchActions,
    FetchBars,
    ListingResolver,
)
from tradepartner.adapters.edgar_source import EdgarFilingSource
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.backfill import backfill
from tradepartner.config import Settings, get_settings
from tradepartner.health import HealthReport, health_report
from tradepartner.ingest import SOURCES, IngestResult, ingest_session
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import StoreLockedError, open_read_only, utc_now
from tradepartner.store.schema import REGISTRY_TABLE_NAMES, TABLE_NAMES
from tradepartner.timeutil import ensure_tz_aware_utc

EXIT_OK = 0
EXIT_SOURCE_FAILED = 1
EXIT_USAGE = 2
EXIT_CHECK_FAILED = 3
EXIT_STORE_UNAVAILABLE = 4

_APP_PATH = Path(__file__).resolve().parent / "dashboard" / "app.py"
#: T11f's run-message field (`failed filings: K; quarantined: Q; ...`).
_QUARANTINED = re.compile(r"\bquarantined: (\d+)")


class StoreResolvedAlpacaSource(PriceSource):
    """`AlpacaPriceSource` whose `ListingResolver` is built, on each call,
    from the store's listings known at the clock. Built lazily because the
    EDGAR chunk of the same run writes the listings the price side reads."""

    def __init__(
        self,
        settings: Settings,
        *,
        clock: Callable[[], datetime] = utc_now,
        fetch_bars: FetchBars | None = None,
        fetch_actions: FetchActions | None = None,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._fetchers: dict[str, Any] = {}
        if fetch_bars is not None:
            self._fetchers["fetch_bars"] = fetch_bars
        if fetch_actions is not None:
            self._fetchers["fetch_actions"] = fetch_actions

    def _source(self, security_ids: Sequence[str]) -> AlpacaPriceSource:
        t = ensure_tz_aware_utc(self._clock(), field_name="clock()")
        with open_read_only(self._settings) as conn:
            listings = listings_as_of(conn, t, list(security_ids)).to_dicts()
        return AlpacaPriceSource(
            ListingResolver(listings), settings=self._settings, **self._fetchers
        )

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        return self._source(security_ids).bars(security_ids, start, end)

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        return self._source(security_ids).corporate_actions(security_ids, start, end)


def _default_launch(argv: list[str]) -> int:
    return subprocess.call(argv)  # argv is built here, never from input


@dataclass
class CliDeps:
    """What the commands need from the outside world (module docstring)."""

    settings: Callable[[], Settings] = get_settings
    http_client: Callable[[Settings], httpx.Client | None] = lambda _settings: None
    clock: Callable[[], datetime] = utc_now
    price_source: Callable[[Settings], PriceSource] | None = None
    launch: Callable[[list[str]], int] = field(default=_default_launch)

    def prices(self, settings: Settings) -> PriceSource:
        if self.price_source is not None:
            return self.price_source(settings)
        return StoreResolvedAlpacaSource(settings, clock=self.clock)


def _secrets(settings: Settings) -> list[str]:
    values = (settings.alpaca_api_key, settings.alpaca_api_secret, settings.sec_edgar_user_agent)
    return [v.get_secret_value() for v in values if v is not None and v.get_secret_value().strip()]


def _redact(text: str, settings: Settings) -> str:
    for secret in _secrets(settings):
        text = text.replace(secret, "[redacted]")
    return text


def _fail(message: str, code: int, settings: Settings | None = None) -> typer.Exit:
    typer.echo(_redact(message, settings) if settings is not None else message, err=True)
    return typer.Exit(code)


def _blank(value: Any) -> bool:
    return value is None or not value.get_secret_value().strip()


def _missing_credentials(settings: Settings, source: str) -> list[str]:
    """The environment variables `source` needs that are unset or blank."""
    missing: list[str] = []
    if source in ("all", "edgar") and _blank(settings.sec_edgar_user_agent):
        missing.append("SEC_EDGAR_USER_AGENT")
    if source in ("all", "alpaca"):
        for name, value in (
            ("ALPACA_API_KEY", settings.alpaca_api_key),
            ("ALPACA_API_SECRET", settings.alpaca_api_secret),
        ):
            if _blank(value):
                missing.append(name)
    return missing


def _print_result(result: IngestResult) -> None:
    for run in result.runs:
        typer.echo(
            f"{run.source}: {run.status}, {run.rows_added} rows, cursor {run.chunk_cursor}: "
            f"{run.message}"
        )


@contextmanager
def _read_store(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    """A read-only connection, or exit 4 naming why there is none. Never
    creates the store file."""
    if not Path(settings.store.path).exists():
        raise _fail(
            f"no store at {settings.store.path}; run `tradepartner ingest` first",
            EXIT_STORE_UNAVAILABLE,
        )
    try:
        with open_read_only(settings) as conn:
            yield conn
    except StoreLockedError as exc:
        raise _fail(f"store busy: {exc}", EXIT_STORE_UNAVAILABLE, settings) from exc
    except duckdb.Error as exc:
        raise _fail(f"store unreadable: {exc}", EXIT_STORE_UNAVAILABLE, settings) from exc


def _quarantine_warning(report: HealthReport) -> str | None:
    """T11f: the latest EDGAR run row's quarantined count, when above zero."""
    for status in report.ingests:
        if status.source == "edgar" and status.latest_message:
            match = _QUARANTINED.search(status.latest_message)
            if match and int(match.group(1)) > 0:
                return (
                    f"warning: the last EDGAR run reports {match.group(1)} quarantined "
                    "filing(s); review failed_filings.json under edgar.cache_dir"
                )
    return None


def _report_lines(report: HealthReport) -> list[str]:
    lines = [f"health at {report.t.isoformat()} (session {report.session})"]
    for status in report.ingests:
        last_ok = status.last_ok_finished_at.isoformat() if status.last_ok_finished_at else "never"
        lines.append(f"  ingest {status.source}: last ok {last_ok}; latest {status.latest_status}")
    cov = report.coverage
    lines += [
        f"  coverage: {len(cov.live) - len(cov.missing)}/{len(cov.live)} live names "
        f"({cov.share:.1%}); bars {cov.first_bar} to {cov.last_bar}",
        f"  gaps: {report.gaps.names_with_gaps} names, "
        f"{report.gaps.missing_sessions} missing sessions",
        f"  survivorship gap: {report.survivorship.missing.height} names "
        f"({report.survivorship.count_share:.1%})",
        f"  unclassifiable: {report.unclassifiable.count}",
        f"  snapshot_static reliance: {report.static_reliance.count}",
        f"  delisted names: {report.delisted.count}",
        f"  settings: {report.settings}",
    ]
    lines.append(
        "  integrity: ok" if report.ok else f"  integrity: FAILED {', '.join(report.failures)}"
    )
    return lines


def build_app(deps: CliDeps | None = None) -> typer.Typer:
    """The Typer app over `deps` (module docstring)."""
    deps = deps or CliDeps()
    app = typer.Typer(no_args_is_help=True, add_completion=False)

    @app.command()
    def ingest(
        source: Annotated[str, typer.Option(help="all, alpaca or edgar")] = "all",
        backfill_: Annotated[
            bool, typer.Option("--backfill", help="backfill month by month from --since")
        ] = False,
        since: Annotated[
            str | None, typer.Option(help="backfill start, YYYY-MM-DD (with --backfill)")
        ] = None,
        dry_run: Annotated[bool, typer.Option("--dry-run", help="roll every chunk back")] = False,
    ) -> None:
        """Ingest the expected session, or backfill from --since (spec req 9)."""
        settings = deps.settings()
        if source not in ("all", *SOURCES):
            raise _fail(f"--source must be all or one of {', '.join(SOURCES)}", EXIT_USAGE)
        if backfill_ != (since is not None):
            raise _fail("--backfill and --since go together", EXIT_USAGE)
        if backfill_ and dry_run:
            raise _fail("--dry-run is not supported with --backfill", EXIT_USAGE)
        start: date | None = None
        if since is not None:
            try:
                start = date.fromisoformat(since)
            except ValueError as exc:
                raise _fail(f"--since must be YYYY-MM-DD, got {since!r}", EXIT_USAGE) from exc
        missing = _missing_credentials(settings, source)
        if missing:
            raise _fail(
                f"{', '.join(missing)} must be set (see .env.example) for --source {source}",
                EXIT_USAGE,
            )
        filings = EdgarFilingSource(settings, client=deps.http_client(settings), clock=deps.clock)
        prices = deps.prices(settings)
        if start is not None:
            result = backfill(
                settings,
                prices=prices,
                filings=filings,
                since=start,
                source=source,
                clock=deps.clock,
            )
        else:
            result = ingest_session(
                settings,
                prices=prices,
                filings=filings,
                source=source,
                clock=deps.clock,
                dry_run=dry_run,
            )
        _print_result(result)
        raise typer.Exit(result.exit_code)

    @app.command()
    def health(
        check: Annotated[
            bool, typer.Option("--check", help="exit 3 when any integrity rule fails")
        ] = False,
    ) -> None:
        """Print the data-health report (spec req 11)."""
        settings = deps.settings()
        t = ensure_tz_aware_utc(deps.clock(), field_name="clock()")
        with _read_store(settings) as conn:
            report = health_report(conn, t, settings)
        for line in _report_lines(report):
            typer.echo(line)
        warning = _quarantine_warning(report)
        if warning:
            typer.echo(_redact(warning, settings), err=True)
        if check and not report.ok:
            raise _fail(f"health check failed: {', '.join(report.failures)}", EXIT_CHECK_FAILED)

    @app.command()
    def dashboard() -> None:
        """Launch the Streamlit dashboard (spec req 12)."""
        code = deps.launch([sys.executable, "-m", "streamlit", "run", str(_APP_PATH)])
        raise typer.Exit(code)

    @app.command()
    def export(
        out_dir: Annotated[Path, typer.Argument(help="new or empty directory for the files")],
    ) -> None:
        """Write every store table to `<out_dir>/<table>.parquet`."""
        settings = deps.settings()
        if out_dir.exists() and (not out_dir.is_dir() or any(out_dir.iterdir())):
            raise _fail(f"{out_dir} exists and is not an empty directory", EXIT_USAGE)
        with _read_store(settings) as conn:
            present = {
                row[0]
                for row in conn.execute(
                    "SELECT table_name FROM information_schema.tables"
                ).fetchall()
            }
            tables = [t for t in (*TABLE_NAMES, *REGISTRY_TABLE_NAMES) if t in present]
            out_dir.mkdir(parents=True, exist_ok=True)
            for table in tables:
                target = out_dir / f"{table}.parquet"
                # `table` comes from the schema's own name list, never from input.
                conn.execute(f"COPY {table} TO '{target.as_posix()}' (FORMAT parquet)")
        typer.echo(f"exported {len(tables)} tables to {out_dir}")

    return app


app = build_app()


def main() -> None:
    """Entry point for `[project.scripts]` (added by the T19 PR, not this spike)."""
    app()


if __name__ == "__main__":
    main()
