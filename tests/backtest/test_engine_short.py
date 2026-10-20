"""The engine's signed book on a hand fixture (backtest spec amendment #1872, items 4 to
7; plan T222).

The `portfolio` keys are refused at registration until T223 (`PortfolioConfig`'s #1788
guard), so these settings are built round the guard with `model_construct`, the #1820
lock pattern. A hand fixture drives the loop through `run` with each rebalance plan's
targets supplied by hand (T223 wires the real `long_short` plan), and every number the
engine produces is checked against a ledger computed here by hand. Zero costs (and a
flat price path) make the ledger exact; the fee, accrual and ruin cases add one term at a
time.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

import polars as pl
import pytest

from backtest import test_engine, test_engine_costs, test_frozen_defaults
from backtest.fake_provider import FakeProvider
from tradepartner.backtest import engine
from tradepartner.backtest.engine import BacktestResult, Plan, run
from tradepartner.backtest.provider import GapReading
from tradepartner.backtest.schedule import fill_session, rebalance_sessions
from tradepartner.calendar import all_sessions, previous_session, session_close
from tradepartner.config import PortfolioConfig, Settings
from tradepartner.store import delistings

T0, T1, T2, T3 = date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 28), date(2024, 4, 30)
F0, F1, F2 = fill_session(T0), fill_session(T1), fill_session(T2)
HISTORY_START, HISTORY_END = date(2022, 12, 1), date(2024, 5, 31)
SESSIONS = [s for s in all_sessions() if HISTORY_START <= s <= HISTORY_END]
CAPITAL = 100_000.0
KNOWN = pl.Datetime("us", "UTC")

#: The one short name of the single-name fixtures (`A`/`B` is the 50/50 pair).
SEC = "S"

_BAR_SCHEMA = {
    "security_id": pl.Utf8,
    "session": pl.Date,
    "open": pl.Float64,
    "close": pl.Float64,
    "known_at": KNOWN,
}
_LISTING_SCHEMA = {
    "security_id": pl.Utf8,
    "valid_from": pl.Date,
    "status": pl.Utf8,
    "end_session": pl.Date,
    "known_at": KNOWN,
}
_DIVIDEND_SCHEMA = {
    "security_id": pl.Utf8,
    "ex_date": pl.Date,
    "ratio_or_amount": pl.Float64,
    "known_at": KNOWN,
}
_RATE_SCHEMA = {
    "series": pl.Utf8,
    "observation_date": pl.Date,
    "value": pl.Float64,
    "vintage_date": pl.Date,
    "known_at": KNOWN,
}


def _signed(**overrides: Any) -> Settings:
    """A `long_short` book's frozen `Settings`, built round `PortfolioConfig`'s #1788
    guard (T223 removes it) with `model_construct`, as T217's tests step round theirs."""
    base = Settings(_env_file=None, strategy={"top_fraction": 0.5}, **overrides)
    portfolio = PortfolioConfig.model_construct(
        side="long_short",
        gross_exposure=1.0,
        net_exposure=0.0,
        short_min_price=5.0,
        short_cover_weight=0.10,
    )
    return base.model_copy(update={"portfolio": portfolio})


def _install_plan(
    monkeypatch: pytest.MonkeyPatch, targets: Mapping[date, Mapping[str, float]]
) -> None:
    """Serve each rebalance session's targets by hand, in place of `engine._plan` (T223
    builds the real signed plan; T222 only reads what it produces)."""

    def fake(provider: Any, params: Settings, session: date, family: Any) -> Plan:
        return Plan(
            session=session,
            fill_session=fill_session(session, params.schedule.rebalance_cadence),
            targets=dict(targets.get(session, {})),
            n_universe=0,
            n_static_listings=0,
            n_excluded_no_history=0,
            gap=GapReading(count_share=0.0, size_share=0.0),
            members=(),
            scores={},
            excluded_no_history=(),
        )

    monkeypatch.setattr(engine, "_plan", fake)


