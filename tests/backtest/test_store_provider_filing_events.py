"""`StoreProvider.filing_events` (plan T416; backtest spec amendment 2026-11-16, #3504,
items 3, 5 and 14 "Provider").

The rows are built here, in a temp-file store with no fixture universe, so the shared
`tests/fixtures/universe/filing_events.csv` (three rows that nine other tests count) is
untouched. Two issuers: `SEC_SOLO` (CIK_SOLO) and a dual-class `SEC_DUAL_A`/`SEC_DUAL_B`
(CIK_DUAL). Times are New York wall times on 2024-03-05 and 2024-03-06 (EST).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import polars as pl
import pytest

from backtest.fake_provider import Call, FakeProvider
from tradepartner.backtest.provider import FILING_INDEX_ACCEPTED_COLUMN, AnnouncementProvider
from tradepartner.backtest.store_provider import StoreProvider
from tradepartner.calendar import next_session, session_close
from tradepartner.config import Settings
from tradepartner.store import registry, schema
from tradepartner.store.asof import filing_events_as_of
from tradepartner.store.db import (
    configure_connection,
    insert_row,
    open_for_write,
    open_read_only,
)

_NY = ZoneInfo("America/New_York")
CIK_SOLO = "CIK0000000001"
CIK_DUAL = "CIK0000000002"
FORMS = ["8-K"]
ITEMS = ["2.02"]

T_SESSION = date(2024, 3, 5)  # Tuesday
T_CLOSE = session_close(T_SESSION)
T_NEXT = next_session(T_SESSION)
T_NEXT_CLOSE = session_close(T_NEXT)

#: accession -> (cik, form, items, accepted_at New York wall time)
ROWS: dict[str, tuple[str, str, str, datetime]] = {
    "A-PRE": (CIK_SOLO, "8-K", "2.02,9.01", datetime(2024, 3, 5, 7, 30, tzinfo=_NY)),
    "A-1630": (CIK_SOLO, "8-K", "2.02", datetime(2024, 3, 5, 16, 30, tzinfo=_NY)),
    "A-AMEND": (CIK_SOLO, "8-K/A", "2.02", datetime(2024, 3, 5, 7, 0, tzinfo=_NY)),
    "A-OTHER": (CIK_SOLO, "8-K", "12.02", datetime(2024, 3, 5, 7, 5, tzinfo=_NY)),
    "A-DUAL": (CIK_DUAL, "8-K", "2.02", datetime(2024, 3, 5, 8, 0, tzinfo=_NY)),
}
#: The index disagrees with the table for this accession only.
MISMATCH = "A-DUAL"
MISMATCH_STAMP = datetime(2024, 3, 5, 9, 0, tzinfo=_NY)
#: In the table but never listed in the index.
UNINDEXED = "A-OTHER"


def _settings(path: Path) -> Settings:
    return Settings(_env_file=None, store={"path": str(path)})


@pytest.fixture(autouse=True)
def _no_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(tmp_path / "none.env"))


def _stamp(at: datetime) -> dict[str, Any]:
    return {"known_at": at, "ingested_at": at + timedelta(minutes=10)}


class Store:
    def __init__(self, path: Path, handle: registry.TrialHandle, settings: Settings) -> None:
        self.path = path
        self.handle = handle
        self.settings = settings
        self.opened = 0
        self.open_now = 0

    def connect(self) -> AbstractContextManager[duckdb.DuckDBPyConnection]:
        @contextmanager
        def _connect() -> Iterator[duckdb.DuckDBPyConnection]:
            with open_read_only(self.settings) as conn:
                self.opened += 1
                self.open_now += 1
                try:
                    yield conn
                finally:
                    self.open_now -= 1

        return _connect()

    def provider(self) -> StoreProvider:
        return StoreProvider(self.connect, self.handle, self.settings)

    @contextmanager
    def direct(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with open_read_only(self.settings) as conn:
            yield conn


@pytest.fixture(scope="module")
def store(tmp_path_factory: pytest.TempPathFactory) -> Store:
    tmp = tmp_path_factory.mktemp("filing_events_provider")
    path = tmp / "events.duckdb"
    settings = _settings(path)
    conn = duckdb.connect(str(path))
    try:
        configure_connection(conn)
        schema.init_schema(conn)
        early = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)
        for sid, cik in (
            ("SEC_SOLO", CIK_SOLO),
            ("SEC_DUAL_A", CIK_DUAL),
            ("SEC_DUAL_B", CIK_DUAL),
        ):
            insert_row(
                conn,
                "securities",
                {
                    "security_id": sid,
                    "cik": cik,
                    "name": sid,
                    "benchmark": False,
                    **_stamp(early),
                    "source": "edgar",
                    "provenance": "filing",
                },
            )
        for accession, (cik, form, items, accepted_at) in ROWS.items():
            insert_row(
                conn,
                "filing_events",
                {
                    "cik": cik,
                    "accession": accession,
                    "form": form,
                    "items": items,
                    "accepted_at": accepted_at,
                    **_stamp(accepted_at),
                    "source": "edgar",
                    "provenance": "filing",
                },
            )
            if accession == UNINDEXED:
                continue
            stamp = MISMATCH_STAMP if accession == MISMATCH else accepted_at
            insert_row(
                conn,
                "filing_index",
                {
                    "cik": cik,
                    "accession": accession,
                    "form": form,
                    "company_name": cik,
                    "filed_on": accepted_at.astimezone(_NY).date(),
                    "accepted_at": stamp,
                    "listed_on": accepted_at.astimezone(_NY).date(),
                    "list_position": None,
                    **_stamp(stamp),
                    "source": "edgar",
                    "provenance": "filing",
                },
            )
            if accession == "A-1630":  # a later filed-on revision must not move the stamp
                revised = datetime(2024, 3, 7, 12, 0, tzinfo=UTC)
                insert_row(
                    conn,
                    "filing_index",
                    {
                        "cik": cik,
                        "accession": accession,
                        "form": form,
                        "company_name": cik,
                        "filed_on": date(2024, 3, 6),
                        "accepted_at": stamp,
                        "listed_on": date(2024, 3, 6),
                        "list_position": None,
                        **_stamp(revised),
                        "source": "edgar",
                        "provenance": "filing",
                    },
                )
    finally:
        conn.close()
    registry_settings = _settings(tmp / "real.duckdb")
    with open_for_write(settings) as conn:
        hypothesis = registry.register_hypothesis(
            conn,
            slug="h-events",
            family="momentum",
            title="test",
            doc_path="docs/hypotheses/h-events.md",
            doc_sha256="0" * 64,
            params={"costs.per_side_bps": 15.0},
            in_sample_start=date(2018, 1, 31),
            holdout_start=date(2023, 1, 1),
            holdout_end=date(2025, 12, 31),
            registered_by="owner",
            settings=registry_settings,
        )
        handle = registry.open_trial(
            conn,
            hypothesis_id=hypothesis.hypothesis_id,
            kind="in_sample",
            start_session=date(2024, 1, 31),
            end_session=date(2024, 3, 29),
            data_cutoff=datetime(2024, 3, 29, 20, tzinfo=UTC),
            synthetic=True,
            run_by="test",
            settings=registry_settings,
        )
    return Store(path, handle, settings)


IDS = ["SEC_SOLO", "SEC_DUAL_A", "SEC_DUAL_B"]


def _read(store: Store, t: datetime, ids: list[str] | None = None) -> pl.DataFrame:
    with store.provider() as provider:
        return provider.filing_events(t, ids or IDS, forms=FORMS, items=ITEMS)


def _accessions(frame: pl.DataFrame) -> set[str]:
    return set(frame["accession"].to_list())


def test_is_a_data_provider(store: Store) -> None:
    with store.provider() as provider:
        assert isinstance(provider, AnnouncementProvider)
    assert isinstance(FakeProvider(pl.DataFrame(), {}, {}), AnnouncementProvider)


def test_8k_accepted_after_close_is_invisible_at_t_and_visible_at_next_read(store: Store) -> None:
    at_t = _read(store, T_CLOSE)
    assert "A-1630" not in _accessions(at_t)
    assert "A-PRE" in _accessions(at_t)  # accepted 07:30, before close(T)
    at_next = _read(store, T_NEXT_CLOSE)
    assert "A-1630" in _accessions(at_next)


def test_1630_row_is_invisible_at_close_t_and_first_visible_at_t_plus_1(store: Store) -> None:
    accepted = ROWS["A-1630"][3]
    assert accepted.date() == T_SESSION  # a `filed`-keyed table would show it at T
    just_before = _read(store, accepted - timedelta(microseconds=1))
    at_accept = _read(store, accepted)
    assert "A-1630" not in _accessions(_read(store, T_CLOSE))
    assert "A-1630" not in _accessions(just_before)
    assert "A-1630" in _accessions(at_accept)
    # Its first read at a session close is close(T + 1): s0 = T + 1.
    closes = [session_close(T_SESSION), session_close(T_NEXT)]
    first = next(c for c in closes if "A-1630" in _accessions(_read(store, c)))
    assert first == T_NEXT_CLOSE


def test_a_filed_keyed_read_would_fail_the_same_check(store: Store) -> None:
    # The teeth: key the same store on the index's `filed_on` and the 16:30 row shows at
    # T's close, which the acceptance-keyed provider read refuses.
    with store.direct() as conn:
        filed_keyed = {
            row[0]
            for row in conn.execute(
                "SELECT accession FROM filing_index WHERE filed_on <= ? AND form = '8-K'",
                [T_SESSION],
            ).fetchall()
        }
    assert "A-1630" in filed_keyed
    assert "A-1630" not in _accessions(_read(store, T_CLOSE))


def test_form_and_item_filters_are_exact(store: Store) -> None:
    got = _accessions(_read(store, T_NEXT_CLOSE))
    assert "A-AMEND" not in got  # 8-K/A is never an 8-K
    assert "A-OTHER" not in got  # 12.02 is not 2.02
    assert got == {"A-PRE", "A-1630", "A-DUAL"}


def test_one_row_per_security_and_accession_for_a_dual_class_issuer(store: Store) -> None:
    frame = _read(store, T_NEXT_CLOSE)
    dual = frame.filter(pl.col("accession") == "A-DUAL")
    assert sorted(dual["security_id"].to_list()) == ["SEC_DUAL_A", "SEC_DUAL_B"]
    assert not frame.select(pl.struct("security_id", "accession").is_duplicated().any()).item()


def test_restricted_to_the_ids(store: Store) -> None:
    frame = _read(store, T_NEXT_CLOSE, ["SEC_DUAL_B"])
    assert set(frame["security_id"].to_list()) == {"SEC_DUAL_B"}
    assert _read(store, T_NEXT_CLOSE, ["SEC_UNKNOWN"]).is_empty()


def test_cross_check_column_is_the_index_first_known_stamp_and_flags_a_mismatch(
    store: Store,
) -> None:
    frame = _read(store, T_NEXT_CLOSE)
    assert frame.columns[-1] == FILING_INDEX_ACCEPTED_COLUMN
    pre = frame.filter(pl.col("accession") == "A-PRE")
    assert (pre[FILING_INDEX_ACCEPTED_COLUMN] == pre["accepted_at"]).all()
    # A later filed-on revision of the index row does not move the stamp.
    late = frame.filter(pl.col("accession") == "A-1630")
    assert (late[FILING_INDEX_ACCEPTED_COLUMN] == late["accepted_at"]).all()
    planted = frame.filter(pl.col("accession") == MISMATCH)
    assert planted.height == 2
    assert planted[FILING_INDEX_ACCEPTED_COLUMN].to_list() == [MISMATCH_STAMP] * 2
    assert all(
        stamp != accepted
        for stamp, accepted in zip(
            planted[FILING_INDEX_ACCEPTED_COLUMN], planted["accepted_at"], strict=True
        )
    )


def test_cross_check_is_null_when_the_index_has_no_row_known_at_t(store: Store) -> None:
    # A-PRE's index row is stamped 07:30 NY; before it the event row is not visible either,
    # so use the unindexed accession through a wider item filter.
    with store.provider() as provider:
        frame = provider.filing_events(T_NEXT_CLOSE, IDS, forms=FORMS, items=["12.02"])
    assert _accessions(frame) == {UNINDEXED}
    assert frame[FILING_INDEX_ACCEPTED_COLUMN].null_count() == frame.height


def test_equals_the_as_of_function_plus_the_column(store: Store) -> None:
    with store.direct() as conn:
        want = filing_events_as_of(conn, T_NEXT_CLOSE, FORMS, ITEMS)
    got = _read(store, T_NEXT_CLOSE)
    assert got.drop(FILING_INDEX_ACCEPTED_COLUMN).equals(want)


def test_read_uses_the_short_lived_read_only_connection(store: Store) -> None:
    before = store.opened
    with store.provider() as provider:
        provider.filing_events(T_CLOSE, IDS, forms=FORMS, items=ITEMS)
        assert store.opened == before + 2  # the registry check, then the step's connection
        assert store.open_now == 1
        provider.filing_events(T_CLOSE, IDS, forms=FORMS, items=ITEMS)
        assert store.opened == before + 2  # same step, same connection
        provider.filing_events(T_NEXT_CLOSE, IDS, forms=FORMS, items=ITEMS)
        assert store.opened == before + 3
    assert store.open_now == 0


def test_bare_strings_and_naive_t_raise(store: Store) -> None:
    with store.provider() as provider:
        with pytest.raises(TypeError):
            provider.filing_events(T_CLOSE, "SEC_SOLO", forms=FORMS, items=ITEMS)
        with pytest.raises(TypeError):
            provider.filing_events(T_CLOSE, IDS, forms="8-K", items=ITEMS)  # type: ignore[arg-type]
        with pytest.raises((TypeError, ValueError)):
            provider.filing_events(T_CLOSE.replace(tzinfo=None), IDS, forms=FORMS, items=ITEMS)


# --- the recording fake ---------------------------------------------------------------


def _fake() -> FakeProvider:
    rows = pl.DataFrame(
        {
            "security_id": ["SEC_SOLO", "SEC_SOLO"],
            "cik": [CIK_SOLO, CIK_SOLO],
            "accession": ["A-PRE", "A-1630"],
            "form": ["8-K", "8-K"],
            "items": ["2.02,9.01", "2.02"],
            "accepted_at": [ROWS["A-PRE"][3], ROWS["A-1630"][3]],
            "known_at": [ROWS["A-PRE"][3], ROWS["A-1630"][3]],
            FILING_INDEX_ACCEPTED_COLUMN: [ROWS["A-PRE"][3], ROWS["A-1630"][3]],
        }
    ).with_columns(pl.col(pl.Datetime).dt.convert_time_zone("UTC").cast(pl.Datetime("us", "UTC")))
    return FakeProvider(pl.DataFrame(), {}, {}, filing_event_rows=rows)


def test_the_recording_fake_records_the_read_and_hides_the_late_row() -> None:
    fake = _fake()
    at_t = fake.filing_events(T_CLOSE, ["SEC_SOLO"], forms=FORMS, items=ITEMS)
    at_next = fake.filing_events(T_NEXT_CLOSE, ["SEC_SOLO"], forms=FORMS, items=ITEMS)
    assert _accessions(at_t) == {"A-PRE"}
    assert _accessions(at_next) == {"A-PRE", "A-1630"}
    assert fake.calls[0] == Call(
        "filing_events", T_CLOSE, ids=("SEC_SOLO",), forms=("8-K",), items=("2.02",)
    )
    assert fake.read_times() == {T_CLOSE, T_NEXT_CLOSE}


def test_index_read_is_cut_at_t_between_acceptance_and_index_stamp(store: Store) -> None:
    # A-DUAL is accepted 08:00 NY; the index row is stamped (known) 09:00 NY. At 08:30 the
    # event is visible but the index has no row known yet, so the cross-check is null.
    at = datetime(2024, 3, 5, 8, 30, tzinfo=_NY)
    frame = _read(store, at).filter(pl.col("accession") == MISMATCH)
    assert frame.height == 2
    assert frame[FILING_INDEX_ACCEPTED_COLUMN].null_count() == 2
    later = _read(store, datetime(2024, 3, 5, 9, 0, tzinfo=_NY)).filter(
        pl.col("accession") == MISMATCH
    )
    assert later[FILING_INDEX_ACCEPTED_COLUMN].to_list() == [MISMATCH_STAMP] * 2


def test_a_filed_on_index_revision_known_at_t_does_not_move_the_stamp(store: Store) -> None:
    t = session_close(date(2024, 3, 7))  # after the A-1630 revision (2024-03-07 12:00 UTC)
    row = _read(store, t).filter(pl.col("accession") == "A-1630")
    assert row[FILING_INDEX_ACCEPTED_COLUMN].to_list() == row["accepted_at"].to_list()
