"""The bounded raw re-derive (plan T352, #2714; ADR 0028; data-foundation spec
amendment #2714 items 1, 3 and 4).

Re-parses the archived raw bodies behind named stored rows with today's parser
and corrects the rows a parser mistake left wrong, keeping their original
`known_at`. It reads only the archive, never SEC, and never the one latest
`data/edgar_cache` zip.

**Selection** (bounded by the owner's command): every `sec_form4_transactions`
row of the named accessions whose source is `sec_form4_xml` or
`sec_insider_bulk`, or every `filing_index` row listed on the named daily file
dates. One family per run.

**Match** (a dry run refuses on any failure). Each writer run of the named mode
(`edgar`/`sec_forms` for Form 4, `edgar_index` for the daily index; not failed)
whose message carries `provenance_sha256` yields its manifest, read hash-checked;
the manifest must name that run ID, source and mode. A stored row then maps by
row source, mode, accession or file date, table key and row digest to exactly
one entry (`form4_archive.find_entry`, `daily_index.find_index_entry`), never by
an `ingested_at` window, so a zero-width quarter `sec_insider_bulk` run row is
no second match. An entry fetched after its run finished, or a bulk entry whose
zip its manifest does not list for that quarter, is refused: a later fetch is
never an earlier run's input. An entry with no fetch time is refused too (#3001),
except a bulk entry whose listed zip has none either (a zip cached before T348's
fetch record): its quarter and hash in the run's own zips list are the binding.
An XML entry always has one, and a daily file cached before T347 has nothing else
to bind it.

**Inputs.** Every hash the matched entries name (Form 4 XML bodies, archived
insider bulk zips, daily index bodies) goes to `raw_archive.download_inputs` at
once, which checks the disk floor, requests and waits for every body and
re-hashes each before any parse. The match runs first on a read-only connection
that is closed before the download, so the bounded iCloud wait holds no store
lock; the second pass matches again and refuses if the hashes changed, and a body
evicted in between is refused by `read_body`, not downloaded (#3001). The floor
is checked again before each body is read (`check_archive_write` with no bytes),
so a disk that fills mid-run stops it before a write.

**Compare.** A stored row whose parser-derived columns differ from the re-parse
is a correction. The bound: a stored row the re-parse no longer produces, or a
Form 4 row it would add, is refused (a delete or a new fact is not a re-derive).

**Write** (only with a release; the #1682 item 7 rules for never-true rows):
only inside the open data release named (`repair.release_refusal`), only the
dry run's count and digest (`expect`, `expect_digest`: the rows, `known_at`s and
old and new values, #3001), and only rows no open paper window read: with a
window open, a row whose `known_at` is at or before a read instant, or on or
after the window's first session, is refused (`delete_master_rows`' rule).
Each correction updates the derived columns in place with `ingested_at` the
run's time; `known_at` and the key never change. The run archives a `repair`
mode manifest of the rows it wrote, citing the same raw hashes, and records one
`ingestion_runs` row (status `repaired`, never `ok`, so no nightly cursor reads
it) naming that manifest. The release's close then stales the trials whose
window it touched (`registry.close_data_release`).

**Repeat repairs** (#3022). Each repair entry names the writer mode its rows were
first written under (`writer_mode`) and, for each row, the run, key and digest of
the entry it replaces (`replaces`). A repair entry matches only under its own
writer mode, and an entry a later repair replaced never matches, so a repair that
puts back a row's earlier value maps to that repair alone, not to it and the first
writer. A repair entry with neither field (none was written before #3022) never
matches, and whatever is still ambiguous is refused, never guessed.
"""

from __future__ import annotations

import hashlib
import io
import json
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import UTC, date, datetime
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

import duckdb

