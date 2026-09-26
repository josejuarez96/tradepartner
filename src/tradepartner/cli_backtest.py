"""Backtest CLI commands (backtest spec reqs 11, 12, 16; plan T42). SPIKE, not reviewed.

`backtest <hypothesis>`, `hypothesis register <file>`, `trials` and
`decision gap-signoff --trial <id> --reason`. Plan T42 puts them in `cli.py`, which T19
creates; this spike keeps them here as a Typer app plus `add_to(root)`, so T19's app can
mount them without both spikes editing one file.

Every command runs on `settings.store.path`: there is no `--synthetic` and no store-path
option (T42), so `run_hypothesis` never gets a `store_path` from here. Exit codes: 0 ok,
1 failed (or an error before any trial exists), 2 refused. Everything printed passes
through `cli_record.scrub_text` with the configured secrets, the patterns T42's tests
check the output against.
"""

from __future__ import annotations

import base64
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

import duckdb
import typer
from pydantic import SecretStr

from tradepartner.backtest import hypothesis as hypothesis_file
from tradepartner.backtest.holdout import Flags, Reasons
from tradepartner.backtest.run import RunOutcome, run_hypothesis
from tradepartner.cli_record import scrub_text
from tradepartner.config import Settings, get_settings
from tradepartner.store import registry, schema
from tradepartner.store.db import open_for_write, open_read_only

EXIT_OK, EXIT_FAILED, EXIT_REFUSED = 0, 1, 2
_EXIT_BY_STATUS = {"ok": EXIT_OK, "failed": EXIT_FAILED}

#: The metrics the `backtest` table shows, per series at the base cost level.
TABLE_METRICS = (
    "cagr",
    "vol_annual",
    "sharpe_annual",
    "max_drawdown",
    "turnover_monthly",
    "excess_cagr_spy",
)
TABLE_SERIES = ("strategy", "SPY", "MTUM")

app = typer.Typer(no_args_is_help=True, add_completion=False)
hypothesis_app = typer.Typer(no_args_is_help=True, help="Pre-register hypotheses.")
decision_app = typer.Typer(no_args_is_help=True, help="Record owner decisions.")


def _secret(value: SecretStr | None) -> str | None:
    text = None if value is None else value.get_secret_value()
    return text if text and text.strip() else None


def _secrets(settings: Settings) -> list[str]:
    """Every configured secret value, and the Alpaca Basic-auth form (as `cli_record`)."""
    key, secret = _secret(settings.alpaca_api_key), _secret(settings.alpaca_api_secret)
    values = [v for v in (key, secret, _secret(settings.sec_edgar_user_agent)) if v]
    if key and secret:
        values.append(base64.b64encode(f"{key}:{secret}".encode()).decode())
    return values


def _echo(text: str, *, err: bool = False) -> None:
    """Print `text` scrubbed of secrets, emails and key-shaped tokens."""
    scrubbed, _ = scrub_text(text, secrets=_secrets(get_settings()))
    typer.echo(scrubbed, err=err)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def _report(outcome: RunOutcome) -> None:
    """The trial id, status, base-level metrics table, gap maxima and flags, read back
    from the store (what was written, not what the engine returned)."""
    settings = get_settings()
    with open_read_only(settings) as conn:
        trial = conn.execute(
            "SELECT t.kind, t.holdout_repeat, r.status, r.message, "
            "r.red_flag, r.gap_max_count_share, r.gap_max_size_share, r.n_trials, "
            "r.dsr, r.dsr_excess, r.dsr_basis "
            "FROM trials t JOIN trial_results r USING (trial_id) WHERE trial_id = ?",
            [outcome.trial_id],
        ).fetchone()
        if trial is None:  # the process wrote no result row: cannot happen on return
            _echo(f"trial {outcome.trial_id}: unfinished", err=True)
            return
        (kind, repeat, status, message, red, gap_count, gap_size, n, dsr, dsr_x, basis) = trial
        _echo(f"trial {outcome.trial_id}: {status} ({kind})")
        if message:
            _echo(f"  {message}")
        if status != "ok":
            return
        base = hypothesis_file.load_frozen(
            conn, _slug_of(conn, outcome.trial_id), settings=settings
        ).costs.per_side_bps
        rows = conn.execute(
            "SELECT series, metric, value FROM trial_metrics "
            "WHERE trial_id = ? AND cost_per_side_bps = ?",
            [outcome.trial_id, base],
        ).fetchall()
    values = {(series, metric): value for series, metric, value in rows}
    width = max(len(m) for m in TABLE_METRICS)
    _echo(f"metrics at {base:g} bp per side")
    _echo(f"  {'metric':<{width}}  " + "  ".join(f"{s:>10}" for s in TABLE_SERIES))
    for metric in TABLE_METRICS:
        cells = "  ".join(f"{_fmt(values.get((s, metric))):>10}" for s in TABLE_SERIES)
        _echo(f"  {metric:<{width}}  {cells}")
    _echo(f"deflated Sharpe ({basis}, N={n}): raw {_fmt(dsr)}, excess over SPY {_fmt(dsr_x)}")
    _echo(f"gap maxima: count share {_fmt(gap_count)}, size share {_fmt(gap_size)}")
    flags = [name for name, on in (("red_flag", red), ("holdout_repeat", repeat)) if on]
    _echo(f"flags: {', '.join(flags) if flags else 'none'}")


