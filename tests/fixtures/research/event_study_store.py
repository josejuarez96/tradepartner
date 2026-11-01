"""Synthetic, file-backed event-study store for T302 and downstream tests."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from tradepartner.calendar import is_session, previous_session, session_close
from tradepartner.config import Settings
from tradepartner.store import schema
from tradepartner.store.db import configure_connection, insert_row


def build_event_study_store(
    path: Path,
    *,
    truncate_at: date | None = None,
    identity_known_at: datetime | None = None,
) -> Settings:
    """Build a store with two securities and facts on both sides of the study cut."""
    cutoff = None
    if truncate_at is not None:
        last_session = truncate_at if is_session(truncate_at) else previous_session(truncate_at)
        cutoff = session_close(last_session)

    def included(known_at: datetime) -> bool:
        return cutoff is None or known_at <= cutoff

    settings = Settings(_env_file=None).model_copy(
        update={"store": Settings(_env_file=None).store.model_copy(update={"path": str(path)})}
    )
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        early = identity_known_at or datetime(2018, 6, 1, 15, tzinfo=UTC)
        for sid, benchmark in (("ISSUER", False), ("SPY_ID", True)):
            insert_row(
                conn,
                "securities",
                {
                    "security_id": sid,
                    "cik": sid,
                    "name": sid,
                    "benchmark": benchmark,
                    "known_at": early,
                    "ingested_at": early,
                    "source": "test",
                    "provenance": "snapshot",
                },
            )
            insert_row(
                conn,
                "listings",
                {
                    "security_id": sid,
                    "ticker": "SPY" if benchmark else "AAA",
                    "exchange": "XNYS",
                    "valid_from": date(2018, 6, 1),
                    "known_at": early,
                    "ingested_at": early,
                    "source": "test",
                    "provenance": "snapshot",
                },
            )
            for session, price in ((date(2018, 6, 4), 10.0), (date(2018, 6, 8), 11.0)):
                known_at = datetime(session.year, session.month, session.day, 20, tzinfo=UTC)
                if included(known_at):
                    insert_row(
                        conn,
                        "prices_daily",
                        {
                            "security_id": sid,
                            "session": session,
                            "open": price,
                            "high": price,
                            "low": price,
                            "close": price,
                            "volume": 1000,
                            "known_at": known_at,
                            "ingested_at": known_at,
                            "source": "test",
                            "provenance": "bar",
                        },
                    )
            future = datetime(2018, 6, 11, 20, tzinfo=UTC)
            if included(future):
                insert_row(
                    conn,
                    "prices_daily",
                    {
                        "security_id": sid,
                        "session": date(2018, 6, 11),
                        "open": 99.0,
                        "high": 99.0,
                        "low": 99.0,
                        "close": 99.0,
                        "volume": 1000,
                        "known_at": future,
                        "ingested_at": future,
                        "source": "test",
                        "provenance": "bar",
                    },
                )
            late_revision = datetime(2018, 6, 8, 21, tzinfo=UTC)
            if included(late_revision):
                insert_row(
                    conn,
                    "prices_daily",
                    {
                        "security_id": sid,
                        "session": date(2018, 6, 8),
                        "open": 22.0,
                        "high": 22.0,
                        "low": 22.0,
                        "close": 22.0,
                        "volume": 1000,
                        "known_at": late_revision,
                        "ingested_at": late_revision,
                        "source": "test",
                        "provenance": "bar",
                    },
                )
        for kind, ex_date, amount, known_at in (
            ("dividend", date(2018, 6, 8), 1.0, datetime(2018, 6, 7, 20, tzinfo=UTC)),
            ("split", date(2018, 6, 8), 2.0, datetime(2018, 6, 11, 20, tzinfo=UTC)),
        ):
            if included(known_at):
                insert_row(
                    conn,
                    "corporate_actions",
                    {
                        "security_id": "ISSUER",
                        "action_type": kind,
                        "ex_date": ex_date,
                        "ratio_or_amount": amount,
                        "known_at": known_at,
                        "ingested_at": known_at,
                        "source": "test",
                        "provenance": "action",
                    },
                )
        for value, known_at in (
            (1_000_000.0, datetime(2018, 6, 4, 20, tzinfo=UTC)),
            (2_000_000.0, datetime(2018, 6, 7, 20, tzinfo=UTC)),
            (3_000_000.0, datetime(2018, 6, 11, 20, tzinfo=UTC)),
        ):
            if not included(known_at):
                continue
            insert_row(
                conn,
                "facts",
                {
                    "security_id": "ISSUER",
                    "fact_name": "shares_outstanding",
                    "as_of_date": known_at.date(),
                    "class_member": "",
                    "value": value,
                    "known_at": known_at,
                    "ingested_at": known_at,
                    "source": "test",
                    "provenance": "filing",
                },
            )
    finally:
        conn.close()
    return settings
