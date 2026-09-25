"""Owner-run probe for #106 (protocol "Probe 3"): how paper fills pre-open market orders.

    uv run python scripts/probe_open_fills.py submit [--opg]      # 09:00-09:15 ET, PAPER only
    uv run python scripts/probe_open_fills.py collect [--session YYYY-MM-DD]   # after 09:46 ET
    uv run python scripts/probe_open_fills.py analyze             # offline, all sessions so far

`submit` places, on the **paper** account only, a $5 notional (F) and a 1-share (W)
market DAY buy for KO and AAPL, plus with `--opg` a 1-share OPG control (O). It
refuses outside 09:00-09:15 ET on a full XNYS session and refuses any client
whose base URL is not the paper endpoint. Client order ids are deterministic
(`probe106-<date>-<symbol>-<kind>`), so a re-run the same day is rejected by
Alpaca as a duplicate instead of placing a second order.

`collect` fetches each order's final state, the SIP daily bar open, SIP trades
09:29:50-09:31:00 ET (official opening print) and SIP quotes at submit and
fill time. Run it on 5 normal sessions (no early close, no KO or AAPL earnings).
Raw payloads are saved outside the repo; nothing is written to the store.
Spike code (#106), never merged.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from tradepartner import calendar

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = Path.home() / "tradepartner-probes" / "106-open"
NEW_YORK = ZoneInfo("America/New_York")
LISTING = {"KO": "N", "AAPL": "Q"}  # SIP exchange code of the listing market
SUBMIT_FROM, SUBMIT_TO = time(9, 0), time(9, 15)
LATE = timedelta(seconds=60)  # "does not fill by 09:31"
FREE_PLAN_DELAY = timedelta(minutes=16)  # SIP queries must end >= 15 min in the past
SAME_PRICE = 0.005
PAPER_HOST = "paper-api.alpaca.markets"


@dataclass(frozen=True)
class PlannedOrder:
    symbol: str
    kind: str  # F fractional notional, W whole share, O OPG control
    qty: int | None
    notional: int | None
    tif: str
    client_order_id: str


def order_plan(session: date, *, include_opg: bool) -> list[PlannedOrder]:
    """The protocol's orders for one session, with deterministic client ids."""
    out = []
    for sym in LISTING:
        kinds = [("F", None, 5, "day"), ("W", 1, None, "day")]
        if include_opg:
            kinds.append(("O", 1, None, "opg"))
        for kind, qty, notional, tif in kinds:
            cid = f"probe106-{session:%Y%m%d}-{sym}-{kind}"
            out.append(PlannedOrder(sym, kind, qty, notional, tif, cid))
    return out


def submit_window_error(now: datetime) -> str | None:
    """Why `now` is not a valid submit time, or None if it is."""
    local = now.astimezone(NEW_YORK)
    day = local.date()
    if not calendar.is_session(day):
        return f"{day} is not an XNYS session"
    if calendar.is_half_day(day):
        return f"{day} is an early-close session"
    if not SUBMIT_FROM <= local.time().replace(microsecond=0) <= SUBMIT_TO:
        return f"{local:%H:%M:%S} ET is outside 09:00-09:15 ET"
    return None


def require_paper(client: Any) -> None:
    """Exit unless the trading client points at the paper endpoint."""
    base = getattr(client, "_base_url", "")
    url = str(getattr(base, "value", base))
    if PAPER_HOST not in url:
        sys.exit(f"refusing: trading client base URL is not paper ({url!r})")


_NANOS = re.compile(r"(\.\d{6})\d+")


def parse_ts(value: str) -> datetime:
    """An Alpaca timestamp (may carry nanoseconds) as tz-aware UTC."""
    text = _NANOS.sub(r"\1", str(value).replace("Z", "+00:00"))
    return datetime.fromisoformat(text).astimezone(UTC)


