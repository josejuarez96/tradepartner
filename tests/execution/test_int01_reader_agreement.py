"""Reader agreement (#2601): one fake-broker session from S-1 ingest to journal readers."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.adapters.broker import Asset, OrderRequest, PositionIntent, Side
from tradepartner.calendar import session_close
from tradepartner.errors import ReconciliationError
from tradepartner.execution import book_view, headroom, home_view, ops, report
from tradepartner.execution.marks import equity_at, signed_value
from tradepartner.store import journal, registry
from tradepartner.store.db import open_read_only
from tradepartner.store.journal import (
    DecisionRow,
    OrderEventRow,
    OrderRow,
    PaperRunResultRow,
    PaperRunRow,
)

from .test_run_trade import F_0, SYMBOLS, Env, at, bought, delist, split
from .test_run_trade import env as env
from .test_run_trade import window as window

NEXT = date(2019, 5, 2)
FINAL = date(2019, 5, 3)
SHORT = "SEC_SPY"


def _carry_short(env: Env) -> None:
    """A prior filled short, on both the journal and fake broker, before S runs."""
    stamp = at(F_0, 19)
    assert env.window is not None and env.window.window_id is not None
    (run_id,) = env.append(
        PaperRunRow(
            window_id=env.window.window_id,
            session=F_0,
            kind="mark",
            started_at=stamp,
            invoked_by="scheduler",
            code_version="test",
            known_at=stamp,
            ingested_at=stamp,
        )
    )
    assert run_id is not None
    (decision_id,) = env.append(
        DecisionRow(
            run_id=run_id,
            rebalance_session=F_0,
            security_id=SHORT,
            side="sell",
            planned_quantity=10.0,
            whole_share=True,
            decision="trade",
            position_side="short",
            known_at=stamp,
            ingested_at=stamp,
        )
    )
    assert decision_id is not None
    coid = "tp-20190501-SEC_SPY-short-1"
    env.append(
        OrderRow(
            client_order_id=coid,
            decision_id=decision_id,
            run_id=run_id,
            session=F_0,
            attempt=1,
            phase="buy",
            security_id=SHORT,
            symbol="SPY",
            side="sell",
            quantity=10.0,
            position_side="short",
            sells_in_flight_at_submit=False,
            known_at=stamp,
            ingested_at=stamp,
        ),
        *(
            OrderEventRow(client_order_id=coid, status=s, known_at=stamp, ingested_at=stamp)
            for s in ("pending", "accepted")
        ),
    )
    env.clock.now = stamp
    env.fake.submit(
        OrderRequest(
            coid, "SPY", Side.SELL, quantity=10.0, position_intent=PositionIntent.SELL_TO_OPEN
        )
    )
    env.fake.simulate_fill(coid)
    assert env.fake.positions()["SPY"].quantity == -10.0
    env.append(
        PaperRunResultRow(
            run_id=run_id,
            finished_at=stamp,
            status="ok",
            clock_fault=False,
            known_at=stamp,
            ingested_at=stamp,
        )
    )


def test_filled_rebalance_rerun_sends_no_duplicate_orders(
    env: Env, window: journal.PaperWindowRow
) -> None:
    """A filled May 1 rebalance re-run on May 1 sends no duplicate order. Today the
    re-run then fails closed on reconciliation (#2649 asks whether that is intended)."""
    bought(env)
    submitted = len(env.submits())
    assert submitted >= 3
    with pytest.raises(ReconciliationError, match="broker_only_position, cash"):
        env.run(at(F_0))
    assert len(env.submits()) == submitted


@pytest.mark.parametrize(
    "require_short_mark",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.xfail(
                strict=True,
                reason="#2038: marks_for omits Ledger.short_holdings",
            ),
        ),
    ],
)
def test_one_session_readers_agree_with_the_journal(
    require_short_mark: bool,
    env: Env,
    window: journal.PaperWindowRow,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Compare real readers on the same book, marked session and cutoff."""
    env.new_fake(
        paper=True,
        assets={
            **env.fake.active_assets("DUALB", "SPFT", "TRNS", "WNDX", "DUALA"),
            "SPY": Asset(
                True, True, "active", None, shortable=True, easy_to_borrow=True, marginable=True
            ),
        },
    )
    frozen = json.loads(window.frozen_json)
    frozen.update({"paper.tracking_k": 2.0, "paper.tracking_rule": "raw"})
    frozen_text = json.dumps(frozen, sort_keys=True)
    frozen_hash = registry.params_sha256(frozen)
    with env.connect() as conn:
        conn.execute(
            "UPDATE paper_windows SET frozen_json = ?, frozen_sha256 = ? WHERE window_id = ?",
            [frozen_text, frozen_hash, window.window_id],
        )
    window = replace(window, frozen_json=frozen_text, frozen_sha256=frozen_hash)
    env.window = window
    bought(env)
    first_submits = len(env.submits())
    assert first_submits >= 3
    _carry_short(env)
    assert env.held()["SPY"] == pytest.approx(-10)
    delist(env, "SEC_DUAL_B", at(FINAL, 19))
    split(env, "SEC_TRANSFER", NEXT, 2.0, at(F_0, 19))
    env.fake.apply_split("TRNS", 2.0)
    env.insert(
        "corporate_actions",
        dict(
            security_id="SEC_SPLIT_FUTURE",
            action_type="dividend",
            ex_date=NEXT,
            ratio_or_amount=0.25,
            announced_at=None,
            source_action_id="int01-dividend",
            cancelled=False,
            known_at=at(F_0, 19),
            ingested_at=at(F_0, 19),
            source="alpaca",
            provenance="action",
        ),
    )
    env.price_at(NEXT)
    before = len(env.submits())
    outcome = env.run(at(NEXT), prices={"TRNS": env.prices["TRNS"] / 2})
    assert outcome.status == "ok"
    assert env.query(
        "SELECT sum(f.quantity) FROM fills f JOIN orders o USING (client_order_id) "
        "WHERE o.security_id = 'SEC_SPY' AND o.position_side = 'short'"
    )[0][0] == pytest.approx(10)
    assert len(env.submits()) == before
    assert env.run(at(FINAL)).status == "ok"
    assert env.run(at(FINAL)).status == "ok"
    assert len(env.submits()) == before

    assert window.window_id is not None
    cutoff = session_close(FINAL)
    with open_read_only(env.settings) as conn:
        marks = [
            m
            for m in journal.positions_daily_for(conn, window.window_id)
            if m.session == NEXT and m.known_at <= cutoff
        ]
        assert {m.security_id for m in marks} >= {"SEC_DUAL_B", "SEC_SPLIT_FUTURE", "SEC_TRANSFER"}
        by_name = {(m.security_id, m.position_side): m.quantity for m in marks}
        assert by_name[("SEC_TRANSFER", "long")] > 0
        assert next(m.value for m in marks if m.security_id == "SEC_DUAL_B") > 0
        assert (
            env.query("SELECT count(*) FROM delistings WHERE security_id = 'SEC_DUAL_B'")[0][0] == 1
        )
        net = sum(signed_value(m) or 0 for m in marks)
        gross = sum(abs(m.value or 0) for m in marks)
        cash = marks[0].cash
        assert cash is not None
        assert cash == pytest.approx(env.fake.account().cash)
        equity = equity_at(marks, NEXT)
        assert equity == pytest.approx(cash + net)

        operational = ops.page_data(conn, env.settings)
        assert operational.positions_count == len(marks)
        assert operational.positions_value == pytest.approx(net)
        monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "absent.env"))
        monkeypatch.setenv("STORE__PATH", str(env.settings.store.path))
        status = CliRunner().invoke(
            cli.make_app(clock=env.clock, broker=lambda _settings, _clock, _book: env.fake),
            ["paper", "status"],
        )
        assert status.exit_code == 0, status.output
        assert f"positions {len(marks)} (value {net:.2f})" in status.output

        risk = headroom.read(conn, window)
        assert risk.session == NEXT
        assert risk.cash == pytest.approx(cash)
        assert risk.equity == pytest.approx(equity)
        assert next(r for r in risk.rows if r.key == "gross").value * risk.equity == pytest.approx(
            gross
        )

        home = home_view.build(conn, env.settings, cutoff)
        card = next(c for c in home.books if c.book_id == window.book_id)
        assert card.session == NEXT
        assert card.equity == pytest.approx(equity)
        assert card.cash == pytest.approx(cash)
        assert card.holdings_count == len(marks)

        book = book_view.build(conn, env.settings, window.book_id, cutoff)
        assert book.header is not None and book.holdings is not None
        assert book.header.as_of_session == NEXT
        assert book.header.value == pytest.approx(equity)
        assert book.holdings.summary.cash == pytest.approx(cash)
        assert {(r.ticker, r.side): r.quantity for r in book.holdings.rows} == pytest.approx(
            {(SYMBOLS[sid], side): quantity for (sid, side), quantity in by_name.items()}
        )

        paper = report._journal_for(conn, window.window_id)
        assert equity_at(paper.positions_daily, NEXT) == pytest.approx(equity)
        trial = report.TrialPeriods(
            sessions=(F_0, NEXT),
            equity={day: equity_at(paper.positions_daily, day) for day in (F_0, NEXT)},
            cost_paid={F_0: 0.0},
            cadence="daily",
        )
        actions = conn.execute("SELECT * FROM corporate_actions").pl()

        def price_of(security_id: str, day: date) -> float | None:
            row = conn.execute(
                "SELECT close FROM prices_daily WHERE security_id = ? AND session = ?",
                [security_id, day],
            ).fetchone()
            return float(row[0]) if row else None

        comparison = report.compare_periods(window, trial, paper, actions, price_of, None)
        (period,) = comparison.periods
        assert period.raw == pytest.approx(0)
        assert period.dividend_term == pytest.approx(
            0.25 * by_name[("SEC_SPLIT_FUTURE", "long")] / trial.equity[F_0]
        )
        assert period.residue_term == pytest.approx(0)
        if require_short_mark:
            assert by_name.get((SHORT, "short"), 0) == pytest.approx(10), (net, gross, cash, equity)
            assert gross > abs(net)
