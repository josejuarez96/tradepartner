"""Tests for tradepartner.research.experiment (research-registry spec req 2 and req
11's `dataset register` helpers; plan T82). Pure parser only: every refusal here is
file-level, no store or connection involved."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from tradepartner.config import Settings
from tradepartner.research.experiment import (
    ExperimentFileError,
    canonical_experiment_json,
    check_declared_event_span,
    check_sealed_split_has_period,
    effective_sealed_splits,
    event_span,
    experiment_sha256,
    hash_directory,
    hash_export,
    hash_file,
    load_split_assignment,
    parse_experiment_file,
    read_event_column,
    split_event_spans,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "experiments"


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


# --------------------------------------------------------------------------------
# The two spec Data / interfaces examples register cleanly against the fixture
# claims file (plan T82 acceptance; development-process "A fixture docs/experiments/
# directory... registers cleanly against the fixture claims file").
# --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("slug", "splits", "confirmatory_basis_family"),
    [
        ("e1h-demand-deterioration-revenue", ("pilot",), None),
        ("pilot-agreement-d1-d3", ("pilot",), None),
    ],
)
def test_spec_examples_register_cleanly(
    settings: Settings, slug: str, splits: tuple[str, ...], confirmatory_basis_family: None
) -> None:
    parsed = parse_experiment_file(FIXTURES / f"{slug}.md", FIXTURES, settings=settings)
    assert parsed.slug == slug
    assert parsed.splits == splits
    assert parsed.confirmatory is True
    assert parsed.claims  # non-empty, every id graded in the fixture claims.toml

    # quant-auditor, PR #935 SHOULD FIX 2: both examples write `splits = [...]`
    # directly after `[window]`'s keys, which parses as `window.splits` under TOML's
    # table-scoping rules. The stored params_json must still carry a top-level
    # `splits` (what req 2 and every later reader expect), not bury it under
    # `window`, and the hash must match whichever table the file put it under.
    stored = json.loads(parsed.params_json)
    assert stored["splits"] == list(splits)
    assert "splits" not in stored["window"]


# --------------------------------------------------------------------------------
# Every file-level req 2 refusal, one fixture each.
# --------------------------------------------------------------------------------

REFUSALS: dict[str, str] = {
    "refuse-slug-mismatch": "must equal the file name",
    "refuse-missing-key": "required keys missing",
    "refuse-unknown-key": "unknown keys",
    "refuse-bad-kind": "kind must be one of",
    "refuse-bad-stage": "stage must be one of",
    "refuse-bad-provenance": "provenance must be one of",
    "refuse-bad-split": "splits must be one of",
    "refuse-bad-direction": "primary.direction must be one of",
    "refuse-bad-method": "multiplicity.method must be one of",
    "refuse-return-not-touches-returns": 'kind = "return" requires touches_returns',
    "refuse-touches-returns-no-family": "touches_returns = true requires a family",
    "refuse-family-unknown": "is not in hypotheses.families",
    "refuse-claim-missing": "claim ids absent from",
    "refuse-claim-ungraded-confirmatory": "UNGRADED claims cannot support",
    "refuse-test-split-exploratory": 'splits may name "test" only when confirmatory',
    "refuse-model-historical-confirmatory": 'provenance = "model_historical"',
    "refuse-budget-runs-zero": "budget.runs must be >= 1",
    "refuse-min-clusters-zero": "primary.min_clusters must be >= 1",
}


@pytest.mark.parametrize(("slug", "message"), sorted(REFUSALS.items()))
def test_file_level_refusal(settings: Settings, slug: str, message: str) -> None:
    with pytest.raises(ExperimentFileError, match=message):
        parse_experiment_file(FIXTURES / f"{slug}.md", FIXTURES, settings=settings)


def test_refuses_file_outside_experiments_dir(settings: Settings) -> None:
    outside = FIXTURES.parent / "refuse-outside-dir.md"
    with pytest.raises(ExperimentFileError, match="must be directly inside"):
        parse_experiment_file(outside, FIXTURES, settings=settings)


def test_every_refusal_fixture_is_exercised() -> None:
    """Catches a fixture added without a matching case above, or vice versa."""
    on_disk = {p.stem for p in FIXTURES.glob("refuse-*.md")}
    assert on_disk == set(REFUSALS)


# --------------------------------------------------------------------------------
# Hash stability and canonical JSON.
# --------------------------------------------------------------------------------


def test_doc_and_params_hashes_are_stable_across_reads(settings: Settings) -> None:
    path = FIXTURES / "e1h-demand-deterioration-revenue.md"
    first = parse_experiment_file(path, FIXTURES, settings=settings)
    second = parse_experiment_file(path, FIXTURES, settings=settings)
    assert first.doc_sha256 == second.doc_sha256
    assert first.params_sha256 == second.params_sha256
    assert first.params_json == second.params_json


def test_a_changed_file_is_a_new_hash(tmp_path: Path, settings: Settings) -> None:
    experiments_dir = tmp_path / "experiments"
    experiments_dir.mkdir()
    research_dir = tmp_path / "research"
    research_dir.mkdir()
    (research_dir / "claims.toml").write_text(
        '[[claim]]\nid = "FX-1"\ngrade = "SUPPORTED"\n', encoding="utf-8"
    )
    original = (FIXTURES / "e1h-demand-deterioration-revenue.md").read_text(encoding="utf-8")
    path = experiments_dir / "e1h-demand-deterioration-revenue.md"
    path.write_text(
        original.replace("ER-4", "FX-1").replace('["FX-1", "ER-5", "INT-4"]', '["FX-1"]'),
        encoding="utf-8",
    )
    before = parse_experiment_file(path, experiments_dir, settings=settings)
    path.write_text(
        path.read_text(encoding="utf-8") + "\n<!-- a prose edit -->\n", encoding="utf-8"
    )
    after = parse_experiment_file(path, experiments_dir, settings=settings)
    assert before.doc_sha256 != after.doc_sha256
    # The TOML block itself is unchanged, so the canonical params hash is unchanged;
    # only the whole-file doc hash moved (req 2: prose is hashed too, in doc_sha256).
    assert before.params_sha256 == after.params_sha256


def test_canonical_json_is_key_sorted_compact_and_date_safe() -> None:
    block = {"b": 1, "a": {"end": date(2024, 1, 1), "start": date(2023, 1, 1)}}
    text = canonical_experiment_json(block)
    assert text == json.dumps(
        {"a": {"end": "2024-01-01", "start": "2023-01-01"}, "b": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    assert experiment_sha256(block) == experiment_sha256(
        {"a": {"start": date(2023, 1, 1), "end": date(2024, 1, 1)}, "b": 1}
    )


def _fixture_experiment_tree(tmp_path: Path, name: str, text: str) -> tuple[Path, Path]:
    """A fresh `<experiments_dir>, <path>` pair under `tmp_path/name`, with the
    fixture claims.toml as its sibling `research/claims.toml` and `text` written at
    `<slug from text>.md`. Each test variant gets its own tree so two files that
    must carry the *same* slug (one TOML text difference each) don't collide."""
    root = tmp_path / name
    experiments_dir = root / "experiments"
    experiments_dir.mkdir(parents=True)
    research_dir = root / "research"
    research_dir.mkdir()
    (research_dir / "claims.toml").write_text(
        '[[claim]]\nid = "FX-1"\ngrade = "SUPPORTED"\n', encoding="utf-8"
    )
    path = experiments_dir / "e1h-demand-deterioration-revenue.md"
    path.write_text(text, encoding="utf-8")
    return experiments_dir, path


