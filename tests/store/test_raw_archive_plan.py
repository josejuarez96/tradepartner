"""The existing-cache migration planner (plan T360, #2758; data-foundation spec
amendment 2026-10-31, #2743, items 1, 2, 3 and 6, and the owner's answers).

The planner is pure: every listing here is in memory, no path in it exists, and no
test reads or writes the disk or the archive.
"""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from tradepartner.adapters import edgar, edgar_source
from tradepartner.config import Settings
from tradepartner.store.raw_archive_config import migration_config
from tradepartner.store.raw_archive_plan import (
    VERSION_TREES,
    CacheFile,
    MigrationClass,
    MigrationConfig,
    MigrationPlan,
    MigrationPlanRefusedError,
    current_versions,
    plan_migration,
)

GB = 1_000_000_000
MOVE, BACKUP, KEEP = MigrationClass.MOVE, MigrationClass.BACKUP, MigrationClass.KEEP


@pytest.fixture(autouse=True)
def _no_archive_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shell's own archive and EDGAR settings never reach these tests."""
    for key in list(os.environ):
        upper = key.upper()
        if upper.startswith(("RAW_ARCHIVE__", "EDGAR__")) or upper == "TRADEPARTNER_RAW_ARCHIVE":
            monkeypatch.delenv(key)


def _sha(n: int) -> str:
    return f"{n:064x}"


def _file(path: str, size: int = 100, sha: str | None = None) -> CacheFile:
    return CacheFile(path=path, size_bytes=size, mtime_ns=1_700_000_000_000_000_000, sha256=sha)


def _config(
    extra: tuple[str, ...] = (), *, floor: int = 10 * GB, bulk_stamps: bool = False
) -> MigrationConfig:
    return MigrationConfig(extra_paths=extra, floor_bytes=floor, bulk_stamps_enabled=bulk_stamps)


def _plan(
    files: list[CacheFile],
    *,
    config: MigrationConfig | None = None,
    known: frozenset[str] = frozenset(),
    free: int = 11 * GB,
) -> MigrationPlan:
    return plan_migration(
        files,
        versions=current_versions(),
        config=config or _config(),
        known_hashes=known,
        free_bytes=free,
    )


def _classes(plan: MigrationPlan) -> dict[str, MigrationClass]:
    return {f.path: f.klass for f in plan.files}


# Today's tree (amendment item 1, `du` on 2026-10-31), one file per folder.
_TODAY: dict[str, MigrationClass] = {
    "fsn/v4/2019q1/sub.tsv": MOVE,
    "fsn/v5/manifests/2019q1.json": MOVE,
    "fsn/v6/manifests/2019q1.json": KEEP,
    "fsn/_extract/2019q1/num.txt": KEEP,
    **{f"cover/v{n}/0000000000-19-000001.json": MOVE for n in range(1, 6)},
    "cover/v6/0000000000-19-000001.json": KEEP,
    "header/v1/320193/0000320193-19-000001.json": KEEP,
    "delisting/v1/0000000000-19-000001.json": KEEP,
    "filename/v1/320193.json": KEEP,
    "statement_facts/v1/320193.json": KEEP,
    "facts/v1/320193.json": KEEP,
    "stamps/v1/320193.json": BACKUP,
    "stamps/v1.restamp-1497/manifest.json": BACKUP,
    "stamps/v1.restamp-1497.json": BACKUP,
    "stamps/v1.cover-listings-1541.bak/320193.json": BACKUP,
    "bulk/submissions.zip": KEEP,
    "bulk/companyfacts.zip": KEEP,
    "index/2019-QTR1.idx.gz": KEEP,
    "insider/2016q1_form345.zip": KEEP,
    "sec_forms_identity/v1/0000000000-19-000001": KEEP,
    "sec_forms_parties/v1/0000000000-19-000001.json": KEEP,
    "sgml_unread/v1.json": KEEP,
    "failed_filings.json": KEEP,
    "failed_filings.json.lock": KEEP,
    "validation/filing-failures-20261031T000000000000Z.json": KEEP,
    "corpus/departure-reason/raw/Archives/edgar/data/1/a.htm": MOVE,
    "corpus/departure-reason/corpus.jsonl": KEEP,
    "corpus/departure-reason/counts.json": KEEP,
    "_archived/index.jsonl": KEEP,
}


def test_todays_tree_classifies_as_the_spec_says() -> None:
    plan = _plan([_file(p) for p in _TODAY])
    assert _classes(plan) == _TODAY
    # Moves free their bytes; backups and keeps free nothing.
    moves = sum(1 for c in _TODAY.values() if c is MOVE)
    assert plan.bytes_to_free == 100 * moves
    assert plan.transient_backup_bytes == 100 * sum(1 for c in _TODAY.values() if c is BACKUP)
    assert plan.duplicates == ()


