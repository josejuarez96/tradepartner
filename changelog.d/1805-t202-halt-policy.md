- T202 halt policy: three per-name submit refusals are skips, skip cap = max(floor, share x plan), risk.max_skip_share frozen (PR #1829)
### Added
- Halt policy (T202): the broker's asset-not-found, not-fractionable and not-tradable refusals on a buy are skips (a rejected order plus a skipped decision, a skipped alert), and the skip cap is max(risk.max_skips_per_run, floor(risk.max_skip_share x decisions)); a window frozen before the key reads the share as 0.
