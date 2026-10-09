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
a grid value off its `lab.axis_lattice` step; two grid values that validate (or fall on
the lattice) to one.
`expand_grid` builds each variant's frozen set exactly as `hypothesis.frozen_params`
builds a hypothesis's (file values over live `Settings`, validated, in JSON form),
refuses a product above `lab.max_variants_per_sweep` and two variants with one
fingerprint, and returns the variants in canonical order (ascending `params_sha256`).

`register` (T104) applies the **store-level** refusals of req 1 before any row is
written, in a sweep's existing family: `oracle` on the real store, the family rules
(holdout, `in_sample_start`, every forbidden-prefix key; `costs.per_side_bps` may only
rise), a fingerprint already registered anywhere, family readiness, the anchor check
against the store's first session and the family rules' own lattice. It then writes the
`sweeps` row, one `hypotheses`, `sweep_variants` and `hypothesis_fingerprints` row per
variant, and leaves the transaction to the caller.

A family with no `family_rules` row and no registered hypothesis is a **new family**
(T104b; Definitions, Family rules): its first sweep fixes the rules. Its parent is
`config.FAMILY_PARENTS`' entry (a file naming another is refused); its holdout may not
overlap any existing non-oracle family's; a child (a parent in the table) must hold out
only after the parent's `holdout.end` and register only once the parent has spent its
holdout or reached its spend cap, and its SR* high-water mark is seeded from the
parent's; a root (`None`) has no parent, its own mark and the overlap rule only. The
`family_rules` row is written once, with the live caps, `min_sharpe_variance_annual`
and lattice, and never read live afterwards. A family with hypotheses but no rules row
is refused. Every function above `register` reads a file and `Settings` only.

