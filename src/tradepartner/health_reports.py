"""The report types of the health check: what `tradepartner.health` returns.

The dataclasses of `HealthReport` and its blocks, the rule-name constants and
schemas they read, and the two staleness functions `HealthReport.check_failures`
calls (T444, #3657: slice 1 of the `health.py` split). `tradepartner.health`
builds these and re-exports every name here; its module docstring describes
each block. This module never imports `tradepartner.health`."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from tradepartner.calendar import all_sessions
from tradepartner.gap import SurvivorshipGap
from tradepartner.store.dividend_reviews import EqualAmountGroup
from tradepartner.store.figi_identity import CANNOT_CHECK as FIGI_CANNOT_CHECK
from tradepartner.store.figi_identity import IDENTITY_SCHEMA as FIGI_IDENTITY_SCHEMA
from tradepartner.store.figi_identity import KINDS as FIGI_KINDS
from tradepartner.timeutil import ensure_tz_aware_utc
from tradepartner.universe import SHARES_CAP_JUMP_SCHEMA

#: The coverage check's rule (#1541): reported from the `coverage_reference`
#: block, never from `INTEGRITY_RULES` (module docstring).
COVERAGE_REFERENCE_NAMES = "coverage_reference_names"
#: The index answer key's rule (#1682, T189b): reported from the
#: `coverage_reference` block's `index_members`, never from `INTEGRITY_RULES`.
INDEX_MEMBERS_MISSING = "index_members_missing"
#: The freshness rule (#3329): the store has no price bars or is more than
#: `health.max_stale_sessions` sessions behind. Reported by `check_failures`
#: only, so the verdict line and the page keep describing the lag in words.
STORE_STALE = "store_stale"


#: The OpenFIGI types rule (#1713, T189c): reported from the `figi_types`
#: block, never from `INTEGRITY_RULES`, and only while
#: `openfigi.max_disagreements` is set.
FIGI_TYPES_DISAGREEMENTS = "figi_types_disagreements"

#: The OpenFIGI identity rule (plan T399, #3437): reported from the
#: `figi_identity` block, failing only while
#: `openfigi.max_identity_disagreements` is set.
FIGI_IDENTITY_DISAGREEMENTS = "figi_identity_disagreements"


@dataclass(frozen=True)
class IngestStatus:
    """One source's ingest record at `t` (see the module docstring)."""

    source: str
    last_ok_finished_at: datetime | None
    last_ok_cursor: str | None
    latest_status: str | None
    latest_started_at: datetime | None
    latest_message: str | None


@dataclass(frozen=True)
class Coverage:
    """Bars at `session` for the names live then, and the store's bar range."""

    session: date
    live: tuple[str, ...]
    missing: tuple[str, ...]
    share: float
    first_bar: date | None
    last_bar: date | None
    names_with_bars: int


@dataclass(frozen=True, eq=False)
class BarGaps:
    """Interior bar holes, one row per security that has any."""

    rows: pl.DataFrame

    @property
    def names_with_gaps(self) -> int:
        """How many securities have at least one hole."""
        return self.rows.height

    @property
    def missing_sessions(self) -> int:
        """Missing sessions summed over every security."""
        return int(self.rows["missing_sessions"].sum())


@dataclass(frozen=True)
class Unclassifiable:
    """Universe rule 1's missing-data names known at `t`."""

    unclassifiable: tuple[str, ...]
    unclassified: tuple[str, ...]

    @property
    def count(self) -> int:
        """Both groups together."""
        return len(self.unclassifiable) + len(self.unclassified)


@dataclass(frozen=True)
class StaticReliance:
    """Securities resting on `snapshot_static` rows at `t`."""

    ids: tuple[str, ...]
    by_table: dict[str, int]

    @property
    def count(self) -> int:
        """How many securities rely on at least one static row."""
        return len(self.ids)


@dataclass(frozen=True, eq=False)
class DelistedNames:
    """Securities whose current listing is delisted at `t`."""

    frame: pl.DataFrame

    @property
    def count(self) -> int:
        """How many securities are delisted."""
        return self.frame.height


