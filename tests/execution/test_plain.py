"""Plain-words halt reasons and failure hints (#2203): display only."""

from __future__ import annotations

from tradepartner.adapters.alpaca_trading_raw import AlpacaTradingError
from tradepartner.execution import plain


def test_engaged_cause_names_who_and_keeps_event_id() -> None:
    text = plain.plain_cause("kill_switch event 7 engaged (fault)")
    assert "a fault switched it on" in text and "event 7" in text
    assert "kill_switch event" not in text and "engaged (" not in text


def test_run_causes_in_words() -> None:
    assert plain.plain_cause("run 42 failed") == "run 42 failed with an error"
    assert "never finished" in plain.plain_cause("run 3 unfinished")
    assert plain.plain_cause("something else") == "something else"


def test_causes_join() -> None:
    assert plain.plain_causes(["run 1 halted", "run 2 crashed"]).count("; ") == 1


def test_failure_hint_for_404_names_next_step() -> None:
    hint = plain.failure_hint(AlpacaTradingError(404, "not found", retried=False))
    assert "does not know" in hint
    assert "paper.err.log" in hint and "paper status" in hint


def test_failure_hint_generic() -> None:
    assert "unexpected error" in plain.failure_hint(RuntimeError("x"))
