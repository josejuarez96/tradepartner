"""`bt` oracle for the backtest engine (backtest spec req 14, ADR 0004; plan T41).

A synthetic `family=oracle` hypothesis is registered on a temp-file copy of the
fixture universe and run through `run_hypothesis(..., store_path=...)` at zero costs.
`bt` then replays the run from what the returned `BacktestResult` holds: the **stitched
frame** (per name, each session's return from the marking frame of the step that
contains it, cumulated into one index) and the lagged **targets** keyed by fill
session. The only other input is the forced exit ADR 0004 names, the delisted fixture
name, as a test constant. `bt` fills at each fill session with fractional shares and no
commissions, by spec req 4's rule (`_Fill`): a held name with no bar that session is not
sold, and buys are capped by cash, which `bt`'s stock `Rebalance` would not do. Share
counts, daily marks and cash are `bt`'s own. The two equity series must agree at every
compared session to within `TOLERANCE`.

`execution.fill_price=close`, the frozen default, so every session is compared. The
`open` case is not exercised here: see the open question in the PR (#236).

**Window.** In-sample from 2018-11-30 to 2019-12-31 (holdout 2020-01-02..2020-06-30).
It starts after the fixture's exchange transfer (SEC_TRANSFER), whose Form 25 is known
at the 2018-10-31 read before its new listing is, so the engine exits it mid-step: a
second forced exit that ADR 0004 does not name. Inside this window the only forced
exit is SEC_WINDOW_DELIST (WNDX): last bar 2019-06-24, Form 25 known after
close(2019-06-28), so the step read at close(2019-07-31) exits it at its 2019-06-24
close, booked on the fill session 2019-07-01, where it is not a target. `bt` holds the
forward-filled stitched level, leaves it untraded in the 2019-07-01 fill (no bar), as
the engine does, then closes it at that same last close. The test asserts both, and
that no other exit or missing fill occurs, so a fixture change that breaks this
premise fails loudly instead of passing by accident.

The oracle checks the accounting loop (fills, drift, carry, splits and dividends
through the stitched ratios, the delisting exit), not the signal or the adjustment:
`bt` shares our frame and weights (spec "Oracle blind spots").
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path

import bt
import duckdb
import pandas as pd
import polars as pl
import pytest

from tradepartner.backtest.engine import STRATEGY_SERIES, BacktestResult
from tradepartner.backtest.holdout import Flags
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.backtest.run import run_hypothesis
from tradepartner.calendar import next_session
from tradepartner.config import Settings
from tradepartner.store import registry
from tradepartner.store.db import open_for_write

#: Maximum absolute relative equity difference allowed between `bt` and our engine
#: (ADR 0004). A constant, not config: changing it needs review in the PR.
TOLERANCE = 1e-9

SLUG = "oracle-bt"
IN_SAMPLE_START = date(2018, 11, 30)
HOLDOUT = (date(2020, 1, 2), date(2020, 6, 30))
DELISTED = "SEC_WINDOW_DELIST"
DELISTED_LAST_BAR = date(2019, 6, 24)
#: The rebalance whose step reads WNDX's Form 25, and that step's fill session.
EXIT_REBALANCE = date(2019, 6, 28)
EXIT_FILL = date(2019, 7, 1)
ZERO_COSTS = 0.0
#: The forced exit `bt` is told about (ADR 0004): the delisted fixture name, on the
#: session our engine books it.
EXITS = {EXIT_FILL: (DELISTED,)}


def _frozen() -> Settings:
    """Zero costs, no sensitivity levels, close fills; half the ranked names so the
    book turns over (names enter and leave, both fixture split cases are held)."""
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        costs={
            "per_side_bps": ZERO_COSTS,
            "sensitivity_per_side_bps": [],
            "commission_per_share": 0.0,
            "commission_per_order": 0.0,
        },
        execution={"fill_price": "close"},
        holdout={"start": HOLDOUT[0], "end": HOLDOUT[1]},
    )


@pytest.fixture(autouse=True)
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Live settings without a `.env`, whose store is another temp file, so the
    fixture store is never `settings.store.path` and the oracle family is allowed."""
    real = tmp_path / "real_store.duckdb"
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(real))
    return real


