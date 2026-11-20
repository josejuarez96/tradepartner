"""`irrx_reversal`: announcement-adjusted, industry-relative one-month reversal, pure
(plan T415b; backtest spec amendment 2026-11-16 (#3504), items 4, 5 and 6).

For each name, the compounded return over the formation sessions, less its industry's
compounded return over the same sessions, with the name's own announcement sessions (and
the sessions on which too few rivals have a usable return) left out. The score is the
negative of that relative return, so the engine's top of the ranking is the biggest loser.

No I/O: the frames, the SICs, the event rows and `t` are arguments, as is every session
count, threshold and the SIC length (an AST check pins that the module holds no numeric
literal beyond the structural 0 and 1). Returns are handled as growth ratios
(`close / previous close`), which compound by product and need no unit.

**Input contracts.** `adjusted` and `raw` are `(security_id, session, close[, volume],
known_at)` frames of traded bars (`traded_only`, #787; `adjusted` already carries
dividends or not per `irrx.signal_total_return`, chosen by the reader). `sics` is the
provider's `sics(t, ids)` (already as of `t`). `events` has `security_id`, `form`,
`items`, `accepted_at` (the row's `known_at`) and `index_accepted_at`, the
`filing_index.first_known` stamp for the same `(cik, accession)` (null when the index has
no such row; that is a stamp mismatch).
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

import polars as pl

from tradepartner import calendar as _xnys
from tradepartner.backtest.announce import SessionCalendar, dropped_sessions, reaction_session
from tradepartner.config_backtest import IrrxConfig
from tradepartner.timeutil import ensure_tz_aware_utc

_NEW_YORK = ZoneInfo("America/New_York")

IrrxExclusion = Literal[
    "no_sic", "no_events", "stamp", "late_event", "thin_industry", "short_window", "adv"
]
#: The exclusion reasons in the order the first failing rule decides (spec item 6).
IRRX_EXCLUSIONS: tuple[IrrxExclusion, ...] = (
    "no_sic",
    "no_events",
    "stamp",
    "late_event",
    "thin_industry",
    "short_window",
    "adv",
)
_BAR_COLUMNS = ("security_id", "session", "close")
_EVENT_COLUMNS = ("security_id", "form", "items", "accepted_at", "index_accepted_at")


class IrrxCalendar(SessionCalendar, Protocol):
    """`SessionCalendar` plus the two lookups the formation window needs; the
    `tradepartner.calendar` module satisfies it."""

    def previous_session(self, day: date) -> date:
        """The last session strictly before `day`."""
        ...

    def last_completed_session(self, as_of: datetime) -> date:
        """The most recent session whose close is at or before `as_of`."""
        ...


@dataclass(frozen=True)
class IrrxSignal:
    """Scores of the ranked names and every other requested name under the reason that
    excluded it (spec item 6).

    `scores` is ordered by `security_id`; each `exclusions` tuple too, every reason
    present. `counts` holds the ten `trial_rebalance_counts` keys of item 6.
    """

    scores: dict[str, float]
    exclusions: dict[IrrxExclusion, tuple[str, ...]]
    counts: dict[str, int]

    @property
    def ranked(self) -> tuple[str, ...]:
        """Scored names by score descending, ties by `security_id` ascending (rule 5),
        the order the engine selects the long leg from."""
        return tuple(sorted(self.scores, key=lambda sid: (-self.scores[sid], sid)))


def _walk_back(end: date, count: int, calendar: IrrxCalendar) -> list[date]:
    """The `count` sessions ending at `end`, newest first, then the one before them."""
    days = [end]
    for _ in range(count):
        days.append(calendar.previous_session(days[-1]))
    return days


def _industry(sic: int, industry_digits: int, sic_digits: int) -> int:
    """The first `industry_digits` digits of the `sic_digits`-digit form (SIC 100 is
    industry 01, not 10), as an int."""
    text = str(sic).zfill(sic_digits)
    if sic < 0 or len(text) > sic_digits:
        raise ValueError(f"SIC {sic} is not a {sic_digits}-digit code")
    return int(text[:industry_digits])


def _visible(
    frame: pl.DataFrame, columns: tuple[str, ...], t: datetime, t_day: date
) -> pl.DataFrame:
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise ValueError(f"frame lacks columns {missing}")
    visible = frame.filter(pl.col("session") <= t_day)
    if "known_at" in frame.columns:
        visible = visible.filter(pl.col("known_at") <= t)
    return visible


def _growth(adjusted: pl.DataFrame, calendar: IrrxCalendar) -> dict[str, dict[date, float]]:
    """Each name's growth ratio on each session with a bar on it and on the previous
    session; a gap is a missing session, never a multi-day ratio."""
    if adjusted.select(pl.struct("security_id", "session").is_duplicated().any()).item():
        raise ValueError("adjusted frame has a duplicate (security_id, session) bar")
    closes: dict[tuple[str, date], float] = {}
    for sid, session, close in adjusted.select(_BAR_COLUMNS).iter_rows():
        if close is None or not (math.isfinite(close) and close > 0):
            raise ValueError(f"close for {sid} on {session} must be positive and finite: {close}")
        closes[(sid, session)] = close
    out: dict[str, dict[date, float]] = defaultdict(dict)
    for (sid, session), close in closes.items():
        previous = closes.get((sid, calendar.previous_session(session)))
        if previous is not None:
            out[sid][session] = close / previous
    return out


@dataclass(frozen=True)
class _Event:
    s0: date
    dropped: frozenset[date]
    stamp_ok: bool
    late: bool


def _events_by_name(
    events: pl.DataFrame,
    t: datetime,
    t_day: date,
    config: IrrxConfig,
    calendar: IrrxCalendar,
) -> dict[str, list[_Event]]:
    missing = [c for c in _EVENT_COLUMNS if c not in events.columns]
    if missing:
        raise ValueError(f"events frame lacks columns {missing}")
    cutoff = time.fromisoformat(config.dissemination_cutoff)
    out: dict[str, list[_Event]] = defaultdict(list)
    for sid, form, items, accepted_at, index_stamp in events.select(_EVENT_COLUMNS).iter_rows():
        if form != config.event_form:
            continue
        if config.event_item not in [code.strip() for code in items.split(",")]:
            continue
        accepted = ensure_tz_aware_utc(accepted_at, field_name="accepted_at")
        if accepted > t:  # rule 0: not known at t
            continue
        s0 = reaction_session(accepted, calendar)
        if s0 > t_day:
            continue
        late = s0 == t_day and accepted.astimezone(_NEW_YORK).time() > cutoff
        stamp_ok = index_stamp is not None and (
            ensure_tz_aware_utc(index_stamp, field_name="index_accepted_at") == accepted
        )
        window = dropped_sessions(s0, config.announce_post_sessions, calendar)
        out[sid].append(_Event(s0, frozenset(window), stamp_ok, late))
    return out


def irrx_reversal(
    adjusted: pl.DataFrame,
    raw: pl.DataFrame,
    sics: Mapping[str, int | None],
    events: pl.DataFrame,
    t: datetime,
    *,
    security_ids: Collection[str],
    config: IrrxConfig,
    sic_digits: int,
    calendar: IrrxCalendar = _xnys,
) -> IrrxSignal:
    """The `irrx` scores at read time `t` (spec item 6, rules 0 to 5).

    `t` is `close(T)` for a session T (a naive `t`, or one that is not a session close,
    raises `ValueError`). `security_ids` is the universe at `t`; `config` carries every
    `irrx.*` value; `sic_digits` is the SIC code length (4). `calendar` is the XNYS
    calendar (default `tradepartner.calendar`). Rows and events known after `t`, and
    bars after T, are dropped first. A name is excluded under the first of `no_sic`,
    `no_events`, `stamp`, `late_event`, `thin_industry`, `short_window`, `adv` it fails;
    an event accepted after `irrx.dissemination_cutoff` whose s0 is T is ignored
    everywhere (it drops no session and is no coverage). The benchmark set is the names
    with a SIC, event coverage, trusted stamps and at least one daily return; the scored
    set is those with a median dollar volume at the floor (item 3).
    """
    t = ensure_tz_aware_utc(t, field_name="t")
    t_day = calendar.last_completed_session(t)
    if t != calendar.session_close(t_day):
        raise ValueError(
            f"t {t.isoformat()} is not a session close; the last close at or before it "
            f"is {calendar.session_close(t_day).isoformat()} ({t_day.isoformat()})"
        )
    names = sorted(set(security_ids))
    universe = set(names)

    # Window sessions (rule 1): the lookback sessions ending at T; the walk's last
    # element is the base session before them, whose bar anchors the first return.
    walk = _walk_back(t_day, config.lookback_sessions, calendar)
    walk.pop()
    window = walk
    window_set = frozenset(window)
    coverage = _walk_back(t_day, config.event_coverage_sessions, calendar)
    coverage.pop()
    coverage_start = coverage[-1]
    adv_days = _walk_back(t_day, config.adv_sessions, calendar)
    adv_days.pop()

    growth = _growth(_visible(adjusted, _BAR_COLUMNS, t, t_day), calendar)
    by_name = _events_by_name(events, t, t_day, config, calendar)
    raw_visible = _visible(raw, (*_BAR_COLUMNS, "volume"), t, t_day)

    # Per-name event facts. Late events are ignored everywhere; of the rest, only those
    # that can matter (coverage window, or a dropped session inside the window).
    dropped: dict[str, frozenset[date]] = {}
    covered: set[str] = set()
    stamp_bad: set[str] = set()
    late: set[str] = set()
    for sid in names:
        kept_events = [e for e in by_name.get(sid, []) if not e.late]
        if any(e.late for e in by_name.get(sid, [])):
            late.add(sid)
        relevant = [e for e in kept_events if e.s0 >= coverage_start or e.dropped & window_set]
        if any(e.s0 >= coverage_start for e in kept_events):
            covered.add(sid)
        if any(not e.stamp_ok for e in relevant):
            stamp_bad.add(sid)
        dropped[sid] = frozenset().union(*(e.dropped for e in relevant)) & window_set

    industry: dict[str, int] = {}
    for sid in names:
        sic = sics.get(sid)
        if sic is not None:
            industry[sid] = _industry(sic, config.industry_digits, sic_digits)

    returns: dict[str, dict[date, float]] = {
        sid: {s: g for s, g in growth.get(sid, {}).items() if s in window_set} for sid in names
    }
    # Benchmark set (item 3): SIC, coverage, trusted stamps, a usable return panel.
    benchmark = {
        sid
        for sid in names
        if sid in industry and sid in covered and sid not in stamp_bad and returns[sid]
    }
    # Per session and industry: the sum and count of the contributing members' growth.
    totals: dict[tuple[int, date], tuple[float, int]] = {}
    for sid in benchmark:
        for s, g in returns[sid].items():
            if s in dropped[sid]:
                continue
            key = (industry[sid], s)
            total, count = totals.get(key, (0.0, 0))
            totals[key] = (total + g, count + 1)

    adv_ok = _adv_ok(raw_visible, universe, set(adv_days), config)
    scores: dict[str, float] = {}
    excluded: dict[IrrxExclusion, list[str]] = {r: [] for r in IRRX_EXCLUSIONS}
    for sid in names:
        if sid not in industry:
            excluded["no_sic"].append(sid)
        elif sid not in covered:
            excluded["no_events"].append(sid)
        elif sid in stamp_bad:
            excluded["stamp"].append(sid)
        elif sid in late:
            excluded["late_event"].append(sid)
        else:
            own_growth: list[float] = []
            peer_growth: list[float] = []
            valid = False
            for s in window:
                own = returns[sid].get(s)
                if own is None:
                    continue
                total, count = totals.get((industry[sid], s), (0.0, 0))
                if sid in benchmark and s not in dropped[sid]:
                    total, count = total - own, count - 1
                if count < config.min_industry_names:
                    continue
                valid = True
                if s in dropped[sid]:
                    continue
                own_growth.append(own)
                peer_growth.append(total / count)
            if not returns[sid]:  # no usable return panel: not a benchmark member either
                excluded["short_window"].append(sid)
            elif not valid:
                excluded["thin_industry"].append(sid)
            elif len(own_growth) < config.min_kept_sessions:
                excluded["short_window"].append(sid)
            elif not adv_ok.get(sid, False):
                excluded["adv"].append(sid)
            else:
                scores[sid] = -(math.prod(own_growth) - math.prod(peer_growth))
    counts = {"n_ranked": len(scores)}
    counts.update({f"n_excluded_{r}": len(excluded[r]) for r in _COUNT_ORDER})
    counts["n_adjusted"] = sum(bool(dropped[sid]) for sid in scores)
    counts["n_dropped_sessions"] = sum(len(dropped[sid]) for sid in scores)
    return IrrxSignal(
        scores=scores,
        exclusions={r: tuple(ids) for r, ids in excluded.items()},
        counts=counts,
    )


#: The order of the `n_excluded_*` keys in `counts` (spec item 6's list).
_COUNT_ORDER: tuple[IrrxExclusion, ...] = (
    "no_sic",
    "thin_industry",
    "no_events",
    "stamp",
    "late_event",
    "short_window",
    "adv",
)


def _adv_ok(
    raw: pl.DataFrame, universe: set[str], days: set[date], config: IrrxConfig
) -> dict[str, bool]:
    """Item 3's scored-set test: median raw close times volume over the traded bars in
    the `irrx.adv_sessions` window, with at least `irrx.min_adv_bars` bars."""
    values: dict[str, list[float]] = defaultdict(list)
    for sid, session, close, volume in raw.select(*_BAR_COLUMNS, "volume").iter_rows():
        if sid in universe and session in days and volume is not None and volume > 0:
            values[sid].append(close * volume)
    return {
        sid: len(v) >= config.min_adv_bars and statistics.median(v) >= config.min_dollar_volume
        for sid, v in values.items()
    }
