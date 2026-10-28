"""`census_quarter` reads `form.idx` by the header's column offsets (#2491).

A multi-word form type (`SC 13D`, `SC 13D/A`) and a company name with spaces must
not bleed into each other: the form column is whatever sits left of `Company Name`.
"""

from __future__ import annotations

from pathlib import Path

from tradepartner.adapters.edgar_source import census_quarter

# The header and dashes lines, read byte-exact from a recorded form.idx.
_FIXTURE = Path(__file__).parents[1] / "fixtures" / "edgar" / "filing_index_2024_qtr1.txt"
_FIXTURE_LINES = _FIXTURE.read_text().splitlines()
_AT = next(i for i, ln in enumerate(_FIXTURE_LINES) if ln.startswith("Form Type"))
_HEADER = "\n".join(_FIXTURE_LINES[_AT : _AT + 2]) + "\n"


def _row(form: str, name: str, cik: int, accession: str, filed: str = "2016-08-01") -> str:
    # The real files pad the form to 17 though the header puts Company Name at column 12.
    return f"{form:<17}{name:<62}{cik:<12}{filed}  edgar/data/{cik}/{accession}.txt\n"


def _counts(text: str) -> dict[str, int]:
    accessions, _ciks, unparsed = census_quarter(text)
    assert unparsed == 0
    return {f: len(a) for f, a in accessions[2016].items()}


def test_multi_word_forms_and_spaced_names_are_counted_per_form() -> None:
    text = (
        _HEADER
        + _row("SC 13D", "ADAGE CAPITAL PARTNERS L.P.", 111, "0000000111-16-000001")
        + _row("SC 13D/A", "AAL ENERGY HOLDINGS INC", 222, "0000000222-16-000002")
        + _row("SC 13D/A", "AAL ENERGY HOLDINGS INC", 222, "0000000222-16-000003")
        + _row("SCHEDULE 13D", "BETA CORP", 666, "0000000666-16-000006")
        + _row("SCHEDULE 13D/A", "BETA CORP", 666, "0000000666-16-000007")
        + _row("4", "ACM RESEARCH CORP", 333, "0000000333-16-000004")
        + _row("4", "SMITH JOHN Q", 444, "0000000333-16-000004")  # joint: one accession
        + _row("10-K", "NOT WANTED CO", 555, "0000000555-16-000005")
    )
    assert _counts(text) == {
        "SC 13D": 1,
        "SC 13D/A": 2,
        "SCHEDULE 13D": 1,
        "SCHEDULE 13D/A": 1,
        "4": 1,
    }


def test_a_form_that_fills_its_column_does_not_absorb_the_name() -> None:
    # Real header, 17-wide form column: `SCHEDULE 13D/A` (14) is followed by 3 spaces.
    row = _row("SCHEDULE 13D/A", "TARGET CORP", 9, "0000000009-16-000001")
    assert _counts(_HEADER + row) == {"SCHEDULE 13D/A": 1}


def test_a_wanted_form_row_with_a_broken_tail_is_counted_unparsed() -> None:
    bad = f"{'SC 13D':<17}{'X':<62}no-cik-here edgar/data/1/0000000001-16-000001.txt\n"
    accessions, _ciks, unparsed = census_quarter(_HEADER + bad)
    assert accessions == {}
    assert unparsed == 1


def test_invalid_filing_date_is_counted_unparsed_not_counted() -> None:
    """An invalid date like 2016-99-99 should be counted as unparsed, not counted as a valid row."""
    bad = f"{'SC 13D':<17}{'X':<62}{1:<12}2016-99-99  edgar/data/1/0000000001-16-000001.txt\n"
    accessions, _ciks, unparsed = census_quarter(_HEADER + bad)
    assert accessions == {}
    assert unparsed == 1


def test_one_space_between_form_and_name_is_parsed_correctly() -> None:
    """A row with only one space between form and name should be parsed correctly
    (previously it was silently dropped)."""
    # Form column is 17 chars, but only 1 space before name (not 2+)
    row = (
        "SC 13D ADAGE CAPITAL PARTNERS L.P.          "
        "111          2016-08-01  edgar/data/111/0000000111-16-000001.txt\n"
    )
    accessions, _ciks, unparsed = census_quarter(_HEADER + row)
    assert accessions == {2016: {"SC 13D": {"0000000111-16-000001"}}}
    assert unparsed == 0
