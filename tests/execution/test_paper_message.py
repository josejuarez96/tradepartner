"""Paper timing notes are stored for analysis and omitted from owner text."""

from datetime import UTC, datetime
from types import SimpleNamespace

from tradepartner.execution import paper_message
from tradepartner.execution.wrapper import RiskGatedBroker


def test_owner_message_keeps_other_notes_and_drops_only_phase_parts() -> None:
    raw = "order delayed; paper_phase sells_submitted=2; check the fill; paper_phase done=3"
    assert paper_message.owner_message(raw) == "order delayed; check the fill"
    assert paper_message.owner_message("paper_phase done=3") is None
    assert paper_message.owner_message(None) is None
    assert raw.endswith("paper_phase done=3")


def test_parser_reads_the_wrappers_timestamp_and_count_format() -> None:
    at = datetime(2026, 10, 30, 14, 1, tzinfo=UTC)
    gate = SimpleNamespace(read_clock=lambda: at)
    notes: list[str] = []
    RiskGatedBroker._phase_time(gate, notes, "sells_submitted")  # type: ignore[arg-type]
    RiskGatedBroker._phase_time(gate, notes, "sells_poll_rounds", count=2)  # type: ignore[arg-type]
    message = "; ".join(["other note", *notes])
    assert paper_message.parse_paper_phases(message) == {
        "sells_submitted": at.isoformat(),
        "sells_poll_rounds": "2",
    }
    assert paper_message.owner_message(message) == "other note"
