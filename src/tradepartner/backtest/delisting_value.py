"""The delisting exit rule, shared by the backtest engine and the store provider.

One rule for trials and event studies (research-registry spec amendment 2026-11-16,
#3498; data-foundation amendment #2670, req 2; plan T328c, T410). It holds

- `ended_listings`: the ended-listing choice (the current listing row at a read, kept
  when `delisted`; a `transferred` listing is never an end);
- `delisting_exit_valuation` / `delisting_exit_factor`: the pure policy rule, from an
  ended listing, the reason rows and the merger rows to the exit's value;
- `merger_exit_frame`: the merger-exit frame construction `StoreProvider.merger_exits`
  serves (rows matched only by the acquirer dropped, the acquirer mapped through
  `live_chains` (#2949), forks refused, `acquirer_raw_close` joined from
  `prices_as_of(traded_only=True)` at the effective date).

The engine keeps its own arithmetic order (`shares * consideration`, `value * (1 +
shock)`), its short refusal and its counts; moving the rule here changed no number.
"""

from __future__ import annotations

import bisect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final, cast

import duckdb
import polars as pl

from tradepartner.backtest.provider import MERGER_EXIT_COLUMNS
from tradepartner.calendar import all_sessions
from tradepartner.config_backtest import DelistingPolicy
from tradepartner.store.asof import (
    live_chains,
    mergers_as_of,
    prices_as_of,
)
from tradepartner.store.delistings import DELISTED
from tradepartner.store.listing_selection import prefer_current_listing

#: The confirmed-failure reasons a policy shocks (spec amendment #2670, req 2).
FAILURE_REASONS: Final = frozenset({"performance_failure", "bankruptcy"})
MERGER_REASON: Final = "merger_acquisition"
UNKNOWN_REASON: Final = "unknown"
#: The listing columns a delisting exit reads from the current listing row.
LISTING_EXIT_FIELDS: Final = ("exchange", "delisting_form", "delisting_filed_at")
#: `merger_exit_frame`'s join key: the acquirer's id mapped through the chains known at `t`.
_ACQUIRER_KEY: Final = "_acquirer_key"


def ended_listings(listing_ends: pl.DataFrame, session: date) -> dict[str, dict[str, object]]:
    """Securities whose current listing at the read is `delisted`, each with that row's
    `end_session`, `exchange`, `delisting_form` and `delisting_filed_at` (None for a
    column the frame lacks).

    The current listing has the latest `valid_from` on or before `session`. On a
    same-day tie, a delisted line wins so a stale listed sibling cannot hide a real
    exit; otherwise a live line wins over a withdrawn line. A `transferred` listing is
    not an end (#555). Rows without `valid_from` are taken as they are."""
    dated = "valid_from" in listing_ends.columns
    current: dict[str, tuple[date | None, dict[str, object]]] = {}
    for row in listing_ends.iter_rows(named=True):
        valid_from = row["valid_from"] if dated else None
        if valid_from is not None and valid_from > session:
            continue
        held = current.get(row["security_id"])
        if prefer_current_listing(row, held[1] if held is not None else None, session):
            current[row["security_id"]] = (valid_from, row)
    return {
        sid: {
            "end_session": row["end_session"],
            **{name: row.get(name) for name in LISTING_EXIT_FIELDS},
        }
        for sid, (_, row) in current.items()
        if row["status"] == DELISTED
    }


def window_end(first: date, sessions: int) -> date:
    """The trading session `sessions` after `first` (`first` itself at 0)."""
    every = all_sessions()
    at = bisect.bisect_left(every, first)
    return every[min(at + sessions, len(every) - 1)]


@dataclass(frozen=True)
class ExitValuation:
    """How a delisting exit is valued: `per_share` is a matched merger's consideration
    per share (cash plus ratio times the acquirer's raw close), else None, and
    `multiplier` is `1 + shock` on a shocked exit and 1.0 otherwise (last close)."""

    per_share: float | None = None
    multiplier: float = 1.0


