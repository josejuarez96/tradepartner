"""Event-study driver contracts (T303)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tradepartner.research import NoRunHandle, _issue_run_handle
from tradepartner.research.event_study.run import (
    count_study_events,
    entry_session,
    match_events,
    run_study,
)

ET = ZoneInfo("America/New_York")


def instant(day: int, hour: int, minute: int = 0, *, month: int = 6) -> datetime:
    return datetime(2018, month, day, hour, minute, tzinfo=ET).astimezone(UTC)


@pytest.mark.parametrize(
    ("known_at", "instant_entry", "date_entry"),
    [
        (instant(4, 8), date(2018, 6, 4), date(2018, 6, 5)),
        (instant(4, 16), date(2018, 6, 5), date(2018, 6, 5)),
        (instant(4, 16, 1), date(2018, 6, 5), date(2018, 6, 5)),
        (instant(9, 12), date(2018, 6, 11), date(2018, 6, 11)),
        (instant(3, 16, month=7), date(2018, 7, 5), date(2018, 7, 5)),
    ],
)
def test_entry_rules(known_at: datetime, instant_entry: date, date_entry: date) -> None:
    assert entry_session(known_at, "next_open_after_instant") == instant_entry
    assert entry_session(known_at, "next_session_after_date") == date_entry


def test_shared_counts_use_exact_groups_and_one_exit_per_event() -> None:
    counts = count_study_events(
        [
            {"group": "normal", "exited_early_2": True, "exited_early_3": True},
            {"group": "pre_normal", "exited_early_2": False, "exited_early_3": False},
        ],
        {"groups": ["normal", "pre_normal"], "horizons": [2, 3]},
        {"n_dropped_horizon_pre_normal": 1},
        {"n_traded_normal": 1, "n_traded_pre_normal": 0},
    )
    assert counts == {
        "n_events_normal": 1,
        "n_events_pre_normal": 2,
        "n_traded_normal": 1,
        "n_traded_pre_normal": 0,
        "n_delisted_exits": 1,
    }


_MATCH_DESIGN: dict[str, Any] = {
    "bucket_by": ["quarter"],
    "peer_by": ["stratum"],
    "n_buckets": 1,
    "min_bucket_events": 2,
    "peer_group": "normal",
    "peer_window": 2,
    "min_peers": 1,
    "near_k": 1,
    "horizons": [1],
}


def _match_row(
    event_id: str, group: str, day: int, cap: float, ret: float, known_at: datetime | None = None
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "security_id": event_id,
        "group": group,
        "stratum": "A",
        "quarter": 1,
        "entry": f"2018-06-{day:02d}",
        "entry_index": day,
        "known_at": known_at or instant(day - 1, 16, 1),
        "mcap": cap,
        "r_1": ret,
        "rb_1": ret,
        "beta": 1.0,
    }


def _matched(rows: list[dict[str, Any]], event_id: str, mode: str, **design: Any) -> dict[str, Any]:
    matched, _ = match_events(rows, {**_MATCH_DESIGN, **design, "matching": mode})
    return next(row for row in matched if row["event_id"] == event_id)


def test_causal_matching_is_invariant_to_future_events() -> None:
    rows = [
        _match_row("prior", "normal", 4, 10.0, 0.01),
        _match_row("target", "early", 5, 20.0, 0.03),
        _match_row("future", "normal", 6, 100.0, 0.02),
    ]
    causal_full = _matched(rows, "target", "causal")
    assert causal_full == _matched(rows[:2], "target", "causal")
    assert causal_full["bucket"] == 0
    assert causal_full["peer_n_1"] == 1
    ex_post_full = _matched(rows, "target", "ex_post")
    assert ex_post_full["peer_n_1"] == 2
    assert ex_post_full["spread_1"] != _matched(rows[:2], "target", "ex_post")["spread_1"]
    assert (
        _matched(rows, "target", "causal", n_buckets=2)["bucket"]
        == _matched(rows[:2], "target", "causal", n_buckets=2)["bucket"]
    )
    assert (
        _matched(rows, "target", "ex_post", n_buckets=2)["bucket"]
        != _matched(rows[:2], "target", "ex_post", n_buckets=2)["bucket"]
    )


def test_causal_matching_every_event_sees_only_its_past() -> None:
    """For every event, dropping every event not known before its entry open
    leaves its bucket, peers and spread unchanged (#2493 no look-ahead)."""
    rows = [
        _match_row(f"x{day}{group}", group, day, cap, ret)
        for day, group, cap, ret in (
            (4, "normal", 30.0, 0.01),
            (4, "early", 12.0, 0.02),
            (5, "normal", 5.0, -0.01),
            (6, "early", 40.0, 0.04),
            (6, "normal", 22.0, 0.00),
            (7, "normal", 8.0, 0.03),
            (8, "early", 18.0, -0.02),
            (8, "normal", 50.0, 0.05),
        )
    ]
    for mode_design in ({"n_buckets": 1}, {"n_buckets": 2, "min_bucket_events": 3}):
        full = {
            row["event_id"]: row for row in match_events(rows, {**_MATCH_DESIGN, **mode_design})[0]
        }
        for event in rows:
            past = [row for row in rows if row["entry_index"] <= event["entry_index"]]
            alone = _matched(past, event["event_id"], "causal", **mode_design)
            assert full[event["event_id"]] == alone, event["event_id"]


def test_causal_refuses_an_event_not_known_before_its_entry_open() -> None:
    """Both entry rules enter strictly after `known_at`; a row that does not is
    refused under causal matching rather than matched on an unknown fact."""
    target = _match_row("target", "early", 5, 20.0, 0.03)
    before = _match_row("peer", "normal", 5, 10.0, 0.01, known_at=instant(5, 9, 29))
    assert _matched([target, before], "target", "causal")["peer_n_1"] == 1
    at_open = {**before, "known_at": instant(5, 9, 30)}
    with pytest.raises(ValueError, match="not known before its entry open"):
        match_events([target, at_open], {**_MATCH_DESIGN, "matching": "causal"})


def test_no_handle_precedes_every_read(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("market data read before handle validation")

    monkeypatch.setattr("tradepartner.research.event_study.reader.EventStudyReader", forbidden)
    with pytest.raises(NoRunHandle):
        run_study(None, {}, [])


def handle(tmp_path: Path) -> object:
    dataset = SimpleNamespace(
        event_start=date(2018, 6, 1), event_end=date(2018, 6, 8), split_spans={}
    )
    return _issue_run_handle(
        run_id=1,
        slug="test",
        dataset=dataset,
        n_configurations_declared=2,
        refusal=None,
        database=str(tmp_path / "store.duckdb"),
        split="full",
    )


def study() -> dict[str, object]:
    return {"configurations": 2, "kill": [{"name": "long"}, {"name": "short"}]}


@pytest.mark.parametrize(
    "rows",
    [
        [
            {
                "event_id": "x",
                "security_id": "ISSUER",
                "known_at": datetime(2018, 6, 4, 15, tzinfo=UTC).replace(tzinfo=None),
            }
        ],
        [{"event_id": "x", "security_id": "ISSUER", "known_at": instant(4, 8)}] * 2,
        [{"event_id": "x", "security_id": "ISSUER", "known_at": instant(11, 8)}],
    ],
)
def test_bad_event_table_refused_before_price_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, object]]
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("market data read before event validation")

    monkeypatch.setattr("tradepartner.research.event_study.reader.EventStudyReader", forbidden)
    with pytest.raises(ValueError):
        run_study(handle(tmp_path), study(), rows)


