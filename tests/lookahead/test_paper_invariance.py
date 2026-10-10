"""No look-ahead in the tracking run's pure pieces (Phase 4 plan T63g; spec acceptance
"`tests/lookahead/`").

A run on session S may use only what was known at close(S-1). The fixture builds one
store holding a paper window's journal as it stood before the run on S wrote anything,
then injects on top every kind of row known after close(S-1):

- later runs' rows: the run on S itself (a decision, an order, its events and fill on
  S, a `carried_residue` adjustment, an `engaged` kill-switch row, the `executed`
  event of the previous rebalance and an `ok` reconciliation at S's close), the fill
  and terminal event of an order placed before the cut, filled on S-1 and collected on
  S, and the S+1 run (unfinished) with its `positions_daily` mark;
- a late bar revision of a held name's S-1 bar, and of an anchor bar the plan at T_i
  reads;
- late corporate actions: a split of one held name with ex-date S-1 and a dividend of
  the other with ex-date S, both known only after close(S-1).

Each piece is computed on that full store and on `TruncatedStore` cut at close(S-1),
the harness extended to cut `JOURNAL_TABLE_NAMES` too (`tables=CUT_TABLES`), and the
two must be equal:

- the plan at T_i (`engine.plan` over `StoreProvider`), S being T_i's fill session;
- the ledger stated through S-1 and through S, read the way `reconcile_now` reads it
  (`reconcile_run._journal_state` at `as_of`, splits from `live_actions_as_of` at
  close(S-1)), with `as_of` = close(S-1) on the full store (#488);
- the reconciliation explanations (`reconcile_run.explanations_as_of`, `as_of` =
  close(S-1) on the full store);
- the marks a run on S writes, every session from the window's first through S-1
  (`marks.marks_for`);
- the journal-derived state: `plan.decision_state`, `plan.remainder`, `plan.residue`,
  `plan.rebalance_state` and `switch.derive`. These are pure over the rows they are
  given; per the owner's decision on #488 they are invariant because their journal
  input is cut at `as_of` (`known_at <= as_of`) where it is loaded. The run's
  loader `window_journal_inputs` (step 4, step 7b, `_skipped`) reads
  every row the window holds with no `as_of`; `_derived` uses it on the cut store
  (`as_of is None`), and `_known` at `as_of=CUT` on the full store. The check
  compares production reads on a truncated store against the rule; its liveness
  half is the evidence that the loader must not drop, add or rescope rows.
- a `stop` run's own two pure pieces, `outcomes.due_outcomes` and `exits.stop_exits`,
  given this window by hand as if S were a stop requested at T_PREV (`#598`: this
  window's own run on S is a rebalance, so no `stop` run of its own has read them).
  Each is built the same way as the derived state above, from the same readers cut at
  `as_of`.

Every equality has a liveness half, so an equal result is the pieces ignoring the late
rows, not the rows missing: `test_every_injected_fact_is_live` sees each injected row
on the full store and not on the cut, and each check asserts that the same piece read
past the cut (`as_of` after the late rows, or the uncut rows) differs.

A bar revision known after close(S-1) changes the run on S+1
(`test_a_late_bar_revision_changes_the_next_run_only`): its explanations' reference
price for that name is the revised S-1 close. The fixture drops that name's own bar
for S, so S-1's is the latest the S+1 run can read (a halted session). No other piece
of the S+1 run reads it: marks price each session at its own close, and the ledger
reads no bars.

The cut connection is read-only and carries no registry, so the plan's provider checks
its trial handle on the full store (as `test_backtest_plan_timing.py` does).
`store.journal.require_journal` counts base tables only, and the cut journal is views,
so the journal readers run with a stand-in that also accepts views (`_views_count`).

`test_a_stop_run_s_outcomes_and_exits_are_unchanged_on_the_cut` (#598) gives
`due_outcomes` and `stop_exits` the fixture's own rows, read at `as_of` the same way
as `_derived`, with `OutcomeWindow(stop_requested=T_PREV)` by hand. Both pieces always
read store facts (actions, prices) at close(S-1), never later, as a run does; its
liveness half reads the held[1] split action past the cut (changing the F_PREV buy's
`position_return`, as `test_a_late_corporate_action_...` does for the remainder) and
the held[1] trim's own terminal state past the cut (an open sell blocks `stop_exits`
for it at the cut; filled past it, the name gets an exit).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import duckdb
import polars as pl
import pytest
from conftest import load_universe_fixtures

from lookahead.harness import _FACT_TABLES, TruncatedStore
from tradepartner.backtest import engine
from tradepartner.backtest.schedule import fill_session, read_time, rebalance_sessions
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import next_session, previous_session, session_close
from tradepartner.config import RiskConfig, Settings
from tradepartner.execution import exits, outcomes, plan, reconcile_run, switch
from tradepartner.execution.exits import ExitAsset, ExitDecision
from tradepartner.execution.ledger import Ledger
from tradepartner.execution.marks import Mark, marks_for
from tradepartner.execution.outcomes import OutcomeWindow
from tradepartner.execution.reconcile import Explanations
from tradepartner.execution.risk import unfilled_sells
from tradepartner.execution.run import window_journal_inputs
from tradepartner.store import journal, registry, schema
from tradepartner.store.asof import live_actions_as_of, prices_as_of
from tradepartner.store.db import configure_connection, insert_row
from tradepartner.store.journal import (
    AdjustmentRow,
    DecisionRow,
    FillRow,
    KillSwitchRow,
    OrderEventRow,
    OrderRow,
    PaperRunResultRow,
    PaperRunRow,
    PaperWindowRow,
    PositionDailyRow,
    RebalanceEventRow,
    ReconciliationRow,
    append,
)

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
FIXTURE_START = date(2017, 1, 3)
FIXTURE_END = date(2020, 6, 30)

#: The window's first rebalance and the rebalance whose fill session is S.
T_PREV = date(2018, 12, 31)
T_I = date(2019, 1, 31)
F_PREV = fill_session(T_PREV)
S = fill_session(T_I)
S_PREV = previous_session(S)
S_NEXT = next_session(S)
CUT = session_close(S_PREV)
#: An instant after every injected row (the S+1 run's are the latest).
LATER = session_close(S_NEXT)
#: The anchor bar momentum 12-1 reads at T_i: the month-end one month back.
ANCHOR = T_PREV
#: How long after close(S-1) each late store fact becomes known (before close(S)).
LATE = timedelta(hours=1)
FROZEN = RiskConfig()
TOLERANCE = FROZEN.reconcile_quantity_tolerance
STARTING_CASH = 100_000.0
QUANTITY = 10.0
#: The quantity of the sell placed before the cut and collected after it.
SOLD = 5.0
DIVIDEND = 0.25
#: Every journal table the cut store truncates, with the fact tables.
CUT_TABLES = (*_FACT_TABLES, *schema.JOURNAL_TABLE_NAMES)

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]


class _HasKnownAt(Protocol):
    @property
    def known_at(self) -> datetime: ...


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `StoreProvider` refuses frozen calendar settings that differ from the live ones.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _views_count(conn: duckdb.DuckDBPyConnection) -> None:
    """`require_journal`, counting views as well as tables (the cut journal is views)."""
    names = list(schema.JOURNAL_TABLE_NAMES)
    marks = ", ".join("?" for _ in names)
    (present,) = conn.execute(  # type: ignore[misc]
        "SELECT COUNT(*) FROM (SELECT table_name AS name FROM duckdb_tables() "
        "WHERE schema_name = current_schema() UNION SELECT view_name FROM duckdb_views() "
        f"WHERE schema_name = current_schema()) WHERE name IN ({marks})",
        names,
    ).fetchone()
    if present != len(names):
        raise journal.JournalNotInitialised("journal tables missing")


@pytest.fixture(autouse=True)
def _journal_views(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(journal, "require_journal", _views_count)


def _settings() -> Settings:
    return Settings(_env_file=None, strategy={"top_fraction": 0.5})


def _lend(conn: duckdb.DuckDBPyConnection) -> Connect:
    @contextmanager
    def lend() -> Iterator[duckdb.DuckDBPyConnection]:
        yield conn

    return lend


def _open_trial(conn: duckdb.DuckDBPyConnection, settings: Settings) -> registry.TrialHandle:
    """A synthetic trial on the in-memory store (never the real store)."""
    hypothesis = registry.register_hypothesis(
        conn,
        slug="h-paper-invariance",
        family="momentum",
        title="paper no-look-ahead check",
        doc_path="docs/hypotheses/h-paper-invariance.md",
        doc_sha256="0" * 64,
        params={"costs.per_side_bps": 0.0},
        in_sample_start=FIXTURE_START,
        holdout_start=date(2023, 1, 1),
        holdout_end=date(2025, 12, 31),
        registered_by="test",
        settings=settings,
    )
    return registry.open_trial(
        conn,
        hypothesis_id=hypothesis.hypothesis_id,
        kind="in_sample",
        start_session=FIXTURE_START,
        end_session=FIXTURE_END,
        data_cutoff=read_time(rebalance_sessions(FIXTURE_START, FIXTURE_END)[-1]),
        synthetic=True,
        run_by="test",
        settings=settings,
    )


def _at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


def _bar(conn: duckdb.DuckDBPyConnection, sid: str, session: date, t: datetime) -> dict[str, Any]:
    """`sid`'s bar for `session` as known at `t`."""
    [row] = prices_as_of(conn, t, [sid]).filter(pl.col("session") == session).iter_rows(named=True)
    return row