@dataclass(frozen=True, eq=False)
class ListedWithoutBars:
    """Securities listed at `t` on a row that starts after a Form 25 and has
    no bar known at `t` (#1460; module docstring), one row per security."""

    frame: pl.DataFrame

    @property
    def count(self) -> int:
        """How many securities are listed with no bars after a Form 25."""
        return self.frame.height


@dataclass(frozen=True, eq=False)
class SplicedSeries:
    """Bar gaps with a close jump on one security at `t` (#3056; module
    docstring), one row per gap."""

    frame: pl.DataFrame

    @property
    def count(self) -> int:
        """How many gaps look spliced."""
        return self.frame.height


_SPLICED_SCHEMA: dict[str, Any] = {
    "security_id": pl.String,
    "prev_session": pl.Date,
    "session": pl.Date,
    "prev_close": pl.Float64,
    "close": pl.Float64,
    "ratio": pl.Float64,
    "gap_sessions": pl.Int64,
}


@dataclass(frozen=True, eq=False)
class PriceJumps:
    """The price-jump review list at `t` (#787), one row per jump, only sessions
    before `before` when it is set."""

    frame: pl.DataFrame
    before: date | None = None

    @property
    def pending(self) -> pl.DataFrame:
        """The jumps the owner has not accepted."""
        return self.frame.filter(~pl.col("accepted"))


@dataclass(frozen=True, eq=False)
class SharesOutliers:
    """The shares-outlier review list at `t` (#845), one row per out-of-line
    fact, only `as_of_date`s before `before` when it is set."""

    frame: pl.DataFrame
    before: date | None = None

    @property
    def pending(self) -> pl.DataFrame:
        """The out-of-line facts the owner has not accepted."""
        return self.frame.filter(~pl.col("accepted"))


@dataclass(frozen=True, eq=False)
class SharesCapJumps:
    """The shares cap-jump review list at `t` (#3055, `universe.shares_cap_jumps`),
    one row per fact whose implied cap is out of line, only `as_of_date`s before
    `before` when it is set. Report only: no integrity rule reads it."""

    frame: pl.DataFrame = field(default_factory=lambda: pl.DataFrame(schema=SHARES_CAP_JUMP_SCHEMA))
    before: date | None = None


@dataclass(frozen=True, eq=False)
class AcceptedSameDayPairs:
    """Same-start listing pairs the owner accepted (#855), one row per pair in
    `non_overlapping_listings`' violation shape; each would fail that rule
    without its `universe.accepted_same_day_pairs` entry."""

    frame: pl.DataFrame


@dataclass(frozen=True)
class StatementCoverage:
    """Statement-facts freshness and derived share at `t` (module
    docstring, #660, T77c). `fresh`/`total` are over `universe_as_of(T)`
    names (`total`); `derived`/`reported` count every known `gross_profit`
    row by `basis`, one per `(cik, period_end, period_days)`, over every
    CIK with a `securities` row known at `t` -- not only `universe_as_of`'s
    members, so a name excluded from today's universe (delisted, stale
    shares, ...) still counts here."""

    fresh: int
    total: int
    derived: int
    reported: int

    @property
    def share(self) -> float:
        """Coverage share, 0.0 when `total` is 0 (this module's usual
        empty-population convention, as `Coverage.share`)."""
        return self.fresh / self.total if self.total else 0.0

    @property
    def derived_share(self) -> float:
        """Derived share of known `gross_profit` rows, 0.0 when none."""
        n = self.derived + self.reported
        return self.derived / n if n else 0.0


@dataclass(frozen=True)
class StatementFacts:
    """Statement-facts health at `t` (#660, T77c). `enabled` is
    `edgar.statement_facts_enabled`; `coverage` is `None` while it is off
    (nothing to report on, and `universe_as_of` is not run for nothing).
    `counts` is `statement_counts` of the latest EDGAR run's message,
    empty when it names none (the switch has always been off, or the run
    predates T77b)."""

    enabled: bool
    coverage: StatementCoverage | None
    counts: dict[str, int]

    @property
    def vintage_late(self) -> int:
        """`statement_vintage_late` from the latest run's counts, 0 if
        unnamed."""
        return self.counts.get("vintage_late", 0)


