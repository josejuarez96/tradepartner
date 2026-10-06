"""Sweep files and their grid (strategy-lab spec req 1, Definitions "Sweep", "Grid",
"Read group"; plan task T102).

A sweep is a Markdown file under `docs/sweeps/<slug>.md` (template
`docs/templates/sweep.md`). The registry reads one part of it: the single fenced block
whose info string is `toml sweep`. Its top level names `slug` (equal to the file name),
`family`, `title`, `in_sample_start` and, for a new family's first file, `parent_family`;
`[holdout]` and the other frozen sections pin fixed values; `[grid]` maps dotted keys to
lists of values; `[lab]` holds the selection statistic and the promotion and retirement
floors. The rest of the file is prose, hashed with the block.

`parse_sweep_file` applies every **file-level** refusal of req 1, each as its own
`SweepFileError` subclass: a family signal-section or `costs.*` key, or
`schedule.rebalance_cadence`, neither fixed nor gridded; a key both fixed and gridded; an
axis outside `lab.sweepable_keys` (or under `FORBIDDEN_AXIS_PREFIXES`); a `[lab]` block
missing a field, with `promote_at_least` below `lab.promotion_min_dsr_excess`, or with
`retire_below >= promote_at_least` under `dsr_excess`; `dsr_excess` with a cadence axis;
a grid value off its `lab.axis_lattice` step; two grid values that validate to one.
`expand_grid` builds each variant's frozen set exactly as `hypothesis.frozen_params`
builds a hypothesis's (file values over live `Settings`, validated, in JSON form),
refuses a product above `lab.max_variants_per_sweep` and two variants with one
fingerprint, and returns the variants in canonical order (ascending `params_sha256`).

Store-level refusals (family rules, a fingerprint already registered, family readiness,
the anchor check against the store's first session, the family rules' own lattice and
`oracle` on the real store) belong to `register` (T104). Nothing here touches a store.
"""

from __future__ import annotations

import itertools
import json
import math
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Final, Literal, get_args

from tradepartner.backtest import frozen, hypothesis
from tradepartner.config import FORBIDDEN_AXIS_PREFIXES, Settings, get_settings
from tradepartner.store import registry

__all__ = [
    "FORBIDDEN_AXIS_PREFIXES",
    "READ_GROUP_FREE_KEYS",
    "AxisNotSweepableError",
    "CadenceAxisStatisticError",
    "DuplicateFingerprintError",
    "DuplicateGridValueError",
    "FixedAndGriddedError",
    "LabBlockError",
    "MissingRequiredKeyError",
    "OffLatticeError",
    "SelectionStatistic",
    "SweepFile",
    "SweepFileError",
    "SweepLab",
    "TooManyVariantsError",
    "Variant",
    "expand_grid",
    "parse_sweep_file",
    "read_groups",
    "variant_slug",
]

SelectionStatistic = Literal["dsr_excess", "sharpe_annual_excess_spy", "excess_cagr_spy"]

#: The keys that change a variant's computation but not its provider reads (Definitions,
#: "Read group"): variants differing only in these share one read set per step.
READ_GROUP_FREE_KEYS: Final = frozenset(
    {
        "strategy.formation_months",
        "strategy.skip_months",
        "strategy.top_fraction",
        "strategy.weighting",
        "schedule.signal_anchor",
    }
)

_METADATA_KEYS: Final = ("slug", "family", "title", "in_sample_start")
_OPTIONAL_METADATA_KEYS: Final = ("parent_family",)
_LAB_FIELDS: Final = (
    "selection_statistic",
    "expected_excess_cagr_spy_pp",
    "expected_range_pp",
    "promote_at_least",
    "retire_below",
)
_CADENCE_KEY: Final = "schedule.rebalance_cadence"
_DATE_KEYS: Final = ("in_sample_start", "holdout.start", "holdout.end")
_BLOCK_OPEN: Final = re.compile(r"^```toml sweep[ \t]*$", re.MULTILINE)
_BLOCK_CLOSE: Final = re.compile(r"^```[ \t]*$", re.MULTILINE)
# A grid value is on its lattice when value / step is this close to an integer.
_LATTICE_TOLERANCE: Final = 1e-9


