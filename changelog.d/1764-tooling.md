- Tooling: ready_pr defaults to a 45-min CI wait and its own pytest basetemp; team.py start runs uv sync and pre-commit install; PR template cut to four boxes (PR pending)
### Changed
- ready_pr default --timeout-min 45 and a per-checkout .pytest-tmp basetemp for its local pytest; team.py start sets up the env and prints the shell lines; four-box PR template (#1764)
