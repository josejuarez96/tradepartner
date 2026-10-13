"""Tests for tradepartner.repair (#819): removing the Alpaca rows the
resolver no longer assigns to their security (a reused ticker's bars on a
delisted security), in one recorded transaction."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tradepartner import cli, repair
from tradepartner.calendar import previous_session, session_close
from tradepartner.config import Settings
from tradepartner.health import last_ingests
from tradepartner.repair import (
    REPAIR,
    REPAIRED,
    RepairRefused,
    misattributed,
    repair_resolution,
    store_resolver,
)
from tradepartner.store import registry
from tradepartner.store.asof import adjusted_prices_as_of, prices_as_of
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.dividend_reviews import ReviewedGroup
from tradepartner.store.retraction import retraction
from tradepartner.store.schema import init_schema

RUN = datetime(2026, 10, 4, 18, tzinfo=UTC)
FILED = datetime(2021, 5, 21, 20, 30, tzinfo=UTC)
EFFECTIVE = date(2021, 5, 31)
DEAD, LIVE = "0000000001", "0000000002"
#: The data release every real run in the `store` fixture runs inside (T140c).
REL = "repair-test"


def _open_release(settings: Settings, name: str = REL) -> None:
    """Open the data release `name` on the store, the store itself as its backup."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        registry.open_data_release(conn, conn, name=name, backup_path="b.duckdb", reason="#1319")


def _close_release(settings: Settings, name: str = REL) -> None:
    with open_for_write(settings) as conn:
        registry.close_data_release(
            conn, name=name, sessions_from=date(2021, 6, 1), sessions_to=date(2021, 6, 2)
        )


def _listing(sid: str, ticker: str, valid_from: date) -> dict[str, Any]:
    return {
        "security_id": sid,
        "ticker": ticker,
        "exchange": "NYSE",
        "class_title": "Common Stock",
        "valid_from": valid_from,
        "known_at": datetime(valid_from.year, valid_from.month, valid_from.day, 21, tzinfo=UTC),
        "ingested_at": RUN,
        "source": "edgar",
        "provenance": "filing",
    }


def _bar(
    sid: str,
    session: date,
    *,
    close: float = 10.0,
    known: datetime | None = None,
    source: str = "alpaca_sip",
) -> dict[str, Any]:
    known = known or session_close(session)
    return {
        "security_id": sid,
        "session": session,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000,
        "known_at": known,
        "ingested_at": RUN,
        "source": source,
        "provenance": "bar",
    }


def _dividend(sid: str, ex_date: date) -> dict[str, Any]:
    known = session_close(previous_session(ex_date))
    return {
        "security_id": sid,
        "action_type": "dividend",
        "ex_date": ex_date,
        "ratio_or_amount": 0.25,
        "announced_at": None,
        "source_action_id": f"id-{sid}-{ex_date}",
        "cancelled": False,
        "known_at": known,
        "ingested_at": RUN,
        "source": "alpaca",
        "provenance": "action",
    }