from tradepartner.adapters import daily_index as di
from tradepartner.adapters.edgar import SEC_FORMS_PARSER_VERSION, Form4Transaction, parse_form4
from tradepartner.adapters.edgar_insider_bulk import InsiderBulk, read_insider_bulk
from tradepartner.adapters.form4_archive import (
    BULK_SOURCE,
    ROW_SOURCES,
    TABLE_KEYS,
    WRITER_SOURCES,
    XML_SOURCE,
    ProvenanceError,
    RawInput,
    build_manifest,
    canonical_row,
    find_entry,
    manifest_entry,
    message_digest,
    parse_manifest,
    row_digest,
)
from tradepartner.adapters.form4_bulk_route import BULK_READER_VERSION
from tradepartner.config import Settings
from tradepartner.ingest import SourceRun, write_run
from tradepartner.repair import REPAIR, REPAIRED, release_refusal
from tradepartner.store import raw_archive
from tradepartner.store.db import open_for_write, open_read_only, utc_now
from tradepartner.store.raw_archive_config import check_archive_write, require_archive_root
from tradepartner.store.restamp_precheck import (
    crossed_instants,
    read_instants,
    window_first_session,
)
from tradepartner.timeutil import ensure_tz_aware_utc

FORM4_TABLE = "sec_form4_transactions"
_FORM4_FIELDS = tuple(f.name for f in fields(Form4Transaction))
#: A daily row's parser-derived columns. `list_position` is not one: ingest keeps the
#: first listing's position on a revision on purpose (`edgar_source`), so it is no
#: parser mistake to correct.
_INDEX_FIELDS = ("form", "company_name", "filed_on")
_PARSERS = {
    XML_SOURCE: f"sec_forms_parser={SEC_FORMS_PARSER_VERSION}",
    BULK_SOURCE: f"bulk_reader={BULK_READER_VERSION}",
    di.DAILY_SOURCE: di.DAILY_PARSER_VERSION,
}
_NEVER = datetime.max.replace(tzinfo=UTC)
_NEW_YORK = ZoneInfo("America/New_York")


class RederiveRefused(ValueError):
    """A selection, match, input, window or release the re-derive refuses; nothing
    was written."""


@dataclass(frozen=True)
class _Matched:
    table: str
    row_source: str
    group: str  # the accession, or the daily file date
    key: tuple[Any, ...]
    row: Mapping[str, Any]
    run_id: str
    entry: Mapping[str, Any]


@dataclass(frozen=True)
class Correction:
    """One stored row's derived columns as the archived body re-parses them."""

    table: str
    key: tuple[Any, ...]
    known_at: datetime
    old: Mapping[str, Any]
    new: Mapping[str, Any]
    matched: _Matched

    def label(self) -> str:
        cols = ", ".join(f"{c} {self.old[c]!r} -> {self.new[c]!r}" for c in sorted(self.new))
        return f"  {self.table} {list(self.key)} known_at {self.known_at.isoformat()}: {cols}"


@dataclass(frozen=True)
class RederiveResult:
    """What one dry or real re-derive found, and refused."""

    mode: str
    release: str | None
    rows: int
    runs: tuple[str, ...]
    hashes: tuple[str, ...]
    unchanged: int
    corrections: tuple[Correction, ...]
    refused: tuple[str, ...]

    @property
    def digest(self) -> str:
        """12 hex characters of SHA-256 over the sorted corrections (table, key,
        `known_at`, old and new values): what a write's `expect_digest` must match,
        so a write changes exactly the rows and values its dry run listed."""
        lines = sorted(
            "|".join(
                [
                    c.table,
                    *map(str, c.key),
                    c.known_at.isoformat(),
                    canonical_row({f"old_{k}": v for k, v in c.old.items()}),
                    canonical_row({f"new_{k}": v for k, v in c.new.items()}),
                ]
            )
            for c in self.corrections
        )
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:12]

    def sessions(self) -> tuple[date, date] | None:
        """The New York dates of the earliest and latest corrected `known_at`."""
        days = sorted(c.known_at.astimezone(_NEW_YORK).date() for c in self.corrections)
        return (days[0], days[-1]) if days else None

    def lines(self) -> list[str]:
        head = "dry run" if self.release is None else f"release {self.release}"
        span = self.sessions()
        out = [
            f"raw re-derive ({head}), mode {self.mode}: {self.rows} stored rows, "
            f"writer runs {', '.join(self.runs) or 'none'}",
            f"inputs: {len(self.hashes)} archived bodies requested and hash-checked",
            f"unchanged {self.unchanged}, corrections {len(self.corrections)}, "
            f"digest {self.digest}" + (f" (known {span[0]}..{span[1]})" if span else ""),
            *(c.label() for c in self.corrections),
            f"refused {len(self.refused)}",
            *self.refused,
        ]
        return out