def _slug_of(conn: duckdb.DuckDBPyConnection, trial_id: int) -> str:
    row = conn.execute(
        "SELECT h.slug FROM trials t JOIN hypotheses h USING (hypothesis_id) WHERE trial_id = ?",
        [trial_id],
    ).fetchone()
    if row is None:
        raise registry.RegistryError(f"trial {trial_id} does not exist")
    return str(row[0])


@app.command("backtest")
def backtest(
    hypothesis: Annotated[str, typer.Argument(help="Slug of a registered hypothesis.")],
    start: Annotated[datetime | None, typer.Option(formats=["%Y-%m-%d"])] = None,
    end: Annotated[datetime | None, typer.Option(formats=["%Y-%m-%d"])] = None,
    spend_holdout: Annotated[bool, typer.Option("--spend-holdout")] = False,
    holdout_reason: Annotated[str | None, typer.Option("--holdout-reason")] = None,
    holdout_repeat: Annotated[bool, typer.Option("--holdout-repeat")] = False,
    override_gap: Annotated[bool, typer.Option("--override-gap")] = False,
    gap_reason: Annotated[str | None, typer.Option("--gap-reason")] = None,
    note: Annotated[str | None, typer.Option("--note")] = None,
) -> None:
    """Run a registered hypothesis as one trial on the store (exit 0 ok, 1 failed,
    2 refused). A flag without its reason is refused by the holdout rules, as a trial."""
    try:
        outcome = run_hypothesis(
            hypothesis,
            _date(start),
            _date(end),
            Flags(spend_holdout, holdout_repeat, override_gap),
            reasons=Reasons(holdout_reason, gap_reason),
            note=note,
        )
    except (registry.RegistryError, hypothesis_file.HypothesisFileError) as exc:
        _echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_FAILED) from exc
    _report(outcome)
    if outcome.error is not None:
        _echo(outcome.error, err=True)
    raise typer.Exit(_EXIT_BY_STATUS.get(outcome.status, EXIT_REFUSED))


def _date(value: datetime | None) -> date | None:
    return None if value is None else value.date()


@hypothesis_app.command("register")
def register(
    file: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
) -> None:
    """Register a hypothesis file; print its frozen parameters and their hash."""
    settings = get_settings()
    try:
        with open_for_write(settings) as conn:
            schema.init_schema(conn)
            record = hypothesis_file.register(conn, file, registered_by="owner", settings=settings)
    except (registry.RegistryError, hypothesis_file.HypothesisFileError) as exc:
        _echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_FAILED) from exc
    _echo(f"hypothesis {record.hypothesis_id}: {record.slug} ({record.family})")
    for key in sorted(record.params):
        _echo(f"  {key} = {record.params[key]!r}")
    _echo(f"params_sha256 {record.params_sha256}")


