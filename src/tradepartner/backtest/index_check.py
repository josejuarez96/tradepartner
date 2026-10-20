"""The known-answer index check's arithmetic (backtest spec amendment #1598).

Pure functions and plain dataclasses, no I/O: the store reads and the CLI are T245.
The check holds the whole universe with no signal and compares it with SPY and RSP.
It is never a trial, so this module imports no registry writer and no engine entry
point.

Weights are equal (1/|M|) or cap (`market_cap` over the members with a positive,
finite cap; the rest are left out and counted). A name's return is its end mark over
its entry mark, minus 1; with no entry bar it is 0 and flagged. A month's gap is the
index return minus the benchmark return, and a name's contribution
c_j = w_j (r_j - r_b) sums to that gap. A month is **protected** when its last session
is after the edge E; protected rows carry gaps, tracking error and counts, and **no
level** of either series. The protected row types have no level fields at all, and
their dict form has no level keys (not nulls).
"""

from __future__ import annotations

import dataclasses
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

EdgeSource = Literal["development_boundary", "holdout_start"]


# ---- weights and returns --------------------------------------------------------------


def equal_weights(security_ids: Sequence[str]) -> dict[str, float]:
    """1/|M| per member; empty for no members."""
    if len(set(security_ids)) != len(security_ids):
        raise ValueError("duplicate security_id in the members")
    n = len(security_ids)
    return {s: 1.0 / n for s in security_ids}


def cap_weights(caps: Mapping[str, float | None]) -> tuple[dict[str, float], int]:
    """Cap weights over the members with a positive, finite cap, and `n_no_cap`.

    A member without one is left out of the cap book and counted. With no member
    capped the book is empty.
    """
    capped = {s: float(c) for s, c in caps.items() if c is not None and math.isfinite(c) and c > 0}
    total = math.fsum(capped.values())
    weights = {s: c / total for s, c in capped.items()}
    return weights, len(caps) - len(capped)


def name_return(entry_mark: float | None, end_mark: float | None) -> tuple[float, bool]:
    """(r_j, no_entry_bar): `end / entry - 1` from the latest traded marks.

    A delisted member's end mark is its last traded close, so nothing after it is read.
    With no entry bar the return is 0 and the flag is set.
    """
    if entry_mark is None:
        return 0.0, True
    if end_mark is None:
        raise ValueError("a name with an entry mark must have an end mark")
    if not entry_mark > 0 or not end_mark > 0:
        raise ValueError(f"marks must be positive, got {entry_mark} and {end_mark}")
    return end_mark / entry_mark - 1.0, False


def index_return(weights: Mapping[str, float], returns: Mapping[str, float]) -> float:
    """R = sum w_j (1 + r_j) - 1, rebalanced at the start of the month.

    The weights must be non-empty and sum to 1; otherwise the month has no book and
    the result would be a spurious -100 %, so it raises.
    """
    total = math.fsum(weights.values())
    if not weights or abs(total - 1.0) > 1e-9:
        raise ValueError(f"weights must be non-empty and sum to 1, got {total}")
    return math.fsum(w * (1.0 + returns[s]) for s, w in weights.items()) - 1.0


def contributions(
    weights: Mapping[str, float], returns: Mapping[str, float], bench_return: float
) -> dict[str, float]:
    """c_j = w_j (r_j - r_b); the values sum to the month's gap."""
    return {s: w * (returns[s] - bench_return) for s, w in weights.items()}


@dataclass(frozen=True)
class BookReturn:
    """One weighting's month: the index return and the benchmark return."""

    index: float
    bench: float


def gap(book: BookReturn) -> float:
    """G = R - r_b."""
    return book.index - book.bench


def year_gap(books: Sequence[BookReturn]) -> float:
    """Prod(1 + R) - Prod(1 + r_b) over the months given; 0 for none."""
    if not books:
        return 0.0
    return math.prod(1.0 + b.index for b in books) - math.prod(1.0 + b.bench for b in books)


def tracking_error(gaps: Sequence[float]) -> float | None:
    """Sample standard deviation of the monthly gaps times sqrt(12); None under two."""
    if len(gaps) < 2:
        return None
    return statistics.stdev(gaps) * math.sqrt(12.0)


# ---- ranking ---------------------------------------------------------------------------


def worst_months(gaps: Mapping[str, float], n: int) -> list[str]:
    """The `n` months with the largest |G|, both signs; ties by month."""
    ranked = sorted(gaps, key=lambda m: (-abs(gaps[m]), m))
    return ranked[: max(n, 0)]


def listed_names(
    contribs: Mapping[str, float],
    returns: Mapping[str, float],
    names_per_month: int,
    extreme_name_return: float,
) -> list[str]:
    """The top `names_per_month` by |c_j| (ties by `security_id`), plus every member
    with |r_j| at or over `extreme_name_return`, in |c_j| order."""

    def key(s: str) -> tuple[float, str]:
        return (-abs(contribs[s]), s)

    top = sorted(contribs, key=key)[: max(names_per_month, 0)]
    extreme = [s for s in contribs if abs(returns[s]) >= extreme_name_return]
    return sorted(set(top) | set(extreme), key=key)


# ---- the protected edge ----------------------------------------------------------------


@dataclass(frozen=True)
class ProtectedEdge:
    """E and where it came from; both None when neither source is in the store."""

    date: date | None
    source: EdgeSource | None


