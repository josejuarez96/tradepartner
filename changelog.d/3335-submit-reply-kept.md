- Paper wrapper keeps the submit reply: a confirmed order is never closed not_received or resent (#3335, PR #3370)
### Fixed
- Paper wrapper journals the submit reply's broker order id on pending_unresolved; the next run reads it by that id or stops, never closing a confirmed order not_received (#3335)
