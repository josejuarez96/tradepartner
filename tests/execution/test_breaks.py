"""Pure break state, cause and size (paper spec amendment 2026-10-26, #2266,
items 2, 4, 5 and 14; plan T280b)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest

from tradepartner.adapters.broker import CashActivity
from tradepartner.calendar import session_close
from tradepartner.config import RiskConfig
from tradepartner.execution import breaks
from tradepartner.execution.breaks import BreakPart, StoreEvidence
from tradepartner.store.journal import ReconcileBreakRow

RISK = RiskConfig()
S = date(2026, 10, 1)
#: The tenth XNYS session back from S, inclusive: the default lookback's first day.
FIRST = date(2026, 9, 18)
CUT = session_close(date(2026, 9, 30))  # close(S-1)


def _store(kind: str, on: date = S, known_at: datetime = CUT) -> StoreEvidence:
    return StoreEvidence(kind=kind, on=on, known_at=known_at)  # type: ignore[arg-type]


def _activity(activity_type: str, on: date = S) -> CashActivity:
    return CashActivity(activity_id="A1", activity_type=activity_type, date=on, status="executed")


def _cause(
    kind: str,
    store: tuple[StoreEvidence, ...] = (),
    activities: tuple[CashActivity, ...] = (),
    *,
    own_trading: bool = False,
    activity_type: str | None = None,
) -> str:
    return breaks.part_cause(
        kind,
        session=S,
        lookback_sessions=RISK.break_cause_lookback_sessions,
        store_rows=store,
        activities=activities,
        own_trading=own_trading,
        activity_type=activity_type,
    )


#: Item 14's table: (kind, the name cash activity type, each store row that fits,
#: each broker activity type that fits).
TABLE = [
    ("quantity", None, ["split"], ["SSP", "SSO", "REORG"]),
    ("ledger_only_position", None, ["listing_end", "merger"], ["MA", "REORG", "VOF", "NC", "SC"]),
    (
        "broker_only_position",
        None,
        ["spinoff", "symbol_change"],
        ["SSO", "SSP", "NC", "SC", "REORG"],
    ),
    ("unknown_symbol", None, ["spinoff", "symbol_change"], ["SSO", "SSP", "NC", "SC", "REORG"]),
    ("unknown_security", None, ["listing_end", "symbol_change"], ["NC", "SC"]),
    ("short_external_change", None, ["merger", "listing_end"], []),
    (breaks.NAME_CASH, "DIV", ["dividend"], []),
    (breaks.NAME_CASH, "DIVNRA", ["dividend"], []),
    (breaks.NAME_CASH, "MA", ["merger", "listing_end"], []),
    (breaks.NAME_CASH, "REORG", ["merger", "listing_end"], []),
    (breaks.NAME_CASH, "VOF", ["merger", "listing_end"], []),
    (breaks.NAME_CASH, "CIL", ["split", "spinoff"], []),
]
STORE_KINDS = ["split", "spinoff", "dividend", "merger", "listing_end", "symbol_change"]
BROKER_TYPES = ["SSP", "SSO", "REORG", "MA", "VOF", "NC", "SC", "DIV", "CIL", "FEE"]


@pytest.mark.parametrize(("kind", "activity_type", "fits", "_b"), TABLE)
@pytest.mark.parametrize("store_kind", STORE_KINDS)
def test_store_row_sets_corporate_action_only_when_it_fits_the_kind(
    kind: str, activity_type: str | None, fits: list[str], _b: list[str], store_kind: str
) -> None:
    """Item 14: a store row in the window sets `corporate_action` only when its
    type can produce the kind; any other row leaves the part `unattributed`."""
    expected = "corporate_action" if store_kind in fits else "unattributed"
    assert _cause(kind, (_store(store_kind),), activity_type=activity_type) == expected


@pytest.mark.parametrize(("kind", "activity_type", "_s", "fits"), TABLE)
@pytest.mark.parametrize("broker_type", BROKER_TYPES)
def test_broker_activity_sets_its_cause_only_when_it_fits_the_kind(
    kind: str, activity_type: str | None, _s: list[str], fits: list[str], broker_type: str
) -> None:
    """Item 14: a broker activity on the name in the window sets
    `broker_activity` only for the types the kind's row names (none for a
    short or a name cash break: the activity is the break itself)."""
    expected = "broker_activity" if broker_type in fits else "unattributed"
    activities = (_activity(broker_type),)
    assert _cause(kind, activities=activities, activity_type=activity_type) == expected


@pytest.mark.parametrize(("kind", "activity_type", "store_fits", "broker_fits"), TABLE)
def test_own_trading_forces_unattributed_whatever_the_evidence(
    kind: str, activity_type: str | None, store_fits: list[str], broker_fits: list[str]
) -> None:
    """Item 14: an own fill or own open order on the name since the latest ok or
    breaks reconciliation forces `unattributed`, whatever the evidence."""
    store = tuple(_store(k) for k in store_fits)
    activities = tuple(_activity(t) for t in broker_fits)
    assert _cause(kind, store, activities, activity_type=activity_type) != "unattributed"
    assert (
        _cause(kind, store, activities, own_trading=True, activity_type=activity_type)
        == "unattributed"
    )


@pytest.mark.parametrize("kind", ["merger_conflict", "unexplained_dividend_group"])
def test_the_two_store_findings_are_data_unless_we_traded(kind: str) -> None:
    assert _cause(kind) == "data"
    assert _cause(kind, own_trading=True) == "unattributed"


@pytest.mark.parametrize("activity_type", ["FEE", "JNLC", "INT", "SSP", "NEWTYPE", None])
def test_a_name_cash_break_of_another_type_has_no_external_cause(activity_type: str | None) -> None:
    store = tuple(_store(k) for k in STORE_KINDS)
    activities = tuple(_activity(t) for t in BROKER_TYPES)
    assert _cause(breaks.NAME_CASH, store, activities, activity_type=activity_type) == (
        "unattributed"
    )


def test_evidence_counts_only_inside_the_lookback_sessions() -> None:
    """Item 14: "in the window" is a date within the lookback's XNYS sessions up
    to S, both ends included; a lookback of 0 has no window."""
    assert _cause("quantity", (_store("split", FIRST),)) == "corporate_action"
    assert _cause("quantity", (_store("split", date(2026, 9, 17)),)) == "unattributed"
    assert _cause("quantity", (_store("split", date(2026, 10, 2)),)) == "unattributed"
    assert _cause("quantity", activities=(_activity("SSP", FIRST),)) == "broker_activity"
    assert _cause("quantity", activities=(_activity("SSP", date(2026, 9, 17)),)) == ("unattributed")
    zero = breaks.part_cause(
        "quantity",
        session=S,
        lookback_sessions=0,
        store_rows=(_store("split"),),
        activities=(_activity("SSP"),),
        own_trading=False,
    )
    assert zero == "unattributed"


def test_a_store_row_known_after_close_of_s_minus_1_sets_no_cause() -> None:
    """Item 13 (no look-ahead): a row stamped after close(S-1) never sets the
    cause at S, though its date is in the window."""
    late = _store("split", known_at=CUT + timedelta(microseconds=1))
    assert _cause("quantity", (late,)) == "unattributed"
    assert _cause("quantity", (_store("split", known_at=CUT),)) == "corporate_action"


def test_a_naive_known_at_is_refused() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        _store("split", known_at=datetime(2026, 9, 30))  # noqa: DTZ001


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError, match="not a name-level break kind"):
        _cause("cash")


def _part(cause: str, kind: str = "quantity") -> BreakPart:
    return BreakPart(kind=kind, cause=cause)


def test_break_cause_is_unattributed_if_any_part_is() -> None:
    """Item 14: for E.2 a break counts as `unattributed` if any part is."""
    assert breaks.break_cause([_part("corporate_action")]) == "corporate_action"
    assert breaks.break_cause([_part("corporate_action"), _part("unattributed")]) == (
        "unattributed"
    )
    assert breaks.break_cause([_part("corporate_action"), _part("data")]) == "data"
    assert breaks.break_cause([_part("broker_activity"), _part("corporate_action")]) == (
        "broker_activity"
    )
    with pytest.raises(ValueError, match="no parts"):
        breaks.break_cause([])


def test_book_level_kinds_are_not_name_breaks() -> None:
    """Items 2 and 3: an unknown kind is book-level (fail closed)."""
    for kind in ("quantity", "unknown_symbol", "merger_conflict", breaks.NAME_CASH):
        assert breaks.is_name_level(kind)
    for kind in ("account_id", "cash", "unexplained_activity", "foreign_order", "bad_reading", "x"):
        assert not breaks.is_name_level(kind)


def test_identity_is_stable_and_new_per_opening_reconciliation() -> None:
    """Item 4: `<window_id>:<name>:<opening reconciliation_id>`, the name the
    security, else the broker symbol; the same inputs give the same id and a
    break opened again on one session by a later reconciliation gets a new one."""
    first = breaks.identity(7, "SEC_SPY", "SPY", 41)
    assert first == ("SEC_SPY", "7:SEC_SPY:41")
    assert breaks.identity(7, "SEC_SPY", "SPY", 41) == first
    assert breaks.identity(7, "SEC_SPY", "SPY", 42) == ("SEC_SPY", "7:SEC_SPY:42")
    assert breaks.identity(7, None, "ZZZ", 41) == ("ZZZ", "7:ZZZ:41")
    with pytest.raises(ValueError, match="neither"):
        breaks.identity(7, None, None, 41)


def test_finding_keys_and_parts_json_are_canonical() -> None:
    """Item 4: each finding key is a deterministic string; `parts_json` lists the
    parts in one order whatever order they came in."""
    assert breaks.merger_conflict_key(["M2", "M1"]) == breaks.merger_conflict_key(["M1", "M2"])
    assert breaks.unknown_symbol_key("ZZZ", 10.0) != breaks.unknown_symbol_key("ZZZ", 11.0)
    key = breaks.dividend_group_key("SEC_A", [date(2026, 9, 2), date(2026, 9, 1)])
    assert key == breaks.dividend_group_key("SEC_A", [date(2026, 9, 1), date(2026, 9, 2)])
    cash = BreakPart(
        kind=breaks.NAME_CASH, cause="unattributed", cash_amount=3.5, activity_ids=("B", "A")
    )
    qty = BreakPart(
        kind="quantity", cause="corporate_action", ledger_quantity=10.0, broker_quantity=20.0
    )
    assert breaks.parts_json([cash, qty]) == breaks.parts_json([qty, cash])
    parsed = json.loads(breaks.parts_json([cash]))
    assert parsed[0]["activity_ids"] == ["A", "B"]
    assert parsed[0]["finding_key"] is None


def test_dollar_size_is_quantity_times_price_plus_cash() -> None:
    """Item 4: |broker - ledger| x reference price + |cash|; no price is null."""
    assert breaks.dollar_size(10.0, 12.0, 100.0, -5.0) == pytest.approx(205.0)
    assert breaks.dollar_size(12.0, 10.0, 100.0, None) == pytest.approx(200.0)
    assert breaks.dollar_size(None, 3.0, 50.0, None) == pytest.approx(150.0)
    assert breaks.dollar_size(10.0, 10.0, None, 4.0) is None
    for bad_price in (0.0, -1.0, float("nan"), float("inf")):
        assert breaks.dollar_size(10.0, 12.0, bad_price, None) is None
    with pytest.raises(ValueError, match="finite"):
        breaks.dollar_size(float("nan"), 1.0, 1.0, None)


def _material(
    size: float | None,
    kinds: tuple[str, ...] = ("quantity",),
    prior: float = 0.0,
    equity: float = 100_000.0,
    risk: RiskConfig = RISK,
) -> bool:
    return breaks.is_material(
        kinds, size, prior_auto_accepted=prior, ledger_equity=equity, risk=risk
    )


def test_materiality_is_at_or_below_both_thresholds_cumulatively() -> None:
    """Item 5: immaterial at or below both $25 and 5 bps of equity; the third $10
    on one name is material (prior plus this one); a $100 book accepts $0.05."""
    assert not _material(10.0)
    assert not _material(25.0)
    assert _material(25.01)
    assert not _material(10.0, prior=10.0)
    assert _material(10.0, prior=20.0)
    assert not _material(0.05, equity=100.0)
    assert _material(0.06, equity=100.0)


def test_always_material_cases() -> None:
    """Item 5: a short's external change, an unpriced break, the three finding
    kinds, a non-finite or non-positive equity, and a threshold of 0."""
    assert _material(None)
    for kind in ("short_external_change", "unknown_symbol", "merger_conflict"):
        assert _material(0.0, kinds=("quantity", kind))
    assert _material(0.0, kinds=("unexplained_dividend_group",))
    assert _material(1.0, equity=float("nan"))
    assert _material(1.0, equity=0.0)
    assert _material(0.01, risk=RISK.model_copy(update={"break_materiality_usd": 0.0}))
    assert not _material(0.0, risk=RISK.model_copy(update={"break_materiality_usd": 0.0}))


def test_over_cap_counts_and_shares() -> None:
    """Item 5: more open material breaks than the count, or a priced total above
    the share of equity, is over the cap; an unpriced one counts by number only;
    a cap of 0 makes any material break a halt."""
    assert not breaks.over_cap([10.0, 10.0, None], ledger_equity=100_000.0, risk=RISK)
    assert breaks.over_cap([10.0, 10.0, 10.0, None], ledger_equity=100_000.0, risk=RISK)
    assert not breaks.over_cap([2_000.0], ledger_equity=100_000.0, risk=RISK)
    assert breaks.over_cap([2_000.01], ledger_equity=100_000.0, risk=RISK)
    assert breaks.over_cap([1.0], ledger_equity=float("nan"), risk=RISK)
    assert not breaks.over_cap([], ledger_equity=0.0, risk=RISK)
    zero = RISK.model_copy(update={"max_open_breaks": 0})
    assert breaks.over_cap([None], ledger_equity=100_000.0, risk=zero)


def _event(event_id: int, break_id: str, event: str) -> ReconcileBreakRow:
    at = datetime(2026, 10, 1, 20, tzinfo=UTC)
    return ReconcileBreakRow(
        event_id=event_id,
        break_id=break_id,
        window_id=7,
        book_id="main",
        event=event,
        parts_json="[]",
        cause="unattributed" if event == "opened" else None,
        security_id=break_id.split(":")[1],
        material=True,
        first_seen_session=S,
        reconciliation_id=1,
        known_at=at,
        ingested_at=at,
    )


def test_state_is_the_latest_event_in_write_order() -> None:
    """Item 4: open after `opened`, `observed` or `reopened`; closed after a
    closing event; read from the latest event per break in `event_id` order."""
    events = [
        _event(3, "7:B:1", "opened"),
        _event(1, "7:A:1", "opened"),
        _event(2, "7:A:1", "auto_accepted"),
        _event(4, "7:C:1", "opened"),
        _event(5, "7:C:1", "trued_up"),
        _event(6, "7:C:1", "reopened"),
        _event(7, "7:B:1", "observed"),
    ]
    assert list(breaks.open_breaks(events)) == ["7:C:1", "7:B:1"]
    assert breaks.open_breaks(events)["7:B:1"].event == "observed"
    assert breaks.is_open("observed") and not breaks.is_open("resolved_by_broker")
    with pytest.raises(ValueError, match="not a break event"):
        breaks.is_open("deleted")


@pytest.mark.parametrize("kinds", [(), ("cash",), ("quantity", "account_id"), ("x",)])
def test_materiality_refuses_book_level_or_no_kinds(kinds: tuple[str, ...]) -> None:
    """Items 2 and 3 (fail closed): a book-level, unknown or empty kind set is
    never judged immaterial, so it can never be auto-accepted."""
    with pytest.raises(ValueError, match="name-level kinds only"):
        _material(1.0, kinds=kinds)


def test_negative_sizes_and_totals_are_refused() -> None:
    """Item 5's cumulative bound and the cap cannot be lowered by a negative."""
    with pytest.raises(ValueError, match=">= 0"):
        _material(-1.0)
    with pytest.raises(ValueError, match=">= 0"):
        _material(10.0, prior=-30.0)
    with pytest.raises(ValueError, match=">= 0"):
        breaks.over_cap([2_000.0, -1.0], ledger_equity=100_000.0, risk=RISK)


