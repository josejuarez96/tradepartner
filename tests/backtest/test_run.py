"""Run orchestration and refusals (backtest spec reqs 9-11, 14; plan T39).

Every test registers a hypothesis on `fixture_store_path` and calls `run_hypothesis`
with `store_path` set to it, while the live `settings.store.path` names another
temp file. `StoreProvider` is replaced by a subclass that records every
`DataProvider` call (and its construction) and can inject an error or a write.

The frozen window: in-sample from 2018-01-31 (the benchmarks are known from
2018-01-02), holdout 2019-06-03..2020-06-30. The fixture's survivorship-gap count
share is 0.0769 at 2019-06-28 and 0 at the other sessions of the holdout window
used here, so the frozen threshold 0.05 refuses it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import duckdb
import pytest
from conftest import load_universe_fixtures, mark_pre_lab

from tradepartner.backtest import engine
from tradepartner.backtest import run as run_module
from tradepartner.backtest.frozen import frozen_values
from tradepartner.backtest.holdout import Flags, Reasons
from tradepartner.backtest.hypothesis import frozen_params_of
from tradepartner.backtest.run import run_hypothesis
from tradepartner.backtest.schedule import read_time
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.config import FORBIDDEN_AXIS_PREFIXES, Settings
from tradepartner.store import lab_registry, lab_schema, registry, schema
from tradepartner.store.db import configure_connection, insert_row, open_for_write, utc_now

SLUG = "h-run"
IN_SAMPLE_START = date(2018, 1, 31)
HOLDOUT = (date(2019, 6, 3), date(2020, 6, 30))
#: The last rebalance session before holdout.start: the default window's end.
DEFAULT_END = date(2019, 5, 31)
HOLDOUT_WINDOW = (date(2019, 5, 31), date(2019, 8, 30))
HOLDOUT_SESSIONS = (date(2019, 5, 31), date(2019, 6, 28), date(2019, 7, 31), date(2019, 8, 30))
LEVELS = {0.0, 15.0, 30.0, 60.0, 100.0}
SPEND = Flags(spend_holdout=True)
SPEND_REASON = Reasons(holdout_reason="owner spends the holdout")
OVERRIDE = Flags(spend_holdout=True, override_gap=True)
OVERRIDE_REASONS = Reasons(holdout_reason="spend", gap_reason="owner accepts the June gap")

_METHODS = (
    "universe",
    "adjusted_prices",
    "raw_prices",
    "listing_ends",
    "benchmark_ids",
    "survivorship_gap",
    "dropped_dividends",
    "late_dividends",
    "static_listing_count",
)


def _frozen() -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        holdout={"start": HOLDOUT[0], "end": HOLDOUT[1]},
        gap={"count_share_threshold": 0.05},
    )


def _store(path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(path)})


@pytest.fixture(autouse=True)
def live(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Live settings without a `.env`, whose store is another temp file."""
    real = tmp_path / "real_store.duckdb"
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("STORE__PATH", str(real))
    return real


@pytest.fixture
def store(fixture_store_path: Path) -> Path:
    frozen = _frozen()
    with open_for_write(_store(fixture_store_path)) as conn:
        registry.register_hypothesis(
            conn,
            slug=SLUG,
            family="momentum",
            title="run orchestration",
            doc_path=f"docs/hypotheses/{SLUG}.md",
            doc_sha256="0" * 64,
            params=frozen_params_of(frozen, family="momentum"),
            in_sample_start=IN_SAMPLE_START,
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            registered_by="test",
            settings=frozen,
        )
    return fixture_store_path


class Calls(list[tuple[str, Any]]):
    """`(method, t)` per provider call (`t` the read time, `late_dividends`' second
    argument); `("__init__", None)` per construction. `inserted` is set once the
    mid-run write has happened."""

    inserted = False

    def read_times(self) -> list[datetime]:
        return [t for name, t in self if name != "__init__"]


def _spy(
    monkeypatch: pytest.MonkeyPatch,
    fail: str | None = None,
    write_to: Path | None = None,
    fail_message: str = "injected provider error",
) -> Calls:
    calls = Calls()

    class Spy(StoreProvider):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            calls.append(("__init__", None))
            super().__init__(*args, **kwargs)

    def wrap(name: str) -> Any:
        def method(self: StoreProvider, *args: Any, **kwargs: Any) -> Any:
            calls.append((name, args[1] if name == "late_dividends" else args[0]))
            if name == fail:
                raise RuntimeError(fail_message)
            if name == "adjusted_prices" and write_to is not None and not calls.inserted:
                calls.inserted = True
                _insert_mid_run(self, write_to)
            return getattr(StoreProvider, name)(self, *args, **kwargs)

        return method

    for name in _METHODS:
        setattr(Spy, name, wrap(name))
    monkeypatch.setattr(run_module, "StoreProvider", Spy)
    return calls


def _insert_mid_run(provider: StoreProvider, path: Path) -> None:
    """A late bar inside the window (known before its data cutoff), ingested now,
    between two reads: it moves the data vintage at the cutoff (#1232)."""
    provider.end_step()
    with open_for_write(_store(path)) as conn:
        insert_row(
            conn,
            "prices_daily",
            {
                "security_id": "SEC_LATE",
                "session": date(2019, 5, 30),
                "open": 1.0,
                "high": 1.0,
                "low": 1.0,
                "close": 1.0,
                "volume": 1,
                "known_at": datetime(2019, 5, 30, 20, 0, tzinfo=UTC),
                "ingested_at": utc_now(),
                "source": "alpaca",
                "provenance": "bar",
            },
        )


