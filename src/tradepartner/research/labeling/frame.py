"""Frame build: the rule answer at `t` (research-labeling spec C11, req 2
"Frame build"; plan T122).

`build_frame` reads a departure-reason corpus (T120's
`tradepartner.corpus.departure_fetch` output, `corpus.jsonl`) as plain JSON
lines -- this module imports no adapter and nothing from `tradepartner.corpus`
(ADR 0013 point 3) -- and the runtime store at a fixed as-of time `t`, and
writes one tabular dataset (`frame.parquet`, content-addressed through
`research.datafiles`) with one row per listing end: the corpus's own fields
(the whole corpus object, under `documents`) and the **rule answer** the
spec's Definitions section describes (`RuleAnswer`, `crosswalk.py`), computed
purely from reads known at `t`.

**Point-in-time, no look-ahead.** A listing end whose `form25_accepted_at` is
after `t` never reaches the frame (dropped and counted, `after_t`); a marker
filing (`15-12B`, `15-12G`) accepted after `t` is never used for the Form 15
veto below, even though it stays in the row's `documents["markers"]` for
display (ignored and counted, `markers_after_t`). A relisting `listings` row
whose `valid_from` is after `t`'s session is not "relisted" either, even if
`known_at <= t` already let it through the as-of read: knowing about a future
listing early is not the same as it having started.

**The rule answer** (req 2 Definitions, "Rule answer"), computed with
**one short-lived read-only connection** to `settings.store.path`
(`tradepartner.store.db.open_read_only`), opened after the corpus is parsed
and closed before `frame.parquet` or `counts.json` is written:

- `rule_status`: `"listed"` / `"delisted"` / `"transferred"` / `"unmatched"`.
  The listing end's `(cik, exchange, form25_accepted_at)` is joined to a
  `delistings` row through `securities_as_of` (CIK -> candidate
  `security_id`s) and `delistings_as_of` (exact `exchange` and `filed_at`
  match); `"unmatched"` when no such row joins. When one does, the resolved
  `security_id`'s listings at `t` (`listing_ends_as_of`) are checked for the
  one this exact delisting ended: its `status` (`"delisted"` or
  `"transferred"`) if found, else `"listed"` -- the delisting row exists but
  never ended anything, reported in `counts.json`'s `unjoined_delistings` by
  `security_id` (a live line with a Form 25 is a disagreement by
  construction, ADR 0013 point 2 row A).
- `rule_relisted`, `rule_successor_id`, `rule_form15_in_window`: computed
  only when `rule_status == "delisted"` (the crosswalk's rows 2 to 5); each
  bounded by the *next* listing end of the same security (`rule_relisted`)
  or the same CIK (`rule_successor_id`), so a later, unrelated episode in the
  same corpus is never folded into this one's answer.
  - `rule_relisted`: a `listings` row of the same `security_id` with
    `valid_from` after the Form 25's `effective_on`, on or before `t`'s
    session, and before the next bound.
  - `rule_successor_id`: a `securities_as_of` row whose id matches
    `config.parse_successor_id`'s `<cik>@<date>` shape for the same CIK,
    with `<date>` after the Form 25's filing session and before the next
    bound (the earliest such id; `None` if none).
  - `rule_form15_in_window`: a `15-12B` or `15-12G` in the corpus record's
    own `markers` (never the store, which holds none of them), accepted at
    or before `t`, filed within `master.reorganisation_window_sessions`
    XNYS sessions of the Form 25's filing session, on either side.

`rule_row` is `crosswalk.crosswalk_row` over the `RuleAnswer`; `rule_as_of` is
`t`; `rule_code_version` is the git commit of this checkout (a local lookup,
never `store.registry.code_version`, which this module may not import).

`register` registers the frame under `departure-reason-frame` through
`store.research.register_dataset`, with `event_column = "form25_accepted_at"`;
it takes an already-open connection (this module never opens a write
connection itself) for the CLI (T124) to supply.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from tradepartner.calendar import is_session, last_completed_session, next_session, previous_session
from tradepartner.config import Settings, parse_successor_id
from tradepartner.research.datafiles import frame_counts_path, frame_path
from tradepartner.research.labeling.crosswalk import RuleAnswer, RuleStatus, crosswalk_row
from tradepartner.store.asof import _EXCHANGE_TZ, listings_as_of
from tradepartner.store.db import ensure_tz_aware, open_read_only
from tradepartner.store.delistings import delistings_as_of, listing_ends_as_of
from tradepartner.store.master import securities_as_of
from tradepartner.store.research import DatasetRecord, register_dataset

#: The two marker forms the Form 15 veto reads (req 2's marker filings, the
#: other two -- `8-A12B`, `8-K12B` -- feed `rule_successor_id` through the
#: store's own `securities`, never read here directly).
_MARKER_FORMS = frozenset({"15-12B", "15-12G"})

_SCHEMA: dict[str, Any] = {
    "listing_end_id": pl.Utf8,
    "cik": pl.Utf8,
    "exchange": pl.Utf8,
    "form25_accepted_at": pl.Datetime("us", "UTC"),
    "form25_filed_on": pl.Date,
    "documents": pl.Utf8,
    "rule_status": pl.Utf8,
    "rule_relisted": pl.Boolean,
    "rule_successor_id": pl.Utf8,
    "rule_form15_in_window": pl.Boolean,
    "rule_row": pl.Int64,
    "rule_as_of": pl.Datetime("us", "UTC"),
    "rule_code_version": pl.Utf8,
}


@dataclass(frozen=True)
class FrameCounts:
    """Req 2's count identity, extended with the two point-in-time drops."""

    listing_ends_seen: int
    kept: int
    after_t: int
    markers_after_t: int

    def identity_holds(self) -> bool:
        """Whether every listing end the corpus carried is accounted for."""
        return self.kept + self.after_t == self.listing_ends_seen

    def as_json(self) -> dict[str, int | bool]:
        return {
            "listing_ends_seen": self.listing_ends_seen,
            "kept": self.kept,
            "after_t": self.after_t,
            "markers_after_t": self.markers_after_t,
            "identity_holds": self.identity_holds(),
        }