def _close(conn: duckdb.DuckDBPyConnection, sid: str, session: date) -> float:
    return float(_bar(conn, sid, session, session_close(session))["close"])


def _revise(conn: duckdb.DuckDBPyConnection, sid: str, session: date, factor: float) -> None:
    """A second bar for (`sid`, `session`), every price times `factor`, known after the cut."""
    row = _bar(conn, sid, session, session_close(session))
    for column in ("open", "high", "low", "close"):
        row[column] = row[column] * factor
    row.update(known_at=CUT + LATE, ingested_at=CUT + LATE, provenance="bar")
    insert_row(conn, "prices_daily", row)


def _action(
    conn: duckdb.DuckDBPyConnection, sid: str, kind: str, ex: date, value: float, known: datetime
) -> None:
    insert_row(
        conn,
        "corporate_actions",
        {
            "security_id": sid,
            "action_type": kind,
            "ex_date": ex,
            "ratio_or_amount": value,
            "known_at": known,
            "ingested_at": known,
            "source": "alpaca",
            "provenance": "action",
        },
    )


def _run(conn: duckdb.DuckDBPyConnection, window_id: int, session: date, kind: str) -> int:
    started = _at(session, 14)
    run_id = append(
        conn,
        PaperRunRow(
            window_id=window_id,
            session=session,
            kind=kind,
            started_at=started,
            invoked_by="scheduler",
            code_version="test",
            known_at=started,
            ingested_at=started,
        ),
    )
    assert run_id is not None
    return run_id


