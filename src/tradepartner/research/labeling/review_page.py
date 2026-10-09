"""The review page: the gold labelling and the disagreement review, one case per screen.

Research-labeling spec C10 (docs/specs/research-labeling.md) as the 2026-10-07
amendment (#1121) edits it; plan task T123c. A Streamlit script that `tradepartner
research gold` and `tradepartner research review --run <id>` (T124) start with
`streamlit run <this file> --server.address localhost --browser.gatherUsageStats false
-- --session <path>`. It is not a dashboard page: it lives inside the boundary and
takes from it only `gold` (T131) and `review` (T123b), drawing what they hand it and
writing only through them.

**The arguments.** `--session` names the gold session's `session.json`
(`gold.session_path`), which selects gold mode, or a run's review file
(`reviews/departure-reason/<run_id>.jsonl`), which selects review mode; anything else
is refused. Review mode also needs `--code-version`, written on every review record:
the CLI reads it, since this package may not run `git` (ADR 0013 point 3).

**Refusals before anything renders.** The running `server.address` must be
`localhost` or `127.0.0.1` and `browser.gatherUsageStats` false (ADR 0011 point 3, the
check `dashboard.app.render_app` makes); otherwise the page says what to fix and draws
nothing else. In gold mode `gold.refuse_if_inference_files` (its recursive walk under
`inferences/`) runs on every script run, so a file planted after the session was
built stops the page; no inference file is ever opened here.

**Writes.** Every write (an answer, a skip, a back) is an `on_click` callback that
first checks it has not already run: the case or item it was drawn for is still the
current one (`gold.next_case` / `review.next_item`), and the session is not locked or
finished. A rerun therefore writes nothing, and the modules refuse a second answer on
their own (#1029 F5). The page writes only the session's own files (the gold working
file, the review file) and **never the store** (owner decision on #1330, option B:
`tradepartner.research` imports no `store.db.open_for_write`, spec req 13 (iii)). When
the last case is answered it says "done, you may close this tab" and stops the
server; the launching CLI (T124) then runs `gold.lock_gold` or `review.finish` and
prints the count and the hash. A complete session that is not locked yet shows the
same message and stops the server again; a locked or finished session only displays.

**Keys.** A small listener (`st.iframe` on a constant HTML string, the successor
Streamlit 1.64 names for the deprecated `st.components.v1.html`) clicks the button whose label
starts with the pressed key: gold mode `1` to `9` (the options), `s` (skip) and `b`
(back); review mode `a`, `b`, `w`, `u` (the four decisions; `b` is answer `b`, and back
is a button with no key; #1029 F3, #1121). The buttons work without it.

**The store.** Review mode rebuilds the session through `review.build_review_session`,
whose `attach_run` reads the registry: on a short-lived `store.db.open_read_only`
connection (an allowlisted reader). Gold mode opens no store connection.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal

import streamlit as st

from tradepartner.config import Settings, get_settings
from tradepartner.research.labeling import gold, review
from tradepartner.store.db import StoreLockedError, open_read_only
from tradepartner.store.research import ResearchError

Mode = Literal["gold", "review"]

#: ADR 0011 point 3: the only addresses the page accepts for `server.address`.
ALLOWED_SERVER_ADDRESSES: Final = frozenset({"localhost", "127.0.0.1"})
#: C10: the owner-time budget the `dev` cases' mean is compared with (owner decision 3).
BUDGET_MINUTES_PER_CASE: Final = 2.0
#: Gold mode's keys: `1` to `9` the options in order, `s` skip, `b` back.
SKIP_KEY: Final = "s"
BACK_KEY: Final = "b"
#: Review mode's keys: the four decisions. Back has no key (`b` is answer `b`).
REVIEW_KEYS: Final[Mapping[str, str]] = {
    "a": "a",
    "b": "b",
    "w": "both_wrong",
    "u": "unresolved",
}
REVIEW_LABELS: Final[Mapping[str, str]] = {
    "a": "answer a",
    "b": "answer b",
    "both_wrong": "both wrong",
    "unresolved": "unresolved",
}
OUTSIDE: Final = "outside"
DONE_MESSAGE: Final = "done, you may close this tab"
#: Seconds between the done message and the server stop, so the message reaches the tab.
STOP_DELAY_SECONDS: Final = 2.0


class PageRefused(ValueError):
    """The page cannot open this session (the message says what to fix)."""


# --- pure parts -------------------------------------------------------------------


def server_options_ok(address: object, gather_usage_stats: object) -> tuple[bool, str]:
    """Whether the running Streamlit options satisfy ADR 0011 point 3 and, if not, a
    message naming what to fix (the check `dashboard.app.render_app` makes)."""
    if address in ALLOWED_SERVER_ADDRESSES and gather_usage_stats is False:
        return True, ""
    return False, (
        f"Refusing to render: server.address is {address!r} and "
        f"browser.gatherUsageStats is {gather_usage_stats!r}. ADR 0011 requires "
        f"server.address to be one of {sorted(ALLOWED_SERVER_ADDRESSES)} and "
        "browser.gatherUsageStats to be false, so this page, which shows filings and "
        "writes labels, is never reachable from another machine. Start it through "
        "`tradepartner research gold` or `tradepartner research review` (or fix "
        "`.streamlit/config.toml`, STREAMLIT_SERVER_ADDRESS or --server.address) and reload."
    )


@dataclass(frozen=True)
class Arguments:
    """What the CLI passes after `--`: the session, and in review mode the code
    version written on every review record (the CLI reads it; this package may not
    run `git`, ADR 0013 point 3)."""

    session: Path
    code_version: str | None


def page_arguments(argv: Sequence[str]) -> Arguments:
    """`--session <path>` (and `--code-version <v>`) given after `--` on the
    `streamlit run` line."""
    parser = argparse.ArgumentParser(prog="review_page", add_help=False)
    parser.add_argument("--session", required=False)
    parser.add_argument("--code-version", required=False)
    known, _ = parser.parse_known_args(list(argv))
    if not known.session:
        raise PageRefused("no --session <path> was given; start the page through the CLI")
    return Arguments(Path(known.session), known.code_version or None)


def mode_of(settings: Settings, path: Path) -> tuple[Mode, int | None]:
    """Gold mode for the gold session's `session.json`, review mode (with the run id)
    for a review file `<run_id>.jsonl` under the research store; else refused."""
    if path.resolve() == gold.session_path(settings).resolve():
        return "gold", None
    if path.suffix == ".jsonl" and path.stem.isdigit() and int(path.stem) > 0:
        return "review", int(path.stem)
    raise PageRefused(
        f"--session {path} is neither the gold session ({gold.session_path(settings)}) "
        "nor a review file `<run_id>.jsonl`"
    )


def gold_key_table(options: Sequence[tuple[str, str]]) -> dict[str, str]:
    """Gold mode's key -> button label prefix: `1` to `9`, `s` and `b`."""
    table = {str(i): f"{i} ·" for i in range(1, len(options) + 1)}
    table[SKIP_KEY] = f"{SKIP_KEY} ·"
    table[BACK_KEY] = f"{BACK_KEY} ·"
    return table


