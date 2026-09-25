"""Hypothesis files and pre-registration (Phase 3 spec req 10; plan T35).

A hypothesis is a Markdown file under `docs/hypotheses/<slug>.md` (template:
`docs/templates/hypothesis.md`). The registry reads one part of it: the single
fenced block whose info string is `toml hypothesis`. Its top level names `slug`
(equal to the file name), `family`, `title` and `in_sample_start`; its tables name
config keys (`[holdout]`, `[strategy]`, `[costs]`, and any other frozen key the
file wants to pin). Every other part of the file is prose, but it is hashed too.

**The file must name** `in_sample_start`, `holdout.start`, `holdout.end` and every
`strategy.*` and `costs.*` key, or it is refused; the holdout never comes from live
`Settings`. A key outside the frozen list below is refused rather than ignored.

**The frozen set** is every key of `strategy`, `universe`, `costs`, `backtest`,
`adjust`, `master`, `gap`, `holdout` and `metrics`, plus `execution.fill_price`,
`benchmarks` and `alpaca.historical_feed`. File values win for the keys the file
names; the live `Settings` fill the rest at registration. The merged values are
validated through `Settings` and stored in their JSON form (floats for float keys,
ISO strings for dates), so `15` and `15.0` in a file hash the same.

**A run reads the frozen values back** with `load_frozen`, which overlays them on the
live `Settings` without reading the environment again, so no environment variable can
move a registered hypothesis's holdout or threshold. It refuses a stored parameter
set whose hash no longer matches, or whose keys differ from today's frozen list (a
new config key in a frozen section makes every older hypothesis unrunnable until it
is re-registered, rather than letting the live value in silently).
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

import duckdb
from pydantic import BaseModel, ValidationError

from tradepartner.config import Settings, get_settings
from tradepartner.store import registry

#: Settings sections frozen whole (spec req 10).
FROZEN_SECTIONS: Final = (
    "strategy",
    "universe",
    "costs",
    "backtest",
    "adjust",
    "master",
    "gap",
    "holdout",
    "metrics",
)
#: Single frozen keys outside those sections.
FROZEN_SINGLE_KEYS: Final = ("execution.fill_price", "benchmarks", "alpaca.historical_feed")
#: Sections every one of whose keys the file must name.
REQUIRED_SECTIONS: Final = ("strategy", "costs")
#: Keys the file must name outside those sections.
REQUIRED_SINGLE_KEYS: Final = ("holdout.start", "holdout.end")
#: Top-level keys of the parameter block that are not config keys.
METADATA_KEYS: Final = ("slug", "family", "title", "in_sample_start")

_BLOCK_OPEN: Final = re.compile(r"^```toml hypothesis[ \t]*$", re.MULTILINE)
_DATE_KEYS: Final = ("in_sample_start", "holdout.start", "holdout.end")


class HypothesisFileError(ValueError):
    """A hypothesis file, or its stored parameters, that breaks a req 10 rule."""


@dataclass(frozen=True)
class HypothesisFile:
    """A parsed hypothesis file. `file_params` holds the config keys it names, dotted."""

    path: Path
    slug: str
    family: str
    title: str
    in_sample_start: date
    holdout_start: date
    holdout_end: date
    doc_sha256: str
    file_params: dict[str, Any]


def _section_keys(section: str) -> tuple[str, ...]:
    model = Settings.model_fields[section].annotation
    assert isinstance(model, type) and issubclass(model, BaseModel), section
    return tuple(f"{section}.{name}" for name in model.model_fields)


def frozen_keys() -> tuple[str, ...]:
    """Every frozen config key, dotted, in spec order."""
    keys: list[str] = []
    for section in FROZEN_SECTIONS:
        keys.extend(_section_keys(section))
    keys.extend(FROZEN_SINGLE_KEYS)
    return tuple(keys)


def required_keys() -> frozenset[str]:
    """The config keys a hypothesis file must name (besides `in_sample_start`)."""
    keys = set(REQUIRED_SINGLE_KEYS)
    for section in REQUIRED_SECTIONS:
        keys.update(_section_keys(section))
    return frozenset(keys)


def _flatten(table: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in table.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


def _parameter_block(text: str, path: Path) -> str:
    opens = list(_BLOCK_OPEN.finditer(text))
    if len(opens) != 1:
        raise HypothesisFileError(
            f"{path}: expected exactly one ```toml hypothesis parameter block, found {len(opens)}"
        )
    start = opens[0].end() + 1
    close = re.compile(r"^```[ \t]*$", re.MULTILINE).search(text, start)
    if close is None:
        raise HypothesisFileError(f"{path}: the parameter block is not closed")
    return text[start : close.start()]


def _plain_date(key: str, value: object, path: Path) -> date:
    # A TOML offset or local date-time parses to `datetime`, a subclass of `date`.
    if not isinstance(value, date) or isinstance(value, datetime):
        raise HypothesisFileError(f"{path}: {key} must be a TOML date (YYYY-MM-DD), got {value!r}")
    return value


def parse_file(path: Path) -> HypothesisFile:
    """Parse and check a hypothesis file's parameter block (no `Settings` involved)."""
    raw = path.read_bytes()
    try:
        block = tomllib.loads(_parameter_block(raw.decode("utf-8"), path))
    except tomllib.TOMLDecodeError as exc:
        raise HypothesisFileError(f"{path}: parameter block is not valid TOML: {exc}") from exc
    flat = _flatten(block)
    allowed = set(frozen_keys()) | set(METADATA_KEYS)
    unknown = sorted(set(flat) - allowed)
    if unknown:
        raise HypothesisFileError(
            f"{path}: keys outside the frozen list are refused: {', '.join(unknown)}"
        )
    missing = sorted((required_keys() | {"slug", "family", "title", "in_sample_start"}) - set(flat))
    if missing:
        raise HypothesisFileError(f"{path}: required keys missing: {', '.join(missing)}")
    for key in ("slug", "family", "title"):
        if not isinstance(flat[key], str) or not flat[key].strip():
            raise HypothesisFileError(f"{path}: {key} must be a non-empty string")
    if flat["slug"] != path.stem:
        raise HypothesisFileError(
            f"{path}: slug {flat['slug']!r} must equal the file name {path.stem!r}"
        )
    dates = {key: _plain_date(key, flat[key], path) for key in _DATE_KEYS}
    if dates["in_sample_start"] >= dates["holdout.start"]:
        raise HypothesisFileError(
            f"{path}: in_sample_start ({dates['in_sample_start']}) must be before "
            f"holdout.start ({dates['holdout.start']})"
        )
    return HypothesisFile(
        path=path,
        slug=flat["slug"],
        family=flat["family"],
        title=flat["title"],
        in_sample_start=dates["in_sample_start"],
        holdout_start=dates["holdout.start"],
        holdout_end=dates["holdout.end"],
        doc_sha256=sha256(raw).hexdigest(),
        file_params={k: v for k, v in flat.items() if k not in METADATA_KEYS},
    )


