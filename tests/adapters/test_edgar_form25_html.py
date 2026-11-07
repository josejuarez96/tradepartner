"""#3084: an issuer's own Form 25 is an HTML document, not the XML form.

`parse_delisting_html` reads the printed layout: issuer and exchange above
"(Exact name of Issuer ...)", the class above "(Description of class of
securities)". The documents below follow the shapes seen in EDGAR's issuer
Form 25s (2019-2026): a filing agent's table layout with entities and
no-break spaces, an exchange named on its own line or left out, and a class
description with parentheses. Nothing is guessed: a missing exchange or label
raises `ValueError`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from tradepartner.adapters.edgar import HTML_DELISTING_FORMS, parse_delisting_html

ACCESSION = "0001193125-26-164179"
CIK = "1327273"
AT = datetime(2026, 4, 20, 21, 5, 11, tzinfo=UTC)


def form25(
    *,
    issuer: str = "LYRA THERAPEUTICS, INC.",
    exchange: str | None = "THE NASDAQ CAPITAL MARKET",
    class_title: str = "Common Stock, par value $0.001 per share",
    address: bool = True,
    file_number: str = "001-39273",
) -> str:
    """A Form 25 in a filing agent's table layout (entities, `&nbsp;`, a
    script block that names another exchange and must not be read)."""
    exchange_row = f"<tr><td><p>{exchange}</p></td></tr>" if exchange is not None else ""
    address_row = (
        "<tr><td>480 Arsenal Way<br>Watertown, Massachusetts 02472</td></tr>"
        "<tr><td><p>(Address, including zip code, and telephone number, including area "
        "code, of Issuer&#8217;s principal executive offices)</p></td></tr>"
        if address
        else ""
    )
    return (
        "<html><head><script>var x = 'New York Stock Exchange';</script>"
        "<style>p {font: Nasdaq}</style></head><body>"
        "<p>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</p><p>FORM&nbsp;25</p>"
        "<p>NOTIFICATION OF REMOVAL FROM LISTING AND/OR REGISTRATION UNDER SECTION 12(b) "
        "OF THE SECURITIES EXCHANGE ACT OF 1934.</p>"
        f"<p>Commission File Number&nbsp;{file_number}</p><table>"
        f"<tr><td><b>{issuer}</b></td></tr>{exchange_row}"
        "<tr><td>(Exact name of Issuer as specified in its charter, and name of Exchange "
        "where security is listed and/or registered)</td></tr>"
        f"{address_row}"
        f"<tr><td><p>{class_title}</p></td></tr>"
        "<tr><td>(Description of class of securities)</td></tr></table>"
        "<p>Please place an X in the box to designate the rule provision relied upon "
        "to strike the class of securities from listing and registration:</p>"
        "<p>&#9746; Pursuant to 17 CFR 240.12d2-2(c), the Issuer has complied with the "
        "rules of the Exchange</p>"
        "<p>Pursuant to the requirements of the Securities Exchange Act of 1934, Lyra "
        "Therapeutics, Inc. certifies ... on the New York Stock Exchange</p>"
        "</body></html>"
    )


def parse(text: str, form: str = "25") -> object:
    return parse_delisting_html(text, cik=CIK, form=form, accession=ACCESSION, accepted_at=AT)


def test_reads_class_exchange_and_the_filers_cik() -> None:
    filing = parse_delisting_html(form25(), cik=CIK, form="25", accession=ACCESSION, accepted_at=AT)
    assert (filing.cik, filing.form, filing.class_title, filing.exchange) == (
        "0001327273",
        "25",
        "Common Stock, par value $0.001 per share",
        "NASDAQ",
    )
    assert filing.accession == ACCESSION
    assert filing.accepted_at == AT
    assert filing.effective_on is None  # the store's Rule 12d2-2 default applies


@pytest.mark.parametrize(
    ("exchange", "code"),
    [
        ("The Nasdaq Stock Market LLC", "NASDAQ"),
        ("Nasdaq Global Select Market", "NASDAQ"),
        ("New York Stock Exchange LLC", "NYSE"),
        ("NYSE", "NYSE"),
        ("NYSE American LLC", "NYSE_AMERICAN"),
        ("NYSE MKT LLC", "NYSE_AMERICAN"),
        ("NYSE Arca, Inc.", "NYSE_ARCA"),
        ("Cboe BZX Exchange, Inc.", "CBOE_BZX"),
    ],
)
def test_exchange_names_map_to_config_codes(exchange: str, code: str) -> None:
    assert (
        parse_delisting_html(
            form25(exchange=exchange), cik=CIK, form="25", accession=ACCESSION, accepted_at=AT
        ).exchange
        == code
    )


def test_exchange_on_the_issuer_line_is_read() -> None:
    filing = parse_delisting_html(
        form25(issuer="STRATA Skin Sciences, Inc. The Nasdaq Stock Market LLC", exchange=None),
        cik=CIK,
        form="25/A",
        accession=ACCESSION,
        accepted_at=AT,
    )
    assert (filing.exchange, filing.form) == ("NASDAQ", "25/A")


def test_class_with_parentheses_and_entities_is_kept_whole() -> None:
    filing = parse_delisting_html(
        form25(
            class_title="American&nbsp;Depositary Shares (each representing 25 Ordinary Shares)"
        ),
        cik=CIK,
        form="25",
        accession=ACCESSION,
        accepted_at=AT,
    )
    assert filing.class_title == "American Depositary Shares (each representing 25 Ordinary Shares)"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (form25(exchange=None), "names no single exchange"),  # Evolving Systems' shape
        (form25(exchange="Nasdaq PHLX LLC"), "names no single exchange"),
        (form25(issuer="NYSE American LLC", exchange="The Nasdaq Stock Market"), "single exchange"),
        (form25(address=False), "no address label"),
        (form25(class_title=""), "names no class"),
        (form25(class_title="x" * 400), "names no class"),
        ("<html><body><p>FORM 25</p><p>Common Stock</p></body></html>", "labels not found"),
    ],
)
def test_nothing_is_guessed(document: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse(document)


def test_the_script_naming_another_exchange_is_not_read() -> None:
    """The fixture's `<script>` names NYSE and its signature NYSE; only the
    issuer block above the label is read."""
    assert (
        parse_delisting_html(
            form25(), cik=CIK, form="25", accession=ACCESSION, accepted_at=AT
        ).exchange
        == "NASDAQ"
    )


@pytest.mark.parametrize("form", ["25-NSE", "25-NSE/A", "10-K"])
def test_only_an_issuers_form_25_is_read_as_html(form: str) -> None:
    assert form not in HTML_DELISTING_FORMS
    with pytest.raises(ValueError, match="not an issuer Form 25"):
        parse(form25(), form=form)


def test_accepted_at_must_be_aware_and_is_kept_in_utc() -> None:
    eastern = timezone(timedelta(hours=-4))
    filing = parse_delisting_html(
        form25(),
        cik=CIK,
        form="25",
        accession=ACCESSION,
        accepted_at=AT.astimezone(eastern),
    )
    assert filing.accepted_at == AT
    with pytest.raises(ValueError):
        parse_delisting_html(
            form25(), cik=CIK, form="25", accession=ACCESSION, accepted_at=AT.replace(tzinfo=None)
        )
