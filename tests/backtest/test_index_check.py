"""The index check's pure arithmetic (backtest spec amendment #1598, items 1, 4, 5)."""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import date

import pytest

from tradepartner.backtest import index_check as ic

TOL = 1e-12
LEVEL_WORDS = ("return", "level", "bench")

# The spec's three-name hand case: caps 1, 1, 2.
CAPS = {"A": 1.0, "B": 1.0, "C": 2.0}
M1 = {"A": 0.10, "B": -0.10, "C": 0.04}
M2 = {"A": 0.0, "B": 0.05, "C": -0.02}
B1, B2 = 0.01, 0.02


def _calc(month: str, rets: dict[str, float], bench: float, *, protected: bool = False):
    ew = ic.equal_weights(list(CAPS))
    cw, n_no_cap = ic.cap_weights(CAPS)
    return ic.MonthCalc(
        month=month,
        protected=protected,
        n_members=3,
        n_no_cap=n_no_cap,
        equal=ic.BookReturn(ic.index_return(ew, rets), bench),
        cap=ic.BookReturn(ic.index_return(cw, rets), bench),
    )


def test_weights() -> None:
    ew = ic.equal_weights(["A", "B", "C"])
    assert ew == {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    cw, n_no_cap = ic.cap_weights(CAPS)
    assert n_no_cap == 0
    assert cw == {"A": 0.25, "B": 0.25, "C": 0.5}


def test_member_without_cap_is_left_out_and_counted() -> None:
    caps = {"A": 1.0, "B": None, "C": 3.0, "D": 0.0, "E": -2.0, "F": math.nan, "G": math.inf}
    cw, n_no_cap = ic.cap_weights(caps)
    assert cw == {"A": 0.25, "C": 0.75}
    assert n_no_cap == 5
    assert sum(cw.values()) == pytest.approx(1.0, abs=TOL)


def test_no_member_has_a_cap() -> None:
    cw, n_no_cap = ic.cap_weights({"A": None, "B": 0.0})
    assert cw == {}
    assert n_no_cap == 2


def test_name_return_from_marks() -> None:
    assert ic.name_return(100.0, 110.0) == (pytest.approx(0.10, abs=TOL), False)
    r, flag = ic.name_return(50.0, 40.0)
    assert r == pytest.approx(-0.2, abs=TOL) and flag is False


def test_name_return_no_entry_bar_is_zero_and_flagged() -> None:
    assert ic.name_return(None, None) == (0.0, True)
    assert ic.name_return(None, 12.0) == (0.0, True)


def test_delisted_member_return_is_from_its_last_mark() -> None:
    # The caller passes the last traded close as the end mark; nothing later is read.
    r, flag = ic.name_return(10.0, 7.5)
    assert r == pytest.approx(-0.25, abs=TOL) and flag is False


def test_name_return_rejects_an_entry_without_an_end() -> None:
    with pytest.raises(ValueError, match="end mark"):
        ic.name_return(10.0, None)
    with pytest.raises(ValueError, match="positive"):
        ic.name_return(0.0, 1.0)


def test_hand_case_equal_weight() -> None:
    m1, m2 = _calc("2020-01", M1, B1), _calc("2020-02", M2, B2)
    assert m1.equal.index == pytest.approx(0.04 / 3, abs=TOL)
    assert m2.equal.index == pytest.approx(0.03 / 3, abs=TOL)
    g1, g2 = ic.gap(m1.equal), ic.gap(m2.equal)
    assert g1 == pytest.approx(0.04 / 3 - 0.01, abs=TOL)  # +0.3333 pp
    assert g2 == pytest.approx(0.01 - 0.02, abs=TOL)  # -1.0000 pp
    year = (76 / 75) * 1.01 - 1.01 * 1.02
    assert ic.year_gap([m1.equal, m2.equal]) == pytest.approx(year, abs=TOL)
    assert round(year * 100, 4) == -0.6733
    te = ic.tracking_error([g1, g2])
    assert te == pytest.approx(abs(g1 - g2) / math.sqrt(2) * math.sqrt(12), abs=TOL)
    assert round((te or 0) * 100, 3) == 3.266


def test_hand_case_cap_weight() -> None:
    m1, m2 = _calc("2020-01", M1, B1), _calc("2020-02", M2, B2)
    assert m1.cap.index == pytest.approx(0.02, abs=TOL)
    assert m2.cap.index == pytest.approx(0.0025, abs=TOL)
    g1, g2 = ic.gap(m1.cap), ic.gap(m2.cap)
    assert g1 == pytest.approx(0.01, abs=TOL)
    assert g2 == pytest.approx(-0.0175, abs=TOL)
    year = 1.02 * 1.0025 - 1.01 * 1.02
    assert ic.year_gap([m1.cap, m2.cap]) == pytest.approx(year, abs=TOL)
    assert round(year * 100, 4) == -0.765
    te = ic.tracking_error([g1, g2])
    assert te == pytest.approx(0.0275 / math.sqrt(2) * math.sqrt(12), abs=TOL)
    assert round((te or 0) * 100, 3) == 6.736


def test_contributions_month_one_equal_weight() -> None:
    ew = ic.equal_weights(["A", "B", "C"])
    c = ic.contributions(ew, M1, B1)
    assert c["A"] == pytest.approx(0.03, abs=TOL)
    assert c["B"] == pytest.approx(-0.11 / 3, abs=TOL)  # -3.6667 pp
    assert c["C"] == pytest.approx(0.01, abs=TOL)
    gap = ic.index_return(ew, M1) - B1
    assert sum(c.values()) == pytest.approx(gap, abs=TOL)


def test_contributions_sum_to_the_cap_gap_over_the_cap_book() -> None:
    cw, _ = ic.cap_weights({"A": 1.0, "B": None, "C": 2.0})
    c = ic.contributions(cw, M1, B1)
    assert set(c) == {"A", "C"}
    assert sum(c.values()) == pytest.approx(ic.index_return(cw, M1) - B1, abs=TOL)


def test_tracking_error_blank_with_fewer_than_two_months() -> None:
    assert ic.tracking_error([]) is None
    assert ic.tracking_error([0.01]) is None


def test_year_rows_one_month_year_has_blank_te() -> None:
    rows = ic.year_rows([_calc("2020-12", M1, B1), _calc("2021-01", M2, B2)])
    assert [r.year for r in rows] == [2020, 2021]
    assert all(r.equal_te is None and r.cap_te is None for r in rows)
    assert all(isinstance(r, ic.DevYearRow) for r in rows)


def test_zone_totals_split_development_from_protected() -> None:
    months = [
        _calc("2020-01", M1, B1),
        _calc("2020-02", M2, B2, protected=True),
        _calc("2020-03", M1, B1, protected=True),
    ]
    dev = ic.zone_total(months, protected=False)
    prot = ic.zone_total(months, protected=True)
    assert isinstance(dev, ic.DevYearRow) and dev.months == 1
    assert isinstance(prot, ic.ProtectedYearRow) and prot.months == 2
    g2, g3 = ic.gap(months[1].equal), ic.gap(months[2].equal)
    assert prot.equal_gap == pytest.approx((1 + 0.01) * (1 + 0.04 / 3) - (1.02 * 1.01), abs=TOL)
    assert prot.equal_te == pytest.approx(ic.tracking_error([g2, g3]), abs=TOL)
    assert ic.zone_total(months[:1], protected=True) is None


def test_a_year_with_any_protected_month_is_protected() -> None:
    rows = ic.year_rows([_calc("2020-01", M1, B1), _calc("2020-02", M2, B2, protected=True)])
    assert len(rows) == 1 and isinstance(rows[0], ic.ProtectedYearRow)
    assert rows[0].months == 2


def test_worst_months_by_absolute_gap_both_signs_ties_by_month() -> None:
    gaps = {"2020-03": -0.05, "2020-01": 0.05, "2020-02": 0.01, "2020-04": -0.02}
    assert ic.worst_months(gaps, 3) == ["2020-01", "2020-03", "2020-04"]
    assert ic.worst_months(gaps, 0) == []
    assert ic.worst_months(gaps, 99) == ["2020-01", "2020-03", "2020-04", "2020-02"]


def test_listed_names_top_by_abs_contribution_then_extremes_ties_by_security_id() -> None:
    c = {"s3": 0.02, "s1": -0.02, "s2": 0.02, "s4": 0.001, "s5": 0.0005}
    r = {"s1": 0.1, "s2": 0.1, "s3": 0.1, "s4": 0.7, "s5": -0.6}
    # top 2 by |c|: ties s1, s2, s3 broken by id; extremes s4 and s5 added.
    assert ic.listed_names(c, r, names_per_month=2, extreme_name_return=0.6) == [
        "s1",
        "s2",
        "s4",
        "s5",
    ]
    assert ic.listed_names(c, r, names_per_month=1, extreme_name_return=2.0) == ["s1"]


def test_protected_edge_is_the_earlier_of_the_two_sources() -> None:
    boundary = date(2023, 12, 29)
    e = ic.protected_edge(boundary, [date(2025, 1, 1), date(2024, 1, 1)])
    assert e == ic.ProtectedEdge(date(2023, 12, 29), "development_boundary")
    e = ic.protected_edge(date(2024, 6, 3), [date(2024, 1, 2)])
    assert e == ic.ProtectedEdge(date(2024, 1, 1), "holdout_start")
    e = ic.protected_edge(None, [date(2024, 1, 2), date(2025, 1, 2)])
    assert e == ic.ProtectedEdge(date(2024, 1, 1), "holdout_start")
    e = ic.protected_edge(boundary, [])
    assert e == ic.ProtectedEdge(boundary, "development_boundary")


def test_every_month_is_protected_with_neither_source() -> None:
    edge = ic.protected_edge(None, [])
    assert edge.date is None and edge.source is None
    assert ic.is_protected(date(1990, 1, 31), edge)


def test_is_protected_is_strictly_after_the_edge() -> None:
    edge = ic.protected_edge(date(2023, 12, 29), [])
    assert not ic.is_protected(date(2023, 12, 29), edge)
    assert ic.is_protected(date(2024, 1, 31), edge)


def _levelish(name: str) -> bool:
    return any(w in name for w in LEVEL_WORDS)


def test_protected_rows_carry_no_level_field_or_json_key() -> None:
    protected_types = (
        ic.ProtectedMonthRow,
        ic.ProtectedYearRow,
        ic.ProtectedNameRow,
    )
    for typ in protected_types:
        names = [f.name for f in dataclasses.fields(typ)]
        assert not [n for n in names if _levelish(n)], (typ, names)
    month = ic.month_row(_calc("2024-01", M1, B1, protected=True))
    year = ic.year_rows([_calc("2024-01", M1, B1, protected=True)])[0]
    name = ic.name_row("A", "AAA", 0.03, 0.1, protected=True)
    for row in (month, year, name):
        text = json.dumps(ic.row_dict(row))
        keys = json.loads(text).keys()
        assert not [k for k in keys if _levelish(k)], keys
        assert json.loads(text)["protected"] is True
    assert isinstance(month, ic.ProtectedMonthRow)
    assert ic.row_dict(month)["equal_gap"] == pytest.approx(0.04 / 3 - 0.01, abs=TOL)


def test_development_rows_show_levels() -> None:
    month = ic.month_row(_calc("2020-01", M1, B1))
    assert isinstance(month, ic.DevMonthRow)
    d = ic.row_dict(month)
    assert d["protected"] is False
    assert d["equal_return"] == pytest.approx(0.04 / 3, abs=TOL)
    assert d["equal_bench_return"] == B1
    name = ic.name_row("A", "AAA", 0.03, 0.1, protected=False)
    assert isinstance(name, ic.DevNameRow) and name.name_return == 0.1
    assert ic.row_dict(name)["name_return"] == 0.1


def test_year_gap_of_no_months_is_zero_and_empty_rejects_rows() -> None:
    assert ic.year_gap([]) == 0.0
    assert ic.year_rows([]) == []


def test_index_return_refuses_an_empty_or_unnormalised_book() -> None:
    with pytest.raises(ValueError, match="sum to 1"):
        ic.index_return({}, {})
    with pytest.raises(ValueError, match="sum to 1"):
        ic.index_return({"A": 0.5}, {"A": 0.1})
    cw, _ = ic.cap_weights({"A": None})
    with pytest.raises(ValueError, match="sum to 1"):
        ic.index_return(cw, {})


def test_equal_weights_refuse_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ic.equal_weights(["A", "B", "A"])
    assert ic.equal_weights([]) == {}