def _rows(conn: duckdb.DuckDBPyConnection, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
    cursor = conn.execute(sql, list(params))
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, values, strict=True)) for values in cursor.fetchall()]


def _selected(
    conn: duckdb.DuckDBPyConnection, accessions: Sequence[str], file_dates: Sequence[date]
) -> list[tuple[str, str, str, tuple[Any, ...], dict[str, Any]]]:
    """(table, row source, group, key, row) of every selected stored row."""
    if bool(accessions) == bool(file_dates):
        raise RederiveRefused("name accessions or daily file dates, not both and not neither")
    out = []
    if accessions:
        rows = _rows(
            conn,
            f"SELECT * FROM {FORM4_TABLE} WHERE list_contains(?, accession) "
            "AND list_contains(?, source) ORDER BY accession, filer_cik, line_no",
            [sorted(set(accessions)), sorted(ROW_SOURCES)],
        )
        if missing := sorted(set(accessions) - {r["accession"] for r in rows}):
            raise RederiveRefused(f"no Form 4 XML or bulk row is stored for {', '.join(missing)}")
        for row in rows:
            key = tuple(row[c] for c in TABLE_KEYS[FORM4_TABLE])
            out.append((FORM4_TABLE, row["source"], row["accession"], key, row))
        return out
    rows = _rows(
        conn,
        f"SELECT * FROM {di.INDEX_TABLE} WHERE list_contains(?, listed_on) "
        "ORDER BY listed_on, cik, accession, known_at",
        [sorted(set(file_dates))],
    )
    if absent := sorted(set(file_dates) - {r["listed_on"] for r in rows}):
        raise RederiveRefused(f"no filing_index row is listed on {', '.join(map(str, absent))}")
    for row in rows:
        key = tuple(di.index_key(row))
        out.append((di.INDEX_TABLE, di.DAILY_SOURCE, row["listed_on"].isoformat(), key, row))
    return out


def _manifests(
    conn: duckdb.DuckDBPyConnection, settings: Settings, *, index: bool, mode: str
) -> tuple[list[dict[str, Any]], dict[str, datetime | None]]:
    """Every non-failed writer run's manifest of `mode` or of an earlier re-derive
    (`repair`), checked against its run row, and each run's finish time."""
    writers = [di.INDEX_WRITER] if index else sorted(WRITER_SOURCES)
    parse = di.parse_index_manifest if index else parse_manifest
    runs = conn.execute(
        "SELECT run_id, source, mode, finished_at, message FROM ingestion_runs "
        "WHERE list_contains(?, source) AND list_contains(?, mode) AND status <> 'failed' "
        "ORDER BY started_at, run_id",
        [writers, sorted({mode, REPAIR})],
    ).fetchall()
    manifests: list[dict[str, Any]] = []
    finished: dict[str, datetime | None] = {}
    for run_id, source, run_mode, finished_at, message in runs:
        digest = message_digest(message)
        if digest is None:
            continue
        try:
            document = parse(raw_archive.read_manifest(settings, digest))
        except (raw_archive.RawArchiveMissingError, raw_archive.RawArchiveOfflineError) as exc:
            raise RederiveRefused(f"writer run {run_id}'s manifest is unreadable: {exc}") from None
        except (raw_archive.RawArchiveIntegrityError, ProvenanceError) as exc:
            raise RederiveRefused(f"writer run {run_id}'s manifest: {exc}") from None
        writer = document["writer"]
        if (writer.get("run_id"), writer.get("source"), writer.get("mode")) != (
            run_id,
            source,
            run_mode,
        ):
            raise RederiveRefused(
                f"manifest {digest} names run {writer.get('run_id')} "
                f"{writer.get('source')}/{writer.get('mode')}, not {run_id} {source}/{run_mode}"
            )
        manifests.append(document)
        finished[run_id] = finished_at
    return manifests, finished


def _key_text(table: str, key: Sequence[Any]) -> str:
    return json.dumps([table, *key], default=str)


def _replaced(manifests: Sequence[dict[str, Any]]) -> set[tuple[str, str]]:
    """(run ID, table and key) of every row entry a later repair replaced."""
    return {
        (str(r.get("run_id")), _key_text(str(r.get("table")), r.get("key") or ()))
        for manifest in manifests
        if manifest["writer"].get("mode") == REPAIR
        for entry in manifest["entries"]
        for r in entry.get("replaces") or ()
    }