@dataclass(frozen=True, eq=False)
class FilingEvents:
    """Filing-events health (#1358, T164e; module docstring). `enabled` is
    `edgar.filing_events_enabled`; `per_form` has one row per stored form
    (`form`, `rows`, `latest_accepted_at`), empty when the table is; `counts`
    is `filing_event_counts` of the latest EDGAR run's message, empty when it
    names none."""

    enabled: bool
    per_form: pl.DataFrame
    counts: dict[str, int]

    @property
    def warnings(self) -> tuple[str, ...]:
        """The two warnings: `items_missing` and `clock_suspect`, each
        while its count in the latest run is positive."""
        return tuple(
            f"filing_events_{name}: {self.counts[name]}"
            for name in ("items_missing", "clock_suspect")
            if self.counts.get(name, 0) > 0
        )


@dataclass(frozen=True, eq=False)
class IncrementalEdgar:
    """The incremental EDGAR ingest's two report-only warnings (#2629, T321;
    module docstring). `counts` is `incremental_edgar_counts` of the latest
    EDGAR run's message and `drift_ciks` the CIKs it names (up to ten)."""

    counts: dict[str, int]
    drift_ciks: tuple[str, ...] = ()

    @property
    def warnings(self) -> tuple[str, ...]:
        """`incremental_drift` (naming its CIKs) and `index_stamp_changed`,
        each while its count in the latest run is positive."""
        out: list[str] = []
        drift = self.counts.get("incremental_drift", 0)
        if drift > 0:
            named = f" ({' '.join(self.drift_ciks)})" if self.drift_ciks else ""
            out.append(f"incremental_drift: {drift}{named}")
        changed = self.counts.get("index_stamp_changed", 0)
        if changed > 0:
            out.append(f"index_stamp_changed: {changed}")
        return tuple(out)


@dataclass(frozen=True)
class Form4Compare:
    """The Form 4 rule 5 comparison of the latest Form 4 writer run (T351, #2714): the
    disagreeing and compared XML-fetched accessions, the configured share threshold
    (`unset` reports only) and the unresolved review markers in its manifest. Report-only:
    the run itself fails at or above the threshold. Nothing compared is `unchecked`."""

    disagreeing: int = 0
    compared: int = 0
    threshold: str = "unset"
    markers: int = 0

    @property
    def share(self) -> Decimal | None:
        """Disagreeing over compared; `None` when nothing was compared."""
        return Decimal(self.disagreeing) / Decimal(self.compared) if self.compared else None


@dataclass(frozen=True)
class RateSeriesHealth:
    """One stored series, its latest FRED run and report-only warnings."""

    series: str
    rows: int
    latest_observation_date: date | None
    latest_vintage_date: date | None
    latest_known_at: datetime | None
    revised_keys: int
    longest_gap_sessions: int
    run_counts: dict[str, int]
    stale_sessions: int | None
    last_daily_late_rows: int
    implausible_rows: int
    max_stale_sessions: int

    @property
    def warnings(self) -> tuple[str, ...]:
        """Quality warnings; none is an integrity failure."""
        out: list[str] = []
        if self.revised_keys:
            out.append(f"revised keys: {self.revised_keys}")
        if self.last_daily_late_rows:
            out.append(f"last daily late rows: {self.last_daily_late_rows}")
        if self.longest_gap_sessions > self.max_stale_sessions:
            out.append(f"longest gap: {self.longest_gap_sessions} sessions")
        if self.implausible_rows:
            out.append(f"outside plausible range: {self.implausible_rows}")
        return tuple(out)


@dataclass(frozen=True)
class RatesHealth:
    """Rate summaries for configured or stored series."""

    series: tuple[RateSeriesHealth, ...]


