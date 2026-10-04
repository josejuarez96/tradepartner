"""The `tradepartner` command (Phase 2 plan T19; spec reqs 9, 11, 12 and "CLI";
Phase 3 plan T42, backtest spec reqs 10-12 and 16).

- `tradepartner ingest [--source alpaca|edgar|all] [--backfill --since DATE]
  [--dry-run]` runs `ingest.ingest_session`, or `backfill.backfill` with
  `--backfill`, over the real sources built from settings: `EdgarFilingSource`
  and an `AlpacaPriceSource` whose ticker resolver is read from the store when
  the price side first fetches (`StorePriceSource`), so it sees the listings the
  EDGAR chunk has just committed. It prints one line per source and exits with
  the result's code: 0 when every source is `ok`, 1 otherwise.
- `tradepartner health [--check] [--jumps-before DATE]` prints
  `health.health_report` at the current time; `--jumps-before` limits the
  price-jump review list (#787) to sessions before DATE, and the
  shares-outlier review list (#845) to facts dated before it, so the owner
  can review a hypothesis's in-sample entries without reading its holdout
  period.
  `--check` exits 1 when any integrity rule fails and names the rules. It
  also warns, without failing, when the latest EDGAR run row reports
  quarantined accessions (the T11h failure policy): those filings get no
  further request until the owner clears them.
- `tradepartner dashboard` runs the Streamlit shell (`dashboard/app.py`) bound to
  localhost with usage telemetry off (ADR 0011), and exits with Streamlit's code.
- `tradepartner export OUT_DIR` writes every table in the store to
  `OUT_DIR/<table>.parquet` through a read-only connection, and refuses to
  overwrite a file already there.

Phase 3 (T42):

- `tradepartner backtest <hypothesis> [--start] [--end] [--spend-holdout
  --holdout-reason] [--holdout-repeat] [--override-gap --gap-reason] [--note]`
  runs `backtest.run.run_hypothesis` on `settings.store.path`: there is no
  `--synthetic` flag and no store-path option. It prints the trial id and
  status, the base-cost metrics table, the strategy per cost level, the gap
  maxima, the DSR and the flags, and exits 0 for `ok`, 1 for `failed` and 2 for
  any refusal. `--spend-holdout` without a non-blank `--holdout-reason`, a reason
  without its flag, or a bad date is refused before any trial is opened; an
  unregistered slug likewise. `--override-gap` without `--gap-reason` is left to
  the run, which records it as `refused_gap`.
- `tradepartner hypothesis register <file>` registers the file and prints the
  full frozen set and its hash (spec req 10).
- `tradepartner trials [--hypothesis] [--include-synthetic]` lists trials newest
  first, `unfinished`, failed and refused ones with their message; synthetic
  trials only when asked for.
- `tradepartner decision gap-signoff --trial <id> --reason` appends a
  `gap_signoff` owner decision for an `ok` trial, with its gap maxima and the
  frozen threshold (spec req 12, ADR 0003 rule 8).

Text that can carry an exception's words (run messages and tracebacks) is
passed through the `cli_record` fixture scrub before it is printed.

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

import json
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
from tradepartner.adapters.alpaca_prices import AlpacaPriceSource
from tradepartner.adapters.edgar_source import EdgarFilingSource
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.backfill import backfill
from tradepartner.backtest.holdout import GAP_THRESHOLD_KEY, Flags, Reasons
from tradepartner.backtest.hypothesis import HypothesisFileError, register
from tradepartner.backtest.metrics import METRIC_KEYS
from tradepartner.backtest.run import RunOutcome, run_hypothesis
from tradepartner.cli_record import _configured_secrets, scrub_text
from tradepartner.config import Settings, get_settings
from tradepartner.health import HealthReport, health_report
from tradepartner.ingest import SOURCES, IngestResult, _read, ingest_session
from tradepartner.repair import RepairRefused, repair_resolution, store_resolver
from tradepartner.store import registry, schema
from tradepartner.store.db import StoreLockedError, open_for_write, open_read_only, utc_now
from tradepartner.timeutil import ensure_tz_aware_utc

USAGE_ERROR = 2
DASHBOARD_APP = Path(__file__).resolve().parent / "dashboard" / "app.py"

#: The T11h count `ingest` appends to an EDGAR run message.
_QUARANTINED = re.compile(r"\bquarantined: (\d+)\b")

Clock = Callable[[], datetime]
Launcher = Callable[[list[str]], int]


class StorePriceSource(PriceSource):
    """An `AlpacaPriceSource` whose `ListingResolver` is built from the
    store's listings known at the first fetch, not when the run starts,
    with the `registrant_evidence` of the facts and listing ends known then.

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
                resolver = store_resolver(conn, at, self._settings)  # #793 evidence included
            self._inner = AlpacaPriceSource(
                resolver,
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

    def resolution_summary(self) -> str:
        """`AlpacaPriceSource.resolution_summary`, `""` before the first fetch."""
        return "" if self._inner is None else self._inner.resolution_summary()


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
    jumps = report.price_jumps
    shown = "" if jumps.before is None else f" before {jumps.before.isoformat()}"
    echo(f"price jumps{shown}: {jumps.pending.height} to review, {jumps.frame.height} in all")
    for row in jumps.pending.iter_rows(named=True):
        echo(
            f"  {row['security_id']}@{row['session']} {row['prev_close']} -> {row['close']} "
            f"(x{row['ratio']:.2f} since {row['prev_session']})"
        )
    outliers = report.shares_outliers
    shown = "" if outliers.before is None else f" before {outliers.before.isoformat()}"
    echo(
        f"shares outliers{shown}: {outliers.pending.height} to review, "
        f"{outliers.frame.height} in all"
    )
    for row in outliers.pending.iter_rows(named=True):
        echo(
            f"  {row['security_id']}@{row['as_of_date']} {row['value']:.0f} "
            f"(x{row['ratio']:.4g} over {row['baseline_value']:.0f} "
            f"as of {row['baseline_as_of']})"
        )
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


# --- Phase 3: backtest, hypothesis register, trials, decision (plan T42) ------------

#: `backtest`'s exit code per trial status (spec req 16): 0 ok, 1 failed, 2 refused.
STATUS_EXIT: dict[str, int] = {
    "ok": 0,
    "failed": 1,
    "refused_window": USAGE_ERROR,
    "refused_holdout": USAGE_ERROR,
    "refused_gap": USAGE_ERROR,
}
_REGISTERED_BY = "owner"
_SERIES_ORDER = ("strategy", "SPY", "MTUM")
#: The metrics shown per cost level, strategy series only.
_LEVEL_METRICS = ("cagr", "sharpe_annual", "excess_cagr_spy", "max_drawdown", "cost_drag")


def _scrubbed(text: str, settings: Settings) -> str:
    """`text` with every configured secret, email and key-shaped token replaced,
    by the `cli_record` fixture scrub, for text that can carry an exception's words."""
    return scrub_text(text, secrets=_configured_secrets(settings))[0]


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _bps(level: float) -> str:
    return f"{level:g} bps"


def _parse_day(flag: str, value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise _fail(f"{flag} must be YYYY-MM-DD, got {value!r}", USAGE_ERROR) from None


def _blank_text(value: str | None) -> bool:
    return value is None or not value.strip()


def _row(conn: duckdb.DuckDBPyConnection, table: str, trial_id: int) -> dict[str, Any] | None:
    cursor = conn.execute(f"SELECT * FROM {table} WHERE trial_id = ?", [trial_id])
    names = [d[0] for d in cursor.description]
    row = cursor.fetchone()
    return None if row is None else dict(zip(names, row, strict=True))


def _print_trial(conn: duckdb.DuckDBPyConnection, outcome: RunOutcome, settings: Settings) -> None:
    """The trial id and status, its message, and for `ok` the metrics table, the
    per-level table, the gap maxima and the flags (spec req 16)."""
    trial = _row(conn, "trials", outcome.trial_id) or {}
    result = _row(conn, "trial_results", outcome.trial_id) or {}
    typer.echo(f"trial {outcome.trial_id}: {outcome.status}")
    if result.get("message"):
        typer.echo(f"  {_scrubbed(str(result['message']), settings)}")
    typer.echo(
        f"  kind {trial.get('kind')}, window {trial.get('start_session')} to "
        f"{trial.get('end_session')}"
    )
    if outcome.status != "ok":
        return
    hypothesis = registry.get_hypothesis_by_id(conn, int(trial["hypothesis_id"]))
    base = float(hypothesis.params[registry.BASE_COST_KEY])
    rows = conn.execute(
        "SELECT series, cost_per_side_bps, metric, value FROM trial_metrics WHERE trial_id = ?",
        [outcome.trial_id],
    ).fetchall()
    values = {(s, float(level), m): v for s, level, m, v in rows}
    metrics = [m for m in METRIC_KEYS if any(k[2] == m for k in values)]
    width = max(len(m) for m in metrics) if metrics else 6
    typer.echo(f"metrics at the base cost, {_bps(base)} per side:")
    typer.echo(f"  {'metric':<{width}}  " + "  ".join(f"{s:>10}" for s in _SERIES_ORDER))
    for metric in metrics:
        cells = "  ".join(f"{_fmt(values.get((s, base, metric))):>10}" for s in _SERIES_ORDER)
        typer.echo(f"  {metric:<{width}}  {cells}")
    levels = sorted({k[1] for k in values})
    typer.echo("strategy per cost level:")
    typer.echo(f"  {'level':<8}  " + "  ".join(f"{m:>15}" for m in _LEVEL_METRICS))
    for level in levels:
        cells = "  ".join(f"{_fmt(values.get(('strategy', level, m))):>15}" for m in _LEVEL_METRICS)
        typer.echo(f"  {_bps(level):<8}  {cells}")
    typer.echo(
        f"gap max: count share {_fmt(result.get('gap_max_count_share'))}, "
        f"size share {_fmt(result.get('gap_max_size_share'))}"
    )
    typer.echo(
        f"dsr {_fmt(result.get('dsr'))}, dsr excess SPY {_fmt(result.get('dsr_excess'))} "
        f"(basis {result.get('dsr_basis')}, n trials {result.get('n_trials')})"
    )
    typer.echo(
        f"flags: red flag {_fmt(result.get('red_flag'))}, synthetic "
        f"{_fmt(trial.get('synthetic'))}, holdout repeat {_fmt(trial.get('holdout_repeat'))}"
    )
    for label, key in (
        ("holdout reason", "holdout_reason"),
        ("gap override", "gap_override_reason"),
    ):
        if trial.get(key):
            typer.echo(f"{label}: {_scrubbed(str(trial[key]), settings)}")


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

    @app.command("repair-resolution")
    def repair_resolution_(
        dry_run: Annotated[
            bool, typer.Option(help="count what would be deleted and change nothing")
        ] = False,
        expect_bar_rows: Annotated[
            int | None, typer.Option(help="the dry run's bar row count (required to delete)")
        ] = None,
        expect_action_rows: Annotated[
            int | None, typer.Option(help="the dry run's action row count (required to delete)")
        ] = None,
    ) -> None:
        """Delete Alpaca bars and actions the resolver no longer assigns to their security."""
        expect = None
        if not dry_run:
            if expect_bar_rows is None or expect_action_rows is None:
                raise _fail(
                    "run --dry-run first, then pass its counts as --expect-bar-rows and "
                    "--expect-action-rows",
                    USAGE_ERROR,
                )
            expect = (expect_bar_rows, expect_action_rows)
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            result = repair_resolution(s, clock=clock, dry_run=dry_run, expect=expect)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except RepairRefused as exc:
            raise _fail(f"repair refused: {exc}", 1) from None
        typer.echo(result.summary())
        for line in result.lines():
            typer.echo(line)

    @app.command()
    def health(
        check: Annotated[
            bool, typer.Option(help="exit non-zero if any integrity rule fails")
        ] = False,
        jumps_before: Annotated[
            str | None,
            typer.Option(
                help=(
                    "list only price jumps and shares outliers before this day "
                    "(a holdout start), YYYY-MM-DD"
                )
            ),
        ] = None,
    ) -> None:
        """Print the data-health report."""
        cutoff = _parse_day("--jumps-before", jumps_before)
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        t = ensure_tz_aware_utc(clock(), field_name="clock()")
        try:
            with open_read_only(s) as conn:
                report = health_report(conn, t, s, jumps_before=cutoff)
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

    @app.command()
    def backtest(
        hypothesis: Annotated[str, typer.Argument(help="the registered hypothesis slug")],
        start: Annotated[str | None, typer.Option(help="first session, YYYY-MM-DD")] = None,
        end: Annotated[str | None, typer.Option(help="last session, YYYY-MM-DD")] = None,
        spend_holdout: Annotated[
            bool, typer.Option(help="run a window touching the holdout (needs a reason)")
        ] = False,
        holdout_reason: Annotated[str | None, typer.Option(help="why the holdout is spent")] = None,
        holdout_repeat: Annotated[
            bool, typer.Option(help="spend this hypothesis's holdout again")
        ] = False,
        override_gap: Annotated[
            bool, typer.Option(help="run past the survivorship-gap gate (needs a reason)")
        ] = False,
        gap_reason: Annotated[str | None, typer.Option(help="why the gap is accepted")] = None,
        note: Annotated[str | None, typer.Option(help="a note stored with the trial")] = None,
    ) -> None:
        """Run a registered hypothesis as one trial on the store."""
        first, last = _parse_day("--start", start), _parse_day("--end", end)
        if spend_holdout and _blank_text(holdout_reason):
            raise _fail("--spend-holdout needs --holdout-reason", USAGE_ERROR)
        if holdout_reason is not None and not spend_holdout:
            raise _fail("--holdout-reason goes with --spend-holdout", USAGE_ERROR)
        if gap_reason is not None and not override_gap:
            raise _fail("--gap-reason goes with --override-gap", USAGE_ERROR)
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        if s.store.path != get_settings().store.path:
            # run_hypothesis loads its own settings; the result is read back through `s`.
            raise _fail("backtest runs only on the loaded settings' store", USAGE_ERROR)
        try:
            outcome = run_hypothesis(
                hypothesis,
                first,
                last,
                Flags(
                    spend_holdout=spend_holdout,
                    holdout_repeat=holdout_repeat,
                    override_gap=override_gap,
                ),
                reasons=Reasons(holdout_reason=holdout_reason, gap_reason=gap_reason),
                note=note,
            )
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except (registry.RegistryError, ValueError) as exc:
            # Raised before a trial is opened (unknown slug, stored parameters that
            # fail their hash, a frozen window that cannot be resolved): a refusal.
            raise _fail(_scrubbed(f"{type(exc).__name__}: {exc}", s), USAGE_ERROR) from None
        except Exception as exc:
            raise _fail(_scrubbed(f"{type(exc).__name__}: {exc}", s), 1) from None
        with open_read_only(s) as conn:
            _print_trial(conn, outcome, s)
        if outcome.error:
            typer.echo(_scrubbed(outcome.error, s), err=True)
        raise typer.Exit(STATUS_EXIT[outcome.status])

    hypothesis_app = typer.Typer(no_args_is_help=True, help="Pre-register hypotheses.")
    app.add_typer(hypothesis_app, name="hypothesis")

    @hypothesis_app.command("register")
    def hypothesis_register(
        file: Annotated[Path, typer.Argument(help="the hypothesis file (docs/hypotheses/*.md)")],
    ) -> None:
        """Register a hypothesis file and print its frozen parameters and their hash."""
        s = settings()
        if not file.is_file():
            raise _fail(f"no hypothesis file at {file}", USAGE_ERROR)
        try:
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                record = register(conn, file, registered_by=_REGISTERED_BY, settings=s)
        except (HypothesisFileError, registry.RegistryError) as exc:
            raise _fail(_scrubbed(str(exc), s), USAGE_ERROR) from None
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        typer.echo(
            f"hypothesis {record.hypothesis_id}: {record.slug} ({record.family}), "
            f"in-sample from {record.in_sample_start}, holdout {record.holdout_start} "
            f"to {record.holdout_end}"
        )
        typer.echo(f"frozen parameters sha256 {record.params_sha256}:")
        for key in sorted(record.params):
            typer.echo(f"  {key} = {json.dumps(record.params[key], default=str)}")

    @app.command()
    def trials(
        hypothesis: Annotated[str | None, typer.Option(help="only this slug")] = None,
        include_synthetic: Annotated[bool, typer.Option(help="list synthetic trials too")] = False,
    ) -> None:
        """List trials newest first, with failed, refused and unfinished ones."""
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_read_only(s) as conn:
                schema.init_schema(conn)  # read-only: checks the version, never migrates
                listed = registry.list_trials(conn, hypothesis, include_synthetic)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except schema.RegistryNotInitialised:
            raise _fail("registry not initialised: register a hypothesis first", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        typer.echo("id  hypothesis  kind  window  status  flags  message")
        for t in listed:
            flags = [
                name
                for name, on in (("synthetic", t.synthetic), ("repeat", t.holdout_repeat))
                if on
            ]
            message = _scrubbed(t.message, s).strip() if t.message else ""
            typer.echo(
                f"{t.trial_id}  {t.slug}  {t.kind}  {t.start_session}..{t.end_session}  "
                f"{t.status}  {','.join(flags) or '-'}  {message}"
            )

    decision_app = typer.Typer(no_args_is_help=True, help="Record an owner decision.")
    app.add_typer(decision_app, name="decision")

    @decision_app.command("gap-signoff")
    def gap_signoff(
        trial: Annotated[int, typer.Option(help="the ok trial whose gap is signed off")],
        reason: Annotated[str, typer.Option(help="why the recorded gap is accepted")],
    ) -> None:
        """Sign off a trial's recorded survivorship gap (ADR 0003 rule 8)."""
        if not reason.strip():
            raise _fail("--reason must not be blank", USAGE_ERROR)
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                trial_row = _row(conn, "trials", trial)
                result = _row(conn, "trial_results", trial)
                if trial_row is None:
                    raise _fail(f"trial {trial} does not exist", USAGE_ERROR)
                if result is None or result["status"] != "ok":
                    status = "unfinished" if result is None else result["status"]
                    raise _fail(f"trial {trial} is {status}; only an ok trial", USAGE_ERROR)
                record = registry.get_hypothesis_by_id(conn, int(trial_row["hypothesis_id"]))
                decision_id = registry.record_decision(
                    conn,
                    kind="gap_signoff",
                    reason=reason,
                    values={
                        "gap_max_count_share": result["gap_max_count_share"],
                        "gap_max_size_share": result["gap_max_size_share"],
                        "count_share_threshold": record.params[GAP_THRESHOLD_KEY],
                        "start_session": str(trial_row["start_session"]),
                        "end_session": str(trial_row["end_session"]),
                    },
                    hypothesis_id=record.hypothesis_id,
                    trial_id=trial,
                )
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        typer.echo(f"decision {decision_id}: gap_signoff for trial {trial} ({record.slug})")

    return app


def main() -> None:
    """Console-script entry point (`[project.scripts] tradepartner`)."""
    make_app()()


if __name__ == "__main__":
    main()
