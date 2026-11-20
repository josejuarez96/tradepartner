- Labeling: a request the vendor rejects (HTTP 400, 404, 405, 413, 422) stops the run failed after its record, at zero cost, instead of finishing a run that measured nothing (#3660).
### Fixed
- research/labeling: the first reply with HTTP 400, 404, 405, 413 or 422 ends the batch, the drift probe or the reversed pass `failed` with `request rejected: HTTP <status>` after its record is written; that record costs 0 on the Jev and the Claude cost path (#3660).
