"""`tradepartner paper lots-reconcile` (Phase 4 plan T90; ADR 0010 amendment
2026-10-04): its flags, every exit code, and that it writes nothing.

The live store is a temp copy of the fixture universe (`STORE__PATH`, no
`.env`), with a window and one lot-ledger set appended the way
`execution.outcomes` writes them. The export parser is the real stub where a
test needs the refusal, and a parser returning typed rows elsewhere: the real
one is written once the owner's first export exists.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pytest
import typer
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.adapters.broker import OrderRequest, Side
from tradepartner.adapters.fake_broker import Expire, FakeBroker, PartialFill, SetPosition, Vanish
from tradepartner.cli_record import scrub_text
from tradepartner.config import Settings
from tradepartner.execution import ops, switch, window
from tradepartner.execution import report as paper_report
from tradepartner.execution import resume as paper_resume
from tradepartner.execution import run as paper_run
from tradepartner.execution import shakedown as paper_shakedown
from tradepartner.execution.lock import run_lock
from tradepartner.execution.lots_reconcile import BrokerLotRow
from tradepartner.execution.wrapper import WRITE_FAILED_EXIT_CODE
from tradepartner.store import journal, registry
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import (
    DecisionRow,
    DisposalRow,
    LotRow,
    OrderEventRow,
    OrderRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PaperWindowStopRow,
    WashSaleFlagRow,
)
from tradepartner.store.schema import init_schema

ALPACA_KEY = "PK" + "T" * 20  # key-shaped for the scrub, not a real key
ALPACA_SECRET = "not-a-real-secret-" + "s" * 12
EXIT = cli.LOTS_RECONCILE_REFUSAL_EXIT
WINDOW_START = datetime(2026, 1, 2, 14, tzinfo=UTC)
SET_STAMP = datetime(2026, 12, 31, 22, tzinfo=UTC)
CUSIP = "98422D105"


@pytest.fixture(autouse=True)
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_store_path: Path) -> Path:
    """The live store is the fixture copy; secrets are set so a leak is detectable."""
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(fixture_store_path))
    monkeypatch.setenv("ALPACA_API_KEY", ALPACA_KEY)
    monkeypatch.setenv("ALPACA_API_SECRET", ALPACA_SECRET)
    return fixture_store_path


@pytest.fixture
def export_file(tmp_path: Path) -> Path:
    path = tmp_path / "gains-2026.csv"
    path.write_text("Symbol,Date Sold,Quantity,Proceeds,Cost Basis,Wash Sale\nnot,a,known,format\n")
    return path


def _settings() -> Settings:
    return Settings(_env_file=None)


def _window(window_id: int | None, started: datetime) -> PaperWindowRow:
    return PaperWindowRow(
        window_id=window_id,
        hypothesis_id=1,
        first_rebalance_session=started.date(),
        account_id="PA1",
        starting_cash=100_000.0,
        starting_equity=100_000.0,
        code_version="test",
        started_at=started,
        frozen_json="{}",
        frozen_sha256="0" * 64,
        known_at=started,
        ingested_at=started,
    )


def _lot(lot_id: int, day: date, qty: float, basis: float) -> LotRow:
    at = datetime(day.year, day.month, day.day, 15, tzinfo=UTC)
    return LotRow(
        lot_id=lot_id,
        account_id="PA1",
        account_type="paper",
        account_owner="self",
        security_id="SEC_XYZ",
        symbol="XYZ",
        cusip=CUSIP,
        trade_at=at,
        trade_date_local=day,
        quantity=qty,
        cost_basis=basis,
        fill_id=lot_id,
        known_at=SET_STAMP,
        ingested_at=SET_STAMP,
    )


def _disposal(disposal_id: int, lot_id: int, day: date, proceeds: float, pnl: float) -> DisposalRow:
    return DisposalRow(
        disposal_id=disposal_id,
        lot_id=lot_id,
        account_id="PA1",
        trade_at=datetime(day.year, day.month, day.day, 15, tzinfo=UTC),
        trade_date_local=day,
        quantity=10.0,
        proceeds=proceeds,
        realised_pnl=pnl,
        tax_year=day.year,
        fill_id=100 + disposal_id,
        known_at=SET_STAMP,
        ingested_at=SET_STAMP,
    )


def _seed(path: Path, *, earlier_window: bool = False) -> None:
    """A window started 2026-01-02 and one ledger set: lot 1 sold at a loss
    on 2026-02-02 (100.00 disallowed by lot 2), lot 2 sold 2026-06-01. With
    `earlier_window`, an earlier window started 2025-03-03 and closed
    2026-01-02, whose lots the later window's rebuild replaced."""
    with open_for_write(_settings()) as conn:
        init_schema(conn)
        if earlier_window:
            first = journal.append(conn, _window(None, datetime(2025, 3, 3, 14, tzinfo=UTC)))
            assert first is not None
            journal.append(
                conn,
                PaperWindowStopRow(
                    window_id=first,
                    at=WINDOW_START - timedelta(hours=1),
                    state="closed",
                    known_at=WINDOW_START - timedelta(hours=1),
                    ingested_at=WINDOW_START - timedelta(hours=1),
                ),
            )
        journal.append(conn, _window(None, WINDOW_START))
        journal.append(conn, _lot(1, date(2026, 1, 5), 10.0, 1000.0))
        journal.append(conn, _lot(2, date(2026, 2, 10), 10.0, 950.0))
        journal.append(conn, _disposal(1, 1, date(2026, 2, 2), 900.0, -100.0))
        journal.append(conn, _disposal(2, 2, date(2026, 6, 1), 1100.0, 150.0))
        journal.append(
            conn,
            WashSaleFlagRow(
                disposal_id=1,
                replacement_lot_id=2,
                matched_quantity=10.0,
                disallowed_amount=100.0,
                scanned_at=SET_STAMP,
                known_at=SET_STAMP,
                ingested_at=SET_STAMP,
            ),
        )


def _row(day: date, proceeds: str, basis: str, box_1g: str = "0") -> BrokerLotRow:
    return BrokerLotRow(
        symbol="XYZ",
        cusip=CUSIP,
        trade_date=day,
        quantity=Decimal(10),
        proceeds=Decimal(proceeds),
        cost_basis=Decimal(basis),
        disallowed_loss=Decimal(box_1g),
    )


AGREEING = [
    _row(date(2026, 2, 2), "900.00", "1000.00", "100.00"),
    _row(date(2026, 6, 1), "1100.00", "950.00"),
]


def _parser(rows: list[BrokerLotRow]) -> Callable[[Path], list[BrokerLotRow]]:
    return lambda _path: list(rows)


class Out:
    def __init__(self, result: Any) -> None:
        self.exit_code: int = result.exit_code
        self.stdout: str = result.stdout
        self.output: str = result.output