def _overlay(settings: Settings, params: Mapping[str, Any]) -> Settings:
    """`settings` with `params` (dotted keys) set, validated, the environment unread."""
    values = settings.model_dump()
    for key, value in params.items():
        section, _, field = key.partition(".")
        if field:
            values[section][field] = value
        else:
            values[section] = value
    try:
        return Settings.model_validate(values)
    except ValidationError as exc:
        raise HypothesisFileError(f"frozen values fail validation: {exc}") from exc


def frozen_params_of(settings: Settings) -> dict[str, Any]:
    """The frozen keys of `settings`, dotted, in their JSON form."""
    dumped = settings.model_dump(mode="json")
    params: dict[str, Any] = {}
    for key in frozen_keys():
        section, _, field = key.partition(".")
        params[key] = dumped[section][field] if field else dumped[section]
    return params


def frozen_params(parsed: HypothesisFile, settings: Settings) -> dict[str, Any]:
    """The frozen set: the file's values over `settings` for every frozen key."""
    return frozen_params_of(_overlay(settings, parsed.file_params))


def register(
    conn: duckdb.DuckDBPyConnection,
    path: Path,
    *,
    registered_by: str,
    settings: Settings | None = None,
) -> registry.HypothesisRecord:
    """Register the hypothesis in `path` through `store.registry` and return its record.

    An unchanged file with unchanged live values returns the existing record; a changed
    file or frozen set is a new hypothesis (the registry decides both, and refuses a
    family outside `hypotheses.families`).
    """
    settings = settings if settings is not None else get_settings()
    parsed = parse_file(path)
    return registry.register_hypothesis(
        conn,
        slug=parsed.slug,
        family=parsed.family,
        title=parsed.title,
        doc_path=path.as_posix(),
        doc_sha256=parsed.doc_sha256,
        params=frozen_params(parsed, settings),
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by=registered_by,
        settings=settings,
    )


def load_frozen(
    conn: duckdb.DuckDBPyConnection, slug: str, *, settings: Settings | None = None
) -> Settings:
    """`Settings` for a run of `slug`'s latest registration: its frozen values over the
    live `settings` (default: loaded config) for every other key. `UnknownHypothesis`
    for an unregistered slug."""
    record = registry.get_hypothesis(conn, slug)
    if registry.params_sha256(record.params) != record.params_sha256:
        raise HypothesisFileError(
            f"{slug!r}: stored parameters do not match their hash {record.params_sha256}"
        )
    if set(record.params) != set(frozen_keys()):
        drift = sorted(set(record.params) ^ set(frozen_keys()))
        raise HypothesisFileError(
            f"{slug!r}: registered with a different frozen key set ({', '.join(drift)}); "
            "re-register the hypothesis"
        )
    live = settings if settings is not None else get_settings()
    return _overlay(live, record.params)
