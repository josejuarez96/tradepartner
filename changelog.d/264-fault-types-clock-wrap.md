### Added
- `tradepartner.errors`: the shared fault types (`SystemFaultError` and subclasses, `StaleDataError`); `FakeBroker` raises `ClockError` instead of `ValueError` on a bad clock (ADR 0007 Task A, #264).
