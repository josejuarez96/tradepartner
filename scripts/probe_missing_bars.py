"""Owner-run probe for #106 (protocol "Probe 2"): missing-bar share on SIP daily bars.

    uv run python scripts/probe_missing_bars.py fetch             # needs ALPACA keys in .env
    uv run python scripts/probe_missing_bars.py live <run_dir>    # once, >= close + settle delay
    uv run python scripts/probe_missing_bars.py analyze <run_dir> # offline, re-runnable

`fetch` lists active, tradable NYSE / Nasdaq / NYSE American equities once
(`get_all_assets`, option (b) of the protocol), then pulls SIP daily bars for
the 20 most recent sessions ending 5 sessions before today, 200 symbols per
call. `live` counts the latest completed session at the time ingest would run
(session close + `ingest.settle_delay_minutes`). Raw payloads are saved outside
the repo; nothing is written to the store and no order is placed.

Zero-volume placeholder bars (v=0, n=0; Alpaca emits them after a delisting,
#104) count as missing. Spike code (#106), never merged.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tradepartner import calendar

REPO_ROOT = Path(__file__).resolve().parents[1]
NEW_YORK = ZoneInfo("America/New_York")
EXCHANGES = frozenset({"NYSE", "NASDAQ", "AMEX"})  # AMEX is Alpaca's name for NYSE American
BATCH = 200
WINDOW_SESSIONS = 20
WINDOW_GAP = 5
STEP = 0.005
FREE_PLAN_DELAY = timedelta(minutes=16)  # SIP queries must end >= 15 min in the past


def bar_session(bar: Mapping[str, Any]) -> date:
    """Session date of a daily bar (Alpaca stamps it at midnight New York time)."""
    t = datetime.fromisoformat(str(bar["t"]).replace("Z", "+00:00"))
    return t.astimezone(NEW_YORK).date()


def is_placeholder(bar: Mapping[str, Any]) -> bool:
    """A zero-volume, zero-trade bar: a placeholder, not a traded session (#104)."""
    return bar.get("v") == 0 and bar.get("n") == 0


def present_sessions(bars: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict[str, set[date]]:
    """Sessions with a real (non-placeholder) bar, per symbol."""
    return {
        sym: {bar_session(b) for b in rows if not is_placeholder(b)} for sym, rows in bars.items()
    }


def listed_symbols(assets: Iterable[Mapping[str, Any]]) -> list[str]:
    """Active, tradable US equities on NYSE, Nasdaq or NYSE American, sorted."""
    return sorted(
        a["symbol"]
        for a in assets
        if a.get("status") == "active"
        and a.get("tradable") is True
        and a.get("class") == "us_equity"
        and a.get("exchange") in EXCHANGES
    )


def window_sessions(today: date, n: int = WINDOW_SESSIONS, gap: int = WINDOW_GAP) -> list[date]:
    """The `n` XNYS sessions ending `gap` sessions before `today`, oldest first."""
    end = today
    for _ in range(gap):
        end = calendar.previous_session(end)
    out = [end]
    while len(out) < n:
        out.append(calendar.previous_session(out[-1]))
    return sorted(out)


_KINDS = [
    ("unit", re.compile(r"\bunits?\b", re.I)),
    ("warrant", re.compile(r"\bwarrants?\b", re.I)),
    ("right", re.compile(r"\brights?\b", re.I)),
    ("preferred", re.compile(r"\bpreferred\b", re.I)),
    ("notes", re.compile(r"\bnotes?\b|\bdebentures?\b", re.I)),
]


def instrument_kind(name: str) -> str:
    """Rough instrument type from the asset name; "common-like" when none matches.

    Name keywords only (Alpaca's asset has no security-type field). ETFs, trusts
    and ADRs stay common-like; the real universe filter is T13's job.
    """
    for kind, pattern in _KINDS:
        if pattern.search(name):
            return kind
    return "common-like"


def all_rows(bars: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict[str, set[date]]:
    """Sessions with any bar row, placeholders included, per symbol."""
    return {sym: {bar_session(b) for b in rows} for sym, rows in bars.items()}


@dataclass(frozen=True)
class SessionRow:
    session: date
    expected: int
    missing: int

    @property
    def share(self) -> float:
        return self.missing / self.expected if self.expected else 0.0


@dataclass(frozen=True)
class MissingStats:
    rows: list[SessionRow]
    never_traded: list[str]
    probable_ipo: list[str]
    top_missing: list[tuple[str, int]]

    @property
    def median_share(self) -> float:
        return statistics.median(r.share for r in self.rows) if self.rows else 0.0

    @property
    def max_share(self) -> float:
        return max((r.share for r in self.rows), default=0.0)

    @property
    def max_session(self) -> date | None:
        return max(self.rows, key=lambda r: r.share).session if self.rows else None


def missing_stats(
    listed: Iterable[str],
    present: Mapping[str, set[date]],
    window: list[date],
    top: int = 10,
    *,
    first_seen: Mapping[str, set[date]] | None = None,
) -> MissingStats:
    """The protocol's E_s / M_s count over `window`.

    A symbol is expected from its first row in the window: `first_seen` (any
    row, placeholders included) when given, else `present`. A symbol with no
    such row is "never traded" and leaves E; one whose first row is after the
    window start is a probable IPO. Without `first_seen`, a listed name that
    simply did not trade on the first session reads as an IPO (run 1 of #106).
    """
    in_window = set(window)
    expected = Counter[date]()
    missing = Counter[date]()
    per_symbol = Counter[str]()
    never, ipo = [], []
    for sym in sorted(set(listed)):
        days = present.get(sym, set()) & in_window
        seen = (first_seen.get(sym, set()) if first_seen is not None else days) & in_window
        if not seen:
            never.append(sym)
            continue
        first = min(seen)
        if first > window[0]:
            ipo.append(sym)
        for s in window:
            if s < first:
                continue
            expected[s] += 1
            if s not in days:
                missing[s] += 1
                per_symbol[sym] += 1
    rows = [SessionRow(s, expected[s], missing[s]) for s in window]
    return MissingStats(rows, never, ipo, per_symbol.most_common(top))


@dataclass(frozen=True)
class Tightening:
    candidate: float
    value: float
    tighten: bool
    provisional: bool


def _ceil_step(x: float) -> float:
    return round(math.ceil(round(x / STEP, 9)) * STEP, 6)


def tightening(max_share: float, live_share: float | None, current: float) -> Tightening:
    """The protocol's rule: max(2 x max, live + 0.005), up to the next 0.005.

    Never below the observed max or the live share. Tighten only when the
    candidate is below `current`; otherwise keep `current` and investigate.
    Without a live-case count the candidate is provisional.
    """
    raw = 2 * max_share
    if live_share is not None:
        raw = max(raw, live_share + STEP)
    candidate = _ceil_step(max(raw, max_share, live_share or 0.0, STEP))
    tighten = candidate < current
    return Tightening(candidate, candidate if tighten else current, tighten, live_share is None)


def live_share(
    listed: Iterable[str], present: Mapping[str, set[date]], session: date, exclude: set[str]
) -> tuple[int, int, list[str]]:
    """(expected, missing, missing symbols) for one session; `exclude` are never-traded names."""
    expected = [s for s in sorted(set(listed)) if s not in exclude]
    gone = [s for s in expected if session not in present.get(s, set())]
    return len(expected), len(gone), gone


# ---------------------------------------------------------------- I/O below


def check_out_dir(path: Path) -> Path:
    """Refuse an output directory inside the repo (raw payloads stay out of git)."""
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        sys.exit(f"refusing to write inside the repo: {resolved}")
    return resolved


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))


def _load_bars(folder: Path) -> tuple[dict[str, list[Any]], list[str], list[str]]:
    """Merged bars, feed echoes and error texts from a folder of batch files."""
    bars: dict[str, list[Any]] = {}
    feeds: list[str] = []
    for p in sorted(folder.glob("batch_*.json")):
        payload = json.loads(p.read_text())
        feeds.append(str(payload.get("feed")))
        for sym, rows in (payload.get("bars") or {}).items():
            bars.setdefault(sym, []).extend(rows)
    errors = [e.read_text().strip() for e in sorted(folder.glob("batch_*.error.txt"))]
    return bars, feeds, errors


def _fetch_batches(folder: Path, symbols: list[str], call: Any) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(symbols), BATCH):
        stem = f"batch_{i // BATCH:04d}"
        try:
            _write(folder / f"{stem}.json", call(symbols[i : i + BATCH]))
        except Exception as exc:
            (folder / f"{stem}.error.txt").write_text(f"{type(exc).__name__}: {exc}\n")
            print(f"{stem}: {type(exc).__name__}")
        else:
            print(f"{stem}: ok")


