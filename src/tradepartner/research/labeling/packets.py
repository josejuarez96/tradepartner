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
delimiters (req 3).

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
    body = "\n\n".join(sections)
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
