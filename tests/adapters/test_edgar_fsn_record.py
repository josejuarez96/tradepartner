"""The `cli_record fsn` target (T11g, split from T11c #224): records the FSN
data-set page and periods `2025_10` and `2026_02`, trimmed to one fixture
accession each and scrubbed. Every request goes to an `httpx.MockTransport`;
the zips are synthetic, stated as synthetic."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from tradepartner import cli_record
from tradepartner.config import Settings

USER_AGENT = "TradePartner test test@example.com"
FSN_PAGE_URL = (
    "https://www.sec.gov/data-research/sec-markets-data/financial-statement-notes-data-sets"
)


def _zip_url(period: str) -> str:
    return (
        "https://www.sec.gov/files/dera/data/financial-statement-notes-data-sets/"
        f"{period}_notes.zip"
    )


def _tsv(header: list[str], rows: list[list[str]]) -> str:
    return "\n".join("\t".join(line) for line in [header, *rows]) + "\n"


def _synthetic_zip(accession: str, other: str) -> bytes:
    """One period: the fixture accession plus `other`, which trimming drops;
    a kept tag, a dropped tag, a referenced and an unreferenced `dim` row."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("sub.tsv", _tsv(["adsh", "form"], [[accession, "10-K"], [other, "8-K"]]))
        facts = ["adsh", "tag", "dimh", "value"]
        archive.writestr(
            "num.tsv",
            _tsv(
                facts,
                [
                    [accession, "EntityCommonStockSharesOutstanding", "0xaa", "1"],
                    [accession, "Revenues", "0x00", "2"],
                    [other, "EntityCommonStockSharesOutstanding", "0x00", "3"],
                ],
            ),
        )
        archive.writestr("txt.tsv", _tsv(facts, [[accession, "TradingSymbol", "0xbb", "AB"]]))
        archive.writestr(
            "dim.tsv",
            _tsv(
                ["dimhash", "segments"],
                [
                    ["0xaa", "ClassOfStock=CommonClassA;"],
                    ["0xbb", "ClassOfStock=CommonClassB;"],
                    ["0xcc", "ClassOfStock=Unused;"],
                ],
            ),
        )
    return buffer.getvalue()


@pytest.fixture
def recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Runs `main(["fsn"])` against the mock transport into `tmp_path`."""
    settings = Settings(_env_file=None, sec_edgar_user_agent=USER_AGENT)
    monkeypatch.setattr(cli_record, "get_settings", lambda: settings)
    monkeypatch.setattr(cli_record, "FIXTURES_ROOT", tmp_path)
    monkeypatch.setattr(cli_record, "EDGAR_FSN_FIXTURES_DIR", tmp_path / "fsn")
    zips = {
        period: _synthetic_zip(accession, "0000000099-26-000001")
        for period, accession in cli_record.FSN_RECORD_PERIODS.items()
    }
    # The page echoes the User-Agent so the scrub has something to remove.
    page = f"<a href='{_zip_url('2025_10')}'>x</a><a href='{_zip_url('2026_02')}'>y</a>"
    page += f"<!-- served to {USER_AGENT} -->"

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == FSN_PAGE_URL:
            return httpx.Response(200, content=page)
        for period, body in zips.items():
            if url == _zip_url(period):
                return httpx.Response(200, content=body)
        pytest.fail(f"unrouted: {url}")

    monkeypatch.setattr(
        cli_record.edgar_raw,
        "_default_client",
        lambda timeout: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert cli_record.main(["fsn"]) == 0
    return tmp_path / "fsn"


def test_the_page_is_recorded_and_scrubbed(recorded: Path) -> None:
    page = (recorded / "page.html").read_text()
    assert "2025_10" in page and "2026_02" in page
    assert USER_AGENT not in page and "test@example.com" not in page


def test_each_period_is_trimmed_to_its_fixture_accession(recorded: Path) -> None:
    for period, accession in cli_record.FSN_RECORD_PERIODS.items():
        member = {name: (recorded / period / name).read_text() for name in cli_record._FSN_MEMBERS}
        assert accession in member["sub.tsv"] and "0000000099" not in member["sub.tsv"]
        assert "Revenues" not in member["num.tsv"]  # not a recorded tag
        assert "EntityCommonStockSharesOutstanding" in member["num.tsv"]
        assert "TradingSymbol" in member["txt.tsv"]
        # `dim` keeps exactly the hashes the kept rows reference.
        assert "0xaa" in member["dim.tsv"] and "0xbb" in member["dim.tsv"]
        assert "0xcc" not in member["dim.tsv"]


def test_the_downloaded_zips_are_deleted(recorded: Path) -> None:
    assert not list(recorded.rglob("*.zip"))


def test_fsn_without_a_user_agent_fails_before_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_record, "get_settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(
        cli_record.edgar_raw,
        "_default_client",
        lambda timeout: pytest.fail("no request may be made"),
    )
    assert cli_record.main(["fsn"]) == 1
    assert "SEC_EDGAR_USER_AGENT" in capsys.readouterr().err


def test_an_unknown_target_is_refused_not_recorded_as_all(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A typo must not fall through to the full recorder, which needs Alpaca keys."""
    monkeypatch.setattr(
        cli_record, "get_settings", lambda: pytest.fail("settings must not be read")
    )
    assert cli_record.main(["fns"]) == 2
    assert "fns" in capsys.readouterr().err
