"""Pure reader of one SEC Insider Transactions Data Set quarter (plan T305b, #2498).

The SEC's DERA data sets cut every Form 3, 4 and 5 by filing quarter into one
`<yyyy>q<n>_form345.zip` of tab-separated tables. This module reads three of them,
`SUBMISSION`, `REPORTINGOWNER` and `NONDERIV_TRANS`, for the Form 4 and 4/A accessions
only, into T305's `Form4Transaction` rows. It takes the zip (a path or an open file),
touches no store and makes no request; `edgar_raw.insider_bulk_zip` fetches the zip.

Nothing here decides a route. Per accession it returns the raw facts the route rules
read (each row's code and price, the order-bad flag, the identity-bad flag, the header
fields the submissions cross-check compares) and T305c applies the rules. The row rules
are `parse_form4`'s, applied to the tab-separated columns (a blank is null, never zero;
a row missing its code, date, A/D flag, share count or ownership form is skipped and
still holds its `line_no` position), and the `Form4Transaction` type is T305's.

**Line order.** `line_no` is the zero-based rank of the accession's `NONDERIV_TRANS` rows
by the surrogate key `NONDERIV_TRANS_SK`, ascending, every such row ranked and a skipped
row keeping its position; `NONDERIV_HOLDING` is never read. A duplicate or non-integer
key makes the order unknowable: `order_bad`, no rows returned for the accession (it goes
to XML).

**Owner order.** The data set does not keep the XML's `reportingOwner` order, so rows
are ordered by `(line_no, filer_cik)` and `filer_ciks` is sorted; the identity
`(accession, filer_cik, line_no)` is the same, and the comparison with `parse_form4` is
by that key, never by owner position.

**Identity.** A blank or non-numeric `ISSUERCIK`, no `REPORTINGOWNER` row, an owner with
a blank or non-numeric `RPTOWNERCIK`, or an owner listed twice is `identity_bad`: no rows
returned, because `parse_form4` would refuse the same accession.

**Accepted rounding.** The set rounds prices and share counts to 2 decimals. A fractional
share count (a reinvested dividend, a split remainder) can differ from the XML by under
0.005 shares, and a true price of 0.995 to 0.999 reads as 1.00 (#2498 open questions 2
and 5). Parity with the XML is therefore asserted at 2 decimals.

**Fail closed.** A zip missing a table, a table missing a column named below (the
readme's names, pinned against a recorded zip), a row whose cell count differs from the
header (a stray tab shifts every later cell), a byte that is not UTF-8, a table member
over the size cap (declared or actually read), or a table that does not parse, raises
`InsiderBulkUnreadableError`: nothing is returned from that quarter.

Forms 3, 5, 3/A and 5/A and the `DERIV_*` and `NONDERIV_HOLDING` tables are not read.
"""

from __future__ import annotations

import csv
import re
import zipfile
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import IO, Any

from tradepartner.adapters.edgar import Form4Transaction, _BadField, _cik, _xml_decimal

_FORMS = frozenset({"4", "4/A"})

#: The cap on one table member's uncompressed size (`events.form4_bulk_max_member_bytes`
#: is the config key; a test pins that the two defaults agree).
DEFAULT_MAX_MEMBER_BYTES = 1024**3

#: The columns each table must carry (the data set readme's names).
_SUBMISSION_COLUMNS = (
    "ACCESSION_NUMBER",
    "FILING_DATE",
    "DATE_OF_ORIG_SUB",
    "DOCUMENT_TYPE",
    "ISSUERCIK",
)
_OWNER_COLUMNS = ("ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP")
_TRANS_COLUMNS = (
    "ACCESSION_NUMBER",
    "NONDERIV_TRANS_SK",
    "SECURITY_TITLE",
    "TRANS_DATE",
    "DEEMED_EXECUTION_DATE",
    "TRANS_CODE",
    "TRANS_ACQUIRED_DISP_CD",
    "TRANS_SHARES",
    "TRANS_PRICEPERSHARE",
    "SHRS_OWND_FOLWNG_TRANS",
    "DIRECT_INDIRECT_OWNERSHIP",
)
_MONTHS = {
    name: number
    for number, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1
    )
}


class InsiderBulkUnreadableError(ValueError):
    """The quarter's zip is missing a table or a named column, or a table does not
    parse (`sec_forms_bulk_unreadable`): nothing is read from it."""