@pytest.fixture
def run(fixture_store_path: Path) -> tuple[Path, int, BacktestResult]:
    """Register the oracle hypothesis and run it once: the store, trial id and the
    zero-cost result."""
    frozen = _frozen()
    store = Settings(_env_file=None, store={"path": str(fixture_store_path)})
    with open_for_write(store) as conn:
        registry.register_hypothesis(
            conn,
            slug=SLUG,
            family="oracle",
            title="bt oracle",
            doc_path="tests/oracle/test_bt_oracle.py",
            doc_sha256="0" * 64,
            params=frozen_params_of(frozen),
            in_sample_start=IN_SAMPLE_START,
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            registered_by="test",
            settings=frozen,
        )
    outcome = run_hypothesis(
        SLUG, None, None, Flags(), synthetic=True, store_path=fixture_store_path, run_by="oracle"
    )
    assert (outcome.status, outcome.error) == ("ok", None)
    assert outcome.results is not None and set(outcome.results) == {ZERO_COSTS}
    return fixture_store_path, outcome.trial_id, outcome.results[ZERO_COSTS]


def _our_equity(result: BacktestResult) -> dict[date, float]:
    return {row.session: row.equity for row in result.equity if row.series == STRATEGY_SERIES}


def _stitched(
    result: BacktestResult, sessions: list[date]
) -> tuple[pd.DataFrame, frozenset[tuple[date, str]]]:
    """From the marking frames only: the stitched close index per name on `sessions`,
    forward-filled (a name with no bar keeps its last mark, as in the engine), and the
    `(session, security_id)` pairs that have a bar."""
    stitched = result.stitched_returns().select("session", "security_id", "close")
    bars = frozenset(stitched.select("session", "security_id").iter_rows())
    frame = stitched.to_pandas().pivot(index="session", columns="security_id", values="close")
    frame.index = pd.DatetimeIndex(frame.index)
    return frame.reindex(pd.DatetimeIndex(sessions)).ffill(), bars


class _Fill(bt.Algo):  # type: ignore[misc]
    """Spec req 4's fill rule, in `bt`'s own accounting: at each fill session, trade
    towards the targets from the equity before the fill, sells first, then buys in
    `security_id` order, each capped by `bt`'s cash; a name with no bar that session is
    not traded. Then the forced exits in `exits` close their names at the frame's price,
    the forward-filled last close. Share counts, marks and cash stay `bt`'s."""

    def __init__(
        self,
        targets: Mapping[date, Mapping[str, float]],
        bars: frozenset[tuple[date, str]],
        exits: Mapping[date, tuple[str, ...]],
    ) -> None:
        super().__init__()
        self._targets, self._bars, self._exits = targets, bars, exits

    def __call__(self, target: bt.core.StrategyBase) -> bool:
        now = target.now.date()
        weights = self._targets.get(now)
        if weights is not None:
            equity = target.value
            held = {sid: c.value for sid, c in target.children.items() if c.position != 0}
            names = [sid for sid in sorted({*held, *weights}) if (now, sid) in self._bars]
            for sid in names:
                if sid not in weights:
                    if sid in held:
                        target.close(sid)
                elif held.get(sid, 0.0) > weights[sid] * equity:
                    target.allocate(weights[sid] * equity - held[sid], sid)
            for sid in names:
                delta = weights.get(sid, 0.0) * equity - held.get(sid, 0.0)
                if sid in weights and delta > 0:
                    target.allocate(min(delta, target.capital), sid)
        for sid in self._exits.get(now, ()):
            target.close(sid)
        return True


