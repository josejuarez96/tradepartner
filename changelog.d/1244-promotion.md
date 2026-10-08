- T109 promotion and retirement (#1244): backtest/promotion.py promote() runs req 4's refusals in order, registers the argmax file and appends the promotion decision; retire() appends sweep_retired; stacked on #1242.
### Added
- Strategy lab: `promotion.promote` and `promotion.retire` (req 4): a complete sweep's argmax becomes a standalone hypothesis after every refusal (caps, identity, provenance, SR* high-water floor), with its `promotion` decision; neither changes N.
