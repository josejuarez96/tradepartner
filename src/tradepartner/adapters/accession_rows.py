"""Order-preserving accession selection shared by EDGAR index readers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class AccessionRow(Protocol):
    """Fields needed for the SEC index's first-row and exchange-copy rules."""

    @property
    def cik(self) -> str: ...

    @property
    def accession(self) -> str: ...

    @property
    def form(self) -> str: ...


_DELISTING_FORMS = frozenset({"25", "25/A", "25-NSE", "25-NSE/A"})
_EXCHANGE_FORMS = frozenset({"25-NSE", "25-NSE/A"})


def first_per_accession[Row: AccessionRow](rows: Iterable[Row]) -> dict[str, Row]:
    """Keep the first SEC listing for each accession in the input's order."""
    first: dict[str, Row] = {}
    for row in rows:
        first.setdefault(row.accession, row)
    return first


def issuer_delistings[Row: AccessionRow](rows: Iterable[Row]) -> list[Row]:
    """Drop an exchange's duplicate 25-NSE when its subject is also listed."""
    listed = [row for row in rows if row.form in _DELISTING_FORMS]
    ciks: dict[str, set[str]] = {}
    for row in listed:
        ciks.setdefault(row.accession, set()).add(row.cik)
    return [
        row
        for row in listed
        if not (
            row.form in _EXCHANGE_FORMS
            and int(row.accession[:10]) == int(row.cik)
            and len(ciks[row.accession]) > 1
        )
    ]
