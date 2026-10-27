"""The known-answer index check's store reads (backtest spec amendment #1598, T245).

`run_index_check` holds the whole universe with no signal for each month of a window
and compares it with SPY and RSP, using `index_check`'s arithmetic (T244) over reads
from the store. **It is never a trial**: it opens no handle, calls no registry writer
and writes nothing. It reads through `store.asof`, `universe.universe_as_of`,
`store.delistings` and `store.benchmarks`, on a short-lived read-only connection per
month (`store.db.open_read_only`, the provider's own pattern), and a preflight
connection for the refusals, the protected edge and the header. The only reads it takes
from `store.registry` and `store.lab_registry` are `development_boundary`,
`data_vintage` and `family_rules` (an AST test pins this), and it imports nothing from
`backtest.engine` or `backtest.holdout`.

**One month** (item 1): the members at t_{m-1} (`universe_as_of`, live `Settings`), one
marking frame at t_m for the members and both benchmarks (`adjusted_prices_as_of`,
dividends in, `traded_only`, the spin-off key from `adjust.apply_spin_offs`), the
benchmarks resolved with `check_only=True`. A member's entry mark is its latest traded
bar at or before T_{m-1} and its end mark its latest at or before T_m, so a listing that
ends inside the month ends at its last traded close. A benchmark with no traded bar on
T_{m-1} or on T_m stops the run (`missing_benchmark_bar <symbol> <session>`); the
check never carries a stale benchmark mark. Every other read of a month is at t_m: the
flags of item 4 come from actions, listing ends and `dropped_dividends_as_of` known at
t_m, so a row planted after t_m changes nothing for the month.

**Protected months** (item 5): a month whose T_m is after the edge E carries gaps and
counts only. Its name rows drop r_j (`index_check.name_row`) and its row types have no
level field. The levels exist only in memory, in `MonthCalc`, and never reach a report
row.

**Refusals** (item 6, before any month is read): `IndexCheckRefused` with the code
`refused_window` or `refused_rank_by`, or `benchmark_identity` for a
`BenchmarkIdentityError` on SPY or RSP. A month that fails after reading has begun
raises `IndexCheckFailed` (`missing_benchmark_bar`, or the `ValueError` `universe_as_of`
raises).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

import duckdb
import polars as pl

from tradepartner.backtest import index_check as ic
from tradepartner.calendar import all_sessions, last_session_of_month, session_close
from tradepartner.config import Settings
from tradepartner.store.asof import (
    adjusted_prices_as_of,
    dropped_dividends_as_of,
    live_actions_as_of,
)
from tradepartner.store.benchmarks import BenchmarkIdentityError, benchmark_security_ids
from tradepartner.store.db import open_read_only
from tradepartner.store.delistings import DELISTED, listing_ends_as_of
from tradepartner.store.lab_registry import family_rules
from tradepartner.store.lab_schema import is_lab_initialised
from tradepartner.store.registry import data_vintage, development_boundary
from tradepartner.universe import universe_as_of

Connect = Callable[[Settings], AbstractContextManager[duckdb.DuckDBPyConnection]]

#: The registry's oracle family name (`registry.ORACLE_FAMILY`; a test pins the
#: equality). Its holdout never moves the edge.
ORACLE_FAMILY: Final = "oracle"
PROTECTED_LINE: Final = (
    "protected months show gaps only (owner, 2026-10-12, #1598); not a trial, nothing written"
)
CAP_CAVEAT: Final = (
    "caps rest on cover-page shares, not float-adjusted: a known source of cap-weight "
    "tracking error (#1598, first pass)"
)
#: The census and flag labels, in print order.
FLAG_NAMES: Final = (
    "missing_bars",
    "no_entry_bar",
    "split",
    "dividend",
    "dup_dividend",
    "dropped_dividend",
    "spin_off",
    "delisted",
    "extreme",
)
EQUAL: Final = "equal"
CAP: Final = "cap"
_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class IndexCheckRefused(Exception):
    """A refusal before any month is read (exit 2): `code` is `refused_window`,
    `refused_rank_by` or `benchmark_identity`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class IndexCheckFailed(Exception):
    """A month failed after reading began (exit 1)."""


# ---- the window -----------------------------------------------------------------------


@dataclass(frozen=True)
class MonthSpec:
    """One month's two instants: T_{m-1}, T_m and their closes."""

    month: str
    prev_session: date
    session: date
    t_prev: datetime
    t: datetime


