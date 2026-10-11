- #1290 item 1: risk.reconcile_cash_tolerance default 0.01 -> 1.26 (0.005 x 250 orders + 0.01, per-fill cent rounding), ADR 0010 amendment 2026-10-10; frozen windows keep their value (H1 window 1 froze 0.01); item 2 open.
### Changed
- `risk.reconcile_cash_tolerance` defaults to 1.26 (was 0.01) so per-fill cent rounding over a full run cannot halt a correct reconciliation; windows already started keep the value they froze (ADR 0010 amendment 2026-10-10, #1290).
