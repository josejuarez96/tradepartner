"""The additive statement backfill's equality and look-ahead proof (T365b, #2826).

Built on T323b's simulated SEC (`test_incremental_edgar`: `_Sec`, `_SimSource`,
the two-store settings, the row-for-row table reader). A small multi-day
history of four issuers runs nightly under the default statement names; two
names (`net_income`, `operating_income`) are then added to
`edgar.statement_tags` and `ingest --statement-backfill` fills them. The
adapter is the real `EdgarFilingSource` in its statement-backfill mode (stamps
from the stored `filing_index`, nothing fetched). Only the per-CIK
companyfacts answer is simulated: `_StatementSim.statement_facts` builds each
CIK's payload from the scenario, runs the adapter's own `parse_statement_facts`
(tag precedence, periods, units) and its own `_stamp_statement` (form and
acceptance from the stamps, the comparative flag).

Every store lives under `tmp_path` (the T323b guard is reused).
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from lookahead.harness import PROBE_EPSILON, TruncatedStore
from lookahead.test_incremental_edgar import (  # noqa: F401  (autouse store guard)
    SCENARIO,
    _eastern,
    _Filing,
    _night,
    _NoPrices,
    _rows,
    _Sec,
    _SimSource,
    _store_under_tmp,
    _tables,
    _with,
)
from lookahead.test_incremental_edgar import _settings as _two_store_settings
from tradepartner import ingest as ingest_module
from tradepartner.adapters.edgar_source import EdgarFilingSource
from tradepartner.adapters.filings import StatementFactRecord
from tradepartner.config import Settings
from tradepartner.health import statement_counts
from tradepartner.ingest import IngestResult, ingest_session
from tradepartner.store.asof import statement_facts_as_of

assert SCENARIO.is_file()  # T323b's fixture directory: the builder this proof sits on

ALPHA, BETA, GAMMA, DELTA = "0000000401", "0000000402", "0000000403", "0000000404"
ALL = (ALPHA, BETA, GAMMA, DELTA)
NEW_NAMES = ("net_income", "operating_income")
NET = "us-gaap:NetIncomeLoss"
OPERATING = "us-gaap:OperatingIncomeLoss"
OPERATING_FALLBACK = "us-gaap:IncomeLossFromContinuingOperationsBeforeIncomeTaxes"
ASSETS = "us-gaap:Assets"
REVENUE = "us-gaap:Revenues"

SEED = date(2026, 2, 2)
LAST_NIGHT = date(2026, 2, 12)
NIGHTLY = date(2026, 2, 13)
FY = (date(2025, 1, 1), date(2025, 12, 31))
PRIOR_FY = (date(2024, 1, 1), date(2024, 12, 31))
QUARTER = (date(2025, 10, 1), date(2025, 12, 31))

#: (tag, period start or None for an instant, period end, value)
Entry = tuple[str, date | None, date, float]


@dataclass
class _World:
    """The SEC of the proof and, beside it, what each accession's companyfacts
    entries are."""

    sec: _Sec
    entries: dict[str, list[Entry]]
    filings: dict[str, _Filing] = field(default_factory=dict)


def _filing(cik: str, form: str, number: int, day: date, hour: int, minute: int = 0) -> _Filing:
    name = {ALPHA: "Alpha", BETA: "Beta", GAMMA: "Gamma", DELTA: "Delta"}[cik]
    return _Filing(
        cik,
        f"{name} Corp",
        form,
        f"{cik}-26-{number:06d}",
        _eastern(day, hour, minute),
        day,
        day,
        ticker=name[:3].upper(),
        exchange="NYSE",
    )


def _world() -> _World:
    feb = lambda d: date(2026, 2, d)  # noqa: E731
    k_alpha = _filing(ALPHA, "10-K", 1, feb(3), 17)
    k_alpha_a = _filing(ALPHA, "10-K/A", 2, feb(10), 17)
    q_beta = _filing(BETA, "10-Q", 1, feb(4), 16, 30)  # after the close
    k_gamma = _filing(GAMMA, "10-K", 1, feb(3), 18)
    a_gamma = _filing(GAMMA, "10-K/A", 2, feb(5), 11)  # the first carrier of the new names
    q_delta = _filing(DELTA, "10-Q", 1, feb(6), 9)
    q_alpha = _filing(ALPHA, "10-Q", 3, NIGHTLY, 17)  # tonight's, after the backfill
    filings = [k_alpha, k_alpha_a, q_beta, k_gamma, a_gamma, q_delta, q_alpha]
    entries: dict[str, list[Entry]] = {
        k_alpha.accession: [
            (REVENUE, *FY, 100.0),
            (ASSETS, None, FY[1], 500.0),
            (NET, *FY, 10.0),
            (NET, *PRIOR_FY, 8.0),  # a comparative period
            (OPERATING, *FY, 15.0),
            (OPERATING_FALLBACK, *FY, 16.0),  # both tags of the list: the first wins
        ],
        # Restates net income after the 10-K (counted, never stored); the prior
        # year repeats its value, which is no restatement.
        k_alpha_a.accession: [(NET, *FY, 11.0), (NET, *PRIOR_FY, 8.0)],
        q_beta.accession: [(NET, *QUARTER, 3.0), (OPERATING, *QUARTER, 4.0)],
        k_gamma.accession: [(REVENUE, *FY, 200.0), (ASSETS, None, FY[1], 900.0)],
        a_gamma.accession: [
            (REVENUE, *FY, 200.0),
            (NET, *FY, 20.0),
            (OPERATING_FALLBACK, *FY, 25.0),  # only the fallback tag is carried
        ],
        q_delta.accession: [(REVENUE, *QUARTER, 30.0), (NET, *QUARTER, 2.5)],
        q_alpha.accession: [
            (NET, date(2026, 1, 1), date(2026, 1, 31), 2.0),
            (OPERATING, date(2026, 1, 1), date(2026, 1, 31), 3.0),
        ],
    }
    issuers = {
        cik: (f"{name} Corp", name[:3].upper(), "NYSE")
        for cik, name in ((ALPHA, "Alpha"), (BETA, "Beta"), (GAMMA, "Gamma"), (DELTA, "Delta"))
    }
    sec = _Sec(filings, issuers, fsn={"2025_12": _eastern(date(2026, 1, 2), 12)})
    return _World(sec, entries, {f.accession: f for f in filings})


class _StatementSim(_SimSource):
    """T323b's adapter over `_Sec`, with the one companyfacts payload simulated.

    `statement_facts` is the adapter's own: its cache file (keyed by the latest
    cover-form accession and the tag digest), `parse_statement_facts` (tag
    precedence, periods, units), `_stamp_statement` (form, acceptance and the
    comparative flag from the stamps) and the counts. Only the payload the
    adapter would read from `companyfacts.zip` or the per-CIK API is built
    here, from the scenario, as of the clock: SEC's file lists what is filed."""

    def __init__(self, settings: Settings, world: _World, **kwargs: Any) -> None:
        super().__init__(settings, world.sec, **kwargs)
        self._world = world
        self.payload_reads: list[str] = []

    def statement_facts(self, cik: str) -> list[StatementFactRecord]:
        """T323b's `_SimSource` answers `[]`; this proof wants the adapter's own."""
        return EdgarFilingSource.statement_facts(self, cik)

    def _absent_and_empty(self, cik: str, path: Path, latest: Any) -> bool:
        return False  # the simulation has no zip to lack a member

    def _company_facts_payload(self, cik: str, latest: str) -> tuple[Any | None, bool, str | None]:
        self.payload_reads.append(cik)
        payload: dict[str, Any] = {"cik": int(cik), "facts": {}}
        for filing in self._sec.of_cik(cik):
            if filing.accepted > self._sec.now:
                continue
            for tag, start, end, value in self._world.entries.get(filing.accession, ()):
                taxonomy, _, local = tag.partition(":")
                entry: dict[str, Any] = {
                    "end": end.isoformat(),
                    "val": value,
                    "accn": filing.accession,
                    "fy": end.year,
                    "fp": "FY",
                    "form": filing.form,
                    "filed": filing.filed_on.isoformat(),
                }
                if start is not None:
                    entry["start"] = start.isoformat()
                concept = payload["facts"].setdefault(taxonomy, {}).setdefault(local, {"units": {}})
                concept["units"].setdefault("USD", []).append(entry)
        if not payload["facts"]:
            return None, True, None
        return payload, True, "simulated"