class SweepFileError(ValueError):
    """A sweep file that breaks a file-level rule of req 1 (or a malformed block)."""


class MissingRequiredKeyError(SweepFileError):
    """A required key (the family's signal section, `costs.*`, `holdout.*`,
    `schedule.rebalance_cadence`) is neither fixed nor gridded."""


class FixedAndGriddedError(SweepFileError):
    """A key is both fixed and a grid axis."""


class AxisNotSweepableError(SweepFileError):
    """A grid axis outside `lab.sweepable_keys`, under a forbidden prefix, or in another
    family's signal section."""


class LabBlockError(SweepFileError):
    """A `[lab]` block missing a field, with an unknown field or a bad value, below the
    promotion floor, or retiring at or above its promotion floor under `dsr_excess`."""


class CadenceAxisStatisticError(SweepFileError):
    """`dsr_excess` as the selection statistic with `schedule.rebalance_cadence` as an
    axis (its T differs by cadence)."""


class OffLatticeError(SweepFileError):
    """A grid value that is not a multiple of its axis's `lab.axis_lattice` step."""


class DuplicateGridValueError(SweepFileError):
    """Two values of one axis that are one value after `Settings` validation."""


class TooManyVariantsError(SweepFileError):
    """A grid whose product exceeds `lab.max_variants_per_sweep`."""


class DuplicateFingerprintError(SweepFileError):
    """Two variants of one grid with one fingerprint."""


@dataclass(frozen=True)
class SweepLab:
    """The `[lab]` block: the selection statistic, the excess-return prior and range,
    and the promotion and retirement floors."""

    selection_statistic: SelectionStatistic
    expected_excess_cagr_spy_pp: float
    expected_range_pp: tuple[float, float]
    promote_at_least: float
    retire_below: float


@dataclass(frozen=True)
class SweepFile:
    """A parsed sweep file. `fixed_params` holds the fixed config keys, dotted, as the
    file wrote them (`holdout.*` included); `grid` maps each axis, in file order, to its
    values validated through `Settings` in JSON form, in file order."""

    path: Path
    slug: str
    family: str
    title: str
    in_sample_start: date
    holdout_start: date
    holdout_end: date
    parent_family: str | None
    doc_sha256: str
    fixed_params: dict[str, Any]
    grid: dict[str, tuple[Any, ...]]
    lab: SweepLab


@dataclass(frozen=True)
class Variant:
    """One point of the grid: its 1-based canonical index, the axis values (JSON form),
    its frozen set (`frozen_set`, what `registry.register_hypothesis` stores as `params`),
    that set's `params_sha256` and its fingerprint."""

    index: int
    values: dict[str, Any]
    frozen_set: dict[str, Any]
    params_sha256: str
    fingerprint: str


def _flatten(table: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in table.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, f"{dotted}."))
        else:
            flat[dotted] = value
    return flat


def _block(text: str, path: Path) -> dict[str, Any]:
    opens = list(_BLOCK_OPEN.finditer(text))
    if len(opens) != 1:
        raise SweepFileError(
            f"{path}: expected exactly one ```toml sweep block, found {len(opens)}"
        )
    start = opens[0].end() + 1
    close = _BLOCK_CLOSE.search(text, start)
    if close is None:
        raise SweepFileError(f"{path}: the sweep block is not closed")
    try:
        return tomllib.loads(text[start : close.start()])
    except tomllib.TOMLDecodeError as exc:
        raise SweepFileError(f"{path}: sweep block is not valid TOML: {exc}") from exc


def _plain_date(key: str, value: object, path: Path) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise SweepFileError(f"{path}: {key} must be a TOML date (YYYY-MM-DD), got {value!r}")
    return value