@dataclass(frozen=True)
class BorrowHealth:
    """The borrow-status block (module docstring, "Borrow status").
    `shortable` and `easy_to_borrow` are `None` with no observation;
    `stale_sessions` too."""

    enabled: bool
    rows: int
    assets: int
    latest_observation: datetime | None
    latest_known_at: datetime | None
    last_run_status: str | None
    run_counts: dict[str, int]
    shortable: int | None
    easy_to_borrow: int | None
    stale_sessions: int | None
    max_stale_sessions: int

    @property
    def warnings(self) -> tuple[str, ...]:
        """Report-only warnings; none is an integrity failure."""
        out: list[str] = []
        if not self.enabled:
            out.append("borrow.enabled is off: `ingest --source all` takes no snapshot")
        if self.latest_observation is None:
            if self.enabled:
                out.append("no borrow snapshot observed")
        elif self.stale_sessions is not None and self.stale_sessions > self.max_stale_sessions:
            out.append(
                f"latest observation {self.latest_observation.isoformat()} is "
                f"{self.stale_sessions} sessions old; maximum {self.max_stale_sessions}"
            )
        return tuple(out)


@dataclass(frozen=True, eq=False)
class MergersHealth:
    """The mergers block (module docstring, "Mergers", #1762, T248e): report
    only. `rows_by_kind` counts the latest revision of each identity known at
    `t`; `run_counts` is the latest Alpaca run's merger figures (empty with no
    run); `conflicts` lists two ids of one acquiree and one `effective_date`
    (the paper reader halts on them); `after_effective` lists the acquirees
    with a bar after their effective date (a wrong resolution or a broken
    deal). Neither list fails `--check`."""

    rows_by_kind: dict[str, int]
    latest_known_at: datetime | None
    run_counts: dict[str, int]
    conflicts: pl.DataFrame
    after_effective: pl.DataFrame


@dataclass(frozen=True, eq=False)
class DelistingReasonsHealth:
    """The delisting-reason block at `t` (spec amendment #2670, "Health and
    acceptance"; plan T329): report only, nothing here fails `--check` (the stamp
    rule `delisting_reasons_known_at_from_acceptance` is an integrity rule).

    `by_reason_source` counts the latest reason revision of each Form 25 episode
    known at `t` by `(reason, reason_source)`. `unknown` lists the episodes with no
    revision or an `unknown` one. `conflicted` lists the ended-listing keys the
    engine matches on (`security_id`, `delisting_form`, `delisting_exchange`,
    `delisting_filed_at`) that more than one revision answers, which the engine
    reads as `unknown` (`n_reason_conflicts`). `unmatched_items` counts, per reason
    8-K item, each `(security, 8-K)` of a security with an episode whose item falls
    outside every episode's `ReasonWindows`, or, for Item 2.01, inside one whose
    reason is not `merger_acquisition` (uncorroborated). `missing_consideration`
    lists each `merger_acquisition` episode the engine would value at the last
    close (a transferred listing is never an exit and is skipped): no delisted
    listing end, not exactly one acquiree `mergers` row in
    [`end_session`, `end_session` + the window], or a stock component without the
    acquirer's raw traded close on `effective_date`. `missing_eightk_evidence`
    names 8-K sourced reasons whose accession has no filing event visible at `t`."""

    by_reason_source: dict[tuple[str, str], int]
    unknown: pl.DataFrame
    conflicted: pl.DataFrame
    unmatched_items: dict[str, int]
    missing_consideration: pl.DataFrame
    missing_eightk_evidence: pl.DataFrame

    def lines(self) -> list[str]:
        """The block as text lines."""
        counts = ", ".join(
            f"{reason}/{source} {n}"
            for (reason, source), n in sorted(self.by_reason_source.items())
        )
        items = ", ".join(f"{item} {n}" for item, n in self.unmatched_items.items())
        missing = ", ".join(
            f"{row['security_id']} ({row['reason_evidence_accession']})"
            for row in self.missing_eightk_evidence.iter_rows(named=True)
        )
        return [
            f"delisting reasons: {counts or 'none'}",
            f"  unknown episodes: {self.unknown.height}; conflicted: {self.conflicted.height}",
            f"  unmatched 8-K items: {items}",
            f"  missing merger consideration: {self.missing_consideration.height}",
            f"  missing 8-K evidence: {self.missing_eightk_evidence.height}",
            *([f"    {missing}"] if missing else []),
        ]