def review_key_table() -> dict[str, str]:
    """Review mode's key -> button label prefix: `a`, `b`, `w`, `u` only."""
    return {key: f"{key} ·" for key in REVIEW_KEYS}


def keyboard_html(table: Mapping[str, str]) -> str:
    """The listener: a key press clicks the parent page's button whose label starts
    with the mapped prefix; typing in a text field is left alone."""
    return f"""<script>
const table = {json.dumps(dict(table))};
const doc = window.parent.document;
if (doc.__reviewKeys) {{ doc.removeEventListener("keydown", doc.__reviewKeys); }}
doc.__reviewKeys = function (event) {{
  const target = event.target;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const typing = ["text", "search", "url", "email", "number", "password"];
  if (target && (target.tagName === "TEXTAREA" ||
      (target.tagName === "INPUT" && typing.includes((target.type || "text").toLowerCase())))) {{
    return;
  }}
  const prefix = table[event.key];
  if (!prefix) return;
  for (const button of doc.querySelectorAll("button")) {{
    if (button.innerText.trim().startsWith(prefix)) {{
      event.preventDefault();
      button.click();
      return;
    }}
  }}
}};
doc.addEventListener("keydown", doc.__reviewKeys);
</script>"""


def relied_on_choices(notice: str | None, eightk: gold.EightKView | None) -> list[str]:
    """C10's "relied on" choice: `notice` when the notice is text, each shown 8-K item,
    then `outside`; the first entry is the default (`outside` alone when nothing is
    shown as text, so no label claims a passage the page did not show)."""
    choices = ["notice"] if notice else []
    choices += [f"8-K item {item}" for item, _ in (eightk.items if eightk else ())]
    return [*choices, OUTSIDE]