def _decision(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    sid: str,
    rebalance: date,
    side: str,
    known: datetime,
    *,
    target: float | None = None,
) -> int:
    decision_id = append(
        conn,
        DecisionRow(
            run_id=run_id,
            rebalance_session=rebalance,
            security_id=sid,
            side=side,
            planned_quantity=None if side == "buy" else SOLD,
            target_notional=target,
            whole_share=False,
            decision="trade",
            known_at=known,
            ingested_at=known,
        ),
    )
    assert decision_id is not None
    return decision_id


def _order(
    conn: duckdb.DuckDBPyConnection,
    run_id: int,
    decision_id: int,
    sid: str,
    session: date,
    side: str,
    quantity: float,
    known: datetime,
) -> str:
    coid = f"tp-{session:%Y%m%d}-{sid}-{side}-1"
    append(
        conn,
        OrderRow(
            client_order_id=coid,
            decision_id=decision_id,
            run_id=run_id,
            session=session,
            attempt=1,
            phase=side,
            security_id=sid,
            symbol=sid,
            side=side,
            quantity=quantity,
            sells_in_flight_at_submit=False,
            known_at=known,
            ingested_at=known,
        ),
    )
    for status in ("pending", "accepted"):
        append(
            conn,
            OrderEventRow(client_order_id=coid, status=status, known_at=known, ingested_at=known),
        )
    return coid


def _filled(
    conn: duckdb.DuckDBPyConnection,
    coid: str,
    quantity: float,
    price: float,
    filled: datetime,
    known: datetime,
) -> None:
    """The fill of `coid` and its terminal event, both journaled at `known`."""
    append(
        conn,
        FillRow(
            client_order_id=coid,
            filled_at=filled,
            quantity=quantity,
            price=price,
            price_implied=False,
            broker_fill_id=f"{coid}-f",
            source="broker_feed",
            known_at=known,
            ingested_at=known,
        ),
    )
    append(
        conn,
        OrderEventRow(client_order_id=coid, status="filled", known_at=known, ingested_at=known),
    )


def _bought(
    conn: duckdb.DuckDBPyConnection, run_id: int, sid: str, session: date, rebalance: date
) -> float:
    """A buy of `QUANTITY` shares of `sid` on `session` at its close, decided, ordered,
    filled and collected within the hour. Returns the cash it moved."""
    price = _close(conn, sid, session)
    known = _at(session, 15)
    decision_id = _decision(conn, run_id, sid, rebalance, "buy", known, target=QUANTITY * price)
    coid = _order(conn, run_id, decision_id, sid, session, "buy", QUANTITY, known)
    _filled(conn, coid, QUANTITY, price, known, known + timedelta(minutes=30))
    return -QUANTITY * price


@dataclass(frozen=True)
class Fixture:
    full: duckdb.DuckDBPyConnection
    settings: Settings
    handle: registry.TrialHandle
    window: PaperWindowRow
    held: tuple[str, ...]
    #: A name the run on S buys, held nowhere before it.
    new: str
    #: The decision placed before the cut whose sell fills on S-1, collected on S.
    in_flight: int
    #: The run on S (the reading run of the switch).
    run_s: int
    #: held[0]'s S-1 close as first known, and as revised after the cut.
    original_close: float
    revised_close: float
    #: The scored member whose anchor bar (T_{i-1}) is revised after the cut.
    anchored: str

    @property
    def window_id(self) -> int:
        assert self.window.window_id is not None
        return self.window.window_id


def _plan_at(
    conn: duckdb.DuckDBPyConnection,
    registry_conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    handle: registry.TrialHandle,
    session: date,
) -> engine.Plan:
    with StoreProvider(_lend(conn), handle, settings, registry_connect=_lend(registry_conn)) as p:
        return engine.plan(p, settings, session, family="momentum")


