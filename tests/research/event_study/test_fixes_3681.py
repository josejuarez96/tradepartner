"""#3681: split-adjusted market cap reads and a name-independent long kill."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from fixtures.research.event_study_store import build_event_study_store

from tradepartner.research.event_study.reader import EventStudyReader
from tradepartner.research.event_study.stats import net_long
from tradepartner.research.event_study.summary import summarise

END = date(2018, 6, 8)
EVENT = datetime(2018, 6, 5, 15, tzinfo=UTC)


def test_split_factor_is_point_in_time(tmp_path: Path) -> None:
    """Only splits known at the read time, effective in `(as_of, through]`."""
    settings = build_event_study_store(tmp_path / "store.duckdb")
    known = datetime(2018, 6, 6, 20, tzinfo=UTC)  # after EVENT, before the cut
    conn = duckdb.connect(str(tmp_path / "store.duckdb"))
    try:
        conn.execute(
            "INSERT INTO corporate_actions (security_id, action_type, ex_date, ratio_or_amount,"
            " known_at, ingested_at, source, provenance) VALUES"
            " ('ISSUER', 'split', DATE '2018-06-05', 3.0, ?, ?, 'test', 'action')",
            [known, known],
        )
    finally:
        conn.close()
    reader = EventStudyReader(settings, END)
    span = {"as_of": date(2018, 6, 4), "through": date(2018, 6, 6)}
    assert reader.split_factor("ISSUER", EVENT, **span) == 1.0  # not yet known at the event
    assert reader.split_factor("ISSUER", EVENT, facts_read="window_end", **span) == 3.0
    # Effective on or before the fact's date, or after the price date: not applied.
    same_day = {"as_of": date(2018, 6, 5), "through": date(2018, 6, 6)}
    assert reader.split_factor("ISSUER", EVENT, facts_read="window_end", **same_day) == 1.0
    before = {"as_of": date(2018, 6, 4), "through": date(2018, 6, 4)}
    assert reader.split_factor("ISSUER", EVENT, facts_read="window_end", **before) == 1.0


def _rows(spread: float) -> list[dict[str, object]]:
    rng = random.Random(3)
    rows: list[dict[str, object]] = []
    for i in range(40):
        for group in ("early", "late"):
            noise = rng.gauss(0, 0.001)
            rows.append(
                {
                    "event_id": f"{group}{i}",
                    "group": group,
                    "security_id": f"S{i}{group}",
                    "entry_index": i * 3,
                    "acc_date": f"2018-0{1 + i % 6}-10",
                    "qidx": i % 4,
                    "spread_5": spread + noise,
                    "spread_badj_5": spread + noise,
                    "spread_sz_5": spread + noise,
                    "spread_szb_5": spread + noise,
                    "r_5": spread,
                    "spy_5": 0.0,
                    "beta_gap": 0.0,
                    "logmcap_gap": 0.0,
                }
            )
    return rows


def _summary(spread: float, readings: tuple[str, ...]) -> dict[str, object]:
    line = {
        "readings": readings,
        "metric": "net_bp",
        "floor_bp": 0.0,
        "t_floor": -1e9,
        "which_t": "min",
    }
    return summarise(
        _rows(spread),
        {},
        groups=("early", "normal", "late"),
        peer_group="normal",
        horizons=(5,),
        costs=(30.0,),
        borrows=(30.0,),
        cluster_by=("month", "quarter"),
        kill_lines={"long": line, "short": line},
        primary_cost=30.0,
        short_borrow=30.0,
        z=1.645,
        peer_window=0,
    )


def test_fallback_long_kill_uses_the_net_figure() -> None:
    """A long line judged on `net_bp` is killed by its real net, not by a missing key."""
    won = _summary(0.05, ("raw",))
    net = [net_long(float(r["spread_5"]), 30.0) for r in _rows(0.05) if r["group"] == "early"]  # type: ignore[arg-type]
    assert won["kill"]["long"]["readings"]["raw"]["net_bp"] == pytest.approx(  # type: ignore[index]
        sum(net) / len(net) * 10_000
    )
    assert won["secondary"]["kill_long"] == 0.0  # type: ignore[index]
    assert _summary(-0.05, ("raw",))["secondary"]["kill_long"] == 1.0  # type: ignore[index]
