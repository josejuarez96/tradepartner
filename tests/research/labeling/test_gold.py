"""Tests for `tradepartner.research.labeling.gold` (plan T131; research-labeling spec
C10 as #1121 amends it, req 10's gold sample).

A synthetic frame of 40 listing ends is written per test: 20 in 2016 (the `dev`
span, with `dev_span_min_rows = 20`) and 20 in 2017 onwards, two listing ends per
CIK for some issuers so the one-per-CIK rule binds. The tests use a small `n` and
`n_dev`; the module's defaults are req 10's 150 and 30.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest

from tradepartner.config import Settings
from tradepartner.research import datafiles
from tradepartner.research.labeling import gold
from tradepartner.store import schema
from tradepartner.store.db import StoreLockedError, configure_connection, open_for_write
from tradepartner.store.research import get_dataset

DEV_ROWS = 20
N_DEV = 3
N = 9
SEED = 7
OUTSIDE_LABEL = "merger_or_acquisition"


def _documents(i: int, cik: str, filed: date, amendments: list[str]) -> str:
    return json.dumps(
        {
            "listing_end_id": f"LE{i:03d}",
            "cik": cik,
            "issuer": f"Issuer {cik}",
            "exchange": "NYSE",
            "exchange_name": "New York Stock Exchange",
            "class_title": "Common Stock",
            "form25_filed_on": filed.isoformat(),
            "effective_on": (filed + timedelta(days=10)).isoformat(),
            "rule_provision": "12d2-2(a)(3)",
            "rule_provision_raw": "17 CFR 240.12d2-2(a)(3)",
            "amendments": [
                {"accession": a, "accepted_at": f"{filed.isoformat()}T15:00:00+00:00"}
                for a in amendments
            ],
            "exhibit": {"status": "text", "text": f"Notice {i}: the merger closed."},
            "eightk": {
                "form": "8-K",
                "accession": f"8K-{i:03d}",
                "filed_on": filed.isoformat(),
                "items": {"8.01": "Other events.", "2.01": "Completion.", "3.01": "Notice."},
                "body_head": None,
            },
        },
        sort_keys=True,
    )


def _frame_rows(amend: dict[int, list[str]] | None = None) -> list[dict[str, Any]]:
    """40 listing ends; CIK `i // 2` for i < 8 (two per issuer), else one each."""
    amend = amend or {}
    rows = []
    for i in range(40):
        cik = f"{(i // 2 if i < 8 else i) + 1000:010d}"
        if i < DEV_ROWS:
            accepted = datetime(2016, 1, 4, 15, tzinfo=UTC) + timedelta(days=7 * i)
        else:
            accepted = datetime(2017, 1, 4, 15, tzinfo=UTC) + timedelta(days=7 * (i - DEV_ROWS))
        rows.append(
            {
                "listing_end_id": f"LE{i:03d}",
                "cik": cik,
                "exchange": "NYSE",
                "form25_accepted_at": accepted,
                "form25_filed_on": accepted.date(),
                "documents": _documents(i, cik, accepted.date(), amend.get(i, [])),
                "rule_status": "delisted",
                "rule_relisted": i % 3 == 0,
                "rule_successor_id": None,
                "rule_form15_in_window": i % 2 == 0,
                "rule_row": 4 + i % 2,
                "rule_as_of": datetime(2026, 9, 1, tzinfo=UTC),
                "rule_code_version": "abc",
            }
        )
    return rows


def _write_frame(
    tmp_path: Path, rows: list[dict[str, Any]], name: str = "frame"
) -> gold.FrameExport:
    buffer = io.BytesIO()
    pl.DataFrame(rows).write_parquet(buffer)
    data = buffer.getvalue()
    path = tmp_path / f"{name}.parquet"
    path.write_bytes(data)
    return gold.FrameExport(dataset_id=1, path=path, sha256=hashlib.sha256(data).hexdigest())


def _exclusion(tmp_path: Path, rows: list[tuple[str, str]], name: str = "exclude.csv") -> Path:
    path = tmp_path / name
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["accession", "cik"])
        writer.writerows(rows)
    return path


@pytest.fixture
def gsettings(settings: Settings, tmp_path: Path) -> Settings:
    research = settings.research.model_copy(update={"data_dir": str(tmp_path / "research")})
    out = settings.model_copy(update={"research": research})
    conn = duckdb.connect(out.store.path)
    try:
        configure_connection(conn)
        schema.init_schema(conn)
    finally:
        conn.close()
    return out


@dataclass
class Built:
    settings: Settings
    frame: gold.FrameExport
    exclude: Path
    result: gold.BuildResult

    @property
    def session(self) -> gold.GoldSession:
        return self.result.session


def _build(
    gsettings: Settings,
    tmp_path: Path,
    *,
    rows: list[dict[str, Any]] | None = None,
    excluded: list[tuple[str, str]] | None = None,
    seed_cases: tuple[str, ...] = (),
    seed: int = SEED,
) -> Built:
    frame = _write_frame(tmp_path, rows if rows is not None else _frame_rows())
    exclude = _exclusion(tmp_path, excluded or [])
    result = gold.build_gold_session(
        frame,
        seed,
        N,
        exclude,
        settings=gsettings,
        seed_cases=seed_cases,
        n_dev=N_DEV,
        dev_span_min_rows=DEV_ROWS,
    )
    return Built(gsettings, frame, exclude, result)


def _connect(settings: Settings) -> Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]:
    return lambda: open_for_write(settings)


def _label_all(session: gold.GoldSession, label: str = OUTSIDE_LABEL) -> None:
    while (i := gold.next_case(session)) is not None:
        gold.record_label(
            session,
            session.cases[i].listing_end_id,
            label=label,
            relied_on="notice",
            seconds_spent=30.0,
        )


def _working(session: gold.GoldSession) -> list[dict[str, Any]]:
    return datafiles.read_jsonl(session.working_path)


# --- the draw and the exclusion ---------------------------------------------------


def test_draw_counts_one_per_cik_per_period(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    dev = [c for c in session.cases if c.split == "dev"]
    pilot = [c for c in session.cases if c.split == "pilot"]
    assert (len(dev), len(pilot)) == (N_DEV, N - N_DEV)
    for group in (dev, pilot):
        ciks = [session.rows[c.listing_end_id]["cik"] for c in group]
        assert len(set(ciks)) == len(ciks)
    for case in pilot:
        accepted = session.rows[case.listing_end_id]["form25_accepted_at"]
        assert session.pilot_period[0] <= accepted.date() <= session.pilot_period[1]


def test_draw_is_identical_with_rule_columns_shuffled(gsettings: Settings, tmp_path: Path) -> None:
    first = _build(gsettings, tmp_path).session.cases
    rows = _frame_rows()
    shuffled = [dict(r) for r in rows]
    rule_cols = [k for k in rows[0] if k.startswith("rule_")]
    for i, row in enumerate(shuffled):
        donor = rows[(i * 7 + 3) % len(rows)]
        for col in rule_cols:
            row[col] = donor[col]
        docs = json.loads(row["documents"])
        docs["rule_provision"] = "12d2-2(b)" if i % 2 else "12d2-2(a)(1)"
        docs["rule_provision_raw"] = "17 CFR 240." + docs["rule_provision"]
        row["documents"] = json.dumps(docs, sort_keys=True)
    other = tmp_path / "other"
    other.mkdir()
    research = gsettings.research.model_copy(update={"data_dir": str(other / "research")})
    second = _build(gsettings.model_copy(update={"research": research}), other, rows=shuffled)
    assert second.session.cases == first


def test_exclusion_by_accession_amendment_and_cik(gsettings: Settings, tmp_path: Path) -> None:
    # LE000 (CIK 1000) by its own accession; LE010 through an amendment; LE030 by a
    # file CIK given unpadded with an accession that matches no row.
    rows = _frame_rows(amend={10: ["AMEND-10"]})
    built = _build(
        gsettings,
        tmp_path,
        rows=rows,
        excluded=[("LE000", ""), ("AMEND-10", ""), ("NO-SUCH-ACC", str(1000 + 30))],
    )
    removed = {"LE000", "LE001", "LE010", "LE030"}  # LE001 shares LE000's CIK
    drawn = {c.listing_end_id for c in built.session.cases}
    assert not drawn & removed
    ex = built.result.exclusion
    assert (ex.accessions_matched, ex.ciks, ex.rows_removed) == (2, 3, 4)
    assert ex.unmatched == ("NO-SUCH-ACC",)
    doc = json.loads(built.session.path.read_text(encoding="utf-8"))
    assert doc["exclusion"]["sha256"] == hashlib.sha256(built.exclude.read_bytes()).hexdigest()


def test_exclusion_keeps_another_issuer_on_the_same_day(
    gsettings: Settings, tmp_path: Path
) -> None:
    rows = _frame_rows()
    same_day = dict(rows[12])
    same_day.update(listing_end_id="LE999", cik=f"{9999:010d}")
    rows.append(same_day)
    removed, ex = gold.apply_exclusion(rows, _exclusion(tmp_path, [("LE012", "")]))
    assert removed == {"LE012"}
    assert ex.rows_removed == 1


def test_unmatched_accession_without_cik_is_refused(gsettings: Settings, tmp_path: Path) -> None:
    with pytest.raises(gold.GoldRefused, match="NO-SUCH-ACC"):
        _build(gsettings, tmp_path, excluded=[("NO-SUCH-ACC", "")])
    assert not gold.session_path(gsettings).exists()


def test_seed_case_of_an_excluded_cik_stays(gsettings: Settings, tmp_path: Path) -> None:
    built = _build(gsettings, tmp_path, excluded=[("LE030", "")], seed_cases=("LE030", "GONE"))
    seeds = [c for c in built.session.cases if c.seed_case]
    assert [(c.listing_end_id, c.split, c.in_random_draw) for c in seeds] == [
        ("LE030", "pilot", False)
    ]
    assert built.result.seed_cases_missing == ("GONE",)


# --- the refusals -----------------------------------------------------------------


def _plant(settings: Settings, relative: str) -> Path:
    path = datafiles.data_dir(settings) / "inferences" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}\n", encoding="utf-8")
    return path


def test_build_refuses_with_an_inference_file(gsettings: Settings, tmp_path: Path) -> None:
    _plant(gsettings, "departure-reason/notes.txt")
    with pytest.raises(gold.InferenceFilesPresent):
        _build(gsettings, tmp_path)


def test_an_empty_inferences_directory_does_not_refuse(gsettings: Settings, tmp_path: Path) -> None:
    (datafiles.data_dir(gsettings) / "inferences" / "departure-reason").mkdir(parents=True)
    _build(gsettings, tmp_path)


def test_resume_refuses_a_file_planted_after_the_build(gsettings: Settings, tmp_path: Path) -> None:
    built = _build(gsettings, tmp_path)
    _plant(gsettings, "stray/x.bin")
    with pytest.raises(gold.InferenceFilesPresent):
        gold.open_gold_session(built.session.path, gold.GoldFlags(), settings=gsettings)


def test_frame_hash_mismatch_is_refused(gsettings: Settings, tmp_path: Path) -> None:
    frame = _write_frame(tmp_path, _frame_rows())
    wrong = gold.FrameExport(frame.dataset_id, frame.path, "0" * 64)
    with pytest.raises(gold.FrameChanged):
        gold.build_gold_session(
            wrong,
            SEED,
            N,
            _exclusion(tmp_path, []),
            settings=gsettings,
            n_dev=N_DEV,
            dev_span_min_rows=DEV_ROWS,
        )


def test_resume_with_no_flags_and_refusing_different_flags(
    gsettings: Settings, tmp_path: Path
) -> None:
    built = _build(gsettings, tmp_path)
    path = built.session.path
    same = gold.GoldFlags(frame_dataset_id=1, seed=SEED, n=N, exclude=built.exclude)
    assert (
        gold.open_gold_session(path, gold.GoldFlags(), settings=gsettings).cases
        == built.session.cases
    )
    assert gold.open_gold_session(path, same, settings=gsettings).cases == built.session.cases
    other = _exclusion(tmp_path, [("LE005", "")], name="other.csv")
    for flags in (
        gold.GoldFlags(seed=SEED + 1),
        gold.GoldFlags(n=N + 1),
        gold.GoldFlags(frame_dataset_id=2),
        gold.GoldFlags(exclude=other),
    ):
        with pytest.raises(gold.SessionMismatch):
            gold.open_gold_session(path, flags, settings=gsettings)


def test_a_second_build_is_refused(gsettings: Settings, tmp_path: Path) -> None:
    built = _build(gsettings, tmp_path)
    with pytest.raises(gold.GoldRefused, match="already exists"):
        gold.build_gold_session(
            built.frame,
            SEED,
            N,
            built.exclude,
            settings=gsettings,
            n_dev=N_DEV,
            dev_span_min_rows=DEV_ROWS,
        )


# --- case_view --------------------------------------------------------------------


def test_case_view_draws_no_rule_value_or_model_field(
    gsettings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _build(gsettings, tmp_path).session
    _plant(gsettings, "departure-reason/1.jsonl")
    opened: list[Path] = []
    real_read = datafiles.read_jsonl

    def spy_read(path: Path) -> list[dict[str, Any]]:
        opened.append(Path(path))
        return real_read(path)

    def refuse(*_a: object, **_k: object) -> Any:
        raise AssertionError("an inference path was resolved in gold mode")

    monkeypatch.setattr(datafiles, "read_jsonl", spy_read)
    monkeypatch.setattr(datafiles, "inference_path", refuse)
    monkeypatch.setattr(datafiles, "inference_paths", refuse)
    view = gold.case_view(session, 0)
    gold.next_case(session)
    inferences = datafiles.data_dir(gsettings) / "inferences"
    assert not any(inferences in p.parents for p in opened)

    fields = vars(view)
    assert not [k for k in fields if k.startswith("rule_") or k in ("seed_case", "probabilities")]
    text = json.dumps(fields, default=str)
    for value in ("delisted", "rule_row", "probabilit", "selected_option"):
        assert value not in text
    assert view.notice and view.notice.startswith("Notice")
    assert view.eightk is not None
    assert [k for k, _ in view.eightk.items] == ["3.01", "2.01", "8.01"]
    assert len(view.options) == 9
    assert view.index_url.startswith("https://www.sec.gov/cgi-bin/browse-edgar")


# --- the working file -------------------------------------------------------------


def test_record_label_appends_before_returning(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    lid = session.cases[0].listing_end_id
    gold.record_label(
        session, lid, label="bankruptcy", relied_on="8-K item 1.03", seconds_spent=700
    )
    (line,) = _working(session)
    assert line["listing_end_id"] == lid and line["gold_label"] == "bankruptcy"
    assert (line["seconds_spent"], line["idle"]) == (gold.SECONDS_CAP, True)
    labeled_at = datetime.fromisoformat(line["labeled_at"])
    assert labeled_at.tzinfo is not None and labeled_at.utcoffset() == timedelta(0)


def test_second_answer_refused_until_undo(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    lid = session.cases[0].listing_end_id
    gold.record_label(session, lid, label="bankruptcy", relied_on="notice", seconds_spent=5)
    with pytest.raises(gold.AlreadyAnswered):
        gold.record_label(session, lid, label="going_private", relied_on="notice", seconds_spent=5)
    with pytest.raises(gold.AlreadyAnswered):
        gold.record_skip(session, lid)
    gold.record_undo(session, lid)
    assert gold.next_case(session) == 0
    gold.record_label(session, lid, label="going_private", relied_on="notice", seconds_spent=5)
    assert [ln.get("gold_label") for ln in _working(session)] == [
        "bankruptcy",
        None,
        "going_private",
    ]


def test_a_final_undo_leaves_the_session_partial(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    _label_all(session)
    gold.record_undo(session, session.cases[2].listing_end_id)
    with pytest.raises(gold.SessionPartial):
        gold.lock_gold(session, _connect(gsettings))


def test_label_checks(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    lid = session.cases[0].listing_end_id
    with pytest.raises(gold.GoldRefused, match="not one of the options"):
        gold.record_label(session, lid, label="other", relied_on="notice", seconds_spent=1)
    with pytest.raises(gold.GoldRefused, match="accession or URL"):
        gold.record_label(session, lid, label="bankruptcy", relied_on="outside", seconds_spent=1)
    assert not session.working_path.exists()


def test_skip_restart_skip_ends_unlabelled(gsettings: Settings, tmp_path: Path) -> None:
    built = _build(gsettings, tmp_path)
    session = built.session
    first = session.cases[0].listing_end_id
    gold.record_skip(session, first)
    for case in session.cases[1:]:
        gold.record_label(
            session, case.listing_end_id, label="bankruptcy", relied_on="notice", seconds_spent=1
        )
    resumed = gold.open_gold_session(session.path, gold.GoldFlags(), settings=gsettings)
    assert gold.next_case(resumed) == 0
    gold.record_skip(resumed, first)
    assert _working(resumed)[-1]["unlabelled"] is True
    assert gold.next_case(resumed) is None
    result = gold.lock_gold(resumed, _connect(gsettings))
    export = pl.read_parquet(datafiles.gold_path(gsettings, result.sha256))
    assert export.height == N  # no replacement case
    row = export.filter(pl.col("listing_end_id") == first).to_dicts()[0]
    assert row["unlabelled"] is True and row["gold_label"] is None


def test_next_case_resumes_with_the_skipped_case_last(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    gold.record_skip(session, session.cases[0].listing_end_id)
    gold.record_label(
        session,
        session.cases[1].listing_end_id,
        label="bankruptcy",
        relied_on="notice",
        seconds_spent=1,
    )
    resumed = gold.open_gold_session(session.path, gold.GoldFlags(), settings=gsettings)
    assert gold.next_case(resumed) == 2
    for case in resumed.cases[2:]:
        gold.record_label(
            resumed, case.listing_end_id, label="bankruptcy", relied_on="notice", seconds_spent=1
        )
    assert gold.next_case(resumed) == 0


# --- the lock ---------------------------------------------------------------------


def test_lock_refuses_a_partial_session(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    gold.record_label(
        session,
        session.cases[0].listing_end_id,
        label="bankruptcy",
        relied_on="notice",
        seconds_spent=1,
    )
    with pytest.raises(gold.SessionPartial):
        gold.lock_gold(session, _connect(gsettings))


def test_lock_rejects_a_label_outside_the_set(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    _label_all(session)
    datafiles.append_jsonl(
        session.working_path,
        [{"listing_end_id": session.cases[0].listing_end_id, "undo": True}],
    )
    datafiles.append_jsonl(
        session.working_path,
        [
            {
                "listing_end_id": session.cases[0].listing_end_id,
                "gold_label": "made_up",
                "relied_on": "notice",
                "confidence": "high",
            }
        ],
    )
    with pytest.raises(gold.GoldRefused, match="not one of the options"):
        gold.lock_gold(session, _connect(gsettings))


def test_lock_demands_passage_ref_on_outside(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    _label_all(session)
    lid = session.cases[0].listing_end_id
    datafiles.append_jsonl(
        session.working_path,
        [
            {"listing_end_id": lid, "undo": True},
            {
                "listing_end_id": lid,
                "gold_label": "bankruptcy",
                "relied_on": "outside",
                "passage_ref": "",
                "confidence": "high",
            },
        ],
    )
    with pytest.raises(gold.GoldRefused, match="accession or URL"):
        gold.lock_gold(session, _connect(gsettings))


def test_lock_writes_and_registers_the_export(gsettings: Settings, tmp_path: Path) -> None:
    built = _build(gsettings, tmp_path, seed_cases=("LE039",))
    session = built.session
    cases = session.cases
    unresolved_pilot = next(c for c in cases if c.split == "pilot" and c.in_random_draw)
    undone = cases[0].listing_end_id
    for case in cases:
        lid = case.listing_end_id
        if lid == unresolved_pilot.listing_end_id:
            gold.record_label(session, lid, label="unresolved", relied_on="notice", seconds_spent=1)
        elif lid == undone:
            gold.record_label(session, lid, label="bankruptcy", relied_on="notice", seconds_spent=1)
            gold.record_undo(session, lid)
            gold.record_label(
                session,
                lid,
                label=OUTSIDE_LABEL,
                relied_on="outside",
                passage_ref="0000000000-17-000001",
                text_states="unresolved",
                seconds_spent=1,
            )
        else:
            gold.record_label(session, lid, label="bankruptcy", relied_on="notice", seconds_spent=1)

    result = gold.lock_gold(session, _connect(gsettings))
    assert result.state == "locked"
    n_pilot_drawn = sum(1 for c in cases if c.split == "pilot" and c.in_random_draw)
    assert result.scorable_pilot == n_pilot_drawn - 1

    export_path = datafiles.gold_path(gsettings, result.sha256)
    data = export_path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == result.sha256
    export = pl.read_parquet(export_path)
    assert {
        "documents",
        "form25_accepted_at",
        "gold_label",
        "text_states",
        "gold_class",
        "seed_case",
        "unlabelled",
        "passage_ref",
    } <= set(export.columns)
    by_id = {r["listing_end_id"]: r for r in export.to_dicts()}
    assert by_id[undone]["gold_label"] == OUTSIDE_LABEL  # the undo's superseding line wins
    assert by_id[undone]["text_states"] == "unresolved"
    assert by_id[undone]["passage_ref"] == "0000000000-17-000001"
    plain = next(lid for lid in by_id if lid not in (undone, unresolved_pilot.listing_end_id))
    assert by_id[plain]["text_states"] == "bankruptcy"  # copied into an empty text_states
    assert by_id[plain]["gold_class"] == "insolvency"
    assert by_id["LE039"]["seed_case"] is True
    meta = pl.read_parquet_metadata(export_path)
    assert meta["exclusion_sha256"] == session.exclusion_sha256

    splits = json.loads(datafiles.gold_splits_path(gsettings, result.sha256).read_text())["splits"]
    conn = duckdb.connect(gsettings.store.path, read_only=True)
    try:
        record = get_dataset(conn, result.dataset_id or 0)
    finally:
        conn.close()
    assert record.name == gold.DATASET_NAME and record.locked
    assert record.sealed_splits == ("pilot",)
    (period,) = record.sealed_periods
    for row, split in zip(export.to_dicts(), splits, strict=True):
        if split == "pilot":
            assert period[0] <= row["form25_accepted_at"].date() <= period[1]
    assert session.lock_path.exists()
    with pytest.raises(gold.SessionLocked):
        gold.record_undo(session, undone)
    assert gold.lock_gold(session, _connect(gsettings)).sha256 == result.sha256


def test_store_locked_at_the_lock_is_retryable(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    _label_all(session)
    before = session.working_path.read_bytes()

    @contextmanager
    def busy() -> Iterator[duckdb.DuckDBPyConnection]:
        raise StoreLockedError("locked by another process")
        yield  # pragma: no cover

    result = gold.lock_gold(session, busy)
    assert result.state == "busy" and "store busy" in (result.message or "")
    assert session.working_path.read_bytes() == before
    assert not session.lock_path.exists()
    assert gold.lock_gold(session, _connect(gsettings)).state == "locked"


def test_no_runtime_store_connection_outside_the_lock(
    gsettings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    real_connect = duckdb.connect

    def recording(*args: object, **kwargs: object) -> Any:
        calls.append(args)
        return real_connect(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(duckdb, "connect", recording)
    session = _build(gsettings, tmp_path).session
    resumed = gold.open_gold_session(session.path, gold.GoldFlags(), settings=gsettings)
    gold.case_view(resumed, 0)
    _label_all(resumed)
    assert calls == []
    gold.lock_gold(resumed, _connect(gsettings))
    assert len(calls) == 1  # the caller's own write connection, for the registration
