"""Hand-computed contracts for pure event-study maths."""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from tradepartner.research.event_study.peers import assign_buckets, attach_spreads, select_peers
from tradepartner.research.event_study.stats import (
    cluster_stats,
    hold_overlap,
    hold_overlap_sessions,
    kish_clusters,
    net_long,
    net_short,
    overlap_result_interval,
    result_interval,
)
from tradepartner.research.event_study.summary import evaluate_kill, summarise

KILL_LINES = {
    "long": {
        "readings": ("raw", "badj", "sz", "szb"),
        "metric": "gross_spread_bp",
        "floor_bp": 60.0,
        "t_floor": 1.0,
        "which_t": "min",
    },
    "short": {
        "readings": ("raw", "badj", "sz", "szb"),
        "metric": "net_bp",
        "floor_bp": 60.0,
        "t_floor": 1.0,
        "which_t": "min",
    },
}


REQUIRED = {
    "cluster_by": ("month", "quarter"),
    "primary_cost": 30.0,
    "short_borrow": 30.0,
    "z": 1.645,
    "peer_window": 0,
}


def test_cluster_stats_and_undefined() -> None:
    got = cluster_stats([1.0, 3.0, 2.0, 4.0], ["a", "a", "b", "b"])
    assert got["mean"] == 2.5
    assert got["se"] == pytest.approx(math.sqrt(2) / 4)
    assert got["t"] == pytest.approx(2.5 / (math.sqrt(2) / 4))
    assert got["clusters"] == 2
    assert math.isnan(cluster_stats([], [])["t"])
    assert math.isnan(cluster_stats([1.0], ["a"])["mean"])
    with pytest.raises(ValueError):
        cluster_stats([1.0], [])
    assert kish_clusters(["a", "a", "b"]) == pytest.approx(9 / 5)


def test_overlap_and_costs() -> None:
    holds = [
        ("A", date(2020, 1, 1), date(2020, 1, 3)),
        ("A", date(2020, 1, 2), date(2020, 1, 4)),
        ("B", date(2020, 1, 3), date(2020, 1, 3)),
    ]
    assert hold_overlap(holds) == {"mean_open": 1.75, "max_open": 3, "same_security": 1}
    assert hold_overlap_sessions([("A", 0, 2), ("A", 1, 3), ("B", 2, 2)]) == {
        "mean_open": 1.75,
        "max_open": 3,
        "same_security": 1,
    }
    assert net_long(0.02, 30) == pytest.approx(0.014)
    assert net_short(0.02, 30, 30, 60) == pytest.approx(-0.026 - 0.003 * 60 / 252)


def test_buckets_peers_and_spreads() -> None:
    rows = [
        {
            "event_id": "t",
            "issuer_key": "T",
            "group": "early",
            "quarter": 1,
            "stratum": "Q",
            "mcap": 2.0,
            "entry_index": 10,
            "r_60": 0.10,
            "rb_60": 0.08,
            "beta": 1.2,
        },
        {
            "event_id": "p",
            "issuer_key": "P",
            "group": "normal",
            "quarter": 1,
            "stratum": "Q",
            "mcap": 3.0,
            "entry_index": 11,
            "r_60": 0.04,
            "rb_60": 0.03,
            "beta": 1.0,
        },
        {
            "event_id": "self",
            "issuer_key": "T",
            "group": "normal",
            "quarter": 1,
            "stratum": "Q",
            "mcap": 2.0,
            "entry_index": 10,
            "r_60": 0.90,
            "rb_60": 0.90,
            "beta": 1.0,
        },
    ]
    bucketed = assign_buckets(
        rows, bucket_by=("quarter", "stratum"), n_buckets=1, min_bucket_events=2
    )
    assert [r["bucket"] for r in bucketed] == [0, 0, 0]
    peers = select_peers(
        bucketed[0], bucketed, peer_group="normal", peer_by=("stratum",), peer_window=1
    )
    assert [p["event_id"] for p in peers] == ["p"]
    spread = attach_spreads(
        bucketed,
        horizons=(60,),
        treated_groups=("early",),
        peer_group="normal",
        peer_by=("stratum",),
        peer_window=1,
        min_peers=1,
        near_k=1,
    )
    assert spread[0]["spread_60"] == pytest.approx(0.06)
    assert spread[0]["spread_badj_60"] == pytest.approx(0.05)
    assert spread[0]["peer_n_60"] == 1
    assert spread[0]["beta_gap"] == pytest.approx(0.2)
    assert (
        "spread_60"
        not in attach_spreads(
            bucketed,
            horizons=(60,),
            treated_groups=("early",),
            peer_group="normal",
            peer_by=("stratum",),
            peer_window=1,
            min_peers=2,
            near_k=1,
        )[0]
    )
    assert all("bucket" not in r for r in rows)


