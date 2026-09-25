"""Fixture `PriceSource`: replays the fixture universe's recorded price rows
(spec req 6, plan T7).

Reads three CSVs from a fixture directory (the T5 universe in
`tests/fixtures/universe/` by default layout):

- `securities.csv` (**required**): the set of `security_id`s the source
  can resolve. It stands in for the security master (T8) here; an id not in
  it raises `UnknownSecurityIdError`, and a price or action row for an id
  not in it is refused at load time.
- `prices_daily.csv` and `corporate_actions.csv` (optional; absent means no
  rows): the records, in the store's own column layout.

**Replay, not snapshot.** A real source returns today's values; ingest
(T16) turns a changed value into a revision with `prices.revision_of`. A
fixture is a recording of every record the source produced over time, each
already stamped, so this adapter returns **every** row in range, first-seen
records and revisions alike, sorted by natural key then `known_at`.
`ingested_at` and `provenance` are read only to check the contract below;
they are not part of the returned records.

**The fixture contract, enforced when the adapter is built.** A fixture that
breaks a timing rule would make every downstream look-ahead test vouch for
bad data, so the adapter refuses it with `FixtureContractError` (naming the
file and line) instead of emitting it. Rows sharing a natural key are taken
in ingest order (`ingested_at`, then `known_at`):

1. Every timestamp is tz-aware; `known_at <= ingested_at`; `provenance` is
   `bar` for prices and `action` for actions; the record itself validates
   (see `prices.Bar` / `prices.CorporateAction`).
2. A first-seen bar has `known_at` equal to `prices.bar_known_at(session)`,
   the XNYS close of its session, so a bar on a non-session is refused too.
   A first-seen action has `known_at` equal to
   `prices.action_first_seen_known_at(ex_date, announced_at=...)`: its
   `announced_at` if the row has one, else exactly the first-seen proxy.
   A stamp earlier than the proxy with no `announced_at` is look-ahead and
   refused (issue #83). `announced_at` is optional per row (an empty cell)
   but the column is required.
3. Every later row for the same key must be what `prices.revision_of`
   produces from the row before it at that row's `ingested_at`: values that
   differ, and `known_at` equal to its own `ingested_at` (never back-dated).
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache
from itertools import pairwise
from pathlib import Path

from tradepartner.adapters.prices import (
    Bar,
    CorporateAction,
    PriceSource,
    UnknownSecurityIdError,
    action_first_seen_known_at,
    bar_known_at,
    check_request,
    revision_of,
)

_SECURITIES_CSV = "securities.csv"
_PRICES_CSV = "prices_daily.csv"
_ACTIONS_CSV = "corporate_actions.csv"

_COMMON_COLUMNS = ("security_id", "known_at", "ingested_at", "source", "provenance")
_PRICES_COLUMNS = (*_COMMON_COLUMNS, "session", "open", "high", "low", "close", "volume")
_ACTIONS_COLUMNS = (
    *_COMMON_COLUMNS,
    "action_type",
    "ex_date",
    "ratio_or_amount",
    "announced_at",
)

#: `bar_known_at` builds a pandas timestamp per call; the fixture has ~900
#: distinct sessions across ~11k bars, so memoize it for the load.
_cached_bar_known_at: Callable[[date], datetime] = cache(bar_known_at)


class FixtureContractError(ValueError):
    """A fixture row breaks a timing rule or fails validation; the message
    names the file and line."""


@dataclass(frozen=True)
class _Row[RecordT: (Bar, CorporateAction)]:
    """One validated record plus the bookkeeping the contract checks need."""

    record: RecordT
    ingested_at: datetime
    where: str


def _parse_instant(value: str, *, column: str, where: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise FixtureContractError(f"{where}: {column} {value!r} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise FixtureContractError(f"{where}: {column} {value!r} must be tz-aware")
    return parsed


def _parse_date(value: str, *, column: str, where: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise FixtureContractError(f"{where}: {column} {value!r} is not a YYYY-MM-DD date") from exc


def _read_csv(path: Path, required: Sequence[str]) -> list[tuple[str, dict[str, str]]]:
    """`(where, row)` pairs for every data row, `where` = `file:line`
    (the header is line 1). A header missing any `required` column is
    refused up front rather than surfacing later as a bare `KeyError`."""
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [column for column in required if column not in (reader.fieldnames or [])]
        if missing:
            raise FixtureContractError(f"{path.name}:1: header is missing column(s) {missing}")
        return [(f"{path.name}:{line_no}", row) for line_no, row in enumerate(reader, start=2)]


def _check_common(
    row: dict[str, str], *, provenance: str, known_ids: frozenset[str], where: str
) -> tuple[datetime, datetime]:
    """Contract rule 1 for the columns every price-side row shares; returns
    `(known_at, ingested_at)`."""
    security_id = row["security_id"]
    if security_id not in known_ids:
        raise FixtureContractError(
            f"{where}: security_id {security_id!r} is not in {_SECURITIES_CSV}"
        )
    if row["provenance"] != provenance:
        raise FixtureContractError(
            f"{where}: provenance {row['provenance']!r}, expected {provenance!r}"
        )
    known_at = _parse_instant(row["known_at"], column="known_at", where=where)
    ingested_at = _parse_instant(row["ingested_at"], column="ingested_at", where=where)
    if known_at > ingested_at:
        raise FixtureContractError(
            f"{where}: known_at {row['known_at']} is after ingested_at {row['ingested_at']}"
        )
    return known_at, ingested_at


def _load_bars(path: Path, known_ids: frozenset[str]) -> list[_Row[Bar]]:
    rows: list[_Row[Bar]] = []
    for where, row in _read_csv(path, _PRICES_COLUMNS):
        known_at, ingested_at = _check_common(
            row, provenance="bar", known_ids=known_ids, where=where
        )
        try:
            bar = Bar(
                security_id=row["security_id"],
                session=_parse_date(row["session"], column="session", where=where),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=int(row["volume"]),
                known_at=known_at,
                source=row["source"],
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, FixtureContractError):
                raise
            raise FixtureContractError(f"{where}: {exc}") from exc
        rows.append(_Row(bar, ingested_at, where))
    return rows


def _load_actions(path: Path, known_ids: frozenset[str]) -> list[_Row[CorporateAction]]:
    rows: list[_Row[CorporateAction]] = []
    for where, row in _read_csv(path, _ACTIONS_COLUMNS):
        known_at, ingested_at = _check_common(
            row, provenance="action", known_ids=known_ids, where=where
        )
        try:
            action = CorporateAction(
                security_id=row["security_id"],
                action_type=row["action_type"],  # type: ignore[arg-type]  # coerced
                ex_date=_parse_date(row["ex_date"], column="ex_date", where=where),
                ratio_or_amount=float(row["ratio_or_amount"]),
                known_at=known_at,
                source=row["source"],
                announced_at=(
                    _parse_instant(row["announced_at"], column="announced_at", where=where)
                    if row["announced_at"]
                    else None
                ),
            )
        except (TypeError, ValueError) as exc:
            if isinstance(exc, FixtureContractError):
                raise
            raise FixtureContractError(f"{where}: {exc}") from exc
        rows.append(_Row(action, ingested_at, where))
    return rows


def _check_bar_first_seen(row: _Row[Bar]) -> None:
    """Contract rule 2 for bars: stamped at the XNYS close of its session."""
    bar = row.record
    try:
        expected = _cached_bar_known_at(bar.session)
    except ValueError as exc:
        raise FixtureContractError(
            f"{row.where}: session {bar.session.isoformat()} is not an XNYS session"
        ) from exc
    if bar.known_at != expected:
        raise FixtureContractError(
            f"{row.where}: first-seen bar known_at {bar.known_at.isoformat()} is not the "
            f"session close {expected.isoformat()} of {bar.session.isoformat()}"
        )


def _check_action_first_seen(row: _Row[CorporateAction]) -> None:
    """Contract rule 2 for actions: stamped exactly at its `announced_at`
    if it has one, else exactly at the first-seen proxy."""
    action = row.record
    expected = action_first_seen_known_at(action.ex_date, announced_at=action.announced_at)
    if action.known_at == expected:
        return
    if action.announced_at is not None:
        raise FixtureContractError(
            f"{row.where}: first-seen action known_at {action.known_at.isoformat()} is not "
            f"its announced_at {expected.isoformat()}; an announced action is stamped at "
            "its announcement"
        )
    when = "before" if action.known_at < expected else "after"
    raise FixtureContractError(
        f"{row.where}: first-seen action known_at {action.known_at.isoformat()} is {when} "
        f"the first-seen proxy {expected.isoformat()} (close before ex-date "
        f"{action.ex_date.isoformat()}) and the row has no announced_at; without an "
        "announcement the stamp is the proxy exactly (an earlier one is look-ahead, #83)"
    )


def _check_history[RecordT: (Bar, CorporateAction)](
    rows: list[_Row[RecordT]],
    key: Callable[[RecordT], Hashable],
    check_first_seen: Callable[[_Row[RecordT]], None],
) -> list[RecordT]:
    """Contract rules 2 and 3 over every key's rows in ingest order; returns
    the records sorted by natural key then `known_at`."""
    by_key: defaultdict[Hashable, list[_Row[RecordT]]] = defaultdict(list)
    for row in rows:
        by_key[key(row.record)].append(row)
    for history in by_key.values():
        history.sort(key=lambda r: (r.ingested_at, r.record.known_at))
        check_first_seen(history[0])
        for previous, current in pairwise(history):
            try:
                expected = revision_of(
                    current.record, previous.record, ingested_at=current.ingested_at
                )
            except ValueError as exc:
                raise FixtureContractError(f"{current.where}: {exc}") from exc
            if expected is None:
                raise FixtureContractError(
                    f"{current.where}: revision of {key(current.record)} has values identical "
                    f"to {previous.where}; an unchanged re-fetch is not a new row"
                )
            if expected.known_at != current.record.known_at:
                raise FixtureContractError(
                    f"{current.where}: revision of {key(current.record)} has known_at "
                    f"{current.record.known_at.isoformat()} but must be stamped at its "
                    f"ingested_at {current.ingested_at.isoformat()}; a revision is never "
                    "back-dated"
                )
    return sorted(
        (row.record for row in rows),
        key=lambda r: (key(r), r.known_at),
    )


class FixturePriceSource(PriceSource):
    """`PriceSource` over a fixture directory; see the module docstring for
    what it reads and the contract it enforces on construction."""

    def __init__(self, fixtures_dir: Path) -> None:
        securities_path = fixtures_dir / _SECURITIES_CSV
        if not securities_path.is_file():
            raise FixtureContractError(
                f"{securities_path} is missing; the fixture price source resolves "
                f"security_ids from {_SECURITIES_CSV}"
            )
        self._known_ids = frozenset(
            row["security_id"] for _, row in _read_csv(securities_path, ("security_id",))
        )

        prices_path = fixtures_dir / _PRICES_CSV
        bar_rows = _load_bars(prices_path, self._known_ids) if prices_path.is_file() else []
        actions_path = fixtures_dir / _ACTIONS_CSV
        action_rows = _load_actions(actions_path, self._known_ids) if actions_path.is_file() else []

        self._bars: defaultdict[str, list[Bar]] = defaultdict(list)
        for bar in _check_history(bar_rows, lambda b: b.key, _check_bar_first_seen):
            self._bars[bar.security_id].append(bar)
        self._actions: defaultdict[str, list[CorporateAction]] = defaultdict(list)
        for action in _check_history(action_rows, lambda a: a.key, _check_action_first_seen):
            self._actions[action.security_id].append(action)

    def _resolve(self, security_ids: Sequence[str], start: date, end: date) -> list[str]:
        """Validated, de-duplicated, sorted ids; `UnknownSecurityIdError`
        naming only the unknown ones."""
        ids = check_request(security_ids, start, end)
        unknown = sorted(set(ids) - self._known_ids)
        if unknown:
            raise UnknownSecurityIdError(
                f"unknown security_id(s) {unknown}; resolve tickers through the security "
                "master first"
            )
        return sorted(set(ids))

    def bars(self, security_ids: Sequence[str], start: date, end: date) -> list[Bar]:
        """Every recorded bar (first-seen and revisions) for `security_ids`
        with `start <= session <= end`, sorted by `(security_id, session,
        known_at)`."""
        return [
            bar
            for security_id in self._resolve(security_ids, start, end)
            for bar in self._bars.get(security_id, [])
            if start <= bar.session <= end
        ]

    def corporate_actions(
        self, security_ids: Sequence[str], start: date, end: date
    ) -> list[CorporateAction]:
        """Every recorded action (first-seen and revisions) for
        `security_ids` with `start <= ex_date <= end`, sorted by
        `(security_id, action_type, ex_date, known_at)`."""
        return [
            action
            for security_id in self._resolve(security_ids, start, end)
            for action in self._actions.get(security_id, [])
            if start <= action.ex_date <= end
        ]
