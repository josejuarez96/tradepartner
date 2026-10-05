"""Tests for `tradepartner master-retract` (#859): finding the stored master
rows the current rules no longer derive and retracting them as point-in-time
revisions, recorded for health.

A fixture EDGAR source derives one class, ACME on NYSE. A false successor
(`<cik>@<date>`, as #826 wrote WillScot's) is stored by hand, as an earlier
rule would have written it, with a listing under the same ticker."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, ClassVar

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
from tradepartner.health import UNDERIVED_MASTER_ROWS, integrity_checks, last_ingests
from tradepartner.ingest import ingest_session
from tradepartner.retract import RetractRefused, RetractResult, master_retract
from tradepartner.store.asof import listings_as_of
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.master import securities_as_of
from tradepartner.store.retraction import RETRACT, RETRACTED, Underived, underived_as_of
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


def _apply(settings: Settings, at: datetime, **kwargs: Any) -> RetractResult:
    """A dry run, then an apply with its count and digest, both at `at`."""
    dry = master_retract(settings, filings=_filings(), clock=lambda: at)
    return master_retract(
        settings,
        filings=_filings(),
        clock=lambda: at,
        dry_run=False,
        expect=(dry.rows, dry.digest),
        **kwargs,
    )


def test_a_dry_run_lists_every_underived_row_and_changes_nothing(store: Settings) -> None:
    result = master_retract(store, filings=_filings(), clock=lambda: CHECK)
    assert [(u.table, u.key) for u in result.found] == [
        ("securities", (FALSE_ID,)),
        ("listings", (FALSE_ID, "ACME", "NYSE", date(2019, 5, 1))),
    ]
    assert result.summary().startswith(
        f"would retract 2 rows (1 securities, 1 listings), digest {result.digest}, at "
    )
    assert len(result.digest) == 12
    assert result.lines()[0] == f"securities: {FALSE_ID} (known {FALSE_KNOWN.isoformat()})"
    assert FALSE_ID in _listed(store, AFTER)
    with _read(store) as conn:
        assert conn.execute("SELECT count(*) FROM ingestion_runs").fetchone() == (1,)


def test_the_apply_retracts_at_its_clock_and_reads_before_it_are_unchanged(
    store: Settings,
) -> None:
    """No look-ahead: an as-of read before the correction still sees the row."""
    _apply(store, RETRACT_AT)
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
    result = _apply(store, RETRACT_AT)
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


def test_the_apply_refuses_another_set_and_writes_nothing(store: Settings) -> None:
    dry = master_retract(store, filings=_filings(), clock=lambda: CHECK)
    for expect in ((1, dry.digest), (2, "000000000000")):
        with pytest.raises(RetractRefused, match="found 2 rows"):
            master_retract(
                store, filings=_filings(), clock=lambda: RETRACT_AT, dry_run=False, expect=expect
            )
    assert FALSE_ID in _listed(store, AFTER)
    with pytest.raises(ValueError, match="dry run"):
        master_retract(store, filings=_filings(), clock=lambda: RETRACT_AT, dry_run=False)


def test_the_digest_names_the_set_not_the_count() -> None:
    def result(sid: str) -> RetractResult:
        row = {"security_id": sid, "known_at": FALSE_KNOWN}
        return RetractResult(found=(Underived("securities", row),), at=CHECK, dry_run=True)

    assert result("A").rows == result("B").rows
    assert result("A").digest != result("B").digest


def test_a_traded_name_is_marked_and_refused_without_the_flag(store: Settings) -> None:
    with open_for_write(store) as conn:
        conn.execute(
            "INSERT INTO orders (client_order_id, decision_id, run_id, session, attempt, phase, "
            "security_id, symbol, side, notional, quantity, sells_in_flight_at_submit, "
            "known_at, ingested_at) VALUES ('o1', 1, 1, DATE '2019-06-03', 1, 'buy', ?, "
            "'ACME', 'buy', 100.0, NULL, FALSE, ?, ?)",
            [FALSE_ID, FALSE_KNOWN, FALSE_KNOWN],
        )
    dry = master_retract(store, filings=_filings(), clock=lambda: CHECK)
    assert all(line.endswith(" [traded]") for line in dry.lines())
    with pytest.raises(RetractRefused, match="traded securities"):
        _apply(store, RETRACT_AT)
    assert FALSE_ID in _listed(store, AFTER)
    _apply(store, RETRACT_AT, allow_traded=True)
    assert FALSE_ID not in _listed(store, AFTER)


def test_a_spinoff_receipt_with_no_order_is_traded(store: Settings) -> None:
    """Safety review of #872, pass 2: a spin-off child arrives as an
    adjustment, never an order, yet a window holds it."""
    with open_for_write(store) as conn:
        conn.execute(
            "INSERT INTO adjustments (adjustment_id, window_id, run_id, session, kind, origin, "
            "security_id, quantity, cash, explanation_json, known_at, ingested_at) VALUES "
            "(1, 1, NULL, DATE '2019-06-03', 'spinoff_receipt', NULL, ?, 10.0, NULL, NULL, ?, ?)",
            [FALSE_ID, FALSE_KNOWN, FALSE_KNOWN],
        )
    dry = master_retract(store, filings=_filings(), clock=lambda: CHECK)
    assert dry.traded == frozenset({FALSE_ID})
    with pytest.raises(RetractRefused, match="traded securities"):
        _apply(store, RETRACT_AT)


def test_a_snapshot_row_of_a_name_gone_from_the_snapshot_is_never_judged(
    store: Settings,
) -> None:
    """Quant audit of #872: today's companies snapshot lists only names
    trading today, so a delisted name's snapshot listing is not underived."""
    gone = {
        "security_id": "0000000007",
        "ticker": "GONE",
        "exchange": "NYSE",
        "class_title": None,
        "valid_from": date(2010, 3, 1),
        "known_at": FALSE_KNOWN,
        "ingested_at": FALSE_KNOWN,
        "source": "edgar",
        "provenance": "snapshot_static",
    }
    with open_for_write(store) as conn:
        insert_row(conn, "listings", gone)
    found = master_retract(store, filings=_filings(), clock=lambda: CHECK).found
    assert {u.row["security_id"] for u in found} == {FALSE_ID}


