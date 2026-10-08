"""Hypothesis files and pre-registration (Phase 3 spec req 10; plan T35).

A hypothesis is a Markdown file under `docs/hypotheses/<slug>.md` (template:
`docs/templates/hypothesis.md`). The registry reads one part of it: the single
fenced block whose info string is `toml hypothesis`. Its top level names `slug`
(equal to the file name), `family`, `title` and `in_sample_start`; its tables name
config keys (`[holdout]`, `[strategy]`, `[costs]`, and any other frozen key the
file wants to pin). Every other part of the file is prose, but it is hashed too.

**The file must name** `in_sample_start`, `holdout.start`, `holdout.end`, every
`costs.*` key and every key of its family's signal section (`strategy.*` for
`momentum`, `profitability.*` for `profitability`; `FAMILIES[family].sections`), or it is
refused; the holdout never comes from live `Settings`. A key outside the frozen list
below is refused rather than ignored, and so is a key of another family's signal
section (`frozen.inert_sections`).

**The frozen set** is every key of `strategy`, `profitability`, `schedule`, `universe`,
`costs`, `backtest`, `adjust`, `master`, `gap`, `holdout` and `metrics`, plus
`execution.fill_price`, `benchmarks` and `alpaca.historical_feed`, less the family's
inert sections: a registration stores only its own family's signal section, never
reading the inert one from live `Settings` (backtest spec amendment #720), and a run
keeps the live value of a section its family never reads. File values win for the
keys the file names; the live `Settings` fill the rest at registration. The merged values are
validated through `Settings` and stored in their JSON form (floats for float keys,
ISO strings for dates), so `15` and `15.0` in a file hash the same.

**A run reads the frozen values back** with `load_frozen` from the slug's latest
registration. It passes every setting explicitly (the frozen values over a full dump of
the live `Settings`), and explicit values take priority over the environment, so no
environment variable can move a registered hypothesis's holdout or threshold. Because
runs take the latest registration, `register` refuses to re-register a file whose
record is an older one (a file reverted to an earlier version): what it would report
is not what would run. `load_frozen` reads the stored set through
`frozen.frozen_values`, which overlays the `FROZEN_KEY_DEFAULTS` default for every
table key the set lacks (a registration from before `schedule.*` reads `month_end`
for both keys, at its stored hash). It refuses a stored parameter set whose hash no
longer matches, or whose keys still differ from today's frozen list after that
overlay (a drift the table does not cover), rather than letting a live value in
silently.

**Registration after the lab** (strategy-lab spec reqs 1, 4 and 5; plan task T104c).
In every state `register` returns the existing record for an unchanged slug, doc hash
and canonical frozen set, newest first (never calling `registry.register_hypothesis`,
so a registration from before a `FROZEN_KEY_DEFAULTS` key landed is still the record)
and refuses a prose-only edit (the slug's latest canonical frozen set, hence its
fingerprint, under a new doc hash; a change to a frozen key outside the fingerprint is
a new frozen set, which a lab store refuses as a standalone file anyway). **When
`lab_schema.is_lab_initialised`** it refuses every other standalone file unless the
caller registers it as a promoted file (`promotion_of`, the variant it promotes,
which `sweep promote` names in the `promotion` decision it appends next): a new
hypothesis is written as a one-value sweep. A promoted file must carry its variant's
fingerprint, its family must have family rules (a new family's rules are written by
`sweep.register` only), it must keep them (`costs.per_side_bps` may only rise) and its
signal anchor must be feasible against the store's first session. **When the lab is
not initialised** none of those lab refusals applies: the Phase 3 rules, so H1 and B3
register on a pre-lab store as before.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Final

import duckdb
from pydantic import BaseModel, ValidationError

from tradepartner.backtest import frozen
from tradepartner.backtest.schedule import rebalance_sessions
from tradepartner.backtest.signals import check_anchor_feasible
from tradepartner.config import (
    FAMILIES,
    FORBIDDEN_AXIS_PREFIXES,
    Settings,
    get_settings,
    render_validation_errors,
)
from tradepartner.store import lab_registry, registry
from tradepartner.store.lab_schema import is_lab_initialised


def _family_frozen_sections() -> tuple[str, ...]:
    """Every section any family lists in `FAMILIES` (ADR 0014 point 2), in dict order
    without duplicates: today `("strategy", "profitability")`."""
    seen: dict[str, None] = {}
    for spec in FAMILIES.values():
        for section in spec.sections:
            seen.setdefault(section, None)
    return tuple(seen)


#: Settings sections frozen whole (spec req 10). The family part derives from `FAMILIES`
#: (ADR 0014 point 2, T128); the fixed sections are listed as today.
FROZEN_SECTIONS: Final = (
    *_family_frozen_sections(),
    "schedule",
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
#: Keys the file must name outside those sections.
REQUIRED_SINGLE_KEYS: Final = ("holdout.start", "holdout.end")
#: Top-level keys of the parameter block that are not config keys.
METADATA_KEYS: Final = ("slug", "family", "title", "in_sample_start")

_BLOCK_OPEN: Final = re.compile(r"^```toml hypothesis[ \t]*$", re.MULTILINE)
_DATE_KEYS: Final = ("in_sample_start", "holdout.start", "holdout.end")


class HypothesisFileError(ValueError):
    """A hypothesis file, or its stored parameters, that breaks a req 10 rule."""


class ProseOnlyEditError(HypothesisFileError):
    """A registered file edited outside its frozen values: same slug and fingerprint,
    new doc hash (strategy-lab spec, "Family rules" criterion). In every state."""


class LabRegistrationError(HypothesisFileError):
    """A standalone file refused after the lab (`is_lab_initialised`): not a promoted
    file, a new family's first file, a promoted file off its variant's fingerprint or
    its family rules, or with an infeasible signal anchor."""


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


def family_frozen_keys(family: str) -> tuple[str, ...]:
    """`frozen_keys()` less `family`'s inert sections: what a registration stores."""
    inert = frozen.inert_sections(family)
    return tuple(k for k in frozen_keys() if k.partition(".")[0] not in inert)


