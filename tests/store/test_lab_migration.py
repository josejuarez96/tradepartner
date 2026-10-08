"""The lab migration, schema version 16, "L" (#1195, strategy-lab plan T113; spec
Definitions "Frozen-key defaults", "Family rules", "Fingerprint"; "Data / interfaces";
the acceptance criterion "Migration identity").

On a version-15 ("P") store holding Phase 3 rows, H1's fixture twin included (stored
pre-lab, without `schedule.*`), a duplicate registration of the twin's fingerprint (a
Phase 3 prose-only re-registration) and a `profitability` hypothesis: every
pre-migration row of `trial_results` and `owner_decisions` (the two rebuilt tables)
is byte-identical after it, every other table's DDL and rows are unchanged,
`pre_lab_hypotheses` holds exactly the pre-existing `hypotheses` rows,
`hypothesis_fingerprints` one row per hypothesis (both duplicates kept, the earliest
returned), `family_rules` one row per family from its earliest hypothesis with the
live caps; no `register_hypothesis` call runs; a read-only open of a version-15 store
still serves the non-lab reads; `is_lab_initialised` is false before and true after.
Every store here is an in-memory or temp-file fixture, never the owner's store.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner import config
from tradepartner.backtest import frozen, hypothesis
from tradepartner.config import FORBIDDEN_AXIS_PREFIXES, Settings
from tradepartner.store import lab_registry, lab_schema, registry, schema

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "hypotheses"
TWIN_FILE = _FIXTURES / "fixture-momentum.md"
PROF_FILE = _FIXTURES / "fixture-profitability.md"
_SCHEDULE = ("schedule.",)
_CUTOFF = datetime(2022, 12, 30, 21, 0, tzinfo=UTC)
#: Caps the migration must copy from the live `Settings`, not the defaults.
_LIVE_LAB = {"max_family_holdout_spends": 5, "max_family_promotions": 4}
_REBUILT = ("trial_results", "owner_decisions")


def _settings() -> Settings:
    return Settings(_env_file=None, store={"path": "/nonexistent/real.duckdb"})  # type: ignore[call-arg]


def _live_settings() -> Settings:
    base = _settings()
    return base.model_copy(update={"lab": base.lab.model_copy(update=_LIVE_LAB)})


@pytest.fixture(autouse=True)
def _live_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """The migration reads the live caps through `config.get_settings`."""
    monkeypatch.setattr(config, "get_settings", _live_settings)


def _register(
    conn: duckdb.DuckDBPyConnection,
    path: Path,
    *,
    slug: str | None = None,
    doc_sha256: str | None = None,
    drop: tuple[str, ...] = (),
) -> registry.HypothesisRecord:
    parsed = hypothesis.parse_file(path)
    params = {
        key: value
        for key, value in hypothesis.frozen_params(parsed, _settings()).items()
        if not key.startswith(drop)
    }
    return registry.register_hypothesis(
        conn,
        slug=slug or parsed.slug,
        family=parsed.family,
        title=parsed.title,
        doc_path=path.as_posix(),
        doc_sha256=doc_sha256 or parsed.doc_sha256,
        params=params,
        in_sample_start=parsed.in_sample_start,
        holdout_start=parsed.holdout_start,
        holdout_end=parsed.holdout_end,
        registered_by="owner",
        settings=_settings(),
    )


def _trial(
    conn: duckdb.DuckDBPyConnection, record: registry.HypothesisRecord, tmp_path: Path, status: str
) -> int:
    handle = registry.open_trial(
        conn,
        hypothesis_id=record.hypothesis_id,
        kind="in_sample",
        start_session=record.in_sample_start,
        end_session=date(2022, 12, 30),
        data_cutoff=_CUTOFF,
        synthetic=False,
        run_by="owner",
        settings=_settings(),
        repo_dir=tmp_path,
    )
    if status == "ok":
        statistics = registry.ResultStatistics()
        assert registry.write_result(conn, handle, statistics) == "ok"
    else:
        registry.close_trial(conn, handle, status, "phase 3 refusal")
    return handle.trial_id


def _version_15_store(conn: duckdb.DuckDBPyConnection, tmp_path: Path) -> dict[str, int]:
    """A store as version 15 left it (no lab table; version 16 adds nothing else)
    holding Phase 3 rows: H1's fixture twin registered pre-lab, a prose-only
    re-registration of it (same frozen set, new slug and doc hash), a
    `profitability` hypothesis, `ok` and refused trials, and owner decisions."""
    schema.init_schema(conn)
    assert not lab_schema.is_lab_initialised(conn)
    conn.execute("UPDATE schema_version SET version = 15")
    twin = _register(conn, TWIN_FILE, drop=_SCHEDULE)
    duplicate = _register(conn, TWIN_FILE, slug="fixture-momentum-prose", doc_sha256="e" * 64)
    prof = _register(conn, PROF_FILE, drop=_SCHEDULE)
    ok = _trial(conn, twin, tmp_path, "ok")
    _trial(conn, twin, tmp_path, "refused_window")
    _trial(conn, prof, tmp_path, "ok")
    registry.record_decision(
        conn,
        kind="gap_signoff",
        reason="gaps read and accepted",
        values={"missing_tail": 0},
        hypothesis_id=twin.hypothesis_id,
        trial_id=ok,
    )
    registry.record_decision(
        conn, kind="gap_override", reason="one stale name", values={}, trial_id=ok
    )
    conn.execute(
        "INSERT INTO prices_daily VALUES "
        "('SEC_A', '2022-12-30', 1, 1, 1, 1, 1, ?, ?, 'test', 'bar')",
        [_CUTOFF, _CUTOFF],
    )
    return {
        "twin": twin.hypothesis_id,
        "duplicate": duplicate.hypothesis_id,
        "prof": prof.hypothesis_id,
    }


@pytest.fixture
def v15(tmp_path: Path) -> Iterator[tuple[duckdb.DuckDBPyConnection, dict[str, int]]]:
    conn = duckdb.connect(":memory:")
    try:
        ids = _version_15_store(conn, tmp_path)
        yield conn, ids
    finally:
        conn.close()


def _tables(conn: duckdb.DuckDBPyConnection) -> set[str]:
    return {t for (t,) in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()}


def _snapshot(conn: duckdb.DuckDBPyConnection, tables: set[str]) -> dict[str, Any]:
    """Each table's DDL, row count, checksum and rows in insertion order."""
    out: dict[str, Any] = {}
    for table in sorted(tables):
        (ddl,) = conn.execute(  # type: ignore[misc]
            "SELECT sql FROM duckdb_tables() WHERE table_name = ?", [table]
        ).fetchone()
        (count, checksum) = conn.execute(  # type: ignore[misc]
            f"SELECT COUNT(*), md5(COALESCE(string_agg(t::VARCHAR, '|' ORDER BY rowid), '')) "
            f"FROM {table} t"
        ).fetchone()
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
        out[table] = (ddl, count, checksum, rows)
    return out