@dataclass(frozen=True)
class UnjoinedDelisting:
    """A `delistings` row the master resolved for a listing end's Form 25
    but never attached to any listing (module docstring): reported by
    `security_id`, never guessed at."""

    security_id: str
    exchange: str
    filed_at: datetime


@dataclass(frozen=True)
class FrameResult:
    """Where the frame build wrote and what it counted."""

    frame_path: Path
    counts_path: Path
    sha256: str
    n_rows: int
    event_start: date
    event_end: date
    counts: FrameCounts
    unjoined_delistings: tuple[UnjoinedDelisting, ...]


def _validate_as_of(t: datetime) -> datetime:
    """Reject a bare `date` (`TypeError`) or a naive `datetime` (`ValueError`),
    the same rule every as-of function in `store.asof` applies to `t`."""
    if not isinstance(t, datetime):
        raise TypeError(f"as_of must be a tz-aware datetime, got {t!r}")
    return ensure_tz_aware(t, field="as_of")


def _read_corpus(path: Path) -> list[dict[str, Any]]:
    """Every record of a `corpus.jsonl` file, in file order."""
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def _parse_utc(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _filing_session(accepted_at: datetime) -> date:
    """The XNYS session an acceptance's New York date falls on, or the next
    one (the same rule `store.delistings._filing_session` applies)."""
    day = accepted_at.astimezone(_EXCHANGE_TZ).date()
    return day if is_session(day) else next_session(day)


def _session_window(session: date, sessions: int) -> tuple[date, date]:
    """`sessions` XNYS sessions either side of `session`, inclusive."""
    low = high = session
    for _ in range(sessions):
        low = previous_session(low)
        high = next_session(high)
    return low, high


def _next_bound(times: Sequence[datetime], accepted_at: datetime) -> datetime | None:
    """The earliest of `times` strictly after `accepted_at`, or `None`."""
    later = sorted(tm for tm in times if tm > accepted_at)
    return later[0] if later else None


def _code_version(repo_dir: Path | None = None) -> str:
    """The git HEAD commit of `repo_dir` (default: this checkout), or
    `"unknown"` outside one. A local lookup: this module may not import
    `store.registry.code_version` (ADR 0013 point 3 (c))."""
    cwd = repo_dir if repo_dir is not None else Path(__file__).resolve().parents[3]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=cwd, check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def _match_delisting(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str],
    exchange: str,
    accepted_at: datetime,
) -> str | None:
    """The one `security_id` whose `delistings` row joins `(exchange,
    accepted_at)` among `security_ids` (a CIK's candidates), or `None`."""
    if not security_ids:
        return None
    rows = delistings_as_of(conn, t, list(security_ids))
    matches = rows.filter((pl.col("exchange") == exchange) & (pl.col("filed_at") == accepted_at))
    ids = matches["security_id"].unique().to_list()
    if len(ids) > 1:
        raise ValueError(
            f"{exchange} {accepted_at.isoformat()}: more than one security's delistings row "
            f"matches ({sorted(ids)}); the master's resolution is ambiguous"
        )
    return ids[0] if ids else None


