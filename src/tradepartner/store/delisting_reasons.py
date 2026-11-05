"""Point-in-time evidence about delisting episodes (plans T326, T327).

Reason rows are independent of the Form 25 delisting and its derived
listing end. Consumers opt in to this read explicitly.

**Building the rows (T327, spec amendment 2026-10-30, #2670 req 1).**
`build_delisting_reasons` is pure: Form 25 episodes, their Form 25 evidence and
the issuers' 8-K items in, `delisting_reasons` revisions out. Every piece of
evidence is a *set of compatible classes*, never a verdict:

- the Rule 12d2-2 provision (`PROVISION_CLASSES`) and the notice text
  (`classify_notice_text`) each name the classes they allow;
- 8-K Item 1.03 allows `bankruptcy`; Item 2.01 allows `merger_acquisition` but
  counts only when another piece of evidence already allows a merger (the
  issuer's own 2.01 may be an acquisition *by* it, so alone it does not match the
  security and a completed transaction); Item 3.01 is a bare notice and never
  narrows anything.

At each distinct acceptance time of an episode's evidence the classes known *by
then* are intersected. One class left is the reason; two or more is ambiguous and
none left is a conflict (reported, never overwritten), both `unknown`. A revision
is written when that outcome changes, `known_at` = that acceptance, so the as-of
read (latest `known_at <= t`) never sees evidence before it was filed, and an
8-K accepted after the close is first visible at the next close. An episode whose
first outcome is `unknown` gets no row (absence reads as unknown). Nothing here
reads or changes `delistings`.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import duckdb
import polars as pl

from tradepartner.adapters.edgar import REASON_8K_ITEMS, Form25Evidence, eightk_item_codes
from tradepartner.store.asof import _latest_as_of, _validate_t, filing_events_as_of
from tradepartner.store.db import insert_row
from tradepartner.store.delistings import delistings_as_of
from tradepartner.timeutil import ensure_tz_aware_utc

_EPISODE_KEY = (
    "security_id",
    "delisting_form",
    "delisting_class_title",
    "delisting_exchange",
    "delisting_filed_at",
)


def delisting_reasons_as_of(
    conn: duckdb.DuckDBPyConnection,
    t: datetime,
    security_ids: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Return reason revisions only for Form 25 episodes known at UTC instant `t`."""
    t = _validate_t(t)
    reasons = _latest_as_of(conn, "delisting_reasons", _EPISODE_KEY, t, security_ids)
    episodes = delistings_as_of(conn, t, security_ids).rename(
        {
            "form": "delisting_form",
            "class_title": "delisting_class_title",
            "exchange": "delisting_exchange",
            "filed_at": "delisting_filed_at",
        }
    )
    return reasons.join(episodes.select(_EPISODE_KEY), on=_EPISODE_KEY, how="semi")


# --- evidence to classes (T327) -----------------------------------------------

PERFORMANCE_FAILURE = "performance_failure"
BANKRUPTCY = "bankruptcy"
MERGER = "merger_acquisition"
VOLUNTARY = "voluntary_other"
UNKNOWN = "unknown"

#: What each Rule 12d2-2 provision allows (the departure labeling crosswalk's
#: `RULE_PROVISION_ARM`, mapped onto reason classes): (a)(1) and (a)(2) retire an
#: instrument, (c) is the exchange's own transfer or the issuer's withdrawal, none
#: a failure; (a)(3) is a merger, going private, reorganisation or bankruptcy and
#: (a)(4) a retirement or bankruptcy; (b) is an exchange-initiated removal, a
#: compliance failure or a bankruptcy. A provision outside the map allows nothing.
PROVISION_CLASSES: dict[str, frozenset[str]] = {
    "12d2-2(a)(1)": frozenset({VOLUNTARY}),
    "12d2-2(a)(2)": frozenset({VOLUNTARY}),
    "12d2-2(a)(3)": frozenset({MERGER, VOLUNTARY, BANKRUPTCY}),
    "12d2-2(a)(4)": frozenset({VOLUNTARY, BANKRUPTCY}),
    "12d2-2(b)": frozenset({PERFORMANCE_FAILURE, BANKRUPTCY}),
    "12d2-2(c)": frozenset({VOLUNTARY}),
}