def test_quantile_cut_and_nearest_peer() -> None:
    cut_rows = [{"quarter": 1, "mcap": float(n)} for n in (1, 2, 3, 4)]
    assert [
        row["bucket"]
        for row in assign_buckets(
            cut_rows, bucket_by=("quarter",), n_buckets=2, min_bucket_events=4
        )
    ] == [0, 0, 1, 1]
    rows = [
        {
            "event_id": "treated",
            "issuer_key": "T",
            "group": "early",
            "bucket": 0,
            "mcap": 1.1,
            "entry_index": 10,
            "r_60": 0.10,
            "rb_60": 0.09,
            "beta": 1.0,
        },
        {
            "event_id": "near",
            "issuer_key": "N",
            "group": "normal",
            "bucket": 0,
            "mcap": 1.0,
            "entry_index": 10,
            "r_60": 0.02,
            "rb_60": 0.01,
            "beta": 1.0,
        },
        {
            "event_id": "far",
            "issuer_key": "F",
            "group": "normal",
            "bucket": 0,
            "mcap": 2.0,
            "entry_index": 10,
            "r_60": 0.06,
            "rb_60": 0.05,
            "beta": 1.0,
        },
    ]
    result = attach_spreads(
        rows,
        horizons=(60,),
        treated_groups=("early",),
        peer_group="normal",
        peer_by=(),
        peer_window=0,
        min_peers=2,
        near_k=1,
    )[0]
    assert result["spread_60"] == pytest.approx(0.06)
    assert result["spread_sz_60"] == pytest.approx(0.08)


def test_kill_nan_and_interval() -> None:
    line = {
        "readings": ("raw", "badj"),
        "metric": "net_bp",
        "floor_bp": 10.0,
        "t_floor": 1.0,
        "which_t": "min",
    }
    assert evaluate_kill(
        line, {"raw": {"net_bp": 20.0, "t_min": 2.0}, "badj": {"net_bp": math.nan, "t_min": 2.0}}
    )["killed"]
    with pytest.raises(ValueError, match="metric"):
        evaluate_kill({**line, "metric": "hit_rate"}, {})
    with pytest.raises(ValueError, match="kill t"):
        evaluate_kill({**line, "which_t": "independent"}, {})
    estimate = result_interval([1.0, 3.0, 2.0, 4.0], ["a", "a", "b", "b"])
    assert estimate["effect"] == 2.5
    assert estimate["ci_low"] == pytest.approx(2.5 - 1.96 * math.sqrt(2) / 4)
    assert estimate["mde_80"] == pytest.approx((1.96 + 0.8416212335729143) * math.sqrt(2) / 4)


def _null_rows(rng: random.Random) -> list[dict[str, object]]:
    """Twenty events per date, entries 20 sessions apart, 60-session holds sharing shocks."""
    rows: list[dict[str, object]] = []
    shocks = [rng.gauss(0, 1) for _ in range(92)]
    for d in range(90):
        common = sum(shocks[d : d + 3]) / math.sqrt(3)
        day = date(2020, 1, 1) + timedelta(days=d * 20)
        for k in range(20):
            spread = math.sqrt(0.10) * common + math.sqrt(0.90) * rng.gauss(0, 1)
            rows.append(
                {
                    "group": "early",
                    "security_id": f"s{d}_{k}",
                    "entry_index": d * 20,
                    "acc_date": day.isoformat(),
                    "qidx": d // 4,
                    "spread_60": spread,
                    "spread_badj_60": spread,
                    "spread_sz_60": spread,
                    "spread_szb_60": spread,
                    "r_60": spread,
                    "spy_60": 0.0,
                    "beta_gap": 0.0,
                    "logmcap_gap": 0.0,
                }
            )
    return rows