def _listing_status(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    settings: Settings,
    security_id: str,
    exchange: str,
    accepted_at: datetime,
) -> RuleStatus:
    """`"delisted"` or `"transferred"` for the listing this delisting ended,
    or `"listed"` when it ended none (module docstring)."""
    ends = listing_ends_as_of(conn, t, settings, [security_id])
    matches = ends.filter(
        (pl.col("exchange") == exchange) & (pl.col("delisting_filed_at") == accepted_at)
    )
    if matches.height == 0:
        return "listed"
    statuses = matches["status"].unique().to_list()
    if len(statuses) > 1:
        raise ValueError(f"{security_id}: more than one status for one delisting: {statuses}")
    status = statuses[0]
    if status not in ("delisted", "transferred"):
        raise ValueError(f"{security_id}: unexpected ended status {status!r}")
    return status  # type: ignore[no-any-return]


def _relisted(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_id: str,
    effective_on: date,
    bound: datetime | None,
) -> bool:
    """Whether `security_id` has a `listings` row after `effective_on`, on
    or before `t`'s session, and before `bound` (the next listing end's
    acceptance for the same security, if any)."""
    t_session = last_completed_session(t)
    bound_session = _filing_session(bound) if bound is not None else None
    for valid_from in listings_as_of(conn, t, [security_id])["valid_from"].to_list():
        if valid_from <= effective_on or valid_from > t_session:
            continue
        if bound_session is not None and valid_from >= bound_session:
            continue
        return True
    return False


def _successor_id(
    securities: pl.DataFrame, cik: str, filing_session: date, bound: datetime | None
) -> str | None:
    """The earliest `<cik>@<date>` security known at `t` whose `<date>` is
    after `filing_session` and before `bound` (the CIK's next listing end's
    acceptance, if any), or `None`."""
    bound_session = _filing_session(bound) if bound is not None else None
    candidates: list[tuple[date, str]] = []
    for sid in securities["security_id"].to_list():
        try:
            parsed_cik, valid_from = parse_successor_id(sid)
        except ValueError:
            continue
        if parsed_cik != cik or valid_from <= filing_session:
            continue
        if bound_session is not None and valid_from >= bound_session:
            continue
        candidates.append((valid_from, sid))
    return min(candidates)[1] if candidates else None


def _form15_in_window(
    markers: Sequence[Mapping[str, Any]], t: datetime, filing_session: date, window_sessions: int
) -> tuple[bool, int]:
    """Whether a `15-12B`/`15-12G` of `markers` was accepted at or before `t`
    and filed within `window_sessions` either side of `filing_session`, and
    how many markers were ignored for being accepted after `t`."""
    low, high = _session_window(filing_session, window_sessions)
    found = False
    ignored = 0
    for marker in markers:
        if marker["form"] not in _MARKER_FORMS:
            continue
        accepted = marker.get("accepted_at")
        if not accepted:
            continue
        if _parse_utc(accepted) > t:
            ignored += 1
            continue
        filed_on = date.fromisoformat(marker["filed_on"])
        session = filed_on if is_session(filed_on) else next_session(filed_on)
        if low <= session <= high:
            found = True
    return found, ignored


def _frame_row(record: Mapping[str, Any], answer: RuleAnswer, t: datetime) -> dict[str, Any]:
    return {
        "listing_end_id": record["listing_end_id"],
        "cik": record["cik"],
        "exchange": record["exchange"],
        "form25_accepted_at": _parse_utc(record["form25_accepted_at"]),
        "form25_filed_on": date.fromisoformat(record["form25_filed_on"]),
        "documents": json.dumps(record, sort_keys=True, ensure_ascii=False),
        "rule_status": answer.status,
        "rule_relisted": answer.relisted,
        "rule_successor_id": answer.successor_id,
        "rule_form15_in_window": answer.form15_in_window,
        "rule_row": crosswalk_row(answer),
        "rule_as_of": t,
        "rule_code_version": _code_version(),
    }