@dataclass(frozen=True, eq=False)
class FigiTypes:
    """The `figi_types` block at `t` (module docstring, "OpenFIGI types").

    `rows` names every type disagreement, one row per security whose latest
    observation disagrees with its latest classification: both types, the
    current listing's title and the observation's `known_at`. `observations`
    is how many securities have a stored row known at `t` (mapped or not);
    `unmapped` how many of their latest rows are `mapped = FALSE`. `worst` is
    the gated number, disagreements plus unmapped; `max_disagreements` is
    `openfigi.max_disagreements` as the block was computed -- only while it
    is set does the block fail."""

    max_disagreements: int | None
    observations: int
    unmapped: int
    rows: pl.DataFrame

    @property
    def disagreements(self) -> int:
        """How many securities' latest type disagrees with their classification."""
        return self.rows.height

    @property
    def worst(self) -> int:
        """The gated count: named disagreements plus unmapped securities."""
        return self.disagreements + self.unmapped

    @property
    def checked(self) -> bool:
        """Whether any observation is known at `t`: with none, the block
        checked nothing and never passes a set gate."""
        return self.observations > 0

    @property
    def failed(self) -> bool:
        """While `max_disagreements` is set: nothing observed at `t`, or
        `worst` over it. Never while it is unset."""
        if self.max_disagreements is None:
            return False
        return not self.checked or self.worst > self.max_disagreements

    @property
    def verdict(self) -> str:
        """`nothing checked`, `<n> disagreements, <n> unmapped (report-only)`,
        then `pass (max <m>)` or `fail (max <m>)` while a threshold is set."""
        if not self.checked:
            return "nothing checked: fail" if self.failed else "nothing checked"
        head = f"{self.disagreements} disagreements, {self.unmapped} unmapped"
        if self.max_disagreements is None:
            return f"{head} (report-only)"
        return f"{head}: {'fail' if self.failed else 'pass'} (max {self.max_disagreements})"


@dataclass(frozen=True, eq=False)
class FigiIdentity:
    """The `figi_identity` block at `t` (module docstring, "OpenFIGI identity").

    `rows` is `store.figi_identity`'s frame, one row per disagreement;
    `observations` how many ids have a stored row known at `t`;
    `max_identity_disagreements` is `openfigi.max_identity_disagreements` as the
    block was computed -- only while it is set does the block fail."""

    max_identity_disagreements: int | None
    observations: int
    rows: pl.DataFrame

    @property
    def disagreements(self) -> int:
        """How many disagreement rows there are."""
        return self.rows.height

    @property
    def counts(self) -> dict[str, int]:
        """Rows per kind, every kind present (0 when none)."""
        return {kind: self.rows.filter(pl.col("kind") == kind).height for kind in FIGI_KINDS}

    @property
    def cannot_check(self) -> tuple[str, ...]:
        """What the stored OpenFIGI data cannot show."""
        return FIGI_CANNOT_CHECK

    def first_rows(self, count: int) -> pl.DataFrame:
        """The first `count` rows, in kind order."""
        return self.rows.head(count)

    @property
    def checked(self) -> bool:
        """Whether any observation is known at `t`."""
        return self.observations > 0

    @property
    def failed(self) -> bool:
        """While the threshold is set: nothing observed at `t`, or the rows
        over it. Never while it is unset."""
        if self.max_identity_disagreements is None:
            return False
        return not self.checked or self.disagreements > self.max_identity_disagreements

    @property
    def verdict(self) -> str:
        """`nothing checked`, `<n> disagreements (report-only)`, then
        `pass (max <m>)` or `fail (max <m>)` while a threshold is set."""
        if not self.checked:
            return "nothing checked: fail" if self.failed else "nothing checked"
        head = f"{self.disagreements} disagreements"
        if self.max_identity_disagreements is None:
            return f"{head} (report-only)"
        verdict = "fail" if self.failed else "pass"
        return f"{head}: {verdict} (max {self.max_identity_disagreements})"


