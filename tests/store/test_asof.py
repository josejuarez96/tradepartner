"""Tests for tradepartner.store.asof (T6).

Covers the "Timing and store" acceptance criteria in
docs/specs/data-foundation.md that name `prices_as_of`,
`adjusted_prices_as_of` and `facts_as_of` directly, plus `listings_as_of`'s
issue #35 behavior (owner decision: strict `known_at`): latest revision as
of T, bar revision, a split known
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
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import duckdb
import polars as pl
import pytest

from tradepartner.calendar import next_session
from tradepartner.config import AdjustConfig, Settings
from tradepartner.store import schema
from tradepartner.store.asof import (
    adjusted_prices_as_of,
    dropped_dividends_as_of,
    facts_as_of,
    filing_events_as_of,
    listings_as_of,
    prices_as_of,
    statement_facts_as_of,
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

    def test_dividend_at_or_above_prior_close_is_dropped_not_raised(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Finding 2, revised by #841. A dividend amount >= the prior close
        # would make `1 - amount / prior_close` zero or negative, and
        # DuckDB's LN() (the cumulative-factor window) raises on that. It
        # is bad source data on one name (Alpaca's CG 2017-09-13 $25 on a
        # $22 stock), so it is left unapplied and reported by
        # `dropped_dividends_as_of` rather than failing every read.
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
        adjusted = adjusted_prices_as_of(synthetic_store, t, include_dividends=True)
        assert _one(adjusted, session=date(2021, 1, 4))["close"] == pytest.approx(10.0)
        row = _one(dropped_dividends_as_of(synthetic_store, t), security_id="SEC_BAD_DIV")
        assert row["reason"] == "implausible_amount"

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

    def test_immediately_prior_session_is_a_gap_of_one(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        sid = "SEC_GAP_ONE"
        _bar(
            synthetic_store,
            sid,
            date(2021, 1, 8),
            50.0,
            known_at=datetime(2021, 1, 8, 21, tzinfo=UTC),
        )
        _action(
            synthetic_store,
            sid,
            "dividend",
            date(2021, 1, 11),
            5.0,
            known_at=datetime(2021, 1, 8, 22, tzinfo=UTC),
        )
        adjusted = adjusted_prices_as_of(
            synthetic_store, self.T, include_dividends=True, settings=_gap_settings(1)
        )
        assert _one(adjusted, session=date(2021, 1, 8))["close"] == pytest.approx(45.0)

    def test_prior_bar_before_calendar_start_is_dropped_not_undercounted(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Quant-auditor on PR #79: the calendar starts 1990-01-02, so a
        # 1985 bar has no session index. It must not count as if it were
        # on the first calendar session (gap 2 here).
        sid = "SEC_PRE_CALENDAR"
        _bar(
            synthetic_store,
            sid,
            date(1985, 6, 3),
            50.0,
            known_at=datetime(1985, 6, 3, 21, tzinfo=UTC),
        )
        _action(
            synthetic_store,
            sid,
            "dividend",
            date(1990, 1, 4),
            1.0,
            known_at=datetime(1990, 1, 3, 21, tzinfo=UTC),
        )
        t = datetime(1990, 2, 1, tzinfo=UTC)
        adjusted = adjusted_prices_as_of(synthetic_store, t, include_dividends=True)
        assert _one(adjusted, session=date(1985, 6, 3))["close"] == pytest.approx(50.0)
        row = _one(dropped_dividends_as_of(synthetic_store, t), security_id=sid)
        assert row["reason"] == "outside_calendar_range"
        assert row["gap_sessions"] is None

    def test_ex_date_before_calendar_start_is_outside_range(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        sid = "SEC_PRE_CALENDAR_EX"
        _bar(
            synthetic_store,
            sid,
            date(1989, 6, 2),
            50.0,
            known_at=datetime(1989, 6, 2, 21, tzinfo=UTC),
        )
        _action(
            synthetic_store,
            sid,
            "dividend",
            date(1989, 6, 5),
            1.0,
            known_at=datetime(1989, 6, 2, 22, tzinfo=UTC),
        )
        row = _one(dropped_dividends_as_of(synthetic_store, self.T), security_id=sid)
        assert row["reason"] == "outside_calendar_range"

    def test_ex_date_after_calendar_end_is_outside_range(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # The calendar ends 2035-12-31; a 2036 ex-date must not be counted
        # against the last configured session.
        sid = "SEC_POST_CALENDAR"
        _bar(
            synthetic_store,
            sid,
            date(2035, 12, 31),
            50.0,
            known_at=datetime(2035, 12, 31, 22, tzinfo=UTC),
        )
        _action(
            synthetic_store,
            sid,
            "dividend",
            date(2036, 1, 2),
            1.0,
            known_at=datetime(2035, 12, 31, 23, tzinfo=UTC),
        )
        t = datetime(2036, 2, 1, tzinfo=UTC)
        row = _one(dropped_dividends_as_of(synthetic_store, t), security_id=sid)
        assert row["reason"] == "outside_calendar_range"

    def test_dropped_dividends_raises_on_negative_amount_like_adjusted(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Quant-auditor NIT: a negative amount is bad data, not a drop;
        # both functions must agree and raise.
        self._gap_scenario(synthetic_store, amount=-1.0)
        with pytest.raises(ValueError, match="SEC_GAP"):
            dropped_dividends_as_of(synthetic_store, self.T, settings=_gap_settings(4))

    def test_dropped_dividends_bare_date_raises(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        with pytest.raises(TypeError):
            dropped_dividends_as_of(synthetic_store, date(2021, 3, 1))  # type: ignore[arg-type]


#: A bar's `known_at` time of day: 21:00 UTC, after the 16:00 New York close.
_CLOSE_UTC = time(21, tzinfo=UTC)


def _amount_settings(max_share: float) -> Settings:
    """Settings with `adjust.max_dividend_to_prior_close = max_share`, no `.env`."""
    return Settings(_env_file=None, adjust=AdjustConfig(max_dividend_to_prior_close=max_share))


class TestImplausibleDividendAmount:
    """Issue #841: a dividend whose amount is at or above
    `adjust.max_dividend_to_prior_close` times its prior close is bad
    source data on one name. It is left unapplied (NULL factor) and
    reported by `dropped_dividends_as_of` as `implausible_amount`, so one
    such row no longer fails every adjusted read that includes the name."""

    T = datetime(2017, 10, 31, 21, tzinfo=UTC)

    def _carlyle(self, conn: duckdb.DuckDBPyConnection, *, amount: float = 25.0) -> None:
        # The #841 shape: an ordinary dividend, then a $25 "dividend" on
        # a $22.35 close with no drop on the ex-date.
        sid = "SEC_CG"
        for session, close in (
            (date(2017, 8, 9), 20.00),
            (date(2017, 8, 10), 19.58),
            (date(2017, 9, 12), 22.35),
            (date(2017, 9, 13), 22.80),
        ):
            _bar(conn, sid, session, close, known_at=datetime.combine(session, _CLOSE_UTC))
        _action(
            conn,
            sid,
            "dividend",
            date(2017, 8, 10),
            0.42,
            known_at=datetime(2017, 8, 9, 20, tzinfo=UTC),
        )
        _action(
            conn,
            sid,
            "dividend",
            date(2017, 9, 13),
            amount,
            known_at=datetime(2017, 9, 12, 20, tzinfo=UTC),
        )

    def test_the_841_dividend_is_dropped_and_the_good_one_still_applies(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        self._carlyle(synthetic_store)
        adjusted = adjusted_prices_as_of(synthetic_store, self.T, include_dividends=True)
        # Only the 0.42 dividend adjusts the 08-09 bar; the $25 row is ignored.
        assert _one(adjusted, session=date(2017, 8, 9))["close"] == pytest.approx(
            20.0 * (1 - 0.42 / 20.0)
        )
        assert _one(adjusted, session=date(2017, 9, 12))["close"] == pytest.approx(22.35)
        dropped = dropped_dividends_as_of(synthetic_store, self.T)
        row = _one(dropped, security_id="SEC_CG")
        assert row["ex_date"] == date(2017, 9, 13)
        assert row["ratio_or_amount"] == pytest.approx(25.0)
        assert row["prior_session"] == date(2017, 9, 12)
        assert row["gap_sessions"] == 1
        assert row["reason"] == "implausible_amount"

    def test_not_a_drop_before_it_is_known(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        self._carlyle(synthetic_store)
        before_known = datetime(2017, 9, 12, 19, tzinfo=UTC)
        assert dropped_dividends_as_of(synthetic_store, before_known).height == 0

    def test_bound_comes_from_config(self, synthetic_store: duckdb.DuckDBPyConnection) -> None:
        # 6.0 on a 22.35 close is 27% of it: applied at the default bound
        # (1.0), dropped at 0.25, applied again at 0.30.
        self._carlyle(synthetic_store, amount=6.0)
        bar = date(2017, 9, 12)
        applied = 22.35 * (1 - 6.0 / 22.35)
        for share, expected, n_dropped in (
            (None, applied, 0),
            (0.25, 22.35, 1),
            (0.30, applied, 0),
        ):
            cfg = None if share is None else _amount_settings(share)
            adjusted = adjusted_prices_as_of(
                synthetic_store, self.T, include_dividends=True, settings=cfg
            )
            assert _one(adjusted, session=bar)["close"] == pytest.approx(expected), share
            dropped = dropped_dividends_as_of(synthetic_store, self.T, settings=cfg)
            assert dropped.height == n_dropped, share

    def test_stale_prior_bar_keeps_its_own_reason(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # A gap drop is reported as such even when the amount is also too big.
        sid = "SEC_BOTH"
        _bar(
            synthetic_store,
            sid,
            date(2021, 1, 4),
            5.0,
            known_at=datetime(2021, 1, 4, 21, tzinfo=UTC),
        )
        _action(
            synthetic_store,
            sid,
            "dividend",
            date(2021, 1, 11),
            9.0,
            known_at=datetime(2021, 1, 8, 21, tzinfo=UTC),
        )
        t = datetime(2021, 3, 1, tzinfo=UTC)
        row = _one(
            dropped_dividends_as_of(synthetic_store, t, settings=_gap_settings(4)), security_id=sid
        )
        assert row["reason"] == "stale_prior_bar"

    def test_a_prior_bar_revision_known_after_t_does_not_change_the_drop_at_t(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        # The 09-12 close is revised to 30.00 after T: at T the $25 row is
        # still sized against 22.35 and dropped; once the revision is known
        # it is under the close and applies.
        self._carlyle(synthetic_store)
        _bar(
            synthetic_store,
            "SEC_CG",
            date(2017, 9, 12),
            30.0,
            known_at=datetime(2017, 11, 15, 21, tzinfo=UTC),
        )
        assert dropped_dividends_as_of(synthetic_store, self.T).height == 1
        later = datetime(2017, 11, 30, 21, tzinfo=UTC)
        assert dropped_dividends_as_of(synthetic_store, later).height == 0
        adjusted = adjusted_prices_as_of(synthetic_store, later, include_dividends=True)
        assert _one(adjusted, session=date(2017, 9, 12))["close"] == pytest.approx(5.0)

    @pytest.mark.parametrize("amount", [float("nan"), float("inf")])
    def test_non_finite_amount_still_raises(
        self, synthetic_store: duckdb.DuckDBPyConnection, amount: float
    ) -> None:
        self._carlyle(synthetic_store, amount=amount)
        with pytest.raises(ValueError, match="SEC_CG"):
            adjusted_prices_as_of(synthetic_store, self.T, include_dividends=True)
        with pytest.raises(ValueError, match="SEC_CG"):
            dropped_dividends_as_of(synthetic_store, self.T)

    def test_zero_prior_close_still_raises(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        self._carlyle(synthetic_store)
        _bar(
            synthetic_store,
            "SEC_CG",
            date(2017, 9, 12),
            0.0,
            known_at=datetime(2017, 9, 12, 22, tzinfo=UTC),
        )
        with pytest.raises(ValueError, match="SEC_CG"):
            adjusted_prices_as_of(synthetic_store, self.T, include_dividends=True)

    def test_negative_amount_still_raises(self, synthetic_store: duckdb.DuckDBPyConnection) -> None:
        self._carlyle(synthetic_store, amount=-1.0)
        with pytest.raises(ValueError, match="SEC_CG"):
            adjusted_prices_as_of(synthetic_store, self.T, include_dividends=True)

    @pytest.mark.parametrize("share", [0.0, -0.1, 1.01])
    def test_bound_outside_zero_one_is_refused(self, share: float) -> None:
        with pytest.raises(ValueError):
            AdjustConfig(max_dividend_to_prior_close=share)


def _fact(
    conn: duckdb.DuckDBPyConnection,
    as_of: date,
    value: float,
    accession: str | None,
    ingested_at: datetime,
    *,
    known_at: datetime = datetime(2021, 1, 20, 21, 0, tzinfo=UTC),
) -> None:
    """One `shares_outstanding` fact row for SEC_FACTS_RESTATED (T11e reader tests)."""
    insert_row(
        conn,
        "facts",
        {
            "security_id": "SEC_FACTS_RESTATED",
            "fact_name": "shares_outstanding",
            "class_member": "",
            "as_of_date": as_of,
            "value": value,
            "filing_accession": accession,
            "known_at": known_at,
            "ingested_at": ingested_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )


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

    def test_one_accession_and_class_resolves_to_its_latest_ingested_row(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        """T11e reader rule: a fact re-dated for the same `(security, fact name,
        class, filing accession)` (FSN's month end first, the company-facts
        date on a later run) is served once, under the latest-ingested date;
        rows with no accession are untouched."""
        known_at = datetime(2021, 1, 20, 21, 0, tzinfo=UTC)
        base = {
            "security_id": "SEC_FACTS_RESTATED",
            "fact_name": "shares_outstanding",
            "class_member": "",
            "known_at": known_at,
            "source": "edgar",
            "provenance": "filing",
        }
        insert_row(
            fixture_store,
            "facts",
            {
                **base,
                "as_of_date": date(2021, 1, 31),
                "value": 500.0,
                "filing_accession": "0000000001-21-000001",
                "ingested_at": datetime(2021, 2, 1, tzinfo=UTC),
            },
        )
        insert_row(
            fixture_store,
            "facts",
            {
                **base,
                "as_of_date": date(2021, 1, 15),
                "value": 500.0,
                "filing_accession": "0000000001-21-000001",
                "ingested_at": datetime(2021, 3, 1, tzinfo=UTC),
            },
        )
        insert_row(
            fixture_store,
            "facts",
            {
                **base,
                "as_of_date": date(2021, 1, 10),
                "value": 400.0,
                "filing_accession": None,
                "ingested_at": datetime(2021, 2, 1, tzinfo=UTC),
            },
        )
        rows = facts_as_of(
            fixture_store, datetime(2021, 6, 1, tzinfo=UTC), security_ids=["SEC_FACTS_RESTATED"]
        )
        by_accession = rows.filter(pl.col("filing_accession") == "0000000001-21-000001")
        assert by_accession.height == 1
        assert by_accession.row(0, named=True)["as_of_date"] == date(2021, 1, 15)
        assert _one(rows, as_of_date=date(2021, 1, 10))["value"] == pytest.approx(400.0)

    def test_two_dates_of_one_accession_from_one_ingest_are_both_served(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        """A filing may carry two dates (same-source records keep their own
        keys): both rows of the latest ingest are served, never one picked
        arbitrarily."""
        ingested = datetime(2021, 2, 1, tzinfo=UTC)
        for as_of, value in ((date(2021, 1, 31), 500.0), (date(2021, 1, 15), 510.0)):
            _fact(fixture_store, as_of, value, "0000000001-21-000002", ingested)
        rows = facts_as_of(
            fixture_store, datetime(2021, 6, 1, tzinfo=UTC), security_ids=["SEC_FACTS_RESTATED"]
        )
        both = rows.filter(pl.col("filing_accession") == "0000000001-21-000002")
        assert sorted(both["as_of_date"].to_list()) == [date(2021, 1, 15), date(2021, 1, 31)]

    def test_a_stale_re_dated_row_never_shadows_another_filings_row(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        """A2's genuine 10-31 row (known 11-05) and A1's stale FSN row on the
        same date (known 11-20, later re-dated to 10-17): after the re-date,
        A1 serves 10-17 and A2's 10-31 row is still served, because the
        accession rule runs before the per-date collapse."""
        _fact(
            fixture_store,
            date(2025, 10, 31),
            200.0,
            "0000000002-25-000001",
            datetime(2025, 11, 5, tzinfo=UTC),
            known_at=datetime(2025, 11, 5, tzinfo=UTC),
        )
        a1_known = datetime(2025, 11, 20, tzinfo=UTC)
        _fact(
            fixture_store,
            date(2025, 10, 31),
            100.0,
            "0000000001-25-000001",
            a1_known,
            known_at=a1_known,
        )
        _fact(
            fixture_store,
            date(2025, 10, 17),
            100.0,
            "0000000001-25-000001",
            datetime(2026, 1, 10, tzinfo=UTC),
            known_at=a1_known,
        )
        early = facts_as_of(
            fixture_store, datetime(2025, 11, 10, tzinfo=UTC), security_ids=["SEC_FACTS_RESTATED"]
        )
        assert _one(early, as_of_date=date(2025, 10, 31))["value"] == pytest.approx(200.0)
        late = facts_as_of(
            fixture_store, datetime(2026, 3, 1, tzinfo=UTC), security_ids=["SEC_FACTS_RESTATED"]
        )
        served = {
            (r["as_of_date"], r["filing_accession"]): r["value"]
            for r in late.iter_rows(named=True)
            if r["as_of_date"] >= date(2025, 10, 1)
        }
        assert served == {
            (date(2025, 10, 31), "0000000002-25-000001"): 200.0,
            (date(2025, 10, 17), "0000000001-25-000001"): 100.0,
        }

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

    def test_snapshot_static_listing_invisible_inside_its_valid_range_before_known_at(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        # Issue #35, owner decision (b): no snapshot_static exemption. At a
        # 2018 T, PRE9's listing is already "valid" (valid_from 2017-01-03)
        # and its bars are known, but the listing row itself is not.
        t = datetime(2018, 6, 29, 21, 0, tzinfo=UTC)
        assert listings_as_of(fixture_store, t, security_ids=["SEC_STATIC_PRE2019"]).height == 0
        bars = prices_as_of(fixture_store, t, security_ids=["SEC_STATIC_PRE2019"])
        assert bars.height > 0

    def test_bare_date_raises(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(TypeError):
            listings_as_of(fixture_store, date(2020, 1, 15))  # type: ignore[arg-type]


def _security(
    conn: duckdb.DuckDBPyConnection,
    security_id: str,
    cik: str,
    *,
    known_at: datetime,
) -> None:
    """One `securities` row (T76b synthetic-store tests)."""
    insert_row(
        conn,
        "securities",
        {
            "security_id": security_id,
            "cik": cik,
            "name": f"{security_id} Inc",
            "benchmark": False,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "test",
            "provenance": "filing",
        },
    )


def _statement_fact(
    conn: duckdb.DuckDBPyConnection,
    cik: str,
    fact_name: str,
    period_end: date,
    value: float,
    *,
    known_at: datetime,
    period_start: date | None = None,
    accession: str = "0000000001-21-000001",
) -> None:
    """One `statement_facts` row (T76b synthetic-store tests): an instant
    fact (`period_start=None`, `period_days=0`) unless `period_start` is
    given."""
    insert_row(
        conn,
        "statement_facts",
        {
            "cik": cik,
            "fact_name": fact_name,
            "xbrl_tag": f"us-gaap:{fact_name}",
            "period_start": period_start,
            "period_end": period_end,
            "period_days": 0 if period_start is None else (period_end - period_start).days,
            "value": value,
            "unit": "USD",
            "form": "10-K",
            "filing_accession": accession,
            "basis": "reported",
            "comparative": False,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "test",
            "provenance": "filing",
        },
    )


class TestStatementFactsAsOf:
    """T76b (#660): `statement_facts_as_of` joins through
    `store.master.securities_as_of(t)` on `cik`. The fixture's
    CIK0001000006 ("Dual Class Holdings") carries SEC_DUAL_A, SEC_DUAL_B
    and SEC_DUAL_PFD, all known at 2016-12-23, and a `revenue` statement
    fact known at 2020-02-03T20:30:00+00:00 -- the multi-class case; the
    other cases use a `synthetic_store` for exact control.
    """

    _DUAL_CIK = "CIK0001000006"
    _DUAL_KNOWN_AT = datetime(2020, 2, 3, 20, 30, 0, tzinfo=UTC)

    def test_invisible_before_known_at_visible_after(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        just_before = self._DUAL_KNOWN_AT - timedelta(microseconds=1)
        just_after = self._DUAL_KNOWN_AT
        before = statement_facts_as_of(fixture_store, just_before, security_ids=["SEC_DUAL_A"])
        assert before.filter(pl.col("fact_name") == "revenue").height == 0
        after = statement_facts_as_of(fixture_store, just_after, security_ids=["SEC_DUAL_A"])
        assert after.filter(pl.col("fact_name") == "revenue").height == 1

    def test_dual_class_cik_rows_appear_once_per_class(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        rows = statement_facts_as_of(fixture_store, self._DUAL_KNOWN_AT)
        revenue = rows.filter(
            (pl.col("cik") == self._DUAL_CIK) & (pl.col("fact_name") == "revenue")
        )
        assert sorted(revenue["security_id"]) == ["SEC_DUAL_A", "SEC_DUAL_B", "SEC_DUAL_PFD"]
        assert revenue["value"].n_unique() == 1  # same statement row, repeated per class

    def test_security_ids_restricts_to_the_requested_class(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        rows = statement_facts_as_of(
            fixture_store, self._DUAL_KNOWN_AT, security_ids=["SEC_DUAL_B"]
        )
        assert rows["security_id"].unique().to_list() == ["SEC_DUAL_B"]

    def test_cik_with_no_securities_row_returns_nothing(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        known_at = datetime(2021, 6, 1, 20, 0, tzinfo=UTC)
        _statement_fact(
            synthetic_store, "CIK0009999999", "revenue", date(2020, 12, 31), 1.0, known_at=known_at
        )
        rows = statement_facts_as_of(synthetic_store, known_at + timedelta(days=1))
        assert rows.height == 0

    def test_cik_whose_security_is_not_yet_known_at_t_returns_nothing(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        """The statement fact is known at T, but the master's own
        `securities` row for its CIK is not known until later -- the join
        through `securities_as_of(t)` must still hide it (spec: a CIK with
        no `securities` row at T is invisible at T)."""
        fact_known = datetime(2021, 6, 1, 20, 0, tzinfo=UTC)
        security_known = datetime(2021, 6, 10, 20, 0, tzinfo=UTC)
        _statement_fact(
            synthetic_store,
            "CIK0001234567",
            "revenue",
            date(2020, 12, 31),
            1.0,
            known_at=fact_known,
        )
        _security(synthetic_store, "SEC_LATE_MASTER", "CIK0001234567", known_at=security_known)

        between = statement_facts_as_of(synthetic_store, fact_known + timedelta(hours=1))
        assert between.height == 0
        after_both = statement_facts_as_of(synthetic_store, security_known)
        assert after_both.height == 1
        assert after_both.row(0, named=True)["security_id"] == "SEC_LATE_MASTER"

    def test_no_revision_first_vintage_only(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        """`statement_facts` holds no revision (its `UNIQUE (cik, fact_name,
        period_end, period_days)` excludes `known_at`), so this is a plain
        `known_at <= t` filter, never a latest-revision query: a second row
        for a *different* period on the same CIK simply adds a row, never
        replaces the first, and the schema itself -- not this function --
        is what stops a second row for the *same* key (quant-auditor on
        #904: the earlier version of this test never inserted a colliding
        row, so it tested nothing about that)."""
        security_known = datetime(2021, 1, 1, tzinfo=UTC)
        _security(synthetic_store, "SEC_FIRST_VINTAGE", "CIK0001234568", known_at=security_known)
        first_known = datetime(2021, 6, 1, 20, 0, tzinfo=UTC)
        _statement_fact(
            synthetic_store,
            "CIK0001234568",
            "revenue",
            date(2020, 12, 31),
            100.0,
            known_at=first_known,
        )
        # A different period adds a second row.
        _statement_fact(
            synthetic_store,
            "CIK0001234568",
            "revenue",
            date(2021, 3, 31),
            25.0,
            known_at=first_known + timedelta(days=90),
            period_start=date(2021, 1, 1),
        )
        rows = statement_facts_as_of(synthetic_store, first_known + timedelta(days=200))
        assert sorted(rows["period_end"]) == [date(2020, 12, 31), date(2021, 3, 31)]

        # A later row for the *same* key (even a different value, even a
        # later known_at) is not a revision -- the UNIQUE constraint itself
        # raises, which is the schema enforcing "no revision" at the DDL
        # level rather than this function silently picking one.
        with pytest.raises(duckdb.ConstraintException):
            _statement_fact(
                synthetic_store,
                "CIK0001234568",
                "revenue",
                date(2020, 12, 31),
                999.0,
                known_at=first_known + timedelta(days=30),
            )
        rows = statement_facts_as_of(synthetic_store, first_known + timedelta(days=200))
        assert rows.filter(pl.col("period_end") == date(2020, 12, 31)).row(0, named=True)[
            "value"
        ] == pytest.approx(100.0)

    def test_empty_security_ids_returns_empty_frame_with_schema(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        rows = statement_facts_as_of(fixture_store, self._DUAL_KNOWN_AT, security_ids=[])
        assert rows.height == 0
        assert "security_id" in rows.columns
        assert "fact_name" in rows.columns

    def test_bare_date_raises(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(TypeError):
            statement_facts_as_of(fixture_store, date(2020, 1, 1))  # type: ignore[arg-type]

    def test_naive_datetime_raises(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(ValueError):
            statement_facts_as_of(fixture_store, datetime(2020, 1, 1, 21, 0, 0))  # noqa: DTZ001

    def test_view_is_dropped_after_the_call_even_on_a_read_only_connection(
        self, tmp_path: Path
    ) -> None:
        path = str(tmp_path / "statement_facts_ro.duckdb")
        conn = duckdb.connect(path)
        try:
            configure_connection(conn)
            schema.init_schema(conn)
            known_at = datetime(2021, 1, 1, tzinfo=UTC)
            _security(conn, "SEC_RO_STMT", "CIK0001234569", known_at=known_at)
            _statement_fact(
                conn, "CIK0001234569", "revenue", date(2020, 12, 31), 1.0, known_at=known_at
            )
        finally:
            conn.close()

        ro = duckdb.connect(path, read_only=True)
        try:
            configure_connection(ro)
            rows = statement_facts_as_of(ro, known_at + timedelta(days=1))
            assert rows.height == 1
            with pytest.raises(duckdb.CatalogException):
                ro.execute("SELECT * FROM _asof_statement_securities")
        finally:
            ro.close()


def test_dividend_queries_work_on_a_read_only_connection_and_drop_their_view(
    tmp_path: Path,
) -> None:
    """Safety-reviewer NIT on PR #79: the sessions view is registered on the
    caller's connection, which may be read-only, and must be gone after
    every call, including one that raises."""
    path = str(tmp_path / "store.duckdb")
    conn = duckdb.connect(path)
    configure_connection(conn)
    schema.init_schema(conn)
    known = datetime(2021, 1, 8, 22, tzinfo=UTC)
    _bar(conn, "SEC_RO", date(2021, 1, 8), 50.0, known_at=known)
    _action(conn, "SEC_RO", "dividend", date(2021, 1, 11), 5.0, known_at=known)
    _bar(conn, "SEC_RO_BAD", date(2021, 1, 8), 50.0, known_at=known)
    conn.close()

    t = datetime(2021, 3, 1, tzinfo=UTC)
    ro = duckdb.connect(path, read_only=True)
    try:
        configure_connection(ro)
        adjusted = adjusted_prices_as_of(ro, t, include_dividends=True)
        assert _one(adjusted, session=date(2021, 1, 8), security_id="SEC_RO")[
            "close"
        ] == pytest.approx(45.0)
        assert dropped_dividends_as_of(ro, t).height == 0
        with pytest.raises(duckdb.CatalogException):
            ro.execute("SELECT * FROM _asof_xnys_sessions")
    finally:
        ro.close()

    rw = duckdb.connect(path)
    try:
        configure_connection(rw)
        _action(rw, "SEC_RO_BAD", "dividend", date(2021, 1, 11), -1.0, known_at=known)
        with pytest.raises(ValueError, match="SEC_RO_BAD"):
            adjusted_prices_as_of(rw, t, include_dividends=True)
        with pytest.raises(duckdb.CatalogException):
            rw.execute("SELECT * FROM _asof_xnys_sessions")
    finally:
        rw.close()


#: Two ex-dates, each carrying a split and a dividend (#1099, #1119 item 3). These
#: amounts made the pre-#1119 read differ in the last bit by insertion order and
#: between the table and a truncated view.
_SAME_DAY_ACTIONS = [
    ("split", date(2021, 1, 11), 0.409),
    ("dividend", date(2021, 1, 11), 2.113),
    ("split", date(2021, 1, 19), 4.919),
    ("dividend", date(2021, 1, 19), 0.301),
]


def _same_day_store(actions: list[tuple[str, date, float]]) -> duckdb.DuckDBPyConnection:
    # Several threads, as production reads run: under the test suite's
    # threads=1 (#953) the scan order never varies, so the old sum never showed it.
    conn = duckdb.connect(":memory:", config={"threads": 4})
    configure_connection(conn)
    schema.init_schema(conn)
    known = datetime(2021, 1, 4, 21, 0, tzinfo=UTC)
    closes = [10.3, 10.7, 11.1, 9.9, 33.7, 31.9, 32.3, 33.1, 47.3, 46.9]
    day = date(2021, 1, 4)
    for close in closes:
        _bar(conn, "SEC_X", day, close, known_at=known)
        day = next_session(day)
    for kind, ex_date, amount in actions:
        _action(conn, "SEC_X", kind, ex_date, amount, known_at=known)
    return conn


def test_a_split_and_dividend_on_one_ex_date_read_bit_equal_full_and_cut() -> None:
    """One ex-date's factors sum in a fixed order (#1099): the full table and a
    truncated view yield bit-identical adjusted frames, whatever the actions'
    insertion order, and the factor is the split's times the dividend's."""
    from lookahead.harness import TruncatedStore

    t = datetime(2021, 1, 29, 21, 0, tzinfo=UTC)
    forward = _same_day_store(_SAME_DAY_ACTIONS)
    backward = _same_day_store(list(reversed(_SAME_DAY_ACTIONS)))
    try:
        full = adjusted_prices_as_of(forward, t, include_dividends=True)
        # The old scan-order sum differed on about 3 reads in 5; repeat to catch it.
        for _ in range(10):
            for store in (forward, backward):
                assert full.equals(adjusted_prices_as_of(store, t, include_dividends=True))
                cut = TruncatedStore(store)
                try:
                    cut_frame = adjusted_prices_as_of(cut.at(t), t, include_dividends=True)
                    assert full.equals(cut_frame)
                finally:
                    cut.close()
        raw = prices_as_of(forward, t)
        prior_1 = _one(raw, session=date(2021, 1, 8))["close"]
        prior_2 = _one(raw, session=date(2021, 1, 15))["close"]
        assert isinstance(prior_1, float) and isinstance(prior_2, float)
        factor = (1 - 2.113 / prior_1) / 0.409 * (1 - 0.301 / prior_2) / 4.919
        assert _one(full, session=date(2021, 1, 8))["close"] == pytest.approx(prior_1 * factor)
    finally:
        forward.close()
        backward.close()


def _filing_event(
    conn: duckdb.DuckDBPyConnection,
    cik: str,
    accession: str,
    items: str,
    *,
    accepted_at: datetime,
    form: str = "8-K",
) -> None:
    """One `filing_events` row (T164d synthetic-store tests): `known_at =
    accepted_at`, as the table's `CHECK` requires."""
    insert_row(
        conn,
        "filing_events",
        {
            "cik": cik,
            "accession": accession,
            "form": form,
            "items": items,
            "accepted_at": accepted_at,
            "known_at": accepted_at,
            "ingested_at": accepted_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )


class TestFilingEventsAsOf:
    """T164d (#1358): `filing_events_as_of(t, forms, items)` joins through
    `securities_as_of(t)` on `cik`, one row per `(security_id, accession)`,
    filters `form` exactly and `items` by whole code. The fixture's three rows
    (CIK0001000011, `SEC_SPLIT_PLAIN`) cover the boundary; the filters and the
    join use a `synthetic_store`. The look-ahead cases are in
    `tests/lookahead/test_filing_events.py`."""

    _TEETH_KNOWN_AT = datetime(2020, 4, 30, 20, 5, tzinfo=UTC)
    _AFTER_ALL = datetime(2020, 6, 1, tzinfo=UTC)
    _CIK = "CIK0007000001"
    _SECURITY_KNOWN = datetime(2019, 1, 2, tzinfo=UTC)

    def _issuer(self, conn: duckdb.DuckDBPyConnection) -> None:
        _security(conn, "SEC_EVENTS", self._CIK, known_at=self._SECURITY_KNOWN)

    def test_invisible_before_known_at_visible_at_it(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        before = filing_events_as_of(
            fixture_store, self._TEETH_KNOWN_AT - timedelta(microseconds=1)
        )
        assert before.height == 0
        at = filing_events_as_of(fixture_store, self._TEETH_KNOWN_AT)
        assert at["accession"].to_list() == ["0001000011-20-000101"]
        assert at["security_id"].to_list() == ["SEC_SPLIT_PLAIN"]

    def test_columns_are_security_id_then_the_table_in_schema_order(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        rows = filing_events_as_of(fixture_store, self._AFTER_ALL)
        table_columns = [r[0] for r in fixture_store.execute("DESCRIBE filing_events").fetchall()]
        assert rows.columns == ["security_id", *table_columns]

    def test_sorted_by_known_at_then_accession_then_security_id(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        self._issuer(synthetic_store)
        _security(synthetic_store, "SEC_EVENTS_B", self._CIK, known_at=self._SECURITY_KNOWN)
        late = datetime(2020, 3, 2, 21, 0, tzinfo=UTC)
        early = datetime(2020, 3, 1, 21, 0, tzinfo=UTC)
        _filing_event(synthetic_store, self._CIK, "A-3", "8.01", accepted_at=late)
        _filing_event(synthetic_store, self._CIK, "A-2", "8.01", accepted_at=late)
        _filing_event(synthetic_store, self._CIK, "A-9", "8.01", accepted_at=early)
        rows = filing_events_as_of(synthetic_store, self._AFTER_ALL)
        assert list(zip(rows["accession"], rows["security_id"], strict=True)) == [
            ("A-9", "SEC_EVENTS"),
            ("A-9", "SEC_EVENTS_B"),
            ("A-2", "SEC_EVENTS"),
            ("A-2", "SEC_EVENTS_B"),
            ("A-3", "SEC_EVENTS"),
            ("A-3", "SEC_EVENTS_B"),
        ]

    def test_dual_class_issuer_event_appears_once_per_class(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        self._issuer(synthetic_store)
        class_b_known = datetime(2020, 3, 10, tzinfo=UTC)
        _security(synthetic_store, "SEC_EVENTS_B", self._CIK, known_at=class_b_known)
        accepted = datetime(2020, 3, 1, 21, 0, tzinfo=UTC)
        _filing_event(synthetic_store, self._CIK, "A-1", "2.02,9.01", accepted_at=accepted)
        before_b = filing_events_as_of(synthetic_store, class_b_known - timedelta(seconds=1))
        assert before_b["security_id"].to_list() == ["SEC_EVENTS"]
        after_b = filing_events_as_of(synthetic_store, class_b_known)
        assert sorted(after_b["security_id"]) == ["SEC_EVENTS", "SEC_EVENTS_B"]
        assert after_b["accession"].unique().to_list() == ["A-1"]

    def test_cik_with_no_securities_row_at_t_is_invisible(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        accepted = datetime(2020, 3, 1, 21, 0, tzinfo=UTC)
        _filing_event(synthetic_store, self._CIK, "A-1", "2.02", accepted_at=accepted)
        assert filing_events_as_of(synthetic_store, self._AFTER_ALL).height == 0
        security_known = datetime(2020, 4, 1, tzinfo=UTC)
        _security(synthetic_store, "SEC_LATE", self._CIK, known_at=security_known)
        assert (
            filing_events_as_of(synthetic_store, security_known - timedelta(seconds=1)).height == 0
        )
        assert filing_events_as_of(synthetic_store, security_known).height == 1

    def test_forms_filter_is_exact(self, synthetic_store: duckdb.DuckDBPyConnection) -> None:
        self._issuer(synthetic_store)
        accepted = datetime(2020, 3, 1, 21, 0, tzinfo=UTC)
        _filing_event(synthetic_store, self._CIK, "A-1", "2.02", accepted_at=accepted)
        _filing_event(synthetic_store, self._CIK, "A-2", "2.02", accepted_at=accepted, form="8-K/A")

        def accessions(forms: list[str] | None) -> list[str]:
            frame = filing_events_as_of(synthetic_store, self._AFTER_ALL, forms=forms)
            return sorted(frame["accession"])

        assert accessions(["8-K"]) == ["A-1"]
        assert accessions(["8-K/A"]) == ["A-2"]
        assert accessions(["8-K", "8-K/A"]) == ["A-1", "A-2"]
        assert accessions(None) == ["A-1", "A-2"]
        assert accessions([]) == []
        assert accessions(["8-k"]) == []

    def test_items_filter_matches_whole_codes_only(
        self, synthetic_store: duckdb.DuckDBPyConnection
    ) -> None:
        self._issuer(synthetic_store)
        accepted = datetime(2020, 3, 1, 21, 0, tzinfo=UTC)
        _filing_event(synthetic_store, self._CIK, "A-1", "2.02,9.01", accepted_at=accepted)
        _filing_event(synthetic_store, self._CIK, "A-2", "12.02,2.021", accepted_at=accepted)
        _filing_event(synthetic_store, self._CIK, "A-3", "5.02", accepted_at=accepted)
        _filing_event(synthetic_store, self._CIK, "A-4", "", accepted_at=accepted)
        _filing_event(synthetic_store, self._CIK, "A-5", "9.01, 2.02", accepted_at=accepted)

        def accessions(items: list[str] | None) -> list[str]:
            frame = filing_events_as_of(synthetic_store, self._AFTER_ALL, items=items)
            return sorted(frame["accession"])

        assert accessions(["2.02"]) == ["A-1", "A-5"]
        assert accessions(["5.02", "9.01"]) == ["A-1", "A-3", "A-5"]
        assert accessions(["2.0"]) == []
        assert accessions(None) == ["A-1", "A-2", "A-3", "A-4", "A-5"]
        assert accessions([]) == []

    def test_forms_and_items_combine(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        rows = filing_events_as_of(fixture_store, self._AFTER_ALL, forms=["8-K"], items=["2.02"])
        assert rows["accession"].to_list() == ["0001000011-20-000101", "0001000011-20-000102"]
        assert filing_events_as_of(
            fixture_store, self._AFTER_ALL, forms=["8-K/A"], items=["2.02"]
        ).is_empty()

    @pytest.mark.parametrize("argument", ["forms", "items"])
    def test_a_bare_string_filter_raises(
        self, fixture_store: duckdb.DuckDBPyConnection, argument: str
    ) -> None:
        with pytest.raises(TypeError):
            filing_events_as_of(fixture_store, self._AFTER_ALL, **{argument: "2.02"})

    def test_bare_date_raises(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(TypeError):
            filing_events_as_of(fixture_store, date(2020, 5, 1))  # type: ignore[arg-type]

    def test_naive_datetime_raises(self, fixture_store: duckdb.DuckDBPyConnection) -> None:
        with pytest.raises(ValueError):
            filing_events_as_of(fixture_store, datetime(2020, 5, 1, 21, 0))  # noqa: DTZ001

    def test_view_is_unregistered_after_the_call(
        self, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        filing_events_as_of(fixture_store, self._AFTER_ALL)
        with pytest.raises(duckdb.CatalogException):
            fixture_store.execute("SELECT * FROM _asof_filing_event_securities")