@pytest.fixture(scope="module")
def fixture() -> Iterator[Fixture]:
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, UNIVERSE_DIR)
    settings = _settings()
    handle = _open_trial(conn, settings)

    first = _plan_at(conn, conn, settings, handle, T_PREV)
    later = _plan_at(conn, conn, settings, handle, T_I)
    # Two targets with no split of their own near S, so the quantities below are plain.
    split = live_actions_as_of(conn, LATER).filter(
        (pl.col("action_type") == "split") & (pl.col("ex_date") > T_PREV)
    )
    ranked = sorted(
        set(first.targets) - set(split["security_id"].to_list()),
        key=lambda sid: (-first.targets[sid], sid),
    )
    held = tuple(sorted(ranked[:2]))
    new = sorted(set(later.targets) - set(held))[0]

    # The journal as it stood before the run on S wrote anything (all known <= CUT).
    started = _at(F_PREV, 13)
    window = PaperWindowRow(
        hypothesis_id=handle.hypothesis_id,
        first_rebalance_session=T_PREV,
        account_id="PA1",
        starting_cash=STARTING_CASH,
        starting_equity=STARTING_CASH,
        code_version="test",
        started_at=started,
        frozen_json="{}",
        frozen_sha256="0" * 64,
        known_at=started,
        ingested_at=started,
    )
    window_id = append(conn, window)
    assert window_id is not None
    window = replace(window, window_id=window_id)
    run_id = _run(conn, window_id, F_PREV, "rebalance")
    cash = STARTING_CASH + sum(_bought(conn, run_id, sid, F_PREV, T_PREV) for sid in held)
    # A trim of held[1] placed on F_PREV, still accepted at the cut.
    placed = _at(F_PREV, 16)
    in_flight = _decision(conn, run_id, held[1], T_PREV, "sell", placed)
    trim = _order(conn, run_id, in_flight, held[1], F_PREV, "sell", SOLD, placed)
    reconciled = _at(F_PREV, 21, 30)
    append(
        conn,
        ReconciliationRow(
            window_id=window_id,
            run_id=run_id,
            at=reconciled,
            status="ok",
            broker_cash=cash,
            known_at=reconciled,
            ingested_at=reconciled,
        ),
    )
    finished = _at(F_PREV, 21, 45)
    append(
        conn,
        PaperRunResultRow(
            run_id=run_id,
            finished_at=finished,
            status="ok",
            clock_fault=False,
            known_at=finished,
            ingested_at=finished,
        ),
    )
    # A dividend of held[1] with ex-date S, known before the cut (held[0]'s comes late).
    _action(conn, held[1], "dividend", S, DIVIDEND, CUT - LATE)

    # Everything known after close(S-1): the run on S ...
    run_s = _run(conn, window_id, S, "rebalance")
    collected = _at(S, 14, 5)  # step 3: the trim filled at 15:00Z on S-1
    trim_price = _close(conn, held[1], S_PREV)
    _filled(conn, trim, SOLD, trim_price, _at(S_PREV, 15), collected)
    cash += SOLD * trim_price
    append(
        conn,
        RebalanceEventRow(
            rebalance_session=T_PREV,
            run_id=run_s,
            status="executed",
            known_at=collected,
            ingested_at=collected,
        ),
    )
    append(
        conn,
        AdjustmentRow(
            window_id=window_id,
            run_id=run_s,
            session=S,
            kind="carried_residue",
            origin="dust",
            security_id=held[0],
            quantity=1.0,
            known_at=collected,
            ingested_at=collected,
        ),
    )
    cash += _bought(conn, run_s, new, S, T_I)
    engaged = _at(S, 18)
    append(
        conn,
        KillSwitchRow(
            window_id=window_id,
            at=engaged,
            state="engaged",
            source="owner",
            reason="late",
            known_at=engaged,
            ingested_at=engaged,
        ),
    )
    at_s = _at(S, 21, 30)
    append(
        conn,
        ReconciliationRow(
            window_id=window_id,
            run_id=run_s,
            at=at_s,
            status="ok",
            broker_cash=cash,
            known_at=at_s,
            ingested_at=at_s,
        ),
    )
    # ... the S+1 run, unfinished, and its mark ...
    run_next = _run(conn, window_id, S_NEXT, "mark")
    marked = _at(S_NEXT, 14, 5)
    append(
        conn,
        PositionDailyRow(
            run_id=run_next,
            session=S,
            security_id=new,
            quantity=QUANTITY,
            known_at=marked,
            ingested_at=marked,
        ),
    )
    # ... and late store facts: bar revisions, a split and a dividend.
    original = _close(conn, held[0], S_PREV)
    _revise(conn, held[0], S_PREV, 1.25)
    conn.execute(  # held[0] does not trade on S (a halted session)
        "DELETE FROM prices_daily WHERE security_id = ? AND session = ?", [held[0], S]
    )
    anchored = next(sid for sid in later.members if sid in later.scores)
    _revise(conn, anchored, ANCHOR, 1.5)
    _action(conn, held[1], "split", S_PREV, 2.0, CUT + LATE)
    _action(conn, held[0], "dividend", S, DIVIDEND, CUT + LATE)
    try:
        yield Fixture(
            full=conn,
            settings=settings,
            handle=handle,
            window=window,
            held=held,
            new=new,
            in_flight=in_flight,
            run_s=run_s,
            original_close=original,
            revised_close=_bar(conn, held[0], S_PREV, LATER)["close"],
            anchored=anchored,
        )
    finally:
        conn.close()


@pytest.fixture(scope="module")
def cut(fixture: Fixture) -> Iterator[duckdb.DuckDBPyConnection]:
    store = TruncatedStore(fixture.full, tables=CUT_TABLES)
    try:
        yield store.at(CUT)
    finally:
        store.close()


# --- the pieces --------------------------------------------------------------------


def _plan(fixture: Fixture, conn: duckdb.DuckDBPyConnection) -> engine.Plan:
    return _plan_at(conn, fixture.full, fixture.settings, fixture.handle, T_I)


def _ledger_for(
    fixture: Fixture, conn: duckdb.DuckDBPyConnection, as_of: datetime
) -> Callable[[date], Ledger]:
    """The window's ledger stated through a session as `reconcile_now` builds it: the
    journal read at `as_of`, splits at close(S-1)."""
    state = reconcile_run._journal_state(conn, fixture.window_id, as_of)
    actions = live_actions_as_of(conn, CUT)

    def ledger(through: date) -> Ledger:
        return reconcile_run._ledger(state, actions, fixture.window, through, TOLERANCE)

    return ledger


def _explain(
    fixture: Fixture, conn: duckdb.DuckDBPyConnection, as_of: datetime, session: date = S
) -> Explanations:
    return reconcile_run.explanations_as_of(
        conn,
        fixture.window,
        session,
        as_of=as_of,
        settings=fixture.settings,
        quantity_tolerance=TOLERANCE,
    )