def _parse_month(flag: str, value: str) -> tuple[int, int]:
    found = _MONTH.match(value)
    if found is None:
        raise IndexCheckRefused("refused_window", f"{flag} must be YYYY-MM, got {value!r}")
    return int(found.group(1)), int(found.group(2))


def _previous(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def window_months(start: str, end: str, now: datetime) -> list[MonthSpec]:
    """The months `start`..`end` (YYYY-MM) with their instants. Raises
    `IndexCheckRefused("refused_window")` for a bad format, an end before the start, a
    start whose T_{m-1} is before the calendar's first session, or an end month not
    completed at `now` (its T_m close after `now`)."""
    (y0, m0), (y1, m1) = _parse_month("--start", start), _parse_month("--end", end)
    if (y1, m1) < (y0, m0):
        raise IndexCheckRefused("refused_window", f"end {end} is before start {start}")
    first = all_sessions()[0]
    specs: list[MonthSpec] = []
    year, month = y0, m0
    while (year, month) <= (y1, m1):
        py, pm = _previous(year, month)
        try:
            prev_session = last_session_of_month(py, pm)
            session = last_session_of_month(year, month)
        except ValueError as exc:
            raise IndexCheckRefused("refused_window", f"{year:04d}-{month:02d}: {exc}") from None
        if prev_session < first:
            raise IndexCheckRefused(
                "refused_window",
                f"{year:04d}-{month:02d}: T_(m-1) {prev_session} is before the calendar's "
                f"first session {first}",
            )
        specs.append(
            MonthSpec(
                f"{year:04d}-{month:02d}",
                prev_session,
                session,
                session_close(prev_session),
                session_close(session),
            )
        )
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    if specs[-1].t > now:
        raise IndexCheckRefused(
            "refused_window",
            f"end month {specs[-1].month} is not completed at {now.isoformat()} "
            f"(its last close is {specs[-1].t.isoformat()})",
        )
    return specs


# ---- the report -----------------------------------------------------------------------


@dataclass(frozen=True)
class ListedName:
    """A listed name of a worst month: its row (no r_j when protected) and its flags."""

    row: ic.NameRow
    flags: tuple[str, ...]


@dataclass(frozen=True)
class WorstMonth:
    """One of the worst months of a weighting, with the names behind it."""

    month: str
    gap: float
    names: tuple[ListedName, ...]


@dataclass(frozen=True)
class ReportHeader:
    """What the run read and under which settings."""

    store_path: str
    schema_version: int | None
    data_vintage: datetime | None
    start_month: str
    end_month: str
    edge: date | None
    edge_source: str | None
    universe: dict[str, Any]
    apply_spin_offs: bool
    equal_benchmark: str
    cap_benchmark: str


@dataclass(frozen=True)
class IndexCheckReport:
    """The masked report: no protected row carries a level."""

    header: ReportHeader
    years: tuple[ic.YearRow, ...]
    year_members: dict[int, int]
    zones: tuple[ic.YearRow, ...]
    months: tuple[ic.MonthRow, ...]
    census: dict[str, dict[str, int]]
    worst: dict[str, tuple[WorstMonth, ...]]


@dataclass(frozen=True)
class _MonthDetail:
    calc: ic.MonthCalc  # in memory only: it holds the levels of a protected month
    census: dict[str, int]
    listed: dict[str, tuple[ListedName, ...]]


def report_dict(report: IndexCheckReport) -> dict[str, Any]:
    """The report as a JSON-ready dict: each row with exactly its type's fields."""
    head = report.header
    return {
        "header": {
            "store_path": head.store_path,
            "schema_version": head.schema_version,
            "data_vintage": head.data_vintage.isoformat() if head.data_vintage else None,
            "start_month": head.start_month,
            "end_month": head.end_month,
            "edge": head.edge.isoformat() if head.edge else None,
            "edge_source": head.edge_source,
            "universe": head.universe,
            "apply_spin_offs": head.apply_spin_offs,
            "equal_benchmark": head.equal_benchmark,
            "cap_benchmark": head.cap_benchmark,
            "protected_line": PROTECTED_LINE,
            "cap_caveat": CAP_CAVEAT,
        },
        "years": [ic.row_dict(r) for r in report.years],
        "year_members": {str(y): n for y, n in report.year_members.items()},
        "zones": [ic.row_dict(r) for r in report.zones],
        "months": [ic.row_dict(r) | {"census": report.census[r.month]} for r in report.months],
        "worst": {
            book: [
                {
                    "month": w.month,
                    "gap": w.gap,
                    "names": [ic.row_dict(n.row) | {"flags": list(n.flags)} for n in w.names],
                }
                for w in worst
            ]
            for book, worst in report.worst.items()
        },
    }


# ---- the preflight --------------------------------------------------------------------


def _schema_version(conn: duckdb.DuckDBPyConnection) -> int | None:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    except duckdb.Error:
        return None
    return int(row[0]) if row is not None and row[0] is not None else None


def _holdout_starts(conn: duckdb.DuckDBPyConnection) -> list[date]:
    """`holdout_start` of every registered non-oracle family's rules (`family_rules`)."""
    if not is_lab_initialised(conn):
        return []
    names = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT family FROM family_rules WHERE family <> ? ORDER BY family",
            [ORACLE_FAMILY],
        ).fetchall()
    ]
    starts: list[date] = []
    for name in names:
        rules = family_rules(conn, name)
        if rules is not None:
            starts.append(rules.holdout_start)
    return starts


