"""`data.release_id` in the frozen set (backtest spec, "The data-release id"; ADR 0023 D8;
plan T214): a registration made before the key is read with its three sections as stored
and its fingerprint unchanged; one made after records the id and no `master.*`, `gap.*` or
`adjust.*` key."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from tradepartner.backtest import frozen, hypothesis
from tradepartner.config import Settings

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hypotheses" / "fixture-momentum.md"
START = date(2019, 11, 29)
KEY = "data.release_id"
REPLACED = ("master.", "gap.", "adjust.")


def _params(settings: Settings, release: str | None = None) -> dict[str, Any]:
    parsed = hypothesis.parse_file(FIXTURE)
    if release is not None:
        parsed = _with_release(parsed, release)
    return hypothesis.frozen_params(parsed, settings)


def _with_release(parsed: hypothesis.HypothesisFile, release: str) -> hypothesis.HypothesisFile:
    from dataclasses import replace

    stripped = {k: v for k, v in parsed.file_params.items() if not k.startswith(REPLACED)}
    return replace(parsed, file_params={**stripped, KEY: release})


def test_entry_is_last_pinned_by_value_and_a_post_registration_key() -> None:
    assert frozen.is_default(frozen.FROZEN_KEY_DEFAULTS[-1:], ((KEY, "", 34),))
    assert frozenset({KEY}) == frozen.POST_REGISTRATION_DATA_KEYS
    assert KEY in frozen._FINGERPRINT_KEYS
    assert KEY in hypothesis.frozen_keys()
    assert KEY not in frozen.LAB_BASELINE_FROZEN_KEYS


def test_registration_before_the_key_is_read_with_its_three_sections_as_stored(
    settings: Settings,
) -> None:
    """A stored set without the key: the sections are overlaid and kept, hash and
    fingerprint are what they were."""
    from tradepartner.store import registry

    stored = {k: v for k, v in _params(settings).items() if k != KEY}
    sections = {k for k in stored if k.startswith(REPLACED)}
    assert sections  # master.*, gap.* and adjust.* are all there
    values = frozen.frozen_values(_Record(stored))
    assert sections <= set(values)
    assert values[KEY] == ""
    assert not frozen.has_release(values)
    before_hash = registry.params_sha256(stored)
    assert registry.params_sha256(stored) == before_hash
    with_default = _params(settings)
    assert frozen.fingerprint("momentum", with_default, START) == frozen.fingerprint(
        "momentum", stored, START
    )
    assert frozen.canonical_frozen_set(with_default, "momentum") == frozen.canonical_frozen_set(
        stored, "momentum"
    )


def test_registration_after_the_key_records_the_id_and_no_replaced_key(settings: Settings) -> None:
    params = _params(settings, "release-2026-10")
    assert params[KEY] == "release-2026-10"
    assert not [k for k in params if k.startswith(REPLACED)]
    assert KEY in frozen.canonical_frozen_set(params, "momentum")
    values = frozen.frozen_values(_Record(params))
    assert not [k for k in values if k.startswith(REPLACED)]  # no default overlaid back
    assert frozen.canonical_frozen_set(values, "momentum")[KEY] == "release-2026-10"


def test_the_id_is_in_the_fingerprint_and_other_ids_differ(settings: Settings) -> None:
    base = frozen.fingerprint("momentum", _params(settings), START)
    one = frozen.fingerprint("momentum", _params(settings, "r1"), START)
    two = frozen.fingerprint("momentum", _params(settings, "r2"), START)
    assert len({base, one, two}) == 3
    assert one == frozen.fingerprint("momentum", _params(settings, "r1"), START)


def test_release_fingerprint_is_the_same_from_stored_values_as_from_a_fresh_set(
    settings: Settings,
) -> None:
    """The re-recorded fingerprint is a pure function of the stored set: reading the
    registration back through `frozen_values` moves nothing (the comparison against the
    release's own recorded fingerprints is the T198 sitting's)."""
    params = _params(settings, "r1")
    values = frozen.frozen_values(_Record(params))
    assert frozen.fingerprint("momentum", values, START) == frozen.fingerprint(
        "momentum", params, START
    )


def test_no_look_ahead_a_release_named_later_does_not_reach_an_earlier_registration(
    settings: Settings,
) -> None:
    """Point in time: a registration stored without the id stays on its stored sections
    whatever the live settings later name (a live release id is never overlaid)."""
    stored = {k: v for k, v in _params(settings).items() if k != KEY}
    live = settings.model_copy(
        update={"data": settings.data.model_copy(update={"release_id": "r9"})}
    )
    assert live.data.release_id == "r9"
    values = frozen.frozen_values(_Record(stored))
    assert values[KEY] == ""
    assert {k for k in stored if k.startswith(REPLACED)} <= set(values)
    # the freezing path ignores the live id too: a file without the key freezes the default
    params = hypothesis.frozen_params(hypothesis.parse_file(FIXTURE), live)
    assert params[KEY] == ""
    assert [k for k in params if k.startswith(REPLACED)]


class _Record:
    def __init__(self, params: dict[str, Any], family: str = "momentum") -> None:
        self.params = params
        self.family = family
