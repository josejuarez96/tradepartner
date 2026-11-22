"""`irrx_reversal`, pure (plan T415b; backtest spec amendment 2026-11-16 (#3504), items 4
to 6 and the acceptance "Signal on a hand fixture").

The fixture: three industries (SIC major groups 20, 35, 73) of four names, a five-session
formation window ending Fri 2021-12-31 (the read session T), a base bar on 2021-12-23
(2021-12-24 was a holiday). Every close is built from the growth ratios below, so a
session's return is the ratio less one and a product of ratios is a compounded return.
The window and the other thresholds are `IrrxConfig` arguments, here shrunk.
"""

from __future__ import annotations

import ast
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

from tradepartner import calendar as xnys
from tradepartner.backtest import signals_irrx
from tradepartner.backtest.signals_irrx import IrrxSignal, irrx_reversal
from tradepartner.config_backtest import IrrxConfig

NY = ZoneInfo("America/New_York")
T_DAY = date(2021, 12, 31)
T = xnys.session_close(T_DAY)
BASE = date(2021, 12, 23)
SESSIONS = [date(2021, 12, d) for d in (27, 28, 29, 30, 31)]
KNOWN = datetime(2021, 12, 1, tzinfo=UTC)
SIC_DIGITS = 4  # the SIC length: an argument of the signal, not module code

CFG = IrrxConfig(
    lookback_sessions=5,
    min_kept_sessions=2,
    announce_post_sessions=2,
    min_industry_names=2,
    min_dollar_volume=1_000_000.0,
    adv_sessions=5,
    min_adv_bars=4,
)

GROWTH: dict[str, list[float]] = {
    "A1": [1.01, 0.98, 1.02, 0.99, 1.03],
    "A2": [1.00, 1.01, 0.99, 1.02, 1.00],
    "A3": [0.99, 1.00, 1.01, 1.00, 0.98],
    "A4": [1.02, 1.02, 0.97, 1.01, 1.01],
    "B1": [0.97, 0.99, 0.98, 1.01, 0.96],
    "B2": [1.01, 1.03, 1.00, 0.99, 1.02],
    "B3": [1.00, 0.98, 1.02, 1.03, 0.99],
    "B4": [1.02, 1.01, 1.01, 0.98, 1.00],
    "C1": [1.05, 1.01, 0.99, 1.00, 1.02],
    "C2": [0.98, 0.97, 1.01, 1.02, 0.99],
    "C3": [1.01, 1.00, 0.98, 0.99, 1.04],
    "C4": [1.00, 1.02, 1.03, 1.01, 0.97],
}
SIC: dict[str, int | None] = {
    **{f"A{i}": 2000 + i for i in range(1, 5)},
    **{f"B{i}": 3570 + i for i in range(1, 5)},
    **{f"C{i}": 7370 + i for i in range(1, 5)},
}
A2_DROPPED = {date(2021, 12, d) for d in (28, 29, 30)}


def _ny(y: int, mo: int, d: int, h: int, mi: int, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=NY).astimezone(UTC)


def _bars(growth: dict[str, list[float]], *, raw: bool = False) -> pl.DataFrame:
    rows: list[dict[str, object]] = []
    for sid, ratios in growth.items():
        close = 100.0
        path = [(BASE, close)]
        for s, g in zip(SESSIONS, ratios, strict=True):
            close *= g
            path.append((s, close))
        for s, c in path:
            row: dict[str, object] = {"security_id": sid, "session": s, "close": c}
            row["known_at"] = KNOWN
            if raw:
                row["close"] = 100.0
                row["volume"] = 20_000.0
            rows.append(row)
    return pl.DataFrame(rows, schema_overrides={"known_at": pl.Datetime("us", "UTC")})


