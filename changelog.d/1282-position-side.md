- T134 (ADR 0015 seam 2): check_phase refuses any non-long order or session decisions/orders row as refused_position_side (LimitBreachError, no submit); wrapper journals orders long explicitly; lots.rebuild refuses short orders.
### Added
- refused_position_side: the wrapper halts a batch holding any short order or session row before its first submit, and the lot ledger refuses a short order (ADR 0015 seam 2, T134).
