# Trial Registry

Every backtest or strategy evaluation run gets a row: failures, parameter tweaks and reruns included. This is how we measure the multiple-testing burden, which feeds the deflated Sharpe calculation. It moves into the database in Phase 3.

| # | Date | Hypothesis (link) | Variant / params | Data window | Holdout touched? | Result (Sharpe, CAGR, maxDD, net of costs) | Run by | Notes |
|---|---|---|---|---|---|---|---|---|