def _marks(fixture: Fixture, conn: duckdb.DuckDBPyConnection, as_of: datetime) -> list[Mark]:
    sessions: list[date] = []
    day = F_PREV
    while day <= S_PREV:
        sessions.append(day)
        day = next_session(day)
    flags = dict.fromkeys(fixture.held, True)
    return marks_for(
        conn,
        fixture.window,
        _ledger_for(fixture, conn, as_of),
        sessions,
        flags,
        actions=live_actions_as_of(conn, CUT),
    )


def _known[R: _HasKnownAt](rows: Sequence[R], as_of: datetime | None) -> list[R]:
    """The rows known at `as_of` (#488's rule), or every row when it is None."""
    return [r for r in rows if as_of is None or r.known_at <= as_of]


@dataclass(frozen=True)
class Derived:
    """The journal-derived state a run on S reads (T63g: "no derived state")."""

    decisions: dict[int, plan.DecisionState]
    remainder: plan.Remainder
    residue: float
    rebalance: plan.RebalanceState
    switch: switch.SwitchState


def _derived(fixture: Fixture, conn: duckdb.DuckDBPyConnection, as_of: datetime | None) -> Derived:
    """Every derived state of the window on S from `conn`'s journal.

    On the cut store (`as_of is None`): loads all eleven journal inputs through
    `window_journal_inputs` (the run's load path for step 4, step 7b's
    `decision_state`/`rebalance_state` and `_skipped`'s `switch.derive`;
    `_locked_run` reads the same runs, kill-switch and rebalance readers directly,
    and `_exit_book` is excluded as it reads cross-window).

    On the full store (`as_of=CUT`): loads through `store.journal` readers and
    applies the `_known` cut at close(S-1).

    Store facts (actions, prices) are read at close(S-1) either way.
    """
    window_id = fixture.window_id
    if as_of is None:
        inputs = window_journal_inputs(conn, window_id)
        with_events = inputs.decisions
        decisions = [d.decision for d in with_events]
        events = [e for d in with_events for e in d.events]
        orders = inputs.orders
        order_events = inputs.order_events
        fills = inputs.fills
        runs = inputs.runs
        results = inputs.results
        adjustments = inputs.adjustments
        positions_daily = inputs.positions_daily
        rebalance_events = inputs.rebalance_events
        kill_switch = inputs.kill_switch_events
    else:
        with_events = journal.decisions_for(conn, window_id)
        decisions = _known([d.decision for d in with_events], as_of)
        events = _known([e for d in with_events for e in d.events], as_of)
        orders = _known(journal.orders_for(conn, window_id=window_id), as_of)
        order_events = _known(journal.order_events_for(conn, window_id=window_id), as_of)
        fills = [
            f for f in journal.fills_for(conn, window_id=window_id) if f.fill.known_at <= as_of
        ]
        runs_with = journal.runs_for(conn, window_id)
        runs = _known([r.run for r in runs_with], as_of)
        results = _known([r.result for r in runs_with if r.result is not None], as_of)
        adjustments = _known(journal.adjustments_for(conn, window_id), as_of)
        positions_daily = _known(journal.positions_daily_for(conn, window_id), as_of)
        rebalance_events = _known(journal.rebalance_events_for(conn, window_id), as_of)
        kill_switch = _known(journal.kill_switch_events_for(conn, window_id), as_of)
    actions = live_actions_as_of(conn, CUT)
    closes = {
        row["security_id"]: float(row["close"])
        for row in prices_as_of(conn, CUT, [d.security_id for d in decisions])
        .filter(pl.col("session") == S_PREV)
        .iter_rows(named=True)
    }
    states = {
        d.decision_id: plan.decision_state(
            d, events, orders, order_events, fills, actions, closes.__getitem__, FROZEN, session=S
        )
        for d in decisions
        if d.decision_id is not None
    }
    [trim] = [d for d in decisions if d.decision_id == fixture.in_flight]
    ledger = _ledger_for(fixture, conn, as_of or LATER)(S)
    return Derived(
        decisions=states,
        remainder=plan.remainder(
            trim, orders, order_events, fills, actions, closes.__getitem__, session=S
        ),
        residue=plan.residue(
            fixture.held[0],
            adjustments,
            decisions,
            events,
            positions_daily,
            ledger,
            actions,
            window_id=window_id,
            runs=runs,
        ),
        rebalance=plan.rebalance_state(
            T_PREV,
            fixture.window,
            runs,
            rebalance_events,
            [(d, states[d.decision_id]) for d in decisions if d.decision_id is not None],
            session=S,
            cadence="month_end",
        ),
        switch=switch.derive(
            fixture.window,
            kill_switch,
            runs,
            results,
            reading_run=fixture.run_s,
            lock_free=True,
        ),
    )


@dataclass(frozen=True)
class _StopAsset:
    """A stand-in `ExitAsset` (module docstring): not journal data, so it is
    the same whichever store is read."""

    tradable: bool = True
    fractionable: bool = True


def _closes_at(
    conn: duckdb.DuckDBPyConnection, names: Sequence[str], session: date
) -> dict[str, float]:
    return {
        row["security_id"]: float(row["close"])
        for row in prices_as_of(conn, CUT, list(names))
        .filter(pl.col("session") == session)
        .iter_rows(named=True)
    }