def test_unknown_symbol_key_is_stable_across_int_and_float() -> None:
    assert breaks.unknown_symbol_key("ZZZ", 10) == breaks.unknown_symbol_key("ZZZ", 10.0)


def test_cause_refuses_a_session_that_is_not_xnys() -> None:
    with pytest.raises(ValueError, match="not an XNYS session"):
        breaks.part_cause(
            "quantity",
            session=date(2026, 10, 3),
            lookback_sessions=10,
            store_rows=(),
            activities=(),
            own_trading=False,
        )


def test_open_breaks_refuses_an_unwritten_event() -> None:
    row = _event(1, "7:A:1", "opened")
    with pytest.raises(ValueError, match="event_id"):
        breaks.open_breaks([replace(row, event_id=None)])


# --- restriction and the cap over open material breaks (T282a; items 5 and 7) ----------


def _open(
    event_id: int,
    security_id: str | None,
    *,
    material: bool = True,
    size: float | None = 10.0,
    event: str = "opened",
    symbol: str | None = None,
) -> ReconcileBreakRow:
    name = security_id or symbol
    return replace(
        _event(event_id, f"7:{name}:1", event),
        security_id=security_id,
        symbol=symbol,
        material=material,
        dollar_size=size,
    )


def test_restricted_names_are_the_open_material_breaks_security_ids() -> None:
    """Item 7: restriction is derived: an open material break on the name, and nothing else."""
    events = [
        _open(1, "A"),
        _open(2, "B", material=False),
        _open(3, "C"),
        _open(4, "C", event="trued_up"),
        _open(5, None, symbol="ZZZ"),
        _open(6, "D", event="observed"),
    ]
    assert breaks.restricted_names(events) == frozenset({"A", "D"})
    assert breaks.restricted_names([]) == frozenset()


