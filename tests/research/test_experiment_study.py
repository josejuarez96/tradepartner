"""T300 study table parser contract."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from tradepartner.config import Settings
from tradepartner.research.experiment import ExperimentFileError, parse_experiment_file

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "experiments" / "c1b-filing-delay-matched.md"
)


def _parse(tmp_path: Path, content: str):  # type: ignore[no-untyped-def]
    experiments = tmp_path / "experiments"
    experiments.mkdir(parents=True)
    research = tmp_path / "research"
    research.mkdir()
    (research / "claims.toml").write_text(
        '[[claim]]\nid = "QI-2"\ngrade = "SUPPORTED"\n[[claim]]\nid = "LS-6"\ngrade = "SUPPORTED"\n'
    )
    path = experiments / FIXTURE.name
    path.write_text(content)
    return parse_experiment_file(path, experiments, settings=Settings(_env_file=None))


def test_c1b_study_parses_and_is_hashed(tmp_path: Path) -> None:
    source = FIXTURE.read_text()
    result = _parse(tmp_path, source)
    params = json.loads(result.params_json)
    assert params["study"]["entry"] == "next_session_after_date"
    assert len(params["study"]["kill"]) == 2
    assert result.params_sha256 == sha256(result.params_json.encode()).hexdigest()
    assert result.params_json == _parse(tmp_path / "again", source).params_json
    changed = _parse(
        tmp_path / "changed",
        source.replace('entry = "next_session_after_date"', 'entry = "next_open_after_instant"'),
    )
    assert changed.params_sha256 != result.params_sha256
    assert result.study is not None
    assert result.study["configurations"] == 2
    assert result.study["matching"] == "ex_post"


def test_matching_is_optional_causal_default_and_explicit_ex_post_is_hashed(
    tmp_path: Path,
) -> None:
    source = FIXTURE.read_text()
    causal = _parse(tmp_path / "causal", source.replace('matching = "ex_post"\n', ""))
    assert causal.study is not None
    assert "matching" not in causal.study
    ex_post = _parse(tmp_path / "expost", source)
    assert ex_post.params_sha256 != causal.params_sha256
    with pytest.raises(ExperimentFileError, match=r"study\.matching"):
        _parse(tmp_path / "invalid", source.replace('matching = "ex_post"', 'matching = "other"'))


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("z = 1.645\n", "", "study.z"),
        ("z = 1.645", 'z = "1.645"', "study.z"),
        ("z = 1.645", "z = 1.96", "study.z"),
        ("z = 1.645", "z = 1.645\nmagic = 1", "unknown keys"),
        ('entry = "next_session_after_date"', 'entry = "today"', "study.entry"),
        (
            'readings = ["raw", "beta_adjusted", "size_tight", "size_tight_beta_adjusted"]',
            'readings = ["unknown"]',
            "study.kill",
        ),
        ("configurations = 2\nhorizons", "configurations = 1\nhorizons", "study.configurations"),
        ("configurations = 2\nhorizons", "configurations = 3\nhorizons", "study.configurations"),
        ('kind = "return"', 'kind = "economic"', "study requires kind"),
        ('cluster_by = ["month", "quarter"]\n', "", "study.cluster_by"),
        ("cost_bps = [15.0, 30.0]\n", "", "study.cost_bps"),
        ("borrow_bps = [0.0, 30.0, 300.0]\n", "", "study.borrow_bps"),
        ('side = "long"', 'side = "sideways"', "study.kill.side"),
    ],
)
def test_study_refusals(tmp_path: Path, old: str, new: str, match: str) -> None:
    source = FIXTURE.read_text()
    assert old in source
    with pytest.raises(ExperimentFileError, match=match):
        _parse(tmp_path, source.replace(old, new, 1))
