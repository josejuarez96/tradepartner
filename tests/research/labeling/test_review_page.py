"""Tests for `tradepartner.research.labeling.review_page` (plan T123c; research-labeling
spec C10 as #1121 amends it, and the owner's decision on #1330: the page never writes
the store, the CLI locks or finishes after it stops).

Headless through `streamlit.testing.v1.AppTest` on fixture sessions with the research
store in a temporary directory: gold mode on T131's synthetic frame (test_gold's
helpers), review mode on T123b's scripted batch (test_review's World). The page's
script runs from a two-line string that imports the module, so `get_settings`,
`open_read_only` and the server stop can be replaced on the module; one test runs the
real file through its `__main__` guard. The keyboard listener's key handling cannot
run under `AppTest` (T126's first session checks the keys by hand); its key tables and
its presence are asserted here.
"""

from __future__ import annotations

import contextlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from research.labeling.test_gold import (
    DEV_ROWS,
    N_DEV,
    SEED,
    N,
    _exclusion,
    _frame_rows,
    _write_frame,
)
from research.labeling.test_job import World
from research.labeling.test_review import _batch
from tradepartner.config import Settings
from tradepartner.research import datafiles
from tradepartner.research.labeling import gold, review, review_page
from tradepartner.research.labeling.questions import DEFAULT_OPTION_SET
from tradepartner.store import schema
from tradepartner.store.db import configure_connection

PAGE_FILE = Path(review_page.__file__)
SCRIPT = "from tradepartner.research.labeling import review_page\nreview_page.main()\n"
#: Planted in the frame's `rule_*` columns: none may reach the screen.
RULE_SENTINELS = ("RULESTATUSSENTINEL", "RULESUCCESSORSENTINEL")


# --- fixtures ---------------------------------------------------------------------


@pytest.fixture
def gsettings(settings: Settings, tmp_path: Path) -> Settings:
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})
    conn = duckdb.connect(out.store.path)
    try:
        configure_connection(conn)
        schema.init_schema(conn)
    finally:
        conn.close()
    return out


@pytest.fixture
def gold_session(gsettings: Settings, tmp_path: Path) -> gold.GoldSession:
    rows = [
        {**row, "rule_status": RULE_SENTINELS[0], "rule_successor_id": RULE_SENTINELS[1]}
        for row in _frame_rows()
    ]
    frame = _write_frame(tmp_path, rows)
    result = gold.build_gold_session(
        frame,
        SEED,
        N,
        _exclusion(tmp_path, []),
        settings=gsettings,
        n_dev=N_DEV,
        dev_span_min_rows=DEV_ROWS,
    )
    return result.session


