- Insider bulk reader hardened: strict UTF-8, per-member size cap, bounds on two events keys
### Fixed
- The DERA insider bulk reader fails a quarter closed on a non-UTF-8 byte or an oversized table member, and events.form4_bulk_order_sample / sec_forms_max_requests have upper bounds (#2528)
