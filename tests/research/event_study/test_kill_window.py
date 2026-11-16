"""The per-line kill window `[[study.kill]].entry_start` (#3503)."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tradepartner.config import Settings
from tradepartner.research.event_study.run import summarise_study
from tradepartner.research.experiment import ExperimentFileError, parse_experiment_file

C1B = (
    Path(__file__).resolve().parents[2] / "fixtures" / "experiments" / "c1b-filing-delay-matched.md"
)


def _parse(tmp_path: Path, text: str) -> Any:
    experiments = tmp_path / "experiments"
    experiments.mkdir(parents=True)
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "claims.toml").write_text(
        '[[claim]]\nid = "QI-2"\ngrade = "SUPPORTED"\n[[claim]]\nid = "LS-6"\ngrade = "SUPPORTED"\n'
    )
    (experiments / C1B.name).write_text(text)
    return parse_experiment_file(
        experiments / C1B.name, experiments, settings=Settings(_env_file=None)
    )


def _with_start(value: str) -> str:
    source = C1B.read_text()
    marker = 'side = "long"'
    assert source.count(marker) == 1
    return source.replace(marker, f"{marker}\nentry_start = {value}")


def test_entry_start_is_optional_and_hashed(tmp_path: Path) -> None:
    plain = _parse(tmp_path / "plain", C1B.read_text())
    windowed = _parse(tmp_path / "windowed", _with_start("2020-01-02"))
    assert all("entry_start" not in line for line in json.loads(plain.params_json)["study"]["kill"])
    assert windowed.params_sha256 != plain.params_sha256
    assert date(2020, 1, 2) in [line.get("entry_start") for line in windowed.study["kill"]]


@pytest.mark.parametrize("value", ['"2020-01-02"', "2020-01-02T00:00:00Z", "20200102"])
def test_entry_start_must_be_a_date(tmp_path: Path, value: str) -> None:
    with pytest.raises(ExperimentFileError, match="must be a TOML date"):
        _parse(tmp_path, _with_start(value))


def test_unknown_kill_key_still_refused(tmp_path: Path) -> None:
    text = C1B.read_text().replace('side = "long"', 'side = "long"\nentry_end = 2021-01-04')
    with pytest.raises(ExperimentFileError, match=r"study\.kill keys"):
        _parse(tmp_path, text)


def test_windowed_line_judges_only_later_entries(tmp_path: Path) -> None:
    design = dict(_parse(tmp_path, C1B.read_text()).study)
    full = {**design["kill"][0], "readings": ["raw"], "floor": 0.0, "t_floor": -1e9}
    windowed = {**full, "name": "long2020", "entry_start": "2020-01-02"}
    design["kill"] = [windowed, full]  # the windowed line first: it must not be `kill_long`
    design["configurations"] = 2
    values = {"2019-06-03": -0.04, "2019-09-03": -0.06, "2020-03-02": 0.03, "2020-06-01": 0.05}
    processed = [
        {"group": full["group"], "security_id": f"s{i}", "bucket": 0, "acc_date": day,
         "entry": day, "qidx": i, "entry_index": i * 100, "beta_gap": 0.0, "logmcap_gap": 0.0,
         **{f"{k}_{h}": v for k in ("spread", "r", "spread_badj", "spread_sz", "spread_szb")
            for h in design["horizons"]},
         **{f"spy_{h}": 0.0 for h in design["horizons"]}}
        for i, (day, v) in enumerate(values.items())
    ]  # fmt: skip
    summary = summarise_study(processed, {}, design)
    assert summary["kill"]["long2020"]["readings"]["raw"]["gross_spread_bp"] == pytest.approx(400)
    assert summary["kill"][full["name"]]["readings"]["raw"]["gross_spread_bp"] == pytest.approx(-50)
    assert summary["secondary"]["kill_long2020"] == 0.0
    assert summary["secondary"]["kill_long"] == 1.0
