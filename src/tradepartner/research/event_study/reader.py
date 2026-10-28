"""Bounded, read-only store access for event studies."""

from __future__ import annotations

from datetime import UTC, date, datetime

import polars as pl

from tradepartner.calendar import is_session, previous_session, session_close
from tradepartner.config import Settings
from tradepartner.store.asof import adjusted_prices_as_of, facts_as_of, listings_as_of, prices_as_of
from tradepartner.store.db import open_read_only
from tradepartner.store.master import securities_as_of


class EventStudyReader:
    """Read market data only at the registered window's closing instant or earlier."""

    def __init__(self, settings: Settings, window_end: date) -> None:
        self.settings = settings
        closing_session = window_end if is_session(window_end) else previous_session(window_end)
        self.cutoff = session_close(closing_session)
        self.max_read_t: datetime | None = None

    def _record(self, t: datetime) -> datetime:
        if t.tzinfo is None or t.utcoffset() is None:
            raise ValueError("known_at must be timezone-aware UTC")
        t = t.astimezone(UTC)
        if t > self.cutoff:
            raise ValueError("read after window end")
        if self.max_read_t is None or t > self.max_read_t:
            self.max_read_t = t
        return t

    def benchmark(self, symbol: str, *, start: date) -> str:
        """Resolve a benchmark identity within the study window."""
        self._record(self.cutoff)
        with open_read_only(self.settings) as conn:
            securities = securities_as_of(conn, self.cutoff).filter(pl.col("benchmark"))
            listings = listings_as_of(conn, self.cutoff).filter(
                (pl.col("ticker").str.to_uppercase() == symbol.strip().upper())
                & (pl.col("valid_from") <= start)
            )
        ids: set[str] = {str(sid) for sid in securities["security_id"].to_list()} & {
            str(sid) for sid in listings["security_id"].to_list()
        }
        if len(ids) != 1 or start > self.cutoff.date():
            raise ValueError(f"benchmark {symbol!r} is missing or ambiguous at window end")
        return next(iter(ids))

    def bars(
        self, security_ids: list[str], *, sessions_from: date
    ) -> tuple[pl.DataFrame, pl.DataFrame]:
        """Return dividend-adjusted and raw traded bars at the window-end cut."""
        t = self._record(self.cutoff)
        with open_read_only(self.settings) as conn:
            adjusted = adjusted_prices_as_of(
                conn,
                t,
                security_ids,
                include_dividends=True,
                settings=self.settings,
                traded_only=True,
                sessions_from=sessions_from,
            )
            raw = prices_as_of(
                conn,
                t,
                security_ids,
                traded_only=True,
                sessions_from=sessions_from,
            )
        return adjusted, raw

    def shares(
        self,
        security_id: str,
        event_known_at: datetime,
        *,
        fact_name: str,
        facts_read: str = "event_known_at",
    ) -> list[dict[str, object]]:
        """Return company-level shares known at the event or declared window cut."""
        if facts_read not in {"event_known_at", "window_end"}:
            raise ValueError("invalid facts_read mode")
        event_t = self._record(event_known_at)
        t = self._record(self.cutoff if facts_read == "window_end" else event_t)
        with open_read_only(self.settings) as conn:
            frame = facts_as_of(conn, t, [security_id])
        return (
            frame.filter((pl.col("fact_name") == fact_name) & (pl.col("class_member") == ""))
            .sort("known_at")
            .to_dicts()
        )
