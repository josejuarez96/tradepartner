"""The window's `frozen_json` is checked against its `frozen_sha256` at run time
(#1984; the 2026-10-20 break test): an in-place edit to looser limits halts the
run before any order, and the switch holds the next run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tradepartner.errors import LimitBreachError
from tradepartner.store.journal import PaperWindowRow

from .test_run_trade import F_0, F_0_PLUS_1, FROZEN, Env, at, frozen_json
from .test_run_trade import _no_env_file as _no_env_file
from .test_run_trade import env as env


def _rewrite(env: Env, window: PaperWindowRow, text: str) -> None:
    with env.connect() as conn:
        conn.execute(
            "UPDATE paper_windows SET frozen_json = ? WHERE window_id = ?",
            [text, window.window_id],
        )


def _loosened(**changes: object) -> str:
    values = json.loads(frozen_json(FROZEN))
    values.update({f"risk.{k}": v for k, v in changes.items()})
    return json.dumps(values, sort_keys=True)


def test_a_loosened_frozen_section_halts_the_run_before_any_order(env: Env, tmp_path: Path) -> None:
    """The window froze order notional 0.30 (the plan's third-of-equity buys breach
    it); frozen_json is then rewritten to 0.40 with frozen_sha256 unchanged. The
    run halts on the mismatch: nothing is sent, the switch is engaged, the alert
    names the window; the next run halts the same way."""
    window = env.open_window(
        frozen=FROZEN.model_copy(update={"max_order_notional_fraction": 0.30}), tmp_path=tmp_path
    )
    _rewrite(env, window, _loosened(max_order_notional_fraction=0.40))
    with pytest.raises(LimitBreachError, match="frozen_sha256"):
        env.run(at(F_0))
    assert env.result(env.latest_run())[:2] == ("halted", "LimitBreachError")
    assert env.submits() == [] and env.count("orders") == 0
    assert [r[0] for r in env.query("SELECT state FROM kill_switch")] == ["engaged"]
    (alert,) = env.alerts("halted")
    assert f"window {window.window_id}" in alert[2]
    # A release would not help: every later run re-checks the hash and halts again.
    with pytest.raises(LimitBreachError, match="frozen_sha256"):
        env.run(at(F_0_PLUS_1))
    assert env.submits() == []


def test_an_untouched_window_runs_and_a_reformatted_one_with_the_same_values_too(
    env: Env, tmp_path: Path
) -> None:
    """Control: the hash is of the canonical form, so the intact window trades,
    and a whitespace-only rewrite (same values) is not a mismatch."""
    window = env.open_window(tmp_path=tmp_path)
    _rewrite(env, window, json.dumps(json.loads(frozen_json(FROZEN)), indent=2))
    assert env.run(at(F_0)).status == "ok"
    assert len(env.submits()) == 3
