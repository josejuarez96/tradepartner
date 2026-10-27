"""The Lab's file listing and sweep preview (plan T289, #2319; amendment
2026-10-27, #2304).

Two read-only functions for the Lab page, with no Streamlit and no store write:

- `list_files(conn, settings, docs_root)` returns one `LabFile` per `.md` under
  `<docs_root>/sweeps/` and `<docs_root>/hypotheses/`: its registry state (read
  through `lab_registry.sweep_by_slug` and `registry.get_hypothesis`, comparing the
  file's `doc_sha256` with the latest registration's) and its git state.
- `preview(path, settings, conn)` reads one sweep file through
  `sweep.parse_sweep_file` and `sweep.expand_grid` and says what registering would
  do, without writing anything.

**Git state.** `merged` only when `git status --porcelain -- <path>` prints nothing,
the path is tracked, and `git merge-base --is-ancestor HEAD origin/main` holds.
Both commands are read-only, run in list form with no shell, from the project root
(`docs_root`'s parent). An untracked, edited or off-main file, or any git failure,
is `not_merged`.

**Names.** A file name must match `[a-z0-9][a-z0-9-]*\\.md`; a separator, `..`, or a
symlink resolving out of its directory is refused (`LabFileRefused`) by
`resolve_file`, and left out of the listing.

**Desk only.** A hypothesis file on a lab-initialised store is `desk_only`: it
registers only through `sweep promote` (open question 3), so the page offers no
Register for it.

Every message shown is scrubbed of the settings' secrets and control characters.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

import duckdb

from tradepartner.backtest import hypothesis, results, sweep
from tradepartner.backtest.holdout import Frozen, is_forward
from tradepartner.config import Settings, secret_values
from tradepartner.store import lab_registry, lab_schema, registry

#: A file name the Lab accepts; no separator, no dot-dot, lower-case.
NAME_PATTERN = re.compile(r"[a-z0-9][a-z0-9-]*\.md")
_CONTROL = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|[\x00-\x08\x0b-\x1f\x7f]")


class FileKind(StrEnum):
    """Which folder a file sits in."""

    SWEEP = "sweep"
    HYPOTHESIS = "hypothesis"


#: The folder under the docs root for each kind.
_FOLDERS = {FileKind.SWEEP: "sweeps", FileKind.HYPOTHESIS: "hypotheses"}


class RegistryState(StrEnum):
    """Where a file stands against the registry."""

    NOT_REGISTERED = "not_registered"
    REGISTERED = "registered"
    CHANGED_SINCE_REGISTRATION = "changed_since_registration"
    UNREADABLE = "unreadable"


class GitState(StrEnum):
    """Whether the file is exactly what is on `origin/main`."""

    MERGED = "merged"
    NOT_MERGED = "not_merged"


class LabFileRefused(ValueError):
    """A file name or path the Lab will not open."""


@dataclass(frozen=True)
class LabFile:
    """One listed file. `registered_at` and `stored_sha256` are set when a
    registration exists; `message` is the scrubbed parser message of an unreadable
    file (or a note); `desk_only` has no Register on the page."""

    kind: FileKind
    name: str
    slug: str
    path: Path
    registry_state: RegistryState
    git_state: GitState
    desk_only: bool
    file_sha256: str | None = None
    registered_at: datetime | None = None
    stored_sha256: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class PreviewVariant:
    """One grid point: its canonical 1-based index and its varied values."""

    index: int
    variant_params: dict[str, object]


@dataclass(frozen=True)
class SweepPreview:
    """What a sweep file declares and what registering it would do."""

    slug: str
    family: str
    title: str
    in_sample_start: date
    holdout_start: date
    holdout_end: date
    holdout_kind: str
    variants: tuple[PreviewVariant, ...]
    selection_statistic: str
    promote_at_least: float
    retire_below: float
    grid_sha256: str
    doc_sha256: str
    family_n: int
    declared_count: int | None
    registration_effect: str
    registers_unchanged: bool


def scrub(text: str, settings: Settings) -> str:
    """`text` with the settings' secrets and control characters removed, capped."""
    for value in sorted(secret_values(settings), key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    text = _CONTROL.sub(" ", text).strip()
    cap = settings.ui.lab_message_chars
    return text if len(text) <= cap else text[:cap] + "..."


def _folder(docs_root: Path, kind: FileKind) -> Path:
    return docs_root / _FOLDERS[kind]


def resolve_file(docs_root: Path, kind: FileKind, name: str) -> Path:
    """The path of `name` in `kind`'s folder, or `LabFileRefused`: the name must
    match `NAME_PATTERN`, the file exist, and (after symlinks) sit in the folder."""
    if not NAME_PATTERN.fullmatch(name):
        raise LabFileRefused("a file name is lower-case letters, digits and hyphens, then .md")
    folder = _folder(docs_root, kind)
    path = folder / name
    try:
        resolved = path.resolve(strict=True)
        parent = folder.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LabFileRefused(f"{name} is not a file in {_FOLDERS[kind]}/") from exc
    if resolved.parent != parent or not resolved.is_file():
        raise LabFileRefused(f"{name} does not resolve to a file inside {_FOLDERS[kind]}/")
    return path


def _check_sweep_path(path: Path) -> None:
    """Refuse a `preview` path that is not a plain `sweeps/<name>.md` file."""
    try:
        resolved = path.resolve(strict=True)
        parent = path.parent.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise LabFileRefused(f"{path.name} is not a file") from exc
    if (
        not NAME_PATTERN.fullmatch(path.name)
        or parent.name != _FOLDERS[FileKind.SWEEP]
        or resolved.parent != parent
        or not resolved.is_file()
    ):
        raise LabFileRefused(f"{path.name} is not a sweep file in {_FOLDERS[FileKind.SWEEP]}/")


def _git(args: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def git_state(
    path: Path, root: Path, settings: Settings, *, on_main: bool | None = None
) -> GitState:
    """`merged` only when the file is tracked, unedited and HEAD is on `origin/main`."""
    timeout = settings.ui.lab_git_timeout_seconds
    # Git runs in `root`, so a path relative to the caller would be read from there.
    root = root.resolve()
    path = path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
    if on_main is None:
        on_main = _head_on_main(root, timeout)
    if not on_main:
        return GitState.NOT_MERGED
    status = _git(["--no-optional-locks", "status", "--porcelain", "--", str(path)], root, timeout)
    if status is None or status.returncode != 0 or status.stdout.strip():
        return GitState.NOT_MERGED
    tracked = _git(["ls-files", "--error-unmatch", "--", str(path)], root, timeout)
    if tracked is None or tracked.returncode != 0:
        return GitState.NOT_MERGED
    return GitState.MERGED


def _head_on_main(root: Path, timeout: int) -> bool:
    done = _git(["merge-base", "--is-ancestor", "HEAD", "origin/main"], root, timeout)
    return done is not None and done.returncode == 0


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _sweep_row(
    conn: duckdb.DuckDBPyConnection, settings: Settings, path: Path, lab: bool
) -> dict[str, object]:
    try:
        parsed = sweep.parse_sweep_file(path, settings)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return {
            "slug": path.stem,
            "state": RegistryState.UNREADABLE,
            "message": scrub(str(exc), settings),
        }
    if not lab:
        return {
            "slug": parsed.slug,
            "sha": parsed.doc_sha256,
            "state": RegistryState.NOT_REGISTERED,
            "message": "the strategy lab is not initialised on this store",
        }
    record = lab_registry.sweep_by_slug(conn, parsed.slug)
    if record is None:
        return {
            "slug": parsed.slug,
            "sha": parsed.doc_sha256,
            "state": RegistryState.NOT_REGISTERED,
        }
    changed = record.doc_sha256 != parsed.doc_sha256
    return {
        "slug": parsed.slug,
        "sha": parsed.doc_sha256,
        "state": RegistryState.CHANGED_SINCE_REGISTRATION if changed else RegistryState.REGISTERED,
        "at": record.registered_at,
        "stored": record.doc_sha256,
    }


def _hypothesis_row(
    conn: duckdb.DuckDBPyConnection, settings: Settings, path: Path
) -> dict[str, object]:
    try:
        parsed = hypothesis.parse_file(path)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        return {
            "slug": path.stem,
            "state": RegistryState.UNREADABLE,
            "message": scrub(str(exc), settings),
        }
    try:
        record = registry.get_hypothesis(conn, parsed.slug)
    except registry.UnknownHypothesis:
        return {
            "slug": parsed.slug,
            "sha": parsed.doc_sha256,
            "state": RegistryState.NOT_REGISTERED,
        }
    changed = record.doc_sha256 != parsed.doc_sha256
    return {
        "slug": parsed.slug,
        "sha": parsed.doc_sha256,
        "state": RegistryState.CHANGED_SINCE_REGISTRATION if changed else RegistryState.REGISTERED,
        "at": record.registered_at,
        "stored": record.doc_sha256,
    }


def list_files(
    conn: duckdb.DuckDBPyConnection, settings: Settings, docs_root: Path
) -> list[LabFile]:
    """One `LabFile` per acceptable `.md` under `<docs_root>/sweeps/` and
    `<docs_root>/hypotheses/` (sweeps first, each folder by name). A name or symlink
    `resolve_file` would refuse is left out. Reads the store and runs read-only git;
    writes nothing."""
    docs_root = docs_root.resolve()
    root = docs_root.parent
    lab = lab_schema.is_lab_initialised(conn)
    on_main = _head_on_main(root, settings.ui.lab_git_timeout_seconds)
    files: list[LabFile] = []
    for kind in (FileKind.SWEEP, FileKind.HYPOTHESIS):
        folder = _folder(docs_root, kind)
        names = sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []
        for name in names:
            try:
                path = resolve_file(docs_root, kind, name)
            except LabFileRefused:
                continue
            row = (
                _sweep_row(conn, settings, path, lab)
                if kind is FileKind.SWEEP
                else _hypothesis_row(conn, settings, path)
            )
            state = row["state"]
            assert isinstance(state, RegistryState)
            files.append(
                LabFile(
                    kind=kind,
                    name=name,
                    slug=str(row["slug"]),
                    path=path,
                    registry_state=state,
                    git_state=git_state(path, root, settings, on_main=on_main),
                    desk_only=kind is FileKind.HYPOTHESIS and lab,
                    file_sha256=row.get("sha"),  # type: ignore[arg-type]
                    registered_at=row.get("at"),  # type: ignore[arg-type]
                    stored_sha256=row.get("stored"),  # type: ignore[arg-type]
                    message=row.get("message"),  # type: ignore[arg-type]
                )
            )
    return files


def preview(
    path: Path,
    settings: Settings,
    conn: duckdb.DuckDBPyConnection,
    *,
    today: date | None = None,
) -> SweepPreview:
    """What the sweep file at `path` declares and what registering it would do.

    Raises `LabFileRefused` (scrubbed message) for a file the sweep parser or
    `expand_grid` refuses. Reads the store; writes nothing. `today` (default the UTC
    day) is the registration day of a family with no hypothesis yet, which decides
    whether its holdout is forward (ADR 0016 point 4).
    """
    _check_sweep_path(path)
    try:
        parsed = sweep.parse_sweep_file(path, settings)
        variants = sweep.expand_grid(parsed, settings)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise LabFileRefused(scrub(str(exc), settings)) from exc
    lab = lab_schema.is_lab_initialised(conn)
    registered_on = registry.family_registered_on(conn, parsed.family)
    if registered_on is None:
        registered_on = today if today is not None else datetime.now(UTC).date()
    forward = is_forward(
        Frozen(
            hypothesis_id=0,
            in_sample_start=parsed.in_sample_start,
            holdout_start=parsed.holdout_start,
            holdout_end=parsed.holdout_end,
            gap_count_share_threshold=0.0,
            registered_on=registered_on,
        )
    )
    grid = {axis: list(values) for axis, values in parsed.grid.items()}
    grid_sha256 = sha256(registry.canonical_params_json(grid).encode()).hexdigest()
    declared: int | None = None
    unchanged = False
    if not lab:
        effect = "cannot register: the strategy lab is not initialised on this store"
    else:
        declared = lab_registry.family_declared_count(conn, parsed.family)
        unchanged = sweep._existing_registration(conn, parsed, variants) is not None
        held = (
            None
            if unchanged
            else next(
                (
                    h
                    for v in variants
                    if (h := lab_registry.fingerprint_registered(conn, v.fingerprint)) is not None
                ),
                None,
            )
        )
        if unchanged:
            effect = "unchanged, nothing written"
        elif held is not None:
            effect = (
                f"refused: a variant has the fingerprint of registered hypothesis "
                f"{held.slug!r}; the existing registration is the record"
            )
        else:
            effect = (
                f"a new registration of {len(variants)} variants "
                "(the engine's other checks run when it registers)"
            )
    return SweepPreview(
        slug=parsed.slug,
        family=parsed.family,
        title=parsed.title,
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        holdout_kind="forward" if forward else "historical",
        variants=tuple(PreviewVariant(v.index, dict(v.values)) for v in variants),
        selection_statistic=parsed.lab.selection_statistic,
        promote_at_least=parsed.lab.promote_at_least,
        retire_below=parsed.lab.retire_below,
        grid_sha256=grid_sha256,
        doc_sha256=parsed.doc_sha256,
        family_n=results.family_n(conn, parsed.family),
        declared_count=declared,
        registration_effect=effect,
        registers_unchanged=unchanged,
    )