def test_a_cik_with_a_failed_or_quarantined_filing_is_not_judged(store: Settings) -> None:
    class Failing(FixtureFilingSource):
        """As the EDGAR adapter: one of ACME's filings failed this run."""

        _pending_failures: ClassVar[dict[str, tuple[str, str, str]]] = {
            f"{ACME}-18-1": ("ParseError", "10-K", "bad cover")
        }

    result = master_retract(store, filings=_filings(Failing), clock=lambda: CHECK)
    assert result.found == ()
    assert result.unjudged == frozenset({ACME})
    assert f"not judged (filings failed or quarantined): 1 CIKs: {ACME}" in result.summary()

    class Unnamed(FixtureFilingSource):
        _fsn_extraction_failures_this_run: ClassVar[int] = 1

    with pytest.raises(RetractRefused, match="name no CIK"):
        master_retract(store, filings=_filings(Unnamed), clock=lambda: CHECK)


def test_the_retraction_is_stamped_after_the_lock(store: Settings) -> None:
    """The clock is read again once the store is open: the stamp is never
    earlier than a revision committed while the command waited."""
    ticks = iter([CHECK, CHECK, RETRACT_AT, AFTER])
    dry = master_retract(store, filings=_filings(), clock=lambda: CHECK)
    result = master_retract(
        store,
        filings=_filings(),
        clock=lambda: next(ticks),
        dry_run=False,
        expect=(dry.rows, dry.digest),
    )
    assert result.at == RETRACT_AT
    assert FALSE_ID in _listed(store, RETRACT_AT.replace(minute=59, hour=1))
    assert FALSE_ID not in _listed(store, RETRACT_AT)


def test_a_retract_run_never_hides_the_latest_edgar_ingest(store: Settings) -> None:
    _apply(store, RETRACT_AT)
    with _read(store) as conn:
        [edgar] = [s for s in last_ingests(conn, AFTER) if s.source == "edgar"]
    assert edgar.latest_status == "ok"


def test_a_second_run_finds_nothing(store: Settings) -> None:
    _apply(store, RETRACT_AT)
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
    _apply(store, RETRACT_AT)
    assert _rule(store, AFTER) == []
    assert len(_rule(store, CHECK.replace(hour=3))) == 2  # health at T before the apply


def test_a_later_ingest_keeps_a_retracted_row_retracted(store: Settings) -> None:
    _apply(store, RETRACT_AT)
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
    for alone in (["--expect-rows", "2"], ["--allow-traded"]):
        assert runner.invoke(app, ["master-retract", *alone]).exit_code == cli.USAGE_ERROR
    digest = dry.output.split("digest ")[1].split(",")[0]
    wrong = runner.invoke(
        app, ["master-retract", "--apply", "--expect-rows", "3", "--expect-digest", digest]
    )
    assert wrong.exit_code == 1
    assert "retract refused" in wrong.output
    done = runner.invoke(
        app, ["master-retract", "--apply", "--expect-rows", "2", "--expect-digest", digest]
    )
    assert done.exit_code == 0, done.output
    assert done.output.startswith("retracted 2 rows")
    assert FALSE_ID not in _listed(store, AFTER)


