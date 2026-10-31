"""T317: recorded SEC daily-index inputs for the incremental EDGAR read (#2659).

The fixtures are one quarter's directory listing (2026 QTR2 `index.json`) and
two of its daily `form.<yyyymmdd>.idx` files, each with its response headers.
The daily bodies are stored gzipped (`.idx.gz`) to stay under the repository's
large-file limit. For the fixture scrub (`tests/test_fixture_scrub.py`), SEC's
banner contact address and one company-name word that looks like an Alpaca key
prefix were replaced with `<scrubbed>`, in file order; putting them back gives
the bytes SEC sent (`_original`). The fixtures pin three
facts the T319a adapter builds on: daily files carry `YYYYMMDD` dates that
today's quarterly parser does not read, every recorded file is named by the
listing, and SEC sends both an ETag (the MD5 of the decoded body plus `-gzip`)
and a Last-Modified header.
"""

import gzip
import hashlib
import json
import re
from pathlib import Path

import pytest

from tradepartner.adapters.edgar import parse_filing_index

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "daily_index"
DAILY = ["form.20260401.idx", "form.20260402.idx"]
# What the scrub replaced, per file and in order. SEC's public banner contact is
# split so no file holds an email-shaped string.
_SCRUBBED = {
    "form.20260401.idx": ["webmaster" + "@" + "sec.gov", "AKTIEN" + "GESELLSCHAFT"],
    "form.20260402.idx": ["webmaster" + "@" + "sec.gov"],
}
_HEADERS = {"etag", "last-modified", "content-length", "content-encoding", "content-type", "date"}
_DAILY_DATE = re.compile(r"(?<=\s)(\d{4})(\d{2})(\d{2})(?=\s+edgar/data/)")


def _normalise(text: str) -> str:
    """Test-only: give data-row dates the quarterly `YYYY-MM-DD` form."""
    return "\n".join(
        _DAILY_DATE.sub(r"\1-\2-\3", line) if "edgar/data/" in line else line
        for line in text.split("\n")
    )


def _body(name: str) -> bytes:
    """A recorded response body, decoding a gzipped daily file."""
    path = FIXTURES / name
    return (
        path.read_bytes()
        if path.exists()
        else gzip.decompress((FIXTURES / f"{name}.gz").read_bytes())
    )


def _original(name: str) -> bytes:
    """The body SEC sent: the recorded one with each scrubbed span put back."""
    body = _body(name)
    for value in _SCRUBBED.get(name, []):
        body = body.replace(b"<scrubbed>", value.encode(), 1)
    assert b"<scrubbed>" not in body
    return body


def test_two_daily_files_are_recorded_and_the_listing_names_them() -> None:
    listing = json.loads((FIXTURES / "index.json").read_text(encoding="utf-8"))
    names = {item["name"] for item in listing["directory"]["item"]}
    assert sorted(f.name.removesuffix(".gz") for f in FIXTURES.glob("form.*.idx.gz")) == DAILY
    assert set(DAILY) <= names


def test_daily_dates_are_yyyymmdd_and_need_a_normaliser() -> None:
    raw = _body(DAILY[0]).decode("utf-8")
    first = next(line for line in raw.splitlines() if "edgar/data/" in line)
    assert re.search(r"\s20260401\s+edgar/data/", first)
    with pytest.raises(ValueError):
        parse_filing_index(first, {})
    parsed = parse_filing_index(_normalise(first), {})
    assert parsed.unstamped[0].filed_on.isoformat() == "2026-04-01"


@pytest.mark.parametrize("name", DAILY)
def test_each_daily_file_parses_whole_after_normalising(name: str) -> None:
    raw = _body(name).decode("utf-8")
    data_lines = sum("edgar/data/" in line for line in raw.splitlines())
    rows = parse_filing_index(_normalise(raw), {}).unstamped
    assert len(rows) == data_lines > 0
    day = f"{name[5:9]}-{name[9:11]}-{name[11:13]}"
    assert any(row.filed_on.isoformat() == day for row in rows)


@pytest.mark.parametrize("name", ["index.json", *DAILY])
def test_sec_sends_both_validators_and_the_etag_is_the_body_md5(name: str) -> None:
    headers = json.loads((FIXTURES / f"{name}.headers.json").read_text(encoding="utf-8"))
    assert headers["last-modified"].endswith(" GMT")
    body = _original(name)
    assert headers["etag"] == f'"{hashlib.md5(body, usedforsecurity=False).hexdigest()}-gzip"'


@pytest.mark.parametrize("name", ["index.json", *DAILY])
def test_recorded_headers_are_only_the_allow_list(name: str) -> None:
    headers = json.loads((FIXTURES / f"{name}.headers.json").read_text(encoding="utf-8"))
    assert set(headers) <= _HEADERS
