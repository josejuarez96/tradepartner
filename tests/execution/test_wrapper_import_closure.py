"""The order wrapper's import closure stays clear of the ingest module (#3595)."""

from __future__ import annotations

import subprocess
import sys


def test_wrapper_does_not_import_ingest() -> None:
    code = "import sys, tradepartner.execution.wrapper; print('tradepartner.ingest' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_status_values_are_unchanged() -> None:
    from tradepartner import ingest, ingest_status

    assert ingest.OK == ingest_status.OK == "ok"
    assert ingest.MODE == ingest_status.MODE == "session"
    assert ingest.PRICE_SOURCE == ingest_status.PRICE_SOURCE == "alpaca"
    assert (ingest.STALE, ingest.FAILED, ingest.LOCKED) == ("stale", "failed", "locked")
    assert (ingest_status.STALE, ingest_status.FAILED, ingest_status.LOCKED) == (
        "stale",
        "failed",
        "locked",
    )