def official_open(trades: Iterable[Mapping[str, Any]], exchange: str) -> float | None:
    """Price of the listing exchange's opening print (condition O, or its Q duplicate)."""
    ours = sorted((t for t in trades if t.get("x") == exchange), key=lambda t: parse_ts(t["t"]))
    for code in ("O", "Q"):
        for t in ours:
            if code in (t.get("c") or []):
                return float(t["p"])
    return None


def nbbo_at(quotes: Iterable[Mapping[str, Any]], at: datetime) -> tuple[float, float] | None:
    """(bid, ask) of the last quote at or before `at`."""
    before = [q for q in quotes if parse_ts(q["t"]) <= at]
    if not before:
        return None
    q = max(before, key=lambda q: parse_ts(q["t"]))
    return float(q["bp"]), float(q["ap"])


@dataclass(frozen=True)
class Metrics:
    client_order_id: str
    symbol: str
    kind: str
    status: str
    latency_s: float | None
    gap_official_bps: float | None
    gap_bar_bps: float | None
    late: bool
    at_official: bool
    at_pre_ask: bool


def _bps(fill: float, ref: float | None) -> float | None:
    return None if ref is None else 10_000 * (fill - ref) / ref


def order_metrics(
    order: Mapping[str, Any],
    session: date,
    *,
    bar_open: float | None,
    official: float | None,
    pre_ask: float | None,
) -> Metrics:
    """Latency after the open and gaps to the official and bar opens (buys: + is cost)."""
    cid = str(order["client_order_id"])
    kind = cid.rsplit("-", 1)[-1]
    opened = calendar.session_open(session)
    if not order.get("filled_at") or not order.get("filled_avg_price"):
        return Metrics(
            cid, str(order["symbol"]), kind, str(order.get("status")), None, None, None,
            True, False, False,
        )  # fmt: skip
    filled_at = parse_ts(order["filled_at"])
    fill = float(order["filled_avg_price"])
    return Metrics(
        client_order_id=cid,
        symbol=str(order["symbol"]),
        kind=kind,
        status=str(order.get("status")),
        latency_s=(filled_at - opened).total_seconds(),
        gap_official_bps=_bps(fill, official),
        gap_bar_bps=_bps(fill, bar_open),
        late=filled_at > opened + LATE,
        at_official=official is not None and abs(fill - official) < SAME_PRICE,
        at_pre_ask=pre_ask is not None and abs(fill - pre_ask) < SAME_PRICE,
    )


def summarize(rows: Sequence[Metrics]) -> dict[str, dict[str, Any]]:
    """Per order kind: fill count and (median, worst) of latency and both gaps."""
    out: dict[str, dict[str, Any]] = {}
    for kind in sorted({r.kind for r in rows}):
        mine = [r for r in rows if r.kind == kind]
        s: dict[str, Any] = {"n": sum(r.latency_s is not None for r in mine)}
        for field in ("latency_s", "gap_official_bps", "gap_bar_bps"):
            vals = [v for r in mine if (v := getattr(r, field)) is not None]
            s[field] = (statistics.median(vals), max(vals)) if vals else (None, None)
        out[kind] = s
    return out


# ---------------------------------------------------------------- I/O below


def check_out_dir(path: Path) -> Path:
    """Refuse an output directory inside the repo (raw payloads stay out of git)."""
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        sys.exit(f"refusing to write inside the repo: {resolved}")
    return resolved


def _write(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=1, sort_keys=True, default=str))


def _read(path: Path) -> Any:
    return json.loads(path.read_text()) if path.exists() else None


def _paper_client() -> Any:
    from tradepartner.adapters import alpaca_raw
    from tradepartner.config import get_settings

    client = alpaca_raw._trading_client(get_settings())  # constructed with paper=True
    require_paper(client)
    return client


