- #2493: event-study runner matches causally by default (size cuts and peers known before each entry open); C1b ex-post cell only as a named, disclosed [study] option; spec amended 2026-10-30
### Fixed
- Event-study runner: `[study].matching` is `causal` (default) or `ex_post`; causal size-bucket cuts and peer pools use only events known strictly before each event's entry open, never later peers; the C1b golden names `ex_post` and still reproduces run 3 (#2493)
