"""The Lab's file listing and sweep preview (plan T289, #2319): registry state
through the registry readers, git state through read-only git in a throwaway
repository, the name and symlink refusals, and the preview's figures. Nothing here
writes to the store the page reads (counts and file bytes are compared).
"""

from __future__ import annotations

import subprocess
import time
from datetime import date
from pathlib import Path

import duckdb
import pytest
from backtest.test_sweep_register import (
    SWEEP_SOURCE,
    TWIN_SOURCE,
    _copy,
    _counts,
    _settings,
    _twin,
)

from tradepartner.backtest import results, sweep
from tradepartner.config import Settings
from tradepartner.dashboard import lab_files
from tradepartner.dashboard.lab_files import (
    FileKind,
    GitState,
    LabFileRefused,
    RegistryState,
)
from tradepartner.store import lab_registry


@pytest.fixture
def settings() -> Settings:
    return _settings()


@pytest.fixture
def ready(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> duckdb.DuckDBPyConnection:
    _twin(lab_store, settings, tmp_path)
    return lab_store


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def docs(tmp_path: Path) -> Path:
    """A throwaway repository: `docs/sweeps/fixture-sweep.md` and
    `docs/hypotheses/fixture-momentum.md` committed, HEAD equal to `origin/main`."""
    root = tmp_path / "repo"
    sweeps = root / "docs" / "sweeps"
    hypotheses = root / "docs" / "hypotheses"
    sweeps.mkdir(parents=True)
    hypotheses.mkdir(parents=True)
    sweep_text = _copy(tmp_path, SWEEP_SOURCE).read_text()
    (sweeps / "fixture-sweep.md").write_text(sweep_text)
    (hypotheses / "fixture-momentum.md").write_text(_copy(tmp_path, TWIN_SOURCE).read_text())
    _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "files")
    _git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    return root / "docs"


def _by_name(files: list[lab_files.LabFile]) -> dict[str, lab_files.LabFile]:
    return {f.name: f for f in files}


