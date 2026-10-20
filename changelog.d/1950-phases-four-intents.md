- T230: phases.py orders the four intents: covers in the sell phase, short opens then long buys in the buy phase, flips wait on their first leg (#1967)
### Added
- execution/phases.py: sell_orders orders covers (buy_to_close, whole shares, capped at the held short less open covers); buy_orders orders short opens (sell_to_open, whole shares, borrow re-check) before long buys sized within cash less held shorts; requests_for sets position_intent and the T228 id token (T230, #1950)