def test_configuration_overrun_precedes_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("market data read before budget check")

    monkeypatch.setattr("tradepartner.research.event_study.reader.EventStudyReader", forbidden)
    with pytest.raises(ValueError, match="configurations"):
        run_study(handle(tmp_path), {"configurations": 3, "kill": [{}, {}, {}]}, [])


@pytest.mark.parametrize("two_horizons", [False, True], ids=["single-horizon", "short-exit-only"])
def test_event_stage_delisting_and_causal_matching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, two_horizons: bool
) -> None:
    """Stage B on synthetic bars: entry, returns, held delisting, and causal pools."""
    import json

    import polars as pl

    from tradepartner.config import Settings

    sessions = [date(2018, 6, d) for d in (1, 4, 5, 6, 7, 8)]
    prices = {
        "ISSUER": [20.0, 21.0, 23.0, 24.0, None, None],
        "PEER": [25.0, 26.0, 28.0, 29.0, 30.0, 31.0],
        "SPY_ID": [10.0, 11.0, 13.0, 14.0, 15.0, 16.0],
    }
    bars = pl.DataFrame(
        [
            {"security_id": sid, "session": day, "open": price, "close": price, "volume": 1000}
            for sid, values in prices.items()
            for day, price in zip(sessions, values, strict=True)
            if price is not None
        ]
    )

    class FakeReader:
        late = False

        def __init__(self, _settings: object, _end: date) -> None:
            self.max_read_t = datetime(2018, 6, 8, 20, tzinfo=UTC)

        def benchmark(self, _symbol: str, *, start: date) -> str:
            return "SPY_ID"

        def bars(
            self, _ids: list[str], *, sessions_from: date
        ) -> tuple[pl.DataFrame, pl.DataFrame]:
            if two_horizons:
                short_only = pl.concat(
                    [
                        bars,
                        pl.DataFrame(
                            [
                                {
                                    "security_id": "ISSUER",
                                    "session": date(2018, 6, 8),
                                    "open": 25.0,
                                    "close": 25.0,
                                    "volume": 1000,
                                }
                            ]
                        ),
                    ]
                )
                return short_only, short_only
            if self.late:
                changed = bars.with_columns(
                    pl.when(
                        (pl.col("security_id") == "PEER") & (pl.col("session") == date(2018, 6, 8))
                    )
                    .then(99.0)
                    .otherwise(pl.col("close"))
                    .alias("close")
                )
                return changed, changed
            return bars, bars

        def shares(
            self, _sid: str, _known_at: datetime, *, fact_name: str, facts_read: str
        ) -> list[dict[str, object]]:
            result: list[dict[str, object]] = [
                {
                    "as_of_date": date(2018, 6, 5),
                    "value": 100_000.0 if _sid == "PEER" else 1_000_000.0,
                    "known_at": instant(5, 15),
                }
            ]
            if self.late and facts_read == "window_end":
                result.append(
                    {
                        "as_of_date": date(2018, 6, 5),
                        "value": 5_000_000.0,
                        "known_at": instant(7, 15),
                    }
                )
            return result

    monkeypatch.setattr("tradepartner.research.event_study.run.EventStudyReader", FakeReader)
    cfg = Settings(_env_file=None).model_copy(
        update={
            "store": Settings(_env_file=None).store.model_copy(
                update={"path": str(tmp_path / "store.duckdb")}
            ),
            "research": Settings(_env_file=None).research.model_copy(
                update={"data_dir": str(tmp_path / "artifacts")}
            ),
        }
    )
    design = {
        "configurations": 2,
        "horizons": [2],
        "groups": ["early", "late", "normal"],
        "peer_group": "normal",
        "entry": "next_session_after_date",
        "facts_read": "event_known_at",
        "bucket_by": ["quarter"],
        "peer_by": ["stratum"],
        "n_buckets": 1,
        "min_bucket_events": 1,
        "price_floor": 1.0,
        "volume_floor": 1.0,
        "volume_lookback": 1,
        "min_volume_bars": 1,
        "shares_stale_days": 30,
        "shares_fact": "shares_outstanding",
        "benchmark_security": "SPY",
        "peer_window": 2,
        "min_peers": 1,
        "near_k": 1,
        "beta_lookback": 3,
        "beta_min_bars": 2,
        "cost_bps": [15.0, 30.0],
        "borrow_bps": [0.0, 30.0],
        "z": 1.645,
        "cluster_by": ["month", "quarter"],
        "kill": [
            {
                "name": name,
                "side": name,
                "group": "early" if name == "long" else "late",
                "horizon": 2,
                "readings": ["raw"],
                "metric": "gross_spread_bp",
                "floor": 0.0,
                "t_metric": "net_bp",
                "t_floor": 0.0,
                "t": "min",
                "cost_bps": 30.0,
                "borrow_bps": 0.0 if name == "long" else 30.0,
            }
            for name in ("long", "short")
        ],
    }
    if two_horizons:
        design["horizons"] = [2, 3]
    events = [
        {
            "event_id": "e",
            "security_id": "ISSUER",
            "known_at": instant(5, 16, 1),
            "group": "early",
            "stratum": "A",
        },
        {
            "event_id": "n",
            "security_id": "PEER",
            "known_at": instant(6, 16, 1),
            "group": "normal",
            "stratum": "A",
        },
    ]
    parsed = SimpleNamespace(
        study=design, window_start=date(2018, 6, 1), window_end=date(2018, 6, 8), slug="test"
    )
    default = run_study(handle(tmp_path), parsed, events, settings=cfg)
    artifact = json.loads(default.artifact_path.read_text())
    early = next(row for row in artifact["events"] if row["event_id"] == "e")
    assert early["r_2"] == pytest.approx(0.0)  # last available close on entry day
    assert early["spy_2"] == pytest.approx(15 / 14 - 1)
    assert early["last_bar_2"] == "2018-06-06"
    assert early["scheduled_exit_2"] == "2018-06-07"
    assert early["exited_early_2"] is True
    assert default.secondary["n_delisted_exits"] == 1
    if two_horizons:
        assert early["exited_early_3"] is False
        return
    assert default.secondary["n_dropped_no_peers_early"] == 1
    assert artifact["meta"]["max_read_t"] == "2018-06-08T20:00:00+00:00"
    FakeReader.late = True
    with_later_data = run_study(handle(tmp_path), parsed, events, settings=cfg)
    later_early = next(
        row
        for row in json.loads(with_later_data.artifact_path.read_text())["events"]
        if row["event_id"] == "e"
    )
    assert later_early["beta"] == early["beta"]
    assert later_early["mcap"] == early["mcap"]
    FakeReader.late = False
    design["matching"] = "ex_post"
    ex_post = run_study(handle(tmp_path), parsed, events, settings=cfg)
    assert ex_post.secondary["n_dropped_no_peers_early"] == 0
    # A peer disclosed after the treated event, but before its entry open,
    # belongs to the causal pool. The cutoff is entry, not event acceptance.
    design["matching"] = "causal"
    design["entry"] = "next_open_after_instant"
    events[1]["known_at"] = instant(6, 8)
    same_entry = run_study(handle(tmp_path), parsed, events, settings=cfg)
    assert same_entry.secondary["n_dropped_no_peers_early"] == 0
    # A later, smaller-cap event must not move the treated event's bucket.
    # This test fails if the default pools the whole quarter ex post.
    events[1]["known_at"] = instant(6, 16, 1)
    design["entry"] = "next_session_after_date"
    design["n_buckets"] = 2
    causal_cut = run_study(handle(tmp_path), parsed, events, settings=cfg)
    causal_event = next(
        row
        for row in json.loads(causal_cut.artifact_path.read_text())["events"]
        if row["event_id"] == "e"
    )
    assert causal_event["bucket"] == 0
    design["matching"] = "ex_post"
    pooled_cut = run_study(handle(tmp_path), parsed, events, settings=cfg)
    pooled_event = next(
        row
        for row in json.loads(pooled_cut.artifact_path.read_text())["events"]
        if row["event_id"] == "e"
    )
    assert pooled_event["bucket"] == 1


