- sandbox marker is read beside the resolved store path; runbook stop/abandon steps corrected
### Fixed
- Sandbox guard follows a file symlink to the marked copy (and never treats a symlink to the live store as sandbox); runbook stop needs --reason with request, run, close, and abandon refuses open orders and does not flatten (#2500)
