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
  `--apply` also needs `--release <name>`, the open data release.
- `tradepartner repair-resolution [--dry-run | --expect-bar-rows N
  --expect-action-rows M --release <name>]` runs `repair.repair_resolution`
  (#819), and `tradepartner repair-bars --security <id> --from <session> --to
  <session> --release <name> [--dry-run | --expect-bar-rows N
  --expect-action-rows M]` runs `repair.delete_bars` (#1319, data-foundation
  plan T140c): one security's Alpaca bars and actions on those sessions, every
  revision. Both list the row counts on a dry run; a real run deletes only
  those counts, only inside the open data release named by `--release`
  (`docs/runbooks/data-releases.md`), and exits 1 on a refusal, 2 on a usage
  error.
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
- `tradepartner decision data-release open --name --backup --reason`, `close
  --name --from <session> --to <session> [--reason]`, `record --name --backup
  --trial <id> --reason` and `import-file <path>` write the named data releases of
  `docs/runbooks/data-releases.md` (#1319, data-foundation plan T140b) through
  `store.registry`'s release writers: `open` the `before` row (the backup, read
  read-only, must hold the store's latest `ingested_at`), `close` the open
  release's `after` row with the sessions touched, `record` a closed row for the
  backup that holds a trial's state (its vintage at the trial's cutoff, whether
  it equals the trial's, and the repair runs since), and `import-file` the
  hand-written `data/releases.toml` once, every entry checked before anything is
  written, then one transaction per release. Exit 0 when written, 1 on a
  refusal or a busy store, 2 on a usage error.
- `tradepartner decision development-boundary --date <YYYY-MM-DD> --reason` writes
  a new `development_boundary` row (ADR 0016 points 1 and 6; data-foundation plan
  T142b) through `registry.write_development_boundary`, which refuses a date on or
  after the `holdout.start` of any registered non-oracle family (spent, unspent or
  forward) or on or before any such family's `in_sample_start`. The boundary moves
  only by a new row. Exit 0 when written, 1 on a refusal or a busy store, 2 on a
  usage error.
- `tradepartner decision shakedown-span --sessions N --order-sessions M --reason`
  writes a `shakedown_span` row (ADR 0017 part E; paper-trading plan T157): the
  span opens at the session after it, and its two thresholds are the row's,
  never settings; a new row restarts the span. `tradepartner decision
  shakedown-note --alert <id> --reason` writes a `shakedown_note` row naming an
  existing alert, refusing an unknown one. Both write through
  `registry.record_decision`. Exit 0 when written, 1 on a refusal or a busy
  store, 2 on a usage error (a blank reason, `--order-sessions` below 1 or above
  `--sessions`).

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

Research labeling (research-labeling spec C12 and req 18, amendment 2026-10-08
#1330; plan T124). Each command calls its own module, `store.db`'s connections
and `store.registry.code_version`, and prints through the `cli_record` scrub:

- `tradepartner corpus fetch departure-reason [--since] [--until] [--cik]...
  [--limit]` runs `corpus.departure_fetch.fetch_departure_corpus` through the
  paced EDGAR client and prints the corpus path and its count identity.
- `tradepartner research frame build departure-reason --corpus <path> --as-of
  <t> [--register]` runs `research.labeling.frame.build_frame` at `t` (an ISO
  datetime with its UTC offset) and either registers the frame
  (`frame.register`, on an `open_for_write` connection) or prints the `dataset
  register` line that would.
- `tradepartner research gold [--frame <id> --seed <s> [--n 150] --exclude
  <csv>] [--lock]` builds the gold session (`gold.build_gold_session`, the
  frame's export read at its content address in the research store and checked
  against the row's SHA-256) or, with no flags, resumes
  it (`gold.open_gold_session`, which refuses flags that differ from the
  session); `--lock` alone locks an existing complete session and launches
  nothing.
- `tradepartner research review --run <id> [--finish]` builds or resumes the
  review session of an unfinished batch run (`review.build_review_session`, on
  a read-only connection); `--finish` alone finishes a fully decided session
  and launches nothing.
- Both page commands close every connection they opened, then launch `streamlit
  run` on `REVIEW_PAGE` through the same launcher as `dashboard`, with
  `--server.address localhost --browser.gatherUsageStats false` before `--` and
  `--session <path>` after it (`research review` also passes `--code-version
  <commit>[+dirty]`). The page writes no store. Once the launcher returns (the
  page's own stop, Ctrl-C or any exit status) the command reads the session: a
  complete one is locked (`gold.lock_gold`) or finished (`review.finish`) on an
  `open_for_write` connection and its count and hash are printed (exit 0); a
  partial one prints how many cases or items are open and stays unlocked (exit
  0); a busy store prints "store busy" and exits 1 with nothing written to the
  store. Nothing of a case's content (labels, passages, answers) is printed.
- `tradepartner research label <slug> --dataset <id> --split <split> --model
  <jev-X.Y.Z> [--configurations 1] [--accepted-from] [--accepted-to] [--limit]
  [--drift-gold <id> --drift-baseline-run <id>] [--spend-holdout
  --holdout-reason]` runs one batch (`job.run_batch`) on a non-synthetic run;
  a frame split needs the two drift flags (the drift probe of C5). With
  `--dry-run` it prints `job.dry_run`'s estimate, spend sums and headroom on a
  read-only connection, opens no run and calls nothing. Exit 0 for `ok` or
  `unfinished` (a batch awaiting review), 1 for `failed`, 2 for a refusal.

There is no `research probe`, `research spend` or `research record-fixtures`
command and no `--out` flag (C12). The model client is `make_app`'s
`model_client` edge (`job.CLIENT_FACTORY`, which builds the disabled client
unless both spend ceilings and the key are set in the owner's environment).

Paper trading (Phase 4 spec req 16; plans T67 and T90; ADR 0010 amendment
2026-10-04):

- `tradepartner paper start --hypothesis <slug>`, `stop --reason`, `run`,
  `reconcile`, `kill --reason`, `resume --reason [--accept-broker-fills]
  [--accept-rejections]`, `report`, `check`, `status`, `abandon --reason`,
  `override --kind [--session --name] --reason` (T67) and `settle --order
  <client_order_id> --reason` (T84c; `--order` exactly once) each call the one
  `execution` function of that name (`window.start`, `window.stop`,
  `run.tracking_run`, `reconcile_run.reconcile_command`, `window.kill`,
  `resume.resume`, `report.report`, `check.check`, `ops.page_data`,
  `window.abandon`, `window.override`, the page's own writer,
  `window.settle_order`; `settle` refuses each of req 17's gates by its reason
  code, and a `ClockError` or a broker error is a failure). The broker comes
  from `execution.brokers.build_broker` (a test injects one through `make_app`'s
  `broker`), built with the command's one clock object, which the command also
  hands to the function, so the wrapper `run` builds holds the same clock as the
  adapter (ADR 0007 point 5); a broker that cannot be built is a failure (1),
  its message scrubbed like every other line. A blank `--reason` is a usage error (2) before any
  broker is built. `run` reads `TRADEPARTNER_INVOKED_BY` (`run.invoked_by`).
  No flag selects an endpoint, a run session or a store path, or bypasses the
  switch or a limit: `override --session` names the rebalance session the
  override applies to (spec req 9), never the session a command runs on.
  `--accept-rejections` is spec req 5's owner acceptance of rejection-cap
  verdicts (#472), which lifts no other refusal.
  **Exit codes.** `paper run` keeps the runbook's table: `RunOutcome.exit_code`
  (0 for `ok`, `no_session` and `skipped_kill_switch`, else 1), 1 for a halt or
  failure, `wrapper.WRITE_FAILED_EXIT_CODE` (3) when the halt path cannot write
  the switch. Every other command exits 0 on success, 1 on a failure (an
  exception, a reconciliation that is not `ok`, a failing `check`; `report` and
  `check` with no window are such a failure), 2 on a usage
  error, 3 when `kill` cannot write its row, and `PAPER_REFUSAL_EXIT`'s code for
  `refused` (the reason code is printed), `locked` and `no_window`.
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
  (no journal or no window), `schema_version` (a journal predating schema
  version 17; its message names the fix) and `no_store`. The ledger holds one
  window's rebuild (spec req 13), so the report names that window and every
  other window that reaches the tax year, whose disposals are not compared.

Strategy lab (strategy-lab spec req 15; plan T111):

- `tradepartner sweep register <file>` registers a sweep file
  (`backtest.sweep.register`) and prints the registration and its variant
  slugs; an unchanged file returns its registration and writes nothing.
- `tradepartner sweep run <slug> [--time-budget-minutes M] [--rerun]
  [--note TEXT]` runs the sweep's planned variants (`backtest.lab.run_sweep`)
  on `settings.store.path` and prints the run's counts and every trial; it
  exits 0 when every trial it opened is `ok`, 1 when any failed or was refused.
- `tradepartner sweep status [<slug>]` prints every sweep's latest
  registration and its state, or one sweep's state with its stale,
  terminal-failed and unrun variants (`store.lab_queries.sweep_state`).
- `tradepartner sweep report <slug>` prints `backtest.sweep_report`'s report.
- `tradepartner sweep promote <slug> --file <path> --reason TEXT` and
  `tradepartner sweep retire <slug> --reason TEXT` record a promotion (with the
  promoted file's registration) or a retirement (`backtest.promotion`).
- `tradepartner lab status` prints `sweep_report.lab_status`.

A refusal of any of these exits 2 with nothing written; on a store without the
lab tables each exits 2 naming the lab migration, while `backtest` and
`hypothesis register` there keep the Phase 3 rules. `backtest <variant-slug>`
records a `refused_variant` trial and exits 2. No lab command takes a store
path, a window, a holdout, gap or synthetic flag, and all of their output is
passed through the `cli_record` fixture scrub.

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
client, the price source, the dashboard launcher, the broker export parser,
the `paper` broker factory, the research model client); `main` is the console
script over the real ones.
"""

from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields
from datetime import UTC, date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import duckdb
import httpx
import polars as pl
import typer

from tradepartner.adapters import alpaca_raw
from tradepartner.adapters.alpaca_prices import AlpacaPriceSource, FetchBars
from tradepartner.adapters.broker import Broker
from tradepartner.adapters.edgar_source import EdgarFilingSource
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.backfill import BenchmarkSeed, HoleFill, backfill, backfill_benchmark, fill_holes
from tradepartner.backtest import lab, promotion, sweep_report
from tradepartner.backtest import sweep as lab_sweep
from tradepartner.backtest.frozen import frozen_values
from tradepartner.backtest.holdout import GAP_THRESHOLD_KEY, Flags, Reasons
from tradepartner.backtest.hypothesis import HypothesisFileError, register
from tradepartner.backtest.metrics import METRIC_KEYS
from tradepartner.backtest.run import RunOutcome, run_hypothesis
from tradepartner.cli_record import _configured_secrets, scrub_text
from tradepartner.config import MAIN_BOOK_ID, Settings, get_settings
from tradepartner.corpus import departure_fetch
from tradepartner.execution import check as paper_check
from tradepartner.execution import lots_reconcile, ops, reconcile_run, window
from tradepartner.execution import report as paper_report
from tradepartner.execution import resume as paper_resume
from tradepartner.execution import run as paper_run
from tradepartner.execution.brokers import build_broker
from tradepartner.execution.lock import LockHeld
from tradepartner.execution.lots_reconcile import BrokerLotRow
from tradepartner.execution.lots_reconcile_export import UnknownExportFormat
from tradepartner.execution.lots_reconcile_export import parse_export as parse_broker_export
from tradepartner.execution.reconcile import OK as RECONCILE_OK
from tradepartner.execution.wrapper import CRASH_EXIT_CODE, WRITE_FAILED_EXIT_CODE
from tradepartner.health import HealthReport, health_report
from tradepartner.ingest import SOURCES, IngestResult, _read, ingest_session
from tradepartner.repair import RepairRefused, delete_bars, repair_resolution, store_resolver
from tradepartner.research import EVERY_ROW_SPLITS, DatasetChanged, datafiles
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
from tradepartner.research.labeling import frame as labeling_frame
from tradepartner.research.labeling import gold, job, review
from tradepartner.retract import RetractRefused, master_retract
from tradepartner.store import (
    journal,
    lab_queries,
    lab_registry,
    lab_schema,
    registry,
    research,
    schema,
)
from tradepartner.store.db import (
    StoreLockedError,
    open_for_write,
    open_read_only,
    utc_now,
)
from tradepartner.store.lab_schema import LabNotInitialised
from tradepartner.timeutil import ensure_tz_aware_utc

USAGE_ERROR = 2
DASHBOARD_APP = Path(__file__).resolve().parent / "dashboard" / "app.py"
#: The review page (C10) `research gold` and `research review` launch: a path, never
#: an import, since importing a Streamlit script runs it.
REVIEW_PAGE = Path(__file__).resolve().parent / "research" / "labeling" / "review_page.py"
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
        fetch_bars: FetchBars | None = None,
        fetch_actions: Callable[[list[str], date, date], Any] | None = None,
        fill_handovers: bool = False,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._fill_handovers = fill_handovers  # #1314: `--fill-holes` lands rule-8 bars
        self._fetch_bars = fetch_bars or (
            lambda symbols, start, end, *, asof=None: alpaca_raw.daily_bars(
                symbols, start, end, asof=asof, settings=settings, clock=clock
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
                fill_handovers=self._fill_handovers,
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
        f"{len(gap.stale_shares)} stale shares, "
        f"{gap.stale_listings.height} stale listings"
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
    "refused_variant": USAGE_ERROR,
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


def _repair_expect(
    dry_run: bool, bar_rows: int | None, action_rows: int | None, release: str | None
) -> tuple[int, int] | None:
    """A real repair's `expect` from the dry run's counts, or a usage error when a
    count or the release name is missing (#819; data-foundation plan T140c)."""
    if dry_run:
        return None
    if bar_rows is None or action_rows is None:
        raise _fail(
            "run --dry-run first, then pass its counts as --expect-bar-rows and "
            "--expect-action-rows",
            USAGE_ERROR,
        )
    if _blank_text(release):
        raise _fail(
            "a repair writes only inside a data release: pass --release <name>", USAGE_ERROR
        )
    return (bar_rows, action_rows)


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
    base = float(frozen_values(hypothesis)[registry.BASE_COST_KEY])
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


# --- Strategy lab: sweep, lab status (strategy-lab plan T111) -----------------------

#: What every lab command says on a store without the lab tables (plan choice 2).
LAB_MIGRATION_HINT = (
    "lab not initialised: run the lab migration first; until then this store keeps "
    "the Phase 3 rules (backtest, hypothesis register)"
)
#: Errors a lab command reports as a refusal (exit 2): raised before anything is
#: written, or inside a write chunk that rolls back. `LabNotInitialised` is separate.
_LAB_REFUSALS = (registry.RegistryError, ValueError)
#: `run_sweep`'s refusals, each raised before it writes a row (its docstring); the
#: unknown slug and the budget are checked by `sweep run` itself first. Any other
#: error comes after the run opened, so it exits 1, not as a refusal.
_SWEEP_RUN_REFUSALS = (
    lab.RegistryTooLarge,
    lab.NoCodeVintage,
    lab_queries.SweepNotCompleteError,
    registry.UnmarkedStoreRefused,
)


def _describe(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def _echo_scrubbed(text: str, settings: Settings) -> None:
    """Print `text` through the `cli_record` fixture scrub (every lab command's output)."""
    typer.echo(_scrubbed(text, settings))


def _lab_fail(exc: BaseException, settings: Settings) -> typer.Exit:
    """Exit 2: the migration hint for `LabNotInitialised` (or a store with no
    registry at all), else the scrubbed refusal."""
    if isinstance(exc, (LabNotInitialised, schema.RegistryNotInitialised)):
        return _fail(LAB_MIGRATION_HINT, USAGE_ERROR)
    return _fail(_scrubbed(f"refused: {type(exc).__name__}: {exc}", settings), USAGE_ERROR)


def _hypothesis_slug(conn: duckdb.DuckDBPyConnection, hypothesis_id: int) -> str:
    return registry.get_hypothesis_by_id(conn, hypothesis_id).slug


def _sweep_state_lines(
    conn: duckdb.DuckDBPyConnection, sweep: lab_registry.SweepRecord
) -> list[str]:
    """One sweep's state and the slugs of its stale, terminal-failed and unrun variants."""
    state = lab_queries.sweep_state(conn, sweep.sweep_id)
    lines = [
        f"sweep {sweep.slug} (registration {sweep.sweep_id}, {sweep.family}): "
        f"{sweep.n_variants} variants; {state.state}"
    ]
    for label, variants in (
        ("stale", state.stale),
        ("terminal-failed", state.terminal_failed),
        ("unrun", state.unrun),
    ):
        slugs = [_hypothesis_slug(conn, v.hypothesis_id) for v in variants]
        lines.append(f"  {label}: {', '.join(slugs) or 'none'}")
    return lines


def _sweep_status_text(conn: duckdb.DuckDBPyConnection, slug: str | None) -> str:
    """`sweep status [<slug>]`: one sweep, or every slug's latest registration."""
    lab_schema.require_lab(conn)
    if slug is not None:
        record = lab_registry.sweep_by_slug(conn, slug)
        if record is None:
            raise ValueError(f"no sweep is registered as {slug!r}")
        return "\n".join(_sweep_state_lines(conn, record))
    slugs = [
        str(s) for (s,) in conn.execute("SELECT DISTINCT slug FROM sweeps ORDER BY 1").fetchall()
    ]
    lines: list[str] = []
    for each in slugs:
        record = lab_registry.sweep_by_slug(conn, each)
        assert record is not None
        lines.extend(_sweep_state_lines(conn, record))
    return "\n".join(lines) if lines else "no sweeps registered"


def _sweep_run_text(outcome: lab.SweepRunOutcome, conn: duckdb.DuckDBPyConnection) -> str:
    """`sweep run`'s summary: the run's counts, then one line per trial it opened."""
    lines = [
        f"sweep run {outcome.sweep_run_id} (registration {outcome.sweep_id}"
        f"{', rerun' if outcome.rerun else ''}): {outcome.n_declared} declared, "
        f"{outcome.n_planned} planned, {outcome.n_ok} ok, {outcome.n_failed} failed, "
        f"{outcome.n_terminal_failed} terminal-failed; "
        f"{'complete' if outcome.completed else 'incomplete'}"
        f"{'; stopped by the time budget' if outcome.stopped_by_budget else ''}; "
        f"{outcome.seconds:.1f} s"
    ]
    lines += [f"warning: {w}" for w in outcome.warnings]
    for t in outcome.trials:
        lines.append(
            f"  trial {t.trial_id}: {_hypothesis_slug(conn, t.hypothesis_id)} "
            f"(group {t.read_group_index}) {t.status}" + (f": {t.message}" if t.message else "")
        )
    return "\n".join(lines)


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


# --- Research labeling: corpus, frame, gold, label, review (plan T124) -------------

#: The frame's registered dataset name (C7; `research.labeling.frame.register`).
FRAME_DATASET = "departure-reason-frame"
#: The gold sample's size when `--n` is not given (req 10, owner decision 3).
GOLD_N_DEFAULT = 150
#: The exit code of a command the owner stopped with Ctrl-C (128 + SIGINT).
INTERRUPTED_EXIT = 130
#: Errors a labeling command reports as a refusal (exit 2): the research refusals,
#: an export it cannot read, a changed export, a records file the spend sum refuses,
#: and another labeling run holding the research store's run lock.
_LABELING_REFUSALS = (
    *_DATASET_REFUSALS,
    DatasetChanged,
    job.RunInProgress,
    job.UnexpectedInferenceFile,
)


def _export_path(settings: Settings, record: research.DatasetRecord) -> Path:
    """Where the research store keeps a frame or gold export, by its content address
    (spec "Research store layout"): the caller checks the file against the row's
    SHA-256, and a dataset row's own `path` is read only by `research.load_dataset`."""
    if record.name == FRAME_DATASET:
        return datafiles.frame_path(settings, record.sha256)
    if record.name == gold.DATASET_NAME:
        return datafiles.gold_path(settings, record.sha256)
    raise ExperimentFileError(
        f"dataset {record.dataset_id} is {record.name!r}: neither "
        f"{FRAME_DATASET} nor {gold.DATASET_NAME}"
    )


def _latest_run_id(conn: duckdb.DuckDBPyConnection) -> int:
    """The newest research run id (0 when none), to tell a refusal before any run
    opened from a failure after one did."""
    return max((r.run_id for r in research.list_runs(conn, include_synthetic=True)), default=0)


def _close_open_runs(
    conn: duckdb.DuckDBPyConnection, settings: Settings, after: int, why: str
) -> list[int]:
    """The run ids above `after` (the ones this command opened), each still-open one
    closed `failed` with `why`; the caller commits them."""
    runs = [r for r in research.list_runs(conn, include_synthetic=True) if r.run_id > after]
    for run in runs:
        if run.outcome == research.UNFINISHED:
            handle = research.attach_run(conn, run.run_id, settings=settings)
            research.close_run(conn, handle, "failed", _scrubbed(f"interrupted: {why}", settings))
    return sorted(r.run_id for r in runs)


def _page_argv(session: Path, *page_args: str) -> list[str]:
    """`streamlit run` on the review page, bound to localhost with usage stats off
    (ADR 0011 point 3), the page's own arguments after `--` (C10, #1330)."""
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(REVIEW_PAGE),
        "--server.address",
        "localhost",
        "--browser.gatherUsageStats",
        "false",
        "--",
        "--session",
        str(session),
        *page_args,
    ]


def _launch(launcher: Launcher, argv: list[str]) -> int:
    """Run the page and return its exit status; Ctrl-C ends it like any other exit
    (#1330 point 3: the command then checks the session either way)."""
    try:
        return launcher(argv)
    except KeyboardInterrupt:
        return INTERRUPTED_EXIT


def _code_version_text() -> str:
    """`<commit>` or `<commit>+dirty` from `store.registry.code_version` (#1330 point 4)."""
    commit, dirty = registry.code_version()
    return f"{commit}+dirty" if dirty else commit


def _parse_as_of(value: str) -> datetime:
    """`--as-of`: an ISO datetime carrying its UTC offset, returned in UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise _fail(f"--as-of {value!r} is not an ISO datetime", USAGE_ERROR) from None
    if parsed.tzinfo is None:
        raise _fail(
            f"--as-of {value!r} has no UTC offset; give one, e.g. 2026-10-09T00:00:00+00:00",
            USAGE_ERROR,
        )
    return parsed.astimezone(UTC)


def _gold_open_count(session: gold.GoldSession) -> int:
    """Cases with no final answer (open or skipped): a partial session is never locked."""
    status, _, _ = gold._states(session)
    return sum(1 for s in status.values() if s in ("open", "skipped"))


def _settle_gold(session: gold.GoldSession, settings: Settings, *, recovery: bool) -> None:
    """After the page (or for `--lock`): lock a complete session on an
    `open_for_write` connection and print the scorable `pilot` count and the export's
    hash; a partial one prints its open count and stays unlocked (#1330 point 3)."""
    open_count = _gold_open_count(session)
    if open_count:
        message = (
            f"gold session: {open_count} of {len(session.cases)} cases open; "
            "left unlocked (run `tradepartner research gold` to resume)"
        )
        if recovery:
            raise _fail(f"refused: {message}", USAGE_ERROR)
        typer.echo(message)
        return
    result = gold.lock_gold(session, lambda: open_for_write(settings))
    if result.state == "busy":
        raise _fail(_scrubbed(result.message or "store busy", settings), 1)
    typer.echo(
        f"gold session locked: dataset {result.dataset_id}, scorable pilot count "
        f"{result.scorable_pilot}, sha256 {result.sha256}"
    )


def _review_open_count(session: review.ReviewSession) -> int:
    """Items with no final decision: a partial session is never finished."""
    return sum(1 for line in review.final_lines(session).values() if line is None)


def _settle_review(session: review.ReviewSession, settings: Settings, *, recovery: bool) -> None:
    """After the page (or for `--finish`): finish a fully decided session on an
    `open_for_write` connection and print `n_reviewed` and the review file's hash; a
    partial one prints its open count and stays unfinished (#1330 point 3)."""
    open_count = _review_open_count(session)
    if open_count:
        message = (
            f"review session: {open_count} of {len(session.items)} items open; "
            f"left unfinished (run `tradepartner research review --run {session.run_id}` "
            "to resume)"
        )
        if recovery:
            raise _fail(f"refused: {message}", USAGE_ERROR)
        typer.echo(message)
        return
    writable = dataclasses.replace(session, connect=lambda: open_for_write(settings))
    try:
        result = review.finish(writable)
    except StoreLockedError as exc:
        raise _fail(_scrubbed(f"store busy: {exc}", settings), 1) from None
    typer.echo(
        f"review finished: run {session.run_id} {result.outcome}, n_reviewed "
        f"{result.metrics['n_reviewed']}, dataset {result.dataset_id}, sha256 {result.sha256}"
    )


# --- Phase 4: the paper commands (plan T67) ---------------------------------------

BrokerFactory = Callable[[Settings, Clock], Broker]


def _main_book_broker(settings: Settings, clock: Clock) -> Broker:
    """`build_broker` for book `main`, H1's (`main`'s pair exactly as before T153).
    Not the live `paper.book_id`: until `--book` (T155b) picks the book from the
    command and its window, a config edit must never point `main`'s window at
    another book's account."""
    return build_broker(settings, clock, MAIN_BOOK_ID)


#: The `paper` commands' refusal exit codes besides `paper run`'s (module docstring).
PAPER_REFUSAL_EXIT: Mapping[str, int] = MappingProxyType(
    {"refused": 4, "locked": 5, "no_window": 6}
)


#: `--reason`, required by `stop`, `kill`, `resume`, `abandon`, `override` and `settle`.
_REASON_OPTION = typer.Option("--reason", help="why, in words (journaled)")


def _paper_call[T](settings: Settings, call: Callable[[], T]) -> T:
    """`call()`, every refusal and failure turned into its scrubbed message and exit."""
    try:
        return call()
    except (LockHeld, StoreLockedError) as exc:
        message, code = f"refused: locked: {exc}", PAPER_REFUSAL_EXIT["locked"]
    except reconcile_run.NoWindowError as exc:  # its message starts `no_window:`
        message, code = f"refused: {exc}", PAPER_REFUSAL_EXIT["no_window"]
    except window.WindowCommandRefused as exc:
        key = "no_window" if exc.reason == window.NO_WINDOW else "refused"
        message, code = f"refused: {exc.reason}: {exc}", PAPER_REFUSAL_EXIT[key]
    except window.StartRefusedError as exc:
        message, code = f"refused: {exc.reason}: {exc}", PAPER_REFUSAL_EXIT["refused"]
    except registry.UnknownHypothesis as exc:
        message, code = f"refused: unknown_hypothesis: {exc}", PAPER_REFUSAL_EXIT["refused"]
    except window.KillWriteFailed as exc:
        message, code = f"failed: {exc}", WRITE_FAILED_EXIT_CODE
    except Exception as exc:
        message, code = f"failed: {_describe(exc)}", CRASH_EXIT_CODE
    raise _fail(_scrubbed(message, settings), code)


def _status_lines(data: ops.OpsData) -> list[str]:
    """`paper status`: the operations page's numbers (spec req 12), one per line."""
    if data.journal_outdated is not None:
        return [f"paper status: {data.journal_outdated}"]
    if data.window is None:
        return ["paper status: no paper window yet"]
    state = data.switch_state
    switch_text = "n/a" if state is None else ("engaged" if state.engaged else "released")
    causes = "; ".join(state.causes) if state is not None and state.causes else "-"
    recon = data.reconciliation
    lines = [
        f"window {data.window.window_id} (first rebalance {data.window.first_rebalance_session})",
        f"as of {_fmt(data.as_of)}; last updated {_fmt(data.last_updated)}"
        + ("; STALE: no run for S-1" if data.stale else ""),
        f"positions {data.positions_count} (value {data.positions_value:.2f}); open orders "
        f"{data.open_orders_count}; targets {data.targets_count}",
        f"kill switch {switch_text} (causes: {causes})",
        "reconciliation "
        + ("n/a" if recon is None else f"{recon.reconciliation_id} {recon.status} at {recon.at}"),
        f"alerts ({len(data.alerts)}{', capped' if data.alerts_capped else ''}):",
    ]
    lines += [f"  {a.at} {a.kind} {a.session}: {a.message}" for a in data.alerts]
    return lines


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
        "schema_version": 9,
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
    except schema.SchemaVersionError as exc:
        raise _LotsRefused("schema_version", f"schema_version: {exc}") from None
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
    sweep_clock: lab.Clock | None = None,
    broker: BrokerFactory = _main_book_broker,
    model_client: job.ClientFactory = job.CLIENT_FACTORY,
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
        prices = (
            price_source(s)
            if price_source
            else StorePriceSource(s, clock=clock, fill_handovers=fill_holes_)
        )
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
        release: Annotated[
            str | None, typer.Option(help="the open data release (required to delete)")
        ] = None,
    ) -> None:
        """Delete Alpaca bars and actions the resolver no longer assigns to their security."""
        expect = _repair_expect(dry_run, expect_bar_rows, expect_action_rows, release)
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            result = repair_resolution(
                s, clock=clock, dry_run=dry_run, expect=expect, release=release
            )
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except RepairRefused as exc:
            raise _fail(f"repair refused: {exc}", 1) from None
        typer.echo(result.summary())
        for line in result.lines():
            typer.echo(line)

    @app.command("repair-bars")
    def repair_bars(
        security: Annotated[str, typer.Option(help="the security id whose bars to delete")],
        from_: Annotated[str, typer.Option("--from", help="the first session to delete")],
        to: Annotated[str, typer.Option(help="the last session to delete")],
        release: Annotated[str, typer.Option(help="the open data release (checked on a real run)")],
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
        """Delete one security's Alpaca bars and actions, every revision, on a session range."""
        first = _parse_day("--from", from_)
        last = _parse_day("--to", to)
        assert first is not None and last is not None
        expect = _repair_expect(dry_run, expect_bar_rows, expect_action_rows, release)
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            result = delete_bars(
                s,
                security_id=security,
                sessions_from=first,
                sessions_to=last,
                clock=clock,
                dry_run=dry_run,
                expect=expect,
                release=release,
            )
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except RepairRefused as exc:
            raise _fail(f"repair refused: {exc}", 1) from None
        except ValueError as exc:
            raise _fail(f"refused: {exc}", USAGE_ERROR) from None
        typer.echo(result.summary())

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
        release: Annotated[
            str | None, typer.Option(help="the open data release (required with --apply)")
        ] = None,
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
            if _blank_text(release):
                raise _fail(
                    "--apply writes only inside a data release: pass --release <name>",
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
                release=release,
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
        typer.echo(
            f"frozen parameters (stored set sha256 {record.params_sha256}; a key the "
            "stored set lacks shows its table default):"
        )
        values = frozen_values(record)
        for key in sorted(values):
            typer.echo(f"  {key} = {json.dumps(values[key], default=str)}")

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
                        "count_share_threshold": frozen_values(record)[GAP_THRESHOLD_KEY],
                        "start_session": str(trial_row["start_session"]),
                        "end_session": str(trial_row["end_session"]),
                    },
                    hypothesis_id=record.hypothesis_id,
                    trial_id=trial,
                )
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        typer.echo(f"decision {decision_id}: gap_signoff for trial {trial} ({record.slug})")

    release_app = typer.Typer(no_args_is_help=True, help="Record a named data release.")
    decision_app.add_typer(release_app, name="data-release")

    def _backup(path: Path, s: Settings) -> duckdb.DuckDBPyConnection:
        try:
            return registry.open_backup(path, Path(s.store.path))
        except registry.BackupRefused as exc:
            raise _fail(_scrubbed(str(exc), s), USAGE_ERROR) from None

    def _importer(
        group: Sequence[Mapping[str, Any]],
    ) -> Callable[[duckdb.DuckDBPyConnection], list[int]]:
        return lambda conn: registry.import_release(conn, group)

    def _release_write[T](write: Callable[[duckdb.DuckDBPyConnection], T], s: Settings) -> T:
        """Run one release write in its own committed chunk on a migrated store."""
        try:
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                return write(conn)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        except registry.ReleaseRefused as exc:
            raise _fail(f"release refused: {_scrubbed(str(exc), s)}", 1) from None

    @release_app.command("open")
    def release_open(
        name: Annotated[str, typer.Option(help="the release name (lowercase, digits, hyphens)")],
        backup: Annotated[Path, typer.Option(help="the backup file taken just before")],
        reason: Annotated[str, typer.Option(help="why, and the issue number")],
    ) -> None:
        """Open a release: write its `before` row (runbook steps 3 and 4)."""
        if not reason.strip():
            raise _fail("--reason must not be blank", USAGE_ERROR)
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        copy = _backup(backup, s)
        try:
            decision_id = _release_write(
                lambda conn: registry.open_data_release(
                    conn, copy, name=name, backup_path=str(backup), reason=reason
                ),
                s,
            )
        finally:
            copy.close()
        typer.echo(f"decision {decision_id}: data_release {name} before (open)")

    @release_app.command("close")
    def release_close(
        name: Annotated[str, typer.Option(help="the open release's name")],
        from_: Annotated[str, typer.Option("--from", help="the first session the repair touched")],
        to: Annotated[str, typer.Option(help="the last session the repair touched")],
        reason: Annotated[
            str | None, typer.Option(help="what differs from the plan (default: open's)")
        ] = None,
    ) -> None:
        """Close the open release: write its `after` row with the sessions touched."""
        if reason is not None and not reason.strip():
            raise _fail("--reason must not be blank", USAGE_ERROR)
        first = _parse_day("--from", from_)
        last = _parse_day("--to", to)
        assert first is not None and last is not None
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        decision_id = _release_write(
            lambda conn: registry.close_data_release(
                conn, name=name, sessions_from=first, sessions_to=last, reason=reason
            ),
            s,
        )
        typer.echo(f"decision {decision_id}: data_release {name} after ({first}..{last})")

    @release_app.command("record")
    def release_record(
        name: Annotated[str, typer.Option(help="the record's name")],
        backup: Annotated[Path, typer.Option(help="the backup that holds the trial's state")],
        trial: Annotated[int, typer.Option(help="the trial whose state the backup holds")],
        reason: Annotated[str, typer.Option(help="why, and the issue number")],
    ) -> None:
        """Record which backup holds the store state a trial read (a closed row)."""
        if not reason.strip():
            raise _fail("--reason must not be blank", USAGE_ERROR)
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing

        def _record(conn: duckdb.DuckDBPyConnection) -> registry.DataRelease:
            decision_id = registry.record_trial_state(
                conn, copy, name=name, backup_path=str(backup), trial_id=trial, reason=reason
            )
            (row,) = [r for r in registry.data_releases(conn) if r.decision_id == decision_id]
            return row

        copy = _backup(backup, s)
        try:
            row = _release_write(_record, s)
        finally:
            copy.close()
        equal = row.values["vintage_equals_trial"]
        runs = len(row.values["repair_runs"])
        typer.echo(
            f"decision {row.decision_id}: data_release {name} record for trial {trial}: "
            f"data_vintage {_fmt(row.data_vintage)} "
            f"{'equals' if equal else 'DIFFERS FROM'} the trial's "
            f"{row.values.get('trial_data_vintage', '-')}; {runs} repair run(s) since the trial"
        )

    @release_app.command("import-file")
    def release_import(
        path: Annotated[Path, typer.Argument(help="the hand-written data/releases.toml")],
    ) -> None:
        """Import the hand-written release record once, one release per transaction."""
        try:
            entries = registry.read_release_file(path)
        except registry.ReleaseFileError as exc:
            raise _fail(str(exc), USAGE_ERROR) from None
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        groups = _release_write(lambda conn: registry.plan_release_import(conn, entries), s)
        for group in groups:
            ids = _release_write(_importer(group), s)
            stages = ", ".join(str(e["stage"]) for e in group)
            typer.echo(f"{group[0]['name']}: imported {stages} (decisions {ids})")

    @decision_app.command("development-boundary")
    def development_boundary(
        date_: Annotated[str, typer.Option("--date", help="the last development day (YYYY-MM-DD)")],
        reason: Annotated[
            str, typer.Option(help="why, naming every family registered or run under the old one")
        ],
    ) -> None:
        """Set the development boundary: a new row, never an edit (ADR 0016)."""
        if not reason.strip():
            raise _fail("--reason must not be blank", USAGE_ERROR)
        day = _parse_day("--date", date_)
        assert day is not None
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                decision_id = registry.write_development_boundary(conn, boundary=day, reason=reason)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        except registry.BoundaryRefused as exc:
            raise _fail(f"boundary refused: {exc}", 1) from None
        typer.echo(f"decision {decision_id}: development_boundary {day}")

    def _shakedown_write(
        kind: registry.DecisionKind, values: Mapping[str, Any], reason: str
    ) -> int:
        """Write one shakedown decision on a migrated store (ADR 0017 part E)."""
        if not reason.strip():
            raise _fail("--reason must not be blank", USAGE_ERROR)
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                return registry.record_decision(conn, kind=kind, reason=reason, values=values)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        except registry.ShakedownRefused as exc:
            raise _fail(f"shakedown refused: {exc}", 1) from None
        except ValueError as exc:
            raise _fail(str(exc), USAGE_ERROR) from None

    @decision_app.command("shakedown-span")
    def shakedown_span(
        sessions: Annotated[int, typer.Option(help="N: the sessions the span needs")],
        order_sessions: Annotated[
            int, typer.Option(help="M: the sessions with a live fill it needs (1 to N)")
        ],
        reason: Annotated[str, typer.Option(help="why, naming the strategy that goes live first")],
    ) -> None:
        """Open (or restart) the shakedown span with its two thresholds (ADR 0017 E)."""
        decision_id = _shakedown_write(
            "shakedown_span",
            {"sessions": sessions, "order_sessions": order_sessions},
            reason,
        )
        typer.echo(
            f"decision {decision_id}: shakedown_span (sessions {sessions}, "
            f"order sessions {order_sessions}); the span starts at the next session"
        )

    @decision_app.command("shakedown-note")
    def shakedown_note(
        alert: Annotated[int, typer.Option(help="the alert id the note explains")],
        reason: Annotated[str, typer.Option(help="what happened and why it is accepted")],
    ) -> None:
        """Note an alert inside the shakedown span (ADR 0017 E.1 and E.7)."""
        decision_id = _shakedown_write("shakedown_note", {"alert_id": alert}, reason)
        typer.echo(f"decision {decision_id}: shakedown_note for alert {alert}")

    sweep_app = typer.Typer(no_args_is_help=True, help="Register, run and judge sweeps.")
    app.add_typer(sweep_app, name="sweep")

    @sweep_app.command("register")
    def sweep_register(
        file: Annotated[Path, typer.Argument(help="the sweep file (docs/sweeps/*.md)")],
    ) -> None:
        """Register a sweep file: its sweep row, one hypothesis per variant."""
        s = settings()
        if not file.is_file():
            raise _fail(f"no sweep file at {file}", USAGE_ERROR)
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                registration = lab_sweep.register(conn, file, s, registered_by=_REGISTERED_BY)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        except (LabNotInitialised, *_LAB_REFUSALS) as exc:
            raise _lab_fail(exc, s) from None
        record = registration.sweep
        lines = [
            f"sweep {record.slug} (registration {record.sweep_id}, {record.family}): "
            f"{record.n_variants} variants, in-sample from {record.in_sample_start}, "
            f"holdout {record.holdout_start} to {record.holdout_end}"
            + ("" if registration.created else "; unchanged, nothing written"),
            f"selection statistic {record.selection_statistic}, promote at least "
            f"{record.promote_at_least}, retire below {record.retire_below}",
        ]
        lines += [
            f"  {v.variant_index}: {h.slug} "
            + json.dumps(v.variant_params, sort_keys=True, default=str)
            for v, h in zip(registration.variants, registration.hypotheses, strict=True)
        ]
        _echo_scrubbed("\n".join(lines), s)

    @sweep_app.command("run")
    def sweep_run(
        slug: Annotated[str, typer.Argument(help="the registered sweep slug")],
        time_budget_minutes: Annotated[
            float | None,
            typer.Option(help="stop at a read-group boundary after this many minutes"),
        ] = None,
        rerun: Annotated[
            bool, typer.Option(help="rerun every variant of a complete sweep")
        ] = False,
        note: Annotated[str | None, typer.Option(help="a note stored with the run")] = None,
    ) -> None:
        """Run a sweep's planned variants as trials on the store."""
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        if s.store.path != get_settings().store.path:
            # run_sweep loads its own settings; the result is read back through `s`.
            raise _fail("sweep run runs only on the loaded settings' store", USAGE_ERROR)
        if time_budget_minutes is not None and not time_budget_minutes > 0:
            raise _fail("--time-budget-minutes must be positive", USAGE_ERROR)
        try:
            with open_read_only(s) as conn:
                schema.init_schema(conn)  # read-only: checks the version, never migrates
                lab_schema.require_lab(conn)
                if lab_registry.sweep_by_slug(conn, slug) is None:
                    raise ValueError(f"no sweep is registered as {slug!r}")
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        except (LabNotInitialised, schema.RegistryNotInitialised, *_LAB_REFUSALS) as exc:
            raise _lab_fail(exc, s) from None
        try:
            outcome = lab.run_sweep(
                slug,
                time_budget_minutes=time_budget_minutes,
                rerun=rerun,
                note=note,
                clock=sweep_clock,
            )
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except (LabNotInitialised, *_SWEEP_RUN_REFUSALS) as exc:
            raise _lab_fail(exc, s) from None
        except Exception as exc:
            # Raised after the run opened: its rows so far stay recorded (the run's
            # row is closed incomplete), so this is a failure, not a refusal.
            message = f"sweep run failed (rows written so far stay recorded): {_describe(exc)}"
            raise _fail(_scrubbed(message, s), 1) from None
        with open_read_only(s) as conn:
            _echo_scrubbed(_sweep_run_text(outcome, conn), s)
        for error in dict.fromkeys(e.strip() for e in outcome.errors.values()):
            typer.echo(_scrubbed(error, s), err=True)
        raise typer.Exit(0 if outcome.n_failed == 0 else 1)

    @sweep_app.command("status")
    def sweep_status(
        slug: Annotated[
            str | None, typer.Argument(help="one sweep slug; every sweep if none")
        ] = None,
    ) -> None:
        """Every sweep's state, or one sweep's stale, terminal-failed and unrun variants."""
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_read_only(s) as conn:
                schema.init_schema(conn)  # read-only: checks the version, never migrates
                text = _sweep_status_text(conn, slug)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        except (LabNotInitialised, schema.RegistryNotInitialised, *_LAB_REFUSALS) as exc:
            raise _lab_fail(exc, s) from None
        _echo_scrubbed(text, s)

    @sweep_app.command("report")
    def sweep_report_(
        slug: Annotated[str, typer.Argument(help="the registered sweep slug")],
    ) -> None:
        """The sweep's report: every variant, the distribution, the verdicts."""
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_read_only(s) as conn:
                schema.init_schema(conn)  # read-only: checks the version, never migrates
                text = sweep_report.format_report(sweep_report.sweep_report(conn, slug))
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        except (LabNotInitialised, schema.RegistryNotInitialised, *_LAB_REFUSALS) as exc:
            raise _lab_fail(exc, s) from None
        _echo_scrubbed(text, s)

    @sweep_app.command("promote")
    def sweep_promote(
        slug: Annotated[str, typer.Argument(help="the complete sweep's slug")],
        file: Annotated[Path, typer.Option(help="the argmax's hypothesis file")],
        reason: Annotated[str, typer.Option(help="why the argmax is promoted")],
    ) -> None:
        """Promote a complete sweep's argmax: register its file with the decision."""
        s = settings()
        if not reason.strip():
            raise _fail("--reason must not be blank", USAGE_ERROR)
        if not file.is_file():
            raise _fail(f"no hypothesis file at {file}", USAGE_ERROR)
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                outcome = promotion.promote(
                    conn, slug, file, reason, s, registered_by=_REGISTERED_BY
                )
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        except (LabNotInitialised, *_LAB_REFUSALS) as exc:
            raise _lab_fail(exc, s) from None
        promoted = outcome.promoted
        _echo_scrubbed(
            f"decision {outcome.decision_id}: promotion of {outcome.variant.slug} "
            f"(sweep registration {outcome.sweep_id}) as hypothesis "
            f"{promoted.hypothesis_id}: {promoted.slug} ({promoted.family}), holdout "
            f"{promoted.holdout_start} to {promoted.holdout_end}",
            s,
        )

    @sweep_app.command("retire")
    def sweep_retire(
        slug: Annotated[str, typer.Argument(help="the sweep slug")],
        reason: Annotated[str, typer.Option(help="why the sweep is retired")],
    ) -> None:
        """Retire a sweep: it promotes nothing afterwards."""
        s = settings()
        if not reason.strip():
            raise _fail("--reason must not be blank", USAGE_ERROR)
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                decision_id = promotion.retire(conn, slug, reason)
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        except (LabNotInitialised, *_LAB_REFUSALS) as exc:
            raise _lab_fail(exc, s) from None
        _echo_scrubbed(f"decision {decision_id}: sweep_retired for {slug}", s)

    lab_app = typer.Typer(no_args_is_help=True, help="The strategy lab's registry.")
    app.add_typer(lab_app, name="lab")

    @lab_app.command("status")
    def lab_status_() -> None:
        """The store size, registry row counts, families, open sweeps and last runs."""
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            with open_read_only(s) as conn:
                schema.init_schema(conn)  # read-only: checks the version, never migrates
                status = sweep_report.lab_status(conn, s, now=clock())
        except StoreLockedError as exc:
            raise _fail(f"store busy: {exc}", 1) from None
        except schema.SchemaVersionError as exc:
            raise _fail(str(exc), 1) from None
        except (LabNotInitialised, schema.RegistryNotInitialised, *_LAB_REFUSALS) as exc:
            raise _lab_fail(exc, s) from None
        _echo_scrubbed(sweep_report.format_lab_status(status), s)

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

    # --- Research labeling (research-labeling spec C12, #1330; plan T124) -------------

    corpus_app = typer.Typer(no_args_is_help=True, help="Fetch research corpora from EDGAR.")
    app.add_typer(corpus_app, name="corpus")
    corpus_fetch_app = typer.Typer(no_args_is_help=True, help="Fetch one corpus.")
    corpus_app.add_typer(corpus_fetch_app, name="fetch")

    @corpus_fetch_app.command("departure-reason")
    def corpus_fetch_departure_reason(
        since: Annotated[
            str, typer.Option(help="first Form 25 filing date, YYYY-MM-DD")
        ] = departure_fetch.POPULATION_START.isoformat(),
        until: Annotated[
            str | None, typer.Option(help="last Form 25 filing date (default: today, UTC)")
        ] = None,
        cik: Annotated[
            list[str] | None, typer.Option("--cik", help="only this issuer CIK (repeatable)")
        ] = None,
        limit: Annotated[
            int | None, typer.Option(help="only the first n accessions by filing date")
        ] = None,
    ) -> None:
        """Fetch the departure-reason corpus (Form 25s, notices, one 8-K each)."""
        start = _parse_day("--since", since)
        assert start is not None
        end = _parse_day("--until", until)
        if limit is not None and limit < 1:
            raise _fail("--limit must be at least 1", USAGE_ERROR)
        ciks = tuple(c.strip() for c in cik or ())
        bad = [c for c in ciks if not (c.isascii() and c.isdigit())]
        if bad:
            raise _fail(f"--cik must be digits: {', '.join(bad)}", USAGE_ERROR)
        s = settings()
        missing = _missing_secrets(s, "edgar")
        if missing:
            raise _fail(f"missing required secret(s): {', '.join(missing)}", USAGE_ERROR)
        now = ensure_tz_aware_utc(clock(), field_name="clock()")
        last = end if end is not None else now.date()
        if last < start:
            what = f"--until {last}" if end is not None else f"today ({last}, the default --until)"
            raise _fail(f"--since {start} is after {what}", USAGE_ERROR)
        try:
            result = departure_fetch.fetch_departure_corpus(
                since=start,
                until=last,
                ciks=ciks,
                limit=limit,
                settings=s,
                client=edgar_client,
                now=now,
            )
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
            raise _fail(_scrubbed(f"corpus fetch failed: {_describe(exc)}", s), 1) from None
        counts = result.counts.as_json()
        _echo_scrubbed(f"corpus {result.corpus_path}", s)
        _echo_scrubbed(f"  counts {result.counts_path}", s)
        typer.echo("  " + ", ".join(f"{k} {v}" for k, v in counts.items()))

    research_app = typer.Typer(
        no_args_is_help=True, help="Research labeling under ADR 0013 (run by the owner)."
    )
    app.add_typer(research_app, name="research")
    frame_app = typer.Typer(no_args_is_help=True, help="Build research frames.")
    research_app.add_typer(frame_app, name="frame")
    frame_build_app = typer.Typer(no_args_is_help=True, help="Build one frame.")
    frame_app.add_typer(frame_build_app, name="build")

    @frame_build_app.command("departure-reason")
    def frame_build_departure_reason(
        corpus: Annotated[Path, typer.Option(help="the corpus file (corpus.jsonl)")],
        as_of: Annotated[str, typer.Option(help="t, an ISO datetime with its UTC offset")],
        register_: Annotated[
            bool, typer.Option("--register", help="register the frame as a dataset version")
        ] = False,
    ) -> None:
        """Build the departure-reason frame: the rule answer of every listing end at t."""
        t = _parse_as_of(as_of)
        s = settings()
        if t > ensure_tz_aware_utc(clock(), field_name="clock()"):
            raise _fail(f"--as-of {t.isoformat()} is in the future", USAGE_ERROR)
        if not corpus.is_file():
            raise _fail(f"no corpus file at {corpus}", USAGE_ERROR)
        if (missing := _store_missing(s)) is not None:
            raise missing
        try:
            result = labeling_frame.build_frame(corpus, t, s)
            record = None
            if register_:
                with open_for_write(s) as conn:
                    schema.init_schema(conn)
                    record = labeling_frame.register(conn, result)
        except StoreLockedError as exc:
            raise _fail(_scrubbed(f"store busy: {exc}", s), 1) from None
        except (*_LABELING_REFUSALS, KeyError) as exc:
            raise _refusal(exc, s) from None
        _echo_scrubbed(f"frame {result.frame_path}", s)
        typer.echo(
            f"  sha256 {result.sha256}, {result.n_rows} rows, events {result.event_start} "
            f"to {result.event_end}, as of {t.isoformat()}"
        )
        typer.echo("  " + ", ".join(f"{k} {v}" for k, v in result.counts.as_json().items()))
        for label, rows in (
            ("delistings ending no listing", result.delistings_ending_no_listing),
            ("unmatched delistings", result.unmatched_delistings),
        ):
            ids = sorted({r.security_id for r in rows})
            typer.echo(f"  {label}: {len(rows)}" + (f" ({', '.join(ids)})" if ids else ""))
        if record is not None:
            typer.echo(f"dataset {record.dataset_id}: {record.name} {record.version}")
            return
        _echo_scrubbed(
            f"register it with: tradepartner dataset register --name {FRAME_DATASET} "
            f"--version {result.sha256[:12]} --path {result.frame_path} --event-start "
            f"{result.event_start} --event-end {result.event_end} "
            "--event-column form25_accepted_at",
            s,
        )

    @research_app.command("gold")
    def research_gold(
        frame: Annotated[
            int | None, typer.Option(help="the departure-reason-frame dataset id (new session)")
        ] = None,
        seed: Annotated[int | None, typer.Option(help="the registered seed (new session)")] = None,
        n: Annotated[
            int | None,
            typer.Option("--n", help=f"cases to draw (new session; default {GOLD_N_DEFAULT})"),
        ] = None,
        exclude: Annotated[
            Path | None, typer.Option(help="the exclusion CSV (new session)")
        ] = None,
        lock: Annotated[
            bool, typer.Option("--lock", help="lock a complete session; opens no page")
        ] = False,
    ) -> None:
        """Label the gold sample on the local review page: build the session, or resume
        it with no flags; the session is locked once every case is answered."""
        s = settings()
        given = any(v is not None for v in (frame, seed, n, exclude))
        if lock and given:
            raise _fail("--lock runs alone, on the existing session", USAGE_ERROR)
        path = gold.session_path(s)
        try:
            if path.exists():
                session = gold.open_gold_session(
                    path, gold.GoldFlags(frame, seed, n, exclude), settings=s
                )
            elif lock:
                raise _fail(f"no gold session at {path} to lock", USAGE_ERROR)
            else:
                if frame is None or seed is None or exclude is None:
                    raise _fail(
                        "a new gold session needs --frame, --seed and --exclude", USAGE_ERROR
                    )
                if (missing := _store_missing(s)) is not None:
                    raise missing
                with open_read_only(s) as conn:
                    row = research.get_dataset(conn, frame)
                if row.name != FRAME_DATASET:
                    raise ExperimentFileError(
                        f"dataset {frame} is {row.name!r}, not a {FRAME_DATASET}"
                    )
                built = gold.build_gold_session(
                    gold.FrameExport(frame, _export_path(s, row), row.sha256),
                    seed,
                    GOLD_N_DEFAULT if n is None else n,
                    exclude,
                    settings=s,
                )
                session = built.session
                x = built.exclusion
                typer.echo(
                    f"exclusion: {x.accessions_matched} accessions matched, {x.ciks} issuer "
                    f"CIKs, {x.rows_removed} rows removed, sha256 {x.sha256}"
                )
                if x.unmatched:
                    typer.echo(f"  accessions matching no frame row: {', '.join(x.unmatched)}")
            if lock:
                _settle_gold(session, s, recovery=True)
                return
        except StoreLockedError as exc:
            raise _fail(_scrubbed(f"store busy: {exc}", s), 1) from None
        except _LABELING_REFUSALS as exc:
            raise _refusal(exc, s) from None
        splits = [c.split for c in session.cases]
        typer.echo(
            f"gold session: {len(session.cases)} cases ({splits.count('dev')} dev, "
            f"{splits.count('pilot')} pilot), {_gold_open_count(session)} open"
        )
        _launch(launcher, _page_argv(session.session_file))
        try:  # the session's state is its working file, read again by every step
            _settle_gold(session, s, recovery=False)
        except _LABELING_REFUSALS as exc:
            raise _refusal(exc, s) from None

    @research_app.command("review")
    def research_review(
        run: Annotated[int, typer.Option("--run", help="the unfinished batch run to review")],
        finish: Annotated[
            bool, typer.Option("--finish", help="finish a fully decided session; opens no page")
        ] = False,
    ) -> None:
        """Review a batch's shortlist on the local review page; the run is finished once
        every item is decided."""
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        version = _code_version_text()
        try:
            session = review.build_review_session(
                run, settings=s, connect=lambda: open_read_only(s), code_version=version
            )
            if finish:
                _settle_review(session, s, recovery=True)
                return
        except StoreLockedError as exc:
            raise _fail(_scrubbed(f"store busy: {exc}", s), 1) from None
        except _LABELING_REFUSALS as exc:
            raise _refusal(exc, s) from None
        open_count = _review_open_count(session)
        typer.echo(f"review session: run {run}, {len(session.items)} items, {open_count} open")
        _launch(launcher, _page_argv(session.review_path, "--code-version", version))
        try:
            _settle_review(session, s, recovery=False)
        except _LABELING_REFUSALS as exc:
            raise _refusal(exc, s) from None

    @research_app.command("label")
    def research_label(
        slug: Annotated[str, typer.Argument(help="the registered experiment slug")],
        dataset: Annotated[int, typer.Option(help="the dataset version id to bind")],
        split: Annotated[str, typer.Option(help="the split to bind")],
        model: Annotated[
            str | None, typer.Option(help="the pinned model id, jev-X.Y.Z (never an alias)")
        ] = None,
        dry_run: Annotated[
            bool, typer.Option("--dry-run", help="print the estimate; open nothing, call nothing")
        ] = False,
        configurations: Annotated[
            int, typer.Option(help="configurations this run will evaluate")
        ] = 1,
        accepted_from: Annotated[
            str | None, typer.Option(help="first Form 25 acceptance day, YYYY-MM-DD")
        ] = None,
        accepted_to: Annotated[
            str | None, typer.Option(help="last Form 25 acceptance day, YYYY-MM-DD")
        ] = None,
        limit: Annotated[int | None, typer.Option(help="only the first n rows")] = None,
        drift_gold: Annotated[
            int | None, typer.Option(help="a batch: the gold dataset holding the drift set")
        ] = None,
        drift_baseline_run: Annotated[
            int | None, typer.Option(help="a batch: the frozen configuration's dev run")
        ] = None,
        spend_holdout: Annotated[
            bool, typer.Option(help="score the sealed pilot period (needs a reason)")
        ] = False,
        holdout_reason: Annotated[str | None, typer.Option(help="why the period is spent")] = None,
    ) -> None:
        """Label one batch with the pinned model (or, with --dry-run, estimate it)."""
        start = _parse_day("--accepted-from", accepted_from)
        end = _parse_day("--accepted-to", accepted_to)
        if limit is not None and limit < 1:
            raise _fail("--limit must be at least 1", USAGE_ERROR)
        if configurations < 1:
            raise _fail("--configurations must be at least 1", USAGE_ERROR)
        s = settings()
        if (missing := _store_missing(s)) is not None:
            raise missing
        if dry_run:
            try:
                with open_read_only(s) as conn:
                    row = research.get_dataset(conn, dataset)
                    report = job.dry_run(
                        conn,
                        s,
                        dataset,
                        _export_path(s, row),
                        accepted_from=start,
                        accepted_to=end,
                        limit=limit,
                        now=ensure_tz_aware_utc(clock(), field_name="clock()"),
                    )
            except StoreLockedError as exc:
                raise _fail(_scrubbed(f"store busy: {exc}", s), 1) from None
            except _LABELING_REFUSALS as exc:
                raise _refusal(exc, s) from None
            for line in report.lines():
                typer.echo(line)
            typer.echo(
                "dry run: no run opened, no call made; the estimate covers every row of "
                "the export, an upper bound for the split"
            )
            return
        if model is None:
            raise _fail("--model is required (a pinned jev-X.Y.Z id)", USAGE_ERROR)
        if spend_holdout and _blank_text(holdout_reason):
            raise _fail("--spend-holdout needs a non-blank --holdout-reason", USAGE_ERROR)
        if holdout_reason is not None and not spend_holdout:
            raise _fail("--holdout-reason goes with --spend-holdout", USAGE_ERROR)
        drift_flags = (drift_gold, drift_baseline_run)
        drift: job.DriftProbe | None = None
        if split in job.FRAME_SPLITS:
            if drift_gold is None or drift_baseline_run is None:
                raise _fail(
                    f"a {split!r} batch runs the drift probe first: give --drift-gold and "
                    "--drift-baseline-run",
                    USAGE_ERROR,
                )
            drift = job.DriftProbe(dataset_id=drift_gold, baseline_run_id=drift_baseline_run)
        elif any(v is not None for v in drift_flags):
            raise _fail("--drift-gold and --drift-baseline-run go with a batch split", USAGE_ERROR)
        failure: BaseException | None = None
        result: job.BatchResult | None = None
        opened: list[int] = []
        try:
            with open_for_write(s) as conn:
                schema.init_schema(conn)
                before = _latest_run_id(conn)
                try:
                    result = job.run_batch(
                        conn,
                        s,
                        model_client,
                        slug,
                        dataset,
                        split,
                        model=model,
                        drift=drift,
                        flags=ResearchFlags(spend_holdout=spend_holdout),
                        reasons=ResearchReasons(holdout_reason=holdout_reason),
                        configurations=configurations,
                        accepted_from=start,
                        accepted_to=end,
                        limit=limit,
                        run_by=_REGISTERED_BY,
                    )
                except (Exception, KeyboardInterrupt) as exc:
                    # Commit what the job wrote (its run rows, a `failed` close) rather
                    # than roll it back: the run's records file already exists. A run
                    # the job left open (Ctrl-C reaches no `close_run`) is closed here.
                    failure = exc
                    opened = _close_open_runs(conn, s, before, _describe(exc))
        except StoreLockedError as exc:
            raise _fail(_scrubbed(f"store busy: {exc}", s), 1) from None
        except Exception as exc:
            raise _fail(_scrubbed(f"failed: {_describe(exc)}", s), 1) from None
        if failure is not None:
            if isinstance(failure, KeyboardInterrupt):
                runs = ", ".join(str(r) for r in opened) or "none"
                raise _fail(
                    f"interrupted: run(s) opened and closed failed: {runs}", INTERRUPTED_EXIT
                )
            if opened:
                # A run opened: whatever went wrong after it is a failure, never a refusal.
                runs = ", ".join(str(r) for r in opened)
                raise _fail(_scrubbed(f"failed (run {runs}): {_describe(failure)}", s), 1)
            if isinstance(failure, _LABELING_REFUSALS):
                raise _refusal(failure, s)
            raise _fail(_scrubbed(f"failed: {_describe(failure)}", s), 1)
        assert result is not None
        typer.echo(f"run {result.run_id}: {result.outcome}")
        if result.message:
            typer.echo(f"  {_scrubbed(result.message, s)}")
        if result.inferences_dataset_id is not None:
            typer.echo(f"  inference records: dataset {result.inferences_dataset_id}")
        if result.packets_refused:
            typer.echo(f"  packets refused (over the token cap): {len(result.packets_refused)}")
        if result.shortlist is not None:
            active = len(result.shortlist.items) - result.shortlist.n_deferred
            typer.echo(
                f"  shortlist: {active} items to review, {result.shortlist.n_deferred} deferred; "
                f"review with `tradepartner research review --run {result.run_id}`"
            )
        if result.outcome in ("ok", "unfinished"):
            return
        raise typer.Exit(1 if result.outcome == "failed" else USAGE_ERROR)

    paper_app = typer.Typer(no_args_is_help=True, help="Paper trading (Phase 4).")
    app.add_typer(paper_app, name="paper")

    def paper_settings(reason: str | None = None) -> Settings:
        """Settings for a `paper` command, after its usage checks: a blank
        `--reason` exits 2 and a missing store 1, before any broker is built."""
        if reason is not None and _blank_text(reason):
            raise _fail("--reason must be non-blank", USAGE_ERROR)
        s = settings()
        if (absent := _store_missing(s)) is not None:
            raise absent
        return s

    def write_chunk(s: Settings) -> window.Connect:
        return lambda: open_for_write(s)

    @paper_app.command("start")
    def paper_start(
        hypothesis: Annotated[str, typer.Option(help="the registered hypothesis slug")],
    ) -> None:
        """Open a paper window for a signed-off hypothesis (spec req 14)."""
        s = paper_settings()
        result = _paper_call(
            s,
            lambda: window.start(s, lambda: open_read_only(s), broker(s, clock), clock, hypothesis),
        )
        opened = result.window
        _echo_scrubbed(
            f"paper start: window {opened.window_id} open for {hypothesis!r}; first rebalance "
            f"{opened.first_rebalance_session}; starting equity {opened.starting_equity:.2f}",
            s,
        )
        if result.abandoned_note is not None:
            _echo_scrubbed(f"after an abandoned window: {result.abandoned_note}", s)

    @paper_app.command("stop")
    def paper_stop(reason: Annotated[str, _REASON_OPTION]) -> None:
        """Request the window's stop, or close it once the stop run is done (req 14)."""
        s = paper_settings(reason)
        result = _paper_call(
            s, lambda: window.stop(s, write_chunk(s), broker(s, clock), clock, reason)
        )
        _echo_scrubbed(
            f"paper stop: {result.state}; reconciliation {_fmt(result.reconciliation_id)}; "
            f"residues {result.residues_json or '-'}",
            s,
        )

    @paper_app.command("run")
    def paper_run_() -> None:
        """The tracking run on the clock's session (spec req 7; the scheduler's job)."""
        s = paper_settings()
        try:
            outcome = paper_run.tracking_run(s, write_chunk(s), broker(s, clock), clock)
        except Exception as exc:
            raise _fail(
                _scrubbed(f"paper run: failed: {_describe(exc)}", s), CRASH_EXIT_CODE
            ) from exc
        _echo_scrubbed(
            f"paper run: {outcome.status} (run {_fmt(outcome.run_id)}, session "
            f"{_fmt(outcome.session)}, kind {_fmt(outcome.kind)})",
            s,
        )
        for note in outcome.notes:
            _echo_scrubbed(f"  {note}", s)
        if outcome.exit_code:
            raise typer.Exit(outcome.exit_code)

    @paper_app.command("reconcile")
    def paper_reconcile() -> None:
        """Reconcile the journal with the broker now (spec req 6)."""
        s = paper_settings()
        result = _paper_call(
            s, lambda: reconcile_run.reconcile_command(s, write_chunk(s), broker(s, clock), clock)
        )
        _echo_scrubbed(f"paper reconcile: {result.status}; {result.mismatches_json}", s)
        if result.status != RECONCILE_OK:
            raise typer.Exit(CRASH_EXIT_CODE)

    @paper_app.command("kill")
    def paper_kill(reason: Annotated[str, _REASON_OPTION]) -> None:
        """Engage the kill switch (spec req 5); takes no run lock."""
        s = paper_settings(reason)
        event_id = _paper_call(s, lambda: window.kill(s, write_chunk(s), clock, reason))
        _echo_scrubbed(f"paper kill: engaged (kill_switch event {event_id})", s)

    @paper_app.command("resume")
    def paper_resume_(
        reason: Annotated[str, _REASON_OPTION],
        accept_broker_fills: Annotated[
            bool, typer.Option("--accept-broker-fills", help="settle lagging fills (req 8)")
        ] = False,
        accept_rejections_flag: Annotated[
            bool,
            typer.Option(
                "--accept-rejections",
                help="accept rejection-cap verdicts (req 5)",
                allow_from_autoenv=False,
            ),
        ] = False,
    ) -> None:
        """Settle, collect, reconcile and release the kill switch (spec req 5)."""
        s = paper_settings(reason)
        outcome = _paper_call(
            s,
            lambda: paper_resume.resume(
                s,
                write_chunk(s),
                broker(s, clock),
                clock,
                reason,
                accept_broker_fills,
                accept_rejections=accept_rejections_flag,
            ),
        )
        lines = [
            f"paper resume: {outcome.status} (resume {_fmt(outcome.resume_id)}, reconciliation "
            f"{_fmt(outcome.reconciliation_id)}, released {_fmt(outcome.released_event_id)})",
            *(f"  reason: {r}" for r in outcome.reasons),
            *(f"  crashed run {r}" for r in outcome.crashed_runs),
            *(f"  settled {o}: {how}" for o, how in outcome.settled),
            *(f"  synthetic fill for {o}" for o in outcome.synthetic_fills),
            *(f"  accepted {a}" for a in outcome.accepted_rejections),
        ]
        for line in lines:
            _echo_scrubbed(line, s)
        if outcome.status == paper_resume.NO_WINDOW:
            raise typer.Exit(PAPER_REFUSAL_EXIT["no_window"])
        if outcome.status == paper_resume.REFUSED:
            raise typer.Exit(PAPER_REFUSAL_EXIT["refused"])

    @paper_app.command("report")
    def paper_report_() -> None:
        """Open the tracking trial and compare paper with it (spec req 10)."""
        s = paper_settings()
        result = _paper_call(s, lambda: paper_report.report(s, lambda: open_read_only(s)))
        monthly, row = result.monthly, result.paper_report
        lines = [
            f"paper report: trial {row.trial_id} through {row.through_session}; rule "
            f"{monthly.tracking_rule}, k {monthly.tracking_k:g}; "
            + ("passed" if monthly.passed else f"failed at {monthly.failing_month}"),
            *(
                f"  {m.rebalance_session}: raw {m.raw:.6f} dividend {m.dividend_term:.6f} "
                f"fill {m.fill_timing_term:.6f} residual {m.residual:.6f} residue "
                f"{m.residue_term:.6f} cost {m.modelled_cost:.6f}"
                f"{' excluded' if m.excluded else ''}{' missed' if m.missed else ''}"
                f"{' override' if m.override else ''} {'pass' if m.passed else 'FAIL'}"
                for m in monthly.months
            ),
            *(
                f"  target {t.rebalance_session} {t.security_id}: paper "
                f"{_fmt(t.paper_weight)} trial {_fmt(t.trial_weight)} ({t.difference})"
                for t in result.targets.rows
            ),
        ]
        for line in lines:
            _echo_scrubbed(line, s)

    @paper_app.command("check")
    def paper_check_() -> None:
        """The four exit-criteria checks; exit 0 only when all pass (spec req 15)."""
        s = paper_settings()

        def run_check() -> list[paper_check.CheckLine]:
            with open_read_only(s) as conn:
                return paper_check.check(conn, s)

        lines = _paper_call(s, run_check)
        for line in lines:
            verdict = "PASS" if line.passed else "FAIL"
            _echo_scrubbed(f"{verdict} {line.name}: {line.detail} (query: {line.query})", s)
        if not all(line.passed for line in lines):
            raise typer.Exit(CRASH_EXIT_CODE)

    @paper_app.command("status")
    def paper_status() -> None:
        """The operations page's numbers (spec req 12); read-only."""
        s = paper_settings()

        def read() -> ops.OpsData:
            with open_read_only(s) as conn:
                return ops.page_data(conn, s)

        data = _paper_call(s, read)
        for line in _status_lines(data):
            _echo_scrubbed(line, s)
        if data.journal_outdated is not None:
            raise typer.Exit(PAPER_REFUSAL_EXIT["refused"])

    @paper_app.command("abandon")
    def paper_abandon(reason: Annotated[str, _REASON_OPTION]) -> None:
        """End the window without flattening, owner-only (#247 Q13)."""
        s = paper_settings(reason)
        result = _paper_call(
            s, lambda: window.abandon(s, write_chunk(s), broker(s, clock), clock, reason)
        )
        _echo_scrubbed(
            f"paper abandon: abandoned; reconciliation {result.reconciliation_id} "
            f"{result.reconciliation_status}; residues {result.residues_json}",
            s,
        )

    @paper_app.command("override")
    def paper_override(
        kind: Annotated[str, typer.Option(help="exclude_name, keep_name or engage_kill_switch")],
        reason: Annotated[str, _REASON_OPTION],
        session: Annotated[
            str | None, typer.Option("--session", help="the rebalance session, YYYY-MM-DD")
        ] = None,
        name: Annotated[str | None, typer.Option(help="the security_id")] = None,
    ) -> None:
        """Append an override through the override page's writer (spec req 9)."""
        day = _parse_day("--session", session)
        s = paper_settings(reason)
        override_id = _paper_call(s, lambda: window.override(s, clock, kind, day, name, reason))
        _echo_scrubbed(f"paper override: override {override_id} written ({kind})", s)

    @paper_app.command("settle")
    def paper_settle(
        order: Annotated[
            list[str],
            typer.Option(
                "--order", help="the order's client_order_id, once", allow_from_autoenv=False
            ),
        ],
        reason: Annotated[str, _REASON_OPTION],
    ) -> None:
        """Settle one order no collector can close, owner-only (spec req 17)."""
        if len(order) != 1:
            raise _fail("--order is given exactly once", USAGE_ERROR)
        if _blank_text(order[0]):
            raise _fail("--order must be non-blank", USAGE_ERROR)
        (client_order_id,) = order
        s = paper_settings(reason)
        result = _paper_call(
            s,
            lambda: window.settle_order(
                s, write_chunk(s), broker(s, clock), clock, client_order_id, reason
            ),
        )
        _echo_scrubbed(
            f"paper settle: {result.client_order_id} settled (override {result.override_id}, "
            f"at {_fmt(result.known_at)}{', reset' if result.reset else ''})",
            s,
        )

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
