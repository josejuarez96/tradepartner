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
from tradepartner.execution import ids
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
    position_side: str = "long",
    token: str | None = None,
) -> OrderRow:
    return OrderRow(
        client_order_id=client_order_id("tp", "main", session, security_id, token or side, attempt),
        decision_id=decision_id,
        run_id=1,
        session=session,
        attempt=attempt,
        phase=phase or side,
        security_id=security_id,
        symbol="AAPL",
        side=side,
        position_side=position_side,
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
    assert (
        client_order_id("tp", "main", SESSION, "0000320193", "buy", 1)
        == "tp-main-20261001-0000320193-buy-1"
    )
    assert (
        client_order_id(
            "paper", "fx", date(2027, 1, 4), "0001652044:class-c-capital-stock", "sell", 12
        )
        == "paper-fx-20270104-0001652044:class-c-capital-stock-sell-12"
    )


@pytest.mark.parametrize(
    ("prefix", "book", "session", "security_id", "side", "attempt"),
    [
        ("", "main", SESSION, "0000320193", "buy", 1),
        ("tp", "", SESSION, "0000320193", "buy", 1),
        ("tp", "a-b", SESSION, "0000320193", "buy", 1),
        ("tp", "a:b", SESSION, "0000320193", "buy", 1),
        ("tp", "main", SESSION, "", "buy", 1),
        ("tp", "main", SESSION, "0000320193", "hold", 1),
        ("tp", "main", SESSION, "0000320193", "BUY", 1),
        ("tp", "main", SESSION, "0000320193", "buy", 0),
        ("tp", "main", SESSION, "0000320193", "buy", -1),
        ("tp", "main", AT, "0000320193", "buy", 1),
    ],
)
def test_bad_parts_are_refused(
    prefix: str, book: str, session: date, security_id: str, side: str, attempt: int
) -> None:
    with pytest.raises(ValueError):
        client_order_id(prefix, book, session, security_id, side, attempt)


def test_bool_attempt_is_refused() -> None:
    with pytest.raises(ValueError):
        client_order_id("tp", "main", SESSION, "0000320193", "buy", True)


def test_first_attempt_on_an_empty_session_is_one() -> None:
    assert next_attempt([], "0000320193", "buy") == 1


def test_two_decisions_on_one_name_on_one_session_never_share_an_id() -> None:
    rows: list[OrderRow] = []
    first = _journal(rows, decision_id=1, security_id="0000320193", side="buy")
    second = _journal(rows, decision_id=2, security_id="0000320193", side="buy")
    assert first == "tp-main-20261001-0000320193-buy-1"
    assert second == "tp-main-20261001-0000320193-buy-2"
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
        "tp-main-20261001-0000320193-sell-1",
        "tp-main-20261001-0000320193-buy-1",
        "tp-main-20261001-0000789019-buy-1",
        "tp-main-20261001-0000320193-sell-2",
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


def test_attempt_refuses_rows_from_two_sessions() -> None:
    rows = [
        _row("0000320193", "buy"),
        _row("0000789019", "buy", session=date(2026, 9, 30)),
    ]
    with pytest.raises(ValueError, match="spans sessions"):
        next_attempt(rows, "0000320193", "buy")


@pytest.mark.parametrize(
    "attempts",
    [[2], [1, 3], [1, 1], [2, 1]],
    ids=["missing-first", "gap", "duplicate", "out-of-write-order"],
)
def test_attempt_refuses_a_broken_attempt_sequence(attempts: list[int]) -> None:
    rows = [_row("0000320193", "buy", attempt=a) for a in attempts]
    with pytest.raises(ValueError, match="journaled attempts"):
        next_attempt(rows, "0000320193", "buy")


def test_other_names_do_not_enter_the_sequence_check() -> None:
    rows = [_row("0000789019", "buy", attempt=5), _row("0000320193", "sell", attempt=3)]
    assert next_attempt(rows, "0000320193", "buy") == 1


@pytest.mark.parametrize("token", ["hold", "BUY", "Short", "buy_to_open", "sell_to_open", ""])
def test_attempt_refuses_an_unknown_token(token: str) -> None:
    """T228: `short` and `cover` are tokens now (item 5), so the refusal is pinned on
    values outside the four: an intent's own spelling is not a token."""
    with pytest.raises(ValueError):
        next_attempt([], "0000320193", token)


def _longest_fixture_security_id() -> str:
    with (FIXTURES / "securities.csv").open(newline="") as handle:
        return max((row["security_id"] for row in csv.DictReader(handle)), key=len)


def test_id_fits_the_brokers_recorded_length() -> None:
    settings = Settings(_env_file=None)
    limit = settings.alpaca.client_order_id_max_length
    if limit is None:
        pytest.skip("alpaca.client_order_id_max_length is unset until T48b records it")
    # Attempts per (security, side) add up across the runs of one session, so
    # no config key bounds them; five digits is headroom, not a limit. T48b
    # also confirms the broker accepts the `:` in a secondary class's id.
    attempt = 99_999
    for security_id in (
        _longest_fixture_security_id(),
        "0001652044:class-c-capital-stock",
    ):
        for side in ids.ID_TOKENS:
            order_id = client_order_id(
                settings.paper.order_id_prefix,
                settings.paper.book_id,
                date(2099, 12, 31),
                security_id,
                side,
                attempt,
            )
            assert len(order_id) <= limit, order_id


# --- T228: the id's intent tokens (paper spec amendment 2026-10-19 item 5, #1872) ----
#
# The token is the intent's: `buy` (`buy_to_open`) and `sell` (`sell_to_close`) for the
# long intents, so every long id is byte-identical to today's; `short` for
# `sell_to_open` and `cover` for `buy_to_close`. An `orders` row's intent is the pair
# (`side`, `position_side`) (`schema.POSITION_SIDES`' comment), so a row's token is
# (buy, long) -> buy, (sell, long) -> sell, (sell, short) -> short and
# (buy, short) -> cover, and `next_attempt` counts rows by (`security_id`, token).

#: The four Alpaca `position_intent` values and their id tokens (item 5).
_INTENT_TOKENS = {
    "buy_to_open": "buy",
    "sell_to_close": "sell",
    "sell_to_open": "short",
    "buy_to_close": "cover",
}


def _short_open(attempt: int = 1, decision_id: int = 2) -> OrderRow:
    return _row(
        "0000320193",
        "sell",
        decision_id=decision_id,
        phase="buy",
        attempt=attempt,
        position_side="short",
        token="short",
    )


def _cover(attempt: int = 1, decision_id: int = 3) -> OrderRow:
    return _row(
        "0000320193",
        "buy",
        decision_id=decision_id,
        phase="sell",
        attempt=attempt,
        position_side="short",
        token="cover",
    )


def test_the_four_tokens_in_order() -> None:
    assert ids.ID_TOKENS == ("buy", "sell", "short", "cover")


@pytest.mark.parametrize(("intent", "token"), list(_INTENT_TOKENS.items()))
def test_each_intent_has_its_token(intent: str, token: str) -> None:
    assert ids.token_for(intent) == token


@pytest.mark.parametrize(
    "intent", ["buy", "sell", "short", "cover", "BUY_TO_OPEN", "sell_short", "", "hold"]
)
def test_a_value_outside_the_four_intents_has_no_token(intent: str) -> None:
    with pytest.raises(ValueError):
        ids.token_for(intent)


def test_every_long_id_is_byte_identical() -> None:
    """Item 5: a long id built from its intent's token is today's id, byte for byte
    (the strings `test_derivation_is_pinned` pins, written out again here)."""
    assert (
        client_order_id("tp", "main", SESSION, "0000320193", ids.token_for("buy_to_open"), 1)
        == "tp-main-20261001-0000320193-buy-1"
    )
    assert (
        client_order_id(
            "paper",
            "fx",
            date(2027, 1, 4),
            "0001652044:class-c-capital-stock",
            ids.token_for("sell_to_close"),
            12,
        )
        == "paper-fx-20270104-0001652044:class-c-capital-stock-sell-12"
    )


def test_a_long_only_journal_counts_as_before() -> None:
    """A long-only journal counts as before: rows default to `position_side='long'`."""
    rows = [_row("0000320193", "buy", attempt=1), _row("0000320193", "buy", attempt=2)]
    assert next_attempt(rows, "0000320193", "buy") == 3
    assert next_attempt(rows, "0000320193", "sell") == 1


def test_the_short_and_cover_ids_are_pinned() -> None:
    assert (
        client_order_id("tp", "main", SESSION, "0000320193", "short", 1)
        == "tp-main-20261001-0000320193-short-1"
    )
    assert (
        client_order_id("tp", "main", SESSION, "0000320193", "cover", 3)
        == "tp-main-20261001-0000320193-cover-3"
    )


def test_a_sell_to_close_and_a_short_open_of_one_name_get_distinct_ids_and_attempt_one() -> None:
    """The plan line's test: one name, one session, a long sell in the sell phase and a
    short open in the buy phase. Both `orders` rows have `side='sell'`; the tokens keep
    the counts apart, so each is attempt 1 and the ids differ."""
    rows: list[OrderRow] = []
    sell_attempt = next_attempt(rows, "0000320193", "sell")
    rows.append(_row("0000320193", "sell", decision_id=1, phase="sell", attempt=sell_attempt))
    short_attempt = next_attempt(rows, "0000320193", "short")
    rows.append(_short_open(attempt=short_attempt))
    assert (sell_attempt, short_attempt) == (1, 1)
    assert [row.client_order_id for row in rows] == [
        "tp-main-20261001-0000320193-sell-1",
        "tp-main-20261001-0000320193-short-1",
    ]
    assert next_attempt(rows, "0000320193", "sell") == 2
    assert next_attempt(rows, "0000320193", "short") == 2


def test_a_short_open_first_does_not_advance_the_sell_count() -> None:
    rows = [_short_open(attempt=1)]
    assert next_attempt(rows, "0000320193", "sell") == 1
    assert next_attempt(rows, "0000320193", "short") == 2


def test_a_cover_and_a_long_buy_of_one_name_count_apart() -> None:
    """Both rows have `side='buy'`: (buy, short) is `cover`, (buy, long) is `buy`."""
    rows = [_cover(attempt=1), _row("0000320193", "buy", decision_id=4, attempt=1)]
    assert next_attempt(rows, "0000320193", "cover") == 2
    assert next_attempt(rows, "0000320193", "buy") == 2
    assert next_attempt(rows, "0000320193", "short") == 1
    assert next_attempt(rows, "0000320193", "sell") == 1


def test_a_broken_short_sequence_is_refused() -> None:
    """The sequence check runs per token too: two short rows both at attempt 1."""
    rows = [_short_open(attempt=1), _short_open(attempt=1, decision_id=5)]
    with pytest.raises(ValueError, match="journaled attempts"):
        next_attempt(rows, "0000320193", "short")
    assert next_attempt(rows, "0000320193", "sell") == 1


def test_the_longest_id_is_pinned_within_128() -> None:
    """Item 5: the longest id stays within `alpaca.client_order_id_max_length`
    (128). `cover` and `short` are the longest tokens; the hand id below is 61
    characters."""
    settings = Settings(_env_file=None)
    assert settings.alpaca.client_order_id_max_length == 128
    longest = client_order_id(
        "tp", "main", date(2099, 12, 31), "0001652044:class-c-capital-stock", "cover", 99_999
    )
    assert longest == "tp-main-20991231-0001652044:class-c-capital-stock-cover-99999"
    assert len(longest) == 61
    assert max(len(token) for token in ids.ID_TOKENS) == len("cover") == len("short")
