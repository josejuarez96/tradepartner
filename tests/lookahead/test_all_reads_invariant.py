"""Every public as-of read is truncation-invariant, or listed in `departures.ALLOWED` (#3530)."""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import duckdb
import polars as pl
import pytest
from conftest import _FIXTURES_UNIVERSE_DIR as FIXTURES_UNIVERSE_DIR
from conftest import load_chain_overlay, load_universe_fixtures

from lookahead.departures import ALLOWED, DIRECTIONS
from lookahead.harness import _FACT_TABLES, TruncatedStore, probe_timestamps
from tradepartner.config import EventsConfig, Settings
from tradepartner.store import asof, classify, delisting_reasons, delistings, master, schema
from tradepartner.store.db import configure_connection
from tradepartner.universe import Universe, universe_as_of

#: The modules whose public `*_as_of` functions are discovered by introspection (`universe_as_of`
#: is added by name: `tradepartner.universe` also holds `shares_as_of` helpers that are not reads
#: of the store's facts at a probe).
MODULES = (
    "tradepartner.store.asof",
    "tradepartner.store.master",
    "tradepartner.store.delistings",
    "tradepartner.store.delisting_reasons",
    "tradepartner.store.classify",
)

Read = Callable[[duckdb.DuckDBPyConnection, datetime], Any]
_SETTINGS = Settings(_env_file=None, events=EventsConfig(sec_forms_read_enabled=True))
_SUCCESSOR = "0001000011"


def discover() -> dict[str, Callable[..., Any]]:
    """Every public function named `*_as_of` defined in `MODULES`, plus `universe_as_of`."""
    found: dict[str, Callable[..., Any]] = {}
    for module_name in MODULES:
        module = importlib.import_module(module_name)
        for name, func in inspect.getmembers(module, inspect.isfunction):
            if (
                name.endswith("_as_of")
                and not name.startswith("_")
                and func.__module__ == module_name
            ):
                found[name] = func
    found["universe_as_of"] = universe_as_of
    return found


def _universe(conn: duckdb.DuckDBPyConnection, t: datetime) -> tuple[pl.DataFrame, pl.DataFrame]:
    universe: Universe = universe_as_of(conn, t, _SETTINGS)
    return universe.members, universe.exclusions


def _adapters() -> dict[str, Read]:
    return {
        "live_actions_as_of": lambda c, t: asof.live_actions_as_of(c, t),
        "prices_as_of": lambda c, t: asof.prices_as_of(c, t),
        "marking_changed_ids_as_of": lambda c, t: asof.marking_changed_ids_as_of(
            c, t - timedelta(days=1), t, [_SUCCESSOR]
        ),
        "price_jumps_as_of": lambda c, t: asof.price_jumps_as_of(c, t),
        "symbol_changes_as_of": lambda c, t: asof.symbol_changes_as_of(c, t),
        "spinoff_allocations_as_of": lambda c, t: asof.spinoff_allocations_as_of(c, t),
        "mergers_as_of": lambda c, t: asof.mergers_as_of(c, t),
        "rates_as_of": lambda c, t: asof.rates_as_of(c, t),
        "facts_as_of": lambda c, t: asof.facts_as_of(c, t),
        "listings_as_of": lambda c, t: asof.listings_as_of(c, t),
        "statement_facts_as_of": lambda c, t: asof.statement_facts_as_of(c, t),
        "filing_events_as_of": lambda c, t: asof.filing_events_as_of(c, t),
        "sec_filing_events_as_of": lambda c, t: asof.sec_filing_events_as_of(
            c, t, ["8-K"], settings=_SETTINGS
        ),
        "sec_form4_transactions_as_of": lambda c, t: asof.sec_form4_transactions_as_of(
            c, t, settings=_SETTINGS
        ),
        "borrow_status_as_of": lambda c, t: asof.borrow_status_as_of(c, t),
        "adjusted_prices_as_of": lambda c, t: asof.adjusted_prices_as_of(
            c, t, include_dividends=True, include_spin_offs=True, settings=_SETTINGS
        ),
        "dropped_dividends_as_of": lambda c, t: asof.dropped_dividends_as_of(
            c, t, settings=_SETTINGS
        ),
        "unvalued_spin_offs_as_of": lambda c, t: asof.unvalued_spin_offs_as_of(
            c, t, settings=_SETTINGS
        ),
        "securities_as_of": lambda c, t: master.securities_as_of(c, t),
        "delistings_as_of": lambda c, t: delistings.delistings_as_of(c, t),
        "delistings_with_reasons_as_of": lambda c, t: delistings.delistings_with_reasons_as_of(
            c, t
        ),
        "listing_ends_as_of": lambda c, t: delistings.listing_ends_as_of(c, t, _SETTINGS),
        "delisting_reasons_as_of": lambda c, t: delisting_reasons.delisting_reasons_as_of(c, t),
        "classifications_as_of": lambda c, t: classify.classifications_as_of(c, t),
        "universe_as_of": _universe,
    }


#: One adapter per discovered function: how to call it on `(conn, t)` with the arguments its
#: signature needs. A new `*_as_of` function without an entry fails the discovery test.
ADAPTERS = _adapters()


