"""The chain-aware reads refuse a successor chain they cannot trust (#3097).

Audit A scenario s10b: a bankrupt predecessor (OLD, last print 1.0, equity cancelled)
chained to the post-reorganisation equity (NEW, first print 25) turned a -50% book into
+12.4%, because every chain known at `t` was applied 1:1 without a check. A chain whose
predecessor's latest Form 25 episode has a reason other than a merger or a voluntary
delisting (`bankruptcy`, `performance_failure`) at `t`, or whose hand-over comes more than
`master.chain_window_sessions` sessions after the predecessor's end (last bar or Form 25
effective day), or whose hand-over close breaks one share for one share (#3108: the
successor's first close over the predecessor's last, after any split or spin-off between
them known at `t`, outside `master.chain_max_price_ratio` either way), is refused:
`live_chains` leaves it out, so no read re-keys the predecessor and its own bars
stay its own (the engine then exits it by the normal delisting path). `stored_chains` still
names it, and `refused_chains` says why. Legitimate chains (a same-ticker holding-company
reorganisation such as XOM's, with a Form 25 and no reason row; a merger-reason
predecessor) still read live, and the chain overlay's tests keep passing unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import pytest
from lookahead.harness import PROBE_EPSILON

from tradepartner.calendar import session_close
from tradepartner.execution.reconcile_run import _one_successor_chains
from tradepartner.store import schema
from tradepartner.store.asof import (
    RefusedChain,
    chained_read_ids,
    facts_as_of,
    live_chains,
    prices_as_of,
    refused_chains,
    stored_chains,
)
from tradepartner.store.db import insert_row

OLD, NEW = "0000000100", "0000000200"
CHAIN_KNOWN = datetime(2023, 3, 2, 21, tzinfo=UTC)
FORM25_FILED = datetime(2023, 3, 3, 22, tzinfo=UTC)
REASON_KNOWN = datetime(2023, 3, 6, 21, tzinfo=UTC)
T = session_close(date(2023, 3, 31))


def _fact(known_at: datetime) -> dict[str, object]:
    return {
        "known_at": known_at,
        "ingested_at": known_at,
        "source": "edgar",
        "provenance": "filing",
    }


def _store(
    *,
    successor_from: date = date(2023, 3, 1),
    successor_known: datetime = datetime(2023, 2, 27, 13, tzinfo=UTC),
    chain_known: datetime = CHAIN_KNOWN,
    successor_close: float = 1.02,
) -> duckdb.DuckDBPyConnection:
    """s10b's shape: OLD (ABC, NYSE) prints 1.2, 1.0 through 2023-02-28, its Form 25 is
    filed 03-03; NEW (ABC, NYSE) is listed from `successor_from` and prints
    `successor_close` there (1.02, one share for one share; s10b's is 25); a chain OLD ->
    NEW is known at `chain_known`. No reason row yet."""
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    for sid, valid_from, known_at in (
        (OLD, date(2022, 1, 3), datetime(2022, 12, 30, 21, tzinfo=UTC)),
        (NEW, successor_from, successor_known),
    ):
        insert_row(
            conn,
            "securities",
            {"security_id": sid, "cik": sid, "name": sid} | _fact(known_at) | {"benchmark": False},
        )
        insert_row(
            conn,
            "listings",
            {
                "security_id": sid,
                "ticker": "ABC",
                "exchange": "NYSE",
                "class_title": "Common Stock",
                "valid_from": valid_from,
            }
            | _fact(known_at),
        )
    bars = [
        (OLD, date(2023, 2, 27), 1.2),
        (OLD, date(2023, 2, 28), 1.0),
        (NEW, successor_from, successor_close),
    ]
    for sid, session, close in bars:
        _bar(conn, sid, session, close)
    insert_row(
        conn,
        "delistings",
        {
            "security_id": OLD,
            "form": "25",
            "class_title": "Common Stock",
            "exchange": "NYSE",
            "filed_at": FORM25_FILED,
            "effective_on": date(2023, 3, 13),
        }
        | _fact(FORM25_FILED),
    )
    insert_row(
        conn,
        "successor_chains",
        {
            "predecessor_id": OLD,
            "successor_id": NEW,
            "evidence_accession": "0000000000-23-000001",
            "evidence_form": "8-K12B",
            "retracted": False,
            "withdrawn_known_at": None,
        }
        | _fact(chain_known),
    )
    return conn


def _bar(
    conn: duckdb.DuckDBPyConnection,
    sid: str,
    session: date,
    close: float,
    known: datetime | None = None,
) -> None:
    known = known or session_close(session)
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": sid,
            "session": session,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "known_at": known,
            "ingested_at": known,
            "source": "alpaca",
            "provenance": "bar",
        },
    )


def _action(
    conn: duckdb.DuckDBPyConnection,
    sid: str,
    action_type: str,
    ex_date: date,
    ratio: float,
    known: datetime,
    parent: str = "",
) -> None:
    insert_row(
        conn,
        "corporate_actions",
        {
            "security_id": sid,
            "action_type": action_type,
            "ex_date": ex_date,
            "ratio_or_amount": ratio,
            "source_action_id": parent,
            "announced_at": None,
            "cancelled": False,
            "known_at": known,
            "ingested_at": known,
            "source": "alpaca",
            "provenance": "action",
        },
    )


def _reason(
    conn: duckdb.DuckDBPyConnection,
    reason: str,
    known_at: datetime = REASON_KNOWN,
    exchange: str = "NYSE",
) -> None:
    insert_row(
        conn,
        "delisting_reasons",
        {
            "security_id": OLD,
            "delisting_form": "25",
            "delisting_class_title": "Common Stock",
            "delisting_exchange": exchange,
            "delisting_filed_at": FORM25_FILED,
            "reason": reason,
            "reason_evidence_form": "8-K",
            "reason_evidence_accession": "0000000100-23-000009",
            "reason_source": "eightk_item",
            "reason_known_at": known_at,
        }
        | _fact(known_at),
    )


@pytest.fixture
def conn() -> Iterator[duckdb.DuckDBPyConnection]:
    store = _store()
    try:
        yield store
    finally:
        store.close()


@pytest.mark.parametrize("reason", ["bankruptcy", "performance_failure"])
def test_a_failed_predecessor_chain_is_refused(
    conn: duckdb.DuckDBPyConnection, reason: str
) -> None:
    """s10b with its bankruptcy evidence: the chain is not live, so OLD's own bars read as
    OLD's (the engine can exit it at 1.0) and nothing is filed under NEW."""
    _reason(conn, reason)
    assert stored_chains(conn, T) == ((OLD, NEW),)
    assert live_chains(conn, T) == ()
    assert refused_chains(conn, T) == (RefusedChain(OLD, NEW, f"predecessor_reason:{reason}"),)
    assert chained_read_ids(conn, T, [OLD]) == ({OLD: OLD}, {})
    prices = prices_as_of(conn, T, [OLD, NEW]).sort("security_id", "session")
    assert prices.select("security_id", "close").rows() == [(OLD, 1.2), (OLD, 1.0), (NEW, 1.02)]


@pytest.mark.parametrize("reason", ["merger_acquisition", "voluntary_other"])
def test_a_merger_or_voluntary_predecessor_chain_stays_live(
    conn: duckdb.DuckDBPyConnection, reason: str
) -> None:
    """An acquirer or rename chain: the predecessor's Form 25 reads as a merger or a
    voluntary delisting, so the chain reads live and the predecessor's bars are NEW's."""
    _reason(conn, reason)
    assert live_chains(conn, T) == ((OLD, NEW),)
    assert refused_chains(conn, T) == ()
    assert set(prices_as_of(conn, T, [NEW])["close"].to_list()) == {1.2, 1.0, 1.02}


def test_a_form25_without_a_reason_row_stays_live(conn: duckdb.DuckDBPyConnection) -> None:
    """XOM's shape (#2963): a Form 25 on the old CIK, no reason row (absence reads as
    unknown, the store's state today), the successor listed the session after the last
    print. Nothing says the predecessor failed, so the owner-reviewed chain stays live."""
    assert live_chains(conn, T) == ((OLD, NEW),)
    assert refused_chains(conn, T) == ()


def test_an_overlapping_hand_over_stays_live() -> None:
    """XOM's other shape: the successor's first listing row is on or before the
    predecessor's last print (a same-day hand-over); a gap of 0 sessions is in window."""
    conn = _store(
        successor_from=date(2023, 2, 28), successor_known=datetime(2023, 2, 24, tzinfo=UTC)
    )
    try:
        assert live_chains(conn, T) == ((OLD, NEW),)
    finally:
        conn.close()


def test_a_hand_over_beyond_the_window_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """NEW is first listed 2023-08-01, 97 sessions after OLD's end (its Form 25's
    effective day 03-13, later than its last print 02-28): beyond
    `master.chain_window_sessions` (65) the chain is refused; the window is config, so 110
    admits it."""
    t = session_close(date(2023, 8, 31))
    conn = _store(
        successor_from=date(2023, 8, 1),
        successor_known=datetime(2023, 7, 28, tzinfo=UTC),
        chain_known=datetime(2023, 8, 2, 21, tzinfo=UTC),
    )
    try:
        assert stored_chains(conn, t) == ((OLD, NEW),)
        assert live_chains(conn, t) == ()
        assert refused_chains(conn, t) == (RefusedChain(OLD, NEW, "hand_over_window:97"),)
        monkeypatch.setenv("MASTER__CHAIN_WINDOW_SESSIONS", "110")
        assert live_chains(conn, t) == ((OLD, NEW),)
        assert refused_chains(conn, t) == ()
    finally:
        conn.close()


def test_the_refusal_is_point_in_time(conn: duckdb.DuckDBPyConnection) -> None:
    """No look-ahead: the bankruptcy reason refuses the chain only from its own `known_at`.
    Before it the chain is live (it is all a reader at that `t` could know), and before the
    chain's stamp there is no chain at all."""
    _reason(conn, "bankruptcy")
    assert stored_chains(conn, CHAIN_KNOWN - PROBE_EPSILON) == ()
    assert live_chains(conn, CHAIN_KNOWN) == ((OLD, NEW),)
    assert live_chains(conn, REASON_KNOWN - PROBE_EPSILON) == ((OLD, NEW),)
    assert refused_chains(conn, REASON_KNOWN - PROBE_EPSILON) == ()
    assert live_chains(conn, REASON_KNOWN) == ()


def test_a_later_reason_revision_decides(conn: duckdb.DuckDBPyConnection) -> None:
    """The latest reason revision known at `t` is the one judged: a merger reason revised to
    bankruptcy refuses the chain from the revision on."""
    _reason(conn, "merger_acquisition")
    later = datetime(2023, 3, 20, 21, tzinfo=UTC)
    _reason(conn, "bankruptcy", later)
    assert live_chains(conn, later - PROBE_EPSILON) == ((OLD, NEW),)
    assert live_chains(conn, later) == ()


def test_a_successor_listed_only_after_t_is_not_anticipated() -> None:
    """The window judges only listings known at `t`: NEW's far listing row (known
    2023-08-15) is invisible at the 07-31 read, so nothing refuses the chain there; from
    the row's `known_at` the gap is seen and the chain is refused."""
    known = datetime(2023, 8, 15, 21, tzinfo=UTC)
    conn = _store(
        successor_from=date(2023, 8, 16),
        successor_known=known,
        chain_known=datetime(2023, 7, 3, 21, tzinfo=UTC),
    )
    try:
        assert live_chains(conn, session_close(date(2023, 7, 31))) == ((OLD, NEW),)
        assert live_chains(conn, known - PROBE_EPSILON) == ((OLD, NEW),)
        assert [r.why for r in refused_chains(conn, known)] == ["hand_over_window:108"]
    finally:
        conn.close()


def test_the_window_falls_back_to_the_successors_first_listing() -> None:
    """A successor listed under another ticker (no row under the predecessor's ticker and
    exchange) is judged from its first listing row."""
    conn = _store(
        successor_from=date(2023, 8, 1),
        successor_known=datetime(2023, 7, 28, tzinfo=UTC),
        chain_known=datetime(2023, 8, 2, 21, tzinfo=UTC),
    )
    try:
        conn.execute("UPDATE listings SET ticker = 'XYZ' WHERE security_id = ?", [NEW])
        t = session_close(date(2023, 8, 31))
        assert refused_chains(conn, t) == (RefusedChain(OLD, NEW, "hand_over_window:97"),)
    finally:
        conn.close()


def test_same_time_episodes_fail_closed(conn: duckdb.DuckDBPyConnection) -> None:
    """Two Form 25 episodes filed at the same latest time (NYSE and NASDAQ): a refusing
    reason on either refuses, whichever row sorts last."""
    insert_row(
        conn,
        "delistings",
        {
            "security_id": OLD,
            "form": "25",
            "class_title": "Common Stock",
            "exchange": "NASDAQ",
            "filed_at": FORM25_FILED,
            "effective_on": date(2023, 3, 13),
        }
        | _fact(FORM25_FILED),
    )
    _reason(conn, "bankruptcy", exchange="NASDAQ")
    _reason(conn, "merger_acquisition", exchange="NYSE")
    assert live_chains(conn, T) == ()
    assert [r.why for r in refused_chains(conn, T)] == ["predecessor_reason:bankruptcy"]


def test_a_fork_with_one_refused_chain_maps_to_the_other() -> None:
    """OLD has two stored chains: NEW (hand-over the next session) and NEW2 (97 sessions
    after OLD's end). Only NEW2's is refused, so OLD is no fork at `t`: the reads and paper's
    reconcile map it to NEW, never to the refused successor."""
    t = session_close(date(2023, 8, 31))
    conn = _store()
    try:
        new2, known = "0000000300", datetime(2023, 7, 28, tzinfo=UTC)
        insert_row(
            conn,
            "listings",
            {
                "security_id": new2,
                "ticker": "ABC",
                "exchange": "NYSE",
                "class_title": "Common Stock",
                "valid_from": date(2023, 8, 1),
            }
            | _fact(known),
        )
        insert_row(
            conn,
            "successor_chains",
            {
                "predecessor_id": OLD,
                "successor_id": new2,
                "evidence_accession": "0000000300-23-000001",
                "evidence_form": "8-K12B",
                "retracted": False,
                "withdrawn_known_at": None,
            }
            | _fact(datetime(2023, 8, 2, 21, tzinfo=UTC)),
        )
        assert stored_chains(conn, t) == ((OLD, NEW), (OLD, new2))
        assert live_chains(conn, t) == ((OLD, NEW),)
        assert chained_read_ids(conn, t, [OLD]) == ({OLD: NEW}, {})
        assert _one_successor_chains(conn, t) == ({OLD: NEW}, frozenset())
    finally:
        conn.close()


def test_the_resolver_facts_follow_every_stored_chain(conn: duckdb.DuckDBPyConnection) -> None:
    """The ingest's resolver keeps its pre-#3097 view (`every_stored_chain`, beside rule
    9's `stored_chains`): a refused chain still re-keys OLD's facts there, while the
    chain-aware reads keep them OLD's own."""
    insert_row(
        conn,
        "facts",
        {
            "security_id": OLD,
            "fact_name": "shares_outstanding",
            "as_of_date": date(2023, 1, 31),
            "class_member": "",
            "value": 1_000_000.0,
            "filing_accession": "0000000100-23-000002",
        }
        | _fact(datetime(2023, 2, 10, 21, tzinfo=UTC)),
    )
    _reason(conn, "bankruptcy")
    assert facts_as_of(conn, T)["security_id"].to_list() == [OLD]
    assert facts_as_of(conn, T, every_stored_chain=True)["security_id"].to_list() == [NEW]


def test_a_later_notes_form25_neither_hides_the_reason_nor_moves_the_end(
    conn: duckdb.DuckDBPyConnection,
) -> None:
    """A debt class's Form 25 filed after the equity's (notes, no reason row, effective
    months later) is ignored: the equity's bankruptcy reason still refuses the chain, and
    without it the notes' effective day never stretches the predecessor's end."""
    notes = datetime(2023, 3, 20, 21, tzinfo=UTC)
    insert_row(
        conn,
        "delistings",
        {
            "security_id": OLD,
            "form": "25",
            "class_title": "5.25% Senior Notes due 2030",
            "exchange": "NYSE",
            "filed_at": notes,
            "effective_on": date(2023, 12, 29),
        }
        | _fact(notes),
    )
    _reason(conn, "bankruptcy")
    assert [r.why for r in refused_chains(conn, T)] == ["predecessor_reason:bankruptcy"]


def test_a_notes_form25_does_not_stretch_the_window() -> None:
    t = session_close(date(2023, 8, 31))
    conn = _store(
        successor_from=date(2023, 8, 1),
        successor_known=datetime(2023, 7, 28, tzinfo=UTC),
        chain_known=datetime(2023, 8, 2, 21, tzinfo=UTC),
    )
    try:
        notes = datetime(2023, 7, 20, 21, tzinfo=UTC)
        insert_row(
            conn,
            "delistings",
            {
                "security_id": OLD,
                "form": "25",
                "class_title": "5.25% Senior Notes due 2030",
                "exchange": "NYSE",
                "filed_at": notes,
                "effective_on": date(2023, 7, 31),
            }
            | _fact(notes),
        )
        assert [r.why for r in refused_chains(conn, t)] == ["hand_over_window:97"]
    finally:
        conn.close()


# --- the hand-over close (#3108) ---------------------------------------------------


def _price_store(successor_close: float) -> duckdb.DuckDBPyConnection:
    return _store(successor_close=successor_close)


def test_s10b_without_a_reason_is_refused_on_the_hand_over_close() -> None:
    """Audit A s10b as written: no reason row, OLD's last close 1.0, NEW's first 25. A 1:1
    rename cannot jump x25, so the chain is refused and OLD's bars stay its own."""
    conn = _price_store(25.0)
    try:
        assert live_chains(conn, T) == ()
        assert refused_chains(conn, T) == (RefusedChain(OLD, NEW, "hand_over_price:25"),)
        assert chained_read_ids(conn, T, [OLD]) == ({OLD: OLD}, {})
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("close", "live"), [(1.10, True), (0.90, True), (1.20, False), (0.85, False)]
)
def test_the_ratio_bound_is_either_way(close: float, live: bool) -> None:
    """Inside [1/1.15, 1.15] the chain is live; outside it either way it is refused."""
    conn = _price_store(close)
    try:
        assert (live_chains(conn, T) == ((OLD, NEW),)) is live
    finally:
        conn.close()


