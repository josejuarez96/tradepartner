"""Experiment files and pre-registration, file-level only (research-registry spec req 2;
plan T82). The store-level refusals (a stale or missing `amends_sha256`, a reused
`multiplicity.family_id`) are `store.research.register_experiment`'s job (T81); this
module never opens a connection.

An experiment is a file `docs/experiments/<slug>.md` (template
`docs/templates/experiment.md`, config key `research.experiments_dir`). The registry
reads one part of it: the single fenced block whose info string is `toml experiment`.
Everything else in the file is prose, but it is hashed too (`doc_sha256`): a changed
file is always a new registration, never a variant (req 2).

`parse_experiment_file` is pure: it takes a path and the configured experiments
directory, reads the file and the sibling `claims.toml`
(`experiments_dir.parent / "research" / "claims.toml"`), and returns a
`ParsedExperiment` or raises `ExperimentFileError` naming the refusal. It performs
every refusal req 2 states that does not need the store (an unknown slug cannot be
checked against `hypotheses.families` without `Settings`, which this module does read
via `get_settings()`, but no connection).

The canonical form: `params_json` is the compact, key-sorted JSON of the TOML block
(TOML dates become ISO strings, so `2026-10-05` hashes the same from any reader) and
`params_sha256` is its SHA-256, mirroring `store.registry.canonical_params_json` for
the same reason: one parameter set has one hash whoever computes it. `doc_sha256` is
the SHA-256 of the whole file's bytes.

This module also carries the `dataset register` helpers T83's CLI calls (spec req
11): hashing a tabular file or a directory export deterministically regardless of
listing order, reading an event column's span from a tabular export, checking a
declared event span against what the data actually contain, and checking that every
row of a sealed split falls inside a sealed period. None of these touch the store
either; `dataset register`'s store-level rules (sealing never shrinks, same hash and
sealed set returns the existing row) are `store.research.register_dataset`'s job.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

import polars as pl

from tradepartner.config import Settings, get_settings

#: `kind` (Definitions, Kind). A code constant, pinned by a test; a new value is a
#: spec amendment (req 2).
KINDS: Final = ("agreement", "benchmark", "robustness", "economic", "return")
#: `stage` (Definitions, Stage): 2 to 7; stage 1 has no runs.
STAGES: Final = (2, 3, 4, 5, 6, 7)
#: `provenance` of the treatment (Definitions, Provenance).
PROVENANCES: Final = (
    "human",
    "deterministic",
    "classical",
    "model_historical",
    "model_prospective",
    "pit_model",
)
#: `split` (Definitions, Split).
SPLITS: Final = ("dev", "cal", "test", "prospective", "pilot", "full", "none")
#: `primary.direction`.
DIRECTIONS: Final = ("greater", "less")
#: `multiplicity.method`.
MULTIPLICITY_METHODS: Final = ("holm", "fixed_sequence", "bh", "none")
#: A claim grade that never counts as support (claims.toml header; req 2).
UNGRADED_GRADE: Final = "UNGRADED"

#: Top-level/dotted keys every file must name, besides the conditional ones checked
#: by hand below (`family`, `multiplicity.family_id`, `multiplicity.family_size`).
_REQUIRED_KEYS: Final = (
    "slug",
    "kind",
    "stage",
    "title",
    "confirmatory",
    "provenance",
    "touches_returns",
    "claims",
    "seed",
    "dataset.name",
    "window.start",
    "window.end",
    "splits",
    "primary.metric",
    "primary.direction",
    "primary.min_clusters",
    "primary.inference",
    "primary.secondary",
    "primary.comparison_set",
    "multiplicity.method",
    "budget.runs",
    "budget.configurations",
    "budget.stop_rule",
    "budget.expected_effect",
)
#: Keys that may be absent (`None`/omitted is not a refusal).
_OPTIONAL_KEYS: Final = (
    "family",
    "hypothesis_ref",
    "dataset.sha256",
    "primary.threshold",
    "primary.ci_level",
    "multiplicity.family_id",
    "multiplicity.family_size",
    "amends_sha256",
)
_ALLOWED_KEYS: Final = frozenset(_REQUIRED_KEYS) | frozenset(_OPTIONAL_KEYS)
_DEFAULT_CI_LEVEL: Final = 0.95

_BLOCK_OPEN: Final = re.compile(r"^```toml experiment[ \t]*$", re.MULTILINE)
_BLOCK_CLOSE: Final = re.compile(r"^```[ \t]*$", re.MULTILINE)


class ExperimentFileError(ValueError):
    """An experiment file that breaks a req 2 file-level rule."""


@dataclass(frozen=True)
class ParsedExperiment:
    """A parsed, file-level-valid experiment registration (research_registrations'
    columns, minus the ones the store assigns: `registration_id`,
    `amends_registration_id`, `registered_by`, `known_at`)."""

    path: Path
    slug: str
    kind: str
    stage: int
    title: str
    confirmatory: bool
    provenance: str
    touches_returns: bool
    family: str | None
    claims: tuple[str, ...]
    hypothesis_ref: str | None
    dataset_name: str
    dataset_sha256_pin: str | None
    window_start: date
    window_end: date
    splits: tuple[str, ...]
    primary_metric: str
    primary_direction: str
    primary_threshold: float | None
    primary_ci_level: float
    primary_min_clusters: int
    primary_inference: str
    secondary: tuple[str, ...]
    comparison_set: str
    multiplicity_method: str
    multiplicity_family_id: str | None
    multiplicity_family_size: int | None
    budget_runs: int
    budget_configurations: int
    stop_rule: str
    expected_effect: str
    seed: int
    amends_sha256: str | None
    doc_path: str
    doc_sha256: str
    params_json: str
    params_sha256: str


def _flatten(table: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in table.items():
        dotted = f"{prefix}{key}"
        # An empty table (`[foo]` with no keys) has nothing to recurse into; treat
        # it as a leaf so it still reaches the unknown-key check instead of
        # silently vanishing (quant-auditor, PR #935 NIT).
        if isinstance(value, dict) and value:
            flat.update(_flatten(value, f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


def _unflatten(flat: Mapping[str, Any]) -> dict[str, Any]:
    """The inverse of `_flatten`: dotted keys back into nested tables. Used to
    build the canonical JSON from the *normalised* parsed keys (after the
    `window.splits` → `splits` fallback below), so the stored hash reflects what
    was actually parsed rather than the raw TOML block's own table layout
    (quant-auditor, PR #935 SHOULD FIX 2)."""
    nested: dict[str, Any] = {}
    for dotted, value in flat.items():
        *parents, leaf = dotted.split(".")
        cursor = nested
        for part in parents:
            cursor = cursor.setdefault(part, {})
        cursor[leaf] = value
    return nested


def _parameter_block(text: str, path: Path) -> str:
    opens = list(_BLOCK_OPEN.finditer(text))
    if len(opens) != 1:
        raise ExperimentFileError(
            f"{path}: expected exactly one ```toml experiment block, found {len(opens)}"
        )
    start = opens[0].end() + 1
    close = _BLOCK_CLOSE.search(text, start)
    if close is None:
        raise ExperimentFileError(f"{path}: the parameter block is not closed")
    return text[start : close.start()]


def _json_safe(value: Any) -> Any:
    """TOML values, made JSON-serialisable: dates and datetimes become ISO strings so
    the canonical form is the same whatever reader parsed the file."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def canonical_experiment_json(block: Mapping[str, Any]) -> str:
    """The compact, key-sorted JSON text of an experiment block (mirrors
    `store.registry.canonical_params_json`: one block has one hash whoever computes
    it)."""
    return json.dumps(
        _json_safe(dict(block)), sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def experiment_sha256(block: Mapping[str, Any]) -> str:
    """SHA-256 of `block`'s canonical JSON (`params_sha256`): one parameter set has
    one hash whoever computes it, mirroring `store.registry.params_sha256`."""
    return sha256(canonical_experiment_json(block).encode("utf-8")).hexdigest()


def _require_str(key: str, value: object, path: Path, *, non_empty: bool = True) -> str:
    if not isinstance(value, str) or (non_empty and not value.strip()):
        raise ExperimentFileError(f"{path}: {key} must be a non-empty string, got {value!r}")
    return value


def _require_bool(key: str, value: object, path: Path) -> bool:
    if not isinstance(value, bool):
        raise ExperimentFileError(f"{path}: {key} must be a boolean, got {value!r}")
    return value


def _require_int(key: str, value: object, path: Path) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ExperimentFileError(f"{path}: {key} must be an integer, got {value!r}")
    return value


def _require_date(key: str, value: object, path: Path) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ExperimentFileError(f"{path}: {key} must be a TOML date (YYYY-MM-DD), got {value!r}")
    return value


def _require_str_list(
    key: str, value: object, path: Path, *, allow_empty: bool = False
) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ExperimentFileError(f"{path}: {key} must be a list of strings, got {value!r}")
    if not allow_empty and not value:
        raise ExperimentFileError(f"{path}: {key} must not be empty")
    return tuple(value)


def _require_enum(key: str, value: object, allowed: Sequence[Any], path: Path) -> Any:
    if value not in allowed:
        raise ExperimentFileError(f"{path}: {key} must be one of {allowed}, got {value!r}")
    return value


def _load_claims(claims_path: Path) -> dict[str, str]:
    """`id -> grade` for every `[[claim]]` in `claims_path` (claims.toml's own
    format; see its header comment)."""
    if not claims_path.is_file():
        raise ExperimentFileError(f"claims file not found: {claims_path}")
    try:
        doc = tomllib.loads(claims_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ExperimentFileError(f"{claims_path}: not valid TOML: {exc}") from exc
    claims = doc.get("claim", [])
    grades: dict[str, str] = {}
    seen: set[str] = set()
    for entry in claims:
        claim_id = entry.get("id")
        if not isinstance(claim_id, str):
            continue
        if claim_id in seen:
            # A duplicated id is ambiguous data, not this parser's call to referee
            # (quant-auditor, PR #935 NIT): never let it quietly support a
            # confirmatory registration just because the later entry's grade won.
            grades[claim_id] = UNGRADED_GRADE
            continue
        seen.add(claim_id)
        grades[claim_id] = entry.get("grade", UNGRADED_GRADE)
    return grades


def parse_experiment_file(
    path: Path,
    experiments_dir: Path,
    *,
    settings: Settings | None = None,
) -> ParsedExperiment:
    """Parse and check `path`'s file-level req 2 rules (no store access).

    `experiments_dir` is `Settings.research.experiments_dir`, resolved by the
    caller; `path` must sit directly inside it, as `docs/experiments/<slug>.md`
    (mirrors `backtest/hypothesis.py`'s flat `docs/hypotheses/` layout). Claims are
    checked against the sibling `<experiments_dir.parent>/research/claims.toml`.
    """
    settings = settings if settings is not None else get_settings()
    experiments_dir = experiments_dir.resolve()
    resolved = path.resolve()
    if resolved.parent != experiments_dir:
        raise ExperimentFileError(
            f"{path}: must be directly inside {experiments_dir} (research.experiments_dir)"
        )

    raw = path.read_bytes()
    try:
        block = tomllib.loads(_parameter_block(raw.decode("utf-8"), path))
    except tomllib.TOMLDecodeError as exc:
        raise ExperimentFileError(f"{path}: parameter block is not valid TOML: {exc}") from exc

    flat = _flatten(block)
    # The spec's two Data / interfaces examples write `splits = [...]` directly after
    # `[window]`'s `start`/`end` and before the next table header; TOML tables stay
    # open until the next `[...]` header, so that line is literally `window.splits`,
    # not the top-level `splits` req 2 names. Both fixtures must register cleanly
    # (plan T82), so a bare `splits` placed there is accepted as the top-level key.
    if "window.splits" in flat:
        if "splits" in flat:
            raise ExperimentFileError(f"{path}: splits given both at top level and under [window]")
        flat["splits"] = flat.pop("window.splits")
    unknown = sorted(set(flat) - _ALLOWED_KEYS)
    if unknown:
        raise ExperimentFileError(f"{path}: unknown keys: {', '.join(unknown)}")
    missing = sorted(set(_REQUIRED_KEYS) - set(flat))
    if missing:
        raise ExperimentFileError(f"{path}: required keys missing: {', '.join(missing)}")

    slug = _require_str("slug", flat["slug"], path)
    if slug != path.stem:
        raise ExperimentFileError(f"{path}: slug {slug!r} must equal the file name {path.stem!r}")

    kind = _require_enum("kind", flat["kind"], KINDS, path)
    stage = _require_int("stage", flat["stage"], path)
    _require_enum("stage", stage, STAGES, path)
    title = _require_str("title", flat["title"], path)
    confirmatory = _require_bool("confirmatory", flat["confirmatory"], path)
    provenance = _require_enum("provenance", flat["provenance"], PROVENANCES, path)
    touches_returns = _require_bool("touches_returns", flat["touches_returns"], path)

    if kind == "return" and not touches_returns:
        raise ExperimentFileError(f'{path}: kind = "return" requires touches_returns = true')

    family_raw = flat.get("family")
    family: str | None = None
    if family_raw is not None:
        family = _require_str("family", family_raw, path)
    if touches_returns:
        if family is None:
            raise ExperimentFileError(f"{path}: touches_returns = true requires a family")
        valid_families = tuple(settings.hypotheses.families)
        if family not in valid_families:
            raise ExperimentFileError(
                f"{path}: family {family!r} is not in hypotheses.families {valid_families}"
            )

    claims = _require_str_list("claims", flat["claims"], path)
    claims_path = experiments_dir.parent / "research" / "claims.toml"
    grades = _load_claims(claims_path)
    missing_claims = sorted(c for c in claims if c not in grades)
    if missing_claims:
        raise ExperimentFileError(
            f"{path}: claim ids absent from {claims_path}: {', '.join(missing_claims)}"
        )
    if confirmatory:
        ungraded = sorted(c for c in claims if grades[c] == UNGRADED_GRADE)
        if ungraded:
            raise ExperimentFileError(
                f"{path}: UNGRADED claims cannot support a confirmatory registration: "
                f"{', '.join(ungraded)}"
            )

    hypothesis_ref_raw = flat.get("hypothesis_ref")
    hypothesis_ref = (
        _require_str("hypothesis_ref", hypothesis_ref_raw, path)
        if hypothesis_ref_raw is not None
        else None
    )

    seed = _require_int("seed", flat["seed"], path)
    dataset_name = _require_str("dataset.name", flat["dataset.name"], path)
    dataset_sha256_pin_raw = flat.get("dataset.sha256")
    dataset_sha256_pin = (
        _require_str("dataset.sha256", dataset_sha256_pin_raw, path)
        if dataset_sha256_pin_raw is not None
        else None
    )

    window_start = _require_date("window.start", flat["window.start"], path)
    window_end = _require_date("window.end", flat["window.end"], path)
    if window_end < window_start:
        raise ExperimentFileError(
            f"{path}: window.end ({window_end}) is before window.start ({window_start})"
        )

    splits = _require_str_list("splits", flat["splits"], path)
    for split in splits:
        _require_enum("splits", split, SPLITS, path)
    if "test" in splits and not confirmatory:
        raise ExperimentFileError(f'{path}: splits may name "test" only when confirmatory = true')

    primary_metric = _require_str("primary.metric", flat["primary.metric"], path)
    primary_direction = _require_enum(
        "primary.direction", flat["primary.direction"], DIRECTIONS, path
    )
    threshold_raw = flat.get("primary.threshold")
    primary_threshold = (
        float(threshold_raw)
        if isinstance(threshold_raw, (int, float)) and not isinstance(threshold_raw, bool)
        else None
    )
    if threshold_raw is not None and primary_threshold is None:
        raise ExperimentFileError(
            f"{path}: primary.threshold must be a number, got {threshold_raw!r}"
        )
    ci_level_raw = flat.get("primary.ci_level", _DEFAULT_CI_LEVEL)
    if (
        not isinstance(ci_level_raw, (int, float))
        or isinstance(ci_level_raw, bool)
        or not (0 < ci_level_raw < 1)
    ):
        raise ExperimentFileError(
            f"{path}: primary.ci_level must be in (0, 1), got {ci_level_raw!r}"
        )
    primary_ci_level = float(ci_level_raw)
    # Bake the resolved value back into `flat` so the canonical hash is the same
    # whether the file wrote `ci_level = 0.95` explicitly or left it to default
    # (code-review on #935: a `.get(key, default)` fallback must not make two
    # parameter-identical files hash differently).
    flat["primary.ci_level"] = primary_ci_level
    primary_min_clusters = _require_int("primary.min_clusters", flat["primary.min_clusters"], path)
    if primary_min_clusters < 1:
        raise ExperimentFileError(
            f"{path}: primary.min_clusters must be >= 1, got {primary_min_clusters}"
        )
    primary_inference = _require_str("primary.inference", flat["primary.inference"], path)
    secondary = _require_str_list(
        "primary.secondary", flat["primary.secondary"], path, allow_empty=True
    )
    comparison_set = _require_str("primary.comparison_set", flat["primary.comparison_set"], path)

    multiplicity_method = _require_enum(
        "multiplicity.method", flat["multiplicity.method"], MULTIPLICITY_METHODS, path
    )
    family_id_raw = flat.get("multiplicity.family_id")
    family_size_raw = flat.get("multiplicity.family_size")
    multiplicity_family_id: str | None = None
    multiplicity_family_size: int | None = None
    if multiplicity_method != "none":
        if family_id_raw is None or family_size_raw is None:
            raise ExperimentFileError(
                f"{path}: multiplicity.family_id and multiplicity.family_size are required "
                f'unless multiplicity.method = "none"'
            )
        multiplicity_family_id = _require_str("multiplicity.family_id", family_id_raw, path)
        multiplicity_family_size = _require_int("multiplicity.family_size", family_size_raw, path)
        if multiplicity_family_size < 1:
            raise ExperimentFileError(f"{path}: multiplicity.family_size must be >= 1")
    else:
        if family_id_raw is not None:
            multiplicity_family_id = _require_str("multiplicity.family_id", family_id_raw, path)
        if family_size_raw is not None:
            multiplicity_family_size = _require_int(
                "multiplicity.family_size", family_size_raw, path
            )

    budget_runs = _require_int("budget.runs", flat["budget.runs"], path)
    if budget_runs < 1:
        raise ExperimentFileError(f"{path}: budget.runs must be >= 1, got {budget_runs}")
    budget_configurations = _require_int(
        "budget.configurations", flat["budget.configurations"], path
    )
    if budget_configurations < 1:
        raise ExperimentFileError(
            f"{path}: budget.configurations must be >= 1, got {budget_configurations}"
        )
    stop_rule = _require_str("budget.stop_rule", flat["budget.stop_rule"], path)
    expected_effect = _require_str("budget.expected_effect", flat["budget.expected_effect"], path)

    if confirmatory and provenance == "model_historical":
        raise ExperimentFileError(
            f'{path}: confirmatory = true is refused with provenance = "model_historical"'
        )

    amends_sha256_raw = flat.get("amends_sha256")
    amends_sha256 = (
        _require_str("amends_sha256", amends_sha256_raw, path)
        if amends_sha256_raw is not None
        else None
    )

    # Hash the *normalised* keys (`flat`, after the `window.splits` fallback above),
    # not the raw TOML block: otherwise the stored hash depends on which table the
    # author happened to put `splits` under, and a reader of `params_json` would
    # find no top-level `splits` for either of the spec's own worked examples
    # (quant-auditor, PR #935 SHOULD FIX 2).
    normalised = _unflatten(flat)

    return ParsedExperiment(
        path=path,
        slug=slug,
        kind=kind,
        stage=stage,
        title=title,
        confirmatory=confirmatory,
        provenance=provenance,
        touches_returns=touches_returns,
        family=family,
        claims=claims,
        hypothesis_ref=hypothesis_ref,
        dataset_name=dataset_name,
        dataset_sha256_pin=dataset_sha256_pin,
        window_start=window_start,
        window_end=window_end,
        splits=splits,
        primary_metric=primary_metric,
        primary_direction=primary_direction,
        primary_threshold=primary_threshold,
        primary_ci_level=primary_ci_level,
        primary_min_clusters=primary_min_clusters,
        primary_inference=primary_inference,
        secondary=secondary,
        comparison_set=comparison_set,
        multiplicity_method=multiplicity_method,
        multiplicity_family_id=multiplicity_family_id,
        multiplicity_family_size=multiplicity_family_size,
        budget_runs=budget_runs,
        budget_configurations=budget_configurations,
        stop_rule=stop_rule,
        expected_effect=expected_effect,
        seed=seed,
        amends_sha256=amends_sha256,
        doc_path=path.as_posix(),
        doc_sha256=sha256(raw).hexdigest(),
        params_json=canonical_experiment_json(normalised),
        params_sha256=experiment_sha256(normalised),
    )


# --------------------------------------------------------------------------------
# `dataset register` helpers (req 11; T83's CLI composes these). Pure: no store
# access, no mutation. The store-level dataset rules (sealing never shrinks, the
# same hash and sealed set returns the existing row) belong to
# `store.research.register_dataset` (T81).
# --------------------------------------------------------------------------------


def hash_file(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of a single file's bytes, read in chunks."""
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


# Domain-separation prefix for `hash_directory`'s blob (#938): without it, a
# directory with no files and a zero-byte file both reduce to `sha256(b"")`, so a
# dataset export and a single-file export could be mistaken for one another. The
# prefix is part of the hash contract, not a secret; changing it changes every
# directory digest.
_DIRECTORY_HASH_DOMAIN = b"dir-v1\n"


def hash_directory(root: Path) -> str:
    """SHA-256 of a directory export, independent of listing order (req 11): a
    fixed domain-separation prefix (`_DIRECTORY_HASH_DOMAIN`), followed by the
    sorted list of `relative/path\\tsize\\tsha256` lines, one per file, hashed as
    one blob. Refuses a relative file name containing a control character (tab,
    newline, ...), which could otherwise forge a collision with a differently
    laid out export."""
    if not root.is_dir():
        raise ExperimentFileError(f"{root}: not a directory")
    lines: list[str] = []
    for file_path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = file_path.relative_to(root).as_posix()
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in rel):
            raise ExperimentFileError(
                f"{file_path}: relative file name contains a control character"
            )
        size = file_path.stat().st_size
        lines.append(f"{rel}\t{size}\t{hash_file(file_path)}")
    blob = _DIRECTORY_HASH_DOMAIN + "\n".join(lines).encode("utf-8")
    return sha256(blob).hexdigest()


def hash_export(path: Path) -> str:
    """SHA-256 of a tabular file or a directory export (req 11's `--path`)."""
    if path.is_dir():
        return hash_directory(path)
    return hash_file(path)


def _read_tabular(path: Path) -> pl.DataFrame:
    if path.suffix == ".parquet":
        return pl.read_parquet(path)
    if path.suffix in (".csv", ".tsv"):
        separator = "\t" if path.suffix == ".tsv" else ","
        return pl.read_csv(path, separator=separator, try_parse_dates=True)
    raise ExperimentFileError(
        f"{path}: unsupported tabular export format {path.suffix!r} (expected .csv/.tsv/.parquet)"
    )


def _as_date(value: Any, path: Path, column: str) -> date:
    """One fixed convention, so the same instant reads the same date whatever
    timezone the export's reader (or writer) used (quant-auditor, PR #935 SHOULD
    FIX 1): a naive datetime is treated as UTC (CLAUDE.md: datetimes are always
    timezone-aware UTC), a tz-aware one is converted to UTC, and the calendar date
    is taken only after that conversion."""
    if isinstance(value, datetime):
        aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return aware.date()
    if isinstance(value, date):
        return value
    raise ExperimentFileError(f"{path}: event column {column!r} is not date-like, got {value!r}")


def read_event_column(path: Path, event_column: str) -> list[date]:
    """Every value of `event_column` in the tabular export at `path`, as dates, in
    row order (req 11's `--event-column`)."""
    frame = _read_tabular(path)
    if event_column not in frame.columns:
        raise ExperimentFileError(
            f"{path}: event column {event_column!r} not found; columns: {frame.columns}"
        )
    return [_as_date(v, path, event_column) for v in frame[event_column].to_list()]


def event_span(values: Sequence[date]) -> tuple[date, date]:
    """`(min, max)` of a non-empty sequence of event dates."""
    if not values:
        raise ExperimentFileError("event column has no rows; cannot compute an event span")
    return (min(values), max(values))


def check_declared_event_span(
    declared_start: date,
    declared_end: date,
    actual_start: date,
    actual_end: date,
    *,
    path: Path | None = None,
) -> None:
    """Refuses a declared `[declared_start, declared_end]` the data exceed (req 11)."""
    if actual_start < declared_start or actual_end > declared_end:
        where = f"{path}: " if path is not None else ""
        raise ExperimentFileError(
            f"{where}declared event span [{declared_start}, {declared_end}] is exceeded by the "
            f"data's actual span [{actual_start}, {actual_end}]"
        )


def load_split_assignment(split_path: Path) -> tuple[str, ...]:
    """The per-row split name from a `--split-json` file: `{"splits": ["dev", ...]}`,
    one entry per row of the tabular export, in the same row order."""
    try:
        doc = json.loads(split_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExperimentFileError(f"{split_path}: not valid JSON: {exc}") from exc
    if not isinstance(doc, dict) or "splits" not in doc:
        raise ExperimentFileError(f'{split_path}: expected a JSON object with a "splits" key')
    splits = doc["splits"]
    if not isinstance(splits, list) or not all(isinstance(s, str) for s in splits):
        raise ExperimentFileError(f'{split_path}: "splits" must be a list of strings')
    for split in splits:
        _require_enum("splits[]", split, SPLITS, split_path)
    return tuple(splits)


def split_event_spans(
    event_values: Sequence[date], split_assignment: Sequence[str]
) -> dict[str, tuple[date, date]]:
    """Per-split `(min, max)` event date, from row-aligned `event_values` and
    `split_assignment` (req 11's `split_spans_json`)."""
    if len(event_values) != len(split_assignment):
        raise ExperimentFileError(
            f"event column has {len(event_values)} rows but the split assignment has "
            f"{len(split_assignment)}"
        )
    by_split: dict[str, list[date]] = {}
    for value, split in zip(event_values, split_assignment, strict=True):
        by_split.setdefault(split, []).append(value)
    return {split: event_span(values) for split, values in by_split.items()}


def effective_sealed_splits(
    explicit_sealed: Iterable[str], split_assignment: Iterable[str]
) -> frozenset[str]:
    """The sealed split names: `explicit_sealed` plus `test`, implicitly, whenever
    `test` rows are present in the split assignment (Definitions, Split; req 5)."""
    sealed = set(explicit_sealed)
    if "test" in set(split_assignment):
        sealed.add("test")
    return frozenset(sealed)


def check_sealed_split_has_period(
    split: str, event_values_for_split: Sequence[date], sealed_periods: Sequence[tuple[date, date]]
) -> None:
    """Refuses (`sealed split without period`) unless every row of a sealed `split`
    falls inside at least one of `sealed_periods` (req 11: sealing a split by label
    only, with no period, would let a later relabelling leak rows a period never
    held)."""
    if not sealed_periods:
        raise ExperimentFileError(
            f"sealed split without period: {split!r} is sealed but no period is declared"
        )
    for value in event_values_for_split:
        if not any(start <= value <= end for start, end in sealed_periods):
            raise ExperimentFileError(
                f"sealed split without period: {split!r} row with event date {value} falls "
                f"outside every sealed period {list(sealed_periods)}"
            )
