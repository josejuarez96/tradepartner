# ruff: noqa: F811
"""A universe candidate with no T_0 bar (a partial ingest) is excluded, visibly (#1997).

Spec: paper-trading.md, amendment 2026-10-20 (owner, #1997). The fixture is
test_run_trade's: T_0 = 2019-04-30, the plan buys DUALB, SPFT, TRNS a third each.
"""

from __future__ import annotations

from datetime import date

from execution.test_run_trade import F_0, T_0, Env, at, env, window  # noqa: F401
from tradepartner.config import FAMILIES
from tradepartner.execution.alerts import ALERT_KINDS
from tradepartner.store.journal import PaperWindowRow

TRNS = "SEC_TRANSFER"
INTENDED = {"SEC_DUAL_B", "SEC_SPLIT_FUTURE", TRNS}


def drop_bar(env: Env, security_id: str, session: date) -> None:
    with env.connect() as conn:
        conn.execute(
            "DELETE FROM prices_daily WHERE security_id = ? AND session = ?",
            [security_id, session],
        )


def signal_reasons(env: Env) -> dict[str, str]:
    rows = env.query("SELECT security_id, reason FROM signals WHERE run_id = ?", [env.latest_run()])
    return {r[0]: r[1] for r in rows}


def test_reason_and_alert_kind_are_declared() -> None:
    assert "missing_candidate_bar" in ALERT_KINDS
    assert all("no_t0_bar" in spec.exclusion_reasons for spec in FAMILIES.values())


def test_candidate_with_no_t0_bar_is_excluded_with_the_reason_and_alerts(
    env: Env, window: PaperWindowRow
) -> None:
    drop_bar(env, TRNS, T_0)  # partial ingest: SPY's T_0 bar is there
    outcome = env.run(at(F_0), prices={})
    assert outcome.status == "ok"
    assert signal_reasons(env)[TRNS] == "excluded_no_t0_bar"
    alerts = env.alerts("missing_candidate_bar")
    assert len(alerts) == 1 and TRNS in str(alerts[0])
    assert env.alerts("stale_data") == []


def test_the_run_continues_and_does_not_silently_retarget(env: Env, window: PaperWindowRow) -> None:
    drop_bar(env, TRNS, T_0)
    assert env.run(at(F_0), prices={}).status == "ok"
    bought = {o[1] for o in env.orders()}
    assert bought and TRNS not in bought
    # The retarget (the fixture's next name in, TRNS out) is on the record: the
    # signals row and the alert, the two things the scratch break test asked for.
    assert signal_reasons(env)[TRNS] == "excluded_no_t0_bar"
    assert len(env.alerts("missing_candidate_bar")) == 1


def test_a_complete_ingest_raises_nothing(env: Env, window: PaperWindowRow) -> None:
    env.run(at(F_0))
    assert env.alerts("missing_candidate_bar") == []
    assert "excluded_no_t0_bar" not in signal_reasons(env).values()
    assert {o[1] for o in env.orders()} == INTENDED


def test_a_t0_bar_known_after_the_cut_does_not_count(env: Env, window: PaperWindowRow) -> None:
    """No look-ahead: TRNS's T_0 bar exists in the store but was ingested after the
    run's read, so as of the cut it is missing and the name is still a candidate."""
    with env.connect() as conn:
        conn.execute(
            "UPDATE prices_daily SET known_at = ?, ingested_at = ? "
            "WHERE security_id = ? AND session = ?",
            [at(F_0, 23), at(F_0, 23), TRNS, T_0],
        )
    env.run(at(F_0), prices={})
    assert signal_reasons(env)[TRNS] == "excluded_no_t0_bar"
    assert len(env.alerts("missing_candidate_bar")) == 1


def test_a_name_with_a_long_gap_is_not_a_missing_t0_candidate(
    env: Env, window: PaperWindowRow
) -> None:
    """A name missing T_0 and another history session is a history exclusion, not
    this one: no signals row, no alert."""
    prior = date(2019, 4, 29)
    drop_bar(env, TRNS, T_0)
    drop_bar(env, TRNS, prior)
    env.run(at(F_0), prices={})
    assert env.alerts("missing_candidate_bar") == []
    assert TRNS not in signal_reasons(env)


def test_the_exclusion_leaves_n_universe_unchanged(env: Env, window: PaperWindowRow) -> None:
    """The candidate was never a member: n_universe is the members only."""
    drop_bar(env, TRNS, T_0)
    env.run(at(F_0), prices={})
    (n_universe,) = env.query("SELECT n_universe FROM paper_plans")[0]
    members = env.query(
        "SELECT count(*) FROM signals WHERE run_id = ? AND reason NOT LIKE 'excluded_no_t0%'",
        [env.latest_run()],
    )[0][0]
    assert n_universe == members