def _run_bt(
    result: BacktestResult,
    targets: Mapping[date, Mapping[str, float]],
    exits: Mapping[date, tuple[str, ...]],
) -> bt.Backtest:
    """`bt` over `result`'s stitched frame, filled to `targets` with the forced `exits`,
    fractional shares, no commissions, on every session our engine reports."""
    prices, bars = _stitched(result, sorted(_our_equity(result)))
    backtest = bt.Backtest(
        bt.Strategy("oracle", [_Fill(targets, bars, exits)]),
        prices,
        initial_capital=_frozen().backtest.initial_capital,
        commissions=lambda quantity, price: 0.0,
        integer_positions=False,
        progress_bar=False,
    )
    backtest.run()
    return backtest


def _max_relative_difference(ours: Mapping[date, float], theirs: pd.Series) -> float:
    return max(
        abs(float(theirs[pd.Timestamp(session)]) - value) / abs(value)
        for session, value in ours.items()
    )


def test_bt_and_engine_agree_on_equity_at_every_session(
    run: tuple[Path, int, BacktestResult],
) -> None:
    _, _, result = run
    ours = _our_equity(result)
    assert len(ours) > 200
    assert sum(bool(target) for target in result.targets.values()) >= 10

    backtest = _run_bt(result, result.targets, EXITS)

    assert _max_relative_difference(ours, backtest.strategy.values) <= TOLERANCE


def test_the_delisted_name_is_sold_at_its_last_close_in_both_engines(
    run: tuple[Path, int, BacktestResult],
) -> None:
    _, _, result = run
    # The premise: WNDX is the window's only exit and only missing fill, on EXIT_FILL.
    rows = {row.session: row for row in result.rebalances}
    exits = {s: (r.n_delisting_exits, r.n_stale_exits, r.n_missing_fill) for s, r in rows.items()}
    assert exits == {s: (1, 0, 1) if s == EXIT_REBALANCE else (0, 0, 0) for s in rows}
    assert rows[EXIT_REBALANCE].fill_session == EXIT_FILL
    assert DELISTED not in result.targets[EXIT_FILL]
    stitched = result.stitched_returns().filter(pl.col("security_id") == DELISTED)
    assert stitched["session"].max() == DELISTED_LAST_BAR
    last_close = float(stitched.filter(pl.col("session") == DELISTED_LAST_BAR)["close"].item())

    # Ours: marked at its last close through the exit, zero from the exit session on.
    values = dict(
        result.position_values.filter(pl.col("security_id") == DELISTED)
        .filter(pl.col("session") >= DELISTED_LAST_BAR)
        .select("session", "value")
        .iter_rows()
    )
    held = values[DELISTED_LAST_BAR]
    assert held > 0
    assert all(v == held for s, v in values.items() if s < EXIT_FILL)
    assert all(v == 0.0 for s, v in values.items() if s >= EXIT_FILL)

    # bt: sold on the exit fill session at the stitched last close, for the same value.
    child = _run_bt(result, result.targets, EXITS).strategy.children[DELISTED]
    before, at = pd.Timestamp(EXIT_REBALANCE), pd.Timestamp(EXIT_FILL)
    quantity = float(child.positions[before])
    assert quantity > 0
    assert float(child.positions[at]) == 0.0
    assert float(child.prices[at]) == last_close
    assert abs(quantity * last_close - held) / held <= TOLERANCE


def test_a_one_session_lag_in_bt_fails_the_comparison(
    run: tuple[Path, int, BacktestResult],
) -> None:
    _, _, result = run
    late = {next_session(fill): target for fill, target in result.targets.items()}

    backtest = _run_bt(result, late, EXITS)

    assert _max_relative_difference(_our_equity(result), backtest.strategy.values) > TOLERANCE


def test_the_oracle_trial_is_synthetic_in_the_oracle_family(
    run: tuple[Path, int, BacktestResult], live: Path
) -> None:
    store, trial_id, _ = run
    with duckdb.connect(str(store), read_only=True) as conn:
        row = conn.execute(
            "SELECT t.synthetic, t.kind, h.family, r.status FROM trials t "
            "JOIN hypotheses h USING (hypothesis_id) "
            "JOIN trial_results r USING (trial_id) WHERE t.trial_id = ?",
            [trial_id],
        ).fetchone()
    assert row == (True, "in_sample", "oracle", "ok")
    assert not live.exists()
