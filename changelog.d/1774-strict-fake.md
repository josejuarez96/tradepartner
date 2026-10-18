- T201 strict FakeBroker refusals and explicit test asset catalogs built; fault drills follow in a second PR.
### Added
- FakeBroker returns unresolved asset reads, mirrors recorded 403 sell refusals, and reserves shares for open sells; fixtures declare broker assets explicitly. The exact submit-time untradable body remains an open question.
