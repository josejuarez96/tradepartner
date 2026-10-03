# TradePartner Integration Note: ML/LLM Research Direction

Date: 2026-10-03
Status: Architecture/research note
Related areas: PIT store, EDGAR, backtest, trial registry, holdout, LLM governance.

## Summary

TradePartner already contains most of the difficult infrastructure required for serious ML research:

* point-in-time known_at discipline
* security-master and delisting handling
* UTC/tz-aware timestamps
* real exchange calendar
* EDGAR ingestion
* append-only trial registry
* explicit holdout spending
* survivorship-gap gating
* realistic cost/fill modeling
* paper-trading journal
* deterministic execution/risk boundaries

The recommended direction is therefore not a new repository. The ML research layer should be added on top of the existing system.

The largest architectural gap is the absence of a first-class research observation / feature / label / model-run layer between raw PIT facts and portfolio construction.

## Existing strengths to preserve

Do not rewrite:

* broker abstractions
* paper engine
* execution wrapper
* deterministic risk rules
* calendar layer
* price adapters
* PIT-store principles
* trial registry
* holdout protections
* configuration system
* dashboard read model
* agent/PR governance
* existing tests

These are the exact controls that make ML research credible.

## Proposed architecture

```
                         RAW SOURCES
                              |
              +---------------+----------------+
              |               |                |
           Alpaca           EDGAR          future data
              |               |
              +-------+-------+
                      |
               POINT-IN-TIME STORE
                      |
          +-----------+-------------+
          |                         |
   existing signals          research layer
      momentum                    |
                                  +-- events
                                  +-- documents
                                  +-- features
                                  +-- labels
                                  +-- datasets
                                  +-- model runs
                                         |
                              +----------+----------+
                              |                     |
                       deterministic            learned
                         features               models
                              |                     |
                              +----------+----------+
                                         |
                                      rankings
                                         |
                              EXISTING BACKTEST ENGINE
                                         |
                              costs / fills / portfolio
                                         |
                                  trial registry
                                         |
                               holdout / validation
                                         |
                                   paper engine
                                         |
                                deterministic risk
                                         |
                                       broker
```

## Recommended new conceptual layer

Prefer a research namespace such as:

```
src/tradepartner/research/
    events/
    features/
    labels/
    datasets/
    validation/
    models/
```

Do not place ML directly inside the execution path.

The research layer should produce immutable, versioned artifacts that the existing backtest engine can consume.

## Candidate future store objects

### research_events

```
event_id
security_id
event_type
event_at
known_at
trade_eligible_at
source_document_id
```

### research_features

```
event_id
feature_set_id
feature_name
value
known_at
generator
generator_version
provenance
```

### research_labels

```
event_id
label_name
horizon
value
label_start
label_end
```

### model_runs

```
model_run_id
dataset_version
feature_set_version
label_definition
train_window
validation_window
model_type
hyperparameters
random_seed
code_commit
trial_id
```

### model_predictions

```
model_run_id
event_id
score
known_at
```

Feature data and outcome labels should remain logically separate to reduce accidental leakage.

## Trial registry

The existing trial registry should remain the center of experiment governance.

Do not create an independent ML tracker that bypasses it.

Eventually a trial should capture:

```
hypothesis
dataset version
feature-set version
label definition
train window
validation window
model specification
hyperparameters
random seed
code commit
results
holdout status
```

Every hyperparameter search, feature-family test and model variation contributes to the multiple-testing burden.

## Validation change required for ML

The current in-sample/holdout design is appropriate for the first simple strategy, but ML requires an explicit development-validation structure inside the unspent research region.

Example:

```
Train      2012-2018
Validate   2019
Test       2020
Train      2012-2019
Validate   2020
Test       2021
Train      2012-2020
Validate   2021
Test       2022
```

with a final untouched holdout.

The existing holdout mechanism should remain the final gate.

## EDGAR extension

TradePartner already has substantial EDGAR ingestion infrastructure. The next research requirement is document-body and section history.

Potential objects:

```
filing_documents
filing_sections
```

Candidate fields:

