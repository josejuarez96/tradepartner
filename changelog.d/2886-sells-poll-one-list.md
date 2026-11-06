- Paper sells wait: one open-orders read per poll round instead of one get_order per open sell (#2614 item 1); #2886's book-order wait needs no code (per-book plists or T377b/T378).
### Changed
- Paper run: the sells' wait reads the broker's open-orders list once per poll round, and a sell leaves the wait only when its own read is terminal, so the buys go out sooner after the last sell fills (#2614 item 1).
