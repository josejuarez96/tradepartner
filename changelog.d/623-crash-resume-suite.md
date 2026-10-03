- T63h: crash, resume and alert suite — end-to-end crash/resume/transport-error/fill-lag/chain/cent-rounding/alert-kind tests on the fixture store; 4 alert kinds have no production writer, xfailed with issue #644 (#623)
### Added
- Phase 4 T63h: end-to-end crash, resume and alert tests (tests/execution/test_crash_resume.py) covering the crash/resume mechanics, the transport-error case, the written_off resume half, the fill-lag bound, the chain criterion, cent-rounding tolerance, and the full alert-kind suite (11 real triggers plus 4 `xfail`ed kinds with no production writer yet, #644)