# --- the stores ----------------------------------------------------------------


def _added(settings: Settings, **edgar: Any) -> Settings:
    """`settings` with the two new names appended to `statement_tags`."""
    tags = {
        **settings.edgar.statement_tags,
        "net_income": [NET],
        "operating_income": [OPERATING, OPERATING_FALLBACK],
    }
    return _with(settings, statement_tags=tags, **edgar)


def _run(
    settings: Settings,
    world: _World,
    at: datetime,
    *,
    backfill: bool = False,
    rebuild: bool = False,
    clock: datetime | None = None,
) -> tuple[IngestResult, _StatementSim]:
    world.sec.now = at
    local = {"reuse_cached": True, "statement_backfill": True} if backfill else {}
    source = _StatementSim(settings, world, **local)
    try:
        result = ingest_session(
            settings,
            prices=_NoPrices(),
            filings=source,
            source="edgar",
            clock=lambda: clock or at,
            rebuild_index=rebuild,
            statement_backfill=backfill,
        )
    finally:
        source.close()
    return result, source


@dataclass
class _Pre:
    """The store after the incremental nights, before any backfill."""

    world: _World
    root: Path
    settings: Settings  # the default names
    at: datetime  # the backfill's and the rebuild's one clock: the last night's


@pytest.fixture(scope="module")
def pre_backfill(tmp_path_factory: pytest.TempPathFactory) -> _Pre:
    """Seed with `--rebuild-index`, then run each night to `LAST_NIGHT`
    incrementally under the default names."""
    world = _world()
    root = tmp_path_factory.mktemp("pre-backfill")
    settings = _two_store_settings(root, "store", 2026)
    nights = [SEED + timedelta(days=n) for n in range((LAST_NIGHT - SEED).days + 1)]
    for number, night in enumerate(nights):
        result, _ = _run(settings, world, _night(night), rebuild=number == 0)
        assert result.ok, [(r.source, r.status, r.message) for r in result.runs]
    return _Pre(world, root, settings, _night(LAST_NIGHT))