def _hypothesis_ids(conn: duckdb.DuckDBPyConnection) -> list[int]:
    return [
        i for (i,) in conn.execute("SELECT hypothesis_id FROM hypotheses ORDER BY 1").fetchall()
    ]


def test_version_is_17_and_16_is_the_pre_expansion_seams_version() -> None:
    assert schema.CURRENT_SCHEMA_VERSION == 17
    assert schema._PRE_EXPANSION_SEAMS_VERSION == 16
    assert schema._PRE_LAB_VERSION == 15


def test_registry_table_names_gain_the_lab_tables() -> None:
    assert schema.REGISTRY_TABLE_NAMES[:8] == (
        "hypotheses",
        "trials",
        "trial_results",
        "trial_metrics",
        "trial_rebalances",
        "trial_equity",
        "trial_weights",
        "owner_decisions",
    )
    assert schema.REGISTRY_TABLE_NAMES[8:] == lab_schema.LAB_TABLE_NAMES


def test_migration_identity(v15: tuple[duckdb.DuckDBPyConnection, dict[str, int]]) -> None:
    """The spec's "Migration identity" criterion."""
    conn, _ = v15
    before_tables = _tables(conn)
    before = _snapshot(conn, before_tables)
    hypotheses_before = _hypothesis_ids(conn)

    schema.init_schema(conn)

    after = _snapshot(conn, before_tables)
    for table in sorted(before_tables - {"schema_version", *_REBUILT}):
        assert after[table] == before[table], table
    for table in _REBUILT:
        _, count, checksum, rows = before[table]
        # Byte-identical rows in insertion order; only the CHECK's text widened.
        assert after[table][1:] == (count, checksum, rows), table
        assert after[table][0] != before[table][0]
    assert before["owner_decisions"][1] == 2 and before["trial_results"][1] == 3
    pre_lab = conn.execute("SELECT hypothesis_id FROM pre_lab_hypotheses ORDER BY 1").fetchall()
    assert [i for (i,) in pre_lab] == hypotheses_before
    versions = conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall()
    assert versions == [(15,), (16,), (17,)]
    assert _tables(conn) == before_tables | set(lab_schema.LAB_TABLE_NAMES)
    # Only the three populated lab tables have rows.
    for table in lab_schema.LAB_TABLE_NAMES:
        (count,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # type: ignore[misc]
        populated = table in ("pre_lab_hypotheses", "hypothesis_fingerprints", "family_rules")
        assert bool(count) == populated, table


def test_is_lab_initialised_false_before_and_true_after(
    v15: tuple[duckdb.DuckDBPyConnection, dict[str, int]],
) -> None:
    conn, _ = v15
    assert not lab_schema.is_lab_initialised(conn)
    schema.init_schema(conn)
    assert lab_schema.is_lab_initialised(conn)
    # The widened enumerations take the lab's values.
    for table, column in (("trial_results", "status"), ("owner_decisions", "kind")):
        _, values = lab_schema._current_enum(conn, table, column)
        added = (
            lab_schema.LAB_TRIAL_STATUSES
            if table == "trial_results"
            else (lab_schema.LAB_DECISION_KINDS)
        )
        assert all(f"'{value}'" in values for value in added)


def test_every_hypothesis_gets_its_fingerprint_duplicates_kept(
    v15: tuple[duckdb.DuckDBPyConnection, dict[str, int]],
) -> None:
    conn, ids = v15
    schema.init_schema(conn)
    stored = dict(
        conn.execute("SELECT hypothesis_id, fingerprint FROM hypothesis_fingerprints").fetchall()
    )
    assert sorted(stored) == _hypothesis_ids(conn)
    for hypothesis_id, fingerprint in stored.items():
        record = registry.get_hypothesis_by_id(conn, hypothesis_id)
        expected = frozen.fingerprint(
            record.family, frozen.frozen_values(record), record.in_sample_start
        )
        assert fingerprint == expected
    # The twin stored without `schedule.*` and its re-registration with it: one test.
    assert stored[ids["twin"]] == stored[ids["duplicate"]]
    assert stored[ids["prof"]] != stored[ids["twin"]]
    earliest = lab_registry.fingerprint_registered(conn, stored[ids["twin"]])
    assert earliest is not None and earliest.hypothesis_id == ids["twin"]
    assert lab_registry.grandfathered_fingerprints(conn) == {
        stored[ids["twin"]]: (ids["twin"], ids["duplicate"])
    }


def test_family_rules_from_each_family_earliest_hypothesis(
    v15: tuple[duckdb.DuckDBPyConnection, dict[str, int]],
) -> None:
    conn, ids = v15
    schema.init_schema(conn)
    live = _live_settings().lab
    families = [f for (f,) in conn.execute("SELECT family FROM family_rules ORDER BY 1").fetchall()]
    assert families == ["momentum", "profitability"]
    for family, first in (("momentum", ids["twin"]), ("profitability", ids["prof"])):
        rules = lab_registry.family_rules(conn, family)
        record = registry.get_hypothesis_by_id(conn, first)
        assert rules is not None
        assert rules.first_hypothesis_id == first
        assert rules.parent_family is None  # FAMILY_PARENTS: both roots
        assert (rules.holdout_start, rules.holdout_end, rules.in_sample_start) == (
            record.holdout_start,
            record.holdout_end,
            record.in_sample_start,
        )
        expected = {
            key: value
            for key, value in frozen.frozen_values(record).items()
            if key.startswith(FORBIDDEN_AXIS_PREFIXES)
        }
        assert rules.fixed_params == expected
        assert rules.fixed_params["costs.per_side_bps"] == record.params["costs.per_side_bps"]
        assert rules.max_family_holdout_spends == live.max_family_holdout_spends == 5
        assert rules.max_family_promotions == live.max_family_promotions == 4
        assert rules.min_sharpe_variance_annual == live.min_sharpe_variance_annual
        assert rules.axis_lattice == live.axis_lattice
        assert rules.sr_star_seed_annual is None
    # The twin and its duplicate meet their family's rules: nothing grandfathered.
    assert lab_registry.grandfathered_members(conn) == []


def test_the_migration_registers_no_hypothesis(
    v15: tuple[duckdb.DuckDBPyConnection, dict[str, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, _ = v15
    calls: list[Any] = []
    real = registry.register_hypothesis

    def recording(*args: Any, **kwargs: Any) -> registry.HypothesisRecord:
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(registry, "register_hypothesis", recording)
    count = len(_hypothesis_ids(conn))
    schema.init_schema(conn)
    assert calls == []
    assert len(_hypothesis_ids(conn)) == count


def test_a_second_open_changes_nothing_and_marks_no_later_hypothesis(
    v15: tuple[duckdb.DuckDBPyConnection, dict[str, int]],
) -> None:
    conn, _ = v15
    schema.init_schema(conn)
    lab = set(lab_schema.LAB_TABLE_NAMES) | {"schema_version"}
    first = _snapshot(conn, lab)
    later = _register(conn, PROF_FILE, slug="fixture-profitability-later", doc_sha256="f" * 64)
    schema.init_schema(conn)
    assert _snapshot(conn, lab) == first
    assert not lab_registry.is_pre_lab(conn, later.hypothesis_id)


def test_a_store_with_no_hypothesis_gets_empty_lab_tables(tmp_path: Path) -> None:
    conn = duckdb.connect(":memory:")
    try:
        schema.init_schema(conn)
        conn.execute("UPDATE schema_version SET version = 15")
        schema.init_schema(conn)
        assert lab_schema.is_lab_initialised(conn)
        for table in lab_schema.LAB_TABLE_NAMES:
            assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)
    finally:
        conn.close()


def test_a_fresh_store_stays_without_the_lab_tables() -> None:
    """A store created at the current version has no lab tables (plan choice 2: the
    plain fixture store keeps the Phase 3 rules; the `lab_store` fixture applies
    them)."""
    conn = duckdb.connect(":memory:")
    try:
        schema.init_schema(conn)
        assert not lab_schema.is_lab_initialised(conn)
        assert conn.execute("SELECT version FROM schema_version").fetchall() == [(17,)]
    finally:
        conn.close()


def test_a_failed_population_leaves_the_store_at_version_15(
    v15: tuple[duckdb.DuckDBPyConnection, dict[str, int]], monkeypatch: pytest.MonkeyPatch
) -> None:
    conn, _ = v15
    before = _snapshot(conn, _tables(conn))

    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(lab_registry, "write_family_rules", boom)
    with pytest.raises(RuntimeError, match="boom"):
        schema.init_schema(conn)
    assert _snapshot(conn, _tables(conn)) == before
    assert not lab_schema.is_lab_initialised(conn)


def test_read_only_open_of_a_version_15_store_serves_non_lab_reads(tmp_path: Path) -> None:
    path = tmp_path / "v15.duckdb"
    conn = duckdb.connect(str(path))
    try:
        ids = _version_15_store(conn, tmp_path)
    finally:
        conn.close()

    ro = duckdb.connect(str(path), read_only=True)
    try:
        schema.init_schema(ro)
        twin = registry.get_hypothesis(ro, "fixture-momentum")
        assert twin.hypothesis_id == ids["twin"]
        assert registry.get_hypothesis_by_id(ro, ids["prof"]).family == "profitability"
        assert registry.family_holdout_spends(ro, "momentum") == []
        assert ro.execute("SELECT COUNT(*) FROM trial_results").fetchone() == (3,)
        assert ro.execute("SELECT COUNT(*) FROM owner_decisions").fetchone() == (2,)
        assert not lab_schema.is_lab_initialised(ro)
        with pytest.raises(lab_schema.LabNotInitialised):
            lab_registry.is_pre_lab(ro, ids["twin"])
    finally:
        ro.close()

    rw = duckdb.connect(str(path))
    try:
        schema.init_schema(rw)
        assert lab_schema.is_lab_initialised(rw)
        assert lab_registry.is_pre_lab(rw, ids["twin"])
    finally:
        rw.close()
