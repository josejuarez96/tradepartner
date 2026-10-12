"""The packet builder and the passage-kind sequence, pure.

Research-labeling spec req 3 and its 2026-10-06 amendment C2
(docs/specs/research-labeling.md). Builds the exact text sent to the model as
`models.ModelRequest.state` for one listing end's `A` or `B` call, and picks
the next passage kind to send given the last call's answer. No I/O, no store,
no network, no model client: the only input is one frame row's `documents`
column -- the corpus fetch's per-listing-end record
(`tradepartner.corpus.departure_fetch`'s `_record`), already carrying the one
8-K C2 selected and the per-document caps `edgar.cache_dir` applied at fetch
time. This module never imports `tradepartner.corpus` (boundary test (b)(v));
its own sentence cut (`_truncate_sentence`) duplicates the fetch's in
miniature so a packet's shape depends only on `row` and `limits`, not on
which settings the corpus was fetched under.

**Test (c)** (req 14) scans this module for any store table name as a whole
token and the owner's gold/review dataset names, with an empty allowlist; it
never reads `rule_*`, `gold_*`, `text_states`, `evidence_quote` or `decision`
-- the crosswalk and the review logic read those, this module only ever reads
`row["documents"]` (and `row["listing_end_id"]` for the record's
`document_ids`, which carries no table token). The text is wrapped between
fixed delimiters and labelled as filing text; the question and option set are
sent separately by the caller (`research.models`) and never appear inside the
delimiters (req 3). Filing text is untrusted, so a body's own copies of either
delimiter are rewritten to a bracketed form before the wrap
(`_neutralise_delimiters`, #1510): a body can neither close nor reopen the
delimited block, and a body carrying none of them ships unchanged.

`max_packet_tokens` is a hard refusal (`PacketTooLarge`), never a truncation
rule (C2): a packet whose estimated tokens (characters divided by
`limits.chars_per_token`) exceed it is never sent. The per-document caps
(`exhibit_max_chars`, `item_max_chars`, `eightk_max_chars`) are applied here,
each cut at a sentence boundary, never mid-word.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from typing import Any, Literal

from tradepartner.research.labeling.questions import UNRESOLVED, OptionSet

Kind = Literal["A", "B"]

#: The fixed delimiters (req 3): the question text and option set are never
#: inside them.
DELIM_START = "=== FILING TEXT START ==="
DELIM_END = "=== FILING TEXT END ==="

#: The same two delimiters in the form they take inside a filing body once
#: `_neutralise_delimiters` has rewritten them: the words they carry, between
#: square brackets, so the rewritten string can never match the delimiter
#: again. Derived from the delimiters above, so the two cannot drift.
_NEUTRAL_START = f"[{DELIM_START.strip('= ')}]"
_NEUTRAL_END = f"[{DELIM_END.strip('= ')}]"

#: The zero-width and format characters that can hide between any two
#: characters of a marker imitation, which `\s` does not match (`\u180e` is
#: one older Unicode releases still called a space). Every member is spelled
#: out: `frozenset` over the range string `"\u200b-\u200f"` would keep its
#: literal `-` and drop the code points between the ends, and this set is
#: membership-tested per character rather than handed to a regex engine.
_ZERO_WIDTH_SET = frozenset(
    chr(code)
    for code in (
        0x00AD,  # soft hyphen
        0x180E,  # Mongolian vowel separator
        *range(0x200B, 0x2010),  # ZWSP, ZWNJ, ZWJ, LRM, RLM
        *range(0x2060, 0x2065),  # word joiner and the invisible operators
        0xFEFF,  # zero-width no-break space (BOM)
    )
)

#: The characters a delimiter's own `=` side is made of: the ASCII `=` and
#: the fullwidth `=` (U+FF1D) a body can write instead.
_EQUALS_SET = frozenset("=\uff1d")

#: One marker as the matcher reads it: the words of the delimiter, in order,
#: and the bracketed form a match of it neutralises to.
_MARKERS = (
    (tuple(DELIM_START.strip().strip("=").split()), _NEUTRAL_START),
    (tuple(DELIM_END.strip().strip("=").split()), _NEUTRAL_END),
)


def _matching_view(body: str) -> tuple[str, list[int]]:
    """`body` in the view the marker matcher reads, and `origin`: the index
    every view character starts at in `body`, so a view span maps back to the
    original span it rewrites (#1510). Zero-width and format characters are
    dropped -- an imitation hides them between any two of its characters,
    inside a word or inside the `=` run alike -- and every run of whitespace
    (spaces, tabs, a CRLF, non-breaking spaces) collapses to one space, which
    leaves the marker's skeleton: the `=` runs and the words, at most one
    space between any two characters. One pass, so the view costs one copy of
    `body` however adversarial it is."""
    chars: list[str] = []
    origin: list[int] = []
    at = 0
    length = len(body)
    while at < length:
        char = body[at]
        if char in _ZERO_WIDTH_SET:
            at += 1
        elif char.isspace():
            chars.append(" ")
            origin.append(at)
            at += 1
            while at < length and (body[at].isspace() or body[at] in _ZERO_WIDTH_SET):
                at += 1
        else:
            chars.append(char)
            origin.append(at)
            at += 1
    return "".join(chars), origin


def _word_at(view: str, at: int, word: str) -> int:
    """The index past `word` read from `view[at:]` in any case, with the one
    view space allowed before each of its characters, or `-1` when the word is
    not there."""
    for char in word:
        following = at + 1 if view[at : at + 1] == " " else at
        if view[following : following + 1].lower() != char.lower():
            return -1
        at = following + 1
    return at


def _equals_run_at(view: str, at: int) -> int:
    """The index past a run of two or more `=`-side characters at
    `view[at:]`, the one view space allowed between any two of them, or `at`
    itself when there is no such run there."""
    count = 0
    index = at
    while True:
        following = index + 1 if view[index : index + 1] == " " else index
        if view[following : following + 1] in _EQUALS_SET:
            count += 1
            index = following + 1
        else:
            return index if count >= 2 else at


def _marker_at(
    view: str, at: int, run_end: int, words: tuple[str, ...], neutral: str
) -> tuple[int, str] | None:
    """`(end, neutral)` when a marker starts at `view` position `at` -- the
    first `=` of the run that ends at `run_end`, then `words` in order, then
    a second `=` run -- or `None` when it does not."""
    after = run_end
    for word in words:
        after = _word_at(view, after, word)
        if after < 0:
            return None
    end = _equals_run_at(view, after)
    return None if end == after else (end, neutral)


def _marker_spans(view: str) -> list[tuple[int, int, str]]:
    """Every `(start, end, neutral)` in `view`: the end-exclusive span of a
    marker imitation and the bracketed form it neutralises to, left to right.
    Linear in `len(view)` (#1510): a marker can only begin at a `=`-side
    character, and when the marker at such a character fails the scan jumps
    past the whole `=` run it opened on -- every later start inside that run
    meets the same words, so trying each of them would cost more and find
    nothing. A span always opens and closes on a `=`-side character."""
    spans: list[tuple[int, int, str]] = []
    at = 0
    while at < len(view):
        if view[at] not in _EQUALS_SET:
            at += 1
            continue
        run_end = _equals_run_at(view, at)
        matched: tuple[int, str] | None = None
        for words, neutral in _MARKERS:
            matched = _marker_at(view, at, run_end, words, neutral)
            if matched is not None:
                break
        if matched is None:
            at = max(run_end, at + 1)
        else:
            spans.append((at, matched[0], matched[1]))
            at = matched[0]
    return spans


_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")

#: `_truncate_sentence` only uses a sentence boundary if it keeps at least
#: this share of `limit` -- otherwise an early abbreviation-like period
#: ("Mr.", "Inc.", "No.") would collapse the result to a few characters
#: instead of using the character budget (mirrors
#: `tradepartner.corpus.departure_fetch.truncate_sentence`'s own guard).
_MIN_SENTENCE_SHARE = 0.5


class PacketTooLarge(ValueError):
    """`build_packet` refuses a packet whose estimated tokens exceed
    `limits.max_packet_tokens` (C2: a refusal, never a truncation rule)."""


@dataclass(frozen=True)
class PacketLimits:
    """The character caps and the token-estimate divisor
    (`research.labeling.*`, T119's config; req 3, C2). A caller builds one of
    these from `Settings.research.labeling` once per run; this module takes
    no config dependency of its own, to keep every value here explicit and
    the module trivially testable."""

    exhibit_max_chars: int
    item_max_chars: int
    eightk_max_chars: int
    max_packet_tokens: int
    chars_per_token: float


@dataclass(frozen=True)
class Packet:
    """One built packet (req 3, C2; C8's per-call record fields): the exact
    text sent as `state`, its SHA-256 (`passage_sha256`), the documents it
    carries (`document_ids`: the row's `listing_end_id` first, standing in
    for the Form 25, then, for `B` with an 8-K, that 8-K's accession), and
    the option set it was built for (`option_set_version`, `option_set_hash`),
    so a caller writing the per-call record needs nothing else from this
    packet."""

    kind: Kind
    text: str
    sha256: str
    document_ids: tuple[str, ...]
    option_set_version: str
    option_set_hash: str


@dataclass(frozen=True)
class Answer:
    """The last call's kind and selected option -- `next_kind`'s only state,
    since this module holds no run history itself. `selected_option` is
    `questions.UNRESOLVED` for a resolved-but-unresolved answer and for a
    `timeout`, which carries no answer (`crosswalk.InferenceOutcome` treats a
    timeout the same way: neither an agreement nor a disagreement)."""

    kind: Kind
    selected_option: str


def _words_and_iso(value: str | None) -> str | None:
    """`value` (an ISO `YYYY-MM-DD` date) rendered as both forms at once --
    "<Month> <day>, <year> (<iso>)" -- so the model is shown the same date
    twice and need not parse one form to compare it with the other (TC §4,
    the vendor's documented weakness on date comparison; req 3). `None`
    passes through unchanged."""
    if value is None:
        return None
    parsed = date.fromisoformat(value)
    return f"{parsed:%B} {parsed.day}, {parsed.year} ({value})"


def _truncate_sentence(text: str, limit: int) -> str:
    """`text` cut to at most `limit` characters, at the latest sentence
    boundary at or before the cut (C2: "each cut at a sentence boundary").
    The whole text, unchanged, when it already fits; the raw head, right-
    trimmed, when no boundary falls inside the limit.

    Boundaries are found over the whole of `text`, not just `text[:limit]`:
    a boundary is the whitespace run right after a sentence-ending mark
    (`_SENTENCE_BOUNDARY`'s lookbehind), and that whitespace can start
    exactly at `limit` when a sentence ends right at the cut (e.g. `limit`
    lands just past "...here."), in which case the sentence still fits
    and must not be dropped for want of its own trailing space. A boundary
    kept only when it is at or past `_MIN_SENTENCE_SHARE` of `limit`
    (otherwise an early abbreviation-like period would collapse the result
    to a few characters instead of using the budget)."""
    if len(text) <= limit:
        return text
    boundaries = [m for m in _SENTENCE_BOUNDARY.finditer(text) if m.start() <= limit]
    if boundaries and boundaries[-1].start() >= limit * _MIN_SENTENCE_SHARE:
        return text[: boundaries[-1].start()].rstrip()
    return text[:limit].rstrip()


def _field(label: str, value: str | None) -> str | None:
    return f"{label}: {value}" if value else None


def _neutralise_delimiters(body: str) -> str:
    """`body` with every marker imitation rewritten, so it can no longer
    close or reopen the delimited block `build_packet` wraps around the body
    (#1510). Filing text is untrusted (the vendor documents that the model
    "can be steered by injected text"), so a body carrying
    `=== FILING TEXT END ===` would otherwise let a filing put its own words
    after what reads as the end of the block. The matcher reads the body
    through `_matching_view`, so an imitation in any case, with extra or
    missing spaces, with a CRLF or non-breaking spaces inside, with zero-width
    characters between any two of its characters, or with a fullwidth `=` for
    the `=`, all match, and each is rewritten to the same words between
    square brackets (`_NEUTRAL_START`, `_NEUTRAL_END`): readable, never
    longer than the imitation it replaces (every view character stands for at
    least one of the body's) and never matching a delimiter itself. A body
    carrying no imitation is returned unchanged, so a clean body builds
    exactly the packet it built before this rewriting, and the whole pass is
    linear in `len(body)` whatever it contains."""
    view, origin = _matching_view(body)
    spans = _marker_spans(view)
    if not spans:
        return body
    pieces: list[str] = []
    cursor = 0
    for start, end, neutral in spans:
        pieces.append(body[cursor : origin[start]])
        pieces.append(neutral)
        cursor = origin[end - 1] + 1
    pieces.append(body[cursor:])
    return "".join(pieces)


def _form25_fields(documents: Mapping[str, Any]) -> str:
    """The Form 25 rendered as labelled fields (C2): issuer, exchange, class,
    rule provision as cited, filing date and signature date in words and ISO
    form."""
    lines = [
        _field("Issuer", documents.get("issuer")),
        _field("Exchange", documents.get("exchange_name") or documents.get("exchange")),
        _field("Class", documents.get("class_title")),
        _field("Rule provision", documents.get("rule_provision_raw")),
        _field("Filed on", _words_and_iso(documents.get("form25_filed_on"))),
        _field("Signature date", _words_and_iso(documents.get("signature_date"))),
    ]
    return "Form 25 notification\n" + "\n".join(line for line in lines if line)


def _notice_section(documents: Mapping[str, Any], limits: PacketLimits) -> str | None:
    """The EX-99.25 notice, only when the fetch classified it `text` (C2): a
    `stub` or `none` exhibit contributes nothing to either packet."""
    exhibit = documents.get("exhibit")
    if not exhibit or exhibit.get("status") != "text" or not exhibit.get("text"):
        return None
    text = _truncate_sentence(exhibit["text"], limits.exhibit_max_chars)
    return "EX-99.25 notice\n" + text


def has_eightk(row: Mapping[str, Any]) -> bool:
    """Whether `row`'s `documents` carries the one 8-K C2 selected (the call-
    order test: "B when an 8-K exists")."""
    documents = row["documents"]
    return bool(documents.get("eightk"))


def _eightk_section(
    documents: Mapping[str, Any], limits: PacketLimits
) -> tuple[str | None, tuple[str, ...]]:
    """Packet B's 8-K section (C2): its items in the fetch's own priority
    order, each cut at a sentence boundary, stopping once
    `limits.eightk_max_chars` is spent on the rendered body -- the "Item
    N" header and the blank-line join between items count against the
    budget too, so the assembled body never exceeds `limits.eightk_max_chars`
    characters; the body's head at the same cap when none of the items
    segmented. Returns the rendered text (or `None` for no 8-K) and the
    accession it carries."""
    eightk = documents.get("eightk")
    if not eightk:
        return None, ()
    filed_on = eightk.get("filed_on")
    header = (
        f"{eightk.get('form') or '8-K'} filed on {_words_and_iso(filed_on) or filed_on} "
        f"(accession {eightk.get('accession')})"
    )
    items: Mapping[str, str] = eightk.get("items") or {}
    if items:
        budget = limits.eightk_max_chars
        pieces: list[str] = []
        for number, text in items.items():
            item_header = f"Item {number}\n"
            separator = "\n\n" if pieces else ""
            overhead = len(item_header) + len(separator)
            remaining_for_text = min(limits.item_max_chars, budget - overhead)
            if remaining_for_text <= 0:
                break
            piece = _truncate_sentence(text, remaining_for_text)
            rendered = f"{separator}{item_header}{piece}"
            pieces.append(rendered)
            budget -= len(rendered)
        body = "".join(pieces)
    else:
        head_limit = min(limits.item_max_chars, limits.eightk_max_chars)
        body = _truncate_sentence(eightk.get("body_head") or "", head_limit)
    accession = eightk.get("accession")
    return f"{header}\n{body}", (accession,) if accession else ()


def next_kind(row: Mapping[str, Any], answer: Answer | None) -> Kind | None:
    """The next passage kind to send for `row`, given the last call's
    `answer` (`None` before any call for this listing end; req 3, C2's "call
    order per listing end, fixed by code"):

    - no prior call: `B` when the row has an 8-K, else `A` (there is nothing
      to try after `A`, since an `A`-only row has no 8-K to fall back to);
    - after a `B` whose answer was `unresolved`: `A`, the fallback C2's
      evidence (E5) says recovers five of the notice-only misses;
    - after anything else (a resolved `B`, or any `A`, whatever its answer):
      nothing more (`None`) -- the label is the last call's answer, which is
      `B` whenever `B` ran and resolved, else `A` (C2).
    """
    if answer is None:
        return "B" if has_eightk(row) else "A"
    if answer.kind == "B" and answer.selected_option == UNRESOLVED:
        return "A"
    return None


def build_packet(
    row: Mapping[str, Any],
    option_set: OptionSet,
    limits: PacketLimits,
    kind: Kind,
) -> Packet:
    """The `kind` packet for `row` (req 3, C2): the Form 25 fields, its
    notice when it is text, and, for `B`, the one 8-K -- all read from
    `row["documents"]` and nothing else.

    Raises `PacketTooLarge` when the estimated tokens (characters divided by
    `limits.chars_per_token`) exceed `limits.max_packet_tokens` -- a refusal,
    never a truncation rule (C2): the per-document caps above are the only
    truncation this function performs.
    """
    documents = row["documents"]
    sections = [_form25_fields(documents)]
    notice = _notice_section(documents, limits)
    if notice:
        sections.append(notice)
    listing_end_id = str(row.get("listing_end_id") or documents.get("listing_end_id") or "")
    document_ids: tuple[str, ...] = (listing_end_id,) if listing_end_id else ()
    if kind == "B":
        eightk_text, eightk_ids = _eightk_section(documents, limits)
        if eightk_text:
            sections.append(eightk_text)
            document_ids += eightk_ids
    body = _neutralise_delimiters("\n\n".join(sections))
    text = f"{DELIM_START}\n{body}\n{DELIM_END}"
    estimated_tokens = len(text) / limits.chars_per_token
    if estimated_tokens > limits.max_packet_tokens:
        raise PacketTooLarge(
            f"{kind} packet for {listing_end_id!r}: "
            f"~{estimated_tokens:.0f} estimated tokens > {limits.max_packet_tokens}"
        )
    return Packet(
        kind=kind,
        text=text,
        sha256=sha256(text.encode("utf-8")).hexdigest(),
        document_ids=document_ids,
        option_set_version=option_set.version,
        option_set_hash=option_set.hash,
    )
