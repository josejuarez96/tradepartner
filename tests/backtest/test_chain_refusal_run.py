"""End to end on a real store: a refused successor chain exits the held predecessor
by the normal delisting path (#3097, Audit A s10b).

OLD (ABC, NYSE) is bought at 20 on 2023-02-01, falls to 1.0 and last prints 02-28; its
Form 25 is filed 03-03 with an 8-K Item 1.03 (bankruptcy) beside it. NEW, the
post-reorganisation equity, lists ABC from 03-01 at 25, and a chain OLD -> NEW is known
03-02. With the bankruptcy reason the chain is refused: OLD exits at 1.0
(`n_delisting_exits`), nothing is renamed and `n_refused_chains` is 1. Without the reason
row (the control) the chain is live and OLD's position becomes NEW's at 25, the s10b bug.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.backtest import engine
from tradepartner.backtest.provider import GapReading
from tradepartner.backtest.schedule import fill_session, rebalance_sessions
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import all_sessions, session_close
from tradepartner.config import Settings
from tradepartner.store import lab_schema, registry, schema
from tradepartner.store.db import configure_connection, insert_row, open_for_write, open_read_only

OLD, NEW, B = "0000000100", "0000000200", "0000000300"
START, END = date(2023, 1, 31), date(2023, 3, 31)
FORM25 = datetime(2023, 3, 3, 22, tzinfo=UTC)
CAPITAL = 1_000_000.0


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # The provider compares the frozen calendar and chain window with the live settings.
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _fact(known_at: datetime) -> dict[str, Any]:
    return {
        "known_at": known_at,
        "ingested_at": known_at,
        "source": "edgar",
        "provenance": "filing",
    }


def _build(path: Path, *, bankruptcy: bool) -> None:
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        lab_schema.create_store_markers(conn)
        lab_schema.write_fixture_marker(conn, "test_chain_refusal_run")
        listed = datetime(2022, 12, 30, 21, tzinfo=UTC)
        for sid, ticker, valid_from, known in (
            (OLD, "ABC", date(2022, 1, 3), listed),
            (B, "BBB", date(2022, 1, 3), listed),
            (NEW, "ABC", date(2023, 3, 1), datetime(2023, 2, 27, 13, tzinfo=UTC)),
        ):
            insert_row(
                conn,
                "securities",
                {"security_id": sid, "cik": sid, "name": sid, "benchmark": False} | _fact(known),
            )
            insert_row(
                conn,
                "listings",
                {
                    "security_id": sid,
                    "ticker": ticker,
                    "exchange": "NYSE",
                    "class_title": "Common Stock",
                    "valid_from": valid_from,
                }
                | _fact(known),
            )
        for session in (s for s in all_sessions() if date(2023, 1, 3) <= s <= END):
            closes = {B: 50.0}
            if session <= date(2023, 2, 28):
                closes[OLD] = 20.0 if session <= date(2023, 2, 1) else 1.0
            if session >= date(2023, 3, 1):
                closes[NEW] = 25.0
            for sid, close in closes.items():
                known = session_close(session)
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
                        "volume": 1_000_000,
                        "known_at": known,
                        "ingested_at": known,
                        "source": "alpaca",
                        "provenance": "bar",
                    },
                )
        insert_row(
            conn,
            "delistings",
            {
                "security_id": OLD,
                "form": "25",
                "class_title": "Common Stock",
                "exchange": "NYSE",
                "filed_at": FORM25,
                "effective_on": date(2023, 3, 13),
            }
            | _fact(FORM25),
        )
        if bankruptcy:
            insert_row(
                conn,
                "delisting_reasons",
                {
                    "security_id": OLD,
                    "delisting_form": "25",
                    "delisting_class_title": "Common Stock",
                    "delisting_exchange": "NYSE",
                    "delisting_filed_at": FORM25,
                    "reason": "bankruptcy",
                    "reason_evidence_form": "8-K",
                    "reason_evidence_accession": "0000000100-23-000009",
                    "reason_source": "eightk_item",
                    "reason_known_at": FORM25,
                }
                | _fact(FORM25),
            )
        insert_row(
            conn,
            "successor_chains",
            {
                "predecessor_id": OLD,
                "successor_id": NEW,
                "evidence_accession": "0000000200-23-000001",
                "evidence_form": "8-K12B",
                "retracted": False,
                "withdrawn_known_at": None,
            }
            | _fact(datetime(2023, 3, 2, 21, tzinfo=UTC)),
        )
    finally:
        conn.close()


def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, bankruptcy: bool) -> Any:
    path = tmp_path / "store.duckdb"
    _build(path, bankruptcy=bankruptcy)
    settings = Settings(
        _env_file=None,
        store={"path": str(path)},
        benchmarks=[],
        execution={"fill_price": "close"},
        costs={"per_side_bps": 10.0, "spread_bps": 0.0, "commission_per_order": 0.0},
        backtest={"initial_capital": CAPITAL},
        schedule={"rebalance_cadence": "month_end"},
        metrics={"risk_free_source": "constant"},
    )
    sessions = rebalance_sessions(START, END, "month_end")
    real = Settings(_env_file=None, store={"path": str(tmp_path / "real.duckdb")})
    with open_for_write(settings) as conn:
        hypothesis = registry.register_hypothesis(
            conn,
            slug="h-chain-refusal",
            family="momentum",
            title="chain refusal",
            doc_path="docs/hypotheses/h-chain-refusal.md",
            doc_sha256="0" * 64,
            params={"costs.per_side_bps": 10.0},
            in_sample_start=date(2000, 1, 3),
            holdout_start=date(2034, 1, 3),
            holdout_end=date(2035, 6, 29),
            registered_by="owner",
            settings=real,
        )
        handle = registry.open_trial(
            conn,
            hypothesis_id=hypothesis.hypothesis_id,
            kind="in_sample",
            start_session=START,
            end_session=END,
            data_cutoff=session_close(sessions[-1]),
            synthetic=True,
            run_by="test",
            settings=real,
        )

    @contextmanager
    def connect() -> Iterator[duckdb.DuckDBPyConnection]:
        with open_read_only(settings) as conn:
            yield conn

    def fixed_plan(provider: Any, params: Any, session: date, family: Any) -> engine.Plan:
        # OLD is a target only at T_0 (bought on 02-01); the 02-28 plan holds B alone,
        # so the 03-31 step decides OLD's fate through the chain read.
        targets = {OLD: 0.5, B: 0.5} if session == START else {B: 1.0}
        return engine.Plan(
            session=session,
            fill_session=fill_session(session, "month_end"),
            targets=targets,
            n_universe=len(targets),
            n_static_listings=0,
            n_excluded_no_history=0,
            gap=GapReading(0.0, 0.0),
            members=tuple(sorted(targets)),
            scores=dict(targets),
            excluded_no_history=(),
            counts={},
            exclusions={},
        )

    monkeypatch.setattr(engine, "_plan", fixed_plan)
    provider = StoreProvider(connect, handle, settings)
    try:
        results = engine.run(settings, provider, START, END, handle, [10.0], family="momentum")
    finally:
        provider.close()
    return results[10.0]


def test_a_refused_chain_exits_the_held_predecessor_at_its_last_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(tmp_path, monkeypatch, bankruptcy=True)
    assert sum(row.n_delisting_exits for row in result.rebalances) == 1
    assert sum(row.counts.get("n_refused_chains", 0) for row in result.rebalances) == 1
    assert sum(row.counts.get("n_chained_positions", 0) for row in result.rebalances) == 0
    held = set(result.position_values.filter(result.position_values["value"] != 0)["security_id"])
    assert NEW not in held
    final = [r for r in result.equity if r.series == engine.STRATEGY_SERIES][-1].equity
    # Half the book in OLD at 20 -> 1.0 (about -47.5%), never NEW's 25.
    assert final / CAPITAL - 1 == pytest.approx(-0.4758, abs=2e-3)


def test_the_control_without_the_reason_still_renames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The teeth: the same store with no reason row keeps the chain live, so OLD's
    position is renamed into NEW (the s10b splice the refusal prevents)."""
    result = _run(tmp_path, monkeypatch, bankruptcy=False)
    assert sum(row.counts.get("n_chained_positions", 0) for row in result.rebalances) == 1
    assert sum(row.counts.get("n_refused_chains", 0) for row in result.rebalances) == 0
    final = [r for r in result.equity if r.series == engine.STRATEGY_SERIES][-1].equity
    assert final / CAPITAL - 1 > 0.0
