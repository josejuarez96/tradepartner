"""Pure C1b-compatible event-study summaries and declarative kill lines."""

from __future__ import annotations

import math
from collections.abc import Hashable, Mapping, Sequence
from typing import Any

from tradepartner.research.event_study.stats import (
    cluster_stats,
    hold_overlap_sessions,
    kish_clusters,
    net_long,
    net_short,
    overlap_result_interval,
)

BPS = 10_000


def _mean_bp(values: Sequence[float]) -> float:
    return sum(values) / len(values) * BPS if values else math.nan


def _cluster_key(row: Mapping[str, Any], kind: str) -> Hashable:
    if kind == "month":
        return str(row["acc_date"])[:7]
    if kind == "quarter":
        return str(row["qidx"])
    if kind == "event_date":
        return str(row["acc_date"])[:10]
    raise ValueError(f"unknown cluster kind: {kind}")


def _ts(
    values: Sequence[float], rows: Sequence[Mapping[str, Any]], kinds: Sequence[str]
) -> dict[str, float]:
    ts = {
        kind: cluster_stats(values, [_cluster_key(row, kind) for row in rows])["t"]
        for kind in kinds
    }
    ts["min"] = min(ts.values()) if all(math.isfinite(t) for t in ts.values()) else math.nan
    return ts


def evaluate_kill(
    line: Mapping[str, Any], readings: Mapping[str, Mapping[str, float]]
) -> dict[str, Any]:
    """Kill if any declared reading is absent, non-finite or below either floor."""
    selected = tuple(str(name) for name in line["readings"])
    metric = str(line["metric"])
    which_t = str(line["which_t"])
    if metric not in {"gross_spread_bp", "net_bp"}:
        raise ValueError(f"unknown kill metric: {metric}")
    if which_t not in {"min", "event_date", "month", "quarter"}:
        raise ValueError(f"unknown kill t: {which_t}")
    t_key = f"t_{which_t}"
    floor = float(line["floor_bp"])
    t_floor = float(line["t_floor"])
    details: dict[str, dict[str, float]] = {}
    killed = not selected
    for name in selected:
        result = readings.get(name, {})
        effect, t = float(result.get(metric, math.nan)), float(result.get(t_key, math.nan))
        details[name] = {metric: effect, t_key: t}
        for key in ("effect_bp", "ci_low_bp", "ci_high_bp", "mde_80_bp", "calibrated_t"):
            if key in result:
                details[name][key] = float(result[key])
        if not math.isfinite(effect) or not math.isfinite(t) or effect < floor or t < t_floor:
            killed = True
    return {"killed": killed, "readings": details}