@pytest.fixture
def store(settings: Settings) -> Settings:
    """DEAD lists GONE from 2019 and is delisted from 2021-05-31; another
    equity's GONE bars and dividend sit on it after that day. LIVE trades."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        for row in (
            _listing(DEAD, "GONE", date(2019, 1, 2)),
            _listing(LIVE, "LIVE", date(2019, 1, 2)),
        ):
            insert_row(conn, "listings", row)
        insert_row(
            conn,
            "delistings",
            {
                "security_id": DEAD,
                "form": "25-NSE",
                "class_title": "Common Stock",
                "exchange": "NYSE",
                "filed_at": FILED,
                "effective_on": EFFECTIVE,
                "known_at": FILED,
                "ingested_at": RUN,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        for row in (
            _bar(DEAD, date(2021, 5, 27)),
            _bar(DEAD, date(2021, 6, 1), close=25.0),
            _bar(DEAD, date(2021, 6, 1), close=26.0, known=datetime(2021, 6, 3, tzinfo=UTC)),
            _bar(DEAD, date(2021, 6, 2), close=25.5),
            _bar(DEAD, date(2021, 6, 3), source="fixture"),  # another source: never touched
            _bar(LIVE, date(2021, 6, 1)),
        ):
            insert_row(conn, "prices_daily", row)
        insert_row(conn, "corporate_actions", _dividend(DEAD, date(2021, 5, 20)))
        insert_row(conn, "corporate_actions", _dividend(DEAD, date(2021, 6, 15)))
    _open_release(settings)
    return settings


def _rows(settings: Settings, sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(sql).fetchall()


def _bars(settings: Settings) -> list[tuple[Any, ...]]:
    return _rows(
        settings,
        "SELECT security_id, session, close, source FROM prices_daily "
        "ORDER BY security_id, session, known_at",
    )


def test_a_dry_run_counts_and_changes_nothing(store: Settings) -> None:
    before = _bars(store)
    result = repair_resolution(store, clock=lambda: RUN, dry_run=True)
    assert (result.bar_rows, result.action_rows) == (3, 1)
    assert sorted(result.found.bars) == [(DEAD, date(2021, 6, 1)), (DEAD, date(2021, 6, 2))]
    assert (result.bar_keys_checked, result.action_keys_checked) == (4, 2)
    assert result.summary().startswith("would delete 3 bar rows (2 of 4 keys) and 1 action rows")
    assert _bars(store) == before
    assert _rows(store, "SELECT count(*) FROM ingestion_runs") == [(0,)]


def test_the_repair_deletes_every_revision_of_a_misattributed_row(store: Settings) -> None:
    result = repair_resolution(store, clock=lambda: RUN, expect=(3, 1), release=REL)
    assert (result.bar_rows, result.action_rows) == (3, 1)
    assert _bars(store) == [
        (DEAD, date(2021, 5, 27), 10.0, "alpaca_sip"),
        (DEAD, date(2021, 6, 3), 10.0, "fixture"),
        (LIVE, date(2021, 6, 1), 10.0, "alpaca_sip"),
    ]
    assert _rows(store, "SELECT security_id, ex_date FROM corporate_actions") == [
        (DEAD, date(2021, 5, 20))
    ]


def test_the_repair_is_recorded_and_never_reads_as_a_fresh_ingest(store: Settings) -> None:
    result = repair_resolution(store, clock=lambda: RUN, expect=(3, 1), release=REL)
    [(source, status, mode, message)] = _rows(
        store, "SELECT source, status, mode, message FROM ingestion_runs"
    )
    assert (source, status, mode) == ("alpaca", REPAIRED, REPAIR)
    assert status != "ok"  # execution and health read only ok runs as fresh
    assert message == f"{result.summary()}; release {REL}"
    assert f"on 1 securities: {DEAD}" in message


def test_a_second_repair_deletes_nothing(store: Settings) -> None:
    repair_resolution(store, clock=lambda: RUN, expect=(3, 1), release=REL)
    again = repair_resolution(store, clock=lambda: RUN, expect=(0, 0), release=REL)
    assert (again.bar_rows, again.action_rows) == (0, 0)
    assert again.found.securities == ()


def test_the_verdict_is_the_ingest_resolvers(store: Settings) -> None:
    with duckdb.connect(store.store.path, read_only=True) as conn:
        resolver = store_resolver(conn, RUN, store)
    assert resolver.resolve("GONE", date(2021, 5, 28)) == DEAD
    assert resolver.resolve("GONE", EFFECTIVE) is None
    found = misattributed(
        resolver,
        [(DEAD, date(2021, 5, 28), 1), (DEAD, date(2021, 6, 1), 2), (LIVE, date(2021, 6, 1), 1)],
        [(DEAD, date(2021, 6, 1), 1)],  # resolves on 2021-05-28, before the effective day
    )
    assert dict(found.bars) == {(DEAD, date(2021, 6, 1)): 2}
    assert dict(found.actions) == {}


def test_the_command_dry_run_and_repair(store: Settings) -> None:
    app = cli.make_app(settings=lambda: store, clock=lambda: RUN)
    runner = CliRunner()
    dry = runner.invoke(app, ["repair-resolution", "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert dry.output.startswith("would delete 3 bar rows")
    assert f"  {DEAD}: 3 bar rows 2021-06-01..2021-06-02, 1 action rows" in dry.output
    unguarded = runner.invoke(app, ["repair-resolution"])
    assert unguarded.exit_code != 0
    assert len(_bars(store)) == 6
    stale = [
        "repair-resolution",
        *("--expect-bar-rows", "2", "--expect-action-rows", "1", "--release", REL),
    ]
    refused = runner.invoke(app, stale)
    assert refused.exit_code == 1
    assert "repair refused" in refused.output
    assert len(_bars(store)) == 6
    expect = ["--expect-bar-rows", "3", "--expect-action-rows", "1"]
    unreleased = runner.invoke(app, ["repair-resolution", *expect])
    assert unreleased.exit_code == cli.USAGE_ERROR
    assert "--release" in unreleased.output
    other = runner.invoke(app, ["repair-resolution", *expect, "--release", "other"])
    assert other.exit_code == 1
    assert f"no open data release named 'other' (release {REL!r} is open)" in other.output
    assert len(_bars(store)) == 6
    done = runner.invoke(app, ["repair-resolution", *expect, "--release", REL])
    assert done.exit_code == 0, done.output
    assert done.output.startswith("deleted 3 bar rows")
    assert len(_bars(store)) == 3


def test_the_command_refuses_a_missing_store(settings: Settings) -> None:
    app = cli.make_app(settings=lambda: settings, clock=lambda: RUN)
    result = CliRunner().invoke(
        app,
        [
            "repair-resolution",
            *("--expect-bar-rows", "0", "--expect-action-rows", "0", "--release", REL),
        ],
    )
    assert result.exit_code == 1
    assert "no store" in result.output


def test_a_repair_needs_the_dry_runs_counts(store: Settings) -> None:
    before = _bars(store)
    with pytest.raises(ValueError, match="dry run"):
        repair_resolution(store, clock=lambda: RUN)
    with pytest.raises(RepairRefused):
        repair_resolution(store, clock=lambda: RUN, expect=(3, 0), release=REL)
    assert _bars(store) == before
    assert _rows(store, "SELECT count(*) FROM corporate_actions") == [(2,)]
    assert _rows(store, "SELECT count(*) FROM ingestion_runs") == [(0,)]


def test_a_delete_that_misses_its_count_rolls_everything_back(
    store: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # safety-reviewer on #830: never a partial repair.
    real = repair._delete

    def short(*args: Any, **kwargs: Any) -> int:
        return real(*args, **kwargs) - 1

    monkeypatch.setattr(repair, "_delete", short)
    before = _bars(store)
    with pytest.raises(RuntimeError, match="rolled back"):
        repair_resolution(store, clock=lambda: RUN, expect=(3, 1), release=REL)
    assert _bars(store) == before
    assert _rows(store, "SELECT count(*) FROM corporate_actions") == [(2,)]
    assert _rows(store, "SELECT count(*) FROM ingestion_runs") == [(0,)]


# --- T140c: every real repair runs inside an open data release ---


def test_a_repair_writes_only_inside_the_open_release_of_its_name(store: Settings) -> None:
    before = _bars(store)
    with pytest.raises(ValueError, match="named data release"):
        repair_resolution(store, clock=lambda: RUN, expect=(3, 1))
    with pytest.raises(RepairRefused, match=f"'other' \\(release {REL!r} is open\\)"):
        repair_resolution(store, clock=lambda: RUN, expect=(3, 1), release="other")
    _close_release(store)
    with pytest.raises(RepairRefused, match="no release is open"):
        repair_resolution(store, clock=lambda: RUN, expect=(3, 1), release=REL)
    assert _bars(store) == before
    assert _rows(store, "SELECT count(*) FROM corporate_actions") == [(2,)]
    assert _rows(store, "SELECT count(*) FROM ingestion_runs") == [(0,)]
    # A dry run needs no release.
    assert repair_resolution(store, clock=lambda: RUN, dry_run=True).bar_rows == 3


JUNE = (date(2021, 6, 1), date(2021, 6, 15))


def _delete_bars(settings: Settings, **kwargs: Any) -> repair.BarsDeleted:
    first, last = JUNE
    args: dict[str, Any] = {"security_id": DEAD, "sessions_from": first, "sessions_to": last}
    return repair.delete_bars(settings, clock=lambda: RUN, **(args | kwargs))


def test_delete_bars_dry_run_counts_every_revision_and_changes_nothing(store: Settings) -> None:
    before = _bars(store)
    result = _delete_bars(store, dry_run=True)
    assert (result.bar_rows, result.action_rows) == (3, 1)
    assert result.bar_sessions == (date(2021, 6, 1), date(2021, 6, 2))
    assert result.ex_dates == (date(2021, 6, 15),)
    assert result.summary() == (
        f"would delete 3 bar rows on 2 sessions 2021-06-01..2021-06-02 and 1 action rows "
        f"(ex-dates 2021-06-15) of {DEAD} in 2021-06-01..2021-06-15"
    )
    assert _bars(store) == before
    assert _rows(store, "SELECT count(*) FROM ingestion_runs") == [(0,)]


def test_delete_bars_deletes_the_range_every_revision_and_records_the_run(
    store: Settings,
) -> None:
    result = _delete_bars(store, expect=(3, 1), release=REL)
    assert _bars(store) == [
        (DEAD, date(2021, 5, 27), 10.0, "alpaca_sip"),  # before the range
        (DEAD, date(2021, 6, 3), 10.0, "fixture"),  # another source
        (LIVE, date(2021, 6, 1), 10.0, "alpaca_sip"),  # another security
    ]
    assert _rows(store, "SELECT security_id, ex_date FROM corporate_actions") == [
        (DEAD, date(2021, 5, 20))
    ]
    [(source, status, mode, rows, message)] = _rows(
        store, "SELECT source, status, mode, rows_added, message FROM ingestion_runs"
    )
    assert (source, status, mode, rows) == ("alpaca", REPAIRED, REPAIR, 0)
    assert message == f"{result.summary()}; release {REL}"
    assert message.startswith("deleted 3 bar rows")


def test_delete_bars_refuses_and_deletes_nothing(store: Settings) -> None:
    before = _bars(store)
    with pytest.raises(ValueError, match="dry run"):
        _delete_bars(store, release=REL)
    with pytest.raises(ValueError, match="named data release"):
        _delete_bars(store, expect=(3, 1))
    with pytest.raises(ValueError, match="not an XNYS session"):
        _delete_bars(store, sessions_to=date(2021, 6, 5), dry_run=True)  # a Saturday
    with pytest.raises(ValueError, match="is after"):
        _delete_bars(store, sessions_from=date(2021, 6, 15), sessions_to=date(2021, 6, 1))
    with pytest.raises(RepairRefused, match="not the expected"):
        _delete_bars(store, expect=(2, 1), release=REL)
    with pytest.raises(RepairRefused, match="no security"):
        _delete_bars(store, security_id="0000009999", expect=(0, 0), release=REL)
    with pytest.raises(RepairRefused, match="named 'other'"):
        _delete_bars(store, expect=(3, 1), release="other")
    _close_release(store)
    with pytest.raises(RepairRefused, match="no release is open"):
        _delete_bars(store, expect=(3, 1), release=REL)
    assert _bars(store) == before
    assert _rows(store, "SELECT count(*) FROM corporate_actions") == [(2,)]
    assert _rows(store, "SELECT count(*) FROM ingestion_runs") == [(0,)]


def test_delete_bars_that_misses_its_count_rolls_everything_back(
    store: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = repair._delete
    monkeypatch.setattr(repair, "_delete", lambda *a, **k: real(*a, **k) - 1)
    before = _bars(store)
    with pytest.raises(RuntimeError, match="rolled back"):
        _delete_bars(store, expect=(3, 1), release=REL)
    assert _bars(store) == before
    assert _rows(store, "SELECT count(*) FROM corporate_actions") == [(2,)]
    assert _rows(store, "SELECT count(*) FROM ingestion_runs") == [(0,)]


def test_a_delete_is_not_a_revision(store: Settings) -> None:
    """The deleted key is gone at every as-of time, the rest reads as it did,
    and no row is written to `prices_daily`: the look-ahead rules are
    unchanged, nothing new is visible at any T."""
    times = [session_close(date(2021, 6, 1)), datetime(2021, 6, 3, tzinfo=UTC), RUN]

    def read(t: datetime) -> list[tuple[Any, ...]]:
        with duckdb.connect(store.store.path, read_only=True) as conn:
            frame = prices_as_of(conn, t)
        return sorted(frame.select("security_id", "session", "close").iter_rows())

    before = {t: read(t) for t in times}
    assert (DEAD, date(2021, 6, 1), 26.0) in before[RUN]  # the later revision
    _delete_bars(store, expect=(3, 1), release=REL)
    for t in times:
        assert read(t) == [
            row
            for row in before[t]
            if not (row[0] == DEAD and row[1] in {date(2021, 6, 1), date(2021, 6, 2)})
        ]
    stamps = _rows(store, "SELECT DISTINCT ingested_at FROM prices_daily")
    assert stamps == [(RUN,)]  # the fixture's rows only: nothing written


def test_the_repair_bars_command(store: Settings) -> None:
    app = cli.make_app(settings=lambda: store, clock=lambda: RUN)
    runner = CliRunner()
    base = ["repair-bars", "--security", DEAD, "--from", "2021-06-01", "--to", "2021-06-15"]
    dry = runner.invoke(app, [*base, "--release", REL, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert dry.output.startswith("would delete 3 bar rows on 2 sessions")
    assert runner.invoke(app, [*base, "--dry-run"]).exit_code == cli.USAGE_ERROR  # no release
    expect = ["--expect-bar-rows", "3", "--expect-action-rows", "1"]
    assert runner.invoke(app, [*base, "--release", REL]).exit_code == cli.USAGE_ERROR
    bad_day = runner.invoke(app, [*base[:-1], "2021-06-05", "--release", REL, *expect])
    assert bad_day.exit_code == cli.USAGE_ERROR
    assert "not an XNYS session" in bad_day.output
    other = runner.invoke(app, [*base, "--release", "other", *expect])
    assert other.exit_code == 1
    assert "repair refused: no open data release named 'other'" in other.output
    assert len(_bars(store)) == 6
    done = runner.invoke(app, [*base, "--release", REL, *expect])
    assert done.exit_code == 0, done.output
    assert done.output.startswith("deleted 3 bar rows")
    assert len(_bars(store)) == 3


VALARIS, VALARIS_NEW = "0000314808", "0000314808:common-shares"


def _titled(sid: str, ticker: str, valid_from: date, class_title: str) -> dict[str, Any]:
    return {**_listing(sid, ticker, valid_from), "class_title": class_title}


@pytest.fixture
def valaris(settings: Settings) -> Settings:
    """#921, the owner's store's facts: Valaris's Class A lists VAL from
    2019-08-01; a 25-NSE filed 2020-09-04 delists it from 2020-09-14 (last
    bar 2020-08-14); a Chapter 11 10-Q on 2020-10-29 writes `VAL*`; the
    post-bankruptcy Common Shares list VAL from 2021-08-03 under their own
    class id, with their own bars."""
    class_a = "Class A Ordinary Shares"
    with open_for_write(settings) as conn:
        init_schema(conn)
        for row in (
            _titled(VALARIS, "VAL", date(2019, 8, 1), class_a),
            _titled(VALARIS, "VAL*", date(2020, 10, 29), class_a),
            _titled(VALARIS_NEW, "VAL", date(2021, 8, 3), "Common Shares"),
        ):
            insert_row(conn, "listings", row)
        filed = datetime(2020, 9, 4, 20, tzinfo=UTC)
        insert_row(
            conn,
            "delistings",
            {
                "security_id": VALARIS,
                "form": "25-NSE",
                "class_title": class_a,
                "exchange": "NYSE",
                "filed_at": filed,
                "effective_on": date(2020, 9, 14),
                "known_at": filed,
                "ingested_at": RUN,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        for sid, session in (
            (VALARIS, date(2019, 8, 1)),
            (VALARIS, date(2020, 8, 14)),
            (VALARIS_NEW, date(2021, 8, 3)),
            (VALARIS_NEW, date(2024, 3, 1)),
            (VALARIS_NEW, date(2026, 10, 2)),
        ):
            insert_row(conn, "prices_daily", _bar(sid, session))
    return settings


def test_a_footnoted_row_after_a_form_25_leaves_both_valaris_lines_alone(
    valaris: Settings,
) -> None:
    # #921 acceptance: the post-bankruptcy bars stay on the new class and the
    # pre-bankruptcy bars on the old one; the repair proposes nothing.
    result = repair_resolution(valaris, clock=lambda: RUN, dry_run=True)
    assert (result.bar_rows, result.action_rows) == (0, 0)
    assert result.found.securities == ()
    with duckdb.connect(valaris.store.path, read_only=True) as conn:
        resolver = store_resolver(conn, RUN, valaris)
    assert resolver.resolve("VAL", date(2020, 8, 14)) == VALARIS
    assert resolver.resolve("VAL", date(2020, 12, 1)) is None
    for session in (date(2021, 8, 3), date(2024, 3, 1), date(2026, 10, 2)):
        assert resolver.resolve("VAL", session) == VALARIS_NEW, session


MIMEDX = "0001376339"


@pytest.fixture
def mimedx(settings: Settings) -> Settings:
    """#943, the issue's facts: MiMedx lists MDXG; Nasdaq delists it from
    2019-03-08 (Form 25 filed 2019-02-26, last Nasdaq bar 2018-11-07), it
    trades OTC with no Alpaca bars and relists in November 2020 under the
    same id and ticker, with bars to 2026."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        for row in (
            _listing(MIMEDX, "MDXG", date(2016, 3, 1)),
            _listing(MIMEDX, "MDXG", date(2020, 11, 4)),
        ):
            insert_row(conn, "listings", row)
        filed = datetime(2019, 2, 26, 21, tzinfo=UTC)
        insert_row(
            conn,
            "delistings",
            {
                "security_id": MIMEDX,
                "form": "25-NSE",
                "class_title": "Common Stock",
                "exchange": "NYSE",
                "filed_at": filed,
                "effective_on": date(2019, 3, 8),
                "known_at": filed,
                "ingested_at": RUN,
                "source": "edgar",
                "provenance": "filing",
            },
        )
        for session in (date(2016, 3, 1), date(2018, 11, 7), date(2020, 11, 4), date(2026, 10, 2)):
            insert_row(conn, "prices_daily", _bar(MIMEDX, session))
    return settings


