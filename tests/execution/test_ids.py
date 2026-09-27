"""Client order ids (ADR 0010 point 3; Phase 4 plan T50).

The id is a pure function of the journal: prefix, session, security, side and
the attempt number, where the attempt is 1 plus the `orders` rows already
journaled on the session for that (security, side), counted across every
decision and including rows written earlier in the same batch.
"""

from __future__ import annotations

import csv
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tradepartner.config import Settings
from tradepartner.execution.ids import client_order_id, next_attempt
from tradepartner.store.journal import OrderRow

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "universe"
SESSION = date(2026, 10, 1)
AT = datetime(2026, 10, 1, 13, 0, tzinfo=UTC)


def _row(
    security_id: str,
    side: str,
    *,
    decision_id: int = 1,
    phase: str | None = None,
    attempt: int = 1,
    session: date = SESSION,
) -> OrderRow:
    return OrderRow(
        client_order_id=client_order_id("tp", session, security_id, side, attempt),
        decision_id=decision_id,
        run_id=1,
        session=session,
        attempt=attempt,
        phase=phase or side,
        security_id=security_id,
        symbol="AAPL",
        side=side,
        notional=100.0,
        sells_in_flight_at_submit=False,
        known_at=AT,
        ingested_at=AT,
    )


def _journal(rows: list[OrderRow], decision_id: int, security_id: str, side: str) -> str:
    """Journal one attempt the way the wrapper will: derive, then append."""
    attempt = next_attempt(rows, security_id, side)
    row = _row(security_id, side, decision_id=decision_id, attempt=attempt)
    rows.append(row)
    return row.client_order_id


def test_derivation_is_pinned() -> None:
    assert client_order_id("tp", SESSION, "0000320193", "buy", 1) == "tp-20261001-0000320193-buy-1"
    assert (
        client_order_id("paper", date(2027, 1, 4), "0001652044:class-c-capital-stock", "sell", 12)
        == "paper-20270104-0001652044:class-c-capital-stock-sell-12"
    )


@pytest.mark.parametrize(
    ("prefix", "session", "security_id", "side", "attempt"),
    [
        ("", SESSION, "0000320193", "buy", 1),
        ("tp", SESSION, "", "buy", 1),
        ("tp", SESSION, "0000320193", "hold", 1),
        ("tp", SESSION, "0000320193", "BUY", 1),
        ("tp", SESSION, "0000320193", "buy", 0),
        ("tp", SESSION, "0000320193", "buy", -1),
        ("tp", AT, "0000320193", "buy", 1),
    ],
)
def test_bad_parts_are_refused(
    prefix: str, session: date, security_id: str, side: str, attempt: int
) -> None:
    with pytest.raises(ValueError):
        client_order_id(prefix, session, security_id, side, attempt)


def test_bool_attempt_is_refused() -> None:
    with pytest.raises(ValueError):
        client_order_id("tp", SESSION, "0000320193", "buy", True)


def test_first_attempt_on_an_empty_session_is_one() -> None:
    assert next_attempt([], "0000320193", "buy") == 1


def test_two_decisions_on_one_name_on_one_session_never_share_an_id() -> None:
    rows: list[OrderRow] = []
    first = _journal(rows, decision_id=1, security_id="0000320193", side="buy")
    second = _journal(rows, decision_id=2, security_id="0000320193", side="buy")
    assert first == "tp-20261001-0000320193-buy-1"
    assert second == "tp-20261001-0000320193-buy-2"
    assert first != second


def test_sides_and_names_count_separately() -> None:
    rows: list[OrderRow] = []
    ids = [
        _journal(rows, 1, "0000320193", "sell"),
        _journal(rows, 2, "0000320193", "buy"),
        _journal(rows, 3, "0000789019", "buy"),
        _journal(rows, 4, "0000320193", "sell"),
    ]
    assert ids == [
        "tp-20261001-0000320193-sell-1",
        "tp-20261001-0000320193-buy-1",
        "tp-20261001-0000789019-buy-1",
        "tp-20261001-0000320193-sell-2",
    ]
    assert len(set(ids)) == len(ids)


def test_same_batch_rows_count_in_write_order() -> None:
    rows: list[OrderRow] = []
    ids = [_journal(rows, decision, "0000320193", "buy") for decision in (7, 8, 9)]
    assert [row.attempt for row in rows] == [1, 2, 3]
    assert ids[-1].endswith("-buy-3")


def test_attempt_counts_across_decisions_and_phases() -> None:
    rows = [
        _row("0000320193", "sell", decision_id=1, phase="sell", attempt=1),
        _row("0000320193", "sell", decision_id=2, phase="exit", attempt=2),
    ]
    assert next_attempt(rows, "0000320193", "sell") == 3


def test_attempt_reads_only_the_rows_it_is_given() -> None:
    given = [_row("0000320193", "buy")]
    consumed: list[OrderRow] = []

    def once() -> Iterator[OrderRow]:
        for row in given:
            consumed.append(row)
            yield row

    assert next_attempt(once(), "0000320193", "buy") == 2
    assert consumed == given
    assert next_attempt([], "0000320193", "buy") == 1


def test_attempt_refuses_an_unknown_side() -> None:
    with pytest.raises(ValueError):
        next_attempt([], "0000320193", "short")


def _longest_fixture_security_id() -> str:
    with (FIXTURES / "securities.csv").open(newline="") as handle:
        return max((row["security_id"] for row in csv.DictReader(handle)), key=len)


def test_id_fits_the_brokers_recorded_length() -> None:
    settings = Settings(_env_file=None)
    limit = settings.alpaca.client_order_id_max_length
    if limit is None:
        pytest.skip("alpaca.client_order_id_max_length is unset until T48b records it")
    attempt = settings.risk.max_orders_per_run
    for security_id in (
        _longest_fixture_security_id(),
        "0001652044:class-c-capital-stock",
    ):
        for side in ("buy", "sell"):
            order_id = client_order_id(
                settings.paper.order_id_prefix, date(2099, 12, 31), security_id, side, attempt
            )
            assert len(order_id) <= limit, order_id
