- #332 Schema version 6: order_events.reason is a nullable closed set {halt, not_received} (schema.ORDER_EVENT_REASONS, used by plan.decision_state); the version-5 migration rebuilds order_events, refusing on a stray reason
### Added
- Schema version 6: CHECK on order_events.reason ({halt, not_received} or null) with shared constants, and a migration from version 5 that rebuilds order_events (#332)