Read = Callable[[], duckdb.DuckDBPyConnection]


@pytest.fixture
def read(store: Path) -> Iterator[Read]:
    """Opens lazily: no read-only connection may be open while a run writes."""
    conns: list[duckdb.DuckDBPyConnection] = []

    def _open() -> duckdb.DuckDBPyConnection:
        conns.append(duckdb.connect(str(store), read_only=True))
        return conns[-1]

    yield _open
    for conn in conns:
        conn.close()


def _row(conn: duckdb.DuckDBPyConnection, table: str, trial_id: int) -> dict[str, Any]:
    cursor = conn.execute(f"SELECT * FROM {table} WHERE trial_id = ?", [trial_id])
    names = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    assert len(rows) == 1, f"{table} rows for trial {trial_id}: {rows}"
    return dict(zip(names, rows[0], strict=True))


def _decisions(conn: duckdb.DuckDBPyConnection, trial_id: int) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT kind, reason, values_json FROM owner_decisions WHERE trial_id = ?", [trial_id]
    ).fetchall()
    return {kind: {"reason": reason, "values": json.loads(values)} for kind, reason, values in rows}


# --- ok, failed ---------------------------------------------------------------------


def test_ok_run_returns_results_and_leaves_an_ok_row(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch)
    outcome = run_hypothesis(SLUG, None, None, Flags(), synthetic=True, store_path=store)

    assert (outcome.status, outcome.error) == ("ok", None)
    results = outcome.results
    assert results is not None and set(results) == LEVELS
    assert max(row.n_targets for row in results[15.0].rebalances) >= 2
    conn = read()
    trial = _row(conn, "trials", outcome.trial_id)
    assert (trial["kind"], trial["synthetic"]) == ("in_sample", True)
    assert (trial["start_session"], trial["end_session"]) == (IN_SAMPLE_START, DEFAULT_END)
    assert trial["data_cutoff"] == read_time(DEFAULT_END)
    assert _row(conn, "trial_results", outcome.trial_id)["status"] == "ok"
    levels = conn.execute(
        "SELECT DISTINCT cost_per_side_bps FROM trial_equity WHERE trial_id = ?",
        [outcome.trial_id],
    ).fetchall()
    assert {level for (level,) in levels} == LEVELS
    assert calls.count(("__init__", None)) == 1
    # Every read is at or before the default window's last rebalance close.
    assert calls.read_times() and max(calls.read_times()) <= read_time(DEFAULT_END)


def test_frozen_params_used(store: Path, read: Read, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "STRATEGY__TOP_FRACTION": "0.9",
        "COSTS__PER_SIDE_BPS": "40",
        "HOLDOUT__START": "2018-06-01",
        "HOLDOUT__END": "2018-12-31",
        "GAP__COUNT_SHARE_THRESHOLD": "0.5",
    }.items():
        monkeypatch.setenv(key, value)
    seen: list[tuple[Settings, date, date, list[float]]] = []
    real_run = engine.run

    def spy_run(
        params: Settings, provider: Any, start: date, end: date, *rest: Any, **kw: Any
    ) -> Any:
        seen.append((params, start, end, list(rest[1])))
        return real_run(params, provider, start, end, *rest, **kw)

    monkeypatch.setattr(engine, "run", spy_run)
    outcome = run_hypothesis(SLUG, None, None, Flags(), store_path=store)

    assert outcome.status == "ok" and outcome.results is not None
    [(params, start, end, levels)] = seen
    assert params.strategy.top_fraction == 0.5
    assert params.costs.per_side_bps == 15.0
    assert (params.holdout.start, params.holdout.end) == HOLDOUT
    assert params.gap.count_share_threshold == 0.05
    assert (start, end) == (IN_SAMPLE_START, DEFAULT_END)
    assert set(levels) == LEVELS
    assert _row(read(), "trial_results", outcome.trial_id)["status"] == "ok"


