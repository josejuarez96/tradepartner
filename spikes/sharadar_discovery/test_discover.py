# ruff: noqa: E501
"""Tests for the spike, on synthetic data only. Run: uv run pytest spikes/sharadar_discovery/test_discover.py -q"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import discover
import make_synthetic

warnings.filterwarnings("ignore", category=RuntimeWarning)


def run(tmp: Path, name: str, **kw) -> Path:
    data, work = tmp / name, tmp / (name + "_work")
    make_synthetic.make(data, n_primary=280, n_secondary=0, **kw)
    discover.main(["--data", str(data), "--work", str(work), "explore"])
    discover.main(["--data", str(data), "--work", str(work), "confirm"])
    return work


@pytest.fixture(scope="module")
def planted(tmp_path_factory) -> Path:
    return run(tmp_path_factory.mktemp("syn"), "planted", effect=0.012)


def test_a_planted_effect_is_found_and_confirmed(planted: Path) -> None:
    conf = json.loads((planted / "confirm.json").read_text())
    assert conf["passed"] is True
    assert conf["primary"]["net50"][0] > 0.5 and conf["primary"]["net50"][1] > 2.0
    assert "gpa" in json.loads((planted / "frozen.json").read_text())["features"]


def test_no_effect_does_not_pass(tmp_path: Path) -> None:
    conf = json.loads((run(tmp_path, "null", effect=0.0, seed=1) / "confirm.json").read_text())
    assert conf["passed"] is False
    assert abs(conf["primary"]["gross"][1]) < 3.0


def test_fundamentals_dated_after_the_month_end_are_never_used(tmp_path: Path) -> None:
    """The fundamentals here encode the NEXT month's return but are dated after it. A correct as-of join cannot
    see them in time, so nothing is found. A join that ignored datekey would pass easily."""
    conf = json.loads(
        (run(tmp_path, "leak", effect=0.0, leak=True, seed=2) / "confirm.json").read_text()
    )
    assert conf["passed"] is False


def test_confirm_is_read_once_and_explore_is_then_closed(planted: Path, capsys) -> None:
    before = (planted / "confirm.json").read_text()
    discover.main(["--data", str(planted.parent / "planted"), "--work", str(planted), "confirm"])
    assert "already read once" in capsys.readouterr().out
    assert (planted / "confirm.json").read_text() == before
    with pytest.raises(SystemExit):
        discover.main(
            ["--data", str(planted.parent / "planted"), "--work", str(planted), "explore"]
        )


def test_exam_needs_a_confirm_pass(tmp_path: Path, planted: Path) -> None:
    work = run(tmp_path, "null2", effect=0.0, seed=3)
    with pytest.raises(SystemExit):
        discover.main(["--data", str(tmp_path / "null2"), "--work", str(work), "exam"])
    discover.main(["--data", str(planted.parent / "planted"), "--work", str(planted), "exam"])
    assert json.loads((planted / "exam.json").read_text())["window"] == list(discover.EXAM)


def test_nothing_after_2023_is_loaded_and_windows_do_not_touch(planted: Path) -> None:
    import pandas as pd

    panel = pd.read_parquet(planted / "panel.parquet")
    assert panel.ym.max() <= "2023-12"
    assert discover.EXPLORE[1] < discover.previous_month(discover.CONFIRM[0], 1)
    assert discover.CONFIRM[1] < discover.EXAM[0]


def test_a_thin_universe_is_not_recorded_as_a_read(tmp_path: Path) -> None:
    data, work = tmp_path / "thin", tmp_path / "thin_work"
    make_synthetic.make(data, n_primary=170, n_secondary=0, effect=0.012, seed=4)
    discover.main(["--data", str(data), "--work", str(work), "explore"])
    with pytest.raises(SystemExit):
        discover.main(["--data", str(data), "--work", str(work), "confirm"])
    assert not (work / "confirm.json").exists()
