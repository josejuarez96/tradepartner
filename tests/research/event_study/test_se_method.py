"""`[study].se_method` (#3679): the quarter-cluster t, its calibration, and the frozen default.

The null harness exercises the real functions on three designs where the true
mean spread is zero, so a well-sized t line rejects at its nominal rate: 5% at
1.645 and about 16% at 1.0. Entries are bunched into 30 of every 63 sessions,
as earnings seasons bunch them; the 63-session block stands in for the calendar
quarter. The replication count sets the band: a rate ``p`` measured over ``R``
replications has standard error ``sqrt(p (1 - p) / R)``, and the band is nominal
plus or minus three of them.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from functools import cache
from statistics import NormalDist

import numpy as np
import pytest

from tradepartner.research.event_study.stats import (
    QUARTER_CLUSTER_MIN_QUARTERS,
    SE_METHODS,
    TooFewClustersError,
    entry_quarter,
    overlap_cluster_stats,
    overlap_result_interval,
    quarter_cluster_stats,
    se_result_interval,
    student_t_cdf,
    student_t_quantile,
)
from tradepartner.research.event_study.summary import summarise

SESSIONS, HOLD, LAG, SEASON_LEN = 1200, 60, 69, 63
SEASON = np.array([s for s in range(SESSIONS - HOLD) if s % SEASON_LEN < 30])
Z1 = NormalDist().inv_cdf(1 - 0.158655)  # the t >= 1.0 line, on the normal scale
NOMINAL_1645, NOMINAL_1 = 0.05, 1 - NormalDist().cdf(1.0)


def _draw(
    rng: np.random.Generator, n: int, shock_sd: float | None
) -> tuple[list[int], list[float], list[int]]:
    entries = np.sort(rng.choice(SEASON, size=n))
    values = rng.normal(0.0, 0.12 if shock_sd is None else 0.10, n)
    if shock_sd:
        cum = np.concatenate([[0.0], np.cumsum(rng.normal(0.0, shock_sd, SESSIONS))])
        values = values + (cum[entries + HOLD] - cum[entries])
    return (
        [int(e) for e in entries],
        [float(v) for v in values],
        [int(e) // SEASON_LEN for e in entries],
    )


@cache
def _rates(design: str, reps: int) -> dict[str, tuple[float, float, int]]:
    """Rejection rates at (1.0, 1.645) and NaN count for each method, one seeded pass."""
    n, shock = {"bunched": (3000, None), "overlap": (3000, 0.01), "sparse": (60, None)}[design]
    rng = np.random.default_rng({"bunched": 101, "overlap": 202, "sparse": 505}[design])
    hh: list[float] = []
    qt: list[float] = []
    for _ in range(reps):
        entries, values, quarters = _draw(rng, n, shock)
        hh.append(overlap_cluster_stats(values, entries, max_lag_sessions=LAG)["t"])
        qt.append(quarter_cluster_stats(values, quarters, min_quarters=8)["z_eq"])
    out = {}
    for name, series in (("hansen_hodrick", hh), ("quarter_cluster_t", qt)):
        a = np.array(series)
        ok = a[~np.isnan(a)]
        out[name] = (
            float((ok > 1.0).mean()),
            float((ok > 1.645).mean()),
            int(np.isnan(a).sum()),
        )
    return out


def _band(reps: int) -> tuple[float, float]:
    half = 3 * math.sqrt(NOMINAL_1645 * (1 - NOMINAL_1645) / reps)
    return NOMINAL_1645 - half, NOMINAL_1645 + half


def test_quarter_cluster_t_is_sized_in_the_bunched_design() -> None:
    """1,500 replications: the 3-SE band is [.033, .067]; measured .048 (nominal .05)."""
    low, high = _band(1500)
    assert low <= _rates("bunched", 1500)["quarter_cluster_t"][1] <= high


def test_flat_hansen_hodrick_fails_the_same_size_test() -> None:
    """The test has teeth: today's statistic (.081 here) is outside the band."""
    _, high = _band(1500)
    assert _rates("bunched", 1500)["hansen_hodrick"][1] > high