def _assets(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "assets.json"
    if not path.exists():
        from alpaca.trading.enums import AssetClass, AssetStatus
        from alpaca.trading.requests import GetAssetsRequest

        from tradepartner.adapters import alpaca_raw
        from tradepartner.config import get_settings

        client = alpaca_raw._trading_client(get_settings())
        req = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
        raw: Any = client.get_all_assets(req)  # raw_data=True: a list of dicts
        _write(path, [dict(a) for a in raw])
    data: list[dict[str, Any]] = json.loads(path.read_text())
    return data


def fetch(run_dir: Path, today: date) -> None:
    """Asset list once, then SIP daily bars for the window in batches of 200."""
    from alpaca.data.enums import DataFeed

    from tradepartner.adapters import alpaca_raw

    listed = listed_symbols(_assets(run_dir))
    window = window_sessions(today)
    _write(run_dir / "window.json", [d.isoformat() for d in window])
    print(f"{len(listed)} listed symbols, window {window[0]} to {window[-1]}")
    _fetch_batches(
        run_dir / "bars",
        listed,
        lambda b: alpaca_raw.daily_bars(b, window[0], window[-1], feed=DataFeed.SIP),
    )


def fetch_live(run_dir: Path, now: datetime) -> None:
    """Bars for the latest completed session, at close + settle delay or later."""
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    from tradepartner.adapters import alpaca_raw
    from tradepartner.config import get_settings

    settings = get_settings()
    session = calendar.last_completed_session(now)
    due = calendar.session_close(session) + timedelta(minutes=settings.ingest.settle_delay_minutes)
    if now < due:
        sys.exit(f"too early: ingest for {session} runs at {due.isoformat()}")
    if now - due > timedelta(hours=2):
        print(f"warning: {now - due} after ingest time; the count reads more complete than live")
    client = alpaca_raw._stock_data_client(settings)
    start = datetime.combine(session, time.min, tzinfo=UTC)
    end = now - FREE_PLAN_DELAY

    def call(batch: list[str]) -> dict[str, Any]:
        req = StockBarsRequest(
            symbol_or_symbols=batch,
            start=start,
            end=end,
            timeframe=TimeFrame.Day,
            adjustment=Adjustment.RAW,
            feed=DataFeed.SIP,
        )
        return {"feed": "sip", "bars": client.get_stock_bars(req)}

    listed = listed_symbols(_assets(run_dir))
    live_dir = run_dir / "live"
    live_dir.mkdir(parents=True, exist_ok=True)
    _write(live_dir / "meta.json", {"session": session.isoformat(), "fetched_at": now.isoformat()})
    _fetch_batches(live_dir, listed, call)


def _section(
    title: str,
    stats: MissingStats,
    status: Mapping[str, tuple[Any, Any]],
    live: tuple[date, int, int, list[str]] | None,
    current: float,
) -> list[str]:
    out = [f"## {title}", ""]
    out += ["| Session | Expected | Missing | Share |", "|---|---|---|---|"]
    out += [f"| {r.session} | {r.expected} | {r.missing} | {r.share:.4f} |" for r in stats.rows]
    out += ["", f"- Median share: {stats.median_share:.4f}"]
    out.append(f"- Max share: {stats.max_share:.4f} on {stats.max_session}")
    out.append(f"- Never traded (no row in window, not in E): {len(stats.never_traded)}")
    out.append(f"- Probable IPOs (first row after window start): {len(stats.probable_ipo)}")
    if stats.top_missing:
        out += ["", "| Symbol | Sessions missing | Status | Tradable |", "|---|---|---|---|"]
        for sym, n in stats.top_missing:
            st, tr = status.get(sym, (None, None))
            out.append(f"| {sym} | {n} | {st} | {tr} |")
    live_value: float | None = None
    if live is not None:
        session, e, m, gone = live
        live_value = m / e if e else 0.0
        out += ["", f"- Live case {session}: expected {e}, missing {m}, share {live_value:.4f}"]
        if gone:
            out.append(f"- Live missing (first 20): {', '.join(gone[:20])}")
    rec = tightening(stats.max_share, live_value, current)
    note = " (provisional: no live case)" if rec.provisional else ""
    verdict = f"tighten to {rec.value:.3f}" if rec.tighten else f"keep {current}; investigate first"
    out += ["", f"- Tightening rule: candidate {rec.candidate:.3f}{note}; {verdict}", ""]
    return out


def analyze(run_dir: Path) -> str:
    """The probe report for a saved run, as Markdown: three readings of "missing".

    A counts only absent rows (the source did not deliver the session). B also
    counts zero-volume placeholders (the protocol with #104's rule). C is B on
    common-like names only. Every reading takes a name as expected from its
    first row of any kind, so a listed name that did not trade is not an IPO.
    """
    from tradepartner.config import get_settings

    current = get_settings().ingest.max_missing_share
    assets = _assets(run_dir)
    status = {a["symbol"]: (a.get("status"), a.get("tradable")) for a in assets}
    names = {a["symbol"]: str(a.get("name") or "") for a in assets}
    listed = listed_symbols(assets)
    common = [s for s in listed if instrument_kind(names[s]) == "common-like"]
    window = [date.fromisoformat(d) for d in json.loads((run_dir / "window.json").read_text())]
    bars, feeds, errors = _load_bars(run_dir / "bars")
    rows_any, real = all_rows(bars), present_sessions(bars)
    placeholders = sum(is_placeholder(b) for rows in bars.values() for b in rows)
    kinds = Counter(instrument_kind(names[s]) for s in listed)

    live_session: date | None = None
    l_any: dict[str, set[date]] = {}
    l_real: dict[str, set[date]] = {}
    live_dir = run_dir / "live"
    live_note = ""
    if (live_dir / "meta.json").exists():
        meta = json.loads((live_dir / "meta.json").read_text())
        live_session = date.fromisoformat(meta["session"])
        lbars, _, lerrors = _load_bars(live_dir)
        l_any, l_real = all_rows(lbars), present_sessions(lbars)
        live_note = f"{live_session}, fetched {meta['fetched_at']}, batch errors {len(lerrors)}"

    out = [f"# #106: missing-bar share on SIP daily bars ({run_dir.name})", ""]
    out.append(f"- Window: {window[0]} to {window[-1]} ({len(window)} XNYS sessions)")
    out.append(f"- Listed (active, tradable, NYSE/Nasdaq/NYSE American today): {len(listed)}")
    out.append(f"- By name: {', '.join(f'{k} {n}' for k, n in kinds.most_common())}")
    out.append(f"- Feed echo: {sorted(set(feeds))}; batch errors: {len(errors)}")
    out.append(f"- Zero-volume placeholder rows: {placeholders}")
    out.append(f"- Live case: {live_note or 'not run'}")
    out.append(f"- Current `ingest.max_missing_share`: {current}")
    out.append("")
    readings = [
        ("A. No bar row (placeholders count as present)", listed, rows_any, l_any),
        ("B. No traded bar (placeholders count as missing)", listed, real, l_real),
        ("C. As B, common-like names only", common, real, l_real),
    ]
    for title, names_, present, lpresent in readings:
        stats = missing_stats(names_, present, window, first_seen=rows_any)
        live = None
        if live_session is not None:
            e, m, gone = live_share(names_, lpresent, live_session, set(stats.never_traded))
            live = (live_session, e, m, gone)
        out += _section(title, stats, status, live, current)
    if errors:
        out += ["## Errors (verbatim)", "", "```", *errors, "```"]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="call Alpaca (needs keys), save payloads, print report")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    f.add_argument(
        "--out", type=Path, default=Path.home() / "tradepartner-probes" / "106-missing" / stamp
    )
    lv = sub.add_parser("live", help="count the latest session at ingest time (needs keys)")
    lv.add_argument("run_dir", type=Path)
    a = sub.add_parser("analyze", help="print the report for a saved run (offline)")
    a.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)

    now = datetime.now(UTC)
    if args.cmd == "fetch":
        run_dir = check_out_dir(args.out)
        run_dir.mkdir(parents=True, exist_ok=True)
        fetch(run_dir, now.astimezone(NEW_YORK).date())
    else:
        run_dir = check_out_dir(args.run_dir)
        if args.cmd == "live":
            fetch_live(run_dir, now)
    report = analyze(run_dir)
    (run_dir / "report.md").write_text(report)
    print(report)
    print(f"saved: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
