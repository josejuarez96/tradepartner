"""The identity answer key: hard security histories judged by outside sources
(#3275, ADR 0029 decision 3, T394).

**This key is never a source.** It is a hand-built list of security histories
whose true outcome is known from outside this repo: which entity (by CIK, not
by a store id) holds a ticker on which days, which days have bars, and when an
exchange line ended or did not. It judges the resolver; it never feeds one. No
universe, signal, backtest or research module imports it (a source scan in
`tests/lookahead/test_check_only_inputs.py` pins that).

The packaged file `identity_answer_key.toml` holds the histories. A history has

- `inputs`: the events that reach our store (a Form 25, an 8-K12B, a cover-page
  listing) as a minimal fixture,
- `bars`: the sessions that had bars (T387 and T388 read bars as evidence), and
- `expected`: the outcomes, written down from the cited source alone.

**Every fact carries a citation**: a filing accession, an exchange or company
notice, or a dated press release, with the document's date. The loader refuses
a fact with no citation, a citation of an unknown kind or with no usable
reference, and a citation dated after the check date (a source that did not yet
exist cannot back a fact checked at that date). A reference is an EDGAR
accession number or an `https` URL, never a path in this repo.

`match` is pure: it compares a history's expected outcomes with what a resolver
produced, given as `Observed` spans and line ends keyed by CIK and ticker, and
returns one `Miss` per expectation not met, each naming its citation.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from importlib import resources
from typing import Any, Final, Literal

_FILE: Final = "identity_answer_key.toml"
#: Citation kinds. An accession is an EDGAR filing; the others are documents
#: an exchange or the company published, with a URL.
CITATION_KINDS: Final = ("accession", "exchange_notice", "company_notice", "press_release")
_ACCESSION: Final = re.compile(r"^\d{10}-\d{2}-\d{6}$")
_CIK: Final = re.compile(r"^\d{10}$")
INPUT_KINDS: Final = ("form25", "8-k12b", "listing")
EXPECTED_KINDS: Final = ("holds", "bars", "line_end")

ExpectedKind = Literal["holds", "bars", "line_end"]


class AnswerKeyError(ValueError):
    """The key file breaks a rule of the format; the message names the fact."""


@dataclass(frozen=True)
class Citation:
    """Where a fact comes from: an outside document and its date."""

    kind: str
    ref: str
    date: date


@dataclass(frozen=True)
class InputEvent:
    """One event that reaches the store: a filing or a listing."""

    kind: str
    cik: str
    date: date
    exchange: str
    ticker: str
    predecessor_cik: str | None
    citation: Citation


@dataclass(frozen=True)
class BarSpan:
    """Sessions with bars for one entity and ticker, `first` to `last` inclusive
    (`last` None: still trading at the check date)."""

    cik: str
    ticker: str
    first: date
    last: date | None
    citation: Citation


@dataclass(frozen=True)
class Expected:
    """One expected outcome.

    `holds`: `cik` holds `ticker` from `first` to `last`. `bars`: the same span
    has bars. `line_end`: `cik`'s `exchange` line ended on `last`, the last
    session the security traded on that line, or, with `last` None, did not end.
    `holds` and `line_end` dates are calendar days; `bars` dates are sessions.
    """

    kind: ExpectedKind
    cik: str
    first: date | None
    last: date | None
    ticker: str | None
    exchange: str | None
    citation: Citation


@dataclass(frozen=True)
class History:
    """A hard security history: inputs, bars and the expected outcomes."""

    id: str
    title: str
    inputs: tuple[InputEvent, ...]
    bars: tuple[BarSpan, ...]
    expected: tuple[Expected, ...]


@dataclass(frozen=True)
class Span:
    """What a resolver produced: `cik` held (or had bars for) `ticker`."""

    cik: str
    ticker: str
    first: date
    last: date | None

    def __post_init__(self) -> None:
        if self.last is not None and self.last < self.first:
            raise ValueError(f"span {self.cik} {self.ticker} ends before it starts")


@dataclass(frozen=True)
class Observed:
    """A resolver's answer for one history, in outside identifiers.

    `holds` and `bars` are spans (a caller builds bar spans from sessions by
    joining consecutive trading sessions); `line_ends` maps `(cik, exchange)`
    to the day the line ended, None for a line still listed.
    """

    holds: tuple[Span, ...] = ()
    bars: tuple[Span, ...] = ()
    line_ends: Mapping[tuple[str, str], date | None] = field(default_factory=dict)


@dataclass(frozen=True)
class Miss:
    """An expectation the resolver did not meet."""

    history: str
    expected: Expected
    why: str

    def __str__(self) -> str:
        c = self.expected.citation
        return f"{self.history}: {self.why} [{c.kind} {c.ref}, {c.date.isoformat()}]"


def _fail(where: str, why: str) -> AnswerKeyError:
    return AnswerKeyError(f"{where}: {why}")


def _date(raw: Any, where: str, name: str) -> date:
    if isinstance(raw, datetime) or not isinstance(raw, date):
        raise _fail(where, f"{name} is not a calendar date")
    return raw


_KEYS: Final = {
    "history": {"id", "title", "input", "bars", "expected"},
    "input": {"kind", "cik", "date", "exchange", "ticker", "predecessor_cik", "cite"},
    "bars": {"cik", "ticker", "first", "last", "cite"},
    "expected": {"kind", "cik", "first", "last", "ticker", "exchange", "cite"},
    "cite": {"kind", "ref", "date"},
}


def _known(row: Mapping[str, Any], table: str, where: str) -> None:
    """Refuse a key the format does not define (a typo like `lst` would turn
    "ended on X" into "did not end")."""
    extra = sorted(set(row) - _KEYS[table])
    if extra:
        raise _fail(where, f"unknown key(s) {extra}")


def _text(row: Mapping[str, Any], name: str, where: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not value.strip():
        raise _fail(where, f"{name} must be a non-empty string")
    return value


def _optional_date(row: Mapping[str, Any], name: str, where: str) -> date | None:
    return _date(row[name], where, name) if name in row else None


def _cik(row: Mapping[str, Any], name: str, where: str) -> str:
    value = row.get(name)
    if not isinstance(value, str) or not _CIK.match(value):
        raise _fail(where, f"{name} must be a 10-digit CIK string")
    return value


def _citation(row: Mapping[str, Any], where: str, check_date: date) -> Citation:
    raw = row.get("cite")
    if not isinstance(raw, dict):
        raise _fail(where, "fact has no citation")
    _known(raw, "cite", where)
    kind, ref = raw.get("kind"), raw.get("ref")
    if kind not in CITATION_KINDS:
        raise _fail(where, f"citation kind {kind!r} is not one of {CITATION_KINDS}")
    if not isinstance(ref, str) or not ref.strip():
        raise _fail(where, "citation has no reference")
    if kind == "accession":
        if not _ACCESSION.match(ref):
            raise _fail(where, f"accession {ref!r} is not NNNNNNNNNN-YY-NNNNNN")
    elif not ref.startswith("https://"):
        raise _fail(where, f"{kind} reference {ref!r} must be an https URL")
    if "date" not in raw:
        raise _fail(where, "citation has no date")
    cited = _date(raw["date"], where, "citation date")
    if cited > check_date:
        raise _fail(where, f"citation is dated {cited}, after the check date {check_date}")
    return Citation(kind=kind, ref=ref, date=cited)


def parse_key(text: str, check_date: date | None = None) -> tuple[History, ...]:
    """Parse and validate the key's TOML; refuse any fact breaking the rules."""
    check = check_date or datetime.now(UTC).date()
    data = tomllib.loads(text)
    out: list[History] = []
    seen: set[str] = set()
    for h in data.get("history", []):
        _known(h, "history", "history")
        hid = h.get("id")
        if not isinstance(hid, str) or not hid or hid in seen:
            raise _fail("history", f"id {hid!r} is missing or repeated")
        seen.add(hid)
        inputs: list[InputEvent] = []
        for i, row in enumerate(h.get("input", [])):
            where = f"{hid}.input[{i}]"
            _known(row, "input", where)
            if row.get("kind") not in INPUT_KINDS:
                raise _fail(where, f"kind {row.get('kind')!r} is not one of {INPUT_KINDS}")
            succ = _cik(row, "predecessor_cik", where) if "predecessor_cik" in row else None
            inputs.append(
                InputEvent(
                    kind=row["kind"],
                    cik=_cik(row, "cik", where),
                    date=_date(row.get("date"), where, "date"),
                    exchange=_text(row, "exchange", where),
                    ticker=_text(row, "ticker", where),
                    predecessor_cik=succ,
                    citation=_citation(row, where, check),
                )
            )
        bars: list[BarSpan] = []
        for i, row in enumerate(h.get("bars", [])):
            where = f"{hid}.bars[{i}]"
            _known(row, "bars", where)
            first = _date(row.get("first"), where, "first")
            last = _optional_date(row, "last", where)
            if last is not None and last < first:
                raise _fail(where, "last is before first")
            bars.append(
                BarSpan(
                    cik=_cik(row, "cik", where),
                    ticker=_text(row, "ticker", where),
                    first=first,
                    last=last,
                    citation=_citation(row, where, check),
                )
            )
        expected: list[Expected] = []
        for i, row in enumerate(h.get("expected", [])):
            where = f"{hid}.expected[{i}]"
            _known(row, "expected", where)
            kind = row.get("kind")
            if kind not in EXPECTED_KINDS:
                raise _fail(where, f"kind {kind!r} is not one of {EXPECTED_KINDS}")
            e_first = _optional_date(row, "first", where)
            e_last = _optional_date(row, "last", where)
            if kind in ("holds", "bars") and (e_first is None or not row.get("ticker")):
                raise _fail(where, f"{kind} needs a ticker and a first date")
            if kind == "line_end" and not row.get("exchange"):
                raise _fail(where, "line_end needs an exchange")
            if e_first is not None and e_last is not None and e_last < e_first:
                raise _fail(where, "last is before first")
            expected.append(
                Expected(
                    kind=kind,
                    cik=_cik(row, "cik", where),
                    first=e_first,
                    last=e_last,
                    ticker=row.get("ticker"),
                    exchange=row.get("exchange"),
                    citation=_citation(row, where, check),
                )
            )
        if not expected:
            raise _fail(hid, "a history needs at least one expected outcome")
        out.append(
            History(
                id=hid,
                title=str(h.get("title", hid)),
                inputs=tuple(inputs),
                bars=tuple(bars),
                expected=tuple(expected),
            )
        )
    return tuple(out)


