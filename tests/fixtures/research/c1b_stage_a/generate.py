"""Regenerate synthetic stage-A expectations with the frozen C1b probe.

Run from the repo: uv run python tests/fixtures/research/c1b_stage_a/generate.py
"""

from __future__ import annotations

import importlib.util
import json
from datetime import date
from pathlib import Path

PROBE = Path.home() / "tradepartner-probes/c1lag/c1b_filing_delay.py"
OUT = Path(__file__).with_name("expected.json")
spec = importlib.util.spec_from_file_location("c1b_probe", PROBE)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
probe.SIZE_BUCKETS = 1
probe.MIN_BUCKET_EVENTS = 2
probe.PEER_WINDOW = 0
probe.MIN_PEERS = 1
rows = []
session_index = {}
for i, month in enumerate(range(1, 13)):
    d = date(2020, month, 15)
    session_index[d] = i * 20
    for j, group in enumerate(("normal", "normal", "normal", "early", "late")):
        row = {
            "event_id": f"event-{i}-{j}",
            "accession": f"event-{i}-{j}",
            "security_id": f"security-{i}-{j}",
            "issuer_key": f"issuer-{i}-{j}",
            "cik": f"issuer-{i}-{j}",
            "form": "10-Q",
            "stratum": "10-Q",
            "quarter": probe.quarter_index(d),
            "qidx": probe.quarter_index(d),
            "group": group,
            "acc_date": d.isoformat(),
            "entry": d.isoformat(),
            "entry_index": i * 20,
            "mcap": 1_000_000 * (1 + j / 10),
            "beta": 1 + j / 20,
        }
        for h in probe.HORIZONS:
            row[f"r_{h}"] = (
                (j - 1) * 0.01
                + i * 0.002
                + h / 10_000
                + (0.004 * ((i * 7) % 5 - 2) if group != "normal" else 0.0)
            )
            row[f"rb_{h}"] = row[f"r_{h}"] - 0.003
            row[f"spy_{h}"] = 0.002 + h / 20_000
        rows.append(row)
probe.size_buckets(rows)
probe.attach_spreads(rows, session_index, 1, 0)
expected = probe.summarise(rows, {"n_events_early": 12, "n_no_label": 2})
OUT.write_text(
    json.dumps(
        {
            "input": [
                {
                    k: v
                    for k, v in row.items()
                    if k not in {"bucket", "no_peers", "beta_gap", "logmcap_gap"}
                    and not k.startswith("spread_")
                    and not k.startswith("peer_n_")
                }
                for row in rows
            ],
            "expected_rows": rows,
            "expected": expected,
        },
        indent=2,
        sort_keys=True,
    )
    + "\n"
)