_TEXT_PATTERNS: dict[str, re.Pattern[str]] = {
    BANKRUPTCY: re.compile(r"chapter\s+(7|11)\b|bankruptcy|receivership|insolven", re.I),
    MERGER: re.compile(
        r"\bmerg(er|ed)\b|acquisition|\bacquired\b|tender offer|business combination", re.I
    ),
    PERFORMANCE_FAILURE: re.compile(
        r"continued listing|fail(ed|ure)?\s+to\s+(comply|satisfy|meet)|minimum bid|"
        r"market value of (publicly held|listed)",
        re.I,
    ),
    VOLUNTARY: re.compile(r"voluntar|going private|deregist", re.I),
}

#: Evidence ranks when several pieces agree on the class: the issuer's 8-K item
#: first, then the notice text, then the bare provision.
_SOURCE_RANK = {"eightk_item": 0, "form25_text": 1, "form25_provision": 2}

_EpisodeKey = tuple[str, str, str, str, datetime]


def classify_notice_text(text: str | None) -> frozenset[str]:
    """The classes a Form 25 notice's text allows: each class whose cue words it
    contains (a text naming a bankruptcy and a continued-listing failure allows
    both). Blank or cue-less text allows nothing."""
    if not text:
        return frozenset()
    return frozenset(name for name, pattern in _TEXT_PATTERNS.items() if pattern.search(text))


def _default_before() -> dict[str, int]:
    return {"1.03": 365, "2.01": 60, "3.01": 120}


def _default_after() -> dict[str, int]:
    return {"1.03": 30, "2.01": 30, "3.01": 10}


@dataclass(frozen=True)
class ReasonWindows:
    """How far from the Form 25's acceptance an 8-K item may fall and still be
    evidence for that episode: days before and days after, per item."""

    before_days: Mapping[str, int] = field(default_factory=_default_before)
    after_days: Mapping[str, int] = field(default_factory=_default_after)

    def holds(self, item: str, filed_at: datetime, accepted_at: datetime) -> bool:
        """Whether an 8-K accepted at `accepted_at` carrying `item` is in range."""
        return (
            filed_at - timedelta(days=self.before_days[item])
            <= accepted_at
            <= filed_at + timedelta(days=self.after_days[item])
        )


@dataclass(frozen=True)
class Episode:
    """One Form 25 delisting episode of a security (`delistings`' key) and the
    issuer CIK its 8-Ks file under."""

    security_id: str
    cik: str
    form: str
    class_title: str
    exchange: str
    filed_at: datetime

    @property
    def key(self) -> _EpisodeKey:
        """The `delistings` episode key."""
        return (self.security_id, self.form, self.class_title, self.exchange, self.filed_at)


@dataclass(frozen=True)
class EightK:
    """An issuer 8-K or 8-K/A from `filing_events`, `items` verbatim."""

    cik: str
    form: str
    accession: str
    items: str
    accepted_at: datetime


@dataclass(frozen=True)
class ReasonConflict:
    """Evidence known together that allows no common class, reported not overwritten."""

    episode: _EpisodeKey
    known_at: datetime
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ItemCoverage:
    """Per reason item, counted once per (episode, 8-K): `matched` (in window, and
    for 2.01 corroborated by a merger-allowing piece of evidence) and `unmatched`
    (the issuer has an episode but the item is out of window or an uncorroborated
    2.01). An 8-K of an issuer with no episode is not counted."""

    matched: Mapping[str, int]
    unmatched: Mapping[str, int]


@dataclass(frozen=True)
class ReasonBuild:
    """`build_delisting_reasons`' result."""

    rows: tuple[dict[str, object], ...]
    conflicts: tuple[ReasonConflict, ...]
    coverage: ItemCoverage
    n_episodes: int
    #: Episodes whose final outcome is `unknown` (no row, or an `unknown` revision).
    n_unknown: int


@dataclass(frozen=True)
class _Observation:
    known_at: datetime
    source: str
    form: str
    accession: str
    classes: frozenset[str]
    needs_merger: bool = False
    #: An Item 3.01 notice: it allows no class, but a voluntary reading on a bare
    #: provision no longer stands beside it (a deficiency notice then a 12d2-2(c)
    #: withdrawal is a distressed exit, not a voluntary one).
    notice: bool = False