def test_injected_provider_error_leaves_failed_and_returns_the_traceback(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    _spy(monkeypatch, fail="universe")
    outcome = run_hypothesis(SLUG, None, None, Flags(), store_path=store)

    assert (outcome.status, outcome.results) == ("failed", None)
    assert outcome.error is not None
    assert outcome.error.startswith("Traceback (most recent call last):")
    assert "in method" in outcome.error  # the spy's frame: the whole stack is kept
    assert outcome.error.rstrip().endswith("RuntimeError: injected provider error")
    result = _row(read(), "trial_results", outcome.trial_id)
    assert result["status"] == "failed"
    assert result["message"] == "RuntimeError: injected provider error"


def test_failed_message_is_scrubbed_before_it_reaches_the_registry(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception's words can carry a configured secret; the registry copy of
    the failure message is scrubbed at write time, as `ingest._clean` does (#342)."""
    secret = "fake-smtp-password-for-test-342"
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", secret)
    _spy(monkeypatch, fail="universe", fail_message=f"login failed for {secret}")
    outcome = run_hypothesis(SLUG, None, None, Flags(), store_path=store)

    assert outcome.status == "failed"
    result = _row(read(), "trial_results", outcome.trial_id)
    assert secret not in result["message"]
    assert result["message"].startswith("RuntimeError: ")


def test_row_inserted_mid_run_leaves_failed(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch, write_to=store)
    outcome = run_hypothesis(SLUG, None, None, Flags(), store_path=store)

    assert (outcome.status, outcome.results, outcome.error) == ("failed", None, None)
    assert calls.inserted and ("adjusted_prices", read_time(DEFAULT_END)) in calls
    result = _row(read(), "trial_results", outcome.trial_id)
    assert (result["status"], result["message"]) == ("failed", registry.STORE_CHANGED_MESSAGE)


# --- refusals -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [(date(2017, 12, 29), DEFAULT_END), (IN_SAMPLE_START, date(2020, 7, 31))],
    ids=["start-before-in-sample", "end-after-holdout"],
)
def test_refused_window_makes_no_provider_call(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch, start: date, end: date
) -> None:
    calls = _spy(monkeypatch)
    outcome = run_hypothesis(SLUG, start, end, SPEND, store_path=store)

    assert (outcome.status, outcome.results) == ("refused_window", None)
    assert calls == []
    conn = read()
    assert _row(conn, "trial_results", outcome.trial_id)["status"] == "refused_window"
    trial = _row(conn, "trials", outcome.trial_id)
    assert (trial["start_session"], trial["end_session"]) == (start, end)


def test_refused_holdout_makes_no_provider_call(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch)
    outcome = run_hypothesis(SLUG, *HOLDOUT_WINDOW, Flags(), store_path=store)

    assert (outcome.status, outcome.results) == ("refused_holdout", None)
    assert calls == []
    conn = read()
    assert _row(conn, "trial_results", outcome.trial_id)["status"] == "refused_holdout"
    assert _row(conn, "trials", outcome.trial_id)["kind"] == "in_sample"
    assert _decisions(conn, outcome.trial_id) == {}


def test_refused_gap_after_gap_reads_only(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch)
    outcome = run_hypothesis(SLUG, *HOLDOUT_WINDOW, SPEND, reasons=SPEND_REASON, store_path=store)

    assert (outcome.status, outcome.results) == ("refused_gap", None)
    assert calls == [
        ("__init__", None),
        *(("survivorship_gap", read_time(s)) for s in HOLDOUT_SESSIONS),
    ]
    conn = read()
    result = _row(conn, "trial_results", outcome.trial_id)
    assert result["status"] == "refused_gap"
    assert "2019-06-28" in result["message"]
    trial = _row(conn, "trials", outcome.trial_id)
    assert (trial["kind"], trial["holdout_reason"]) == ("holdout", SPEND_REASON.holdout_reason)
    assert trial["gap_override_reason"] is None
    decisions = _decisions(conn, outcome.trial_id)
    assert set(decisions) == {"holdout_spend"}
    assert decisions["holdout_spend"]["reason"] == SPEND_REASON.holdout_reason


def test_override_without_a_gap_reason_spends_nothing(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch)
    blank = Reasons(holdout_reason="spend", gap_reason="  ")
    refused = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, OVERRIDE, reasons=blank, synthetic=True, store_path=store
    )

    assert (refused.status, refused.results) == ("refused_gap", None)
    assert calls == []
    conn = read()
    trial = _row(conn, "trials", refused.trial_id)
    assert (trial["kind"], trial["holdout_reason"], trial["gap_override_reason"]) == (
        "in_sample",
        None,
        None,
    )
    assert _decisions(conn, refused.trial_id) == {}
    conn.close()

    spend = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, OVERRIDE, reasons=OVERRIDE_REASONS, synthetic=True, store_path=store
    )
    assert spend.status == "ok"
    assert _row(read(), "trials", spend.trial_id)["holdout_repeat"] is False


def test_gap_override_runs_and_records_the_owner_decision(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcome = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, OVERRIDE, reasons=OVERRIDE_REASONS, synthetic=True, store_path=store
    )

    assert outcome.status == "ok" and outcome.results is not None
    conn = read()
    assert _row(conn, "trial_results", outcome.trial_id)["status"] == "ok"
    trial = _row(conn, "trials", outcome.trial_id)
    assert (trial["kind"], trial["gap_override_reason"]) == ("holdout", OVERRIDE_REASONS.gap_reason)
    decisions = _decisions(conn, outcome.trial_id)
    assert set(decisions) == {"holdout_spend", "gap_override"}
    override = decisions["gap_override"]
    assert override["reason"] == OVERRIDE_REASONS.gap_reason
    assert override["values"]["count_share"]["2019-06-28"] == pytest.approx(1 / 13)
    assert override["values"]["threshold"] == 0.05


def test_a_second_spend_by_the_same_hypothesis_needs_holdout_repeat(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, OVERRIDE, reasons=OVERRIDE_REASONS, synthetic=True, store_path=store
    )
    assert first.status == "ok"

    calls = _spy(monkeypatch)
    second = run_hypothesis(
        SLUG, *HOLDOUT_WINDOW, OVERRIDE, reasons=OVERRIDE_REASONS, synthetic=True, store_path=store
    )
    assert (second.status, second.results) == ("refused_holdout", None)
    assert calls == []

    repeat_flags = Flags(spend_holdout=True, override_gap=True, holdout_repeat=True)
    third = run_hypothesis(
        SLUG,
        *HOLDOUT_WINDOW,
        repeat_flags,
        reasons=OVERRIDE_REASONS,
        synthetic=True,
        store_path=store,
    )
    assert third.status == "ok" and third.results is not None

    conn = read()
    marks = {
        o.trial_id: (
            _row(conn, "trials", o.trial_id)["kind"],
            _row(conn, "trials", o.trial_id)["holdout_repeat"],
        )
        for o in (first, second, third)
    }
    assert marks == {
        first.trial_id: ("holdout", False),
        second.trial_id: ("in_sample", False),
        third.trial_id: ("holdout", True),
    }