def required_keys(family: str) -> frozenset[str]:
    """The config keys a `family` hypothesis file must name (besides `in_sample_start`):
    every key of its listed sections (`FAMILIES[family].sections`) plus every `costs.*`
    key. Raises `KeyError` for an unlisted family (ADR 0014 point 2: the momentum
    fallback goes)."""
    keys = set(REQUIRED_SINGLE_KEYS)
    for section in (*FAMILIES[family].sections, "costs"):  # type: ignore[index]
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
    family_value = flat.get("family")
    if not isinstance(family_value, str) or not family_value.strip():
        raise HypothesisFileError(f"{path}: family must be a non-empty string")
    family = family_value
    try:
        inert_set = frozen.inert_sections(family)
    except KeyError as exc:
        raise HypothesisFileError(f"{path}: family {family!r} is not listed in FAMILIES") from exc
    inert = sorted(k for k in flat if k.partition(".")[0] in inert_set)
    if inert:
        raise HypothesisFileError(
            f"{path}: keys of another family's signal section are refused for "
            f"family {family!r}: {', '.join(inert)}"
        )
    required = required_keys(family) | {"slug", "family", "title", "in_sample_start"}
    missing = sorted(required - set(flat))
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
    """`settings` with `params` (dotted keys) set and validated. Every field is passed
    explicitly, so the environment and `.env` (which `Settings.__init__` still reads)
    cannot override any of them."""
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
        # `Settings` hides input values in its errors (#1093). `params` are frozen
        # values (a file's, which may name frozen keys only, or a registration's
        # stored ones), never secrets, so a location at or under one of them shows
        # its value; any other location (a section rule, the live settings) does not.
        keys = set(params)

        def frozen(key: str) -> bool:
            parts = key.split(".")
            return any(".".join(parts[:i]) in keys for i in range(1, len(parts) + 1))

        detail = render_validation_errors(exc, show_input=frozen)
        raise HypothesisFileError(f"frozen values fail validation: {detail}") from exc


def frozen_params_of(settings: Settings, *, family: str) -> dict[str, Any]:
    """The frozen keys of `settings` a `family` registration stores, dotted, in their
    JSON form (no inert section). Raises `KeyError` for an unlisted family (ADR 0014
    point 2: the momentum fallback goes)."""
    dumped = settings.model_dump(mode="json")
    params: dict[str, Any] = {}
    for key in family_frozen_keys(family):
        section, _, field = key.partition(".")
        params[key] = dumped[section][field] if field else dumped[section]
    return params


def frozen_hash_matches(settings: Settings, stored_sha256: str, *, family: str) -> bool:
    """True when the frozen keys of `settings` are the `family` registration whose
    stored hash is `stored_sha256`: written out in full, or, for a registration stored
    before a suffix of `FROZEN_KEY_DEFAULTS` landed, with those table keys (at their
    defaults) left out, which is exactly what `frozen.frozen_values` overlaid when
    `load_frozen` built it. The family's inert sections take no part."""
    params = frozen_params_of(settings, family=family)
    table = [
        (key, default) for key, default, _version in frozen.FROZEN_KEY_DEFAULTS if key in params
    ]
    for i in range(len(table), -1, -1):
        later = table[i:]
        if not all(frozen.is_default(params.get(key), default) for key, default in later):
            continue
        dropped = {key for key, _default in later}
        stored = {k: v for k, v in params.items() if k not in dropped}
        if registry.params_sha256(stored) == stored_sha256:
            return True
    return False


