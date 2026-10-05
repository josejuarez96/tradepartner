"""Tests for `tradepartner master-retract` (#859): finding the stored master
rows the current rules no longer derive and retracting them as point-in-time
revisions, recorded for health.

A fixture EDGAR source derives one class, ACME on NYSE. A false successor
(`<cik>@<date>`, as #826 wrote WillScot's) is stored by hand, as an earlier
rule would have written it, with a listing under the same ticker."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

import duckdb
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.adapters.filings import (
    CompanySnapshotEntry,
    CoverListing,
    CoverPage,
    FilingHeader,
    FilingIndexEntry,
)
from tradepartner.adapters.fixture_filings import FixtureFilingSource
from tradepartner.adapters.prices import Bar, CorporateAction, PriceSource
from tradepartner.config import Settings
from tradepartner.health import UNDERIVED_MASTER_ROWS, integrity_checks
from tradepartner.ingest import ingest_session
from tradepartner.retract import RetractRefused, master_retract
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.master import securities_as_of
from tradepartner.store.retraction import RETRACT, RETRACTED, underived_as_of
from tradepartner.store.schema import init_schema

ACME = "0000000001"
FALSE_ID = f"{ACME}@2019-05-01"  # a successor the current rules do not make
FALSE_KNOWN = datetime(2019, 5, 1, 20, 30, tzinfo=UTC)
INGEST = datetime(2019, 6, 29, 2, 0, tzinfo=UTC)
CHECK = datetime(2019, 7, 2, 2, 0, tzinfo=UTC)
RETRACT_AT = datetime(2019, 7, 3, 2, 0, tzinfo=UTC)
AFTER = datetime(2019, 7, 4, 2, 0, tzinfo=UTC)


def _at(year: int, month: int, day: int) -> datetime:
    return datetime(year, month, day, 20, 30, tzinfo=UTC)


def _filings(cls: type[FixtureFilingSource] = FixtureFilingSource) -> FixtureFilingSource:
    return cls(
        index=[FilingIndexEntry(ACME, "Acme Corp", "10-K", f"{ACME}-18-1", _at(2018, 3, 1))],
        cover_pages=[
            CoverPage(
                ACME,
                f"{ACME}-19-1",
                _at(2019, 3, 1),
                (CoverListing("Common Stock", "ACME", "NYSE"),),
            )
        ],
        headers=[FilingHeader(ACME, f"{ACME}-19-1", "10-K", 3571, _at(2019, 3, 1))],
        snapshot=[
            CompanySnapshotEntry("0000884394", "SPDR S&P 500", "SPY", "NYSE_ARCA", _at(2019, 6, 28))
        ],
    )


class _NoPrices(PriceSource):
    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        return []

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        return []


def _ingest(settings: Settings, at: datetime) -> None:
    result = ingest_session(
        settings, prices=_NoPrices(), filings=_filings(), source="edgar", clock=lambda: at
    )
    assert result.ok, result.runs


def _false_rows() -> tuple[dict[str, Any], dict[str, Any]]:
    common = {
        "known_at": FALSE_KNOWN,
        "ingested_at": FALSE_KNOWN,
        "source": "edgar",
        "provenance": "filing",
    }
    security = {"security_id": FALSE_ID, "cik": ACME, "name": "Acme Corp", "benchmark": False}
    listing = {
        "security_id": FALSE_ID,
        "ticker": "ACME",
        "exchange": "NYSE",
        "class_title": "Common Stock",
        "valid_from": date(2019, 5, 1),
    }
    return security | common, listing | common


@pytest.fixture
def store(settings: Settings) -> Settings:
    """ACME ingested from the fixture source, plus #826-style false rows."""
    _ingest(settings, INGEST)
    security, listing = _false_rows()
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(conn, "securities", security)
        insert_row(conn, "listings", listing)
    return settings


def _read(settings: Settings) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(settings.store.path, read_only=True)


