"""FRED output_type=1 parser and RateSource adapter."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx

from tradepartner.adapters import fred_raw
from tradepartner.adapters.rates import RateObservation, RateSource
from tradepartner.config import Settings


def parse_observations(payload: dict[str, Any], series: str) -> tuple[list[RateObservation], int]:
    """Parse one output_type=1 payload, returning records and `.` count."""
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ValueError("FRED response lacks observations")
    rows: list[RateObservation] = []
    missing = 0
    for record in observations:
        if not isinstance(record, dict):
            raise ValueError("FRED observation must be an object")
        try:
            observation_date = date.fromisoformat(record["date"])
            vintage_date = date.fromisoformat(record["realtime_start"])
            value_text = record["value"]
            if value_text == ".":
                missing += 1
                continue
            value = float(value_text)
            rows.append(RateObservation(series, observation_date, value, vintage_date))
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid FRED observation") from None
    return rows, missing


class FredRateSource(RateSource):
    """Live FRED source, with a replaceable client for offline tests."""

    name = "fred"

    def __init__(
        self, *, settings: Settings | None = None, client: httpx.Client | None = None
    ) -> None:
        super().__init__()
        self.settings = settings
        self.client = client

    def observations(
        self, series: str, realtime_start: date, observation_start: date
    ) -> list[RateObservation]:
        payload = fred_raw.observations(
            series, realtime_start, observation_start, settings=self.settings, client=self.client
        )
        rows, self.missing_values = parse_observations(payload, series)
        rows.sort(key=lambda row: (row.observation_date, row.vintage_date))
        return rows
