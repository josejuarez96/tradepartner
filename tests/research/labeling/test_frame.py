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
        "markers_unstamped": 0,
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
    assert documents["markers"][0]["accepted_at"] == "2027-01-20T00:00:00+00:00"


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

    assert result.delistings_ending_no_listing == (
        frame_mod.UnjoinedDelisting(security_id="0003000001", exchange="NASDAQ", filed_at=accepted),
    )
    assert result.unmatched_delistings == ()
    counts_json = json.loads(result.counts_path.read_text())
    assert counts_json["delistings_ending_no_listing"] == [
        {"security_id": "0003000001", "exchange": "NASDAQ", "filed_at": accepted.isoformat()}
    ]
    df = pl.read_parquet(result.frame_path)
    assert _row(df, "LEU")["rule_status"] == "listed"


def test_a_store_delisting_with_no_corpus_record_is_reported_unmatched(
    settings: Settings, tmp_path: Path
) -> None:
    """The survivorship check (req 2): a `delistings` row at `t` for a CIK
    the corpus names must join some corpus record by `(exchange, filed_at)`;
    one that joins none -- here, a second delisting the corpus fetch never
    carried, perhaps because the join key drifted -- is reported."""
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})
    accepted = datetime(2026, 1, 15, 14, tzinfo=UTC)
    orphan_accepted = datetime(2026, 6, 1, 14, tzinfo=UTC)

    conn = duckdb.connect(out.store.path)
    configure_connection(conn)
    schema.init_schema(conn)
    _security(conn, "0003000004", "0003000004", EARLY)
    _listing(conn, "0003000004", "GGG", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(conn, "0003000004", "NASDAQ", accepted, date(2026, 1, 25))
    # The corpus fetch never carried this one (a drifted join key, say).
    _delisting(conn, "0003000004", "NASDAQ", orphan_accepted, date(2026, 6, 11))
    conn.commit()
    conn.close()

    corpus_path = tmp_path / "corpus.jsonl"
    with corpus_path.open("w") as handle:
        handle.write(json.dumps(_minimal_record("LEG", "0003000004", "NASDAQ", accepted)) + "\n")
        # A second, unrelated record so the corpus's fetched span (its
        # accepted_at range) covers `orphan_accepted` too.
        later = datetime(2026, 9, 1, 14, tzinfo=UTC)
        handle.write(json.dumps(_minimal_record("LEH", "0003000099", "NASDAQ", later)) + "\n")

    result = frame_mod.build_frame(corpus_path, T, out)

    assert result.unmatched_delistings == (
        frame_mod.UnjoinedDelisting(
            security_id="0003000004", exchange="NASDAQ", filed_at=orphan_accepted
        ),
    )


def test_a_store_delisting_outside_the_corpus_span_is_not_reported(
    settings: Settings, tmp_path: Path
) -> None:
    """The survivorship check is restricted to the corpus's own fetched
    span: a store `delistings` row for a CIK the corpus names, but filed
    outside every `form25_accepted_at` the corpus carries, is a filing a
    `--since`/`--until`-restricted fetch never asked EDGAR for -- not a
    drift to report."""
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})
    accepted = datetime(2026, 1, 15, 14, tzinfo=UTC)
    outside_span = datetime(2026, 12, 1, 14, tzinfo=UTC)

    conn = duckdb.connect(out.store.path)
    configure_connection(conn)
    schema.init_schema(conn)
    _security(conn, "0003000010", "0003000010", EARLY)
    _listing(conn, "0003000010", "KKK", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(conn, "0003000010", "NASDAQ", accepted, date(2026, 1, 25))
    # Outside the corpus's own span (the fetch was `--until <some earlier
    # date>`, say): known before T, but the corpus names no record anywhere
    # near this date.
    _delisting(conn, "0003000010", "NASDAQ", outside_span, date(2026, 12, 11))
    conn.commit()
    conn.close()

    corpus_path = tmp_path / "corpus.jsonl"
    corpus_path.write_text(
        json.dumps(_minimal_record("LEN", "0003000010", "NASDAQ", accepted)) + "\n"
    )

    result = frame_mod.build_frame(corpus_path, T, out)

    assert result.unmatched_delistings == ()


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


def test_a_listing_row_known_only_after_t_does_not_relist(
    settings: Settings, tmp_path: Path
) -> None:
    """A relisting `listings` row whose `valid_from` is on or before `t`
    (so a latest-state read would include it) but not yet `known_at <= t`
    must not make `rule_relisted` true either: this is the as-of read
    itself (`listings_as_of`), not the extra `valid_from` guard the sibling
    test above checks."""
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})
    accepted = datetime(2026, 2, 2, 14, tzinfo=UTC)

    conn = duckdb.connect(out.store.path)
    configure_connection(conn)
    schema.init_schema(conn)
    _security(conn, "0003000011", "0003000011", EARLY)
    _listing(conn, "0003000011", "LLL", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(conn, "0003000011", "NASDAQ", accepted, date(2026, 2, 12))
    # `valid_from` is well before T, but it is not known until after T.
    _listing(
        conn,
        "0003000011",
        "LLL",
        "NASDAQ",
        date(2026, 3, 1),
        datetime(2027, 6, 1, tzinfo=UTC),
    )
    conn.commit()
    conn.close()

    corpus_path = tmp_path / "corpus.jsonl"
    corpus_path.write_text(
        json.dumps(_minimal_record("LEJ", "0003000011", "NASDAQ", accepted)) + "\n"
    )

    result = frame_mod.build_frame(corpus_path, T, out)

    df = pl.read_parquet(result.frame_path)
    row = _row(df, "LEJ")
    assert (row["rule_status"], row["rule_relisted"]) == ("delisted", False)


def test_a_delisting_known_only_after_t_leaves_the_listing_end_unmatched(
    settings: Settings, tmp_path: Path
) -> None:
    """A `delistings` row filed before `t` but not yet `known_at <= t` must
    not join: the as-of read itself (`delistings_as_of`) excludes it, so
    `rule_status` is `"unmatched"`, never `"delisted"`."""
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})
    accepted = datetime(2026, 1, 15, 14, tzinfo=UTC)

    conn = duckdb.connect(out.store.path)
    configure_connection(conn)
    schema.init_schema(conn)
    _security(conn, "0003000005", "0003000005", EARLY)
    _listing(conn, "0003000005", "HHH", "NASDAQ", date(2020, 1, 1), EARLY)
    # Filed before T, but not recorded (known_at) until after T.
    insert_row(
        conn,
        "delistings",
        {
            "security_id": "0003000005",
            "form": "25",
            "class_title": "Common Stock",
            "exchange": "NASDAQ",
            "filed_at": accepted,
            "effective_on": date(2026, 1, 25),
            "known_at": datetime(2027, 6, 1, tzinfo=UTC),
            "ingested_at": datetime(2027, 6, 1, tzinfo=UTC),
            "source": "edgar",
            "provenance": "filing",
        },
    )
    conn.commit()
    conn.close()

    corpus_path = tmp_path / "corpus.jsonl"
    corpus_path.write_text(
        json.dumps(_minimal_record("LEK", "0003000005", "NASDAQ", accepted)) + "\n"
    )

    result = frame_mod.build_frame(corpus_path, T, out)

    df = pl.read_parquet(result.frame_path)
    assert _row(df, "LEK")["rule_status"] == "unmatched"


