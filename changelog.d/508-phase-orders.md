- T60c done (PR #513): execution/phases.py builds the pure sells and buys phase orders (sell_orders, buy_orders, requests_for) for T60b's driver; follow-ups in #518.
### Added
- Pure phase-order building for the paper wrapper (execution/phases.py, T60c): quantity sells for full exits and trims with the per-name skips, buys sized through risk.size_buys with deferrals and the cash left, and validated OrderRequests with attempt-counted ids.
