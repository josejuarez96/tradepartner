"""1099-B reconciliation of the lot ledger (Phase 4 plan T90; ADR 0010
amendment 2026-10-04; research G7 item 9, R22, R23).

`compare(rows, disposals, lots, flags)` is pure over typed rows: the broker's
realised-gains or 1099-B export as `BrokerLotRow`s, and the ledger's
`disposals`, `lots` and `wash_sale_flags` rows exactly as `execution.outcomes`
wrote them (no as-of: the comparison is of the ledger as journaled). It takes no
connection and writes nothing; nothing is adjusted automatically and the ledger
stays append-only.

- **Matching.** Each export row, in its order, is matched to one unused
  disposal whose lot is the same security, whose exchange-local trade date is
  the row's trade date and whose quantity equals the row's exactly. The same
  security means equal CUSIPs when both sides carry one, else equal broker
  symbols. When more than one disposal qualifies (one sale closing two equal
  lots) the first, in disposal order, whose figures all agree is taken, else
  the first.
- **Figures.** Proceeds, cost basis (the disposal's proceeds less its realised
  gain or loss, before any adjustment) and the box 1g disallowed loss (the sum
  of the disposal's `wash_sale_flags`), each in `Decimal` from the stored
  float's `repr` and rounded half-up to the cent, as a 1099-B reports them.
  Every figure that differs is returned with both values, for Form 8949 with
  code W.
- **Expected.** The ledger stores basis before any wash-sale carry-forward
  (G7 items 6 and 7, Phase 6), so for a disposal of a lot named as a
  replacement in `wash_sale_flags` a cost-basis or box 1g difference is
  reported as "expected: carry-forward not modelled", not as a mismatch. A
  proceeds difference is still a mismatch: a carry-forward never changes
  proceeds.
- **Unmatched.** Every export row with no disposal, and every disposal with no
  export row, is returned.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from tradepartner.store.journal import DisposalRow, LotRow, WashSaleFlagRow

#: A 1099-B reports amounts in dollars and cents; ledger figures are rounded to it.
CENT = Decimal("0.01")

PROCEEDS = "proceeds"
COST_BASIS = "cost_basis"
DISALLOWED_LOSS = "box_1g_disallowed_loss"
#: The figures compared, in report order.
FIGURES: tuple[str, ...] = (PROCEEDS, COST_BASIS, DISALLOWED_LOSS)
#: The figures a wash-sale carry-forward onto a replacement lot can change.
_CARRY_FORWARD_FIGURES = frozenset({COST_BASIS, DISALLOWED_LOSS})

MATCH = "match"
DIFFERENCE = "difference"
EXPECTED = "expected"


class LedgerSetError(ValueError):
    """The ledger rows passed in do not form one set (a disposal whose lot is
    not among `lots`)."""


@dataclass(frozen=True)
class BrokerLotRow:
    """One row of the broker's realised-gains or 1099-B export: the sale's
    symbol, CUSIP (None when the export gives none), trade date, quantity,
    proceeds, cost basis and box 1g wash-sale loss disallowed (zero when blank)."""

    symbol: str
    cusip: str | None
    trade_date: date
    quantity: Decimal
    proceeds: Decimal
    cost_basis: Decimal
    disallowed_loss: Decimal

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("a broker row needs a symbol")
        for name in ("quantity", "proceeds", "cost_basis", "disallowed_loss"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"a broker row's {name} must be a finite Decimal: {value!r}")
        if self.quantity <= 0:
            raise ValueError(f"a broker row's quantity must be positive: {self.quantity}")


@dataclass(frozen=True)
class FigureDifference:
    """One figure on which the export and the ledger disagree, with both values."""

    figure: str
    broker: Decimal
    ledger: Decimal


@dataclass(frozen=True)
class MatchedRow:
    """An export row and the disposal it matched, with every figure that
    differs. `replacement_lot` is True when the disposal's lot is named as a
    replacement in `wash_sale_flags`."""

    row: BrokerLotRow
    disposal_id: int
    lot_id: int
    differences: tuple[FigureDifference, ...]
    replacement_lot: bool

    @property
    def mismatches(self) -> tuple[FigureDifference, ...]:
        """The differences that count as mismatches (module docstring, "Expected")."""
        if not self.replacement_lot:
            return self.differences
        return tuple(d for d in self.differences if d.figure not in _CARRY_FORWARD_FIGURES)

    @property
    def expected(self) -> tuple[FigureDifference, ...]:
        """The differences reported as "expected: carry-forward not modelled"."""
        if not self.replacement_lot:
            return ()
        return tuple(d for d in self.differences if d.figure in _CARRY_FORWARD_FIGURES)

    @property
    def status(self) -> str:
        """`difference` on any mismatch, else `expected` on any expected
        difference, else `match`."""
        if self.mismatches:
            return DIFFERENCE
        return EXPECTED if self.expected else MATCH


@dataclass(frozen=True)
class Report:
    """What `compare` found: every matched row, every unmatched export row and
    every unmatched ledger disposal."""

    matched: tuple[MatchedRow, ...]
    unmatched_rows: tuple[BrokerLotRow, ...]
    unmatched_disposals: tuple[DisposalRow, ...]

    @property
    def has_difference(self) -> bool:
        """True on any mismatched figure or any unmatched row on either side."""
        return (
            any(m.status == DIFFERENCE for m in self.matched)
            or bool(self.unmatched_rows)
            or bool(self.unmatched_disposals)
        )

    @property
    def clean(self) -> bool:
        """At least one row matched and there is no difference. Zero matched
        rows is never clean."""
        return bool(self.matched) and not self.has_difference


def compare(
    rows: Sequence[BrokerLotRow],
    disposals: Sequence[DisposalRow],
    lots: Sequence[LotRow],
    flags: Sequence[WashSaleFlagRow],
) -> Report:
    """Match `rows` to `disposals` and their `lots` and compare the three
    figures (module docstring). Raises `LedgerSetError` when a disposal's lot
    is not in `lots`."""
    lot_by_id = {lot.lot_id: lot for lot in lots}
    ordered = sorted(disposals, key=lambda d: (d.disposal_id is None, d.disposal_id or 0))
    for disposal in ordered:
        if disposal.disposal_id is None:
            raise LedgerSetError("a disposal row has no disposal_id")
        if disposal.lot_id not in lot_by_id:
            raise LedgerSetError(
                f"disposal {disposal.disposal_id} names lot {disposal.lot_id}, not in the set"
            )
    disallowed = disallowed_by_disposal(flags)
    replacements = frozenset(flag.replacement_lot_id for flag in flags)
    used: set[int] = set()
    matched: list[MatchedRow] = []
    unmatched_rows: list[BrokerLotRow] = []
    for row in rows:
        candidates = [
            d
            for d in ordered
            if d.disposal_id not in used and _same_sale(row, d, lot_by_id[d.lot_id])
        ]
        if not candidates:
            unmatched_rows.append(row)
            continue
        scored = [(d, _differences(row, d, disallowed)) for d in candidates]
        disposal, differences = next(((d, diffs) for d, diffs in scored if not diffs), scored[0])
        disposal_id = disposal.disposal_id
        assert disposal_id is not None
        used.add(disposal_id)
        matched.append(
            MatchedRow(
                row=row,
                disposal_id=disposal_id,
                lot_id=disposal.lot_id,
                differences=differences,
                replacement_lot=disposal.lot_id in replacements,
            )
        )
    return Report(
        matched=tuple(matched),
        unmatched_rows=tuple(unmatched_rows),
        unmatched_disposals=tuple(d for d in ordered if d.disposal_id not in used),
    )


def disallowed_by_disposal(flags: Sequence[WashSaleFlagRow]) -> dict[int, Decimal]:
    """Each flagged disposal's total disallowed loss, in `Decimal`, unrounded."""
    totals: dict[int, Decimal] = {}
    for flag in flags:
        totals[flag.disposal_id] = totals.get(flag.disposal_id, Decimal(0)) + _dec(
            flag.disallowed_amount
        )
    return totals