def test_a_successor_known_only_after_t_does_not_count(settings: Settings, tmp_path: Path) -> None:
    """A `<cik>@<date>` successor security not yet `known_at <= t` must not
    set `rule_successor_id`, even though its own `<date>` would otherwise
    qualify."""
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})
    accepted = datetime(2026, 4, 2, 14, tzinfo=UTC)

    conn = duckdb.connect(out.store.path)
    configure_connection(conn)
    schema.init_schema(conn)
    _security(conn, "0003000006", "0003000006", EARLY)
    _listing(conn, "0003000006", "III", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(conn, "0003000006", "NASDAQ", accepted, date(2026, 4, 12))
    # Known only after T, even though its own <date> is well before T.
    _security(conn, "0003000006@2026-05-01", "0003000006", datetime(2027, 6, 1, tzinfo=UTC))
    conn.commit()
    conn.close()

    corpus_path = tmp_path / "corpus.jsonl"
    corpus_path.write_text(
        json.dumps(_minimal_record("LES", "0003000006", "NASDAQ", accepted)) + "\n"
    )

    result = frame_mod.build_frame(corpus_path, T, out)

    df = pl.read_parquet(result.frame_path)
    assert _row(df, "LES")["rule_successor_id"] is None


def test_a_form15_marker_outside_the_window_does_not_veto(
    frame_settings: Settings,
) -> None:
    """A `15-12B`/`15-12G` accepted at or before `t` but outside the
    reorganisation window must not set `rule_form15_in_window`."""
    corpus_path = Path(frame_settings.research.data_dir).parent / "corpus_outside_window.jsonl"
    accepted = datetime(2026, 5, 4, 14, tzinfo=UTC)
    record = _minimal_record("LEW", "0001000005", "NASDAQ", accepted)
    record["markers"] = [
        {
            "form": "15-12B",
            "accession": "0001000005-26-000002",
            # Far outside `master.reorganisation_window_sessions` (10) of
            # the Form 25's own filing session.
            "filed_on": "2026-08-01",
            "accepted_at": "2026-08-01T00:00:00+00:00",
        }
    ]
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    corpus_path.write_text(json.dumps(record) + "\n")

    result = frame_mod.build_frame(corpus_path, T, frame_settings)

    df = pl.read_parquet(result.frame_path)
    assert _row(df, "LEW")["rule_form15_in_window"] is False


# LE5's own Form 25 (`frame_settings`) is accepted 2026-05-04T14:00Z, a
# session; `master.reorganisation_window_sessions` (10, default) around its
# filing session gives the window [2026-04-20, 2026-05-18], one session
# short of 2026-05-19 (checked against the real XNYS calendar when these
# tests were written).
def test_a_form15_marker_on_the_window_edge_vetoes_by_its_own_session(
    frame_settings: Settings,
) -> None:
    """A `15-12B` accepted at 18:00 ET on the window's last session (so its
    EDGAR `filed_on`, by the SEC's own late-acceptance convention, lands
    the *next* session, one beyond the window) must still veto: the
    session comes from the marker's own `accepted_at`, never `filed_on`
    (SHOULD FIX 2)."""
    corpus_path = Path(frame_settings.research.data_dir).parent / "corpus_edge_in.jsonl"
    accepted = datetime(2026, 5, 4, 14, tzinfo=UTC)
    record = _minimal_record("LEE1", "0001000005", "NASDAQ", accepted)
    record["markers"] = [
        {
            "form": "15-12B",
            "accession": "0001000005-26-000003",
            # EDGAR's own `filingDate` for an 18:00 ET acceptance: the next
            # session, one beyond the window -- the pass-1 bug would read
            # this and wrongly refuse to veto.
            "filed_on": "2026-05-19",
            "accepted_at": "2026-05-18T22:00:00+00:00",
        }
    ]
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    corpus_path.write_text(json.dumps(record) + "\n")

    result = frame_mod.build_frame(corpus_path, T, frame_settings)

    df = pl.read_parquet(result.frame_path)
    assert _row(df, "LEE1")["rule_form15_in_window"] is True


def test_a_form15_marker_one_session_beyond_the_edge_does_not_veto(
    frame_settings: Settings,
) -> None:
    """A `15-12B` whose own `accepted_at` falls one session beyond the
    window (2026-05-19) must not veto."""
    corpus_path = Path(frame_settings.research.data_dir).parent / "corpus_edge_out.jsonl"
    accepted = datetime(2026, 5, 4, 14, tzinfo=UTC)
    record = _minimal_record("LEE2", "0001000005", "NASDAQ", accepted)
    record["markers"] = [
        {
            "form": "15-12B",
            "accession": "0001000005-26-000004",
            "filed_on": "2026-05-19",
            "accepted_at": "2026-05-19T14:00:00+00:00",
        }
    ]
    corpus_path.parent.mkdir(parents=True, exist_ok=True)
    corpus_path.write_text(json.dumps(record) + "\n")

    result = frame_mod.build_frame(corpus_path, T, frame_settings)

    df = pl.read_parquet(result.frame_path)
    assert _row(df, "LEE2")["rule_form15_in_window"] is False


def test_next_bound_is_the_earliest_later_time_or_none() -> None:
    """`_next_bound` (the per-episode bound `rule_relisted` and
    `rule_successor_id` are computed under): the earliest time strictly
    after `accepted_at`, ignoring earlier and equal times, `None` with
    nothing later."""
    accepted_at = datetime(2026, 3, 1, tzinfo=UTC)
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    later_a = datetime(2026, 6, 1, tzinfo=UTC)
    later_b = datetime(2026, 9, 1, tzinfo=UTC)

    assert frame_mod._next_bound([earlier, later_b, later_a, accepted_at], accepted_at) == later_a
    assert frame_mod._next_bound([earlier, accepted_at], accepted_at) is None
    assert frame_mod._next_bound([], accepted_at) is None


def test_tightest_bound_is_the_earliest_non_none() -> None:
    a = datetime(2026, 1, 1, tzinfo=UTC)
    b = datetime(2026, 6, 1, tzinfo=UTC)

    assert frame_mod._tightest_bound(b, a) == a
    assert frame_mod._tightest_bound(None, a) == a
    assert frame_mod._tightest_bound(None, None) is None


def test_a_relisting_after_the_next_episode_does_not_bound_back(
    settings: Settings, tmp_path: Path
) -> None:
    """Two listing ends of the same security, with a relisting row between
    them (the second's own listing: `derive_listing_ends` never attaches a
    delisting to a listing an earlier filing already ended) *and* a further
    relisting after the second episode. Without the bound, the first
    episode's `rule_relisted` would wrongly pick up the later relisting too
    (it is after its `effective_on` and on or before `t`); with the bound
    (the second episode's own acceptance), it must not."""
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})
    first_accepted = datetime(2026, 1, 15, 14, tzinfo=UTC)
    second_accepted = datetime(2026, 6, 1, 14, tzinfo=UTC)

    conn = duckdb.connect(out.store.path)
    configure_connection(conn)
    schema.init_schema(conn)
    _security(conn, "0003000007", "0003000007", EARLY)
    _listing(conn, "0003000007", "JJJ", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(conn, "0003000007", "NASDAQ", first_accepted, date(2026, 1, 25))
    # The second episode's own listing: on the first episode's own
    # `effective_on`, so it is *not* the first episode's relisting (a
    # relisting row is strictly after `effective_on`) but is still an
    # eligible target for the second delisting (`valid_from` on or before
    # its filing session).
    _listing(
        conn, "0003000007", "JJJ", "NASDAQ", date(2026, 1, 25), datetime(2026, 1, 25, tzinfo=UTC)
    )
    _delisting(conn, "0003000007", "NASDAQ", second_accepted, date(2026, 6, 11))
    # After the second episode: must bound only the second episode's own
    # check, never leak back into the first's.
    _listing(
        conn, "0003000007", "JJJ", "NASDAQ", date(2026, 7, 1), datetime(2026, 7, 1, tzinfo=UTC)
    )
    conn.commit()
    conn.close()

    corpus_path = tmp_path / "corpus.jsonl"
    with corpus_path.open("w") as handle:
        handle.write(
            json.dumps(_minimal_record("LE1ST", "0003000007", "NASDAQ", first_accepted)) + "\n"
        )
        handle.write(
            json.dumps(_minimal_record("LE2ND", "0003000007", "NASDAQ", second_accepted)) + "\n"
        )

    result = frame_mod.build_frame(corpus_path, T, out)

    df = pl.read_parquet(result.frame_path)
    first = _row(df, "LE1ST")
    second = _row(df, "LE2ND")
    # Bounded correctly: the first episode sees no relisting of its own
    # (the only candidate row is exactly at its `effective_on`, excluded,
    # and the later one is beyond its bound); the second does, from the
    # unbounded later relisting.
    assert (first["rule_status"], first["rule_relisted"]) == ("delisted", False)
    assert (second["rule_status"], second["rule_relisted"]) == ("delisted", True)


def test_a_successor_after_the_next_episode_does_not_bound_back(
    settings: Settings, tmp_path: Path
) -> None:
    """Two listing ends of the same CIK on different securities (dual
    class), with a `<cik>@<date>` successor after the *second* episode's
    acceptance: it must count for the second episode's `rule_successor_id`
    (nothing bounds it) but never leak back into the first's, which the
    second episode's own acceptance bounds."""
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})
    first_accepted = datetime(2026, 1, 15, 14, tzinfo=UTC)
    second_accepted = datetime(2026, 6, 1, 14, tzinfo=UTC)

    conn = duckdb.connect(out.store.path)
    configure_connection(conn)
    schema.init_schema(conn)
    cik = "0003000012"
    _security(conn, cik, cik, EARLY)
    _listing(conn, cik, "MMM", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(conn, cik, "NASDAQ", first_accepted, date(2026, 1, 25))
    _security(conn, f"{cik}:B", cik, EARLY)
    _listing(conn, f"{cik}:B", "MMMB", "NASDAQ", date(2020, 1, 1), EARLY)
    _delisting(conn, f"{cik}:B", "NASDAQ", second_accepted, date(2026, 6, 11))
    # Known before T, dated after the second episode's acceptance.
    _security(conn, f"{cik}@2026-07-01", cik, datetime(2026, 7, 2, tzinfo=UTC))
    conn.commit()
    conn.close()

    corpus_path = tmp_path / "corpus.jsonl"
    with corpus_path.open("w") as handle:
        handle.write(json.dumps(_minimal_record("LE1ST", cik, "NASDAQ", first_accepted)) + "\n")
        handle.write(json.dumps(_minimal_record("LE2ND", cik, "NASDAQ", second_accepted)) + "\n")

    result = frame_mod.build_frame(corpus_path, T, out)

    df = pl.read_parquet(result.frame_path)
    first = _row(df, "LE1ST")
    second = _row(df, "LE2ND")
    assert first["rule_successor_id"] is None
    assert second["rule_successor_id"] == f"{cik}@2026-07-01"


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
