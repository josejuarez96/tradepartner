"""Tests for `tradepartner.backtest.hypothesis` (spec req 10, plan T35).

Pre-registration freezes parameters: the hypothesis file is the source of truth for
the keys it names, the live `Settings` fill the rest of the spec's frozen list at
registration, and a run reads the frozen values back, never the live ones, so no
environment variable can move a registered hypothesis's holdout or threshold.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path

import duckdb
import pytest

from tradepartner.backtest import hypothesis
from tradepartner.backtest.hypothesis import HypothesisFileError
from tradepartner.config import Settings
from tradepartner.store import registry, schema

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "hypotheses" / "fixture-momentum.md"
SLUG = "fixture-momentum"

# Spec req 10: the frozen sections (every key) and the single frozen keys.
FROZEN_SECTIONS = (
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
    required = hypothesis.required_keys()
    assert {"holdout.start", "holdout.end"} <= required
    assert {k for k in _spec_frozen_keys() if k.startswith(("strategy.", "costs."))} <= required
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
    assert set(frozen) == _spec_frozen_keys()
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


def test_changed_file_gives_a_new_hypothesis(
    tmp_path: Path, conn: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    path = _copy(tmp_path)
    first = _register(conn, path, settings)
    path.write_text(path.read_text() + "\nA prose edit outside the parameter block.\n")
    second = _register(conn, path, settings)
    assert second.hypothesis_id > first.hypothesis_id
    assert second.doc_sha256 != first.doc_sha256
    assert second.params_sha256 == first.params_sha256


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
    with pytest.raises(registry.RegistryError, match="family"):
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
    assert hypothesis.frozen_params_of(frozen) == record.params
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