def delisting_exit_valuation(
    sid: str,
    listing: Mapping[str, object],
    reasons: Sequence[Mapping[str, object]],
    merger_rows: Sequence[Mapping[str, object]],
    final: date | None,
    terms: DelistingPolicy,
    policy: str,
    counts: dict[str, int],
) -> ExitValuation:
    """The valuation of `sid`'s delisting exit under the policy `terms` (plan T328c).

    `listing` is the ended listing (`ended_listings`), `reasons` and `merger_rows` the
    security's rows read at the cutoff, `final` its final session. The reason is the
    single reason row matching the listing's form, filing and exchange, else `unknown`;
    the merger row is the single row with `final <= effective_date <=` the window end.
    The outcome is counted in `counts`. Raises for a shock on an exchange outside the
    terms' map (a failed trial, never 0%)."""
    key = (listing.get("delisting_form"), listing.get("delisting_filed_at"))
    matched = [
        row
        for row in reasons
        if (row["delisting_form"], row["delisting_filed_at"]) == key
        and row["delisting_exchange"] == listing.get("exchange")
        and None not in key
    ]
    reason = str(matched[0]["reason"]) if len(matched) == 1 else UNKNOWN_REASON
    counts["n_reason_conflicts"] += len(matched) > 1
    counts["n_unknown_exits"] += reason == UNKNOWN_REASON
    candidates = (
        []
        if final is None
        else [
            row
            for row in merger_rows
            if final
            <= cast(date, row["effective_date"])
            <= window_end(final, terms.merger_window_sessions)
        ]
    )
    merger = candidates[0] if len(candidates) == 1 else None
    if reason == MERGER_REASON:
        cash = cast(float | None, merger["cash_per_share"]) if merger is not None else None
        ratio = cast(float | None, merger["share_ratio"]) if merger is not None else None
        close = cast(float | None, merger["acquirer_raw_close"]) if merger is not None else None
        if merger is None or (ratio is not None and close is None):
            counts["n_merger_fallbacks"] += 1
            counts["n_merger_conflicts"] += len(candidates) > 1
            return ExitValuation()
        counts["n_merger_consideration"] += 1
        stock = 0.0 if ratio is None else ratio * cast(float, close)
        return ExitValuation(per_share=(cash or 0.0) + stock)
    shocked = reason in FAILURE_REASONS or (
        terms.shock_unknown and reason == UNKNOWN_REASON and merger is None
    )
    if not shocked:
        return ExitValuation()
    exchange = listing.get("exchange")
    if exchange not in terms.shocks:
        raise ValueError(
            f"{sid}: listing exchange {exchange!r} is outside "
            f"backtest.delisting_failure_return {policy!r}'s exchange map "
            f"({', '.join(sorted(terms.shocks))}); the policy cannot value this exit"
        )
    counts["n_failure_shocks"] += 1
    return ExitValuation(multiplier=1 + terms.shocks[cast(str, exchange)])


def delisting_exit_factor(valuation: ExitValuation, raw_close: float) -> float:
    """The factor on the marked last-close value: a merger's consideration over the raw
    last close, else `1 + shock` (1.0 when the exit is not shocked). Numerator and
    denominator are both raw, so a dividend-adjusted caller does not double count."""
    if valuation.per_share is not None:
        return valuation.per_share / raw_close
    return valuation.multiplier


def short_cover_factor(valuation: ExitValuation, raw_close: float) -> float:
    """The factor on a short's marked last-close value: `max(f, 1)`, `f` being
    `delisting_exit_factor`. A failure shock or a take-under would be a gain to a short,
    which a backtest cannot bank on a cover not executable at the last close, so it
    credits nothing; a merger consideration above the last close is paid (backtest spec
    amendment #3504, item 10; ADR 0024 consequence 5). Only the engine's short branch
    calls it: the event-study caller of the shared rule is unchanged."""
    return max(delisting_exit_factor(valuation, raw_close), 1.0)


def short_gain_withheld(valuation: ExitValuation, raw_close: float) -> bool:
    """Whether a short's cover factor was lifted to 1: the exit's factor is below 1, a
    gain `short_cover_factor` did not credit (`n_short_failure_uncredited`)."""
    return delisting_exit_factor(valuation, raw_close) < 1.0


def merger_exit_frame(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    wanted: Sequence[str],
    refuse_forks_at: Callable[[datetime, Sequence[str]], None],
) -> pl.DataFrame:
    """The merger-exit frame at `t` for the acquiree ids `wanted`
    (`DataProvider.merger_exits`), sorted by security, effective date and source action.

    `refuse_forks_at(t, ids)` is the caller's fork refusal, called on the acquirees
    first and then on the acquirers."""
    refuse_forks_at(t, wanted)
    # `mergers_as_of` also keeps rows matched only by their acquirer; drop them.
    mergers = mergers_as_of(conn, t, list(wanted)).filter(pl.col("security_id").is_in(wanted))
    # `prices_as_of` keys a chained predecessor's bars under its successor (#821), so
    # read the acquirer's close under the id the chains known at `t` map it to (#2949).
    chains = dict(live_chains(conn, t))
    mergers = mergers.with_columns(
        pl.col("acquirer_security_id").replace(chains).alias(_ACQUIRER_KEY)
    )
    acquirers = sorted(set(mergers[_ACQUIRER_KEY].drop_nulls().to_list()))
    refuse_forks_at(t, [*acquirers, *mergers["acquirer_security_id"].drop_nulls()])
    # Bound the acquirers' bars to the earliest effective date: only those dates join.
    first = mergers["effective_date"].min()
    sessions_from = first if isinstance(first, date) else None
    closes = prices_as_of(conn, t, acquirers, traded_only=True, sessions_from=sessions_from).select(
        pl.col("security_id").alias(_ACQUIRER_KEY),
        pl.col("session").alias("effective_date"),
        pl.col("close").alias("acquirer_raw_close"),
    )
    return (
        mergers.join(closes, on=[_ACQUIRER_KEY, "effective_date"], how="left")
        .select(MERGER_EXIT_COLUMNS)
        .sort("security_id", "effective_date", "source_action_id")
    )


__all__ = [
    "ExitValuation",
    "delisting_exit_factor",
    "delisting_exit_valuation",
    "ended_listings",
    "merger_exit_frame",
    "short_cover_factor",
    "short_gain_withheld",
    "window_end",
]
