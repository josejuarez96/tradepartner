"""The reviewed list of exchange-listed banks that file with their banking
regulator instead of EDGAR, and a read-only census of what the store holds
for them (#3078, #3053 item 9; claim TP-4; data-foundation spec C6).

A bank registered under Exchange Act section 12(i) files its 10-K and 10-Q
with the FDIC, the Federal Reserve or the OCC. The master reads identity from
EDGAR filings and keys every security on its CIK and class (ADR 0022 decision
2), so such a bank has no `securities` row, no listing, no bars and no
delisting. First Republic (FRC, closed 2023-05-01) and Signature (SBNY,
closed 2023-03-12) are outside the universe on every session: their failures
are missing, which can only flatter a long book. EDGAR still holds a CIK for
each, but only for filings other people made about them (Schedules 13G), so
nothing the master reads names a ticker, a class or a Form 25.

This module holds only evidence for a decision, never store input:

- `bank_filers.csv`, one reviewed row per bank: ticker, exchange, regulator,
  the EDGAR subject CIK when one exists, and for a failure the last exchange
  session, the regulator's closing date and the exchange's notice date, each
  with its source URLs;
- `census`, which reports per row what the store holds (master rows under the
  CIK, other companies holding the ticker, bars, delistings);
- `candidates`, which reads an Alpaca assets list for bank-level names whose
  symbol no listing row carries, as leads for the next review.

Nothing outside this module and the CLI imports it (a source-scan test pins
that), so every universe, backtest and paper read is unchanged. Writing master
rows, bars and delistings for these banks waits on an ADR 0022 amendment that
says how to key a security whose identity is not read from EDGAR filings, and
what dated evidence, public at the time, lets a listing row carry a `known_at`
before the sessions it covers. A row stamped at this list's review date would
be free of look-ahead but would still keep every 2023 read without the bank.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any, Final

import duckdb

from tradepartner.calendar import is_session
from tradepartner.store import schema

#: The file's columns, in order.
COLUMNS: Final = (
    "ticker",
    "name",
    "exchange",
    "regulator",
    "edgar_cik",
    "last_exchange_session",
    "closed_on",
    "exit_notice_on",
    "exit_kind",
    "regulator_evidence",
    "closing_source",
    "exit_source",
)
_FILE: Final = "bank_filers.csv"

#: The banking regulators a section 12(i) registrant files with.
REGULATORS: Final = frozenset({"FDIC", "FRB", "OCC"})
#: The exits the list records. Only a regulator's closing so far.
EXIT_KINDS: Final = frozenset({"failure"})
_EXIT_COLUMNS: Final = ("last_exchange_session", "closed_on", "exit_notice_on", "exit_kind")
_CIK: Final = re.compile(r"\d{10}")


class BankFilersError(ValueError):
    """The reviewed list is malformed; the message names the line."""


@dataclass(frozen=True)
class BankFiler:
    """One reviewed bank-level registrant."""

    ticker: str
    name: str
    exchange: str
    regulator: str
    #: The EDGAR CIK filings about the bank are filed under, if any.
    edgar_cik: str | None
    #: The last session the common traded on its exchange (a failure only).
    last_exchange_session: date | None
    #: The regulator's closing date (a failure only; may be a weekend).
    closed_on: date | None
    #: The date of the exchange's halt or delisting notice (a failure only).
    exit_notice_on: date | None
    exit_kind: str | None
    regulator_evidence: str
    closing_source: str
    exit_source: str


def _date(fail_line: str, column: str, value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise BankFilersError(f"{fail_line}: {column} {value!r} is not a date") from None


def _row(line: int, raw: Mapping[str, str]) -> BankFiler:
    where = f"{_FILE} line {line}"

    def fail(why: str) -> BankFilersError:
        return BankFilersError(f"{where}: {why}")

    v = {k: (raw[k] or "").strip() for k in COLUMNS}
    for column in ("ticker", "name", "exchange", "regulator_evidence"):
        if not v[column]:
            raise fail(f"empty {column}")
    if v["regulator"] not in REGULATORS:
        raise fail(f"regulator {v['regulator']!r} is not one of {sorted(REGULATORS)}")
    cik = v["edgar_cik"] or None
    if cik is not None and not _CIK.fullmatch(cik):
        raise fail(f"edgar_cik {cik!r} is not ten digits")
    present = [bool(v[c]) for c in _EXIT_COLUMNS]
    if any(present) and not all(present):
        raise fail(f"{', '.join(_EXIT_COLUMNS)} are all or none")
    last = closed = notice = None
    kind = None
    if all(present):
        kind = v["exit_kind"]
        if kind not in EXIT_KINDS:
            raise fail(f"exit_kind {kind!r} is not one of {sorted(EXIT_KINDS)}")
        last = _date(where, "last_exchange_session", v["last_exchange_session"])
        if not is_session(last):
            raise fail(f"last_exchange_session {last} is not an XNYS session")
        closed = _date(where, "closed_on", v["closed_on"])
        if closed <= last:
            raise fail(f"closed_on {closed} must be after last_exchange_session {last}")
        notice = _date(where, "exit_notice_on", v["exit_notice_on"])
        if notice < closed:
            raise fail(f"exit_notice_on {notice} is before closed_on {closed}")
        for column in ("closing_source", "exit_source"):
            if not v[column]:
                raise fail(f"empty {column}")
    return BankFiler(
        ticker=v["ticker"],
        name=v["name"],
        exchange=v["exchange"],
        regulator=v["regulator"],
        edgar_cik=cik,
        last_exchange_session=last,
        closed_on=closed,
        exit_notice_on=notice,
        exit_kind=kind,
        regulator_evidence=v["regulator_evidence"],
        closing_source=v["closing_source"],
        exit_source=v["exit_source"],
    )


def parse_bank_filers(text: str) -> tuple[BankFiler, ...]:
    """The reviewed rows in the CSV `text`, in file order. Raises
    `BankFilersError`, naming the line, on another header, a short or long
    row, an empty ticker, name, exchange or evidence, an unknown regulator, a
    CIK that is not ten digits, a partial exit block, a last exchange session
    that is not an XNYS session, a closing on or before it, a notice before
    the closing, or a ticker listed twice."""
    reader = csv.DictReader(io.StringIO(text), strict=True)
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise BankFilersError(f"{_FILE}: header {reader.fieldnames} is not {list(COLUMNS)}")
    rows: list[BankFiler] = []
    seen: set[str] = set()
    for raw in reader:
        line = reader.line_num
        if None in raw or any(raw[c] is None for c in COLUMNS):
            raise BankFilersError(f"{_FILE} line {line}: expected {len(COLUMNS)} fields")
        filer = _row(line, raw)
        if filer.ticker in seen:
            raise BankFilersError(f"{_FILE} line {line}: ticker {filer.ticker} listed twice")
        seen.add(filer.ticker)
        rows.append(filer)
    return tuple(rows)


def load_bank_filers() -> tuple[BankFiler, ...]:
    """The packaged reviewed list."""
    text = resources.files("tradepartner.store").joinpath(_FILE).read_text(encoding="utf-8")
    return parse_bank_filers(text)


# ---------------------------------------------------------------- the census


@dataclass(frozen=True)
class TickerHolder:
    """A non-retracted listing row of the bank's ticker under another CIK."""

    security_id: str
    cik: str
    name: str
    exchange: str
    valid_from: date