def _form25_observations(episode: Episode, evidence: Form25Evidence) -> list[_Observation]:
    out: list[_Observation] = []
    allowed = PROVISION_CLASSES.get(evidence.provision or "")
    if allowed:
        out.append(
            _Observation(
                evidence.accepted_at, "form25_provision", episode.form, evidence.accession, allowed
            )
        )
    text_classes = classify_notice_text(evidence.notice_text)
    if text_classes:
        out.append(
            _Observation(
                evidence.accepted_at, "form25_text", episode.form, evidence.accession, text_classes
            )
        )
    return out


def _live(known: Sequence[_Observation]) -> list[_Observation]:
    """`known` less a 2.01 with no merger-allowing observation beside it."""
    corroborated = any(MERGER in o.classes for o in known if not o.needs_merger)
    return [o for o in known if not o.notice and (corroborated or not o.needs_merger)]


def _resolve(
    known: Sequence[_Observation],
) -> tuple[str, _Observation | None, tuple[str, ...]]:
    """The outcome of the observations known together: `(reason, supporting
    observation, accessions of a conflict)`. Ambiguity and conflict are `unknown`."""
    live = _live(known)
    if not live:
        return UNKNOWN, None, ()
    common = frozenset.intersection(*(o.classes for o in live))
    if not common:
        return UNKNOWN, None, tuple(sorted({o.accession for o in live}))
    if len(common) > 1:
        return UNKNOWN, None, ()
    (reason,) = common
    best = min(live, key=lambda o: (_SOURCE_RANK[o.source], -o.known_at.timestamp()))
    if reason == VOLUNTARY and best.source == "form25_provision" and any(o.notice for o in known):
        return UNKNOWN, None, ()
    return reason, best, ()


def _episode_rows(
    episode: Episode, observations: list[_Observation], ingested_at: datetime
) -> tuple[list[dict[str, object]], list[ReasonConflict], str]:
    rows: list[dict[str, object]] = []
    conflicts: list[ReasonConflict] = []
    last: tuple[str, str, str | None] | None = None
    state: tuple[str, str, str | None]
    final = UNKNOWN
    for known_at in sorted({o.known_at for o in observations}):
        known = [o for o in observations if o.known_at <= known_at]
        final, support, clash = _resolve(known)
        if clash and (not conflicts or conflicts[-1].evidence != clash):
            conflicts.append(ReasonConflict(episode.key, known_at, clash))
        if support is None:
            state = (UNKNOWN, "unknown", None)
            form, accession = max(known, key=lambda o: o.known_at).form, None
        else:
            state = (final, support.source, support.accession)
            form, accession = support.form, support.accession
        if state == last or (last is None and final == UNKNOWN):
            continue
        last = state
        rows.append(
            {
                "security_id": episode.security_id,
                "delisting_form": episode.form,
                "delisting_class_title": episode.class_title,
                "delisting_exchange": episode.exchange,
                "delisting_filed_at": episode.filed_at,
                "reason": final,
                "reason_evidence_form": form,
                "reason_evidence_accession": accession,
                "reason_source": state[1],
                "reason_known_at": known_at,
                "known_at": known_at,
                "ingested_at": ingested_at,
                "source": "edgar",
                "provenance": "filing",
            }
        )
    return rows, conflicts, final