def _listed(settings: Settings, t: datetime) -> set[str]:
    with _read(settings) as conn:
        return set(listings_as_of(conn, t)["security_id"].to_list())


def _known(settings: Settings, t: datetime) -> set[str]:
    with _read(settings) as conn:
        return set(securities_as_of(conn, t)["security_id"].to_list())


def _rule(settings: Settings, t: datetime) -> list[dict[str, Any]]:
    with _read(settings) as conn:
        [check] = [
            c for c in integrity_checks(conn, t, settings) if c.rule == UNDERIVED_MASTER_ROWS
        ]
        return check.violations.to_dicts()


def test_a_dry_run_lists_every_underived_row_and_changes_nothing(store: Settings) -> None:
    result = master_retract(store, filings=_filings(), clock=lambda: CHECK)
    assert [(u.table, u.key) for u in result.found] == [
        ("securities", (FALSE_ID,)),
        ("listings", (FALSE_ID, "ACME", "NYSE", date(2019, 5, 1))),
    ]
    assert result.summary().startswith("would retract 2 rows (1 securities, 1 listings)")
    assert result.lines()[0] == f"securities: {FALSE_ID} (known {FALSE_KNOWN.isoformat()})"
    assert FALSE_ID in _listed(store, AFTER)
    with _read(store) as conn:
        assert conn.execute("SELECT count(*) FROM ingestion_runs").fetchone() == (1,)


def test_the_apply_retracts_at_its_clock_and_reads_before_it_are_unchanged(
    store: Settings,
) -> None:
    """No look-ahead: an as-of read before the correction still sees the row."""
    master_retract(store, filings=_filings(), clock=lambda: RETRACT_AT, dry_run=False, expect=2)
    for t in (FALSE_KNOWN, CHECK):
        assert FALSE_ID in _listed(store, t)
        assert FALSE_ID in _known(store, t)
    assert FALSE_ID not in _listed(store, RETRACT_AT)
    assert FALSE_ID not in _known(store, AFTER)
    assert _listed(store, AFTER) == {ACME, "BENCH:SPY"}
    with _read(store) as conn:
        rows = conn.execute(
            "SELECT known_at, ingested_at, retracted FROM listings "
            "WHERE security_id = ? ORDER BY known_at",
            [FALSE_ID],
        ).fetchall()
    assert rows == [(FALSE_KNOWN, FALSE_KNOWN, False), (RETRACT_AT, RETRACT_AT, True)]


def test_the_apply_is_recorded_and_never_reads_as_a_fresh_ingest(store: Settings) -> None:
    result = master_retract(
        store, filings=_filings(), clock=lambda: RETRACT_AT, dry_run=False, expect=2
    )
    with _read(store) as conn:
        [(run_id, source, status, mode, rows, message)] = conn.execute(
            "SELECT run_id, source, status, mode, rows_added, message FROM ingestion_runs "
            "WHERE mode = ?",
            [RETRACT],
        ).fetchall()
        recorded = conn.execute(
            "SELECT run_id, recorded_at, table_name FROM master_underived WHERE run_id = ? "
            "ORDER BY table_name",
            [run_id],
        ).fetchall()
    assert (source, status, rows) == ("edgar", RETRACTED, 2)
    assert status != "ok" and mode == RETRACT
    assert message == result.summary()
    assert recorded == [(run_id, RETRACT_AT, "listings"), (run_id, RETRACT_AT, "securities")]


def test_the_apply_refuses_another_count_and_writes_nothing(store: Settings) -> None:
    with pytest.raises(RetractRefused, match="found 2 rows, not the expected 1"):
        master_retract(store, filings=_filings(), clock=lambda: RETRACT_AT, dry_run=False, expect=1)
    assert FALSE_ID in _listed(store, AFTER)
    with pytest.raises(ValueError, match="dry run"):
        master_retract(store, filings=_filings(), clock=lambda: RETRACT_AT, dry_run=False)


