- T226 done: OrderRequest.position_intent (defaults from side), Broker.paper, fake broker short rules (paper only)
### Added
- Broker shape for shorts: every order carries a position_intent (sent to Alpaca), Broker.paper, and the fake broker refuses shorts as the paper sandbox does (T226, #1885)