def _number(path: Path, field: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise LabBlockError(f"{path}: [lab] {field} must be a finite number, got {value!r}")
    return float(value)


def _parse_lab(raw: object, path: Path, settings: Settings, cadence_axis: bool) -> SweepLab:
    if not isinstance(raw, dict):
        raise LabBlockError(f"{path}: the [lab] block is missing: {', '.join(_LAB_FIELDS)}")
    missing = [f for f in _LAB_FIELDS if f not in raw]
    if missing:
        raise LabBlockError(f"{path}: [lab] is missing: {', '.join(missing)}")
    unknown = sorted(set(raw) - set(_LAB_FIELDS))
    if unknown:
        raise LabBlockError(f"{path}: [lab] has unknown fields: {', '.join(unknown)}")
    statistic = raw["selection_statistic"]
    if statistic not in get_args(SelectionStatistic):
        raise LabBlockError(
            f"{path}: [lab] selection_statistic {statistic!r} is not one of "
            f"{', '.join(get_args(SelectionStatistic))}"
        )
    span = raw["expected_range_pp"]
    if not isinstance(span, list) or len(span) != 2:
        raise LabBlockError(f"{path}: [lab] expected_range_pp must be [lo, hi], got {span!r}")
    lo, hi = (_number(path, "expected_range_pp", v) for v in span)
    if lo > hi:
        raise LabBlockError(f"{path}: [lab] expected_range_pp [{lo}, {hi}] has lo above hi")
    promote = _number(path, "promote_at_least", raw["promote_at_least"])
    retire = _number(path, "retire_below", raw["retire_below"])
    floor = settings.lab.promotion_min_dsr_excess
    if promote < floor:
        raise LabBlockError(
            f"{path}: [lab] promote_at_least {promote} is below "
            f"lab.promotion_min_dsr_excess {floor}"
        )
    if statistic == "dsr_excess" and retire >= promote:
        raise LabBlockError(
            f"{path}: [lab] retire_below {retire} must be below promote_at_least {promote} "
            "under dsr_excess, so no argmax can both retire and promote"
        )
    if statistic == "dsr_excess" and cadence_axis:
        raise CadenceAxisStatisticError(
            f"{path}: selection_statistic dsr_excess is refused with {_CADENCE_KEY} as an axis "
            "(its T differs by cadence); use sharpe_annual_excess_spy or excess_cagr_spy"
        )
    return SweepLab(
        selection_statistic=statistic,
        expected_excess_cagr_spy_pp=_number(
            path, "expected_excess_cagr_spy_pp", raw["expected_excess_cagr_spy_pp"]
        ),
        expected_range_pp=(lo, hi),
        promote_at_least=promote,
        retire_below=retire,
    )


def _as_hypothesis(
    path: Path,
    slug: str,
    family: str,
    title: str,
    dates: Mapping[str, date],
    doc_sha256: str,
    file_params: dict[str, Any],
) -> hypothesis.HypothesisFile:
    return hypothesis.HypothesisFile(
        path=path,
        slug=slug,
        family=family,
        title=title,
        in_sample_start=dates["in_sample_start"],
        holdout_start=dates["holdout.start"],
        holdout_end=dates["holdout.end"],
        doc_sha256=doc_sha256,
        file_params=file_params,
    )


def _frozen_set(parsed: hypothesis.HypothesisFile, settings: Settings) -> dict[str, Any]:
    try:
        return hypothesis.frozen_params(parsed, settings)
    except hypothesis.HypothesisFileError as exc:
        raise SweepFileError(f"{parsed.path}: {exc}") from exc


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _on_lattice(value: object, step: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return True
    ratio = value / step
    return abs(ratio - round(ratio)) <= _LATTICE_TOLERANCE * max(1.0, abs(ratio))


def parse_sweep_file(path: Path, settings: Settings | None = None) -> SweepFile:
    """Parse a sweep file and apply every file-level refusal of req 1.

    `settings` supplies `lab.sweepable_keys`, `lab.axis_lattice` and
    `lab.promotion_min_dsr_excess`, and validates the grid values (default: the live
    `get_settings()`). Raises a `SweepFileError` subclass naming the rule broken.
    """
    settings = settings if settings is not None else get_settings()
    raw = path.read_bytes()
    doc_sha256 = sha256(raw).hexdigest()
    block = _block(raw.decode("utf-8"), path)
    grid_raw = block.pop("grid", None)
    lab_raw = block.pop("lab", None)
    flat = _flatten(block)

    for key in ("slug", "family", "title"):
        if not isinstance(flat.get(key), str) or not flat[key].strip():
            raise SweepFileError(f"{path}: {key} must be a non-empty string")
    slug, family, title = flat["slug"], flat["family"], flat["title"]
    if slug != path.stem:
        raise SweepFileError(f"{path}: slug {slug!r} must equal the file name {path.stem!r}")
    if family not in settings.hypotheses.families:
        raise SweepFileError(
            f"{path}: family {family!r} is not one of hypotheses.families "
            f"({', '.join(settings.hypotheses.families)})"
        )
    parent = flat.get("parent_family")
    if parent is not None and not (isinstance(parent, str) and parent.strip()):
        raise SweepFileError(f"{path}: parent_family must be a non-empty string")

    fixed = {k: v for k, v in flat.items() if k not in _METADATA_KEYS + _OPTIONAL_METADATA_KEYS}
    allowed = set(hypothesis.frozen_keys())
    unknown = sorted(set(fixed) - allowed)
    if unknown:
        raise SweepFileError(
            f"{path}: keys outside the frozen list are refused: {', '.join(unknown)}"
        )
    inert = frozen.inert_sections(family)
    inert_fixed = sorted(k for k in fixed if k.partition(".")[0] in inert)
    if inert_fixed:
        raise SweepFileError(
            f"{path}: keys of another family's signal section are refused for family "
            f"{family!r}: {', '.join(inert_fixed)}"
        )

    if not isinstance(grid_raw, dict) or not grid_raw:
        raise SweepFileError(f"{path}: the [grid] table must name at least one axis")
    grid_lists = _flatten(grid_raw)
    for axis, values in grid_lists.items():
        if not isinstance(values, list) or not values:
            raise SweepFileError(f"{path}: grid axis {axis} must be a non-empty list of values")
    for axis in grid_lists:
        if (
            axis not in settings.lab.sweepable_keys
            or axis.startswith(FORBIDDEN_AXIS_PREFIXES)
            or axis.partition(".")[0] in inert
        ):
            raise AxisNotSweepableError(
                f"{path}: grid axis {axis} is not in lab.sweepable_keys "
                f"({', '.join(settings.lab.sweepable_keys)})"
            )
    both = sorted(set(grid_lists) & set(fixed))
    if both:
        raise FixedAndGriddedError(
            f"{path}: keys both fixed and gridded are refused: {', '.join(both)}"
        )
    required = hypothesis.required_keys(family) | {_CADENCE_KEY}
    missing = sorted(required - set(fixed) - set(grid_lists))
    if missing:
        raise MissingRequiredKeyError(
            f"{path}: required keys neither fixed nor gridded: {', '.join(missing)}"
        )

    dates = {
        key: _plain_date(key, fixed[key] if key in fixed else flat.get(key), path)
        for key in _DATE_KEYS
    }
    if dates["in_sample_start"] >= dates["holdout.start"]:
        raise SweepFileError(
            f"{path}: in_sample_start ({dates['in_sample_start']}) must be before "
            f"holdout.start ({dates['holdout.start']})"
        )

    lab = _parse_lab(lab_raw, path, settings, cadence_axis=_CADENCE_KEY in grid_lists)

    grid: dict[str, tuple[Any, ...]] = {}
    for axis, values in grid_lists.items():
        validated: list[Any] = []
        seen: dict[str, object] = {}
        for value in values:
            params = _frozen_set(
                _as_hypothesis(
                    path, slug, family, title, dates, doc_sha256, {**fixed, axis: value}
                ),
                settings,
            )
            json_value = params[axis]
            key = _canonical_json(json_value)
            if key in seen:
                raise DuplicateGridValueError(
                    f"{path}: grid axis {axis} values {seen[key]!r} and {value!r} are one value "
                    f"({json_value!r}) after Settings validation"
                )
            seen[key] = value
            step = settings.lab.axis_lattice.get(axis)
            if step is not None and not _on_lattice(json_value, step):
                raise OffLatticeError(
                    f"{path}: grid axis {axis} value {value!r} is not a multiple of its "
                    f"lab.axis_lattice step {step}"
                )
            validated.append(json_value)
        grid[axis] = tuple(validated)

    return SweepFile(
        path=path,
        slug=slug,
        family=family,
        title=title,
        in_sample_start=dates["in_sample_start"],
        holdout_start=dates["holdout.start"],
        holdout_end=dates["holdout.end"],
        parent_family=parent,
        doc_sha256=doc_sha256,
        fixed_params=fixed,
        grid=grid,
        lab=lab,
    )


def expand_grid(file: SweepFile, settings: Settings) -> list[Variant]:
    """Every variant of `file`'s grid, in canonical order (ascending `params_sha256`),
    indexed from 1.

    Each frozen set is the file's fixed values plus the variant's axis values over
    `settings`, built by `hypothesis.frozen_params` as for a standalone file. Raises
    `TooManyVariantsError` above `lab.max_variants_per_sweep` (before any set is built)
    and `DuplicateFingerprintError` when two variants share a fingerprint.
    """
    axes = list(file.grid)
    n_variants = math.prod(len(file.grid[axis]) for axis in axes)
    cap = settings.lab.max_variants_per_sweep
    if n_variants > cap:
        raise TooManyVariantsError(
            f"{file.path}: the grid has {n_variants} variants, above "
            f"lab.max_variants_per_sweep {cap}"
        )
    dates = {
        "in_sample_start": file.in_sample_start,
        "holdout.start": file.holdout_start,
        "holdout.end": file.holdout_end,
    }
    built: list[tuple[str, str, dict[str, Any], dict[str, Any]]] = []
    by_fingerprint: dict[str, dict[str, Any]] = {}
    for combo in itertools.product(*(file.grid[axis] for axis in axes)):
        values = dict(zip(axes, combo, strict=True))
        params = _frozen_set(
            _as_hypothesis(
                file.path,
                file.slug,
                file.family,
                file.title,
                dates,
                file.doc_sha256,
                {**file.fixed_params, **values},
            ),
            settings,
        )
        fp = frozen.fingerprint(file.family, params, file.in_sample_start)
        if fp in by_fingerprint:
            raise DuplicateFingerprintError(
                f"{file.path}: variants {by_fingerprint[fp]} and {values} have one fingerprint"
            )
        by_fingerprint[fp] = values
        built.append((registry.params_sha256(params), fp, values, params))
    built.sort(key=lambda item: item[0])
    return [
        Variant(index=i, values=values, frozen_set=params, params_sha256=sha, fingerprint=fp)
        for i, (sha, fp, values, params) in enumerate(built, start=1)
    ]


def read_groups(variants: Sequence[Variant]) -> list[list[Variant]]:
    """`variants` split into read groups: those agreeing on every frozen key outside
    `READ_GROUP_FREE_KEYS`. Groups are ordered by their lowest canonical index and each
    group's members by index."""
    groups: dict[str, list[Variant]] = {}
    for variant in sorted(variants, key=lambda v: v.index):
        shared = {k: v for k, v in variant.frozen_set.items() if k not in READ_GROUP_FREE_KEYS}
        groups.setdefault(_canonical_json(shared), []).append(variant)
    return sorted(groups.values(), key=lambda group: group[0].index)


def variant_slug(sweep_slug: str, sweep_id: int, index: int, n_variants: int) -> str:
    """`<sweep-slug>--r<sweep_id>-v<NNN>`: the 1-based canonical `index` zero-padded to
    the digit count of the sweep's own `n_variants`."""
    if n_variants < 1:
        raise ValueError(f"n_variants must be >= 1, got {n_variants}")
    if not 1 <= index <= n_variants:
        raise ValueError(f"index must be in 1..{n_variants}, got {index}")
    if sweep_id < 0:
        raise ValueError(f"sweep_id must be >= 0, got {sweep_id}")
    width = len(str(n_variants))
    return f"{sweep_slug}--r{sweep_id}-v{index:0{width}d}"