def _find(
    manifests: Sequence[dict[str, Any]],
    table: str,
    row_source: str,
    group: str,
    key: tuple[Any, ...],
    row: Mapping[str, Any],
    mode: str,
) -> tuple[str, Mapping[str, Any]]:
    """The one entry, in a manifest of `mode` or of a re-derive of `mode`'s rows,
    that wrote `row` as it is stored now and that no later re-derive replaced: a
    corrected row's digest is its last re-derive's, an untouched row's its first
    writer's (#3022). Two such entries, or none, raise `ProvenanceError`."""
    digest = row_digest(row)
    replaced = _replaced(manifests)
    ident = _key_text(table, key)
    writers = [m for m in manifests if m["writer"].get("mode") == mode]
    # A repair manifest is searched only for its entries of this writer mode.
    repairs = [
        {**m, "entries": [e for e in m["entries"] if e.get("writer_mode") == mode]}
        for m in manifests
        if m["writer"].get("mode") == REPAIR
    ]
    found: list[tuple[str, Mapping[str, Any]]] = []
    for each, searched in ((mode, writers), (REPAIR, repairs)):
        for manifest in searched:  # one at a time, so matches across manifests all count
            try:
                if table == FORM4_TABLE:
                    hit = find_entry(
                        [manifest],
                        row_source=row_source,
                        mode=each,
                        accession=group,
                        table=table,
                        key=[*key],
                        digest=digest,
                    )
                else:
                    hit = di.find_index_entry(
                        [manifest], mode=each, listed_on=row["listed_on"], key=[*key], digest=digest
                    )
            except ProvenanceError as exc:
                if str(exc).startswith("more than one"):
                    raise
                continue
            if (hit[0], ident) not in replaced:
                found.append(hit)
    if len(found) == 1:
        return found[0]
    if found:
        raise ProvenanceError(f"more than one manifest entry wrote {table} row {list(key)}")
    raise ProvenanceError(f"no manifest entry wrote {table} row {list(key)} of {group}")


def _match(
    selected: Sequence[tuple[str, str, str, tuple[Any, ...], dict[str, Any]]],
    manifests: Sequence[dict[str, Any]],
    finished: Mapping[str, datetime | None],
    mode: str,
) -> tuple[list[_Matched], list[str]]:
    by_run = {m["writer"]["run_id"]: m for m in manifests}
    matched: list[_Matched] = []
    refused: list[str] = []
    for table, row_source, group, key, row in selected:
        try:
            run_id, entry = _find(manifests, table, row_source, group, key, row, mode)
        except ProvenanceError as exc:
            refused.append(f"  {table} {list(key)}: {exc}")
            continue
        if entry.get("outcome") != "added":
            refused.append(f"  {table} {list(key)}: its entry's outcome is {entry.get('outcome')}")
            continue
        fetched, done = entry.get("fetched_at"), finished.get(run_id)
        if done is None:
            refused.append(f"  {table} {list(key)}: writer run {run_id} has no finish time")
            continue
        if fetched is not None and datetime.fromisoformat(fetched) > done:
            refused.append(f"  {table} {list(key)}: its input was fetched after run {run_id}")
            continue
        zips = [
            z
            for z in by_run[run_id].get("zips", ())
            if z.get("quarter") == entry.get("quarter") and z.get("sha256") == entry["raw_sha256"]
        ]
        if row_source == BULK_SOURCE and not zips:
            refused.append(f"  {table} {list(key)}: run {run_id} lists no such quarter zip")
            continue
        # A null fetch time passes only on a bulk entry whose zip, as the run's own
        # zips list names it, has none either: a zip cached before T348 (#3001).
        if fetched is None and (
            row_source != BULK_SOURCE or any(z.get("fetched_at") is not None for z in zips)
        ):
            refused.append(
                f"  {table} {list(key)}: its input has no fetch time, so it cannot be shown "
                f"to be run {run_id}'s"
            )
            continue
        matched.append(_Matched(table, row_source, group, key, row, run_id, entry))
    return matched, refused


