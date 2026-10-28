"""The Lab's sweep-only page and its callback boundaries (T294)."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import duckdb
import pytest

from tradepartner.backtest import holdout, sweep_report
from tradepartner.backtest.results import NextTrialCost
from tradepartner.config import Settings
from tradepartner.dashboard import lab_page
from tradepartner.dashboard.lab_files import FileKind, GitState, LabFile, RegistryState
from tradepartner.dashboard.lab_run import Busy, SlotRecord, SlotStatus, Taken
from tradepartner.dashboard.lab_verdict import InFlight, LabVerdict, SweepVerdicts
from tradepartner.store import lab_registry, registry


def _file(
    *, state: RegistryState = RegistryState.NOT_REGISTERED, git: GitState = GitState.MERGED
) -> LabFile:
    return LabFile(
        FileKind.SWEEP, "b10.md", "b10", Path("/tmp/docs/sweeps/b10.md"), state, git, False
    )


def test_registration_reason_is_visible_for_unmerged_and_hypothesis() -> None:
    assert "not merged" in lab_page.register_refusal(_file(git=GitState.NOT_MERGED))
    hypothesis = LabFile(
        FileKind.HYPOTHESIS,
        "h.md",
        "h",
        Path("/tmp/h.md"),
        RegistryState.NOT_REGISTERED,
        GitState.MERGED,
        True,
    )
    assert "desk" in lab_page.register_refusal(hypothesis).lower()


def test_register_confirm_uses_callback_action(monkeypatch: pytest.MonkeyPatch) -> None:
    action = Mock(return_value=Mock(status="created", output="created"))
    monkeypatch.setattr(lab_page.lab_register, "register", action)
    settings = Settings(_env_file=None)
    lab_page._on_register(settings, "b10.md")
    action.assert_called_once_with(settings, lab_page.DOCS_ROOT, FileKind.SWEEP, "b10.md")


def test_slot_states_are_plain_words() -> None:
    assert "running" in lab_page.status_sentence(SlotStatus("running", None, ""))
    assert "ended" in lab_page.status_sentence(SlotStatus("ended", None, ""))
    assert "starting" in lab_page.status_sentence(SlotStatus("starting", None, ""))


def test_busy_slot_names_its_cause() -> None:
    assert "other-run" in lab_page.busy_sentence(Busy(("other-run",)))


def test_run_callback_refuses_when_slot_busy_without_spawning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, store={"path": str(tmp_path / "store.duckdb")})
    now = datetime.now(UTC)
    monkeypatch.setattr(lab_page.lab_run, "take", Mock(return_value=Busy(("other-run",))))
    spawn = Mock()
    monkeypatch.setattr(lab_page.lab_child, "spawn", spawn)
    lab_page._start_run(settings, "b10", None, now, ("other-run",), 0)
    spawn.assert_not_called()


def test_failed_spawn_abandons_slot_and_scrubs_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, store={"path": str(tmp_path / "store.duckdb")})
    now = datetime.now(UTC)
    state: dict[str, object] = {}
    monkeypatch.setattr(lab_page, "st", Mock(session_state=state))
    taken = Taken(tmp_path / "store.duckdb.lab-run.json", "token")
    monkeypatch.setattr(lab_page.lab_run, "take", Mock(return_value=taken))
    abandon = Mock(return_value=True)
    monkeypatch.setattr(lab_page.lab_run, "abandon", abandon)
    monkeypatch.setattr(
        lab_page.lab_child,
        "spawn",
        Mock(side_effect=lab_page.lab_child.LabRefused("secret sentinel")),
    )
    monkeypatch.setattr(
        lab_page, "_safe_scrub", lambda text, _settings: text.replace("secret", "[redacted]")
    )
    lab_page._start_run(settings, "b10", None, now, (), 0, Mock())
    abandon.assert_called_once_with(taken.slot_path, taken)
    assert "secret" not in str(state[lab_page._OUTCOME])


def test_record_failure_after_spawn_keeps_slot_reserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, store={"path": str(tmp_path / "store.duckdb")})
    now = datetime.now(UTC)
    state: dict[str, object] = {}
    monkeypatch.setattr(lab_page, "st", Mock(session_state=state))
    taken = Taken(tmp_path / "store.duckdb.lab-run.json", "token")
    monkeypatch.setattr(lab_page.lab_run, "take", Mock(return_value=taken))
    abandon = Mock()
    monkeypatch.setattr(lab_page.lab_run, "abandon", abandon)
    monkeypatch.setattr(
        lab_page.lab_child,
        "spawn",
        Mock(return_value=SimpleNamespace(child=Mock(pid=7), log_path=tmp_path / "run.log")),
    )
    monkeypatch.setattr(lab_page.lab_run, "record", Mock(side_effect=OSError("disk full")))
    monkeypatch.setattr(lab_page, "_safe_scrub", lambda text, _settings: text)
    lab_page._start_run(settings, "b10", None, now, (), 0, Mock())
    abandon.assert_not_called()
    assert "may be running" in str(state[lab_page._OUTCOME])


def test_changed_cost_quote_refuses_before_slot_or_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tradepartner.dashboard.app as app

    state: dict[str, object] = {}
    monkeypatch.setattr(lab_page, "st", Mock(session_state=state))

    @contextmanager
    def store(_settings: Settings):
        yield Mock()

    monkeypatch.setattr(app, "open_store_connection", store)
    monkeypatch.setattr(
        lab_page.lab_registry,
        "sweep_by_slug",
        Mock(return_value=SimpleNamespace(sweep_id=3, family="momentum")),
    )
    monkeypatch.setattr(
        lab_page.lab_queries,
        "plan_run",
        Mock(return_value=SimpleNamespace(variants=(object(),))),
    )
    monkeypatch.setattr(lab_page.lab_registry, "family_rules", Mock(return_value=None))
    old = NextTrialCost(1, 2, 0.1, 0.2, "excess_spy", False, 0.1)
    new = NextTrialCost(2, 3, 0.2, 0.3, "excess_spy", False, 0.1)
    raw = NextTrialCost(1, 2, 0.1, 0.2, "raw", False, 0.1)
    monkeypatch.setattr(lab_page.results, "next_trial_cost", Mock(side_effect=(new, raw)))
    start = Mock()
    monkeypatch.setattr(lab_page, "_start_run", start)
    lab_page._on_run(Settings(_env_file=None), "b10", 3, 1, old, raw)
    assert "cost changed" in str(state[lab_page._OUTCOME]).lower()
    start.assert_not_called()


def test_hypothesis_window_uses_frozen_cadence_and_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hypothesis = SimpleNamespace(
        hypothesis_id=8,
        family="momentum",
        in_sample_start=date(2018, 1, 1),
        holdout_start=date(2020, 1, 1),
        holdout_end=date(2020, 12, 31),
    )
    monkeypatch.setattr(lab_page.registry, "get_hypothesis", Mock(return_value=hypothesis))
    monkeypatch.setattr(lab_page.registry, "is_sweep_variant", Mock(return_value=False))
    monkeypatch.setattr(lab_page.lab_registry, "promotion_for", Mock(return_value=object()))
    monkeypatch.setattr(lab_page.registry, "boundary_date", Mock(return_value=date(2019, 6, 30)))
    monkeypatch.setattr(
        lab_page,
        "frozen_values",
        Mock(
            return_value={
                "gap.count_share_threshold": 0.1,
                "schedule.rebalance_cadence": "month_end",
            }
        ),
    )
    window = lab_page._hypothesis_window(Mock(), "h")
    assert window == holdout.Window(date(2018, 1, 1), date(2019, 6, 28))


def test_hypothesis_window_refuses_without_promotion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        lab_page.registry,
        "get_hypothesis",
        Mock(return_value=SimpleNamespace(hypothesis_id=8, family="momentum")),
    )
    monkeypatch.setattr(lab_page.registry, "is_sweep_variant", Mock(return_value=False))
    monkeypatch.setattr(lab_page.lab_registry, "promotion_for", Mock(return_value=None))
    with pytest.raises(ValueError, match="promoted"):
        lab_page._hypothesis_window(Mock(), "h")


def test_hypothesis_click_rechecks_quiet_and_refuses_without_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tradepartner.dashboard.app as app

    @contextmanager
    def store(_settings: Settings):
        yield Mock()

    state: dict[str, object] = {}
    monkeypatch.setattr(lab_page, "st", Mock(session_state=state))
    monkeypatch.setattr(app, "open_store_connection", store)
    window = holdout.Window(date(2018, 1, 1), date(2019, 6, 28))
    monkeypatch.setattr(lab_page, "_hypothesis_window", Mock(return_value=window))
    monkeypatch.setattr(lab_page, "_hypothesis_start_decision", Mock(return_value="wait"))
    monkeypatch.setattr(
        lab_page.registry, "get_hypothesis", Mock(return_value=SimpleNamespace(hypothesis_id=8))
    )
    monkeypatch.setattr(
        lab_page, "frozen_values", Mock(return_value={"schedule.rebalance_cadence": "month_end"})
    )
    spawn = Mock()
    monkeypatch.setattr(lab_page.lab_child, "spawn", spawn)
    lab_page._on_hypothesis_run(Settings(_env_file=None), "h", window, 8, Mock(), Mock())
    spawn.assert_not_called()
    assert "wait" in str(state[lab_page._OUTCOME]).lower()


@pytest.mark.parametrize("changed", ["registration", "window", "cost"])
def test_hypothesis_click_refuses_changed_quote_before_spawn(
    monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    import tradepartner.dashboard.app as app

    @contextmanager
    def store(_settings: Settings):
        yield Mock()

    state: dict[str, object] = {}
    monkeypatch.setattr(lab_page, "st", Mock(session_state=state))
    monkeypatch.setattr(app, "open_store_connection", store)
    window = holdout.Window(date(2018, 1, 1), date(2019, 6, 28))
    allowed = holdout.Window(date(2018, 1, 1), date(2019, 5, 31)) if changed == "window" else window
    hypothesis = SimpleNamespace(
        hypothesis_id=9 if changed == "registration" else 8, family="momentum"
    )
    monkeypatch.setattr(lab_page.registry, "get_hypothesis", Mock(return_value=hypothesis))
    monkeypatch.setattr(lab_page, "_hypothesis_window", Mock(return_value=allowed))
    monkeypatch.setattr(
        lab_page, "frozen_values", Mock(return_value={"schedule.rebalance_cadence": "month_end"})
    )
    monkeypatch.setattr(lab_page, "_hypothesis_start_decision", Mock(return_value="start"))
    monkeypatch.setattr(lab_page.lab_registry, "family_rules", Mock(return_value=None))
    cost = NextTrialCost(1, 2, 0.1, 0.2, "excess_spy", False, 0.1)
    raw = NextTrialCost(1, 2, 0.1, 0.2, "raw", False, 0.1)
    next_cost = Mock(side_effect=(cost, raw))
    monkeypatch.setattr(lab_page.results, "next_trial_cost", next_cost)
    spawn = Mock()
    monkeypatch.setattr(lab_page.lab_child, "spawn", spawn)
    expected = (
        NextTrialCost(2, 3, 0.2, 0.3, "excess_spy", False, 0.1) if changed == "cost" else cost
    )
    lab_page._on_hypothesis_run(Settings(_env_file=None), "h", window, 8, expected, raw)
    assert "nothing ran" in str(state[lab_page._OUTCOME])
    spawn.assert_not_called()
    if changed == "cost":
        assert next_cost.call_count == 2
    else:
        next_cost.assert_not_called()


def test_status_poll_reruns_once_after_child_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    ui = Mock(session_state={})
    monkeypatch.setattr(lab_page, "st", ui)
    monkeypatch.setattr(
        lab_page.lab_run, "status", Mock(return_value=SlotStatus("ended", None, ""))
    )
    lab_page._poll_status(Settings(_env_file=None))
    lab_page._poll_status(Settings(_env_file=None))
    ui.rerun.assert_called_once_with()


@pytest.mark.parametrize(
    ("file", "busy"),
    [
        (_file(state=RegistryState.REGISTERED), True),
        (_file(state=RegistryState.CHANGED_SINCE_REGISTRATION), False),
        (
            LabFile(
                FileKind.HYPOTHESIS,
                "h.md",
                "h",
                Path("/tmp/h.md"),
                RegistryState.NOT_REGISTERED,
                GitState.MERGED,
                True,
            ),
            False,
        ),
    ],
)
def test_render_has_no_run_on_busy_changed_or_hypothesis(
    monkeypatch: pytest.MonkeyPatch, file: LabFile, busy: bool
) -> None:
    ui = MagicMock()
    ui.container.return_value = nullcontext()
    ui.button.return_value = False
    monkeypatch.setattr(lab_page, "st", ui)
    monkeypatch.setattr(lab_page, "_status", Mock(return_value=busy))
    monkeypatch.setattr(lab_page.lab_files, "list_files", Mock(return_value=[file]))
    monkeypatch.setattr(lab_page.lab_files, "preview", Mock(return_value=Mock()))
    monkeypatch.setattr(lab_page.lab_registry, "sweep_by_slug", Mock(return_value=None))
    lab_page.render(Mock(), Settings(_env_file=None))
    labels = [call.args[0] for call in ui.button.call_args_list]
    assert "Run" not in labels


def test_ended_card_uses_registry_verdict_and_shows_both_rules(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = Settings(_env_file=None, store={"path": str(tmp_path / "store.duckdb")})
    now = datetime.now(UTC)
    rec = SlotRecord(7, "pid-start", "sweep_run", "b10", 2, now.isoformat(), "log", 0)
    monkeypatch.setattr(lab_page, "st", Mock(session_state={}))
    monkeypatch.setattr(lab_page, "_safe_scrub", lambda text, _settings: text)
    monkeypatch.setattr(lab_page.lab_run, "status", Mock(return_value=SlotStatus("ended", rec, "")))
    monkeypatch.setattr(lab_page.lab_run, "release", Mock(return_value=True))
    monkeypatch.setattr(lab_page.lab_verdict, "in_flight", Mock(return_value=InFlight((), ())))
    reading = LabVerdict(
        "sweep_run",
        "b10",
        "complete",
        "complete (2 ok)",
        None,
        True,
        verdicts=SweepVerdicts("b10-v1", 0.8, True, 0.2, False),
    )
    read = Mock(return_value=reading)
    monkeypatch.setattr(lab_page.lab_verdict, "verdict", read)
    assert not lab_page._status(Mock(), settings, now)
    read.assert_called_once()
    words = " ".join(str(call.args[0]) for call in lab_page.st.write.call_args_list)
    assert "complete" in words
    assert "promote at least 0.8: met" in words
    assert "retire below 0.2: not met" in words


def _reading_fixture(*, complete: bool = True) -> tuple[SimpleNamespace, SimpleNamespace]:
    sweep = SimpleNamespace(
        sweep_id=3,
        slug="b10",
        family="momentum",
        selection_statistic="dsr_excess",
        promote_at_least=0.6,
        retire_below=0.2,
        holdout_start="2025-01-01",
        holdout_end="2025-12-31",
        registered_at=datetime(2024, 1, 1, tzinfo=UTC),
        grid_sha256="grid123",
    )
    row = SimpleNamespace(
        variant_index=1,
        slug="b10-v1",
        status="counted",
        trial_id=17,
        dsr_excess=0.731,
        red_flag=True,
        message=None,
    )
    verdicts = SimpleNamespace(
        argmax=row,
        sr_star_high_water_annual=0.49,
        dsr_excess_at_high_water=0.683,
        promote_at_least_met=True,
        retire_below_met=False,
    )
    report = SimpleNamespace(
        rows=(row,),
        family_n=7,
        sr_star_annual=0.43,
        distribution=SimpleNamespace(
            minimum=0.731, q1=0.731, median=0.731, q3=0.731, maximum=0.731
        ),
        red_flag_count=1,
        verdicts=verdicts if complete else None,
        state="complete" if complete else "incomplete (unrun)",
    )
    return sweep, report


def test_result_reading_orders_thresholds_result_rule_and_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweep, report = _reading_fixture()
    events: list[str] = []
    ui = MagicMock()
    ui.write.side_effect = lambda value: events.append(str(value))
    ui.caption.side_effect = lambda value: events.append(str(value))
    ui.expander.side_effect = lambda label: (events.append(f"expander:{label}"), nullcontext())[1]
    ui.selectbox.return_value = 17
    monkeypatch.setattr(lab_page, "st", ui)
    monkeypatch.setattr(lab_page, "_safe_scrub", lambda text, _settings: text)
    monkeypatch.setattr(lab_page.sweep_report, "sweep_report", Mock(return_value=report))
    monkeypatch.setattr(lab_page.registry, "family_holdout_spends", Mock(return_value=[]))
    monkeypatch.setattr(lab_page.registry, "development_boundary", Mock(return_value=None))
    view = SimpleNamespace(
        dsr_rows=(
            SimpleNamespace(
                basis="excess_spy",
                stored_dsr=0.64,
                today=SimpleNamespace(dsr=0.71),
                today_error=None,
            ),
        ),
        rebalances=({"gap_count_share": 0.03},),
    )
    loader = Mock(return_value=view)
    monkeypatch.setattr(lab_page.backtest_page, "load_trial_view", loader)
    lab_page._result_reading(Mock(), Settings(_env_file=None), sweep)
    loader.assert_called_once()
    assert loader.call_args.args[1] == 17
    text = " | ".join(events)
    assert text.index("grid123") < text.index("0.731") < text.index("sweep promote")
    assert text.index("sweep promote") < text.index("expander:How it was computed")
    for value in ("0.6", "0.2", "0.731", "7", "0.43", "0.49", "0.683", "0.64", "0.71", "0.03"):
        assert value in text
    for term in (
        "Pre-registration",
        "Trial",
        "N",
        "SR*",
        "Deflated Sharpe ratio",
        "dsr_excess",
        "Holdout",
        "Survivorship gap",
        "Development boundary",
        "Owner decision",
    ):
        assert f"{term} ⓘ" in text


def test_incomplete_sweep_has_no_argmax_or_desk_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sweep, report = _reading_fixture(complete=False)
    ui = MagicMock()
    ui.selectbox.return_value = None
    monkeypatch.setattr(lab_page, "st", ui)
    monkeypatch.setattr(lab_page.sweep_report, "sweep_report", Mock(return_value=report))
    monkeypatch.setattr(lab_page.registry, "family_holdout_spends", Mock(return_value=[]))
    monkeypatch.setattr(lab_page.registry, "development_boundary", Mock(return_value=None))
    lab_page._result_reading(Mock(), Settings(_env_file=None), sweep)
    words = " ".join(str(call.args[0]) for call in ui.write.call_args_list)
    assert "incomplete" in words
    assert "argmax" not in words
    assert "sweep promote" not in words


def test_registered_values_match_real_sweep_report(
    monkeypatch: pytest.MonkeyPatch, lab_store: duckdb.DuckDBPyConnection, settings: Settings
) -> None:
    start, holdout_start, holdout_end = date(2020, 8, 31), date(2024, 1, 2), date(2026, 9, 30)
    sweep = lab_registry.register_sweep(
        lab_store,
        slug="reading",
        family="momentum",
        title="Reading",
        doc_path="docs/sweeps/reading.md",
        doc_sha256="s" * 64,
        grid={"strategy.top_fraction": [0.2]},
        n_variants=1,
        selection_statistic="dsr_excess",
        expected_excess_cagr_spy_pp=0.0,
        expected_range_pp=(-2.0, 2.0),
        promote_at_least=0.8,
        retire_below=0.2,
        in_sample_start=start,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        registered_by="owner",
        settings=settings,
    )
    hypothesis = registry.register_hypothesis(
        lab_store,
        slug="reading-v1",
        family="momentum",
        title="Reading v1",
        doc_path="docs/hypotheses/reading-v1.md",
        doc_sha256="h" * 64,
        params={
            "costs.per_side_bps": 15.0,
            "execution.fill_price": "close",
            "gap.count_share_threshold": 0.02,
            "universe.top_n_by_cap": 500,
            "strategy.top_fraction": 0.2,
        },
        in_sample_start=start,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        registered_by="owner",
        settings=settings,
    )
    lab_registry.write_sweep_variant(
        lab_store,
        sweep_id=sweep.sweep_id,
        variant_index=1,
        hypothesis_id=hypothesis.hypothesis_id,
        fingerprint="1" * 64,
        variant_params={"strategy.top_fraction": 0.2},
    )
    report = sweep_report.sweep_report(lab_store, sweep.slug)
    events: list[str] = []
    ui = MagicMock()
    ui.write.side_effect = lambda value: events.append(str(value))
    ui.selectbox.return_value = None
    monkeypatch.setattr(lab_page, "st", ui)
    lab_page._result_reading(lab_store, settings, sweep)
    words = " ".join(events)
    assert f"promote_at_least {report.promote_at_least}" in words
    assert f"retire_below {report.retire_below}" in words
    assert f"today's family N {report.family_n}" in words
    assert report.rows[0].slug in words
    assert report.state in words