def load_answer_key(check_date: date | None = None) -> tuple[History, ...]:
    """Load the packaged key at `check_date` (default today, UTC)."""
    text = (resources.files("tradepartner.store") / _FILE).read_text(encoding="utf-8")
    return parse_key(text, check_date)


def _covered(
    spans: tuple[Span, ...], cik: str, ticker: str, first: date, last: date | None
) -> bool:
    """Whether one run of joined spans for `cik` and `ticker` covers `first` to
    `last` (None: to the end, so the run must itself be open). Spans that
    overlap or are at most one calendar day apart are joined."""
    mine = sorted((s for s in spans if s.cik == cik and s.ticker == ticker), key=lambda s: s.first)
    runs: list[tuple[date, date | None]] = []
    for s in mine:
        if runs:
            r_first, r_last = runs[-1]
            if r_last is None or s.first <= r_last + timedelta(days=1):
                joined = None if r_last is None or s.last is None else max(r_last, s.last)
                runs[-1] = (r_first, joined)
                continue
        runs.append((s.first, s.last))
    return any(
        r_first <= first and (r_last is None or (last is not None and r_last >= last))
        for r_first, r_last in runs
    )


def match(history: History, observed: Observed) -> list[Miss]:
    """Compare a history's expectations with a resolver's answer; pure.

    A `holds` or `bars` expectation is met when one joined run of observed
    spans for that CIK and ticker covers it. A `line_end` is met when the
    observed end for `(cik, exchange)` equals the expected one; a line the
    resolver does not know at all is a miss.
    """
    misses: list[Miss] = []
    for e in history.expected:
        if e.kind in ("holds", "bars"):
            assert e.ticker is not None and e.first is not None
            spans = observed.holds if e.kind == "holds" else observed.bars
            if not _covered(spans, e.cik, e.ticker, e.first, e.last):
                end = e.last.isoformat() if e.last else "open"
                misses.append(
                    Miss(
                        history.id,
                        e,
                        f"{e.kind}: {e.cik} {e.ticker} {e.first.isoformat()}..{end} not covered",
                    )
                )
        else:
            assert e.exchange is not None
            key = (e.cik, e.exchange)
            if key not in observed.line_ends:
                misses.append(Miss(history.id, e, f"line_end: {e.cik} {e.exchange} unknown"))
            elif observed.line_ends[key] != e.last:
                got = observed.line_ends[key]
                want = e.last.isoformat() if e.last else "no end"
                misses.append(
                    Miss(
                        history.id,
                        e,
                        f"line_end: {e.cik} {e.exchange} ended "
                        f"{got.isoformat() if got else 'never'}, expected {want}",
                    )
                )
    misses.extend(_double_holders(history, observed))
    return misses


def _double_holders(history: History, observed: Observed) -> list[Miss]:
    """A ticker held (or with bars) under two entities at once, or a span
    running into a window the key gives to another entity, is a miss: coverage
    alone passes spans that run too long."""
    misses: list[Miss] = []
    for e in history.expected:
        if e.kind not in ("holds", "bars") or e.first is None or e.ticker is None:
            continue
        for s in observed.holds if e.kind == "holds" else observed.bars:
            if s.ticker != e.ticker or s.cik == e.cik:
                continue
            if s.first <= (e.last or date.max) and (s.last or date.max) >= e.first:
                end = s.last.isoformat() if s.last else "open"
                why = f"{e.kind}: {s.cik} also has {e.ticker} {s.first.isoformat()}..{end} "
                misses.append(Miss(history.id, e, why + f"inside {e.cik}'s window"))
    return misses