@dataclass(frozen=True)
class BulkRowFact:
    """The raw facts of one `NONDERIV_TRANS` row that the route rules read. `price` is
    the 2-decimal bulk price (`None` when blank or unparseable, never zero-filled);
    `line_no` is `None` only when the accession is `order_bad`; `skipped` marks a row
    that gave no `Form4Transaction`."""

    line_no: int | None
    transaction_code: str
    price: Decimal | None
    skipped: bool


@dataclass(frozen=True)
class BulkAccession:
    """One Form 4 or 4/A accession of the quarter. `filing_date` and `document_type` are
    the bulk header fields, only to be checked against the submissions record, never
    used as a date or form. `transactions` is empty when `identity_bad` or `order_bad`
    (the accession goes to XML); `rows` still lists every row's facts."""

    accession: str
    document_type: str
    filing_date: date | None
    issuer_cik: str | None
    filer_ciks: tuple[str, ...]
    transactions: tuple[Form4Transaction, ...]
    skipped: int
    rows: tuple[BulkRowFact, ...]
    order_bad: bool
    identity_bad: bool


@dataclass(frozen=True)
class InsiderBulk:
    """Every Form 4 and 4/A accession of one quarter's zip, keyed by accession."""

    accessions: dict[str, BulkAccession]


def _bulk_date(text: str) -> date:
    """A data-set date, `DD-MON-YYYY` (`31-MAR-2026`), month names matched without a
    locale. Raises `_BadField`."""
    parts = text.strip().split("-")
    try:
        day, month, year = parts
        return date(int(year), _MONTHS[month.upper()], int(day))
    except (ValueError, KeyError) as error:
        raise _BadField(f"not a data-set date: {text!r}") from error


def _table(
    archive: zipfile.ZipFile, name: str, required: tuple[str, ...], max_bytes: int
) -> Iterator[dict[str, str]]:
    """The rows of one tab-separated member as dicts (a row of the wrong width is
    unreadable). Raises `InsiderBulkUnreadableError` for a missing member or column or a
    malformed table."""
    try:
        info = archive.getinfo(f"{name}.tsv")
    except KeyError as error:
        raise InsiderBulkUnreadableError(f"{name}.tsv is not in the zip") from error
    if info.file_size > max_bytes:
        raise InsiderBulkUnreadableError(
            f"{name}.tsv is {info.file_size} bytes, over the {max_bytes}-byte cap"
        )
    try:
        with archive.open(info) as member:
            read = 0

            def decoded() -> Iterator[str]:
                nonlocal read
                for line in member:
                    read += len(line)
                    if read > max_bytes:  # a header can understate the size
                        raise InsiderBulkUnreadableError(
                            f"{name}.tsv is over the {max_bytes}-byte cap"
                        )
                    yield line.decode("utf-8-sig")

            lines = decoded()
            reader = csv.reader(lines, delimiter="\t", quoting=csv.QUOTE_NONE)
            header = next(reader, None)
            if header is None:
                raise InsiderBulkUnreadableError(f"{name}.tsv is empty")
            header = [column.strip() for column in header]
            missing = [column for column in required if column not in header]
            if missing:
                raise InsiderBulkUnreadableError(f"{name}.tsv lacks column(s) {missing}")
            width = len(header)
            for cells in reader:
                if not any(cell.strip() for cell in cells):
                    continue
                if len(cells) != width:
                    # A stray tab shifts every later cell (a code read as a date, a
                    # price as blank): no cell of the row can be trusted.
                    raise InsiderBulkUnreadableError(
                        f"{name}.tsv row has {len(cells)} cells, the header {width}"
                    )
                yield dict(zip(header, cells, strict=True))
    except (csv.Error, zipfile.BadZipFile, OSError, EOFError, UnicodeDecodeError) as error:
        raise InsiderBulkUnreadableError(f"{name}.tsv does not parse: {error!r}") from error


def _cik_or_none(raw: str) -> str | None:
    text = raw.strip()
    if not text:
        return None
    try:
        return _cik(text)
    except ValueError:
        return None