def _parsed(
    settings: Settings,
    m: _Matched,
    load: Callable[[], bytes],
    bulks: dict[str, InsiderBulk],
) -> dict[tuple[Any, ...], dict[str, Any]]:
    """The re-parsed rows of `m`'s group, keyed as `m.key`. A bulk zip is read and
    parsed once for all its accessions (#3022): `load` runs only on a miss."""
    sha = m.entry["raw_sha256"]
    if m.table == di.INDEX_TABLE:
        rows: dict[tuple[Any, ...], dict[str, Any]] = {}
        for r in di.parse_daily_body(load()):  # the first listing wins, as in ingest
            rows.setdefault((r.cik, r.accession), {c: getattr(r, c) for c in _INDEX_FIELDS})
        return rows
    if m.row_source == XML_SOURCE:
        transactions = parse_form4(load().decode("utf-8")).transactions
    else:
        if sha not in bulks:
            bulks[sha] = read_insider_bulk(
                io.BytesIO(load()), max_member_bytes=settings.events.form4_bulk_max_member_bytes
            )
        record = bulks[sha].accessions.get(m.group)
        transactions = () if record is None else record.transactions
    return {(m.group, t.filer_cik, t.line_no): asdict(t) for t in transactions}


def _same(a: object, b: object) -> bool:
    return canonical_row({"v": a}) == canonical_row({"v": b})


def _compare(
    settings: Settings, matched: Sequence[_Matched], icloud: raw_archive.ICloudItems
) -> tuple[list[Correction], int, list[str]]:
    root = require_archive_root(settings)
    groups: dict[tuple[str, str, str], list[_Matched]] = defaultdict(list)
    for m in matched:
        groups[(m.row_source, m.entry["raw_sha256"], m.group)].append(m)
    bulks: dict[str, InsiderBulk] = {}
    unparsed: dict[str, str] = {}
    corrections: list[Correction] = []
    unchanged = 0
    refused: list[str] = []
    for (row_source, sha, group), members in sorted(groups.items()):
        check_archive_write(settings, root, 0)  # the floor, during the run
        if sha in unparsed:  # a bulk zip that already failed for another accession
            refused.append(f"  {row_source} {group}: body {sha} does not parse: {unparsed[sha]}")
            continue
        try:
            parsed = _parsed(
                settings,
                members[0],
                partial(raw_archive.read_body, settings, sha, icloud),
                bulks,
            )
        except (ValueError, UnicodeError) as exc:
            if row_source == BULK_SOURCE:
                unparsed[sha] = str(exc)
            refused.append(f"  {row_source} {group}: body {sha} does not parse: {exc}")
            continue
        columns = _INDEX_FIELDS if members[0].table == di.INDEX_TABLE else _FORM4_FIELDS
        if members[0].table == FORM4_TABLE:
            stored = {m.key for m in members}
            if added := sorted(set(parsed) - stored):
                refused.append(
                    f"  {row_source} {group}: the re-parse adds rows {[list(k) for k in added]}"
                    " (a new fact, not a re-derive)"
                )
                continue
        for m in members:
            lookup = (m.row["cik"], m.row["accession"]) if m.table == di.INDEX_TABLE else m.key
            if lookup not in parsed:
                refused.append(f"  {m.table} {list(m.key)}: the re-parse no longer produces it")
                continue
            new = {c: parsed[lookup][c] for c in columns if not _same(m.row[c], parsed[lookup][c])}
            if not new:
                unchanged += 1
                continue
            known = ensure_tz_aware_utc(m.row["known_at"], field_name="known_at")
            old = {c: m.row[c] for c in new}
            corrections.append(Correction(m.table, m.key, known, old, new, m))
    return corrections, unchanged, refused


def _window_refusals(
    conn: duckdb.DuckDBPyConnection, corrections: Sequence[Correction]
) -> list[str]:
    """The #1682 item 7 open-window rule: a correction no open window read."""
    first = window_first_session(conn)
    if first is None:
        return []
    instants = read_instants(conn)
    out = []
    for c in corrections:
        if crossed := crossed_instants(_NEVER, c.known_at, instants):
            out.append(
                f"{c.label().strip()}: an open paper window (first session {first}) read it "
                f"at {len(crossed)} instants from {crossed[0].isoformat()}"
            )
        elif c.known_at.astimezone(_NEW_YORK).date() >= first:
            out.append(f"{c.label().strip()}: known inside the open paper window ({first})")
    return ["  " + line for line in out]