def submit(root: Path, *, include_opg: bool, now: datetime) -> None:
    """Place the session's paper orders (refuses outside the window or off paper)."""
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    if err := submit_window_error(now):
        sys.exit(f"refusing to submit: {err}")
    if include_opg and now.astimezone(NEW_YORK).time() >= time(9, 28):
        sys.exit("refusing: OPG must be submitted before 09:28 ET")
    session = now.astimezone(NEW_YORK).date()
    print("Reminder: skip early-close days and KO/AAPL earnings days (not checked here).")
    client = _paper_client()
    day = root / session.isoformat()
    day.mkdir(parents=True, exist_ok=True)
    results = []
    for p in order_plan(session, include_opg=include_opg):
        req = MarketOrderRequest(
            symbol=p.symbol,
            qty=p.qty,
            notional=p.notional,
            side=OrderSide.BUY,
            time_in_force=TimeInForce(p.tif),
            extended_hours=False,
            client_order_id=p.client_order_id,
        )
        local = datetime.now(UTC).isoformat()
        try:
            resp: Any = client.submit_order(req)
            results.append({"planned": p.__dict__, "local_submit": local, "response": dict(resp)})
            print(f"{p.client_order_id}: {resp.get('status')}")
        except Exception as exc:
            results.append({"planned": p.__dict__, "local_submit": local, "error": repr(exc)})
            print(f"{p.client_order_id}: {type(exc).__name__}")
    prior = _read(day / "submitted.json") or []
    _write(day / "submitted.json", prior + results)


def collect(root: Path, session: date, now: datetime) -> None:
    """Final order states, SIP bar open, opening trades and quotes for one session."""
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest, StockQuotesRequest, StockTradesRequest
    from alpaca.data.timeframe import TimeFrame

    from tradepartner.adapters import alpaca_raw
    from tradepartner.config import get_settings

    opened = calendar.session_open(session)
    if now < opened + timedelta(minutes=16):
        sys.exit(f"too early: collect after {(opened + timedelta(minutes=16)).isoformat()}")
    day = root / session.isoformat()
    day.mkdir(parents=True, exist_ok=True)
    trading = _paper_client()
    orders = []
    for p in order_plan(session, include_opg=True):
        try:
            orders.append(dict(trading.get_order_by_client_id(p.client_order_id)))
        except Exception as exc:
            print(f"{p.client_order_id}: not found ({type(exc).__name__})")
    _write(day / "orders_final.json", orders)

    data = alpaca_raw._stock_data_client(get_settings())
    end = min(now - FREE_PLAN_DELAY, datetime.combine(session, time.max, tzinfo=UTC))
    bars = data.get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=list(LISTING),
            start=datetime.combine(session, time.min, tzinfo=UTC),
            end=end,
            timeframe=TimeFrame.Day,
            feed=DataFeed.SIP,
        )
    )
    _write(day / "bars.json", {"feed": "sip", "bars": bars})
    for sym in LISTING:
        req = StockTradesRequest(
            symbol_or_symbols=sym,
            start=opened - timedelta(seconds=10),
            end=opened + timedelta(seconds=60),
            feed=DataFeed.SIP,
        )
        _write(day / f"trades_{sym}.json", data.get_stock_trades(req))

    def quotes(sym: str, lo: datetime, hi: datetime) -> Any:
        req = StockQuotesRequest(symbol_or_symbols=sym, start=lo, end=hi, feed=DataFeed.SIP)
        return data.get_stock_quotes(req)

    for o in orders:
        cid, sym = o["client_order_id"], o["symbol"]
        sub = parse_ts(o["submitted_at"])
        _write(day / f"quotes_{cid}_submit.json", quotes(sym, sub - timedelta(minutes=5), sub))
        if o.get("filled_at"):
            fat = parse_ts(o["filled_at"])
            _write(day / f"quotes_{cid}_fill.json", quotes(sym, fat - timedelta(seconds=2), fat))
    print(f"collected {len(orders)} orders for {session}")


