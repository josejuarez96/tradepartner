"""Tests for tradepartner.store.asof (T6).

Covers the "Timing and store" acceptance criteria in
docs/specs/data-foundation.md that name `prices_as_of`,
`adjusted_prices_as_of` and `facts_as_of` directly, plus `listings_as_of`'s
issue #35 behavior: latest revision as of T, bar revision, a split known
before T with ex-date after T (not applied) versus once its ex-date has
also passed (applied), the backfilled-2018-split acceptance criterion, a
revised dividend, a restated shares fact, and "a bare date passed as T
raises". Every as-of function returns a `polars.DataFrame` (this module's
docstring); `_one` filters one down to a single named row for assertions.

Audit round 1 additions (PR #69): exchange-local `ex_date <= T` at the
UTC/ET day boundary, invalid (non-positive/non-finite) adjustment factors
raising `ValueError`, a dividend's prior-close ASOF fallback when the
exact prior session's bar is missing, the ASOF-join boundary (a bar *on*
the ex-date is raw), a compounding case sensitive to the cumulative
window's direction, and `security_ids=[]` returning an empty frame. These
use a `synthetic_store` (schema only, no CSV fixture data) via `_bar`/
`_action` so the scenario's exact numbers are controlled directly, rather
than hunting for a fixture case that happens to fit.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime

import duckdb
import polars as pl
import pytest

from tradepartner.config import AdjustConfig, Settings
from tradepartner.store import schema
from tradepartner.store.asof import (
    adjusted_prices_as_of,
    dropped_dividends_as_of,
    facts_as_of,
    listings_as_of,
    prices_as_of,
)
from tradepartner.store.db import configure_connection, insert_row


def _one(df: pl.DataFrame, **match: object) -> dict[str, object]:
    """The single row of `df` whose fields match every `match` kwarg, as a
    `{column: value}` dict, or fail loudly (there should never be more or
    fewer than one for these tests' natural keys)."""
    filtered = df
    for key, value in match.items():
        filtered = filtered.filter(pl.col(key) == value)
    assert filtered.height == 1, f"expected exactly one match for {match}, got {filtered}"
    return filtered.row(0, named=True)


@pytest.fixture
def synthetic_store() -> Iterator[duckdb.DuckDBPyConnection]:
    """A fresh in-memory store with the schema applied and no fixture data
    loaded, for tests that need exact control over a scenario's numbers
    rather than a CSV fixture case that happens to fit."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def _bar(
    conn: duckdb.DuckDBPyConnection,
    security_id: str,
    session: date,
    close: float,
    *,
    known_at: datetime,
) -> None:
    """Insert one `prices_daily` row with `open = high = low = close`
    (these tests only ever assert on `close`) and `ingested_at =
    known_at`."""
    insert_row(
        conn,
        "prices_daily",
        {
            "security_id": security_id,
            "session": session,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "test",
            "provenance": "bar",
        },
    )


def _action(
    conn: duckdb.DuckDBPyConnection,
    security_id: str,
    action_type: str,
    ex_date: date,
    ratio_or_amount: float,
    *,
    known_at: datetime,
) -> None:
    """Insert one `corporate_actions` row with `ingested_at = known_at`."""
    insert_row(
        conn,
        "corporate_actions",
        {
            "security_id": security_id,
            "action_type": action_type,
            "ex_date": ex_date,
            "ratio_or_amount": ratio_or_amount,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "test",
            "provenance": "action",
        },
    )


class TestPricesAsOf:
    def test_bar_revision_returns_original_before_and_revision_after(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # SEC_SPLIT_BACKFILLED, session 2018-06-01: original close 38.45
        # (known_at 2018-06-01T20:00:00+00:00), revision close 40.37
        # (known_at 2018-06-15T20:00:00+00:00).
        before_revision = datetime(2018, 6, 15, 19, 59, 59, tzinfo=UTC)
        after_revision = datetime(2018, 6, 15, 20, 0, 0, tzinfo=UTC)

        before_rows = prices_as_of(
            fixture_store, before_revision, security_ids=["SEC_SPLIT_BACKFILLED"]
        )
        original = _one(before_rows, session=date(2018, 6, 1))
        assert original["close"] == pytest.approx(38.45)

        after_rows = prices_as_of(
            fixture_store, after_revision, security_ids=["SEC_SPLIT_BACKFILLED"]
        )
        revised = _one(after_rows, session=date(2018, 6, 1))
        assert revised["close"] == pytest.approx(40.37)

    def test_only_one_row_per_session_ever_returned(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        far_future = datetime(2030, 1, 1, tzinfo=UTC)
        rows = prices_as_of(fixture_store, far_future, security_ids=["SEC_SPLIT_BACKFILLED"])
        assert rows["session"].n_unique() == rows.height

    def test_bare_date_raises(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(TypeError):
            prices_as_of(fixture_store, date(2019, 1, 31))  # type: ignore[arg-type]

    def test_naive_datetime_raises(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(ValueError):
            prices_as_of(fixture_store, datetime(2019, 1, 31, 21, 0, 0))  # noqa: DTZ001

    def test_empty_security_ids_returns_empty_frame_with_schema(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # NIT 7: security_ids=[] must not build "IN ()" (a DuckDB syntax
        # error) -- it means "restrict to no securities", i.e. no rows,
        # with the normal prices_daily column schema still intact.
        t = datetime(2019, 1, 31, 21, 0, 0, tzinfo=UTC)
        rows = prices_as_of(fixture_store, t, security_ids=[])
        assert rows.height == 0
        assert rows.columns == prices_as_of(fixture_store, t, security_ids=["SEC_SPY"]).columns


class TestAdjustedPricesAsOf:
    def test_backfilled_2018_split_adjusts_2017_prices(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Spec acceptance: "Backfilled 2018 split in 2026:
        # adjusted_prices_as_of(2019-01-31 close) adjusts 2017 prices."
        # SEC_SPLIT_BACKFILLED: split ex_date 2018-03-15, known_at
        # 2018-03-14T20:00:00+00:00, ingested_at 2026-01-20 (a late
        # backfill discovery -- known_at is unaffected by that).
        t = datetime(2019, 1, 31, 21, 0, 0, tzinfo=UTC)
        raw = _one(
            prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_BACKFILLED"]),
            session=date(2017, 1, 3),
        )
        adjusted = _one(
            adjusted_prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_BACKFILLED"]),
            session=date(2017, 1, 3),
        )
        assert adjusted["close"] == pytest.approx(float(raw["close"]) / 2.0)
        assert adjusted["open"] == pytest.approx(float(raw["open"]) / 2.0)
        assert adjusted["high"] == pytest.approx(float(raw["high"]) / 2.0)
        assert adjusted["low"] == pytest.approx(float(raw["low"]) / 2.0)

    def test_split_known_before_t_with_ex_date_after_t_not_applied(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Spec req 8 level rule + acceptance line "Split known before T
        # with ex-date after T: ... rule 4 uses the raw close." SEC_SPLIT_
        # FUTURE: split announced (known_at) 2018-11-15T21:00:00+00:00,
        # ex_date 2019-02-14 (far after). At probe T = 2018-11-23 (from the
        # fixture README), the split is known but not yet effective
        # (ex_date > T), so even a session long before ex-date must stay
        # unadjusted.
        t = datetime(2018, 11, 23, 18, 0, 0, tzinfo=UTC)
        raw = _one(
            prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_FUTURE"]),
            session=date(2018, 6, 1),
        )
        adjusted = _one(
            adjusted_prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_FUTURE"]),
            session=date(2018, 6, 1),
        )
        assert adjusted["close"] == pytest.approx(float(raw["close"]))

    def test_split_applied_once_both_known_and_ex_date_has_passed(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Same split, probed after its ex_date (2019-02-14) has passed:
        # now both known_at <= T and ex_date <= T hold, so bars before the
        # ex-date are adjusted.
        t = datetime(2019, 2, 15, tzinfo=UTC)
        raw = _one(
            prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_FUTURE"]),
            session=date(2018, 6, 1),
        )
        adjusted = _one(
            adjusted_prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_FUTURE"]),
            session=date(2018, 6, 1),
        )
        assert adjusted["close"] == pytest.approx(float(raw["close"]) / 4.0)

    def test_revised_dividend_uses_old_amount_before_new_after(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # SEC_DIV_REVISED: ex_date 2019-02-25, first-seen known_at
        # 2019-02-22T21:00:00+00:00 amount 0.10; revision known_at =
        # ingested_at 2019-03-25T21:00:00+00:00 amount 0.12. The session
        # immediately before ex_date is 2019-02-22 (raw close 37.41).
        session = date(2019, 2, 21)
        prior_close = 37.41

        before_revision = datetime(2019, 3, 25, 20, 59, 59, 999999, tzinfo=UTC)
        after_revision = datetime(2019, 3, 25, 21, 0, 0, tzinfo=UTC)

        raw = _one(
            prices_as_of(fixture_store, before_revision, security_ids=["SEC_DIV_REVISED"]),
            session=session,
        )
        raw_close = float(raw["close"])

        old_amount_adjusted = _one(
            adjusted_prices_as_of(
                fixture_store,
                before_revision,
                security_ids=["SEC_DIV_REVISED"],
                include_dividends=True,
            ),
            session=session,
        )
        assert old_amount_adjusted["close"] == pytest.approx(raw_close * (1 - 0.10 / prior_close))

        new_amount_adjusted = _one(
            adjusted_prices_as_of(
                fixture_store,
                after_revision,
                security_ids=["SEC_DIV_REVISED"],
                include_dividends=True,
            ),
            session=session,
        )
        assert new_amount_adjusted["close"] == pytest.approx(raw_close * (1 - 0.12 / prior_close))

    def test_dividends_ignored_by_default(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        t = datetime(2019, 3, 26, tzinfo=UTC)
        session = date(2019, 2, 21)
        raw = _one(
            prices_as_of(fixture_store, t, security_ids=["SEC_DIV_REVISED"]), session=session
        )
        adjusted = _one(
            adjusted_prices_as_of(fixture_store, t, security_ids=["SEC_DIV_REVISED"]),
            session=session,
        )
        assert adjusted["close"] == pytest.approx(float(raw["close"]))

    def test_bare_date_raises(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(TypeError):
            adjusted_prices_as_of(fixture_store, date(2019, 1, 31))  # type: ignore[arg-type]

    def test_empty_security_ids_returns_empty_frame_with_schema(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        t = datetime(2019, 1, 31, 21, 0, 0, tzinfo=UTC)
        rows = adjusted_prices_as_of(fixture_store, t, security_ids=[])
        assert rows.height == 0
        assert (
            rows.columns
            == adjusted_prices_as_of(fixture_store, t, security_ids=["SEC_SPY"]).columns
        )

    def test_ex_date_compared_in_exchange_local_time_not_applied_before_open(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Audit round 1, finding 1. SEC_SPLIT_FUTURE's split has ex_date
        # 2019-02-14. T = 2019-02-14T01:00Z is 2019-02-13T20:00
        # America/New_York (EST, UTC-5 in February) -- still the day
        # *before* the ex-date in the exchange's own calendar, even though
        # T's UTC date already reads 2019-02-14. Comparing against T's UTC
        # date (the bug) would wrongly apply the split here.
        t = datetime(2019, 2, 14, 1, 0, 0, tzinfo=UTC)
        raw = _one(
            prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_FUTURE"]),
            session=date(2018, 6, 1),
        )
        adjusted = _one(
            adjusted_prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_FUTURE"]),
            session=date(2018, 6, 1),
        )
        assert adjusted["close"] == pytest.approx(float(raw["close"]))

    def test_ex_date_compared_in_exchange_local_time_applied_after_open(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Same split; T = 2019-02-14T15:00Z is 2019-02-14T10:00
        # America/New_York -- now the ex-date itself in exchange-local
        # time, so the split is effective.
        t = datetime(2019, 2, 14, 15, 0, 0, tzinfo=UTC)
        raw = _one(
            prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_FUTURE"]),
            session=date(2018, 6, 1),
        )
        adjusted = _one(
            adjusted_prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_FUTURE"]),
            session=date(2018, 6, 1),
        )
        assert adjusted["close"] == pytest.approx(float(raw["close"]) / 4.0)

    def test_bar_on_ex_date_itself_is_raw_prior_session_is_adjusted(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Audit round 1, finding 4(a): the ASOF-join boundary is strict
        # (`session < ex_date`), so the bar dated exactly on the ex-date
        # is never adjusted for that event, while the immediately
        # preceding session's bar is. SEC_SPLIT_BACKFILLED's split has
        # ex_date 2018-03-15; its own fixture bars already show the real
        # price drop across that boundary (raw close 70.91 on 2018-03-14,
        # 35.16 on 2018-03-15 -- roughly a 2x split), which this asserts
        # against directly rather than just checking "unchanged".
        t = datetime(2019, 1, 31, 21, 0, 0, tzinfo=UTC)
        on_ex_date = _one(
            adjusted_prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_BACKFILLED"]),
            session=date(2018, 3, 15),
        )
        raw_on_ex_date = _one(
            prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_BACKFILLED"]),
            session=date(2018, 3, 15),
        )
        assert on_ex_date["close"] == pytest.approx(float(raw_on_ex_date["close"]))

        prior_session = _one(
            adjusted_prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_BACKFILLED"]),
            session=date(2018, 3, 14),
        )
        raw_prior_session = _one(
            prices_as_of(fixture_store, t, security_ids=["SEC_SPLIT_BACKFILLED"]),
            session=date(2018, 3, 14),
        )
        assert prior_session["close"] == pytest.approx(float(raw_prior_session["close"]) / 2.0)


class TestAdjustedPricesAsOfSynthetic:
    """Audit round 1 findings 2, 3 and 4(b): scenarios that need exact
    control over the numbers, built directly on a `synthetic_store` rather
    than hunted for in the CSV fixture."""

    def test_dividend_at_or_above_prior_close_raises(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Finding 2. A dividend amount >= the prior close makes
        # `1 - amount / prior_close` zero or negative -- DuckDB's LN()
        # (used by the cumulative-factor window) raises on that input
        # rather than returning -inf/NaN, so this must be caught and
        # turned into a clean ValueError before LN() ever runs.
        _bar(
            synthetic_store,
            "SEC_BAD_DIV",
            date(2021, 1, 4),
            close=10.0,
            known_at=datetime(2021, 1, 4, 21, 0, tzinfo=UTC),
        )
        _action(
            synthetic_store,
            "SEC_BAD_DIV",
            "dividend",
            date(2021, 1, 5),
            10.0,  # amount == prior close -> factor exactly 0
            known_at=datetime(2021, 1, 4, 22, 0, tzinfo=UTC),
        )
        t = datetime(2021, 2, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="SEC_BAD_DIV"):
            adjusted_prices_as_of(synthetic_store, t, include_dividends=True)

    def test_negative_dividend_amount_raises(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Audit round 2, finding 2. A negative dividend amount makes
        # `1 - amount / prior_close` a positive, finite number greater
        # than 1 (it would *raise* the adjusted price), so the
        # factor > 0 AND isfinite(factor) check alone lets it through --
        # a negative dividend is simply bad data and needs its own check.
        _bar(
            synthetic_store,
            "SEC_NEGATIVE_DIV",
            date(2021, 1, 4),
            close=10.0,
            known_at=datetime(2021, 1, 4, 21, 0, tzinfo=UTC),
        )
        _action(
            synthetic_store,
            "SEC_NEGATIVE_DIV",
            "dividend",
            date(2021, 1, 5),
            -1.0,
            known_at=datetime(2021, 1, 4, 22, 0, tzinfo=UTC),
        )
        t = datetime(2021, 2, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="SEC_NEGATIVE_DIV"):
            adjusted_prices_as_of(synthetic_store, t, include_dividends=True)

    def test_zero_split_ratio_raises(self, synthetic_store: duckdb.DuckDBPyConnection) -> None:
        # Finding 2. A split ratio_or_amount of 0 divides by zero; DuckDB
        # returns `inf` for that (no error), which would otherwise
        # silently poison every earlier bar's adjusted price with `inf`.
        _bar(
            synthetic_store,
            "SEC_BAD_SPLIT",
            date(2021, 1, 4),
            close=10.0,
            known_at=datetime(2021, 1, 4, 21, 0, tzinfo=UTC),
        )
        _action(
            synthetic_store,
            "SEC_BAD_SPLIT",
            "split",
            date(2021, 1, 5),
            0.0,
            known_at=datetime(2021, 1, 4, 22, 0, tzinfo=UTC),
        )
        t = datetime(2021, 2, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="SEC_BAD_SPLIT"):
            adjusted_prices_as_of(synthetic_store, t)

    def test_dividend_prior_close_falls_back_to_latest_known_bar_before_ex_date(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Finding 3. No bar exists on 2021-01-08 (the session that would
        # immediately precede the 2021-01-11 ex-date if it were present);
        # the only known bar before the ex-date is from 2021-01-04. The
        # ASOF join must fall back to that bar rather than dropping the
        # dividend's factor entirely.
        _bar(
            synthetic_store,
            "SEC_GAP_DIV",
            date(2021, 1, 4),
            close=50.0,
            known_at=datetime(2021, 1, 4, 21, 0, tzinfo=UTC),
        )
        _action(
            synthetic_store,
            "SEC_GAP_DIV",
            "dividend",
            date(2021, 1, 11),
            5.0,
            known_at=datetime(2021, 1, 10, 21, 0, tzinfo=UTC),
        )
        t = datetime(2021, 2, 1, tzinfo=UTC)
        adjusted = _one(
            adjusted_prices_as_of(synthetic_store, t, include_dividends=True),
            session=date(2021, 1, 4),
        )
        # factor = 1 - 5.0 / 50.0 = 0.9
        assert adjusted["close"] == pytest.approx(50.0 * 0.9)

    def test_two_splits_and_a_dividend_compound_in_the_right_direction(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Finding 4(b). Sessions s1 < s2 < s3 < s4; split1 (ratio 2) at
        # ex_date s2, split2 (ratio 3) at ex_date s3, a dividend (amount
        # 2.0, against the raw close at s3 = 20.0 via the ASOF prior-close
        # join, factor 1 - 2/20 = 0.9) at ex_date s4. Expected factors:
        # s1 (before every event): 1/2 * 1/3 * 0.9 = 0.15
        # s2 (after split1, before split2): 1/3 * 0.9 = 0.3
        # s3 (after split2, before the dividend): 0.9
        # s4 (on the dividend's own ex-date): 1.0 (raw)
        # These four expected values are only reproduced by accumulating
        # the cumulative-factor window from the *latest* ex_date backward
        # (`ORDER BY ex_date DESC`); accumulating forward (`ASC`) computes
        # a materially different, wrong number for every bar except the
        # last, so this fails loudly under that regression.
        s1, s2, s3, s4 = date(2021, 1, 4), date(2021, 1, 5), date(2021, 1, 6), date(2021, 1, 7)
        sid = "SEC_COMPOUND"
        _bar(synthetic_store, sid, s1, close=1000.0, known_at=datetime(2021, 1, 4, 21, tzinfo=UTC))
        _bar(synthetic_store, sid, s2, close=50.0, known_at=datetime(2021, 1, 5, 21, tzinfo=UTC))
        _bar(synthetic_store, sid, s3, close=20.0, known_at=datetime(2021, 1, 6, 21, tzinfo=UTC))
        _bar(synthetic_store, sid, s4, close=25.0, known_at=datetime(2021, 1, 7, 21, tzinfo=UTC))
        _action(
            synthetic_store, sid, "split", s2, 2.0, known_at=datetime(2021, 1, 4, 22, tzinfo=UTC)
        )
        _action(
            synthetic_store, sid, "split", s3, 3.0, known_at=datetime(2021, 1, 5, 22, tzinfo=UTC)
        )
        _action(
            synthetic_store,
            sid,
            "dividend",
            s4,
            2.0,
            known_at=datetime(2021, 1, 6, 22, tzinfo=UTC),
        )

        t = datetime(2021, 2, 1, tzinfo=UTC)
        adjusted = adjusted_prices_as_of(synthetic_store, t, include_dividends=True)

        assert _one(adjusted, session=s1)["close"] == pytest.approx(1000.0 * 0.15)
        assert _one(adjusted, session=s2)["close"] == pytest.approx(50.0 * 0.3)
        assert _one(adjusted, session=s3)["close"] == pytest.approx(20.0 * 0.9)
        assert _one(adjusted, session=s4)["close"] == pytest.approx(25.0)


def _gap_settings(max_gap: int) -> Settings:
    """Settings with `adjust.max_prior_close_gap_sessions = max_gap`, no `.env`."""
    return Settings(_env_file=None, adjust=AdjustConfig(max_prior_close_gap_sessions=max_gap))


class TestDividendPriorCloseStaleness:
    """Issue #72: the dividend prior-close ASOF fallback is bounded by
    `adjust.max_prior_close_gap_sessions`, counted in XNYS sessions. A
    prior bar further back leaves the dividend unapplied (NULL factor) and
    `dropped_dividends_as_of` reports it."""

    T = datetime(2021, 3, 1, tzinfo=UTC)

    def _gap_scenario(self, conn: duckdb.DuckDBPyConnection, amount: float = 5.0) -> None:
        # Only known bar before the 2021-01-11 ex-date is 2021-01-04. XNYS
        # sessions in [01-04, 01-11): 04, 05, 06, 07, 08 -> gap of 5.
        _bar(conn, "SEC_GAP", date(2021, 1, 4), 50.0, known_at=datetime(2021, 1, 4, 21, tzinfo=UTC))
        _bar(
            conn, "SEC_GAP", date(2021, 1, 11), 45.0, known_at=datetime(2021, 1, 11, 21, tzinfo=UTC)
        )
        _action(
            conn,
            "SEC_GAP",
            "dividend",
            date(2021, 1, 11),
            amount,
            known_at=datetime(2021, 1, 8, 21, tzinfo=UTC),
        )

    def test_prior_bar_exactly_at_the_limit_is_used(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        self._gap_scenario(synthetic_store)
        adjusted = adjusted_prices_as_of(
            synthetic_store, self.T, include_dividends=True, settings=_gap_settings(5)
        )
        assert _one(adjusted, session=date(2021, 1, 4))["close"] == pytest.approx(50.0 * 0.9)
        dropped = dropped_dividends_as_of(synthetic_store, self.T, settings=_gap_settings(5))
        assert dropped.height == 0

    def test_prior_bar_one_session_past_the_limit_is_not_used(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        self._gap_scenario(synthetic_store)
        adjusted = adjusted_prices_as_of(
            synthetic_store, self.T, include_dividends=True, settings=_gap_settings(4)
        )
        assert _one(adjusted, session=date(2021, 1, 4))["close"] == pytest.approx(50.0)
        dropped = dropped_dividends_as_of(synthetic_store, self.T, settings=_gap_settings(4))
        row = _one(dropped, security_id="SEC_GAP")
        assert row["ex_date"] == date(2021, 1, 11)
        assert row["prior_session"] == date(2021, 1, 4)
        assert row["gap_sessions"] == 5
        assert row["reason"] == "stale_prior_bar"

    def test_stale_prior_close_below_amount_does_not_raise(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # The stale close (50.0) is below the amount: used as-is it would
        # make the factor negative and fail the whole query. Once it is
        # out of bounds the dividend is dropped instead.
        self._gap_scenario(synthetic_store, amount=60.0)
        adjusted = adjusted_prices_as_of(
            synthetic_store, self.T, include_dividends=True, settings=_gap_settings(4)
        )
        assert _one(adjusted, session=date(2021, 1, 4))["close"] == pytest.approx(50.0)

    def test_gap_counts_xnys_sessions_not_weekdays(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # 2021-01-18 is MLK Day (XNYS closed). Sessions in [01-14, 01-19):
        # 14, 15 -> gap 2. Counting weekdays would give 3 and drop it.
        sid = "SEC_HOLIDAY"
        _bar(
            synthetic_store,
            sid,
            date(2021, 1, 14),
            20.0,
            known_at=datetime(2021, 1, 14, 21, tzinfo=UTC),
        )
        _action(
            synthetic_store,
            sid,
            "dividend",
            date(2021, 1, 19),
            1.0,
            known_at=datetime(2021, 1, 15, 21, tzinfo=UTC),
        )
        adjusted = adjusted_prices_as_of(
            synthetic_store, self.T, include_dividends=True, settings=_gap_settings(2)
        )
        assert _one(adjusted, session=date(2021, 1, 14))["close"] == pytest.approx(19.0)

    def test_dividend_with_no_prior_bar_is_reported(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        sid = "SEC_NO_PRIOR"
        _bar(
            synthetic_store,
            sid,
            date(2021, 1, 11),
            45.0,
            known_at=datetime(2021, 1, 11, 21, tzinfo=UTC),
        )
        _action(
            synthetic_store,
            sid,
            "dividend",
            date(2021, 1, 11),
            1.0,
            known_at=datetime(2021, 1, 8, 21, tzinfo=UTC),
        )
        row = _one(dropped_dividends_as_of(synthetic_store, self.T), security_id=sid)
        assert row["reason"] == "no_prior_bar"
        assert row["prior_session"] is None
        assert row["gap_sessions"] is None

    def test_dropped_dividends_respects_known_at_and_ex_date(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Before the dividend is known, and after it is known but before
        # its ex-date, it is not a drop: it does not apply yet at all.
        self._gap_scenario(synthetic_store)
        cfg = _gap_settings(4)
        before_known = datetime(2021, 1, 8, 20, tzinfo=UTC)
        before_ex = datetime(2021, 1, 10, 12, tzinfo=UTC)
        assert dropped_dividends_as_of(synthetic_store, before_known, settings=cfg).height == 0
        assert dropped_dividends_as_of(synthetic_store, before_ex, settings=cfg).height == 0

    def test_dropped_dividends_filters_security_ids(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        self._gap_scenario(synthetic_store)
        cfg = _gap_settings(4)
        assert dropped_dividends_as_of(synthetic_store, self.T, ["OTHER"], settings=cfg).height == 0
        assert dropped_dividends_as_of(synthetic_store, self.T, [], settings=cfg).height == 0
        assert (
            dropped_dividends_as_of(synthetic_store, self.T, ["SEC_GAP"], settings=cfg).height == 1
        )

    def test_dropped_dividends_bare_date_raises(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        with pytest.raises(TypeError):
            dropped_dividends_as_of(synthetic_store, date(2021, 3, 1))  # type: ignore[arg-type]


class TestFactsAsOf:
    def test_restated_shares_fact_returns_earlier_then_later_value(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # SEC_FACTS_RESTATED, as_of_date 2019-01-16: known_at
        # 2019-01-24T20:30:00+00:00 value 1,000,000; known_at
        # 2019-03-29T20:30:00+00:00 value 1,050,000.
        before_restatement = datetime(2019, 3, 29, 20, 29, 59, tzinfo=UTC)
        after_restatement = datetime(2019, 3, 29, 20, 30, 0, tzinfo=UTC)

        earlier = _one(
            facts_as_of(fixture_store, before_restatement, security_ids=["SEC_FACTS_RESTATED"]),
            as_of_date=date(2019, 1, 16),
        )
        assert earlier["value"] == pytest.approx(1_000_000)

        later = _one(
            facts_as_of(fixture_store, after_restatement, security_ids=["SEC_FACTS_RESTATED"]),
            as_of_date=date(2019, 1, 16),
        )
        assert later["value"] == pytest.approx(1_050_000)

    def test_before_any_known_at_returns_nothing(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        far_past = datetime(2000, 1, 1, tzinfo=UTC)
        rows = facts_as_of(fixture_store, far_past, security_ids=["SEC_FACTS_RESTATED"])
        assert rows.height == 0

    def test_bare_date_raises(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(TypeError):
            facts_as_of(fixture_store, date(2019, 1, 16))  # type: ignore[arg-type]


class TestListingsAsOf:
    def test_snapshot_static_listing_invisible_before_its_own_known_at(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # SEC_STATIC_PRE2019 (ticker PRE9): valid_from 2017-01-03,
        # provenance snapshot_static, known_at 2020-01-15T14:00:00+00:00.
        # Issue #35: known_at <= T applies literally here too.
        just_before = datetime(2020, 1, 15, 13, 59, 59, 999999, tzinfo=UTC)
        just_after = datetime(2020, 1, 15, 14, 0, 0, tzinfo=UTC)

        before_rows = listings_as_of(
            fixture_store, just_before, security_ids=["SEC_STATIC_PRE2019"]
        )
        assert before_rows.height == 0
        after_rows = listings_as_of(fixture_store, just_after, security_ids=["SEC_STATIC_PRE2019"])
        listing = _one(after_rows, security_id="SEC_STATIC_PRE2019")
        assert listing["ticker"] == "PRE9"
        assert listing["valid_from"] == date(2017, 1, 3)

    def test_bare_date_raises(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(TypeError):
            listings_as_of(fixture_store, date(2020, 1, 15))  # type: ignore[arg-type]
