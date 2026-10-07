"""The `tradepartner` command (Phase 2 plan T19; spec reqs 9, 11, 12 and "CLI";
Phase 3 plan T42, backtest spec reqs 10-12 and 16).

- `tradepartner ingest [--source alpaca|edgar|all] [--backfill --since DATE]
  [--dry-run]` runs `ingest.ingest_session`, or `backfill.backfill` with
  `--backfill`, over the real sources built from settings: `EdgarFilingSource`
  and an `AlpacaPriceSource` whose ticker resolver is read from the store when
  the price side first fetches (`StorePriceSource`), so it sees the listings the
  EDGAR chunk has just committed. It prints one line per source and exits with
  the result's code: 0 when every source is `ok`, 1 otherwise.
  `--backfill --since DATE --source alpaca --fill-holes [--dry-run]` runs
  `backfill.fill_holes` instead (#831): the dry run lists the (security,
  month) holes in the backfill's committed months, needs no Alpaca secret
  and fetches nothing; the real run refetches them and exits 0 only when
  every month it fetched is `filled`. Only holes the store's resolver can
  assign are listed or fetched; the rest are counted per reason (#876). A
  rename lead's gap inside a month with stored bars is a hole too (#891).
  `--security ID` (repeatable, or comma-separated) limits both to the named
  securities; a named id with no hole fetched is listed with why, not an
  error. `--bulk-from-cache` (#660) builds the EDGAR source with
  `reuse_cached`: company facts come from the cached `companyfacts.zip`
  with no request for it. `--rebuild-statement-facts` deletes and
  re-ingests `statement_facts` in the EDGAR chunk's transaction and needs
  `edgar.statement_facts_enabled`. Both are off by default and refused with
  `--backfill` or `--source alpaca` (usage error, 2).
- `tradepartner backfill-benchmark SYMBOL --since DATE [--cik --name
  --exchange]` runs `backfill.backfill_benchmark` (#840): the owner's one-off
  for a configured benchmark the store lacks (MTUM), seeded from the three
  options when no benchmark security lists the symbol, then fetched month by
  month through the same `StorePriceSource`. It needs the Alpaca keys, prints
  one line per month and exits like `ingest`; a refused symbol or identity is
  a usage error (2), with nothing fetched.
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
- `tradepartner master-retract [--apply --expect-rows N --expect-digest D
  [--allow-traded]]` runs `retract.master_retract` (#859): the dry run (the
  default) builds the master as an EDGAR ingest would and lists every stored
  `securities` or `listings` filing row the current rules no longer derive,
  with a digest, changing nothing; with `--apply` and the dry run's count and
  digest it retracts them (refused, writing nothing, on another set, or on a
  traded name without `--allow-traded`). Needs the EDGAR user agent.
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

Research registry (research-registry spec req 11 and req 14; plan T83):

- `tradepartner experiment register <file>` registers an experiment file from
  `research.experiments_dir` and prints its registration id and hashes.
- `tradepartner experiment open <slug> --dataset <id> --split <split> --config
  <json file> [--configurations <n>] [--spend-holdout --holdout-reason]
  [--holdout-repeat] [--note]` opens a run on `settings.store.path` and prints
  its id: exit 0 when it opens, 2 on a gate's refusal, which is recorded as the
  run's result. `--spend-holdout` without a non-blank reason, a reason without
  its flag, `--holdout-repeat` without `--spend-holdout`, `--configurations`
  under 1 and a config that is not a JSON object are refused before anything is
  written, as are an unknown slug or dataset.
- `tradepartner experiment abandon --run <id> --reason` appends the run's
  `abandoned` result.
- `tradepartner experiments [--registration] [--family] [--kind]
  [--include-synthetic]` lists runs newest first with outcome, verdict,
  confirmatory, split, holdout flags and unfinished rows.
- `tradepartner dataset register --name --version --path --event-start
  --event-end [--event-column] [--split-json] [--sealed <split>]...
  [--sealed-period <start> <end>]... [--locked] [--seed] [--note]` hashes the
  export, checks the declared span against the event column and every sealed
  split's rows against the sealed periods, and records the version.

Paper trading (Phase 4 plan T90; ADR 0010 amendment 2026-10-04):

- `tradepartner paper lots-reconcile --export <file> --tax-year <y>` parses the
  broker's realised-gains or 1099-B export (`execution.lots_reconcile_export`,
  a stub that refuses every file until the first real export) and compares it,
  through `execution.lots_reconcile.compare`, with the latest journaled
  lot-ledger set's disposals of that tax year, read through
  `store.db.open_read_only`. It writes nothing and has no fix, adjust or write
  flag. It exits 0 only when at least one row matched and nothing differs, 3
  on any difference (an unmatched row on either side is one), and
  `LOTS_RECONCILE_REFUSAL_EXIT`'s code per refusal: `unknown_export_format`,
  `no_disposals` (none in the tax year), `locked` (store busy), `no_window`
  (no journal or no window) and `no_store`. The ledger holds one window's
  rebuild (spec req 13), so the report names that window and every other
  window that reaches the tax year, whose disposals are not compared.

There is no `--synthetic` flag, no store-path option, and no edit, delete,
unseal, reopen or import command: every run the CLI opens is a non-synthetic
run on the store it is configured for.

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
client, the price source, the dashboard launcher, the broker export parser);
`main` is the console script
over the real ones.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import duckdb
import httpx
import polars as pl
import typer

from tradepartner.adapters import alpaca_raw
from tradepartner.adapters.alpaca_prices import AlpacaPriceSource
from tradepartner.adapters.edgar_source import EdgarFilingSource
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.backfill import BenchmarkSeed, HoleFill, backfill, backfill_benchmark, fill_holes
from tradepartner.backtest.holdout import GAP_THRESHOLD_KEY, Flags, Reasons
from tradepartner.backtest.hypothesis import HypothesisFileError, register
from tradepartner.backtest.metrics import METRIC_KEYS
from tradepartner.backtest.run import RunOutcome, run_hypothesis
from tradepartner.cli_record import _configured_secrets, scrub_text
from tradepartner.config import Settings, get_settings
from tradepartner.execution import lots_reconcile
from tradepartner.execution.lots_reconcile import BrokerLotRow
from tradepartner.execution.lots_reconcile_export import UnknownExportFormat
from tradepartner.execution.lots_reconcile_export import parse_export as parse_broker_export
from tradepartner.health import HealthReport, health_report
from tradepartner.ingest import SOURCES, IngestResult, _read, ingest_session
from tradepartner.repair import RepairRefused, repair_resolution, store_resolver
from tradepartner.research import EVERY_ROW_SPLITS
from tradepartner.research.experiment import (
    SPLITS,
    ExperimentFileError,
    _read_tabular,
    check_declared_event_span,
    check_sealed_split_has_period,
    effective_sealed_splits,
    event_span,
    hash_export,
    hash_file,
    load_split_assignment,
    parse_experiment_file,
    read_event_column,
    split_event_spans,
)
from tradepartner.research.gates import Flags as ResearchFlags
from tradepartner.research.gates import Reasons as ResearchReasons
from tradepartner.retract import RetractRefused, master_retract
from tradepartner.store import journal, registry, research, schema
from tradepartner.store.db import StoreLockedError, open_for_write, open_read_only, utc_now
from tradepartner.timeutil import ensure_tz_aware_utc

USAGE_ERROR = 2
DASHBOARD_APP = Path(__file__).resolve().parent / "dashboard" / "app.py"
#: Relative path-shaped settings resolve against the project root (config.py).
PROJECT_ROOT = Path(__file__).resolve().parents[2]

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


def _print_holes(result: HoleFill) -> None:
    """Any run rows (a real run's months, or a locked store), then a dry
    run's holes: the summary and one line per security; or a real run's
    holes not fetched per reason and its named securities with no hole
    fetched."""
    for run in result.runs:
        typer.echo(
            f"{run.source}: {run.status}, {run.rows_added} rows, "
            f"cursor {run.chunk_cursor}: {run.message}"
        )
    if result.dry_run and result.exit_code == 0:
        typer.echo(f"alpaca: holes as of now: {result.summary()}")
        for line in result.lines():
            typer.echo(line)
        return
    if not result.runs:
        typer.echo("alpaca: no holes to fill")
    if result.dropped:
        typer.echo(f"alpaca: {result.dropped_note().removeprefix('; ')}")
    for named in result.named:
        typer.echo(f"  {named.security_id}: not fetched, {named.reason}")


class _NoPrices(PriceSource):
    """The price source of a hole-fill dry run, which fetches nothing."""

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        raise RuntimeError("a hole-fill dry run fetches no bars")

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        raise RuntimeError("a hole-fill dry run fetches no actions")


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
        why = (
            "not a share count"
            if row["ratio"] is None
            else f"x{row['ratio']:.4g} over {row['baseline_value']:.0f} "
            f"as of {row['baseline_as_of']}"
        )
        echo(f"  {row['security_id']}@{row['as_of_date']} {row['value']:.0f} ({why})")
    pairs = report.accepted_same_day_pairs.frame
    echo(f"accepted same-day pairs: {pairs.height}")
    for row in pairs.iter_rows(named=True):
        echo(f"  {row['security_id']}@{row['valid_from']} {row['ticker']}/{row['next_ticker']}")
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


# --- Research registry: experiment, experiments, dataset (plan T83) ----------------

#: Export formats `dataset register` counts rows of (spec req 11: "where tabular").
_TABULAR_SUFFIXES = frozenset({".csv", ".tsv", ".parquet"})
#: Errors a research command reports as a refusal (exit 2): nothing was written.
_RESEARCH_REFUSALS = (ExperimentFileError, registry.RegistryError, ValueError)
#: `dataset register` also refuses an export or split file it cannot read or parse.
_DATASET_REFUSALS = (*_RESEARCH_REFUSALS, OSError, pl.exceptions.PolarsError)


def _experiments_dir(settings: Settings) -> Path:
    """`research.experiments_dir`, relative to the project root unless absolute."""
    configured = Path(settings.research.experiments_dir)
    return configured if configured.is_absolute() else PROJECT_ROOT / configured


def _refusal(exc: BaseException, settings: Settings) -> typer.Exit:
    return _fail(_scrubbed(f"refused: {exc}", settings), USAGE_ERROR)


def _read_config(path: Path, settings: Settings) -> dict[str, Any]:
    """`--config`'s JSON object, refused (exit 2) before anything is written."""
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        message = f"--config {path}: not a readable JSON file ({exc})"
        raise _fail(_scrubbed(message, settings), USAGE_ERROR) from None
    if not isinstance(config, dict):
        raise _fail(f"--config {path}: must be a JSON object", USAGE_ERROR)
    return config


def _sealed_periods(extra: Sequence[str]) -> list[tuple[date, date]]:
    """The `--sealed-period <start> <end>` pairs from the command's extra
    arguments (Typer cannot declare a repeated two-value option). Any other
    extra argument, such as an unknown flag, is a usage error."""
    periods: list[tuple[date, date]] = []
    rest = list(extra)
    while rest:
        flag = rest.pop(0)
        if flag != "--sealed-period":
            raise _fail(f"no such option or argument: {flag}", USAGE_ERROR)
        if len(rest) < 2 or any(v.startswith("--") for v in rest[:2]):
            raise _fail("--sealed-period takes two dates, <start> <end>", USAGE_ERROR)
        start = _parse_day("--sealed-period", rest.pop(0))
        end = _parse_day("--sealed-period", rest.pop(0))
        assert start is not None and end is not None
        if end < start:
            raise _fail(f"--sealed-period end {end} is before its start {start}", USAGE_ERROR)
        periods.append((start, end))
    return periods


def _flag_names(run: research.RunSummary) -> str:
    flags = [
        name
        for name, on in (
            ("synthetic", run.synthetic),
            ("spent", run.holdout_spent),
            ("repeat", run.holdout_repeat),
        )
        if on
    ]
    return ",".join(flags) or "-"


# --- Phase 4: paper lots-reconcile (plan T90) -------------------------------------

#: `paper lots-reconcile` exits this on any difference (an unmatched row is one).
LOTS_RECONCILE_DIFFERENCE_EXIT = 3
#: `paper lots-reconcile`'s exit code per refusal, each distinct (plan T90).
LOTS_RECONCILE_REFUSAL_EXIT: Mapping[str, int] = MappingProxyType(
    {
        "unknown_export_format": 4,
        "no_disposals": 5,
        "locked": 6,
        "no_window": 7,
        "no_store": 8,
    }
)
_NEW_YORK = ZoneInfo("America/New_York")
#: Every table of one lot-ledger set shares the set's stamp, the latest `lots.known_at`.
_LATEST_SET = "known_at = (SELECT max(known_at) FROM lots)"


class _LotsRefused(Exception):
    """A `paper lots-reconcile` refusal: its `LOTS_RECONCILE_REFUSAL_EXIT` key and message."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _LedgerSet:
    """The latest journaled lot-ledger set, as written, and the windows around it."""

    window_id: int
    window_started: date
    other_windows: tuple[int, ...]  # other windows reaching the tax year
    lots: list[journal.LotRow]
    disposals: list[journal.DisposalRow]
    flags: list[journal.WashSaleFlagRow]


def _set_rows(conn: duckdb.DuckDBPyConnection, row_type: Any, order: str) -> list[Any]:
    """`row_type`'s rows of the latest lot-ledger set, exactly as written."""
    names = [f.name for f in fields(row_type)]
    columns = ", ".join(f'"{name}"' for name in names)
    rows = conn.execute(
        f"SELECT {columns} FROM {row_type.TABLE} WHERE {_LATEST_SET} ORDER BY {order}"
    ).fetchall()
    return [row_type(**dict(zip(names, row, strict=True))) for row in rows]


def _local(at: datetime) -> date:
    return at.astimezone(_NEW_YORK).date()


def _read_ledger_set(conn: duckdb.DuckDBPyConnection, tax_year: int) -> _LedgerSet:
    """The latest lot-ledger set, the window it belongs to (the last window
    started by the set's stamp) and every other window that reaches the tax
    year, or a `no_window` refusal. Read-only."""
    try:
        journal.require_journal(conn)
    except journal.JournalNotInitialised:
        raise _LotsRefused("no_window", "no_window: the store has no paper journal") from None
    windows = [
        (int(w), s)
        for w, s in conn.execute(
            "SELECT window_id, started_at FROM paper_windows ORDER BY window_id"
        ).fetchall()
    ]
    if not windows:
        raise _LotsRefused("no_window", "no_window: no paper window was ever started")
    stamp_row = conn.execute("SELECT max(known_at) FROM lots").fetchone()
    stamp = None if stamp_row is None else stamp_row[0]
    started = [(w, s) for w, s in windows if stamp is None or s <= stamp]
    window_id, window_started = (started or windows)[-1]
    ended = {
        int(w): _local(at)
        for w, at in conn.execute(
            'SELECT window_id, min("at") FROM paper_window_stops WHERE list_contains(?, state) '
            "GROUP BY window_id",
            [list(journal.CLOSING_STOP_STATES)],
        ).fetchall()
    }
    others = tuple(
        w
        for w, s in windows
        if w != window_id
        and _local(s).year <= tax_year
        and (w not in ended or ended[w].year >= tax_year)
    )
    return _LedgerSet(
        window_id=window_id,
        window_started=_local(window_started),
        other_windows=others,
        lots=_set_rows(conn, journal.LotRow, "lot_id"),
        disposals=_set_rows(conn, journal.DisposalRow, "disposal_id"),
        flags=_set_rows(conn, journal.WashSaleFlagRow, "flag_id"),
    )


def _row_text(row: BrokerLotRow) -> str:
    cusip = f" ({row.cusip})" if row.cusip else ""
    return f"{row.symbol}{cusip} {row.trade_date} qty {row.quantity}"


def _lots_report_lines(report: lots_reconcile.Report, ledger: _LedgerSet) -> list[str]:
    """Every difference with both figures, every expected difference, every
    unmatched row on either side, then the result."""
    symbol = {lot.lot_id: lot.symbol for lot in ledger.lots}
    disallowed = lots_reconcile.disallowed_by_disposal(ledger.flags)
    lines = [f"matched: {len(report.matched)}"]
    for m in report.matched:
        lot = f"lot {m.lot_id}, {symbol[m.lot_id]}"
        head = f"disposal {m.disposal_id} ({lot}) <- {_row_text(m.row)}"
        lines.extend(
            f"difference: {head}: {d.figure} broker {d.broker} ledger {d.ledger}"
            for d in m.mismatches
        )
        lines.extend(
            f"expected (carry-forward not modelled): {head}: {d.figure} "
            f"broker {d.broker} ledger {d.ledger}"
            for d in m.expected
        )
    lines.extend(
        f"unmatched export row: {_row_text(row)} proceeds {row.proceeds} "
        f"cost basis {row.cost_basis} box 1g {row.disallowed_loss}"
        for row in report.unmatched_rows
    )
    for d in report.unmatched_disposals:
        figures = lots_reconcile.ledger_figures(d, disallowed)
        lines.append(
            f"unmatched ledger disposal: {d.disposal_id} (lot {d.lot_id}, {symbol[d.lot_id]}) "
            f"{d.trade_date_local} qty {d.quantity} "
            + " ".join(f"{name} {figures[name]}" for name in lots_reconcile.FIGURES)
        )
    lines.append(f"result: {'clean' if report.clean else 'differences'}")
    return lines


def make_app(
    *,
    settings: Callable[[], Settings] = get_settings,
    clock: Clock = utc_now,
    edgar_client: httpx.Client | None = None,
    price_source: Callable[[Settings], PriceSource] | None = None,
    launcher: Launcher = subprocess.call,
    parse_export: Callable[[Path], list[BrokerLotRow]] = parse_broker_export,
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
        fill_holes_: Annotated[
            bool,
            typer.Option(
                "--fill-holes",
                help="with --backfill: refetch committed months' missing bars (#831)",
            ),
        ] = False,
        security: Annotated[
            list[str] | None,
            typer.Option(
                "--security",
                help="with --fill-holes: only this security_id's holes (repeatable, or ID,ID)",
            ),
        ] = None,
        bulk_from_cache: Annotated[
            bool,
            typer.Option(
                "--bulk-from-cache",
                help="company facts from the cached companyfacts.zip, no request for it (#660)",
            ),
        ] = False,
        rebuild_statement_facts: Annotated[
            bool,
            typer.Option(
                "--rebuild-statement-facts",
                help="delete and re-ingest statement_facts in the EDGAR chunk (#660)",
            ),
        ] = False,
    ) -> None:
        """Bring the store up to the expected session, or backfill it."""
        if source not in ("all", *SOURCES):
            raise _fail(
                f"--source must be all, {' or '.join(SOURCES)}; got {source!r}", USAGE_ERROR
            )
        if backfill_ != (since is not None):
            raise _fail("--backfill and --since go together", USAGE_ERROR)
        if fill_holes_ and not backfill_:
            raise _fail("--fill-holes needs --backfill and --since", USAGE_ERROR)
        if fill_holes_ and source != "alpaca":
            raise _fail("--fill-holes refetches prices only: pass --source alpaca", USAGE_ERROR)
        if backfill_ and dry_run and not fill_holes_:
            raise _fail("--dry-run is not available with --backfill", USAGE_ERROR)
        for flag, given in (
            ("--bulk-from-cache", bulk_from_cache),
            ("--rebuild-statement-facts", rebuild_statement_facts),
        ):
            if given and (backfill_ or source == "alpaca"):
                raise _fail(f"{flag} is for a session ingest with the edgar source", USAGE_ERROR)
        named: list[str] | None = None
        if security is not None:
            if not fill_holes_:
                raise _fail("--security needs --fill-holes", USAGE_ERROR)
            named = [part.strip() for value in security for part in value.split(",")]
            if not all(named):
                raise _fail(f"--security takes non-blank ids, got {security!r}", USAGE_ERROR)
        start: date | None = None
        if since is not None:
            try:
                start = date.fromisoformat(since)
            except ValueError:
                raise _fail(f"--since must be YYYY-MM-DD, got {since!r}", USAGE_ERROR) from None
        s = settings()
        if rebuild_statement_facts and not s.edgar.statement_facts_enabled:
            raise _fail(
                "--rebuild-statement-facts needs edgar.statement_facts_enabled", USAGE_ERROR
            )
        if fill_holes_ and start is not None:
            if (absent := _store_missing(s)) is not None:
                raise absent
            if dry_run:  # fetches nothing: no secret needed
                listed = fill_holes(
                    s,
                    prices=_NoPrices(),
                    since=start,
                    clock=clock,
                    dry_run=True,
                    securities=named,
                )
                _print_holes(listed)
                raise typer.Exit(listed.exit_code)
        missing = _missing_secrets(s, source)
        if missing:
            raise _fail(
                f"missing required secret(s): {', '.join(missing)}. "
                "Set them in .env (see .env.example).",
                USAGE_ERROR,
            )
        filings = EdgarFilingSource(
            s, client=edgar_client, clock=clock, reuse_cached=bulk_from_cache
        )
        prices = price_source(s) if price_source else StorePriceSource(s, clock=clock)
        if fill_holes_ and start is not None:
            filled = fill_holes(s, prices=prices, since=start, clock=clock, securities=named)
            _print_holes(filled)
            raise typer.Exit(filled.exit_code)
        if start is not None:
            result = backfill(
                s, prices=prices, filings=filings, since=start, source=source, clock=clock
            )
        else:
            result = ingest_session(
                s,
                prices=prices,
                filings=filings,
                source=source,
                clock=clock,
                dry_run=dry_run,
                rebuild_statement_facts=rebuild_statement_facts,
            )
        _print_result(result)
        raise typer.Exit(result.exit_code)

    @app.command("backfill-benchmark")
    def backfill_benchmark_(
        symbol: Annotated[str, typer.Argument(help="a configured benchmark, e.g. MTUM")],
        since: Annotated[str, typer.Option(help="first day to fetch, YYYY-MM-DD")],
        cik: Annotated[
            str | None, typer.Option(help="10-digit CIK, to seed a benchmark the store lacks")
        ] = None,
        name: Annotated[str | None, typer.Option(help="security name, to seed it")] = None,
        exchange: Annotated[str | None, typer.Option(help="listing exchange, to seed it")] = None,
    ) -> None:
        """Seed (if missing) and backfill one configured benchmark's bars (#840)."""
        start = _parse_day("--since", since) or date.min
        given = [cik, name, exchange]
        if any(v is not None for v in given) and not all(v is not None for v in given):
            raise _fail("--cik, --name and --exchange go together", USAGE_ERROR)
        seed: BenchmarkSeed | None = None
        if cik is not None and name is not None and exchange is not None:
            try:
                seed = BenchmarkSeed(cik=cik, name=name, exchange=exchange)
            except ValueError as exc:
                raise _fail(str(exc), USAGE_ERROR) from None
        s = settings()
        if (missing_store := _store_missing(s)) is not None:
            raise missing_store
        missing = _missing_secrets(s, "alpaca")
        if missing:
            raise _fail(
                f"missing required secret(s): {', '.join(missing)}. "
                "Set them in .env (see .env.example).",
                USAGE_ERROR,
            )
        prices = price_source(s) if price_source else StorePriceSource(s, clock=clock)
        try:
            result = backfill_benchmark(
                s, prices=prices, symbol=symbol, since=start, seed=seed, clock=clock
            )
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except ValueError as exc:
            raise _fail(f"refused: {exc}", USAGE_ERROR) from None
        state = "seeded" if result.seeded else "already in the store"
        if not result.seeded and seed is not None:
            state += "; --cik, --name and --exchange not used"
        typer.echo(f"{result.symbol}: {result.security_id} {state}")
        _print_result(IngestResult(result.runs))
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

    @app.command("master-retract")
    def master_retract_(
        apply: Annotated[
            bool, typer.Option("--apply", help="retract the rows (default: dry run)")
        ] = False,
        expect_rows: Annotated[
            int | None, typer.Option(help="the dry run's row count (required with --apply)")
        ] = None,
        expect_digest: Annotated[
            str | None, typer.Option(help="the dry run's digest (required with --apply)")
        ] = None,
        allow_traded: Annotated[
            bool,
            typer.Option("--allow-traded", help="with --apply: retract rows of traded names"),
        ] = False,
    ) -> None:
        """Retract stored master rows the current rules no longer derive (#859)."""
        expect: tuple[int, str] | None = None
        if apply:
            if expect_rows is None or expect_digest is None:
                raise _fail(
                    "run without --apply first, then pass its count and digest as "
                    "--expect-rows and --expect-digest",
                    USAGE_ERROR,
                )
            expect = (expect_rows, expect_digest)
        elif expect_rows is not None or expect_digest is not None or allow_traded:
            raise _fail(
                "--expect-rows, --expect-digest and --allow-traded go with --apply", USAGE_ERROR
            )
        s = settings()
        if (missing_store := _store_missing(s)) is not None:
            raise missing_store
        missing = _missing_secrets(s, "edgar")
        if missing:
            raise _fail(
                f"missing required secret(s): {', '.join(missing)}. "
                "Set them in .env (see .env.example).",
                USAGE_ERROR,
            )
        filings = EdgarFilingSource(s, client=edgar_client, clock=clock)
        try:
            result = master_retract(
                s,
                filings=filings,
                clock=clock,
                dry_run=not apply,
                expect=expect,
                allow_traded=allow_traded,
            )
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except RetractRefused as exc:
            raise _fail(f"retract refused: {_scrubbed(str(exc), s)}", 1) from None
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

    experiment_app = typer.Typer(no_args_is_help=True, help="Pre-register and run experiments.")
    app.add_typer(experiment_app, name="experiment")

    @experiment_app.command("register")
    def experiment_register(
        file: Annotated[Path, typer.Argument(help="the experiment file (docs/experiments/*.md)")],
    ) -> None:
        """Register an experiment file and print its registration and hashes."""
        s = settings()
        if not file.is_file():
            raise _fail(f"no experiment file at {file}", USAGE_ERROR)
        try:
            parsed = parse_experiment_file(file, _experiments_dir(s), settings=s)
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                record = research.register_experiment(conn, parsed, _REGISTERED_BY)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except _RESEARCH_REFUSALS as exc:
            raise _refusal(exc, s) from None
        basis = "confirmatory" if record.confirmatory else "exploratory"
        typer.echo(
            f"registration {record.registration_id}: {record.slug} ({record.kind}, stage "
            f"{record.stage}, {basis}, family {_fmt(record.family)})"
        )
        if record.amends_registration_id is not None:
            typer.echo(f"  amends registration {record.amends_registration_id}")
        typer.echo(
            f"  dataset {record.dataset_name}, window {record.window_start} to "
            f"{record.window_end}, splits {','.join(record.splits)}"
        )
        typer.echo(
            f"  budget {record.budget_runs} runs, {record.budget_configurations} configurations"
        )
        typer.echo(f"  params sha256 {record.params_sha256}")
        typer.echo(f"  doc sha256 {record.doc_sha256}")

    @experiment_app.command("open")
    def experiment_open(
        slug: Annotated[str, typer.Argument(help="the registered experiment slug")],
        dataset: Annotated[int, typer.Option(help="the dataset version id to bind")],
        split: Annotated[str, typer.Option(help="the split to bind")],
        config: Annotated[Path, typer.Option(help="the run's config, a JSON object file")],
        configurations: Annotated[
            int, typer.Option(help="configurations this run will evaluate")
        ] = 1,
        spend_holdout: Annotated[
            bool, typer.Option(help="open a run touching a protected window (needs a reason)")
        ] = False,
        holdout_reason: Annotated[str | None, typer.Option(help="why the window is spent")] = None,
        holdout_repeat: Annotated[
            bool, typer.Option(help="spend an already-spent protected window again")
        ] = False,
        note: Annotated[str | None, typer.Option(help="a note stored with the run")] = None,
    ) -> None:
        """Open a run of a registered experiment and print its id."""
        if spend_holdout and _blank_text(holdout_reason):
            raise _fail("--spend-holdout needs a non-blank --holdout-reason", USAGE_ERROR)
        if holdout_reason is not None and not spend_holdout:
            raise _fail("--holdout-reason goes with --spend-holdout", USAGE_ERROR)
        if holdout_repeat and not spend_holdout:
            raise _fail("--holdout-repeat goes with --spend-holdout", USAGE_ERROR)
        if configurations < 1:
            raise _fail("--configurations must be at least 1", USAGE_ERROR)
        s = settings()
        run_config = _read_config(config, s)
        try:
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                handle = research.open_run(
                    conn,
                    slug,
                    dataset,
                    split,
                    run_config,
                    _REGISTERED_BY,
                    note,
                    flags=ResearchFlags(spend_holdout=spend_holdout, holdout_repeat=holdout_repeat),
                    reasons=ResearchReasons(holdout_reason=holdout_reason),
                    configurations=configurations,
                    settings=s,
                )
                listed = research.list_runs(conn, registration=handle.slug)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except _RESEARCH_REFUSALS as exc:
            raise _refusal(exc, s) from None
        if handle.refusal is not None:
            typer.echo(f"run {handle.run_id}: {handle.refusal}")
            if handle.message:
                typer.echo(f"  {_scrubbed(handle.message, s)}")
            raise typer.Exit(USAGE_ERROR)
        run = next(r for r in listed if r.run_id == handle.run_id)
        typer.echo(
            f"run {handle.run_id}: open ({handle.slug}, dataset {handle.dataset.dataset_id}, "
            f"split {handle.split}, {handle.n_configurations_declared} configurations, "
            f"confirmatory basis {run.confirmatory_basis})"
        )
        if run.holdout_spent:
            typer.echo(f"  holdout spent{' (repeat)' if run.holdout_repeat else ''}")

    @experiment_app.command("abandon")
    def experiment_abandon(
        run: Annotated[int, typer.Option(help="the unfinished run to abandon")],
        reason: Annotated[str, typer.Option(help="why the run is abandoned")],
    ) -> None:
        """Close an unfinished run as `abandoned`."""
        if not reason.strip():
            raise _fail("--reason must not be blank", USAGE_ERROR)
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                handle = research.attach_run(conn, run, settings=s)
                research.close_run(conn, handle, "abandoned", reason)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except _RESEARCH_REFUSALS as exc:
            raise _refusal(exc, s) from None
        typer.echo(f"run {run}: abandoned ({handle.slug})")

    @app.command()
    def experiments(
        registration: Annotated[str | None, typer.Option(help="only this slug")] = None,
        family: Annotated[str | None, typer.Option(help="only this family")] = None,
        kind: Annotated[str | None, typer.Option(help="only this kind")] = None,
        include_synthetic: Annotated[bool, typer.Option(help="list synthetic runs too")] = False,
    ) -> None:
        """List research runs newest first, with refused, failed and unfinished ones."""
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_read_only(s) as conn:
                schema.init_schema(conn)  # read-only: checks the version, never migrates
                listed = research.list_runs(conn, registration, family, kind, include_synthetic)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except (schema.ResearchNotInitialised, schema.RegistryNotInitialised):
            raise _fail("research registry not initialised", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        typer.echo("id  experiment  kind  split  outcome  verdict  basis  flags  message")
        for r in listed:
            basis = "confirmatory" if r.confirmatory else "exploratory"
            message = _scrubbed(r.message, s).strip() if r.message else ""
            typer.echo(
                f"{r.run_id}  {r.slug}  {r.kind}  {r.split}  {r.outcome}  {_fmt(r.verdict)}  "
                f"{basis}  {_flag_names(r)}  {message}"
            )

    dataset_app = typer.Typer(no_args_is_help=True, help="Register research datasets.")
    app.add_typer(dataset_app, name="dataset")

    @dataset_app.command(
        "register",
        context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
    )
    def dataset_register(
        ctx: typer.Context,
        name: Annotated[str, typer.Option(help="the dataset name")],
        version: Annotated[str, typer.Option(help="the version label")],
        path: Annotated[Path, typer.Option(help="the export: a tabular file or a directory")],
        event_start: Annotated[str, typer.Option(help="earliest source event, YYYY-MM-DD")],
        event_end: Annotated[str, typer.Option(help="latest source event, YYYY-MM-DD")],
        event_column: Annotated[
            str | None, typer.Option(help="the export's event-date column")
        ] = None,
        split_json: Annotated[
            Path | None, typer.Option(help='the split file, {"splits": [...]} per row')
        ] = None,
        sealed: Annotated[
            list[str] | None, typer.Option(help="a sealed split name (repeatable)")
        ] = None,
        locked: Annotated[bool, typer.Option(help="the export is a locked label set")] = False,
        seed: Annotated[
            int | None, typer.Option(help="the seed the splits were drawn with")
        ] = None,
        note: Annotated[str | None, typer.Option(help="a note stored with the version")] = None,
    ) -> None:
        """Register a dataset version. `--sealed-period <start> <end>` (repeatable)
        seals a period of source events."""
        periods = _sealed_periods(ctx.args)
        start = _parse_day("--event-start", event_start)
        end = _parse_day("--event-end", event_end)
        assert start is not None and end is not None
        s = settings()
        explicit_sealed = tuple(sealed or ())
        try:
            if not path.exists():
                raise ExperimentFileError(f"no export at {path}")
            if split_json is not None and event_column is None:
                raise ExperimentFileError(
                    "split without event column: --split-json needs --event-column"
                )
            unknown = sorted(set(explicit_sealed) - set(SPLITS))
            if unknown:
                raise ExperimentFileError(f"--sealed {unknown}: not among the splits {SPLITS}")
            if path.is_dir() and explicit_sealed:
                raise ExperimentFileError(
                    "sealed split without period: a directory export has no event column, "
                    "so it cannot seal a split"
                )
            if path.is_dir() and event_column is not None:
                raise ExperimentFileError(
                    "--event-column needs a tabular export; a directory export has no rows"
                )
            sha = hash_export(path)
            values = read_event_column(path, event_column) if event_column is not None else None
            n_rows: int | None = None
            if values is not None:
                n_rows = len(values)
                check_declared_event_span(start, end, *event_span(values), path=path)
            elif path.is_file() and path.suffix in _TABULAR_SUFFIXES:
                n_rows = _read_tabular(path).height
            assignment = load_split_assignment(split_json) if split_json is not None else None
            spans = (
                split_event_spans(values, assignment)
                if values is not None and assignment is not None
                else None
            )
            all_sealed = effective_sealed_splits(explicit_sealed, assignment or ())
            for split in sorted(all_sealed):
                if values is None:
                    raise ExperimentFileError(
                        f"sealed split without period: {split!r} is sealed, which needs "
                        "--event-column and a sealed period holding its rows"
                    )
                # `full` and `none` bind every row (req 3), so sealing one
                # seals every row, whatever the split file labels them.
                rows = (
                    list(values)
                    if split in EVERY_ROW_SPLITS or assignment is None
                    else [v for v, a in zip(values, assignment, strict=True) if a == split]
                )
                check_sealed_split_has_period(split, rows, periods)
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                record = research.register_dataset(
                    conn,
                    name=name,
                    version=version,
                    path=str(path.resolve()),
                    sha256=sha,
                    event_start=start,
                    event_end=end,
                    n_rows=n_rows,
                    event_column=event_column,
                    split_path=str(split_json.resolve()) if split_json is not None else None,
                    split_sha256=hash_file(split_json) if split_json is not None else None,
                    split_spans=spans,
                    sealed_splits=sorted(all_sealed),
                    sealed_periods=periods,
                    locked=locked,
                    seed=seed,
                    note=note,
                )
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except _DATASET_REFUSALS as exc:
            raise _refusal(exc, s) from None
        typer.echo(
            f"dataset {record.dataset_id}: {record.name} {record.version}, "
            f"{_fmt(record.n_rows)} rows, events {record.event_start} to {record.event_end}"
        )
        typer.echo(f"  sha256 {record.sha256}")
        if record.split_sha256 is not None:
            typer.echo(f"  split file sha256 {record.split_sha256}")
        for split_name, (first, last) in sorted(record.split_spans.items()):
            typer.echo(f"  split {split_name}: {first} to {last}")
        typer.echo(
            f"  sealed splits {','.join(record.sealed_splits) or '-'}, sealed periods "
            + (", ".join(f"{a} to {b}" for a, b in record.sealed_periods) or "-")
        )
        typer.echo(f"  locked {_fmt(record.locked)}, seed {_fmt(record.seed)}")

    paper_app = typer.Typer(no_args_is_help=True, help="Paper trading (Phase 4).")
    app.add_typer(paper_app, name="paper")

    @paper_app.command("lots-reconcile")
    def lots_reconcile_(
        export: Annotated[
            Path,
            typer.Option(
                help="the broker's realised-gains or 1099-B export",
                exists=True,
                dir_okay=False,
                readable=True,
            ),
        ],
        tax_year: Annotated[int, typer.Option(help="the tax year the export covers")],
    ) -> None:
        """Compare a broker 1099-B export with the lot ledger; writes nothing."""
        s = settings()

        def refuse(code: str, message: str) -> typer.Exit:
            return _fail(_scrubbed(f"refused: {message}", s), LOTS_RECONCILE_REFUSAL_EXIT[code])

        if not Path(s.store.path).exists():
            raise refuse("no_store", f"no store at {s.store.path}")
        try:
            rows = parse_export(export)
        except UnknownExportFormat as exc:
            raise refuse("unknown_export_format", str(exc)) from None
        try:
            with open_read_only(s) as conn:
                ledger = _read_ledger_set(conn, tax_year)
        except StoreLockedError as exc:
            raise refuse("locked", f"locked: store busy: {exc}") from None
        except _LotsRefused as exc:
            raise refuse(exc.code, str(exc)) from None

        def echo(line: str) -> None:
            typer.echo(_scrubbed(line, s))

        echo(
            f"lots-reconcile: tax year {tax_year}, the ledger of window {ledger.window_id} "
            f"(started {ledger.window_started}); the store holds one window's rebuild"
        )
        if ledger.other_windows:
            names = ", ".join(str(w) for w in ledger.other_windows)
            echo(
                f"scope: tax year {tax_year} also reaches window(s) {names}, whose "
                "disposals the store no longer holds and are not compared"
            )
        disposals = [d for d in ledger.disposals if d.tax_year == tax_year]
        if not disposals:
            raise refuse(
                "no_disposals",
                f"no_disposals: the ledger has no disposal in tax year {tax_year}",
            )
        in_year = [r for r in rows if r.trade_date.year == tax_year]
        if skipped := len(rows) - len(in_year):
            echo(f"export rows outside tax year {tax_year}, not compared: {skipped}")
        report = lots_reconcile.compare(in_year, disposals, ledger.lots, ledger.flags)
        for line in _lots_report_lines(report, ledger):
            echo(line)
        if not report.clean:
            raise typer.Exit(LOTS_RECONCILE_DIFFERENCE_EXIT)

    return app


def main() -> None:
    """Console-script entry point (`[project.scripts] tradepartner`)."""
    make_app()()


if __name__ == "__main__":
    main()
