"""E1 event-builder core and the per-line kill window (#3503)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from tradepartner.calendar import all_sessions, next_session, session_close
from tradepartner.config import Settings
from tradepartner.research.event_study.earnings import (
    day_zero,
    dedupe_releases,
    disseminated_on,
    kish,
    power_counts,
    session_events,
)
from tradepartner.research.event_study.run import summarise_study
from tradepartner.research.experiment import ExperimentFileError, parse_experiment_file
from tradepartner.store import schema
from tradepartner.store.db import insert_row

C1B = (
    Path(__file__).resolve().parents[2] / "fixtures" / "experiments" / "c1b-filing-delay-matched.md"
)
EARLY = datetime(2019, 1, 2, 12, tzinfo=UTC)
SESSION = date(2019, 5, 1)  # a Wednesday; close 20:00 UTC


def _utc(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=UTC)


@pytest.mark.parametrize(
    ("accepted", "expected"),
    [
        (_utc(2019, 5, 1, 12), date(2019, 5, 1)),  # 08:00 New York, pre-open
        (_utc(2019, 5, 1, 16), date(2019, 5, 1)),  # 12:00, intraday
        (_utc(2019, 5, 1, 20), date(2019, 5, 2)),  # 16:00, at the close
        (_utc(2019, 5, 1, 21, 15), date(2019, 5, 2)),  # 17:15, the filed-date trap
        (_utc(2019, 11, 29, 18, 30), date(2019, 12, 2)),  # 13:30 on a 13:00 half day
        (_utc(2019, 5, 4, 15), date(2019, 5, 6)),  # Saturday
    ],
)
def test_day_zero(accepted: datetime, expected: date) -> None:
    assert day_zero(accepted) == expected


def test_day_zero_refuses_naive() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        day_zero(datetime(2019, 5, 1, 12))  # noqa: DTZ001


def test_dissemination_after_an_edgar_holiday() -> None:
    # Friday 18:00 New York; Monday 2019-10-14 is Columbus Day: XNYS open, EDGAR shut.
    accepted = _utc(2019, 10, 11, 22)
    assert day_zero(accepted) == date(2019, 10, 14)
    assert disseminated_on(accepted) == date(2019, 10, 15)
    assert disseminated_on(_utc(2019, 10, 11, 21, 15)) == date(2019, 10, 11)  # 17:15


def _store(rows: list[dict[str, Any]], *, cut: datetime | None = None) -> duckdb.DuckDBPyConnection:
    """Two listed CIKs; `rows` are `filing_events` rows mirrored into `filing_index`
    (an `index_accepted_at` key overrides the index's stamp). Rows known after `cut`
    are left out, as a store truncated at `cut` would hold them."""
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    for sid in ("AAA", "BBB"):
        common = {"known_at": EARLY, "ingested_at": EARLY, "source": "test"}
        insert_row(conn, "securities", {"security_id": sid, "cik": f"C{sid}", "name": sid,
                   "benchmark": False, **common, "provenance": "snapshot"})  # fmt: skip
        insert_row(conn, "listings", {"security_id": sid, "ticker": sid, "exchange": "XNYS",
                   "valid_from": date(2019, 1, 2), **common, "provenance": "snapshot"})  # fmt: skip
    for row in rows:
        if cut is not None and row["accepted_at"] > cut:
            continue
        stamp = {"known_at": row["accepted_at"], "ingested_at": row["accepted_at"]}
        base = {"cik": row["cik"], "accession": row["accession"], "form": row["form"]}
        insert_row(conn, "filing_events", {**base, "items": row["items"],
                   "accepted_at": row["accepted_at"], **stamp, "source": "test",
                   "provenance": "filing"})  # fmt: skip
        insert_row(conn, "filing_index", {**base, "company_name": row["cik"],
                   "filed_on": row["accepted_at"].date(), "listed_on": row["accepted_at"].date(),
                   "accepted_at": row.get("index_accepted_at", row["accepted_at"]), **stamp,
                   "source": "test", "provenance": "filing"})  # fmt: skip
    return conn


def _filing(accession: str, accepted: datetime, **extra: Any) -> dict[str, Any]:
    return {"cik": "CAAA", "accession": accession, "form": "8-K", "items": "2.02,9.01",
            "accepted_at": accepted, **extra}  # fmt: skip


ROWS = [
    _filing("pre-open", _utc(2019, 5, 1, 12)),
    _filing("after-close", _utc(2019, 5, 1, 21, 15)),  # 17:15 New York
    _filing("amended", _utc(2019, 5, 1, 13), form="8-K/A"),
    _filing("other-item", _utc(2019, 5, 1, 14), items="12.02"),
    _filing("bad-stamp", _utc(2019, 5, 1, 15), cik="CBBB", index_accepted_at=_utc(2019, 5, 1, 9)),
]


def test_an_8k_accepted_after_the_close_is_not_that_sessions_event() -> None:
    conn = _store(ROWS)
    today = session_events(conn, SESSION, {"AAA", "BBB"})
    assert [e["accession"] for e in today["events"]] == ["pre-open"]
    assert (today["n_8ka"], today["n_stamp_mismatch"], today["n_edgar_holiday"]) == (1, 1, 0)
    tomorrow = session_events(conn, next_session(SESSION), {"AAA", "BBB"})
    assert [e["accession"] for e in tomorrow["events"]] == ["after-close"]
    assert tomorrow["events"][0]["s0"] == date(2019, 5, 2)


def test_session_events_are_truncation_invariant() -> None:
    full = session_events(_store(ROWS), SESSION, {"AAA", "BBB"})
    cut = session_events(_store(ROWS, cut=session_close(SESSION)), SESSION, {"AAA", "BBB"})
    assert full == cut


def test_session_events_keep_universe_members_only() -> None:
    assert session_events(_store(ROWS), SESSION, {"BBB"})["events"] == []


def test_dedupe_keeps_first_of_a_chain_from_the_last_kept() -> None:
    sessions = all_sessions()
    start = sessions.index(date(2019, 5, 1))
    days = [sessions[start + k] for k in (0, 15, 30, 31)]
    events = [{"cik": "CAAA", "s0": day} for day in days] + [{"cik": "CBBB", "s0": days[1]}]
    events.sort(key=lambda e: e["s0"])
    kept, dropped = dedupe_releases(events, gap_sessions=20)
    assert [(e["cik"], e["s0"]) for e in kept] == [
        ("CAAA", days[0]),
        ("CBBB", days[1]),
        ("CAAA", days[2]),
    ]
    assert dropped == 2


def test_kish_and_power_counts_by_hand() -> None:
    assert kish([2, 2]) == 2.0
    assert kish([3, 1]) == pytest.approx(16 / 10)
    assert kish([]) == 0.0
    # Q1 2020: 4 events on 2 dates (Kish 2); Q2: 2 events on 1 date (Kish 1); 6 sessions.
    entries = [date(2020, 1, 2)] * 2 + [date(2020, 1, 3)] * 2 + [date(2020, 4, 1)] * 2
    result = power_counts(
        entries, [date(2020, 1, d) for d in range(2, 8)], tail_share=0.5,
        overlap_factor=2.0, needed_effective=0.5,
    )  # fmt: skip
    assert result["bunching"] == pytest.approx(2.0)
    assert result["n_eff"] == pytest.approx(0.5 * 6 / (2.0 * 2.0))
    assert (result["months_with_events"], result["pass"]) == (2, True)


def _parse(tmp_path: Path, text: str) -> Any:
    experiments = tmp_path / "experiments"
    experiments.mkdir(parents=True)
    (tmp_path / "research").mkdir()
    (tmp_path / "research" / "claims.toml").write_text(
        '[[claim]]\nid = "QI-2"\ngrade = "SUPPORTED"\n[[claim]]\nid = "LS-6"\ngrade = "SUPPORTED"\n'
    )
    (experiments / C1B.name).write_text(text)
    return parse_experiment_file(
        experiments / C1B.name, experiments, settings=Settings(_env_file=None)
    )


def _with_start(value: str) -> str:
    source = C1B.read_text()
    marker = 'side = "long"'
    assert source.count(marker) == 1
    return source.replace(marker, f"{marker}\nentry_start = {value}")


def test_entry_start_is_optional_and_hashed(tmp_path: Path) -> None:
    plain = _parse(tmp_path / "plain", C1B.read_text())
    windowed = _parse(tmp_path / "windowed", _with_start("2020-01-02"))
    assert all("entry_start" not in line for line in json.loads(plain.params_json)["study"]["kill"])
    assert windowed.params_sha256 != plain.params_sha256
    assert date(2020, 1, 2) in [line.get("entry_start") for line in windowed.study["kill"]]


@pytest.mark.parametrize("value", ['"2020-01-02"', "2020-01-02T00:00:00Z", "20200102"])
def test_entry_start_must_be_a_date(tmp_path: Path, value: str) -> None:
    with pytest.raises(ExperimentFileError, match="entry_start"):
        _parse(tmp_path, _with_start(value))


def test_unknown_kill_key_still_refused(tmp_path: Path) -> None:
    text = C1B.read_text().replace('side = "long"', 'side = "long"\nentry_end = 2021-01-04')
    with pytest.raises(ExperimentFileError, match=r"study\.kill keys"):
        _parse(tmp_path, text)


def test_windowed_line_judges_only_later_entries(tmp_path: Path) -> None:
    design = dict(_parse(tmp_path, C1B.read_text()).study)
    full = {**design["kill"][0], "readings": ["raw"], "floor": 0.0, "t_floor": -1e9}
    windowed = {**full, "name": "long2020", "entry_start": "2020-01-02"}
    design["kill"] = [windowed, full]  # the windowed line first: it must not be `kill_long`
    design["configurations"] = 2
    values = {"2019-06-03": -0.04, "2019-09-03": -0.06, "2020-03-02": 0.03, "2020-06-01": 0.05}
    processed = [
        {"group": full["group"], "security_id": f"s{i}", "bucket": 0, "acc_date": day,
         "entry": day, "qidx": i, "entry_index": i * 100, "beta_gap": 0.0, "logmcap_gap": 0.0,
         **{f"{k}_{h}": v for k in ("spread", "r", "spread_badj", "spread_sz", "spread_szb")
            for h in design["horizons"]},
         **{f"spy_{h}": 0.0 for h in design["horizons"]}}
        for i, (day, v) in enumerate(values.items())
    ]  # fmt: skip
    summary = summarise_study(processed, {}, design)
    assert summary["kill"]["long2020"]["readings"]["raw"]["gross_spread_bp"] == pytest.approx(400)
    assert summary["kill"][full["name"]]["readings"]["raw"]["gross_spread_bp"] == pytest.approx(-50)
    assert summary["secondary"]["kill_long2020"] == 0.0
    assert summary["secondary"]["kill_long"] == 1.0
