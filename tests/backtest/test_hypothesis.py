"""Tests for `tradepartner.backtest.hypothesis` (spec req 10, plan T35).

Pre-registration freezes parameters: the hypothesis file is the source of truth for
the keys it names, the live `Settings` fill the rest of the spec's frozen list at
registration, and a run reads the frozen values back, never the live ones, so no
environment variable can move a registered hypothesis's holdout or threshold.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import mark_pre_lab

from tradepartner.backtest import frozen, hypothesis
from tradepartner.backtest.hypothesis import HypothesisFileError, LabRegistrationError
from tradepartner.config import FORBIDDEN_AXIS_PREFIXES, Settings
from tradepartner.store import lab_registry, registry, schema
from tradepartner.store.db import utc_now
from tradepartner.store.lab_schema import is_lab_initialised

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hypotheses" / "fixture-momentum.md"
SLUG = "fixture-momentum"
PROF_FIXTURE = FIXTURE.with_name("fixture-profitability.md")
PROF_SLUG = "fixture-profitability"
COMBINED_FIXTURE = FIXTURE.with_name("fixture-combined.md")
COMBINED_SLUG = "fixture-combined"

# Spec req 10: the frozen sections (every key) and the single frozen keys.
FROZEN_SECTIONS = (
    "combined",
    "strategy",
    "profitability",
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
FROZEN_SINGLE_KEYS = ("execution.fill_price", "benchmarks", "alpaca.historical_feed")


def _spec_frozen_keys() -> set[str]:
    keys = set(FROZEN_SINGLE_KEYS)
    for section in FROZEN_SECTIONS:
        model = type(getattr(Settings(_env_file=None), section))
        keys.update(f"{section}.{name}" for name in model.model_fields)
    return keys


@pytest.fixture
def conn(tmp_path: Path) -> duckdb.DuckDBPyConnection:
    """A registry on a temp file that is not `settings.store.path`."""
    connection = duckdb.connect(str(tmp_path / "scratch.duckdb"))
    schema.init_schema(connection)
    return connection


def _copy(tmp_path: Path, text: str | None = None) -> Path:
    """The fixture (or `text`) at `<tmp>/hypotheses/fixture-momentum.md`."""
    path = tmp_path / "hypotheses" / f"{SLUG}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text if text is not None else FIXTURE.read_text())
    return path


def _without_line(line: str) -> str:
    text = FIXTURE.read_text()
    assert f"\n{line}\n" in text, line
    return text.replace(f"\n{line}\n", "\n", 1)


def _register(
    conn: duckdb.DuckDBPyConnection, path: Path, settings: Settings
) -> registry.HypothesisRecord:
    return hypothesis.register(conn, path, registered_by="test", settings=settings)


# --- parsing -----------------------------------------------------------------


def test_fixture_file_parses(settings: Settings) -> None:
    parsed = hypothesis.parse_file(FIXTURE)
    assert parsed.slug == SLUG
    assert parsed.family == "momentum"
    assert parsed.title == "Fixture 12-1 momentum"
    assert parsed.in_sample_start == date(2017, 1, 31)
    assert parsed.holdout_start == date(2023, 1, 3)
    assert parsed.holdout_end == date(2025, 12, 31)
    assert parsed.doc_sha256 == sha256(FIXTURE.read_bytes()).hexdigest()
    assert parsed.file_params["strategy.top_fraction"] == 0.10
    assert parsed.file_params["costs.sensitivity_per_side_bps"] == [0.0, 30.0, 60.0, 100.0]
    assert parsed.file_params["gap.count_share_threshold"] == 0.05


def test_fixture_file_names_every_required_key() -> None:
    parsed = hypothesis.parse_file(FIXTURE)
    required = hypothesis.required_keys("momentum")
    assert {"holdout.start", "holdout.end"} <= required
    assert {
        k for k in _spec_frozen_keys() if k.startswith(("strategy.", "costs."))
    } - frozen.POST_REGISTRATION_OWN_KEYS <= required
    assert required <= set(parsed.file_params)


@pytest.mark.parametrize(
    "line",
    [
        "end = 2025-12-31",  # holdout.end
        "start = 2023-01-03",  # holdout.start
        "in_sample_start = 2017-01-31",
        "commission_per_order = 0.0",  # a costs.* key
        "sensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]",
        "top_fraction = 0.10",  # a strategy.* key
        "signal_total_return = true",
    ],
)
def test_file_missing_a_required_key_is_refused(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings, line: str
) -> None:
    path = _copy(tmp_path, _without_line(line))
    with pytest.raises(HypothesisFileError, match="missing"):
        _register(conn, path, settings)
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone() == (0,)


def test_holdout_never_comes_from_live_settings(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection
) -> None:
    live = Settings(_env_file=None, holdout={"start": "2023-01-03", "end": "2025-12-31"})
    path = _copy(tmp_path, _without_line("end = 2025-12-31"))
    with pytest.raises(HypothesisFileError, match=r"holdout\.end"):
        _register(conn, path, live)


@pytest.mark.parametrize(
    ("addition", "match"),
    [
        ('\n[store]\npath = "elsewhere.duckdb"\n', r"store\.path"),
        ('\n[hypotheses]\nfamilies = ["momentum"]\n', r"hypotheses\.families"),
        ('\n[execution]\nfill_price = "open"\nslippage = 1\n', r"execution\.slippage"),
        ("\n[strategy.extra]\nx = 1\n", r"strategy\.extra\.x"),
        ("\nnote = 'x'\n", "note"),
    ],
)
def test_keys_outside_the_frozen_list_are_refused(
    tmp_path: Path, addition: str, match: str
) -> None:
    text = FIXTURE.read_text().replace("\n```\n", addition + "```\n", 1)
    with pytest.raises(HypothesisFileError, match=match):
        hypothesis.parse_file(_copy(tmp_path, text))


def test_file_needs_exactly_one_parameter_block(tmp_path: Path) -> None:
    text = FIXTURE.read_text()
    no_block = text.replace("```toml hypothesis", "```toml", 1)
    with pytest.raises(HypothesisFileError, match="parameter block"):
        hypothesis.parse_file(_copy(tmp_path, no_block))
    block = text[text.index("```toml hypothesis") :]
    block = block[: block.index("\n```\n") + 5]
    with pytest.raises(HypothesisFileError, match="parameter block"):
        hypothesis.parse_file(_copy(tmp_path, text + "\n" + block))


def test_slug_must_match_the_file_name(tmp_path: Path) -> None:
    path = tmp_path / "other-name.md"
    path.write_text(FIXTURE.read_text())
    with pytest.raises(HypothesisFileError, match="slug"):
        hypothesis.parse_file(path)


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        ("start = 2023-01-03", 'start = "2023-01-03"', r"holdout\.start"),
        ("in_sample_start = 2017-01-31", "in_sample_start = 2023-06-30", "in_sample_start"),
        ("end = 2025-12-31", "end = 2025-12-31T00:00:00Z", r"holdout\.end"),
        ('title = "Fixture 12-1 momentum"', "title = 3", "title"),
    ],
)
def test_malformed_values_are_refused(tmp_path: Path, old: str, new: str, match: str) -> None:
    with pytest.raises(HypothesisFileError, match=match):
        hypothesis.parse_file(_copy(tmp_path, FIXTURE.read_text().replace(old, new, 1)))


def test_out_of_range_value_is_refused(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    path = _copy(tmp_path, FIXTURE.read_text().replace("top_fraction = 0.10", "top_fraction = 2"))
    with pytest.raises(HypothesisFileError, match="top_fraction"):
        _register(conn, path, settings)


# --- the frozen set ------------------------------------------------------------


def test_frozen_set_is_exactly_the_spec_key_list(settings: Settings) -> None:
    frozen = hypothesis.frozen_params(hypothesis.parse_file(FIXTURE), settings)
    # A momentum registration stores no `profitability.*` key (amendment #720, T85) and
    # no `combined.*` key (an unlisted section is inert, ADR 0014 point 2).
    assert set(frozen) == {
        k for k in _spec_frozen_keys() if not k.startswith(("profitability.", "combined."))
    }
    assert set(hypothesis.frozen_keys()) == _spec_frozen_keys()
    for key in (
        "metrics.risk_free_rate",
        "metrics.red_flag_excess_cagr_pp",
        "master.static_columns",
        "master.transfer_window_sessions",
        "benchmarks",
        "alpaca.historical_feed",
        "execution.fill_price",
        "holdout.start",
        "holdout.end",
    ):
        assert key in frozen
    for key in ("store.path", "hypotheses.families", "ingest.max_missing_share", "in_sample_start"):
        assert key not in frozen


def test_frozen_values_are_json_normalized(settings: Settings) -> None:
    frozen = hypothesis.frozen_params(hypothesis.parse_file(FIXTURE), settings)
    assert frozen["holdout.start"] == "2023-01-03"
    assert frozen["holdout.end"] == "2025-12-31"
    assert frozen["universe.exclude_sic_ranges"] == [[4900, 4999]]
    assert frozen["universe.min_price"] == 5.0
    assert isinstance(frozen["universe.min_price"], float)


def test_integer_written_for_a_float_key_hashes_like_the_float(
    tmp_path: Path, settings: Settings
) -> None:
    as_float = hypothesis.frozen_params(hypothesis.parse_file(FIXTURE), settings)
    text = FIXTURE.read_text().replace("per_side_bps = 15.0", "per_side_bps = 15")
    as_int = hypothesis.frozen_params(hypothesis.parse_file(_copy(tmp_path, text)), settings)
    assert registry.params_sha256(as_int) == registry.params_sha256(as_float)


def test_file_values_win_over_settings_and_settings_fill_the_rest() -> None:
    live = Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        gap={"count_share_threshold": 0.9},
        universe={"top_n_by_cap": 500},
        benchmarks=["QQQ"],
    )
    frozen = hypothesis.frozen_params(hypothesis.parse_file(FIXTURE), live)
    assert frozen["strategy.top_fraction"] == 0.10
    assert frozen["gap.count_share_threshold"] == 0.05
    assert frozen["universe.top_n_by_cap"] == 500
    assert frozen["benchmarks"] == ["QQQ"]


# --- registration --------------------------------------------------------------


def test_register_stores_file_hash_and_frozen_set(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    record = _register(conn, FIXTURE, settings)
    frozen = hypothesis.frozen_params(hypothesis.parse_file(FIXTURE), settings)
    assert record.slug == SLUG
    assert record.family == "momentum"
    assert record.title == "Fixture 12-1 momentum"
    assert record.doc_sha256 == sha256(FIXTURE.read_bytes()).hexdigest()
    assert record.params == frozen
    assert record.params_sha256 == registry.params_sha256(frozen)
    assert (record.in_sample_start, record.holdout_start, record.holdout_end) == (
        date(2017, 1, 31),
        date(2023, 1, 3),
        date(2025, 12, 31),
    )
    assert record.registered_by == "test"


def test_unchanged_file_registers_once(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    first = _register(conn, FIXTURE, settings)
    again = _register(conn, FIXTURE, settings)
    assert again.hypothesis_id == first.hypothesis_id
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone() == (1,)


def test_a_prose_only_edit_is_refused_and_writes_nothing(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """Strategy-lab T104c: a prose-only edit (same slug and fingerprint, new doc hash) is
    refused in every state, here on a store without the lab tables. Before T104c it was
    a second registration (the Phase 3 duplicates spec req 13 grandfathers)."""
    path = _copy(tmp_path)
    _register(conn, path, settings)
    path.write_text(path.read_text() + "\nA prose edit outside the parameter block.\n")
    with pytest.raises(hypothesis.ProseOnlyEditError, match="prose-only"):
        _register(conn, path, settings)
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone() == (1,)


def test_a_changed_frozen_value_is_a_new_hypothesis(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    path = _copy(tmp_path)
    first = _register(conn, path, settings)
    path.write_text(path.read_text().replace("top_fraction = 0.10", "top_fraction = 0.15"))
    second = _register(conn, path, settings)
    assert second.hypothesis_id > first.hypothesis_id
    assert second.params_sha256 != first.params_sha256


def test_a_slug_moved_between_families_is_refused_not_a_prose_only_edit(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """#1247: `same_set` compared the window and the canonical frozen set but not the
    family. `momentum` and `oracle` list the same `strategy` section, so their canonical
    sets compare equal; a file that keeps its slug and changes `family` must be refused
    as a move between families, never accepted as a prose-only edit or as the other
    family's record."""
    path = _copy(tmp_path)
    first = _register(conn, path, settings)
    assert first.family == "momentum"
    path.write_text(path.read_text().replace('family = "momentum"', 'family = "oracle"', 1))
    with pytest.raises(registry.RegistryError, match="cannot move"):
        _register(conn, path, settings)
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone() == (1,)