def test_quarter_cluster_t_is_sized_with_few_events() -> None:
    """60 events, 1,500 replications; measured .055 (nominal .05), never NaN (HH: .085, 12 NaN)."""
    low, high = _band(1500)
    rates = _rates("sparse", 1500)
    assert low <= rates["quarter_cluster_t"][1] <= high
    assert rates["quarter_cluster_t"][2] == 0
    assert rates["hansen_hodrick"][2] > 0  # the audit's negative variance, pinned for old studies


def test_overlap_design_stays_mildly_liberal_but_bounded() -> None:
    """60-session holds sharing session shocks: about 6 in 100 where 5 are intended.

    Holds spill into the next season, and a 1,200-session span holds about 20
    independent 60-session holds, so no cluster-robust t is exact here. The
    measured .069 here (the auditor's seeds: .061; HH: .088) is held under .08 at
    1,500 replications; this is a ceiling, not a claim of calibration. At the 1.0
    line it measures about .199 where .159 is intended.
    """
    at_1645 = _rates("overlap", 1500)["quarter_cluster_t"]
    assert at_1645[1] <= 0.08
    assert at_1645[0] <= 0.22
    assert at_1645[2] == 0


def test_student_t_distribution_function() -> None:
    assert student_t_cdf(0.0, 7) == pytest.approx(0.5)
    assert student_t_cdf(1.0, 1) == pytest.approx(0.75)  # Cauchy
    assert student_t_cdf(2.0, 5) == pytest.approx(0.9490302606, abs=1e-9)
    assert student_t_cdf(-2.0, 5) == pytest.approx(1 - 0.9490302606, abs=1e-9)
    assert student_t_cdf(1.0, 10_000) == pytest.approx(NormalDist().cdf(1.0), abs=1e-4)
    assert student_t_quantile(0.95, 18) == pytest.approx(1.7340636, abs=1e-6)
    with pytest.raises(ValueError, match="df"):
        student_t_cdf(1.0, 0)


def test_quarter_cluster_stats_by_hand() -> None:
    values = [1.0, 3.0, 2.0, 4.0, 0.0, 6.0, 5.0, 3.0, 2.0, 4.0]
    quarters = [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]
    got = quarter_cluster_stats(values, quarters, min_quarters=5)
    mean = 3.0
    sums = [-2 + 0, -1 + 1, -3 + 3, 2 + 0, -1 + 1]  # per-quarter sums of deviations
    se = math.sqrt(sum(s * s for s in sums) * 5 / 4) / 10
    assert got["mean"] == pytest.approx(mean)
    assert got["clusters"] == 5
    assert got["se"] == pytest.approx(se)
    assert got["t"] == pytest.approx(mean / se)
    # the t(4) tail mapped to the normal scale: z_eq is smaller than t
    assert abs(got["z_eq"]) < abs(got["t"])
    assert NormalDist().cdf(got["z_eq"]) == pytest.approx(student_t_cdf(got["t"], 4))


def test_too_few_quarters_is_a_named_refusal_not_nan() -> None:
    assert QUARTER_CLUSTER_MIN_QUARTERS == 8
    with pytest.raises(TooFewClustersError, match="at least 8"):
        quarter_cluster_stats([1.0, 2.0, 3.0, 4.0], [0, 1, 2, 3], min_quarters=8)
    with pytest.raises(TooFewClustersError):
        quarter_cluster_stats([1.0, 2.0], [0, 0], min_quarters=1)


def test_z_eq_stays_finite_for_an_overwhelming_pass() -> None:
    values = [1.0 + 1e-9 * i for i in range(20)]
    got = quarter_cluster_stats(values, list(range(20)), min_quarters=8)
    assert math.isfinite(got["z_eq"])
    assert got["z_eq"] > 5


def test_entry_quarter_is_calendar() -> None:
    assert entry_quarter(date(2024, 1, 2)) == 2024 * 4
    assert entry_quarter(date(2024, 3, 29)) == 2024 * 4
    assert entry_quarter(date(2024, 4, 1)) == 2024 * 4 + 1
    assert entry_quarter(date(2024, 12, 31)) == 2024 * 4 + 3