def build_delisting_reasons(
    episodes: Iterable[Episode],
    form25_evidence: Iterable[Form25Evidence],
    eightks: Iterable[EightK],
    *,
    ingested_at: datetime,
    windows: ReasonWindows | None = None,
) -> ReasonBuild:
    """`delisting_reasons` revisions for `episodes`, as the module note describes.

    `form25_evidence` joins an episode on its issuer CIK, form, class, exchange and
    `accepted_at == filed_at`; `eightks` on the issuer CIK within `windows`.
    Raises `ValueError` if evidence used was accepted after `ingested_at`
    (`known_at <= ingested_at`)."""
    ingested_at = ensure_tz_aware_utc(ingested_at, field_name="ingested_at")
    windows = windows or ReasonWindows()
    evidence_by_key: dict[tuple[str, str, str, str, datetime], list[Form25Evidence]] = defaultdict(
        list
    )
    for ev in form25_evidence:
        evidence_by_key[(ev.cik, ev.form, ev.class_title, ev.exchange, ev.accepted_at)].append(ev)
    eightks_by_cik: dict[str, list[EightK]] = defaultdict(list)
    for eightk in eightks:
        eightks_by_cik[eightk.cik].append(eightk)

    rows: list[dict[str, object]] = []
    conflicts: list[ReasonConflict] = []
    matched: dict[str, int] = dict.fromkeys(REASON_8K_ITEMS, 0)
    unmatched: dict[str, int] = dict.fromkeys(REASON_8K_ITEMS, 0)
    n_unknown = 0
    ordered = sorted(episodes, key=lambda e: (e.key, e.cik))
    for episode in ordered:
        observations: list[_Observation] = []
        for ev in evidence_by_key.get(
            (episode.cik, episode.form, episode.class_title, episode.exchange, episode.filed_at),
            [],
        ):
            observations.extend(_form25_observations(episode, ev))
        n_merger_items = 0
        for eightk in eightks_by_cik.get(episode.cik, []):
            codes = eightk_item_codes(eightk.items)
            for item in REASON_8K_ITEMS:
                if item not in codes:
                    continue
                if not windows.holds(item, episode.filed_at, eightk.accepted_at):
                    unmatched[item] += 1
                elif item == "3.01":
                    matched[item] += 1  # a bare notice: it establishes no class
                    observations.append(
                        _Observation(
                            eightk.accepted_at,
                            "eightk_item",
                            eightk.form,
                            eightk.accession,
                            frozenset(),
                            notice=True,
                        )
                    )
                else:
                    if item == "1.03":
                        matched[item] += 1
                    else:
                        n_merger_items += 1
                    observations.append(
                        _Observation(
                            eightk.accepted_at,
                            "eightk_item",
                            eightk.form,
                            eightk.accession,
                            frozenset({BANKRUPTCY if item == "1.03" else MERGER}),
                            needs_merger=item == "2.01",
                        )
                    )
        for obs in observations:
            if obs.known_at > ingested_at:
                raise ValueError(
                    f"{obs.accession}: accepted_at {obs.known_at.isoformat()} is after "
                    f"ingested_at {ingested_at.isoformat()}"
                )
        n_live_merger_items = sum(1 for o in _live(observations) if o.needs_merger)
        matched["2.01"] += n_live_merger_items
        unmatched["2.01"] += n_merger_items - n_live_merger_items
        episode_rows, episode_conflicts, final = _episode_rows(episode, observations, ingested_at)
        rows.extend(episode_rows)
        conflicts.extend(episode_conflicts)
        n_unknown += final == UNKNOWN
    return ReasonBuild(
        rows=tuple(rows),
        conflicts=tuple(conflicts),
        coverage=ItemCoverage(matched=matched, unmatched=unmatched),
        n_episodes=len(ordered),
        n_unknown=n_unknown,
    )


def write_delisting_reasons(conn: duckdb.DuckDBPyConnection, build: ReasonBuild) -> int:
    """Insert every row of `build`; return the number inserted."""
    for row in build.rows:
        insert_row(conn, "delisting_reasons", row)
    return len(build.rows)


def build_delisting_reasons_from_store(
    conn: duckdb.DuckDBPyConnection,
    form25_evidence: Iterable[Form25Evidence],
    *,
    ingested_at: datetime,
    windows: ReasonWindows | None = None,
) -> ReasonBuild:
    """`build_delisting_reasons` over the store's episodes (`delistings_as_of`) and
    8-K items (`filing_events_as_of`: forms 8-K and 8-K/A, the reason items), both
    read at `ingested_at`. Writes nothing; `write_delisting_reasons` does."""
    from tradepartner.store.master import securities_as_of

    ingested_at = ensure_tz_aware_utc(ingested_at, field_name="ingested_at")
    ciks = dict(securities_as_of(conn, ingested_at).select("security_id", "cik").iter_rows())
    episodes = [
        Episode(
            security_id=row["security_id"],
            cik=ciks[row["security_id"]],
            form=row["form"],
            class_title=row["class_title"],
            exchange=row["exchange"],
            filed_at=row["filed_at"],
        )
        for row in delistings_as_of(conn, ingested_at).iter_rows(named=True)
        if row["security_id"] in ciks
    ]
    events = filing_events_as_of(conn, ingested_at, ("8-K", "8-K/A"), REASON_8K_ITEMS)
    eightks = {
        (row["cik"], row["accession"]): EightK(
            cik=row["cik"],
            form=row["form"],
            accession=row["accession"],
            items=row["items"],
            accepted_at=row["accepted_at"],
        )
        for row in events.iter_rows(named=True)
    }
    return build_delisting_reasons(
        episodes, form25_evidence, eightks.values(), ingested_at=ingested_at, windows=windows
    )