def test_null_calibration_through_summarise() -> None:
    """Zero true effect: summarise rejects near 5%; a naive per-event t over-rejects.

    500 trials, fixed seed. Monte Carlo SE of a 5% rate is
    sqrt(.05 * .95 / 500) = 0.0097, so the accepted band is 5% +- 3 SE = [0.02, 0.08].
    """
    rng = random.Random(2473)
    trials = 500
    rejected = ci_rejected = naive = 0
    for _ in range(trials):
        rows = _null_rows(rng)
        got = summarise(
            rows,
            {},
            groups=("early", "normal", "late"),
            peer_group="normal",
            horizons=(60,),
            costs=(0.0,),
            borrows=(0.0,),
            kill_lines=KILL_LINES,
            cluster_by=("event_date", "month"),
            primary_cost=0.0,
            short_borrow=0.0,
            z=1.96,
            peer_window=0,
        )
        rejected += abs(got["inference"]["t"]) > 1.96
        ci_rejected += got["primary"]["ci_low"] > 0 or got["primary"]["ci_high"] < 0
        values = [float(r["spread_60"]) for r in rows]  # type: ignore[arg-type]
        mean = sum(values) / len(values)
        se = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1) / len(values))
        naive += abs(mean / se) > 1.96
    assert 0.02 <= rejected / trials <= 0.08
    assert ci_rejected == rejected
    assert naive / trials > 0.30


def _lag_rows() -> list[dict[str, object]]:
    rows = _null_rows(random.Random(7))
    return [r for r in rows if int(r["entry_index"]) < 600]  # type: ignore[call-overload]


def _run(rows: list[dict[str, object]], **over: object) -> dict[str, object]:
    args: dict[str, object] = {
        "groups": ("early", "normal", "late"),
        "peer_group": "normal",
        "horizons": (60,),
        "costs": (30.0,),
        "borrows": (30.0,),
        "kill_lines": KILL_LINES,
        **REQUIRED,
    }
    args.update(over)
    return summarise(rows, {}, **args)  # type: ignore[arg-type]


def test_overlap_lag_includes_both_peer_windows() -> None:
    rows = _lag_rows()
    for pw in (0, 1, 2):
        got = _run(rows, peer_window=pw)
        values = [net_long(float(r["spread_60"]), 30.0) for r in rows]  # type: ignore[arg-type]
        want = overlap_result_interval(
            values,
            [int(r["entry_index"]) for r in rows],  # type: ignore[call-overload]
            max_lag_sessions=59 + 2 * pw,
            z=1.645,
        )
        assert got["inference"]["t"] == pytest.approx(want["t"])  # type: ignore[index]
    assert _run(rows, peer_window=0)["inference"]["t"] != pytest.approx(  # type: ignore[index]
        _run(rows, peer_window=1)["inference"]["t"]  # type: ignore[index]
    )


def test_kill_t_is_calibrated_on_the_net_series() -> None:
    rows = _lag_rows()
    got = _run(rows, peer_window=3)
    values = [net_long(float(r["spread_60"]), 30.0) for r in rows]  # type: ignore[arg-type]
    want = overlap_result_interval(
        values,
        [int(r["entry_index"]) for r in rows],  # type: ignore[call-overload]
        max_lag_sessions=59 + 6,
        z=1.645,
    )
    raw = got["kill"]["long"]["readings"]["raw"]  # type: ignore[index]
    assert raw["calibrated_t"] == pytest.approx(want["t"])
    assert raw["effect_bp"] == pytest.approx(want["effect"] * 10_000)
    for which in ("min", "event_date", "month", "quarter"):
        lines = {side: {**line, "which_t": which} for side, line in KILL_LINES.items()}
        details = _run(rows, peer_window=3, kill_lines=lines)["kill"]["long"]["readings"]["raw"]  # type: ignore[index]
        # the t the kill line compares is the calibrated one, not a clustered one
        assert details[f"t_{which}"] == pytest.approx(want["t"])


@pytest.mark.parametrize("missing", [*sorted(REQUIRED), "costs", "borrows", "kill_lines"])
def test_summarise_requires_every_input(missing: str) -> None:
    args: dict[str, object] = {
        "groups": ("early", "normal", "late"),
        "peer_group": "normal",
        "horizons": (60,),
        "costs": (30.0,),
        "borrows": (30.0,),
        "kill_lines": KILL_LINES,
        **REQUIRED,
    }
    del args[missing]
    with pytest.raises(TypeError):
        summarise([], {}, **args)  # type: ignore[arg-type]


