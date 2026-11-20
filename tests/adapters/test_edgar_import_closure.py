"""#3644: the EDGAR adapter's import closure stays below the orchestrators.

The filing-date clock rule lives in the leaf `adapters.edgar_clock`, so
importing `adapters.edgar_source` loads none of `health`, `ingest` or
`backfill`; `health` re-exports the same objects for its own callers.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import time

_PROBE = (
    "import sys, tradepartner.adapters.edgar_source; "
    "print(sorted(m for m in ('tradepartner.health', 'tradepartner.ingest', "
    "'tradepartner.backfill') if m in sys.modules))"
)


def test_edgar_source_does_not_import_health_or_ingest() -> None:
    """A fresh interpreter that imports only the adapter has loaded none of
    the orchestrator-level modules."""
    done = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "[]"


def test_health_reexports_the_clock_rule() -> None:
    """One definition: `health` hands out the leaf module's own objects."""
    from tradepartner import health
    from tradepartner.adapters import edgar_clock

    assert health.clock_suspect is edgar_clock.clock_suspect
    cutoff = health.EDGAR_FILING_DATE_CUTOFF
    assert cutoff is edgar_clock.EDGAR_FILING_DATE_CUTOFF
    assert cutoff == time(17, 30)
