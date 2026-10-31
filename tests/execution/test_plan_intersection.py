"""The paper plan records the engine's point-in-time intersection result (T242)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from functools import partial
from pathlib import Path

import duckdb
import pytest

from tradepartner.backtest import engine
from tradepartner.backtest.schedule import read_time
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.config import Settings
from tradepartner.execution import planning
from tradepartner.store import journal, registry, schema
from tradepartner.store.schema import SchemaVersionError

from . import test_planning
from .test_planning import open_env


def test_pre_intersection_read_only_store_refuses_plans_for(fixture_store_path: Path) -> None:
    """The reader names the needed migration before selecting absent columns."""
    with duckdb.connect(str(fixture_store_path)) as conn:
        for column in ("n_assets_intersected", "assets_intersection_skipped"):
            conn.execute(f"ALTER TABLE paper_plans DROP COLUMN {column}")
        conn.execute("DELETE FROM schema_version")
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, current_timestamp)",
            [schema._PRE_ASSETS_INTERSECTION_VERSION],
        )
    with (
        duckdb.connect(str(fixture_store_path), read_only=True) as conn,
        pytest.raises(SchemaVersionError, match="version 38"),
    ):
        journal.plans_for(conn, 1)


@pytest.mark.parametrize(
    ("checked", "skipped", "members"),
    [(0, True, 4), (4, False, 3), (0, False, 6)],
)
def test_plan_row_copies_engine_record_and_reuse_writes_no_second_row(
    fixture_store_path: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checked: int,
    skipped: bool,
    members: int,
) -> None:
    """The journal copies the engine's record; a reused plan is not appended."""
    with open_env(fixture_store_path, tmp_path) as env:
        real = planning._plan_at

        def recorded_plan(*args: object, **kwargs: object) -> object:
            plan = real(*args, **kwargs)  # type: ignore[arg-type]
            return replace(
                plan,
                n_assets_intersected=checked,
                assets_intersection_skipped=skipped,
                n_universe=members,
            )

        monkeypatch.setattr(planning, "_plan_at", recorded_plan)
        assert env.plan().status == "planned"
        assert env.window.window_id is not None
        rows = journal.plans_for(env.conn, env.window.window_id)
        assert len(rows) == 1
        assert (
            rows[0].n_assets_intersected,
            rows[0].assets_intersection_skipped,
            rows[0].n_universe,
        ) == (checked, skipped, members)
        assert env.plan().status == "reused"
        assert journal.plans_for(env.conn, env.window.window_id) == rows


def test_dollar_volume_paper_and_backtest_rows_agree_on_both_sides_of_snapshot(
    fixture_store_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same registered rank rule and store give identical per-date records."""
    frozen_settings = test_planning._frozen_settings

    def dollar_volume_settings(turnover_top_fraction: float | None = None) -> Settings:
        settings = frozen_settings(turnover_top_fraction)
        return settings.model_copy(
            update={
                "universe": settings.universe.model_copy(
                    update={"rank_by": "dollar_volume", "top_n_by_cap": 3}
                )
            }
        )

    monkeypatch.setattr(test_planning, "_frozen_settings", dollar_volume_settings)
    with open_env(fixture_store_path, tmp_path) as env:
        assert env.window.window_id is not None
        env.settings = env.settings.model_copy(
            update={
                "paper": env.settings.paper.model_copy(
                    update={"min_universe_names": 1, "max_universe_drop_fraction": 1.0}
                )
            }
        )
        plans: list[engine.Plan] = []
        real = planning._plan_at

        def capture(
            conn: duckdb.DuckDBPyConnection,
            handle: registry.TrialHandle,
            params: Settings,
            t_i: date,
            family: str,
        ) -> engine.Plan:
            plan = real(conn, handle, params, t_i, family)  # type: ignore[arg-type]
            plans.append(plan)
            return plan

        monkeypatch.setattr(planning, "_plan_at", capture)
        for run_day, rebalance, expected in (
            (date(2020, 6, 1), date(2020, 5, 29), (0, True, 4)),
            (date(2020, 7, 1), date(2020, 6, 30), (4, False, 1)),
        ):
            run = env.run_on(run_day)
            assert env.plan(session=run_day, run=run).status == "planned"
            paper = next(
                row
                for row in journal.plans_for(env.conn, env.window.window_id)
                if row.rebalance_session == rebalance
            )
            assert (
                paper.n_assets_intersected,
                paper.assets_intersection_skipped,
                paper.n_universe,
            ) == expected
            assert (
                plans[-1].n_assets_intersected,
                plans[-1].assets_intersection_skipped,
                plans[-1].n_universe,
            ) == expected
        backtest_handle = registry.open_trial(
            env.conn,
            hypothesis_id=env.hypothesis_id,
            kind="tracking",
            start_session=date(2020, 5, 29),
            end_session=date(2020, 6, 30),
            data_cutoff=read_time(date(2020, 6, 30), "month_end"),
            synthetic=False,
            run_by="test",
            settings=env.settings,
        )
        lend = partial(planning._lend, env.conn)
        with StoreProvider(lend, backtest_handle, env.params, registry_connect=lend) as provider:
            result = engine.run(
                env.params,
                provider,
                date(2020, 5, 29),
                date(2020, 7, 31),
                backtest_handle,
                [env.params.costs.per_side_bps],
                family="momentum",
            )
        registry.write_rebalances(
            env.conn, backtest_handle, result[env.params.costs.per_side_bps].rebalances
        )
        trials = env.conn.execute(
            "SELECT session, n_assets_intersected, assets_intersection_skipped, n_universe "
            "FROM trial_rebalances WHERE trial_id = ? ORDER BY session",
            [backtest_handle.trial_id],
        ).fetchall()
        papers = journal.plans_for(env.conn, env.window.window_id)
        assert trials == [
            (
                paper.rebalance_session,
                paper.n_assets_intersected,
                paper.assets_intersection_skipped,
                paper.n_universe,
            )
            for paper in papers
        ]
