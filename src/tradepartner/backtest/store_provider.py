"""The store-backed `DataProvider` (backtest spec req 1, plan T38).

`StoreProvider` answers every `DataProvider` method from the as-of API
(`store.asof`, `store.master`, `store.delistings`, `universe.universe_as_of`,
`gap.survivorship_gap`), passing the trial's **frozen** `Settings` into every
call. One setting still escapes: `tradepartner.calendar` reads `calendar.*`
from the live environment (`get_settings()`), and the calendar bounds feed
the dividend adjustment and the gap's session counts. Until the calendar
takes settings, construction refuses frozen settings whose `calendar`
differs from the live one, so a changed calendar cannot silently change a
run; it fails it instead. It never touches an
adapter: nothing under `backtest/` imports `tradepartner.adapters` (tested).
Reads are restricted to the ids the engine passes (members, holdings and
benchmarks), never the whole store.

**One short-lived read-only connection per step.** A step is one read time
`t` (the engine reads everything for step i at close(T_{i+1})). The first
read at a new `t` closes the previous step's connection and opens a new one
from `connect`; `end_step()` releases the current one early, and `close()`
(or leaving the `with` block) releases the last. So a long run never holds
the store between steps. If a writer (the ingest job) holds the lock when a
step opens, the open is retried until `store.lock_retry_seconds` of the
frozen settings has passed, so a write between steps delays the next step
instead of failing it.

**The handle.** Construction takes a `TrialHandle` (a plain id is refused:
ADR 0005, no id, no run) and checks it once against `registry_connect`
(default `connect`, with the same lock retry): the handle's `trials` row
must be in that store and have no result row yet. T40
passes a truncated data store as `connect` and the untruncated fixture
store as `registry_connect`.

**Frames** are the as-of functions' own frames, unchanged. Two reads have no
single as-of function and are built from `store.asof` reads:

- `late_dividends(t_prev, t, ids)`: dividend keys known at `t` but not at
  `t_prev`, with an ex-date on or before `t_prev`'s session, at their latest
  revision as of `t` (`security_id`, `ex_date`, `ratio_or_amount`,
  `known_at`). A revision of a dividend already known at `t_prev` is not late.
- `static_listing_count(t, ids)`: ids whose current listing at `t`'s session
  (latest `valid_from` on or before it, among rows known at `t`) is a
  `snapshot_static` row.

`benchmark_ids(t)` maps each benchmark security known at `t`
(`securities.benchmark`) to the ticker of its current listing (`SPY`,
`MTUM`), keeping only the frozen `benchmarks` names.

A security's **current listing** is the one with the latest `valid_from` on
or before `t`'s session; between two rows with the same `valid_from` the
first in `listings_as_of` order wins, the same rule `universe_as_of` uses,
so the static count and benchmark tickers agree with the universe.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, ExitStack
from datetime import date, datetime
from types import TracebackType
from typing import Any, Final, Self

import duckdb
import polars as pl

from tradepartner import gap as gap_module
from tradepartner.backtest.provider import GapReading, check_t
from tradepartner.calendar import last_completed_session
from tradepartner.config import Settings, get_settings
from tradepartner.store import registry
from tradepartner.store.asof import (
    _latest_as_of,
    adjusted_prices_as_of,
    dropped_dividends_as_of,
    listings_as_of,
    prices_as_of,
)
from tradepartner.store.db import StoreLockedError
from tradepartner.store.delistings import listing_ends_as_of
from tradepartner.store.master import securities_as_of
from tradepartner.universe import Universe, universe_as_of

Connect = Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]

_ACTION_KEY: Final = ("security_id", "action_type", "ex_date")
_LATE_COLUMNS: Final = ("security_id", "ex_date", "ratio_or_amount", "known_at")
_RETRY_POLL_SECONDS: Final = 0.25


def _ids(ids: Sequence[str]) -> list[str]:
    if isinstance(ids, str) or not isinstance(ids, Sequence):
        raise TypeError(f"ids must be a sequence of security_id strings, got {ids!r}")
    return list(ids)


class StoreProvider:
    """A `DataProvider` over the store; see the module docstring."""

    def __init__(
        self,
        connect: Connect,
        handle: registry.TrialHandle,
        frozen_settings: Settings,
        registry_connect: Connect | None = None,
    ) -> None:
        if not isinstance(handle, registry.TrialHandle):
            raise TypeError(
                f"a StoreProvider needs a TrialHandle from registry.open_trial, got {handle!r}"
            )
        live_calendar = get_settings().calendar
        if frozen_settings.calendar != live_calendar:
            raise ValueError(
                f"the live calendar settings ({live_calendar!r}) differ from the trial's frozen "
                f"ones ({frozen_settings.calendar!r}); tradepartner.calendar reads the live "
                "ones, so this run would not use its frozen calendar"
            )
        self._connect = connect
        self.handle = handle
        self.settings = frozen_settings
        stack, conn = self._open(registry_connect or connect)
        with stack:
            registry._check_open(conn, handle)
        self._step: ExitStack | None = None
        self._step_t: datetime | None = None
        self._conn: duckdb.DuckDBPyConnection | None = None

    # --- connections ---------------------------------------------------------

    def _open(self, connect: Connect | None = None) -> tuple[ExitStack, duckdb.DuckDBPyConnection]:
        """A connection from `connect` (default the step factory), retried
        while a writer holds the lock, up to `store.lock_retry_seconds`."""
        factory = connect or self._connect
        deadline = time.monotonic() + self.settings.store.lock_retry_seconds
        while True:
            stack = ExitStack()
            try:
                return stack, stack.enter_context(factory())
            except StoreLockedError:
                stack.close()
                if time.monotonic() >= deadline:
                    raise
                time.sleep(_RETRY_POLL_SECONDS)

    def _at(self, t: datetime) -> duckdb.DuckDBPyConnection:
        """The connection of the step reading at `t`, opening it if `t` is new."""
        if self._conn is None or self._step_t != t:
            self.end_step()
            self._step, self._conn = self._open()
            self._step_t = t
        return self._conn

    def end_step(self) -> None:
        """Close the current step's connection (if any); the next read opens one."""
        step, self._step, self._conn, self._step_t = self._step, None, None, None
        if step is not None:
            step.close()

    def close(self) -> None:
        """Release the store: the same as `end_step`."""
        self.end_step()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # --- DataProvider ------------------------------------------------------------

    def universe(self, t: datetime) -> Universe:
        t = check_t(t)
        return universe_as_of(self._at(t), t, self.settings)

    def adjusted_prices(
        self, t: datetime, ids: Sequence[str], include_dividends: bool
    ) -> pl.DataFrame:
        t, wanted = check_t(t), _ids(ids)
        return adjusted_prices_as_of(
            self._at(t), t, wanted, include_dividends=include_dividends, settings=self.settings
        )

    def raw_prices(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        t, wanted = check_t(t), _ids(ids)
        return prices_as_of(self._at(t), t, wanted)

    def listing_ends(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        t, wanted = check_t(t), _ids(ids)
        return listing_ends_as_of(self._at(t), t, self.settings, wanted)

    def benchmark_ids(self, t: datetime) -> Mapping[str, str]:
        t = check_t(t)
        conn = self._at(t)
        benchmarks = securities_as_of(conn, t).filter(pl.col("benchmark"))["security_id"].to_list()
        names = set(self.settings.benchmarks)
        out: dict[str, str] = {}
        for row in self._current_listings(conn, t, benchmarks).values():
            if row["ticker"] in names:
                out[row["ticker"]] = row["security_id"]
        return dict(sorted(out.items()))

    def survivorship_gap(self, t: datetime) -> GapReading:
        t = check_t(t)
        reading = gap_module.survivorship_gap(self._at(t), t, self.settings)
        return GapReading(count_share=reading.count_share, size_share=reading.size_share)

    def dropped_dividends(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        t, wanted = check_t(t), _ids(ids)
        return dropped_dividends_as_of(self._at(t), t, wanted, settings=self.settings)

    def late_dividends(self, t_prev: datetime, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        t, t_prev, wanted = check_t(t), check_t(t_prev, name="t_prev"), _ids(ids)
        if t_prev >= t:
            raise ValueError(f"t_prev {t_prev.isoformat()} is not before t {t.isoformat()}")
        conn = self._at(t)
        prev_session = last_completed_session(t_prev)

        def dividends(at: datetime) -> pl.DataFrame:
            actions = _latest_as_of(conn, "corporate_actions", _ACTION_KEY, at, wanted)
            return actions.filter(pl.col("action_type") == "dividend")

        known_before = dividends(t_prev).select("security_id", "ex_date")
        late = (
            dividends(t)
            .join(known_before, on=["security_id", "ex_date"], how="anti")
            .filter(pl.col("ex_date") <= prev_session)
        )
        return late.select(_LATE_COLUMNS).sort("security_id", "ex_date")

    def static_listing_count(self, t: datetime, ids: Sequence[str]) -> int:
        t, wanted = check_t(t), _ids(ids)
        current = self._current_listings(self._at(t), t, wanted)
        return sum(1 for row in current.values() if row["provenance"] == "snapshot_static")

    # --- helpers --------------------------------------------------------------------

    @staticmethod
    def _current_listings(
        conn: duckdb.DuckDBPyConnection, t: datetime, ids: Sequence[str]
    ) -> dict[str, dict[str, Any]]:
        """Each id's listing with the latest `valid_from` on or before `t`'s
        session, among rows known at `t`."""
        session: date = last_completed_session(t)
        current: dict[str, dict[str, Any]] = {}
        for row in listings_as_of(conn, t, ids).iter_rows(named=True):
            if row["valid_from"] > session:
                continue
            seen = current.get(row["security_id"])
            if seen is None or row["valid_from"] > seen["valid_from"]:
                current[row["security_id"]] = row
        return current
