"""Point-in-time contract for the event-study store reader (T302)."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pytest
from fixtures.research.event_study_store import build_event_study_store

from tradepartner.config import Settings
from tradepartner.research.event_study import reader as reader_module
from tradepartner.research.event_study.reader import EventStudyReader
from tradepartner.store import schema
from tradepartner.store.db import open_read_only

END = date(2018, 6, 8)
EVENT = datetime(2018, 6, 5, 15, tzinfo=UTC)


def test_truncation_invariance_and_benchmark(tmp_path: Path) -> None:
    full = EventStudyReader(build_event_study_store(tmp_path / "full.duckdb"), END)
    truncated = EventStudyReader(
        build_event_study_store(tmp_path / "truncated.duckdb", truncate_at=END), END
    )
    for reader in (full, truncated):
        assert reader.benchmark("SPY", start=date(2018, 6, 1)) == "SPY_ID"
        adjusted, raw = reader.bars(["ISSUER", "SPY_ID"], sessions_from=date(2018, 6, 1))
        assert adjusted.equals(full.bars(["ISSUER", "SPY_ID"], sessions_from=date(2018, 6, 1))[0])
        assert raw.equals(full.bars(["ISSUER", "SPY_ID"], sessions_from=date(2018, 6, 1))[1])
        assert reader.max_read_t == datetime(2018, 6, 8, 20, tzinfo=UTC)
        assert reader.shares(
            "ISSUER", EVENT, fact_name="shares_outstanding", facts_read="window_end"
        ) == full.shares("ISSUER", EVENT, fact_name="shares_outstanding", facts_read="window_end")
    adjusted, raw = truncated.bars(["ISSUER"], sessions_from=date(2018, 6, 1))
    assert (
        adjusted.filter(adjusted["session"] == date(2018, 6, 4))["close"][0]
        != raw.filter(raw["session"] == date(2018, 6, 4))["close"][0]
    )


def test_benchmark_resolves_snapshot_static_identity_stamped_after_the_window(
    tmp_path: Path,
) -> None:
    """#2517: SPY's identity rows are stamped at the snapshot fetch (after any
    historical window); #840 reads identity by symbol, bars stay point-in-time."""
    settings = build_event_study_store(
        tmp_path / "store.duckdb", identity_known_at=datetime(2026, 10, 3, 12, tzinfo=UTC)
    )
    reader = EventStudyReader(settings, END)
    assert reader.benchmark("SPY", start=date(2018, 6, 1)) == "SPY_ID"
    assert reader.max_read_t == datetime(2018, 6, 8, 20, tzinfo=UTC)
    adjusted, _ = reader.bars(["SPY_ID"], sessions_from=date(2018, 6, 1))
    assert adjusted["session"].max() <= END  # bars stay cut at the window end


def test_benchmark_identity_by_symbol_ignores_data_after_cutoff(tmp_path: Path) -> None:
    """No look-ahead: the resolved id and bars do not change with later bars."""
    late = datetime(2026, 10, 3, 12, tzinfo=UTC)
    full = EventStudyReader(
        build_event_study_store(tmp_path / "f.duckdb", identity_known_at=late), END
    )
    cut = EventStudyReader(
        build_event_study_store(tmp_path / "c.duckdb", truncate_at=END, identity_known_at=late), END
    )
    assert full.benchmark("SPY", start=date(2018, 6, 1)) == cut.benchmark(
        "SPY", start=date(2018, 6, 1)
    )
    assert full.bars(["SPY_ID"], sessions_from=date(2018, 6, 1))[0].equals(
        cut.bars(["SPY_ID"], sessions_from=date(2018, 6, 1))[0]
    )


def test_non_session_window_end_uses_prior_close(tmp_path: Path) -> None:
    reader = EventStudyReader(build_event_study_store(tmp_path / "store.duckdb"), date(2018, 6, 9))
    assert reader.cutoff == datetime(2018, 6, 8, 20, tzinfo=UTC)


def test_default_shares_hide_later_fact(tmp_path: Path) -> None:
    reader = EventStudyReader(build_event_study_store(tmp_path / "store.duckdb"), END)
    rows = reader.shares("ISSUER", EVENT, fact_name="shares_outstanding")
    assert [row["value"] for row in rows] == [1_000_000]
    assert reader.max_read_t == EVENT
    later = reader.shares("ISSUER", EVENT, fact_name="shares_outstanding", facts_read="window_end")
    assert [row["value"] for row in later] == [1_000_000, 2_000_000]
    assert reader.max_read_t == datetime(2018, 6, 8, 20, tzinfo=UTC)


def test_reader_rejects_invalid_cut_or_mode(tmp_path: Path) -> None:
    reader = EventStudyReader(build_event_study_store(tmp_path / "store.duckdb"), END)
    with pytest.raises(ValueError, match="known_at"):
        reader.shares(
            "ISSUER",
            datetime(2018, 6, 5, 15, tzinfo=UTC).replace(tzinfo=None),
            fact_name="shares_outstanding",
        )
    with pytest.raises(ValueError, match="window"):
        reader.shares("ISSUER", datetime(2018, 6, 9, tzinfo=UTC), fact_name="shares_outstanding")
    with pytest.raises(ValueError, match="facts_read"):
        reader.shares("ISSUER", EVENT, fact_name="shares_outstanding", facts_read="other")


def test_every_store_connection_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader = EventStudyReader(build_event_study_store(tmp_path / "store.duckdb"), END)
    opened = 0

    @contextmanager
    def checked_open(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
        nonlocal opened
        opened += 1
        with open_read_only(settings) as conn:
            with pytest.raises(duckdb.Error):
                conn.execute("CREATE TABLE forbidden_write (id INTEGER)")
            yield conn

    monkeypatch.setattr(reader_module, "open_read_only", checked_open)
    reader.benchmark("SPY", start=date(2018, 6, 1))
    reader.bars(["ISSUER", "SPY_ID"], sessions_from=date(2018, 6, 1))
    reader.shares("ISSUER", EVENT, fact_name="shares_outstanding")
    assert opened == 3


def test_event_study_source_is_isolated_and_read_only() -> None:
    root = Path(__file__).resolve().parents[3] / "src/tradepartner/research/event_study"
    for source in root.glob("*.py"):
        tree = ast.parse(source.read_text())
        assert "RESEARCH_TABLE_NAMES" not in source.read_text()
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value not in schema.RESEARCH_TABLE_NAMES
            if isinstance(node, ast.ImportFrom):
                assert node.module != "tradepartner.store.research"
                assert not (
                    node.module == "tradepartner.store"
                    and any(alias.name == "research" for alias in node.names)
                )
            if isinstance(node, ast.Import):
                assert all(alias.name != "tradepartner.store.research" for alias in node.names)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id != "connect"
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert not (
                    node.func.attr == "connect"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "duckdb"
                )
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for table in schema.RESEARCH_TABLE_NAMES:
                    assert re.search(rf"\b{re.escape(table)}\b", node.value) is None