def test_the_long_gap_relisting_loses_its_later_bars_by_default(mimedx: Settings) -> None:
    # #943: the #847 accepted cost, as the owner's dry run shows it.
    result = repair_resolution(mimedx, clock=lambda: RUN, dry_run=True)
    assert result.found.securities == (MIMEDX,)
    assert sorted(day for _, day in result.found.bars) == [date(2020, 11, 4), date(2026, 10, 2)]


def test_an_accepted_relisting_keeps_every_bar(mimedx: Settings) -> None:
    # #943 acceptance: with the owner's `ALPACA__ACCEPTED_RELISTINGS` line
    # the dry run proposes nothing on MiMedx.
    accepted = mimedx.model_copy(
        update={"alpaca": mimedx.alpaca.model_copy(update={"accepted_relistings": [MIMEDX]})}
    )
    result = repair_resolution(accepted, clock=lambda: RUN, dry_run=True)
    assert (result.bar_rows, result.action_rows) == (0, 0)
    assert result.found.securities == ()


SEAGATE = "0001137789"


@pytest.fixture
def seagate(settings: Settings) -> Settings:
    """#943, the owner's store's facts: Seagate's untitled snapshot row
    lists STX from 2002-10-11 and cover pages from 2019-11-01, 2021-06-01
    and 2021-08-06; a 25-NSE filed 2010-07-02 (effective 07-12, the
    Cayman->Ireland move) and one filed 2021-05-18 (effective 05-28, the
    redomicile); bars from 2016-01-04 with a hole from 2016-01-29 to
    2019-10-31, then to 2026."""
    title = "Ordinary Shares, par value $0.00001 per share"
    with open_for_write(settings) as conn:
        init_schema(conn)
        rows = [
            {**_listing(SEAGATE, "STX", date(2002, 10, 11)), "class_title": None},
            *(
                {**_listing(SEAGATE, "STX", day), "class_title": title}
                for day in (date(2019, 11, 1), date(2021, 6, 1), date(2021, 8, 6))
            ),
        ]
        for row in rows:
            insert_row(conn, "listings", {**row, "exchange": "NASDAQ"})
        for filed, effective, cls in (
            (datetime(2010, 7, 2, 20, 8, tzinfo=UTC), date(2010, 7, 12), "Common Stock"),
            (
                datetime(2021, 5, 18, 20, 27, tzinfo=UTC),
                date(2021, 5, 28),
                "Seagate Technology PLC Ordinary Shares",
            ),
        ):
            insert_row(
                conn,
                "delistings",
                {
                    "security_id": SEAGATE,
                    "form": "25-NSE",
                    "class_title": cls,
                    "exchange": "NASDAQ",
                    "filed_at": filed,
                    "effective_on": effective,
                    "known_at": filed,
                    "ingested_at": RUN,
                    "source": "edgar",
                    "provenance": "filing",
                },
            )
        for session in (
            date(2016, 1, 4),
            date(2016, 1, 29),
            date(2019, 11, 1),
            date(2021, 5, 28),
            date(2021, 6, 1),
            date(2026, 10, 2),
        ):
            insert_row(conn, "prices_daily", _bar(SEAGATE, session))
        insert_row(conn, "corporate_actions", _dividend(SEAGATE, date(2024, 3, 26)))
    return settings


