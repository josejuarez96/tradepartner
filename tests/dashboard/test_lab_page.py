"""The Lab's sweep-only page and its callback boundaries (T294)."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from tradepartner.backtest.results import NextTrialCost
from tradepartner.config import Settings
from tradepartner.dashboard import lab_page
from tradepartner.dashboard.lab_files import FileKind, GitState, LabFile, RegistryState
from tradepartner.dashboard.lab_run import Busy, SlotRecord, SlotStatus, Taken
from tradepartner.dashboard.lab_verdict import InFlight, LabVerdict, SweepVerdicts


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
                RegistryState.REGISTERED,
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
