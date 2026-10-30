"""C1b run 3 golden, summary stage (amendment 2026-10-28 #2440, plan T304).

From the run 3 artifact's per-event rows, with the run's recorded counts passed
through, the runner's matching and summary stages (the functions `run_study`
itself calls, so the secondary is what `experiment study` records, kill lines
included) must give the registered `secondary_json` key for key: counts and
booleans exact, NaN equal to NaN, floats within a relative 1e-9. The artifact is
real price-derived data kept outside git (`research.data_dir`), so the check
skips with its reason when the artifact is absent (CI, team checkouts).
The same path runs in CI on the committed synthetic stage A fixture, so the code
is exercised either way.
"""

from __future__ import annotations

import json
import math
import shutil
from collections.abc import Mapping, Sequence
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest

from tradepartner.calendar import all_sessions
from tradepartner.config import Settings
from tradepartner.research.event_study.run import match_events, summarise_study
from tradepartner.research.experiment import parse_experiment_file

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
C1B_FILE = FIXTURES / "experiments" / "c1b-filing-delay-matched.md"
STAGE_A = FIXTURES / "research" / "c1b_stage_a" / "expected.json"
SLUG = "c1b-filing-delay-matched"
#: Run 3's registered artifact and its SHA-256 (the probe's run 3 output).
ARTIFACT = Path(Settings(_env_file=None).research.data_dir) / SLUG / f"{SLUG}-run3.json"
ARTIFACT_SHA256 = "a30adf20f70557482b13d6fbe807d678fb68697180a09b64a079dd10637d2793"
#: Keys the probe derived per row; the runner recomputes them.
DERIVED = ("bucket", "no_peers", "beta_gap", "logmcap_gap")


def _design(tmp_path: Path) -> dict[str, Any]:
    """The C1b `[study]` table, parsed by the experiment parser."""
    experiments = tmp_path / "experiments"
    experiments.mkdir(parents=True)
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "claims.toml").write_text(
        '[[claim]]\nid = "QI-2"\ngrade = "SUPPORTED"\n[[claim]]\nid = "LS-6"\ngrade = "SUPPORTED"\n'
    )
    path = experiments / C1B_FILE.name
    shutil.copy(C1B_FILE, path)
    study = parse_experiment_file(path, experiments, settings=Settings(_env_file=None)).study
    assert study is not None
    return study


def _runner_rows(probe_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Probe artifact rows in runner format: accession as event id, form as stratum,
    CIK as issuer, the calendar quarter, and the XNYS index of the entry session."""
    index = {day: i for i, day in enumerate(all_sessions())}
    return [
        {
            **{k: v for k, v in row.items() if k not in DERIVED and not k.startswith("spread_")},
            "event_id": row["accession"],
            "stratum": row["form"],
            "issuer_key": row["cik"],
            "quarter": row["qidx"],
            "entry_index": index[date.fromisoformat(str(row["entry"]))],
        }
        for row in probe_rows
    ]


def _reproduce(
    rows: Sequence[Mapping[str, Any]], counts: Mapping[str, Any], design: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    """The runner's own matching and summary stages (`run.match_events`,
    `run.summarise_study`, as `run_study` calls them) over traded event rows."""
    rows = [{k: v for k, v in row.items() if not k.startswith("peer_n_")} for row in rows]
    processed, derived = match_events(rows, design)
    return processed, derived, summarise_study(processed, counts, design)


def _assert_same(actual: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    assert sorted(actual) == sorted(expected)
    for key, value in expected.items():
        got = actual[key]
        if isinstance(value, float) and math.isnan(value):
            assert isinstance(got, float) and math.isnan(got), key
        elif isinstance(value, float):
            assert got == pytest.approx(value, rel=1e-9), key
        else:
            assert got == value, key


def _assert_derived(derived: Mapping[str, int], recorded: Mapping[str, Any]) -> None:
    """The counts the matching stage derives equal the recorded ones it names."""
    for key, value in derived.items():
        if key in recorded:
            assert value == recorded[key], key


def _counts(secondary: Mapping[str, Any]) -> dict[str, Any]:
    """The recorded counts passed through (they need the dropped and unlabelled
    events, which the traded rows do not carry)."""
    return {key: value for key, value in secondary.items() if key.startswith("n_")}


def test_synthetic_stage_a_through_the_golden_path(tmp_path: Path) -> None:
    """CI: the committed synthetic fixture (probe-generated) through the same path."""
    case = json.loads(STAGE_A.read_text())
    design = {
        **_design(tmp_path),
        "n_buckets": 1,
        "min_bucket_events": 2,
        "peer_window": 0,
        "min_peers": 1,
    }
    expected = case["expected"]
    rows, derived, summary = _reproduce(case["input"], _counts(expected["secondary"]), design)
    for row, probe_row in zip(rows, case["expected_rows"], strict=True):
        assert row["bucket"] == probe_row["bucket"]
    _assert_derived(derived, expected["secondary"])
    _assert_same(summary["secondary"], expected["secondary"])


def test_c1b_run3_secondary_from_the_artifact_event_rows(tmp_path: Path) -> None:
    if not ARTIFACT.is_file():
        reason = (
            f"C1b run 3 artifact absent at {ARTIFACT}; the full-artifact golden runs "
            "where the owner's research data directory holds it"
        )
        print(reason)
        pytest.skip(reason)
    data = ARTIFACT.read_bytes()
    assert sha256(data).hexdigest() == ARTIFACT_SHA256, "not run 3's registered artifact"
    artifact = json.loads(data)
    expected = artifact["summary"]["secondary"]
    rows, derived, summary = _reproduce(
        _runner_rows(artifact["events"]), _counts(expected), _design(tmp_path)
    )
    for row, probe_row in zip(rows, artifact["events"], strict=True):
        assert row["bucket"] == probe_row["bucket"], row["event_id"]
    _assert_derived(derived, expected)
    _assert_same(summary["secondary"], expected)
    primary = artifact["summary"]["primary"]
    assert summary["primary"]["value"] == pytest.approx(primary["value"], rel=1e-9)
    assert (summary["primary"]["n"], summary["primary"]["n_clusters"]) == (
        primary["n"],
        primary["n_clusters"],
    )