def test_a_live_name_with_a_bar_hole_keeps_every_bar(seagate: Settings) -> None:
    # #943 acceptance: the repair proposes nothing on Seagate.
    result = repair_resolution(seagate, clock=lambda: RUN, dry_run=True)
    assert (result.bar_rows, result.action_rows) == (0, 0)
    assert result.found.securities == ()


META, REUSER = "0001326801", "0000000099"
COVER = date(2019, 7, 24)


def _security(sid: str, known: datetime, name: str) -> dict[str, Any]:
    return {
        "security_id": sid,
        "cik": sid,
        "name": name,
        "benchmark": False,
        "known_at": known,
        "ingested_at": RUN,
        "source": "edgar",
        "provenance": "filing",
    }


def _split(sid: str, ex_date: date) -> dict[str, Any]:
    return {**_dividend(sid, ex_date), "action_type": "split", "ratio_or_amount": 2.0}


def _without_lead(settings: Settings) -> Settings:
    alpaca = settings.alpaca.model_copy(update={"first_span_lead": False})
    return settings.model_copy(update={"alpaca": alpaca})


@pytest.fixture
def led(settings: Settings) -> Settings:
    """#974, META-shaped: Facebook's first filing is accepted 2012-02-01 and
    its first cover page naming FB on 2019-07-24; a later securities
    revision (a rename) is stamped at an ingest clock in 2024. Bars from
    2017 and a split ex 2017-06-01 were stored under the first-span lead."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(conn, "securities", _security(META, datetime(2012, 2, 1, 21, tzinfo=UTC), "FB"))
        insert_row(conn, "securities", _security(META, datetime(2024, 1, 5, tzinfo=UTC), "Meta"))
        insert_row(conn, "listings", _listing(META, "FB", COVER))
        for session in (date(2017, 5, 31), date(2018, 7, 2), COVER):
            insert_row(conn, "prices_daily", _bar(META, session))
        insert_row(conn, "corporate_actions", _split(META, date(2017, 6, 1)))
    return settings


def test_the_repair_keeps_lead_bars_and_a_lead_window_split(led: Settings) -> None:
    # The floor is the earliest securities row's session (2012-02-01), not
    # the 2024 revision's, which would leave no lead window at all.
    result = repair_resolution(led, clock=lambda: RUN, dry_run=True)
    assert (result.bar_rows, result.action_rows) == (0, 0)
    with duckdb.connect(led.store.path, read_only=True) as conn:
        resolver = store_resolver(conn, RUN, led)
    assert resolver.lead("FB", date(2012, 2, 1)) == META
    assert resolver.lead("FB", date(2012, 1, 31)) is None
    assert resolver.report.first_span_leads == 1


def test_the_floor_is_the_earliest_securities_row_known_at_the_run(led: Settings) -> None:
    earlier = datetime(2011, 12, 29, 22, tzinfo=UTC)  # its New York day, as `store.master`
    with open_for_write(led) as conn:
        insert_row(conn, "securities", _security(META, earlier, "early") | {"ingested_at": RUN})
    with duckdb.connect(led.store.path, read_only=True) as conn:
        resolver = store_resolver(conn, RUN, led)
    assert resolver.lead("FB", date(2011, 12, 29)) == META
    assert resolver.lead("FB", date(2011, 12, 28)) is None


def test_a_retraction_never_moves_the_first_session(led: Settings) -> None:
    """#1408 (owner decision 5 on #987 item 1): the floor skips retracted rows,
    but `retraction(row, at)` always stamps after the row it withdraws, so on
    rows the code writes the floor stays the withdrawn row's session, through a
    retraction and a later restore (the amendment's "changes nothing today")."""
    sid = "0000000999"
    live = _security(sid, datetime(2011, 12, 29, 22, tzinfo=UTC), "wrong")
    withdrawn = retraction(live, datetime(2012, 2, 1, 22, tzinfo=UTC))
    restored = _security(sid, datetime(2012, 3, 1, 22, tzinfo=UTC), "right")
    with open_for_write(led) as conn:
        for row in (live, withdrawn, restored):
            insert_row(conn, "securities", row)
    with duckdb.connect(led.store.path, read_only=True) as conn:
        assert repair.first_sessions(conn, RUN, led)[sid] == date(2011, 12, 29)


def test_the_switch_off_lists_the_lead_bars_and_split(led: Settings) -> None:
    result = repair_resolution(_without_lead(led), clock=lambda: RUN, dry_run=True)
    assert sorted(result.found.bars) == [(META, date(2017, 5, 31)), (META, date(2018, 7, 2))]
    assert sorted(result.found.actions) == [(META, date(2017, 6, 1))]


def test_a_bar_under_a_refused_lead_is_listed(led: Settings) -> None:
    with open_for_write(led) as conn:
        insert_row(conn, "listings", _listing(REUSER, "FB", date(2016, 3, 1)))
        insert_row(conn, "listings", _listing(REUSER, "OTHR", date(2018, 1, 2)))
    result = repair_resolution(led, clock=lambda: RUN, dry_run=True)
    assert sorted(result.found.bars) == [(META, date(2017, 5, 31)), (META, date(2018, 7, 2))]
    assert sorted(result.found.actions) == [(META, date(2017, 6, 1))]


def test_a_known_at_before_the_calendar_maps_to_its_first_session(led: Settings) -> None:
    # code-review on #983: calendar.start (1990-01-01) is a holiday, before
    # the first session; a floor from that day must not raise.
    early = datetime(1989, 6, 1, 21, tzinfo=UTC)
    with open_for_write(led) as conn:
        insert_row(conn, "securities", _security(REUSER, early, "early filer"))
    with duckdb.connect(led.store.path, read_only=True) as conn:
        sessions = repair.first_sessions(conn, RUN, led)
        resolver = store_resolver(conn, RUN, led)
    assert sessions[REUSER] == date(1990, 1, 2)
    assert sessions[META] == date(2012, 2, 1)
    assert resolver.lead("FB", date(2012, 2, 1)) == META


# --- rule 8 (#1314 item 2): a span ends when another issuer takes its ticker ---

TOPW, LATER_HOLDER = "0000000011", "0000000022"
TAKEN = date(2020, 6, 1)  # the later holder's first cover page naming TOPW (D)


@pytest.fixture
def handed(settings: Settings) -> Settings:
    """TOPW trades to 2020-04-01 (its last traded bar, L) and keeps the
    ticker on paper until another company's TOPW row of 2020-06-01. A
    zero-volume placeholder sits on it after L, a traded bar known only
    after the run, and a bar of another source."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        for row in (
            _listing(TOPW, "TOPW", date(2020, 1, 2)),
            _listing(LATER_HOLDER, "TOPW", TAKEN),
        ):
            insert_row(conn, "listings", row)
        placeholder = {**_bar(TOPW, date(2020, 5, 28)), "volume": 0}
        for row in (
            _bar(TOPW, date(2020, 3, 31)),
            _bar(TOPW, date(2020, 4, 1)),
            placeholder,
            {
                **_bar(TOPW, date(2020, 5, 20), known=datetime(2026, 10, 5, tzinfo=UTC)),
                "ingested_at": datetime(2026, 10, 5, tzinfo=UTC),
            },
            _bar(TOPW, date(2020, 5, 21), source="fixture"),
        ):
            insert_row(conn, "prices_daily", row)
    return settings


def test_rule_8_reads_the_last_traded_alpaca_bar_known_at_the_run(handed: Settings) -> None:
    with duckdb.connect(handed.store.path, read_only=True) as conn:
        assert repair.last_bar(conn, RUN, TOPW, date(2020, 1, 2), TAKEN) == date(2020, 4, 1)
        resolver = store_resolver(conn, RUN, handed)
    assert [(h.start, h.end) for h in resolver.handovers(TOPW)] == [(date(2020, 4, 2), TAKEN)]
    assert resolver.resolve("TOPW", date(2020, 4, 1)) == TOPW
    assert resolver.resolve("TOPW", date(2020, 5, 28)) is None
    assert resolver.resolve("TOPW", TAKEN) == LATER_HOLDER


def test_f_the_run_row_counts_the_spans_ended(handed: Settings) -> None:
    from tradepartner.adapters.alpaca_prices import AlpacaPriceSource

    with duckdb.connect(handed.store.path, read_only=True) as conn:
        resolver = store_resolver(conn, RUN, handed)
    line = AlpacaPriceSource(resolver, settings=handed).resolution_summary()
    assert "1 spans ended where another issuer took the ticker" in line


def test_f_the_dry_run_lists_s_bars_from_the_hand_over_day(handed: Settings) -> None:
    result = repair_resolution(handed, clock=lambda: RUN, dry_run=True)
    assert result.found.securities == (TOPW,)
    # The placeholder and the bar known only after the run (every stored row is
    # judged, #819); never the other source's.
    assert sorted(day for _, day in result.found.bars) == [date(2020, 5, 20), date(2020, 5, 28)]


def test_rule_8_waits_while_the_contaminated_bars_run_on_to_d(handed: Settings) -> None:
    # The fail-safe before #1314 item 3: S's stored bars run to the session
    # before D, so nothing is cut and nothing is listed.
    with open_for_write(handed) as conn:
        insert_row(conn, "prices_daily", _bar(TOPW, date(2020, 5, 29), close=99.0))
    result = repair_resolution(handed, clock=lambda: RUN, dry_run=True)
    assert result.found.securities == ()
    with duckdb.connect(handed.store.path, read_only=True) as conn:
        assert repair.last_bar(conn, RUN, TOPW, date(2020, 1, 2), TAKEN) == date(2020, 5, 29)
        assert store_resolver(conn, RUN, handed).handovers(TOPW) == ()


# --- repair-dividends (#1613, T186) ---------------------------------------

DUP, PAIR, OTHER = "0000000011", "0000000012", "0000000013"
EX = date(2021, 6, 15)
LATE = date(2021, 7, 15)
KNOWN = session_close(previous_session(EX))


def _cash(
    sid: str,
    action_id: str,
    *,
    ex_date: date = EX,
    amount: float = 0.5,
    known: datetime = KNOWN,
    cancelled: bool = False,
    action_type: str = "dividend",
) -> dict[str, Any]:
    return {
        "security_id": sid,
        "action_type": action_type,
        "ex_date": ex_date,
        "ratio_or_amount": amount,
        "announced_at": None,
        "source_action_id": action_id,
        "cancelled": cancelled,
        "known_at": known,
        "ingested_at": RUN,
        "source": "alpaca",
        "provenance": "action",
    }


def _entry(
    sid: str, verdict: str, *, ex_date: date = EX, amount: float = 0.5, retract: str = "b"
) -> ReviewedGroup:
    return ReviewedGroup(
        security_id=sid,
        ex_date=ex_date,
        amount=amount,
        verdict=verdict,  # type: ignore[arg-type]
        retract_source_action_id=retract if verdict == "duplicate" else "",
        ticker="",
        evidence="test",
    )


#: DUP's pair on EX is one payment stored twice; PAIR's is two payments; DUP's
#: LATE pair is after the `--to` bound used below.
LIST = (
    _entry(DUP, "duplicate"),
    _entry(PAIR, "distinct"),
    _entry(DUP, "duplicate", ex_date=LATE, retract="d"),
)


@pytest.fixture
def dividends(settings: Settings) -> Settings:
    """A duplicate group (DUP a/b on EX), a distinct pair (PAIR a/b), an
    unrelated action (OTHER), a later duplicate group (DUP c/d on LATE), and
    DUP's bars around EX."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        for row in (
            _cash(DUP, "a"),
            _cash(DUP, "b"),
            _cash(PAIR, "a"),
            _cash(PAIR, "b"),
            _cash(OTHER, "a"),
            _cash(DUP, "c", ex_date=LATE),
            _cash(DUP, "d", ex_date=LATE),
        ):
            insert_row(conn, "corporate_actions", row)
        for day in (date(2021, 6, 11), date(2021, 6, 14), EX):
            insert_row(conn, "prices_daily", _bar(DUP, day, close=10.0))
    _open_release(settings)
    return settings