def _apply(
    conn: duckdb.DuckDBPyConnection, corrections: Sequence[Correction], at: datetime
) -> list[dict[str, Any]]:
    """Update each correction in place; the written rows, re-read."""
    written = []
    for c in corrections:
        keys = di.INDEX_KEY if c.table == di.INDEX_TABLE else TABLE_KEYS[c.table]
        where = " AND ".join(f"{k} = ?" for k in keys if k != "known_at")
        values = [c.matched.row[k] for k in keys if k != "known_at"]
        sets = ", ".join(f"{col} = ?" for col in sorted(c.new))
        row = conn.execute(
            f"UPDATE {c.table} SET {sets}, ingested_at = ? WHERE {where} AND known_at = ?",
            [*(c.new[col] for col in sorted(c.new)), at, *values, c.known_at],
        ).fetchone()
        if row is None or int(row[0]) != 1:
            raise RuntimeError(f"{c.label().strip()}: updated {row} rows, not 1; rolled back")
        (stored,) = _rows(
            conn,
            f"SELECT * FROM {c.table} WHERE {where} AND known_at = ?",
            [*values, c.known_at],
        )
        written.append(stored)
    return written


def _repair_manifest(
    run_id: str,
    corrections: Sequence[Correction],
    written: Sequence[dict[str, Any]],
    *,
    index: bool,
    mode: str,
) -> bytes:
    """The re-derive run's own manifest (mode `repair`): each corrected row's new
    digest under the same raw hash its writer read, with today's parser, plus the
    writer mode and, per row, the run, key and digest of the entry it replaces
    (#3022)."""
    by_input: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    entries: dict[tuple[str, str, str], Mapping[str, Any]] = {}
    replaces: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for c, row in zip(corrections, written, strict=True):
        m = c.matched
        ident = (m.row_source, m.group, m.entry["raw_sha256"])
        by_input[ident].append(row)
        entries[ident] = m.entry
        replaces[ident].append(
            {"run_id": m.run_id, "table": m.table, "key": list(m.key), "digest": row_digest(m.row)}
        )
    out = []
    for ident, rows in sorted(by_input.items()):
        entry = entries[ident]
        fetched = entry.get("fetched_at")
        raw = RawInput(
            entry["raw_sha256"],
            entry.get("url"),
            None if fetched is None else datetime.fromisoformat(fetched),
            _PARSERS[ident[0]],
        )
        made: dict[str, object]
        if index:
            made = di.index_entry(path=entry["path"], raw=raw, parsed=True, rows=rows)
        else:
            made = manifest_entry(
                accession=ident[1],
                row_source=ident[0],
                quarter=entry.get("quarter"),
                raw=raw,
                outcome="added",
                rows={FORM4_TABLE: rows},
            )
        made["writer_mode"] = mode
        made["replaces"] = json.loads(
            json.dumps(
                sorted(replaces[ident], key=lambda r: _key_text(r["table"], r["key"])),
                default=str,
            )
        )
        out.append(made)
    if index:
        return di.build_index_manifest(run_id=run_id, mode=REPAIR, entries=out)
    zips = {
        (e["quarter"], e["raw_sha256"]) for e in entries.values() if e["row_source"] == BULK_SOURCE
    }
    return build_manifest(
        run_id=run_id,
        source="edgar",
        mode=REPAIR,
        entries=out,
        zips=[{"quarter": q, "sha256": sha} for q, sha in sorted(zips)],
    )


def _matched(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    *,
    mode: str,
    accessions: Sequence[str],
    file_dates: Sequence[date],
) -> tuple[list[tuple[str, str, str, tuple[Any, ...], dict[str, Any]]], bool, list[_Matched]]:
    """The selection, whether it is the daily index, and each row's one matched
    entry; raises `RederiveRefused` on any match failure."""
    selected = _selected(conn, accessions, file_dates)
    index = selected[0][0] == di.INDEX_TABLE
    manifests, finished = _manifests(conn, settings, index=index, mode=mode)
    matched, refused = _match(selected, manifests, finished, mode)
    if refused:
        raise RederiveRefused(
            f"{len(refused)} of {len(selected)} stored rows map to no single archived "
            "manifest entry; nothing was read or written:\n" + "\n".join(refused)
        )
    return selected, index, matched