def _event(
    sid: str,
    when: datetime,
    *,
    index: datetime | None = None,
    form: str = "8-K",
    items: str = "2.02,9.01",
) -> dict[str, object]:
    return {
        "security_id": sid,
        "form": form,
        "items": items,
        "accepted_at": when,
        "index_accepted_at": when if index is None else index,
    }


def _events(rows: list[dict[str, object]]) -> pl.DataFrame:
    schema = {
        "security_id": pl.String,
        "form": pl.String,
        "items": pl.String,
        "accepted_at": pl.Datetime("us", "UTC"),
        "index_accepted_at": pl.Datetime("us", "UTC"),
    }
    return pl.DataFrame(rows, schema=schema)


def _old_events(ids: list[str]) -> list[dict[str, object]]:
    """A pre-open 2.02 on 2021-11-10 for each name: coverage, and no dropped session in
    the formation window."""
    return [_event(sid, _ny(2021, 11, 10, 8, 0)) for sid in ids]


def _call(
    growth: dict[str, list[float]],
    events: list[dict[str, object]],
    *,
    sics: dict[str, int | None] | None = None,
    t: datetime = T,
    adjusted: pl.DataFrame | None = None,
    raw: pl.DataFrame | None = None,
    ids: list[str] | None = None,
) -> IrrxSignal:
    return irrx_reversal(
        _bars(growth) if adjusted is None else adjusted,
        _bars(growth, raw=True) if raw is None else raw,
        SIC if sics is None else sics,
        _events(events),
        t,
        security_ids=list(growth) if ids is None else ids,
        config=CFG,
        sic_digits=SIC_DIGITS,
    )


def _fixture_events() -> list[dict[str, object]]:
    # A2 announces 2021-12-28 10:00 New York: s0 = 12-28, dropped 12-28, 12-29, 12-30.
    return [*_old_events(list(GROWTH)), _event("A2", _ny(2021, 12, 28, 10, 0))]


def _reference(
    growth: dict[str, list[float]], dropped: dict[str, set[date]], min_ind: int, min_kept: int
) -> dict[str, float]:
    """Plain-loop restatement of item 6's rules 2 and 3 for the fixture's names."""
    out: dict[str, float] = {}
    for sid, ratios in growth.items():
        own, ind_growth = 1.0, 1.0
        kept = 0
        for k, s in enumerate(SESSIONS):
            if s in dropped.get(sid, set()):
                continue
            peers = [
                growth[o][k]
                for o in growth
                if o != sid and o[0] == sid[0] and s not in dropped.get(o, set())
            ]
            if len(peers) < min_ind:
                continue
            own *= ratios[k]
            ind_growth *= sum(peers) / len(peers)
            kept += 1
        if kept >= min_kept:
            out[sid] = -(own - ind_growth)
    return out


def test_scores_equal_the_hand_product() -> None:
    sig = _call(GROWTH, _fixture_events())
    # A1's industry (A2, A3, A4), per session; A2 is left out of the mean on 12-28..30.
    own = 1.01 * 0.98 * 1.02 * 0.99 * 1.03
    ind = (
        ((1.00 + 0.99 + 1.02) / 3)
        * ((1.00 + 1.02) / 2)
        * ((1.01 + 0.97) / 2)
        * ((1.00 + 1.01) / 2)
        * ((1.00 + 0.98 + 1.01) / 3)
    )
    assert sig.scores["A1"] == pytest.approx(-(own - ind), abs=1e-12)
    assert sig.exclusions["short_window"] == ()
    expected = _reference(GROWTH, {"A2": A2_DROPPED}, min_ind=2, min_kept=2)
    assert set(sig.scores) == set(GROWTH)
    for sid, score in expected.items():
        assert sig.scores[sid] == pytest.approx(score, abs=1e-12), sid


