- FakeBroker.reveal_hidden_fills() delivers already-recorded lagged fills on the next fills(); test_resume and test_crash_resume use it instead of writing _fill_hidden_reads (#742).
### Added
- FakeBroker.reveal_hidden_fills(): a public test hook that makes every already-recorded hidden-lag fill visible on the next fills() call (#742).
