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
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import duckdb
import polars as pl
import pytest

from tradepartner.store.asof import (
    adjusted_prices_as_of,
    facts_as_of,
    listings_as_of,
    prices_as_of,
)


def _one(df: pl.DataFrame, **match: object) -> dict[str, object]:
    """The single row of `df` whose fields match every `match` kwarg, as a
    `{column: value}` dict, or fail loudly (there should never be more or
    fewer than one for these tests' natural keys)."""
    filtered = df
    for key, value in match.items():
        filtered = filtered.filter(pl.col(key) == value)
    assert filtered.height == 1, f"expected exactly one match for {match}, got {filtered}"
    return filtered.row(0, named=True)


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