# --- synthetic --------------------------------------------------------------------------


def test_synthetic_refused_on_the_real_store(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STORE__PATH", str(store))
    calls = _spy(monkeypatch)
    with pytest.raises(registry.RealStoreRefused):
        run_hypothesis(SLUG, None, None, Flags(), synthetic=True)

    assert calls == []
    assert read().execute("SELECT COUNT(*) FROM trials").fetchone() == (0,)


def test_synthetic_accepted_on_a_temp_file(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch, live: Path
) -> None:
    _spy(monkeypatch, fail="universe")
    outcome = run_hypothesis(SLUG, None, None, Flags(), synthetic=True, store_path=store)

    assert not live.exists()
    trial = _row(read(), "trials", outcome.trial_id)
    assert trial["synthetic"] is True


# --- kind="tracking" (plan T65b; spec req 10, "Tracking trial") ---------------------

#: A holdout ending inside the existing holdout window, chosen so its tracking
#: window [`TRACKING_START`, `TRACKING_END`] (the first two rebalance sessions
#: strictly after `TRACKING_SLUG_HOLDOUT_END`) still falls inside the fixture's
#: price history, which ends 2020-06-30.
TRACKING_SLUG = "h-tracking"
TRACKING_SLUG_HOLDOUT_END = date(2019, 6, 28)
TRACKING_START = date(2019, 7, 31)
TRACKING_END = date(2019, 9, 30)


def _frozen_tracking() -> Settings:
    return Settings(
        _env_file=None,
        strategy={"top_fraction": 0.5},
        holdout={"start": HOLDOUT[0], "end": TRACKING_SLUG_HOLDOUT_END},
        gap={"count_share_threshold": 0.05},
    )


@pytest.fixture
def tracking_store(store: Path) -> Path:
    """`store` plus a second hypothesis, same family, registered with a holdout
    ending earlier (module comment) so its tracking window has real bars."""
    frozen = _frozen_tracking()
    with open_for_write(_store(store)) as conn:
        registry.register_hypothesis(
            conn,
            slug=TRACKING_SLUG,
            family="momentum",
            title="tracking run",
            doc_path=f"docs/hypotheses/{TRACKING_SLUG}.md",
            doc_sha256="1" * 64,
            params=frozen_params_of(frozen, family="momentum"),
            in_sample_start=IN_SAMPLE_START,
            holdout_start=HOLDOUT[0],
            holdout_end=TRACKING_SLUG_HOLDOUT_END,
            registered_by="test",
            settings=frozen,
        )
    return store


def test_tracking_run_leaves_family_sharpes_and_holdout_spends_unchanged(
    tracking_store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before any trial of this family runs, `family_sharpes` counts no
    in-sample trial and `family_holdout_spends` lists none; a `kind="tracking"`
    run (`trials.kind = 'tracking'`, filtered out of both queries) must leave
    both exactly as they were (no read connection opens before the run's write
    connection, since DuckDB allows only one mode per file per process)."""
    before_sharpes = registry.FamilySharpes(n_trials=0, raw=(), excess_spy=())
    before_spends: list[registry.HoldoutSpend] = []
    _spy(monkeypatch)

    outcome = run_hypothesis(
        TRACKING_SLUG,
        TRACKING_START,
        TRACKING_END,
        Flags(),
        synthetic=True,
        store_path=tracking_store,
        kind="tracking",
    )

    assert outcome.status == "ok"
    conn = read()
    trial = _row(conn, "trials", outcome.trial_id)
    assert trial["kind"] == "tracking"
    assert registry.family_sharpes(conn, "momentum") == before_sharpes
    assert registry.family_holdout_spends(conn, "momentum") == before_spends


def test_tracking_window_on_or_before_holdout_end_is_refused(
    tracking_store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _spy(monkeypatch)
    outcome = run_hypothesis(
        TRACKING_SLUG,
        TRACKING_SLUG_HOLDOUT_END,
        TRACKING_END,
        Flags(),
        synthetic=True,
        store_path=tracking_store,
        kind="tracking",
    )

    assert (outcome.status, outcome.results) == ("refused_window", None)
    assert calls == []
    trial = _row(read(), "trials", outcome.trial_id)
    assert trial["kind"] == "in_sample"


# --- the development boundary (ADR 0016 points 2 and 4; plan T142b) -----------------

#: A boundary inside the fixture's in-sample years; the default window then ends at the
#: last month end on or before it.
BOUNDARY = date(2018, 12, 15)
BOUNDARY_END = date(2018, 11, 30)


def _set_boundary(store: Path, day: date) -> None:
    with open_for_write(_store(store)) as conn:
        registry.write_development_boundary(conn, boundary=day, reason="test")


def test_the_default_window_ends_at_the_boundary_and_the_trial_records_it(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    _set_boundary(store, BOUNDARY)
    calls = _spy(monkeypatch)
    outcome = run_hypothesis(SLUG, None, None, Flags(), synthetic=True, store_path=store)

    assert outcome.status == "ok"
    trial = _row(read(), "trials", outcome.trial_id)
    assert (trial["start_session"], trial["end_session"]) == (IN_SAMPLE_START, BOUNDARY_END)
    assert trial["development_boundary"] == BOUNDARY
    assert calls.read_times() and max(calls.read_times()) <= read_time(BOUNDARY_END)


@pytest.mark.parametrize(
    ("window", "flags", "match"),
    [
        # An in-sample window ending after the boundary, whatever its flags.
        ((IN_SAMPLE_START, DEFAULT_END), Flags(), "after the development boundary"),
        ((IN_SAMPLE_START, DEFAULT_END), SPEND, "after the development boundary"),
        # A spend starting before holdout.start (a dead session), flag or not.
        (HOLDOUT_WINDOW, SPEND, "before holdout.start 2019-06-03"),
    ],
    ids=["in-sample", "in-sample-with-spend-flag", "spend-from-a-dead-session"],
)
def test_a_window_past_the_boundary_is_refused_window_with_no_spend(
    store: Path,
    read: Read,
    monkeypatch: pytest.MonkeyPatch,
    window: tuple[date, date],
    flags: Flags,
    match: str,
) -> None:
    _set_boundary(store, BOUNDARY if window[1] == DEFAULT_END else DEFAULT_END)
    calls = _spy(monkeypatch)
    outcome = run_hypothesis(SLUG, *window, flags, reasons=SPEND_REASON, store_path=store)

    assert (outcome.status, outcome.results) == ("refused_window", None)
    assert calls == []
    conn = read()
    result = _row(conn, "trial_results", outcome.trial_id)
    assert result["status"] == "refused_window" and match in result["message"]
    assert _row(conn, "trials", outcome.trial_id)["kind"] == "in_sample"
    assert _decisions(conn, outcome.trial_id) == {}


def test_a_spend_inside_the_holdout_runs_under_the_boundary(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary leaves a spend that lies inside the holdout to the holdout and gap
    rules, as before (here the gap gate refuses the June 2019 session)."""
    _set_boundary(store, DEFAULT_END)
    _spy(monkeypatch)
    outcome = run_hypothesis(
        SLUG, date(2019, 6, 3), date(2019, 8, 30), SPEND, reasons=SPEND_REASON, store_path=store
    )
    assert outcome.status == "refused_gap"


def test_a_forward_holdouts_tracking_window_starts_inside_it(
    tracking_store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0016 point 4: the run passes the family's first registration day, so a
    holdout that starts after it is forward and its tracking window may start at its
    first rebalance session. Registered today, the same holdout is historical and the
    window is refused."""
    _spy(monkeypatch)
    window = (date(2019, 6, 28), date(2019, 9, 30))
    refused = run_hypothesis(
        SLUG, *window, Flags(), synthetic=True, store_path=tracking_store, kind="tracking"
    )
    assert refused.status == "refused_window"
    with open_for_write(_store(tracking_store)) as conn:
        conn.execute(
            "UPDATE hypotheses SET registered_at = ? WHERE family = 'momentum'",
            [datetime(2019, 5, 1, 12, tzinfo=UTC)],
        )
    outcome = run_hypothesis(
        SLUG, *window, Flags(), synthetic=True, store_path=tracking_store, kind="tracking"
    )
    assert outcome.status == "ok"
    assert _row(read(), "trials", outcome.trial_id)["kind"] == "tracking"


def test_tracking_run_needs_an_explicit_start_and_end(tracking_store: Path) -> None:
    with pytest.raises(ValueError, match="explicit start and end"):
        run_hypothesis(
            TRACKING_SLUG,
            None,
            TRACKING_END,
            Flags(),
            synthetic=True,
            store_path=tracking_store,
            kind="tracking",
        )
    with pytest.raises(ValueError, match="explicit start and end"):
        run_hypothesis(
            TRACKING_SLUG,
            TRACKING_START,
            None,
            Flags(),
            synthetic=True,
            store_path=tracking_store,
            kind="tracking",
        )


def test_a_pre_lab_registration_loads_backtests_and_records(
    fixture_store_path: Path, read: Read
) -> None:
    """A registration frozen under the pre-T96 key set (no `schedule.*`, as H1 on the
    owner's store) goes through `load_frozen` → backtest → `record_results` at
    `month_end`, and its stored hash is untouched (strategy-lab T96)."""
    frozen = _frozen()
    all_params = frozen_params_of(frozen, family="momentum")
    # Pre-T96, so also before `gap.stale_listing_sessions` (#1199) landed.
    params = {
        k: v
        for k, v in all_params.items()
        if not k.startswith(("schedule.", "gap.stale_listing_sessions"))
    }
    hashed = sha256(json.dumps(params, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    with open_for_write(_store(fixture_store_path)) as conn:
        record = registry.register_hypothesis(
            conn,
            slug=SLUG,
            family="momentum",
            title="pre-lab twin",
            doc_path=f"docs/hypotheses/{SLUG}.md",
            doc_sha256="0" * 64,
            params=params,
            in_sample_start=IN_SAMPLE_START,
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            registered_by="test",
            settings=frozen,
        )
    assert record.params_sha256 == hashed

    outcome = run_hypothesis(
        SLUG, None, None, Flags(), synthetic=True, store_path=fixture_store_path
    )

    assert (outcome.status, outcome.error) == ("ok", None)
    conn = read()
    assert _row(conn, "trial_results", outcome.trial_id)["status"] == "ok"
    stored = registry.get_hypothesis(conn, SLUG)
    assert stored.params_sha256 == hashed
    assert not any(key.startswith("schedule.") for key in stored.params)


def test_profitability_registration_passes_its_stored_family_to_the_engine(
    fixture_store_path: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run path uses the stored family rather than momentum's default."""
    frozen = _frozen()
    with open_for_write(_store(fixture_store_path)) as conn:
        registry.register_hypothesis(
            conn,
            slug="b3-run",
            family="profitability",
            title="family dispatch",
            doc_path="docs/hypotheses/b3-run.md",
            doc_sha256="0" * 64,
            params=frozen_params_of(frozen, family="profitability"),
            in_sample_start=IN_SAMPLE_START,
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            registered_by="test",
            settings=frozen,
        )
    seen: list[str] = []

    def capture(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs["family"])
        raise RuntimeError("stopped after dispatch")

    monkeypatch.setattr(engine, "run", capture)
    outcome = run_hypothesis("b3-run", None, None, Flags(), store_path=fixture_store_path)
    assert outcome.status == "failed"
    assert seen == ["profitability"]
    assert read().execute("SELECT COUNT(*) FROM trials").fetchone() == (1,)


# --- the strategy-lab rules on the run path (strategy-lab spec req 5; plan T110) ----


def _register_more(
    path: Path,
    slug: str,
    *,
    doc_sha256: str = "2" * 64,
    pre_lab: bool = False,
    drop_schedule: bool = False,
    **frozen_overrides: Any,
) -> registry.HypothesisRecord:
    """Another `momentum` hypothesis on `path` with the module's window and holdout
    (a different `strategy.top_fraction` by default, so a different frozen set)."""
    overrides: dict[str, Any] = {
        "strategy": {"top_fraction": 0.4},
        "holdout": {"start": HOLDOUT[0], "end": HOLDOUT[1]},
        "gap": {"count_share_threshold": 0.05},
        **frozen_overrides,
    }
    frozen = Settings(_env_file=None, **overrides)
    params = frozen_params_of(frozen, family="momentum")
    if drop_schedule:
        # Pre-T96, so also before `gap.stale_listing_sessions` (#1199) landed.
        later = ("schedule.", "gap.stale_listing_sessions")
        params = {k: v for k, v in params.items() if not k.startswith(later)}
    with open_for_write(_store(path)) as conn:
        record = registry.register_hypothesis(
            conn,
            slug=slug,
            family="momentum",
            title=slug,
            doc_path=f"docs/hypotheses/{slug}.md",
            doc_sha256=doc_sha256,
            params=params,
            in_sample_start=IN_SAMPLE_START,
            holdout_start=HOLDOUT[0],
            holdout_end=frozen.holdout.end,
            registered_by="test",
            settings=frozen,
        )
        if pre_lab:
            mark_pre_lab(conn, record.hypothesis_id)
    return record


@pytest.fixture
def lab_path(store: Path) -> Path:
    """`store` (H1's twin `SLUG` registered) with the lab tables applied and the twin
    marked pre-lab, as the lab migration (T113) would leave it."""
    with open_for_write(_store(store)) as conn:
        lab_schema.apply_lab_schema(conn)
        mark_pre_lab(conn, registry.get_hypothesis(conn, SLUG).hypothesis_id)
    return store


def _family_rules(path: Path, cap: int) -> None:
    rules = Settings(_env_file=None, lab={"max_family_holdout_spends": cap})
    with open_for_write(_store(path)) as conn:
        first = registry.get_hypothesis(conn, SLUG)
        fixed = {
            key: value
            for key, value in frozen_values(first).items()
            if key.startswith(FORBIDDEN_AXIS_PREFIXES)
        }
        lab_registry.write_family_rules(
            conn,
            family="momentum",
            first_hypothesis_id=first.hypothesis_id,
            parent_family=None,
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            in_sample_start=IN_SAMPLE_START,
            fixed_params=fixed,
            sr_star_seed_annual=None,
            settings=rules,
        )


def _sweep_variant(path: Path, slug: str = "mom-grid--r1-v1") -> registry.HypothesisRecord:
    """A one-variant sweep whose variant is `slug`."""
    record = _register_more(path, slug, doc_sha256="5" * 64)
    with open_for_write(_store(path)) as conn:
        sweep = lab_registry.register_sweep(
            conn,
            slug="mom-grid",
            family="momentum",
            title="grid",
            doc_path="docs/sweeps/mom-grid.md",
            doc_sha256="5" * 64,
            grid={"strategy.top_fraction": [0.4]},
            n_variants=1,
            selection_statistic="sharpe_annual_excess_spy",
            expected_excess_cagr_spy_pp=2.0,
            expected_range_pp=(0.0, 4.0),
            promote_at_least=0.6,
            retire_below=0.1,
            in_sample_start=IN_SAMPLE_START,
            holdout_start=HOLDOUT[0],
            holdout_end=HOLDOUT[1],
            registered_by="test",
            settings=Settings(_env_file=None),
        )
        lab_registry.write_sweep_variant(
            conn,
            sweep_id=sweep.sweep_id,
            variant_index=1,
            hypothesis_id=record.hypothesis_id,
            fingerprint="f" * 64,
            variant_params={"strategy.top_fraction": 0.4},
        )
    return record


def _spend(slug: str, path: Path, repeat: bool = False) -> run_module.RunOutcome:
    flags = Flags(spend_holdout=True, override_gap=True, holdout_repeat=repeat)
    return run_hypothesis(
        slug, *HOLDOUT_WINDOW, flags, reasons=OVERRIDE_REASONS, synthetic=True, store_path=path
    )


def test_backtest_of_a_variant_is_refused_variant_with_its_rows_and_no_provider_call(
    lab_path: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spec's `backtest <variant-slug>` criterion at the run path: a trial and a
    result row with status `refused_variant`, no provider call, N and V unchanged. (The
    CLI's exit code 2 for the status is T111's `STATUS_EXIT` entry.)"""
    variant = _sweep_variant(lab_path)
    first = run_hypothesis(SLUG, None, None, Flags(), store_path=lab_path)
    assert first.status == "ok"
    conn = read()
    before = registry.family_sharpes(conn, "momentum")
    conn.close()
    calls = _spy(monkeypatch)

    outcome = run_hypothesis(variant.slug, None, None, Flags(), store_path=lab_path)

    assert (outcome.status, outcome.results) == ("refused_variant", None)
    assert calls == []
    conn = read()
    result = _row(conn, "trial_results", outcome.trial_id)
    assert result["status"] == "refused_variant"
    assert "sweep run" in result["message"]
    trial = _row(conn, "trials", outcome.trial_id)
    assert (trial["hypothesis_id"], trial["kind"]) == (variant.hypothesis_id, "in_sample")
    assert registry.family_sharpes(conn, "momentum") == before


def test_a_variant_cannot_spend_the_holdout_either(
    lab_path: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    variant = _sweep_variant(lab_path)
    calls = _spy(monkeypatch)
    outcome = _spend(variant.slug, lab_path)
    assert outcome.status == "refused_variant"
    assert calls == []
    conn = read()
    assert _row(conn, "trials", outcome.trial_id)["kind"] == "in_sample"
    assert _decisions(conn, outcome.trial_id) == {}


@pytest.mark.parametrize("with_sweep", [False, True], ids=["no-sweep", "sweep"])
def test_a_direct_row_without_a_pre_lab_row_is_refused_holdout(
    lab_path: Path, read: Read, monkeypatch: pytest.MonkeyPatch, with_sweep: bool
) -> None:
    """A `hypotheses` row inserted by the test (a nudged `top_fraction`, a new
    fingerprint) with no `pre_lab_hypotheses` row and no promotion cannot spend."""
    if with_sweep:
        _sweep_variant(lab_path)
    nudged = _register_more(lab_path, "h-nudged", strategy={"top_fraction": 0.51})
    calls = _spy(monkeypatch)
    outcome = _spend(nudged.slug, lab_path)
    assert (outcome.status, outcome.results) == ("refused_holdout", None)
    assert calls == []
    conn = read()
    assert "pre-lab or promoted" in _row(conn, "trial_results", outcome.trial_id)["message"]
    assert _row(conn, "trials", outcome.trial_id)["kind"] == "in_sample"
    assert registry.family_holdout_spends(conn, "momentum") == []


def test_a_promoted_hypothesis_spends(lab_path: Path, read: Read) -> None:
    promoted = _register_more(lab_path, "h-promoted")
    with open_for_write(_store(lab_path)) as conn:
        conn.execute(
            "INSERT INTO owner_decisions VALUES (?, ?, 'promotion', ?, NULL, '{}', 'argmax')",
            [100, utc_now(), promoted.hypothesis_id],
        )
    outcome = _spend(promoted.slug, lab_path)
    assert outcome.status == "ok"
    assert _row(read(), "trials", outcome.trial_id)["kind"] == "holdout"


@pytest.mark.parametrize("drop_schedule", [False, True], ids=["schedule-keys", "pre-lab-keys"])
def test_h1_twin_with_its_pre_lab_row_spends(
    fixture_store_path: Path, read: Read, drop_schedule: bool
) -> None:
    """H1's twin spends with its `pre_lab_hypotheses` row, whether its stored params
    carry `schedule.*` or not."""
    with open_for_write(_store(fixture_store_path)) as conn:
        lab_schema.apply_lab_schema(conn)
    twin = _register_more(fixture_store_path, "h1-twin", pre_lab=True, drop_schedule=drop_schedule)
    assert any(k.startswith("schedule.") for k in twin.params) is not drop_schedule
    outcome = _spend(twin.slug, fixture_store_path)
    assert outcome.status == "ok"
    trial = _row(read(), "trials", outcome.trial_id)
    assert (trial["kind"], trial["holdout_repeat"]) == ("holdout", False)


def test_the_family_cap_at_one_refuses_a_second_spend_naming_the_first(
    lab_path: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    _family_rules(lab_path, cap=1)
    member = _register_more(lab_path, "h-member", pre_lab=True)
    first = _spend(SLUG, lab_path)
    assert first.status == "ok"

    calls = _spy(monkeypatch)
    second = _spend(member.slug, lab_path)
    assert (second.status, second.results) == ("refused_holdout", None)
    assert calls == []
    message = _row(read(), "trial_results", second.trial_id)["message"]
    assert "cap of 1 holdout spends" in message
    assert f"{SLUG} (trial {first.trial_id}, ok)" in message


def test_below_the_cap_a_grandfathered_member_spends_as_a_holdout_repeat(
    lab_path: Path, read: Read
) -> None:
    """A pre-lab member whose frozen values differ from the family rules (a later
    holdout end, a higher threshold: grandfathered) spends under the Phase 3 rules,
    marked `holdout_repeat` after the twin's spend, below a cap of 2."""
    _family_rules(lab_path, cap=2)
    member = _register_more(
        lab_path,
        "h-grandfathered",
        pre_lab=True,
        holdout={"start": HOLDOUT[0], "end": date(2020, 5, 29)},
        gap={"count_share_threshold": 0.06},
    )
    with open_for_write(_store(lab_path)) as conn:
        assert [m.slug for m in lab_registry.grandfathered_members(conn)] == [member.slug]
    first = _spend(SLUG, lab_path)
    second = _spend(member.slug, lab_path)
    assert (first.status, second.status) == ("ok", "ok")
    conn = read()
    trials = [_row(conn, "trials", o.trial_id) for o in (first, second)]
    marks = [(t["kind"], t["holdout_repeat"]) for t in trials]
    assert marks == [("holdout", False), ("holdout", True)]


def test_on_a_plain_fixture_store_decide_gets_no_lab_state(
    store: Path, read: Read, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A store without the lab tables (the owner's store at version P) decides
    exactly as Phase 3: `decide` is called with `lab=None` on every call, and a
    hypothesis with no pre-lab row and no promotion spends."""
    seen: list[object] = []
    real_decide = run_module.decide

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(kwargs.get("lab", "missing"))
        return real_decide(*args, **kwargs)

    monkeypatch.setattr(run_module, "decide", spy)
    nudged = _register_more(store, "h-nudged", strategy={"top_fraction": 0.51})
    outcome = _spend(nudged.slug, store)
    assert outcome.status == "ok"
    assert seen == [None, None]  # the open, then the gap gate
    assert _row(read(), "trials", outcome.trial_id)["kind"] == "holdout"


# --- the fixture marker on the backtest path (spec, Definitions, Fixture marker) -----


def _unmarked_copy(source: Path, target: Path) -> Path:
    """`source`'s rows in every schema table, but no `store_markers` table: what a
    copy of the real store looks like. (Built by copying rows, not by deleting the
    marker, since only `lab_schema` may write `store_markers`.)"""
    conn = duckdb.connect(str(target))
    configure_connection(conn)
    schema.init_schema(conn)
    conn.execute(f"ATTACH '{source}' AS src (READ_ONLY)")
    tables = conn.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = 'src' "
        "AND table_name <> 'store_markers'"
    ).fetchall()
    for (table,) in tables:
        conn.execute(f"INSERT INTO main.{table} SELECT * FROM src.main.{table}")
    conn.execute("DETACH src")
    assert not lab_schema.has_fixture_marker(conn)
    conn.close()
    return target


def test_a_store_without_the_fixture_marker_is_refused_before_any_trial(
    store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copy = _unmarked_copy(store, tmp_path / "copy_of_real.duckdb")
    calls = _spy(monkeypatch)
    for synthetic in (False, True):
        with pytest.raises(registry.UnmarkedStoreRefused, match="fixture"):
            run_hypothesis(SLUG, None, None, Flags(), synthetic=synthetic, store_path=copy)
    assert calls == []
    with duckdb.connect(str(copy), read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM trials").fetchone() == (0,)


def test_the_fixture_store_is_accepted_and_every_trial_on_it_is_synthetic(
    store: Path, read: Read
) -> None:
    outcome = run_hypothesis(SLUG, None, None, Flags(), synthetic=False, store_path=store)
    assert outcome.status == "ok"
    conn = read()
    assert _row(conn, "trials", outcome.trial_id)["synthetic"] is True
    assert registry.family_sharpes(conn, "momentum").n_trials == 0


def test_the_backtest_runner_temp_store_carries_the_marker_and_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `backtest-runner` agent's recipe (`.claude/agents/backtest-runner.md`, step
    1): `init_schema`, then `load_universe_fixtures`; its store carries the row."""
    path = tmp_path / "runner" / "store.duckdb"
    path.parent.mkdir()
    conn = duckdb.connect(str(path))
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, Path(__file__).resolve().parents[1] / "fixtures" / "universe")
    assert lab_schema.has_fixture_marker(conn)
    conn.close()
    _register_more(path, SLUG)
    outcome = run_hypothesis(SLUG, None, None, Flags(), synthetic=True, store_path=path)
    assert outcome.status == "ok"


def test_a_tracking_run_on_the_real_store_needs_no_marker(
    tracking_store: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`store_path` equal to `settings.store.path` is the real store, not a copy: the
    paper report's tracking trial (non-synthetic, `kind="tracking"`) runs on it as
    before, though it has no marker row."""
    real = _unmarked_copy(tracking_store, tmp_path / "real.duckdb")
    monkeypatch.setenv("STORE__PATH", str(real))
    _spy(monkeypatch)
    outcome = run_hypothesis(
        TRACKING_SLUG,
        TRACKING_START,
        TRACKING_END,
        Flags(),
        synthetic=False,
        store_path=real,
        kind="tracking",
    )
    assert outcome.status == "ok"
    with duckdb.connect(str(real), read_only=True) as conn:
        trial = _row(conn, "trials", outcome.trial_id)
        assert (trial["kind"], trial["synthetic"]) == ("tracking", False)
        assert not lab_schema.has_fixture_marker(conn)