def protected_edge(boundary: date | None, holdout_starts: Sequence[date]) -> ProtectedEdge:
    """E: the earlier of the development boundary and the day before the earliest
    `holdout.start`. With neither, E is None and every month is protected."""
    candidates: list[tuple[date, EdgeSource]] = []
    if boundary is not None:
        candidates.append((boundary, "development_boundary"))
    if holdout_starts:
        candidates.append((min(holdout_starts) - timedelta(days=1), "holdout_start"))
    if not candidates:
        return ProtectedEdge(None, None)
    edge, source = min(candidates, key=lambda c: c[0])
    return ProtectedEdge(edge, source)


def is_protected(last_session: date, edge: ProtectedEdge) -> bool:
    """A month is protected when T_m is after E; with no E every month is."""
    return edge.date is None or last_session > edge.date


# ---- report rows ------------------------------------------------------------------------


@dataclass(frozen=True)
class MonthCalc:
    """One month's full arithmetic, in memory only; `month_row` masks it for output."""

    month: str
    protected: bool
    n_members: int
    n_no_cap: int
    equal: BookReturn
    cap: BookReturn


@dataclass(frozen=True)
class DevMonthRow:
    """A development month: levels and gaps."""

    month: str
    n_members: int
    n_no_cap: int
    equal_return: float
    equal_bench_return: float
    equal_gap: float
    cap_return: float
    cap_bench_return: float
    cap_gap: float
    protected: Literal[False] = False


@dataclass(frozen=True)
class ProtectedMonthRow:
    """A protected month: gaps and counts only."""

    month: str
    n_members: int
    n_no_cap: int
    equal_gap: float
    cap_gap: float
    protected: Literal[True] = True


@dataclass(frozen=True)
class DevYearRow:
    """A development year (or the development zone total)."""

    year: int | None
    months: int
    equal_return: float
    equal_bench_return: float
    equal_gap: float
    equal_te: float | None
    cap_return: float
    cap_bench_return: float
    cap_gap: float
    cap_te: float | None
    protected: Literal[False] = False


@dataclass(frozen=True)
class ProtectedYearRow:
    """A protected year (or the protected zone total): gaps and TE only."""

    year: int | None
    months: int
    equal_gap: float
    equal_te: float | None
    cap_gap: float
    cap_te: float | None
    protected: Literal[True] = True


@dataclass(frozen=True)
class DevNameRow:
    """A listed name in a development month."""

    security_id: str
    ticker: str
    contribution: float
    name_return: float
    protected: Literal[False] = False


@dataclass(frozen=True)
class ProtectedNameRow:
    """A listed name in a protected month: its contribution, with no return."""

    security_id: str
    ticker: str
    contribution: float
    protected: Literal[True] = True


MonthRow = DevMonthRow | ProtectedMonthRow
YearRow = DevYearRow | ProtectedYearRow
NameRow = DevNameRow | ProtectedNameRow


def month_row(m: MonthCalc) -> MonthRow:
    """The month's output row; a protected month's type has no level field."""
    if m.protected:
        return ProtectedMonthRow(m.month, m.n_members, m.n_no_cap, gap(m.equal), gap(m.cap))
    return DevMonthRow(
        m.month,
        m.n_members,
        m.n_no_cap,
        m.equal.index,
        m.equal.bench,
        gap(m.equal),
        m.cap.index,
        m.cap.bench,
        gap(m.cap),
    )


def name_row(
    security_id: str, ticker: str, contribution: float, ret: float, *, protected: bool
) -> NameRow:
    """A listed name's row; in a protected month it drops r_j."""
    if protected:
        return ProtectedNameRow(security_id, ticker, contribution)
    return DevNameRow(security_id, ticker, contribution, ret)


def _group_row(year: int | None, months: Sequence[MonthCalc], protected: bool) -> YearRow:
    equal = [m.equal for m in months]
    cap = [m.cap for m in months]
    equal_te = tracking_error([gap(b) for b in equal])
    cap_te = tracking_error([gap(b) for b in cap])
    if protected:
        return ProtectedYearRow(year, len(months), year_gap(equal), equal_te, year_gap(cap), cap_te)
    return DevYearRow(
        year,
        len(months),
        math.prod(1.0 + b.index for b in equal) - 1.0,
        math.prod(1.0 + b.bench for b in equal) - 1.0,
        year_gap(equal),
        equal_te,
        math.prod(1.0 + b.index for b in cap) - 1.0,
        math.prod(1.0 + b.bench for b in cap) - 1.0,
        year_gap(cap),
        cap_te,
    )


def year_rows(months: Sequence[MonthCalc]) -> list[YearRow]:
    """One row per calendar year in the window; a year with any protected month is
    protected."""
    by_year: dict[int, list[MonthCalc]] = {}
    for m in months:
        by_year.setdefault(int(m.month[:4]), []).append(m)
    return [_group_row(y, ms, any(m.protected for m in ms)) for y, ms in sorted(by_year.items())]


def zone_total(months: Sequence[MonthCalc], *, protected: bool) -> YearRow | None:
    """The pair over the development months, or over the protected months; None when
    the zone has no month."""
    zone = [m for m in months if m.protected == protected]
    return _group_row(None, zone, protected) if zone else None


def row_dict(row: MonthRow | YearRow | NameRow) -> dict[str, Any]:
    """The row as a JSON-ready dict with exactly its type's fields (no level keys for a
    protected row, no nulls added)."""
    return dataclasses.asdict(row)