**The development boundary** (ADR 0016 point 2; data-foundation plan T142b).
`register` reads `registry.development_boundary` once: a file whose `in_sample_start`
is on or after it, or whose variant has no rebalance session at its cadence on or
before it, is `DevelopmentBoundaryError`, so no default window is ever empty,
and the readiness check's default windows (`_unrun_standalone`,
`lab_registry.family_ready_for_sweep`) end at it. With no boundary row nothing changes.
"""

from __future__ import annotations

import functools
import itertools
import json
import math
import re
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Final, Literal, get_args

import duckdb
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from tradepartner import config
from tradepartner.backtest import frozen, hypothesis, results
from tradepartner.backtest.holdout import Frozen, default_in_sample_window
from tradepartner.backtest.schedule import rebalance_sessions
from tradepartner.backtest.signals import check_anchor_feasible
from tradepartner.config import FORBIDDEN_AXIS_PREFIXES, Settings, get_settings
from tradepartner.store import lab_registry, registry
from tradepartner.store.lab_schema import require_lab

__all__ = [
    "FORBIDDEN_AXIS_PREFIXES",
    "READ_GROUP_FREE_KEYS",
    "AnchorInfeasibleError",
    "AxisNotSweepableError",
    "CadenceAxisStatisticError",
    "DuplicateFingerprintError",
    "DuplicateGridValueError",
    "FamilyLatticeError",
    "FamilyNotReadyError",
    "FamilyRuleError",
    "FingerprintRegisteredError",
    "FixedAndGriddedError",
    "InvalidVariantError",
    "LabBlockError",
    "MissingRequiredKeyError",
    "NewFamilyError",
    "NoFamilyRulesError",
    "OffLatticeError",
    "SelectionStatistic",
    "SweepFile",
    "SweepFileError",
    "SweepLab",
    "SweepRegistration",
    "SweepRegistrationError",
    "TooManyVariantsError",
    "Variant",
    "expand_grid",
    "family_rule_params",
    "parse_sweep_file",
    "read_groups",
    "register",
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
# Float round-off allowed when matching a grid value to its lattice point k x step, as a
# fraction of the step: far below any step a sweep would use and far above binary
# round-off, so 0.1000000001 on a 0.01 step is off the lattice (a numerical tolerance,
# not a research threshold).
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


class InvalidVariantError(SweepFileError):
    """A grid combination `Settings` refuses as a whole (a cross-field rule such as
    `strategy.formation_months > strategy.skip_months`), though each value is valid."""


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
    if statistic == "dsr_excess" and cadence_axis:
        raise CadenceAxisStatisticError(
            f"{path}: selection_statistic dsr_excess is refused with {_CADENCE_KEY} as an axis "
            "(its T differs by cadence); use sharpe_annual_excess_spy or excess_cagr_spy"
        )
    if statistic == "dsr_excess" and retire >= promote:
        raise LabBlockError(
            f"{path}: [lab] retire_below {retire} must be below promote_at_least {promote} "
            "under dsr_excess, so no argmax can both retire and promote"
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


def _frozen_set(
    parsed: hypothesis.HypothesisFile, settings: Settings, values: Mapping[str, Any]
) -> dict[str, Any]:
    try:
        return hypothesis.frozen_params(parsed, settings)
    except hypothesis.HypothesisFileError as exc:
        raise InvalidVariantError(f"{parsed.path}: the variant {dict(values)} {exc}") from exc


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


@functools.cache
def _field_adapter(key: str) -> TypeAdapter[Any]:
    """A validator for one dotted `Settings` key alone: its type and field constraints,
    without the section's cross-field rules, so a value is judged on its own and never
    against another axis's live value. Used for the sections a grid axis lives in
    (`strategy`, `schedule`), which carry no per-field validators."""
    section, _, name = key.partition(".")
    field = Settings.model_fields[section]
    if name:
        model = field.annotation
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            raise SweepFileError(f"{key}: {section} is not a settings section")
        field = model.model_fields[name]
    annotated: Any = (
        Annotated[field.annotation, *field.metadata] if field.metadata else field.annotation
    )
    return TypeAdapter(annotated, config=ConfigDict(allow_inf_nan=False))


def _validated(path: Path, key: str, value: object) -> Any:
    """`value` validated as `key` alone, in the JSON form a frozen set stores."""
    adapter = _field_adapter(key)
    try:
        return adapter.dump_python(adapter.validate_python(value), mode="json")
    except ValidationError as exc:
        raise SweepFileError(f"{path}: {key} = {value!r} fails validation: {exc}") from exc


def _lattice_index(value: float, step: float) -> int | None:
    """The k with value = k x step up to round-off, or None when `value` is off the
    lattice."""
    k = round(value / step)
    return k if abs(value - k * step) <= _LATTICE_TOLERANCE * step else None


def parse_sweep_file(path: Path, settings: Settings | None = None) -> SweepFile:
    """Parse a sweep file and apply every file-level refusal of req 1.

    `settings` supplies `lab.sweepable_keys`, `lab.axis_lattice` and
    `lab.promotion_min_dsr_excess` (default: the live `get_settings()`). Every fixed value
    and grid value is validated against its own field's type and constraints alone, so
    no refusal here depends on another key's live value; a combination `Settings`
    refuses as a whole (a cross-field rule) is `expand_grid`'s `InvalidVariantError`.
    Raises a `SweepFileError` subclass naming the rule broken.
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

    dates = {key: _plain_date(key, flat.get(key), path) for key in _DATE_KEYS}
    # Sections no axis lives in are validated whole, field and section rules included
    # (a guarded `universe.*` value, the holdout's ordering), exactly as a hypothesis
    # file's are; an axis's section is validated field by field here, and as a whole per
    # combination in `expand_grid`, so no refusal turns on another axis's live value.
    axis_sections = {axis.partition(".")[0] for axis in grid_lists}
    for key, value in fixed.items():
        if key.partition(".")[0] in axis_sections:
            _validated(path, key, value)
    untouched = {k: v for k, v in fixed.items() if k.partition(".")[0] not in axis_sections}
    try:
        hypothesis.frozen_params(
            _as_hypothesis(path, slug, family, title, dates, doc_sha256, untouched), settings
        )
    except hypothesis.HypothesisFileError as exc:
        raise SweepFileError(f"{path}: the fixed block's {exc}") from exc
    if dates["in_sample_start"] >= dates["holdout.start"]:
        raise SweepFileError(
            f"{path}: in_sample_start ({dates['in_sample_start']}) must be before "
            f"holdout.start ({dates['holdout.start']})"
        )

    lab = _parse_lab(lab_raw, path, settings, cadence_axis=_CADENCE_KEY in grid_lists)

    grid: dict[str, tuple[Any, ...]] = {}
    for axis, values in grid_lists.items():
        step = settings.lab.axis_lattice.get(axis)
        if step is not None and step <= 0:
            raise SweepFileError(
                f"{path}: lab.axis_lattice step for {axis} must be > 0, got {step}"
            )
        validated: list[Any] = []
        seen: dict[str, object] = {}
        for value in values:
            json_value = _validated(path, axis, value)
            key = _canonical_json(json_value)
            numeric = isinstance(json_value, int | float) and not isinstance(json_value, bool)
            if step is not None and numeric:
                index = _lattice_index(float(json_value), step)
                if index is None:
                    raise OffLatticeError(
                        f"{path}: grid axis {axis} value {value!r} is not a multiple of its "
                        f"lab.axis_lattice step {step}"
                    )
                # Stored as the lattice point itself, so round-off never yields a second
                # fingerprint for one point.
                json_value = _validated(path, axis, round(index * step, 12))
                key = f"lattice:{index}"
                if key in seen:
                    raise DuplicateGridValueError(
                        f"{path}: grid axis {axis} values {seen[key]!r} and {value!r} fall on "
                        f"one lab.axis_lattice point ({json_value!r}, step {step})"
                    )
            if key in seen:
                raise DuplicateGridValueError(
                    f"{path}: grid axis {axis} values {seen[key]!r} and {value!r} are one value "
                    f"({json_value!r}) after Settings validation"
                )
            seen[key] = value
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
    `settings`, built by `hypothesis.frozen_params` as for a standalone file. Pass the
    `settings` `parse_sweep_file` checked the file against. Raises
    `TooManyVariantsError` above `lab.max_variants_per_sweep` (before any set is built),
    `InvalidVariantError` for a combination `Settings` refuses, and
    `DuplicateFingerprintError` when two variants share a fingerprint.
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
            values,
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


