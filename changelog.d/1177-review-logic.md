- T123b review logic (#1177): review.py builds the blind review session from a batch's shortlist, records decisions and undos append-only; finish registers the review file and writes yield and the weighted accuracies; test (d) runs.
### Added
- Research labeling review logic (`research/labeling/review.py`, T123b): the blind review session over a frame batch's shortlist, `record_decision` with attribution after the save, `record_undo`, and `finish` with the batch metrics of req 10 (`yield`, stratum-weighted model and rule accuracy, `n_reviewed`, `n_deferred`).