def test_announcing_name_drops_its_own_sessions() -> None:
    sig = _call(GROWTH, _fixture_events())
    # A2 keeps 12-27 and 12-31 only, compared with its industry on those two.
    own = 1.00 * 1.00
    ind = ((1.01 + 0.99 + 1.02) / 3) * ((1.03 + 0.98 + 1.01) / 3)
    assert sig.scores["A2"] == pytest.approx(-(own - ind), abs=1e-12)
    assert sig.counts["n_adjusted"] == 1
    assert sig.counts["n_dropped_sessions"] == 3


def test_leave_one_out_means() -> None:
    sig = _call(GROWTH, _fixture_events())
    # B names are untouched by A2's announcement: plain leave-one-out means.
    own = 0.97 * 0.99 * 0.98 * 1.01 * 0.96
    ind = (
        ((1.01 + 1.00 + 1.02) / 3)
        * ((1.03 + 0.98 + 1.01) / 3)
        * ((1.00 + 1.02 + 1.01) / 3)
        * ((0.99 + 1.03 + 0.98) / 3)
        * ((1.02 + 0.99 + 1.00) / 3)
    )
    assert sig.scores["B1"] == pytest.approx(-(own - ind), abs=1e-12)


def test_lowest_relative_return_ranks_first() -> None:
    sig = _call(GROWTH, _fixture_events())
    expected = _reference(GROWTH, {"A2": A2_DROPPED}, min_ind=2, min_kept=2)
    # `expected` holds scores (-r): the lowest r is the highest score and ranks first
    lowest_r = max(expected, key=lambda sid: expected[sid])
    assert sig.ranked[0] == lowest_r
    assert sig.scores[lowest_r] == max(sig.scores.values())
    assert sig.ranked[-1] == min(expected, key=lambda sid: expected[sid])
    assert sig.scores[lowest_r] > 0 > sig.scores[sig.ranked[-1]]
    assert sig.counts["n_ranked"] == len(GROWTH)


def test_rule0_drops_rows_and_events_known_after_t() -> None:
    base = _call(GROWTH, _fixture_events())
    late_bars = pl.DataFrame(
        [
            {
                "security_id": "Z9",
                "session": T_DAY,
                "close": 50.0,
                "known_at": T + timedelta(seconds=1),
            },
            {
                "security_id": "A1",
                "session": date(2022, 1, 3),
                "close": 1.0,
                "known_at": T + timedelta(days=1),
            },
        ],
        schema_overrides={"known_at": pl.Datetime("us", "UTC")},
    )
    # A3's announcement accepted 16:30 on T: after close(T), so it does not exist at t.
    events = [*_fixture_events(), _event("A3", _ny(2021, 12, 31, 16, 30))]
    sig = _call(
        GROWTH,
        events,
        adjusted=pl.concat([_bars(GROWTH), late_bars]),
        ids=[*GROWTH, "Z9"],
        sics={**SIC, "Z9": 2099},
    )
    assert sig.scores == base.scores
    assert "Z9" in sig.exclusions["no_events"]


def test_naive_t_raises_and_t_must_be_a_close() -> None:
    with pytest.raises(ValueError, match=r"tz-aware|naive"):
        _call(GROWTH, _fixture_events(), t=datetime(2021, 12, 31, 21, 0))  # noqa: DTZ001
    with pytest.raises(ValueError, match="session close"):
        _call(GROWTH, _fixture_events(), t=T - timedelta(hours=1))