```
accession_number
cik
form
filed_at
accepted_at
known_at
section
raw_text_hash
normalized_text
parser_version
```

The first useful pairing is:

```
10-Q[t].MD&A
    vs
10-Q[t-1].MD&A
```

and similarly for Risk Factors.

## Recommended research sequence

### Stage 1 — finish H1

Keep H1 unchanged. It is useful as a systems-validation hypothesis and validates the backtester, cost model, holdout controls and trial registry.

### Stage 2 — deterministic filing-change research

Before adding LLMs, implement:

* TF-IDF similarity
* changed-sentence fraction
* inserted/deleted sentence fraction
* section-length change
* Risk Factors additions
* MD&A additions/deletions

Research question:

Do changes in filings predict future fundamentals and/or returns in the TradePartner data environment?

This creates a baseline for later semantic methods.

### Stage 3 — Form 4 insider events

Add open-market/private purchases:

```
transaction_code = P
acquired_disposed = A
```

Store transaction date separately from filing/known time.

Test:

* purchase intensity
* insider role
* abnormal purchase size
* cluster purchases
* recent drawdown
* disclosure state

### Stage 4 — tabular ML

Only after useful baseline feature families exist, add learned combination models.

Start with:

* Elastic Net
* LightGBM / XGBoost

The model should output a cross-sectional ranking score, not an order.

Existing portfolio/backtest machinery then consumes that score.

### Stage 5 — LLM semantic extraction

This conflicts with the current interpretation of ADR 0008, which rejects LLM-produced extracted values as decision inputs.

The proposed research use is materially different from an LLM stock-picker:

```
document pair
    |
    v
fixed-schema semantic annotation
    |
    v
research feature
    |
    v
statistical validation
```

A future ADR should decide whether this class of research measurement is allowed while preserving the prohibition on direct LLM investment authority.

## Proposed LLM research constraints

If semantic extraction is admitted later:

* fixed taxonomy
* fixed output schema
* full provenance
* model/version logged
* prompt/schema version logged
* source passages retained
* no browsing/tools
* ticker and future-price context withheld where practical
* LLM output never overwrites source data
* LLM features must beat deterministic text baselines
* LLM-derived features require explicit promotion before production use
* deterministic portfolio/risk/execution rules remain authoritative

## Historical-model contamination

A 2026 frontier model analyzing a 2018 filing may contain post-2018 information in its parameters.

Therefore separate:

### Historical extraction research

Use historical documents to evaluate whether the model correctly measures changes in the documents.

### Prospective feature validation

Freeze the taxonomy, prompt, model and feature definitions, then generate features only as new disclosures arrive.

TradePartner’s existing known_at discipline is well suited to this.

## Architectural principle to revisit

The existing rule “code computes numbers” is strong for money handling but may become too restrictive for research once ML enters scope.

A more durable boundary would be:

> Learned or nondeterministic components may produce versioned research measurements and scores, but all portfolio construction, sizing, risk and execution rules remain deterministic and auditable.

This is a governance question, not a code shortcut, and should be handled by ADR before implementation.

## Recommended first new research family

After H1, consider a disclosure-change family.

Initial hypothesis:

Changes in 10-Q MD&A and Risk Factors contain incremental information about subsequent firm fundamentals and/or returns among underfollowed U.S. equities.

The first version should use deterministic text features only.

That provides a clean replication-style benchmark before any LLM cost or contamination issue enters the system.

## Bottom line

TradePartner is already closer to an ML research platform than it first appears.

The main missing pieces are:

1. event/research observation abstraction
2. document-body and section-level EDGAR history
3. feature/label dataset construction
4. walk-forward ML validation
5. model-run provenance
6. eventual ADR treatment of semantic LLM research features

The correct progression is:

```
quant research correctness
        |
        v
H1 validates engine
        |
        v
event/document research
        |
        v
deterministic textual signals
        |
        v
insider-event signals
        |
        v
ML signal combination
        |
        v
LLM semantic extraction
        |
        v
prospective validation
```

Do not jump directly from momentum to an LLM-driven trading model.
