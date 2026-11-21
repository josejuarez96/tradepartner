"""Shape and effective-count arithmetic (T441, #3741): the spec's cells, and purity."""

from __future__ import annotations

import ast
import math
from collections.abc import Callable
from pathlib import Path

import pytest

from tradepartner.research import shape

SHAPE_PATH = Path(shape.__file__)

# IRRX (PR #3683): gross 29 bp a month, sigma 140, 84 months, borrow 1.25 bp a month.
IRRX_GROSS = 29.0
IRRX_SIGMA = 140.0
IRRX_BORROW = 1.25


def _irrx(turnover: float) -> float:
    rt = shape.trial_round_trip_bp(turnover, 50.0, IRRX_BORROW)
    return shape.trial_pass_probability(IRRX_GROSS, rt, IRRX_SIGMA, 84)


def test_trial_form_reproduces_pr3683_cells() -> None:
    assert _irrx(1.0) == pytest.approx(1.1e-06, rel=0.05)
    assert _irrx(0.7) == pytest.approx(0.0028, rel=0.02)


def test_event_form_reproduces_pr3683_cell() -> None:
    p = shape.event_pass_probability(27.5, 100.0, 1500.0, 1000.0)
    assert p == pytest.approx(0.0057, abs=5e-5)


def test_event_form_worked_example_is_eighty_percent() -> None:
    sd = shape.default_event_sd_bp(20)
    assert sd == pytest.approx(866.0254, abs=1e-3)
    p = shape.event_pass_probability(200.0, 100.0, sd, 254.4)
    assert p == pytest.approx(0.80, abs=0.002)


def test_min_counts_at_ratio_two() -> None:
    assert shape.event_min_count(200.0, 100.0, 866.0) == 255
    assert shape.event_min_count(200.0, 100.0, 1500.0) == 764
    assert shape.event_min_count(200.0, 100.0, shape.default_event_sd_bp(20)) == 255
    assert shape.event_min_count(200.0, 100.0, shape.default_event_sd_bp(60)) == 764


@pytest.mark.parametrize(
    ("gross", "n20", "n60"),
    [(140.0, 5313, 15938), (150.0, 1329, 3985), (300.0, 64, 191)],
)
def test_min_counts_match_the_spec_table(gross: float, n20: int, n60: int) -> None:
    assert shape.event_min_count(gross, 100.0, shape.default_event_sd_bp(20)) == n20
    assert shape.event_min_count(gross, 100.0, shape.default_event_sd_bp(60)) == n60


@pytest.mark.parametrize("gross", [130.0, 100.0, 0.0, -5.0])
def test_min_count_undefined_at_or_under_the_floor(gross: float) -> None:
    assert shape.event_min_count(gross, 100.0, 866.0) is None


def test_min_count_reaches_its_power() -> None:
    n = shape.event_min_count(200.0, 100.0, 866.0)
    assert n is not None
    assert shape.event_pass_probability(200.0, 100.0, 866.0, n) >= 0.80
    assert shape.event_pass_probability(200.0, 100.0, 866.0, n - 1) < 0.80


def test_floor_binds_when_it_is_the_tighter_leg() -> None:
    # net 40 with F 30: the (net - F)/z leg binds over net/(t_f + z).
    n = shape.event_min_count(140.0, 100.0, 866.0)
    z = shape.z_power()
    assert n == math.ceil((866.0 * z / 10.0) ** 2)


def test_z80_and_rounding_up() -> None:
    assert shape.z_power() == pytest.approx(0.8416, abs=5e-5)
    assert shape.z_power(0.5) == 0.0
    # An exact integer count is not pushed up by float noise.
    assert shape.event_min_count(200.0, 100.0, 1000.0 / (1 + shape.z_power())) == 100


def test_round_trip_legs_and_defaults() -> None:
    assert shape.event_round_trip_bp() == 100.0
    assert shape.event_round_trip_bp(50.0, 2) == 200.0
    assert shape.trial_round_trip_bp(1.0, 50.0) == 100.0


def test_trial_required_net_ir() -> None:
    assert shape.trial_required_net_ir(40) == pytest.approx(0.46, abs=0.005)
    assert shape.trial_required_net_ir(84) == pytest.approx(0.32, abs=0.005)
    assert shape.trial_required_net_ir(40, sr_star_annual=1.2) == pytest.approx(1.66, abs=0.01)


def test_trial_spec_example_passes_the_power_line() -> None:
    p = shape.trial_pass_probability(75.0, 50.0, 140.0, 40)
    assert shape.trial_net_ir(25.0, 140.0) == pytest.approx(0.62, abs=0.005)
    assert p > 0.80


def test_trial_bar_moves_with_sr_star() -> None:
    p0 = shape.trial_pass_probability(75.0, 50.0, 140.0, 40)
    p1 = shape.trial_pass_probability(75.0, 50.0, 140.0, 40, sr_star_annual=1.28)
    assert p1 < p0
    assert shape.trial_bar_bp(1.2, 140.0) == pytest.approx(1.2 * 140.0 / math.sqrt(12))


@pytest.mark.parametrize(
    "call",
    [
        lambda: shape.event_pass_probability(200, 100, 0, 10),
        lambda: shape.event_pass_probability(200, 100, 866, 0),
        lambda: shape.event_min_count(200, 100, 0),
        lambda: shape.trial_pass_probability(29, 100, 140, 0),
        lambda: shape.trial_pass_probability(29, 100, 0, 84),
        lambda: shape.default_event_sd_bp(0),
        lambda: shape.event_round_trip_bp(50, 0),
        lambda: shape.effective_count([], 20),
        lambda: shape.effective_count([("a", 0)], 0),
        lambda: shape.effective_count([("a", 0)], 20, rho=1.5),
    ],
)
def test_bad_inputs_are_refused(call: Callable[[], object]) -> None:
    with pytest.raises(ValueError):
        call()