def _retract(settings: Settings, **kwargs: Any) -> repair.DividendsRetracted:
    args: dict[str, Any] = {"reviewed": LIST, "to": EX}
    return repair.retract_dividends(settings, clock=lambda: RUN, **(args | kwargs))


def _actions(settings: Settings) -> list[tuple[Any, ...]]:
    return _rows(
        settings,
        "SELECT security_id, ex_date, source_action_id FROM corporate_actions "
        "ORDER BY security_id, ex_date, source_action_id, known_at",
    )


ALL_ACTIONS = [
    (DUP, EX, "a"),
    (DUP, EX, "b"),
    (DUP, LATE, "c"),
    (DUP, LATE, "d"),
    (PAIR, EX, "a"),
    (PAIR, EX, "b"),
    (OTHER, EX, "a"),
]


def test_a_dividend_dry_run_counts_and_changes_nothing(dividends: Settings) -> None:
    result = _retract(dividends, dry_run=True)
    assert (result.entries, result.identities, result.action_rows) == (2, 1, 1)
    assert (result.deferred, result.already_repaired, result.stale) == (1, 0, ())
    assert result.ex_dates == (EX,)
    assert result.summary() == (
        "would delete 1 duplicate dividend rows of 1 identities on 1 ex-dates "
        f"{EX}..{EX}; entries 2 through {EX}, deferred 1, already repaired 0, stale 0"
    )
    assert _actions(dividends) == ALL_ACTIONS
    assert _rows(dividends, "SELECT count(*) FROM ingestion_runs") == [(0,)]