def frozen_params(parsed: HypothesisFile, settings: Settings) -> dict[str, Any]:
    """The frozen set: the file's values over `settings` for every frozen key."""
    return frozen_params_of(_overlay(settings, parsed.file_params), family=parsed.family)


def _check_latest(
    conn: duckdb.DuckDBPyConnection, path: Path, record: registry.HypothesisRecord
) -> registry.HypothesisRecord:
    latest = registry.get_hypothesis(conn, record.slug)
    if latest.hypothesis_id != record.hypothesis_id:
        raise HypothesisFileError(
            f"{path}: matches registration {record.hypothesis_id} of {record.slug!r}, "
            f"which is not the latest ({latest.hypothesis_id}); runs use the latest. "
            "Change the file (a new hypothesis) or use a new slug"
        )
    return record


def _slug_registrations(
    conn: duckdb.DuckDBPyConnection, slug: str
) -> list[registry.HypothesisRecord]:
    return [
        registry.get_hypothesis_by_id(conn, hypothesis_id)
        for (hypothesis_id,) in conn.execute(
            "SELECT hypothesis_id FROM hypotheses WHERE slug = ? ORDER BY hypothesis_id", [slug]
        ).fetchall()
    ]


def _rule_differences(
    parsed: HypothesisFile, params: Mapping[str, Any], rules: lab_registry.FamilyRules
) -> list[str]:
    """The family rules `parsed` (frozen set `params`) breaks, by name: the window, and
    every forbidden-prefix value (`costs.per_side_bps` may only rise; a frozen key the
    rules lack is accepted at its `FROZEN_KEY_DEFAULTS` default)."""
    differences = [
        name
        for name, ours, theirs in (
            ("holdout.start", parsed.holdout_start, rules.holdout_start),
            ("holdout.end", parsed.holdout_end, rules.holdout_end),
            ("in_sample_start", parsed.in_sample_start, rules.in_sample_start),
        )
        if ours != theirs
    ]
    for key, rule in sorted(rules.fixed_params.items()):
        value = params.get(key)
        if key == registry.BASE_COST_KEY:
            if isinstance(value, int | float) and not isinstance(value, bool) and value >= rule:
                continue
            differences.append(f"{key} (lower than the family's {rule})")
        elif key not in params or not frozen.is_default(value, rule):
            differences.append(key)
    defaults = {key: default for key, default, _version in frozen.FROZEN_KEY_DEFAULTS}
    differences += sorted(
        f"{key} (not fixed by the family rules)"
        for key, value in params.items()
        if key.startswith(FORBIDDEN_AXIS_PREFIXES)
        and key not in rules.fixed_params
        and not (key in defaults and frozen.is_default(value, defaults[key]))
    )
    return differences


def _anchor_refusal(
    conn: duckdb.DuckDBPyConnection, parsed: HypothesisFile, params: Mapping[str, Any]
) -> str | None:
    """Req 1(d) for a promoted file, or None. Only a family whose signal reads the
    `strategy` anchors has anchors to check."""
    if "strategy.formation_months" not in params:
        return None
    first = lab_registry.first_session(conn)
    if first is None:
        return "the store has no bar sessions, so no formation anchor can be read"
    cadence = params[lab_registry.CADENCE_KEY]
    sessions = rebalance_sessions(
        parsed.in_sample_start, parsed.holdout_start - timedelta(days=1), cadence
    )
    if not sessions:
        return (
            f"no rebalance session at {cadence} between in_sample_start "
            f"{parsed.in_sample_start} and holdout.start {parsed.holdout_start}"
        )
    return check_anchor_feasible(
        params["strategy.formation_months"],
        params["strategy.skip_months"],
        params["schedule.signal_anchor"],
        cadence,
        sessions[0],
        first,
    )


def _lab_refusal(
    conn: duckdb.DuckDBPyConnection,
    path: Path,
    parsed: HypothesisFile,
    params: Mapping[str, Any],
    fingerprint: str,
    promotion_of: int | None,
) -> None:
    """Every lab refusal of a new standalone registration (module docstring)."""
    if promotion_of is None:
        raise LabRegistrationError(
            f"{path}: after the strategy lab a standalone file is registered only as a "
            "promoted file (`sweep promote`); write it as a one-value sweep "
            "(docs/templates/sweep.md), run it and promote it"
        )
    row = conn.execute(
        "SELECT fingerprint FROM sweep_variants WHERE hypothesis_id = ?", [promotion_of]
    ).fetchone()
    if row is None:
        raise LabRegistrationError(f"{path}: promotion_of {promotion_of} is not a sweep variant")
    if row[0] != fingerprint:
        raise LabRegistrationError(
            f"{path}: its fingerprint differs from variant {promotion_of}'s, which it promotes"
        )
    rules = lab_registry.family_rules(conn, parsed.family)
    if rules is None:
        raise LabRegistrationError(
            f"{path}: family {parsed.family!r} has no family rules; a new family's first "
            "registration is a sweep (`sweep register`)"
        )
    broken = _rule_differences(parsed, params, rules)
    if broken:
        raise LabRegistrationError(
            f"{path}: differs from family {parsed.family!r}'s rules: {', '.join(broken)}"
        )
    reason = _anchor_refusal(conn, parsed, params)
    if reason is not None:
        raise LabRegistrationError(f"{path}: {reason}")