def _serialize(rows: list[dict[str, Any]]) -> tuple[bytes, str]:
    frame = pl.DataFrame(rows, schema=_SCHEMA) if rows else pl.DataFrame(schema=_SCHEMA)
    frame = frame.sort("listing_end_id")
    buffer = io.BytesIO()
    frame.write_parquet(buffer)
    data = buffer.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def build_frame(corpus_path: Path, as_of: datetime, settings: Settings) -> FrameResult:
    """Build the departure-reason frame from `corpus_path` at `as_of`
    (module docstring): one `frame.parquet` row per kept listing end, plus
    `counts.json`, written under `research.datafiles.frame_path`."""
    t = _validate_as_of(as_of)
    records = _read_corpus(corpus_path)

    kept: list[dict[str, Any]] = []
    after_t = 0
    for record in records:
        if _parse_utc(record["form25_accepted_at"]) > t:
            after_t += 1
        else:
            kept.append(record)

    unjoined: list[UnjoinedDelisting] = []
    markers_after_t = 0
    rows: list[dict[str, Any]] = []
    with open_read_only(settings) as conn:
        ciks = sorted({record["cik"] for record in kept})
        securities = securities_as_of(conn, t)
        ids_by_cik = {
            cik: securities.filter(pl.col("cik") == cik)["security_id"].to_list() for cik in ciks
        }

        resolved: dict[str, str | None] = {}
        statuses: dict[str, RuleStatus] = {}
        for record in kept:
            accepted_at = _parse_utc(record["form25_accepted_at"])
            sid = _match_delisting(
                conn, t, ids_by_cik[record["cik"]], record["exchange"], accepted_at
            )
            resolved[record["listing_end_id"]] = sid
            if sid is None:
                statuses[record["listing_end_id"]] = "unmatched"
                continue
            status = _listing_status(conn, t, settings, sid, record["exchange"], accepted_at)
            statuses[record["listing_end_id"]] = status
            if status == "listed":
                unjoined.append(UnjoinedDelisting(sid, record["exchange"], accepted_at))

        by_security: dict[str, list[datetime]] = defaultdict(list)
        by_cik: dict[str, list[datetime]] = defaultdict(list)
        for record in kept:
            accepted_at = _parse_utc(record["form25_accepted_at"])
            sid = resolved[record["listing_end_id"]]
            if sid is not None:
                by_security[sid].append(accepted_at)
            by_cik[record["cik"]].append(accepted_at)

        for record in kept:
            listing_end_id = record["listing_end_id"]
            accepted_at = _parse_utc(record["form25_accepted_at"])
            sid = resolved[listing_end_id]
            status = statuses[listing_end_id]
            relisted = False
            successor_id: str | None = None
            form15 = False
            if status == "delisted":
                assert sid is not None
                effective_on = date.fromisoformat(record["effective_on"])
                filing_session = _filing_session(accepted_at)
                security_bound = _next_bound(by_security.get(sid, []), accepted_at)
                cik_bound = _next_bound(by_cik.get(record["cik"], []), accepted_at)
                relisted = _relisted(conn, t, sid, effective_on, security_bound)
                successor_id = _successor_id(securities, record["cik"], filing_session, cik_bound)
                window = settings.master.reorganisation_window_sessions
                form15, ignored = _form15_in_window(record["markers"], t, filing_session, window)
                markers_after_t += ignored
            answer = RuleAnswer(
                status=status,
                relisted=relisted,
                successor_id=successor_id,
                form15_in_window=form15,
            )
            rows.append(_frame_row(record, answer, t))

    counts = FrameCounts(
        listing_ends_seen=len(records),
        kept=len(kept),
        after_t=after_t,
        markers_after_t=markers_after_t,
    )

    data, sha256_hex = _serialize(rows)
    out_path = frame_path(settings, sha256_hex)
    counts_path = frame_counts_path(settings, sha256_hex)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    counts_json = {
        **counts.as_json(),
        "unjoined_delistings": [
            {
                "security_id": u.security_id,
                "exchange": u.exchange,
                "filed_at": u.filed_at.isoformat(),
            }
            for u in unjoined
        ],
    }
    counts_path.write_text(json.dumps(counts_json, indent=2) + "\n", encoding="utf-8")

    accepted_times = [_parse_utc(r["form25_accepted_at"]) for r in kept]
    event_start = min(accepted_times).astimezone(UTC).date() if accepted_times else t.date()
    event_end = max(accepted_times).astimezone(UTC).date() if accepted_times else t.date()

    return FrameResult(
        frame_path=out_path,
        counts_path=counts_path,
        sha256=sha256_hex,
        n_rows=len(rows),
        event_start=event_start,
        event_end=event_end,
        counts=counts,
        unjoined_delistings=tuple(unjoined),
    )


def register(
    conn: duckdb.DuckDBPyConnection, result: FrameResult, settings: Settings
) -> DatasetRecord:
    """Register `result` under `departure-reason-frame` (C11): the caller
    supplies an already-open write `conn` (this module never opens one, and
    reads `settings` only for a future amendment's sake; it registers no
    split -- the whole frame is `full`)."""
    del settings
    return register_dataset(
        conn,
        name="departure-reason-frame",
        version=result.sha256[:12],
        path=str(result.frame_path),
        sha256=result.sha256,
        event_start=result.event_start,
        event_end=result.event_end,
        n_rows=result.n_rows,
        event_column="form25_accepted_at",
    )
