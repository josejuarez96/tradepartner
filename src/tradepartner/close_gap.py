"""Stored daily close against the official close (#3077, #3053 item 11).

Measured 2026-11-06: Alpaca's SIP daily bar matches the official bar on open, high
and low for SPY on every session, but its close is not always the 4 pm consolidated
closing-auction price (20 of 2,731 sessions differ by more than 0.1 %, 15 of them in
March 2020, worst +2.87 % on 2020-03-12; on 2020-03-09/12/18 some 3-6 % of all names
differ by more than 0.5 %). That is the vendor's definition of the bar's close, not an
ingest bug (the feed is `sip`, the adjustment `raw`), so the store keeps the vendor
close and this check names the sessions where it strays: benchmark returns that read
those sessions carry the gap.

`close_gaps` is pure. The official closes come from a reference file the owner
supplies (`session,close` per symbol); vendor data is never stored in the repo.
Fail-closed: a reference session with no stored bar is a gap, and an empty
reference refuses rather than passing.
"""

from __future__ import annotations

import polars as pl

GAP_SCHEMA = {
    "session": pl.Date,
    "stored": pl.Float64,
    "official": pl.Float64,
    "gap": pl.Float64,
}


def close_gaps(stored: pl.DataFrame, official: pl.DataFrame, max_gap: float) -> pl.DataFrame:
    """Sessions whose stored close is more than `max_gap` (a fraction, 0.005 = 0.5 %)
    from the official close, or has no stored bar at all.

    `stored` and `official` carry `session` and `close`. `gap` is the signed
    `stored / official - 1` (null for a missing bar). Sorted by session."""
    if official.is_empty():
        raise ValueError("no official closes to compare: refusing to pass an empty reference")
    if max_gap < 0:
        raise ValueError(f"max_gap must not be negative, got {max_gap}")
    joined = official.select("session", pl.col("close").alias("official")).join(
        stored.select("session", pl.col("close").alias("stored")), on="session", how="left"
    )
    return (
        joined.with_columns((pl.col("stored") / pl.col("official") - 1.0).alias("gap"))
        .filter(pl.col("gap").is_null() | (pl.col("gap").abs() > max_gap))
        .select(list(GAP_SCHEMA))
        .sort("session")
    )
