### Added
- `corporate_actions.source_action_id` (`''` = none) and `corporate_actions.cancelled`, `CorporateAction.source_action_id`/`cancelled`, schema version 2, fixture cases `SEC_SPLIT_REDATED` and `SEC_DIV_CANCELLED` (#108).
### Fixed
- A corporate action re-dated by the source no longer applies twice: an action's identity is its source id when present, else `(security_id, action_type, ex_date)`; `adjusted_prices_as_of` and `dropped_dividends_as_of` take the latest revision per identity before the ex-date and cancel filters, so a re-date past T or a cancellation retires the older revision (#108).