def summarise(
    rows: Sequence[Mapping[str, Any]],
    counts: Mapping[str, Any],
    *,
    groups: Sequence[str],
    peer_group: str,
    horizons: Sequence[int],
    costs: Sequence[float],
    borrows: Sequence[float],
    cluster_by: Sequence[str],
    kill_lines: Mapping[str, Mapping[str, Any]],
    primary_cost: float,
    short_borrow: float,
    z: float,
    peer_window: int,
) -> dict[str, Any]:
    """Build C1b secondary keys by declared group and add calibrated inference.

    The first treated group is the long thesis, and the last treated group is
    the short thesis. Costs, borrow, ``z``, ``cluster_by`` and ``peer_window``
    are required: no C1b defaults. The secondary ``t_*`` keys stay the C1b
    clustered descriptives. The primary interval, ``inference`` and every
    kill-line t use the overlap-calibrated SE with lag ``h - 1 + 2 *
    peer_window`` (peers enter up to ``peer_window`` sessions from the event,
    so two spreads share bars at entry distance up to that lag), computed on the
    same net series the kill line judges.
    """
    treated = [group for group in groups if group != peer_group]
    if len(treated) != 2 or not horizons or not cluster_by:
        raise ValueError("summary requires two treated groups, horizons and cluster choices")
    if peer_window < 0:
        raise ValueError("peer_window must be non-negative")
    long_group, short_group = treated
    main_h = max(horizons)
    lag = main_h - 1 + 2 * peer_window
    sec: dict[str, Any] = {}

    def sel(group: str, horizon: int) -> list[Mapping[str, Any]]:
        return [row for row in rows if row["group"] == group and f"spread_{horizon}" in row]

    for group in treated:
        for h in horizons:
            selected = sel(group, h)
            sec[f"{group}_{h}_n"] = len(selected)
            sec[f"{group}_{h}_spread_gross_bp"] = _mean_bp(
                [float(row[f"spread_{h}"]) for row in selected]
            )
            sec[f"{group}_{h}_raw_bp"] = _mean_bp([float(row[f"r_{h}"]) for row in selected])
            sec[f"{group}_{h}_spy_adj_bp"] = _mean_bp(
                [float(row[f"r_{h}"]) - float(row[f"spy_{h}"]) for row in selected]
            )
        selected = sel(group, main_h)
        sec[f"{group}_{main_h}_badj_gross_bp"] = _mean_bp(
            [float(row[f"spread_badj_{main_h}"]) for row in selected]
        )
        sec[f"{group}_{main_h}_beta_gap"] = (
            sum(float(row["beta_gap"]) for row in selected) / len(selected)
            if selected
            else math.nan
        )
        sec[f"{group}_{main_h}_logmcap_gap"] = (
            sum(float(row["logmcap_gap"]) for row in selected) / len(selected)
            if selected
            else math.nan
        )
    for h in horizons:
        longs, shorts = sel(long_group, h), sel(short_group, h)
        for cost in costs:
            sec[f"{long_group}_{h}_net{int(cost)}_bp"] = _mean_bp(
                [net_long(float(row[f"spread_{h}"]), cost) for row in longs]
            )
            for borrow in borrows:
                sec[f"short_{short_group}_{h}_c{int(cost)}_b{int(borrow)}_net_bp"] = _mean_bp(
                    [net_short(float(row[f"spread_{h}"]), cost, borrow, h) for row in shorts]
                )
        for label, selected, values in (
            (
                f"{long_group}_{h}",
                longs,
                [net_long(float(row[f"spread_{h}"]), primary_cost) for row in longs],
            ),
            (
                f"short_{short_group}_{h}",
                shorts,
                [
                    net_short(float(row[f"spread_{h}"]), primary_cost, short_borrow, h)
                    for row in shorts
                ],
            ),
        ):
            for kind, t in _ts(values, selected, cluster_by).items():
                sec[f"{label}_t_{kind}"] = t
    variants = (("badj", "spread_badj"), ("sz", "spread_sz"), ("szb", "spread_szb"))
    long_rows, short_rows = sel(long_group, main_h), sel(short_group, main_h)
    for name, key in variants:
        for group, selected in ((long_group, long_rows), (short_group, short_rows)):
            if name != "badj":
                sec[f"{group}_{main_h}_{name}_gross_bp"] = _mean_bp(
                    [float(row[f"{key}_{main_h}"]) for row in selected]
                )
        sec[f"{long_group}_{main_h}_{name}_t_min"] = _ts(
            [net_long(float(row[f"{key}_{main_h}"]), primary_cost) for row in long_rows],
            long_rows,
            cluster_by,
        )["min"]
        net_key = (
            f"short_{short_group}_{main_h}_{name}_c{int(primary_cost)}_b{int(short_borrow)}_net_bp"
        )
        sec[net_key] = _mean_bp(
            [
                net_short(float(row[f"{key}_{main_h}"]), primary_cost, short_borrow, main_h)
                for row in short_rows
            ]
        )
        sec[f"short_{short_group}_{main_h}_{name}_t_min"] = _ts(
            [
                net_short(float(row[f"{key}_{main_h}"]), primary_cost, short_borrow, main_h)
                for row in short_rows
            ],
            short_rows,
            cluster_by,
        )["min"]
    reading_names = ("raw", "badj", "sz", "szb")
    long_readings: dict[str, dict[str, float]] = {}
    short_readings: dict[str, dict[str, float]] = {}
    for name in reading_names:
        key = "" if name == "raw" else f"_{name}"
        gross_key = (
            f"{long_group}_{main_h}_spread_gross_bp"
            if name == "raw"
            else f"{long_group}_{main_h}_{name}_gross_bp"
        )
        long_readings[name] = {
            "gross_spread_bp": float(sec[gross_key]),
        }
        net_key = (
            f"short_{short_group}_{main_h}_c{int(primary_cost)}_b{int(short_borrow)}_net_bp"
            if name == "raw"
            else (
                f"short_{short_group}_{main_h}_{name}_c{int(primary_cost)}"
                f"_b{int(short_borrow)}_net_bp"
            )
        )
        short_readings[name] = {
            "net_bp": float(sec[net_key]),
        }
    for name in reading_names:
        source = "spread" if name == "raw" else f"spread_{name}"
        for selected, reading, is_short in (
            (long_rows, long_readings[name], False),
            (short_rows, short_readings[name], True),
        ):
            raw_values = [float(row[f"{source}_{main_h}"]) for row in selected]
            net = (
                [net_short(value, primary_cost, short_borrow, main_h) for value in raw_values]
                if is_short
                else [net_long(value, primary_cost) for value in raw_values]
            )
            interval = overlap_result_interval(
                net,
                [int(row["entry_index"]) for row in selected],
                max_lag_sessions=lag,
                z=z,
            )
            for field, target in (
                ("effect", "effect_bp"),
                ("ci_low", "ci_low_bp"),
                ("ci_high", "ci_high_bp"),
                ("mde_80", "mde_80_bp"),
            ):
                reading[target] = interval[field] * BPS
            reading["calibrated_t"] = interval["t"]
            for kind in ("min", "event_date", "month", "quarter"):
                reading[f"t_{kind}"] = interval["t"]
    long_kill = evaluate_kill(kill_lines["long"], long_readings)
    short_kill = evaluate_kill(kill_lines["short"], short_readings)
    sec["kill_long"] = float(long_kill["killed"])
    sec["kill_short"] = float(short_kill["killed"])
    sec.update(counts)
    values = [net_long(float(row[f"spread_{main_h}"]), primary_cost) for row in long_rows]
    first_clusters = [_cluster_key(row, cluster_by[0]) for row in long_rows]
    primary_stats = cluster_stats(values, first_clusters)
    primary = {
        "value": primary_stats["mean"] * BPS,
        "ci_low": 0.0,
        "ci_high": 0.0,
        "n": len(values),
        "n_clusters": int(primary_stats["clusters"]),
    }
    inference = overlap_result_interval(
        values,
        [int(row["entry_index"]) for row in long_rows],
        max_lag_sessions=lag,
        z=z,
    )
    primary["ci_low"] = inference["ci_low"] * BPS
    primary["ci_high"] = inference["ci_high"] * BPS
    for key in ("effect", "ci_low", "ci_high", "mde_80"):
        inference[key] *= BPS
    inference["kish_clusters"] = kish_clusters(first_clusters)
    exploratory: dict[str, Any] = {}
    for h in horizons:
        selected = sel(long_group, h) + sel(short_group, h)
        exploratory[f"h{h}"] = {
            "n": len(selected),
            "clusters": {
                kind: len({_cluster_key(row, kind) for row in selected}) for kind in cluster_by
            },
            "kish_clusters": {
                kind: kish_clusters([_cluster_key(row, kind) for row in selected])
                for kind in cluster_by
            },
            "overlap": hold_overlap_sessions(
                [
                    (
                        str(row["security_id"]),
                        int(row["entry_index"]),
                        int(row["entry_index"]) + h - 1,
                    )
                    for row in selected
                ]
            ),
        }
    return {
        "primary": primary,
        "secondary": sec,
        "kill": {"long": long_kill, "short": short_kill},
        "inference": inference,
        "exploratory": exploratory,
    }
