"""Tests for `adapters.rates` (the record and the stamp rule) and
`adapters.fixture_rates` (the fixture source), plan task T169 (#1498): the
data-foundation spec's "Schema and record" acceptance line and the six
generated fixture cases.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tradepartner.adapters.fixture_rates import FixtureRateSource
from tradepartner.adapters.rates import RateObservation, RateSource, rate_known_at
from tradepartner.calendar import is_session, next_session, session_close

_RATES_CSV = Path(__file__).resolve().parents[1] / "fixtures" / "universe" / "rates.csv"


def _obs(day: date, value: float, vintage: date, series: str = "DTB3") -> RateObservation:
    return RateObservation(series, day, value, vintage)


# --- the record -----------------------------------------------------------


def test_the_record_keeps_its_fields_and_makes_the_value_a_float() -> None:
    record = RateObservation("DTB3", date(2020, 1, 2), 2, date(2020, 1, 3))
    assert record == RateObservation("DTB3", date(2020, 1, 2), 2.0, date(2020, 1, 3))
    assert isinstance(record.value, float)


@pytest.mark.parametrize(
    "field_name",
    ["observation_date", "vintage_date"],
)
@pytest.mark.parametrize(
    "bad",
    [
        datetime(2020, 1, 3),  # noqa: DTZ001 - the naive datetime the record refuses
        datetime(2020, 1, 3, tzinfo=UTC),
        "2020-01-03",
        None,
    ],
    ids=["naive-datetime", "aware-datetime", "string", "none"],
)
def test_the_record_refuses_a_datetime_or_a_non_date(field_name: str, bad: object) -> None:
    fields: dict[str, object] = {
        "series": "DTB3",
        "observation_date": date(2020, 1, 2),
        "value": 1.5,
        "vintage_date": date(2020, 1, 3),
    }
    fields[field_name] = bad
    with pytest.raises(TypeError, match=field_name):
        RateObservation(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_the_record_refuses_a_non_finite_value(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        _obs(date(2020, 1, 2), value, date(2020, 1, 3))


@pytest.mark.parametrize("value", ["1.5", None, True])
def test_the_record_refuses_a_value_that_is_not_a_number(value: object) -> None:
    with pytest.raises(TypeError, match="value"):
        RateObservation("DTB3", date(2020, 1, 2), value, date(2020, 1, 3))  # type: ignore[arg-type]


@pytest.mark.parametrize("series", ["", None])
def test_the_record_refuses_an_empty_series(series: object) -> None:
    with pytest.raises(ValueError, match="series"):
        RateObservation(series, date(2020, 1, 2), 1.5, date(2020, 1, 3))  # type: ignore[arg-type]


def test_the_record_refuses_a_vintage_before_its_observation_date() -> None:
    with pytest.raises(ValueError, match="before observation_date"):
        _obs(date(2020, 1, 3), 1.5, date(2020, 1, 2))
    # A vintage on the observation date itself (FRED's lower bound) is a record.
    assert _obs(date(2020, 1, 2), 1.5, date(2020, 1, 2)).vintage_date == date(2020, 1, 2)


def test_a_negative_value_is_a_record() -> None:
    """A few T-bill prints were negative (2015, 2020): the plausibility band
    is health's warning, never the record's refusal."""
    assert _obs(date(2020, 3, 26), -0.04, date(2020, 3, 27)).value == -0.04


# --- the stamp rule -------------------------------------------------------


@pytest.mark.parametrize(
    "vintage,expected_session",
    [
        (date(2019, 5, 8), date(2019, 5, 9)),  # a Wednesday session: Thursday's close
        (date(2018, 3, 9), date(2018, 3, 12)),  # a Friday: Monday's close
        (date(2018, 6, 16), date(2018, 6, 18)),  # a Saturday: Monday's close
        (date(2018, 11, 22), date(2018, 11, 23)),  # Thanksgiving: the half day's close
        (date(2019, 7, 3), date(2019, 7, 5)),  # the day before a holiday: after it
    ],
    ids=["session", "friday", "saturday", "holiday", "eve-of-holiday"],
)
def test_rate_known_at_is_the_close_of_the_next_session(
    vintage: date, expected_session: date
) -> None:
    stamp = rate_known_at(vintage)
    assert stamp == session_close(next_session(vintage)) == session_close(expected_session)
    assert stamp.tzinfo is not None and stamp.utcoffset() == timedelta(0)
    assert stamp.date() > vintage


def test_rate_known_at_on_a_holiday_vintage_reads_the_half_day_close() -> None:
    """Thanksgiving 2018 is not a session; the next one is a half day, whose
    close is 13:00 New York (18:00 UTC), not 16:00."""
    assert not is_session(date(2018, 11, 22))
    assert rate_known_at(date(2018, 11, 22)) == datetime(2018, 11, 23, 18, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "bad",
    [datetime(2019, 5, 8), datetime(2019, 5, 8, tzinfo=UTC)],  # noqa: DTZ001 - refused
    ids=["naive", "aware"],
)
def test_rate_known_at_refuses_a_datetime(bad: datetime) -> None:
    with pytest.raises(TypeError, match="not a datetime"):
        rate_known_at(bad)


# --- the fixture source ---------------------------------------------------


def test_the_fixture_source_is_a_rate_source_that_stamps_nothing() -> None:
    source = FixtureRateSource.from_csv(_RATES_CSV)
    assert isinstance(source, RateSource)
    assert source.name == "fred"
    record = source.observations("DTB3", date(2019, 5, 1), date(2019, 5, 7))[0]
    assert not hasattr(record, "known_at")


def test_the_fixture_source_serves_the_six_generated_cases() -> None:
    """A full-history request (`realtime_start = observation_start` = the
    series' first day) returns every CSV value with its own vintage: the six
    cases of the spec's "Fixture" paragraph."""
    source = FixtureRateSource.from_csv(_RATES_CSV)
    first = date(2016, 12, 23)
    served = source.observations("DTB3", first, first)
    by_day: dict[date, list[RateObservation]] = {}
    for record in served:
        by_day.setdefault(record.observation_date, []).append(record)
    # The series starts five sessions before the first fixture session.
    assert min(by_day) == first and first < date(2017, 1, 3)
    # (a) ordinary: D, vintage D+1, stamped close(D+2).
    (ordinary,) = by_day[date(2019, 5, 7)]
    assert ordinary.vintage_date == date(2019, 5, 8)
    assert rate_known_at(ordinary.vintage_date) == session_close(date(2019, 5, 9))
    # (b) a Friday vintage, stamped at Monday's close.
    (friday,) = by_day[date(2018, 3, 8)]
    assert friday.vintage_date == date(2018, 3, 9) and friday.vintage_date.weekday() == 4
    assert rate_known_at(friday.vintage_date) == session_close(date(2018, 3, 12))
    # (c) the exchange holiday: no observation.
    assert date(2018, 11, 22) not in by_day
    assert date(2018, 11, 21) in by_day and date(2018, 11, 23) in by_day
    # (d) the bank-holiday gap: no observation on either business day.
    assert date(2018, 10, 8) not in by_day and date(2018, 10, 9) not in by_day
    assert is_session(date(2018, 10, 8)) and is_session(date(2018, 10, 9))
    (before_gap,) = by_day[date(2018, 10, 5)]
    assert before_gap.vintage_date == date(2018, 10, 9)
    # (e) one observation, two vintages, two values; each stamped at its own
    # vintage's next close.
    first_value, revised = by_day[date(2019, 3, 12)]
    assert (first_value.vintage_date, revised.vintage_date) == (
        date(2019, 3, 13),
        date(2019, 3, 20),
    )
    assert first_value.value != revised.value
    assert rate_known_at(revised.vintage_date) == session_close(date(2019, 3, 21))
    assert [d for d, records in by_day.items() if len(records) > 1] == [date(2019, 3, 12)]
    # (f) a Saturday vintage, stamped at Monday's close.
    (saturday,) = by_day[date(2018, 6, 15)]
    assert saturday.vintage_date == date(2018, 6, 16) and saturday.vintage_date.weekday() == 5
    assert rate_known_at(saturday.vintage_date) == session_close(date(2018, 6, 18))


def test_observation_start_filters_by_observation_date() -> None:
    source = FixtureRateSource.from_csv(_RATES_CSV)
    served = source.observations("DTB3", date(2016, 12, 23), date(2020, 6, 1))
    assert served and min(r.observation_date for r in served) == date(2020, 6, 1)
    assert served == sorted(served, key=lambda r: (r.observation_date, r.vintage_date))


def test_an_unknown_series_returns_nothing() -> None:
    source = FixtureRateSource.from_csv(_RATES_CSV)
    assert source.observations("DGS10", date(2016, 12, 23), date(2016, 12, 23)) == []


def test_realtime_start_is_a_lower_bound_on_the_reported_vintage() -> None:
    """A value first served before `realtime_start` comes back with
    `realtime_start` as its vintage (FRED's bound), never earlier: a hole
    refetched late is stamped late."""
    source = FixtureRateSource(
        [
            _obs(date(2020, 1, 2), 1.52, date(2020, 1, 3)),
            _obs(date(2020, 1, 6), 1.50, date(2020, 1, 7)),
        ]
    )
    served = source.observations("DTB3", date(2020, 1, 6), date(2020, 1, 2))
    assert served == [
        _obs(date(2020, 1, 2), 1.52, date(2020, 1, 6)),
        _obs(date(2020, 1, 6), 1.50, date(2020, 1, 7)),
    ]


def test_a_value_superseded_before_realtime_start_is_not_returned() -> None:
    old = _obs(date(2019, 3, 12), 2.25, date(2019, 3, 13))
    new = _obs(date(2019, 3, 12), 2.27, date(2019, 3, 20))
    source = FixtureRateSource([new, old])
    # A window starting inside the first value's real-time period returns both.
    assert source.observations("DTB3", date(2019, 3, 19), date(2019, 3, 12)) == [
        _obs(date(2019, 3, 12), 2.25, date(2019, 3, 19)),
        new,
    ]
    # One starting on the revision's vintage returns only the revision.
    assert source.observations("DTB3", date(2019, 3, 20), date(2019, 3, 12)) == [new]
    # A full-history window returns both with their own vintages.
    assert source.observations("DTB3", date(2019, 3, 12), date(2019, 3, 12)) == [old, new]


def test_missing_values_are_never_records_and_are_counted_per_call() -> None:
    source = FixtureRateSource(
        [_obs(date(2020, 1, 2), 1.52, date(2020, 1, 3))],
        missing=[
            ("DTB3", date(2020, 1, 6)),
            ("DTB3", date(2019, 12, 31)),
            ("DGS10", date(2020, 1, 6)),
        ],
    )
    assert source.missing_values == 0
    served = source.observations("DTB3", date(2020, 1, 2), date(2020, 1, 2))
    assert [r.observation_date for r in served] == [date(2020, 1, 2)]
    assert source.missing_values == 1
    source.observations("DTB3", date(2019, 12, 1), date(2019, 12, 1))
    assert source.missing_values == 2


def test_the_fixture_source_refuses_inconsistent_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="share a vintage"):
        FixtureRateSource(
            [
                _obs(date(2020, 1, 2), 1.52, date(2020, 1, 3)),
                _obs(date(2020, 1, 2), 1.53, date(2020, 1, 3)),
            ]
        )
    with pytest.raises(ValueError, match="both a value and missing"):
        FixtureRateSource(
            [_obs(date(2020, 1, 2), 1.52, date(2020, 1, 3))], missing=[("DTB3", date(2020, 1, 2))]
        )
    bad = tmp_path / "rates.csv"
    bad.write_text("series,observation_date,value\nDTB3,2020-01-02,1.5\n")
    with pytest.raises(ValueError, match="vintage_date"):
        FixtureRateSource.from_csv(bad)
