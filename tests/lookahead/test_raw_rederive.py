"""No look-ahead through the raw re-derive (plan T352, #2714 item 4).

A re-derive reads only the hash its row's writer run named. A later run that
fetched a changed body for the same accession is a new run: its body never becomes
the earlier row's input, so no store truncated before that fetch can see a value
only the later body holds, while the corrected row keeps its original `known_at`.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from lookahead.harness import TruncatedStore
from lookahead.rederive_rig import ACC, ACCEPTED, NOW, WRONG_SHARES, XML, Rig
from tradepartner.store.db import open_read_only
from tradepartner.store.raw_rederive import rederive

LATER = datetime(2026, 6, 1, 15, tzinfo=ACCEPTED.tzinfo)
CHANGED = XML.replace(b"10000", b"55555")


def _shares(store: TruncatedStore, t: datetime) -> list[Decimal]:
    rows = store.at(t).execute("SELECT shares FROM sec_form4_transactions ORDER BY line_no")
    return [r[0] for r in rows.fetchall()]


def test_a_later_fetch_is_never_an_earlier_rows_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, monkeypatch)
    rig.form4_run(wrong=WRONG_SHARES)
    rig.form4_run(
        run_id="run-2",
        body=CHANGED,
        store=False,
        outcome="changed_refused",
        fetched_at=LATER,
        at=LATER,
    )
    rig.open_release()

    result = rederive(
        rig.settings,
        rig.icloud,
        mode="backfill",
        accessions=[ACC],
        release="rederive-2714",
        expect=1,
        clock=lambda: NOW,
    )

    assert result.hashes == (hashlib.sha256(XML).hexdigest(),)
    assert result.runs == ("run-1",)
    with open_read_only(rig.settings) as conn:
        store = TruncatedStore(conn, tables=["sec_form4_transactions"])
    try:
        assert _shares(store, ACCEPTED - timedelta(seconds=1)) == []  # known_at kept
        for t in (ACCEPTED, LATER - timedelta(microseconds=1), LATER, NOW):
            assert Decimal("55555") not in _shares(store, t)
        assert _shares(store, ACCEPTED)[0] == Decimal("10000")
    finally:
        store.close()
