- T84 (#714): settlement plumbing for req 17: schema 9 (owner_settled_unknown, settle_order, overrides.client_order_id), write-off protection, the late-live-fill chain rule in check and stop, override refusal; T84b next
### Added
- Schema version 9 (#571, spec req 17): `owner_settled_unknown` order-event reason, `settle_order` override kind with `overrides.client_order_id`; `paper check` and `paper stop` list an order with a live fill journaled after its terminal event; `window.override` and the override page refuse `settle_order`.