def _hashes(matched: Sequence[_Matched]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(m.entry["raw_sha256"] for m in matched))


def rederive(
    settings: Settings,
    icloud: raw_archive.ICloudItems,
    *,
    mode: str,
    accessions: Sequence[str] = (),
    file_dates: Sequence[date] = (),
    release: str | None = None,
    expect: int | None = None,
    expect_digest: str | None = None,
    clock: Callable[[], datetime] = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RederiveResult:
    """Re-derive the selected rows from their archived bodies (module docstring).
    Without `release` a dry run, read-only throughout: it raises `RederiveRefused`
    on any match failure and lists the other refusals. With `release` it writes
    the dry run's `expect` corrections, whose digest is `expect_digest`, or raises,
    writing nothing.

    Both first match on a read-only connection and close it, then download every
    input with no store connection open, so a bounded iCloud wait never holds the
    store lock (#3001). The second pass (under the write lock for a write) matches
    again and refuses if the inputs changed; a body evicted in between is refused
    by `read_body`, not downloaded."""
    if not mode.strip() or mode == REPAIR:
        raise RederiveRefused("name the writer runs' mode (not repair: a re-derive's own)")
    if release is None and (expect is not None or expect_digest is not None):
        flag = "--expect" if expect is not None else "--expect-digest"
        raise RederiveRefused(f"{flag} needs --release: a dry run writes nothing")
    if release is not None and (expect is None or expect_digest is None):
        raise RederiveRefused(
            "a re-derive writes only a dry run's corrections: pass --expect and --expect-digest"
        )
    started = ensure_tz_aware_utc(clock(), field_name="clock()")
    with open_read_only(settings) as conn:
        if release is not None and (why := release_refusal(conn, release)):
            raise RederiveRefused(f"{why}; nothing written")
        downloaded = _hashes(
            _matched(conn, settings, mode=mode, accessions=accessions, file_dates=file_dates)[2]
        )
    raw_archive.download_inputs(settings, downloaded, icloud, clock=monotonic, sleep=sleep)
    opened = open_read_only(settings) if release is None else open_for_write(settings)
    with opened as conn:
        if release is not None and (why := release_refusal(conn, release)):
            raise RederiveRefused(f"{why}; nothing written")
        selected, index, matched = _matched(
            conn, settings, mode=mode, accessions=accessions, file_dates=file_dates
        )
        hashes = _hashes(matched)
        if hashes != downloaded:
            raise RederiveRefused(
                "the matched inputs changed since the download (another run wrote meanwhile); "
                "nothing written; rerun the dry run"
            )
        corrections, unchanged, refused = _compare(settings, matched, icloud)
        refused += _window_refusals(conn, corrections)
        result = RederiveResult(
            mode=mode,
            release=release,
            rows=len(selected),
            runs=tuple(sorted({m.run_id for m in matched})),
            hashes=hashes,
            unchanged=unchanged,
            corrections=tuple(corrections),
            refused=tuple(refused),
        )
        if release is None:
            return result
        if refused:
            raise RederiveRefused(
                f"{len(refused)} refusals; nothing written:\n" + "\n".join(refused)
            )
        if len(corrections) != expect:
            raise RederiveRefused(
                f"found {len(corrections)} corrections where the dry run's --expect is {expect}; "
                "nothing written"
            )
        if result.digest != expect_digest:
            raise RederiveRefused(
                f"found corrections with digest {result.digest} where the dry run's "
                f"--expect-digest is {expect_digest}; nothing written"
            )
        if not corrections:
            return result
        at = ensure_tz_aware_utc(clock(), field_name="clock()")
        written = _apply(conn, corrections, at)
        run_id = uuid.uuid4().hex
        digest = raw_archive.archive_manifest(
            settings, _repair_manifest(run_id, corrections, written, index=index, mode=mode)
        ).sha256
        source = di.INDEX_WRITER if index else "edgar"
        message = (
            f"provenance_sha256={digest}; raw rederive: {len(corrections)} rows "
            f"from {len(hashes)} archived bodies; release {release}"
        )
        write_run(conn, run_id, started, at, SourceRun(source, REPAIRED, 0, "", message), REPAIR)
        return result