@dataclass(frozen=True, eq=False)
class IndexMembersCheck:
    """The S&P 500 answer key against the universe on each sample session
    (#1682, T189b; module docstring). A check only: the key is never a
    universe, signal, filter or feature.

    `commit` and `through` are the key's pinned source SHA and last dated
    row (None when it could not be loaded, `error` saying why). `samples`:
    one row per sample session, `beyond_key` true after `through` (nothing
    resolved or counted there), `members` the key's intervals covering it,
    `hits` the ones whose resolved security is a universe member, `misses`
    the rest, `unresolved` the misses no listing row carries, `hit_share`
    hits over members (None beyond the key or with no members). `hits`: one
    row per hit `(session, ticker, security_id)`. `misses`: one row per miss
    with the resolution outcome, the universe's exclusion for the resolved
    security and its `bucket` (`rule_name:reason`, `unresolved` or
    `not_considered`). `max_missing` is `coverage.index_max_missing` as the
    block was computed: only while it is set does the block fail."""

    commit: str | None
    through: date | None
    error: str | None
    max_missing: int | None
    samples: pl.DataFrame
    hits: pl.DataFrame
    misses: pl.DataFrame

    @property
    def worst(self) -> int:
        """The most misses on one sample session inside the key (0 for none)."""
        counted = self.samples.filter(~pl.col("beyond_key"))
        return 0 if counted.is_empty() else int(counted["misses"].max())  # type: ignore[arg-type]

    @property
    def checked(self) -> bool:
        """Whether some key interval covered some sample session inside the key."""
        return self.error is None and int(self.samples["members"].sum()) > 0

    @property
    def failed(self) -> bool:
        """While `max_missing` is set: the key could not be loaded, it
        checked nothing (every sample session beyond it, or none covered), or
        some sample session's misses exceed it; a gate never passes on a check
        it did not make. Never while it is unset."""
        if self.max_missing is None:
            return False
        return not self.checked or self.worst > self.max_missing

    @property
    def verdict(self) -> str:
        """The one wording the CLI and page share: `key unavailable (...)`,
        `nothing checked`, then `<n> max missing per session` with
        `report-only`, `pass (max <m>)` or `fail (max <m>)`."""
        if self.error is not None:
            gate = "fail" if self.failed else "report-only"
            return f"key unavailable ({self.error}): {gate}"
        if not self.checked:
            return "nothing checked: fail" if self.failed else "nothing checked"
        head = f"{self.worst} max missing per session"
        if self.max_missing is None:
            return f"{head} (report-only)"
        return f"{head}: {'fail' if self.failed else 'pass'} (max {self.max_missing})"


@dataclass(frozen=True, eq=False)
class CoverageReference:
    """The coverage check against a named reference at `t` (#1541; module
    docstring). `samples`: one row per sample session (member count,
    reference names checked and missing, names listed and late-known, the
    late share). `reference_missing`: one row per reference name missing on
    a sample session, with the universe's exclusion for it. `late_known`: one
    row per late-known security per sample session, with its current listing
    at that session. `snapshot_ciks` and `member_ratio`: the latest sample's
    cross-check against the companies snapshot (`member_ratio` is `None`
    with no sample or no snapshot CIK). `check_enabled` is the setting the
    block was computed under: only then does it fail `HealthReport.ok`."""

    check_enabled: bool
    samples: pl.DataFrame
    reference_missing: pl.DataFrame
    late_known: pl.DataFrame
    snapshot_ciks: int
    member_ratio: float | None
    #: The answer-key check; None while `coverage.index_members` is off.
    index_members: IndexMembersCheck | None = None

    @property
    def failed(self) -> bool:
        """Whether a reference name is missing on some sample session."""
        return not self.reference_missing.is_empty()

    @property
    def checked(self) -> bool:
        """Whether some entry covered some sample session: with none, a block
        with nothing missing checked nothing and is never a pass."""
        return not self.samples.is_empty() and int(self.samples["references"].sum()) > 0

    @property
    def verdict(self) -> str:
        """`pass`, `nothing checked`, `fail (<n>)` while `check_enabled`, or
        `<n> missing (report-only)`: the one wording the CLI and page share."""
        missing = self.reference_missing.height
        if self.failed:
            return f"fail ({missing})" if self.check_enabled else f"{missing} missing (report-only)"
        return "pass" if self.checked else "nothing checked"