def _copy(pre: _Pre, name: str, tmp: Path) -> Settings:
    """A copy of the pre-backfill store and its EDGAR cache, as its own settings."""
    shutil.copytree(pre.root / "store", tmp / name)
    return _with_paths(pre.settings, tmp / name)


def _with_paths(settings: Settings, directory: Path) -> Settings:
    store = settings.store.model_copy(update={"path": str(directory / "store.duckdb")})
    edgar = settings.edgar.model_copy(update={"cache_dir": str(directory / "edgar-cache")})
    return settings.model_copy(update={"store": store, "edgar": edgar})


def _backfilled(pre: _Pre, name: str, tmp: Path, **edgar: Any) -> tuple[Settings, IngestResult]:
    base = _copy(pre, name, tmp)
    added = _added(base, **edgar)
    result, _ = _run(added, pre.world, pre.at, backfill=True)
    return added, result


def _tables_but_runs(settings: Settings) -> dict[str, Any]:
    return _tables(settings.store.path)


def _new_rows(settings: Settings) -> list[tuple[Any, ...]]:
    return _rows(
        settings,
        "SELECT cik, fact_name, xbrl_tag, period_end, value, form, filing_accession, "
        "comparative, known_at FROM statement_facts WHERE fact_name IN (?, ?) "
        "ORDER BY cik, fact_name, period_end, known_at",
        list(NEW_NAMES),
    )


