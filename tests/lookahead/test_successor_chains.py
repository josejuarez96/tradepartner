"""Look-ahead for `successor_chains` (#821, plan T187).

The row's `known_at` is the latest of the four evidence stamps (the fixture
carries one row per chainable pair), so a read before it must see two
securities; the truncation harness places a probe just before and just after
it. There is no `successor_chains` reader yet (T187b), so this pins the
harness membership and the row's `known_at` boundaries: the stored row is
absent strictly before its `known_at` and present from it.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from lookahead.harness import PROBE_EPSILON, TruncatedStore
from tradepartner.store import schema
from tradepartner.store.master import Chain, MasterBuild, write_master

#: The fixture's chainable pairs and their `successor_chains` rows, from
#: `chain_cases.json` (the spin-off pair has none).
_CASES = {
    "0001000011": ("0001000010", datetime.fromisoformat("2020-02-07T20:30:00+00:00")),
    "0001000013": ("0001000012", datetime.fromisoformat("2020-03-30T20:30:00+00:00")),
    "0001000015": ("0001000014", datetime.fromisoformat("2020-05-11T20:30:00+00:00")),
}


@pytest.fixture
def truncated(chain_fixture_store: duckdb.DuckDBPyConnection) -> Iterator[TruncatedStore]:
    store = TruncatedStore(chain_fixture_store)
    try:
        yield store
    finally:
        store.close()


def test_the_fixture_stores_a_chain_row_per_chainable_pair(
    chain_fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    rows = chain_fixture_store.execute(
        "SELECT successor_id, predecessor_id, known_at FROM successor_chains ORDER BY successor_id"
    ).fetchall()
    assert rows == [
        (successor, predecessor, known_at)
        for successor, (predecessor, known_at) in sorted(_CASES.items())
    ]
    assert chain_fixture_store.execute(
        "SELECT count(*) FROM successor_chains WHERE successor_id = '0001000017'"
    ).fetchone() == (0,)


def test_the_row_is_absent_before_its_known_at_and_present_from_it(
    truncated: TruncatedStore,
) -> None:
    assert "successor_chains" in truncated.tables
    for successor, (_predecessor, known_at) in _CASES.items():
        before = truncated.at(known_at - PROBE_EPSILON)
        assert before.execute(
            "SELECT count(*) FROM successor_chains WHERE successor_id = ?", [successor]
        ).fetchone() == (0,), successor
        at = truncated.at(known_at)
        assert at.execute(
            "SELECT count(*) FROM successor_chains WHERE successor_id = ?", [successor]
        ).fetchone() == (1,), successor


def test_truncation_sees_exactly_the_rows_known_by_each_probe(
    chain_fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore
) -> None:
    """Every probe sees the fixture rows with `known_at <= T` and no others."""
    probes = [known_at_ for _predecessor, known_at_ in _CASES.values()]
    for known_at in sorted(probes):
        for t in (known_at - PROBE_EPSILON, known_at, known_at + PROBE_EPSILON):
            expected = chain_fixture_store.execute(
                "SELECT successor_id FROM successor_chains "
                "WHERE known_at <= ? ORDER BY successor_id",
                [t],
            ).fetchall()
            seen = (
                truncated.at(t)
                .execute("SELECT successor_id FROM successor_chains ORDER BY successor_id")
                .fetchall()
            )
            assert seen == expected, t


#: The read rule `schema.successor_chains` documents: the successor's
#: unretracted row with the greatest `ingested_at`, unless a retraction was
#: written after it (every revision applied, the corrected history the
#: UPDATE-moved listing rows give, T166b), shown at `t` only when its
#: `known_at <= t`.
_LIVE_AT_SQL = """
    SELECT evidence_accession, known_at FROM successor_chains c
    WHERE successor_id = '0001000011' AND NOT retracted AND known_at <= ?
      AND NOT EXISTS (
          SELECT 1 FROM successor_chains n
          WHERE n.successor_id = c.successor_id AND n.ingested_at > c.ingested_at
      )
