- Phase 4 T54d: `execution.reserve.open_buy_reserve`, the unfilled notional of our own non-terminal buys from any session (quantity buys split-adjusted, at the buffered price), `Decimal`, rounded up to the cent
### Added
- `execution/reserve.py`: `open_buy_reserve`, the cash reserve for our own open buys that T60b subtracts before buy sizing and the cash rule (ADR 0010 amendment 2026-10-01; T54d, #481)