def test_reverting_to_an_older_registration_is_refused(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """`load_frozen` runs the latest registration of a slug, so re-registering an
    older file must not report the older record as what will run."""
    path = _copy(tmp_path)
    original = path.read_text()
    first = _register(conn, path, settings)
    path.write_text(original.replace("top_fraction = 0.10", "top_fraction = 0.20"))
    second = _register(conn, path, settings)
    path.write_text(original)
    with pytest.raises(HypothesisFileError, match="not the latest"):
        _register(conn, path, settings)
    assert second.hypothesis_id > first.hypothesis_id
    assert hypothesis.load_frozen(conn, SLUG, settings=settings).strategy.top_fraction == 0.20


def test_different_live_settings_give_a_new_hypothesis(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    first = _register(conn, FIXTURE, settings)
    other = settings.model_copy(
        update={"universe": settings.universe.model_copy(update={"top_n_by_cap": 500})}
    )
    second = _register(conn, FIXTURE, other)
    assert second.hypothesis_id != first.hypothesis_id
    assert second.params_sha256 != first.params_sha256


def test_family_outside_the_list_is_refused(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    text = FIXTURE.read_text().replace('family = "momentum"', 'family = "value"', 1)
    # Since T128 (ADR 0014 point 2), parse_file refuses an unlisted family itself (no
    # inherit-momentum fallback through `inert_sections`), rather than letting it reach
    # the registry's `hypotheses.families` check.
    with pytest.raises(HypothesisFileError, match="FAMILIES"):
        _register(conn, _copy(tmp_path, text), settings)
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone() == (0,)


# --- load_frozen ---------------------------------------------------------------


def test_unregistered_slug_is_refused(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    with pytest.raises(registry.UnknownHypothesis):
        hypothesis.load_frozen(conn, "never-registered", settings=settings)


def test_load_frozen_returns_the_frozen_values(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    record = _register(conn, FIXTURE, settings)
    frozen = hypothesis.load_frozen(conn, SLUG, settings=settings)
    assert frozen.holdout.start == date(2023, 1, 3)
    assert frozen.holdout.end == date(2025, 12, 31)
    assert frozen.strategy.top_fraction == 0.10
    assert hypothesis.frozen_params_of(frozen, family="momentum") == record.params
    # Keys outside the frozen list stay live.
    assert frozen.store.path == settings.store.path


@pytest.mark.parametrize(
    ("env", "value", "read"),
    [
        ("HOLDOUT__START", "2020-01-02", lambda s: s.holdout.start),
        ("GAP__COUNT_SHARE_THRESHOLD", "0.5", lambda s: s.gap.count_share_threshold),
        ("STRATEGY__TOP_FRACTION", "0.5", lambda s: s.strategy.top_fraction),
        (
            "ADJUST__MAX_PRIOR_CLOSE_GAP_SESSIONS",
            "9",
            lambda s: s.adjust.max_prior_close_gap_sessions,
        ),
        ("BENCHMARKS", '["QQQ"]', lambda s: s.benchmarks),
        ("ALPACA__HISTORICAL_FEED", "iex", lambda s: s.alpaca.historical_feed),
        ("UNIVERSE__TOP_N_BY_CAP", "500", lambda s: s.universe.top_n_by_cap),
    ],
)
def test_frozen_values_survive_environment_overrides(
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env: str,
    value: str,
    read: object,
) -> None:
    assert callable(read)
    _register(conn, FIXTURE, settings)
    registered = read(hypothesis.load_frozen(conn, SLUG, settings=settings))

    monkeypatch.setenv(env, value)
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "no.env"))
    live = Settings()
    assert read(live) != registered, "the override must change the live value"
    assert read(hypothesis.load_frozen(conn, SLUG, settings=live)) == registered
    assert read(hypothesis.load_frozen(conn, SLUG)) == registered


def test_load_frozen_refuses_tampered_params(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    record = _register(conn, FIXTURE, settings)
    # The registry exposes no UPDATE; a hand edit of the store is what this guards.
    conn.execute(
        "UPDATE hypotheses SET params_json = replace(params_json, ?, ?) WHERE hypothesis_id = ?",
        ['"strategy.top_fraction":0.1', '"strategy.top_fraction":0.2', record.hypothesis_id],
    )
    with pytest.raises(HypothesisFileError, match="hash"):
        hypothesis.load_frozen(conn, SLUG, settings=settings)


def test_load_frozen_refuses_a_stale_key_set(
    conn: duckdb.DuckDBPyConnection, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(conn, FIXTURE, settings)
    monkeypatch.setattr(
        hypothesis, "frozen_keys", lambda: (*hypothesis.FROZEN_SINGLE_KEYS, "backtest.new_key")
    )
    with pytest.raises(HypothesisFileError, match="frozen key set"):
        hypothesis.load_frozen(conn, SLUG, settings=settings)


# --- frozen-key defaults (strategy-lab T96) ----------------------------------


def test_a_new_file_stores_both_schedule_keys(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    record = _register(conn, FIXTURE, settings)
    assert record.params["schedule.rebalance_cadence"] == "month_end"
    assert record.params["schedule.signal_anchor"] == "month_end"


def test_load_frozen_on_the_pre_lab_fixture_twin(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """A registration stored without `schedule.*` (as before the lab) runs, reads
    `month_end` for both keys, and keeps its stored hash."""
    parsed = hypothesis.parse_file(FIXTURE)
    params = {
        k: v
        for k, v in hypothesis.frozen_params(parsed, settings).items()
        if not k.startswith(("schedule.", "gap.stale_listing_sessions"))
    }
    record = registry.register_hypothesis(
        conn,
        slug=parsed.slug,
        family=parsed.family,
        title=parsed.title,
        doc_path=FIXTURE.as_posix(),
        doc_sha256=parsed.doc_sha256,
        params=params,
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by="test",
        settings=settings,
    )
    live = settings.model_copy(
        update={"schedule": settings.schedule.model_copy(update={"rebalance_cadence": "daily"})}
    )
    loaded = hypothesis.load_frozen(conn, SLUG, settings=live)
    assert loaded.schedule.rebalance_cadence == "month_end"
    assert loaded.schedule.signal_anchor == "month_end"
    assert registry.get_hypothesis(conn, SLUG).params_sha256 == record.params_sha256
    assert registry.params_sha256(params) == record.params_sha256


# --- the `profitability` family (backtest spec amendment #720, T85) -----------


def _prof_copy(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "hypotheses" / f"{PROF_SLUG}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def test_profitability_file_registers_without_strategy_keys(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    parsed = hypothesis.parse_file(PROF_FIXTURE)
    assert hypothesis.required_keys("profitability") <= set(parsed.file_params)
    assert {k for k in _spec_frozen_keys() if k.startswith(("profitability.", "costs."))} <= (
        hypothesis.required_keys("profitability")
    )
    record = _register(conn, PROF_FIXTURE, settings)
    assert record.family == "profitability"
    assert not any(k.startswith("strategy.") for k in record.params)
    assert record.params["profitability.top_fraction"] == 0.2
    stored = conn.execute(
        "SELECT params_json FROM hypotheses WHERE hypothesis_id = ?", [record.hypothesis_id]
    ).fetchone()
    assert stored is not None and '"strategy.' not in stored[0]
    assert set(record.params) == {
        k for k in _spec_frozen_keys() if not k.startswith(("strategy.", "combined."))
    }


@pytest.mark.parametrize("line", ["top_fraction = 0.2", "max_fact_age_days = 548"])
def test_profitability_file_missing_its_key_is_refused(tmp_path: Path, line: str) -> None:
    text = PROF_FIXTURE.read_text().replace(f"\n{line}\n", "\n", 1)
    with pytest.raises(HypothesisFileError, match="missing"):
        hypothesis.parse_file(_prof_copy(tmp_path, text))


def test_profitability_file_naming_a_strategy_key_is_refused(tmp_path: Path) -> None:
    added = "\n[strategy]\nskip_months = 1\n\n[costs]\n"
    text = PROF_FIXTURE.read_text().replace("\n[costs]\n", added, 1)
    with pytest.raises(HypothesisFileError, match=r"strategy\.skip_months"):
        hypothesis.parse_file(_prof_copy(tmp_path, text))


def test_momentum_file_naming_a_profitability_key_is_refused(tmp_path: Path) -> None:
    added = "\n[profitability]\ntop_fraction = 0.1\n\n[costs]\n"
    text = FIXTURE.read_text().replace("\n[costs]\n", added, 1)
    with pytest.raises(HypothesisFileError, match=r"profitability\.top_fraction"):
        hypothesis.parse_file(_copy(tmp_path, text))


def test_profitability_registration_ignores_live_strategy_settings(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """An unchanged B3-style file re-registers as a no-op whatever the live momentum
    keys, and a run of it keeps the live `strategy.*` it never reads."""
    first = _register(conn, PROF_FIXTURE, settings)
    live = settings.model_copy(
        update={"strategy": settings.strategy.model_copy(update={"top_fraction": 0.5})}
    )
    again = _register(conn, PROF_FIXTURE, live)
    assert again.hypothesis_id == first.hypothesis_id
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone() == (1,)
    loaded = hypothesis.load_frozen(conn, PROF_SLUG, settings=live)
    assert loaded.profitability.top_fraction == 0.2
    assert loaded.strategy.top_fraction == 0.5
    assert hypothesis.frozen_hash_matches(loaded, first.params_sha256, family="profitability")
    assert hypothesis.frozen_params_of(loaded, family="profitability") == first.params


# --- the `combined` family (hypothesis backlog B4; ADR 0014 point 6, T130) ------


def _combined_copy(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "hypotheses" / f"{COMBINED_SLUG}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def test_combined_file_names_and_freezes_both_sub_signal_keys(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """A `combined` file names every `combined.*`, `strategy.*` and `profitability.*` key
    and its registration freezes all three sections, with no inert section."""
    parsed = hypothesis.parse_file(COMBINED_FIXTURE)
    required = hypothesis.required_keys("combined")
    assert {"holdout.start", "holdout.end"} <= required
    assert {
        k
        for k in _spec_frozen_keys()
        if k.startswith(("combined.", "strategy.", "profitability.", "costs."))
    } - frozen.POST_REGISTRATION_OWN_KEYS <= required
    assert required <= set(parsed.file_params)
    assert frozen.inert_sections("combined") == frozenset()
    record = _register(conn, COMBINED_FIXTURE, settings)
    assert record.family == "combined"
    assert record.params["combined.top_fraction"] == 0.2
    assert record.params["strategy.formation_months"] == 12
    assert record.params["profitability.max_fact_age_days"] == 548
    assert set(record.params) == set(hypothesis.family_frozen_keys("combined"))


def test_combined_file_missing_a_strategy_key_is_refused_as_incomplete(tmp_path: Path) -> None:
    """`strategy` is one of `combined`'s listed sections, so its keys are required, not
    another family's': the refusal is `required keys missing`, not an inert-section one."""
    text = COMBINED_FIXTURE.read_text().replace("\nformation_months = 12\n", "\n", 1)
    with pytest.raises(HypothesisFileError, match="missing") as excinfo:
        hypothesis.parse_file(_combined_copy(tmp_path, text))
    assert "strategy.formation_months" in str(excinfo.value)
    assert "another family" not in str(excinfo.value)


@pytest.mark.parametrize(
    "dropped",
    # The pre-lab twin also predates `gap.stale_listing_sessions` (#1199) and
    # `strategy.turnover_top_fraction` (#1358), as H1 does.
    [
        ("profitability.",),
        (
            "schedule.",
            "profitability.",
            "gap.stale_listing_sessions",
            "strategy.turnover_top_fraction",
        ),
    ],
    ids=["today", "pre-lab"],
)
def test_h1_twin_registered_before_t85_still_loads_and_verifies(
    conn: duckdb.DuckDBPyConnection, settings: Settings, dropped: tuple[str, ...]
) -> None:
    """H1's fixture twin, stored before T85 (no `profitability.*`; the pre-lab twin, as
    the owner's H1 is, also without `schedule.*`), loads at its stored hash with the
    section's live values, `frozen_hash_matches` (what `record_results` checks) holds,
    and `hypothesis register` on the unchanged file writes nothing new."""
    parsed = hypothesis.parse_file(FIXTURE)
    params = {
        k: v
        for k, v in hypothesis.frozen_params(parsed, settings).items()
        if not k.startswith(dropped)
    }
    record = registry.register_hypothesis(
        conn,
        slug=parsed.slug,
        family=parsed.family,
        title=parsed.title,
        doc_path=FIXTURE.as_posix(),
        doc_sha256=parsed.doc_sha256,
        params=params,
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by="test",
        settings=settings,
    )
    live = settings.model_copy(
        update={"profitability": settings.profitability.model_copy(update={"top_fraction": 0.5})}
    )
    loaded = hypothesis.load_frozen(conn, SLUG, settings=live)
    assert loaded.profitability.top_fraction == 0.5  # live: momentum never reads it
    assert registry.get_hypothesis(conn, SLUG).params_sha256 == record.params_sha256
    assert hypothesis.frozen_hash_matches(loaded, record.params_sha256, family="momentum")
    if dropped == ("profitability.",):
        assert _register(conn, FIXTURE, live).hypothesis_id == record.hypothesis_id
        assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone() == (1,)


def test_frozen_hash_matches_compares_a_table_default_by_type(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """#1022: a live `True` is not a table default of `1`, so a registration stored
    without that key does not match live settings that would run `True`."""
    from tradepartner.backtest import frozen

    key = "universe.liquidity_rule_enabled"
    params = hypothesis.frozen_params_of(settings, family="momentum")
    assert params[key] is True
    without = registry.params_sha256({k: v for k, v in params.items() if k != key})
    monkeypatch.setattr(frozen, "FROZEN_KEY_DEFAULTS", ((key, True, 99),))
    assert hypothesis.frozen_hash_matches(settings, without, family="momentum")
    monkeypatch.setattr(frozen, "FROZEN_KEY_DEFAULTS", ((key, 1, 99),))
    assert not hypothesis.frozen_hash_matches(settings, without, family="momentum")


def test_frozen_hash_matches_drops_only_an_at_default_suffix(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    """A later table key at its default is dropped even when an earlier one is not; the
    earlier one stays in the hash at its live value."""
    from tradepartner.backtest import frozen

    first, second = "universe.liquidity_rule_enabled", "strategy.signal_total_return"
    params = hypothesis.frozen_params_of(settings, family="momentum")
    assert params[first] is True and params[second] is True
    table = ((first, 1, 99), (second, True, 99))
    monkeypatch.setattr(frozen, "FROZEN_KEY_DEFAULTS", table)
    without_second = registry.params_sha256({k: v for k, v in params.items() if k != second})
    without_both = registry.params_sha256(
        {k: v for k, v in params.items() if k not in (first, second)}
    )
    assert hypothesis.frozen_hash_matches(settings, without_second, family="momentum")
    assert not hypothesis.frozen_hash_matches(settings, without_both, family="momentum")


def test_invalid_frozen_value_names_its_key_and_the_file_value(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """#1093: `Settings` hides input values in its errors, so the refusal re-renders
    the offending frozen key with the file's own value (frozen values are never
    secrets: a file may name frozen keys only)."""
    text = FIXTURE.read_text()
    assert "\ntop_fraction = 0.10\n" in text
    path = _copy(tmp_path, text.replace("\ntop_fraction = 0.10\n", "\ntop_fraction = 7.5\n", 1))
    with pytest.raises(HypothesisFileError) as excinfo:
        _register(conn, path, settings)
    message = str(excinfo.value)
    assert "strategy.top_fraction = 7.5" in message
    assert "less than or equal to 1" in message


def test_invalid_list_element_names_its_index_and_value(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    text = FIXTURE.read_text()
    old = "\nsensitivity_per_side_bps = [0.0, 30.0, 60.0, 100.0]\n"
    assert old in text
    path = _copy(tmp_path, text.replace(old, "\nsensitivity_per_side_bps = [0.0, -30.0]\n", 1))
    with pytest.raises(HypothesisFileError) as excinfo:
        _register(conn, path, settings)
    assert "costs.sensitivity_per_side_bps.1 = -30.0" in str(excinfo.value)


# --- The family registry: unlisted-family failures (ADR 0014, T128) ----------


def test_required_keys_unchanged_for_momentum_and_profitability(settings: Settings) -> None:
    """The derived `required_keys` returns today's sets for both families."""
    momentum_required = hypothesis.required_keys("momentum")
    assert {"holdout.start", "holdout.end"} <= momentum_required
    assert {
        k for k in _spec_frozen_keys() if k.startswith(("strategy.", "costs."))
    } - frozen.POST_REGISTRATION_OWN_KEYS <= momentum_required
    assert not any(k.startswith("profitability.") for k in momentum_required)
    profitability_required = hypothesis.required_keys("profitability")
    assert {"holdout.start", "holdout.end"} <= profitability_required
    assert {k for k in _spec_frozen_keys() if k.startswith(("profitability.", "costs."))} <= (
        profitability_required
    )
    assert not any(k.startswith("strategy.") for k in profitability_required)


def test_required_keys_raises_for_an_unlisted_family() -> None:
    with pytest.raises(KeyError):
        hypothesis.required_keys("nosuch")


def test_frozen_params_of_raises_for_an_unlisted_family(settings: Settings) -> None:
    """The momentum fallback goes (ADR 0014 point 2): an unlisted family raises instead
    of inheriting momentum's identity."""
    with pytest.raises(KeyError):
        hypothesis.frozen_params_of(settings, family="nosuch")


def test_frozen_hash_matches_raises_for_an_unlisted_family(settings: Settings) -> None:
    with pytest.raises(KeyError):
        hypothesis.frozen_hash_matches(settings, "0" * 64, family="nosuch")


def test_parse_file_refuses_a_non_string_family(tmp_path: Path) -> None:
    """`:185`'s non-string default raises (ADR 0014 point 2)."""
    text = FIXTURE.read_text().replace('family = "momentum"', "family = 3", 1)
    with pytest.raises(HypothesisFileError, match="family"):
        hypothesis.parse_file(_copy(tmp_path, text))


# --- registration after the lab (strategy-lab plan T104c) ----------------------

#: The fixture with `in_sample_start` moved so a 12-month formation anchor at the first
#: rebalance falls inside the fixture bars (first session 2017-01-03).
LAB_START = ("in_sample_start = 2017-01-31", "in_sample_start = 2018-01-31")
_LAB_TABLES = ("hypotheses", "hypothesis_fingerprints", "owner_decisions", "family_rules")


def _lab_file(tmp_path: Path, *edits: tuple[str, str], slug: str = SLUG) -> Path:
    """The momentum fixture with `LAB_START` and `edits` applied, as `<slug>.md`."""
    text = FIXTURE.read_text()
    for old, new in (LAB_START, *edits):
        assert old in text, old
        text = text.replace(old, new, 1)
    text = text.replace(f'slug = "{SLUG}"', f'slug = "{slug}"')
    path = tmp_path / "lab" / f"{slug}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def _counts(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # type: ignore[index]
        for table in _LAB_TABLES
    }


def _direct(
    conn: duckdb.DuckDBPyConnection, path: Path, settings: Settings, *, slug: str | None = None
) -> registry.HypothesisRecord:
    """`path` registered straight through the registry (a pre-lab registration, or a
    sweep's variant with `slug`)."""
    parsed = hypothesis.parse_file(path)
    return registry.register_hypothesis(
        conn,
        slug=slug or parsed.slug,
        family=parsed.family,
        title=parsed.title,
        doc_path=path.as_posix(),
        doc_sha256=parsed.doc_sha256,
        params=hypothesis.frozen_params(parsed, settings),
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by="test",
        settings=settings,
    )


def _fingerprint(record: registry.HypothesisRecord) -> str:
    return frozen.fingerprint(record.family, frozen.frozen_values(record), record.in_sample_start)


def _pre_lab_twin(
    conn: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> registry.HypothesisRecord:
    """H1's fixture twin as the lab migration leaves it: registered, marked pre-lab,
    fingerprinted, and its family rules written from it."""
    record = _direct(conn, _lab_file(tmp_path), settings)
    mark_pre_lab(conn, record.hypothesis_id)
    lab_registry.write_fingerprint(conn, record.hypothesis_id, _fingerprint(record))
    lab_registry.write_family_rules(
        conn,
        family=record.family,
        first_hypothesis_id=record.hypothesis_id,
        parent_family=None,
        holdout_start=record.holdout_start,
        holdout_end=record.holdout_end,
        in_sample_start=record.in_sample_start,
        fixed_params={
            k: v
            for k, v in frozen.frozen_values(record).items()
            if k.startswith(FORBIDDEN_AXIS_PREFIXES)
        },
        sr_star_seed_annual=None,
        settings=settings,
    )
    return record


def _variant(
    conn: duckdb.DuckDBPyConnection, settings: Settings, path: Path
) -> registry.HypothesisRecord:
    """A one-variant sweep registration whose variant has `path`'s frozen set."""
    parsed = hypothesis.parse_file(path)
    sweep_row = lab_registry.register_sweep(
        conn,
        slug=f"{parsed.slug}-sweep",
        family=parsed.family,
        title="one-value sweep",
        doc_path=f"docs/sweeps/{parsed.slug}-sweep.md",
        doc_sha256="s" * 64,
        grid={"strategy.top_fraction": [0.15]},
        n_variants=1,
        selection_statistic="sharpe_annual_excess_spy",
        expected_excess_cagr_spy_pp=0.0,
        expected_range_pp=(-2.0, 2.0),
        promote_at_least=0.5,
        retire_below=0.0,
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by="test",
        settings=settings,
    )
    record = _direct(conn, path, settings, slug=f"{parsed.slug}-sweep--r{sweep_row.sweep_id}-v1")
    fingerprint = _fingerprint(record)
    lab_registry.write_sweep_variant(
        conn,
        sweep_id=sweep_row.sweep_id,
        variant_index=1,
        hypothesis_id=record.hypothesis_id,
        fingerprint=fingerprint,
        variant_params={"strategy.top_fraction": 0.15},
    )
    lab_registry.write_fingerprint(conn, record.hypothesis_id, fingerprint)
    return record


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    """Every `registry.register_hypothesis` call, recorded and passed through."""
    calls: list[dict[str, Any]] = []
    real = registry.register_hypothesis

    def record(*args: Any, **kwargs: Any) -> registry.HypothesisRecord:
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(registry, "register_hypothesis", record)
    yield calls


def test_without_the_lab_a_new_file_registers_as_in_phase_3(
    fixture_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """A plain fixture store (the owner's store before the lab migration): a new
    standalone file, a nudged one and another family's register with no promotion."""
    assert not is_lab_initialised(fixture_store)
    first = _register(fixture_store, _lab_file(tmp_path), settings)
    nudged = _register(
        fixture_store,
        _lab_file(tmp_path, ("top_fraction = 0.10", "top_fraction = 0.15"), slug="nudged"),
        settings,
    )
    assert nudged.hypothesis_id > first.hypothesis_id
    prof = _register(fixture_store, PROF_FIXTURE, settings)
    assert prof.family == "profitability"
    assert fixture_store.execute("SELECT COUNT(*) FROM hypotheses").fetchone() == (3,)


def test_without_the_lab_a_pre_lab_stored_set_is_returned_by_its_canonical_set(
    conn: duckdb.DuckDBPyConnection, settings: Settings, recorded: list[dict[str, Any]]
) -> None:
    """H1 registered before `schedule.*` and `gap.stale_listing_sessions` landed: its
    unchanged file returns the stored record (no second row, no registry call)."""
    parsed = hypothesis.parse_file(FIXTURE)
    params = {
        k: v
        for k, v in hypothesis.frozen_params(parsed, settings).items()
        if not k.startswith(("schedule.", "gap.stale_listing_sessions"))
    }
    stored = registry.register_hypothesis(
        conn,
        slug=parsed.slug,
        family=parsed.family,
        title=parsed.title,
        doc_path=FIXTURE.as_posix(),
        doc_sha256=parsed.doc_sha256,
        params=params,
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by="test",
        settings=settings,
    )
    recorded.clear()

    again = _register(conn, FIXTURE, settings)

    assert again == stored
    assert recorded == []
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone() == (1,)


def test_on_the_lab_h1s_unchanged_file_returns_its_record_and_writes_nothing(
    lab_store: duckdb.DuckDBPyConnection,
    settings: Settings,
    tmp_path: Path,
    recorded: list[dict[str, Any]],
) -> None:
    twin = _pre_lab_twin(lab_store, settings, tmp_path)
    before = _counts(lab_store)
    recorded.clear()

    again = _register(lab_store, _lab_file(tmp_path), settings)

    assert again == twin
    assert _counts(lab_store) == before
    assert recorded == []


def test_on_the_lab_a_prose_only_edit_is_refused(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _pre_lab_twin(lab_store, settings, tmp_path)
    path = _lab_file(tmp_path)
    path.write_text(path.read_text() + "\nA prose edit.\n")
    before = _counts(lab_store)

    with pytest.raises(hypothesis.ProseOnlyEditError, match="prose-only"):
        _register(lab_store, path, settings)
    assert _counts(lab_store) == before


def test_on_the_lab_a_nudged_standalone_file_is_refused_naming_the_one_value_sweep(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _pre_lab_twin(lab_store, settings, tmp_path)
    nudged = _lab_file(tmp_path, ("top_fraction = 0.10", "top_fraction = 0.15"))
    before = _counts(lab_store)

    with pytest.raises(LabRegistrationError, match="one-value sweep"):
        _register(lab_store, nudged, settings)
    assert _counts(lab_store) == before


def test_on_the_lab_a_promoted_file_is_accepted_with_its_promotion_and_refused_without(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _pre_lab_twin(lab_store, settings, tmp_path)
    promoted = _lab_file(
        tmp_path, ("top_fraction = 0.10", "top_fraction = 0.15"), slug="fixture-promoted"
    )
    variant = _variant(lab_store, settings, promoted)
    before = _counts(lab_store)

    with pytest.raises(LabRegistrationError, match="one-value sweep"):
        _register(lab_store, promoted, settings)
    assert _counts(lab_store) == before

    record = hypothesis.register(
        lab_store,
        promoted,
        registered_by="test",
        settings=settings,
        promotion_of=variant.hypothesis_id,
    )
    assert record.slug == "fixture-promoted"
    assert _fingerprint(record) == _fingerprint(variant)
    # `sweep promote` (T109) appends the decision naming it; re-registering the
    # unchanged file then returns it.
    decision = {
        "decision_id": 1,
        "made_at": utc_now(),
        "kind": "promotion",
        "hypothesis_id": record.hypothesis_id,
        "values_json": "{}",
        "reason": "test",
    }
    lab_store.execute(
        "INSERT INTO owner_decisions (decision_id, made_at, kind, hypothesis_id, values_json, "
        "reason) VALUES ($decision_id, $made_at, $kind, $hypothesis_id, $values_json, $reason)",
        decision,
    )
    assert lab_registry.promotion_for(lab_store, record.hypothesis_id) is not None
    assert _register(lab_store, promoted, settings) == record


def test_on_the_lab_a_promotion_of_a_non_variant_or_another_fingerprint_is_refused(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    twin = _pre_lab_twin(lab_store, settings, tmp_path)
    variant = _variant(
        lab_store,
        settings,
        _lab_file(tmp_path, ("top_fraction = 0.10", "top_fraction = 0.15"), slug="v"),
    )
    other = _lab_file(tmp_path, ("top_fraction = 0.10", "top_fraction = 0.20"), slug="other")
    before = _counts(lab_store)

    for promotion_of, match in (
        (twin.hypothesis_id, "not a sweep variant"),
        (
            variant.hypothesis_id,
            "fingerprint differs",
        ),
    ):
        with pytest.raises(LabRegistrationError, match=match):
            hypothesis.register(
                lab_store, other, registered_by="test", settings=settings, promotion_of=promotion_of
            )
    assert _counts(lab_store) == before


def test_on_the_lab_a_promoted_file_off_the_family_rules_is_refused(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _pre_lab_twin(lab_store, settings, tmp_path)
    # `gap.count_share_threshold` is outside the fingerprint, inside the family rules.
    off = _lab_file(
        tmp_path,
        ("top_fraction = 0.10", "top_fraction = 0.15"),
        ("count_share_threshold = 0.05", "count_share_threshold = 0.04"),
        slug="off-rules",
    )
    variant = _variant(lab_store, settings, off)
    before = _counts(lab_store)

    with pytest.raises(LabRegistrationError, match=r"gap\.count_share_threshold"):
        hypothesis.register(
            lab_store,
            off,
            registered_by="test",
            settings=settings,
            promotion_of=variant.hypothesis_id,
        )
    assert _counts(lab_store) == before


def test_on_the_lab_a_promoted_file_with_an_infeasible_anchor_is_refused(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    _pre_lab_twin(lab_store, settings, tmp_path)
    # A 24-month formation anchor at the first rebalance (2018-01-31) precedes the
    # fixture's first session (2017-01-03).
    far = _lab_file(tmp_path, ("formation_months = 12", "formation_months = 24"), slug="far")
    variant = _variant(lab_store, settings, far)

    before = _counts(lab_store)

    with pytest.raises(LabRegistrationError, match="precedes the store's first session"):
        hypothesis.register(
            lab_store,
            far,
            registered_by="test",
            settings=settings,
            promotion_of=variant.hypothesis_id,
        )
    assert _counts(lab_store) == before


def test_on_the_lab_a_new_familys_first_standalone_file_is_refused(
    lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path
) -> None:
    """Family rules are written by `sweep.register` only: a promoted file in a family
    with no rules (here `profitability`, no rules row) is refused."""
    path = tmp_path / "prof" / f"{PROF_SLUG}.md"
    path.parent.mkdir()
    path.write_text(PROF_FIXTURE.read_text())
    variant = _variant(lab_store, settings, path)
    before = _counts(lab_store)

    with pytest.raises(LabRegistrationError, match="no family rules"):
        hypothesis.register(
            lab_store,
            path,
            registered_by="test",
            settings=settings,
            promotion_of=variant.hypothesis_id,
        )
    assert _counts(lab_store) == before


def test_a_file_stored_before_and_after_a_table_key_returns_its_latest_registration(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """The same file registered before `schedule.*` landed (id 1) and after (id 2, a
    new raw hash): the unchanged file returns the latest, not a "not the latest"
    refusal (quant-auditor, #1242)."""
    parsed = hypothesis.parse_file(FIXTURE)
    full = hypothesis.frozen_params(parsed, settings)
    old = {k: v for k, v in full.items() if not k.startswith(("schedule.", "gap.stale_listing"))}
    ids = [
        registry.register_hypothesis(
            conn,
            slug=parsed.slug,
            family=parsed.family,
            title=parsed.title,
            doc_path=FIXTURE.as_posix(),
            doc_sha256=parsed.doc_sha256,
            params=params,
            in_sample_start=parsed.in_sample_start,
            holdout_start=parsed.holdout_start,
            holdout_end=parsed.holdout_end,
            registered_by="test",
            settings=settings,
        ).hypothesis_id
        for params in (old, full)
    ]
    assert ids == [1, 2]

    assert _register(conn, FIXTURE, settings).hypothesis_id == 2


def test_without_the_lab_a_frozen_key_outside_the_fingerprint_is_a_new_hypothesis(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """`gap.count_share_threshold` is frozen but outside the fingerprint: changing it is
    a new frozen set (Phase 3: a new hypothesis), never a prose-only edit."""
    path = _copy(tmp_path)
    first = _register(conn, path, settings)
    path.write_text(
        path.read_text().replace("count_share_threshold = 0.05", "count_share_threshold = 0.04")
    )
    second = _register(conn, path, settings)
    assert second.hypothesis_id > first.hypothesis_id


# --- the turnover key: required-keys carve-out and family refusals (#1358, T165) -----

TURNOVER_KEY = "strategy.turnover_top_fraction"
H1_FILE = Path(__file__).resolve().parents[2] / "docs" / "hypotheses" / "h1-momentum-12-1.md"


def _with_turnover(tmp_path: Path, source: Path, slug: str, value: str) -> Path:
    """`source` with `turnover_top_fraction = <value>` added to its `[strategy]` table."""
    text = source.read_text()
    assert "\n[strategy]\n" in text
    text = text.replace("\n[strategy]\n", f"\n[strategy]\nturnover_top_fraction = {value}\n", 1)
    path = tmp_path / "hypotheses" / f"{slug}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


@pytest.mark.parametrize("family", ["momentum", "oracle", "combined"])
def test_required_keys_leave_out_the_post_registration_key(family: str) -> None:
    assert TURNOVER_KEY in hypothesis.family_frozen_keys(family)  # frozen, not required
    assert TURNOVER_KEY not in hypothesis.required_keys(family)
    assert "strategy.top_fraction" in hypothesis.required_keys(family)


@pytest.mark.parametrize("path", [H1_FILE, FIXTURE])
def test_h1_and_the_twin_parse_unchanged_without_the_key(path: Path) -> None:
    parsed = hypothesis.parse_file(path)
    assert TURNOVER_KEY not in parsed.file_params
    assert hypothesis.required_keys("momentum") <= set(parsed.file_params)


def test_a_file_without_the_key_freezes_the_default_not_the_live_value(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    live = settings.model_copy(
        update={"strategy": settings.strategy.model_copy(update={"turnover_top_fraction": 0.5})}
    )
    assert live.strategy.turnover_top_fraction == 0.5
    record = _register(conn, FIXTURE, live)
    assert record.params[TURNOVER_KEY] == 1.0
    assert hypothesis.load_frozen(conn, SLUG, settings=live).strategy.turnover_top_fraction == 1.0


def test_a_momentum_file_naming_the_key_freezes_its_value(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    record = _register(conn, _with_turnover(tmp_path, FIXTURE, SLUG, "0.20"), settings)
    assert record.params[TURNOVER_KEY] == 0.2


@pytest.mark.parametrize("value", ["0", "1.5"])
def test_a_value_outside_zero_one_is_refused(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings, value: str
) -> None:
    with pytest.raises(HypothesisFileError, match=r"fail validation.*turnover_top_fraction"):
        _register(conn, _with_turnover(tmp_path, FIXTURE, SLUG, value), settings)


@pytest.mark.parametrize(
    ("source", "slug", "family"),
    [(COMBINED_FIXTURE, COMBINED_SLUG, "combined"), (FIXTURE, SLUG, "oracle")],
)
def test_another_family_naming_a_screen_is_refused_naming_the_key(
    tmp_path: Path,
    conn: duckdb.DuckDBPyConnection,
    settings: Settings,
    source: Path,
    slug: str,
    family: str,
) -> None:
    path = _with_turnover(tmp_path, source, slug, "0.5")
    path.write_text(path.read_text().replace('family = "momentum"', f'family = "{family}"'))
    with pytest.raises(HypothesisFileError, match=f"{TURNOVER_KEY} is a 'momentum' rule"):
        _register(conn, path, settings)
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone() == (0,)


def test_a_combined_file_at_the_default_or_without_the_key_stores_the_default(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    live = settings.model_copy(
        update={"strategy": settings.strategy.model_copy(update={"turnover_top_fraction": 0.5})}
    )
    assert _register(conn, COMBINED_FIXTURE, live).params[TURNOVER_KEY] == 1.0
    named = _with_turnover(tmp_path, COMBINED_FIXTURE, COMBINED_SLUG, "1.0")
    assert hypothesis.frozen_params(hypothesis.parse_file(named), live)[TURNOVER_KEY] == 1.0


def test_a_profitability_file_naming_the_key_is_refused_as_inert(tmp_path: Path) -> None:
    text = PROF_FIXTURE.read_text().replace(
        "\n[costs]\n", "\n[strategy]\nturnover_top_fraction = 1.0\n\n[costs]\n", 1
    )
    path = tmp_path / "hypotheses" / f"{PROF_SLUG}.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    with pytest.raises(HypothesisFileError, match="another family"):
        hypothesis.parse_file(path)


def test_a_registration_stored_before_the_entry_still_matches_and_is_returned(
    conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """H1's case: stored without the key. `frozen_hash_matches` holds on its loaded
    settings, and `hypothesis register` on the unchanged file writes nothing."""
    parsed = hypothesis.parse_file(FIXTURE)
    params = {
        k: v for k, v in hypothesis.frozen_params(parsed, settings).items() if k != TURNOVER_KEY
    }
    record = registry.register_hypothesis(
        conn,
        slug=parsed.slug,
        family=parsed.family,
        title=parsed.title,
        doc_path=FIXTURE.as_posix(),
        doc_sha256=parsed.doc_sha256,
        params=params,
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by="test",
        settings=settings,
    )
    live = settings.model_copy(
        update={"strategy": settings.strategy.model_copy(update={"turnover_top_fraction": 0.5})}
    )
    loaded = hypothesis.load_frozen(conn, SLUG, settings=live)
    assert loaded.strategy.turnover_top_fraction == 1.0
    assert hypothesis.frozen_hash_matches(loaded, record.params_sha256, family="momentum")
    assert _register(conn, FIXTURE, live).hypothesis_id == record.hypothesis_id
    assert conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone() == (1,)


def test_a_screened_registration_runs_at_its_frozen_fraction(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    """T165c applies the key (#1358), so `NOT_YET_APPLIED_KEYS` is empty and a
    registration at 0.20 loads at 0.20 (until T165c, quant-auditor on #1387 had it
    refused)."""
    assert frozenset() == hypothesis.NOT_YET_APPLIED_KEYS
    _register(conn, _with_turnover(tmp_path, FIXTURE, SLUG, "0.20"), settings)
    loaded = hypothesis.load_frozen(conn, SLUG, settings=settings)
    assert loaded.strategy.turnover_top_fraction == 0.20


# --- operations files (paper plan T160b; strategy-lab spec req 1, amendment 2026-10-10) --


@pytest.fixture
def ops(lab_store: duckdb.DuckDBPyConnection, settings: Settings, tmp_path: Path) -> Any:
    """`test_promotion`'s lab family (H1's twin with rules and a counted trial) and a
    complete two-variant sweep whose argmax is v1; the operations file runs v2."""
    from backtest.test_promotion import Lab

    root = tmp_path / "ops-files"
    root.mkdir()
    built = Lab(lab_store, settings, root)
    built.trial(built.twin(), 0.3)
    sweep, variants = built.sweep([0.15, 0.25])
    for variant, sharpe in zip(variants, (1.5, 0.4), strict=True):
        built.trial(variant, sharpe)
    built.sweep_row, built.variants = sweep, variants  # type: ignore[attr-defined]
    return built


OPS_SLUG = "ops-book"
OPS_REASON = "a machine test for the shakedown, chosen for trading activity"
_OPS_TABLES = ("hypotheses", "hypothesis_fingerprints", "owner_decisions", "trials")


def _ops_counts(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    return {
        table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # type: ignore[index]
        for table in _OPS_TABLES
    }


def _ops_register(ops: Any, path: Path, variant_slug: str) -> hypothesis.OperationsBookOutcome:
    from backtest.test_promotion import CODE

    return hypothesis.register_operations_book(
        ops.conn,
        path,
        variant_slug,
        OPS_REASON,
        registered_by="owner",
        settings=ops.settings,
        code_vintage=CODE,
    )


def _ops_refused(ops: Any, path: Path, variant_slug: str, match: str) -> None:
    before = _ops_counts(ops.conn)
    with pytest.raises(LabRegistrationError, match=match):
        _ops_register(ops, path, variant_slug)
    assert _ops_counts(ops.conn) == before


def test_an_operations_file_registers_with_its_decision_and_n_and_v_unchanged(ops: Any) -> None:
    from tradepartner.backtest import results

    sweep, (_v1, v2) = ops.sweep_row, ops.variants
    n_before = results.family_n(ops.conn, "momentum")
    v_before = registry.family_sharpes(ops.conn, "momentum").variance("excess_spy")
    trials_before = _ops_counts(ops.conn)["trials"]

    outcome = _ops_register(ops, ops.file(OPS_SLUG, 0.25), v2.slug)

    record = registry.get_hypothesis(ops.conn, OPS_SLUG)
    assert outcome.registered == record and outcome.variant == v2
    assert _fingerprint(record) == _fingerprint(v2)
    (stored,) = ops.conn.execute(  # type: ignore[misc]
        "SELECT fingerprint FROM hypothesis_fingerprints WHERE hypothesis_id = ?",
        [record.hypothesis_id],
    ).fetchone()
    assert stored == _fingerprint(v2)
    decision = lab_registry.operations_book_for(ops.conn, record.hypothesis_id)
    assert decision is not None and decision.decision_id == outcome.decision_id
    assert decision.reason == OPS_REASON
    assert decision.values["sweep_id"] == sweep.sweep_id
    assert decision.values["variant_hypothesis_id"] == v2.hypothesis_id
    assert decision.values["operations_hypothesis_id"] == record.hypothesis_id
    assert decision.values["variant_index"] == 2
    assert decision.values["sharpe_annual_excess_spy"] == pytest.approx(0.4)
    assert decision.values["family_n"] == n_before
    assert decision.values["variant_trial_current"] is True
    # Not a promotion: no promotion row, so every spend, cap and identity rule reads
    # it as unpromoted; and no trial, so N and V are unchanged.
    assert lab_registry.promotion_for(ops.conn, record.hypothesis_id) is None
    assert results.family_n(ops.conn, "momentum") == n_before
    assert registry.family_sharpes(ops.conn, "momentum").variance("excess_spy") == v_before
    assert _ops_counts(ops.conn)["trials"] == trials_before


def test_an_operations_file_accepts_a_stale_ok_trial_and_records_it(ops: Any) -> None:
    """A variant whose `ok` trial is from an earlier code vintage (stale, still counted
    in N) is accepted: a rerun would only add trials to N. The decision says so."""
    v2 = ops.variants[1]
    outcome = hypothesis.register_operations_book(
        ops.conn,
        ops.file(OPS_SLUG, 0.25),
        v2.slug,
        OPS_REASON,
        registered_by="owner",
        settings=ops.settings,
        code_vintage="d" * 64,
    )
    assert outcome.values["variant_trial_current"] is False
    assert outcome.values["variant_trial_id"] is not None


def test_an_operations_file_is_refused_without_the_flag(ops: Any) -> None:
    path = ops.file(OPS_SLUG, 0.25)
    before = _ops_counts(ops.conn)
    with pytest.raises(LabRegistrationError, match="operations file"):
        hypothesis.register(ops.conn, path, registered_by="owner", settings=ops.settings)
    assert _ops_counts(ops.conn) == before


def test_an_operations_file_with_a_changed_non_fingerprint_key_is_refused(ops: Any) -> None:
    """`gap.count_share_threshold` is outside the fingerprint: the fingerprint check
    passes and the canonical frozen-set check (`frozen_set_differences`) names it."""
    v2 = ops.variants[1]
    path = ops.file(OPS_SLUG, 0.25)
    text = path.read_text()
    assert "count_share_threshold = 0.05" in text
    path.write_text(text.replace("count_share_threshold = 0.05", "count_share_threshold = 0.04"))
    parsed = hypothesis.parse_file(path)
    params = hypothesis.frozen_params(parsed, ops.settings)
    assert frozen.fingerprint("momentum", params, parsed.in_sample_start) == _fingerprint(v2)
    _ops_refused(ops, path, v2.slug, r"frozen set differs.*gap\.count_share_threshold")


def test_an_operations_file_of_a_variant_with_no_ok_trial_is_refused(ops: Any) -> None:
    _sweep, (v3,) = ops.sweep([0.35], slug="mom-unrun")
    _ops_refused(ops, ops.file(OPS_SLUG, 0.35), v3.slug, "no counted ok trial")


def test_an_operations_file_outside_paper_families_is_refused(
    ops: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hypothesis, "PAPER_FAMILIES", ("profitability",))
    _ops_refused(ops, ops.file(OPS_SLUG, 0.25), ops.variants[1].slug, "PAPER_FAMILIES")


def test_an_operations_file_of_a_forward_holdout_family_is_refused(ops: Any) -> None:
    """ADR 0016 point 4: the family registered before its holdout starts, so the
    holdout is forward; only the promoted hypothesis's paper book may trade it."""
    from backtest.test_promotion import HOLDOUT

    ops.conn.execute(
        "UPDATE hypotheses SET registered_at = ? WHERE family = 'momentum'",
        [datetime(HOLDOUT[0].year, 1, 2, 12, tzinfo=UTC)],
    )
    _ops_refused(ops, ops.file(OPS_SLUG, 0.25), ops.variants[1].slug, "is forward")


def test_an_operations_file_of_a_variant_already_named_is_refused(ops: Any) -> None:
    v1, v2 = ops.variants
    _ops_register(ops, ops.file(OPS_SLUG, 0.25), v2.slug)
    _ops_refused(ops, ops.file("ops-book-2", 0.25), v2.slug, "already named by decision")
    # A promotion row naming v1 refuses v1 too.
    registry.record_decision(
        ops.conn,
        kind="operations_book",
        reason="stand-in",
        values={"variant_hypothesis_id": v1.hypothesis_id},
    )
    ops.conn.execute(
        "UPDATE owner_decisions SET kind = 'promotion' WHERE decision_id = "
        "(SELECT MAX(decision_id) FROM owner_decisions)"
    )
    _ops_refused(ops, ops.file("ops-book-3", 0.15), v1.slug, "already named by decision")


def test_an_operations_file_of_a_non_variant_or_registered_slug_is_refused(ops: Any) -> None:
    v2 = ops.variants[1]
    _ops_refused(ops, ops.file(OPS_SLUG, 0.25), "h1", "not a sweep variant")
    _ops_refused(ops, ops.file("h1", 0.25), v2.slug, "already registered")
    with pytest.raises(ValueError, match="reason"):
        hypothesis.register_operations_book(
            ops.conn, ops.file(OPS_SLUG, 0.25), v2.slug, " ", registered_by="owner"
        )
