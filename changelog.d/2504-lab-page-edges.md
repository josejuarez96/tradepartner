- Lab page: a quick run now refreshes the page, the next step never suggests promote when the retire rule is met, and a sweep whose variants all failed says so (#2504) (#2509)
### Fixed
- The Lab page refreshes when a run finishes before a poll saw it running, suggests `sweep retire` instead of `sweep promote` when the retire rule is met, and says "no variant completed successfully" for a finished sweep with no winner; the `UiConfig` docstring now describes the three new Lab keys.
