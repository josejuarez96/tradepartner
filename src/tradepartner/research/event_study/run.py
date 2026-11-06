"""Registered event-study driver and reproducible artifact."""

from __future__ import annotations

import json
import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

import polars as pl

from tradepartner.calendar import (
    all_sessions,
    is_session,
    next_session,
    previous_session,
    session_open,
)
from tradepartner.config import Settings, get_settings
from tradepartner.research import RunHandle, require_handle
from tradepartner.research.datafiles import data_dir
from tradepartner.research.event_study.peers import assign_buckets, attach_spreads
from tradepartner.research.event_study.reader import EventStudyReader
from tradepartner.research.event_study.stats import net_long, net_short, overlap_result_interval
from tradepartner.research.event_study.summary import evaluate_kill, summarise

ET = ZoneInfo("America/New_York")
PRE_MATCH_DROP_REASONS = (
    "no_entry_bar",
    "horizon",
    "price_floor",
    "liquidity",
    "no_mcap",
    "no_beta",
)
MATCH_DROP_REASONS = ("small_size_group", "no_peers")


@dataclass(frozen=True)
class StudyResult:
    """Result values for the registry writer and the saved event artifact."""

    primary: dict[str, Any]
    secondary: dict[str, Any]
    exploratory: dict[str, Any]
    artifact_path: Path
    artifact_sha256: str
    summary: dict[str, Any]


def entry_session(known_at: datetime, rule: str) -> date:
    """Choose the first tradable XNYS open under the registered entry rule."""
    offset = known_at.utcoffset()
    if offset is None:
        raise ValueError("known_at must be timezone-aware UTC")
    if offset.total_seconds() != 0:
        raise ValueError("known_at must be UTC")
    local_day = known_at.astimezone(ET).date()
    if rule == "next_session_after_date":
        return next_session(local_day)
    if rule == "next_open_after_instant":
        return (
            local_day
            if is_session(local_day) and known_at < session_open(local_day)
            else next_session(local_day)
        )
    raise ValueError(f"unknown entry rule {rule!r}")


def _events(
    events: Sequence[Mapping[str, Any]] | pl.DataFrame, run: RunHandle
) -> list[dict[str, Any]]:
    rows = events.to_dicts() if isinstance(events, pl.DataFrame) else [dict(row) for row in events]
    ids: set[str] = set()
    span = run.dataset.split_spans.get(run.split, (run.dataset.event_start, run.dataset.event_end))
    for row in rows:
        event_id = row.get("event_id")
        sid = row.get("security_id")
        t = row.get("known_at")
        if not isinstance(event_id, str) or not event_id or not isinstance(sid, str) or not sid:
            raise ValueError("event_id and security_id must be non-empty strings")
        if event_id in ids:
            raise ValueError(f"duplicate event_id {event_id!r}")
        ids.add(event_id)
        if not isinstance(t, datetime) or t.tzinfo is None:
            raise ValueError("known_at must be a timezone-aware UTC datetime")
        offset = t.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("known_at must be UTC")
        if not span[0] <= t.date() <= span[1]:
            raise ValueError(f"event {event_id!r} outside bound dataset span")
        for optional in ("group", "stratum", "issuer_key"):
            if optional in row and not isinstance(row[optional], str):
                raise ValueError(f"{optional} must be a string")
    return rows


def _bar_map(frame: pl.DataFrame) -> dict[str, dict[date, dict[str, Any]]]:
    bars: dict[str, dict[date, dict[str, Any]]] = defaultdict(dict)
    for row in frame.to_dicts():
        bars[str(row["security_id"])][row["session"]] = row
    return bars


def _beta(
    sid: str,
    entry_index: int,
    sessions: Sequence[date],
    adjusted: Mapping[str, Mapping[date, Mapping[str, Any]]],
    benchmark: str,
    lookback: int,
    minimum: int,
) -> float | None:
    pairs: list[tuple[float, float]] = []
    security_bars, benchmark_bars = adjusted.get(sid, {}), adjusted.get(benchmark, {})
    for index in range(max(1, entry_index - lookback), entry_index):
        a, b = sessions[index - 1], sessions[index]
        if not all(day in frame for day in (a, b) for frame in (security_bars, benchmark_bars)):
            continue
        x = float(benchmark_bars[b]["close"]) / float(benchmark_bars[a]["close"]) - 1
        y = float(security_bars[b]["close"]) / float(security_bars[a]["close"]) - 1
        pairs.append((x, y))
    if len(pairs) < minimum:
        return None
    xbar = sum(x for x, _ in pairs) / len(pairs)
    ybar = sum(y for _, y in pairs) / len(pairs)
    variance = sum((x - xbar) ** 2 for x, _ in pairs)
    return sum((x - xbar) * (y - ybar) for x, y in pairs) / variance if variance > 0 else None


