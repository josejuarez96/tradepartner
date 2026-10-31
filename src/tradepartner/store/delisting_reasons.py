"""Point-in-time evidence about delisting episodes (plan T326).

Reason rows are independent of the Form 25 delisting and its derived
listing end. Consumers opt in to this read explicitly.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import duckdb
import polars as pl

from tradepartner.store.asof import _latest_as_of, _validate_t
from tradepartner.store.delistings import delistings_as_of

_EPISODE_KEY = (
    "security_id",
    "delisting_form",
    "delisting_class_title",
    "delisting_exchange",
    "delisting_filed_at",
)


def delisting_reasons_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Return reason revisions only for Form 25 episodes known at UTC instant `t`."""
    t = _validate_t(t)
    reasons = _latest_as_of(conn, "delisting_reasons", _EPISODE_KEY, t, security_ids)
    episodes = delistings_as_of(conn, t, security_ids).rename(
        {
            "form": "delisting_form",
            "class_title": "delisting_class_title",
            "exchange": "delisting_exchange",
            "filed_at": "delisting_filed_at",
        }
    )
    return reasons.join(episodes.select(_EPISODE_KEY), on=_EPISODE_KEY, how="semi")
