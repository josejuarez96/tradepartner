- research-labeling job: the per-call record names its arm and vendor, one Jev label selector, per-vendor spend (T174)

### Changed
- research-labeling `job`: every record now carries `arm` (`jev`) and `vendor` (`typesafe`) and `price_snapshot_output` (null for Jev); a record without `arm` reads as Jev, and every reader other than the arms tables and the Claude spend sum keeps `arm == "jev"` records only, so a record of another arm can never become a label or move a gate (#1504)

### Added
- research-labeling `job.jev_label_record`: the listing end's label under the registered treatment -- B's record where B ran and resolved, else A's, whatever the file order; `scored_items`, the shortlist, the drift baseline and comparison and the reversed-order baseline read through it (#1504)
- research-labeling `job`: `spend_sums` splits by `vendor`, both vendors counted under `research.spend_ceiling_usd_total` and `_month` and the `anthropic` sum alone under `research.claude.spend_ceiling_usd_total`; `spend_check` takes the vendor and applies the three ceilings; `record_cost` for an `anthropic` record keeps its full worst-case estimate when no usage came back; `dry_run` prints both vendors' sums and the headroom under all three (#1504)