def _preflight(
    settings: Settings, specs: Sequence[MonthSpec], connect: Connect
) -> tuple[ic.ProtectedEdge, int | None, datetime | None]:
    """The edge, the schema version and the data vintage at close(T_end), on one
    read-only connection; the benchmark identities are checked on it too."""
    last = specs[-1]
    cfg = settings.index_check
    with connect(settings) as conn:
        try:
            benchmark_security_ids(
                conn,
                [cfg.equal_benchmark, cfg.cap_benchmark],
                start=specs[0].prev_session,
                through=last.session,
                check_only=True,
            )
        except BenchmarkIdentityError as exc:
            raise IndexCheckRefused("benchmark_identity", str(exc)) from None
        found = development_boundary(conn)
        edge = ic.protected_edge(found.boundary if found else None, _holdout_starts(conn))
        vintage = data_vintage(conn, last.t, spin_offs=settings.adjust.apply_spin_offs)
        return edge, _schema_version(conn), vintage


# ---- one month ------------------------------------------------------------------------


def _closes(frame: pl.DataFrame) -> dict[str, dict[date, float]]:
    out: dict[str, dict[date, float]] = {}
    for sid, session, close in frame.select("security_id", "session", "close").iter_rows():
        out.setdefault(sid, {})[session] = close
    return out


def _fmt(x: float) -> str:
    return format(x, "g")


def _flags(
    spec: MonthSpec,
    sid: str,
    *,
    bars: Mapping[date, float],
    no_entry: bool,
    r: float,
    extreme: float,
    sessions: Sequence[date],
    listing: Mapping[str, Any] | None,
    actions: pl.DataFrame,
    dropped: pl.DataFrame,
) -> list[str]:
    """The flags of item 4 for one member, read as of t_m for (T_{m-1}, T_m]."""
    flags: list[str] = []
    ended = listing is not None and listing["status"] == DELISTED
    end_session: date | None = listing["end_session"] if listing is not None else None
    owed_through = spec.session
    if ended:
        owed_through = end_session if end_session is not None else spec.prev_session
    since = listing["valid_from"] if listing is not None else spec.prev_session
    missing = sum(1 for s in sessions if s <= owed_through and s >= since and s not in bars)
    if missing:
        flags.append(f"missing_bars={missing}")
    if no_entry:
        flags.append("no_entry_bar")
    in_month = (pl.col("ex_date") > spec.prev_session) & (pl.col("ex_date") <= spec.session)
    own = actions.filter((pl.col("security_id") == sid) & in_month)
    for ratio in own.filter(pl.col("action_type") == "split")["ratio_or_amount"].to_list():
        flags.append(f"split={_fmt(ratio)}")
    divs = own.filter(pl.col("action_type") == "dividend").sort("ex_date", "ratio_or_amount")
    amounts = list(dict.fromkeys(divs["ratio_or_amount"].to_list()))
    flags.extend(f"dividend={_fmt(a)}" for a in amounts)
    pairs: Counter[tuple[date, float]] = Counter()
    for ex_date, amount, _source in set(
        divs.select("ex_date", "ratio_or_amount", "source_action_id").iter_rows()
    ):
        pairs[(ex_date, amount)] += 1
    if any(n >= 2 for n in pairs.values()):
        flags.append("dup_dividend")
    if dropped.filter(
        (pl.col("security_id") == sid)
        & (pl.col("ex_date") > spec.prev_session)
        & (pl.col("ex_date") <= spec.session)
    ).height:
        flags.append("dropped_dividend")
    if actions.filter(
        (pl.col("action_type") == "spinoff") & (pl.col("source_action_id") == sid) & in_month
    ).height:
        flags.append("spin_off")
    if ended and end_session is not None and spec.prev_session < end_session <= spec.session:
        flags.append("delisted")
    if not no_entry and abs(r) >= extreme:
        flags.append("extreme")
    order = {name: i for i, name in enumerate(FLAG_NAMES)}
    return sorted(flags, key=lambda f: (order[f.split("=")[0]], f))