def ledger_figures(disposal: DisposalRow, disallowed: Mapping[int, Decimal]) -> dict[str, Decimal]:
    """The disposal's three figures, rounded to the cent (module docstring)."""
    proceeds = _dec(disposal.proceeds)
    basis = proceeds - _dec(disposal.realised_pnl)
    loss = disallowed.get(disposal.disposal_id or 0, Decimal(0))
    return {
        PROCEEDS: _cents(proceeds),
        COST_BASIS: _cents(basis),
        DISALLOWED_LOSS: _cents(loss),
    }


def _same_sale(row: BrokerLotRow, disposal: DisposalRow, lot: LotRow) -> bool:
    if disposal.trade_date_local != row.trade_date:
        return False
    if _dec(disposal.quantity) != row.quantity:
        return False
    if row.cusip and lot.cusip:
        return row.cusip.strip().upper() == lot.cusip.strip().upper()
    return row.symbol.strip().upper() == lot.symbol.strip().upper()


def _differences(
    row: BrokerLotRow, disposal: DisposalRow, disallowed: Mapping[int, Decimal]
) -> tuple[FigureDifference, ...]:
    ledger = ledger_figures(disposal, disallowed)
    broker = {
        PROCEEDS: _cents(row.proceeds),
        COST_BASIS: _cents(row.cost_basis),
        DISALLOWED_LOSS: _cents(row.disallowed_loss),
    }
    return tuple(
        FigureDifference(figure, broker[figure], ledger[figure])
        for figure in FIGURES
        if broker[figure] != ledger[figure]
    )


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def _dec(value: float) -> Decimal:
    return Decimal(repr(float(value)))