def _outcomes_for(
    fixture: Fixture, conn: duckdb.DuckDBPyConnection, as_of: datetime | None
) -> list[outcomes.Outcome]:
    """`due_outcomes` for `fixture`'s window as if S were a `stop` run with the
    stop requested at T_PREV (module docstring: #598, no `stop` run of its own
    exists in this window, so `OutcomeWindow` is given by hand). Store facts
    (`actions`, the reference closes) are read at close(S-1) regardless of
    `as_of`, as a run always reads them; the journal rows are cut at `as_of`
    (`_known`), or not cut again when `conn` already is (`cut`, `as_of=None`)."""
    window_id = fixture.window_id
    decisions = _known([d.decision for d in journal.decisions_for(conn, window_id)], as_of)
    order_events = _known(journal.order_events_for(conn, window_id=window_id), as_of)
    orders = _known(journal.orders_for(conn, window_id=window_id), as_of)
    fills = [
        f
        for f in journal.fills_for(conn, window_id=window_id)
        if as_of is None or f.fill.known_at <= as_of
    ]
    marks = _known(journal.positions_daily_for(conn, window_id), as_of)
    actions = live_actions_as_of(conn, CUT)
    return outcomes.due_outcomes(
        OutcomeWindow(window_id=window_id, stop_requested=T_PREV),
        orders,
        order_events,
        fills,
        marks,
        None,
        lambda sid, day: _closes_at(conn, [sid], day).get(sid),
        S,
        decisions=decisions,
        actions=actions,
        cadence="month_end",
    )


def _stop_exits_for(
    fixture: Fixture, conn: duckdb.DuckDBPyConnection, as_of: datetime | None
) -> list[ExitDecision]:
    """`exits.stop_exits` for `fixture`'s two held names, the same rows and cut
    rule as `_outcomes_for` and `_derived`."""
    window_id = fixture.window_id
    with_events = journal.decisions_for(conn, window_id)
    decisions = _known([d.decision for d in with_events], as_of)
    events = _known([e for d in with_events for e in d.events], as_of)
    orders = _known(journal.orders_for(conn, window_id=window_id), as_of)
    order_events = _known(journal.order_events_for(conn, window_id=window_id), as_of)
    fills = [
        f
        for f in journal.fills_for(conn, window_id=window_id)
        if as_of is None or f.fill.known_at <= as_of
    ]
    actions = live_actions_as_of(conn, CUT)
    closes = _closes_at(conn, [d.security_id for d in decisions], S_PREV)
    states = {
        d.decision_id: plan.decision_state(
            d, events, orders, order_events, fills, actions, closes.__getitem__, FROZEN, session=S
        )
        for d in decisions
        if d.decision_id is not None
    }
    runs = _known([r.run for r in journal.runs_for(conn, window_id)], as_of)
    ledger = _ledger_for(fixture, conn, as_of or LATER)(S)
    held = {sid: ledger.positions.get(sid, 0.0) for sid in fixture.held}
    adjustments = _known(journal.adjustments_for(conn, window_id), as_of)
    marks = _known(journal.positions_daily_for(conn, window_id), as_of)
    residues = {
        sid: plan.residue(
            sid,
            adjustments,
            decisions,
            events,
            marks,
            ledger,
            actions,
            window_id=window_id,
            runs=runs,
        )
        for sid in fixture.held
    }
    open_sells = unfilled_sells(orders, order_events, fills, closes.__getitem__, actions, session=S)
    assets: dict[str, ExitAsset] = dict.fromkeys(fixture.held, _StopAsset())
    return exits.stop_exits(
        held,
        residues,
        assets,
        decisions,
        states,
        open_sells,
        (),
        session=S,
        quantity_decimals=6,
    )


# --- the checks --------------------------------------------------------------------


def test_every_injected_fact_is_live(fixture: Fixture, cut: duckdb.DuckDBPyConnection) -> None:
    """Each late row is on the full store and absent from the cut, so the equalities
    below are the pieces ignoring them."""
    late = (
        "fills",
        "orders",
        "order_events",
        "decisions",
        "rebalance_events",
        "adjustments",
        "kill_switch",
        "reconciliations",
        "paper_runs",
        "positions_daily",
    )
    for table in late:
        full_n = fixture.full.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        cut_n = cut.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        assert full_n is not None and cut_n is not None
        assert full_n[0] > cut_n[0], table
    assert fixture.revised_close != fixture.original_close
    assert _bar(fixture.full, fixture.held[0], S_PREV, CUT)["close"] == fixture.original_close
    actions = live_actions_as_of(fixture.full, LATER)
    anchor_cut = _bar(fixture.full, fixture.anchored, ANCHOR, CUT)["close"]
    assert _bar(fixture.full, fixture.anchored, ANCHOR, LATER)["close"] != anchor_cut
    assert _bar(cut, fixture.anchored, ANCHOR, CUT)["close"] == anchor_cut
    for sid, kind in ((fixture.held[1], "split"), (fixture.held[0], "dividend")):
        assert not actions.filter(
            (pl.col("security_id") == sid) & (pl.col("action_type") == kind)
        ).is_empty()
    cut_actions = live_actions_as_of(cut, CUT)
    assert (
        cut_actions.filter(pl.col("ex_date") >= S_PREV)
        .filter(pl.col("security_id").is_in([fixture.held[1], fixture.new]))
        .filter(pl.col("known_at") > CUT)
        .is_empty()
    )
    assert cut_actions.filter(pl.col("known_at") > CUT).is_empty()


def test_the_plan_is_unchanged_on_the_cut(fixture: Fixture, cut: duckdb.DuckDBPyConnection) -> None:
    full = _plan(fixture, fixture.full)
    assert full == _plan(fixture, cut)
    assert full.targets  # the comparison covers a plan that trades