# --- registration (T104) ---------------------------------------------------------


class SweepRegistrationError(ValueError):
    """A sweep file that breaks a store-level rule of req 1. Raised before any row is
    written."""


class NoFamilyRulesError(SweepRegistrationError):
    """The sweep's family has registered hypotheses but no `family_rules` row: a store
    the lab migration did not populate. A family with neither is a new family."""


class NewFamilyError(SweepRegistrationError):
    """A new family's first registration that breaks a lineage rule (Definitions,
    Family rules): its family has no `FAMILY_PARENTS` entry (oracle aside), its holdout
    overlaps an existing non-oracle family's, or, for a
    child, starts on or before the parent's `holdout.end`, or the parent has neither
    a holdout spend nor a reached spend cap (or no rules at all)."""


class FamilyRuleError(SweepRegistrationError):
    """Req 1(a): the file's holdout, `in_sample_start` or a forbidden-prefix value
    differs from the family rules (`costs.per_side_bps` may only rise)."""


class FingerprintRegisteredError(SweepRegistrationError):
    """Req 1(b): a variant's fingerprint is already registered (a standalone
    hypothesis or a variant of any sweep, in any family)."""


class FamilyNotReadyError(SweepRegistrationError):
    """Req 1(c): a standalone hypothesis of the family has no `ok`, non-synthetic,
    in-sample trial over its default window yet."""


class AnchorInfeasibleError(SweepRegistrationError):
    """Req 1(d): a variant whose signal anchor can fall after T, or whose formation
    anchor at the window's first rebalance precedes the store's first bar session."""


class FamilyLatticeError(SweepRegistrationError):
    """Req 1(e): a grid value off its axis's step in the family rules' lattice."""


class DevelopmentBoundaryError(SweepRegistrationError):
    """ADR 0016 point 2: the file's `in_sample_start` is on or after the development
    boundary, so its default in-sample window would be empty."""