@dataclass(frozen=True)
class FilerCensus:
    """What the store holds for one reviewed bank."""

    filer: BankFiler
    #: Non-retracted `securities` ids whose CIK is the bank's EDGAR CIK.
    master_ids: tuple[str, ...]
    bars: int
    last_bar: date | None
    delistings: int
    other_holders: tuple[TickerHolder, ...]

    @property
    def status(self) -> str:
        """`absent` (no master row), `no_bars`, `no_exit` (a failure with no
        delisting row), else `covered`."""
        if not self.master_ids:
            return "absent"
        if self.bars == 0:
            return "no_bars"
        if self.filer.exit_kind is not None and self.delistings == 0:
            return "no_exit"
        return "covered"


def _live(conn: duckdb.DuckDBPyConnection, table: str, alias: str) -> str:
    return f"NOT {alias}.retracted" if schema.has_retracted(conn, table) else "TRUE"


def census(conn: duckdb.DuckDBPyConnection, filers: Sequence[BankFiler]) -> tuple[FilerCensus, ...]:
    """Per reviewed bank, in list order, what the store holds. Reads every
    stored row (a census of the store as it is, not an as-of read) and
    writes nothing, so a `read_only` connection is enough."""
    sec_live = _live(conn, "securities", "s")
    lst_live = _live(conn, "listings", "l")
    out: list[FilerCensus] = []
    for filer in filers:
        ids: tuple[str, ...] = ()
        if filer.edgar_cik is not None:
            ids = tuple(
                r[0]
                for r in conn.execute(
                    f"SELECT DISTINCT security_id FROM securities s WHERE cik = ? AND {sec_live} "
                    "ORDER BY security_id",
                    [filer.edgar_cik],
                ).fetchall()
            )
        bars, last_bar, delistings = 0, None, 0
        if ids:
            bars, last_bar = conn.execute(
                "SELECT count(*), max(session) FROM prices_daily "
                "WHERE list_contains(?, security_id)",
                [list(ids)],
            ).fetchone() or (0, None)
            (delistings,) = conn.execute(
                "SELECT count(*) FROM delistings WHERE list_contains(?, security_id)", [list(ids)]
            ).fetchone() or (0,)
        holders = tuple(
            TickerHolder(*r)
            for r in conn.execute(
                "SELECT DISTINCT l.security_id, s.cik, s.name, l.exchange, l.valid_from "
                "FROM listings l JOIN securities s USING (security_id) "
                f"WHERE l.ticker = ? AND {lst_live} AND {sec_live} "
                "AND NOT list_contains(?, l.security_id) "
                "ORDER BY l.valid_from, l.security_id",
                [filer.ticker, list(ids)],
            ).fetchall()
        )
        out.append(FilerCensus(filer, ids, int(bars), last_bar, int(delistings), holders))
    return tuple(out)