@dataclass(frozen=True, eq=False)
class IntegrityCheck:
    """One integrity rule: passed when `violations` is empty."""

    rule: str
    violations: pl.DataFrame

    @property
    def passed(self) -> bool:
        """True when nothing violates the rule."""
        return self.violations.is_empty()


@dataclass(frozen=True)
class FilenameListings:
    """Filing-name rows known at T and the names without a traded bar in span."""

    rows: int
    only_listing: int
    no_bars: pl.DataFrame


_DUPLICATE_DIVIDENDS_SCHEMA: dict[str, Any] = {
    "security_id": pl.Utf8,
    "ex_date": pl.Date,
    "amount": pl.Float64,
    "source_action_id": pl.Utf8,
    "known_at": pl.Datetime("us", "UTC"),
    "verdict": pl.Utf8,
}


@dataclass(frozen=True)
class DuplicateDividends:
    """Every equal-amount cash dividend key live at `t` and its class under
    the reviewed list (`store.dividend_reviews.equal_amount_groups`; module
    docstring, `duplicate_dividends`). Repaired keys are not kept."""

    groups: tuple[EqualAmountGroup, ...]

    def _of(self, group_class: str) -> tuple[EqualAmountGroup, ...]:
        return tuple(g for g in self.groups if g.group_class == group_class)

    @property
    def accepted(self) -> tuple[EqualAmountGroup, ...]:
        """Listed `distinct`: two declared payments of one amount."""
        return self._of("accepted")

    @property
    def unresolved(self) -> tuple[EqualAmountGroup, ...]:
        """Listed `unresolved`: the known gap, reported, never hidden."""
        return self._of("unresolved")

    @property
    def pending(self) -> tuple[EqualAmountGroup, ...]:
        """Listed `duplicate` still holding both ids: a repair not run yet
        (or deferred by its `--to`). A CLI warning, not a failure."""
        return self._of("pending")

    @property
    def violations(self) -> pl.DataFrame:
        """One row per identity of every `violation` key: the key, the id,
        its `known_at` and the listed verdict (null for an unlisted key)."""
        rows = [
            {
                "security_id": g.security_id,
                "ex_date": g.ex_date,
                "amount": g.amount,
                "source_action_id": i.source_action_id,
                "known_at": ensure_tz_aware_utc(i.known_at, field_name="known_at"),
                "verdict": None if g.reviewed is None else g.reviewed.verdict,
            }
            for g in self._of("violation")
            for i in g.identities
        ]
        return pl.DataFrame(rows, schema=_DUPLICATE_DIVIDENDS_SCHEMA)


@dataclass(frozen=True, eq=False)
class SuccessorChains:
    """The live `successor_chains` rows at `t` (#821, T187c), each with the
    hand-over day and the close ratio across it from the stored bars, the
    predecessor facts dated after the hand-over, and the latest EDGAR run's
    candidates beside them. A price break is a warning, never a failure."""

    frame: pl.DataFrame
    predecessor_facts: pl.DataFrame
    candidates: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        """How many live chains there are."""
        return self.frame.height

    @property
    def breaks(self) -> tuple[str, ...]:
        """The successors whose close ratio is out of bounds."""
        return tuple(self.frame.filter(pl.col("price_break"))["successor_id"].to_list())


