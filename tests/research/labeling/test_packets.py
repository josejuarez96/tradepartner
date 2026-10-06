"""Tests for `tradepartner.research.labeling.packets` (plan T121b).

Research-labeling spec req 3 and its 2026-10-06 amendment C2
(docs/specs/research-labeling.md). The fixture row below is shaped like the
corpus fetch's KLX record (`tradepartner.corpus.departure_fetch._record`,
`tests/corpus/test_departure_fetch.py`), built by hand here so the module
under test never needs to import `tradepartner.corpus` (boundary test
(b)(v); this module does not either).
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tradepartner.research.labeling import packets
from tradepartner.research.labeling.questions import DEFAULT_OPTION_SET, UNRESOLVED

LIMITS = packets.PacketLimits(
    exhibit_max_chars=4000,
    item_max_chars=4000,
    eightk_max_chars=12000,
    max_packet_tokens=8000,
    chars_per_token=2.5,
)


def _klx_documents(**overrides: Any) -> dict[str, Any]:
    documents: dict[str, Any] = {
        "listing_end_id": "0001354457-26-000904",
        "cik": "0001738827",
        "issuer": "KLX Energy Services Holdings, Inc.",
        "exchange": "NASDAQ",
        "exchange_name": "Nasdaq Stock Market LLC",
        "class_title": "rights",
        "form": "25-NSE",
        "form25_accepted_at": "2026-09-24T14:08:40+00:00",
        "form25_filed_on": "2026-09-24",
        "signature_date": "2026-09-24",
        "effective_on": "2026-10-04",
        "rule_provision_raw": "17 CFR 240.12d2-2(a)(2)",
        "rule_provision": "12d2-2(a)(2)",
        "amendments": [],
        "orphan_amendment": False,
        "exhibit": {
            "status": "text",
            "type": "EX-99.25",
            "text": (
                "KLX Energy announces the expiry of its rights. "
                "The rights expired unexercised on the stated date. "
                "No further action by holders is required."
            ),
            "sha256": "a" * 64,
        },
        "eightk": {
            "accession": "0001738827-26-000055",
            "form": "8-K",
            "filed_on": "2026-09-20",
            "accepted_at": "2026-09-20T16:00:00+00:00",
            "index_items": ["3.01"],
            "items": {
                "3.01": (
                    "On September 18, 2026 the registrant received a notice. "
                    "The notice stated the rights would expire. "
                    "The registrant does not intend to appeal."
                )
            },
            "body_head": None,
            "sha256": "b" * 64,
        },
        "eightk_note": None,
        "markers": [],
        "missing": [],
    }
    documents.update(overrides)
    return documents


def _klx_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "listing_end_id": "0001354457-26-000904",
        "documents": _klx_documents(),
    }
    row.update(overrides)
    return row


# --- build_packet, deterministic, A against B -------------------------------------


def test_build_packet_a_is_deterministic_and_has_the_delimiters() -> None:
    row = _klx_row()
    first = packets.build_packet(row, DEFAULT_OPTION_SET, LIMITS, "A")
    second = packets.build_packet(row, DEFAULT_OPTION_SET, LIMITS, "A")

    assert first == second
    assert first.text.startswith(packets.DELIM_START + "\n")
    assert first.text.endswith("\n" + packets.DELIM_END)
    assert first.kind == "A"
    assert first.document_ids == ("0001354457-26-000904",)
    assert first.option_set_version == DEFAULT_OPTION_SET.version
    assert first.option_set_hash == DEFAULT_OPTION_SET.hash


def test_build_packet_a_renders_the_form25_fields_with_dates_in_words_and_iso() -> None:
    packet = packets.build_packet(_klx_row(), DEFAULT_OPTION_SET, LIMITS, "A")

    assert "Issuer: KLX Energy Services Holdings, Inc." in packet.text
    assert "Exchange: Nasdaq Stock Market LLC" in packet.text
    assert "Class: rights" in packet.text
    assert "Rule provision: 17 CFR 240.12d2-2(a)(2)" in packet.text
    assert "Filed on: September 24, 2026 (2026-09-24)" in packet.text
    assert "Signature date: September 24, 2026 (2026-09-24)" in packet.text


def test_build_packet_a_includes_the_text_notice_but_not_the_eightk() -> None:
    packet = packets.build_packet(_klx_row(), DEFAULT_OPTION_SET, LIMITS, "A")

    assert "KLX Energy announces the expiry" in packet.text
    assert "received a notice" not in packet.text  # the 8-K item, A-only excludes it
    assert packet.document_ids == ("0001354457-26-000904",)


def test_build_packet_a_omits_a_stub_or_missing_notice() -> None:
    stub_row = _klx_row(
        documents=_klx_documents(
            exhibit={"status": "stub", "type": "EX-99.25", "text": None, "sha256": "a" * 64}
        )
    )
    none_row = _klx_row(
        documents=_klx_documents(
            exhibit={"status": "none", "type": None, "text": None, "sha256": None}
        )
    )

    for row in (stub_row, none_row):
        packet = packets.build_packet(row, DEFAULT_OPTION_SET, LIMITS, "A")
        assert "EX-99.25 notice" not in packet.text


def test_build_packet_b_adds_the_eightk_and_differs_from_a() -> None:
    row = _klx_row()
    packet_a = packets.build_packet(row, DEFAULT_OPTION_SET, LIMITS, "A")
    packet_b = packets.build_packet(row, DEFAULT_OPTION_SET, LIMITS, "B")

    assert packet_b.text != packet_a.text
    assert "received a notice" in packet_b.text
    assert "Item 3.01" in packet_b.text
    assert "0001738827-26-000055" in packet_b.text  # the accession, in the header
    assert packet_b.document_ids == ("0001354457-26-000904", "0001738827-26-000055")


def test_build_packet_b_uses_body_head_when_no_item_segments() -> None:
    documents = _klx_documents(
        eightk={
            "accession": "0001738827-26-000055",
            "form": "8-K",
            "filed_on": "2026-09-20",
            "accepted_at": "2026-09-20T16:00:00+00:00",
            "index_items": [],
            "items": {},
            "body_head": "No item segmented. This is the plain body text of the filing.",
            "sha256": "b" * 64,
        }
    )
    packet = packets.build_packet(_klx_row(documents=documents), DEFAULT_OPTION_SET, LIMITS, "B")

    assert "No item segmented." in packet.text
    assert "Item " not in packet.text.split("No item segmented.")[0].split("\n\n")[-1]


def test_build_packet_b_with_no_eightk_is_the_same_as_a() -> None:
    documents = _klx_documents(eightk=None)
    row = _klx_row(documents=documents)

    packet_a = packets.build_packet(row, DEFAULT_OPTION_SET, LIMITS, "A")
    packet_b = packets.build_packet(row, DEFAULT_OPTION_SET, LIMITS, "B")

    assert packet_a.text == packet_b.text
    assert packet_b.document_ids == ("0001354457-26-000904",)


# --- next_kind ---------------------------------------------------------------------


def test_next_kind_first_call_is_b_when_an_eightk_exists() -> None:
    assert packets.next_kind(_klx_row(), None) == "B"


def test_next_kind_first_call_is_a_when_no_eightk() -> None:
    row = _klx_row(documents=_klx_documents(eightk=None))
    assert packets.next_kind(row, None) == "A"


def test_next_kind_after_b_unresolved_is_a() -> None:
    answer = packets.Answer(kind="B", selected_option=UNRESOLVED)
    assert packets.next_kind(_klx_row(), answer) == "A"


def test_next_kind_after_b_resolved_is_none() -> None:
    answer = packets.Answer(kind="B", selected_option="bankruptcy")
    assert packets.next_kind(_klx_row(), answer) is None


def test_next_kind_after_a_is_always_none() -> None:
    resolved = packets.Answer(kind="A", selected_option="bankruptcy")
    unresolved = packets.Answer(kind="A", selected_option=UNRESOLVED)
    assert packets.next_kind(_klx_row(), resolved) is None
    assert packets.next_kind(_klx_row(), unresolved) is None


# --- sentence cuts ------------------------------------------------------------------


def test_truncate_sentence_cuts_at_a_sentence_boundary() -> None:
    text = "First sentence here. Second sentence here. Third sentence here."
    cut = packets._truncate_sentence(text, 30)
    assert cut == "First sentence here."
    assert not cut.endswith(" ")


def test_truncate_sentence_keeps_short_text_whole() -> None:
    text = "Short."
    assert packets._truncate_sentence(text, 100) == text


def test_truncate_sentence_with_no_boundary_cuts_the_raw_head() -> None:
    text = "a" * 50
    assert packets._truncate_sentence(text, 10) == "a" * 10


def test_notice_is_cut_at_a_sentence_boundary_under_exhibit_max_chars() -> None:
    long_text = " ".join(f"Sentence number {i} is here." for i in range(50))
    documents = _klx_documents(
        exhibit={"status": "text", "type": "EX-99.25", "text": long_text, "sha256": "a" * 64}
    )
    tight_limits = packets.PacketLimits(
        exhibit_max_chars=40,
        item_max_chars=4000,
        eightk_max_chars=12000,
        max_packet_tokens=8000,
        chars_per_token=2.5,
    )
    packet = packets.build_packet(
        _klx_row(documents=documents), DEFAULT_OPTION_SET, tight_limits, "A"
    )

    notice_section = packet.text.split("EX-99.25 notice\n")[1].removesuffix(
        "\n" + packets.DELIM_END
    )
    assert len(notice_section) <= 40
    assert notice_section.endswith(".")


def test_eightk_items_are_cut_and_stop_once_the_budget_is_spent() -> None:
    documents = _klx_documents(
        eightk={
            "accession": "acc",
            "form": "8-K",
            "filed_on": "2026-09-20",
            "accepted_at": "2026-09-20T16:00:00+00:00",
            "index_items": ["3.01", "2.01"],
            "items": {
                "3.01": "Sentence one. Sentence two. Sentence three.",
                "2.01": "Another sentence one. Another sentence two.",
            },
            "body_head": None,
            "sha256": "b" * 64,
        }
    )
    tight_limits = packets.PacketLimits(
        exhibit_max_chars=4000,
        item_max_chars=20,
        eightk_max_chars=13,
        max_packet_tokens=8000,
        chars_per_token=2.5,
    )
    packet = packets.build_packet(
        _klx_row(documents=documents), DEFAULT_OPTION_SET, tight_limits, "B"
    )

    assert "Item 3.01" in packet.text
    assert "Item 2.01" not in packet.text  # the eightk_max_chars budget was spent on 3.01


# --- the max_packet_tokens refusal --------------------------------------------------


def test_build_packet_refuses_over_max_packet_tokens() -> None:
    tiny_limits = packets.PacketLimits(
        exhibit_max_chars=4000,
        item_max_chars=4000,
        eightk_max_chars=12000,
        max_packet_tokens=5,
        chars_per_token=2.5,
    )
    with pytest.raises(packets.PacketTooLarge):
        packets.build_packet(_klx_row(), DEFAULT_OPTION_SET, tiny_limits, "A")


def test_build_packet_under_the_cap_does_not_raise() -> None:
    generous_limits = packets.PacketLimits(
        exhibit_max_chars=4000,
        item_max_chars=4000,
        eightk_max_chars=12000,
        max_packet_tokens=100_000,
        chars_per_token=2.5,
    )
    packets.build_packet(_klx_row(), DEFAULT_OPTION_SET, generous_limits, "A")


# --- sentinels never reach the packet; nothing outside documents/listing_end_id ----


def test_sentinels_outside_documents_never_reach_the_packet() -> None:
    sentinel_row = _klx_row(
        rule_status="SENTINEL_RULE_STATUS",
        rule_relisted="SENTINEL_RULE_RELISTED",
        gold_label="SENTINEL_GOLD_LABEL",
        gold_class="SENTINEL_GOLD_CLASS",
        text_states="SENTINEL_TEXT_STATES",
        evidence_quote="SENTINEL_EVIDENCE_QUOTE",
        decision="SENTINEL_DECISION",
    )
    clean_row = _klx_row()

    sentinel_packet = packets.build_packet(sentinel_row, DEFAULT_OPTION_SET, LIMITS, "B")
    clean_packet = packets.build_packet(clean_row, DEFAULT_OPTION_SET, LIMITS, "B")

    assert sentinel_packet.text == clean_packet.text
    for sentinel in (
        "SENTINEL_RULE_STATUS",
        "SENTINEL_RULE_RELISTED",
        "SENTINEL_GOLD_LABEL",
        "SENTINEL_GOLD_CLASS",
        "SENTINEL_TEXT_STATES",
        "SENTINEL_EVIDENCE_QUOTE",
        "SENTINEL_DECISION",
    ):
        assert sentinel not in sentinel_packet.text


def test_build_packet_reads_nothing_outside_documents_and_listing_end_id() -> None:
    """A row that differs only outside `documents`/`listing_end_id` builds an
    identical packet (req 3: "from the row's `documents` only")."""
    base = _klx_row()
    mutated = copy.deepcopy(base)
    mutated["unrelated_column"] = object()  # not even JSON-serialisable
    mutated["another_one"] = {"nested": ["anything"]}

    assert packets.build_packet(base, DEFAULT_OPTION_SET, LIMITS, "B") == packets.build_packet(
        mutated, DEFAULT_OPTION_SET, LIMITS, "B"
    )


def test_has_eightk() -> None:
    assert packets.has_eightk(_klx_row()) is True
    assert packets.has_eightk(_klx_row(documents=_klx_documents(eightk=None))) is False