def test_the_dividend_repair_deletes_the_retract_identity_only(dividends: Settings) -> None:
    result = _retract(dividends, expect=1, release=REL)
    assert _actions(dividends) == [row for row in ALL_ACTIONS if row != (DUP, EX, "b")]
    [(source, status, mode, rows, message)] = _rows(
        dividends, "SELECT source, status, mode, rows_added, message FROM ingestion_runs"
    )
    assert (source, status, mode, rows) == ("alpaca", REPAIRED, REPAIR, 0)
    assert message == f"{result.summary()}; release {REL}"
    assert message.startswith("deleted 1 duplicate dividend rows")
    with duckdb.connect(dividends.store.path, read_only=True) as conn:
        [ingest] = [i for i in last_ingests(conn, RUN) if i.source == "alpaca"]
    assert ingest.last_ok_finished_at is None  # a repair is never an ok ingest
    assert ingest.latest_status == REPAIRED


def test_every_entry_without_a_bound(dividends: Settings) -> None:
    result = _retract(dividends, to=None, dry_run=True)
    assert (result.identities, result.deferred, result.ex_dates) == (2, 0, (EX, LATE))


def test_after_the_repair_one_dividend_applies(dividends: Settings) -> None:
    """`adjusted_prices_as_of` applies the payment once after its `known_at`
    and not at all before it, as before the repair."""

    def close_before_ex(t: datetime) -> float:
        with duckdb.connect(dividends.store.path, read_only=True) as conn:
            frame = adjusted_prices_as_of(conn, t, [DUP], include_dividends=True)
        return float(frame.filter(pl.col("session") == date(2021, 6, 11))["close"][0])

    early = session_close(date(2021, 6, 11)) + timedelta(minutes=1)  # before KNOWN
    assert close_before_ex(RUN) == pytest.approx(10.0 * (1 - 0.5 / 10) ** 2)  # twice
    assert close_before_ex(early) == 10.0
    _retract(dividends, expect=1, release=REL)
    assert close_before_ex(RUN) == pytest.approx(10.0 * (1 - 0.5 / 10))  # once
    assert close_before_ex(early) == 10.0  # before the dividend is known: none


