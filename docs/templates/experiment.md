<!--
Copy to docs/experiments/<slug>.md. Merging the file does not register it: the owner
runs `tradepartner experiment register docs/experiments/<slug>.md` after merge
(docs/specs/research-registry.md req 2). The registry hashes the whole file, so any
later edit, prose included, makes a new registration: change the slug for a new
design, or add `amends_sha256` (the latest registration's `params_sha256`) to amend
this one. Settle the text before registering.
-->

# Experiment: <title>

**Kind:** <agreement | benchmark | robustness | economic | return>  ·  **Stage:** <2-7>  ·  **Author:**  ·  **Date:** YYYY-MM-DD

## Rationale
<!-- Why this design answers the question: the treatment, the outcome, and how the
primary metric bears on the claim(s) named below. -->

## Prior-evidence disclosure
<!-- Every result for this window, split or outcome already seen before registration:
papers, earlier exploratory looks, fund fact sheets. "None" only if true. Required for
`confirmatory = true` (req 7: a confirmatory run must predate its dataset's first
version, or bind a sealed split drawn under the registered seed). -->

## Parameters

The block below is the only part the registry parses (`research/experiment.py`). Rules
(docs/specs/research-registry.md req 2):
- `slug` equals this file's name without `.md`.
- `kind`, `stage`, `provenance`, every `splits` entry, `primary.direction` and
  `multiplicity.method` must be one of their code-constant sets.
- `kind = "return"` requires `touches_returns = true`.
- `touches_returns = true` requires `family` from `hypotheses.families`.
- Every `claims` id must exist in the sibling `docs/research/claims.toml`; on a
  `confirmatory` file none may be `UNGRADED`.
- `splits` may name `"test"` only when `confirmatory = true`.
- `confirmatory = true` is refused with `provenance = "model_historical"`.
- `multiplicity.family_id`/`.family_size` are required unless `multiplicity.method = "none"`.
- `budget.runs` and `primary.min_clusters` must be at least 1.
- A re-registration of an already-registered `slug` needs `amends_sha256` (the store
  checks it matches the latest registration's `params_sha256`; this file only carries
  it).
- Keep other fenced blocks out of the parameter block; its first bare ` ``` ` line
  closes it.

```toml experiment
slug = "<slug>"
kind = "<kind>"
stage = <2-7>
title = "<title>"
confirmatory = <true|false>
provenance = "<human|deterministic|classical|model_historical|model_prospective|pit_model>"
touches_returns = <true|false>
family = "<from hypotheses.families, required when touches_returns>"
claims = ["<claim id>", "..."]
hypothesis_ref = "<optional: docs/hypotheses slug or backlog id such as B5>"
seed = <published seed, integer>
splits = ["<dev|cal|test|prospective|pilot|full|none>", "..."]

[dataset]
name = "<dataset name, registered separately with `dataset register`>"
# sha256 = "<optional pin to one dataset version>"

[window]
start = YYYY-MM-DD
end = YYYY-MM-DD

[primary]
metric = "<metric name>"
direction = "<greater|less>"
# threshold = <optional decision threshold>
ci_level = 0.95
min_clusters = <numeric floor below which the result is underpowered>
inference = "<the CI or test method in words>"
secondary = ["<metric name>", "..."]
comparison_set = "<words: what is compared against what>"

[multiplicity]
method = "<holm|fixed_sequence|bh|none>"
# family_id = "<required unless method = \"none\">"
# family_size = <required unless method = \"none\">

[budget]
runs = <maximum runs across the amendment chain, failures and refusals included>
configurations = <maximum configurations evaluated across those runs>
stop_rule = "<words: sensitivity set and tolerance, the revision rule>"
expected_effect = "<words: the prior, at or below the brief's haircuts for return claims>"

# Only on an amendment:
# amends_sha256 = "<params_sha256 of the registration this one supersedes>"
```

## Expected magnitudes and red flags
<!-- What result would suggest a bug or look-ahead rather than a real effect. -->

## Retirement condition
<!-- The result that retires this design, stated before any run. -->