def _extras() -> tuple[dict[str, list[float]], dict[str, int | None], list[dict[str, object]]]:
    growth = dict(GROWTH)
    sics: dict[str, int | None] = dict(SIC)
    ev = _fixture_events()
    for sid, sic in (
        ("NOSIC", None),
        ("NOEV", 2005),
        ("STAMP", 3580),
        ("LATE", 7380),
        ("ONLYLATE", 7381),
        ("EXACT", 7382),
        ("THIN", 1000),
        ("FLAT", 2006),
        ("ADV", 2007),
        ("MULTI", None),
    ):
        growth[sid] = [1.0] * 5
        sics[sid] = sic
    ev += _old_events(["STAMP", "LATE", "EXACT", "THIN", "FLAT", "ADV"])
    # a mismatch with the index's stamp
    ev.append(_event("STAMP", _ny(2021, 12, 2, 8, 0), index=_ny(2021, 12, 2, 9, 0)))
    # 17:45 on 2021-12-30: s0 = 12-31 = T and after the cutoff, so late
    ev.append(_event("LATE", _ny(2021, 12, 30, 17, 45)))
    ev.append(_event("ONLYLATE", _ny(2021, 12, 30, 17, 45)))
    # exactly 17:30:00 is not late: s0 = T is dropped, the name stays in
    ev.append(_event("EXACT", _ny(2021, 12, 30, 17, 30, 0)))
    return growth, sics, ev


def test_every_exclusion_has_its_reason_and_never_scores() -> None:
    growth, sics, ev = _extras()
    raw = _bars(growth, raw=True).with_columns(
        pl.when(pl.col("security_id") == "ADV")
        .then(5_000.0)
        .otherwise(pl.col("volume"))
        .alias("volume")
    )
    sig = _call(growth, ev, sics=sics, raw=raw)
    ex = sig.exclusions
    assert ex["no_sic"] == ("MULTI", "NOSIC")  # MULTI also has no event: counted once
    assert ex["no_events"] == ("NOEV", "ONLYLATE")  # a lone late event is absent
    assert ex["stamp"] == ("STAMP",)
    assert ex["late_event"] == ("LATE",)
    assert ex["thin_industry"] == ("THIN",)
    assert ex["short_window"] == ()
    assert ex["adv"] == ("ADV",)
    for names in ex.values():
        assert not set(names) & set(sig.scores)
    assert "EXACT" in sig.scores  # 17:30:00 is not late
    assert "FLAT" in sig.scores
    assert sig.counts["n_excluded_no_sic"] == 2
    assert sig.counts["n_excluded_no_events"] == 2
    assert sig.counts["n_excluded_stamp"] == 1
    assert sig.counts["n_excluded_late_event"] == 1
    assert sig.counts["n_excluded_thin_industry"] == 1
    assert sig.counts["n_excluded_adv"] == 1
    assert sig.counts["n_ranked"] == len(sig.scores)
    # A2's three sessions plus EXACT's session T (s0 = T, accepted at the cutoff, not late)
    assert sig.counts["n_adjusted"] == 2
    assert sig.counts["n_dropped_sessions"] == 4
    accounted = set(sig.scores) | {s for names in ex.values() for s in names}
    assert accounted == set(growth)


def test_too_few_kept_sessions_is_short_window() -> None:
    growth = dict(GROWTH)
    growth["S"] = [1.0] * 5
    sics: dict[str, int | None] = {**SIC, "S": 2008}
    adjusted = _bars(growth).filter(
        ~((pl.col("security_id") == "S") & (pl.col("session") < date(2021, 12, 30)))
    )
    sig = _call(growth, [*_fixture_events(), *_old_events(["S"])], sics=sics, adjusted=adjusted)
    assert sig.exclusions["short_window"] == ("S",)
    assert "S" not in sig.scores
    assert sig.counts["n_excluded_short_window"] == 1


def test_late_event_is_ignored_everywhere() -> None:
    growth = dict(GROWTH)
    growth["L"] = [1.0, 1.0, 1.0, 1.0, 1.20]  # a big return on T itself
    sics: dict[str, int | None] = {**SIC, "L": 2010}
    events = [
        *_fixture_events(),
        *_old_events(["L"]),
        _event("L", _ny(2021, 12, 30, 17, 45)),
    ]
    sig = _call(growth, events, sics=sics)
    assert sig.exclusions["late_event"] == ("L",)
    # L stays in the benchmark and its T return stays in A1's industry mean on 12-31
    own = 1.01 * 0.98 * 1.02 * 0.99 * 1.03
    ind = (
        ((1.00 + 0.99 + 1.02 + 1.0) / 4)
        * ((1.00 + 1.02 + 1.0) / 3)
        * ((1.01 + 0.97 + 1.0) / 3)
        * ((1.00 + 1.01 + 1.0) / 3)
        * ((1.00 + 0.98 + 1.01 + 1.20) / 4)
    )
    assert sig.scores["A1"] == pytest.approx(-(own - ind), abs=1e-12)


