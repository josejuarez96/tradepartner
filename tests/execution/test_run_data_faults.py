"""Data faults on the paper run (#1983; the 2026-10-20 break test).

Reuses the tracking-run fixture of `test_run_trade.py`: T_0 = 2019-04-30,
F_0 = 2019-05-01, the plan buys DUALB, SPFT, TRNS a third each.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import date

import pytest

from tradepartner.errors import StaleDataError
from tradepartner.store.journal import PaperWindowRow

from .test_run_trade import F_0, T_0, Env, at, bought
from .test_run_trade import _no_env_file as _no_env_file
from .test_run_trade import env as env
from .test_run_trade import window as window

D_NEXT = date(2019, 5, 2)  # a mark session after F_0
D_NEXT2 = date(2019, 5, 3)


def set_close(env: Env, security_id: str, session: date, value: float) -> None:
    with env.connect() as conn:
        conn.execute(
            "UPDATE prices_daily SET close = ? WHERE security_id = ? AND session = ?",
            [value, security_id, session],
        )


def marks(env: Env, security_id: str) -> list[float | None]:
    return [
        r[0]
        for r in env.query("SELECT value FROM positions_daily WHERE security_id = ?", [security_id])
    ]


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan")])
def test_a_bad_close_on_a_held_name_is_never_persisted_as_a_mark(
    env: Env, window: PaperWindowRow, bad: float
) -> None:
    """A held name's S-1 close of 0, negative or NaN is never written to
    `positions_daily`: the first run halts at reconciliation (its reference
    price), and every later run, the switch engaged, halts at the mark step
    naming the security and the session, so no false drawdown is engaged."""
    bought(env)
    before = marks(env, "SEC_TRANSFER")
    submits = len(env.submits())
    set_close(env, "SEC_TRANSFER", F_0, bad)
    with pytest.raises(ValueError, match="reference price of SEC_TRANSFER"):
        env.run(at(D_NEXT), prices={"TRNS": 50.0})
    with pytest.raises(StaleDataError, match=r"SEC_TRANSFER.*2019-05-01"):
        env.run(at(D_NEXT2), prices={"TRNS": 50.0})
    assert env.alerts("stale_data")  # the alert names the bar
    assert "SEC_TRANSFER" in env.alerts("stale_data")[0][2]
    assert marks(env, "SEC_TRANSFER") == before
    faults = {r[0] for r in env.query("SELECT fault_type FROM kill_switch")}
    assert "drawdown" not in faults and "DrawdownBreach" not in faults
    assert len(env.submits()) == submits


def test_a_bad_close_after_the_marked_session_is_not_read(env: Env, window: PaperWindowRow) -> None:
    """No look-ahead: a bad close on D_NEXT is S-1 of D_NEXT2, not of D_NEXT,
    so the run on D_NEXT marks as before."""
    bought(env)
    set_close(env, "SEC_TRANSFER", D_NEXT, 0.0)
    assert env.run(at(D_NEXT), prices={"TRNS": 50.0}).status == "ok"


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan")])
def test_a_bad_close_on_an_unheld_candidate_never_reaches_the_plan_as_a_target(
    env: Env, window: PaperWindowRow, bad: float
) -> None:
    """Today's behaviour, pinned (#1983 part c): `Bar.__post_init__` refuses such a
    price at ingest, so only a direct store write reaches the signal, and the
    candidate is ranked out, never bought."""
    set_close(env, "SEC_TRANSFER", T_0, bad)
    with suppress(Exception):  # a halt is safe too
        env.run(at(F_0), prices={"TRNS": 50.0})
    assert "SEC_TRANSFER" not in {o[1] for o in env.orders()}