def _unquoted(raw: str) -> str:
    """A cell in the data set's CSV quoting as its plain text (#2706).

    The SEC writes a value holding a `"` wrapped in quotes with each inner quote
    doubled, `"Common Stock (""Shares"")"`, though the table is tab-separated; the XML
    holds `Common Stock ("Shares")`. A value not in that form (no wrapping, or a lone
    inner quote) is returned stripped and otherwise as read."""
    text = raw.strip()
    if len(text) < 2 or text[0] != '"' or text[-1] != '"':
        return text
    inner = text[1:-1]
    if '"' in inner.replace('""', ""):
        return text
    return inner.replace('""', '"')


_ROLE_TOKEN = re.compile(r"tenpercentowner|director|officer|other")


def _relationship(raw: str) -> tuple[bool, bool, bool, bool] | None:
    """Decode comma-separated or concatenated roles; unknown text marks identity bad."""
    roles: set[str] = set()
    for part in raw.lower().split(","):
        text = part.strip()
        while text:
            match = _ROLE_TOKEN.match(text)
            if match is None:
                return None
            roles.add(match.group())
            text = text[match.end() :].lstrip()
    return "director" in roles, "officer" in roles, "tenpercentowner" in roles, "other" in roles


def _row_fields(row: dict[str, str]) -> dict[str, Any] | None:
    """`parse_form4`'s row rules on one `NONDERIV_TRANS` row: the owner-independent
    fields, or `None` when a required one is missing or unparseable (skipped)."""
    code = row["TRANS_CODE"].strip()
    acquired = row["TRANS_ACQUIRED_DISP_CD"].strip().upper()
    ownership = row["DIRECT_INDIRECT_OWNERSHIP"].strip().upper()
    shares_text = row["TRANS_SHARES"].strip()
    date_text = row["TRANS_DATE"].strip()
    if not code or acquired not in ("A", "D") or ownership not in ("D", "I"):
        return None
    if not shares_text or not date_text:
        return None
    try:
        price_text = row["TRANS_PRICEPERSHARE"].strip()
        owned_text = row["SHRS_OWND_FOLWNG_TRANS"].strip()
        deemed_text = row["DEEMED_EXECUTION_DATE"].strip()
        try:
            deemed = _bulk_date(deemed_text) if deemed_text else None
        except (_BadField, ValueError):
            deemed = None  # optional: an invalid date is absent, as the XML path (#2514)
        return {
            "transaction_date": _bulk_date(date_text),
            "deemed_execution_date": deemed,
            "transaction_code": code,
            "acquired_disposed": acquired,
            "shares": _xml_decimal(shares_text),
            "price": _xml_decimal(price_text) if price_text else None,
            "shares_owned_after": _xml_decimal(owned_text) if owned_text else None,
            "ownership_form": ownership,
            "security_title": _unquoted(row["SECURITY_TITLE"]),
        }
    except (_BadField, InvalidOperation, ValueError):
        return None


def _ranked(rows: list[dict[str, str]]) -> list[tuple[int, dict[str, str]]] | None:
    """The accession's rows with their zero-based rank by `NONDERIV_TRANS_SK`, or
    `None` when a key is blank, not an integer or listed twice (order unknowable)."""
    keys: list[int] = []
    for row in rows:
        text = row["NONDERIV_TRANS_SK"].strip()
        if not text.isascii() or not text.isdigit():
            return None
        keys.append(int(text))
    if len(set(keys)) != len(keys):
        return None
    order = sorted(range(len(rows)), key=lambda i: keys[i])
    return [(rank, rows[i]) for rank, i in enumerate(order)]


