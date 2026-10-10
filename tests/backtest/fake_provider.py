"""In-memory `DataProvider` for engine tests (T37): as-of reads over hand-built frames,
recording every call and its `t`.

Every frame carries a `known_at` column and each read keeps only rows with
`known_at <= t`, latest revision per key, so a test models a late correction or a
restatement by adding a row with a later `known_at`. The fake does no adjustment: a
test supplies adjusted bars directly (`prices`, and `dividend_prices` for
`include_dividends=True` reads, defaulting to `prices`) and raw bars in `raw`
(defaulting to `prices`).

The statement reads (T85c) work the same way over `statement_rows` (keyed by
`security_id`, as `statement_facts_as_of` returns them after its `cik` join) and
`classification_rows` (`security_id`, `sic`, `known_at`). `statement_rows` holds first
vintages only, as the table does, so two rows for one key are refused.

The turnover read (T165b) works over `raw` (or `prices`; it needs a `volume` column)
for the `traded_only` bars, `shares_rows` (`security_id`, `as_of_date`, `value`,
`known_at`; the pick is the latest `as_of_date` among the rows known at `t`, its latest
revision, with none of rule 7's class or plausibility handling) and `split_rows`
(`security_id`, `action_id`, `ex_date`, `ratio`, `cancelled`, `known_at`; the latest
revision per `(security_id, action_id)` known at `t`, dropped when cancelled, so a
re-dated split counts once).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import polars as pl

from tradepartner.backtest.provider import (
    STATEMENT_FACT_NAMES,
    TURNOVER_BAR_COLUMNS,
    GapReading,
    TurnoverInputs,
    check_sessions_from,
    check_t,
)
from tradepartner.calendar import last_completed_session
from tradepartner.universe import _EXCLUSION_SCHEMA, _MEMBER_SCHEMA, Universe

_LISTING_ENDS_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "status": pl.Utf8,
    "end_session": pl.Date,
    "known_at": pl.Datetime("us", "UTC"),
}
_DIVIDEND_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "ex_date": pl.Date,
    "ratio_or_amount": pl.Float64,
    "known_at": pl.Datetime("us", "UTC"),
}

_STATEMENT_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "fact_name": pl.Utf8,
    "period_start": pl.Date,
    "period_end": pl.Date,
    "period_days": pl.Int32,
    "value": pl.Float64,
    "basis": pl.Utf8,
    "known_at": pl.Datetime("us", "UTC"),
}
_STATEMENT_KEY = ["security_id", "fact_name", "period_end", "period_days"]
_CLASSIFICATION_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "sic": pl.Int32,
    "known_at": pl.Datetime("us", "UTC"),
}


_SHARES_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "as_of_date": pl.Date,
    "value": pl.Float64,
    "known_at": pl.Datetime("us", "UTC"),
}
_SPLIT_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "action_id": pl.Utf8,
    "ex_date": pl.Date,
    "ratio": pl.Float64,
    "cancelled": pl.Boolean,
    "known_at": pl.Datetime("us", "UTC"),
}


@dataclass(frozen=True)
class Call:
    """One provider call: the method, its `t`, and its other arguments."""

    method: str
    t: datetime
    ids: tuple[str, ...] | None = None
    include_dividends: bool | None = None
    t_prev: datetime | None = None
    sessions_from: date | None = None


def _known(frame: pl.DataFrame, t: datetime) -> pl.DataFrame:
    return frame.filter(pl.col("known_at") <= t)


def _latest(frame: pl.DataFrame, key: list[str]) -> pl.DataFrame:
    """The latest-`known_at` row per `key`, sorted by `key`. Two rows for one key with
    the same `known_at` have no latest, so they are refused."""
    if frame.select(pl.struct(*key, "known_at").is_duplicated().any()).item():
        raise ValueError(f"two rows for one {key} share the same known_at")
    return frame.sort(*key, "known_at").unique(subset=key, keep="last").sort(key)


def _for_ids(frame: pl.DataFrame, ids: Sequence[str]) -> pl.DataFrame:
    return frame.filter(pl.col("security_id").is_in(list(ids)))


@dataclass
class FakeProvider:
    """A `DataProvider` over in-memory frames.

    `members` maps a rebalance session to the universe members at its close.
    `gaps` maps a read time to its gap reading (zero when absent). `static_listings`
    names the securities whose listing rests on `snapshot_static` rows.
    `statement_rows` and `classification_rows` back `statement_facts` and `sics`.
    """

    prices: pl.DataFrame
    members: Mapping[date, Sequence[str]]
    benchmarks: Mapping[str, str]
    dividend_prices: pl.DataFrame | None = None
    raw: pl.DataFrame | None = None
    listing_ends_rows: pl.DataFrame = field(
        default_factory=lambda: pl.DataFrame(schema=_LISTING_ENDS_SCHEMA)
    )
    dividends: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=_DIVIDEND_SCHEMA))
    dropped: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=_DIVIDEND_SCHEMA))
    gaps: Mapping[datetime, GapReading] = field(default_factory=dict)
    static_listings: Collection[str] = ()
    statement_rows: pl.DataFrame = field(
        default_factory=lambda: pl.DataFrame(schema=_STATEMENT_SCHEMA)
    )
    classification_rows: pl.DataFrame = field(
        default_factory=lambda: pl.DataFrame(schema=_CLASSIFICATION_SCHEMA)
    )
    shares_rows: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=_SHARES_SCHEMA))
    split_rows: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=_SPLIT_SCHEMA))
    calls: list[Call] = field(default_factory=list)

    def _record(self, method: str, t: datetime, **kwargs: Any) -> datetime:
        t = check_t(t)
        if "t_prev" in kwargs:
            kwargs["t_prev"] = check_t(kwargs["t_prev"], name="t_prev")
        if "ids" in kwargs:
            kwargs["ids"] = tuple(kwargs["ids"])
        self.calls.append(Call(method, t, **kwargs))
        return t

    def read_times(self) -> set[datetime]:
        """Every `t` and `t_prev` passed to the provider so far."""
        times = {call.t for call in self.calls}
        return times | {call.t_prev for call in self.calls if call.t_prev is not None}

    def universe(self, t: datetime) -> Universe:
        t = self._record("universe", t)
        session = last_completed_session(t)
        ids = sorted(self.members.get(session, ()))
        members = pl.DataFrame({"security_id": ids}, schema={"security_id": pl.Utf8})
        members = members.with_columns(
            [
                pl.lit(None, dtype).alias(name)
                for name, dtype in _MEMBER_SCHEMA.items()
                if name != "security_id"
            ]
        ).select(list(_MEMBER_SCHEMA))
        return Universe(
            t=t,
            session=session,
            members=members,
            exclusions=pl.DataFrame(schema=_EXCLUSION_SCHEMA),
            rules_enabled={},
            settings={},
        )

    def adjusted_prices(
        self,
        t: datetime,
        ids: Sequence[str],
        include_dividends: bool,
        *,
        sessions_from: date | None = None,
    ) -> pl.DataFrame:
        if isinstance(sessions_from, datetime):  # refused as the store refuses it (T99)
            raise TypeError(
                f"sessions_from must be a session date, not a datetime: {sessions_from!r}"
            )
        t = self._record(
            "adjusted_prices",
            t,
            ids=ids,
            include_dividends=include_dividends,
            sessions_from=sessions_from,
        )
        source = (
            self.dividend_prices
            if include_dividends and self.dividend_prices is not None
            else self.prices
        )
        frame = _latest(_for_ids(_known(source, t), ids), ["security_id", "session"])
        # The bound applies after the as-of selection, as in the store (T99).
        return frame if sessions_from is None else frame.filter(pl.col("session") >= sessions_from)

    def raw_prices(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        t = self._record("raw_prices", t, ids=ids)
        source = self.raw if self.raw is not None else self.prices
        return _latest(_for_ids(_known(source, t), ids), ["security_id", "session"])

    def listing_ends(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        t = self._record("listing_ends", t, ids=ids)
        # One row per listing: keyed by `valid_from` when a test gives several listings.
        key = ["security_id", *(["valid_from"] if "valid_from" in self.listing_ends_rows else [])]
        return _latest(_for_ids(_known(self.listing_ends_rows, t), ids), key)

    def benchmark_ids(self, t: datetime, through: date | None = None) -> Mapping[str, str]:
        self._record("benchmark_ids", t)
        return dict(self.benchmarks)

    def survivorship_gap(self, t: datetime) -> GapReading:
        t = self._record("survivorship_gap", t)
        return self.gaps.get(t, GapReading(count_share=0.0, size_share=0.0))

    def dropped_dividends(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        t = self._record("dropped_dividends", t, ids=ids)
        session = last_completed_session(t)
        rows = _for_ids(_known(self.dropped, t), ids).filter(pl.col("ex_date") <= session)
        return _latest(rows, ["security_id", "ex_date"])

    def late_dividends(self, t_prev: datetime, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        t = self._record("late_dividends", t, ids=ids, t_prev=t_prev)
        t_prev = check_t(t_prev, name="t_prev")
        prev_session = last_completed_session(t_prev)
        key = ["security_id", "ex_date"]
        known = _for_ids(_known(self.dividends, t), ids)
        first_known = known.group_by(key).agg(pl.col("known_at").min().alias("first_known_at"))
        late_keys = first_known.filter(
            (pl.col("first_known_at") > t_prev) & (pl.col("ex_date") <= prev_session)
        ).select(key)
        return _latest(known.join(late_keys, on=key, how="semi"), key)

    def static_listing_count(self, t: datetime, ids: Sequence[str]) -> int:
        self._record("static_listing_count", t, ids=ids)
        return len(set(ids) & set(self.static_listings))

    def statement_facts(self, t: datetime, ids: Sequence[str]) -> pl.DataFrame:
        t = self._record("statement_facts", t, ids=ids)
        if self.statement_rows.select(pl.struct(*_STATEMENT_KEY).is_duplicated().any()).item():
            raise ValueError(f"two statement rows share one {_STATEMENT_KEY} (first vintage only)")
        rows = _for_ids(_known(self.statement_rows, t), ids)
        return rows.filter(pl.col("fact_name").is_in(STATEMENT_FACT_NAMES)).sort(_STATEMENT_KEY)

    def sics(self, t: datetime, ids: Sequence[str]) -> Mapping[str, int | None]:
        t = self._record("sics", t, ids=ids)
        rows = _latest(_for_ids(_known(self.classification_rows, t), ids), ["security_id"])
        known: dict[str, int | None] = dict(rows.select("security_id", "sic").iter_rows())
        return {sid: known.get(sid) for sid in ids}

    def turnover_inputs(
        self, t: datetime, ids: Sequence[str], sessions_from: date
    ) -> TurnoverInputs:
        sessions_from = check_sessions_from(sessions_from)
        t = self._record("turnover_inputs", t, ids=ids, sessions_from=sessions_from)
        session = last_completed_session(t)
        source = self.raw if self.raw is not None else self.prices
        bars = (
            _latest(_for_ids(_known(source, t), ids), ["security_id", "session"])
            .filter(pl.col("volume") > 0)
            .filter(pl.col("session").is_between(sessions_from, session))
            .select(TURNOVER_BAR_COLUMNS)
            .sort("security_id", "session")
        )
        facts = _latest(_for_ids(_known(self.shares_rows, t), ids), ["security_id", "as_of_date"])
        picks = facts.sort("security_id", "as_of_date").unique(subset="security_id", keep="last")
        shares = {
            sid: (as_of, value)
            for sid, as_of, value in picks.select("security_id", "as_of_date", "value").iter_rows()
        }
        actions = _latest(_for_ids(_known(self.split_rows, t), ids), ["security_id", "action_id"])
        live = actions.filter(~pl.col("cancelled") & (pl.col("ex_date") <= session))
        splits: dict[str, list[tuple[date, float]]] = {}
        for sid, ex_date, ratio in live.select("security_id", "ex_date", "ratio").iter_rows():
            splits.setdefault(sid, []).append((ex_date, ratio))
        return TurnoverInputs(
            t=t,
            bars=bars,
            shares={sid: shares[sid] for sid in ids if sid in shares},
            splits={sid: tuple(sorted(splits[sid])) for sid in ids if sid in splits},
        )