def _final_lines(lines: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    finals: dict[str, Mapping[str, Any]] = {}
    for line in lines:
        finals[str(line["listing_end_id"])] = line
    return finals


def last_final(lines: Sequence[Mapping[str, Any]]) -> str | None:
    """The case or item that "back" reopens: the most recent line that is still its
    case's final line and is not an undo (an answer, a decision or a skip)."""
    finals = _final_lines(lines)
    for line in reversed(lines):
        lid = str(line["listing_end_id"])
        if finals[lid] is line and not line.get("undo"):
            return lid
    return None


def _is_final(line: Mapping[str, Any]) -> bool:
    """A working line that closes its case: an answer or an `unlabelled` skip."""
    if line.get("undo"):
        return False
    return not line.get("skipped") or bool(line.get("unlabelled"))


def answered_seconds(lines: Sequence[Mapping[str, Any]]) -> list[float]:
    """`seconds_spent` of every case whose final line is an answer, idle ones left out
    (C10: a walk-away does not skew the mean)."""
    return [
        float(line["seconds_spent"])
        for line in _final_lines(lines).values()
        if line.get("gold_label") and not line.get("idle")
    ]


def progress_line(position: int, total: int, dev_done: int, n_dev: int, mean: float | None) -> str:
    """C10's progress line: "case 37 of 150, `dev` 30 done, mean 1.6 min"."""
    shown = "n/a" if mean is None else f"{mean / 60:.1f} min"
    return f"case {position} of {total}, `dev` {dev_done} of {n_dev} done, mean {shown}"


def budget_note(mean: float | None) -> str:
    """The one-line note after the `dev` cases: the mean against 2.0 minutes a case."""
    if mean is None:
        return (
            "The `dev` cases are done, with no timed answer to compare with the "
            f"{BUDGET_MINUTES_PER_CASE:.1f} minutes a case budget."
        )
    minutes = mean / 60
    side = "within" if minutes <= BUDGET_MINUTES_PER_CASE else "over"
    return (
        f"The `dev` cases are done: mean {minutes:.1f} min a case, {side} the "
        f"{BUDGET_MINUTES_PER_CASE:.1f} minutes a case budget. Carrying on is the default; "
        "a stop here leaves the session open and unlocked."
    )


def reason_text(preset: str | None, text: str | None) -> str:
    """The review reason: a preset, the typed text, or both; empty when neither."""
    typed = (text or "").strip()
    if preset and typed:
        return f"{preset}: {typed}"
    return preset or typed


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


# --- the server --------------------------------------------------------------------


def _stop_server() -> None:
    """Stop the Streamlit server after the done message has gone out (SIGTERM is
    Streamlit's own graceful shutdown)."""
    timer = threading.Timer(STOP_DELAY_SECONDS, os.kill, args=(os.getpid(), signal.SIGTERM))
    timer.daemon = True
    timer.start()


# --- page state -------------------------------------------------------------------


@dataclass(frozen=True)
class Page:
    """What one script run works on."""

    mode: Mode
    settings: Settings
    gold: gold.GoldSession | None = None
    review: review.ReviewSession | None = None


def _flash(kind: Literal["error", "success", "info"], text: str) -> None:
    st.session_state["flash"] = (kind, text)


def _show_flash() -> None:
    flash = st.session_state.pop("flash", None)
    if flash:
        kind, text = flash
        show = {"error": st.error, "success": st.success, "info": st.info}[kind]
        show(text)


def _shown_at(lid: str) -> float:
    """When the case was first drawn in this tab (the render half of `seconds_spent`)."""
    shown: dict[str, float] = st.session_state.setdefault("shown_at", {})
    return shown.setdefault(lid, time.monotonic())


def _elapsed(lid: str) -> float:
    """Seconds since the case was drawn; the clock restarts the next time it is drawn
    (a skipped case timed again from its return, not from its first showing)."""
    shown: dict[str, float] = st.session_state.setdefault("shown_at", {})
    start = shown.get(lid)
    return 0.0 if start is None else max(time.monotonic() - start, 0.0)


def _restart_clock(lid: str) -> None:
    """After a saved answer or skip: the case's clock starts again when next drawn."""
    st.session_state.setdefault("shown_at", {}).pop(lid, None)


# --- gold mode: callbacks ---------------------------------------------------------


def _gold_current(session: gold.GoldSession, lid: str) -> bool:
    """The callback's check: unlocked, no inference file (callbacks run before the
    script body's refusal), and `lid` is still the case on screen."""
    if session.lock_path.exists():
        return False
    try:
        gold.refuse_if_inference_files(session.settings)
    except gold.InferenceFilesPresent as exc:
        _flash("error", str(exc))
        return False
    i = gold.next_case(session)
    return i is not None and session.cases[i].listing_end_id == lid


def on_gold_answer(page: Page, lid: str, label: str) -> None:
    """An option button: one `record_label` line."""
    session = page.gold
    assert session is not None
    if not _gold_current(session, lid):
        return
    relied_on = str(st.session_state.get(f"relied-{lid}") or "notice")
    passage = str(st.session_state.get(f"passage-{lid}") or "").strip()
    if relied_on == OUTSIDE and not passage:
        _flash("error", "Paste the accession or URL of the filing you used; nothing was saved.")
        return
    text_states = st.session_state.get(f"states-{lid}") if relied_on == OUTSIDE else None
    try:
        gold.record_label(
            session,
            lid,
            label=label,
            relied_on=relied_on,
            seconds_spent=_elapsed(lid),
            passage_ref=passage if relied_on == OUTSIDE else None,
            text_states=text_states,
            confidence="low" if st.session_state.get(f"low-{lid}") else "high",
        )
    except gold.GoldRefused as exc:
        _flash("error", str(exc))
        return
    _restart_clock(lid)


def on_gold_skip(page: Page, lid: str) -> None:
    """Skip: the case goes to the end of the queue (`unlabelled` the second time)."""
    session = page.gold
    assert session is not None
    if not _gold_current(session, lid):
        return
    try:
        gold.record_skip(session, lid, seconds_spent=_elapsed(lid))
    except gold.GoldRefused as exc:
        _flash("error", str(exc))
        return
    _restart_clock(lid)


def on_gold_back(page: Page, target: str) -> None:
    """Back: `record_undo` on the last final case, which is then shown again."""
    session = page.gold
    assert session is not None
    if session.lock_path.exists() or last_final(_read_jsonl(session.working_path)) != target:
        return
    try:
        gold.refuse_if_inference_files(session.settings)
        gold.record_undo(session, target)
    except gold.GoldRefused as exc:
        _flash("error", str(exc))
        return
    # Both the reopened case and the one that was on screen start a fresh clock.
    st.session_state["shown_at"] = {}


# --- review mode: callbacks -------------------------------------------------------


def _review_closed(session: review.ReviewSession) -> bool:
    return session.finished_path.exists() or session.finishing_path.exists()


def _review_current(session: review.ReviewSession, lid: str) -> bool:
    if _review_closed(session):
        return False
    i = review.next_item(session)
    return i is not None and session.items[i].listing_end_id == lid


def on_review_decision(page: Page, lid: str, decision: str) -> None:
    """A decision button: one `record_decision` record."""
    session = page.review
    assert session is not None
    if not _review_current(session, lid):
        return
    reason = reason_text(
        st.session_state.get(f"preset-{lid}"), st.session_state.get(f"reason-{lid}")
    )
    if not reason:
        _flash("error", "Pick a reason (or type one) before deciding; nothing was saved.")
        return
    relied_on = str(st.session_state.get(f"relied-{lid}") or "notice")
    passage = str(st.session_state.get(f"passage-{lid}") or "").strip()
    if relied_on == OUTSIDE and not passage:
        _flash("error", "Paste the accession or URL of the filing you used; nothing was saved.")
        return
    try:
        review.record_decision(
            session,
            lid,
            decision=decision,
            reason=reason,
            relied_on=relied_on,
            passage_ref=passage if relied_on == OUTSIDE else None,
        )
    except review.ReviewRefused as exc:
        _flash("error", str(exc))
        return


def on_review_back(page: Page, target: str) -> None:
    """Back (no key): `record_undo` on the last decided item."""
    session = page.review
    assert session is not None
    if _review_closed(session) or last_final(_read_jsonl(session.review_path)) != target:
        return
    try:
        review.record_undo(session, target)
    except review.ReviewRefused as exc:
        _flash("error", str(exc))


# --- drawing ----------------------------------------------------------------------


def _form25(view: gold.CaseView | review.ItemView, provision: str | None) -> None:
    fields = [
        ("Issuer", view.issuer),
        ("Exchange", view.exchange),
        ("Class", view.class_title),
        ("Form 25 filed", view.filed_on),
        ("Effective", view.effective_on),
    ]
    if provision is not None:
        fields.append(("Rule provision", provision))
    # Filer text goes through `st.text`, never markdown (no link or image renders).
    st.text("\n".join(f"{name}: {value or 'n/a'}" for name, value in fields))


def _passages(view: gold.CaseView | review.ItemView) -> None:
    st.subheader("Notice")
    if view.notice:
        st.text(view.notice)
    else:
        st.caption("The notice is not text.")
    if view.eightk:
        st.subheader("8-K")
        st.text(f"{view.eightk.form} {view.eightk.accession} ({view.eightk.filed_on})")
        for item, text in view.eightk.items:
            st.text(f"Item {item}\n{text}")
        if view.eightk.body_head:
            st.text(view.eightk.body_head)
    st.markdown(f"[The issuer's EDGAR filing index]({view.index_url})")


def _relied_on(lid: str, view: gold.CaseView | review.ItemView, options: Sequence[str]) -> str:
    choices = relied_on_choices(view.notice, view.eightk)
    relied = st.radio("Relied on", choices, key=f"relied-{lid}", horizontal=True)
    if relied == OUTSIDE:
        st.text_input("Accession or URL of the filing you used", key=f"passage-{lid}")
        if options:
            names = list(options)
            st.radio(
                "What the shown text does state",
                names,
                index=names.index("unresolved"),
                key=f"states-{lid}",
                horizontal=True,
            )
    return str(relied)


def _closed(count_label: str, count: object, sha: str) -> None:
    """A locked or finished session: display only."""
    st.success(f"This session is closed. {count_label}: {count}. Hash: `{sha}`.")


def _complete(note: str) -> None:
    """Every case is final: the done message, then the server stops so the CLI can
    lock or finish (owner decision on #1330)."""
    st.success(DONE_MESSAGE)
    st.info(note)
    _stop_server()


def draw_gold(page: Page) -> None:
    """Gold mode: one case per screen, or the locked session's summary."""
    session = page.gold
    assert session is not None
    if session.lock_path.exists():
        done = json.loads(session.lock_path.read_text(encoding="utf-8"))
        _closed("Scorable `pilot` cases", done["scorable_pilot"], done["sha256"])
        return
    i = gold.next_case(session)
    if i is None:
        _complete(
            "Every case is answered. The CLI locks the session and prints the scorable "
            "`pilot` count and the export's hash once this page stops."
        )
        return
    lines = _read_jsonl(session.working_path)
    finals = _final_lines(lines)
    seconds = answered_seconds(lines)
    mean = sum(seconds) / len(seconds) if seconds else None
    dev = [c.listing_end_id for c in session.cases if c.split == "dev"]
    final_ids = {lid for lid, line in finals.items() if _is_final(line)}
    dev_done = len(final_ids.intersection(dev))
    done = len(final_ids)
    view = gold.case_view(session, i)
    lid = view.listing_end_id
    _shown_at(lid)
    st.caption(progress_line(done + 1, view.total, dev_done, len(dev), mean))
    if dev and dev_done == len(dev):
        st.info(budget_note(mean))
    _form25(view, view.provision)
    _passages(view)
    names = [name for name, _ in view.options]
    _relied_on(lid, view, names)
    st.toggle("Low confidence", key=f"low-{lid}")
    for k, (name, description) in enumerate(view.options, start=1):
        st.button(f"{k} · {name}", key=f"opt-{k}", on_click=on_gold_answer, args=(page, lid, name))
        st.caption(description)
    st.button(f"{SKIP_KEY} · skip", key="skip", on_click=on_gold_skip, args=(page, lid))
    target = last_final(lines)
    if target is not None:
        st.button(f"{BACK_KEY} · back", key="back", on_click=on_gold_back, args=(page, target))
    st.iframe(keyboard_html(gold_key_table(view.options)), height=1)


def draw_review(page: Page) -> None:
    """Review mode: one item per screen, or the finished session's summary."""
    session = page.review
    assert session is not None
    if session.finished_path.exists():
        done = json.loads(session.finished_path.read_text(encoding="utf-8"))
        _closed("Items reviewed", done["metrics"]["n_reviewed"], done["sha256"])
        return
    i = review.next_item(session)
    if i is None:
        _complete(
            "Every item is decided. The CLI finishes the review and prints the count "
            "and the review file's hash once this page stops."
        )
        return
    view = review.item_view(session, i)
    lid = view.listing_end_id
    lines = _read_jsonl(session.review_path)
    st.caption(f"item {i + 1} of {view.total}")
    _form25(view, None)
    _passages(view)
    st.markdown(f"**a:** {view.a}  \n**b:** {view.b}")
    st.radio(
        "Reason", list(review.REASON_PRESETS), index=None, key=f"preset-{lid}", horizontal=True
    )
    st.text_input("Reason (optional text)", key=f"reason-{lid}")
    _relied_on(lid, view, ())
    for key, decision in REVIEW_KEYS.items():
        st.button(
            f"{key} · {REVIEW_LABELS[decision]}",
            key=f"decide-{decision}",
            on_click=on_review_decision,
            args=(page, lid, decision),
        )
    target = last_final(lines)
    if target is not None:
        st.button("back", key="back", on_click=on_review_back, args=(page, target))
    st.iframe(keyboard_html(review_key_table()), height=1)


# --- the script -------------------------------------------------------------------


def _show_finished(path: Path, run_id: int | None) -> bool:
    """A finished review only displays: its run is closed, so the session cannot be
    rebuilt (`attach_run` refuses a closed run); `finish`'s marker beside the review
    file holds what to show."""
    marker = path.with_name(f"{run_id}.finished.json")
    if not marker.is_file():
        return False
    try:
        done = json.loads(marker.read_text(encoding="utf-8"))
        count, sha = done["metrics"]["n_reviewed"], done["sha256"]
    except (ValueError, KeyError, TypeError) as exc:
        raise PageRefused(f"{marker} is not a readable finish marker ({exc!r})") from exc
    _closed("Items reviewed", count, sha)
    return True


def _open(
    settings: Settings, path: Path, mode: Mode, run_id: int | None, code_version: str | None
) -> Page:
    cached: Page | None = st.session_state.get("page")
    if cached is not None and cached.settings == settings:
        return cached
    if mode == "gold":
        session = gold.open_gold_session(path, gold.GoldFlags(), settings=settings)
        page = Page("gold", settings, gold=session)
    else:
        assert run_id is not None
        if not code_version:
            raise PageRefused("review mode needs --code-version; start it through the CLI")
        built = review.build_review_session(
            run_id,
            settings=settings,
            connect=lambda: open_read_only(settings),
            code_version=code_version,
        )
        if built.review_path.resolve() != path.resolve():
            raise PageRefused(f"--session {path} is not run {run_id}'s review file")
        page = Page("review", settings, review=built)
    st.session_state["page"] = page
    return page


def main(argv: Sequence[str] | None = None) -> None:
    """One script run: the refusals, then the current case (module docstring)."""
    import sys

    st.set_page_config(page_title="TradePartner review", layout="centered")
    ok, message = server_options_ok(
        st.get_option("server.address"), st.get_option("browser.gatherUsageStats")
    )
    if not ok:
        st.error(message)
        return
    settings = get_settings()
    try:
        args = page_arguments(sys.argv[1:] if argv is None else argv)
        path = args.session
        mode, run_id = mode_of(settings, path)
        if mode == "gold":
            gold.refuse_if_inference_files(settings)  # every start, every rerun
        elif _show_finished(path, run_id):
            return
        page = _open(settings, path, mode, run_id, args.code_version)
    except StoreLockedError as exc:
        st.error(f"store busy: {exc}. Reload this page in a moment.")
        return
    except (PageRefused, gold.GoldRefused, review.ReviewRefused, ResearchError) as exc:
        st.error(f"Refusing to open the session: {exc}")
        return
    st.title("Gold labelling" if mode == "gold" else "Review")
    _show_flash()
    if page.mode == "gold":
        draw_gold(page)
    else:
        draw_review(page)


if __name__ == "__main__":
    main()