def test_summary_empty_is_killed() -> None:
    got = summarise(
        [],
        {},
        groups=("early", "normal", "late"),
        peer_group="normal",
        horizons=(20, 60),
        costs=(15.0, 30.0),
        borrows=(0.0, 30.0, 300.0),
        kill_lines=KILL_LINES,
        **REQUIRED,
    )
    assert got["secondary"]["kill_long"] == 1.0
    assert math.isnan(got["primary"]["value"])


def test_c1b_synthetic_stage_a_every_registered_key() -> None:
    fixture = Path(__file__).resolve().parents[2] / "fixtures/research/c1b_stage_a/expected.json"
    case = json.loads(fixture.read_text())
    rows = assign_buckets(
        case["input"], bucket_by=("stratum", "quarter"), n_buckets=1, min_bucket_events=2
    )
    rows = attach_spreads(
        rows,
        horizons=(20, 60),
        treated_groups=("early", "late"),
        peer_group="normal",
        peer_by=("stratum",),
        peer_window=0,
        min_peers=1,
        near_k=10,
    )
    for actual, probe_row in zip(rows, case["expected_rows"], strict=True):
        for key in (
            "bucket",
            "no_peers",
            "beta_gap",
            "logmcap_gap",
            "peer_n_20",
            "peer_n_60",
            "spread_20",
            "spread_60",
            "spread_badj_60",
            "spread_sz_60",
            "spread_szb_60",
        ):
            if key in probe_row:
                assert actual[key] == pytest.approx(probe_row[key], rel=1e-9, abs=1e-12), key
            else:
                assert key not in actual
    got = summarise(
        rows,
        {"n_events_early": 12, "n_no_label": 2},
        groups=("early", "normal", "late"),
        peer_group="normal",
        horizons=(20, 60),
        costs=(15.0, 30.0),
        borrows=(0.0, 30.0, 300.0),
        kill_lines=KILL_LINES,
        **REQUIRED,
    )
    expected = case["expected"]
    assert got["secondary"].keys() == expected["secondary"].keys()
    for key, value in expected["secondary"].items():
        actual = got["secondary"][key]
        if isinstance(value, float) and math.isnan(value):
            assert math.isnan(actual), key
        elif isinstance(value, float):
            assert actual == pytest.approx(value, rel=1e-9, abs=1e-12), key
        else:
            assert actual == value, key
    for key in ("value", "n", "n_clusters"):
        assert got["primary"][key] == pytest.approx(expected["primary"][key], rel=1e-9), key
    long_rows = [row for row in rows if row["group"] == "early"]
    calibrated = overlap_result_interval(
        [net_long(row["spread_60"], 30.0) for row in long_rows],
        [row["entry_index"] for row in long_rows],
        max_lag_sessions=59,
        z=1.645,
    )
    assert got["primary"]["ci_low"] == pytest.approx(calibrated["ci_low"] * 10_000)
    assert got["primary"]["ci_high"] == pytest.approx(calibrated["ci_high"] * 10_000)
    assert got["inference"]["ci_low"] == pytest.approx(calibrated["ci_low"] * 10_000)
    assert got["inference"]["mde_80"] == pytest.approx(calibrated["mde_80"] * 10_000)
    assert got["kill"]["long"]["readings"]["raw"]["mde_80_bp"] > 0
    assert got["exploratory"]["h60"]["overlap"]["max_open"] > 1


def test_summarise_omitted_cost_row_does_not_crash() -> None:
    """The raw short reading is computed from the chosen settings, not looked up in the grid."""
    rows = _lag_rows()
    rows = rows + [{**r, "group": "late"} for r in rows]
    req = {**REQUIRED, "primary_cost": 15.0, "short_borrow": 15.0}
    in_grid = _run(rows, costs=(15.0,), borrows=(15.0,), **req)
    omitted = _run(rows, costs=(30.0,), borrows=(30.0,), **req)
    assert omitted["kill"] == in_grid["kill"]
    raw = omitted["kill"]["short"]["readings"]["raw"]  # type: ignore[index]
    assert math.isfinite(raw["net_bp"])


def test_summarise_fractional_costs_keep_distinct_keys() -> None:
    rows = _lag_rows()
    got = _run(rows, costs=(30.0, 30.9), borrows=(30.0,))
    sec = got["secondary"]
    assert isinstance(sec, dict)
    keys = [k for k in sec if k.startswith("short_") and "_c30" in k and k.endswith("_net_bp")]
    assert any("_c30_b30_" in k for k in keys) and any("_c30p9_b30_" in k for k in keys)
    assert sec["early_60_net30_bp"] != sec["early_60_net30p9_bp"]