def test_the_pinned_table_names_each_tree_and_its_constant() -> None:
    assert {tree: (module, name) for tree, module, name in VERSION_TREES} == {
        "fsn": ("tradepartner.adapters.edgar_source", "FSN_VERSION"),
        "cover": ("tradepartner.adapters.edgar_source", "COVER_VERSION"),
        "header": ("tradepartner.adapters.edgar_source", "HEADER_VERSION"),
        "delisting": ("tradepartner.adapters.edgar_source", "DELISTING_VERSION"),
        "filename": ("tradepartner.adapters.edgar_source", "FILENAME_VERSION"),
        "statement_facts": ("tradepartner.adapters.edgar_source", "STATEMENT_VERSION"),
        "facts": ("tradepartner.adapters.edgar_source", "PARSER_VERSION"),
        "sec_forms_identity": ("tradepartner.adapters.edgar", "SEC_FORMS_PARSER_VERSION"),
        "sec_forms_parties": ("tradepartner.adapters.edgar", "SEC_FORMS_PARSER_VERSION"),
    }
    assert "stamps" not in current_versions()
    assert current_versions()["fsn"] == edgar_source.FSN_VERSION


@pytest.mark.parametrize(
    ("module", "constant", "tree"),
    [
        (edgar_source, "HEADER_VERSION", "header"),
        (edgar_source, "STATEMENT_VERSION", "statement_facts"),
        (edgar, "SEC_FORMS_PARSER_VERSION", "sec_forms_identity"),
        (edgar, "SEC_FORMS_PARSER_VERSION", "sec_forms_parties"),
    ],
)
def test_a_dead_version_from_a_patched_constant_moves_and_the_current_one_stays(
    monkeypatch: pytest.MonkeyPatch, module: object, constant: str, tree: str
) -> None:
    old = getattr(module, constant)
    monkeypatch.setattr(module, constant, old + 1)
    plan = _plan([_file(f"{tree}/v{old}/a.json"), _file(f"{tree}/v{old + 1}/a.json")])
    assert _classes(plan) == {f"{tree}/v{old}/a.json": MOVE, f"{tree}/v{old + 1}/a.json": KEEP}
    assert plan.files[0].reason == "dead_version"


@pytest.mark.parametrize("layout", [2, 3])
def test_stamps_stay_back_up_under_any_stamps_layout(
    monkeypatch: pytest.MonkeyPatch, layout: int
) -> None:
    monkeypatch.setattr(edgar_source, "STAMPS_LAYOUT", layout)
    plan = _plan([_file("stamps/v1/1.json"), _file(f"stamps/v{layout}/1.json")])
    assert set(_classes(plan).values()) == {BACKUP}