def test_the_dividend_repair_refuses_and_deletes_nothing(dividends: Settings) -> None:
    with pytest.raises(ValueError, match="dry run"):
        _retract(dividends, release=REL)
    with pytest.raises(ValueError, match="named data release"):
        _retract(dividends, expect=1)
    with pytest.raises(ValueError, match="not an XNYS session"):
        _retract(dividends, to=date(2021, 6, 12), dry_run=True)
    with pytest.raises(RepairRefused, match="not the expected 2"):
        _retract(dividends, expect=2, release=REL)
    with pytest.raises(RepairRefused, match="named 'other'"):
        _retract(dividends, expect=1, release="other")
    _close_release(dividends)
    with pytest.raises(RepairRefused, match="no release is open"):
        _retract(dividends, expect=1, release=REL)
    assert _actions(dividends) == ALL_ACTIONS
    assert _rows(dividends, "SELECT count(*) FROM ingestion_runs") == [(0,)]


@pytest.mark.parametrize(
    ("row", "why"),
    [
        # the vendor touched the retract identity after the review
        (_cash(DUP, "b", known=RUN - timedelta(days=1)), "has 2 revisions"),
        # the kept identity is gone: deleting b would remove the payment
        (_cash(DUP, "a", known=RUN - timedelta(days=1), cancelled=True), "no other live"),
        # a third identity on the key
        (_cash(DUP, "e"), "2 other live identities"),
    ],
)
def test_a_stale_entry_refuses_the_whole_run(
    dividends: Settings, row: dict[str, Any], why: str
) -> None:
    with open_for_write(dividends) as conn:
        insert_row(conn, "corporate_actions", row)
    before = _actions(dividends)
    dry = _retract(dividends, dry_run=True)
    [line] = dry.stale
    assert why in line and DUP in line
    assert dry.summary().endswith("stale 1")
    with pytest.raises(RepairRefused, match=rf"1 stale list entries(.|\n)*{why}"):
        _retract(dividends, expect=dry.action_rows, release=REL)
    assert _actions(dividends) == before
    assert _rows(dividends, "SELECT count(*) FROM ingestion_runs") == [(0,)]


@pytest.mark.parametrize(
    ("kept", "retracted", "why"),
    [
        # an earlier-known retract row: deleted, the payment leaves reads between the stamps
        ({}, {"known": KNOWN - timedelta(days=1)}, "known at"),
        ({}, {"amount": 0.51}, "not the listed key"),
        ({}, {"ex_date": date(2021, 6, 16)}, "not the listed key"),
        ({"amount": 0.51}, {}, "no other live"),
    ],
)
def test_a_retract_row_that_differs_from_the_kept_row_is_stale(
    settings: Settings, kept: dict[str, Any], retracted: dict[str, Any], why: str
) -> None:
    with open_for_write(settings) as conn:
        init_schema(conn)
        insert_row(conn, "corporate_actions", _cash(DUP, "a", **kept))
        insert_row(conn, "corporate_actions", _cash(DUP, "b", **retracted))
    _open_release(settings)
    dry = repair.retract_dividends(
        settings, clock=lambda: RUN, dry_run=True, reviewed=[_entry(DUP, "duplicate")]
    )
    assert dry.identities == 0
    [line] = dry.stale
    assert why in line
    with pytest.raises(RepairRefused, match="stale"):
        repair.retract_dividends(
            settings, clock=lambda: RUN, expect=0, release=REL, reviewed=[_entry(DUP, "duplicate")]
        )
    assert len(_actions(settings)) == 2


