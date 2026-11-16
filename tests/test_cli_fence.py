"""`tradepartner fence add | lift | list` (#3501, plan task T401; data-foundation spec
amendment 2026-11-16, item 3, acceptance (a) and (d)).

Fixture stores in `tmp_path` only: no real store, no broker. The universe is replaced by
a fixed member set (`fence_cmd.members_provider`), so the checks do not need a built
universe.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
from typer.testing import CliRunner

from tradepartner import cli, fence_cmd
from tradepartner.config import Settings
from tradepartner.store import fence, journal, registry, schema
from tradepartner.store.db import insert_row, open_for_write
from tradepartner.store.schema import init_schema

NOW = datetime(2020, 7, 1, 23, 0, tzinfo=UTC)
DAYS = [date(2020, 1, 2) + timedelta(days=i) for i in range(10)]
LAST = DAYS[-1]
MEMBERS = {"A", "B", "C", "D"}
ISSUE = "3475"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        store={"path": str(tmp_path / "store.duckdb"), "lock_retry_seconds": 0},
    )


def _invoke(settings: Settings, args: Sequence[str]) -> Any:
    app = cli.make_app(settings=lambda: settings, clock=lambda: NOW)
    return CliRunner().invoke(app, list(args))


@pytest.fixture(autouse=True)
def _members(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        fence_cmd,
        "members_provider",
        lambda conn, settings: lambda session: MEMBERS,
    )


def _store(tmp_path: Path) -> Settings:
    """Securities A to D with ten bars each; D is delisted."""
    settings = _settings(tmp_path)
    with open_for_write(settings) as conn:
        init_schema(conn)
        for sid in sorted(MEMBERS):
            insert_row(
                conn,
                "securities",
                {
                    "security_id": sid,
                    "cik": "1",
                    "name": f"{sid} Inc",
                    "known_at": NOW - timedelta(days=400),
                    "ingested_at": NOW - timedelta(days=400),
                    "source": "test",
                    "provenance": "snapshot",
                },
            )
            for day in DAYS:
                insert_row(
                    conn,
                    "prices_daily",
                    {
                        "security_id": sid,
                        "session": day,
                        "open": 1.0,
                        "high": 1.0,
                        "low": 1.0,
                        "close": 1.0,
                        "volume": 1,
                        "known_at": NOW - timedelta(days=300),
                        "ingested_at": NOW - timedelta(days=300),
                        "source": "test",
                        "provenance": "bar",
                    },
                )
        insert_row(
            conn,
            "delistings",
            {
                "security_id": "D",
                "form": "25",
                "class_title": "common",
                "exchange": "NYSE",
                "filed_at": NOW - timedelta(days=200),
                "effective_on": date(2020, 3, 1),
                "known_at": NOW - timedelta(days=200),
                "ingested_at": NOW - timedelta(days=200),
                "source": "test",
                "provenance": "filing",
            },
        )
    return settings


def _add(settings: Settings, *extra: str, ids: Sequence[str] = ("A", "B")) -> Any:
    args = ["fence", "add", "--reason", "bad history", "--issue", ISSUE]
    for sid in ids:
        args += ["--security-id", sid]
    args += ["--from", DAYS[0].isoformat(), "--to", DAYS[4].isoformat(), *extra]
    return _invoke(settings, args)


def _rows(settings: Settings, where: str = "TRUE") -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(
            "SELECT security_id, from_session, to_session, rule, action, issue, batch, known_at "
            f"FROM security_fence WHERE {where} ORDER BY known_at, security_id"
        ).fetchall()


def _decisions(settings: Settings) -> list[tuple[Any, ...]]:
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        return conn.execute(
            "SELECT decision_id, kind FROM owner_decisions WHERE kind = 'fence_write'"
        ).fetchall()


# --- acceptance (a) -------------------------------------------------------------------


def test_a_dry_run_writes_nothing_and_prints_the_rows_and_the_share(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    result = _add(settings, "--dry-run")
    assert result.exit_code == 0, result.output
    assert f"FENCE A {DAYS[0].isoformat()}..{DAYS[4].isoformat()} rule=manual issue=#3475" in (
        result.output
    )
    assert "FENCE B" in result.output
    assert "2 row(s) to fence" in result.output
    # The share the fence would reach: A and B of 4 members on the latest session are
    # NOT fenced (their window ended before it), but 10 of 40 pairs are (5 days x 2 ids).
    assert "fence: 0 of 4 securities (0.00%), 10 of 40 (security, session) pairs (25.00%)" in (
        result.output
    )
    assert _rows(settings) == []
    assert _decisions(settings) == []


def test_without_a_count_nothing_is_written_and_the_exit_is_1(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    result = _add(settings)
    assert result.exit_code == 1
    assert "nothing written: rerun with --count 2" in result.output
    assert _rows(settings) == []


@pytest.mark.parametrize("wrong", ["0", "1", "3"])
def test_a_count_unequal_to_the_printed_rows_writes_nothing(tmp_path: Path, wrong: str) -> None:
    settings = _store(tmp_path)
    result = _add(settings, "--count", wrong)
    assert result.exit_code == 1
    assert f"2 row(s) to fence and --count is {wrong}" in result.output
    assert _rows(settings) == []
    assert _decisions(settings) == []


def test_the_exact_count_writes_the_rows_with_known_at_and_one_decision(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    result = _add(settings, "--count", "2")
    assert result.exit_code == 0, result.output
    (decision,) = _decisions(settings)
    rows = _rows(settings)
    assert [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows] == [
        ("A", DAYS[0], DAYS[4], "manual", "fence", 3475),
        ("B", DAYS[0], DAYS[4], "manual", "fence", 3475),
    ]
    assert {r[6] for r in rows} == {decision[0]}
    assert {r[7] for r in rows} == {NOW}
    assert f"decision {decision[0]}: fence_write (fence, count 2)" in result.output
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        assert {r.security_id for r in fence.fence_as_of(conn, NOW)} == {"A", "B"}
        assert fence.fence_as_of(conn, NOW - timedelta(seconds=1)) == []


def test_an_already_fenced_window_is_refused(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    assert _add(settings, "--count", "2").exit_code == 0
    again = _add(settings, "--count", "2")
    assert again.exit_code == 1 and "already fenced" in again.output
    assert len(_rows(settings)) == 2


def test_an_unknown_id_a_blank_reason_and_a_reversed_window_are_refused(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    unknown = _add(settings, "--dry-run", ids=("A", "ZZZ"))
    assert unknown.exit_code == 1 and "unknown security id(s): ZZZ" in unknown.output
    blank = _invoke(
        settings,
        [
            *["fence", "add", "--reason", " ", "--issue", ISSUE, "--security-id", "A"],
            *["--from", "2020-01-02", "--to", "2020-01-03", "--dry-run"],
        ],
    )
    assert blank.exit_code == 1 and "needs a reason" in blank.output
    reversed_ = _invoke(
        settings,
        [
            *["fence", "add", "--reason", "r", "--issue", ISSUE, "--security-id", "A"],
            *["--from", "2020-01-05", "--to", "2020-01-03", "--dry-run"],
        ],
    )
    assert reversed_.exit_code == 1 and "--from is after --to" in reversed_.output


# --- the count line (acceptance (d)) --------------------------------------------------


def test_the_count_line_matches_a_direct_query(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    fenced = _invoke(
        settings,
        [
            *["fence", "add", "--reason", "r", "--issue", ISSUE, "--security-id", "D"],
            *["--security-id", "A", "--from", DAYS[5].isoformat(), "--to", "2020-07-01"],
            *["--count", "2"],
        ],
    )
    assert fenced.exit_code == 0, fenced.output
    listed = _invoke(settings, ["fence", "list"])
    assert listed.exit_code == 0, listed.output
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        (pairs,) = conn.execute(
            "SELECT count(*) FROM prices_daily p JOIN security_fence f "
            "ON p.security_id = f.security_id AND p.session BETWEEN f.from_session AND f.to_session"
        ).fetchone()  # type: ignore[misc]
        (delisted,) = conn.execute("SELECT count(DISTINCT security_id) FROM delistings").fetchone()  # type: ignore[misc]
    assert pairs == 10
    assert "2 active fence row(s)" in listed.output
    # The latest completed session (2020-07-01) is inside both windows: 2 of 4
    # securities, 10 of 40 pairs, 1 fenced delisted.
    assert (
        f"fence: 2 of 4 securities (50.00%), {pairs} of 40 (security, session) pairs (25.00%); "
        f"1 fenced delisted; delisted share of the universe {delisted} of 4 (25.00%)"
    ) in listed.output


def test_list_on_an_empty_fence_prints_the_count_line(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    listed = _invoke(settings, ["fence", "list"])
    assert listed.exit_code == 0
    assert "0 active fence row(s)" in listed.output
    assert "fence: 0 of 4 securities" in listed.output


# --- lift -----------------------------------------------------------------------------


def _lift(settings: Settings, *extra: str, ids: Sequence[str] = ("A",)) -> Any:
    args = ["fence", "lift", "--reason", "fixed", "--issue", ISSUE, "--from", DAYS[0].isoformat()]
    for sid in ids:
        args += ["--security-id", sid]
    return _invoke(settings, [*args, *extra])


def test_a_lift_is_a_new_row_and_the_earlier_row_stays(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    assert _add(settings, "--count", "2").exit_code == 0
    lifted = _lift(settings, "--count", "1")
    assert lifted.exit_code == 0, lifted.output
    rows = _rows(settings)
    assert sorted((r[0], r[4]) for r in rows) == [("A", "fence"), ("A", "lift"), ("B", "fence")]
    (lift,) = [r for r in rows if r[4] == "lift"]
    assert (lift[1], lift[2]) == (DAYS[0], DAYS[4])  # the fence's own window
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        assert {r.security_id for r in fence.fence_as_of(conn, NOW)} == {"B"}


def test_a_lift_of_a_key_that_is_not_active_is_refused(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    refused = _lift(settings, "--count", "1")
    assert refused.exit_code == 1 and "nothing to lift" in refused.output
    assert _rows(settings) == []


def test_a_lift_needs_the_exact_count_too(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    assert _add(settings, "--count", "2").exit_code == 0
    wrong = _lift(settings, "--count", "2")
    assert wrong.exit_code == 1 and "1 row(s) to lift and --count is 2" in wrong.output
    assert len(_rows(settings)) == 2
    none = _lift(settings)
    assert none.exit_code == 1 and "nothing written" in none.output
    assert len(_rows(settings)) == 2


# --- rules ----------------------------------------------------------------------------


def test_a_rule_outside_accepting_rules_is_refused_unless_manual(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    refused = _add(settings, "--rule", "non_overlapping_listings", "--dry-run")
    assert refused.exit_code == 1 and "is not accepted by fence" in refused.output
    assert _add(settings, "--rule", "manual", "--dry-run").exit_code == 0


def test_from_rule_is_refused_for_a_rule_health_does_not_accept(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    result = _invoke(
        settings,
        [
            *["fence", "add", "--reason", "r", "--issue", ISSUE],
            *["--from-rule", "no_bars_after_delisting", "--dry-run"],
        ],
    )
    assert result.exit_code == 1 and "is not accepted by fence" in result.output


def test_from_rule_takes_the_findings_of_an_accepting_rule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _store(tmp_path)
    monkeypatch.setattr(fence, "ACCEPTING_RULES", frozenset({"rule_x"}))
    monkeypatch.setitem(
        fence_cmd.FINDING_SOURCES,
        "rule_x",
        lambda conn, at: [
            fence.Finding("rule_x", "C", DAYS[1], DAYS[2]),
            fence.Finding("rule_x", "C", DAYS[1], DAYS[3]),  # same key: the wider window
            fence.Finding("rule_x", "A", DAYS[0], DAYS[0]),
        ],
    )
    base = ["fence", "add", "--reason", "r", "--issue", ISSUE, "--from-rule", "rule_x"]
    dry = _invoke(settings, [*base, "--dry-run"])
    assert dry.exit_code == 0, dry.output
    assert "2 row(s) to fence" in dry.output
    written = _invoke(settings, [*base, "--count", "2"])
    assert written.exit_code == 0, written.output
    assert [(r[0], r[1], r[2], r[3]) for r in _rows(settings)] == [
        ("A", DAYS[0], DAYS[0], "rule_x"),
        ("C", DAYS[1], DAYS[3], "rule_x"),
    ]


# --- the refusals ---------------------------------------------------------------------


def test_the_write_is_refused_while_a_release_is_open(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    backup = tmp_path / "backup.duckdb"
    backup.write_bytes(Path(settings.store.path).read_bytes())
    opened = _invoke(
        settings,
        [
            *["decision", "data-release", "open", "--name", "rel", "--backup", str(backup)],
            *["--reason", "t"],
        ],
    )
    assert opened.exit_code == 0, opened.output
    refused = _add(settings, "--count", "2")
    assert refused.exit_code == 1
    assert "data release 'rel' is open" in refused.output
    assert _rows(settings) == []
    with open_for_write(settings) as conn:
        assert registry.open_release(conn) is not None


def _window(
    settings: Settings,
    *,
    first: date,
    held: Sequence[str] = (),
    open_orders: Sequence[str] = (),
    closed: bool = False,
) -> int:
    """An open (or closed) paper window starting `first`, one marked run, positions in
    `held` and a live buy order for each id in `open_orders`."""
    with open_for_write(settings) as conn:
        init_schema(conn)
        window_id = journal.append(
            conn,
            journal.PaperWindowRow(
                hypothesis_id=1,
                first_rebalance_session=first,
                account_id="acct",
                starting_cash=1000.0,
                starting_equity=1000.0,
                code_version="x",
                started_at=NOW - timedelta(days=30),
                frozen_json="{}",
                frozen_sha256="x",
                known_at=NOW - timedelta(days=30),
                ingested_at=NOW - timedelta(days=30),
            ),
        )
        assert window_id is not None
        run_id = journal.append(
            conn,
            journal.PaperRunRow(
                window_id=window_id,
                session=DAYS[2],
                kind="mark",
                started_at=NOW - timedelta(days=20),
                invoked_by="scheduler",
                code_version="x",
                known_at=NOW - timedelta(days=20),
                ingested_at=NOW - timedelta(days=20),
            ),
        )
        assert run_id is not None
        for sid in held:
            journal.append(
                conn,
                journal.PositionDailyRow(
                    run_id=run_id,
                    session=DAYS[2],
                    security_id=sid,
                    quantity=5.0,
                    known_at=NOW - timedelta(days=20),
                    ingested_at=NOW - timedelta(days=20),
                ),
            )
        for number, sid in enumerate(open_orders):
            journal.append(
                conn,
                journal.OrderRow(
                    client_order_id=f"co-{number}",
                    decision_id=1,
                    run_id=run_id,
                    session=DAYS[2],
                    attempt=1,
                    phase="buy",
                    security_id=sid,
                    symbol=sid,
                    side="buy",
                    sells_in_flight_at_submit=False,
                    known_at=NOW - timedelta(days=20),
                    ingested_at=NOW - timedelta(days=20),
                ),
            )
        if closed:
            journal.append(
                conn,
                journal.PaperWindowStopRow(
                    window_id=window_id,
                    at=NOW - timedelta(days=1),
                    state="closed",
                    known_at=NOW - timedelta(days=1),
                    ingested_at=NOW - timedelta(days=1),
                ),
            )
    return window_id


def test_an_id_an_open_book_holds_is_refused_with_the_interim_text(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    _window(settings, first=date(2030, 1, 2), held=["A"])  # window far after the fence
    refused = _add(settings, "--count", "2")
    assert refused.exit_code == 1
    assert "A: held position: owner decision pending" in refused.output
    assert "B:" not in refused.output.split("fence refused:")[1]
    assert _rows(settings) == []


def test_an_id_with_an_open_order_is_refused_as_held(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    _window(settings, first=date(2030, 1, 2), open_orders=["B"])
    refused = _add(settings, "--count", "2")
    assert refused.exit_code == 1
    assert "B: held position: owner decision pending" in refused.output
    assert "an open order co-0" in refused.output


def test_a_closed_window_holds_nothing(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    _window(settings, first=DAYS[0], held=["A"], open_orders=["B"], closed=True)
    assert _add(settings, "--count", "2").exit_code == 0


def test_a_fence_overlapping_an_open_window_whose_universe_holds_the_id_is_refused(
    tmp_path: Path,
) -> None:
    settings = _store(tmp_path)
    _window(settings, first=DAYS[3])  # the fence runs to DAYS[4]: two shared sessions
    refused = _add(settings, "--count", "2")
    assert refused.exit_code == 1
    assert "overlaps open window" in refused.output and "whose universe holds it" in refused.output
    assert _rows(settings) == []
    lift_target = _lift(settings, "--dry-run")
    assert lift_target.exit_code == 1  # nothing active to lift, whatever the window


def test_a_fence_wholly_before_every_open_window_is_allowed(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    _window(settings, first=DAYS[5])  # the fence ends DAYS[4], the day before
    assert _add(settings, "--count", "2").exit_code == 0


def test_an_overlap_where_the_universe_lacks_the_id_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _store(tmp_path)
    _window(settings, first=DAYS[3])
    monkeypatch.setattr(
        fence_cmd,
        "members_provider",
        lambda conn, settings: lambda session: {"C", "D"},
    )
    assert _add(settings, "--count", "2").exit_code == 0


def test_a_lift_inside_an_open_window_whose_universe_holds_the_id_is_refused(
    tmp_path: Path,
) -> None:
    settings = _store(tmp_path)
    assert _add(settings, "--count", "2").exit_code == 0
    _window(settings, first=DAYS[3])
    refused = _lift(settings, "--count", "1")
    assert refused.exit_code == 1 and "overlaps open window" in refused.output
    assert len(_rows(settings)) == 2


# --- the default universe provider ------------------------------------------------------


def test_the_default_provider_reads_universe_as_of_at_the_session_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tradepartner.universe as universe_module

    seen: list[datetime] = []

    class _Found:
        members = pl.DataFrame({"security_id": ["A", "C"]})

    def fake(conn: object, t: datetime, settings: object = None) -> _Found:
        seen.append(t)
        return _Found()

    monkeypatch.undo()  # the autouse stub off: use the real provider
    monkeypatch.setattr(universe_module, "universe_as_of", fake)
    members = fence_cmd.members_provider(duckdb.connect(":memory:"), _settings(tmp_path))
    session = date(2020, 1, 2)
    first: Collection[str] = members(session)
    assert first == {"A", "C"}
    assert members(session) == first and len(seen) == 1  # memoised
    assert seen[0].tzinfo is not None and seen[0].date() == session


def test_the_store_version_the_command_needs_is_50(tmp_path: Path) -> None:
    settings = _store(tmp_path)
    with duckdb.connect(settings.store.path, read_only=True) as conn:
        assert schema.stored_version(conn) == 50
