"""The local raw archive's path and its disk-floor guard (T345, #2744; ADR 0028
Upload and Disk; data-foundation spec amendment #2714 items 2 and 3).

The disk is faked through `diskfloor.free_bytes`; no test reads the real one, and
no test writes outside its own `tmp_path`.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from pydantic import SecretStr

from tradepartner import diskfloor
from tradepartner.config import Settings
from tradepartner.store.raw_archive_config import (
    ARCHIVE_ENV_NAME,
    RawArchiveFloorError,
    RawArchiveUnavailableError,
    archive_root,
    check_archive_write,
    require_archive_root,
)

GB = 1_000_000_000


@pytest.fixture(autouse=True)
def _no_archive_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shell's own archive settings never reach these tests."""
    for key in list(os.environ):
        if key.upper() == ARCHIVE_ENV_NAME or key.upper().startswith("RAW_ARCHIVE__"):
            monkeypatch.delenv(key)


def _settings(root: Path | str | None = None, *, floor_gb: float | None = None) -> Settings:
    kwargs: dict[str, object] = {"_env_file": None}
    if root is not None:
        kwargs["tradepartner_raw_archive"] = str(root)
    if floor_gb is not None:
        kwargs["raw_archive"] = {"min_free_disk_gb": floor_gb}
    return Settings(**kwargs)


def _free(monkeypatch: pytest.MonkeyPatch, free: int) -> list[Path]:
    seen: list[Path] = []

    def fake(path: Path) -> int:
        seen.append(path)
        return free

    monkeypatch.setattr(diskfloor, "free_bytes", fake)
    return seen


def _assert_hidden(message: str, root: Path) -> None:
    assert str(root) not in message
    assert root.name not in message
    assert "@" not in message


# --- the path setting -------------------------------------------------------------


def test_unset_archive_is_off() -> None:
    assert archive_root(_settings()) is None


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_archive_is_off(blank: str) -> None:
    """`.env.example` names the variable with an empty value; copied as is, the
    archive stays off rather than resolving to the working directory."""
    assert archive_root(_settings(blank)) is None


def test_unset_archive_refused_where_one_is_required() -> None:
    with pytest.raises(RawArchiveUnavailableError, match=ARCHIVE_ENV_NAME):
        require_archive_root(_settings())


def test_existing_writable_directory_is_accepted(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    assert require_archive_root(_settings(root)) == root.resolve()
    assert archive_root(_settings(root)) == root


def test_home_relative_path_is_expanded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "archive").mkdir()
    assert require_archive_root(_settings("~/archive")) == (tmp_path / "archive").resolve()


def test_missing_path_refused_without_naming_it(tmp_path: Path) -> None:
    """A missing root fails the run, and is never created (a typo must not start a
    second archive); the message names the variable, never the path."""
    root = tmp_path / "jdoe@example.com-archive"
    with pytest.raises(RawArchiveUnavailableError) as excinfo:
        require_archive_root(_settings(root))
    assert ARCHIVE_ENV_NAME in str(excinfo.value)
    _assert_hidden(str(excinfo.value), root)
    assert not root.exists()


def test_file_instead_of_directory_refused(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(RawArchiveUnavailableError) as excinfo:
        require_archive_root(_settings(root))
    _assert_hidden(str(excinfo.value), root)


def test_relative_path_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A relative root would follow the working directory (launchd runs from
    `$HOME`), so it is refused even when it exists."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "archive").mkdir()
    with pytest.raises(RawArchiveUnavailableError, match="absolute"):
        require_archive_root(_settings("archive"))


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_path_refused_without_naming_it(tmp_path: Path) -> None:
    root = tmp_path / "archive"
    root.mkdir()
    root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(RawArchiveUnavailableError) as excinfo:
            require_archive_root(_settings(root))
    finally:
        root.chmod(stat.S_IRWXU)
    assert "writable" in str(excinfo.value)
    _assert_hidden(str(excinfo.value), root)


def test_archive_root_does_not_check_the_directory(tmp_path: Path) -> None:
    """`archive_root` only reads the setting; `require_archive_root` is the check."""
    root = tmp_path / "absent"
    assert archive_root(_settings(root)) == root


# --- the floor guard ----------------------------------------------------------------


def test_write_leaving_exactly_the_floor_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _free(monkeypatch, 10 * GB + 500)
    check_archive_write(_settings(tmp_path, floor_gb=10.0), tmp_path, 500)
    assert seen == [tmp_path]


def test_write_leaving_one_byte_below_the_floor_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The floor is checked against the space left after the write, not before
    it: 10 GB + 500 bytes free is above the floor, yet a 501-byte file breaches it."""
    _free(monkeypatch, 10 * GB + 500)
    with pytest.raises(RawArchiveFloorError) as excinfo:
        check_archive_write(_settings(tmp_path, floor_gb=10.0), tmp_path, 501)
    message = str(excinfo.value)
    assert "raw_archive.min_free_disk_gb" in message
    _assert_hidden(message, tmp_path)


def test_write_larger_than_free_space_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _free(monkeypatch, GB)
    with pytest.raises(RawArchiveFloorError):
        check_archive_write(_settings(tmp_path, floor_gb=10.0), tmp_path, 2 * GB)


def test_floor_comes_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _free(monkeypatch, 12 * GB)
    check_archive_write(_settings(tmp_path, floor_gb=10.0), tmp_path, GB)
    with pytest.raises(RawArchiveFloorError):
        check_archive_write(_settings(tmp_path, floor_gb=11.5), tmp_path, GB)


def test_unreadable_free_space_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(path: Path) -> int:
        raise PermissionError(str(path))

    monkeypatch.setattr(diskfloor, "free_bytes", broken)
    with pytest.raises(RawArchiveFloorError) as excinfo:
        check_archive_write(_settings(tmp_path), tmp_path, 1)
    _assert_hidden(str(excinfo.value), tmp_path)


def test_negative_size_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="size"):
        check_archive_write(_settings(tmp_path), tmp_path, -1)


def test_default_floor_is_ten_gb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _free(monkeypatch, 10 * GB)
    check_archive_write(_settings(tmp_path), tmp_path, 0)
    with pytest.raises(RawArchiveFloorError):
        check_archive_write(_settings(tmp_path), tmp_path, 1)


def test_archive_path_is_redacted_like_a_secret(tmp_path: Path) -> None:
    """The path names the macOS account; it is a `SecretStr`, so `repr`, `str` and
    every `clean_message`-stored text hide it (#334)."""
    from tradepartner.config import clean_message

    root = tmp_path / "jdoe-archive"
    settings = _settings(root)
    assert isinstance(settings.tradepartner_raw_archive, SecretStr)
    assert str(root) not in repr(settings)
    assert str(root) not in clean_message(f"failed under {root}/abc", settings)