def test_the_cap_is_four_material_breaks_or_one_above_two_percent() -> None:
    """The spec's acceptance: four material breaks, or one above 2 % of equity."""
    equity = 100_000.0
    three = [_open(i, name) for i, name in enumerate("ABC", 1)]
    assert not breaks.cap_exceeded(three, ledger_equity=equity, risk=RISK)
    assert breaks.cap_exceeded([*three, _open(4, "D")], ledger_equity=equity, risk=RISK)
    assert not breaks.cap_exceeded([_open(1, "A", size=2_000.0)], ledger_equity=equity, risk=RISK)
    assert breaks.cap_exceeded([_open(1, "A", size=2_000.01)], ledger_equity=equity, risk=RISK)


def test_the_cap_ignores_immaterial_and_closed_breaks_and_counts_unpriced_by_count() -> None:
    equity = 100_000.0
    quiet = [
        _open(1, "A", material=False, size=50_000.0),
        _open(2, "B"),
        _open(3, "B", event="resolved_by_broker"),
        _open(4, "C", size=None),
        _open(5, None, symbol="ZZZ", size=None),
    ]
    assert not breaks.cap_exceeded(quiet, ledger_equity=equity, risk=RISK)
    assert breaks.cap_exceeded(
        [*quiet, _open(6, "D"), _open(7, "E")], ledger_equity=equity, risk=RISK
    )


def test_a_cap_of_zero_makes_every_material_break_a_halt() -> None:
    zero = RiskConfig(max_open_breaks=0)
    assert breaks.cap_exceeded([_open(1, "A")], ledger_equity=100_000.0, risk=zero)
    assert not breaks.cap_exceeded([_open(1, "A", material=False)], ledger_equity=1e5, risk=zero)


@pytest.mark.parametrize(
    ("ledger", "broker", "limit"),
    [
        (10.0, 6.0, 6.0),
        (6.0, 10.0, 6.0),
        (10.0, 0.0, 0.0),
        (0.0, 10.0, 0.0),
        (10.0, -3.0, 0.0),  # the broker is short what the ledger holds long: never cross zero
        (-10.0, -4.0, 4.0),  # a short cover
        (-10.0, 4.0, 0.0),
    ],
)
def test_a_reducing_order_never_crosses_zero_on_either_book(
    ledger: float, broker: float, limit: float
) -> None:
    assert breaks.reducing_limit(ledger, broker) == limit


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_reducing_limit_of_a_non_finite_quantity_is_refused(bad: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        breaks.reducing_limit(bad, 1.0)