@pytest.fixture(scope="module")
def backfilled(pre_backfill: _Pre, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    settings, result = _backfilled(
        pre_backfill, "backfilled", tmp_path_factory.mktemp("backfilled"),
        statement_backfill_chunk_ciks=2,
    )  # fmt: skip
    assert result.ok, [(r.status, r.message) for r in result.runs]
    return settings


# --- Equality --------------------------------------------------------------------


def test_the_backfill_equals_rebuild_index_over_a_copy_of_the_store(
    pre_backfill: _Pre, backfilled: Settings, tmp_path: Path
) -> None:
    """Every table but `ingestion_runs` (and the migration ledger's wall clock),
    row for row, under one config and one clock."""
    rebuilt = _added(_copy(pre_backfill, "rebuilt", tmp_path))
    result, _ = _run(rebuilt, pre_backfill.world, pre_backfill.at, rebuild=True)
    assert result.ok, [(r.status, r.message) for r in result.runs]
    ours, theirs = _tables_but_runs(backfilled), _tables_but_runs(rebuilt)
    assert ours.keys() == theirs.keys()
    assert {name for name in ours if ours[name] != theirs[name]} == set()
    names = {name for _, name, *_ in _new_rows(backfilled)}
    assert names == set(NEW_NAMES)  # the comparison was not between two empty additions


# --- Resume ----------------------------------------------------------------------


@pytest.mark.parametrize("stop", range(len(ALL)))
def test_a_backfill_interrupted_after_each_chunk_resumes_to_the_same_end_state(
    stop: int,
    pre_backfill: _Pre,
    backfilled: Settings,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One CIK a chunk: the run dies in chunk `stop` (so `stop` chunks are
    committed), and the next run resumes to the uninterrupted end state."""
    original = ingest_module._write_statement_facts
    calls: list[Sequence[str]] = []

    def broken(conn: Any, filings: Any, chunk: Sequence[str], *args: Any, **kw: Any) -> Any:
        if len(calls) == stop:
            raise RuntimeError(f"interrupted in chunk {stop}")
        calls.append(chunk)
        return original(conn, filings, chunk, *args, **kw)

    added = _added(_copy(pre_backfill, "resume", tmp_path), statement_backfill_chunk_ciks=1)
    monkeypatch.setattr(ingest_module, "_write_statement_facts", broken)
    failed, _ = _run(added, pre_backfill.world, pre_backfill.at, backfill=True)
    assert not failed.ok
    assert calls == [[cik] for cik in ALL[:stop]]
    partial = "SELECT chunk_cursor FROM ingestion_runs WHERE mode = 'statement_backfill' " \
        "AND status = 'partial' ORDER BY rowid"  # fmt: skip
    assert len(_rows(added, partial)) == stop
    monkeypatch.setattr(ingest_module, "_write_statement_facts", original)
    resumed, _ = _run(added, pre_backfill.world, pre_backfill.at, backfill=True)
    assert resumed.ok, [(r.status, r.message) for r in resumed.runs]
    # The committed chunks are not written again: one `partial` row per CIK, once.
    through = [cursor.split(";")[1] for (cursor,) in _rows(added, partial)]
    assert through == [f"through={cik}" for cik in ALL]
    assert _tables_but_runs(added) == _tables_but_runs(backfilled)


# --- No look-ahead ----------------------------------------------------------------


def _frame(conn: duckdb.DuckDBPyConnection, t: datetime) -> pl.DataFrame:
    return statement_facts_as_of(conn, t).sort(
        "security_id", "fact_name", "period_end", "period_days"
    )


def test_statement_facts_as_of_is_truncation_invariant_around_every_new_row(
    backfilled: Settings,
) -> None:
    """f(T) on the full store equals f(T) on the store cut to `known_at <= T`,
    one second either side of every new row's `known_at`."""
    known = sorted({row[-1] for row in _new_rows(backfilled)})
    assert len(known) >= 4
    probes = sorted(
        {k + delta for k in known for delta in (timedelta(seconds=-1), timedelta(seconds=1))}
        | {k + delta for k in known for delta in (-PROBE_EPSILON, PROBE_EPSILON)}
    )
    with duckdb.connect(backfilled.store.path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        cut = TruncatedStore(conn)
        try:
            sizes = set()
            for t in probes:
                full = _frame(conn, t)
                assert full.equals(_frame(cut.at(t), t)), t
                assert full["known_at"].max() is None or full["known_at"].max() <= t
                sizes.add(len(full))
        finally:
            cut.close()
    assert len(sizes) > 4  # the reads grow row by row, so the probes bite


def test_an_after_close_acceptance_is_invisible_at_the_preceding_close(
    backfilled: Settings,
) -> None:
    accepted = _eastern(date(2026, 2, 4), 16, 30)
    close = _eastern(date(2026, 2, 4), 16, 0)
    with duckdb.connect(backfilled.store.path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        at_close = _frame(conn, close)
        later = _frame(conn, accepted)
        before = _frame(conn, accepted - PROBE_EPSILON)
    stored = _rows(backfilled, "SELECT known_at FROM statement_facts WHERE cik = ? "
                   "AND fact_name IN (?, ?)", [BETA, *NEW_NAMES])  # fmt: skip
    assert {row[0] for row in stored} == {accepted.astimezone(UTC)}
    assert at_close.filter(pl.col("cik") == BETA).is_empty()
    assert before.filter(pl.col("cik") == BETA).is_empty()
    assert set(later.filter(pl.col("cik") == BETA)["fact_name"]) == set(NEW_NAMES)


# --- Restatement ------------------------------------------------------------------


def test_a_10_ka_restating_a_new_name_is_counted_and_never_stored(
    pre_backfill: _Pre, backfilled: Settings
) -> None:
    (message,) = _rows(
        backfilled,
        "SELECT message FROM ingestion_runs WHERE mode = 'statement_backfill' AND status = 'ok'",
    )[0]
    counts = statement_counts(message)
    assert counts["restated"] == 1  # Alpha's 10-K/A: net income 11 against 10
    assert counts["added"] == len(_new_rows(backfilled))
    amended = pre_backfill.world.filings[f"{ALPHA}-26-000002"]
    kept = _rows(
        backfilled,
        "SELECT value, filing_accession FROM statement_facts "
        "WHERE cik = ? AND fact_name = 'net_income' AND period_end = ?",
        [ALPHA, FY[1]],
    )
    assert kept == [(10.0, f"{ALPHA}-26-000001")]
    assert not _rows(backfilled, "SELECT 1 FROM statement_facts WHERE filing_accession = ?",
                     [amended.accession])  # fmt: skip
    with duckdb.connect(backfilled.store.path, read_only=True) as conn:
        conn.execute("SET TimeZone='UTC'")
        for t in (amended.accepted - timedelta(seconds=1), amended.accepted + timedelta(seconds=1)):
            frame = _frame(conn, t).filter(
                (pl.col("cik") == ALPHA)
                & (pl.col("fact_name") == "net_income")
                & (pl.col("period_end") == FY[1])
            )
            assert frame["value"].to_list() == [10.0], t


# --- Tag precedence ----------------------------------------------------------------


def test_one_filing_carrying_both_tags_of_a_list_stores_the_first(backfilled: Settings) -> None:
    rows = _rows(
        backfilled,
        "SELECT xbrl_tag, value FROM statement_facts "
        "WHERE cik = ? AND fact_name = 'operating_income'",
        [ALPHA],
    )
    assert rows == [(OPERATING, 15.0)]  # the fallback's 16 is not stored
    gamma = _rows(
        backfilled,
        "SELECT xbrl_tag, value FROM statement_facts "
        "WHERE cik = ? AND fact_name = 'operating_income'",
        [GAMMA],
    )
    assert gamma == [(OPERATING_FALLBACK, 25.0)]  # carried alone, it is the one stored


# --- 10-K/A ------------------------------------------------------------------------


def test_a_10_ka_that_is_the_first_carrier_is_stored_with_its_own_form_and_acceptance(
    pre_backfill: _Pre, backfilled: Settings
) -> None:
    amendment = pre_backfill.world.filings[f"{GAMMA}-26-000002"]
    rows = _rows(
        backfilled,
        "SELECT fact_name, form, filing_accession, known_at FROM statement_facts "
        "WHERE cik = ? AND fact_name IN (?, ?) ORDER BY fact_name",
        [GAMMA, *NEW_NAMES],
    )
    assert [r[0] for r in rows] == list(NEW_NAMES)
    assert {(r[1], r[2], r[3]) for r in rows} == {
        ("10-K/A", amendment.accession, amendment.accepted.astimezone(UTC))
    }


# --- Nightly -----------------------------------------------------------------------


def test_a_nightly_after_the_backfill_extracts_the_new_names_for_its_cik_alone(
    pre_backfill: _Pre, backfilled: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutil.copytree(Path(backfilled.store.path).parent, tmp_path / "nightly")
    settings = _with_paths(backfilled, tmp_path / "nightly")
    before = _rows(settings, "SELECT cik, fact_name, filing_accession FROM statement_facts")
    touched: list[frozenset[str]] = []
    original = ingest_module._touched_for_index

    def record(*args: Any, **kwargs: Any) -> Any:
        found = original(*args, **kwargs)
        touched.append(found[0])
        return found

    monkeypatch.setattr(ingest_module, "_touched_for_index", record)
    result, source = _run(settings, pre_backfill.world, _night(NIGHTLY))
    assert result.ok, [(r.status, r.message) for r in result.runs]
    after = _rows(settings, "SELECT cik, fact_name, filing_accession FROM statement_facts")
    added = sorted(set(after) - set(before))
    assert added == [
        (ALPHA, "net_income", f"{ALPHA}-26-000003"),
        (ALPHA, "operating_income", f"{ALPHA}-26-000003"),
    ]
    assert not set(before) - set(after)
    assert ALPHA in source.payload_reads
    assert touched == [frozenset({ALPHA})]  # the night's one filing names one CIK
