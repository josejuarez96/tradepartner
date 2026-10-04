- CI runs pytest with xdist (-n auto) and caches the uv env keyed on uv.lock, saved only by main (#581, #582)
### Changed
- CI: the `checks` job runs `pytest -n auto` (pytest-xdist) and restores a uv cache keyed on uv.lock that only main saves; `ready_pr.py` full-suite runs use xdist too (#581, #582)