@dataclass(frozen=True)
class SweepRegistration:
    """A sweep registration: its `sweeps` row, its variants' `sweep_variants` rows and
    `hypotheses` records in canonical order, and whether this call wrote them (`False`
    when an unchanged file returned the existing registration)."""

    sweep: lab_registry.SweepRecord
    variants: tuple[lab_registry.SweepVariant, ...]
    hypotheses: tuple[registry.HypothesisRecord, ...]
    created: bool


def family_rule_params(params: Mapping[str, Any]) -> dict[str, Any]:
    """The keys of a frozen set the family rules fix as values (Definitions, Family
    rules): every key under `FORBIDDEN_AXIS_PREFIXES`, `holdout.*` included, as the lab
    migration writes them for an existing family."""
    return {key: value for key, value in params.items() if key.startswith(FORBIDDEN_AXIS_PREFIXES)}


def _rule_differences(
    file: SweepFile, params: Mapping[str, Any], rules: lab_registry.FamilyRules
) -> list[str]:
    """The family rules `file` and one variant's frozen set `params` break, by name."""
    differences = [
        name
        for name, ours, theirs in (
            ("holdout.start", file.holdout_start, rules.holdout_start),
            ("holdout.end", file.holdout_end, rules.holdout_end),
            ("in_sample_start", file.in_sample_start, rules.in_sample_start),
        )
        if ours != theirs
    ]
    if file.parent_family is not None and file.parent_family != rules.parent_family:
        differences.append(f"parent_family (the family's is {rules.parent_family!r})")
    for key, rule in sorted(rules.fixed_params.items()):
        value = params.get(key)
        if key == registry.BASE_COST_KEY:
            if isinstance(value, int | float) and not isinstance(value, bool) and value >= rule:
                continue
            differences.append(f"{key} (lower than the family's {rule})")
        elif key not in params or not frozen.is_default(value, rule):
            differences.append(key)
    table_defaults = {key: default for key, default, _version in frozen.FROZEN_KEY_DEFAULTS}
    for key, value in sorted(family_rule_params(params).items()):
        if key in rules.fixed_params:
            continue
        # A frozen key that landed after the rules were written, at its behaviour-
        # preserving default, is the rules' behaviour; any other value is not.
        if key in table_defaults and frozen.is_default(value, table_defaults[key]):
            continue
        differences.append(f"{key} (not fixed by the family rules)")
    return differences


def _unrun_standalone(
    conn: duckdb.DuckDBPyConnection, family: str, boundary: date | None = None
) -> list[str]:
    """The slugs of `family`'s standalone hypotheses (latest registration per slug, no
    sweep's variant) without an `ok`, non-synthetic, in-sample trial over the default
    window under the development `boundary`: the names refusal 1(c) gives.
    `lab_registry.family_ready_for_sweep` decides."""
    rows = conn.execute(
        "SELECT MAX(hypothesis_id) FROM hypotheses WHERE family = ? AND hypothesis_id NOT IN "
        "(SELECT hypothesis_id FROM sweep_variants) GROUP BY slug ORDER BY 1",
        [family],
    ).fetchall()
    names: list[str] = []
    for (hypothesis_id,) in rows:
        record = registry.get_hypothesis_by_id(conn, hypothesis_id)
        cadence = frozen.frozen_values(record)[lab_registry.CADENCE_KEY]
        window = default_in_sample_window(Frozen.from_hypothesis(record), cadence, boundary)
        found = conn.execute(
            "SELECT 1 FROM trials t JOIN trial_results r USING (trial_id) "
            "WHERE t.hypothesis_id = ? AND t.kind = 'in_sample' AND NOT t.synthetic "
            "AND r.status = 'ok' AND t.start_session = ? AND t.end_session = ?",
            [hypothesis_id, window.start, window.end],
        ).fetchone()
        if found is None:
            names.append(record.slug)
    return names


