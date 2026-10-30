"""C1b run 3 golden, summary stage (amendment 2026-10-28 #2440, plan T304).

From the run 3 artifact's per-event rows, with earlier-stage counts retained,
the runner's matching and summary stages (the functions `run_study`
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
from tradepartner.research.experiment import hash_file, parse_experiment_file
from tradepartner.store import research, schema
from tradepartner.store.db import open_for_write

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
C1B_FILE = FIXTURES / "experiments" / "c1b-filing-delay-matched.md"
STAGE_A = FIXTURES / "research" / "c1b_stage_a" / "expected.json"
SLUG = "c1b-filing-delay-matched"
#: Run 3's registered artifact and its SHA-256 (the probe's run 3 output).
ARTIFACT = Path(Settings(_env_file=None).research.data_dir) / SLUG / f"{SLUG}-run3.json"
# Run 3's registered artifact digest (docs/specs/research-registry.md, req. 8; C1b run 3).
ARTIFACT_SHA256 = "a30adf20f70557482b13d6fbe807d678fb68697180a09b64a079dd10637d2793"
#: Keys the probe derived per row; the runner recomputes them.
DERIVED = ("bucket", "no_peers", "beta_gap", "logmcap_gap")
#: The one accepted departure from the probe (owner decision 2026-10-29 on PR
#: #2609): the probe counted a row as traded only when it had a spread, and
#: peer-group rows never have one, so it recorded 0 (run 3). The runner counts
#: every bucketed peer-group row as traded; the golden asserts that count.
ACCEPTED_DEPARTURE = "n_traded_normal"
#: The second accepted departure (owner decision 2026-10-30 on #2618): the probe
#: counted a delisted exit only when `exited_early_60` was set (`c1b_filing_delay.py`
#: `evaluate`), and the runner (`run_study`) counts an event early at any horizon, as
#: the spec's entry-and-exit rule reads ("delistings and halts alike"). Run 3 records
#: 382; the runner gives 383, the extra
#: event being accession 0001000694-22-000009 (10-Q, entry 2022-05-10, no bar at
#: its 20-session exit 2022-06-07, a bar at its 60-session exit 2022-08-04).
DELISTED_DEPARTURE = "n_delisted_exits"


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
    return (
        processed,
        derived,
        summarise_study(
            processed,
            {**counts, **{key: value for key, value in derived.items() if key in counts}},
            design,
        ),
    )


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


def _assert_derived(
    derived: Mapping[str, int],
    recorded: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    design: Mapping[str, Any],
) -> None:
    """The counts the matching stage derives equal the recorded ones it names,
    except the accepted departure, which is the bucketed peer-group row count."""
    peer_group = str(design["peer_group"])
    assert f"n_traded_{peer_group}" == ACCEPTED_DEPARTURE
    assert derived[ACCEPTED_DEPARTURE] == sum(
        1 for row in rows if row["group"] == peer_group and row["bucket"] is not None
    )
    for key, value in derived.items():
        if key in recorded and key != ACCEPTED_DEPARTURE:
            assert value == recorded[key], key


def _counts(
    rows: Sequence[Mapping[str, Any]], secondary: Mapping[str, Any], design: Mapping[str, Any]
) -> dict[str, Any]:
    """Derive the counts the traded rows carry; pass through the recorded ones that
    need the unlabelled and dropped events (`n_events_total`, `n_no_label`,
    `n_dropped_*` before matching, `n_securities`), as the spec's stage A says."""
    counts = {
        key: value
        for key, value in secondary.items()
        if key.startswith("n_")
        and (
            (
                key.startswith("n_dropped_")
                and not key.startswith(("n_dropped_no_peers_", "n_dropped_small_size_group_"))
            )
            or key in {"n_events_total", "n_no_label", "n_securities"}
        )
    }
    counts.update(
        {
            key: 0
            for key in secondary
            if key.startswith(("n_traded_", "n_dropped_no_peers_", "n_dropped_small_size_group_"))
        }
    )
    for group in {str(row["group"]) for row in rows}:
        if f"n_events_{group}" in secondary:
            counts[f"n_events_{group}"] = sum(row["group"] == group for row in rows) + sum(
                value
                for key, value in counts.items()
                if key.startswith("n_dropped_") and key.endswith(f"_{group}")
            )
    if DELISTED_DEPARTURE in secondary:
        # `run_study`'s rule: one per event row early at any registered horizon.
        counts[DELISTED_DEPARTURE] = sum(
            any(row[f"exited_early_{int(h)}"] for h in design["horizons"]) for row in rows
        )
    return counts


