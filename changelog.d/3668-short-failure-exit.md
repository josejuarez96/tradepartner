task: T418 (PR #3676)
- T418: a short's exit under a failure-return policy is covered at its last close with no failure gain (max(f,1) on a merger), counted n_short_failure_uncredited; PR #3676 draft, quant-auditor pending
### Added
- A short whose listing ends under a failure-return policy is covered at its last close with no failure gain credited, and counted n_short_failure_uncredited, instead of failing the trial (#3668).