def _accession(
    header: dict[str, str],
    duplicate_header: bool,
    owners: list[dict[str, str]],
    trans_rows: list[dict[str, str]],
) -> BulkAccession:
    accession = header["ACCESSION_NUMBER"].strip()
    issuer = _cik_or_none(header["ISSUERCIK"])
    owner_ciks = [_cik_or_none(o["RPTOWNERCIK"]) for o in owners]
    identity_bad = (
        duplicate_header
        or issuer is None
        or not owners
        or any(c is None for c in owner_ciks)
        or len(set(owner_ciks)) != len(owner_ciks)
    )
    try:
        original = (
            _bulk_date(header["DATE_OF_ORIG_SUB"]) if header["DATE_OF_ORIG_SUB"].strip() else None
        )
    except _BadField:
        original = None
    try:
        filed = _bulk_date(header["FILING_DATE"]) if header["FILING_DATE"].strip() else None
    except _BadField:
        filed = None

    ranked = _ranked(trans_rows)
    order_bad = ranked is None
    if ranked is None:
        ranked_rows: list[tuple[int | None, dict[str, str]]] = [(None, r) for r in trans_rows]
    else:
        ranked_rows = [(rank, row) for rank, row in ranked]
    parsed = [(rank, row, _row_fields(row)) for rank, row in ranked_rows]
    facts = tuple(
        BulkRowFact(
            line_no=rank,
            transaction_code=row["TRANS_CODE"].strip(),
            price=fields["price"] if fields is not None else _lenient_price(row),
            skipped=fields is None,
        )
        for rank, row, fields in parsed
    )
    skipped = sum(1 for fact in facts if fact.skipped)

    owner_roles = [_relationship(o["RPTOWNER_RELATIONSHIP"]) for o in owners]
    identity_bad = identity_bad or any(roles is None for roles in owner_roles)
    transactions: tuple[Form4Transaction, ...] = ()
    if not identity_bad and not order_bad and issuer is not None:
        transactions = tuple(
            Form4Transaction(
                issuer_cik=issuer,
                filer_cik=cik,
                line_no=rank,
                date_of_original_submission=original,
                is_director=roles[0],
                is_officer=roles[1],
                is_ten_percent_owner=roles[2],
                is_other=roles[3],
                **fields,
            )
            for rank, _row, fields in parsed
            if rank is not None and fields is not None
            for cik, roles in zip(owner_ciks, owner_roles, strict=True)
            if cik is not None and roles is not None
        )
        transactions = tuple(sorted(transactions, key=lambda t: (t.line_no, t.filer_cik)))
    return BulkAccession(
        accession=accession,
        document_type=header["DOCUMENT_TYPE"].strip(),
        filing_date=filed,
        issuer_cik=issuer,
        filer_ciks=tuple(sorted(c for c in owner_ciks if c is not None)),
        transactions=transactions,
        skipped=skipped,
        rows=facts,
        order_bad=order_bad,
        identity_bad=identity_bad,
    )


def _lenient_price(row: dict[str, str]) -> Decimal | None:
    """The price of a skipped row, for the route rules, when it parses on its own."""
    text = row["TRANS_PRICEPERSHARE"].strip()
    try:
        return _xml_decimal(text) if text else None
    except (_BadField, InvalidOperation, ValueError):
        return None


def read_insider_bulk(
    source: Path | IO[bytes], *, max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES
) -> InsiderBulk:
    """Read one quarter's insider data-set zip: every Form 4 and 4/A accession of its
    `SUBMISSION` table with its owners and non-derivative transactions. Pure over the
    zip; raises `InsiderBulkUnreadableError` (fail closed) for a missing table or
    column or an unparseable table. A `NONDERIV_TRANS` or `REPORTINGOWNER` row of an
    accession that is not a Form 4 or 4/A of `SUBMISSION` (a Form 3 or 5) is not read.
    A table member over `max_member_bytes` uncompressed, or not strict UTF-8, is unreadable."""
    try:
        archive = zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError) as error:
        raise InsiderBulkUnreadableError(f"not a readable zip: {error!r}") from error
    with archive:
        headers: dict[str, dict[str, str]] = {}
        duplicated: set[str] = set()
        for row in _table(archive, "SUBMISSION", _SUBMISSION_COLUMNS, max_member_bytes):
            accession = row["ACCESSION_NUMBER"].strip()
            if row["DOCUMENT_TYPE"].strip() not in _FORMS or not accession:
                continue
            if accession in headers:
                duplicated.add(accession)
            headers[accession] = row
        owners: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in _table(archive, "REPORTINGOWNER", _OWNER_COLUMNS, max_member_bytes):
            accession = row["ACCESSION_NUMBER"].strip()
            if accession in headers:
                owners[accession].append(row)
        trans: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in _table(archive, "NONDERIV_TRANS", _TRANS_COLUMNS, max_member_bytes):
            accession = row["ACCESSION_NUMBER"].strip()
            if accession in headers:
                trans[accession].append(row)
    return InsiderBulk(
        {
            accession: _accession(
                header, accession in duplicated, owners.get(accession, []), trans.get(accession, [])
            )
            for accession, header in headers.items()
        }
    )
