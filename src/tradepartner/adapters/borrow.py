"""The borrow-status record, the parser and the pure diff (data-foundation
spec req 19 and "Data / interfaces" > Amendment 2026-10-11 (#1506); ADR 0021).

Three things live here, and nothing else:

- **The record.** `BorrowStatus` is one asset's borrow state at one instant:
  the payload's eleven stored fields (`asset_id`, `symbol`, `exchange`,
  `status`, the five flags, `borrow_status`, `fractionable`) plus `present`
  and the two stamps. Frozen and validated at construction. `borrow_status`
  is a string, never NULL: the empty string when the payload gives none
  (spec "Borrow status: the table"). It refuses a naive datetime and a
  `known_at` before its `fetched_at`, because `known_at` is the fetch time
  and nothing about an asset is knowable before the fetch that showed it
  (ADR 0021 point 2).
- **The parser.** `parse_assets(payload, fetched_at)` over one unfiltered
  `GET /v2/assets` response (a sequence of mappings, as the recorder's
  `assets-*.json.gz` holds them and `TradingClient.get_all_assets` yields
  once each `Asset` is `dict`-ed) keeps `class = us_equity` rows and counts
  every other class (`AssetSnapshot.other_class`), which the run row
  carries as `borrow_other_class`. A missing field or a non-boolean flag
  raises rather than storing a wrong row.
- **The diff.** `snapshot_rows(latest, assets, fetched_at)` over the latest
  stored row per `asset_id` and one parsed snapshot returns the rows a
  snapshot earns: one per new asset, one per changed asset, one
  `present = FALSE` row per stored asset the snapshot lacks (carrying its
  last values), and none for an unchanged one. `present` is in the compared
  tuple, so an asset that comes back writes its reappearance row rather
  than staying vanished for ever. Its **vanished-share guard** refuses a
  snapshot whose vanished share exceeds the configured threshold, because a
  partial or differently filtered list would write permanent false history
  (ADR 0021 point 3).

The module is pure: it makes no request, reads no config and writes no
store. The ingest stamps `ingested_at`, `source` and `provenance`
(`snapshot`, `alpaca`) when it writes the rows this module returns, and the
as-of read that consumes them is T171b's.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from tradepartner.timeutil import ensure_tz_aware_utc

#: The asset class the table stores. Every other class is counted, never
#: stored (spec "Borrow status: the table"; ADR 0021 point 1).
US_EQUITY_CLASS = "us_equity"

#: The payload fields a row cannot be written without: `borrow_status` is
#: deliberately absent, since the source may omit it (read as `''`).
_REQUIRED_FIELDS: tuple[str, ...] = (
    "id",
    "class",
    "symbol",
    "exchange",
    "status",
    "tradable",
    "marginable",
    "shortable",
    "easy_to_borrow",
    "fractionable",
)

#: The payload's boolean flags, stored as the table's booleans.
_FLAG_FIELDS: tuple[str, ...] = (
    "tradable",
    "marginable",
    "shortable",
    "easy_to_borrow",
    "fractionable",
)

#: The payload's string fields stored verbatim (`status` and `exchange` are
#: `active`/`inactive` and an exchange code; `borrow_status` is the source's
#: own string and is read separately, since it may be absent).
_TEXT_FIELDS: tuple[str, ...] = ("symbol", "exchange", "status")

#: The fields one row is compared by against a stored row, `present`
#: included (spec "rows record change"): a reappearance is a change.
_COMPARED_FIELDS: tuple[str, ...] = (
    "symbol",
    "exchange",
    "status",
    *_FLAG_FIELDS,
    "borrow_status",
    "present",
)


class SnapshotOrderError(ValueError):
    """A snapshot that cannot be diffed as the next one: an `asset_id` twice
    in it (two rows on one `(asset_id, known_at)`, no single state), or a
    fetch time at or before a stored row's `known_at` (it would write history
    earlier than rows already stored; ADR 0021 point 2). Refused before any
    row is written."""


class TruncatedSnapshotError(ValueError):
    """A snapshot whose vanished share exceeds `max_vanished_share`: the
    list came back partial or differently filtered, so writing its
    `present = FALSE` rows (and every later reappearance) would store
    permanent false history. Refused before any row is written
    (ADR 0021 point 3)."""


def _tz_aware(value: object, field_name: str) -> datetime:
    """`value` as a tz-aware UTC datetime, or `ValueError` naming
    `field_name`. A naive datetime is refused here, not left for DuckDB to
    interpret in the connection's session timezone (CLAUDE.md; ADR 0021
    point 2)."""
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a tz-aware datetime, got {value!r}")
    return ensure_tz_aware_utc(value, field_name=field_name)


def _text(value: object, field_name: str, *, allow_empty: bool) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string, got {type(value).__name__}: {value!r}")
    if not value and not allow_empty:
        raise ValueError(f"{field_name} must not be empty, got {value!r}")
    return value


def _flag(value: object, field_name: str) -> bool:
    """A payload flag as a real `bool`. `1`/`0` and `"true"` are refused:
    a flag that is not a boolean is a payload this parser does not
    understand, and guessing would store a wrong row."""
    if not isinstance(value, bool):
        raise TypeError(f"{field_name} must be a boolean, got {type(value).__name__}: {value!r}")
    return value


@dataclass(frozen=True)
class BorrowStatus:
    """One asset's borrow state at one fetch (module docstring).

    `present` is TRUE on every row written from a payload and FALSE on the
    one row written when an asset the store has is missing from a snapshot.
    `known_at` is the fetch time, `fetched_at` the fetch that produced the
    row, and `known_at` is never before it.
    """

    asset_id: str
    symbol: str
    exchange: str
    status: str
    tradable: bool
    marginable: bool
    shortable: bool
    easy_to_borrow: bool
    borrow_status: str
    fractionable: bool
    present: bool
    known_at: datetime
    fetched_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset_id", _text(self.asset_id, "asset_id", allow_empty=False))
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol", allow_empty=False))
        for name in ("exchange", "status", "borrow_status"):
            # Verbatim strings, possibly empty: the payload's own value.
            object.__setattr__(self, name, _text(getattr(self, name), name, allow_empty=True))
        for name in _FLAG_FIELDS:
            object.__setattr__(self, name, _flag(getattr(self, name), name))
        object.__setattr__(self, "present", _flag(self.present, "present"))
        object.__setattr__(self, "known_at", _tz_aware(self.known_at, "known_at"))
        object.__setattr__(self, "fetched_at", _tz_aware(self.fetched_at, "fetched_at"))
        if self.known_at < self.fetched_at:
            raise ValueError(
                f"known_at {self.known_at.isoformat()} is before fetched_at "
                f"{self.fetched_at.isoformat()}: nothing is knowable before the fetch "
                "that showed it (ADR 0021 point 2)"
            )

    def matches(self, other: BorrowStatus) -> bool:
        """Whether `other` states the same borrow state as this row, the
        compared tuple and nothing else: the stamps are never compared, so
        an unchanged asset is unchanged whatever the two fetch times were.
        `present` is compared, so a vanished asset that returns writes a
        row."""
        return all(getattr(self, name) == getattr(other, name) for name in _COMPARED_FIELDS)


@dataclass(frozen=True)
class AssetSnapshot:
    """One parsed `GET /v2/assets` response: the `us_equity` rows and the
    counts the run row carries (spec: assets, `us_equity`, other class)."""

    #: Every `us_equity` asset of the payload, in payload order.
    assets: tuple[BorrowStatus, ...]
    #: Every asset in the payload, whatever its class.
    total: int
    #: The `us_equity` assets, i.e. `len(assets)`.
    us_equity: int
    #: The assets of every other class, counted and not stored.
    other_class: int


def parse_assets(payload: Sequence[Mapping[str, object]], fetched_at: datetime) -> AssetSnapshot:
    """Parse one unfiltered assets response into `us_equity` records
    (module docstring).

    `payload` is the response's raw JSON list of asset mappings (the
    recorder's `assets-*.json.gz` shape, field names as the API sends them:
    `id`, `class`, `borrow_status`). Not alpaca-py's `Asset` model dict-ed:
    that renames `class`, types `id` as a UUID and drops `borrow_status`, so
    the daily ingest (T171c) must hand the raw list over.
    `fetched_at` is the clock reading after the response arrived, which
    stamps every row (an import passes the file's `fetch_finished_utc`): a
    naive datetime is refused here.
    """
    stamp = _tz_aware(fetched_at, "fetched_at")
    assets: list[BorrowStatus] = []
    other_class = 0
    for index, entry in enumerate(payload):
        if not isinstance(entry, Mapping):
            raise TypeError(
                f"asset {index} of the payload is not a mapping, got "
                f"{type(entry).__name__}: {entry!r}"
            )
        missing = [name for name in _REQUIRED_FIELDS if name not in entry]
        if missing:
            raise ValueError(
                f"asset {index} ({entry.get('symbol', '?')!r}) is missing the required "
                f"field(s) {', '.join(repr(name) for name in missing)}"
            )
        if entry["class"] != US_EQUITY_CLASS:
            other_class += 1
            continue
        borrow_status = entry.get("borrow_status")
        if borrow_status is None:
            borrow_status = ""  # the field absent, never NULL
        assets.append(
            BorrowStatus(
                asset_id=entry["id"],  # type: ignore[arg-type]
                symbol=entry["symbol"],  # type: ignore[arg-type]
                exchange=entry["exchange"],  # type: ignore[arg-type]
                status=entry["status"],  # type: ignore[arg-type]
                tradable=entry["tradable"],  # type: ignore[arg-type]
                marginable=entry["marginable"],  # type: ignore[arg-type]
                shortable=entry["shortable"],  # type: ignore[arg-type]
                easy_to_borrow=entry["easy_to_borrow"],  # type: ignore[arg-type]
                borrow_status=borrow_status,  # type: ignore[arg-type]
                fractionable=entry["fractionable"],  # type: ignore[arg-type]
                present=True,
                known_at=stamp,
                fetched_at=stamp,
            )
        )
    return AssetSnapshot(
        assets=tuple(assets),
        total=len(payload),
        us_equity=len(assets),
        other_class=other_class,
    )


def _check_vanished_share(share: float) -> None:
    if isinstance(share, bool) or not isinstance(share, int | float):
        raise TypeError(
            f"max_vanished_share must be a number, got {type(share).__name__}: {share!r}"
        )
    if not math.isfinite(share) or not 0.0 <= share <= 1.0:
        raise ValueError(f"max_vanished_share must be between 0 and 1, got {share!r}")


def snapshot_rows(
    latest: Mapping[str, BorrowStatus],
    assets: Sequence[BorrowStatus],
    fetched_at: datetime,
    *,
    max_vanished_share: float,
) -> list[BorrowStatus]:
    """The rows one snapshot writes, over the latest stored row per
    `asset_id` and the parsed snapshot (module docstring).

    `max_vanished_share` is `borrow.max_vanished_share`: the share of the
    stored **present** assets a snapshot may lack before it is refused as a
    truncated list. A stored row with `present = FALSE` is out of both the
    numerator and the denominator, so a store that has already recorded
    vanishings is not refused for them, and an asset whose latest row is
    absent from the snapshot stays vanished without writing another row.

    The rows are the snapshot's, in payload order, then the vanished ones in
    `latest` order; each carries `known_at = fetched_at`. A snapshot with an
    `asset_id` twice, or with `fetched_at` at or before any stored row's
    `known_at`, raises `SnapshotOrderError`.
    """
    _check_vanished_share(max_vanished_share)
    stamp = _tz_aware(fetched_at, "fetched_at")
    seen: set[str] = set()
    for record in assets:
        if record.asset_id in seen:
            raise SnapshotOrderError(
                f"asset_id {record.asset_id!r} appears twice in this snapshot: refused, "
                "nothing written"
            )
        seen.add(record.asset_id)
    later = [row.asset_id for row in latest.values() if row.known_at >= stamp]
    if later:
        raise SnapshotOrderError(
            f"fetched_at {stamp.isoformat()} is not after the stored row of {len(later)} "
            f"asset(s) (first: {later[0]!r}): a snapshot is diffed only after the latest "
            "stored one, nothing written"
        )
    stored_present = [row for row in latest.values() if row.present]
    in_payload = {record.asset_id for record in assets}
    vanished = [row for row in stored_present if row.asset_id not in in_payload]
    if stored_present and len(vanished) / len(stored_present) > max_vanished_share:
        raise TruncatedSnapshotError(
            f"{len(vanished)} of {len(stored_present)} stored present assets vanished from "
            f"this snapshot ({len(vanished) / len(stored_present):.4f} > {max_vanished_share}): "
            "refused as a truncated assets list, nothing written"
        )
    rows = [
        replace(record, known_at=stamp, fetched_at=stamp)
        for record in assets
        if (stored := latest.get(record.asset_id)) is None or not stored.matches(record)
    ]
    rows.extend(replace(row, present=False, known_at=stamp, fetched_at=stamp) for row in vanished)
    return rows
