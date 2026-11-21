- T445 (#3655): property tests for the numeric core (hypothesis, dev only; P1 to P12 under tests/properties); trade_cost, buy_notional_after_costs, expected_max_sharpe and round_down refuse non-finite or unroundable input with ValueError.
### Added
- tests/properties: 12 Hypothesis properties (P1 to P12) for metrics, costs, round_down/open_sold and split adjustment, deterministic under the default ci profile; hypothesis is a dev dependency (#3655).
### Fixed
- trade_cost, buy_notional_after_costs and expected_max_sharpe returned NaN or inf for a non-finite input, and round_down raised decimal.InvalidOperation for a quantity with too many digits; each now raises ValueError (#3655).