@app.command("trials")
def trials(
    hypothesis: Annotated[str | None, typer.Option("--hypothesis")] = None,
    include_synthetic: Annotated[bool, typer.Option("--include-synthetic")] = False,
) -> None:
    """Every trial, newest first; synthetic trials hidden unless asked for."""
    try:
        with open_read_only(get_settings()) as conn:
            schema.init_schema(conn)  # read-only: a version check, never a migration
            listed = registry.list_trials(conn, hypothesis, include_synthetic)
    except schema.RegistryNotInitialised:
        _echo("registry not initialised (run any writing command to migrate the store)")
        return
    for t in listed:
        synthetic = " synthetic" if t.synthetic else ""
        repeat = " holdout_repeat" if t.holdout_repeat else ""
        message = f"  {t.message}" if t.message else ""
        _echo(
            f"{t.trial_id:>5}  {t.started_at:%Y-%m-%d %H:%M}  {t.slug}  {t.kind}{synthetic}"
            f"{repeat}  {t.start_session}..{t.end_session}  {t.status}{message}"
        )


@decision_app.command("gap-signoff")
def gap_signoff(
    trial: Annotated[int, typer.Option("--trial")],
    reason: Annotated[str, typer.Option("--reason")],
) -> None:
    """Record the owner's sign-off that a trial's survivorship gap is acceptable
    (ADR 0003 rule 8, ADR 0009): an `owner_decisions` row of kind `gap_signoff` with
    the trial's gap values at this time. Only an `ok`, non-synthetic trial."""
    settings = get_settings()
    try:
        with open_for_write(settings) as conn:
            schema.init_schema(conn)
            row = conn.execute(
                "SELECT t.hypothesis_id, t.synthetic, r.status, r.gap_max_count_share, "
                "r.gap_max_size_share FROM trials t LEFT JOIN trial_results r USING (trial_id) "
                "WHERE t.trial_id = ?",
                [trial],
            ).fetchone()
            refusal = _signoff_refusal(trial, row)
            if refusal is not None:
                _echo(f"refused: {refusal}", err=True)
                raise typer.Exit(EXIT_REFUSED)
            assert row is not None
            values: dict[str, Any] = {
                "gap_max_count_share": row[3],
                "gap_max_size_share": row[4],
                "count_share_by_rebalance": _gap_series(conn, trial),
            }
            decision_id = registry.record_decision(
                conn,
                kind="gap_signoff",
                reason=reason,
                values=values,
                hypothesis_id=row[0],
                trial_id=trial,
            )
    except (registry.RegistryError, ValueError) as exc:
        _echo(f"error: {exc}", err=True)
        raise typer.Exit(EXIT_FAILED) from exc
    _echo(f"decision {decision_id}: gap_signoff for trial {trial}")


def _signoff_refusal(trial: int, row: tuple[Any, ...] | None) -> str | None:
    if row is None:
        return f"trial {trial} does not exist"
    if row[1]:
        return f"trial {trial} is synthetic"
    if row[2] != "ok":
        return f"trial {trial} is {row[2] or 'unfinished'}, not ok"
    return None


def _gap_series(conn: duckdb.DuckDBPyConnection, trial: int) -> dict[str, float | None]:
    """The count share per rebalance session (the gap the owner signs); a plan field, so
    the same at every cost level, read at the lowest."""
    rows = conn.execute(
        "SELECT r.session, r.gap_count_share FROM trial_rebalances r "
        "WHERE r.trial_id = ? AND r.cost_per_side_bps = ("
        "  SELECT MIN(cost_per_side_bps) FROM trial_rebalances WHERE trial_id = ?"
        ") ORDER BY r.session",
        [trial, trial],
    ).fetchall()
    return {session.isoformat(): share for session, share in rows}


app.add_typer(hypothesis_app, name="hypothesis")
app.add_typer(decision_app, name="decision")


def add_to(root: typer.Typer) -> None:
    """Mount these commands on T19's `tradepartner` app."""
    root.command("backtest")(backtest)
    root.command("trials")(trials)
    root.add_typer(hypothesis_app, name="hypothesis")
    root.add_typer(decision_app, name="decision")