def test_a_second_dividend_run_deletes_nothing_and_counts_already_repaired(
    dividends: Settings,
) -> None:
    _retract(dividends, expect=1, release=REL)
    again = _retract(dividends, dry_run=True)
    assert (again.identities, again.action_rows, again.already_repaired) == (0, 0, 1)
    assert again.stale == ()
    second = _retract(dividends, expect=0, release=REL)
    assert second.already_repaired == 1
    assert len(_actions(dividends)) == len(ALL_ACTIONS) - 1


def test_a_dividend_repair_that_misses_its_count_rolls_back(
    dividends: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = repair._delete_identities
    monkeypatch.setattr(repair, "_delete_identities", lambda *a, **k: real(*a, **k) - 1)
    with pytest.raises(RuntimeError, match="rolled back"):
        _retract(dividends, expect=1, release=REL)
    assert _actions(dividends) == ALL_ACTIONS
    assert _rows(dividends, "SELECT count(*) FROM ingestion_runs") == [(0,)]


def test_the_repair_dividends_command(dividends: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repair, "reviewed_groups", lambda: LIST)
    app = cli.make_app(settings=lambda: dividends, clock=lambda: RUN)
    runner = CliRunner()
    base = ["repair-dividends", "--release", REL, "--to", str(EX)]
    dry = runner.invoke(app, [*base, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert dry.output.startswith("would delete 1 duplicate dividend rows")
    assert runner.invoke(app, ["repair-dividends", "--dry-run"]).exit_code == cli.USAGE_ERROR
    assert runner.invoke(app, base).exit_code == cli.USAGE_ERROR  # no count
    bad = runner.invoke(app, [*base[:-1], "2021-06-12", "--dry-run"])
    assert bad.exit_code == cli.USAGE_ERROR
    assert "not an XNYS session" in bad.output
    other = runner.invoke(
        app, ["repair-dividends", "--release", "other", "--expect-action-rows", "1"]
    )
    assert other.exit_code == 1
    assert "repair refused: no open data release named 'other'" in other.output
    assert _actions(dividends) == ALL_ACTIONS
    done = runner.invoke(app, [*base, "--expect-action-rows", "1"])
    assert done.exit_code == 0, done.output
    assert done.output.startswith("deleted 1 duplicate dividend rows")
    assert len(_actions(dividends)) == len(ALL_ACTIONS) - 1


def test_the_command_exits_1_on_a_stale_dry_run(
    dividends: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(repair, "reviewed_groups", lambda: LIST)
    with open_for_write(dividends) as conn:
        insert_row(conn, "corporate_actions", _cash(DUP, "e"))
    app = cli.make_app(settings=lambda: dividends, clock=lambda: RUN)
    dry = CliRunner().invoke(app, ["repair-dividends", "--release", REL, "--dry-run"])
    assert dry.exit_code == 1
    assert "stale 1" in dry.output and "2 other live identities" in dry.output


@pytest.mark.parametrize(
    ("entry", "why"),
    [
        # a mistyped retract id: both rows still live on the key
        (_entry(DUP, "duplicate", retract="zz"), "no row and 2 live identities"),
        # a mistyped security: nothing holds the key
        (_entry("0000009999", "duplicate"), "no row and 0 live identities"),
    ],
)
def test_an_absent_retract_id_is_already_repaired_only_beside_one_kept_row(
    dividends: Settings, entry: ReviewedGroup, why: str
) -> None:
    dry = _retract(dividends, reviewed=[entry], dry_run=True)
    assert dry.already_repaired == 0
    [line] = dry.stale
    assert why in line
    with pytest.raises(RepairRefused, match="stale"):
        _retract(dividends, reviewed=[entry], expect=0, release=REL)
    assert _actions(dividends) == ALL_ACTIONS


def test_repair_judges_spinoff_parent_and_child_separately_from_dividend(
    store: Settings,
) -> None:
    ex = date(2021, 6, 15)
    child = "0000000003"
    with open_for_write(store) as conn:
        insert_row(conn, "listings", _listing(child, "CHILD", ex))
        valid = {
            **_dividend(child, ex),
            "action_type": "spinoff",
            "ratio_or_amount": 0.25,
            "source_action_id": LIVE,
            "known_at": session_close(ex),
        }
        bad = {
            **_dividend(DEAD, ex),
            "action_type": "spinoff",
            "ratio_or_amount": 0.25,
            "source_action_id": LIVE,
            "known_at": session_close(ex),
        }
        insert_row(conn, "corporate_actions", valid)
        insert_row(conn, "corporate_actions", bad)
        insert_row(conn, "corporate_actions", _dividend(child, ex))
        insert_row(
            conn,
            "symbol_changes",
            {
                "security_id": child,
                "old_symbol": "OLD",
                "new_symbol": "NEW",
                "old_cusip": "",
                "new_cusip": "",
                "process_date": ex,
                "source_action_id": "rename-1",
                "known_at": session_close(ex),
                "ingested_at": session_close(ex),
                "source": "alpaca",
                "provenance": "action",
            },
        )
    dry = repair_resolution(store, clock=lambda: RUN, dry_run=True)
    assert dry.found.spinoffs == {(DEAD, ex): 1}
    assert dry.unjudged_symbol_changes == 1
    assert "1 symbol_changes rows unjudged" in dry.summary()
    assert (child, ex) in dry.found.actions
    assert (child, ex) not in dry.found.spinoffs
    assert dry.action_rows == 3  # one existing bad dividend, one same-day, one spin-off
    repair_resolution(store, clock=lambda: RUN, expect=(dry.bar_rows, dry.action_rows), release=REL)
    assert _rows(
        store,
        "SELECT action_type, security_id FROM corporate_actions "
        "WHERE ex_date='2021-06-15' ORDER BY 1,2",
    ) == [("spinoff", child)]