def _session_rows(day: Path) -> list[tuple[Metrics, Mapping[str, Any], tuple[float, float] | None]]:
    session = date.fromisoformat(day.name)
    bars = (_read(day / "bars.json") or {}).get("bars") or {}
    rows = []
    for o in _read(day / "orders_final.json") or []:
        sym, cid = o["symbol"], o["client_order_id"]
        sym_bars = bars.get(sym) or []
        bar_open = float(sym_bars[0]["o"]) if sym_bars else None
        official = official_open(
            (_read(day / f"trades_{sym}.json") or {}).get(sym, []), LISTING[sym]
        )
        pre = None
        if o.get("submitted_at"):
            sub_quotes = (_read(day / f"quotes_{cid}_submit.json") or {}).get(sym, [])
            pre = nbbo_at(sub_quotes, parse_ts(o["submitted_at"]))
        at_fill = None
        if o.get("filled_at"):
            fill_quotes = (_read(day / f"quotes_{cid}_fill.json") or {}).get(sym, [])
            at_fill = nbbo_at(fill_quotes, parse_ts(o["filled_at"]))
        m = order_metrics(
            o, session, bar_open=bar_open, official=official, pre_ask=pre[1] if pre else None
        )
        rows.append((m, o, at_fill))
    return rows


def _fmt(v: float | None, spec: str = ".2f") -> str:
    return "" if v is None else format(v, spec)


def analyze(root: Path) -> str:
    """The probe report over every collected session under `root`, as Markdown."""
    days = sorted(p for p in root.iterdir() if p.is_dir() and (p / "orders_final.json").exists())
    out = [f"# #106: paper fills of pre-open market orders ({len(days)} sessions)", ""]
    out += [
        "| Session | Order | Status | Fill | Latency s | vs official bps | vs bar bps "
        "| NBBO at fill | Late | = official | = pre-open ask |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    all_rows: list[Metrics] = []
    for day in days:
        for m, o, at_fill in _session_rows(day):
            all_rows.append(m)
            nbbo = f"{at_fill[0]:.2f} / {at_fill[1]:.2f}" if at_fill else ""
            out.append(
                f"| {day.name} | {m.client_order_id} | {m.status} "
                f"| {o.get('filled_avg_price') or ''} | {_fmt(m.latency_s, '.3f')} "
                f"| {_fmt(m.gap_official_bps)} | {_fmt(m.gap_bar_bps)} "
                f"| {nbbo} | {m.late} | {m.at_official} | {m.at_pre_ask} |"
            )
    out += ["", "## Summary per order type (median, worst)", ""]
    out += ["| Type | Fills | Latency s | vs official bps | vs bar bps |", "|---|---|---|---|---|"]
    for kind, s in summarize(all_rows).items():
        cells = [f"{_fmt(a, '.3f')} / {_fmt(b, '.3f')}" for a, b in
                 (s["latency_s"], s["gap_official_bps"], s["gap_bar_bps"])]  # fmt: skip
        out.append(f"| {kind} | {s['n']} | " + " | ".join(cells) + " |")
    out += ["", "## Outcome flags (see the protocol's reading table)", ""]
    out.append(f"- Not filled by 09:31: {sum(m.late for m in all_rows)}")
    out.append(f"- Filled at the official open: {sum(m.at_official for m in all_rows)}")
    out.append(f"- Filled at pre-open ask: {sum(m.at_pre_ask for m in all_rows)}")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("submit", help="place today's paper orders (09:00-09:15 ET)")
    s.add_argument("--opg", action="store_true", help="also place the OPG control (before 09:28)")
    c = sub.add_parser("collect", help="fetch fills, opens and quotes (after 09:46 ET)")
    c.add_argument("--session", type=date.fromisoformat, default=None)
    sub.add_parser("analyze", help="print the report over all sessions (offline)")
    args = parser.parse_args(argv)

    root = check_out_dir(args.root)
    root.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    if args.cmd == "submit":
        submit(root, include_opg=args.opg, now=now)
        return 0
    if args.cmd == "collect":
        collect(root, args.session or now.astimezone(NEW_YORK).date(), now)
    report = analyze(root)
    (root / "report.md").write_text(report)
    print(report)
    print(f"saved: {root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
