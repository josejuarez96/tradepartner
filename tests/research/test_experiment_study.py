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


def test_delisting_return_is_optional_and_absent_is_not_written_back(tmp_path: Path) -> None:
    """T413 (#3498): absent hashes as before; `none` is a different file; values are enumerated."""
    source = FIXTURE.read_text()
    assert "delisting_return" not in source
    absent = _parse(tmp_path / "absent", source)
    assert absent.study is not None
    assert "delisting_return" not in absent.study
    # Pinned from main before this change: the key must not move a registered hash.
    assert (
        absent.params_sha256 == "41e65b1b61b89655aba8c430bd7e2f8a126af95176ed22e6bc802e7330318be5"
    )
    assert "delisting_return" not in json.loads(absent.params_json)["study"]
    anchor = 'matching = "ex_post"\n'
    hashes = {}
    for value in ("none", "failure_v1", "severe_v1"):
        parsed = _parse(
            tmp_path / value, source.replace(anchor, anchor + f'delisting_return = "{value}"\n')
        )
        assert parsed.study is not None
        assert parsed.study["delisting_return"] == value
        hashes[value] = parsed.params_sha256
    assert len({*hashes.values(), absent.params_sha256}) == 4
    for bad in ('"other"', '"FAILURE_V1"', "1", "true"):
        with pytest.raises(ExperimentFileError, match=r"study\.delisting_return"):
            _parse(
                tmp_path / f"bad{abs(hash(bad))}",
                source.replace(anchor, anchor + f"delisting_return = {bad}\n"),
            )


def test_se_method_is_optional_and_absent_is_not_written_back(tmp_path: Path) -> None:
    """#3679: absent hashes as before; an explicit value differs; values are enumerated."""
    source = FIXTURE.read_text()
    assert "se_method" not in source
    absent = _parse(tmp_path / "absent", source)
    assert absent.study is not None
    assert "se_method" not in absent.study
    assert (
        absent.params_sha256 == "41e65b1b61b89655aba8c430bd7e2f8a126af95176ed22e6bc802e7330318be5"
    )
    assert "se_method" not in json.loads(absent.params_json)["study"]
    anchor = 'matching = "ex_post"\n'
    hashes = {}
    for value in ("hansen_hodrick", "quarter_cluster_t"):
        parsed = _parse(
            tmp_path / value, source.replace(anchor, anchor + f'se_method = "{value}"\n')
        )
        assert parsed.study is not None
        assert parsed.study["se_method"] == value
        hashes[value] = parsed.params_sha256
    assert len({*hashes.values(), absent.params_sha256}) == 3
    for bad in ('"bartlett"', '"QUARTER_CLUSTER_T"', "1", "true"):
        with pytest.raises(ExperimentFileError, match=r"study\.se_method"):
            _parse(
                tmp_path / f"bad{abs(hash(bad))}",
                source.replace(anchor, anchor + f"se_method = {bad}\n"),
            )


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


@pytest.mark.parametrize(
    ("key", "bad_value", "match"),
    [
        ("min_volume_bars", "21", r"study\.min_volume_bars must not exceed study\.volume_lookback"),
        ("beta_min_bars", "121", r"study\.beta_min_bars must not exceed study\.beta_lookback"),
    ],
)
def test_study_min_bars_exceeds_lookback_refused(
    tmp_path: Path, key: str, bad_value: str, match: str
) -> None:
    source = FIXTURE.read_text()
    old_line = f"{key} = 10" if key == "min_volume_bars" else f"{key} = 60"
    new_line = f"{key} = {bad_value}"
    assert old_line in source
    with pytest.raises(ExperimentFileError, match=match):
        _parse(tmp_path, source.replace(old_line, new_line, 1))


_ONE_SIDED = "each bound one-sided 95% (mean -/+ 1.645 SE)"
_TWO_SIDED = "each bound two-sided 95% (mean -/+ 1.96 SE)"


def test_two_sided_study_registers_with_two_sided_z(tmp_path: Path) -> None:
    source = FIXTURE.read_text()
    assert _ONE_SIDED in source and "z = 1.645" in source
    changed = source.replace(_ONE_SIDED, _TWO_SIDED, 1).replace("z = 1.645", "z = 1.96", 1)
    assert json.loads(_parse(tmp_path, changed).params_json)["study"]["z"] == 1.96


@pytest.mark.parametrize(
    ("inference", "z", "match"),
    [
        (_ONE_SIDED, "1.96", r"study\.z must match"),
        (_TWO_SIDED, "1.645", r"study\.z must match"),
        ("each bound 95% (mean -/+ 1.645 SE)", "1.645", r"one-sided or two-sided"),
        (
            "each bound one-sided and two-sided 95% (mean -/+ 1.645 SE)",
            "1.645",
            r"one-sided or two-sided",
        ),
    ],
)
def test_study_z_must_match_declared_interval_side(
    tmp_path: Path, inference: str, z: str, match: str
) -> None:
    source = (
        FIXTURE.read_text().replace(_ONE_SIDED, inference, 1).replace("z = 1.645", f"z = {z}", 1)
    )
    with pytest.raises(ExperimentFileError, match=match):
        _parse(tmp_path, source)


def test_quarter_cluster_t_refuses_a_window_with_too_few_quarters(tmp_path: Path) -> None:
    """#3679: knowable from the frozen dates, so refused at registration, before any read."""
    source = FIXTURE.read_text().replace(
        'matching = "ex_post"\n', 'matching = "ex_post"\nse_method = "quarter_cluster_t"\n'
    )
    old_end = "end = 2023-12-29"
    assert old_end in source
    seven = source.replace(old_end, "end = 2016-12-30")  # 2015Q2 .. 2016Q4
    with pytest.raises(ExperimentFileError, match=r"at least 8 entry quarters.*spans 7"):
        _parse(tmp_path / "seven", seven)
    eight = source.replace(old_end, "end = 2017-03-31")  # 2015Q2 .. 2017Q1
    assert _parse(tmp_path / "eight", eight).study is not None
    sub = eight.replace('name = "long"\n', 'name = "long"\nentry_start = 2016-07-01\n')
    with pytest.raises(ExperimentFileError, match=r"entry_start 2016-07-01.*spans 3"):
        _parse(tmp_path / "sub", sub)
    # the default statistic has no such rule: C1b-style short windows still register
    flat = FIXTURE.read_text().replace(old_end, "end = 2016-12-30")
    assert _parse(tmp_path / "flat", flat).study is not None
