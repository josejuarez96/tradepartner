"""SEC ownership event reads obey the acceptance clock and read gate."""

from datetime import UTC, date, datetime

import duckdb
import pytest

from tradepartner.config import EventsConfig, Settings
from tradepartner.store import asof, schema


def _settings(enabled: bool) -> Settings:
    return Settings(_env_file=None, events=EventsConfig(sec_forms_read_enabled=enabled))


def test_sec_form_reads_gate_and_after_close() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    accepted = datetime(2026, 10, 28, 21, 0, tzinfo=UTC)
    before = datetime(2026, 10, 28, 20, 0, tzinfo=UTC)
    conn.execute(
        "INSERT INTO sec_filing_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            "0001",
            "4",
            accepted,
            "2026-10-28",
            "issuer",
            "owner",
            accepted,
            accepted,
            "edgar",
            "filing",
        ],
    )
    conn.execute(
        "INSERT INTO sec_form4_transactions VALUES (" + ", ".join(["?"] * 23) + ")",
        [
            "0001",
            "issuer",
            "owner",
            0,
            False,
            True,
            False,
            False,
            date(2026, 10, 28),
            None,
            "P",
            "A",
            10,
            2,
            10,
            "D",
            "Common",
            None,
            accepted,
            accepted,
            accepted,
            "edgar",
            "filing",
        ],
    )
    with pytest.raises(PermissionError):
        asof.sec_filing_events_as_of(conn, accepted, ["4"], settings=_settings(False))
    with pytest.raises(PermissionError):
        asof.sec_form4_transactions_as_of(conn, accepted, settings=_settings(False))
    assert asof.sec_filing_events_as_of(conn, before, ["4"], settings=_settings(True)).is_empty()
    assert asof.sec_form4_transactions_as_of(conn, before, settings=_settings(True)).is_empty()
    assert len(asof.sec_filing_events_as_of(conn, accepted, ["4"], settings=_settings(True))) == 1
    assert len(asof.sec_form4_transactions_as_of(conn, accepted, settings=_settings(True))) == 1


def test_sec_reads_agree_with_truncated_store() -> None:
    full = duckdb.connect(":memory:")
    truncated = duckdb.connect(":memory:")
    for conn in (full, truncated):
        schema.init_schema(conn)
    early = datetime(2026, 10, 28, 14, tzinfo=UTC)
    late = datetime(2026, 10, 29, 14, tzinfo=UTC)
    for stamp, accession in ((early, "early"), (late, "late")):
        full.execute(
            "INSERT INTO sec_filing_events VALUES (?, 'SC 13D', ?, '2026-10-28', "
            "'target', 'activist', ?, ?, 'edgar', 'filing')",
            [accession, stamp, stamp, stamp],
        )
    full.execute(
        "INSERT INTO sec_form4_transactions VALUES (" + ", ".join(["?"] * 23) + ")",
        [
            "future_form4",
            "issuer",
            "owner",
            0,
            False,
            True,
            False,
            False,
            date(2026, 10, 28),
            None,
            "P",
            "A",
            10,
            2,
            10,
            "D",
            "Common",
            None,
            late,
            late,
            late,
            "edgar",
            "filing",
        ],
    )
    truncated.execute(
        "INSERT INTO sec_filing_events VALUES (?, 'SC 13D', ?, '2026-10-28', "
        "'target', 'activist', ?, ?, 'edgar', 'filing')",
        ["early", early, early, early],
    )
    settings = _settings(True)
    assert asof.sec_filing_events_as_of(full, early, ["SC 13D"], settings=settings).equals(
        asof.sec_filing_events_as_of(truncated, early, ["SC 13D"], settings=settings)
    )
    for conn in (full, truncated):
        conn.execute(
            "INSERT INTO sec_form4_transactions VALUES (" + ", ".join(["?"] * 23) + ")",
            [
                "form4",
                "issuer",
                "owner",
                0,
                False,
                True,
                False,
                False,
                date(2026, 10, 28),
                None,
                "P",
                "A",
                10,
                2,
                10,
                "D",
                "Common",
                None,
                early,
                early,
                early,
                "edgar",
                "filing",
            ],
        )
    assert asof.sec_form4_transactions_as_of(full, early, settings=settings).equals(
        asof.sec_form4_transactions_as_of(truncated, early, settings=settings)
    )
    assert asof.sec_filing_events_as_of(
        full, late, ["SC 13D"], subject_cik="other", settings=settings
    ).is_empty()


def test_version_38_migration_keeps_existing_filing_rows() -> None:
    conn = duckdb.connect(":memory:")
    schema.init_schema(conn)
    stamp = datetime(2026, 10, 28, 14, tzinfo=UTC)
    conn.execute(
        "INSERT INTO filing_events VALUES ('issuer', '8k', '8-K', '2.02', "
        "?, ?, ?, 'edgar', 'filing')",
        [stamp, stamp, stamp],
    )
    before = conn.execute("SELECT * FROM filing_events").fetchall()
    conn.execute("DROP TABLE sec_filing_events")
    conn.execute("DROP TABLE sec_form4_transactions")
    conn.execute("DELETE FROM schema_version WHERE version > 38")
    schema.init_schema(conn)
    assert conn.execute("SELECT * FROM filing_events").fetchall() == before
    assert {"sec_filing_events", "sec_form4_transactions"} <= {
        name for (name,) in conn.execute("SELECT table_name FROM duckdb_tables()").fetchall()
    }
    assert schema.stored_version(conn) == 40
