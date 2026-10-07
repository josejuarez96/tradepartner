- T121b (#1068): packets.py (build_packet, next_kind) and scoring.py (Wilson/Clopper-Pearson, class_accuracy, precision/recall, ECE/Brier, flip_rate), pure; batch metrics moved to T123b (owner-approved plan amendment).
### Added
- research.labeling.packets (build_packet, next_kind: the A/B passage builder and call-order rule, req 3/C2) and research.labeling.scoring (Wilson and Clopper-Pearson one-sided lower bounds, class_accuracy against gold_label/text_states, per-option precision/recall, seeded_recall, unresolved/unlabelled shares, the worst-case bound, the rule_provision arm's coverage and class accuracy, ECE/multiclass Brier/classwise reliability, the drift probe's flip_rate and mean_abs_probability_shift; req 10, C5, C6) for the departure-reason pilot (T121b).
### Changed
- docs/plans/research-labeling.md: T121b's batch metrics (yield, the stratum-weighted accuracies, n_reviewed, n_deferred) move to T123b's finish(), per the plan line's own ~400-line size rule (#1068).
