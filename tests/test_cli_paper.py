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
from tradepartner.cli_record import scrub_text
from tradepartner.config import Settings
from tradepartner.execution.lots_reconcile import BrokerLotRow
from tradepartner.store import journal
from tradepartner.store.db import open_for_write
from tradepartner.store.journal import (
    DisposalRow,
    LotRow,
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


def test_a_version_16_store_is_refused_as_no_window(export_file: Path, live: Path) -> None:
    """#1261: `require_journal` raises `SchemaVersionError` on a version-16 store;
    `_read_ledger_set` catches it too, so the command refuses `no_window`."""
    with duckdb.connect(str(live)) as conn:
        conn.execute("UPDATE schema_version SET version = 16")
        conn.execute("ALTER TABLE orders DROP COLUMN book_id")

    out = _reconcile(export_file, rows=AGREEING)

    assert out.exit_code == EXIT["no_window"], out.output


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
