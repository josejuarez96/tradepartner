# ruff: noqa: E501
"""A data-driven search on Sharadar tables, under the rules pinned in PROTOCOL.md.

Spike code, never merged. It prints summary statistics only and writes no vendor row outside ``--work``
(which holds a derived monthly panel; delete it with the vendor data).

Modes: describe | explore | confirm | exam.  Run ``describe`` first.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

EXPLORE = ("1999-01", "2014-10")
CONFIRM = ("2015-01", "2019-12")
EXAM = ("2020-01", "2023-11")
LAST_LOAD = "2023-12-31"
BANDS = {"primary": (10e6, 200e6), "secondary": (200e6, float("inf"))}
MIN_PRICE = 5.0
HOLD_SESSIONS = 21
MIN_NAMES = 100
FUND_LAG_DAYS = 5
INST_LAG_DAYS = 50
INSIDER_WINDOW_DAYS = 90
STALE_DAYS = 200
COSTS_BP = (50.0, 25.0, 12.0)
PASS_NET_PCT = 0.20
PASS_T = 2.0
TOP_FRACTION = 0.10
DELIST_SHOCK = -0.30
WALK_FORWARD_FIRST_YEAR = 2004
MIN_WINDOW_SHARE = 0.8

TABLES = ("SEP", "TICKERS", "SF1", "SF2", "SF3A", "SF3", "DAILY", "ACTIONS", "EVENTS", "SFP")
PRICE_FEATURES = ["mom_1m", "mom_6m", "mom_12_1", "vol60", "maxret21", "hi52", "amihud", "dv_chg"]
FUND_FEATURES = [
    "gpa",
    "cfoa",
    "roe",
    "accruals",
    "asset_growth",
    "share_growth",
    "rev_growth",
    "margin_chg",
    "debt_growth",
    "ey",
    "fcfy",
    "bp",
]
INSIDER_FEATURES = ["ins_buyers", "ins_sellers", "ins_net_value"]
INST_FEATURES = ["inst_holders_chg", "inst_units_chg"]


# ---------------------------------------------------------------- files


def find_table(data: Path, name: str) -> Path | None:
    """The file for one Sharadar table, by name, or None."""
    hits = []
    for p in sorted(data.iterdir()):
        if not p.is_file():
            continue
        stem = p.name.upper().split(".")[0]
        if stem in (name, f"SHARADAR_{name}") or stem.startswith((f"SHARADAR_{name}_", f"{name}_")):
            hits.append(p)
    return hits[0] if hits else None


def source(path: Path) -> str:
    """A DuckDB table expression that reads the file."""
    low = path.name.lower()
    quoted = str(path).replace("'", "''")
    if low.endswith(".parquet"):
        return f"read_parquet('{quoted}')"
    if low.endswith((".csv", ".csv.gz")):
        return f"read_csv_auto('{quoted}', sample_size=200000)"
    raise SystemExit(f"{path.name}: unzip it first (a .csv, .csv.gz or .parquet file is needed)")


def columns(con: duckdb.DuckDBPyConnection, src: str) -> list[str]:
    return [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {src}").fetchall()]


def describe(data: Path) -> None:
    """File names, column names, row counts and date ranges. No data values."""
    con = duckdb.connect()
    for name in TABLES:
        path = find_table(data, name)
        if path is None:
            print(f"{name}: not found")
            continue
        src = source(path)
        cols = columns(con, src)
        n = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
        line = f"{name}: {path.name}; {n:,} rows; columns: {', '.join(cols)}"
        for c in ("date", "datekey", "filingdate", "calendardate"):
            if c in cols:
                lo, hi = con.execute(f"SELECT min({c}), max({c}) FROM {src}").fetchone()
                line += f"; {c} {lo} to {hi}"
                break
        if "dimension" in cols:
            dims = [
                r[0]
                for r in con.execute(f"SELECT DISTINCT dimension FROM {src} ORDER BY 1").fetchall()
            ]
            line += f"; dimensions {dims}"
        print(line)


# ---------------------------------------------------------------- the monthly panel


def _need(cols: list[str], wanted: list[str], what: str) -> bool:
    missing = [c for c in wanted if c not in cols]
    if missing:
        print(f"  skipped {what}: missing columns {missing}")
    return not missing


def build_panel(data: Path, work: Path) -> pd.DataFrame:
    """One row per security and month-end: band inputs, features, and the forward return."""
    cache = work / "panel.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    con = duckdb.connect()
    con.execute(f"PRAGMA temp_directory='{work / 'duckdb_tmp'}'")
    sep = find_table(data, "SEP")
    if sep is None:
        raise SystemExit("SEP (daily prices) is required")
    src = source(sep)
    cols = columns(con, src)
    if not _need(cols, ["ticker", "date", "closeadj", "closeunadj", "volume"], "everything (SEP)"):
        raise SystemExit(1)
    dv = "close * volume" if "close" in cols else "closeunadj * volume"
    con.execute(f"""
        CREATE TEMP TABLE px AS
        SELECT ticker, CAST(date AS DATE) AS d, closeadj AS ca, closeunadj AS cu, {dv} AS dv
        FROM {src} WHERE CAST(date AS DATE) <= DATE '{LAST_LOAD}' AND closeadj > 0 AND closeunadj > 0""")
    tick = find_table(data, "TICKERS")
    if tick is not None:
        tcols = columns(con, source(tick))
        if _need(tcols, ["ticker", "category"], "the common-stock filter (TICKERS)"):
            where = "category ILIKE 'Domestic Common Stock%'"
            if "table" in tcols:
                where += " AND \"table\" = 'SEP'"
            con.execute(
                f"DELETE FROM px WHERE ticker NOT IN (SELECT ticker FROM {source(tick)} WHERE {where})"
            )
    else:
        print("  TICKERS not found: no common-stock filter applied")
    data_end = con.execute("SELECT max(d) FROM px").fetchone()[0]
    con.execute("""
        CREATE TEMP TABLE a AS
        SELECT ticker, d, ca, cu, dv,
               ca / lag(ca) OVER w - 1 AS r1,
               lag(ca, 21) OVER w AS c21, lag(ca, 126) OVER w AS c126, lag(ca, 252) OVER w AS c252,
               lead(ca, 1) OVER w AS e1, lead(ca, 22) OVER w AS e22,
               last_value(ca) OVER (PARTITION BY ticker ORDER BY d
                                    ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) AS last_ca,
               max(d) OVER (PARTITION BY ticker) AS last_d
        FROM px WINDOW w AS (PARTITION BY ticker ORDER BY d)""")
    con.execute("""
        CREATE TEMP TABLE b AS
        SELECT *,
               median(dv) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS med_dv20,
               count(dv) OVER (w ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS n20,
               median(dv) OVER (w ROWS BETWEEN 79 PRECEDING AND 20 PRECEDING) AS dv_prev,
               stddev_samp(r1) OVER (w ROWS BETWEEN 59 PRECEDING AND CURRENT ROW) AS vol60,
               max(r1) OVER (w ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) AS maxret21,
               max(ca) OVER (w ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS hi252,
               avg(abs(r1) / nullif(dv, 0)) OVER (w ROWS BETWEEN 20 PRECEDING AND CURRENT ROW) * 1e6 AS amihud
        FROM a WINDOW w AS (PARTITION BY ticker ORDER BY d)""")
    con.execute(f"""
        CREATE TEMP TABLE panel AS
        SELECT b.ticker, b.d, strftime(b.d, '%Y-%m') AS ym, b.cu, b.med_dv20,
               b.ca / b.c21 - 1 AS mom_1m, b.ca / b.c126 - 1 AS mom_6m, b.c21 / b.c252 - 1 AS mom_12_1,
               b.vol60, b.maxret21, b.ca / b.hi252 - 1 AS hi52, b.amihud, b.med_dv20 / b.dv_prev - 1 AS dv_chg,
               CASE WHEN b.e1 IS NULL THEN NULL
                    WHEN b.e22 IS NOT NULL THEN b.e22 / b.e1 - 1
                    WHEN b.last_d < DATE '{data_end}' - INTERVAL 10 DAY THEN b.last_ca / b.e1 - 1
                    ELSE NULL END AS fwd,
               (b.e1 IS NOT NULL AND b.e22 IS NULL AND b.last_d < DATE '{data_end}' - INTERVAL 10 DAY) AS delisted
        FROM b JOIN (SELECT max(d) AS d FROM px GROUP BY strftime(d, '%Y-%m')) me USING (d)
        WHERE b.cu >= {MIN_PRICE} AND b.n20 = 20 AND b.med_dv20 >= {BANDS["primary"][0]}""")
    _add_fundamentals(con, data)
    _add_insiders(con, data)
    _add_institutions(con, data)
    panel = con.execute("SELECT * FROM panel").df()
    work.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(cache)
    return panel


def _add_fundamentals(con: duckdb.DuckDBPyConnection, data: Path) -> None:
    path = find_table(data, "SF1")
    if path is None:
        print("  SF1 not found: no fundamentals features")
        return
    src = source(path)
    need = [
        "ticker",
        "dimension",
        "datekey",
        "assets",
        "gp",
        "revenue",
        "ncfo",
        "netinc",
        "equity",
        "debt",
        "sharesbas",
        "fcf",
    ]
    if not _need(columns(con, src), need, "fundamentals (SF1)"):
        return
    con.execute(f"""
        CREATE TEMP TABLE f AS
        SELECT ticker, CAST(datekey AS DATE) + INTERVAL {FUND_LAG_DAYS} DAY AS avail,
               assets, gp, revenue, ncfo, netinc, equity, debt, sharesbas, fcf,
               lag(assets, 4) OVER w AS assets4, lag(sharesbas, 4) OVER w AS shares4,
               lag(revenue, 4) OVER w AS rev4, lag(gp, 4) OVER w AS gp4, lag(debt, 4) OVER w AS debt4
        FROM {src} WHERE dimension = 'ART' AND CAST(datekey AS DATE) <= DATE '{LAST_LOAD}'
        WINDOW w AS (PARTITION BY ticker ORDER BY datekey)""")
    exprs = {
        "gpa": "f.gp / nullif(f.assets, 0)",
        "cfoa": "f.ncfo / nullif(f.assets, 0)",
        "roe": "CASE WHEN f.equity > 0 THEN f.netinc / f.equity END",
        "accruals": "(f.netinc - f.ncfo) / nullif(f.assets, 0)",
        "asset_growth": "f.assets / nullif(f.assets4, 0) - 1",
        "share_growth": "f.sharesbas / nullif(f.shares4, 0) - 1",
        "rev_growth": "CASE WHEN f.rev4 > 0 THEN f.revenue / f.rev4 - 1 END",
        "margin_chg": "f.gp / nullif(f.revenue, 0) - f.gp4 / nullif(f.rev4, 0)",
        "debt_growth": "CASE WHEN f.debt4 > 0 THEN f.debt / f.debt4 - 1 END",
        "ey": "f.netinc / nullif(f.sharesbas * p.cu, 0)",
        "fcfy": "f.fcf / nullif(f.sharesbas * p.cu, 0)",
        "bp": "f.equity / nullif(f.sharesbas * p.cu, 0)",
    }
    # a fundamentals row is used from FUND_LAG_DAYS after its datekey and for at most STALE_DAYS
    fresh = f"p.d - CAST(f.avail AS DATE) <= {STALE_DAYS}"
    select = ", ".join(f"CASE WHEN {fresh} THEN {e} END AS {name}" for name, e in exprs.items())
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE panel AS
        SELECT p.*, {select}
        FROM panel p ASOF LEFT JOIN f ON p.ticker = f.ticker AND p.d >= f.avail""")


def _truthy(col: str) -> str:
    return f"upper(CAST({col} AS VARCHAR)) IN ('Y', 'TRUE', '1', 'T')"


def _add_insiders(con: duckdb.DuckDBPyConnection, data: Path) -> None:
    path = find_table(data, "SF2")
    if path is None:
        print("  SF2 not found: no insider features")
        return
    src = source(path)
    need = [
        "ticker",
        "filingdate",
        "transactioncode",
        "isofficer",
        "isdirector",
        "ownername",
        "transactionvalue",
    ]
    if not _need(columns(con, src), need, "insiders (SF2)"):
        return
    con.execute(f"""
        CREATE TEMP TABLE ins AS
        SELECT ticker, CAST(filingdate AS DATE) AS fd, transactioncode AS code, ownername,
               abs(coalesce(transactionvalue, 0)) AS val
        FROM {src}
        WHERE transactioncode IN ('P', 'S') AND ({_truthy("isofficer")} OR {_truthy("isdirector")})
          AND CAST(filingdate AS DATE) <= DATE '{LAST_LOAD}'""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE panel AS
        SELECT p.*, coalesce(i.buyers, 0) AS ins_buyers, coalesce(i.sellers, 0) AS ins_sellers,
               coalesce(i.net_val, 0) / nullif(p.med_dv20, 0) AS ins_net_value
        FROM panel p LEFT JOIN (
            SELECT k.ticker, k.d,
                   count(DISTINCT CASE WHEN ins.code = 'P' THEN ins.ownername END) AS buyers,
                   count(DISTINCT CASE WHEN ins.code = 'S' THEN ins.ownername END) AS sellers,
                   sum(CASE WHEN ins.code = 'P' THEN ins.val ELSE -ins.val END) AS net_val
            FROM (SELECT ticker, d FROM panel) k JOIN ins
              ON ins.ticker = k.ticker AND ins.fd <= k.d AND ins.fd > k.d - INTERVAL {INSIDER_WINDOW_DAYS} DAY
            GROUP BY 1, 2) i USING (ticker, d)""")


def _add_institutions(con: duckdb.DuckDBPyConnection, data: Path) -> None:
    path = find_table(data, "SF3A")
    if path is None:
        print("  SF3A not found: no institutional features")
        return
    src = source(path)
    if not _need(
        columns(con, src),
        ["ticker", "calendardate", "shrholders", "shrunits"],
        "institutions (SF3A)",
    ):
        return
    con.execute(f"""
        CREATE TEMP TABLE inst AS
        SELECT ticker, CAST(calendardate AS DATE) + INTERVAL {INST_LAG_DAYS} DAY AS avail,
               shrholders / nullif(lag(shrholders) OVER w, 0) - 1 AS holders_chg,
               shrunits / nullif(lag(shrunits) OVER w, 0) - 1 AS units_chg
        FROM {src} WHERE CAST(calendardate AS DATE) <= DATE '{LAST_LOAD}'
        WINDOW w AS (PARTITION BY ticker ORDER BY calendardate)""")
    con.execute(f"""
        CREATE OR REPLACE TEMP TABLE panel AS
        SELECT p.*,
               CASE WHEN p.d - CAST(i.avail AS DATE) <= {STALE_DAYS} THEN i.holders_chg END AS inst_holders_chg,
               CASE WHEN p.d - CAST(i.avail AS DATE) <= {STALE_DAYS} THEN i.units_chg END AS inst_units_chg
        FROM panel p ASOF LEFT JOIN inst i ON p.ticker = i.ticker AND p.d >= i.avail""")


# ---------------------------------------------------------------- scoring


def prepare(panel: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Bands, the peer-relative target in percent, and ranked features."""
    p = panel[panel.fwd.notna()].copy()
    p["band"] = np.where(p.med_dv20 >= BANDS["secondary"][0], "secondary", "primary")
    size = p.groupby(["ym", "band"]).ticker.transform("size")
    p = p[size >= MIN_NAMES].copy()
    grp = p.groupby(["ym", "band"])
    p["rel"] = 100.0 * (p.fwd - grp.fwd.transform("mean"))
    shocked = np.where(p.delisted, (1.0 + p.fwd) * (1.0 + DELIST_SHOCK) - 1.0, p.fwd)
    p["fwd_shock"] = shocked
    p["rel_shock"] = 100.0 * (p.fwd_shock - p.groupby(["ym", "band"]).fwd_shock.transform("mean"))
    candidates = PRICE_FEATURES + FUND_FEATURES + INSIDER_FEATURES + INST_FEATURES
    explore = p[(p.ym >= EXPLORE[0]) & (p.ym <= EXPLORE[1])]
    feats = [c for c in candidates if c in p.columns and explore[c].notna().mean() > 0.20]
    for c in feats:
        p["x_" + c] = (p.groupby(["ym", "band"])[c].rank(pct=True) - 0.5).fillna(0.0)
    return p, feats


def window(p: pd.DataFrame, win: tuple[str, str]) -> pd.DataFrame:
    return p[(p.ym >= win[0]) & (p.ym <= win[1])]


def tstat(x: pd.Series) -> tuple[float, float, int]:
    x = x.dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return (float(x.mean()) if len(x) else float("nan"), float("nan"), len(x))
    return float(x.mean()), float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), len(x)


def book(scored: pd.DataFrame, band: str, target: str = "rel") -> dict[str, object]:
    """The top-tenth book in one band: gross, turnover, and net at each cost."""
    rows, prev = [], None
    for ym, g in scored[scored.band == band].groupby("ym"):
        k = max(1, round(len(g) * TOP_FRACTION))
        top = g.nlargest(k, "score")
        names = set(top.ticker)
        turn = np.nan if prev is None else 1.0 - len(names & prev) / len(names)
        rows.append((ym, top[target].mean(), turn, len(names)))
        prev = names
    r = pd.DataFrame(rows, columns=["ym", "gross", "turn", "n"]).set_index("ym")
    if r.empty:
        return {"months": 0}
    r["turn"] = r.turn.fillna(r.turn.mean())
    out: dict[str, object] = {
        "months": len(r),
        "names": float(r.n.mean()),
        "turnover": float(r.turn.mean()),
    }
    out["gross"] = tstat(r.gross)[:2]
    for c in COSTS_BP:
        out[f"net{int(c)}"] = tstat(r.gross - 2.0 * r.turn * c / 100.0)[:2]
    return out


def show(label: str, b: dict[str, object]) -> None:
    if not b.get("months"):
        print(f"  {label}: no months")
        return
    parts = [f"gross {b['gross'][0]:+.3f} (t {b['gross'][1]:+.2f})"]
    parts += [
        f"net@{int(c)}bp {b[f'net{int(c)}'][0]:+.3f} (t {b[f'net{int(c)}'][1]:+.2f})"
        for c in COSTS_BP
    ]
    print(
        f"  {label}: {b['months']} months, {b['names']:.0f} names, turnover {b['turnover']:.2f}/month; "
        + "; ".join(parts)
    )


def make_model(kind: str):
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.linear_model import Ridge

    if kind == "ridge":
        return Ridge(alpha=1.0)
    return HistGradientBoostingRegressor(
        max_depth=3, max_iter=200, learning_rate=0.05, min_samples_leaf=200, random_state=0
    )


def previous_month(ym: str, back: int) -> str:
    y, m = int(ym[:4]), int(ym[5:])
    k = y * 12 + (m - 1) - back
    return f"{k // 12:04d}-{k % 12 + 1:02d}"


def explore(data: Path, work: Path) -> None:
    frozen = work / "frozen.json"
    if (work / "confirm.json").exists():
        raise SystemExit(
            "Confirm has been read in this work directory; a changed search needs a new one."
        )
    p, feats = prepare(build_panel(data, work))
    xs = ["x_" + c for c in feats]
    ex = window(p, EXPLORE)
    prim = ex[ex.band == "primary"]
    print(
        f"Explore {EXPLORE[0]} to {EXPLORE[1]}: {prim.ym.nunique()} months, "
        f"{len(prim) / max(1, prim.ym.nunique()):.0f} names a month in the primary band; features: {feats}"
    )
    print(
        "\nOne feature at a time: monthly rank correlation with the peer-relative forward return (primary band)"
    )
    rank_y = prim.groupby("ym").rel.rank()
    for c in feats:
        ic = prim.assign(_y=rank_y).groupby("ym").apply(lambda g, c=c: g["x_" + c].corr(g["_y"]))
        m, t, _n = tstat(ic)
        print(f"  {c:18s} mean {m:+.4f}  t {t:+.2f}")
    print("\nWalk-forward inside Explore (train on earlier months, score one year at a time)")
    best, best_t = None, -np.inf
    for kind in ("ridge", "trees"):
        parts = []
        for year in range(WALK_FORWARD_FIRST_YEAR, int(EXPLORE[1][:4]) + 1):
            train = prim[prim.ym <= previous_month(f"{year}-01", 3)]
            test = ex[ex.ym.str.startswith(str(year))]
            if train.ym.nunique() < 36 or test.empty:
                continue
            model = make_model(kind).fit(train[xs].to_numpy(), train.rel.to_numpy())
            parts.append(test.assign(score=model.predict(test[xs].to_numpy())))
        if not parts:
            raise SystemExit("too little history for the walk-forward")
        scored = pd.concat(parts)
        b = book(scored, "primary")
        show(f"{kind:6s} primary", b)
        show(f"{kind:6s} secondary", book(scored, "secondary"))
        t = b["net50"][1] if b.get("months") else -np.inf
        if t > best_t:
            best, best_t = kind, t
    model = make_model(best).fit(prim[xs].to_numpy(), prim.rel.to_numpy())
    blob = pickle.dumps(model)
    (work / "model.pkl").write_bytes(blob)
    frozen.write_text(
        json.dumps(
            {
                "model": best,
                "features": feats,
                "sha256": hashlib.sha256(blob).hexdigest(),
                "walk_forward_t_net50": best_t,
                "explore": EXPLORE,
            },
            indent=1,
        )
    )
    print(
        f"\nFrozen: {best}, chosen on the walk-forward t at 50 bp ({best_t:+.2f}); sha256 {hashlib.sha256(blob).hexdigest()[:16]}"
    )
    print("Explore figures are in-sample for the choice of model. Only Confirm counts.")


def judged(data: Path, work: Path, win: tuple[str, str], name: str) -> None:
    out = work / f"{name}.json"
    if out.exists():
        print(f"{name} was already read once. The stored result:\n{out.read_text()}")
        return
    frozen_path = work / "frozen.json"
    if not frozen_path.exists():
        raise SystemExit("run explore first")
    if name == "exam":
        conf = work / "confirm.json"
        if not conf.exists() or not json.loads(conf.read_text())["passed"]:
            raise SystemExit("The exam is read only after Confirm has passed.")
    frozen = json.loads(frozen_path.read_text())
    blob = (work / "model.pkl").read_bytes()
    if hashlib.sha256(blob).hexdigest() != frozen["sha256"]:
        raise SystemExit("model.pkl does not match frozen.json")
    model = pickle.loads(blob)
    p, _feats = prepare(build_panel(data, work))
    for c in frozen["features"]:
        if "x_" + c not in p.columns:
            p["x_" + c] = 0.0
    xs = ["x_" + c for c in frozen["features"]]
    w = window(p, win)
    scored = w.assign(score=model.predict(w[xs].to_numpy()))
    result: dict[str, object] = {
        "window": win,
        "model": frozen["model"],
        "sha256": frozen["sha256"],
    }
    print(
        f"{name.capitalize()} {win[0]} to {win[1]}, frozen model {frozen['model']} ({frozen['sha256'][:16]})"
    )
    for band in BANDS:
        b = book(scored, band)
        show(band, b)
        show(band + ", -30% on every exit by delisting", book(scored, band, "rel_shock"))
        result[band] = b
    prim = result["primary"]
    expected = len(pd.period_range(win[0], win[1], freq="M"))
    if prim.get("months", 0) < MIN_WINDOW_SHARE * expected:
        raise SystemExit(
            f"Only {prim.get('months', 0)} of {expected} months have {MIN_NAMES} names in the primary band. "
            "Nothing is recorded and the window is not counted as read; check the universe first."
        )
    passed = (
        bool(prim.get("months")) and prim["net50"][0] >= PASS_NET_PCT and prim["net50"][1] >= PASS_T
    )
    result["passed"] = passed
    out.write_text(json.dumps(result, indent=1, default=str))
    print(
        f"\nPass line: primary band, net at 50 bp a side >= +{PASS_NET_PCT:.2f} %/month with t >= {PASS_T:.1f}."
    )
    print(
        f"{name.upper()}: {'PASS' if passed else 'FAIL'}. This window has now been read; the result is stored in {out.name}."
    )


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", type=Path, required=True, help="directory holding the Sharadar files")
    ap.add_argument(
        "--work", type=Path, required=True, help="an empty directory for this search's files"
    )
    ap.add_argument("mode", choices=["describe", "explore", "confirm", "exam"])
    a = ap.parse_args(argv)
    a.work.mkdir(parents=True, exist_ok=True)
    if a.mode == "describe":
        describe(a.data)
    elif a.mode == "explore":
        explore(a.data, a.work)
    elif a.mode == "confirm":
        judged(a.data, a.work, CONFIRM, "confirm")
    else:
        judged(a.data, a.work, EXAM, "exam")


if __name__ == "__main__":
    main(sys.argv[1:])