def test_hansen_hodrick_method_is_overlap_result_interval_exactly() -> None:
    rng = np.random.default_rng(3)
    entries, values, _ = _draw(rng, 400, None)
    want = overlap_result_interval(values, entries, max_lag_sessions=LAG, z=1.645)
    got = se_result_interval(
        values, entries, None, method="hansen_hodrick", max_lag_sessions=LAG, z=1.645
    )
    assert got == want
    with pytest.raises(ValueError, match="se_method"):
        se_result_interval(values, entries, None, method="bartlett", max_lag_sessions=LAG, z=1.0)
    with pytest.raises(ValueError, match="quarter"):
        se_result_interval(
            values, entries, None, method="quarter_cluster_t", max_lag_sessions=LAG, z=1.0
        )
    assert SE_METHODS == ("hansen_hodrick", "quarter_cluster_t")


def test_quarter_interval_uses_the_t_reference() -> None:
    rng = np.random.default_rng(4)
    entries, values, quarters = _draw(rng, 500, None)
    got = se_result_interval(
        values, entries, quarters, method="quarter_cluster_t", max_lag_sessions=LAG, z=1.645
    )
    crit = student_t_quantile(NormalDist().cdf(1.645), int(got["quarters"]) - 1)
    assert crit > 1.645
    assert got["ci_high"] - got["effect"] == pytest.approx(crit * got["se"])
    assert got["t"] == got["z_eq"] and got["t_raw"] == pytest.approx(got["effect"] / got["se"])


KILL_LINES = {
    side: {
        "readings": ("raw", "badj", "sz", "szb"),
        "metric": "gross_spread_bp" if side == "long" else "net_bp",
        "floor_bp": 60.0,
        "t_floor": 1.0,
        "which_t": "min",
    }
    for side in ("long", "short")
}
REQUIRED = {
    "cluster_by": ("month", "quarter"),
    "primary_cost": 30.0,
    "short_borrow": 30.0,
    "z": 1.645,
    "peer_window": 0,
}


def _rows_with_entry() -> list[dict[str, object]]:
    """Twenty events per date, entries 20 sessions and 20 days apart (about 20 quarters)."""
    rng = random.Random(7)
    rows: list[dict[str, object]] = []
    for d in range(90):
        day = (date(2020, 1, 1) + timedelta(days=d * 20)).isoformat()
        for k in range(20):
            spread = rng.gauss(0, 1) * 0.01
            rows.append(
                {
                    "group": "early",
                    "security_id": f"s{d}_{k}",
                    "entry_index": d * 20,
                    "entry": day,
                    "acc_date": day,
                    "qidx": d // 4,
                    **{f"spread{x}_60": spread for x in ("", "_badj", "_sz", "_szb")},
                    "r_60": spread,
                    "spy_60": 0.0,
                    "beta_gap": 0.0,
                    "logmcap_gap": 0.0,
                }
            )
    return rows


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


def test_summarise_default_method_is_hansen_hodrick_byte_for_byte() -> None:
    rows = _rows_with_entry()
    # NaN != NaN, so compare the serialised form
    assert json.dumps(_run(rows)) == json.dumps(_run(rows, se_method="hansen_hodrick"))
    absent = _run(rows)
    reading = absent["kill"]["long"]["readings"]["raw"]  # type: ignore[index]
    assert "z_eq" not in reading and "quarters" not in reading


def test_summarise_quarter_method_judges_z_eq_against_the_floor() -> None:
    rows = _rows_with_entry()
    got = _run(rows, se_method="quarter_cluster_t")
    reading = got["kill"]["long"]["readings"]["raw"]  # type: ignore[index]
    assert reading["quarters"] >= 8
    assert reading["calibrated_t"] == reading["z_eq"]
    assert reading["t_min"] == reading["z_eq"]
    assert abs(reading["z_eq"]) < abs(reading["t_raw"]) or reading["t_raw"] == 0
    assert got["inference"]["z_eq"] == pytest.approx(reading["z_eq"])  # type: ignore[index]
    assert got != _run(rows)


def test_summarise_refuses_a_study_with_too_few_quarters() -> None:
    rows = [r for r in _rows_with_entry() if int(r["entry_index"]) < 600]  # type: ignore[call-overload]
    with pytest.raises(TooFewClustersError):
        _run(rows, se_method="quarter_cluster_t")