def _anchor_refusal(file: SweepFile, variant: Variant, first_session: date | None) -> str | None:
    """Refusal 1(d) for one variant, or None. Only a family whose signal reads the
    `strategy` anchors (momentum, oracle) has anchors to check."""
    params = variant.frozen_set
    if "strategy.formation_months" not in params:
        return None
    if first_session is None:
        return "the store has no bar sessions, so no formation anchor can be read"
    cadence = params[lab_registry.CADENCE_KEY]
    sessions = rebalance_sessions(
        file.in_sample_start, file.holdout_start - timedelta(days=1), cadence
    )
    if not sessions:
        return (
            f"no rebalance session at {cadence} between in_sample_start "
            f"{file.in_sample_start} and holdout.start {file.holdout_start}"
        )
    return check_anchor_feasible(
        params["strategy.formation_months"],
        params["strategy.skip_months"],
        params["schedule.signal_anchor"],
        cadence,
        sessions[0],
        first_session,
    )


def _new_family_seed(
    conn: duckdb.DuckDBPyConnection, file: SweepFile, path: Path
) -> tuple[str | None, float | None]:
    """The lineage refusals of a new family's first registration, before any row is
    written, and its `(parent_family, sr_star_seed_annual)`: the parent from
    `config.FAMILY_PARENTS` (read at call time) and, for a child, the parent's SR*
    high-water mark today (N from `results.family_n`, V on the excess basis from
    `registry.family_sharpes`, floored by the parent's rules); a root's seed is None."""
    parents: dict[str, str | None] = {
        str(family): parent for family, parent in config.FAMILY_PARENTS.items()
    }
    if file.family not in parents and file.family != registry.ORACLE_FAMILY:
        raise NewFamilyError(
            f"{path}: family {file.family!r} has no entry in FAMILY_PARENTS (a parent or "
            "None), so its lineage is unknown"
        )
    parent = parents.get(file.family)
    if file.parent_family is not None and file.parent_family != parent:
        raise FamilyRuleError(
            f"{path}: parent_family {file.parent_family!r} differs from family "
            f"{file.family!r}'s entry in FAMILY_PARENTS ({parent!r})"
        )
    others = conn.execute(
        "SELECT family, holdout_start, holdout_end FROM family_rules WHERE family <> ? "
        "ORDER BY family",
        [registry.ORACLE_FAMILY],
    ).fetchall()
    for other, start, end in others:
        if file.holdout_start <= end and start <= file.holdout_end:
            raise NewFamilyError(
                f"{path}: the holdout [{file.holdout_start}, {file.holdout_end}] overlaps "
                f"family {other!r}'s [{start}, {end}]"
            )
    if parent is None:
        return None, None
    parent_rules = lab_registry.family_rules(conn, parent)
    if parent_rules is None:
        raise NewFamilyError(
            f"{path}: family {file.family!r}'s parent {parent!r} has no family rules, so it "
            "has neither a holdout spend nor a reached spend cap"
        )
    if file.holdout_start <= parent_rules.holdout_end:
        raise NewFamilyError(
            f"{path}: holdout.start {file.holdout_start} is not after parent family "
            f"{parent!r}'s holdout.end {parent_rules.holdout_end}"
        )
    if not lab_registry.family_holdout_spent_or_capped(conn, parent):
        raise NewFamilyError(
            f"{path}: parent family {parent!r} has neither a holdout spend nor reached its "
            f"spend cap ({parent_rules.max_family_holdout_spends})"
        )
    seed = lab_registry.family_sr_star_high_water_mark(
        conn,
        parent,
        n_trials_today=results.family_n(conn, parent),
        sharpe_variance_annual_today=registry.family_sharpes(conn, parent).variance("excess_spy"),
    )
    return parent, seed


def _existing_registration(
    conn: duckdb.DuckDBPyConnection, file: SweepFile, variants: Sequence[Variant]
) -> SweepRegistration | None:
    """The slug's latest registration when it is this file (doc hash) with these
    canonical frozen sets."""
    latest = lab_registry.sweep_by_slug(conn, file.slug)
    if latest is None or latest.doc_sha256 != file.doc_sha256:
        return None
    rows = lab_registry.sweep_variants(conn, latest.sweep_id)
    records = [registry.get_hypothesis_by_id(conn, row.hypothesis_id) for row in rows]
    # Canonical frozen sets, never raw hashes (Definitions, Fingerprint): a stored set
    # that lacks a table key at its default is the same set.
    stored = sorted(
        _canonical_json(frozen.canonical_frozen_set(frozen.frozen_values(r), r.family))
        for r in records
    )
    ours = sorted(
        _canonical_json(frozen.canonical_frozen_set(v.frozen_set, file.family)) for v in variants
    )
    if stored != ours:
        return None
    return SweepRegistration(
        sweep=latest, variants=tuple(rows), hypotheses=tuple(records), created=False
    )


