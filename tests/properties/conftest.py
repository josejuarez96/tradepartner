"""Hypothesis profiles for the property tests (#3655, plan T445).

`ci` is the default everywhere, so CI and `ready_pr.py` run the same examples on every
run: derandomized, a fixed example budget, no deadline and no example database.
`HYPOTHESIS_PROFILE=explore` is for a local search with a larger budget.

The directory is a package (`__init__.py`), like `tests/execution`: without it this file
would be imported as the top-level module `conftest` and hide `tests/conftest.py` from the
21 test files that do `from conftest import ...`.

Hypothesis's `settings` is imported as `hyp_settings`: the repo has a `settings` fixture
(`tests/conftest.py`). A `@given` test takes no function-scoped fixture.
"""

from __future__ import annotations

import os

from hypothesis import HealthCheck
from hypothesis import settings as hyp_settings

hyp_settings.register_profile(
    "ci",
    derandomize=True,
    max_examples=50,
    deadline=None,
    database=None,
    suppress_health_check=[HealthCheck.too_slow],
)
hyp_settings.register_profile("explore", max_examples=2000, deadline=None)
hyp_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
