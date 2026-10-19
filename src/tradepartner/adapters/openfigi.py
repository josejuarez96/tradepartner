"""Pure parsing of current OpenFIGI mapping results for the health check."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FigiType:
    """One current OpenFIGI instrument description."""

    figi: str
    composite_figi: str
    share_class_figi: str
    security_type: str
    security_type2: str
    market_sector: str
    name: str
    exch_code: str


def parse_mapping(payload: list[Any]) -> list[FigiType | None]:
    """Select one US result per job; preserve unmapped jobs as None."""
    result: list[FigiType | None] = []
    for job in payload:
        data = job.get("data", []) if isinstance(job, dict) else []
        if not isinstance(data, list):
            raise ValueError("OpenFIGI mapping data is not a list")
        us = [x for x in data if isinstance(x, dict) and x.get("exchCode") == "US"]
        # Prefer the US composite, then a stable ordering of response fields.
        chosen = (
            min(
                us,
                key=lambda x: (
                    not (x.get("figi") and x.get("figi") == x.get("compositeFIGI")),
                    json.dumps(x, sort_keys=True, default=str),
                ),
            )
            if us
            else None
        )
        if chosen is None:
            result.append(None)
            continue
        result.append(
            FigiType(
                *(
                    str(chosen.get(k) or "")
                    for k in (
                        "figi",
                        "compositeFIGI",
                        "shareClassFIGI",
                        "securityType",
                        "securityType2",
                        "marketSector",
                        "name",
                        "exchCode",
                    )
                )
            )
        )
    return result