def test_lists_one_row_per_file_with_kind_and_slug(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    files = lab_files.list_files(ready, settings, docs)

    assert [(f.kind, f.name, f.slug) for f in files] == [
        (FileKind.SWEEP, "fixture-sweep.md", "fixture-sweep"),
        (FileKind.HYPOTHESIS, "fixture-momentum.md", "fixture-momentum"),
    ]


def test_registry_states_for_a_sweep(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path, tmp_path: Path
) -> None:
    path = docs / "sweeps" / "fixture-sweep.md"
    [row] = [f for f in lab_files.list_files(ready, settings, docs) if f.kind is FileKind.SWEEP]
    assert row.registry_state is RegistryState.NOT_REGISTERED

    registration = sweep.register(ready, path, settings, registered_by="test")
    [row] = [f for f in lab_files.list_files(ready, settings, docs) if f.kind is FileKind.SWEEP]
    assert row.registry_state is RegistryState.REGISTERED
    assert row.registered_at == registration.sweep.registered_at
    assert row.stored_sha256 == registration.sweep.doc_sha256 == row.file_sha256

    path.write_text(path.read_text() + "\nA prose edit.\n")
    [row] = [f for f in lab_files.list_files(ready, settings, docs) if f.kind is FileKind.SWEEP]
    assert row.registry_state is RegistryState.CHANGED_SINCE_REGISTRATION
    assert row.stored_sha256 != row.file_sha256


def test_an_unreadable_file_carries_the_scrubbed_parser_message(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    (docs / "sweeps" / "broken.md").write_text("# no block here\n")

    row = _by_name(lab_files.list_files(ready, settings, docs))["broken.md"]

    assert row.registry_state is RegistryState.UNREADABLE
    assert row.message


def test_a_secret_in_a_parser_message_is_scrubbed() -> None:
    secret_settings = Settings(_env_file=None, alpaca_api_secret="s3cr3t-value-xyz")
    out = lab_files.scrub("bad key s3cr3t-value-xyz \x1b[31mred\x00", secret_settings)

    assert "s3cr3t-value-xyz" not in out and "\x1b" not in out and "\x00" not in out


def test_a_hypothesis_file_registry_state_and_desk_only(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    # The twin is registered by `_twin` from the same bytes, under the same slug.
    row = _by_name(lab_files.list_files(ready, settings, docs))["fixture-momentum.md"]

    assert row.desk_only
    assert row.registry_state is RegistryState.REGISTERED
    assert row.registered_at is not None and row.stored_sha256 == row.file_sha256

    path = docs / "hypotheses" / "fixture-momentum.md"
    path.write_text(path.read_text() + "\nEdit.\n")
    row = _by_name(lab_files.list_files(ready, settings, docs))["fixture-momentum.md"]
    assert row.registry_state is RegistryState.CHANGED_SINCE_REGISTRATION


def test_an_unregistered_hypothesis_file(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    row = _by_name(lab_files.list_files(fixture_store, settings, docs))["fixture-momentum.md"]

    # A store without the lab tables: nothing is desk-only, nothing registered.
    assert row.registry_state is RegistryState.NOT_REGISTERED
    assert not row.desk_only
    sweep_row = _by_name(lab_files.list_files(fixture_store, settings, docs))["fixture-sweep.md"]
    assert sweep_row.registry_state is RegistryState.NOT_REGISTERED
    assert sweep_row.message and "not initialised" in sweep_row.message


def test_git_state_merged_edited_untracked_and_off_main(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    root = docs.parent
    states = {f.name: f.git_state for f in lab_files.list_files(ready, settings, docs)}
    assert states == {
        "fixture-sweep.md": GitState.MERGED,
        "fixture-momentum.md": GitState.MERGED,
    }

    (docs / "sweeps" / "fixture-sweep.md").write_text("edited\n")
    (docs / "sweeps" / "new-one.md").write_text("untracked\n")
    states = {f.name: f.git_state for f in lab_files.list_files(ready, settings, docs)}
    assert states["fixture-sweep.md"] is GitState.NOT_MERGED
    assert states["new-one.md"] is GitState.NOT_MERGED
    assert states["fixture-momentum.md"] is GitState.MERGED

    # HEAD one commit ahead of origin/main: nothing is merged.
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "ahead")
    states = {f.name: f.git_state for f in lab_files.list_files(ready, settings, docs)}
    assert set(states.values()) == {GitState.NOT_MERGED}


def test_an_ignored_file_is_not_merged(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    (docs.parent / ".gitignore").write_text("docs/sweeps/hidden.md\n")
    _git(docs.parent, "add", "-A")
    _git(docs.parent, "commit", "-q", "-m", "ignore")
    _git(docs.parent, "update-ref", "refs/remotes/origin/main", "HEAD")
    (docs / "sweeps" / "hidden.md").write_text("ignored\n")

    row = _by_name(lab_files.list_files(ready, settings, docs))["hidden.md"]

    assert row.git_state is GitState.NOT_MERGED


def test_outside_a_repository_nothing_is_merged(
    ready: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    docs_root = tmp_path / "plain" / "docs"
    (docs_root / "sweeps").mkdir(parents=True)
    (docs_root / "sweeps" / "a.md").write_text("x\n")

    [row] = lab_files.list_files(ready, settings, docs_root)

    assert row.git_state is GitState.NOT_MERGED


@pytest.mark.parametrize(
    "name",
    ["../x.md", "a/b.md", "..", "Upper.md", "-lead.md", "x.txt", "x.md/", "a\\b.md", ".md", ""],
)
def test_bad_names_are_refused(docs: Path, name: str) -> None:
    with pytest.raises(LabFileRefused):
        lab_files.resolve_file(docs, FileKind.SWEEP, name)


def test_a_symlink_out_of_its_directory_is_refused_and_unlisted(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("x\n")
    (docs / "sweeps" / "link.md").symlink_to(outside)
    (docs / "hypotheses" / "sneaky.md").symlink_to(docs / "sweeps" / "fixture-sweep.md")

    with pytest.raises(LabFileRefused):
        lab_files.resolve_file(docs, FileKind.SWEEP, "link.md")
    with pytest.raises(LabFileRefused):
        lab_files.resolve_file(docs, FileKind.HYPOTHESIS, "sneaky.md")
    with pytest.raises(LabFileRefused):
        lab_files.resolve_file(docs, FileKind.SWEEP, "missing.md")
    names = {f.name for f in lab_files.list_files(ready, settings, docs)}
    assert "link.md" not in names and "sneaky.md" not in names


def test_resolve_file_returns_the_path(docs: Path) -> None:
    assert lab_files.resolve_file(docs, FileKind.SWEEP, "fixture-sweep.md") == (
        docs / "sweeps" / "fixture-sweep.md"
    )


# ── the preview ─────────────────────────────────────────────────────────────────────


def test_preview_reports_the_sweep_and_what_registering_would_do(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    path = docs / "sweeps" / "fixture-sweep.md"
    before_bytes = path.read_bytes()
    before = _counts(ready)

    view = lab_files.preview(path, settings, ready, today=date(2026, 10, 27))

    assert (view.slug, view.family) == ("fixture-sweep", "momentum")
    assert view.in_sample_start == date(2018, 1, 31)
    assert (view.holdout_start, view.holdout_end) == (date(2023, 1, 3), date(2025, 12, 31))
    assert view.holdout_kind == "historical"
    assert (view.selection_statistic, view.promote_at_least, view.retire_below) == (
        "sharpe_annual_excess_spy",
        0.5,
        0.0,
    )
    parsed = sweep.parse_sweep_file(path, settings)
    variants = sweep.expand_grid(parsed, settings)
    assert [(v.index, v.variant_params) for v in view.variants] == [
        (v.index, v.values) for v in variants
    ]
    assert view.family_n == results.family_n(ready, "momentum")
    assert view.declared_count == lab_registry.family_declared_count(ready, "momentum")
    assert view.doc_sha256 == parsed.doc_sha256
    assert len(view.grid_sha256) == 64
    assert "new registration" in view.registration_effect and not view.registers_unchanged
    assert path.read_bytes() == before_bytes
    assert _counts(ready) == before


def test_preview_of_a_registered_unchanged_file_writes_nothing(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    path = docs / "sweeps" / "fixture-sweep.md"
    registration = sweep.register(ready, path, settings, registered_by="test")
    before = _counts(ready)

    view = lab_files.preview(path, settings, ready)

    assert view.registration_effect == "unchanged, nothing written"
    assert view.registers_unchanged
    assert view.grid_sha256 == registration.sweep.grid_sha256
    assert view.declared_count == lab_registry.family_declared_count(ready, "momentum") == 4
    assert _counts(ready) == before


def test_a_prose_edit_to_a_registered_sweep_previews_the_refusal_register_gives(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    path = docs / "sweeps" / "fixture-sweep.md"
    sweep.register(ready, path, settings, registered_by="test")
    path.write_text(path.read_text() + "\nA prose edit.\n")

    view = lab_files.preview(path, settings, ready)

    assert view.registration_effect.startswith("refused: a variant has the fingerprint")
    with pytest.raises(sweep.FingerprintRegisteredError):
        sweep.register(ready, path, settings, registered_by="test")


def test_an_unregistered_sweep_previews_a_new_registration(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    view = lab_files.preview(docs / "sweeps" / "fixture-sweep.md", settings, ready)

    assert view.registration_effect.startswith("a new registration of 4 variants")


def test_a_holdout_after_the_registration_day_is_forward(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    path = docs / "sweeps" / "fixture-sweep.md"
    text = path.read_text().replace("start = 2023-01-03", "start = 2099-01-04", 1)
    path.write_text(text.replace("end = 2025-12-31", "end = 2099-12-31", 1))
    # The family exists (the twin), registered today: a 2099 holdout starts after it.
    view = lab_files.preview(path, settings, ready)

    assert view.holdout_kind == "forward"


def test_a_refused_file_previews_as_a_refusal(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    path = docs / "sweeps" / "fixture-sweep.md"
    path.write_text("# nothing\n")

    with pytest.raises(LabFileRefused):
        lab_files.preview(path, settings, ready)


def test_preview_on_a_store_without_the_lab_says_it_cannot_register(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    view = lab_files.preview(docs / "sweeps" / "fixture-sweep.md", settings, fixture_store)

    assert view.declared_count is None
    assert "not initialised" in view.registration_effect


def test_listing_and_preview_fit_in_half_the_store_wait(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path
) -> None:
    started = time.monotonic()
    lab_files.list_files(ready, settings, docs)
    lab_files.preview(docs / "sweeps" / "fixture-sweep.md", settings, ready)

    assert time.monotonic() - started < settings.store.lock_retry_seconds / 2


def test_preview_refuses_a_path_outside_sweeps(
    ready: duckdb.DuckDBPyConnection, settings: Settings, docs: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text((docs / "sweeps" / "fixture-sweep.md").read_text())
    link = docs / "sweeps" / "link.md"
    link.symlink_to(outside)
    for bad in (
        outside,
        link,
        docs / "hypotheses" / "fixture-momentum.md",
        docs / "sweeps" / "Nope.md",
    ):
        with pytest.raises(LabFileRefused):
            lab_files.preview(bad, settings, ready)
