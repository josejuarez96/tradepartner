"""Edgar and Master config sections and their parse helpers (R1, issue #2512).

Moved verbatim from `tradepartner.config`, which re-exports every name here.
Imports only `config_base`, never `tradepartner.config`.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from tradepartner.config_base import _ClosedConfig

# `edgar.statement_tags`' default (spec amendment 2026-10-03, #660, decision
# (a)): canonical names, each an ordered list of fallbacks, every one the
# concept itself, never a near relative.
#
# #1556 adds a financial-issuer revenue fact and the two constituents of its
# one documented definition, under names that never touch `revenue` (and so
# never touch `gross_profit`). `revenue_financial` is a financial issuer's one
# documented revenue quantity from its own filing: it holds
# **`us-gaap:RevenuesNetOfInterestExpense`** (the issuer's total revenue net of
# interest expense, the concept itself) when the filing reports it, else the
# sum of **net interest income (`us-gaap:InterestIncomeExpenseNet`) plus
# noninterest income (`us-gaap:NoninterestIncome`)**. The sum is computed by the
# ingest's derived-candidate rule (`ingest._derived_candidates`) whenever both
# constituents are the stored vintages of **one filing and one period** (never
# mixing filings), whatever `revenue` tag that filing carries, and
# stored like any derived row: `basis = derived`, `xbrl_tag` the join of the
# two source tags by the operator (`+`). A reported
# `RevenuesNetOfInterestExpense` row is `basis = reported`. The constituents
# are parsed and stored as filed; they are named here so the derivation can
# read them from the same filing's records. Keeping this out of `revenue`
# leaves non-financial issuers' `revenue` and every `gross_profit` byte-identical
# to before and keeps every row point-in-time; a consumer wanting one revenue
# series coalesces `revenue` and `revenue_financial` at read time, taking the
# later `known_at` of the rows it reads. Gross profit
# stays N/A for financials (no `cost_of_revenue`). IFRS issuers are out of
# scope (#1556).
_STATEMENT_TAGS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "us-gaap:Revenues",
        "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
        "us-gaap:SalesRevenueNet",
        "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
        "us-gaap:SalesRevenueGoodsNet",
        "us-gaap:SalesRevenueServicesNet",
    ),
    # #1556: a financial issuer's own revenue fact (see the comment above).
    "revenue_financial": ("us-gaap:RevenuesNetOfInterestExpense",),
    "cost_of_revenue": (
        "us-gaap:CostOfRevenue",
        "us-gaap:CostOfGoodsAndServicesSold",
        "us-gaap:CostOfGoodsSold",
        "us-gaap:CostOfServices",
    ),
    "gross_profit": ("us-gaap:GrossProfit",),
    "total_assets": ("us-gaap:Assets",),
    "operating_cash_flow": (
        "us-gaap:NetCashProvidedByUsedInOperatingActivities",
        "us-gaap:NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    # #1556: the constituents of the financial-revenue sum above.
    "net_interest_income": ("us-gaap:InterestIncomeExpenseNet",),
    "noninterest_income": ("us-gaap:NoninterestIncome",),
}
#: A CIK as the store keys it, and a lettered common-class member (#1169).
_CIK = re.compile(r"\d{10}")
_CLASS_MEMBER = re.compile(r"CommonClass[A-Z]")
_QUALIFIED_TAG = re.compile(r"[A-Za-z][A-Za-z0-9-]*:[A-Za-z_][A-Za-z0-9_]*")
#: An `edgar.filename_listings_refused` entry (#1597): the CIK, then the
#: ticker derived no more for it.
_REFUSED_FILENAME = re.compile(r"\d{10}:[A-Z0-9][A-Z0-9.\-/]*")


def _default_edgar_cache_dir() -> str:
    """`data/edgar_cache`, anchored to the project root the same way `.env` is.

    A relative default would resolve against the process's current working
    directory, which breaks the moment `cli_record`/ingest run from a
    different directory (e.g. a scheduled job run from `$HOME`); T2 review
    round 2 (safety-reviewer) flagged this after `download_filing_file`
    started writing there.
    """
    return str(Path(__file__).resolve().parents[2] / "data" / "edgar_cache")


class EdgarConfig(BaseModel):
    """SEC EDGAR access, incl. `edgartools`' local cache.

    `requests_per_second`/`retry_backoff_seconds`/`request_timeout_seconds`/
    `header_bytes` were added in T2 review round 2 (safety-reviewer SHOULD
    FIX): `adapters/edgar_raw.py`'s throttle, retry backoff, HTTP timeout
    and SGML-header slice size were hardcoded module constants; CLAUDE.md
    requires thresholds to come from config. `max_retry_after_seconds` was
    added in round 3 (safety-reviewer MUST FIX): an SEC response naming an
    unreasonably large (or non-finite) `Retry-After` must not make
    `edgar_raw` sleep for that long, or at all, on a NaN/infinite value.
    `retry_max_attempts`/`retry_backoff_cap_seconds`/`rate_limit_wait_seconds`
    were added in #554 (research #572 pitfalls P1/P2/P10): a single 1-second
    retry couldn't outlast a real outage or SEC's rate-limit block, and a
    truncated zip just failed the whole run. `retry_max_attempts` caps the
    capped-exponential-backoff loop on `429`/`503`/a transport error;
    `retry_backoff_cap_seconds` caps that backoff's own growth (separately
    from `max_retry_after_seconds`, which caps how far an SEC-sent
    `Retry-After` is trusted); `rate_limit_wait_seconds` is the one wait
    before `403` (SEC's rate-limit block) is retried once, then failed.
    Every field here is `gt=0`: a zero or negative throttle/timeout/backoff
    is nonsensical and would either hang or hot-loop `edgar_raw`.

    `requests_per_second` defaults to 9, not 10 (#656, research #572 E3):
    SEC's 10 req/s is a ceiling, not a target; secedgar users saw 429s at
    9.7 req/s and edgartools defaults to 9.

    An unknown key is refused when the model is validated (#1037), so a
    mistyped `EDGAR__...` override fails instead of being silently ignored
    (`model_copy(update=...)` does not validate). A non-finite float is
    refused too (#1093): `inf` passes `gt=0`, and `requests_per_second=inf`
    would turn `edgar_raw`'s throttle off. Errors never echo the input value.
    """

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, hide_input_in_errors=True)

    cache_dir: str = Field(default_factory=_default_edgar_cache_dir)
    # `le=10` (#1108): SEC's ceiling is policy, not a tunable; a larger value would
    # shrink `edgar_raw`'s minimum interval toward 0. Tests that need no throttle
    # raise it with `model_copy(update=...)`, which does not validate.
    requests_per_second: float = Field(default=9.0, gt=0, le=10)
    retry_backoff_seconds: float = Field(default=1.0, gt=0)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    header_bytes: int = Field(default=4096, gt=0)
    max_retry_after_seconds: float = Field(default=120.0, gt=0)
    retry_max_attempts: int = Field(default=5, gt=0)
    retry_backoff_cap_seconds: float = Field(default=60.0, gt=0)
    rate_limit_wait_seconds: float = Field(default=600.0, gt=0)
    # T11b (#163): the EDGAR `FilingSource`. The full index starts in 1993; a
    # quarter's raw index is cached only once fetched this many days after its
    # Eastern-time end; above this many CIKs to stamp, stamping reads the nightly
    # `submissions.zip` instead of per-CIK submissions.
    index_first_year: int = Field(default=1993, ge=1993)
    index_settle_days: int = Field(default=3, ge=0)
    # T319b (#2629, #2659): every Saturday (US/Eastern) the nightly re-reads and
    # reconciles this many of the latest settled quarters, beside the HEAD-ETag test.
    reconcile_recent_quarters: int = Field(default=8, ge=1)
    incremental_index: bool = False
    verify_rotation_days: int = Field(default=28, ge=1)
    bulk_stamp_threshold_ciks: int = Field(default=500, gt=0)
    # #1382 guard: SEC's `submissions.zip` serves `acceptanceDateTime` moved by
    # New York's UTC offset (early for many CIKs, a look-ahead), so stamps never
    # come from it unless this is turned on; above the threshold, stamping goes
    # per CIK like below it. Turn on only once the zip is shown to match the
    # SGML header again (data-foundation plan, the #1382 repair task).
    bulk_stamps_enabled: bool = False
    # #1494 guard: since 2026-10 SEC's per-CIK submissions API has (on and off)
    # served `acceptanceDateTime` moved LATE by New York's UTC offset. With this
    # on, a CIK's new per-CIK stamps are written only after one of them matches
    # its SGML header's ACCEPTANCE-DATETIME (the master acceptance); on a
    # mismatch each new stamp is taken from its own SGML header instead.
    stamp_sgml_check: bool = True
    # #1686: after this many consecutive runs in which none of a CIK's three
    # newest SGML anchors can be read (404/410 or unparseable), the CIK's new
    # stamps are cached as the per-CIK API served them. That stamp is late by
    # New York's UTC offset (4 h EDT, 5 h EST) or exact, never early, so the
    # fallback delays a row and cannot look ahead. Counted per CIK on disk
    # (`sgml_unread/v1.json`), reset by any run whose anchor reads, and
    # reported on the run's message (`stamps from API fallback`).
    stamp_sgml_fallback_runs: int = Field(default=3, ge=1)
    # The longer range asked once for a stamp check's header that the first
    # `header_bytes` cut before `</SEC-HEADER>` (a Form 4 with many owners).
    stamp_header_bytes: int = Field(default=65536, gt=0)
    cover_page_forms: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        default_factory=lambda: ["10-K", "10-Q", "20-F", "40-F"], min_length=1
    )
    # T11g (#216, #231): the oldest year of the SEC Financial Statement and Notes
    # data sets to fetch. `2009` is FSN's own start; the default `2015` is
    # one year before the 2016 price start, so the latest SIC before any
    # T >= 2016 is already present.
    fsn_first_year: int = Field(default=2015, ge=2009)
    # T11d's keys, added in T11g so FSN extraction keeps these forms' SIC from
    # the first run (no FSN_VERSION bump later). 8-K is included so a de-SPAC's
    # new SIC arrives with its 8-K. `header_first_year` None means
    # `fsn_first_year`, so an override of one follows the other.
    header_forms: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        default_factory=lambda: ["S-1", "F-1", "10-12B", "8-K", "10-K", "10-Q", "20-F", "40-F"],
        min_length=1,
    )
    header_first_year: int | None = Field(default=None, ge=1993)
    # T11h: the failure policy (a filing that fails the same way on every
    # run). An accession failing identically this many consecutive counted
    # (Eastern) days is quarantined: no further request until its entry is
    # deleted or FAILURES_VERSION changes.
    max_filing_failures: int = Field(default=3, gt=0)
    # `check_failures()`'s per-group and cross-day thresholds: a group's (or
    # one error-class/base-form pair's) failures must clear both the count
    # floor and the share ceiling to fail the chunk, so neither a handful of
    # failures in a huge FSN period nor a tiny share of a huge run alone
    # trips it.
    min_failed_filings: int = Field(default=5, gt=0)
    max_failed_filing_share: float = Field(default=0.01, gt=0, le=1)
    # #578: how many of the fetch pass's parse failures the run message names
    # (the full list goes to a file under `cache_dir/validation/` whose path
    # the message gives), so a run with thousands stays readable. Also bounds
    # `check_failures`'s list of unaccepted filing failures (#578 part 3; the
    # full list is `failed_filings.json`, written on a non-dry run).
    max_validation_listed: int = Field(default=20, gt=0)
    # T77 (#660, spec amendment 2026-10-03): as-filed statement facts. T78
    # (#1127) turned the switch on after the real-store run passed; while off,
    # nothing reads the other three keys. `statement_tags` maps each canonical
    # fact name to its ordered `taxonomy:tag` fallbacks (the order is the
    # precedence within one filing); `statement_forms` filters on the
    # submissions record's form, never the companyfacts entry's; only
    # `statement_units` are read, any other unit is skipped and counted. None of
    # the three may be empty, nor any fact's fallback list (#1037): an empty one
    # would silently read no statement facts at all.
    statement_facts_enabled: bool = True
    statement_tags: dict[str, Annotated[list[str], Field(min_length=1)]] = Field(
        default_factory=lambda: {name: list(tags) for name, tags in _STATEMENT_TAGS.items()},
        min_length=1,
    )
    statement_forms: list[str] = Field(
        default_factory=lambda: ["10-K", "10-Q", "10-K/A", "10-Q/A", "10-KT", "10-QT"],
        min_length=1,
    )
    statement_units: list[str] = Field(default_factory=lambda: ["USD"], min_length=1)
    # T164e (#1358, spec amendment 2026-10-09): filing events. While the
    # switch is off the ingest never asks `filing_events`, so the adapter makes
    # no request and no cache write for it and no row is added. It stays off
    # until the real-store run has passed and the owner has closed #1382 (the
    # stamps clock: an early stamp is a look-ahead for an event read). Only a
    # form in `event_forms` is ever a row; never empty. Narrowing it later
    # fails `health --check` on the stored rows of a dropped form until a
    # rebuild (the amendment's open question 12).
    filing_events_enabled: bool = False
    # T179 (#1541, spec amendment 2026-10-11): the title fallback in
    # `store.classify` rule 6 (placeholder titles, whole-word type words,
    # issuer-name-first titles, class-letter shares). Off, every classification
    # is as before; the release's own PR (T181) flips it with the cache
    # version bumps, so a nightly ingest after a plain merge changes nothing.
    title_fallback: bool = True  # flipped by the release `cover-listings-1541` (T181)
    event_forms: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        default_factory=lambda: ["8-K", "8-K/A"], min_length=1
    )
    # #1541 (spec amendment 2026-10-11): both cover-page parsers read a
    # context dimensioned by `dei:EntityListingsExchangeAxis` as a listing
    # context. Off until the owner's release (T181) flips it together with
    # the `FSN_VERSION` and `COVER_VERSION` bumps: the caches are keyed by
    # those versions only, so a flip alone re-derives nothing already cached.
    exchange_axis_listings: bool = True  # flipped by the release `cover-listings-1541` (T181)
    # #1651, #1660: the cover-page parser and `parse_fsn` also read `CapitalUnitsByClassAxis` as a
    # class axis (Fifth Third) and merges a class's title, symbol and exchange
    # tagged on several contexts of one class member (Abbott 2020, P&G 2023).
    # Flipped on by the release `cover-listings-1541` (T181) at cache version 4,
    # then backed out by the owner in the same release (2026-10-14, cache
    # version 6): its class identities wait on the security-master review (#1682).
    cover_split_contexts: bool = False
    # #1169 (owner decision 2026-10-07, option 3): per CIK, the class member
    # (`CommonClass<letter>`) whose shares facts go to the CIK's one common
    # class although no title of that class names the letter. Owner-reviewed,
    # never a general rule: DICK'S (DKS), Tootsie Roll (TR) and Vimeo (VMEO)
    # title their one listed class "Common Stock" and tag its shares
    # `CommonClassA` (Class B is unlisted). Keys are 10-digit CIKs.
    class_member_overrides: dict[str, str] = Field(
        default_factory=lambda: {
            "0001089063": "CommonClassA",  # DKS
            "0000098677": "CommonClassA",  # TR
            "0001837686": "CommonClassA",  # VMEO
        }
    )
    # T190d (#1682 lever F): the per-class shares link at ingest. On, a
    # shares fact `fact_rows` leaves unmatched goes to the one common class
    # its member names by `universe.member_links_title` (BF.B, FOX, MKC), a
    # lettered member to the sole listed common when no title names a letter
    # and the filing's other classes are nominal (IBKR, SPG), and a common
    # class missing from the CIK's last `dead_class_filings` cover pages stops
    # being a candidate for a total (WBD's old series), all as of each fact's
    # acceptance.
    # Off, every `facts` row is as before. The release `rederive-1682` (T198)
    # flips it with the facts re-derive, never a nightly run before it.
    class_member_link: bool = False
    # Rule 3's page count: one fiscal year of periodic cover pages.
    dead_class_filings: int = Field(default=4, ge=1)
    # Rule 2's nominal class: every other member of the filing at most this
    # share of the largest (IBKR's 400 Class B shares beside 453M Class A).
    # The census on #1723: a looser rule put an unlisted Class B's count on
    # the listed common (Ford, Weber); DKS's 24M Class B is not nominal, so
    # `class_member_overrides` keeps its three entries.
    lettered_member_max_minor_share: float = Field(default=0.001, gt=0, lt=1)
    # T184 (#1597, spec amendment 2026-10-12): pre-2019 listings read from a
    # filing's own file names and its primary document's text. While the switch
    # is off the EDGAR `FilingSource` answers `filename_listings` as it does
    # today (no answer: T184b owns the fetch, T184c the confirmation rule,
    # T184e the write), makes no request and writes no cache entry, and the
    # store changes nothing. It stays off until the real-store run has passed
    # and #1597's open questions 22 to 24 are answered (T184h's gate).
    filename_listings: bool = False
    # The oldest year read: the amendment's rows are a 10-K's own, and the
    # window starts at FSN's 2015 so no reading is asked for a filing the
    # FSN data sets do not cover.
    filename_listings_since: date = date(2015, 1, 1)
    # The annual-report forms read (10-Qs are out of scope per the
    # amendment). Never empty: an empty one would silently read nothing.
    filename_listing_forms: list[Annotated[str, Field(min_length=1, pattern=r"\S")]] = Field(
        default_factory=lambda: ["10-K", "10-KT"], min_length=1
    )
    # The primary document's read cap for a 10-K's Section 12(b) table: the
    # read stops at the table or here, whichever comes first (#1597), so a
    # filing whose table is never written costs one bounded read.
    filename_document_max_bytes: int = Field(default=4_000_000, gt=0)
    # (CIK, ticker) spans whose `filing_name` rows the run derives no more
    # and `master-retract --release` withdraws: the amendment's rule for a
    # collision the Alpaca backfill counted. Default empty. A malformed
    # entry (not `CIK:TICKER`) refuses the config, so a typo never leaves a
    # live name derived silently.
    filename_listings_refused: list[str] = Field(default_factory=list)

    @field_validator("filename_listings_refused")
    @classmethod
    def _check_filename_listings_refused(cls, value: list[str]) -> list[str]:
        """Every entry is `CIK:TICKER` (#1597: owner-listed, never inferred)."""
        for entry in value:
            if not _REFUSED_FILENAME.fullmatch(entry):
                raise ValueError(
                    "edgar.filename_listings_refused: an entry is not `CIK:TICKER` "
                    f"(got {len(entry)} characters)"
                )
        return value

    @field_validator("class_member_overrides")
    @classmethod
    def _class_member_overrides_are_well_formed(cls, value: dict[str, str]) -> dict[str, str]:
        """Each key is a 10-digit CIK and each value `CommonClass<A-Z>` (#1169)."""
        for cik, member in value.items():
            if not _CIK.fullmatch(cik):
                raise ValueError("edgar.class_member_overrides: a key is not a 10-digit CIK")
            if not _CLASS_MEMBER.fullmatch(member):
                raise ValueError(f"edgar.class_member_overrides[{cik!r}] is not `CommonClass<A-Z>`")
        return value

    @field_validator("statement_tags")
    @classmethod
    def _statement_tags_are_qualified(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        """Every tag is `taxonomy:tag` (#660), so `us-gaap:Revenues` and a
        same-named concept in another taxonomy are never confused."""
        for name, tags in value.items():
            for tag in tags:
                if not _QUALIFIED_TAG.fullmatch(tag):
                    raise ValueError(
                        f"edgar.statement_tags[{name!r}]: {tag!r} is not `taxonomy:tag`"
                    )
        return value

    @property
    def header_start_year(self) -> int:
        """`header_first_year`, or `fsn_first_year` when it is unset."""
        return self.fsn_first_year if self.header_first_year is None else self.header_first_year


class MasterConfig(_ClosedConfig):
    """Security-master construction rules."""

    transfer_window_sessions: int = 5
    #: Sessions either side of a Form 25's filing session in which an 8-K12B
    #: (successor issuer) or a Form 15 (deregistration) belongs to it (#834):
    #: the real cases sit 6 sessions before to 9 after.
    reorganisation_window_sessions: int = 10
    #: Days after a Form 25's effective day before a companies snapshot still
    #: naming the ticker counts as listing evidence (#834), so a fetch before
    #: SEC drops a delisted ticker never relists it.
    snapshot_relisting_lag_days: int = 30
    issuer_forms: list[str] = Field(
        default_factory=lambda: [
            "10-K",
            "10-Q",
            "8-K",
            "20-F",
            "40-F",
            "S-1",
            "F-1",
            "10-12B",
            "25",
            "25-NSE",
        ]
    )
    static_columns: list[str] = Field(default_factory=lambda: ["name", "ticker", "exchange"])
    #: Successor ids (`<cik>@<YYYY-MM-DD>`, `-<n>` for a second one that day,
    #: #820) the owner accepted although the current rules no longer derive
    #: them (#922: MTCH's July 2020 IAC/Match separation, #828).
    #: `master-retract` never proposes their rows and reports them as kept,
    #: and an EDGAR ingest's check does not record them, so health's
    #: `underived_master_rows` does not fail on them. A malformed id refuses
    #: the config.
    keep_successors: list[str] = Field(default_factory=list)
    #: Owner-reviewed 1:1 successor-CIK chains (#821: the amendment of
    #: 2026-10-12): `<predecessor>-><successor>@<accession>`, each side a
    #: `<cik>` or `<cik>:<class>` security id (never a `<cik>@<date>` or a
    #: `keep_successors` id), the two sides of different CIKs, and the
    #: accession the successor CIK's 8-K12B or 8-K12G3. One id may appear in
    #: only one entry (a chained successor's own later reorganisation waits
    #: for an amendment). A malformed entry refuses the config, never chains
    #: silently.
    chains: list[str] = Field(default_factory=list)
    #: Successor ids the owner reviewed and struck (#821): never counted as
    #: candidates on the EDGAR run row (T187c). A malformed id refuses the
    #: config.
    chains_dismissed: list[str] = Field(default_factory=list)
    #: Sessions before a candidate's listing row that another company's
    #: security last listed its ticker and exchange (#821): `chain_candidates`
    #: names a filer whose ticker and exchange another company held this many
    #: sessions or fewer before it. 65, the #821 evidence bound.
    chain_window_sessions: int = Field(default=65, ge=0)
    #: The close ratio across a chain's hand-over above which `health` warns
    #: `chain_price_break` (#821): the ratio or its inverse. 1.15, so a break
    #: over 15% either way.
    chain_max_price_ratio: float = Field(default=1.15, gt=0)

    @field_validator("keep_successors")
    @classmethod
    def _check_keep_successors(cls, value: list[str]) -> list[str]:
        for entry in value:
            parse_successor_id(entry)
        return value

    @field_validator("chains")
    @classmethod
    def _check_chains(cls, value: list[str]) -> list[str]:
        parse_chains(value)
        return value

    @field_validator("chains_dismissed")
    @classmethod
    def _check_chains_dismissed(cls, value: list[str]) -> list[str]:
        for entry in value:
            check_security_id(entry, "master.chains_dismissed")
        return value


#: A chain entry's accession: an SEC accession, `NNNNNNNNNN-YY-NNNNNN`.
_CHAIN_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")

#: A chain entry's security id: a ten-digit CIK, optionally a `:<class slug>`.
#: A `<cik>@<date>` successor (and so a `keep_successors` id) never matches.
_CHAIN_ID = re.compile(r"\d{10}(?::[a-z0-9]+(?:-[a-z0-9]+)*)?")


def parse_chains(entries: list[str]) -> list[tuple[str, str, str]]:
    """Parse `master.chains` entries (#821) into `(predecessor_id,
    successor_id, accession)` tuples, in order. Raises `ValueError` naming the
    key and entry on any other shape: not exactly one `->`, an id that is not
    a `<cik>` or `<cik>:<class>` id (a `<cik>@<date>` or `keep_successors` id
    is never chained), two ids of the same CIK, a malformed accession, or an
    id used in another entry."""
    parsed: list[tuple[str, str, str]] = []
    seen: dict[str, str] = {}
    for entry in entries:
        predecessor, arrow, rest = entry.partition("->")
        if not arrow or "->" in rest:
            raise ValueError(
                f"master.chains entry {entry!r} is not '<predecessor>-><successor>@<accession>'"
            )
        successor, at, accession = rest.rpartition("@")
        if not at:
            raise ValueError(
                f"master.chains entry {entry!r} is not '<predecessor>-><successor>@<accession>'"
            )
        for side, value in (("predecessor", predecessor), ("successor", successor)):
            if _CHAIN_ID.fullmatch(value) is None:
                raise ValueError(
                    f"master.chains entry {entry!r}: {side} {value!r} is not a "
                    "'<cik>' or '<cik>:<class>' security id (never a "
                    "'<cik>@<date>' or a keep_successors id)"
                )
        # #1838: the parsed CIK, not the id's first ten characters.
        if predecessor.partition(":")[0] == successor.partition(":")[0]:
            raise ValueError(
                f"master.chains entry {entry!r}: predecessor and successor are "
                "the same CIK, so there is nothing to chain"
            )
        if _CHAIN_ACCESSION.fullmatch(accession) is None:
            raise ValueError(
                f"master.chains entry {entry!r}: accession {accession!r} is not "
                "'NNNNNNNNNN-YY-NNNNNN'"
            )
        for value in (predecessor, successor):
            other = seen.get(value)
            if other is not None:
                raise ValueError(
                    f"master.chains entry {entry!r}: {value!r} already appears in "
                    f"entry {other!r}; an id may be in only one entry"
                )
            seen[value] = entry
        parsed.append((predecessor, successor, accession))
    return parsed


#: A successor `security_id` as `store.master._successor` writes it.
_SUCCESSOR_ID = re.compile(r"(?P<cik>\d{10})@(?P<day>\d{4}-\d{2}-\d{2})(?:-(?P<n>[2-9]|[1-9]\d+))?")


def parse_successor_id(entry: str) -> tuple[str, date]:
    """A `master.keep_successors` entry, `"<cik>@<YYYY-MM-DD>"` with an
    optional `-<n>` (n >= 2), as `(cik, valid_from)`. Raises `ValueError` on
    any other shape or an invalid day."""
    match = _SUCCESSOR_ID.fullmatch(entry)
    if match is None:
        raise ValueError(
            f"master.keep_successors entry {entry!r} is not a successor id "
            "'<10-digit cik>@<YYYY-MM-DD>' (optionally '-<n>')"
        )
    try:
        day = date.fromisoformat(match["day"])
    except ValueError:
        raise ValueError(f"master.keep_successors entry {entry!r} names no valid day") from None
    return match["cik"], day


#: A master `security_id`: a ten-digit CIK, then nothing, a `:<class slug>`
#: (`store.master._new_class`) or an `@<day>` successor (`-<n>`, #820).
_SECURITY_ID = re.compile(
    r"\d{10}(?::[a-z0-9]+(?:-[a-z0-9]+)*|@(?P<day>\d{4}-\d{2}-\d{2})(?:-(?:[2-9]|[1-9]\d+))?)?"
)


def check_security_id(entry: str, key: str) -> str:
    """`entry` if it is a master security id (`_SECURITY_ID`, with a valid
    day for a successor); else `ValueError` naming the config `key`."""
    match = _SECURITY_ID.fullmatch(entry)
    if match is None:
        raise ValueError(
            f"{key} entry {entry!r} is not a security id "
            "'<10-digit cik>', '<cik>:<class>' or '<cik>@<YYYY-MM-DD>'"
        )
    if match["day"] is not None:
        try:
            date.fromisoformat(match["day"])
        except ValueError:
            raise ValueError(f"{key} entry {entry!r} names no valid day") from None
    return entry