def test_the_screened_plan_is_unchanged_on_the_cut(
    fixture: Fixture, cut: duckdb.DuckDBPyConnection
) -> None:
    """The paper plan of a registration with the turnover screen on (T165d) reads the
    same on the cut as on the full store, and the screen is live: it changes the plan."""
    screened = Settings(
        _env_file=None, strategy={"top_fraction": 0.5, "turnover_top_fraction": 0.5}
    )
    on_full = _plan_at(fixture.full, fixture.full, screened, fixture.handle, T_I)
    on_cut = _plan_at(cut, fixture.full, screened, fixture.handle, T_I)
    assert on_full == on_cut
    assert on_full.targets
    assert on_full.counts.get("n_screened", 0) > 0
    assert on_full != _plan(fixture, fixture.full)


def test_the_ledger_through_s_minus_1_is_unchanged_on_the_cut(
    fixture: Fixture, cut: duckdb.DuckDBPyConnection
) -> None:
    full = _ledger_for(fixture, fixture.full, CUT)(S_PREV)
    assert full == _ledger_for(fixture, cut, LATER)(S_PREV)
    # Neither the trim filled on S-1 but collected on S, nor S's buy, nor the late split.
    assert full.positions == {sid: QUANTITY for sid in fixture.held}
    # Read past the cut, the collected trim is in it.
    after = _ledger_for(fixture, fixture.full, LATER)(S_PREV)
    assert after.positions[fixture.held[1]] == pytest.approx(QUANTITY - SOLD)


def test_the_ledger_through_s_is_unchanged_on_the_cut(
    fixture: Fixture, cut: duckdb.DuckDBPyConnection
) -> None:
    full = _ledger_for(fixture, fixture.full, CUT)(S)
    assert full == _ledger_for(fixture, cut, LATER)(S)
    assert set(full.positions) == set(fixture.held)
    after = _ledger_for(fixture, fixture.full, LATER)(S)
    assert fixture.new in after.positions and after.cash != full.cash


def test_the_explanations_are_unchanged_on_the_cut(
    fixture: Fixture, cut: duckdb.DuckDBPyConnection
) -> None:
    full = _explain(fixture, fixture.full, CUT)
    assert full == _explain(fixture, cut, LATER)
    # The late dividend, the late split and S's own rows explain nothing ...
    assert set(full.symbols) == set(fixture.held)
    # (held[0]'s dividend is known only after the cut; held[1]'s holding is pre-split)
    assert full.dividends == pytest.approx({fixture.held[1]: QUANTITY * DIVIDEND})
    assert full.reference_prices[fixture.held[0]] == fixture.original_close
    # ... and read past the cut, S's rows do: a new name, S's reconciliation as the base.
    after = _explain(fixture, fixture.full, LATER)
    assert fixture.new in after.symbols
    assert after.dividends == {}


def test_the_marks_are_unchanged_on_the_cut(
    fixture: Fixture, cut: duckdb.DuckDBPyConnection
) -> None:
    full = _marks(fixture, fixture.full, CUT)
    assert full == _marks(fixture, cut, LATER)
    assert {m.security_id for m in full} == set(fixture.held)
    [last] = [m for m in full if m.session == S_PREV and m.security_id == fixture.held[0]]
    assert last.mark_price == fixture.original_close
    # Read past the cut, the S-1 mark holds the collected trim.
    after = _marks(fixture, fixture.full, LATER)
    assert after != full


def test_no_derived_state_changes_on_the_cut(
    fixture: Fixture, cut: duckdb.DuckDBPyConnection
) -> None:
    full = _derived(fixture, fixture.full, CUT)
    assert full == _derived(fixture, cut, None)
    assert full.decisions[fixture.in_flight].state == plan.State.IN_FLIGHT
    assert full.remainder.quantity == pytest.approx(SOLD)  # the late split not applied
    assert full.residue == 0.0
    assert full.rebalance == plan.RebalanceState.PENDING
    assert not full.switch.engaged
    # Read uncut, every one of them changes.
    uncut = _derived(fixture, fixture.full, None)
    assert uncut.decisions[fixture.in_flight].state == plan.State.SETTLED
    assert uncut.residue == pytest.approx(1.0)
    assert uncut.rebalance == plan.RebalanceState.EXECUTED
    assert uncut.switch.engaged


def test_a_late_corporate_action_is_used_by_no_explanation_and_no_remainder(
    fixture: Fixture, cut: duckdb.DuckDBPyConnection
) -> None:
    """The split (ex-date S-1) and the dividend (ex-date S) known after the cut: read
    with actions at close(S) instead, the trim's remainder doubles."""
    full = _derived(fixture, fixture.full, CUT)
    assert full.remainder == _derived(fixture, cut, None).remainder
    assert fixture.held[0] not in _explain(fixture, fixture.full, CUT).dividends
    with_events = journal.decisions_for(fixture.full, fixture.window_id)
    [trim] = [d.decision for d in with_events if d.decision.decision_id == fixture.in_flight]
    orders = _known(journal.orders_for(fixture.full, window_id=fixture.window_id), CUT)
    late = plan.remainder(
        trim, orders, [], [], live_actions_as_of(fixture.full, LATER), lambda _s: 1.0, session=S
    )
    assert late.quantity == pytest.approx(2 * SOLD)


