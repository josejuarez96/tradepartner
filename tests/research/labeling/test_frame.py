"""Tests for `tradepartner.research.labeling.frame` (plan T122; research-labeling
spec C11, req 2 "Frame build").

The fixture corpus (`tests/fixtures/research/frame/corpus.jsonl`) has six listing
ends in the amendment's C11 shape: a plain delisted one whose only marker is
accepted after the fixture `t` (LE1), a transfer (LE2), a relisting (LE3), a
successor (LE4), a Form 15 veto (LE5), and one accepted after `t` itself (LE6).
The matching store rows are built directly (`_security`/`_listing`/`_delisting`),
the same way `tests/store/test_asof.py`'s `synthetic_store` does, so the exact
numbers in each scenario are controlled rather than hunted for in a CSV fixture.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import polars as pl
import pytest

from tradepartner.config import Settings
from tradepartner.research.labeling import frame as frame_mod
from tradepartner.store import schema
from tradepartner.store.db import configure_connection, insert_row

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "research" / "frame"
CORPUS = FIXTURES / "corpus.jsonl"
SRC = Path(frame_mod.__file__)

#: The fixture's as-of time: after every scenario but LE6.
T = datetime(2027, 1, 15, tzinfo=UTC)
EARLY = datetime(2020, 1, 1, tzinfo=UTC)


def _security(
    conn: duckdb.DuckDBPyConnection, security_id: str, cik: str, known_at: datetime
) -> None:
    insert_row(
        conn,
        "securities",
        {
            "security_id": security_id,
            "cik": cik,
            "name": f"Company {cik}",
            "benchmark": False,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def _listing(
    conn: duckdb.DuckDBPyConnection,
    security_id: str,
    ticker: str,
    exchange: str,
    valid_from: date,
    known_at: datetime,
) -> None:
    insert_row(
        conn,
        "listings",
        {
            "security_id": security_id,
            "ticker": ticker,
            "exchange": exchange,
            "class_title": "Common Stock",
            "valid_from": valid_from,
            "known_at": known_at,
            "ingested_at": known_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )


def _delisting(
    conn: duckdb.DuckDBPyConnection,
    security_id: str,
    exchange: str,
    filed_at: datetime,
    effective_on: date,
) -> None:
    insert_row(
        conn,
        "delistings",
        {
            "security_id": security_id,
            "form": "25",
            "class_title": "Common Stock",
            "exchange": exchange,
            "filed_at": filed_at,
            "effective_on": effective_on,
            "known_at": filed_at,
            "ingested_at": filed_at,
            "source": "edgar",
            "provenance": "filing",
        },
    )


@pytest.fixture
def frame_settings(settings: Settings, tmp_path: Path) -> Settings:
    """`settings` (its `store.path` already a fresh tmp file) with
    `research.data_dir` pointed at another tmp directory, and the six
    fixture listing ends' store rows written to it."""
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})

    conn = duckdb.connect(out.store.path)
    configure_connection(conn)
    schema.init_schema(conn)

    # LE1: plain delisted; its only marker is accepted after `t` (ignored).
    _security(conn, "0001000001", "0001000001", EARLY)
    _listing(conn, "0001000001", "AAA", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(
        conn, "0001000001", "NASDAQ", datetime(2026, 1, 15, 14, tzinfo=UTC), date(2026, 1, 25)
    )

    # LE2: transferred (a destination listing within the transfer window).
    _security(conn, "0001000002", "0001000002", EARLY)
    _listing(conn, "0001000002", "BBB", "NYSE", date(2020, 1, 1), EARLY)
    le2_accepted = datetime(2026, 2, 2, 14, tzinfo=UTC)
    _delisting(conn, "0001000002", "NYSE", le2_accepted, date(2026, 2, 12))
    _listing(conn, "0001000002", "BBB", "NASDAQ", date(2026, 2, 4), le2_accepted)

    # LE3: relisted (a later listing of the same security on the same exchange).
    _security(conn, "0001000003", "0001000003", EARLY)
    _listing(conn, "0001000003", "CCC", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(
        conn, "0001000003", "NASDAQ", datetime(2026, 3, 2, 14, tzinfo=UTC), date(2026, 3, 12)
    )
    _listing(
        conn, "0001000003", "CCC", "NASDAQ", date(2026, 4, 1), datetime(2026, 4, 1, tzinfo=UTC)
    )

    # LE4: a successor security under the same CIK.
    _security(conn, "0001000004", "0001000004", EARLY)
    _listing(conn, "0001000004", "DDD", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(
        conn, "0001000004", "NASDAQ", datetime(2026, 4, 2, 14, tzinfo=UTC), date(2026, 4, 12)
    )
    _security(conn, "0001000004@2026-05-01", "0001000004", datetime(2026, 5, 1, tzinfo=UTC))

    # LE5: a Form 15 veto, from the corpus's own marker (never the store).
    _security(conn, "0001000005", "0001000005", EARLY)
    _listing(conn, "0001000005", "EEE", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(
        conn, "0001000005", "NASDAQ", datetime(2026, 5, 4, 14, tzinfo=UTC), date(2026, 5, 14)
    )

    # LE6 (accepted after `t`) needs no store rows: it is dropped before any read.

    conn.commit()
    conn.close()
    return out


def _row(df: pl.DataFrame, listing_end_id: str) -> dict[str, object]:
    matches = df.filter(pl.col("listing_end_id") == listing_end_id)
    assert matches.height == 1, f"expected exactly one row for {listing_end_id}, got {matches}"
    return matches.row(0, named=True)


# --- the rule answer, per listing end --------------------------------------


def test_one_row_per_listing_end_with_the_rule_answer(frame_settings: Settings) -> None:
    result = frame_mod.build_frame(CORPUS, T, frame_settings)

    assert result.counts.as_json() == {
        "listing_ends_seen": 6,
        "kept": 5,
        "after_t": 1,
        "markers_after_t": 1,
        "identity_holds": True,
    }
    assert result.n_rows == 5

    df = pl.read_parquet(result.frame_path)
    assert sorted(df["listing_end_id"].to_list()) == ["LE1", "LE2", "LE3", "LE4", "LE5"]
    assert set(df["rule_as_of"].to_list()) == {T}

    le1 = _row(df, "LE1")
    assert (le1["rule_status"], le1["rule_row"]) == ("delisted", 5)
    assert (le1["rule_relisted"], le1["rule_successor_id"], le1["rule_form15_in_window"]) == (
        False,
        None,
        False,
    )

    le2 = _row(df, "LE2")
    assert (le2["rule_status"], le2["rule_row"]) == ("transferred", 1)

    le3 = _row(df, "LE3")
    assert (le3["rule_status"], le3["rule_row"], le3["rule_relisted"]) == ("delisted", 2, True)

    le4 = _row(df, "LE4")
    assert (le4["rule_status"], le4["rule_row"], le4["rule_successor_id"]) == (
        "delisted",
        3,
        "0001000004@2026-05-01",
    )

    le5 = _row(df, "LE5")
    assert (le5["rule_status"], le5["rule_row"], le5["rule_form15_in_window"]) == (
        "delisted",
        4,
        True,
    )

    documents = json.loads(le1["documents"])
    assert documents["listing_end_id"] == "LE1"
    assert documents["markers"][0]["accepted_at"] == "2027-02-01T00:00:00+00:00"


# --- one short-lived read-only connection -----------------------------------


def test_exactly_one_read_only_connection(
    frame_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = 0
    closed = 0
    real_open_read_only = frame_mod.open_read_only

    @contextmanager
    def _counting_open_read_only(settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
        nonlocal opened, closed
        with real_open_read_only(settings) as conn:
            opened += 1
            try:
                yield conn
            finally:
                closed += 1

    monkeypatch.setattr(frame_mod, "open_read_only", _counting_open_read_only)

    result = frame_mod.build_frame(CORPUS, T, frame_settings)

    assert (opened, closed) == (1, 1)
    assert result.n_rows == 5


# --- byte-identical on rebuild -----------------------------------------------


def test_the_same_inputs_twice_give_byte_identical_files(frame_settings: Settings) -> None:
    first = frame_mod.build_frame(CORPUS, T, frame_settings)
    second = frame_mod.build_frame(CORPUS, T, frame_settings)

    assert first.sha256 == second.sha256
    assert first.frame_path == second.frame_path
    assert first.frame_path.read_bytes() == second.frame_path.read_bytes()


# --- the unjoined delistings row report -------------------------------------


def test_a_delisting_that_ends_no_listing_is_reported_by_security_id(
    settings: Settings, tmp_path: Path
) -> None:
    """A `delistings` row the master resolved for the Form 25 but never
    attached to any listing (no `listings` row at all, here): `rule_status`
    is `"listed"` (a live line with a Form 25 is a disagreement by
    construction, ADR 0013 point 2 row A) and it is named in `counts.json`."""
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})
    accepted = datetime(2026, 1, 15, 14, tzinfo=UTC)

    conn = duckdb.connect(out.store.path)
    configure_connection(conn)
    schema.init_schema(conn)
    _security(conn, "0003000001", "0003000001", EARLY)
    _delisting(conn, "0003000001", "NASDAQ", accepted, date(2026, 1, 25))
    conn.commit()
    conn.close()

    corpus_path = tmp_path / "corpus.jsonl"
    corpus_path.write_text(
        json.dumps(_minimal_record("LEU", "0003000001", "NASDAQ", accepted)) + "\n"
    )

    result = frame_mod.build_frame(corpus_path, T, out)

    assert result.unjoined_delistings == (
        frame_mod.UnjoinedDelisting(security_id="0003000001", exchange="NASDAQ", filed_at=accepted),
    )
    counts_json = json.loads(result.counts_path.read_text())
    assert counts_json["unjoined_delistings"] == [
        {"security_id": "0003000001", "exchange": "NASDAQ", "filed_at": accepted.isoformat()}
    ]
    df = pl.read_parquet(result.frame_path)
    assert _row(df, "LEU")["rule_status"] == "listed"


# --- no-look-ahead ------------------------------------------------------------


def test_a_listing_row_with_valid_from_after_t_does_not_relist(
    settings: Settings, tmp_path: Path
) -> None:
    """A relisting `listings` row known at `t` (so the as-of read alone
    would let it through) but whose `valid_from` is after `t`'s session
    must not make `rule_relisted` true: knowing about it early is not the
    same as it having started."""
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})
    accepted = datetime(2026, 2, 2, 14, tzinfo=UTC)

    conn = duckdb.connect(out.store.path)
    configure_connection(conn)
    schema.init_schema(conn)
    _security(conn, "0003000002", "0003000002", EARLY)
    _listing(conn, "0003000002", "FFF", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(conn, "0003000002", "NASDAQ", accepted, date(2026, 2, 12))
    # Known well before T, but its own `valid_from` is after T.
    _listing(
        conn, "0003000002", "FFF", "NASDAQ", date(2027, 3, 1), datetime(2026, 3, 1, tzinfo=UTC)
    )
    conn.commit()
    conn.close()

    corpus_path = tmp_path / "corpus.jsonl"
    corpus_path.write_text(
        json.dumps(_minimal_record("LEF", "0003000002", "NASDAQ", accepted)) + "\n"
    )

    result = frame_mod.build_frame(corpus_path, T, out)

    df = pl.read_parquet(result.frame_path)
    row = _row(df, "LEF")
    assert (row["rule_status"], row["rule_relisted"]) == ("delisted", False)


def test_the_listing_end_accepted_after_t_is_absent_and_counted(
    settings: Settings, tmp_path: Path
) -> None:
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})

    conn = duckdb.connect(out.store.path)
    configure_connection(conn)
    schema.init_schema(conn)
    conn.commit()
    conn.close()

    corpus_path = tmp_path / "corpus.jsonl"
    corpus_path.write_text(
        json.dumps(_minimal_record("LEX", "0003000003", "NASDAQ", datetime(2027, 2, 1, tzinfo=UTC)))
        + "\n"
    )

    result = frame_mod.build_frame(corpus_path, T, out)

    assert (result.counts.kept, result.counts.after_t, result.n_rows) == (0, 1, 0)
    df = pl.read_parquet(result.frame_path)
    assert df.height == 0


def test_a_marker_accepted_after_t_does_not_set_form15_in_window(
    frame_settings: Settings,
) -> None:
    result = frame_mod.build_frame(CORPUS, T, frame_settings)

    df = pl.read_parquet(result.frame_path)
    le1 = _row(df, "LE1")
    assert le1["rule_form15_in_window"] is False
    assert result.counts.markers_after_t == 1


def _minimal_record(listing_end_id: str, cik: str, exchange: str, accepted_at: datetime) -> dict:
    day = accepted_at.date()
    return {
        "listing_end_id": listing_end_id,
        "cik": cik,
        "issuer": f"Issuer {cik}",
        "exchange": exchange,
        "exchange_name": exchange,
        "class_title": "Common Stock",
        "form": "25",
        "form25_accepted_at": accepted_at.isoformat(),
        "form25_filed_on": day.isoformat(),
        "signature_date": day.isoformat(),
        "effective_on": (day).isoformat(),
        "rule_provision_raw": None,
        "rule_provision": None,
        "amendments": [],
        "orphan_amendment": False,
        "exhibit": None,
        "eightk": None,
        "eightk_note": "no 8-K in window",
        "markers": [],
        "missing": [],
    }


# --- the boundary -------------------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_the_module_imports_no_adapter_and_nothing_from_corpus() -> None:
    imported = _imported_modules(SRC)
    assert not any(name.startswith("tradepartner.adapters") for name in imported)
    assert not any(name.startswith("tradepartner.corpus") for name in imported)