def unadapted(names: Any) -> list[str]:
    """The discovered names that have no probe adapter, sorted."""
    return sorted(name for name in names if name not in ADAPTERS)


def same(a: Any, b: Any) -> bool:
    """Equality for a read's result: frames by `DataFrame.equals`, tuples item by item."""
    if isinstance(a, pl.DataFrame) and isinstance(b, pl.DataFrame):
        return a.equals(b)
    if isinstance(a, tuple) and isinstance(b, tuple):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b, strict=True))
    return bool(a == b)


#: The harness probes this test walks. Every read on every probe of the fixture (~2000, ~1.2 s per
#: probe for all reads) is too slow for a guard that runs on each push, so: the probes around every
#: `successor_chains` stamp (the boundary the known departures hang on), every `OTHER_STRIDE`-th
#: probe around the other fact tables' stamps, and every `BAR_STRIDE`-th probe of the whole store
#: (which samples `prices_daily` and `rates`, ~900 stamps each, across the span).
OTHER_STRIDE = 6
BAR_STRIDE = 100
_DENSE_TABLES = frozenset({"prices_daily", "rates", "successor_chains"})


def probes_for(conn: duckdb.DuckDBPyConnection) -> list[datetime]:
    """The harness probes this test walks on `conn`."""
    others = [table for table in _FACT_TABLES if table not in _DENSE_TABLES]
    return sorted(
        {
            *probe_timestamps(conn, ["successor_chains"]),
            *probe_timestamps(conn, others)[::OTHER_STRIDE],
            *probe_timestamps(conn)[::BAR_STRIDE],
        }
    )


def departing_probes(
    conn: duckdb.DuckDBPyConnection, truncated: TruncatedStore, name: str
) -> list[datetime]:
    """The probes at which `name` differs between the full and the truncated store."""
    read = ADAPTERS[name]
    return [t for t in probes_for(conn) if not same(read(conn, t), read(truncated.at(t), t))]


@pytest.fixture(scope="module")
def departures_found() -> dict[str, list[datetime]]:
    """Per discovered read, the probes where it departs, computed once for the module."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, FIXTURES_UNIVERSE_DIR)
    load_chain_overlay(conn)
    truncated = TruncatedStore(conn)
    try:
        return {name: departing_probes(conn, truncated, name) for name in sorted(discover())}
    finally:
        truncated.close()
        conn.close()


def test_every_public_as_of_read_is_listed_or_invariant(
    departures_found: dict[str, list[datetime]],
) -> None:
    assert not unadapted(departures_found), f"no probe adapter: {unadapted(departures_found)}"
    unexplained = {
        name: (len(probes), probes[0])
        for name, probes in departures_found.items()
        if probes and name not in ALLOWED
    }
    assert not unexplained, (
        "reads that depart from truncation invariance and are not in departures.ALLOWED "
        f"(count, first probe): {unexplained}"
    )


def test_every_allowed_departure_still_departs(
    departures_found: dict[str, list[datetime]],
) -> None:
    stale = [name for name in sorted(ALLOWED) if not departures_found.get(name)]
    assert not stale, f"stale ALLOWED entries (no longer depart, or no longer exist): {stale}"


def test_a_new_as_of_function_without_an_adapter_fails_naming_it() -> None:
    def foo_as_of(conn: duckdb.DuckDBPyConnection, t: datetime) -> pl.DataFrame:
        return pl.DataFrame()

    found = {**discover(), "foo_as_of": foo_as_of}
    assert unadapted(found) == ["foo_as_of"]
    assert unadapted(discover()) == []


def test_a_new_function_added_to_a_scanned_module_is_discovered() -> None:
    from tradepartner.store import asof

    def foo_as_of(conn: duckdb.DuckDBPyConnection, t: datetime) -> pl.DataFrame:
        return pl.DataFrame()

    foo_as_of.__module__ = "tradepartner.store.asof"
    asof.foo_as_of = foo_as_of  # type: ignore[attr-defined]
    try:
        assert unadapted(discover()) == ["foo_as_of"]
    finally:
        del asof.foo_as_of  # type: ignore[attr-defined]


def test_every_departure_has_a_citation_and_an_allowed_direction() -> None:
    for name, departure in ALLOWED.items():
        assert departure.name == name
        assert departure.direction in DIRECTIONS, name
        assert departure.condition.strip() and departure.citation.strip(), name


def test_a_hides_data_departure_never_reveals_a_later_value(
    departures_found: dict[str, list[datetime]],
) -> None:
    """The full store may show less than the truncated one, never more."""
    conn = duckdb.connect(":memory:")
    configure_connection(conn)
    schema.init_schema(conn)
    load_universe_fixtures(conn, FIXTURES_UNIVERSE_DIR)
    load_chain_overlay(conn)
    truncated = TruncatedStore(conn)
    try:
        for name, departure in ALLOWED.items():
            if departure.direction != "hides_data":
                continue
            read = ADAPTERS[name]
            for t in departures_found[name]:
                full, seen = read(conn, t), read(truncated.at(t), t)
                if isinstance(full, pl.DataFrame):
                    assert full.height <= seen.height, (name, t)
    finally:
        truncated.close()
        conn.close()