# --- Effective count ---


def test_default_is_one_cluster_per_entry_session() -> None:
    # Sessions with 3, 1 and 2 events, every event a different issuer: N^2 / sum m_d^2.
    pairs = [(0, 0), (0, 1), (0, 2), (100, 0), (200, 0), (200, 1)]
    ec = shape.effective_count([(f"{d}-{k}", d) for d, k in pairs], 20)
    assert ec.n == 6
    assert ec.n_eff == pytest.approx(36 / (9 + 1 + 4))
    assert ec.same_date_factor == pytest.approx(14 / 6)
    assert ec.overlap_factor == pytest.approx(1.0)
    assert ec.n_eff == pytest.approx(ec.n / ec.same_date_factor / ec.overlap_factor)
    assert ec.rho is None


@pytest.mark.parametrize("m", [1, 2, 5, 40])
@pytest.mark.parametrize("rho", [0.0, 0.25, 0.9, 1.0])
def test_cited_rho_on_one_session(m: int, rho: float) -> None:
    ec = shape.effective_count([(f"i{k}", 7) for k in range(m)], 20, rho=rho)
    assert ec.n_eff == pytest.approx(m / (1 + (m - 1) * rho))


def test_rho_zero_with_no_shared_issuer_is_n() -> None:
    events = [(f"i{k}", k) for k in range(50)]  # adjacent sessions, distinct issuers
    ec = shape.effective_count(events, 20, rho=0.0)
    assert ec.n_eff == pytest.approx(50.0)
    assert ec.n_eff_rho0 == pytest.approx(50.0)


def test_one_issuer_sixty_session_holds_spaced_twenty_is_n_over_three() -> None:
    n = 3000
    ec = shape.effective_count([("A", 20 * k) for k in range(n)], 60)
    assert ec.n_eff == pytest.approx(n / 3, rel=2e-3)
    assert ec.overlap_factor == pytest.approx(3.0, rel=2e-3)
    assert ec.same_date_factor == pytest.approx(1.0)


def test_same_issuer_same_session_is_one_cluster() -> None:
    ec = shape.effective_count([("A", 0), ("A", 0), ("B", 0)], 20)
    assert ec.n_eff == pytest.approx(1.0)


def test_bounds_and_default_match_the_pairwise_definition() -> None:
    events = [("A", 0), ("B", 0), ("A", 5), ("C", 12), ("B", 30), ("D", 31)]
    hold = 20

    def overlap(a: int, b: int) -> float:
        return max(0, hold - abs(a - b)) / hold

    def n_eff(rho: float | None) -> float:
        total = 0.0
        for a, i in enumerate(events):
            for b, j in enumerate(events):
                if a == b:
                    total += 1.0
                elif i[0] == j[0]:
                    total += overlap(i[1], j[1])
                elif rho is None:
                    total += 1.0 if i[1] == j[1] else 0.0
                else:
                    total += rho * overlap(i[1], j[1])
        return len(events) ** 2 / total

    ec = shape.effective_count(events, hold, rho=0.4)
    assert ec.n_eff == pytest.approx(n_eff(0.4))
    assert ec.n_eff_rho0 == pytest.approx(n_eff(0.0))
    assert ec.n_eff_rho1 == pytest.approx(n_eff(1.0))
    assert shape.effective_count(events, hold).n_eff == pytest.approx(n_eff(None))
    assert ec.n_eff_rho1 <= ec.n_eff <= ec.n_eff_rho0


def test_open_question_2_comparison() -> None:
    """One event a session, distinct issuers, 20-session holds: default vs rho = 1."""
    ec = shape.effective_count([(f"i{k}", k) for k in range(1000)], 20)
    assert ec.n_eff == pytest.approx(1000.0)  # the default sees no shared session
    assert ec.n_eff_rho1 == pytest.approx(50.0, rel=0.02)  # N / (1 + hold - 1) edge-corrected


# --- Purity ---

ALLOWED_ROOTS = frozenset({"__future__", "math", "collections", "dataclasses", "statistics"})
FORBIDDEN_ROOTS = frozenset(
    {
        "sqlite3",
        "duckdb",
        "pathlib",
        "os",
        "io",
        "socket",
        "subprocess",
        "requests",
        "httpx",
        "urllib",
        "datetime",
        "time",
        "random",
        "tomllib",
        "json",
        "shutil",
        "numpy",
        "pandas",
        "scipy",
    }
)
FORBIDDEN_TRADEPARTNER = frozenset(
    {"store", "config", "config_backtest", "execution", "adapters", "backtest", "data"}
)


def _imports(source: str) -> set[tuple[str, ...]]:
    out: set[tuple[str, ...]] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out.update(tuple(a.name.split(".")) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "relative import in a pure module"
            out.add(tuple((node.module or "").split(".")))
    return out


def test_module_is_pure() -> None:
    imports = _imports(SHAPE_PATH.read_text(encoding="utf-8"))
    roots = {i[0] for i in imports}
    assert roots <= ALLOWED_ROOTS
    assert not roots & FORBIDDEN_ROOTS
    internal = {i[1] for i in imports if i[0] == "tradepartner" and len(i) > 1}
    assert not internal & FORBIDDEN_TRADEPARTNER


def test_purity_detector_catches_forbidden_imports() -> None:
    bad = "import sqlite3\nfrom tradepartner.store import x\nfrom datetime import date\n"
    imports = _imports(bad)
    assert {i[0] for i in imports} & FORBIDDEN_ROOTS == {"sqlite3", "datetime"}
    assert ("tradepartner", "store") in imports
