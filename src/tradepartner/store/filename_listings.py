"""Pure confirmation of 10-K instance prefixes as filing-name listings.

A cover filing or Form 25 naming several classes is one set-valued reading:
a prefix matches it when it names any member. This resolves the class-of-two
case in the #1643 orchestrator comment of 2026-10-13. Readings from separate
filings remain independent. The owner deny-list takes precedence over every
other verdict, as #1605 records for the #1597 amendment. After it, a
co-registrant's copy of a combined filing and a prefix that is no symbol are
refused (#2941) before any reading is compared.
"""

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from tradepartner.adapters.filings import FilenameListing

Verdict = Literal[
    "confirmed",
    "single_reading",
    "renamed",
    "refused_contradicted",
    "refused_owner",
    "refused_coregistrant",
    "refused_symbol",
]

#: The shape of a US equity symbol a filename prefix must have to be one
#: (#2941): one to five letters, then at most one class or series suffix of
#: one or two letters after `.`, `-` or `/` (`BRK-B`, `HEI/A`). A prefix with
#: a digit, an underscore or a longer run is filename residue
#: (`ETR-12312019X10K`, `a10-k20199282019`) or a company abbreviation, never
#: a ticker; every US-listed common symbol fits.
FILENAME_SYMBOL = re.compile(r"[A-Z]{1,5}(?:[.\-/][A-Z]{1,2})?")


@dataclass(frozen=True)
class ClassTicker:
    """A ticker named for a security class by a cover page or Form 25."""

    ticker: str
    class_id: str


@dataclass(frozen=True)
class IndependentReading:
    """All ticker and class pairs named by one cover filing or Form 25."""

    tickers: tuple[ClassTicker, ...]


@dataclass(frozen=True)
class Confirmation:
    """One pre-cover 10-K's verdict and class, if the prefix may be written."""

    filing: FilenameListing
    verdict: Verdict
    class_id: str | None
    class_read: bool


class FilenameClassConflict(ValueError):
    """One security's readings assign a ticker to two class ids (#2929).

    A per-CIK data verdict, not a store fault: the caller writes no
    filename rows for that CIK and counts it, and every other CIK builds."""

    def __init__(self, ticker: str) -> None:
        super().__init__(f"conflicting class assignment for {ticker}")
        self.ticker = ticker


def normalize_ticker(value: str) -> str:
    """The case- and whitespace-free spelling every filename reading is keyed
    by: a Form 25 exhibit file name spells `avy25`, a cover page `AVY25`."""
    return value.strip().upper()


_ticker = normalize_ticker


def is_filename_symbol(ticker: str) -> bool:
    """Whether a filename reading's ticker has a symbol's shape (#2941)."""
    return FILENAME_SYMBOL.fullmatch(_ticker(ticker)) is not None


def is_filename_listing_refused(cik: str, ticker: str, refused: Collection[str]) -> bool:
    """Apply the owner's CIK:TICKER deny-list to a 10-K or Form 25 name."""
    return f"{cik}:{_ticker(ticker)}" in refused


def confirm_filename_listings(
    filings: Sequence[FilenameListing],
    *,
    cover_readings: Sequence[IndependentReading],
    form25_readings: Sequence[IndependentReading],
    first_cover_accepted_at: datetime | None,
    first_cover_prefix: str | None,
    primary_class_id: str,
    refused: Collection[str],
    forms: Collection[str],
) -> tuple[Confirmation, ...]:
    """Judge each configured 10-K before the first cover listing's acceptance.

    Every threshold and list is supplied by the caller. ``filings`` are the
    security's 10-K readings, including their own ``dei_symbol`` tags. Cover
    and exhibit readings may name several classes each; the first cover
    filing's *instance prefix* is a later prefix, not an independent reading.
    The caller passes only the CIK's first cover-page filing as
    ``cover_readings``; later cover pages cannot contradict a pre-cover
    prefix. A ticker assigned to two class ids raises `FilenameClassConflict`
    because the rule cannot choose which security receives the listing; the
    caller fails that CIK closed (#2929), even when no 10-K precedes the first
    cover page (#3190).
    The result is sorted by acceptance and accession, independent of input
    order. A refused verdict has no class assignment because it writes no row.

    #2941: after the owner deny-list, a co-registrant's copy of a combined
    filing (`FilenameListing.coregistrant`: the prefix is the filer's ticker)
    is `refused_coregistrant`, and a prefix outside `FILENAME_SYMBOL` is
    `refused_symbol`. Neither is this CIK's later prefix for a rename. Both
    read only the filing itself.
    """
    candidates = tuple(
        sorted(
            (
                filing
                for filing in filings
                if filing.form in forms
                and (
                    first_cover_accepted_at is None or filing.accepted_at < first_cover_accepted_at
                )
            ),
            key=lambda filing: (filing.accepted_at, filing.accession, filing.ticker),
        )
    )
    # #3190: the conflict check runs before the empty-candidates return, so a
    # CIK with no pre-cover 10-K still fails closed for its Form 25 fallback.
    readings = tuple(cover_readings) + tuple(form25_readings)
    class_by_ticker: dict[str, str] = {}
    for reading in readings:
        for item in reading.tickers:
            ticker = _ticker(item.ticker)
            previous = class_by_ticker.setdefault(ticker, item.class_id)
            if previous != item.class_id:
                raise FilenameClassConflict(ticker)
    if not candidates:
        return ()
    ciks = {filing.cik for filing in candidates}
    if len(ciks) != 1:
        raise ValueError("filename confirmation requires one security's CIK")
    reading_sets = tuple(
        frozenset(_ticker(item.ticker) for item in reading.tickers) for reading in readings
    )
    later_prefixes = tuple(
        (filing.accepted_at, _ticker(filing.ticker))
        for filing in candidates
        if not filing.coregistrant and is_filename_symbol(filing.ticker)
    )
    output: list[Confirmation] = []
    for filing in candidates:
        prefix = _ticker(filing.ticker)
        tag = _ticker(filing.dei_symbol) if filing.dei_symbol is not None else None
        if is_filename_listing_refused(filing.cik, prefix, refused):
            verdict: Verdict = "refused_owner"
        elif filing.coregistrant:
            verdict = "refused_coregistrant"
        elif not is_filename_symbol(prefix):
            verdict = "refused_symbol"
        elif tag is not None and tag != prefix:
            verdict = "refused_contradicted"
        elif tag == prefix:
            verdict = "confirmed"
        else:
            differences = tuple(tickers for tickers in reading_sets if prefix not in tickers)
            if differences:
                later = {
                    ticker
                    for accepted_at, ticker in later_prefixes
                    if accepted_at > filing.accepted_at
                }
                if (
                    first_cover_prefix is not None
                    and first_cover_accepted_at is not None
                    and first_cover_accepted_at > filing.accepted_at
                ):
                    later.add(_ticker(first_cover_prefix))
                verdict = (
                    "renamed"
                    if all(later & tickers for tickers in differences)
                    else "refused_contradicted"
                )
            elif reading_sets:
                verdict = "confirmed"
            else:
                verdict = "single_reading"
        if verdict.startswith("refused_"):
            output.append(Confirmation(filing, verdict, None, False))
        else:
            class_id = class_by_ticker.get(prefix)
            output.append(
                Confirmation(filing, verdict, class_id or primary_class_id, class_id is not None)
            )
    return tuple(output)
