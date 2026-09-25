"""Tests for the `PriceSource` interface, its timing rules and the fixture
adapter (T7).

Covers the plan line's three items -- bars; actions with the first-seen
proxy and the revision rule; resolution by `security_id` -- plus the
record validation, the fixture adapter's contract enforcement (a fixture
row that breaks a timing rule is refused at load time, not emitted) and a
round trip proving the adapter's output loads into a store that agrees
with the CSV-loaded `fixture_store` under the T6 as-of API.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import polars as pl
import pytest

from tradepartner.adapters.fixture_prices import FixtureContractError, FixturePriceSource
from tradepartner.adapters.prices import (
    ActionType,
    Bar,
    CorporateAction,
    PriceSource,
    UnknownSecurityIdError,
    action_first_seen_known_at,
    bar_known_at,
    revision_of,
)
from tradepartner.calendar import session_close
from tradepartner.store import schema
from tradepartner.store.asof import adjusted_prices_as_of, prices_as_of
from tradepartner.store.db import configure_connection, insert_row

UNIVERSE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "universe"

FAR_PAST = date(2000, 1, 1)
FAR_FUTURE = date(2030, 12, 31)


@pytest.fixture(scope="module")
def source() -> FixturePriceSource:
    """One adapter over the committed fixture universe for the whole
    module: loading validates every row against the calendar, which is
    not free."""
    return FixturePriceSource(UNIVERSE_DIR)


def _csv_rows(name: str) -> list[dict[str, str]]:
    with (UNIVERSE_DIR / name).open(newline="") as fh:
        return list(csv.DictReader(fh))


def _write_csv(path: Path, header: list[str], rows: Iterable[Mapping[str, object]]) -> None:
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _make_bar(**overrides: object) -> Bar:
    fields: dict[str, object] = {
        "security_id": "SEC_X",
        "session": date(2019, 3, 1),
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "volume": 1000,
        "known_at": datetime(2019, 3, 1, 21, 0, tzinfo=UTC),
        "source": "test",
    }
    fields.update(overrides)
    return Bar(**fields)  # type: ignore[arg-type]


def _make_action(**overrides: object) -> CorporateAction:
    fields: dict[str, object] = {
        "security_id": "SEC_X",
        "action_type": ActionType.SPLIT,
        "ex_date": date(2019, 3, 4),
        "ratio_or_amount": 2.0,
        "known_at": datetime(2019, 3, 1, 21, 0, tzinfo=UTC),
        "source": "test",
    }
    fields.update(overrides)
    return CorporateAction(**fields)  # type: ignore[arg-type]


class TestInterface:
    def test_price_source_is_abstract(self) -> None:
        with pytest.raises(TypeError):
            PriceSource()  # type: ignore[abstract]

    def test_fixture_adapter_is_a_price_source(self, source: FixturePriceSource) -> None:
        assert isinstance(source, PriceSource)


class TestBars:
    def test_every_bar_matches_its_csv_row_and_is_stamped_at_the_session_close(
        self, source: FixturePriceSource
    ) -> None:
        bars = source.bars(["SEC_25NSE"], FAR_PAST, FAR_FUTURE)
        expected = [r for r in _csv_rows("prices_daily.csv") if r["security_id"] == "SEC_25NSE"]
        assert len(bars) == len(expected) == 101
        for bar, row in zip(bars, expected, strict=True):
            assert bar.session == date.fromisoformat(row["session"])
            assert (bar.open, bar.high, bar.low, bar.close) == (
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
            )
            assert bar.volume == int(row["volume"])
            assert bar.known_at == datetime.fromisoformat(row["known_at"])
            assert bar.known_at == session_close(bar.session) == bar_known_at(bar.session)
            assert bar.source == row["source"]

    def test_bars_are_sorted_by_security_then_session_then_known_at(
        self, source: FixturePriceSource
    ) -> None:
        bars = source.bars(["SEC_SPY", "SEC_25NSE"], FAR_PAST, FAR_FUTURE)
        keys = [(b.security_id, b.session, b.known_at) for b in bars]
        assert keys == sorted(keys)
        assert {b.security_id for b in bars} == {"SEC_SPY", "SEC_25NSE"}

    def test_range_is_inclusive_on_both_ends(self, source: FixturePriceSource) -> None:
        bars = source.bars(["SEC_25NSE"], date(2018, 4, 2), date(2018, 4, 4))
        assert [b.session for b in bars] == [
            date(2018, 4, 2),
            date(2018, 4, 3),
            date(2018, 4, 4),
        ]

    def test_range_with_no_bars_is_empty_not_an_error(self, source: FixturePriceSource) -> None:
        # SEC_25NSE's first bar is 2018-04-02.
        assert source.bars(["SEC_25NSE"], date(2017, 1, 3), date(2017, 12, 29)) == []

    def test_no_ids_means_no_bars(self, source: FixturePriceSource) -> None:
        assert source.bars([], FAR_PAST, FAR_FUTURE) == []

    def test_a_bare_string_is_not_a_list_of_ids(self, source: FixturePriceSource) -> None:
        with pytest.raises(TypeError, match="sequence"):
            source.bars("SEC_SPY", FAR_PAST, FAR_FUTURE)
        with pytest.raises(TypeError, match="sequence"):
            source.corporate_actions("SEC_SPY", FAR_PAST, FAR_FUTURE)

    def test_start_after_end_raises(self, source: FixturePriceSource) -> None:
        with pytest.raises(ValueError, match="start"):
            source.bars(["SEC_25NSE"], date(2018, 5, 1), date(2018, 4, 1))

    def test_datetime_bounds_are_rejected(self, source: FixturePriceSource) -> None:
        with pytest.raises(TypeError, match="date"):
            source.bars(["SEC_25NSE"], datetime(2018, 4, 2, tzinfo=UTC), date(2018, 4, 4))
        with pytest.raises(TypeError, match="date"):
            source.bars(["SEC_25NSE"], date(2018, 4, 2), datetime(2018, 4, 4, tzinfo=UTC))

    def test_half_day_bar_is_stamped_at_the_early_close(self, source: FixturePriceSource) -> None:
        # 2018-11-23 (day after Thanksgiving) closes at 13:00 ET = 18:00 UTC.
        (bar,) = source.bars(["SEC_SPLIT_FUTURE"], date(2018, 11, 23), date(2018, 11, 23))
        assert bar.known_at == datetime(2018, 11, 23, 18, 0, tzinfo=UTC)
        (normal,) = source.bars(["SEC_SPLIT_FUTURE"], date(2018, 11, 21), date(2018, 11, 21))
        assert normal.known_at == datetime(2018, 11, 21, 21, 0, tzinfo=UTC)

    def test_holiday_has_no_bar(self, source: FixturePriceSource) -> None:
        assert source.bars(["SEC_SPLIT_FUTURE"], date(2018, 11, 22), date(2018, 11, 22)) == []

    def test_bar_revision_is_a_second_record_stamped_at_its_ingest_time(
        self, source: FixturePriceSource
    ) -> None:
        # SEC_SPLIT_BACKFILLED 2018-06-01: original close 38.45 at the
        # session close; re-fetched close 40.37 with known_at = ingested_at.
        original, revised = source.bars(
            ["SEC_SPLIT_BACKFILLED"], date(2018, 6, 1), date(2018, 6, 1)
        )
        assert original.close == pytest.approx(38.45)
        assert original.known_at == session_close(date(2018, 6, 1))
        assert revised.close == pytest.approx(40.37)
        assert revised.known_at == datetime(2018, 6, 15, 20, 0, tzinfo=UTC)
        assert revised.known_at > original.known_at
        assert revised.known_at != session_close(revised.session)


class TestCorporateActions:
    def test_plain_split_uses_the_close_before_ex_date_proxy(
        self, source: FixturePriceSource
    ) -> None:
        (split,) = source.corporate_actions(["SEC_SPLIT_PLAIN"], FAR_PAST, FAR_FUTURE)
        assert split.action_type is ActionType.SPLIT
        assert split.ex_date == date(2018, 12, 13)
        assert split.ratio_or_amount == pytest.approx(2.0)
        assert split.known_at == action_first_seen_known_at(split.ex_date)
        assert split.known_at == session_close(date(2018, 12, 12))

    def test_announced_split_is_known_before_the_proxy(self, source: FixturePriceSource) -> None:
        (split,) = source.corporate_actions(["SEC_SPLIT_FUTURE"], FAR_PAST, FAR_FUTURE)
        assert split.ex_date == date(2019, 2, 14)
        assert split.known_at == datetime(2018, 11, 15, 21, 0, tzinfo=UTC)
        assert split.known_at < action_first_seen_known_at(split.ex_date)

    def test_revised_dividend_is_a_second_record_never_back_dated(
        self, source: FixturePriceSource
    ) -> None:
        first, revised = source.corporate_actions(["SEC_DIV_REVISED"], FAR_PAST, FAR_FUTURE)
        assert first.action_type is revised.action_type is ActionType.DIVIDEND
        assert first.ex_date == revised.ex_date == date(2019, 2, 25)
        assert first.ratio_or_amount == pytest.approx(0.10)
        assert first.known_at == action_first_seen_known_at(first.ex_date)
        assert revised.ratio_or_amount == pytest.approx(0.12)
        assert revised.known_at == datetime(2019, 3, 25, 21, 0, tzinfo=UTC)
        assert revised.known_at > first.known_at

    def test_range_filters_on_ex_date_inclusive(self, source: FixturePriceSource) -> None:
        ids = ["SEC_SPY", "SEC_MTUM", "SEC_SPLIT_PLAIN"]
        assert source.corporate_actions(ids, date(2018, 6, 25), date(2018, 6, 25)) != []
        assert source.corporate_actions(ids, date(2018, 6, 26), date(2018, 12, 12)) == []
        both = source.corporate_actions(ids, date(2018, 6, 25), date(2018, 12, 13))
        assert [(a.security_id, a.ex_date) for a in both] == [
            ("SEC_MTUM", date(2018, 6, 25)),
            ("SEC_SPLIT_PLAIN", date(2018, 12, 13)),
            ("SEC_SPY", date(2018, 6, 25)),
        ]

    def test_start_after_end_raises(self, source: FixturePriceSource) -> None:
        with pytest.raises(ValueError, match="start"):
            source.corporate_actions(["SEC_SPY"], date(2019, 1, 1), date(2018, 1, 1))


class TestTimingRules:
    def test_bar_known_at_is_the_session_close(self) -> None:
        assert bar_known_at(date(2018, 11, 23)) == datetime(2018, 11, 23, 18, 0, tzinfo=UTC)
        assert bar_known_at(date(2018, 11, 21)) == datetime(2018, 11, 21, 21, 0, tzinfo=UTC)

    def test_bar_known_at_rejects_a_non_session(self) -> None:
        with pytest.raises(ValueError, match="session"):
            bar_known_at(date(2018, 11, 22))  # Thanksgiving

    def test_bar_known_at_rejects_a_datetime(self) -> None:
        with pytest.raises(TypeError):
            bar_known_at(datetime(2018, 11, 21, tzinfo=UTC))

    def test_first_seen_proxy_is_the_close_of_the_session_before_ex_date(self) -> None:
        assert action_first_seen_known_at(date(2018, 12, 13)) == session_close(date(2018, 12, 12))
        # Ex-date on a Monday: the session before is Friday.
        assert action_first_seen_known_at(date(2019, 2, 25)) == session_close(date(2019, 2, 22))
        # Ex-date that is not itself a session: still the last session before it.
        assert action_first_seen_known_at(date(2018, 12, 15)) == session_close(date(2018, 12, 14))

    def test_proxy_after_a_half_day_is_the_early_close(self) -> None:
        # Monday 2018-11-26 follows the 2018-11-23 half day (13:00 ET).
        assert action_first_seen_known_at(date(2018, 11, 26)) == datetime(
            2018, 11, 23, 18, 0, tzinfo=UTC
        )

    def test_proxy_across_the_spring_dst_change(self) -> None:
        # Clocks moved forward on Sunday 2019-03-10: Friday's 16:00 ET close is
        # 21:00 UTC, Monday's is 20:00 UTC.
        assert action_first_seen_known_at(date(2019, 3, 11)) == datetime(
            2019, 3, 8, 21, 0, tzinfo=UTC
        )
        assert action_first_seen_known_at(date(2019, 3, 12)) == datetime(
            2019, 3, 11, 20, 0, tzinfo=UTC
        )

    def test_announcement_time_wins_over_the_proxy(self) -> None:
        announced = datetime(2018, 11, 15, 16, 0, tzinfo=timezone(timedelta(hours=-5)))
        result = action_first_seen_known_at(date(2019, 2, 14), announced_at=announced)
        assert result == datetime(2018, 11, 15, 21, 0, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_naive_announcement_time_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="announced_at"):
            action_first_seen_known_at(
                date(2019, 2, 14),
                announced_at=datetime(2018, 11, 15, 16, 0),  # noqa: DTZ001
            )


class TestRevisionRule:
    T_INGEST = datetime(2019, 3, 8, 20, 0, tzinfo=UTC)

    def test_first_seen_record_is_returned_unchanged(self) -> None:
        bar = _make_bar()
        assert revision_of(bar, None, ingested_at=self.T_INGEST) is bar

    def test_first_seen_record_not_yet_knowable_is_rejected(self) -> None:
        # Fetched mid-session: stamped at a close that has not happened yet.
        bar = _make_bar(known_at=datetime(2019, 3, 1, 21, 0, tzinfo=UTC))
        with pytest.raises(ValueError, match="not knowable yet"):
            revision_of(bar, None, ingested_at=datetime(2019, 3, 1, 18, 0, tzinfo=UTC))
        assert revision_of(bar, None, ingested_at=bar.known_at) is bar

    def test_identical_values_are_a_no_op(self) -> None:
        stored = _make_bar()
        incoming = _make_bar(source="other")
        assert revision_of(incoming, stored, ingested_at=self.T_INGEST) is None

    def test_changed_bar_is_stamped_at_ingest_time(self) -> None:
        stored = _make_bar()
        incoming = _make_bar(close=10.75)
        revised = revision_of(incoming, stored, ingested_at=self.T_INGEST)
        assert revised is not None
        assert revised.known_at == self.T_INGEST
        assert revised == replace(incoming, known_at=self.T_INGEST)

    def test_changed_action_is_stamped_at_ingest_time(self) -> None:
        stored = _make_action(action_type=ActionType.DIVIDEND, ratio_or_amount=0.10)
        incoming = _make_action(action_type=ActionType.DIVIDEND, ratio_or_amount=0.12)
        revised = revision_of(incoming, stored, ingested_at=self.T_INGEST)
        assert revised is not None
        assert revised.known_at == self.T_INGEST
        assert revised.ratio_or_amount == pytest.approx(0.12)

    def test_revision_is_never_back_dated(self) -> None:
        stored = _make_bar()
        incoming = _make_bar(close=10.75)
        with pytest.raises(ValueError, match="back-dated"):
            revision_of(incoming, stored, ingested_at=stored.known_at)
        with pytest.raises(ValueError, match="back-dated"):
            revision_of(incoming, stored, ingested_at=stored.known_at - timedelta(days=1))

    def test_naive_ingest_time_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="ingested_at"):
            revision_of(
                _make_bar(close=10.75),
                _make_bar(),
                ingested_at=datetime(2019, 3, 8, 20, 0),  # noqa: DTZ001
            )

    def test_mismatched_keys_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="key"):
            revision_of(_make_bar(session=date(2019, 3, 4)), _make_bar(), ingested_at=self.T_INGEST)
        with pytest.raises(ValueError, match="key"):
            revision_of(_make_bar(security_id="SEC_Y"), _make_bar(), ingested_at=self.T_INGEST)
        with pytest.raises(ValueError, match="key"):
            revision_of(
                _make_action(action_type=ActionType.DIVIDEND, ratio_or_amount=0.1),
                _make_action(),
                ingested_at=self.T_INGEST,
            )

    def test_bar_and_action_cannot_be_compared(self) -> None:
        with pytest.raises(ValueError, match="key"):
            revision_of(_make_bar(), _make_action(), ingested_at=self.T_INGEST)  # type: ignore[call-overload]


class TestResolutionBySecurityId:
    def test_reused_ticker_resolves_only_the_requested_company(
        self, source: FixturePriceSource
    ) -> None:
        listings = _csv_rows("listings.csv")
        assert {r["security_id"] for r in listings if r["ticker"] == "REUSE"} == {
            "SEC_REUSE_1",
            "SEC_REUSE_2",
        }
        first = source.bars(["SEC_REUSE_1"], FAR_PAST, FAR_FUTURE)
        second = source.bars(["SEC_REUSE_2"], FAR_PAST, FAR_FUTURE)
        assert {b.security_id for b in first} == {"SEC_REUSE_1"}
        assert {b.security_id for b in second} == {"SEC_REUSE_2"}
        # The first company stops trading before the second one lists.
        assert max(b.session for b in first) < min(b.session for b in second)
        assert min(b.session for b in second) == date(2019, 8, 1)

    def test_a_ticker_is_not_a_security_id(self, source: FixturePriceSource) -> None:
        with pytest.raises(UnknownSecurityIdError, match="REUSE"):
            source.bars(["REUSE"], FAR_PAST, FAR_FUTURE)
        with pytest.raises(UnknownSecurityIdError, match="SPY"):
            source.corporate_actions(["SPY"], FAR_PAST, FAR_FUTURE)

    def test_one_unknown_id_fails_the_whole_call(self, source: FixturePriceSource) -> None:
        with pytest.raises(UnknownSecurityIdError, match="SEC_NOPE") as excinfo:
            source.bars(["SEC_SPY", "SEC_NOPE"], FAR_PAST, FAR_FUTURE)
        assert "SEC_SPY" not in str(excinfo.value)

    def test_a_security_with_no_prices_is_known_but_empty(self, source: FixturePriceSource) -> None:
        # SEC_STATIC_PRE2019 has bars but no corporate actions.
        assert source.corporate_actions(["SEC_STATIC_PRE2019"], FAR_PAST, FAR_FUTURE) == []


class TestRecordValidation:
    def test_naive_known_at_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="known_at"):
            _make_bar(known_at=datetime(2019, 3, 1, 21, 0))  # noqa: DTZ001
        with pytest.raises(ValueError, match="known_at"):
            _make_action(known_at=datetime(2019, 3, 1, 21, 0))  # noqa: DTZ001

    def test_known_at_is_normalized_to_utc(self) -> None:
        eastern = timezone(timedelta(hours=-5))
        bar = _make_bar(known_at=datetime(2019, 3, 1, 16, 0, tzinfo=eastern))
        assert bar.known_at == datetime(2019, 3, 1, 21, 0, tzinfo=UTC)
        assert bar.known_at.tzinfo == UTC

    def test_session_and_ex_date_must_be_dates_not_datetimes(self) -> None:
        with pytest.raises(TypeError, match="session"):
            _make_bar(session=datetime(2019, 3, 1, tzinfo=UTC))
        with pytest.raises(TypeError, match="ex_date"):
            _make_action(ex_date=datetime(2019, 3, 4, tzinfo=UTC))

    @pytest.mark.parametrize(
        "overrides",
        [
            {"low": 12.0},  # low above high
            {"open": 20.0},  # open above high
            {"close": 1.0},  # close below low
            {"close": 0.0},
            {"close": -1.0},
            {"close": float("nan")},
            {"high": float("inf")},
            {"volume": -1},
            {"volume": 10.5},
            {"security_id": ""},
            {"security_id": " SEC_X"},
            {"source": ""},
        ],
    )
    def test_invalid_bar_values_are_rejected(self, overrides: dict[str, object]) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_bar(**overrides)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"ratio_or_amount": 0.0},  # a zero split ratio divides by zero downstream
            {"ratio_or_amount": -2.0},
            {"ratio_or_amount": float("inf")},
            {"action_type": ActionType.DIVIDEND, "ratio_or_amount": -0.1},
            {"action_type": ActionType.DIVIDEND, "ratio_or_amount": float("nan")},
            {"action_type": "merger"},
            {"security_id": ""},
        ],
    )
    def test_invalid_action_values_are_rejected(self, overrides: dict[str, object]) -> None:
        with pytest.raises((ValueError, TypeError)):
            _make_action(**overrides)

    def test_action_type_string_is_coerced(self) -> None:
        action = _make_action(action_type="dividend", ratio_or_amount=0.0)
        assert action.action_type is ActionType.DIVIDEND

    def test_a_zero_dividend_is_allowed(self) -> None:
        assert _make_action(action_type=ActionType.DIVIDEND, ratio_or_amount=0.0)


_SECURITIES_HEADER = [
    "security_id",
    "cik",
    "name",
    "benchmark",
    "known_at",
    "ingested_at",
    "source",
    "provenance",
]
_PRICES_HEADER = [
    "security_id",
    "session",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "known_at",
    "ingested_at",
    "source",
    "provenance",
]
_ACTIONS_HEADER = [
    "security_id",
    "action_type",
    "ex_date",
    "ratio_or_amount",
    "known_at",
    "ingested_at",
    "source",
    "provenance",
]


def _bar_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "security_id": "SEC_A",
        "session": "2019-03-01",
        "open": 10.0,
        "high": 11.0,
        "low": 9.5,
        "close": 10.5,
        "volume": 1000,
        "known_at": "2019-03-01T21:00:00+00:00",
        "ingested_at": "2019-03-01T21:10:00+00:00",
        "source": "alpaca",
        "provenance": "bar",
    }
    row.update(overrides)
    return row


def _action_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "security_id": "SEC_A",
        "action_type": "dividend",
        "ex_date": "2019-03-04",
        "ratio_or_amount": 0.1,
        "known_at": "2019-03-01T21:00:00+00:00",
        "ingested_at": "2019-03-01T21:10:00+00:00",
        "source": "alpaca",
        "provenance": "action",
    }
    row.update(overrides)
    return row


def _write_fixture_dir(
    tmp_path: Path,
    *,
    bars: Iterable[Mapping[str, object]] = (),
    actions: Iterable[Mapping[str, object]] = (),
    securities: Iterable[str] = ("SEC_A",),
    write_securities: bool = True,
) -> Path:
    fixtures = tmp_path / "universe"
    fixtures.mkdir()
    if write_securities:
        _write_csv(
            fixtures / "securities.csv",
            _SECURITIES_HEADER,
            [
                {
                    "security_id": sid,
                    "cik": f"CIK{sid}",
                    "name": sid,
                    "benchmark": "FALSE",
                    "known_at": "2018-01-02T21:00:00+00:00",
                    "ingested_at": "2018-01-02T21:10:00+00:00",
                    "source": "edgar",
                    "provenance": "filing",
                }
                for sid in securities
            ],
        )
    _write_csv(fixtures / "prices_daily.csv", _PRICES_HEADER, bars)
    _write_csv(fixtures / "corporate_actions.csv", _ACTIONS_HEADER, actions)
    return fixtures


class TestFixtureContract:
    def test_a_well_formed_fixture_loads(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(
            tmp_path,
            bars=[
                _bar_row(),
                _bar_row(
                    close=10.75,
                    known_at="2019-03-08T20:00:00+00:00",
                    ingested_at="2019-03-08T20:00:00+00:00",
                ),
            ],
            actions=[
                _action_row(),
                _action_row(
                    ratio_or_amount=0.12,
                    known_at="2019-03-15T20:00:00+00:00",
                    ingested_at="2019-03-15T20:00:00+00:00",
                ),
            ],
        )
        adapter = FixturePriceSource(fixtures)
        assert len(adapter.bars(["SEC_A"], FAR_PAST, FAR_FUTURE)) == 2
        assert len(adapter.corporate_actions(["SEC_A"], FAR_PAST, FAR_FUTURE)) == 2

    def test_missing_securities_csv_is_refused(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(tmp_path, write_securities=False)
        with pytest.raises(FixtureContractError, match=r"securities\.csv"):
            FixturePriceSource(fixtures)

    def test_missing_prices_or_actions_csv_means_no_rows(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(tmp_path)
        (fixtures / "prices_daily.csv").unlink()
        (fixtures / "corporate_actions.csv").unlink()
        adapter = FixturePriceSource(fixtures)
        assert adapter.bars(["SEC_A"], FAR_PAST, FAR_FUTURE) == []
        assert adapter.corporate_actions(["SEC_A"], FAR_PAST, FAR_FUTURE) == []

    def test_bar_not_stamped_at_the_session_close_is_refused(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(
            tmp_path, bars=[_bar_row(known_at="2019-03-01T20:00:00+00:00")]
        )
        with pytest.raises(FixtureContractError, match="session close"):
            FixturePriceSource(fixtures)

    def test_bar_stamped_before_its_session_is_refused(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(
            tmp_path,
            bars=[
                _bar_row(
                    known_at="2019-02-28T21:00:00+00:00", ingested_at="2019-02-28T21:10:00+00:00"
                )
            ],
        )
        with pytest.raises(FixtureContractError, match="session close"):
            FixturePriceSource(fixtures)

    def test_bar_on_a_non_session_is_refused(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(
            tmp_path,
            bars=[
                _bar_row(
                    session="2019-03-02",  # a Saturday
                    known_at="2019-03-02T21:00:00+00:00",
                    ingested_at="2019-03-02T21:10:00+00:00",
                )
            ],
        )
        with pytest.raises(FixtureContractError, match="session"):
            FixturePriceSource(fixtures)

    def test_naive_timestamp_is_refused(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(tmp_path, bars=[_bar_row(known_at="2019-03-01T21:00:00")])
        with pytest.raises(FixtureContractError, match="tz-aware"):
            FixturePriceSource(fixtures)

    def test_known_at_after_ingested_at_is_refused(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(
            tmp_path, actions=[_action_row(ingested_at="2019-03-01T20:00:00+00:00")]
        )
        with pytest.raises(FixtureContractError, match="ingested_at"):
            FixturePriceSource(fixtures)

    def test_revision_not_stamped_at_its_ingest_time_is_refused(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(
            tmp_path,
            bars=[
                _bar_row(),
                _bar_row(
                    close=10.75,
                    known_at="2019-03-01T21:00:00+00:00",
                    ingested_at="2019-03-08T20:00:00+00:00",
                ),
            ],
        )
        with pytest.raises(FixtureContractError, match="revision"):
            FixturePriceSource(fixtures)

    def test_back_dated_action_revision_is_refused(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(
            tmp_path,
            actions=[
                _action_row(),
                # Ingested two weeks later but stamped with the first-seen
                # time: a back-dated revision.
                _action_row(
                    ratio_or_amount=0.12,
                    known_at="2019-03-01T21:00:00+00:00",
                    ingested_at="2019-03-15T20:00:00+00:00",
                ),
            ],
        )
        with pytest.raises(FixtureContractError, match="back-dated"):
            FixturePriceSource(fixtures)

    def test_first_seen_action_stamped_after_the_proxy_is_refused(self, tmp_path: Path) -> None:
        # Proxy for ex-date 2019-03-04 is the 2019-03-01 close (21:00 UTC).
        fixtures = _write_fixture_dir(
            tmp_path,
            actions=[
                _action_row(
                    known_at="2019-03-04T21:00:00+00:00", ingested_at="2019-03-04T21:10:00+00:00"
                )
            ],
        )
        with pytest.raises(FixtureContractError, match="first-seen proxy"):
            FixturePriceSource(fixtures)

    def test_first_seen_action_announced_before_the_proxy_loads(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(
            tmp_path, actions=[_action_row(known_at="2019-02-15T21:00:00+00:00")]
        )
        (action,) = FixturePriceSource(fixtures).corporate_actions(["SEC_A"], FAR_PAST, FAR_FUTURE)
        assert action.known_at == datetime(2019, 2, 15, 21, 0, tzinfo=UTC)

    def test_missing_column_is_refused_with_file_context(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(tmp_path)
        _write_csv(
            fixtures / "prices_daily.csv",
            [c for c in _PRICES_HEADER if c != "ingested_at"],
            [{k: v for k, v in _bar_row().items() if k != "ingested_at"}],
        )
        with pytest.raises(FixtureContractError, match=r"prices_daily\.csv:1: .*ingested_at"):
            FixturePriceSource(fixtures)

    def test_revision_with_identical_values_is_refused(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(
            tmp_path,
            bars=[
                _bar_row(),
                _bar_row(
                    known_at="2019-03-08T20:00:00+00:00", ingested_at="2019-03-08T20:00:00+00:00"
                ),
            ],
        )
        with pytest.raises(FixtureContractError, match="identical"):
            FixturePriceSource(fixtures)

    def test_row_for_an_unknown_security_is_refused(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(tmp_path, bars=[_bar_row(security_id="SEC_B")])
        with pytest.raises(FixtureContractError, match="SEC_B"):
            FixturePriceSource(fixtures)

    def test_invalid_record_values_are_refused_with_row_context(self, tmp_path: Path) -> None:
        fixtures = _write_fixture_dir(tmp_path, bars=[_bar_row(low=12.0)])
        with pytest.raises(FixtureContractError, match=r"prices_daily\.csv:2"):
            FixturePriceSource(fixtures)


class TestRoundTrip:
    """The adapter's records, written to a fresh store, agree with the
    CSV-loaded `fixture_store` under the T6 as-of API. `ingested_at` is
    set to `known_at` (the earliest legal value), so it is the one column
    excluded from the comparison."""

    IDS = (
        "SEC_SPLIT_BACKFILLED",
        "SEC_DIV_REVISED",
        "SEC_SPLIT_FUTURE",
        "SEC_SPLIT_REDATED",
        "SEC_DIV_CANCELLED",
    )
    PROBES = (
        datetime(2018, 6, 15, 19, 59, 59, tzinfo=UTC),  # before the bar revision
        datetime(2019, 1, 31, 21, 0, tzinfo=UTC),  # backfilled-split probe T
        datetime(2019, 3, 25, 20, 59, 59, tzinfo=UTC),  # before the dividend revision
        datetime(2019, 3, 25, 21, 0, tzinfo=UTC),  # at the dividend revision
        datetime(2019, 6, 12, 20, 59, 59, tzinfo=UTC),  # before the split re-date (#108)
        datetime(2019, 6, 12, 21, 0, tzinfo=UTC),  # at the split re-date
        datetime(2019, 9, 20, 21, 0, tzinfo=UTC),  # at the dividend cancel
        datetime(2030, 1, 1, tzinfo=UTC),
    )

    @pytest.fixture
    def adapter_store(self, source: FixturePriceSource) -> Iterator[duckdb.DuckDBPyConnection]:
        conn = duckdb.connect(":memory:")
        configure_connection(conn)
        schema.init_schema(conn)
        for bar in source.bars(list(self.IDS), FAR_PAST, FAR_FUTURE):
            insert_row(
                conn,
                "prices_daily",
                {
                    "security_id": bar.security_id,
                    "session": bar.session,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "known_at": bar.known_at,
                    "ingested_at": bar.known_at,
                    "source": bar.source,
                    "provenance": "bar",
                },
            )
        for action in source.corporate_actions(list(self.IDS), FAR_PAST, FAR_FUTURE):
            insert_row(
                conn,
                "corporate_actions",
                {
                    "security_id": action.security_id,
                    "action_type": action.action_type.value,
                    "ex_date": action.ex_date,
                    "ratio_or_amount": action.ratio_or_amount,
                    "source_action_id": action.source_action_id or "",
                    "cancelled": action.cancelled,
                    "known_at": action.known_at,
                    "ingested_at": action.known_at,
                    "source": action.source,
                    "provenance": "action",
                },
            )
        try:
            yield conn
        finally:
            conn.close()

    def test_row_counts_match_the_csv(
        self, adapter_store: duckdb.DuckDBPyConnection, fixture_store: duckdb.DuckDBPyConnection
    ) -> None:
        for table in ("prices_daily", "corporate_actions"):
            sql = f"SELECT COUNT(*) FROM {table} WHERE security_id IN (?, ?, ?, ?, ?)"
            assert (
                adapter_store.execute(sql, list(self.IDS)).fetchone()
                == fixture_store.execute(sql, list(self.IDS)).fetchone()
            )

    @pytest.mark.parametrize("t", PROBES)
    def test_as_of_reads_agree(
        self,
        adapter_store: duckdb.DuckDBPyConnection,
        fixture_store: duckdb.DuckDBPyConnection,
        t: datetime,
    ) -> None:
        ids = list(self.IDS)
        raw_a = prices_as_of(adapter_store, t, ids).drop("ingested_at")
        raw_f = prices_as_of(fixture_store, t, ids).drop("ingested_at")
        assert raw_a.equals(raw_f)
        adj_a = adjusted_prices_as_of(adapter_store, t, ids, include_dividends=True)
        adj_f = adjusted_prices_as_of(fixture_store, t, ids, include_dividends=True)
        assert adj_a.drop("ingested_at").equals(adj_f.drop("ingested_at"))
        assert isinstance(adj_a, pl.DataFrame)