def test_a_late_bar_revision_changes_the_next_run_only(
    fixture: Fixture, cut: duckdb.DuckDBPyConnection
) -> None:
    """Invisible to the run on S (checked above), the revised S-1 bar is the reference
    price the run on S+1 reads for that name, which has no bar for S."""
    on_s = _explain(fixture, fixture.full, CUT)
    on_next = _explain(fixture, fixture.full, session_close(S), session=S_NEXT)
    assert on_s.reference_prices[fixture.held[0]] == fixture.original_close
    assert on_next.reference_prices[fixture.held[0]] == fixture.revised_close


def test_a_stop_run_s_outcomes_and_exits_are_unchanged_on_the_cut(
    fixture: Fixture, cut: duckdb.DuckDBPyConnection
) -> None:
    """A `stop` run's own two pure pieces (module docstring; #598), given this
    window by hand as if S were a stop requested at T_PREV: `due_outcomes` for
    the F_PREV buys of both held names (due at T_I, before S), and
    `stop_exits` over the ledger held through S. Both are pure over the rows
    they are given, cut the same way as `_derived`, so reading them at `CUT`
    on the full store agrees with the cut store."""
    security_of = {
        o.client_order_id: o.security_id
        for o in journal.orders_for(fixture.full, window_id=fixture.window_id)
    }
    full_outcomes = _outcomes_for(fixture, fixture.full, CUT)
    assert full_outcomes == _outcomes_for(fixture, cut, None)
    by_security = {
        security_of[o.client_order_id]: o
        for o in full_outcomes
        if o.kind == outcomes.POSITION_RETURN
    }
    assert set(by_security) == set(fixture.held)
    assert all(o.value is not None for o in by_security.values())

    full_exits = _stop_exits_for(fixture, fixture.full, CUT)
    assert full_exits == _stop_exits_for(fixture, cut, None)
    # held[1]'s trim is still open at the cut (its fill is known only after it):
    # `stop_exits` makes no exit for a name with a non-terminal own sell.
    assert {e.security_id for e in full_exits} == {fixture.held[0]}

    # Read past the cut, both pieces change: held[1]'s split (ex-date S-1, known
    # only after the cut) is applied to its F_PREV buy's position_return ...
    late_outcomes = outcomes.due_outcomes(
        OutcomeWindow(window_id=fixture.window_id, stop_requested=T_PREV),
        journal.orders_for(fixture.full, window_id=fixture.window_id),
        journal.order_events_for(fixture.full, window_id=fixture.window_id),
        journal.fills_for(fixture.full, window_id=fixture.window_id),
        journal.positions_daily_for(fixture.full, fixture.window_id),
        None,
        lambda sid, day: _closes_at(fixture.full, [sid], day).get(sid),
        S,
        decisions=[d.decision for d in journal.decisions_for(fixture.full, fixture.window_id)],
        actions=live_actions_as_of(fixture.full, LATER),
        cadence="month_end",
    )
    late_by_security = {
        security_of[o.client_order_id]: o
        for o in late_outcomes
        if o.kind == outcomes.POSITION_RETURN
    }
    assert late_by_security[fixture.held[1]].value != by_security[fixture.held[1]].value
    # ... and held[1]'s trim, filled and collected past the cut, is no longer an open
    # sell, so the stop gets an exit for it too.
    late_exits = _stop_exits_for(fixture, fixture.full, None)
    assert {e.security_id for e in late_exits} == set(fixture.held)


# --- `paper settle` (T84b, spec req 17, #571) --------------------------------------------


class _SettleClock:
    """A settable clock (the base `Ticking` moves)."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def test_a_cut_at_the_settle_gates_reading_does_not_see_its_rows(
    fixture_store_path: Path,
) -> None:
    """Both rows `paper settle` writes are stamped after the gate's clock
    reading, so the journal cut at that reading shows the order still
    non-terminal and no `settle_order` override; past the stamp it shows both."""
    from execution.test_window_settle import DAY1, NOTE, Settle, Ticking, new_window

    from tradepartner.adapters.fake_broker import FakeBroker, Vanish
    from tradepartner.execution.window import settle_order
    from tradepartner.store.db import open_read_only

    settings = Settings(_env_file=None, store={"path": str(fixture_store_path)})
    base = _SettleClock(DAY1)
    fake = FakeBroker(clock=base, price_of=lambda _s: 100.0, auto_fill=False, account_id="PA1")
    s = Settle(settings, base, fake, new_window(settings))
    s.place("tp-settle")
    fake.apply("tp-settle", Vanish())
    s.engage()
    clock = Ticking(base)
    result = settle_order(settings, s.connect, fake, clock, "tp-settle", NOTE)
    gate = clock.readings[0]
    assert result.known_at > gate

    def seen(conn: duckdb.DuckDBPyConnection) -> tuple[int, int, list[str]]:
        (overrides,) = conn.execute(  # type: ignore[misc]
            "SELECT count(*) FROM overrides WHERE kind = 'settle_order'"
        ).fetchone()
        (events,) = conn.execute(  # type: ignore[misc]
            "SELECT count(*) FROM order_events WHERE reason = 'owner_settled_unknown'"
        ).fetchone()
        open_ids = [o.client_order_id for o in journal.non_terminal_orders(conn, window_id=None)]
        return int(overrides), int(events), open_ids

    with open_read_only(settings) as full:
        store = TruncatedStore(full, tables=CUT_TABLES)
        try:
            assert seen(store.at(gate)) == (0, 0, ["tp-settle"])
            assert seen(store.at(result.known_at)) == (1, 1, [])
        finally:
            store.close()