@pytest.fixture
def stops(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(review_page, "_stop_server", lambda: calls.append(1))
    return calls


def _app(
    monkeypatch: pytest.MonkeyPatch,
    settings: Settings,
    session: Path,
    stops: list[int],
    extra: tuple[str, ...] = (),
) -> AppTest:
    monkeypatch.setattr(review_page, "get_settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["review_page.py", "--session", str(session), *extra])
    at = AppTest.from_string(SCRIPT, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def _gold_app(
    monkeypatch: pytest.MonkeyPatch, session: gold.GoldSession, stops: list[int]
) -> AppTest:
    return _app(monkeypatch, session.settings, session.session_file, stops)


def _text(at: AppTest) -> str:
    parts: list[str] = []
    for kind in ("markdown", "text", "caption", "title", "subheader", "info", "success", "error"):
        parts += [str(e.value) for e in getattr(at, kind)]
    parts += [str(b.label) for b in at.button]
    for radio in at.radio:
        parts += [str(radio.label), *(str(o) for o in radio.options)]
    return "\n".join(parts)


def _lines(path: Path) -> list[dict[str, Any]]:
    return datafiles.read_jsonl(path) if path.exists() else []


def _current(at: AppTest, session: gold.GoldSession) -> str:
    """The listing end on screen, from its notice text (`Notice <i>: ...`)."""
    for text in at.text:
        if str(text.value).startswith("Notice "):
            i = int(str(text.value).split()[1].rstrip(":"))
            return f"LE{i:03d}"
    raise AssertionError("no notice on screen")


def _press(at: AppTest, key: str) -> AppTest:
    at.button(key=key).click().run()
    assert not at.exception, at.exception
    return at


# --- pure parts -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "stats", "ok"),
    [
        ("localhost", False, True),
        ("127.0.0.1", False, True),
        ("0.0.0.0", False, False),
        (None, False, False),
        ("localhost", True, False),
        ("localhost", None, False),
    ],
)
def test_server_options(address: object, stats: object, ok: bool) -> None:
    passed, message = review_page.server_options_ok(address, stats)
    assert passed is ok
    assert bool(message) is not ok


def test_mode_of_reads_the_session_argument(gsettings: Settings) -> None:
    path = gold.session_path(gsettings)
    assert review_page.mode_of(gsettings, path) == ("gold", None)
    assert review_page.mode_of(gsettings, datafiles.review_path(gsettings, 12)) == ("review", 12)
    with pytest.raises(review_page.PageRefused):
        review_page.mode_of(gsettings, Path("elsewhere.json"))
    with pytest.raises(review_page.PageRefused):
        review_page.page_arguments([])
    args = review_page.page_arguments(["--session", "x.jsonl", "--code-version", "abc"])
    assert args == review_page.Arguments(Path("x.jsonl"), "abc")


def test_relied_on_defaults_to_shown_text_else_outside_alone() -> None:
    eightk = gold.EightKView("8-K", "A1", None, (("2.01", "t"), ("8.01", "u")), None)
    assert review_page.relied_on_choices("notice text", eightk) == [
        "notice",
        "8-K item 2.01",
        "8-K item 8.01",
        "outside",
    ]
    assert review_page.relied_on_choices(None, eightk)[0] == "8-K item 2.01"
    assert review_page.relied_on_choices(None, None) == ["outside"]


def test_budget_note_with_and_without_a_timed_answer() -> None:
    assert "within the 2.0 minutes" in review_page.budget_note(90.0)
    assert "over the 2.0 minutes" in review_page.budget_note(150.0)
    note = review_page.budget_note(None)
    assert "no timed answer" in note and "within" not in note


def test_key_tables_gold_back_on_b_review_b_is_answer_b_only() -> None:
    gold_keys = review_page.gold_key_table(DEFAULT_OPTION_SET.options)
    assert sorted(gold_keys) == sorted([*"123456789", "s", "b"])
    assert gold_keys["b"] == "b ·"  # the back button's label prefix in gold mode
    review_keys = review_page.review_key_table()
    assert sorted(review_keys) == ["a", "b", "u", "w"]
    assert review_page.REVIEW_KEYS["b"] == "b"  # `b` decides answer b, never back
    assert "back" not in review_page.REVIEW_KEYS.values()


# --- refusals ---------------------------------------------------------------------


def test_refuses_off_localhost_through_the_real_script(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession
) -> None:
    real = st.get_option
    monkeypatch.setattr(
        st, "get_option", lambda key: "0.0.0.0" if key == "server.address" else real(key)
    )
    monkeypatch.setattr(sys, "argv", ["p", "--session", str(gold_session.session_file)])
    at = AppTest.from_file(str(PAGE_FILE), default_timeout=30)
    at.run()
    assert not at.exception
    assert any("refusing to render" in str(e.value).lower() for e in at.error)
    assert not at.button


def test_refuses_with_usage_stats_on(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    real = st.get_option
    monkeypatch.setattr(
        st, "get_option", lambda key: True if key == "browser.gatherUsageStats" else real(key)
    )
    at = _gold_app(monkeypatch, gold_session, stops)
    assert any("gatherUsageStats is True" in str(e.value) for e in at.error)
    assert not at.button


def test_gold_start_refuses_when_an_inference_file_exists(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    assert at.button  # the first start renders
    planted = datafiles.data_dir(gold_session.settings) / "inferences" / "deep" / "x.jsonl"
    planted.parent.mkdir(parents=True)
    planted.write_text('{"selected_option": "PLANTEDMODELOUTPUT"}\n', encoding="utf-8")
    at.run()  # the next run of the same tab refuses too, not only a fresh start
    assert any("model output" in str(e.value) for e in at.error)
    assert not at.button
    fresh = _gold_app(monkeypatch, gold_session, stops)
    assert any("model output" in str(e.value) for e in fresh.error)
    assert "PLANTEDMODELOUTPUT" not in _text(fresh)


# --- gold mode --------------------------------------------------------------------


def test_case_renders_passages_progress_and_nine_buttons_and_nothing_hidden(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    text = _text(at)
    lid = _current(at, gold_session)
    i = int(lid[2:])
    assert f"Notice {i}: the merger closed." in text
    assert "Completion." in text and "Other events." in text  # the 8-K items inline
    assert "case 1 of 9" in text and "`dev` 0 of 3 done" in text
    assert "browse-edgar" in text  # the fallback index link
    labels = [str(b.label) for b in at.button]
    assert [lbl for lbl in labels if lbl[0].isdigit()] == [
        f"{k} · {name}" for k, (name, _) in enumerate(DEFAULT_OPTION_SET.options, start=1)
    ]
    for _, description in DEFAULT_OPTION_SET.options:
        assert description in text
    for sentinel in RULE_SENTINELS:
        assert sentinel not in text
    assert "rule_" not in text and "probability" not in text.lower()
    assert "seed_case" not in text and "in_random_draw" not in text


def test_a_planted_inference_file_is_never_opened(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    """Gold refuses on the file's presence; nothing on the page opens it."""
    inferences = datafiles.data_dir(gold_session.settings) / "inferences"
    planted = inferences / "7.jsonl"
    opened: list[str] = []
    real_open = Path.open
    real_read = Path.read_text

    def spy_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if inferences in self.parents:
            opened.append(str(self))
        return real_open(self, *args, **kwargs)

    def spy_read(self: Path, *args: Any, **kwargs: Any) -> str:
        if inferences in self.parents:
            opened.append(str(self))
        return real_read(self, *args, **kwargs)

    at = _gold_app(monkeypatch, gold_session, stops)
    planted.parent.mkdir(parents=True)
    planted.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(Path, "open", spy_open)
    monkeypatch.setattr(Path, "read_text", spy_read)
    at.run()
    assert at.error
    assert opened == []


def test_an_answer_appends_one_line_and_the_next_case_renders_and_reruns_write_nothing(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    first = _current(at, gold_session)
    _press(at, "opt-1")
    lines = _lines(gold_session.working_path)
    assert len(lines) == 1
    assert lines[0]["listing_end_id"] == first
    assert lines[0]["gold_label"] == DEFAULT_OPTION_SET.options[0][0]
    assert lines[0]["relied_on"] == "notice" and lines[0]["confidence"] == "high"
    assert _current(at, gold_session) != first
    assert "case 2 of 9" in _text(at)
    at.run()
    at.run()
    assert len(_lines(gold_session.working_path)) == 1


def test_a_repeated_callback_writes_once(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    """The callback's own check: a second firing for the same case (a rerun that
    replays it) writes nothing, for a skip too, which the module alone would accept."""
    at = _gold_app(monkeypatch, gold_session, stops)
    lid = _current(at, gold_session)
    page = at.session_state["page"]
    _outside_a_run(monkeypatch)
    review_page.on_gold_skip(page, lid)
    review_page.on_gold_skip(page, lid)
    review_page.on_gold_answer(page, lid, "bankruptcy")
    assert len(_lines(gold_session.working_path)) == 1


def _outside_a_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Call a callback directly, outside a script run: `st.session_state` becomes a
    plain mapping, so the widget values fall back to the page's defaults."""
    monkeypatch.setattr(review_page.st, "session_state", {})


def test_skip_moves_the_case_to_the_end(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    skipped = _current(at, gold_session)
    _press(at, "skip")
    (line,) = _lines(gold_session.working_path)
    assert (line["listing_end_id"], line["skipped"]) == (skipped, True)
    seen = []
    for _ in range(N - 1):
        seen.append(_current(at, gold_session))
        _press(at, "opt-2")
    assert skipped not in seen
    assert _current(at, gold_session) == skipped


def test_back_is_an_undo_line_then_the_corrected_answer_wins(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    first = _current(at, gold_session)
    assert not any(b.key == "back" for b in at.button)  # nothing to go back to yet
    _press(at, "opt-1")
    assert any(str(b.label) == "b · back" for b in at.button)
    _press(at, "back")
    lines = _lines(gold_session.working_path)
    assert lines[-1]["listing_end_id"] == first and lines[-1]["undo"] is True
    assert _current(at, gold_session) == first
    _press(at, "opt-5")
    final = _lines(gold_session.working_path)[-1]
    assert (final["listing_end_id"], final["gold_label"]) == (first, "bankruptcy")
    assert len(_lines(gold_session.working_path)) == 3


def test_outside_needs_the_paste_and_shows_the_text_states_row(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    lid = _current(at, gold_session)
    assert not any(str(r.label) == "What the shown text does state" for r in at.radio)
    at.radio(key=f"relied-{lid}").set_value("outside").run()
    states = at.radio(key=f"states-{lid}")
    assert states.value == "unresolved"
    assert list(states.options) == [name for name, _ in DEFAULT_OPTION_SET.options]
    _press(at, "opt-1")
    assert _lines(gold_session.working_path) == []
    assert any("nothing was saved" in str(e.value) for e in at.error)  # the page's check
    assert _current(at, gold_session) == lid
    at.text_input(key=f"passage-{lid}").input("0001234567-19-000001")
    at.radio(key=f"states-{lid}").set_value("exchange_transfer")
    at.toggle(key=f"low-{lid}").set_value(True)
    _press(at, "opt-1")
    (line,) = _lines(gold_session.working_path)
    assert line["relied_on"] == "outside"
    assert line["passage_ref"] == "0001234567-19-000001"
    assert line["text_states"] == "exchange_transfer"
    assert line["confidence"] == "low"


def test_a_restart_resumes_at_the_first_unanswered_case(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    for _ in range(2):
        _press(at, "opt-1")
    expected = gold_session.cases[gold.next_case(gold_session) or 0].listing_end_id
    fresh = _gold_app(monkeypatch, gold_session, stops)
    assert _current(fresh, gold_session) == expected
    assert "case 3 of 9" in _text(fresh)


def test_budget_note_after_the_dev_cases(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    assert not at.info
    for _ in range(N_DEV):
        _press(at, "opt-1")
    assert any("2.0 minutes a case" in str(i.value) for i in at.info)
    assert f"`dev` {N_DEV} of {N_DEV} done" in _text(at)


def test_the_last_answer_shows_done_and_stops_the_server_without_a_store_write(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    for _ in range(N):
        _press(at, "opt-1")
    assert any(review_page.DONE_MESSAGE in str(s.value) for s in at.success)
    assert stops
    assert not gold_session.lock_path.exists()  # the CLI locks (#1330), not the page
    assert not at.button
    count = len(_lines(gold_session.working_path))
    at.run()
    assert len(_lines(gold_session.working_path)) == count


def test_a_locked_session_only_displays_and_a_rerun_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    for _ in range(N - 1):
        _press(at, "opt-1")
    lid = _current(at, gold_session)
    gold_session.lock_path.write_text(
        json.dumps({"dataset_id": 9, "sha256": "f" * 64, "scorable_pilot": 6}), encoding="utf-8"
    )
    page = at.session_state["page"]
    before = len(_lines(gold_session.working_path))
    _outside_a_run(monkeypatch)
    review_page.on_gold_answer(page, lid, "bankruptcy")  # a replayed callback
    monkeypatch.undo()
    _gold_app(monkeypatch, gold_session, stops)
    at.run()
    assert len(_lines(gold_session.working_path)) == before
    assert not at.button
    assert "f" * 64 in _text(at) and "6" in _text(at)


def test_the_keyboard_component_is_present(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    html = [e for e in at.get("iframe") if "addEventListener" in str(getattr(e, "proto", e))]
    assert html, "no keyboard listener on the page"
    assert '"s": "s \\u00b7"' in review_page.keyboard_html(review_page.gold_key_table(()))


def test_a_skipped_case_is_timed_from_its_return(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(review_page.time, "monotonic", lambda: clock[0])
    at = _gold_app(monkeypatch, gold_session, stops)
    skipped = _current(at, gold_session)
    clock[0] += 10
    _press(at, "skip")
    for _ in range(N - 1):
        clock[0] += 100
        _press(at, "opt-1")
    assert _current(at, gold_session) == skipped
    clock[0] += 30
    _press(at, "opt-1")
    final = _lines(gold_session.working_path)[-1]
    assert final["listing_end_id"] == skipped
    assert (final["seconds_spent"], final["idle"]) == (30.0, False)
    assert [line["seconds_spent"] for line in _lines(gold_session.working_path)[1:-1]] == [
        100.0
    ] * (N - 1)


def test_an_inference_file_planted_mid_session_stops_the_next_press(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    """Callbacks run before the script body: the press itself must refuse."""
    at = _gold_app(monkeypatch, gold_session, stops)
    planted = datafiles.data_dir(gold_session.settings) / "inferences" / "1.jsonl"
    planted.parent.mkdir(parents=True)
    planted.write_text("{}\n", encoding="utf-8")
    _press(at, "opt-1")
    assert _lines(gold_session.working_path) == []
    assert any("model output" in str(e.value) for e in at.error)


def test_filer_text_is_never_rendered_as_markdown(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    at = _gold_app(monkeypatch, gold_session, stops)
    markdown = "\n".join(str(m.value) for m in at.markdown)
    assert "Issuer " not in markdown and "Completion." not in markdown
    assert any(str(t.value).startswith("Issuer: Issuer ") for t in at.text)


def test_back_restarts_the_clock_of_both_cases(
    monkeypatch: pytest.MonkeyPatch, gold_session: gold.GoldSession, stops: list[int]
) -> None:
    clock = [1000.0]
    monkeypatch.setattr(review_page.time, "monotonic", lambda: clock[0])
    at = _gold_app(monkeypatch, gold_session, stops)
    clock[0] += 20
    _press(at, "opt-1")  # case 1 answered; case 2 drawn at 1020
    clock[0] += 50
    _press(at, "back")  # case 1 reopened at 1070
    clock[0] += 60
    _press(at, "opt-2")  # case 1 re-answered: 60 s
    clock[0] += 30
    _press(at, "opt-3")  # case 2: 30 s since it was drawn again, not 140 s
    answers = [line for line in _lines(gold_session.working_path) if line.get("gold_label")]
    assert [line["seconds_spent"] for line in answers] == [20.0, 60.0, 30.0]


def test_a_malformed_finish_marker_is_refused_not_a_traceback(
    gsettings: Settings, monkeypatch: pytest.MonkeyPatch, stops: list[int]
) -> None:
    path = datafiles.review_path(gsettings, 5)
    path.parent.mkdir(parents=True)
    path.with_name("5.finished.json").write_text("{not json", encoding="utf-8")
    at = _app(monkeypatch, gsettings, path, stops, ("--code-version", "abc"))
    assert any("finish marker" in str(e.value) for e in at.error)


# --- review mode ------------------------------------------------------------------


@pytest.fixture
def world(tmp_path: Path, settings: Settings) -> Iterator[World]:
    labeling = settings.research.labeling.model_copy(update={"requests_per_second": 1000.0})
    research_config = settings.research.model_copy(
        update={
            "data_dir": str(tmp_path / "research"),
            "spend_ceiling_usd_month": 40.0,
            "spend_ceiling_usd_total": 40.0,
            "labeling": labeling,
        }
    )
    built = World(tmp_path, settings.model_copy(update={"research": research_config}))
    yield built
    built.conn.close()


def _review_app(
    monkeypatch: pytest.MonkeyPatch, world: World, run_id: int, stops: list[int]
) -> AppTest:
    monkeypatch.setattr(
        review_page, "open_read_only", lambda _s: contextlib.nullcontext(world.conn)
    )
    return _app(
        monkeypatch,
        world.settings,
        datafiles.review_path(world.settings, run_id),
        stops,
        ("--code-version", "abc123"),
    )


def _session(world: World, run_id: int) -> review.ReviewSession:
    return review.build_review_session(
        run_id,
        settings=world.settings,
        connect=lambda: contextlib.nullcontext(world.conn),
        code_version="x",
    )


def _decide(at: AppTest, session: review.ReviewSession, decision: str = "a") -> AppTest:
    lid = session.items[review.next_item(session) or 0].listing_end_id
    at.radio(key=f"preset-{lid}").set_value("filing states it")
    return _press(at, f"decide-{decision}")


def test_review_mode_shows_a_and_b_as_class_descriptions_and_nothing_that_attributes(
    monkeypatch: pytest.MonkeyPatch, world: World, stops: list[int]
) -> None:
    run_id = _batch(world)
    session = _session(world, run_id)
    at = _review_app(monkeypatch, world, run_id, stops)
    view = review.item_view(session, 0)
    text = _text(at)
    assert f"**a:** {view.a}" in text and f"**b:** {view.b}" in text
    known = {*review.CLASS_DESCRIPTIONS.values(), review.UNRESOLVED_PHRASE}
    known |= set(review.RULE_PHRASES.values())
    for answer in (view.a, view.b):
        assert answer in known or answer.startswith("one of: ")
    for hidden in ("disagreement", "agreement_sample", "stratum", "probability"):
        assert hidden not in text.lower()
    for name, _ in DEFAULT_OPTION_SET.options:
        if name != "unresolved":
            assert name not in text
    assert [str(b.label) for b in at.button if b.key and b.key.startswith("decide-")] == [
        "a · answer a",
        "b · answer b",
        "w · both wrong",
        "u · unresolved",
    ]


def test_review_decision_needs_a_reason_and_appends_one_record(
    monkeypatch: pytest.MonkeyPatch, world: World, stops: list[int]
) -> None:
    run_id = _batch(world)
    session = _session(world, run_id)
    at = _review_app(monkeypatch, world, run_id, stops)
    _press(at, "decide-a")
    assert _lines(session.review_path) == []
    assert any("Pick a reason" in str(e.value) for e in at.error)  # the page's check
    first = session.items[0].listing_end_id
    at.text_input(key=f"reason-{first}").input("8-K item 2.01 says so")
    _decide(at, session, "b")
    (line,) = _lines(session.review_path)
    assert line["listing_end_id"] == first and line["decision"] == "b"
    assert line["reason"] == "filing states it: 8-K item 2.01 says so"
    at.run()
    assert len(_lines(session.review_path)) == 1
    assert f"item 2 of {len(session.items)}" in _text(at)


def test_review_back_is_a_button_with_no_key(
    monkeypatch: pytest.MonkeyPatch, world: World, stops: list[int]
) -> None:
    run_id = _batch(world)
    session = _session(world, run_id)
    at = _review_app(monkeypatch, world, run_id, stops)
    _decide(at, session)
    back = at.button(key="back")
    assert str(back.label) == "back"  # no key prefix: `b` is answer b
    _press(at, "back")
    lines = _lines(session.review_path)
    assert lines[-1]["undo"] is True
    assert review.next_item(session) == 0


def test_review_last_decision_shows_done_stops_and_never_finishes(
    monkeypatch: pytest.MonkeyPatch, world: World, stops: list[int]
) -> None:
    run_id = _batch(world)
    session = _session(world, run_id)
    at = _review_app(monkeypatch, world, run_id, stops)
    for _ in session.items:
        _decide(at, session, "a")
    assert any(review_page.DONE_MESSAGE in str(s.value) for s in at.success)
    assert stops
    assert not session.finished_path.exists() and not session.finishing_path.exists()
    count = len(_lines(session.review_path))
    at.run()
    assert len(_lines(session.review_path)) == count


def test_review_mode_needs_the_code_version(
    monkeypatch: pytest.MonkeyPatch, world: World, stops: list[int]
) -> None:
    run_id = _batch(world)
    at = _app(monkeypatch, world.settings, datafiles.review_path(world.settings, run_id), stops)
    assert any("--code-version" in str(e.value) for e in at.error)
    assert not at.button


def test_review_record_carries_the_given_code_version(
    monkeypatch: pytest.MonkeyPatch, world: World, stops: list[int]
) -> None:
    run_id = _batch(world)
    session = _session(world, run_id)
    at = _review_app(monkeypatch, world, run_id, stops)
    _decide(at, session)
    assert _lines(session.review_path)[0]["code_version"] == "abc123"


def test_a_finished_review_only_displays(
    monkeypatch: pytest.MonkeyPatch, world: World, stops: list[int]
) -> None:
    run_id = _batch(world)
    session = _session(world, run_id)
    session.finished_path.parent.mkdir(parents=True, exist_ok=True)
    session.finished_path.write_text(
        json.dumps({"sha256": "e" * 64, "metrics": {"n_reviewed": 8}}), encoding="utf-8"
    )
    monkeypatch.setattr(
        review_page.review,
        "build_review_session",
        lambda *a, **k: pytest.fail("a finished run is not rebuilt"),
    )
    at = _review_app(monkeypatch, world, run_id, stops)
    assert not at.button and not at.error
    assert "e" * 64 in _text(at) and "Items reviewed: 8" in _text(at)