def _bars(changes: Mapping[str, Sequence[tuple[date, float]]]) -> pl.DataFrame:
    """A constant-then-step price path: `(session, close)` points, a point applying from
    its session on, so every mark inside a step is a ratio of two constants."""
    rows = []
    for sid, points in changes.items():
        price: float | None = None
        for session in SESSIONS:
            for at, value in points:
                if at <= session:
                    price = value
            if price is None:
                continue
            rows.append((sid, session, price, price, session_close(session)))
    return pl.DataFrame(rows, schema=_BAR_SCHEMA, orient="row")


def _listing_rows(rows: Sequence[tuple[str, date, str, date | None, date]]) -> pl.DataFrame:
    """`(security_id, valid_from, status, end_session, known on)` listing rows."""
    return pl.DataFrame(
        [(sid, vf, status, end, session_close(known)) for sid, vf, status, end, known in rows],
        schema=_LISTING_SCHEMA,
        orient="row",
    )


def _rates(percent: float) -> pl.DataFrame:
    """One constant `DTB3` observation per session, known at its close."""
    return pl.DataFrame(
        [("DTB3", s, percent, s, session_close(s)) for s in SESSIONS],
        schema=_RATE_SCHEMA,
        orient="row",
    )


def _provider(prices: pl.DataFrame, **kwargs: Any) -> FakeProvider:
    # The plans are supplied by hand, so the universe is never read.
    kwargs.setdefault("raw", prices)
    return FakeProvider(prices=prices, members={}, benchmarks={}, **kwargs)


def _strategy(result: BacktestResult) -> dict[date, tuple[float, float | None]]:
    return {
        row.session: (row.equity, row.cash)
        for row in result.equity
        if row.series == engine.STRATEGY_SERIES
    }


def _equity(result: BacktestResult) -> dict[date, float]:
    return {session: equity for session, (equity, _) in _strategy(result).items()}


def _cash(result: BacktestResult) -> dict[date, float]:
    return {session: cash for session, (_, cash) in _strategy(result).items() if cash is not None}


def _value(result: BacktestResult, sid: str, session: date) -> float:
    rows = result.position_values.filter(
        (pl.col("security_id") == sid) & (pl.col("session") == session)
    )
    return float(rows["value"].sum())


def _row(result: BacktestResult, session: date) -> Any:
    (row,) = [row for row in result.rebalances if row.session == session]
    return row


def _steps(first: date, last: date) -> list[date]:
    return [s for s in SESSIONS if first <= s <= last]


# --- the 50/50 hand fixture: open, a 10 % rise, cover, flip and a delisting cover ------

#: A is long at the first plan, then flipped short at T2 and delisted at `DELIST`. B is
#: short (`+10 %` on Feb 2 against it), covered at T1 and long from T2. Prices are
#: constant between the points, so every ratio is exact.
DELIST = date(2024, 4, 15)
A_CHANGES: list[tuple[date, float]] = [(HISTORY_START, 100.0), (F2, 110.0)]
B_CHANGES: list[tuple[date, float]] = [
    (HISTORY_START, 100.0),
    (date(2024, 2, 2), 110.0),
    (F2, 90.0),
]
TARGETS: dict[date, dict[str, float]] = {
    T0: {"A": 0.5, "B": -0.5},  # the short opens at F0
    T1: {"A": 0.5},  # B is covered at F1
    T2: {"A": -0.5, "B": 0.5},  # A flips long-to-short at F2
}


def _hand_equity(session: date) -> float:
    """The hand ledger: equity is flat while both legs' prices are, and moves only where
    a held leg's price moves. 100,000 at F0; 95,000 when B's 10 % rise hits the short;
    95,000 from F1 (A's carry gain offsets the covered loss); 99,750 from F2 (A's 10 %
    carry gain while long)."""
    if session <= F0:
        return CAPITAL
    if session <= T2:
        return CAPITAL - 5_000.0
    return CAPITAL - 250.0