def test_stamps_stay_back_up_under_a_patched_parser_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `PARSER_VERSION` bump kills `facts/v1`, never a stamps tree."""
    monkeypatch.setattr(edgar_source, "PARSER_VERSION", edgar_source.PARSER_VERSION + 1)
    plan = _plan([_file("stamps/v1/1.json"), _file("facts/v1/1.json")])
    assert _classes(plan) == {"stamps/v1/1.json": BACKUP, "facts/v1/1.json": MOVE}


@pytest.mark.parametrize(
    "path",
    [
        "fsn/_extract/2019q1/num.txt",
        "fsn/V4/a.json",
        "fsn/v4.old/a.json",
        "fsn/v4x/a.json",
        "fsn/v/a.json",
        "fsn/v4",  # a file named v4, not a directory
        "cover/v06/a.json",  # the number is the current one: not dead
        "header",
        "something_new/v1/a.json",
        "corpus/departure-reason/raw.bak/a.htm",
        "corpus/other/raw/a.htm",
        "README",
    ],
)
def test_a_non_version_folder_and_an_unnamed_path_are_kept(path: str) -> None:
    (planned,) = _plan([_file(path)]).files
    assert planned.klass is KEEP


def test_an_owner_listed_path_overrides_keep_but_never_back_up() -> None:
    files = [
        _file("bulk/submissions.zip"),
        _file("bulk/companyfacts.zip"),
        _file("insider/2016q1_form345.zip"),
        _file("insider/2016q2_form345.zip"),
        _file("stamps/v1/1.json"),
        _file("_archived/index.jsonl"),
    ]
    config = _config(("bulk/submissions.zip", "insider", "stamps"))
    plan = _plan(files, config=config)
    assert _classes(plan) == {
        "bulk/submissions.zip": MOVE,
        "bulk/companyfacts.zip": KEEP,
        "insider/2016q1_form345.zip": MOVE,
        "insider/2016q2_form345.zip": MOVE,
        "stamps/v1/1.json": BACKUP,
        "_archived/index.jsonl": KEEP,
    }
    assert {f.reason for f in plan.files if f.klass is MOVE} == {"owner_listed"}


def test_an_owner_listed_prefix_matches_whole_components_only() -> None:
    plan = _plan([_file("insider2/a.zip"), _file("insider/a.zip")], config=_config(("insider",)))
    assert _classes(plan) == {"insider2/a.zip": KEEP, "insider/a.zip": MOVE}


@pytest.mark.parametrize("listed", ["bulk/submissions.zip", "bulk"])
def test_submissions_zip_is_refused_while_bulk_stamps_is_on(listed: str) -> None:
    with pytest.raises(MigrationPlanRefusedError, match="bulk_stamps_enabled"):
        _plan([_file("bulk/submissions.zip")], config=_config((listed,), bulk_stamps=True))


def test_bulk_stamps_on_refuses_nothing_when_submissions_zip_is_not_listed() -> None:
    plan = _plan(
        [_file("bulk/submissions.zip"), _file("insider/a.zip")],
        config=_config(("insider",), bulk_stamps=True),
    )
    assert _classes(plan) == {"bulk/submissions.zip": KEEP, "insider/a.zip": MOVE}


def test_a_backup_class_file_with_an_unchanged_hash_is_not_copied_again() -> None:
    files = [
        _file("stamps/v1/1.json", 300, _sha(1)),
        _file("stamps/v1/2.json", 500, _sha(2)),
        _file("stamps/v1/3.json", 700, _sha(2)),  # same bytes as 2: one copy
        _file("stamps/v1/4.json", 900),  # not hashed: copied
    ]
    plan = _plan(files, known=frozenset({_sha(1)}))
    assert [f.copy for f in plan.files] == [False, True, False, True]
    assert plan.transient_backup_bytes == 500 + 900
    assert plan.duplicates == ()


def test_a_known_hash_marks_a_duplicate() -> None:
    files = [
        _file("fsn/v4/a.tsv", 300, _sha(1)),
        _file("fsn/v4/b.tsv", 500, _sha(2)),
        _file("fsn/v5/b.tsv", 500, _sha(2)),  # repeats v4's bytes within the run
        _file("fsn/v5/c.tsv", 700),
        _file("stamps/v1/1.json", 50, _sha(2)),  # archived by the move phase first
    ]
    plan = _plan(files, known=frozenset({_sha(1)}))
    by_path = {f.path: f for f in plan.files}
    assert plan.duplicates == ("fsn/v4/a.tsv", "fsn/v5/b.tsv")
    assert plan.duplicate_bytes == 800
    assert not by_path["fsn/v4/b.tsv"].duplicate
    assert not by_path["fsn/v5/c.tsv"].duplicate
    assert not by_path["stamps/v1/1.json"].copy
    # A duplicate is unlinked, so it frees its bytes as a move does.
    assert plan.bytes_to_free == 300 + 500 + 500 + 700


def test_the_move_phase_ignores_the_floor_and_a_backup_copy_is_projected_to_refuse() -> None:
    floor = 10 * GB
    files = [
        _file("fsn/v4/a.tsv", 1 * GB, _sha(1)),
        _file("stamps/v1/1.json", 1 * GB, _sha(2)),
        _file("stamps/v1/2.json", 1 * GB, _sha(3)),
        _file("stamps/v1/3.json", 1, _sha(4)),
    ]
    # Already below the floor: the move phase still runs.
    plan = _plan(files, config=_config(floor=floor), free=9 * GB)
    start, move, backup = plan.phases
    assert (start.name, start.free_bytes, start.below_floor) == ("start", 9 * GB, True)
    assert (move.name, move.free_bytes, move.floor_applies) == ("move", 10 * GB, False)
    assert backup.name == "backup" and backup.floor_applies
    by_path = {f.path: f for f in plan.files}
    assert not by_path["fsn/v4/a.tsv"].floor_refused
    # The first copy would leave 9 GB: refused, and the run stops there.
    assert [by_path[f"stamps/v1/{n}.json"].floor_refused for n in (1, 2, 3)] == [True] * 3
    assert plan.backup_refused_at == "stamps/v1/1.json"
    assert backup.free_bytes == 10 * GB
    assert plan.transient_backup_bytes == 2 * GB + 1


def test_a_backup_copy_exactly_at_the_floor_runs_and_the_next_stops() -> None:
    files = [_file("stamps/v1/1.json", GB, _sha(1)), _file("stamps/v1/2.json", 1, _sha(2))]
    plan = _plan(files, config=_config(floor=10 * GB), free=11 * GB)
    assert [f.floor_refused for f in plan.files] == [False, True]
    assert plan.backup_refused_at == "stamps/v1/2.json"
    assert plan.phases[-1].free_bytes == 10 * GB
    assert not plan.phases[-1].below_floor


def test_backups_count_the_room_the_moves_free() -> None:
    files = [_file("cover/v1/a.json", 2 * GB, _sha(1)), _file("stamps/v1/1.json", GB, _sha(2))]
    plan = _plan(files, config=_config(floor=10 * GB), free=10 * GB)
    assert plan.backup_refused_at is None
    assert [p.free_bytes for p in plan.phases] == [10 * GB, 12 * GB, 11 * GB]


def test_class_totals_count_files_and_bytes() -> None:
    plan = _plan([_file("fsn/v4/a", 3), _file("fsn/v4/b", 4), _file("stamps/v1/1", 5)])
    assert plan.totals() == {MOVE: (2, 7), BACKUP: (1, 5), KEEP: (0, 0)}


def test_the_plan_is_ordered_by_path_whatever_the_listing_order() -> None:
    files = [_file("stamps/v1/2"), _file("fsn/v4/a"), _file("stamps/v1/1")]
    assert [f.path for f in _plan(files).files] == ["fsn/v4/a", "stamps/v1/1", "stamps/v1/2"]


@pytest.mark.parametrize(
    "path", ["", "/fsn/v4/a", "fsn//a", "fsn/v4/", "./fsn", "fsn/../stamps/v1", "fsn\\v4", "~/x"]
)
def test_a_listing_path_that_is_not_a_clean_relative_path_is_refused(path: str) -> None:
    with pytest.raises(ValueError):
        _plan([_file(path)])


def test_a_listing_with_a_repeated_path_a_negative_size_or_a_bad_hash_is_refused() -> None:
    with pytest.raises(ValueError, match="twice"):
        _plan([_file("fsn/v4/a"), _file("fsn/v4/a")])
    with pytest.raises(ValueError, match="negative"):
        _plan([_file("fsn/v4/a", -1)])
    with pytest.raises(ValueError, match="SHA-256"):
        _plan([_file("fsn/v4/a", 1, "ABC")])
    with pytest.raises(ValueError, match="SHA-256"):
        _plan([_file("fsn/v4/a", 1)], known=frozenset({"not-a-hash"}))


def test_versions_must_name_exactly_the_pinned_trees() -> None:
    versions = current_versions()
    del versions["fsn"]
    with pytest.raises(ValueError, match="pinned"):
        plan_migration(
            [_file("fsn/v4/a")],
            versions=versions,
            config=_config(),
            known_hashes=frozenset(),
            free_bytes=GB,
        )
    with pytest.raises(ValueError, match="pinned"):
        plan_migration(
            [],
            versions={**current_versions(), "stamps": 1},
            config=_config(),
            known_hashes=frozenset(),
            free_bytes=GB,
        )


# --- the `raw_archive.migrate_extra_paths` key ---


def test_migrate_extra_paths_is_empty_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.raw_archive.migrate_extra_paths == []
    config = migration_config(settings)
    assert config == MigrationConfig(extra_paths=(), floor_bytes=10 * GB, bulk_stamps_enabled=False)


def test_migration_config_reads_the_key_the_floor_and_bulk_stamps() -> None:
    settings = Settings(
        _env_file=None,
        raw_archive={"migrate_extra_paths": ["bulk/submissions.zip"], "min_free_disk_gb": 12.5},
        edgar={"bulk_stamps_enabled": True},
    )
    assert migration_config(settings) == MigrationConfig(
        extra_paths=("bulk/submissions.zip",),
        floor_bytes=12_500_000_000,
        bulk_stamps_enabled=True,
    )


@pytest.mark.parametrize(
    "bad",
    [
        "",
        " ",
        "/Users/someone/cache",
        "bulk/",
        "bulk//submissions.zip",
        ".",
        "./bulk",
        "bulk/../stamps",
        "bulk\\submissions.zip",
        "~/cache",
        " bulk",
        "_archived",
        "_archived/index.jsonl",
    ],
)
def test_migrate_extra_paths_refuses_a_path_that_is_not_a_clean_cache_relative_one(
    bad: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, raw_archive={"migrate_extra_paths": [bad]})


def test_migrate_extra_paths_refuses_a_repeat() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, raw_archive={"migrate_extra_paths": ["insider", "insider"]})