def test_splits_hashes_the_same_whichever_table_the_file_puts_it_under(
    tmp_path: Path, settings: Settings
) -> None:
    """quant-auditor, PR #935 SHOULD FIX 2: one registration has one hash, whether
    the author wrote `splits` before `[dataset]` (top level, as req 2 specifies) or
    after `[window]`'s keys (as the spec's own two worked examples do, which a bare
    TOML table-scoping reading would nest under `window`)."""
    original = (FIXTURES / "e1h-demand-deterioration-revenue.md").read_text(encoding="utf-8")
    original = original.replace('["ER-4", "ER-5", "INT-4"]', '["FX-1"]')
    top_level = original.replace('\nsplits = ["pilot"]\n', "\n", 1).replace(
        "seed = 20261003\n", 'seed = 20261003\nsplits = ["pilot"]\n'
    )
    assert "splits" not in top_level.split("[dataset]")[1].split("[primary]")[0]

    window_dir, window_path = _fixture_experiment_tree(tmp_path, "window", original)
    top_dir, top_path = _fixture_experiment_tree(tmp_path, "top", top_level)

    from_window = parse_experiment_file(window_path, window_dir, settings=settings)
    from_top = parse_experiment_file(top_path, top_dir, settings=settings)

    assert from_window.splits == from_top.splits == ("pilot",)
    assert from_window.params_sha256 == from_top.params_sha256


