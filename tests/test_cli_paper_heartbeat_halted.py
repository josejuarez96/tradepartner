# ruff: noqa: F811
"""The paper heartbeat stays red while any open-window book is halted (#2179).

Built on `tests/test_cli_paper.py`'s fixtures. Exit codes are unchanged (launchd
semantics); only the ping differs.
"""

from __future__ import annotations

import pytest
from test_cli_paper import (  # noqa: F401  (fixtures)
    HEARTBEAT_URL,
    LONG_REASON,
    SATURDAY,
    _Clock,
    _Factory,
    _heartbeat_calls,
    _paper,
    books,
    clock,
    factory,
    live,
    started,
)

from tradepartner.execution import run as paper_run


def _fresh_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture store has no 2026 bars: treat the ingest as covering S-1."""
    monkeypatch.setattr(paper_run._Run, "_staleness", lambda self: None)


@pytest.mark.usefixtures("started")
def test_run_on_an_engaged_book_pings_fail_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    calls = _heartbeat_calls(monkeypatch)
    _fresh_data(monkeypatch)
    assert _paper(clock, factory, "kill", "--reason", LONG_REASON).exit_code == 0
    out = _paper(clock, factory, "run")
    assert "skipped_kill_switch" in out.output, out.output
    assert out.exit_code == 0
    assert calls == [HEARTBEAT_URL + "/fail"], calls


@pytest.mark.usefixtures("started")
def test_single_book_run_on_an_engaged_book_pings_fail(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    calls = _heartbeat_calls(monkeypatch)
    _fresh_data(monkeypatch)
    assert _paper(clock, factory, "kill", "--reason", LONG_REASON).exit_code == 0
    out = _paper(clock, factory, "run", "--book", "main")
    assert "skipped_kill_switch" in out.output, out.output
    assert out.exit_code == 0
    assert calls == [HEARTBEAT_URL + "/fail"], calls


@pytest.mark.usefixtures("started")
def test_weekend_run_with_an_engaged_book_pings_fail(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    calls = _heartbeat_calls(monkeypatch)
    assert _paper(clock, factory, "kill", "--reason", LONG_REASON).exit_code == 0
    clock.now = SATURDAY
    out = _paper(clock, factory, "run")
    assert "no_session" in out.output and out.exit_code == 0
    assert calls == [HEARTBEAT_URL + "/fail"], calls


@pytest.mark.usefixtures("started")
def test_weekend_run_with_no_engaged_book_still_pings_ok(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, factory: _Factory
) -> None:
    calls = _heartbeat_calls(monkeypatch)
    clock.now = SATURDAY
    out = _paper(clock, factory, "run")
    assert out.exit_code == 0
    assert calls == [HEARTBEAT_URL], calls


def test_multi_book_run_with_one_engaged_and_one_ok_pings_fail(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, books: object
) -> None:
    calls = _heartbeat_calls(monkeypatch)
    _fresh_data(monkeypatch)
    kill = _paper(
        clock,
        books,  # type: ignore[arg-type]
        "kill",
        "--book",
        "zeta",
        "--reason",
        LONG_REASON,
    )
    assert kill.exit_code == 0
    out = _paper(clock, books, "run")  # type: ignore[arg-type]
    assert "book zeta: skipped_kill_switch" in out.output, out.output
    assert out.exit_code == 0
    assert calls == [HEARTBEAT_URL + "/fail"], calls


def test_a_book_run_never_pings_ok_while_another_book_is_engaged(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, books: object
) -> None:
    calls = _heartbeat_calls(monkeypatch)
    _fresh_data(monkeypatch)
    kill = _paper(
        clock,
        books,  # type: ignore[arg-type]
        "kill",
        "--book",
        "zeta",
        "--reason",
        LONG_REASON,
    )
    assert kill.exit_code == 0
    out = _paper(clock, books, "run", "--book", "main")  # type: ignore[arg-type]
    assert out.exit_code == 0, out.output
    assert calls == [], calls
