- team.py status skips, and claim refuses, a plan task whose line carries a dated Parked note with no later Un-parked note (T195); PR #3195
### Fixed
- `scripts/team.py`: `status` no longer lists a parked plan task (a dated `Parked YYYY-MM-DD` note with no later `Un-parked` note) as ready, and `claim` refuses it with the note (#3182).
