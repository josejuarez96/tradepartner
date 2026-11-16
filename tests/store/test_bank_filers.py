"""Tests for tradepartner.store.bank_filers (#3078, #3053 item 9).

The reviewed list of exchange-listed banks that file with their banking
regulator instead of EDGAR, its loader's refusals, the read-only census on a
synthetic store, the Alpaca-assets discovery, and the source scan that keeps
the list out of every universe, backtest and paper read (it is a census input
only until an ADR 0022 amendment says how to key such a bank).
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.store import schema
from tradepartner.store.bank_filers import (
    COLUMNS,
    BankFilersError,
    candidates,
    census,
    held_tickers,
    load_bank_filers,
    parse_bank_filers,
)
from tradepartner.store.db import configure_connection, insert_row

SRC = Path(__file__).resolve().parents[2] / "src" / "tradepartner"
INGESTED_AT = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
KNOWN = datetime(2020, 1, 2, 21, 0, tzinfo=UTC)

HEADER = ",".join(COLUMNS)
FRC = (
    "FRC,First Republic Bank,NYSE,FDIC,0001132979,2023-04-28,2023-05-01,2023-05-02,"
    "failure,no 10-K on EDGAR,closed by DFPI,NYSE notice"
)
LIVE = "OZK,Bank OZK,NASDAQ,FDIC,,,,,,no 10-K on EDGAR,,"


def _text(*rows: str) -> str:
    return "\n".join((HEADER, *rows)) + "\n"


# -------------------------------------------------------------- the packaged list


def test_packaged_list_loads_and_names_the_two_2023_failures() -> None:
    filers = {f.ticker: f for f in load_bank_filers()}
    assert {"FRC", "SBNY"} <= set(filers)
    frc, sbny = filers["FRC"], filers["SBNY"]
    assert frc.edgar_cik == "0001132979"
    assert frc.last_exchange_session == date(2023, 4, 28)
    assert frc.closed_on == date(2023, 5, 1)
    assert sbny.edgar_cik == "0001288784"
    assert sbny.last_exchange_session == date(2023, 3, 10)
    assert sbny.closed_on == date(2023, 3, 12)
    for filer in filers.values():
        for evidence in (filer.regulator_evidence, filer.closing_source, filer.exit_source):
            if filer.exit_kind is not None:
                assert "https://" in evidence, (filer.ticker, evidence)


def test_live_bank_row_has_no_exit() -> None:
    (row,) = parse_bank_filers(_text(LIVE))
    assert row.edgar_cik is None
    assert row.exit_kind is None
    assert row.last_exchange_session is None and row.closed_on is None


# -------------------------------------------------------------- loader refusals


@pytest.mark.parametrize(
    ("row", "why"),
    [
        (FRC.replace("FRC,First", ",First", 1), "empty ticker"),
        (FRC.replace(",FDIC,", ",SEC,", 1), "regulator"),
        (FRC.replace("0001132979", "1132979", 1), "edgar_cik"),
        (FRC.replace("2023-04-28", "2023-04-29", 1), "not an XNYS session"),
        (FRC.replace("2023-05-01,2023-05-02", "2023-04-28,2023-05-02", 1), "after"),
        (FRC.replace("2023-05-01,2023-05-02", "2023-05-01,2023-04-30", 1), "before"),
        (FRC.replace(",failure,", ",merger,", 1), "exit_kind"),
        (FRC.replace(",failure,", ",,", 1), "all or none"),
        (FRC.replace("no 10-K on EDGAR", "", 1), "empty regulator_evidence"),
        (FRC.replace("NYSE notice", "", 1), "empty exit_source"),
    ],
)
def test_malformed_rows_are_refused_with_the_line(row: str, why: str) -> None:
    with pytest.raises(BankFilersError, match=rf"line 2: .*{why}"):
        parse_bank_filers(_text(row))


def test_wrong_header_and_duplicate_ticker_are_refused() -> None:
    with pytest.raises(BankFilersError, match="header"):
        parse_bank_filers("ticker,name\nFRC,First Republic Bank\n")
    with pytest.raises(BankFilersError, match="line 3: ticker FRC listed twice"):
        parse_bank_filers(_text(FRC, FRC))


# -------------------------------------------------------------- the census


def _store() -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    return conn


def _common() -> dict[str, Any]:
    return {"known_at": KNOWN, "ingested_at": INGESTED_AT, "source": "edgar"}


def _security(conn: duckdb.DuckDBPyConnection, sid: str, cik: str, name: str) -> None:
    insert_row(
        conn,
        "securities",
        {"security_id": sid, "cik": cik, "name": name, "provenance": "filing", **_common()},
    )


def _listing(conn: duckdb.DuckDBPyConnection, sid: str, ticker: str) -> None:
    insert_row(
        conn,
        "listings",
        {
            "security_id": sid,
            "ticker": ticker,
            "exchange": "NYSE",
            "class_title": "Common Stock",
            "valid_from": date(2020, 1, 2),
            "provenance": "filing",
            **_common(),
        },
    )


def _bar(conn: duckdb.DuckDBPyConnection, sid: str, session: date) -> None:
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": sid,
            "session": session,
            "open": 10.0,
            "high": 10.0,
            "low": 10.0,
            "close": 10.0,
            "volume": 100,
            "known_at": datetime(session.year, session.month, session.day, 21, 0, tzinfo=UTC),
            "ingested_at": INGESTED_AT,
            "source": "alpaca",
            "provenance": "bar",
        },
    )


def _delisting(conn: duckdb.DuckDBPyConnection, sid: str) -> None:
    filed = datetime(2023, 5, 2, 14, 0, tzinfo=UTC)
    insert_row(
        conn,
        "delistings",
        {
            "security_id": sid,
            "form": "25-NSE",
            "class_title": "Common Stock",
            "exchange": "NYSE",
            "filed_at": filed,
            "effective_on": date(2023, 5, 12),
            "known_at": filed,
            "ingested_at": INGESTED_AT,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def test_census_absent_bank_and_a_reused_ticker_held_by_another_company() -> None:
    conn = _store()
    # A later company holds the ticker; it is not First Republic.
    _security(conn, "0009999999", "0009999999", "FRC OTHER CORP")
    _listing(conn, "0009999999", "FRC")
    (row,) = census(conn, parse_bank_filers(_text(FRC)))
    assert row.status == "absent"
    assert row.master_ids == ()
    assert [h.security_id for h in row.other_holders] == ["0009999999"]
    assert row.bars == 0 and row.last_bar is None


def test_census_status_moves_no_bars_then_no_exit_then_covered() -> None:
    conn = _store()
    filers = parse_bank_filers(_text(FRC))
    _security(conn, "0001132979", "0001132979", "FIRST REPUBLIC BANK")
    _listing(conn, "0001132979", "FRC")
    (row,) = census(conn, filers)
    assert row.status == "no_bars"
    assert row.master_ids == ("0001132979",)
    assert row.other_holders == ()

    _bar(conn, "0001132979", date(2023, 4, 27))
    _bar(conn, "0001132979", date(2023, 4, 28))
    (row,) = census(conn, filers)
    assert row.status == "no_exit"
    assert (row.bars, row.last_bar) == (2, date(2023, 4, 28))

    _delisting(conn, "0001132979")
    (row,) = census(conn, filers)
    assert row.status == "covered"
    assert row.delistings == 1


def test_census_live_bank_needs_no_exit() -> None:
    conn = _store()
    _security(conn, "0001569650", "0001569650", "BANK OZK")
    _bar(conn, "0001569650", date(2023, 4, 28))
    filers = parse_bank_filers(_text(LIVE.replace(",,,,,,", ",0001569650,,,,,", 1)))
    (row,) = census(conn, filers)
    assert row.status == "covered"


def test_census_skips_retracted_master_rows() -> None:
    conn = _store()
    _security(conn, "0001132979", "0001132979", "FIRST REPUBLIC BANK")
    conn.execute("UPDATE securities SET retracted = TRUE")
    (row,) = census(conn, parse_bank_filers(_text(FRC)))
    assert row.status == "absent"


def test_census_never_writes(tmp_path: Path) -> None:
    path = tmp_path / "s.duckdb"
    conn = duckdb.connect(str(path))
    configure_connection(conn)
    schema.init_schema(conn)
    conn.close()
    ro = duckdb.connect(str(path), read_only=True)
    try:
        census(ro, load_bank_filers())
        assert held_tickers(ro) == frozenset()
    finally:
        ro.close()


# -------------------------------------------------------------- discovery


def _asset(symbol: str, name: str, **kw: object) -> dict[str, object]:
    return {
        "symbol": symbol,
        "name": name,
        "exchange": kw.get("exchange", "NASDAQ"),
        "class": kw.get("cls", "us_equity"),
        "status": kw.get("status", "active"),
    }


def test_candidates_are_bank_level_names_with_no_listing_row() -> None:
    assets = [
        _asset("OZK", "Bank OZK Common Stock"),
        _asset("HIFS", "Hingham Institution for Savings Common Stock"),
        _asset("CADE", "Cadence Bank Common Stock", exchange="NYSE"),
        _asset("BAC", "Bank of America Corporation Common Stock", exchange="NYSE"),
        _asset("ZION", "Zions Bancorporation N.A. Common Stock"),
        _asset("FFIN", "First Financial Bankshares, Inc. Common Stock"),
        _asset("DB", "Deutsche Bank AG Common Stock", exchange="NYSE"),
        _asset("KBE", "SPDR S&P Bank ETF", exchange="ARCA"),
        _asset("XBNK", "Example Bank", exchange="OTC"),
        _asset("CBNK", "Crypto Bank Token", cls="crypto"),
    ]
    found = candidates(assets, held_tickers=frozenset({"CADE"}))
    assert [c.symbol for c in found] == ["HIFS", "OZK", "ZION"]
    assert found[1].name == "Bank OZK Common Stock"


def test_held_tickers_reads_non_retracted_listing_tickers() -> None:
    conn = _store()
    _listing(conn, "0000000001", "AAA")
    _listing(conn, "0000000002", "BBB")
    conn.execute("UPDATE listings SET retracted = TRUE WHERE ticker = 'BBB'")
    assert held_tickers(conn) == frozenset({"AAA"})


# -------------------------------------------------------------- isolation


def test_no_read_path_imports_the_list() -> None:
    """Census input only: no module outside `bank_filers.py` and the CLI
    imports it or names its file, so every universe, backtest and paper read
    is unchanged until an ADR 0022 amendment says how to key such a bank."""
    allowed = {SRC / "store" / "bank_filers.py", SRC / "cli.py"}
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if "bank_filers" in text:
            offenders.append(str(path.relative_to(SRC)))
            continue
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("bank_filers"):
                offenders.append(str(path.relative_to(SRC)))
    assert offenders == []


# -------------------------------------------------------------- the command


def test_command_prints_census_and_leads_and_leaves_the_store_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import gzip
    import hashlib
    import json

    from typer.testing import CliRunner

    from tradepartner import cli

    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    path = tmp_path / "t.duckdb"
    monkeypatch.setenv("STORE__PATH", str(path))
    conn = duckdb.connect(str(path))
    configure_connection(conn)
    schema.init_schema(conn)
    _security(conn, "0009999999", "0009999999", "FRC OTHER CORP")
    _listing(conn, "0009999999", "FRC")
    conn.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assets = tmp_path / "assets.json.gz"
    assets.write_bytes(
        gzip.compress(
            json.dumps(
                {
                    "endpoint": "paper GET /v2/assets",
                    "assets": [_asset("HIFS", "Hingham Institution for Savings Common Stock")],
                }
            ).encode()
        )
    )
    out = CliRunner().invoke(
        cli.make_app(clock=lambda: INGESTED_AT), ["store", "bank-filers", "--assets", str(assets)]
    )
    assert out.exit_code == 0, out.output
    lines = out.output.splitlines()
    assert any(line.startswith("FRC") and " absent " in line for line in lines)
    assert any(line.startswith("SBNY") and " absent " in line for line in lines)
    assert any("ticker held by 0009999999" in line for line in lines)
    assert "candidates (bank-level names, no listing row): 1" in out.output
    assert any(line.strip().startswith("HIFS") for line in lines)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_command_refuses_a_file_that_is_not_an_assets_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from tradepartner import cli

    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    path = tmp_path / "t.duckdb"
    monkeypatch.setenv("STORE__PATH", str(path))
    conn = duckdb.connect(str(path))
    schema.init_schema(conn)
    conn.close()
    bad = tmp_path / "bad.json"
    bad.write_text('{"nope": 1}')
    out = CliRunner().invoke(cli.make_app(), ["store", "bank-filers", "--assets", str(bad)])
    assert out.exit_code == 1
    assert "not an assets list" in out.output
