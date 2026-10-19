- T228 (#1879): store schema 34 adds the short skip and cover reasons and decision kinds; order ids carry a buy, sell, short or cover token (PR #1881)
### Added
- Store schema version 34: the short skip and cover reasons and decision kinds in the decisions, decision_events and order_events CHECKs, and the short and cover id tokens with attempts keyed by (security, token).
