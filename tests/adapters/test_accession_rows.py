"""The SEC file's first-row rule shared by table and quarterly readers."""

from datetime import date

from tradepartner.adapters.accession_rows import first_per_accession, issuer_delistings
from tradepartner.adapters.edgar import UnstampedFiling


def _row(cik: str, accession: str, form: str = "SC 13D") -> UnstampedFiling:
    return UnstampedFiling(cik, cik, form, accession, date(2026, 1, 2))


def test_first_per_accession_preserves_input_order() -> None:
    first = _row("0000000002", "0000000001-26-000001")
    second = _row("0000000001", first.accession)
    assert first_per_accession([first, second])[first.accession] == first


def test_exchange_copy_is_dropped_but_subject_and_form25_are_kept() -> None:
    accession = "0000000001-26-000001"
    exchange = _row("0000000001", accession, "25-NSE")
    subject = _row("0000000002", accession, "25-NSE")
    form25 = _row("0000000003", "0000000003-26-000002", "25")
    assert issuer_delistings([exchange, subject, form25]) == [subject, form25]
    assert issuer_delistings([exchange]) == [exchange]