def register(
    conn: duckdb.DuckDBPyConnection,
    file: Path,
    settings: Settings | None = None,
    *,
    registered_by: str,
) -> SweepRegistration:
    """Register the sweep in `file` (req 1) and return it.

    `LabNotInitialised` first, on a store without the lab tables. Then the file-level
    refusals (`parse_sweep_file`, `expand_grid`) and every store-level one, before any
    row is written: `oracle` on the real store (`registry.RealStoreRefused`), a family
    with hypotheses but no rules (`NoFamilyRulesError`), a new family's lineage
    (`FamilyRuleError` for another `parent_family`, `NewFamilyError`), (a)
    `FamilyRuleError` in an existing family, (b)
    `FingerprintRegisteredError` naming the registration that holds it, (c)
    `FamilyNotReadyError` naming the unrun hypothesis, (d) `AnchorInfeasibleError`
    against `lab_registry.first_session`, (e) `FamilyLatticeError`. An unchanged file
    with unchanged frozen sets returns the slug's latest registration and writes
    nothing; a changed file is a new registration, so its unchanged variants are
    refused by (b). Writes the `sweeps` row (copying the lab caps), then per variant in
    canonical order its `hypotheses` row through `registry.register_hypothesis` (the
    sweep file as `doc_path`), one `sweep_variants` and one `hypothesis_fingerprints`
    row; for a new family, last, its one `family_rules` row (first hypothesis: the
    canonical first variant; its fixed values: `family_rule_params` of that variant's
    frozen set). The transaction is the caller's.
    """
    require_lab(conn)
    settings = settings if settings is not None else get_settings()
    parsed = parse_sweep_file(file, settings)
    variants = expand_grid(parsed, settings)
    if parsed.family == registry.ORACLE_FAMILY and registry._is_real_store(conn, settings):
        raise registry.RealStoreRefused(
            f"{file}: family {registry.ORACLE_FAMILY!r} is refused on the real store"
        )
    existing = _existing_registration(conn, parsed, variants)
    if existing is not None:
        return existing
    boundary = registry.boundary_date(conn)
    if boundary is not None and parsed.in_sample_start >= boundary:
        raise DevelopmentBoundaryError(
            f"{file}: in_sample_start {parsed.in_sample_start} is on or after the "
            f"development boundary {boundary}: no in-sample session would remain"
        )
    if boundary is not None:
        for variant in variants:
            cadence = variant.frozen_set[lab_registry.CADENCE_KEY]
            if not rebalance_sessions(parsed.in_sample_start, boundary, cadence):
                raise DevelopmentBoundaryError(
                    f"{file}: variant {variant.values} has no {cadence} rebalance session "
                    f"between in_sample_start {parsed.in_sample_start} and the development "
                    f"boundary {boundary}"
                )

    rules = lab_registry.family_rules(conn, parsed.family)
    new_family: tuple[str | None, float | None] | None = None
    if rules is None:
        registered = conn.execute(
            "SELECT 1 FROM hypotheses WHERE family = ? LIMIT 1", [parsed.family]
        ).fetchone()
        if registered is not None:
            raise NoFamilyRulesError(
                f"{file}: family {parsed.family!r} has registered hypotheses but no family "
                "rules; the lab migration writes them"
            )
        new_family = _new_family_seed(conn, parsed, file)
    else:
        broken = sorted(
            {d for v in variants for d in _rule_differences(parsed, v.frozen_set, rules)}
        )
        if broken:
            raise FamilyRuleError(
                f"{file}: differs from family {parsed.family!r}'s rules: {', '.join(broken)}"
            )
    for variant in variants:
        holder = lab_registry.fingerprint_registered(conn, variant.fingerprint)
        if holder is not None:
            raise FingerprintRegisteredError(
                f"{file}: variant {variant.values} has the fingerprint of registered "
                f"hypothesis {holder.slug!r} (id {holder.hypothesis_id}); the existing "
                "registration is the record"
            )
    if not lab_registry.family_ready_for_sweep(conn, parsed.family, boundary):
        unrun = _unrun_standalone(conn, parsed.family, boundary)
        raise FamilyNotReadyError(
            f"{file}: family {parsed.family!r} is not ready for a sweep: "
            f"{', '.join(repr(s) for s in unrun)} has no ok, non-synthetic, in-sample "
            "trial over its default window yet"
        )
    first = lab_registry.first_session(conn)
    for variant in variants:
        reason = _anchor_refusal(parsed, variant, first)
        if reason is not None:
            raise AnchorInfeasibleError(f"{file}: variant {variant.values}: {reason}")
    # A new family's lattice is the live one `parse_sweep_file` already applied.
    lattice = rules.axis_lattice if rules is not None else {}
    for axis, values in parsed.grid.items():
        step = lattice.get(axis)
        if step is None:
            continue
        if not step > 0:
            raise FamilyLatticeError(
                f"{file}: the family rules' lattice step for {axis} is {step}, not > 0"
            )
        for value in values:
            numeric = isinstance(value, int | float) and not isinstance(value, bool)
            if numeric and _lattice_index(float(value), step) is None:
                raise FamilyLatticeError(
                    f"{file}: grid axis {axis} value {value!r} is not a multiple of the "
                    f"family rules' lattice step {step}"
                )

    sweep = lab_registry.register_sweep(
        conn,
        slug=parsed.slug,
        family=parsed.family,
        title=parsed.title,
        doc_path=file.as_posix(),
        doc_sha256=parsed.doc_sha256,
        grid={axis: list(values) for axis, values in parsed.grid.items()},
        n_variants=len(variants),
        selection_statistic=parsed.lab.selection_statistic,
        expected_excess_cagr_spy_pp=parsed.lab.expected_excess_cagr_spy_pp,
        expected_range_pp=parsed.lab.expected_range_pp,
        promote_at_least=parsed.lab.promote_at_least,
        retire_below=parsed.lab.retire_below,
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by=registered_by,
        settings=settings,
    )
    records: list[registry.HypothesisRecord] = []
    for variant in variants:
        record = registry.register_hypothesis(
            conn,
            slug=variant_slug(parsed.slug, sweep.sweep_id, variant.index, len(variants)),
            family=parsed.family,
            title=f"{parsed.title} (variant {variant.index})",
            doc_path=file.as_posix(),
            doc_sha256=parsed.doc_sha256,
            params=variant.frozen_set,
            in_sample_start=parsed.in_sample_start,
            holdout_start=parsed.holdout_start,
            holdout_end=parsed.holdout_end,
            registered_by=registered_by,
            settings=settings,
        )
        lab_registry.write_sweep_variant(
            conn,
            sweep_id=sweep.sweep_id,
            variant_index=variant.index,
            hypothesis_id=record.hypothesis_id,
            fingerprint=variant.fingerprint,
            variant_params=variant.values,
        )
        lab_registry.write_fingerprint(conn, record.hypothesis_id, variant.fingerprint)
        records.append(record)
    if new_family is not None:
        parent, seed = new_family
        lab_registry.write_family_rules(
            conn,
            family=parsed.family,
            first_hypothesis_id=records[0].hypothesis_id,
            parent_family=parent,
            holdout_start=parsed.holdout_start,
            holdout_end=parsed.holdout_end,
            in_sample_start=parsed.in_sample_start,
            fixed_params=family_rule_params(variants[0].frozen_set),
            sr_star_seed_annual=seed,
            settings=settings,
        )
    return SweepRegistration(
        sweep=sweep,
        variants=tuple(lab_registry.sweep_variants(conn, sweep.sweep_id)),
        hypotheses=tuple(records),
        created=True,
    )