# ---------------------------------------------------------------- discovery


#: A name that reads as the bank itself: "Bank", "Savings", "N.A." or
#: "National Association". Discovery leads only, reviewed before any row.
_BANK_WORDS: Final = re.compile(r"\b(bank|savings|national association)\b|\bn\.\s?a\.", re.I)
#: A name that reads as a holding company, a foreign issuer or a fund.
_NOT_BANK_LEVEL: Final = re.compile(
    r"\b(bancorp|bancshares|bankshares|holdings?|financial|corp|corporation|group|inc|"
    r"ltd|plc|ag|sa|nv|etf|fund|trust|notes?|preferred|depositary|warrants?|units?)\b",
    re.I,
)
#: The venues the master lists on (Alpaca's `exchange` field).
_EXCHANGES: Final = frozenset({"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS"})


@dataclass(frozen=True)
class Candidate:
    """An Alpaca asset that reads as a bank-level issuer and that no listing
    row carries: a lead for the reviewed list, not evidence."""

    symbol: str
    name: str
    exchange: str
    status: str


def held_tickers(conn: duckdb.DuckDBPyConnection) -> frozenset[str]:
    """Every ticker a non-retracted listing row carries."""
    live = _live(conn, "listings", "l")
    return frozenset(
        r[0]
        for r in conn.execute(f"SELECT DISTINCT ticker FROM listings l WHERE {live}").fetchall()
    )


def candidates(
    assets: Iterable[Mapping[str, object]], held_tickers: Collection[str]
) -> tuple[Candidate, ...]:
    """The US-equity assets on a listing venue whose name reads as a bank
    itself (not a holding company, a foreign issuer or a fund) and whose
    symbol is not in `held_tickers`, sorted by symbol. `assets` is Alpaca's
    raw `GET /v2/assets` list."""
    found: dict[str, Candidate] = {}
    for asset in assets:
        symbol, name = str(asset.get("symbol") or ""), str(asset.get("name") or "")
        exchange = str(asset.get("exchange") or "")
        if asset.get("class") != "us_equity" or exchange not in _EXCHANGES or not symbol:
            continue
        head = name.removesuffix("Common Stock").strip()
        if not _BANK_WORDS.search(head) or _NOT_BANK_LEVEL.search(head):
            continue
        if symbol in held_tickers:
            continue
        found[symbol] = Candidate(symbol, name, exchange, str(asset.get("status") or ""))
    return tuple(found[s] for s in sorted(found))


def read_assets_file(path: Path) -> list[dict[str, Any]]:
    """The raw asset list in an Alpaca `GET /v2/assets` file: the recorder's
    gzip JSON object (its `assets` key) or a plain JSON list. Raises
    `ValueError` when the file holds neither."""
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    document = json.loads(raw)
    if isinstance(document, dict):
        document = document.get("assets")
    if not isinstance(document, list) or not all(isinstance(a, dict) for a in document):
        raise ValueError("not an assets list")
    return document