def _hand_run(monkeypatch: pytest.MonkeyPatch) -> BacktestResult:
    _install_plan(monkeypatch, TARGETS)
    provider = _provider(
        _bars({"A": A_CHANGES, "B": B_CHANGES}),
        listing_ends_rows=_listing_rows(
            [("A", HISTORY_START, delistings.DELISTED, DELIST, DELIST)]
        ),
    )
    return run(_signed(), provider, T0, T3, test_engine._handle(), [0.0], family="momentum")[0.0]


def test_the_signed_book_follows_the_hand_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _hand_run(monkeypatch)
    equity = _equity(result)
    assert set(equity) == set(_steps(T0, T3))
    for session, value in equity.items():
        assert value == pytest.approx(_hand_equity(session), abs=1e-9), session


def test_the_hand_ledger_names_every_position_and_its_cash(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plateau equity is $100,000 cash plus the signed values at every session."""
    result = _hand_run(monkeypatch)
    cash = _cash(result)
    for session in _steps(T0, T3):
        signed = math.fsum(_value(result, sid, session) for sid in ("A", "B"))
        assert cash[session] >= 0
        assert cash[session] + signed == pytest.approx(_hand_equity(session), abs=1e-9), session


def test_a_short_open_a_rise_a_cover_a_flip_and_a_delisting_cover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _hand_run(monkeypatch)
    cash = _cash(result)
    # F0: A long 50,000 (half of capital), B short 50,000; cash 100,000.
    assert _value(result, "A", F0) == pytest.approx(50_000.0)
    assert _value(result, "B", F0) == pytest.approx(-50_000.0)
    assert cash[F0] == pytest.approx(CAPITAL)
    # The 10 % rise on Feb 2 marks the short at -55,000 (the loss), cash unchanged.
    assert _value(result, "B", date(2024, 2, 1)) == pytest.approx(-50_000.0)
    assert _value(result, "B", date(2024, 2, 2)) == pytest.approx(-55_000.0)
    assert cash[T1] == pytest.approx(CAPITAL)
    # T1 covers B: A is trimmed to half of 105,000 (47,500) and 55,000 buys the short back.
    assert _value(result, "A", F1) == pytest.approx(47_500.0)
    assert _value(result, "B", F1) == pytest.approx(0.0)
    assert cash[F1] == pytest.approx(47_500.0)
    # T2 flips A (long to short) and opens B long, each half of 99,750.
    assert _value(result, "A", F2) == pytest.approx(-49_875.0)
    assert _value(result, "B", F2) == pytest.approx(49_875.0)
    assert cash[F2] == pytest.approx(99_750.0)
    # A delists: its short is covered at its last close, so cash pays 49,875 and the
    # position is zero from the delisting session on.
    assert _value(result, "A", previous_session(DELIST)) == pytest.approx(-49_875.0)
    assert _value(result, "A", DELIST) == pytest.approx(0.0)
    assert cash[DELIST] == pytest.approx(49_875.0)
    assert _row(result, T2).n_delisting_exits == 1
    assert _row(result, T2).n_stale_exits == 0


def test_a_delisted_short_covers_at_its_last_close_not_its_otc_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cover books A's delisting-session value even though its bars keep printing."""
    _install_plan(monkeypatch, TARGETS)
    # A's OTC tail prints 100x its last close after the listing ends; it must not mark.
    tail = [(date(2024, 4, 16), 11_000.0)]
    provider = _provider(
        _bars({"A": A_CHANGES + tail, "B": B_CHANGES}),
        listing_ends_rows=_listing_rows(
            [("A", HISTORY_START, delistings.DELISTED, DELIST, DELIST)]
        ),
    )
    result = run(_signed(), provider, T0, T3, test_engine._handle(), [0.0], family="momentum")[0.0]
    assert _value(result, "A", DELIST) == pytest.approx(0.0)
    assert _equity(result)[T3] == pytest.approx(99_750.0)


# --- the dividend on a short (item 5, prior-close term) -------------------------------

DIV_EX = date(2024, 2, 15)
DIV = 1.0
PRIOR_RAW = 50.0  # the raw close of the session before the ex-date
P_AT_EX = 49.0  # the raw close on the ex-date, 1 dollar down
P_AFTER = 51.0


def _raw_close(session: date) -> float:
    if session < DIV_EX:
        return PRIOR_RAW
    return P_AT_EX if session == DIV_EX else P_AFTER


def _dividend_prices() -> tuple[pl.DataFrame, pl.DataFrame]:
    """The raw frame and the store's dividend-adjusted frame: a bar before the ex-date is
    scaled by `1 - D / prior close`, a bar at or after it is raw (spec item 5, #1876)."""
    factor = 1 - DIV / PRIOR_RAW
    raw_rows, adjusted_rows = [], []
    for session in SESSIONS:
        raw = _raw_close(session)
        adjusted = raw * factor if session < DIV_EX else raw
        raw_rows.append((SEC, session, raw, raw, session_close(session)))
        adjusted_rows.append((SEC, session, adjusted, adjusted, session_close(session)))
    return (
        pl.DataFrame(raw_rows, schema=_BAR_SCHEMA, orient="row"),
        pl.DataFrame(adjusted_rows, schema=_BAR_SCHEMA, orient="row"),
    )


def test_a_short_across_a_dividend_ex_date_differs_from_cash_by_item_5s_term(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_plan(monkeypatch, {T0: {SEC: -0.5}})
    raw, adjusted = _dividend_prices()
    provider = _provider(adjusted, dividend_prices=adjusted, raw=raw)
    result = run(_signed(), provider, T0, T1, test_engine._handle(), [0.0], family="momentum")[0.0]
    # $50,000 short at the adjusted fill price of 49; 1,000 raw shares (notional / 50).
    shares = 0.5 * CAPITAL / PRIOR_RAW
    notional = 0.5 * CAPITAL
    equity = _equity(result)
    for session in _steps(F0, T1):
        marked = _value(result, SEC, session)
        cash_paid = -shares * _raw_close(session) - shares * DIV * (session >= DIV_EX)
        term = (
            shares * DIV * (1 - _raw_close(session) / (PRIOR_RAW - DIV))
            if session >= DIV_EX
            else 0.0
        )
        assert marked - cash_paid == pytest.approx(term, abs=1e-9), session
        if session < DIV_EX:
            # Before the ex-date the two frames are a constant apart, so the mark is exact.
            assert marked == pytest.approx(-notional, abs=1e-9), session
        # The equity path carries the same term: cash (150,000 = 100,000 + proceeds) is
        # flat, so the short's mark is the whole difference.
        assert equity[session] - (CAPITAL + notional + cash_paid) == pytest.approx(term, abs=1e-9)


def test_a_short_holding_is_counted_for_a_dropped_dividend(monkeypatch: pytest.MonkeyPatch) -> None:
    """A short held at the close before the ex-date counts, as a long does (item 5)."""
    _install_plan(monkeypatch, {T0: {SEC: -0.5}})
    dropped = pl.DataFrame(
        [(SEC, DIV_EX, DIV, session_close(DIV_EX))],
        schema=_DIVIDEND_SCHEMA,
        orient="row",
    )
    provider = _provider(_bars({SEC: [(HISTORY_START, 100.0)]}), dropped=dropped)
    result = run(_signed(), provider, T0, T1, test_engine._handle(), [0.0], family="momentum")[0.0]
    assert _row(result, T0).n_dropped_dividends == 1


# --- the borrow fee (item 6) -----------------------------------------------------------

#: 360 bp/yr, so a short of 50,000 pays 50,000 * 360/10,000 / 360 = $5 per day.
BORROW_BPS = 360.0


def test_the_borrow_fee_over_a_weekend_is_three_days(monkeypatch: pytest.MonkeyPatch) -> None:
    week_t0, week_t1 = date(2024, 2, 29), date(2024, 3, 28)  # fill F0 = Friday 2024-03-01
    monday, tuesday = date(2024, 3, 4), date(2024, 3, 5)
    _install_plan(monkeypatch, {week_t0: {SEC: -0.5}})
    provider = _provider(_bars({SEC: [(HISTORY_START, 100.0)]}))
    settings = _signed(costs={"per_side_bps": 0.0, "borrow_bps_per_year": BORROW_BPS})
    result = run(
        settings, provider, week_t0, week_t1, test_engine._handle(), [0.0], family="momentum"
    )[0.0]
    cash = _cash(result)
    assert cash[date(2024, 3, 1)] == pytest.approx(CAPITAL + 0.5 * CAPITAL)
    assert cash[monday] == pytest.approx(CAPITAL + 0.5 * CAPITAL - 15.0)
    assert cash[tuesday] == pytest.approx(CAPITAL + 0.5 * CAPITAL - 20.0)
    # Friday to Monday is three calendar days; Monday to Tuesday is one.
    assert cash[date(2024, 3, 1)] - cash[monday] == pytest.approx(
        3 * (cash[monday] - cash[tuesday])
    )


def test_the_carry_window_to_the_fill_is_charged_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan-to-fill session (T_i, F_i] is charged on the positions held at close(T_i),
    so a monthly book is not one session short each rebalance and a daily book is not free
    (its fill session is the step's own end)."""
    _install_plan(monkeypatch, {T0: {SEC: -0.5}, T1: {SEC: 0.0}})
    provider = _provider(_bars({SEC: [(HISTORY_START, 100.0)]}))
    settings = _signed(costs={"per_side_bps": 0.0, "borrow_bps_per_year": BORROW_BPS})
    result = run(settings, provider, T0, T2, test_engine._handle(), [0.0], family="momentum")[0.0]
    cash = _cash(result)
    short = abs(_value(result, SEC, T1))
    # T1 -> F1 covers the short; the carried close(T1) value is charged one day first.
    assert cash[T1] - cash[F1] == pytest.approx(short + short * BORROW_BPS / 1e4 / 360, abs=1e-9)
    assert cash[T1] - cash[F1] > short


def test_a_daily_signed_book_pays_the_borrow_fee_each_day(monkeypatch: pytest.MonkeyPatch) -> None:
    days = rebalance_sessions(date(2024, 3, 1), date(2024, 3, 7), "daily")
    cover = days[-1]
    targets = {session: {SEC: -0.5} for session in days[:-1]}
    targets[days[-2]] = {SEC: 0.0}  # the plan whose fill (the last session) covers
    _install_plan(monkeypatch, targets)
    provider = _provider(_bars({SEC: [(HISTORY_START, 100.0)]}))
    settings = _signed(
        schedule={"rebalance_cadence": "daily"},
        costs={"per_side_bps": 0.0, "borrow_bps_per_year": BORROW_BPS},
    )
    result = run(
        settings, provider, days[0], cover, test_engine._handle(), [0.0], family="momentum"
    )[0.0]
    cash = _cash(result)
    previous = previous_session(cover)
    short = abs(_value(result, SEC, previous))
    # The final cover pays one day's fee on the previous close's short before it fills.
    assert cash[previous] - cash[cover] == pytest.approx(
        short + short * BORROW_BPS / 1e4 / 360, abs=1e-9
    )
    # And the days before it are charged too: a zero-borrow twin, everything else equal,
    # ends strictly richer (pre-fix the two runs were byte-identical at a daily cadence).
    free_provider = _provider(_bars({SEC: [(HISTORY_START, 100.0)]}))
    free = run(
        _signed(schedule={"rebalance_cadence": "daily"}, costs={"per_side_bps": 0.0}),
        free_provider,
        days[0],
        cover,
        test_engine._handle(),
        [0.0],
        family="momentum",
    )[0.0]
    assert _equity(result)[cover] < _equity(free)[cover]


# --- the free-cash accrual (item 6, "no rebate") --------------------------------------


def test_the_free_cash_accrual_is_the_hand_product(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_plan(monkeypatch, {T0: {SEC: -0.5}})
    provider = _provider(_bars({SEC: [(HISTORY_START, 100.0)]}), rate_rows=_rates(5.0))
    settings = _signed(
        metrics={"risk_free_source": "store", "risk_free_basis": "yield"},
        backtest={"cash_rate": "store"},
    )
    result = run(settings, provider, T0, T1, test_engine._handle(), [0.0], family="momentum")[0.0]

    def accrual(session: date) -> float:
        return 0.05 * (session - previous_session(session)).days / 365

    # Cash held to the fill accrues (no short is held yet, so all of it is free).
    cash = CAPITAL + CAPITAL * accrual(F0)
    proceeds = 0.5 * cash  # the short opens at half of the equity at the fill
    cash += proceeds
    expected = {F0: cash}
    for session in _steps(F0, T1):
        if session == F0:
            continue
        # Interest is on free cash only: the short proceeds earn nothing.
        cash = cash + (cash - proceeds) * accrual(session)
        expected[session] = cash

    got = _cash(result)
    for session, value in expected.items():
        assert got[session] == pytest.approx(value, rel=0, abs=1e-9), session
    # Each session's interest is the free-cash product (cash minus the proceeds), not the
    # whole balance's, so the proceeds never earn.
    for session in _steps(F0, T1):
        if session == F0:
            continue
        previous = previous_session(session)
        assert got[session] - got[previous] == pytest.approx(
            (got[previous] - proceeds) * accrual(session), abs=1e-9
        ), session


# --- ruin (item 4) --------------------------------------------------------------------


def test_a_ruined_signed_book_raises_naming_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    ruin = date(2024, 2, 8)
    _install_plan(monkeypatch, {T0: {SEC: -0.5}})
    # A 4x rise: the short is -200,000 against 150,000 of cash, so equity is gone.
    provider = _provider(_bars({SEC: [(HISTORY_START, 100.0), (ruin, 400.0)]}))
    with pytest.raises(ValueError, match=rf"equity is gone on {ruin.isoformat()}"):
        run(_signed(), provider, T0, T1, test_engine._handle(), [0.0], family="momentum")


def test_a_short_of_half_the_book_is_not_ruined_by_a_10_percent_rise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rise = date(2024, 2, 8)
    _install_plan(monkeypatch, {T0: {SEC: -0.5}})
    provider = _provider(_bars({SEC: [(HISTORY_START, 100.0), (rise, 110.0)]}))
    result = run(_signed(), provider, T0, T1, test_engine._handle(), [0.0], family="momentum")[0.0]
    assert _equity(result)[T1] == pytest.approx(CAPITAL - 5_000.0)


# --- the long-only pins (unchanged and unedited) --------------------------------------


def test_the_long_only_pins_are_byte_identical() -> None:
    test_frozen_defaults.test_h1_twin_canonical_set_fingerprint_and_hash_unchanged()
    digests = test_engine_costs._twin_digests(test_engine._twin_settings())
    assert digests == test_engine.TWIN_RUN_DIGESTS


def test_the_reference_metrics_at_the_defaults_are_unchanged() -> None:
    default = Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        costs={"per_side_bps": 15.0, "sensitivity_per_side_bps": [0.0, 30.0, 100.0]},
        metrics={"risk_free_source": "constant"},  # the digest's source, pre-T170d (#2011)
    )
    assert test_engine_costs._metrics_digest(default) == test_engine_costs.REFERENCE_METRICS_SHA256