"""


def _chain_row(known_at: datetime, ingested_at: datetime, **extra: object) -> dict[str, object]:
    return {
        "predecessor_id": "0001000010",
        "successor_id": "0001000011",
        "evidence_accession": "0001000011-24-000001",
        "evidence_form": "8-K12B",
        "retracted": False,
        "withdrawn_known_at": None,
        "known_at": known_at,
        "ingested_at": ingested_at,
        "source": "edgar",
        "provenance": "filing",
    } | extra


def test_a_moved_chain_is_absent_before_its_new_stamp_at_every_read(
    chain_fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """#821: after a move to a later stamp the chain reads absent at every
    `t` in [old, new), as the successor's listing row the move follows (moved
    by UPDATE, T166b) does, so `chain_known_at_ge_successor_rows` holds at
    every read; it reads the new stamp in [new, the run's clock) and after."""
    from tradepartner.store.db import insert_row

    old = datetime.fromisoformat("2020-02-07T20:30:00+00:00")
    new = datetime.fromisoformat("2020-03-08T20:30:00+00:00")
    clock = datetime.fromisoformat("2020-04-01T04:00:00+00:00")
    accession = "0001000011-24-000001"

    def at(t: datetime) -> list[tuple[str, datetime]]:
        return [tuple(row) for row in chain_fixture_store.execute(_LIVE_AT_SQL, [t]).fetchall()]

    assert at(old - PROBE_EPSILON) == []
    assert at(old) == [(accession, old)]
    assert at(new) == [(accession, old)]  # unmoved: the old stamp, no new row yet
    withdrawn = _chain_row(clock, clock, retracted=True, withdrawn_known_at=old)
    insert_row(chain_fixture_store, "successor_chains", withdrawn)
    insert_row(chain_fixture_store, "successor_chains", _chain_row(new, clock))
    for t in (old, old + PROBE_EPSILON, new - PROBE_EPSILON):  # [old, new)
        assert at(t) == [], t
    for t in (new, new + PROBE_EPSILON, clock - PROBE_EPSILON):  # [new, clock)
        assert at(t) == [(accession, new)], t
    for t in (clock, clock.replace(month=5)):  # from the run on
        assert at(t) == [(accession, new)], t


def test_write_master_stores_a_chain_no_earlier_than_its_evidence() -> None:
    """#1838: `write_master`'s chain path stores the row at its evidence
    `known_at`, never the run clock, so a read before that stamp sees no
    chain and a read from it does."""
    known_at = datetime.fromisoformat("2020-02-07T20:30:00+00:00")
    clock = datetime.fromisoformat("2020-04-01T04:00:00+00:00")
    accession = "0001000011-24-000001"
    conn = duckdb.connect(":memory:")
    try:
        schema.init_schema(conn)
        write_master(
            conn,
            MasterBuild(
                (),
                (),
                (),
                (),
                chains=(Chain("0001000010", "0001000011", accession, "8-K12B", known_at, clock),),
            ),
            now=clock,
        )
        truncated = TruncatedStore(conn)
        try:

            def at(t: datetime) -> list[tuple[str, datetime]]:
                return [tuple(row) for row in truncated.at(t).execute(_LIVE_AT_SQL, [t]).fetchall()]

            assert at(known_at - PROBE_EPSILON) == []
            assert at(known_at) == [(accession, known_at)]
            assert at(clock) == [(accession, known_at)]
        finally:
            truncated.close()
    finally:
        conn.close()


# --- the overlay stays out of the shared fixture (#821, PR #1836) -----------------------

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
_GENERATOR_PATH = Path(__file__).resolve().parents[2] / "scripts" / "make_fixture_universe.py"
_CHAIN_IDS = tuple(f"00010000{n}" for n in range(10, 18))


def test_the_shared_fixture_store_holds_no_chain_pair(
    fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """The four pairs live only in the `chains/` overlay: the store every other test
    reads (health, universe, the H1/TWIN/B3 pins) has none of their rows."""
    for table in ("securities", "listings", "prices_daily", "facts", "classifications"):
        count = fixture_store.execute(
            f"SELECT count(*) FROM {table} WHERE security_id = ANY(?)",
            [list(_CHAIN_IDS)],
        ).fetchone()
        assert count == (0,), table
    assert fixture_store.execute("SELECT count(*) FROM successor_chains").fetchone() == (0,)


def test_the_chain_overlay_regenerates_byte_identically(tmp_path: Path) -> None:
    """`make_fixture_universe.py` writes the overlay beside the universe, byte for byte
    what is committed under `tests/fixtures/universe/chains/`."""
    spec = importlib.util.spec_from_file_location("_chain_overlay_gen", _GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.write_fixtures(tmp_path)
    committed = sorted(p.name for p in (_FIXTURES_DIR / "chains").iterdir())
    assert committed == sorted(p.name for p in (tmp_path / "chains").iterdir())
    for name in committed:
        assert (_FIXTURES_DIR / "chains" / name).read_bytes() == (
            tmp_path / "chains" / name
        ).read_bytes(), name
    assert not (tmp_path / "successor_chains.csv").exists()


# --- the reads and the resolver (#821, plan T187b) ------------------------------------

from datetime import date, timedelta  # noqa: E402

import polars as pl  # noqa: E402

from tradepartner.config import Settings, parse_accepted_jump  # noqa: E402
from tradepartner.store.asof import (  # noqa: E402
    LiveChains,
    adjusted_prices_as_of,
    facts_as_of,
    live_actions_as_of,
    live_chains,
    price_jumps_as_of,
    prices_as_of,
)
from tradepartner.store.db import insert_row  # noqa: E402
from tradepartner.universe import universe_as_of  # noqa: E402

#: The 1:1 pair this section reads: CHNA (predecessor) -> Chain One (successor),
#: chained from 2020-02-07T20:30.
_PREDECESSOR = "0001000010"
_SUCCESSOR = "0001000011"
_CHAIN_KNOWN_AT = datetime.fromisoformat("2020-02-07T20:30:00+00:00")
#: A read just after it: the chain is live and the successor's bars through 2020-02-06
#: (it lists 2020-02-04) are known.
_AFTER = datetime.fromisoformat("2020-02-07T21:00:00+00:00")


def _settings(**universe: object) -> Settings:
    return Settings(_env_file=None, universe=universe)


def test_before_the_known_at_the_pair_reads_under_its_own_ids(
    chain_fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    before = _CHAIN_KNOWN_AT - PROBE_EPSILON
    assert live_chains(chain_fixture_store, before) == ()
    predecessor = prices_as_of(chain_fixture_store, before, [_PREDECESSOR])
    successor = prices_as_of(chain_fixture_store, before, [_SUCCESSOR])
    assert predecessor.height == 250 and successor.height > 0
    assert set(predecessor["security_id"].unique().to_list()) == {_PREDECESSOR}
    assert set(successor["security_id"].unique().to_list()) == {_SUCCESSOR}
    assert facts_as_of(chain_fixture_store, before, [_PREDECESSOR]).height == 1
    assert facts_as_of(chain_fixture_store, before, [_SUCCESSOR]).is_empty()


def test_from_the_known_at_the_successor_reads_both_histories(
    chain_fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """One read for the successor is the predecessor's rows re-keyed plus its
    own, in session order; one for the predecessor is empty."""
    assert live_chains(chain_fixture_store, _AFTER) == ((_PREDECESSOR, _SUCCESSOR),)
    merged = prices_as_of(chain_fixture_store, _AFTER, [_SUCCESSOR])
    assert set(merged["security_id"].unique().to_list()) == {_SUCCESSOR}
    # The predecessor's first bar (2019-01-08) and the successor's own (2020-02-04)
    # are both there, in session order, with no bar in the delisting hole.
    sessions = merged["session"].to_list()
    assert sessions == sorted(sessions)
    assert sessions[0] == date(2019, 1, 8) and date(2020, 2, 4) in sessions
    assert date(2020, 1, 10) not in sessions  # the predecessor's delisting tail
    assert prices_as_of(chain_fixture_store, _AFTER, [_PREDECESSOR]).is_empty()
    assert live_actions_as_of(chain_fixture_store, _AFTER, [_PREDECESSOR]).is_empty()
    action = live_actions_as_of(chain_fixture_store, _AFTER, [_SUCCESSOR])
    assert action.select("security_id", "action_type", "ex_date").rows() == [
        (_SUCCESSOR, "dividend", date(2019, 12, 5))
    ]


def test_the_chained_dividend_adjusts_the_successors_early_bars(
    chain_fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    raw = prices_as_of(chain_fixture_store, _AFTER, [_SUCCESSOR])
    adjusted = adjusted_prices_as_of(
        chain_fixture_store, _AFTER, [_SUCCESSOR], include_dividends=True
    )
    session = date(2019, 12, 4)  # the prior close of the 2019-12-05 ex-date
    close = float(raw.filter(pl.col("session") == session)["close"].item())
    adj = float(adjusted.filter(pl.col("session") == session)["close"].item())
    assert adj == pytest.approx(close - 0.25)  # factor 1 - amount / prior close


def test_the_predecessors_facts_read_under_the_successor(
    chain_fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    later = datetime.fromisoformat("2020-03-01T21:00:00+00:00")
    facts = facts_as_of(chain_fixture_store, later, [_SUCCESSOR])
    assert set(facts["security_id"].unique().to_list()) == {_SUCCESSOR}
    # The predecessor's two facts and the successor's own, all under the successor.
    assert sorted(facts["as_of_date"].to_list()) == [
        date(2019, 11, 20),
        date(2020, 1, 13),
        date(2020, 2, 4),
    ]


def test_an_accepted_price_jumps_entry_naming_the_predecessor_excuses_the_successor(
    chain_fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """A revision of one predecessor bar makes a jump under the successor; a
    `universe.accepted_price_jumps` entry naming the predecessor excuses it."""
    session = date(2019, 12, 10)
    before = _CHAIN_KNOWN_AT - PROBE_EPSILON
    (bar,) = (
        prices_as_of(chain_fixture_store, before, [_PREDECESSOR])
        .filter(pl.col("session") == session)
        .iter_rows(named=True)
    )
    original = float(bar["close"])
    insert_row(
        chain_fixture_store,
        "prices_daily",
        bar
        | {
            "close": original * 3,
            "high": max(bar["high"], original * 3),
            "known_at": _AFTER,
            "ingested_at": _AFTER,
        },
    )
    plain = price_jumps_as_of(chain_fixture_store, _AFTER, [_SUCCESSOR], settings=_settings())
    jump = plain.filter(pl.col("session") == session)
    assert jump.height == 1 and jump["security_id"].item() == _SUCCESSOR
    assert jump["accepted"].item() is False
    accepted = price_jumps_as_of(
        chain_fixture_store,
        _AFTER,
        [_SUCCESSOR],
        settings=_settings(accepted_price_jumps=[f"{_PREDECESSOR}@{session.isoformat()}"]),
    ).filter(pl.col("session") == session)
    assert accepted["accepted"].item() is True
    # The helper maps the entry's id the same way it maps the rows.
    chains = LiveChains(pairs={_PREDECESSOR: _SUCCESSOR})
    assert chains.mapped_entry(f"{_PREDECESSOR}@{session.isoformat()}", parse_accepted_jump) == (
        _SUCCESSOR,
        session,
    )


def test_universe_admits_the_chained_history(
    chain_fixture_store: duckdb.DuckDBPyConnection,
) -> None:
    """The predecessor's re-keyed bars are the successor's rule-6 history: by
    2020-05-01 the successor is excluded for liquidity, not for missing bars,
    while the same read without the chain still fails rule 6."""
    late = datetime.fromisoformat("2020-05-01T20:30:00+00:00")
    settings = Settings(_env_file=None)
    with_chain = universe_as_of(chain_fixture_store, late, settings)
    assert "0001000011" not in set(with_chain.members["security_id"].to_list())
    reasons = {row[3] for row in with_chain.exclusions.rows() if row[0] == _SUCCESSOR}
    assert reasons and "missing_bars" not in reasons  # rule 6 satisfied by the chain
    truncated = TruncatedStore(chain_fixture_store)
    try:
        without = universe_as_of(truncated.at(_CHAIN_KNOWN_AT - PROBE_EPSILON), late, settings)
    finally:
        truncated.close()
    # Without the chain the successor has only its own ~3 months of bars, so it is
    # not a member and (its listing known at 20:30) is excluded for its history.
    assert "0001000011" not in set(without.members["security_id"].to_list())


def test_the_chain_reads_are_truncation_invariant_around_the_known_at(
    chain_fixture_store: duckdb.DuckDBPyConnection, truncated: TruncatedStore
) -> None:
    """`f(T)` on the full store equals `f(T)` on the store truncated to
    `known_at <= T`, around the chain's stamp (spec "Look-ahead")."""
    reads = [
        lambda c, t: prices_as_of(c, t, [_SUCCESSOR]),
        lambda c, t: adjusted_prices_as_of(c, t, [_SUCCESSOR], include_dividends=True),
        lambda c, t: live_actions_as_of(c, t, [_SUCCESSOR]),
        lambda c, t: facts_as_of(c, t, [_SUCCESSOR]),
    ]
    for t in (
        _CHAIN_KNOWN_AT - timedelta(days=1),
        _CHAIN_KNOWN_AT - PROBE_EPSILON,
        _CHAIN_KNOWN_AT,
        _CHAIN_KNOWN_AT + PROBE_EPSILON,
        _CHAIN_KNOWN_AT + timedelta(days=30),
    ):
        for read in reads:
            full = read(chain_fixture_store, t)
            seen = read(truncated.at(t), t)
            assert full.equals(seen), (t, read)