def _json_bytes(value: object) -> bytes:
    def default(item: object) -> str:
        if isinstance(item, (date, datetime)):
            return item.isoformat()
        raise TypeError(f"not JSON serializable: {type(item).__name__}")

    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=True, default=default)
        + "\n"
    ).encode()


def match_events(
    event_rows: Sequence[dict[str, Any]], design: Mapping[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Size buckets and matched peers over built event rows (pure).

    Returns the rows with their buckets and peer spreads, and the counts this
    stage derives (`n_dropped_small_size_group_*`, `n_dropped_no_peers_*`,
    `n_traded_*`), which the caller adds to the earlier stages' counts.
    """
    derived: dict[str, int] = defaultdict(int)
    # Default cuts and peer pools are re-evaluated at the treated event's entry.
    # A declared ex-post option reproduces the historical C1b probe.
    matching = design.get("matching", "entry_known")
    if matching not in {"entry_known", "ex_post"}:
        raise ValueError("unknown matching mode")
    bucketed = (
        assign_buckets(
            event_rows,
            bucket_by=design["bucket_by"],
            n_buckets=int(design["n_buckets"]),
            min_bucket_events=int(design["min_bucket_events"]),
        )
        if matching == "ex_post"
        else []
    )
    by_bucket_cell: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in event_rows:
        by_bucket_cell[tuple(row[key] for key in design["bucket_by"])].append(row)
    bucket_cache: dict[tuple[int, tuple[Any, ...]], list[dict[str, Any]]] = {}
    peer_index: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    peer_indices: dict[tuple[Any, ...], list[int]] = {}
    if matching == "ex_post":
        for row in bucketed:
            if row["group"] == design["peer_group"] and row["bucket"] is not None:
                peer_index[(*tuple(row[key] for key in design["peer_by"]), row["bucket"])].append(
                    row
                )
        for key, candidates in peer_index.items():
            candidates.sort(key=lambda row: row["entry_index"])
            peer_indices[key] = [int(row["entry_index"]) for row in candidates]
    processed: list[dict[str, Any]] = []
    for event in bucketed if matching == "ex_post" else event_rows:
        if matching == "entry_known":
            cell = tuple(event[key] for key in design["bucket_by"])
            cache_key = (int(event["entry_index"]), cell)
            if cache_key not in bucket_cache:
                entry_instant = session_open(date.fromisoformat(str(event["entry"])))
                visible = [
                    row
                    for row in by_bucket_cell[cell]
                    if row["entry_index"] <= event["entry_index"]
                    and row["known_at"] <= entry_instant
                ]
                bucket_cache[cache_key] = assign_buckets(
                    visible,
                    bucket_by=design["bucket_by"],
                    n_buckets=int(design["n_buckets"]),
                    min_bucket_events=int(design["min_bucket_events"]),
                )
            pool = bucket_cache[cache_key]
            event = next(row for row in pool if row["event_id"] == event["event_id"])
        else:
            pool = peer_index.get(
                (*tuple(event[key] for key in design["peer_by"]), event["bucket"]), []
            )
        group = str(event["group"])
        if event["bucket"] is None:
            derived[f"n_dropped_small_size_group_{group}"] += 1
            processed.append(event)
            continue
        if group == design["peer_group"]:
            derived[f"n_traded_{group}"] += 1
            processed.append(event)
            continue
        if matching == "ex_post":
            indices = peer_indices.get(
                (*tuple(event[key] for key in design["peer_by"]), event["bucket"]), []
            )
            low = bisect_left(indices, int(event["entry_index"]) - int(design["peer_window"]))
            high = bisect_right(indices, int(event["entry_index"]) + int(design["peer_window"]))
            candidates = pool[low:high]
        else:
            candidates = [
                row
                for row in pool
                if row["group"] == design["peer_group"]
                and row["bucket"] == event["bucket"]
                and all(row[key] == event[key] for key in design["peer_by"])
                and int(event["entry_index"]) - int(row["entry_index"])
                <= int(design["peer_window"])
            ]
        spread = attach_spreads(
            [event, *candidates],
            horizons=design["horizons"],
            treated_groups=[group],
            peer_group=str(design["peer_group"]),
            peer_by=design["peer_by"],
            peer_window=int(design["peer_window"]),
            min_peers=int(design["min_peers"]),
            near_k=int(design["near_k"]),
        )[0]
        if spread["no_peers"]:
            derived[f"n_dropped_no_peers_{group}"] += 1
        else:
            derived[f"n_traded_{group}"] += 1
        processed.append(spread)
    return processed, dict(derived)


def count_study_events(
    event_rows: Sequence[Mapping[str, Any]],
    design: Mapping[str, Any],
    dropped: Mapping[str, int],
    derived: Mapping[str, int],
) -> dict[str, int]:
    """Count built events, earlier drops, traded groups, and early exits (pure).

    An event with several early horizons contributes one delisted exit. Earlier
    drops count toward their exact group; matching drops are already built rows.
    """
    counts: dict[str, int] = {}
    for group in design["groups"]:
        counts[f"n_events_{group}"] = sum(row["group"] == group for row in event_rows) + sum(
            dropped.get(f"n_dropped_{reason}_{group}", 0) for reason in PRE_MATCH_DROP_REASONS
        )
        counts[f"n_traded_{group}"] = derived.get(f"n_traded_{group}", 0)
    counts["n_delisted_exits"] = sum(
        any(row.get(f"exited_early_{int(h)}", False) for h in design["horizons"])
        for row in event_rows
    )
    return counts


def summarise_study(
    processed: Sequence[Mapping[str, Any]], counts: Mapping[str, Any], design: Mapping[str, Any]
) -> dict[str, Any]:
    """The study summary and the declared kill lines over matched rows (pure).

    Its `secondary` is what `experiment study` records as `secondary_json`;
    the C1b golden calls it with run 3's event rows and recorded counts.
    """
    groups = list(design["groups"])
    kills = {
        str(line["name"]): {
            "readings": [
                dict(
                    raw="raw", beta_adjusted="badj", size_tight="sz", size_tight_beta_adjusted="szb"
                )[name]
                for name in line["readings"]
            ],
            "metric": line["metric"],
            "floor_bp": line["floor"],
            "t_floor": line["t_floor"],
            "which_t": line["t"],
        }
        for line in design["kill"]
    }
    by_side = {str(line["side"]): kills[str(line["name"])] for line in design["kill"]}
    fallback = {
        "readings": ["raw"],
        "metric": "net_bp",
        "floor_bp": 0.0,
        "t_floor": 0.0,
        "which_t": "min",
    }
    long_line = next((line for line in design["kill"] if line["side"] == "long"), None)
    short_line = next((line for line in design["kill"] if line["side"] == "short"), None)
    primary_cost = float(long_line["cost_bps"]) if long_line else float(design["cost_bps"][-1])
    short_borrow = float(short_line["borrow_bps"]) if short_line else float(design["borrow_bps"][0])
    summary = summarise(
        processed,
        counts,
        groups=groups,
        peer_group=str(design["peer_group"]),
        horizons=design["horizons"],
        costs=design["cost_bps"],
        borrows=design["borrow_bps"],
        cluster_by=design["cluster_by"],
        kill_lines={"long": by_side.get("long", fallback), "short": by_side.get("short", fallback)},
        primary_cost=primary_cost,
        short_borrow=short_borrow,
        z=float(design["z"]),
        peer_window=int(design["peer_window"]),
    )
    reading_key = {
        "raw": "spread",
        "beta_adjusted": "spread_badj",
        "size_tight": "spread_sz",
        "size_tight_beta_adjusted": "spread_szb",
    }
    for line in design["kill"]:
        group = str(line["group"])
        horizon = int(line["horizon"])
        side = str(line["side"])
        cost = float(line["cost_bps"])
        borrow = float(line["borrow_bps"])
        selected = [
            row for row in processed if row["group"] == group and f"spread_{horizon}" in row
        ]
        readings: dict[str, dict[str, float]] = {}
        for reading in line["readings"]:
            metric_key = f"{reading_key[reading]}_{horizon}"
            gross = [float(row[metric_key]) * (-1 if side == "short" else 1) for row in selected]
            net = [
                net_short(float(row[metric_key]), cost, borrow, horizon)
                if side == "short"
                else net_long(float(row[metric_key]), cost)
                for row in selected
            ]
            t_values = gross if line["t_metric"] == "gross_spread_bp" else net
            interval = overlap_result_interval(
                t_values,
                [int(row["entry_index"]) for row in selected],
                max_lag_sessions=horizon - 1 + 2 * int(design["peer_window"]),
                z=float(design["z"]),
            )
            gross_bp = sum(gross) / len(gross) * 10_000 if gross else math.nan
            net_bp = sum(net) / len(net) * 10_000 if net else math.nan
            readings[str(reading)] = {
                "gross_spread_bp": gross_bp,
                "net_bp": net_bp,
                **{f"t_{kind}": interval["t"] for kind in (*design["cluster_by"], "min")},
            }
        result = evaluate_kill(
            {
                "readings": line["readings"],
                "metric": line["metric"],
                "floor_bp": line["floor"],
                "t_floor": line["t_floor"],
                "which_t": line["t"],
            },
            readings,
        )
        summary["kill"][str(line["name"])] = result
        summary["secondary"][f"kill_{line['name']}"] = float(result["killed"])
    return summary


def run_study(
    handle: object,
    study: Mapping[str, Any] | Any,
    events: Sequence[Mapping[str, Any]] | pl.DataFrame,
    *,
    settings: Settings | None = None,
) -> StudyResult:
    """Run a study within the bound dataset and registered experiment window.

    ``study`` is the parsed experiment, which carries both its frozen ``study``
    table and registered window. This avoids a second registry-table reader.
    """
    run = require_handle(handle)
    design: Mapping[str, Any] = study.study if hasattr(study, "study") else study
    if not isinstance(design, Mapping):
        raise ValueError("study must be a parsed [study] table")
    n_config = len(design.get("kill", ()))
    if (
        n_config > int(design.get("configurations", 0))
        or n_config > run.n_configurations_declared
        or int(design.get("configurations", 0)) > run.n_configurations_declared
    ):
        raise ValueError("evaluated configurations exceed declared configurations")
    rows = _events(events, run)
    start = getattr(study, "window_start", None)
    end = getattr(study, "window_end", None)
    if not isinstance(start, date) or not isinstance(end, date) or end < start:
        raise ValueError("registered study window is required")
    if not isinstance(study, Mapping) and getattr(study, "slug", run.slug) != run.slug:
        raise ValueError("study slug differs from run")
    cfg = settings or get_settings()
    if run.database is not None and str(Path(cfg.store.path).resolve()) != str(
        Path(run.database).resolve()
    ):
        raise ValueError("run belongs to another store")
    reader = EventStudyReader(cfg, end)
    sessions = [day for day in all_sessions() if start <= day <= end]
    if not sessions:
        raise ValueError("registered window has no XNYS sessions")
    max_history = max(int(design["volume_lookback"]), int(design["beta_lookback"])) + 2
    first_index = all_sessions().index(sessions[0])
    history = all_sessions()[max(0, first_index - max_history) :]
    sid_benchmark = reader.benchmark(str(design["benchmark_security"]), start=history[0])
    security_ids = sorted({str(row["security_id"]) for row in rows} | {sid_benchmark})
    adjusted_frame, raw_frame = reader.bars(security_ids, sessions_from=history[0])
    adjusted, raw = _bar_map(adjusted_frame), _bar_map(raw_frame)
    session_index = {day: index for index, day in enumerate(history)}
    counts: dict[str, int] = {
        "n_events_total": len(rows),
        "n_no_label": 0,
        "n_delisted_exits": 0,
        "n_securities": len({row["security_id"] for row in rows}),
    }
    groups = list(design["groups"])
    for group in groups:
        counts[f"n_events_{group}"] = 0
        counts[f"n_traded_{group}"] = 0
        for reason in (*PRE_MATCH_DROP_REASONS, *MATCH_DROP_REASONS):
            counts[f"n_dropped_{reason}_{group}"] = 0
    event_rows: list[dict[str, Any]] = []

    def drop(group: str, reason: str) -> None:
        counts[f"n_dropped_{reason}_{group}"] += 1

    for event in rows:
        group = event.get("group", groups[0])
        if not group:
            counts["n_no_label"] += 1
            continue
        if group not in groups:
            raise ValueError(f"unknown study group {group!r}")
        sid = str(event["security_id"])
        known_at = event["known_at"]
        entry = entry_session(known_at, str(design["entry"]))
        if entry not in session_index or entry < start or entry > end:
            drop(group, "horizon")
            continue
        entry_i = session_index[entry]
        if entry not in adjusted.get(sid, {}):
            drop(group, "no_entry_bar")
            continue
        if any(
            entry_i + int(h) - 1 >= len(history) or history[entry_i + int(h) - 1] > end
            for h in design["horizons"]
        ):
            drop(group, "horizon")
            continue
        prior = history[entry_i - 1] if entry_i else previous_session(entry)
        prior_raw = raw.get(sid, {}).get(prior)
        if prior_raw is None or float(prior_raw["close"]) < float(design["price_floor"]):
            drop(group, "price_floor")
            continue
        volume_bars = [
            raw[sid][day]
            for day in history[max(0, entry_i - int(design["volume_lookback"])) : entry_i]
            if day in raw.get(sid, {})
        ]
        if len(volume_bars) < int(design["min_volume_bars"]) or median(
            float(bar["close"]) * float(bar["volume"]) for bar in volume_bars
        ) < float(design["volume_floor"]):
            drop(group, "liquidity")
            continue
        facts = reader.shares(
            sid,
            known_at,
            fact_name=str(design["shares_fact"]),
            facts_read=str(design["facts_read"]),
        )
        eligible: list[dict[str, object]] = []
        for fact in facts:
            fact_date = fact.get("as_of_date")
            value = fact.get("value")
            if (
                isinstance(fact_date, date)
                and isinstance(value, (int, float))
                and 0 <= (entry - fact_date).days <= int(design["shares_stale_days"])
                and value > 0
            ):
                eligible.append(fact)
        if not eligible:
            drop(group, "no_mcap")
            continue
        mcap = float(str(eligible[-1]["value"])) * float(prior_raw["close"])
        beta = _beta(
            sid,
            entry_i,
            history,
            adjusted,
            sid_benchmark,
            int(design["beta_lookback"]),
            int(design["beta_min_bars"]),
        )
        if beta is None:
            drop(group, "no_beta")
            continue
        local = known_at.astimezone(ET)
        quarter = local.year * 4 + (local.month - 1) // 3
        built: dict[str, Any] = {
            **event,
            "group": group,
            "stratum": event.get("stratum", ""),
            "issuer_key": event.get("issuer_key", sid),
            "entry": entry.isoformat(),
            "entry_index": entry_i,
            "acc_date": local.date().isoformat(),
            "quarter": quarter,
            "qidx": quarter,
            "mcap": mcap,
            "beta": beta,
        }
        for h in design["horizons"]:
            horizon = int(h)
            exit_day = history[entry_i + horizon - 1]
            bar_days = [
                day for day in history[entry_i : entry_i + horizon] if day in adjusted.get(sid, {})
            ]
            last_day = bar_days[-1]
            early = last_day != exit_day
            built[f"exited_early_{horizon}"] = early
            built[f"last_bar_{horizon}"] = last_day.isoformat()
            built[f"scheduled_exit_{horizon}"] = exit_day.isoformat()
            entry_open = float(adjusted[sid][entry]["open"])
            # Carry the last stock close through the scheduled horizon. The
            # benchmark continues over that same horizon, including a gap
            # after the stock's last bar.
            ret = float(adjusted[sid][last_day]["close"]) / entry_open - 1
            benchmark_open = float(adjusted[sid_benchmark][entry]["open"])
            benchmark_return = (
                float(adjusted[sid_benchmark][exit_day]["close"]) / benchmark_open - 1
            )
            built[f"r_{horizon}"] = ret
            built[f"spy_{horizon}"] = benchmark_return
            built[f"rb_{horizon}"] = ret - beta * benchmark_return
        event_rows.append(built)

    processed, derived = match_events(event_rows, design)
    for key, value in derived.items():
        if not key.startswith("n_traded_"):
            counts[key] += value
    counts.update(count_study_events(event_rows, design, counts, derived))
    summary = summarise_study(processed, counts, design)
    matching = design.get("matching", "entry_known")
    summary["exploratory"]["matching"] = matching
    summary["exploratory"]["delisted_exit_note"] = (
        "Last available close carried forward; no delisting return. "
        "This can flatter delistings for cause."
    )
    summary["exploratory"]["peer_cost_note"] = "Matched peer leg assumed costless to trade."
    summary["exploratory"]["small_bucket_note"] = (
        "Entry-known cuts can leave early quarter cells below min_bucket_events; "
        "these events are counted as small_size_group and remain untraded."
    )
    artifact = {
        "meta": {
            "run_id": run.run_id,
            "slug": run.slug,
            "max_read_t": reader.max_read_t,
            "matching": matching,
        },
        "events": processed,
        "summary": summary,
    }
    payload = _json_bytes(artifact)
    digest = sha256(payload).hexdigest()
    path = data_dir(cfg) / run.slug / f"{run.slug}-run{run.run_id}-{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError("artifact digest collision") from None
    return StudyResult(
        summary["primary"],
        summary["secondary"],
        summary["exploratory"],
        path,
        digest,
        summary,
    )
