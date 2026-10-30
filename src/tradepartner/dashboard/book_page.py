"""The Book page (T276, #2170): one book's header, chart, Today and holdings.

It draws `book_view.build(conn, settings, book, now)` (T279) and `home_view
.strategy_sentence` (T273) and computes nothing: every number, sentence and
series comes from those two. It reads through the shell's one read-only
connection, opens no other and writes nothing; `app.py` routes it unchanged.

**Which book.** A `book` query parameter (the Home card's link, `?book=<token>`)
wins when the store has that book and the link is new this session; otherwise the
picker's choice stands; with neither, the one open book, else the paper default.
The picker appears only when the store holds more than one book.

**Plain text.** The rendered text carries no window or trial id, no raw status
word and no UTC time: the status is a pill (Trading, Halted, Closed), times are
the view model's Eastern sentences, and dates are weekday and day.

**The safety and backtest cards** (T277, #2270) draw `book_view.build`'s safety
and backtest parts: an `ok` match is described as within the reconciliation's
tolerance, otherwise the view's words say what differs; the kill switch line; the limits
in its plain words; shakedown progress, the latest tracking report and the
desk command to run another (the page never runs one, so a render adds no trial).
Resume is the desk's alone: the card prints its command and offers no button,
and there is no stop-all here.

**"Stop this book now"** is the page's one write, `book_actions.kill` (ADR 0018
section 2): the desk's `paper kill` as a child process. The button is offered
only when `book_actions.store_refusal` finds the page reading the live store;
otherwise one sentence says why and gives the desk command. It opens a dialog
that names the book, says what happens and asks for a typed reason (empty,
blank or over `ui.max_reason_chars` refused before anything runs). Confirm is an
`on_click` callback, so it runs before the next render opens the store and no
read-only connection is open while the child runs. The outcome is the child's
exit code alone, shown with its scrubbed text. A click is never retried, and
the Override page's content guard refuses a second click carrying the reason
of a kill that engaged or is unconfirmed (a stale double-click). Past the wait
the dialog says NOT confirmed; the card's switch line is the next render's read.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from tradepartner.config import Settings
from tradepartner.dashboard import book_actions, theme
from tradepartner.execution import book_holdings, book_stats, book_view, home_view, ops
from tradepartner.execution.marks import UnreadableMarkError
from tradepartner.store.journal import JournalNotInitialised
from tradepartner.store.schema import SchemaVersionError

BOOK_KEY = "book_page_book"
#: The Stop button's key prefix (one per book), the dialog's widgets and its state.
STOP_BUTTON_KEY = "book_page_stop"
STOP_REASON_KEY = "book_page_stop_reason"
STOP_CONFIRM_KEY = "book_page_stop_confirm"
STOP_OPEN_KEY = "book_page_stop_open"
STOP_OUTCOME_KEY = "book_page_stop_outcome"
#: `(book, trimmed reason)` of the last kill that engaged or is unconfirmed.
STOP_LAST_KEY = "book_page_stop_last"
_EASTERN = ZoneInfo("America/New_York")
RANGE_KEY = "book_page_range"
_LINK_SEEN_KEY = "book_page_link_seen"
#: The Trades page's own book picker key; the "see all" link sets it, then opens that page.
TRADES_BOOK_KEY = "trades_book"
TRADES_PAGE = "trades"

_RANGES: dict[str, str] = {"Since start": "since_start", "1M": "1M", "3M": "3M"}
_DEFAULT_RANGE = "Since start"
_PILL: dict[str, tuple[str, theme.Status]] = {
    "running": ("Trading", "good"),
    "stopped": ("Halted", "critical"),
    "closed": ("Closed", "warning"),
}
_BOOK_LINE = "Book"
_SPY_LINE = "SPY"
_TERM_HELP = {
    "best": "The day the book rose the most from the day before.",
    "worst": "The day the book fell the most from the day before.",
    "drawdown": "The biggest fall from a high.",
    "early": "Too few rebalances have happened to say whether the strategy works.",
}


def _now() -> datetime:
    """The as-of time of one render (a seam for tests)."""
    return datetime.now(UTC)


def _day(day: date) -> str:
    return f"{day:%A, %B} {day.day}"


def _when(at: datetime) -> str:
    local = at.astimezone(_EASTERN)
    return f"{_day(local.date())}, {local:%I:%M %p}".replace(", 0", ", ") + " ET"


def _short_day(day: date) -> str:
    return f"{day:%b} {day.day}"


def _money(amount: float) -> str:
    return f"{'-' if amount < 0 else ''}${abs(amount):,.2f}"


def _signed_money(amount: float) -> str:
    return f"{'-' if amount < 0 else '+'}${abs(amount):,.2f}"


def _pct(value: float | None) -> str:
    return "not yet" if value is None else f"{value:+.2f}%"


def _choose_book(
    summaries: tuple[ops.BookSummary, ...], settings: Settings
) -> ops.BookSummary | None:
    """The shown book: a new link's book, the picker's, else the one open book."""
    if not summaries:
        return None
    tokens = [s.book_id for s in summaries]
    linked = st.query_params.get("book")
    if linked in tokens and st.session_state.get(_LINK_SEEN_KEY) != linked:
        st.session_state[BOOK_KEY] = linked
        st.session_state[_LINK_SEEN_KEY] = linked
    open_books = [s.book_id for s in summaries if s.is_open]
    if len(open_books) == 1:
        default = open_books[0]
    elif settings.paper.book_id in tokens:
        default = settings.paper.book_id
    else:
        default = tokens[0]
    if len(tokens) == 1:
        chosen = tokens[0]
    else:
        if st.session_state.get(BOOK_KEY) not in tokens:
            st.session_state[BOOK_KEY] = default
        chosen = str(st.selectbox("Book", tokens, key=BOOK_KEY))
    return next(s for s in summaries if s.book_id == chosen)


def _header(
    conn: duckdb.DuckDBPyConnection, summary: ops.BookSummary, view: book_view.BookView
) -> None:
    head = view.header
    assert head is not None
    st.header(summary.book_id)
    label, status = _PILL[head.status]
    theme.status_badge(label, status)
    st.write(home_view.strategy_sentence(conn, summary))
    if head.as_of_session is None or head.value is None:
        st.caption("No day has closed for this book yet, so there is no value to show.")
        return
    st.caption(f"As of the close on {_day(head.as_of_session)}.")
    tiles = st.columns(3)
    tiles[0].metric("Value", _money(head.value))
    tiles[1].metric("Return since start", _pct(head.since_start_return_pct))
    tiles[2].metric(
        "SPY over the same days",
        _pct(head.spy_return_pct),
        help=None if head.spy_return_pct is not None else "SPY prices are not available.",
    )


def _chart(piece: book_stats.RangeSlice) -> None:
    palette = theme.palette()
    frame = pd.DataFrame(
        [{"session": d, "series": _BOOK_LINE, "index": v} for d, v in piece.book]
        + [{"session": d, "series": _SPY_LINE, "index": v} for d, v in piece.spy]
    )
    names = [_BOOK_LINE, *([_SPY_LINE] if piece.spy else [])]
    chart = (
        alt.Chart(frame)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("session:T", title=None),
            y=alt.Y("index:Q", title="start = 100", scale=alt.Scale(zero=False)),
            tooltip=[
                alt.Tooltip("session:T"),
                alt.Tooltip("series:N"),
                alt.Tooltip("index:Q", format=".2f"),
            ],
            **theme.series_encodings(palette, names, _BOOK_LINE),
        )
    )
    st.altair_chart(theme.style(chart, palette), theme=None, width="stretch")


def _stat(label: str, value: str, explanation: str) -> None:
    st.metric(label, value)
    st.caption(explanation)


def _summary(perf: book_view.Performance) -> None:
    if perf.best_day is not None:
        day = perf.best_day
        _stat(
            "Best day",
            f"{_signed_money(day.dollars)} ({day.pct:+.2f}%)",
            f"{_short_day(day.session)}. {_TERM_HELP['best']}",
        )
    if perf.worst_day is not None:
        day = perf.worst_day
        _stat(
            "Worst day",
            f"{_signed_money(day.dollars)} ({day.pct:+.2f}%)",
            f"{_short_day(day.session)}. {_TERM_HELP['worst']}",
        )
    if perf.max_drawdown is not None:
        fall = perf.max_drawdown
        _stat(
            "Maximum drawdown",
            f"{_signed_money(fall.dollars)} ({fall.pct:+.2f}%)",
            f"{_short_day(fall.peak_session)} to {_short_day(fall.trough_session)}. "
            f"{_TERM_HELP['drawdown']}",
        )
    if perf.tracking is not None:
        track = perf.tracking
        value = (
            "enough to judge"
            if track.enough
            else f"too early to judge: {track.n if track.n is not None else 'unknown'}"
            f" of {track.m} rebalances"
        )
        _stat("Track record", value, _TERM_HELP["early"])


def _performance(perf: book_view.Performance | None) -> None:
    st.subheader("How it has done")
    if perf is None:
        st.caption("Nothing to draw until the book has a closed day.")
        return
    choice = st.segmented_control(
        "Range", list(_RANGES), default=_DEFAULT_RANGE, key=RANGE_KEY, label_visibility="collapsed"
    )
    key = _RANGES[choice or _DEFAULT_RANGE]
    piece = next(r for r in perf.ranges if r.range_key == key)
    left, right = st.columns([3, 1])
    with left:
        if piece.note is not None:
            st.caption(
                "This range is longer than the book's history, so it shows the whole history."
            )
        if len(piece.book) < 2:
            st.caption("Not enough days yet to draw a line.")
        else:
            _chart(piece)
    with right:
        _summary(perf)


def _today(view: book_view.BookView) -> None:
    st.subheader("Today")
    today = view.today
    if today is None:
        return
    if view.today_session is not None:
        st.caption(_day(view.today_session))
    sentences = [s for s in (today.sells.sentence, today.buys.sentence) if s]
    if not sentences and not today.problems:
        st.write(today.digest)
        return
    for sentence in sentences:
        st.write(sentence)
    for problem in today.problems:
        st.warning(problem.sentence)


def _table(rows: tuple[book_holdings.HoldingRow, ...]) -> None:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Ticker": r.ticker,
                    "Value": None if r.value is None else _money(r.value),
                    "Gain": None if r.gain is None else _signed_money(r.gain),
                    "Since bought": None if r.gain_percent is None else f"{r.gain_percent:+.2f}%",
                }
                for r in rows
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _open_trades(book: str) -> None:
    st.session_state[TRADES_BOOK_KEY] = book
    st.switch_page(TRADES_PAGE)


def _holdings(view: book_view.BookView, book: str) -> None:
    st.subheader("Holdings")
    held = view.holdings
    if held is None:
        st.caption("Nothing is held yet.")
        return
    summary = held.summary
    tiles = st.columns(4)
    tiles[0].metric("Names held", summary.count)
    tiles[1].metric("Cash", "not yet" if summary.cash is None else _money(summary.cash))
    tiles[2].metric("Up today", summary.up_today)
    tiles[3].metric("Down today", summary.down_today)
    gains, losses = st.columns(2)
    with gains:
        st.caption("Biggest gains")
        _table(summary.best)
    with losses:
        st.caption("Biggest losses")
        _table(summary.worst)
    if st.button("See all trades", key=f"book_page_trades_{book}"):
        _open_trades(book)


def _open_stop(book: str) -> None:
    st.session_state[STOP_OPEN_KEY] = book
    st.session_state[STOP_REASON_KEY] = ""
    st.session_state.pop(STOP_OUTCOME_KEY, None)


def _close_stop() -> None:
    st.session_state.pop(STOP_OPEN_KEY, None)
    st.session_state.pop(STOP_OUTCOME_KEY, None)


def _on_confirm(settings: Settings, book: str) -> None:
    """The confirm button's callback: before the next render opens the store
    (module docstring). Leaves a `KillResult` or a refusal sentence to show."""
    state = st.session_state
    reason = state.get(STOP_REASON_KEY)
    refusal = book_actions.reason_refusal(reason, settings)
    if refusal is not None:
        state[STOP_OUTCOME_KEY] = refusal
        return
    assert reason is not None
    signature = (book, reason.strip())
    if signature == state.get(STOP_LAST_KEY):
        state[STOP_OUTCOME_KEY] = (
            "This reason was already sent for this book; nothing ran. "
            "Check the card, or type a new reason to stop it again."
        )
        return
    try:
        result = book_actions.kill(settings, book, reason)
    except book_actions.KillRefused as exc:
        state[STOP_OUTCOME_KEY] = str(exc)
        return
    state[STOP_REASON_KEY] = ""
    if result.status is not book_actions.KillStatus.NOT_ENGAGED:
        state[STOP_LAST_KEY] = signature
    state[STOP_OUTCOME_KEY] = result


def _show_result(result: book_actions.KillResult, settings: Settings, book: str) -> None:
    if result.status is book_actions.KillStatus.ENGAGED:
        st.success(f"The kill switch is engaged for {book}.")
    elif result.status is book_actions.KillStatus.NOT_CONFIRMED:
        st.warning(
            f"The stop is NOT confirmed: the command did not finish within "
            f"{result.wait_seconds} seconds. It was "
            "neither cancelled nor sent again; the card shows the switch as the journal "
            "has it on the next read."
        )
    else:
        st.error(
            f"The kill switch is NOT engaged for {book} (the command ended with code "
            f"{result.exit_code}). Read its text below, then stop it at the desk: "
            f"`{book_actions.desk_command(book)}`."
        )
    if result.output:
        st.code(result.output, language=None)


@st.dialog("Stop this book now", on_dismiss=_close_stop)
def _stop_dialog(settings: Settings, book: str) -> None:
    st.markdown(f"**Stop {book} now?**")
    st.write(
        f"This engages {book}'s kill switch: no run places an order for {book} until you "
        "resume it at the desk. Nothing is sold, and other books keep trading."
    )
    outcome = st.session_state.get(STOP_OUTCOME_KEY)
    if isinstance(outcome, book_actions.KillResult):
        _show_result(outcome, settings, book)
        return
    if isinstance(outcome, str):
        st.error(outcome)
    st.text_area(
        "Why are you stopping it?",
        key=STOP_REASON_KEY,
        max_chars=settings.ui.max_reason_chars,
    )
    st.button(
        f"Stop {book} now",
        key=STOP_CONFIRM_KEY,
        type="primary",
        on_click=_on_confirm,
        args=(settings, book),
    )


def _safety(view: book_view.BookView, settings: Settings, book: str) -> None:
    st.subheader("Safety")
    safety, head = view.safety, view.header
    if safety is None or head is None:
        return
    recon = safety.reconciliation
    if recon.matches:
        st.write("Matches Alpaca (within the reconciliation's tolerance).")
        if recon.at is not None:
            st.caption(f"Checked {_when(recon.at)}.")
    else:
        text = recon.text
        if recon.at is not None:
            text = text.replace(recon.at.isoformat(), _when(recon.at))
        st.write(text)
    switch = safety.kill_switch
    if head.status == "closed":
        st.write(switch.text)
    elif switch.running:
        st.write("Trading: the kill switch is off.")
    else:
        since = f" since {_when(switch.since)}" if switch.since is not None else ""
        st.write(f"Halted{since}: {switch.reason}.")
        resume = f'tradepartner paper resume --book={book} --reason="<why>"'
        st.caption(f"To resume it, at the desk: `{resume}`.")
    for line in safety.limits:
        (st.write if line.status == "within" else st.warning)(line.sentence)
    if head.status == "closed":
        return
    refusal = book_actions.store_refusal(settings, book=book)
    if refusal is not None:
        st.caption(refusal)
        return
    st.button(
        "Stop this book now", key=f"{STOP_BUTTON_KEY}_{book}", on_click=_open_stop, args=(book,)
    )
    if st.session_state.get(STOP_OPEN_KEY) == book:
        _stop_dialog(settings, book)


_TRIAL_WORDS = {"ok": "it finished", "failed": "it failed", "no result yet": "no result yet"}


def _backtest(view: book_view.BookView) -> None:
    st.subheader("Is it doing what the backtest said?")
    card = view.backtest
    if card is None:
        return
    progress = card.shakedown
    if progress is None:
        st.caption("No shakedown span is open.")
    else:
        st.write(
            f"Shakedown: {progress.sessions_done} of {progress.sessions_needed} sessions "
            "since the span started or last restarted; `tradepartner paper shakedown` "
            "says whether they pass."
        )
        if progress.text != f"{progress.sessions_done} of {progress.sessions_needed} sessions":
            st.caption(progress.text)
    report = card.report
    if report is None:
        st.write("No tracking report yet.")
    else:
        trial = report.trial
        words = _TRIAL_WORDS.get(
            trial.status, "it was refused" if trial.status.startswith("refused") else "unknown"
        )
        flag = " A red flag was raised." if trial.red_flag else ""
        st.write(
            f"The latest tracking report runs through {_day(report.through_session)}: "
            f"{words}.{flag} The command below prints how it compares."
        )
    st.write(f"To run a new one, at the desk: `{card.command}`")
    st.caption("Running a new report counts as one test.")


def render(conn: duckdb.DuckDBPyConnection, settings: Settings) -> None:
    """Draw the Book page from the shell's one read-only connection."""
    try:
        summaries = ops.book_summaries(conn, settings, now=_now())
    except (JournalNotInitialised, SchemaVersionError):
        st.info("No book has started yet, so there is nothing to show.")
        return
    summary = _choose_book(summaries, settings)
    if summary is None:
        st.info("No book has started yet, so there is nothing to show.")
        return
    try:
        view = book_view.build(
            conn, settings, summary.book_id, _now(), top_n=settings.ui.holdings_rows
        )
    except UnreadableMarkError:
        st.error("A day's value for this book could not be read, so the page cannot be drawn.")
        return
    if view.header is None:
        st.info("This book has no window yet, so there is nothing to show.")
        return
    _header(conn, summary, view)
    _performance(view.performance)
    _today(view)
    _holdings(view, summary.book_id)
    _safety(view, settings, summary.book_id)
    _backtest(view)