def test_splits_given_in_both_places_is_refused(tmp_path: Path, settings: Settings) -> None:
    original = (FIXTURES / "e1h-demand-deterioration-revenue.md").read_text(encoding="utf-8")
    original = original.replace('["ER-4", "ER-5", "INT-4"]', '["FX-1"]')
    duplicated = original.replace("seed = 20261003\n", 'seed = 20261003\nsplits = ["pilot"]\n')
    experiments_dir, path = _fixture_experiment_tree(tmp_path, "duplicated", duplicated)
    with pytest.raises(ExperimentFileError, match="given both at top level and under"):
        parse_experiment_file(path, experiments_dir, settings=settings)


def test_event_dates_are_normalised_to_utc_regardless_of_source_timezone(tmp_path: Path) -> None:
    """quant-auditor, PR #935 SHOULD FIX 1: the same instant must read as the same
    date whether the export stores it naive, in UTC, or in another zone, or a row
    near a sealed-period edge could land on either side depending on the writer."""
    import polars as pl

    instant_et = datetime(
        2025, 12, 31, 20, 0, tzinfo=timezone(timedelta(hours=-5))
    )  # 2026-01-01 01:00 UTC
    instant_utc = instant_et.astimezone(UTC)
    naive = datetime(2026, 1, 1, 1, 0)  # noqa: DTZ001 -- deliberately naive; treated as UTC

    for name, value in (("et", instant_et), ("utc", instant_utc), ("naive", naive)):
        path = tmp_path / f"{name}.parquet"
        pl.DataFrame({"event_date": [value]}).write_parquet(path)
        values = read_event_column(path, "event_date")
        assert values == [date(2026, 1, 1)], name


def test_hash_file_is_deterministic(tmp_path: Path) -> None:
    f = tmp_path / "a.csv"
    f.write_text("x,y\n1,2\n", encoding="utf-8")
    assert hash_file(f) == hash_file(f)
    assert hash_file(f) == hash_export(f)


def test_hash_directory_is_independent_of_listing_order(tmp_path: Path) -> None:
    a = tmp_path / "export_a"
    a.mkdir()
    (a / "b.txt").write_text("second", encoding="utf-8")
    (a / "a.txt").write_text("first", encoding="utf-8")
    sub = a / "sub"
    sub.mkdir()
    (sub / "c.txt").write_text("third", encoding="utf-8")

    b = tmp_path / "export_b"
    b.mkdir()
    (b / "sub").mkdir()
    (b / "sub" / "c.txt").write_text("third", encoding="utf-8")
    (b / "a.txt").write_text("first", encoding="utf-8")
    (b / "b.txt").write_text("second", encoding="utf-8")

    assert hash_directory(a) == hash_directory(b)
    assert hash_directory(a) == hash_export(a)

    (b / "a.txt").write_text("changed", encoding="utf-8")
    assert hash_directory(a) != hash_directory(b)


