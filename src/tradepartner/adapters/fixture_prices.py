"""Fixture `PriceSource`: replays the fixture universe's recorded price rows
(spec req 6, plan T7).

Reads three CSVs from a fixture directory (the T5 universe in
`tests/fixtures/universe/` by default layout):

- `securities.csv` (**required**): the set of `security_id`s the source
  can resolve. It stands in for the security master (T8) here; an id not in
  it raises `UnknownSecurityIdError`, and a price or action row for an id
  not in it is refused at load time.
- `prices_daily.csv` and `corporate_actions.csv` (optional; absent means no
  rows): the records, in the store's own column layout. The actions'
  `source_action_id` and `cancelled` columns (#108) are optional too: an
  empty or absent id means the source gave none, an absent `cancelled`
  means `FALSE`.

**Replay, not snapshot.** A real source returns today's values; ingest
(T16) turns a changed value into a revision with `prices.revision_of`. A
fixture is a recording of every record the source produced over time, each
already stamped, so this adapter returns **every** row in range, first-seen
records and revisions alike, sorted by natural key then `known_at`.
`ingested_at` and `provenance` are read only to check the contract below;
they are not part of the returned records. An action's natural key is its
identity (`CorporateAction.key`): the source's id when present, so a
re-dated action is checked as a revision of the same event.

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
   A first-seen action has `known_at` at or before the first-seen proxy
   (`prices.action_first_seen_known_at` with no announcement): the proxy
   itself, or an earlier announcement. A stamp before the proxy cannot be
   checked further, because the stored layout carries no announcement time
   to compare it with (issue #83). A first-seen action cannot be cancelled.
   **Replacements** (#108) are the exception: a first-seen action ingested
   together with a cancel of another key for the same security and type
   (same `ingested_at`) replaces that key, as in an id-less re-date or a
   switch from an id-less key to a source id. It revises an event already
   known, so it is stamped `known_at == ingested_at`, never at a proxy. This
   is deliberately broad: an unrelated action first seen in the same ingest
   as a cancel is stamped late, never early.
3. Every later row for the same key must be what `prices.revision_of`
   produces from the row before it at that row's `ingested_at`: values that
   differ, and `known_at` equal to its own `ingested_at` (never back-dated).
   A re-date or a cancellation is such a revision.
4. An action with a source id may not share `(security_id, action_type,
   ex_date)` with an id-less action unless that id-less key was cancelled at
   or before the id row's ingest: otherwise both apply as two events.
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
_ACTIONS_COLUMNS = (*_COMMON_COLUMNS, "action_type", "ex_date", "ratio_or_amount")

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


def _parse_bool(value: str, *, column: str, where: str) -> bool:
    """`TRUE`/`FALSE` in either case, as the generator and DuckDB write them."""
    if value.upper() in ("TRUE", "FALSE"):
        return value.upper() == "TRUE"
    raise FixtureContractError(f"{where}: {column} {value!r} is not TRUE or FALSE")


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
                source_action_id=row.get("source_action_id") or None,
                cancelled=_parse_bool(
                    row.get("cancelled", "FALSE"), column="cancelled", where=where
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


def _cancel_ingests(rows: list[_Row[CorporateAction]]) -> set[tuple[str, str, datetime]]:
    """`(security_id, action_type, ingested_at)` of every cancel row, to
    recognise a replacement written in the same ingest (contract rule 2)."""
    return {
        (r.record.security_id, r.record.action_type.value, r.ingested_at)
        for r in rows
        if r.record.cancelled
    }


def _action_first_seen_check(
    cancel_ingests: set[tuple[str, str, datetime]],
) -> Callable[[_Row[CorporateAction]], None]:
    """Contract rule 2 for actions, given the ingests that cancelled a key."""

    def check(row: _Row[CorporateAction]) -> None:
        _check_action_first_seen(row, cancel_ingests)

    return check


def _check_action_first_seen(
    row: _Row[CorporateAction], cancel_ingests: set[tuple[str, str, datetime]]
) -> None:
    """Contract rule 2 for actions: not cancelled; a replacement stamped at
    its own `ingested_at`; any other first-seen row stamped at or before the
    first-seen proxy (the proxy itself, or an earlier announcement)."""
    action = row.record
    if action.cancelled:
        raise FixtureContractError(
            f"{row.where}: first-seen action {action.key} is cancelled; a cancel only "
            "revises an action already recorded"
        )
    if (action.security_id, action.action_type.value, row.ingested_at) in cancel_ingests:
        if action.known_at != row.ingested_at:
            raise FixtureContractError(
                f"{row.where}: action {action.key} replaces a key cancelled in the same "
                f"ingest ({row.ingested_at.isoformat()}), so it revises a known event and "
                f"must be stamped at that ingested_at, not {action.known_at.isoformat()}"
            )
        return
    proxy = action_first_seen_known_at(action.ex_date)
    if action.known_at > proxy:
        raise FixtureContractError(
            f"{row.where}: first-seen action known_at {action.known_at.isoformat()} is after "
            f"the first-seen proxy {proxy.isoformat()} (close before ex-date "
            f"{action.ex_date.isoformat()}); a first-seen action is stamped at its "
            "announcement or at the proxy, never later"
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


def _check_id_against_idless(rows: list[_Row[CorporateAction]]) -> None:
    """Contract rule 4: an id row may share `(security_id, action_type,
    ex_date)` with an id-less key only once that key is cancelled, at or
    before the id row's ingest."""
    cancelled_at: dict[tuple[str, str, date], datetime] = {}
    idless_keys: set[tuple[str, str, date]] = set()
    for row in rows:
        action = row.record
        if action.source_action_id is None:
            key = (action.security_id, action.action_type.value, action.ex_date)
            idless_keys.add(key)
            if action.cancelled:
                cancelled_at[key] = row.ingested_at
    for row in rows:
        action = row.record
        if action.source_action_id is None:
            continue
        shared = (action.security_id, action.action_type.value, action.ex_date)
        if shared in idless_keys and not (
            shared in cancelled_at and cancelled_at[shared] <= row.ingested_at
        ):
            raise FixtureContractError(
                f"{row.where}: action with source id {action.source_action_id!r} shares "
                f"{shared} with an id-less action that is not cancelled by this ingest; "
                "both would apply as two events"
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
        actions = _check_history(
            action_rows, lambda a: a.key, _action_first_seen_check(_cancel_ingests(action_rows))
        )
        _check_id_against_idless(action_rows)
        # Returned in the documented order, not identity order: an id key
        # and an ex-date key do not sort together.
        actions.sort(
            key=lambda a: (
                a.security_id,
                a.action_type.value,
                a.ex_date,
                a.known_at,
                a.source_action_id or "",
            )
        )
        for action in actions:
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
        `(security_id, action_type, ex_date, known_at)`. Every revision of a
        re-dated action is filtered on its own `ex_date`, so a range can
        hold one revision of an event and not another."""
        return [
            action
            for security_id in self._resolve(security_ids, start, end)
            for action in self._actions.get(security_id, [])
            if start <= action.ex_date <= end
        ]
