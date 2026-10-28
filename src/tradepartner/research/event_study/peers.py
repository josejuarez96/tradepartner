"""Pure size buckets and matched-peer returns."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Hashable, Mapping, Sequence
from typing import Any


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def assign_buckets(
    rows: Sequence[Mapping[str, Any]],
    *,
    bucket_by: Sequence[str],
    n_buckets: int,
    min_bucket_events: int,
) -> list[dict[str, Any]]:
    """Assign linear-quantile market-cap buckets within each declared cell."""
    if n_buckets < 1 or min_bucket_events < 1:
        raise ValueError("bucket counts must be positive")
    pools: dict[tuple[Hashable, ...], list[float]] = defaultdict(list)
    for row in rows:
        mcap = float(row["mcap"])
        if not math.isfinite(mcap) or mcap <= 0:
            raise ValueError("market cap must be finite and positive")
        pools[tuple(row[key] for key in bucket_by)].append(mcap)
    cuts = {
        cell: None
        if len(values) < min_bucket_events
        else [_quantile(sorted(values), i / n_buckets) for i in range(1, n_buckets)]
        for cell, values in pools.items()
    }
    result: list[dict[str, Any]] = []
    for row in rows:
        cell = tuple(row[key] for key in bucket_by)
        boundaries = cuts[cell]
        result.append(
            {
                **row,
                "bucket": None
                if boundaries is None
                else sum(float(row["mcap"]) > boundary for boundary in boundaries),
            }
        )
    return result


def select_peers(
    event: Mapping[str, Any],
    rows: Sequence[Mapping[str, Any]],
    *,
    peer_group: str,
    peer_by: Sequence[str],
    peer_window: int,
) -> list[Mapping[str, Any]]:
    """Find peers by bucket, declared cell, session distance and issuer identity."""
    if peer_window < 0:
        raise ValueError("peer_window must be non-negative")
    if event.get("bucket") is None:
        return []
    return [
        row
        for row in rows
        if row["group"] == peer_group
        and row.get("bucket") == event["bucket"]
        and all(row[key] == event[key] for key in peer_by)
        and abs(int(row["entry_index"]) - int(event["entry_index"])) <= peer_window
        and row["event_id"] != event["event_id"]
        and row.get("issuer_key", row.get("security_id"))
        != event.get("issuer_key", event.get("security_id"))
    ]


def attach_spreads(
    rows: Sequence[Mapping[str, Any]],
    *,
    horizons: Sequence[int],
    treated_groups: Sequence[str],
    peer_group: str,
    peer_by: Sequence[str],
    peer_window: int,
    min_peers: int,
    near_k: int,
) -> list[dict[str, Any]]:
    """Add four peer-relative readings without mutating the input event rows."""
    if min_peers < 1 or near_k < 1:
        raise ValueError("peer counts must be positive")
    result = [dict(row) for row in rows]
    for row in result:
        row["no_peers"] = False
        if row["group"] not in treated_groups or row.get("bucket") is None:
            continue
        peers = select_peers(
            row, result, peer_group=peer_group, peer_by=peer_by, peer_window=peer_window
        )
        if len(peers) < min_peers:
            row["no_peers"] = True
            continue
        logcap = math.log(float(row["mcap"]))
        near = sorted(
            peers,
            key=lambda peer: (abs(math.log(float(peer["mcap"])) - logcap), str(peer["event_id"])),
        )[:near_k]
        for horizon in horizons:
            for suffix, source, selected in (
                ("", "r", peers),
                ("badj_", "rb", peers),
                ("sz_", "r", near),
                ("szb_", "rb", near),
            ):
                key = f"{source}_{horizon}"
                row[f"spread_{suffix}{horizon}"] = float(row[key]) - sum(
                    float(peer[key]) for peer in selected
                ) / len(selected)
            row[f"peer_n_{horizon}"] = len(peers)
        row["beta_gap"] = float(row["beta"]) - sum(float(p["beta"]) for p in peers) / len(peers)
        row["logmcap_gap"] = logcap - sum(math.log(float(p["mcap"])) for p in peers) / len(peers)
    return result
