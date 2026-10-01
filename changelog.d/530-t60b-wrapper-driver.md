- T60b wrapper driver: RiskGatedBroker.execute trades a batch in two phases (sells, then buys over account().cash less the open-buy reserve), with the switch, pre-check, risk gate, missed rows, ack poll and write-offs (#530).
### Added
- RiskGatedBroker.execute(run, decisions, forced_exits) -> BatchOutcome: the two-phase driver over phases, reattempts, reserve and risk.check_phase, journaling every order before its submit (T60b, #530).
