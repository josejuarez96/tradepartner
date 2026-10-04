- Wrapper reads the kill switch before the #569 open-buy reserve pre-check: an engaged switch ends the batch skipped_kill_switch, never a halt on an unpriced open buy (#692, owner Q1=B, no narrowing).
### Changed
- The risk-gated wrapper reads the kill switch before the open-buy reserve pre-check, so an engaged switch skips the batch instead of halting on an unpriced open quantity buy (#692).