def test_stage_b_on_synthetic_store(tmp_path: Path) -> None:
    """The driver reads real as-of bars and shares from the T302 fixture store."""
    import duckdb
    from fixtures.research.event_study_store import build_event_study_store

    from tradepartner.store.db import insert_row

    cfg = build_event_study_store(tmp_path / "store.duckdb")
    cfg = cfg.model_copy(
        update={
            "research": cfg.research.model_copy(update={"data_dir": str(tmp_path / "artifacts")})
        }
    )
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    try:
        for sid, closes in (("ISSUER", (11.0, 13.0, 14.0)), ("SPY_ID", (12.0, 15.0, 16.0))):
            for day, close in zip((5, 6, 7), closes, strict=True):
                t = datetime(2018, 6, day, 20, tzinfo=UTC)
                insert_row(
                    conn,
                    "prices_daily",
                    {
                        "security_id": sid,
                        "session": date(2018, 6, day),
                        "open": close,
                        "high": close,
                        "low": close,
                        "close": close,
                        "volume": 1000,
                        "known_at": t,
                        "ingested_at": t,
                        "source": "test",
                        "provenance": "bar",
                    },
                )
        t = datetime(2018, 6, 4, 20, tzinfo=UTC)
        insert_row(
            conn,
            "facts",
            {
                "security_id": "SPY_ID",
                "fact_name": "shares_outstanding",
                "as_of_date": date(2018, 6, 4),
                "class_member": "",
                "value": 1_000_000.0,
                "known_at": t,
                "ingested_at": t,
                "source": "test",
                "provenance": "filing",
            },
        )
    finally:
        conn.close()
    design = {
        "configurations": 2,
        "horizons": [2],
        "groups": ["early", "late", "normal"],
        "peer_group": "normal",
        "entry": "next_session_after_date",
        "facts_read": "event_known_at",
        "bucket_by": ["quarter"],
        "peer_by": ["stratum"],
        "n_buckets": 1,
        "min_bucket_events": 1,
        "price_floor": 1.0,
        "volume_floor": 1.0,
        "volume_lookback": 1,
        "min_volume_bars": 1,
        "shares_stale_days": 30,
        "shares_fact": "shares_outstanding",
        "benchmark_security": "SPY",
        "peer_window": 1,
        "min_peers": 1,
        "near_k": 1,
        "beta_lookback": 3,
        "beta_min_bars": 2,
        "cost_bps": [15.0, 30.0],
        "borrow_bps": [0.0, 30.0],
        "z": 1.645,
        "cluster_by": ["month", "quarter"],
        "kill": [
            {
                "name": name,
                "side": name,
                "group": "early" if name == "long" else "late",
                "horizon": 2,
                "readings": ["raw"],
                "metric": "gross_spread_bp",
                "floor": 0.0,
                "t_metric": "net_bp",
                "t_floor": 0.0,
                "t": "min",
                "cost_bps": 30.0,
                "borrow_bps": 0.0 if name == "long" else 30.0,
            }
            for name in ("long", "short")
        ],
    }
    parsed = SimpleNamespace(
        study=design,
        window_start=date(2018, 6, 1),
        window_end=date(2018, 6, 8),
        slug="test",
    )
    events = [
        {
            "event_id": "e",
            "security_id": "ISSUER",
            "known_at": instant(6, 16, 1),
            "group": "early",
            "stratum": "A",
        },
        {
            "event_id": "p",
            "security_id": "SPY_ID",
            "known_at": instant(6, 16, 1),
            "group": "normal",
            "stratum": "A",
        },
    ]
    result = run_study(handle(tmp_path), parsed, events, settings=cfg)
    assert result.secondary["n_traded_early"] == 1
    assert result.secondary["n_traded_normal"] == 1
    assert result.artifact_path.is_file()
    assert len(result.artifact_sha256) == 64
    import json

    artifact = json.loads(result.artifact_path.read_text())
    early = next(row for row in artifact["events"] if row["event_id"] == "e")
    assert early["mcap"] == 13_000_000.0  # June 7's later shares revision is excluded
    assert early["entry"] == "2018-06-07"
    assert early["r_2"] == pytest.approx(11 / 13 - 1)  # adjusted open reflects dividend
    assert early["peer_n_2"] == 1
    # Remove a later shares revision and change a post-entry bar in the real
    # fixture store. Causal market cap and beta must remain identical.
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    try:
        conn.execute("DELETE FROM facts WHERE security_id = 'ISSUER' AND value = 2000000")
        conn.execute(
            "UPDATE prices_daily SET close = 20 WHERE security_id = 'ISSUER' AND session = ?",
            [date(2018, 6, 8)],
        )
    finally:
        conn.close()
    changed = run_study(handle(tmp_path), parsed, events, settings=cfg)
    changed_event = next(
        row
        for row in json.loads(changed.artifact_path.read_text())["events"]
        if row["event_id"] == "e"
    )
    assert changed_event["mcap"] == early["mcap"]
    assert changed_event["beta"] == early["beta"]
    assert changed_event["r_2"] != early["r_2"]
