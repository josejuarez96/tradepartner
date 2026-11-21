# ruff: noqa: E501
"""Synthetic Sharadar-shaped files for testing discover.py. No real data."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


def make(
    out: Path,
    *,
    effect: float = 0.012,
    leak: bool = False,
    seed: int = 0,
    n_primary: int = 260,
    n_secondary: int = 120,
) -> None:
    """effect: monthly return per unit of latent quality. leak=True writes fundamentals that encode the NEXT
    month's return but are dated after that month, so a correct as-of join can never use them."""
    rng = np.random.default_rng(seed)
    out.mkdir(parents=True, exist_ok=True)
    days = pd.bdate_range("1998-01-02", "2023-12-29")
    n = n_primary + n_secondary
    tick = [f"T{i:04d}" for i in range(n)]
    month = days.to_period("M")
    months = month.unique()
    q = np.cumsum(rng.normal(0, 0.15, (len(months), n)), axis=0)  # latent quality, slow
    q = (q - q.mean(1, keepdims=True)) / q.std(1, keepdims=True)
    midx = month.factorize()[0]
    per_day = 21.0
    drift = np.zeros((len(days), n))
    drift[:] = (
        effect * np.vstack([q[:1], q[:-1]])[midx] / per_day
    )  # quality known at prior month end
    r = drift + rng.normal(0, 0.02, (len(days), n))
    px = 30.0 * np.exp(np.cumsum(np.log1p(r), axis=0))
    base_dv = np.r_[rng.uniform(15e6, 150e6, n_primary), rng.uniform(250e6, 900e6, n_secondary)]
    vol = base_dv / px * rng.lognormal(0, 0.3, (len(days), n))
    alive_until = np.full(n, len(days))
    dead = rng.choice(n, n // 8, replace=False)
    alive_until[dead] = rng.integers(len(days) // 3, len(days) - 300, len(dead))
    rows = []
    for j in range(n):
        k = alive_until[j]
        rows.append(
            pd.DataFrame(
                {
                    "ticker": tick[j],
                    "date": days[:k].strftime("%Y-%m-%d"),
                    "open": px[:k, j],
                    "high": px[:k, j] * 1.01,
                    "low": px[:k, j] * 0.99,
                    "close": px[:k, j],
                    "volume": vol[:k, j],
                    "closeadj": px[:k, j],
                    "closeunadj": px[:k, j],
                }
            )
        )
    pd.concat(rows).to_csv(out / "SHARADAR_SEP.csv", index=False)
    pd.DataFrame({"table": "SEP", "ticker": tick, "category": "Domestic Common Stock"}).to_csv(
        out / "SHARADAR_TICKERS.csv", index=False
    )
    # fundamentals: quarterly ART rows; gp/assets carries quality (or, with leak, the next month's return)
    mret = pd.DataFrame(np.log1p(r)).groupby(midx).sum().to_numpy()
    f = []
    qm = [i for i, m in enumerate(months) if m.month in (3, 6, 9, 12)]
    for i in qm:
        m = months[i]
        if leak:
            if i + 2 >= len(months):
                continue
            signal = np.expm1(mret[i + 1]) * 20.0  # the month AFTER the quarter end
            datekey = (
                months[i + 2].to_timestamp(how="end")
            ).normalize()  # dated after that month: unusable
        else:
            signal = q[i] + rng.normal(0, 0.5, n)
            datekey = (m.to_timestamp(how="end") + pd.Timedelta(days=35)).normalize()
        assets = 1000.0 + rng.normal(0, 10, n)
        f.append(
            pd.DataFrame(
                {
                    "ticker": tick,
                    "dimension": "ART",
                    "calendardate": m.to_timestamp(how="end").strftime("%Y-%m-%d"),
                    "datekey": datekey.strftime("%Y-%m-%d"),
                    "assets": assets,
                    "gp": assets * (0.3 + 0.05 * signal),
                    "revenue": assets * 0.9,
                    "ncfo": assets * 0.08 + rng.normal(0, 5, n),
                    "netinc": assets * 0.05 + rng.normal(0, 5, n),
                    "equity": assets * 0.5,
                    "debt": assets * 0.3,
                    "sharesbas": 1e7,
                    "fcf": assets * 0.04 + rng.normal(0, 5, n),
                }
            )
        )
    pd.concat(f).to_csv(out / "SHARADAR_SF1.csv", index=False)
