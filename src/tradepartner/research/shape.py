"""Shape and effective-count arithmetic for the idea card (pure).

The closed forms of the idea-pipeline spec's 2026-11-21 amendment (#3702),
section B, and the effective-count rule of the research-registry spec's
feasibility amendment of the same date, item 4. Every input is an argument:
this module reads no store, no clock and no config, so the feasibility command
(T442 to T444) and the card print the same numbers from the same functions.

Units are basis points (bp) unless a name says otherwise. Entry sessions are
integer positions on the trading calendar (the caller's calendar, never
"weekdays"), so overlap is measured in sessions.

Every default below is the spec's and is overridable by argument. Each is
UNGRADED (PR #3683's assumptions) and a card that uses one says so.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass
from statistics import NormalDist

# --- Defaults (idea-pipeline amendment 2026-11-21, section B, "Defaults") ---

DEFAULT_FLOOR_BP = 30.0  # F: the E1 design's net floor, bp per event.
DEFAULT_T_FLOOR = 1.0  # t_f: the E1 design's t floor.
DEFAULT_POWER = 0.80  # Section A item 5: the power line is 80%.
DEFAULT_SD_BP_AT_REF_HOLD = 1500.0  # Event sd, bp, at the reference hold (E1 design).
DEFAULT_SD_REF_HOLD_SESSIONS = 60  # The hold that figure is quoted at.
DEFAULT_SIGMA_LONG_SHORT_BP = 140.0  # Monthly sd, 50/50 long/short book (claim SH-4).
DEFAULT_SIGMA_LONG_ONLY_BP = 231.0  # Monthly sd, long-only vs SPY: 8% a year (Audit B, B3).
DEFAULT_SR_STAR_NEW_ROOT_FAMILY = 0.0  # Annual DSR bar for a new root family today (#3571).
DEFAULT_LEGS_CHARGED = 1  # E1 design: only the treated leg pays (R = 2c).
DEFAULT_DECISION_COST_BP = 50.0  # c: ADR 0023's harsh cell, bp per side (decision 1).

MONTHS_PER_YEAR = 12  # A trial is monthly.
_ROUND_UP_TOLERANCE = 1e-9  # float noise that must not push an exact count up by one.

_NORMAL = NormalDist()


def phi(x: float) -> float:
    """Standard normal distribution function Phi."""
    return _NORMAL.cdf(x)


def phi_inv(p: float) -> float:
    """Inverse standard normal distribution function Phi^-1, for p in (0, 1)."""
    return _NORMAL.inv_cdf(p)


def z_power(power: float = DEFAULT_POWER) -> float:
    """``z80`` for the default power: Phi^-1(power) (0.8416 at 0.80)."""
    return phi_inv(power)


def default_event_sd_bp(
    hold_sessions: int,
    *,
    sd_at_ref_bp: float = DEFAULT_SD_BP_AT_REF_HOLD,
    ref_hold_sessions: int = DEFAULT_SD_REF_HOLD_SESSIONS,
) -> float:
    """Default per-event sd: ``sd_at_ref_bp * sqrt(hold / ref_hold)`` (866 bp at 20 sessions)."""
    if hold_sessions < 1:
        raise ValueError("hold_sessions must be at least 1")
    return sd_at_ref_bp * math.sqrt(hold_sessions / ref_hold_sessions)


# --- The round trip ---


def event_round_trip_bp(
    decision_cost_bp: float = DEFAULT_DECISION_COST_BP,
    legs_charged: int = DEFAULT_LEGS_CHARGED,
) -> float:
    """Event round trip ``R = 2 * c`` per leg the experiment file charges."""
    if legs_charged < 1:
        raise ValueError("legs_charged must be at least 1")
    return 2.0 * decision_cost_bp * legs_charged


def trial_round_trip_bp(
    turnover_per_month: float,
    decision_cost_bp: float = DEFAULT_DECISION_COST_BP,
    borrow_bp_per_month: float = 0.0,
) -> float:
    """Trial round trip: ``2 * turnover per month * c`` plus borrow, bp per month on equity."""
    if turnover_per_month < 0:
        raise ValueError("turnover_per_month must not be negative")
    return 2.0 * turnover_per_month * decision_cost_bp + borrow_bp_per_month


# --- Event form ---


def _require_positive(name: str, value: float) -> None:
    if not value > 0:
        raise ValueError(f"{name} must be positive")


def event_pass_probability(
    gross_bp: float,
    round_trip_bp: float,
    sd_bp: float,
    n_eff: float,
    *,
    floor_bp: float = DEFAULT_FLOOR_BP,
    t_floor: float = DEFAULT_T_FLOOR,
) -> float:
    """Event form: ``Phi((net - bar) / SE)`` with ``net = g - R``, ``SE = sd/sqrt(n_eff)``.

    ``bar = max(F, t_f*SE)``.
    """
    _require_positive("sd_bp", sd_bp)
    _require_positive("n_eff", n_eff)
    se = sd_bp / math.sqrt(n_eff)
    bar = max(floor_bp, t_floor * se)
    return phi((gross_bp - round_trip_bp - bar) / se)


def event_min_count(
    gross_bp: float,
    round_trip_bp: float,
    sd_bp: float,
    *,
    power: float = DEFAULT_POWER,
    floor_bp: float = DEFAULT_FLOOR_BP,
    t_floor: float = DEFAULT_T_FLOOR,
) -> int | None:
    """Smallest effective count with ``power`` chance of a pass, rounded up.

    ``SE_max = min(net/(t_f + z), (net - F)/z)``, ``n_min = (sd/SE_max)^2``.
    ``None`` when ``net <= F``: no count reaches even 50% (the reject case).
    """
    _require_positive("sd_bp", sd_bp)
    net = gross_bp - round_trip_bp
    if net <= floor_bp:
        return None
    z = z_power(power)
    se_max = min(net / (t_floor + z), (net - floor_bp) / z)
    return math.ceil((sd_bp / se_max) ** 2 - _ROUND_UP_TOLERANCE)


# --- Trial form ---


def trial_bar_bp(sr_star_annual: float, sigma_bp: float) -> float:
    """Monthly bar ``b = SR* * sigma / sqrt(12)``, bp (0 for a new root family today)."""
    return sr_star_annual * sigma_bp / math.sqrt(MONTHS_PER_YEAR)


def trial_net_ir(net_bp: float, sigma_bp: float) -> float:
    """Annual net information ratio ``net * sqrt(12) / sigma``."""
    _require_positive("sigma_bp", sigma_bp)
    return net_bp * math.sqrt(MONTHS_PER_YEAR) / sigma_bp


def trial_pass_probability(
    gross_bp: float,
    round_trip_bp: float,
    sigma_bp: float,
    months: int,
    *,
    sr_star_annual: float = DEFAULT_SR_STAR_NEW_ROOT_FAMILY,
) -> float:
    """Trial form: ``Phi((net - b) * sqrt(T) / sigma)``, ``net = g - R`` bp per month."""
    _require_positive("sigma_bp", sigma_bp)
    if months < 1:
        raise ValueError("months must be at least 1")
    net = gross_bp - round_trip_bp
    return phi((net - trial_bar_bp(sr_star_annual, sigma_bp)) * math.sqrt(months) / sigma_bp)


def trial_required_net_ir(
    months: int,
    *,
    sr_star_annual: float = DEFAULT_SR_STAR_NEW_ROOT_FAMILY,
    power: float = DEFAULT_POWER,
) -> float:
    """Net IR for ``power``: ``SR* + z / sqrt(years)`` (0.46 on 40 months, 0.32 on 84, SR* 0)."""
    if months < 1:
        raise ValueError("months must be at least 1")
    return sr_star_annual + z_power(power) / math.sqrt(months / MONTHS_PER_YEAR)


# --- Effective count ---


@dataclass(frozen=True)
class EffectiveCount:
    """Effective count of overlapping events for one band and hold.

    ``n_eff`` is the deciding figure (default clusters, or the cited rho);
    ``n_eff_rho0`` and ``n_eff_rho1`` are the two bounds of the cited form.
    ``same_date_factor`` (B) and ``overlap_factor`` (O) satisfy
    ``n_eff = n / B / O``; ``rho`` is ``None`` under the default.
    """

    n: int
    n_eff: float
    n_eff_rho0: float
    n_eff_rho1: float
    same_date_factor: float
    overlap_factor: float
    rho: float | None


def _ordered_pair_overlap(counts: Mapping[int, int], hold: int) -> float:
    """Sum of ``o_ij`` over ordered pairs ``i != j`` of events with these per-session counts."""
    total = 0.0
    for d, m in counts.items():
        total += m * (m - 1)
        for k in range(1, hold):
            e = counts.get(d + k)
            if e:
                total += 2.0 * m * e * (hold - k) / hold
    return total


def effective_count(
    events: Iterable[tuple[Hashable, int]],
    hold_sessions: int,
    *,
    rho: float | None = None,
) -> EffectiveCount:
    """``n_eff = N^2 / sum_i sum_j w_ij`` for ``(issuer, entry_session)`` events.

    ``entry_session`` is an integer trading-calendar position. ``o_ij`` is the
    share of the hold two events have in common, ``max(0, h - |delta|) / h``. Same
    issuer: ``w_ij = o_ij``. Different issuers: by default ``w_ij = 1`` on a
    shared entry session, else 0 (one cluster per entry session); with a cited
    ``rho``, ``w_ij = rho * o_ij``.
    """
    if hold_sessions < 1:
        raise ValueError("hold_sessions must be at least 1")
    if rho is not None and not 0.0 <= rho <= 1.0:
        raise ValueError("rho must be in [0, 1]")
    by_session: Counter[int] = Counter()
    by_issuer: defaultdict[Hashable, Counter[int]] = defaultdict(Counter)
    by_session_issuer: Counter[tuple[int, Hashable]] = Counter()
    for issuer, session in events:
        by_session[session] += 1
        by_issuer[issuer][session] += 1
        by_session_issuer[(session, issuer)] += 1
    n = sum(by_session.values())
    if n == 0:
        raise ValueError("events must not be empty")

    all_pairs = _ordered_pair_overlap(by_session, hold_sessions)
    same_issuer = sum(_ordered_pair_overlap(c, hold_sessions) for c in by_issuer.values())
    # Different-issuer ordered pairs on one session: all pairs there minus the same-issuer ones.
    same_session_other = sum(m * (m - 1) for m in by_session.values()) - sum(
        c * (c - 1) for c in by_session_issuer.values()
    )

    default_sum = n + same_issuer + same_session_other
    rho0_sum = n + same_issuer
    rho1_sum = n + all_pairs
    cited_sum = n + same_issuer + (rho * (all_pairs - same_issuer) if rho is not None else 0.0)

    session_only_sum = sum(m * m for m in by_session.values())
    n_eff_dates = n * n / session_only_sum
    deciding = n * n / (default_sum if rho is None else cited_sum)
    return EffectiveCount(
        n=n,
        n_eff=deciding,
        n_eff_rho0=n * n / rho0_sum,
        n_eff_rho1=n * n / rho1_sum,
        same_date_factor=n / n_eff_dates,
        overlap_factor=n_eff_dates / deciding,
        rho=rho,
    )