def test_the_ratio_bound_is_config(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _price_store(1.20)
    try:
        assert live_chains(conn, T) == ()
        monkeypatch.setenv("MASTER__CHAIN_MAX_PRICE_RATIO", "1.25")
        assert live_chains(conn, T) == ((OLD, NEW),)
    finally:
        conn.close()


def test_a_rename_with_a_same_day_split_passes() -> None:
    """NEW opens 2-for-1 on the hand-over (ex 03-01, ratio 2): its 0.51 is OLD's 1.0 / 2,
    one share for one share before the split, so the chain is live."""
    conn = _price_store(0.51)
    try:
        _action(conn, NEW, "split", date(2023, 3, 1), 2.0, datetime(2023, 3, 1, 12, tzinfo=UTC))
        assert live_chains(conn, T) == ((OLD, NEW),)
    finally:
        conn.close()


def test_a_split_known_after_t_is_not_applied() -> None:
    conn = _price_store(0.51)
    try:
        late = datetime(2023, 4, 3, 21, tzinfo=UTC)
        _action(conn, NEW, "split", date(2023, 3, 1), 2.0, late)
        assert [r.why for r in refused_chains(conn, T)] == ["hand_over_price:0.51"]
        assert live_chains(conn, late) == ((OLD, NEW),)
    finally:
        conn.close()


def test_a_rename_with_a_same_day_spin_off_passes() -> None:
    """NEW goes ex a spin-off on the hand-over (0.5 CHILD per share, CHILD closes 0.4):
    OLD's 1.0 is worth 1.0 - 0.2 = 0.8 ex, and NEW's 0.8 matches it."""
    conn = _price_store(0.8)
    try:
        child = "0000000900"
        _bar(conn, child, date(2023, 3, 1), 0.4)
        _action(
            conn,
            child,
            "spinoff",
            date(2023, 3, 1),
            0.5,
            datetime(2023, 2, 20, tzinfo=UTC),
            parent=NEW,
        )
        assert live_chains(conn, T) == ((OLD, NEW),)
    finally:
        conn.close()


def test_a_successor_with_no_bar_known_at_t_is_not_judged() -> None:
    """No evidence passes: while NEW's first bar is not yet known (stamped 03-10, after
    the chain's 03-02) there is no ratio to judge; from its stamp the x25 is refused."""
    conn = _price_store(25.0)
    try:
        late = datetime(2023, 3, 10, 21, tzinfo=UTC)
        conn.execute("DELETE FROM prices_daily WHERE security_id = ?", [NEW])
        _bar(conn, NEW, date(2023, 3, 1), 25.0, known=late)
        assert live_chains(conn, late - PROBE_EPSILON) == ((OLD, NEW),)
        assert live_chains(conn, late) == ()
    finally:
        conn.close()


def test_a_close_judged_across_a_long_gap_is_not_a_hand_over() -> None:
    """OLD's last bar 02-28, NEW's first 03-31 (22 sessions, beyond
    `master.transfer_window_sessions`, 5): the x25 measures the gap, not the hand-over,
    so the price check does not judge it (the overlay's acquirer pair, #3006, has a 21
    session gap); within the span it is judged."""
    conn = _price_store(25.0)
    try:
        conn.execute("DELETE FROM prices_daily WHERE security_id = ?", [NEW])
        _bar(conn, NEW, date(2023, 3, 31), 25.0)
        assert live_chains(conn, T) == ((OLD, NEW),)
    finally:
        conn.close()