def _current_listings(ends: pl.DataFrame, session: date) -> dict[str, Mapping[str, Any]]:
    """Each security's listing row with the latest `valid_from` on or before T_m."""
    current: dict[str, Mapping[str, Any]] = {}
    for row in ends.iter_rows(named=True):
        if row["valid_from"] > session:
            continue
        seen = current.get(row["security_id"])
        if seen is None or row["valid_from"] > seen["valid_from"]:
            current[row["security_id"]] = row
    return current


def _month(
    settings: Settings, spec: MonthSpec, edge: ic.ProtectedEdge, connect: Connect
) -> _MonthDetail:
    cfg = settings.index_check
    protected = ic.is_protected(spec.session, edge)
    with connect(settings) as conn:
        members = universe_as_of(conn, spec.t_prev, settings).members
        if members.height == 0:
            raise IndexCheckFailed(f"{spec.month}: no_members at {spec.prev_session}")
        member_ids = members["security_id"].to_list()
        tickers = dict(members.select("security_id", "ticker").iter_rows())
        bench = benchmark_security_ids(
            conn,
            [cfg.equal_benchmark, cfg.cap_benchmark],
            start=spec.prev_session,
            through=spec.session,
            check_only=True,
        )
        frame_args: dict[str, Any] = {
            "include_dividends": True,
            "include_spin_offs": settings.adjust.apply_spin_offs,
            "settings": settings,
            "traded_only": True,
        }
        closes = _closes(
            adjusted_prices_as_of(
                conn,
                spec.t,
                [*member_ids, *bench.values()],
                sessions_from=spec.prev_session,
                **frame_args,
            )
        )
        marks: dict[str, tuple[float, float]] = {}
        for symbol in (cfg.equal_benchmark, cfg.cap_benchmark):
            bars = closes.get(bench[symbol], {})
            for day in (spec.prev_session, spec.session):
                if day not in bars:
                    raise IndexCheckFailed(f"missing_benchmark_bar {symbol} {day.isoformat()}")
            marks[symbol] = (bars[spec.prev_session], bars[spec.session])
        # A member's entry bar is its latest traded bar at or before T_{m-1}: the one on
        # T_{m-1} (in the frame), else an earlier one, read for those names alone.
        stale = [s for s in member_ids if spec.prev_session not in closes.get(s, {})]
        earlier: dict[str, dict[date, float]] = {}
        if stale:
            earlier = _closes(adjusted_prices_as_of(conn, spec.t, stale, **frame_args))
        entry: dict[str, float | None] = {}
        end: dict[str, float | None] = {}
        for sid in member_ids:
            bars = closes.get(sid, {})
            if spec.prev_session in bars:
                entry[sid] = bars[spec.prev_session]
            else:
                before = [d for d in earlier.get(sid, {}) if d <= spec.prev_session]
                entry[sid] = earlier[sid][max(before)] if before else None
            upto = [d for d in bars if d <= spec.session]
            end[sid] = bars[max(upto)] if upto else entry[sid]
        ends = listing_ends_as_of(conn, spec.t, settings, member_ids)
        actions = live_actions_as_of(conn, spec.t)
        dropped = dropped_dividends_as_of(conn, spec.t, member_ids, settings=settings)
    returns: dict[str, float] = {}
    no_entry: dict[str, bool] = {}
    try:
        for sid in member_ids:
            returns[sid], no_entry[sid] = ic.name_return(entry[sid], end[sid])
        eq_w = ic.equal_weights(member_ids)
        cw, n_no_cap = ic.cap_weights(dict(members.select("security_id", "market_cap").iter_rows()))
        if not cw:
            raise IndexCheckFailed(f"{spec.month}: no_cap_members at {spec.prev_session}")
        r_eq = marks[cfg.equal_benchmark][1] / marks[cfg.equal_benchmark][0] - 1.0
        r_cap = marks[cfg.cap_benchmark][1] / marks[cfg.cap_benchmark][0] - 1.0
        calc = ic.MonthCalc(
            spec.month,
            protected,
            len(member_ids),
            n_no_cap,
            ic.BookReturn(ic.index_return(eq_w, returns), r_eq),
            ic.BookReturn(ic.index_return(cw, returns), r_cap),
        )
    except ValueError as exc:
        raise IndexCheckFailed(f"{spec.month}: {exc}") from None
    listing = _current_listings(ends, spec.session)
    sessions = [s for s in all_sessions() if spec.prev_session < s <= spec.session]
    flags = {
        sid: _flags(
            spec,
            sid,
            bars=closes.get(sid, {}),
            no_entry=no_entry[sid],
            r=returns[sid],
            extreme=cfg.extreme_name_return,
            sessions=sessions,
            listing=listing.get(sid),
            actions=actions,
            dropped=dropped,
        )
        for sid in member_ids
    }
    census = Counter(f.split("=")[0] for fl in flags.values() for f in fl)

    def listed(weights: Mapping[str, float], bench_return: float) -> tuple[ListedName, ...]:
        contribs = ic.contributions(weights, returns, bench_return)
        names = ic.listed_names(
            contribs, returns, cfg.names_per_month, cfg.extreme_name_return, member_ids=member_ids
        )
        return tuple(
            ListedName(
                ic.name_row(
                    sid,
                    str(listing[sid]["ticker"]) if sid in listing else tickers[sid],
                    contribs.get(sid, 0.0),
                    returns[sid],
                    protected=protected,
                ),
                tuple(flags[sid]),
            )
            for sid in names
        )

    return _MonthDetail(
        calc,
        {name: census.get(name, 0) for name in FLAG_NAMES},
        {EQUAL: listed(eq_w, r_eq), CAP: listed(cw, r_cap)},
    )