def test_hash_directory_refuses_a_non_directory(tmp_path: Path) -> None:
    f = tmp_path / "x.csv"
    f.write_text("x\n1\n", encoding="utf-8")
    with pytest.raises(ExperimentFileError, match="not a directory"):
        hash_directory(f)


def test_read_event_column_from_csv_and_parquet(tmp_path: Path) -> None:
    import polars as pl

    frame = pl.DataFrame(
        {"event_date": [date(2020, 1, 1), date(2020, 6, 15), date(2020, 12, 31)], "x": [1, 2, 3]}
    )
    csv_path = tmp_path / "export.csv"
    frame.write_csv(csv_path)
    parquet_path = tmp_path / "export.parquet"
    frame.write_parquet(parquet_path)

    for path in (csv_path, parquet_path):
        values = read_event_column(path, "event_date")
        assert event_span(values) == (date(2020, 1, 1), date(2020, 12, 31))


def test_read_event_column_refuses_an_unknown_column(tmp_path: Path) -> None:
    import polars as pl

    path = tmp_path / "export.csv"
    pl.DataFrame({"x": [1]}).write_csv(path)
    with pytest.raises(ExperimentFileError, match="event column"):
        read_event_column(path, "event_date")


def test_check_declared_event_span_refuses_a_span_the_data_exceed() -> None:
    check_declared_event_span(
        date(2020, 1, 1), date(2020, 12, 31), date(2020, 2, 1), date(2020, 11, 1)
    )
    with pytest.raises(ExperimentFileError, match="exceeded"):
        check_declared_event_span(
            date(2020, 1, 1), date(2020, 12, 31), date(2019, 12, 1), date(2020, 11, 1)
        )
    with pytest.raises(ExperimentFileError, match="exceeded"):
        check_declared_event_span(
            date(2020, 1, 1), date(2020, 12, 31), date(2020, 2, 1), date(2021, 1, 1)
        )


def test_load_split_assignment_and_split_event_spans(tmp_path: Path) -> None:
    split_path = tmp_path / "splits.json"
    split_path.write_text(json.dumps({"splits": ["dev", "dev", "test"]}), encoding="utf-8")
    assignment = load_split_assignment(split_path)
    assert assignment == ("dev", "dev", "test")

    values = [date(2020, 1, 1), date(2020, 6, 1), date(2020, 12, 1)]
    spans = split_event_spans(values, assignment)
    assert spans == {
        "dev": (date(2020, 1, 1), date(2020, 6, 1)),
        "test": (date(2020, 12, 1), date(2020, 12, 1)),
    }


def test_load_split_assignment_refuses_bad_shapes(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"splits": ["dev", "not_a_split"]}), encoding="utf-8")
    with pytest.raises(ExperimentFileError, match="splits"):
        load_split_assignment(bad)

    not_json = tmp_path / "not_json.json"
    not_json.write_text("{not valid", encoding="utf-8")
    with pytest.raises(ExperimentFileError, match="not valid JSON"):
        load_split_assignment(not_json)


def test_split_event_spans_refuses_a_length_mismatch() -> None:
    with pytest.raises(ExperimentFileError, match="rows"):
        split_event_spans([date(2020, 1, 1)], ["dev", "test"])


def test_effective_sealed_splits_adds_test_implicitly() -> None:
    assert effective_sealed_splits([], ["dev", "test"]) == frozenset({"test"})
    assert effective_sealed_splits(["dev"], ["dev", "cal"]) == frozenset({"dev"})
    assert effective_sealed_splits(["dev"], ["dev", "test"]) == frozenset({"dev", "test"})


def test_check_sealed_split_has_period() -> None:
    period = (date(2020, 1, 1), date(2020, 12, 31))
    check_sealed_split_has_period("test", [date(2020, 6, 1)], [period])
    with pytest.raises(ExperimentFileError, match="sealed split without period"):
        check_sealed_split_has_period("test", [date(2021, 1, 1)], [period])
    with pytest.raises(ExperimentFileError, match="sealed split without period"):
        check_sealed_split_has_period("test", [date(2020, 6, 1)], [])