def test_a_second_run_finds_nothing(store: Settings) -> None:
    master_retract(store, filings=_filings(), clock=lambda: RETRACT_AT, dry_run=False, expect=2)
    again = master_retract(store, filings=_filings(), clock=lambda: AFTER)
    assert again.found == ()


def test_a_failed_fetch_pass_refuses_and_judges_nothing(store: Settings) -> None:
    class Broken(FixtureFilingSource):
        def cover_pages(self, cik: str) -> list[CoverPage]:
            raise RuntimeError("cover pages down")

    with pytest.raises(RetractRefused, match=r"fetch pass failed.*cover pages down"):
        master_retract(store, filings=_filings(Broken), clock=lambda: CHECK)


def test_a_source_that_derives_no_security_refuses(store: Settings) -> None:
    """An empty answer would call every stored row underived."""
    with pytest.raises(RetractRefused, match="derives no security"):
        master_retract(store, filings=FixtureFilingSource(), clock=lambda: CHECK)


def test_an_edgar_ingest_records_the_underived_rows_for_health(store: Settings) -> None:
    assert _rule(store, CHECK) == []  # the first ingest ran before the false rows
    _ingest(store, CHECK)
    found = _rule(store, AFTER)
    assert [(r["table"], r["security_id"]) for r in found] == [
        ("listings", FALSE_ID),
        ("securities", FALSE_ID),
    ]
    assert found[0]["known_at"] == FALSE_KNOWN
    assert _rule(store, INGEST) == []  # a check is visible only once finished
    master_retract(store, filings=_filings(), clock=lambda: RETRACT_AT, dry_run=False, expect=2)
    assert _rule(store, AFTER) == []
    assert len(_rule(store, CHECK.replace(hour=3))) == 2  # health at T before the apply


def test_a_later_ingest_keeps_a_retracted_row_retracted(store: Settings) -> None:
    master_retract(store, filings=_filings(), clock=lambda: RETRACT_AT, dry_run=False, expect=2)
    _ingest(store, AFTER)
    assert FALSE_ID not in _listed(store, AFTER.replace(hour=5))
    assert _rule(store, AFTER.replace(hour=5)) == []


def test_underived_as_of_is_empty_without_a_check(settings: Settings) -> None:
    with open_for_write(settings) as conn:
        init_schema(conn)
        assert underived_as_of(conn, AFTER).is_empty()


def test_the_command_dry_run_then_apply(store: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "EdgarFilingSource", lambda *args, **kwargs: _filings())
    agent = store.model_copy(update={"sec_edgar_user_agent": SecretStr("Test test@example.com")})
    app = cli.make_app(settings=lambda: agent, clock=lambda: RETRACT_AT)
    runner = CliRunner()
    dry = runner.invoke(app, ["master-retract"])
    assert dry.exit_code == 0, dry.output
    assert dry.output.splitlines()[0].startswith("would retract 2 rows")
    assert FALSE_ID in dry.output
    refused = runner.invoke(app, ["master-retract", "--apply"])
    assert refused.exit_code == cli.USAGE_ERROR
    blank = cli.make_app(settings=lambda: store, clock=lambda: RETRACT_AT)
    assert runner.invoke(blank, ["master-retract"]).exit_code == cli.USAGE_ERROR  # no agent
    lonely = runner.invoke(app, ["master-retract", "--expect-rows", "2"])
    assert lonely.exit_code == cli.USAGE_ERROR
    wrong = runner.invoke(app, ["master-retract", "--apply", "--expect-rows", "3"])
    assert wrong.exit_code == 1
    assert "retract refused" in wrong.output
    done = runner.invoke(app, ["master-retract", "--apply", "--expect-rows", "2"])
    assert done.exit_code == 0, done.output
    assert done.output.startswith("retracted 2 rows")
    assert FALSE_ID not in _listed(store, AFTER)