# --- #922: `master.keep_successors`, an owner-accepted successor is kept ---

OTHER = "0000000002"
OTHER_ID = f"{OTHER}@2019-05-01"  # a second false successor, not kept


def _keep(settings: Settings, *ids: str) -> Settings:
    return settings.model_copy(
        update={"master": settings.master.model_copy(update={"keep_successors": list(ids)})}
    )


@pytest.fixture
def two(store: Settings) -> Settings:
    """`store` plus a second false successor of another CIK."""
    security, listing = _false_rows()
    with open_for_write(store) as conn:
        insert_row(conn, "securities", security | {"security_id": OTHER_ID, "cik": OTHER})
        insert_row(conn, "listings", listing | {"security_id": OTHER_ID, "ticker": "OTHR"})
    return store


def test_a_kept_successor_is_never_proposed_and_the_set_and_digest_change(two: Settings) -> None:
    every = master_retract(two, filings=_filings(), clock=lambda: CHECK)
    assert every.rows == 4
    kept = master_retract(_keep(two, FALSE_ID), filings=_filings(), clock=lambda: CHECK)
    assert {u.row["security_id"] for u in kept.found} == {OTHER_ID}
    assert kept.rows == 2
    assert kept.digest != every.digest
    only_other = RetractResult(
        found=tuple(u for u in every.found if u.row["security_id"] == OTHER_ID),
        at=CHECK,
        dry_run=True,
    )
    assert kept.digest == only_other.digest  # the digest of exactly the proposed set
    assert {u.row["security_id"] for u in kept.kept} == {FALSE_ID}
    assert len(kept.kept) == 2  # its securities row and its listing


def test_the_dry_run_reports_a_kept_row_with_the_reason(two: Settings) -> None:
    result = master_retract(_keep(two, FALSE_ID), filings=_filings(), clock=lambda: CHECK)
    assert result.summary().startswith("would retract 2 rows (1 securities, 1 listings), digest")
    assert "; kept 2 rows on master.keep_successors (owner-accepted): " + FALSE_ID in (
        result.summary()
    )
    kept_lines = [line for line in result.lines() if FALSE_ID in line]
    assert kept_lines == [
        f"securities: {FALSE_ID} (known {FALSE_KNOWN.isoformat()}) [kept: master.keep_successors]",
        f"listings: {FALSE_ID} ACME NYSE 2019-05-01 (known {FALSE_KNOWN.isoformat()}) "
        "[kept: master.keep_successors]",
    ]
    assert not any(OTHER_ID in line and "[kept" in line for line in result.lines())


def test_a_keep_id_with_no_underived_row_is_named(two: Settings) -> None:
    """A typo, or a successor the rules derive again, keeps nothing: say so."""
    absent = f"{ACME}@2001-01-02"
    result = master_retract(_keep(two, absent), filings=_filings(), clock=lambda: CHECK)
    assert result.rows == 4
    assert result.kept == ()
    assert f"master.keep_successors with no underived row: {absent}" in result.summary()


def test_the_apply_leaves_a_kept_successor_live_and_health_passes(two: Settings) -> None:
    keep = _keep(two, FALSE_ID)
    _ingest(keep, CHECK)
    assert {r["security_id"] for r in _rule(keep, CHECK.replace(hour=3))} == {OTHER_ID}
    result = _apply(keep, RETRACT_AT)
    assert result.rows == 2
    assert FALSE_ID in _listed(keep, AFTER) and FALSE_ID in _known(keep, AFTER)
    assert OTHER_ID not in _listed(keep, AFTER)
    with _read(keep) as conn:
        recorded = conn.execute(
            "SELECT DISTINCT security_id FROM master_underived u JOIN ingestion_runs r "
            "USING (run_id) WHERE r.mode = ?",
            [RETRACT],
        ).fetchall()
    assert recorded == [(OTHER_ID,)]
    assert _rule(keep, AFTER) == []
    assert master_retract(keep, filings=_filings(), clock=lambda: AFTER).found == ()


def test_a_keep_list_never_rewrites_an_earlier_check(two: Settings) -> None:
    """Point in time: health at T reads the check finished by T as it was
    recorded; a keep list set later applies from the next check on."""
    _ingest(two, CHECK)  # no keep list yet: all four rows recorded
    before = _rule(two, CHECK.replace(hour=3))
    assert {r["security_id"] for r in before} == {FALSE_ID, OTHER_ID}
    keep = _keep(two, FALSE_ID)
    assert _rule(keep, CHECK.replace(hour=3)) == before
    _ingest(keep, AFTER)
    assert {r["security_id"] for r in _rule(keep, AFTER.replace(hour=5))} == {OTHER_ID}
    assert _rule(keep, CHECK.replace(hour=3)) == before