def test_non_event_forms_and_items_are_not_events() -> None:
    ev = [
        e
        for e in _fixture_events()
        if e["security_id"] != "A2" or e["accepted_at"] != _ny(2021, 12, 28, 10, 0)
    ]
    ev.append(_event("A2", _ny(2021, 12, 28, 10, 0), form="8-K/A"))
    ev.append(_event("A2", _ny(2021, 12, 28, 10, 0), items="12.02"))
    sig = _call(GROWTH, ev)
    # neither row is a 2.02 8-K: no announcement, so every name keeps every session
    expected = _reference(GROWTH, {}, min_ind=2, min_kept=2)
    assert sig.scores["A2"] == pytest.approx(expected["A2"], abs=1e-12)
    assert sig.counts["n_adjusted"] == 0


def test_ties_break_by_security_id() -> None:
    growth = {sid: [1.01, 0.99, 1.02, 1.0, 1.03] for sid in ("T3", "T1", "T2")}
    sics: dict[str, int | None] = {"T3": 5003, "T1": 5001, "T2": 5002}
    sig = _call(growth, _old_events(list(growth)), sics=sics)
    assert len(set(sig.scores.values())) == 1
    assert sig.ranked == ("T1", "T2", "T3")


def test_scores_ordered_and_counts_keys() -> None:
    sig = _call(GROWTH, _fixture_events())
    assert list(sig.scores) == sorted(sig.scores)
    assert list(sig.counts) == [
        "n_ranked",
        "n_excluded_no_sic",
        "n_excluded_thin_industry",
        "n_excluded_no_events",
        "n_excluded_stamp",
        "n_excluded_late_event",
        "n_excluded_short_window",
        "n_excluded_adv",
        "n_adjusted",
        "n_dropped_sessions",
    ]


def test_cutoff_fixture_is_the_new_york_wall_time() -> None:
    cutoff = time.fromisoformat(CFG.dissemination_cutoff)
    assert _ny(2021, 12, 30, 17, 30).astimezone(NY).time() == cutoff


