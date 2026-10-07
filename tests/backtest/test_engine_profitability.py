"""The T85e family seam over a recording provider and annual fact rows."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import polars as pl
import pytest

from backtest.fake_provider import FakeProvider
from backtest.test_engine import T0, T1, _handle, _params, _provider
from tradepartner.backtest.engine import plan, run
from tradepartner.backtest.schedule import read_time
from tradepartner.backtest.strategies import signal_for
from tradepartner.calendar import session_close


def _facts() -> pl.DataFrame:
    rows: list[tuple[Any, ...]] = []
    for sid, profit in (("A", 20.0), ("B", 40.0), ("C", 60.0), ("D", 80.0)):
        for name, value, days in (
            ("gross_profit", profit, 365),
            ("total_assets", 100.0, 0),
        ):
            rows.append(
                (
                    sid,
                    name,
                    date(2023, 1, 1) if days else None,
                    date(2023, 12, 31),
                    days,
                    value,
                    "filed",
                    session_close(T0),
                )
            )
    return pl.DataFrame(
        rows,
        schema={
            "security_id": pl.Utf8,
            "fact_name": pl.Utf8,
            "period_start": pl.Date,
            "period_end": pl.Date,
            "period_days": pl.Int64,
            "value": pl.Float64,
            "basis": pl.Utf8,
            "known_at": pl.Datetime("us", "UTC"),
        },
        orient="row",
    )


class _Provider(FakeProvider):
    facts_override: pl.DataFrame | None = None

    def statement_facts(self, t: datetime, ids: list[str]) -> pl.DataFrame:
        self._record("statement_facts", t, ids=ids)
        facts = self.facts_override if self.facts_override is not None else _facts()
        return facts.filter(pl.col("known_at") <= t, pl.col("security_id").is_in(ids))

    def sics(self, t: datetime, ids: list[str]) -> dict[str, int | None]:
        self._record("sics", t, ids=ids)
        return dict.fromkeys(ids, 1000)


def _provider_with_facts() -> _Provider:
    base = _provider()
    return _Provider(prices=base.prices, members=base.members, benchmarks=base.benchmarks)


def test_family_reads_and_counts() -> None:
    provider = _provider_with_facts()
    params = _params(profitability={"top_fraction": 0.5})
    planned = plan(provider, params, T0, family="profitability")
    assert set(planned.targets) == {"C", "D"}
    assert planned.counts == {
        "n_ranked": 4,
        "n_excluded_no_facts": 0,
        "n_excluded_stale_facts": 0,
        "n_excluded_sector": 0,
        "n_excluded_malformed": 0,
        "n_derived": 0,
    }
    assert planned.n_excluded_no_history == 0
    assert [c.method for c in provider.calls] == [
        "universe",
        "statement_facts",
        "sics",
        "static_listing_count",
        "survivorship_gap",
    ]
    assert provider.read_times() == {read_time(T0)}

    result = run(params, _provider_with_facts(), T0, T1, _handle(), [15.0], family="profitability")
    row = result[15.0].rebalances[0]
    assert (row.n_ranked, row.n_excluded_no_facts, row.n_derived) == (4, 0, 0)
    assert row.n_excluded_no_history == 0


def test_momentum_never_reads_facts_and_keeps_profitability_counts_null() -> None:
    provider = _provider_with_facts()
    result = run(_params(), provider, T0, T1, _handle(), [15.0], family="momentum")
    assert not {"statement_facts", "sics"} & {call.method for call in provider.calls}
    row = result[15.0].rebalances[0]
    assert (row.n_ranked, row.n_excluded_no_facts, row.n_derived) == (None, None, None)


def test_fact_accepted_after_close_first_enters_the_next_plan() -> None:
    provider = _provider_with_facts()
    provider.facts_override = _facts().with_columns(
        pl.when((pl.col("security_id") == "D") & (pl.col("fact_name") == "gross_profit"))
        .then(pl.lit(session_close(T0) + timedelta(hours=1)))
        .otherwise(pl.col("known_at"))
        .alias("known_at")
    )
    params = _params(profitability={"top_fraction": 0.5})
    at_t0 = plan(provider, params, T0, family="profitability")
    at_t1 = plan(provider, params, T1, family="profitability")
    assert (at_t0.counts["n_ranked"], at_t0.counts["n_excluded_no_facts"]) == (3, 1)
    assert set(at_t0.targets) == {"B", "C"}
    assert (at_t1.counts["n_ranked"], at_t1.counts["n_excluded_no_facts"]) == (4, 0)
    assert set(at_t1.targets) == {"C", "D"}


def test_unknown_family_raises_before_provider_read() -> None:
    provider = _provider_with_facts()
    with pytest.raises(KeyError):
        plan(provider, _params(), T0, family="unknown")  # type: ignore[arg-type]
    assert provider.calls == []
    with pytest.raises(KeyError):
        run(_params(), provider, T0, T1, _handle(), [15.0], family="unknown")  # type: ignore[arg-type]
    assert provider.calls == []


def test_table_has_the_declared_reads_and_construction_sections() -> None:
    assert signal_for("profitability").reads == ("statement_facts", "sics")
    assert signal_for("profitability").section == "profitability"
    assert signal_for("momentum").reads == ("adjusted_prices",)