def _cli(*args: str, rows: list[BrokerLotRow] | None = None) -> Out:
    app = cli.make_app() if rows is None else cli.make_app(parse_export=_parser(rows))
    result = CliRunner().invoke(app, list(args))
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception  # a crash is never a refusal
    out = Out(result)
    _, hits = scrub_text(out.output, secrets=[ALPACA_KEY, ALPACA_SECRET])
    assert hits == 0, out.output
    return out


def _reconcile(export: Path, year: int = 2026, rows: list[BrokerLotRow] | None = None) -> Out:
    return _cli(
        "paper", "lots-reconcile", "--export", str(export), "--tax-year", str(year), rows=rows
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- flags ------------------------------------------------------------------------


def test_the_parser_has_exactly_export_and_tax_year() -> None:
    group: Any = typer.main.get_command(cli.make_app())
    command = group.commands["paper"].commands["lots-reconcile"]
    options = {opt for param in command.params for opt in param.opts}

    assert options == {"--export", "--tax-year"}
    for flag in ("--fix", "--adjust", "--write", "--store-path", "--force"):
        assert flag not in options


def test_both_flags_are_required(export_file: Path) -> None:
    assert _cli("paper", "lots-reconcile", "--export", str(export_file)).exit_code == 2
    assert _cli("paper", "lots-reconcile", "--tax-year", "2026").exit_code == 2


def test_an_export_that_does_not_exist_is_a_usage_error(tmp_path: Path, live: Path) -> None:
    _seed(live)
    assert _reconcile(tmp_path / "missing.csv", rows=AGREEING).exit_code == 2


def test_the_exit_codes_are_distinct_and_never_zero() -> None:
    codes = [*EXIT.values(), cli.LOTS_RECONCILE_DIFFERENCE_EXIT]
    assert len(set(codes)) == len(codes)
    assert 0 not in codes and 1 not in codes and 2 not in codes


# --- refusals ---------------------------------------------------------------------


def test_a_malformed_export_is_refused_by_the_stub(export_file: Path, live: Path) -> None:
    _seed(live)

    out = _reconcile(export_file)

    assert out.exit_code == EXIT["unknown_export_format"], out.output
    assert "unknown export format" in out.output


def test_a_missing_store_is_refused(
    export_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("STORE__PATH", str(tmp_path / "nowhere.duckdb"))

    out = _reconcile(export_file, rows=AGREEING)

    assert out.exit_code == EXIT["no_store"], out.output
    assert not (tmp_path / "nowhere.duckdb").exists()


def test_no_window_is_refused(export_file: Path) -> None:
    out = _reconcile(export_file, rows=AGREEING)

    assert out.exit_code == EXIT["no_window"], out.output
    assert "no_window" in out.output


def test_a_store_without_the_journal_is_refused_as_no_window(export_file: Path, live: Path) -> None:
    with duckdb.connect(str(live)) as conn:
        conn.execute("DROP TABLE wash_sale_flags")

    out = _reconcile(export_file, rows=AGREEING)

    assert out.exit_code == EXIT["no_window"], out.output


def test_a_version_16_store_is_refused_as_schema_version(export_file: Path, live: Path) -> None:
    """#1261: `require_journal` raises `SchemaVersionError` on a version-16 store;
    `_read_ledger_set` gives it its own `schema_version` refusal, carrying the
    "open it for writing once" message."""
    with duckdb.connect(str(live)) as conn:
        conn.execute("UPDATE schema_version SET version = 16")
        conn.execute("ALTER TABLE orders DROP COLUMN book_id")

    out = _reconcile(export_file, rows=AGREEING)

    assert out.exit_code == EXIT["schema_version"], out.output
    assert "open it for writing once" in out.output


def test_a_locked_store_is_refused(export_file: Path, live: Path) -> None:
    _seed(live)
    writer = duckdb.connect(str(live))  # a read-write connection in this process
    try:
        out = _reconcile(export_file, rows=AGREEING)
    finally:
        writer.close()

    assert out.exit_code == EXIT["locked"], out.output


def test_no_disposals_in_the_tax_year_is_refused(export_file: Path, live: Path) -> None:
    _seed(live)

    out = _reconcile(export_file, year=2025, rows=[])

    assert out.exit_code == EXIT["no_disposals"], out.output
    assert "no disposal in tax year 2025" in out.output


def test_only_the_latest_ledger_set_is_compared(export_file: Path, live: Path) -> None:
    """An older set that disagrees (another basis, and a flag) is never read:
    the latest set, stamped later and flagless, is the one compared."""
    _seed(live)
    later = SET_STAMP + timedelta(hours=1)
    with open_for_write(_settings()) as conn:
        lot_ids = [
            journal.append(
                conn,
                replace(_lot(0, day, 10.0, basis), lot_id=None, known_at=later, ingested_at=later),
            )
            for day, basis in ((date(2026, 1, 5), 1000.0), (date(2026, 3, 20), 960.0))
        ]
        for lot_id, day, proceeds, pnl in (
            (lot_ids[0], date(2026, 2, 2), 900.0, -100.0),
            (lot_ids[1], date(2026, 6, 1), 1100.0, 140.0),
        ):
            assert lot_id is not None
            journal.append(
                conn,
                replace(
                    _disposal(0, lot_id, day, proceeds, pnl),
                    disposal_id=None,
                    known_at=later,
                    ingested_at=later,
                ),
            )
    rows = [
        _row(date(2026, 2, 2), "900.00", "1000.00"),
        _row(date(2026, 6, 1), "1100.00", "960.00"),
    ]

    out = _reconcile(export_file, rows=rows)

    assert out.exit_code == 0, out.output
    assert "matched: 2" in out.stdout
    # The older set's rows (basis 950, the 100.00 flag) would differ on both.
    assert _reconcile(export_file, rows=AGREEING).exit_code == cli.LOTS_RECONCILE_DIFFERENCE_EXIT


# --- results ----------------------------------------------------------------------


def test_a_matching_year_exits_zero(export_file: Path, live: Path) -> None:
    _seed(live)

    out = _reconcile(export_file, rows=AGREEING)

    assert out.exit_code == 0, out.output
    assert "matched: 2" in out.stdout
    assert "result: clean" in out.stdout
    assert "window 1" in out.stdout


def test_a_difference_exits_three_with_both_figures(export_file: Path, live: Path) -> None:
    _seed(live)
    rows = [AGREEING[0], _row(date(2026, 6, 1), "1099.50", "950.00")]

    out = _reconcile(export_file, rows=rows)

    assert out.exit_code == cli.LOTS_RECONCILE_DIFFERENCE_EXIT, out.output
    assert "difference: disposal 2" in out.stdout
    assert "proceeds broker 1099.50 ledger 1100.00" in out.stdout


def test_an_unmatched_row_on_either_side_exits_three(export_file: Path, live: Path) -> None:
    _seed(live)
    stray = _row(date(2026, 7, 1), "10.00", "10.00")

    out = _reconcile(export_file, rows=[AGREEING[0], stray])

    assert out.exit_code == cli.LOTS_RECONCILE_DIFFERENCE_EXIT, out.output
    assert "unmatched export row: XYZ" in out.stdout
    assert "unmatched ledger disposal: 2" in out.stdout


def test_zero_matched_rows_never_exits_zero(export_file: Path, live: Path) -> None:
    _seed(live)

    out = _reconcile(export_file, rows=[])

    assert out.exit_code == cli.LOTS_RECONCILE_DIFFERENCE_EXIT, out.output
    assert "matched: 0" in out.stdout


def test_a_carried_forward_basis_is_expected_not_a_difference(
    export_file: Path, live: Path
) -> None:
    _seed(live)
    rows = [AGREEING[0], _row(date(2026, 6, 1), "1100.00", "1050.00")]

    out = _reconcile(export_file, rows=rows)

    assert out.exit_code == 0, out.output
    assert "expected (carry-forward not modelled): disposal 2" in out.stdout
    assert "cost_basis broker 1050.00 ledger 950.00" in out.stdout


def test_export_rows_outside_the_tax_year_are_counted_not_compared(
    export_file: Path, live: Path
) -> None:
    _seed(live)

    out = _reconcile(export_file, rows=[*AGREEING, _row(date(2025, 12, 1), "1.00", "1.00")])

    assert out.exit_code == 0, out.output
    assert "outside tax year 2026, not compared: 1" in out.stdout


def test_a_tax_year_reaching_an_earlier_window_says_so(export_file: Path, live: Path) -> None:
    _seed(live, earlier_window=True)

    out = _reconcile(export_file, rows=AGREEING)

    assert out.exit_code == 0, out.output
    assert "the ledger of window 2" in out.stdout
    assert "also reaches window(s) 1" in out.stdout


def test_a_year_inside_the_window_names_no_earlier_window(export_file: Path, live: Path) -> None:
    _seed(live)

    out = _reconcile(export_file, rows=AGREEING)

    assert "also reaches" not in out.stdout


# --- no write ---------------------------------------------------------------------


def test_the_command_opens_the_store_read_only_and_writes_nothing(
    export_file: Path, live: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(live)
    store_before, export_before = _sha256(live), _sha256(export_file)
    opened: list[str] = []
    real = cli.open_read_only

    def read_only(settings: Settings) -> Any:
        opened.append("read_only")
        return real(settings)

    def for_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("paper lots-reconcile opened the store for writing")

    monkeypatch.setattr(cli, "open_read_only", read_only)
    monkeypatch.setattr(cli, "open_for_write", for_write)

    codes = [_reconcile(export_file, rows=rows).exit_code for rows in (AGREEING, [], [])]

    assert codes == [0, 3, 3]
    assert opened == ["read_only"] * 3
    assert _sha256(live) == store_before
    assert _sha256(export_file) == export_before


# --- the paper commands (plan T67) ------------------------------------------------
#
# Each command runs on the fixture store with the scripted fake injected through
# `make_app`'s `broker` parameter, never `build_broker`; the window is opened by
# `paper start` itself, so its `frozen_json` is the real freeze.

SESSION_CLOCK = datetime(2026, 10, 1, 14, tzinfo=UTC)  # 10:00 ET on a session
SATURDAY = datetime(2026, 10, 3, 14, tzinfo=UTC)
HOLDOUT_END = date(2026, 9, 30)  # a completed month-end at SESSION_CLOCK
LONG_REASON = "the owner's reason, long enough for the frozen minimum"
REFUSED = cli.PAPER_REFUSAL_EXIT["refused"]
NO_WINDOW = cli.PAPER_REFUSAL_EXIT["no_window"]
#: Every `paper` command's exact options (spec req 16: no endpoint, run-session,
#: store-path or bypass flag; `override --session` is the override's rebalance session).
PAPER_OPTIONS = {
    "start": {"--hypothesis"},
    "stop": {"--reason"},
    "run": set(),
    "reconcile": set(),
    "kill": {"--reason"},
    "resume": {"--reason", "--accept-broker-fills", "--accept-rejections"},
    "report": {"--book"},
    "check": {"--book"},
    "status": {"--book", "--all"},
    "abandon": {"--reason"},
    "settle": {"--order", "--reason"},
    "override": {"--kind", "--session", "--name", "--reason"},
    "lots-reconcile": {"--export", "--tax-year"},
    "shakedown": set(),
}


class _Clock:
    """The test's clock; `step` moves it on every reading (a real clock does),
    which `paper settle`'s later stamp needs."""

    def __init__(self, now: datetime) -> None:
        self.now = now
        self.step = timedelta(0)

    def __call__(self) -> datetime:
        self.now += self.step
        return self.now


class _Factory:
    """The command's broker parameter: records every clock it is handed and
    returns one scripted fake on the test's clock."""

    def __init__(self, clock: _Clock) -> None:
        self.fake = FakeBroker(
            clock=clock, price_of=lambda _s: 100.0, auto_fill=False, account_id="PA1"
        )
        self.clocks: list[Any] = []

    def __call__(self, _settings: Settings, clock: Any) -> FakeBroker:
        self.clocks.append(clock)
        return self.fake


def _paper(clock: _Clock, factory: _Factory, *args: str) -> Out:
    app = cli.make_app(clock=clock, broker=factory)
    result = CliRunner().invoke(app, ["paper", *args])
    if result.exception is not None and not isinstance(result.exception, SystemExit):
        raise result.exception  # a crash is never a refusal
    out = Out(result)
    _, hits = scrub_text(out.output, secrets=[ALPACA_KEY, ALPACA_SECRET])
    assert hits == 0, out.output
    return out


def _register_h1(tmp_path: Path, *, signed_off: bool) -> None:
    s = _settings()
    with open_for_write(s) as conn:
        init_schema(conn)
        hyp = registry.register_hypothesis(
            conn,
            slug="h1",
            family="momentum",
            title="h1 title",
            doc_path="docs/hypotheses/h1.md",
            doc_sha256="d" * 64,
            params={
                "costs.per_side_bps": 15.0,
                "costs.commission_per_share": 0.0,
                "costs.commission_per_order": 0.0,
                "execution.fill_price": "close",
                "strategy.top_fraction": 0.1,
            },
            in_sample_start=date(2016, 1, 29),
            holdout_start=date(2023, 1, 3),
            holdout_end=HOLDOUT_END,
            registered_by="owner",
            settings=s,
        )
        if not signed_off:
            return
        handle = registry.open_trial(
            conn,
            hypothesis_id=hyp.hypothesis_id,
            kind="in_sample",
            start_session=date(2018, 1, 31),
            end_session=date(2022, 12, 30),
            data_cutoff=datetime(2026, 9, 25, 12, tzinfo=UTC),
            synthetic=False,
            run_by="test",
            settings=s,
            repo_dir=tmp_path,
        )
        assert registry.write_result(conn, handle, registry.ResultStatistics()) == "ok"
        registry.record_decision(
            conn,
            kind="gap_signoff",
            reason="accepted for the test",
            values={},
            hypothesis_id=hyp.hypothesis_id,
            trial_id=handle.trial_id,
        )


@pytest.fixture
def clock() -> _Clock:
    return _Clock(SESSION_CLOCK)


@pytest.fixture
def factory(clock: _Clock) -> _Factory:
    return _Factory(clock)


@pytest.fixture
def started(tmp_path: Path, clock: _Clock, factory: _Factory) -> None:
    """A window opened by `paper start` on a flat fake account."""
    _register_h1(tmp_path, signed_off=True)
    out = _paper(clock, factory, "start", "--hypothesis", "h1")
    assert out.exit_code == 0, out.output
    assert "window 1 open for 'h1'" in out.output


def _count(table: str) -> int:
    with duckdb.connect(_settings().store.path, read_only=True) as conn:
        found = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    assert found is not None
    return int(found[0])


def test_the_paper_parser_has_exactly_the_planned_flags() -> None:
    group: Any = typer.main.get_command(cli.make_app())
    paper = group.commands["paper"].commands
    assert set(paper) == set(PAPER_OPTIONS)
    for name, expected in PAPER_OPTIONS.items():
        options = {o for p in paper[name].params for o in (*p.opts, *p.secondary_opts)}
        assert options - {"--help"} == expected, name
        assert not {"--live", "--store-path", "--force", "--endpoint", "--paper"} & options
        if name != "override":
            assert "--session" not in options, name


@pytest.mark.parametrize("command", ["stop", "kill", "resume", "abandon", "override"])
def test_a_missing_or_blank_reason_exits_non_zero_before_any_broker(
    command: str, clock: _Clock, factory: _Factory
) -> None:
    extra = ["--kind", "engage_kill_switch"] if command == "override" else []
    missing = _paper(clock, factory, command, *extra)
    blank = _paper(clock, factory, command, *extra, "--reason", "   ")
    assert missing.exit_code == cli.USAGE_ERROR
    assert blank.exit_code == cli.USAGE_ERROR
    assert "--reason must be non-blank" in blank.output
    assert factory.clocks == []


def test_paper_start_before_a_gap_signoff_is_refused(
    tmp_path: Path, clock: _Clock, factory: _Factory
) -> None:
    _register_h1(tmp_path, signed_off=False)
    out = _paper(clock, factory, "start", "--hypothesis", "h1")
    assert out.exit_code == REFUSED
    assert "refused: gap_signoff" in out.output
    assert _count("paper_windows") == 0


def test_paper_start_refuses_an_unknown_hypothesis(clock: _Clock, factory: _Factory) -> None:
    out = _paper(clock, factory, "start", "--hypothesis", "nope")
    assert out.exit_code == REFUSED
    assert "unknown_hypothesis" in out.output


def test_paper_run_with_no_window_exits_no_window_with_no_broker_call(
    clock: _Clock, factory: _Factory
) -> None:
    out = _paper(clock, factory, "run")
    assert out.exit_code == 1  # the runbook's table: `no_window` exits CRASH_EXIT_CODE
    assert "paper run: no_window" in out.output
    assert factory.fake.calls == ()
    assert _count("paper_runs") == 0


@pytest.mark.parametrize(
    "args",
    [
        ("stop", "--reason", LONG_REASON),
        ("kill", "--reason", LONG_REASON),
        ("resume", "--reason", LONG_REASON),
        ("abandon", "--reason", LONG_REASON),
        ("reconcile",),
        ("override", "--kind", "engage_kill_switch", "--reason", LONG_REASON),
    ],
)
def test_every_window_command_with_no_window_exits_no_window(
    args: tuple[str, ...], clock: _Clock, factory: _Factory
) -> None:
    out = _paper(clock, factory, *args)
    assert out.exit_code == NO_WINDOW, out.output
    assert "no_window" in out.output
    assert factory.fake.calls == ()


@pytest.mark.parametrize("command", ["report", "check"])
def test_report_and_check_with_no_window_fail(
    command: str, clock: _Clock, factory: _Factory
) -> None:
    out = _paper(clock, factory, command)
    assert out.exit_code == 1
    assert "no paper window" in out.output


def test_paper_status_with_no_window_exits_zero(clock: _Clock, factory: _Factory) -> None:
    out = _paper(clock, factory, "status")
    assert out.exit_code == 0
    assert "no paper window yet" in out.output


@pytest.mark.usefixtures("started")
def test_paper_run_reads_invoked_by_on_a_non_session_day(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    monkeypatch.setenv("TRADEPARTNER_INVOKED_BY", "scheduler")
    clock.now = SATURDAY
    out = _paper(clock, factory, "run")
    assert out.exit_code == 0
    assert "paper run: no_session" in out.output
    with duckdb.connect(_settings().store.path, read_only=True) as conn:
        assert conn.execute("SELECT invoked_by FROM paper_runs").fetchall() == [("scheduler",)]


@pytest.mark.usefixtures("started")
def test_paper_run_builds_the_wrapper_and_the_broker_on_one_clock(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    """The composition-level identity test (ADR 0007 point 5)."""
    wrapper_clocks: list[Any] = []

    class Recorder:
        def __init__(self, broker: Any, wrapper_clock: Any, *_args: Any, **_kw: Any) -> None:
            wrapper_clocks.append(wrapper_clock)
            raise RuntimeError("stop after the composition")

    monkeypatch.setattr(paper_run, "RiskGatedBroker", Recorder)
    factory.clocks.clear()
    out = _paper(clock, factory, "run")
    assert out.exit_code == 1
    assert "stop after the composition" in out.output
    assert factory.clocks == [clock]
    assert wrapper_clocks == [clock]
    assert factory.clocks[0] is wrapper_clocks[0] is clock


@pytest.mark.usefixtures("started")
def test_override_refuses_a_reason_under_the_frozen_minimum(
    clock: _Clock, factory: _Factory
) -> None:
    short = _paper(clock, factory, "override", "--kind", "engage_kill_switch", "--reason", "short")
    assert short.exit_code == REFUSED
    assert "refused: reason" in short.output
    assert _count("overrides") == 0
    ok = _paper(clock, factory, "override", "--kind", "engage_kill_switch", "--reason", LONG_REASON)
    assert ok.exit_code == 0, ok.output
    assert "override 1 written" in ok.output


@pytest.mark.usefixtures("started")
def test_kill_then_stop_is_refused_and_status_shows_the_switch(
    clock: _Clock, factory: _Factory
) -> None:
    assert _paper(clock, factory, "kill", "--reason", LONG_REASON).exit_code == 0
    stop = _paper(clock, factory, "stop", "--reason", LONG_REASON)
    assert stop.exit_code == REFUSED
    assert "refused: kill_switch" in stop.output
    status = _paper(clock, factory, "status")
    assert status.exit_code == 0
    assert "kill switch engaged" in status.output


@pytest.mark.usefixtures("started")
def test_stop_reconcile_resume_abandon_and_check_on_an_open_window(
    clock: _Clock, factory: _Factory
) -> None:
    reconcile = _paper(clock, factory, "reconcile")
    assert reconcile.exit_code == 0, reconcile.output
    assert "paper reconcile: ok" in reconcile.output
    resume = _paper(clock, factory, "resume", "--reason", LONG_REASON)
    assert resume.exit_code == 0, resume.output
    assert "paper resume: not_engaged" in resume.output
    check = _paper(clock, factory, "check")
    assert check.exit_code == 1  # nothing executed yet
    assert "FAIL rebalance_count" in check.output
    stop = _paper(clock, factory, "stop", "--reason", LONG_REASON)
    assert stop.exit_code == 0, stop.output
    assert "paper stop: requested" in stop.output
    abandon = _paper(clock, factory, "abandon", "--reason", LONG_REASON)
    assert abandon.exit_code == 0, abandon.output
    assert "paper abandon: abandoned" in abandon.output
    assert _paper(clock, factory, "run").exit_code == 1  # no window: `no_window`


def test_a_broker_that_cannot_be_built_fails_scrubbed(clock: _Clock) -> None:
    def broken(_settings: Settings, _clock: Any) -> FakeBroker:
        raise RuntimeError(f"no paper client for key {ALPACA_KEY} / {ALPACA_SECRET}")

    for args in (("run",), ("reconcile",), ("stop", "--reason", LONG_REASON)):
        result = CliRunner().invoke(cli.make_app(clock=clock, broker=broken), ["paper", *args])
        assert result.exit_code == 1, (args, result.output)
        assert "RuntimeError" in result.output
        assert scrub_text(result.output, secrets=[ALPACA_KEY, ALPACA_SECRET])[1] == 0


@pytest.mark.usefixtures("started")
def test_paper_kill_exits_write_failed_when_its_row_cannot_be_written(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    monkeypatch.setattr(switch, "engage", lambda *_a, **_k: switch.WriteFailed("store gone"))
    out = _paper(clock, factory, "kill", "--reason", LONG_REASON)
    assert out.exit_code == WRITE_FAILED_EXIT_CODE == 3
    assert "NOT engaged" in out.output


def test_paper_run_passes_the_halt_paths_write_failed_exit_through(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    def halted(*_args: Any, **_kwargs: Any) -> None:
        raise SystemExit(WRITE_FAILED_EXIT_CODE)

    monkeypatch.setattr(paper_run, "tracking_run", halted)
    assert _paper(clock, factory, "run").exit_code == WRITE_FAILED_EXIT_CODE


@pytest.mark.usefixtures("started")
def test_commands_under_a_held_run_lock_exit_locked(clock: _Clock, factory: _Factory) -> None:
    before = factory.fake.calls  # `paper start`'s reads
    with run_lock(_settings()):
        stop = _paper(clock, factory, "stop", "--reason", LONG_REASON)
        reconcile = _paper(clock, factory, "reconcile")
        run = _paper(clock, factory, "run")
    assert stop.exit_code == reconcile.exit_code == cli.PAPER_REFUSAL_EXIT["locked"]
    assert "refused: locked" in stop.output
    assert run.exit_code == 1  # the runbook's table: `locked` exits CRASH_EXIT_CODE
    assert "paper run: locked" in run.output
    assert factory.fake.calls == before


@pytest.mark.parametrize(("flags", "accepted"), [((), False), (("--accept-rejections",), True)])
def test_resume_passes_the_owners_flags_on_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    clock: _Clock,
    factory: _Factory,
    flags: tuple[str, ...],
    accepted: bool,
) -> None:
    seen: list[tuple[Any, ...]] = []

    def recording(*args: Any, **kwargs: Any) -> Any:
        seen.append((args[5], kwargs))
        return paper_resume.ResumeOutcome(paper_resume.RELEASED, 1)

    monkeypatch.setattr(paper_resume, "resume", recording)
    out = _paper(clock, factory, "resume", "--reason", LONG_REASON, *flags)
    assert out.exit_code == 0, out.output
    assert seen == [(False, {"accept_rejections": accepted})]


def test_accept_rejections_is_a_plain_off_by_default_flag() -> None:
    group: Any = typer.main.get_command(cli.make_app())
    resume_command = group.commands["paper"].commands["resume"]
    (param,) = [p for p in resume_command.params if "--accept-rejections" in p.opts]
    assert param.opts == ["--accept-rejections"]
    assert param.secondary_opts == []
    assert param.default is False
    assert param.envvar is None
    assert param.callback is None
    assert param.allow_from_autoenv is False


def test_accept_rejections_ignores_an_auto_envvar_prefix(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    seen: list[Any] = []

    def recording(*_args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return paper_resume.ResumeOutcome(paper_resume.RELEASED, 1)

    monkeypatch.setattr(paper_resume, "resume", recording)
    monkeypatch.setenv("TP_PAPER_RESUME_ACCEPT_REJECTIONS_FLAG", "1")
    app = cli.make_app(clock=clock, broker=factory)
    result = CliRunner().invoke(
        app, ["paper", "resume", "--reason", LONG_REASON], auto_envvar_prefix="TP"
    )
    assert result.exit_code == 0, result.output
    assert seen == [{"accept_rejections": False}]


# --- `paper settle` (plan T84c; spec req 17, #571) ---------------------------------------
#
# The orders are journaled on window 1 (opened by `paper start`) the way a run
# writes them, the fake submits the acknowledged ones, and `paper kill` engages
# the switch the settlement needs.

#: The broker reads `paper settle` may make; never `submit` or `cancel`.
SETTLE_READS = {"account", "get_order", "open_orders", "fills", "positions"}
PLACED_AT = SESSION_CLOCK + timedelta(minutes=1)
SETTLE_CLOCK = SESSION_CLOCK + timedelta(minutes=5)


def _place(fake: FakeBroker, coid: str, *, acknowledged: bool = True) -> None:
    """A run, its decision, the order and its `pending` event on window 1;
    when `acknowledged`, the fake's submit (it stays accepted) and the
    `accepted` event with the broker's id."""
    with open_for_write(_settings()) as conn:
        run_id = journal.append(
            conn,
            PaperRunRow(
                window_id=1,
                session=PLACED_AT.date(),
                kind="rebalance",
                started_at=PLACED_AT,
                invoked_by="scheduler",
                code_version="test",
                known_at=PLACED_AT,
                ingested_at=PLACED_AT,
            ),
        )
        assert run_id is not None
        journal.append(
            conn,
            PaperRunResultRow(
                run_id=run_id,
                finished_at=PLACED_AT,
                status="ok",
                clock_fault=False,
                known_at=PLACED_AT,
                ingested_at=PLACED_AT,
            ),
        )
        decision_id = journal.append(
            conn,
            DecisionRow(
                run_id=run_id,
                rebalance_session=HOLDOUT_END,
                security_id="SEC_SPY",
                side="buy",
                planned_quantity=10.0,
                whole_share=False,
                decision="trade",
                reason=None,
                known_at=PLACED_AT,
                ingested_at=PLACED_AT,
            ),
        )
        assert decision_id is not None
        journal.append(
            conn,
            OrderRow(
                client_order_id=coid,
                decision_id=decision_id,
                run_id=run_id,
                session=PLACED_AT.date(),
                attempt=1,
                phase="buy",
                security_id="SEC_SPY",
                symbol="SPY",
                side="buy",
                quantity=10.0,
                sells_in_flight_at_submit=False,
                known_at=PLACED_AT,
                ingested_at=PLACED_AT,
            ),
        )
        journal.append(
            conn,
            OrderEventRow(
                client_order_id=coid, status="pending", known_at=PLACED_AT, ingested_at=PLACED_AT
            ),
        )
        if acknowledged:
            placed = fake.submit(OrderRequest(coid, "SPY", Side.BUY, quantity=10.0))
            journal.append(
                conn,
                OrderEventRow(
                    client_order_id=coid,
                    status="accepted",
                    broker_order_id=placed.broker_order_id,
                    known_at=PLACED_AT,
                    ingested_at=PLACED_AT,
                ),
            )


def _engage(clock: _Clock, factory: _Factory) -> None:
    clock.now = SETTLE_CLOCK
    assert _paper(clock, factory, "kill", "--reason", LONG_REASON).exit_code == 0


def _settle(clock: _Clock, factory: _Factory, *args: str) -> Out:
    clock.step = timedelta(microseconds=1)
    return _paper(clock, factory, "settle", *args)


@pytest.mark.parametrize(
    ("args", "says"),
    [
        (("--reason", LONG_REASON), "Missing option '--order'"),
        (("--order", "tp-a"), "Missing option '--reason'"),
        (
            ("--order", "tp-a", "--order", "tp-b", "--reason", LONG_REASON),
            "--order is given exactly once",
        ),
        (("--order", "tp-a", "--reason", "   "), "--reason must be non-blank"),
        (("--order", "  ", "--reason", LONG_REASON), "--order must be non-blank"),
    ],
)
def test_settle_without_one_order_and_a_reason_is_a_usage_error_before_any_broker(
    args: tuple[str, ...], says: str, clock: _Clock, factory: _Factory
) -> None:
    out = _settle(clock, factory, *args)
    assert out.exit_code == cli.USAGE_ERROR, out.output
    assert says in re.sub(r"\x1b\[[0-9;]*m", "", out.output)  # rich colours it in CI
    assert factory.clocks == []


def test_settle_with_no_window_exits_no_window(clock: _Clock, factory: _Factory) -> None:
    out = _settle(clock, factory, "--order", "tp-a", "--reason", LONG_REASON)
    assert out.exit_code == NO_WINDOW, out.output
    assert "refused: no_window" in out.output
    assert factory.fake.calls == ()


def _vanished(fake: FakeBroker) -> None:
    _place(fake, "tp-a")
    fake.apply("tp-a", Vanish())


def _expired_with_position(fake: FakeBroker) -> None:
    _place(fake, "tp-a")
    fake.apply("tp-a", Expire())
    fake.apply_account(SetPosition("SPY", 0.5))


def _unjournaled_fill(fake: FakeBroker) -> None:
    _place(fake, "tp-a")
    fake.apply("tp-a", PartialFill(4, 100.0))
    fake.apply("tp-a", Expire())
    fake.apply_account(SetPosition("SPY", None))


def _other_open_order(fake: FakeBroker) -> None:
    _place(fake, "tp-a")
    _place(fake, "tp-b")
    fake.apply("tp-a", Expire())


def _terminal(fake: FakeBroker) -> None:
    _place(fake, "tp-a")
    with open_for_write(_settings()) as conn:
        journal.append(
            conn,
            OrderEventRow(
                client_order_id="tp-a",
                status="expired",
                known_at=PLACED_AT,
                ingested_at=PLACED_AT,
            ),
        )


#: Each refusal of `window.settle_order`, the state that makes it, whether the
#: switch is engaged, and the reason; every one exits `refused`.
SETTLE_REFUSALS: list[tuple[str, Callable[[FakeBroker], None], bool, str, str]] = [
    ("unknown_order", lambda fake: None, True, LONG_REASON, "tp-none"),
    ("already_terminal", _terminal, True, LONG_REASON, "tp-a"),
    (
        "pending_order",
        lambda fake: _place(fake, "tp-a", acknowledged=False),
        True,
        LONG_REASON,
        "tp-a",
    ),
    ("reason", lambda fake: _place(fake, "tp-a"), True, "short", "tp-a"),
    ("not_engaged", lambda fake: _place(fake, "tp-a"), False, LONG_REASON, "tp-a"),
    ("broker_open", lambda fake: _place(fake, "tp-a"), True, LONG_REASON, "tp-a"),
    ("unjournaled_fill", _unjournaled_fill, True, LONG_REASON, "tp-a"),
    ("unexplained_position", _expired_with_position, True, LONG_REASON, "tp-a"),
    ("other_open_order", _other_open_order, True, LONG_REASON, "tp-a"),
]


@pytest.mark.usefixtures("started")
@pytest.mark.parametrize(
    ("code", "arrange", "engaged", "reason", "coid"),
    SETTLE_REFUSALS,
    ids=[r[0] for r in SETTLE_REFUSALS],
)
def test_each_settle_refusal_exits_refused_and_writes_nothing(
    clock: _Clock,
    factory: _Factory,
    code: str,
    arrange: Callable[[FakeBroker], None],
    engaged: bool,
    reason: str,
    coid: str,
) -> None:
    arrange(factory.fake)
    if engaged:
        _engage(clock, factory)
    else:
        clock.now = SETTLE_CLOCK
    mark = len(factory.fake.calls)
    out = _settle(clock, factory, "--order", coid, "--reason", reason)
    assert out.exit_code == REFUSED, out.output
    assert f"refused: {code}:" in out.output
    assert _count("overrides") == 0
    assert {c.method for c in factory.fake.calls[mark:]} <= SETTLE_READS


@pytest.mark.usefixtures("started")
def test_settle_on_another_account_exits_refused(clock: _Clock, factory: _Factory) -> None:
    _place(factory.fake, "tp-a")
    factory.fake.apply("tp-a", Vanish())
    _engage(clock, factory)
    factory.fake = FakeBroker(
        clock=clock, price_of=lambda _s: 100.0, auto_fill=False, account_id="PA2"
    )
    out = _settle(clock, factory, "--order", "tp-a", "--reason", LONG_REASON)
    assert out.exit_code == REFUSED, out.output
    assert "refused: account_mismatch:" in out.output
    assert _count("overrides") == 0


@pytest.mark.usefixtures("started")
def test_settle_under_a_held_run_lock_exits_locked(clock: _Clock, factory: _Factory) -> None:
    _vanished(factory.fake)
    _engage(clock, factory)
    before = factory.fake.calls
    with run_lock(_settings()):
        out = _settle(clock, factory, "--order", "tp-a", "--reason", LONG_REASON)
    assert out.exit_code == cli.PAPER_REFUSAL_EXIT["locked"], out.output
    assert "refused: locked" in out.output
    assert factory.fake.calls == before
    assert _count("overrides") == 0


@pytest.mark.usefixtures("started")
def test_settle_journals_an_order_the_broker_forgot(clock: _Clock, factory: _Factory) -> None:
    _vanished(factory.fake)
    _engage(clock, factory)
    factory.clocks.clear()
    mark = len(factory.fake.calls)
    out = _settle(clock, factory, "--order", "tp-a", "--reason", LONG_REASON)
    assert out.exit_code == 0, out.output
    assert "paper settle: tp-a settled (override 1" in out.output
    assert "reset" in out.output
    assert {c.method for c in factory.fake.calls[mark:]} <= SETTLE_READS
    assert factory.clocks == [clock]
    with duckdb.connect(_settings().store.path, read_only=True) as conn:
        assert conn.execute("SELECT kind, client_order_id FROM overrides").fetchall() == [
            ("settle_order", "tp-a")
        ]
        assert conn.execute(
            "SELECT status, reason FROM order_events WHERE client_order_id = 'tp-a' "
            "AND reason = 'owner_settled_unknown'"
        ).fetchall() == [("cancelled", "owner_settled_unknown")]


@pytest.mark.usefixtures("started")
def test_settle_journals_an_order_the_broker_reports_finished(
    clock: _Clock, factory: _Factory
) -> None:
    """Not the reset case: the broker knows the order expired, holds nothing."""
    _place(factory.fake, "tp-a")
    factory.fake.apply("tp-a", Expire())
    _engage(clock, factory)
    mark = len(factory.fake.calls)
    out = _settle(clock, factory, "--order", "tp-a", "--reason", LONG_REASON)
    assert out.exit_code == 0, out.output
    assert "paper settle: tp-a settled (override 1" in out.output
    assert "reset" not in out.output
    assert {c.method for c in factory.fake.calls[mark:]} <= SETTLE_READS
    assert _count("overrides") == 1


@pytest.mark.usefixtures("started")
def test_settle_hands_the_writer_the_built_broker_and_the_one_clock(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    seen: list[tuple[Any, ...]] = []

    def recording(*args: Any) -> window.SettleResult:
        seen.append(args)
        return window.SettleResult(7, args[4], SETTLE_CLOCK, reset=False)

    monkeypatch.setattr(window, "settle_order", recording)
    factory.clocks.clear()
    out = _settle(clock, factory, "--order", "tp-a", "--reason", LONG_REASON)
    assert out.exit_code == 0, out.output
    ((settings, _connect, broker, settle_clock, coid, reason),) = seen
    assert broker is factory.fake
    assert settle_clock is clock is factory.clocks[0]
    assert (coid, reason) == ("tp-a", LONG_REASON)
    assert settings.store.path == _settings().store.path


def test_settle_order_is_never_read_from_an_auto_envvar() -> None:
    group: Any = typer.main.get_command(cli.make_app())
    settle = group.commands["paper"].commands["settle"]
    (param,) = [p for p in settle.params if "--order" in p.opts]
    assert param.envvar is None
    assert param.allow_from_autoenv is False


# --- `--book` on status, report and check (ADR 0017 B.7; plan T156) ---------------

#: `paper status` and `paper check` on the `started` window exactly as they printed
#: before books existed (captured on main before T156): with one book, `main`, and
#: `month_end`, H1's output must not change by a byte.
STATUS_MAIN = (
    "window 1 (first rebalance 2026-10-30)\n"
    "as of 2026-10-01 14:00:00+00:00; last updated n/a; STALE: no run for S-1\n"
    "positions 0 (value 0.00); open orders 0; targets 0\n"
    "kill switch released (causes: -)\n"
    "reconciliation n/a\n"
    "alerts (0):\n"
)
CHECK_MAIN = (
    "FAIL rebalance_count: 0 scheduler-executed rebalance session(s) [], need >= 6 "
    "(query: distinct rebalance_events.rebalance_session with status='executed' whose "
    "writing paper_runs row has invoked_by='scheduler')\n"
    "FAIL tracking: no paper_reports row yet (query: req 10 tracking check "
    "(report.compare_months) over the window's latest paper_reports row's trial)\n"
    "PASS chain: no incomplete chain once due (query: every order's chain (order -> "
    "terminal event -> outcome) once its outcome is due (spec req 8), and no live fill "
    "journaled after its order's terminal event (req 17))\n"
    "PASS override_reason: every override reason meets the frozen minimum (query: "
    "overrides.reason, trimmed, against the frozen paper.min_override_reason_chars)\n"
)


def _open_book_b() -> int:
    """A second book's window beside `started`'s, on its own account."""
    with open_for_write(_settings()) as conn:
        main = journal.latest_window(conn, "main")
        assert main is not None
        window_id = journal.append(
            conn, replace(main, window_id=None, account_id="PB1", book_id="b")
        )
    assert window_id is not None
    return window_id


@pytest.mark.usefixtures("started")
@pytest.mark.parametrize("flags", [(), ("--book", "main")])
def test_status_and_check_for_main_are_byte_identical(
    flags: tuple[str, ...], clock: _Clock, factory: _Factory
) -> None:
    _open_book_b()  # a newer window of another book changes nothing for `main`
    status = _paper(clock, factory, "status", *flags)
    check = _paper(clock, factory, "check", *flags)
    assert (status.exit_code, status.output) == (0, STATUS_MAIN)
    assert (check.exit_code, check.output) == (1, CHECK_MAIN)


@pytest.mark.usefixtures("started")
def test_status_and_check_for_another_book_read_that_books_window(
    clock: _Clock, factory: _Factory
) -> None:
    missing = _paper(clock, factory, "status", "--book", "b")
    assert (missing.exit_code, missing.output) == (0, "paper status: no paper window yet\n")
    no_window = _paper(clock, factory, "check", "--book", "b")
    assert no_window.exit_code == 1
    assert "no paper window" in no_window.output
    b_window = _open_book_b()
    status = _paper(clock, factory, "status", "--book", "b")
    assert status.exit_code == 0
    assert status.output.startswith(f"window {b_window} (first rebalance 2026-10-30)\n")


@pytest.mark.usefixtures("started")
def test_status_all_prints_one_summary_line_per_book(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    monkeypatch.setattr(ops, "utc_now", lambda: SESSION_CLOCK)
    b_window = _open_book_b()
    out = _paper(clock, factory, "status", "--all")
    assert out.exit_code == 0, out.output
    assert out.output == (
        f"book b: window {b_window} open; positions 0; open orders 0; kill switch released; "
        "last run n/a; next rebalance 2026-10-30\n"
        "book main: window 1 open; positions 0; open orders 0; kill switch released; "
        "last run n/a; next rebalance 2026-10-30\n"
    )


def test_status_all_with_no_window(clock: _Clock, factory: _Factory) -> None:
    out = _paper(clock, factory, "status", "--all")
    assert (out.exit_code, out.output) == (0, "paper status: no paper window yet\n")


@pytest.mark.parametrize(
    "args",
    [
        ("status", "--book", "b-1"),
        ("check", "--book", ""),
        ("report", "--book", "../x"),
        ("status", "--all", "--book", "main"),
    ],
)
def test_a_bad_book_or_all_with_book_is_a_usage_error(
    args: tuple[str, ...], clock: _Clock, factory: _Factory
) -> None:
    out = _paper(clock, factory, *args)
    assert out.exit_code == cli.USAGE_ERROR, out.output
    assert factory.clocks == []


def test_report_passes_the_book_and_prints_the_same_lines(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    """`paper report --book b` hands `b` to `report.report` (no flag hands None, so
    `paper.book_id`); the printed lines are the pre-T156 format, unchanged."""
    t0, t1 = date(2026, 10, 30), date(2026, 11, 30)
    canned = paper_report.Report(
        comparison=paper_report.PeriodComparison(
            tracking_rule="raw",
            tracking_k=2.0,
            periods=(
                paper_report.PeriodRow(
                    rebalance_session=t0,
                    next_session=t1,
                    raw=0.001,
                    dividend_term=0.0,
                    fill_timing_term=-0.0005,
                    residual=0.0005,
                    residue_term=0.0,
                    modelled_cost=0.002,
                    missed=False,
                    override=True,
                    skip_names=(),
                    excluded=False,
                    passed=True,
                ),
            ),
            passed=True,
            failing_period=None,
        ),
        targets=paper_report.TargetComparison(rows=()),
        paper_report=journal.PaperReportRow(
            window_id=1,
            trial_id=7,
            through_session=t1,
            run_at=SESSION_CLOCK,
            known_at=SESSION_CLOCK,
            ingested_at=SESSION_CLOCK,
        ),
    )
    books: list[str | None] = []

    def fake_report(_settings: Settings, _connect: Any, book_id: str | None = None) -> Any:
        books.append(book_id)
        return canned

    monkeypatch.setattr(paper_report, "report", fake_report)
    expected = (
        "paper report: trial 7 through 2026-11-30; rule raw, k 2; passed\n"
        "  2026-10-30: raw 0.001000 dividend 0.000000 fill -0.000500 residual 0.000500 "
        "residue 0.000000 cost 0.002000 override pass\n"
    )
    plain = _paper(clock, factory, "report")
    for_b = _paper(clock, factory, "report", "--book", "b")
    assert (plain.exit_code, plain.output) == (0, expected)
    assert (for_b.exit_code, for_b.output) == (0, expected)
    assert books == [None, "b"]


# --- `paper shakedown` (ADR 0017 part E; plan T157b) --------------------------------


def _shakedown_line(name: str, passed: bool) -> paper_shakedown.ShakedownLine:
    return paper_shakedown.ShakedownLine(
        name=name, passed=passed, rows="r", query="q", thresholds="t", detail="d"
    )


def test_paper_shakedown_with_no_span_row_fails(clock: _Clock, factory: _Factory) -> None:
    out = _paper(clock, factory, "shakedown")
    assert out.exit_code == 1
    assert "no shakedown_span decision" in out.output
    assert factory.fake.calls == ()


def test_paper_shakedown_prints_seven_lines_and_fails_on_an_empty_journal(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    class _Healthy:
        ok = True
        failures: tuple[str, ...] = ()

    monkeypatch.setattr(paper_shakedown, "health_report", lambda conn, t, s: _Healthy())
    with open_for_write(_settings()) as conn:
        registry.record_decision(
            conn,
            kind=registry.SHAKEDOWN_SPAN_KIND,
            reason="H1 goes live first",
            values={"sessions": 10, "order_sessions": 5},
        )
    clock.now = datetime(2026, 12, 1, 14, tzinfo=UTC)
    out = _paper(clock, factory, "shakedown")
    assert out.exit_code == 1
    lines = out.output.strip().splitlines()
    assert len(lines) == 7
    assert lines[0].startswith("FAIL E.1 sessions:")
    assert "sessions >= 10, order sessions >= 5" in lines[0]
    assert [line.split(":")[0].split(" ", 1)[1] for line in lines] == [
        "E.1 sessions",
        "E.2 reconciliation",
        "E.3 orders",
        "E.4 kill-switch drill",
        "E.5 journal",
        "E.6 alerts",
        "E.7 data",
    ]
    assert factory.fake.calls == ()


def test_paper_shakedown_exits_zero_when_every_line_passes_on_a_read_only_store(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    seen: list[datetime] = []

    def fake(
        conn: duckdb.DuckDBPyConnection, s: Settings, *, now: datetime
    ) -> paper_shakedown.Shakedown:
        seen.append(now)
        with pytest.raises(duckdb.Error):
            conn.execute("CREATE TABLE probe (x INTEGER)")
        lines = tuple(_shakedown_line(f"E.{i}", True) for i in range(1, 8))
        return paper_shakedown.Shakedown(span=None, lines=lines)  # type: ignore[arg-type]

    monkeypatch.setattr(paper_shakedown, "shakedown", fake)
    out = _paper(clock, factory, "shakedown")
    assert out.exit_code == 0, out.output
    assert out.output.count("PASS E.") == 7
    assert seen == [SESSION_CLOCK]