@dataclass(frozen=True, eq=False)
class HealthReport:
    """Everything the health command and page show (module docstring)."""

    t: datetime
    session: date
    ingests: tuple[IngestStatus, ...]
    coverage: Coverage
    gaps: BarGaps
    survivorship: SurvivorshipGap
    unclassifiable: Unclassifiable
    static_reliance: StaticReliance
    delisted: DelistedNames
    listed_without_bars: ListedWithoutBars
    price_jumps: PriceJumps
    shares_outliers: SharesOutliers
    accepted_same_day_pairs: AcceptedSameDayPairs
    settings: dict[str, Any]
    statement: StatementFacts
    filing_events: FilingEvents
    rates: RatesHealth
    borrow: BorrowHealth
    mergers: MergersHealth
    figi_types: FigiTypes
    duplicate_dividends: DuplicateDividends
    implausible_form4_dates: int
    filename_listings: FilenameListings
    successor_chains: SuccessorChains
    integrity: tuple[IntegrityCheck, ...]
    #: `None` when the block was not computed ("not computed", never a pass).
    coverage_reference: CoverageReference | None = None
    #: The incremental EDGAR ingest's warnings (#2629).
    incremental_edgar: IncrementalEdgar = field(default_factory=lambda: IncrementalEdgar({}))
    #: The latest Form 4 writer run's rule 5 comparison (T351); `None` before one ran.
    form4_compare: Form4Compare | None = None
    #: The shares cap-jump review list (#3055).
    shares_cap_jumps: SharesCapJumps = field(default_factory=SharesCapJumps)
    #: Bar gaps with a close jump on one id (#3056); report-only.
    spliced: SplicedSeries = field(
        default_factory=lambda: SplicedSeries(pl.DataFrame([], schema=_SPLICED_SCHEMA))
    )
    #: Delisting reasons and their evidence (plan T329); report-only.
    delisting_reasons: DelistingReasonsHealth | None = None
    #: `health.max_stale_sessions` at report time (#3329), read by `check_failures`.
    max_stale_sessions: int = 0
    #: The OpenFIGI identity cross-check (plan T399); report-only while unset.
    figi_identity: FigiIdentity = field(
        default_factory=lambda: FigiIdentity(None, 0, pl.DataFrame([], schema=FIGI_IDENTITY_SCHEMA))
    )

    @property
    def failures(self) -> tuple[str, ...]:
        """The failing integrity rules, in `INTEGRITY_RULES` order, then
        `coverage_reference_names` when the block was computed with
        `coverage.check_enabled` on and a reference name is missing, then
        `index_members_missing` when the block's answer-key check failed
        (`IndexMembersCheck.failed`: only while `coverage.index_max_missing`
        is set), then `figi_types_disagreements` when the `figi_types` block
        failed (`FigiTypes.failed`: only while `openfigi.max_disagreements`
        is set), then `figi_identity_disagreements` when the `figi_identity` block
        failed (only while `openfigi.max_identity_disagreements` is set)."""
        failed = tuple(check.rule for check in self.integrity if not check.passed)
        block = self.coverage_reference
        if block is not None and block.check_enabled and block.failed:
            failed += (COVERAGE_REFERENCE_NAMES,)
        if block is not None and block.index_members is not None and block.index_members.failed:
            failed += (INDEX_MEMBERS_MISSING,)
        if self.figi_types.failed:
            failed += (FIGI_TYPES_DISAGREEMENTS,)
        if self.figi_identity.failed:
            failed += (FIGI_IDENTITY_DISAGREEMENTS,)
        return failed

    @property
    def ok(self) -> bool:
        """True when nothing in `failures` fails."""
        return not self.failures

    @property
    def check_failures(self) -> tuple[str, ...]:
        """What `health --check` fails on: `failures`, then `store_stale` when
        the store has no price bars or is more than `health.max_stale_sessions`
        sessions behind; the one-session gap before tonight's ingest
        (`stale_gap_expected`) is never a failure (#3329)."""
        stale = stale_sessions(self)
        limit = self.max_stale_sessions
        if stale is None or (stale > limit and not stale_gap_expected(self)):
            return (*self.failures, STORE_STALE)
        return self.failures


#: When the scheduled ingest runs (America/New_York). Source: the launchd plist in
#: docs/runbooks/scheduling.md; change the two together.
INGEST_TIME_ET = time(18, 30)
_ET = ZoneInfo("America/New_York")


def stale_sessions(report: HealthReport) -> int | None:
    """Sessions between the store's last bar and the last completed session at the
    report time; `None` when the store has no bars."""
    last_bar = report.coverage.last_bar
    if last_bar is None:
        return None
    sessions = all_sessions()
    return bisect_right(sessions, report.session) - bisect_right(sessions, last_bar)


def stale_gap_expected(report: HealthReport) -> bool:
    """True when the store is one session behind only because today's scheduled
    ingest has not run yet (after the close, before `INGEST_TIME_ET`)."""
    local = report.t.astimezone(_ET)
    return (
        stale_sessions(report) == 1
        and local.date() == report.session
        and local.time() < INGEST_TIME_ET
    )