def _after_t(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(known_at=pl.lit(T + timedelta(seconds=1)).dt.replace_time_zone("UTC"))


def test_rule0_bars_known_after_t_are_ignored_adjusted_and_raw() -> None:
    base = _call(GROWTH, _fixture_events())
    # Z1 is a benchmark-eligible industry-20 name whose every bar is stamped after t: a
    # vendor correction not yet known. Without the guard it enters A1's industry mean.
    z1 = _after_t(_bars({"Z1": [1.30] * 5}))
    # a second, later-known bar for A1 on T with a different close
    dup = _after_t(_bars({"A1": [1.0] * 5})).filter(pl.col("session") == T_DAY)
    sig = _call(
        GROWTH,
        [*_fixture_events(), *_old_events(["Z1"])],
        adjusted=pl.concat([_bars(GROWTH), z1, dup]),
        ids=[*GROWTH, "Z1"],
        sics={**SIC, "Z1": 2050},
    )
    assert sig.scores == base.scores
    assert "Z1" in sig.exclusions["short_window"]
    # raw: a thin-volume name whose later-known bars would lift its dollar volume
    thin = _bars({"V": [1.0] * 5}, raw=True).with_columns(volume=pl.lit(5_000.0))
    rich = _after_t(_bars({"V": [1.0] * 5}, raw=True)).with_columns(volume=pl.lit(10_000_000.0))
    growth = {**GROWTH, "V": [1.0] * 5}
    sig = _call(
        growth,
        [*_fixture_events(), *_old_events(["V"])],
        sics={**SIC, "V": 2060},
        raw=pl.concat([_bars(GROWTH, raw=True), thin, rich]),
    )
    assert sig.exclusions["adv"] == ("V",)


def test_frames_without_known_at_raise() -> None:
    with pytest.raises(ValueError, match="known_at"):
        _call(GROWTH, _fixture_events(), adjusted=_bars(GROWTH).drop("known_at"))
    with pytest.raises(ValueError, match="known_at"):
        _call(GROWTH, _fixture_events(), raw=_bars(GROWTH, raw=True).drop("known_at"))


def test_name_with_no_returns_in_a_thin_industry_is_thin_industry() -> None:
    growth = {**GROWTH, "N": [1.0] * 5}
    adjusted = _bars(growth).filter(pl.col("security_id") != "N")
    sig = _call(
        growth,
        [*_fixture_events(), *_old_events(["N"])],
        sics={**SIC, "N": 7000},
        adjusted=adjusted,
    )
    assert sig.exclusions["thin_industry"] == ("N",)
    assert sig.exclusions["short_window"] == ()


def test_returns_only_on_the_industrys_thin_sessions_is_short_window() -> None:
    growth = {**GROWTH, "P": [1.0] * 5, "Q": [1.0] * 5, "R": [1.0] * 5}
    day30, day29 = date(2021, 12, 30), date(2021, 12, 29)
    sid, session = pl.col("security_id"), pl.col("session")
    # Q and R have no bar on 12-30, so P's industry has no contributor on 12-30; P has
    # bars only on 12-29 and 12-30, so its one return (12-30) falls on that thin session
    # while the industry is valid on 12-27 to 12-29.
    adjusted = _bars(growth).filter(
        ~(sid.is_in(["Q", "R"]) & (session == day30))
        & ~((sid == "P") & (session != day30) & (session != day29))
    )
    sig = _call(
        growth,
        [*_fixture_events(), *_old_events(["P", "Q", "R"])],
        sics={**SIC, "P": 6001, "Q": 6002, "R": 6003},
        adjusted=adjusted,
    )
    assert sig.exclusions["short_window"] == ("P",)
    assert "P" not in sig.exclusions["thin_industry"]  # (Q and R are thin: one peer each)


def test_old_bad_stamp_outside_every_window_does_not_exclude() -> None:
    base = _call(GROWTH, _fixture_events())
    # 2021-03-01 is before the coverage window and its dropped sessions miss the formation
    # window: its stamp cannot matter.
    old_bad = _event("A1", _ny(2021, 3, 1, 8, 0), index=_ny(2021, 3, 1, 9, 0))
    sig = _call(GROWTH, [*_fixture_events(), old_bad])
    assert sig.scores == base.scores
    assert sig.exclusions["stamp"] == ()


def test_null_items_is_not_an_event() -> None:
    ev = [*_old_events(list(GROWTH)), _event("A2", _ny(2021, 12, 28, 10, 0))]
    ev[-1]["items"] = None
    sig = _call(GROWTH, ev)
    assert sig.counts["n_adjusted"] == 0


def test_module_has_no_numeric_literal_and_no_io_import() -> None:
    """Spec item 6's AST check: a session count, a threshold or a SIC length is always
    an argument; only the structural 0 and 1 may appear."""
    tree = ast.parse(Path(signals_irrx.__file__).read_text())
    numbers = [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant)
        and isinstance(n.value, int | float | complex)
        and not isinstance(n.value, bool)
    ]
    assert [v for v in numbers if v not in (0, 1)] == []
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    banned = ("edgar", "store", "duckdb", "requests", "httpx", "sqlite3", "urllib", "socket")
    assert not {m for m in modules if any(b in m for b in banned)}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"open", "print"}