def _assert_stored_secondary(
    tmp_path: Path, summary: Mapping[str, Any], artifact_path: Path
) -> None:
    """The production result writer persists this summary on a temp store."""
    settings = Settings(_env_file=None).model_copy(
        update={
            "store": Settings(_env_file=None).store.model_copy(
                update={"path": str(tmp_path / "golden.duckdb")}
            )
        }
    )
    parsed = parse_experiment_file(
        tmp_path / "experiments" / C1B_FILE.name,
        tmp_path / "experiments",
        settings=settings,
    )
    export = tmp_path / "synthetic-export.json"
    export.write_bytes(STAGE_A.read_bytes())
    with open_for_write(settings) as conn:
        schema.init_schema(conn)
        research.register_experiment(conn, parsed, "test")
        dataset = research.register_dataset(
            conn,
            name=parsed.dataset_name,
            version="synthetic",
            path=str(export),
            sha256=hash_file(export),
            event_start=parsed.window_start,
            event_end=parsed.window_end,
        )
        handle = research.open_run(
            conn, SLUG, dataset.dataset_id, "full", {}, "test", configurations=2, settings=settings
        )
    assert handle.refusal is None
    primary = summary["primary"]
    with open_for_write(settings) as conn:
        assert (
            research.write_result(
                conn,
                handle,
                primary_value=float(primary["value"]),
                primary_ci_low=float(primary["ci_low"]),
                primary_ci_high=float(primary["ci_high"]),
                n_observations=int(primary["n"]),
                n_clusters=int(primary["n_clusters"]),
                n_configurations=2,
                artifact_sha256=hash_file(artifact_path),
                artifact_path=str(artifact_path),
                secondary=summary["secondary"],
            )
            == "ok"
        )
        row = conn.execute(
            "SELECT secondary_json FROM research_results WHERE run_id = ?", [handle.run_id]
        ).fetchone()
    assert row is not None
    stored = json.loads(row[0])
    expected = {
        key: None if isinstance(value, float) and not math.isfinite(value) else value
        for key, value in summary["secondary"].items()
    }
    _assert_same(stored, expected)


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
    rows, derived, summary = _reproduce(
        case["input"], _counts(case["input"], expected["secondary"], design), design
    )
    for row, probe_row in zip(rows, case["expected_rows"], strict=True):
        assert row["bucket"] == probe_row["bucket"]
    _assert_derived(derived, expected["secondary"], rows, design)
    _assert_same(summary["secondary"], expected["secondary"])
    _assert_stored_secondary(tmp_path, summary, STAGE_A)


def test_synthetic_kill_is_decided_by_t_not_bp_floor(tmp_path: Path) -> None:
    design = _design(tmp_path)
    design["kill"] = [{**design["kill"][0], "readings": ["raw"], "floor": 1.0}]
    values = (0.02, -0.01, 0.03, -0.005)
    processed = [
        {
            "group": "early",
            "security_id": f"synthetic-{i}",
            "bucket": 0,
            "acc_date": f"2020-0{i + 1}-15",
            "qidx": i // 3,
            "entry_index": i * 100,
            "beta_gap": 0.0,
            "logmcap_gap": 0.0,
            **{f"spread_{h}": value for h in design["horizons"]},
            **{f"r_{h}": value for h in design["horizons"]},
            **{f"spy_{h}": 0.0 for h in design["horizons"]},
            **{
                f"spread_{variant}_{h}": value
                for variant in ("badj", "sz", "szb")
                for h in design["horizons"]
            },
        }
        for i, value in enumerate(values)
    ]
    summary = summarise_study(processed, {}, design)
    reading = summary["kill"]["long"]["readings"]["raw"]
    assert reading["gross_spread_bp"] > design["kill"][0]["floor"]
    assert 0 < reading["t_min"] < design["kill"][0]["t_floor"]
    assert summary["secondary"]["kill_long"] == 1.0


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
    design = _design(tmp_path)
    input_rows = _runner_rows(artifact["events"])
    rows, derived, summary = _reproduce(input_rows, _counts(input_rows, expected, design), design)
    for row, probe_row in zip(rows, artifact["events"], strict=True):
        assert row["bucket"] == probe_row["bucket"], row["event_id"]
    _assert_derived(derived, expected, rows, design)
    # The accepted delisted-exit departure is exactly the one known event.
    assert (expected[DELISTED_DEPARTURE], summary["secondary"][DELISTED_DEPARTURE]) == (382, 383)
    _assert_same(
        summary["secondary"],
        {
            **expected,
            ACCEPTED_DEPARTURE: derived[ACCEPTED_DEPARTURE],
            DELISTED_DEPARTURE: summary["secondary"][DELISTED_DEPARTURE],
        },
    )
    _assert_stored_secondary(tmp_path, summary, ARTIFACT)
    primary = artifact["summary"]["primary"]
    assert summary["primary"]["value"] == pytest.approx(primary["value"], rel=1e-9)
    assert (summary["primary"]["n"], summary["primary"]["n_clusters"]) == (
        primary["n"],
        primary["n_clusters"],
    )