def register(
    conn: duckdb.DuckDBPyConnection,
    path: Path,
    *,
    registered_by: str,
    settings: Settings | None = None,
    promotion_of: int | None = None,
) -> registry.HypothesisRecord:
    """Register the hypothesis in `path` through `store.registry` and return its record.

    In every state: an unchanged file (same slug and doc hash) whose canonical frozen
    set equals a stored registration's returns that record without writing, newest
    first; a prose-only edit (the slug's latest frozen set, so its fingerprint, with a
    new doc hash) raises `ProseOnlyEditError`; a changed frozen set is a new
    hypothesis. A family outside `hypotheses.families` is refused (`RegistryError`).
    Refuses a file whose record is not the slug's latest registration, since
    `load_frozen` would run the latest one instead. When the lab is initialised, a new
    registration must be a promoted file: `promotion_of` names the sweep variant it
    promotes (`sweep promote` passes it and appends the `promotion` decision naming
    the returned record); otherwise, or when it breaks a lab rule,
    `LabRegistrationError` (module docstring). `promotion_of` is ignored on a store
    without the lab tables.
    """
    settings = settings if settings is not None else get_settings()
    parsed = parse_file(path)
    params = frozen_params(parsed, settings)
    canonical = frozen.canonical_frozen_set(params, parsed.family)
    fingerprint = frozen.fingerprint(parsed.family, params, parsed.in_sample_start)
    if parsed.family not in settings.hypotheses.families:
        raise registry.RegistryError(
            f"family {parsed.family!r} is not in hypotheses.families {settings.hypotheses.families}"
        )
    window = (parsed.in_sample_start, parsed.holdout_start, parsed.holdout_end)

    def same_set(existing: registry.HypothesisRecord) -> bool:
        return (
            existing.in_sample_start,
            existing.holdout_start,
            existing.holdout_end,
        ) == window and frozen.canonical_frozen_set(
            frozen.frozen_values(existing), existing.family
        ) == canonical

    # Newest first: a file stored twice (before and after a table key landed) is its
    # latest registration, the one runs use.
    registrations = _slug_registrations(conn, parsed.slug)[::-1]
    for existing in registrations:
        if existing.doc_sha256 == parsed.doc_sha256 and same_set(existing):
            return _check_latest(conn, path, existing)
    if registrations and same_set(registrations[0]):
        raise ProseOnlyEditError(
            f"{path}: a prose-only edit of registration {registrations[0].hypothesis_id} of "
            f"{parsed.slug!r} (same frozen set and fingerprint, new doc hash) is refused; "
            "the registered file is the record"
        )
    if is_lab_initialised(conn):
        _lab_refusal(conn, path, parsed, params, fingerprint, promotion_of)
    record = registry.register_hypothesis(
        conn,
        slug=parsed.slug,
        family=parsed.family,
        title=parsed.title,
        doc_path=path.as_posix(),
        doc_sha256=parsed.doc_sha256,
        params=params,
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by=registered_by,
        settings=settings,
    )
    return _check_latest(conn, path, record)


def load_frozen(
    conn: duckdb.DuckDBPyConnection, slug: str, *, settings: Settings | None = None
) -> Settings:
    """`Settings` for a run of `slug`'s latest registration: its frozen values (read
    through `frozen.frozen_values`) over the live `settings` (default: loaded config)
    for every other key, the family's inert sections included.
    `UnknownHypothesis` for an unregistered slug."""
    record = registry.get_hypothesis(conn, slug)
    if registry.params_sha256(record.params) != record.params_sha256:
        raise HypothesisFileError(
            f"{slug!r}: stored parameters do not match their hash {record.params_sha256}"
        )
    values = frozen.frozen_values(record)
    inert = frozen.inert_sections(record.family)
    expected = {k for k in frozen_keys() if k.partition(".")[0] not in inert}
    if set(values) != expected:
        drift = sorted(set(values) ^ expected)
        raise HypothesisFileError(
            f"{slug!r}: registered with a different frozen key set ({', '.join(drift)}); "
            "re-register the hypothesis"
        )
    live = settings if settings is not None else get_settings()
    return _overlay(live, values)