# ---- the run --------------------------------------------------------------------------


def run_index_check(
    settings: Settings,
    *,
    now: datetime,
    start: str | None = None,
    end: str | None = None,
    connect: Connect = open_read_only,
) -> IndexCheckReport:
    """Run the check over `start`..`end` (YYYY-MM; default `index_check`'s window) on
    read-only connections and return the masked report.

    Raises `IndexCheckRefused` before any month is read (a bad window, `universe.rank_by`
    not `cap`, a SPY or RSP identity error) and `IndexCheckFailed` when a month fails."""
    cfg = settings.index_check
    first, last = start or cfg.start_month, end or cfg.end_month
    specs = window_months(first, last, now)
    if settings.universe.rank_by != "cap":
        raise IndexCheckRefused(
            "refused_rank_by",
            f"universe.rank_by is {settings.universe.rank_by!r}, not 'cap': no member has a cap",
        )
    edge, schema_version, vintage = _preflight(settings, specs, connect)
    details: list[_MonthDetail] = []
    for spec in specs:
        try:
            details.append(_month(settings, spec, edge, connect))
        except IndexCheckFailed:
            raise
        except ValueError as exc:  # e.g. `universe_as_of` on an invalid corporate action
            raise IndexCheckFailed(f"{spec.month}: {exc}") from None
    calcs = [d.calc for d in details]
    years = ic.year_rows(calcs)
    year_members = {
        int(y): next(c.n_members for c in calcs if c.month.startswith(f"{y:04d}"))
        for y in (r.year for r in years)
        if y is not None
    }
    zones = [
        z
        for z in (ic.zone_total(calcs, protected=False), ic.zone_total(calcs, protected=True))
        if z
    ]
    worst: dict[str, tuple[WorstMonth, ...]] = {}
    by_month = {d.calc.month: d for d in details}
    picks: tuple[tuple[str, Callable[[ic.MonthCalc], ic.BookReturn]], ...] = (
        (EQUAL, lambda c: c.equal),
        (CAP, lambda c: c.cap),
    )
    for book, pick in picks:
        gaps = {d.calc.month: ic.gap(pick(d.calc)) for d in details}
        worst[book] = tuple(
            WorstMonth(m, gaps[m], by_month[m].listed[book])
            for m in ic.worst_months(gaps, cfg.worst_months)
        )
    header = ReportHeader(
        store_path=settings.store.path,
        schema_version=schema_version,
        data_vintage=vintage,
        start_month=first,
        end_month=last,
        edge=edge.date,
        edge_source=edge.source,
        universe=settings.universe.model_dump(mode="json"),
        apply_spin_offs=settings.adjust.apply_spin_offs,
        equal_benchmark=cfg.equal_benchmark,
        cap_benchmark=cfg.cap_benchmark,
    )
    return IndexCheckReport(
        header=header,
        years=tuple(years),
        year_members=year_members,
        zones=tuple(zones),
        months=tuple(ic.month_row(c) for c in calcs),
        census={d.calc.month: d.census for d in details},
        worst=worst,
    )
