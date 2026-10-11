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
from tradepartner.research.labeling.crosswalk import RuleAnswer
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


def _frame_rows(
    amend: dict[int, list[str]] | None = None, *, total: int = 40, dev_rows: int = DEV_ROWS
) -> list[dict[str, Any]]:
    """`total` listing ends, the first `dev_rows` in the dev span; CIK `i // 2` for
    i < 8 (two per issuer), else one each."""
    amend = amend or {}
    rows = []
    for i in range(total):
        cik = f"{(i // 2 if i < 8 else i) + 1000:010d}"
        if i < dev_rows:
            accepted = datetime(2016, 1, 4, 15, tzinfo=UTC) + timedelta(days=i)
        else:
            accepted = datetime(2017, 1, 4, 15, tzinfo=UTC) + timedelta(days=(i - dev_rows))
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
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(buffer)
    data = buffer.getvalue()
    path = tmp_path / f"{name}.parquet"
    path.write_bytes(data)
    return gold.FrameExport(dataset_id=1, file=path, sha256=hashlib.sha256(data).hexdigest())


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
        seed_cases=[_seed(i) for i in seed_cases],
        n_dev=N_DEV,
        dev_span_min_rows=DEV_ROWS,
    )
    return Built(gsettings, frame, exclude, result)


def _seed(listing_end_id: str, status: str = "listed") -> gold.SeedCase:
    return gold.SeedCase(listing_end_id, RuleAnswer(status=status), "f2b5261", "#921")  # type: ignore[arg-type]


def _connect(settings: Settings) -> Callable[[], AbstractContextManager[duckdb.DuckDBPyConnection]]:
    return lambda: open_for_write(settings)


def _label_all(session: gold.GoldSession, label: str = OUTSIDE_LABEL) -> None:
    while (i := gold.next_case(session)) is not None:
        gold.record_label(
            session,
            session.cases[i].listing_end_id,
            label=label,
            relied_on="notice",
            basis="text",
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
    doc = json.loads(built.session.session_file.read_text(encoding="utf-8"))
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


def _seed_file(tmp_path: Path, rows: list[list[str]]) -> Path:
    path = tmp_path / "seeds.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(gold.SEED_FILE_COLUMNS)
        writer.writerows(rows)
    return path


def test_read_seed_file_and_the_session_keeps_each_seeds_pre_fix_answer(
    gsettings: Settings, tmp_path: Path
) -> None:
    """#1351: the seed file carries each seed's pre-fix rule answer into the session."""
    path = _seed_file(
        tmp_path,
        [
            ["LE039", "listed", "false", "", "false", "f2b5261", "#921"],
            ["LE030", "delisted", "false", "", "true", "ca79641", "#943"],
            ["GONE", "delisted", "false", "", "false", "f2b5261", "#922"],
        ],
    )
    seed_file = gold.read_seed_file(path)
    assert seed_file.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    seeds = seed_file.seeds
    assert [(c.listing_end_id, c.row) for c in seeds] == [("LE039", 6), ("LE030", 4), ("GONE", 5)]
    frame = _write_frame(tmp_path, _frame_rows())
    result = gold.build_gold_session(
        frame,
        SEED,
        N,
        _exclusion(tmp_path, []),
        settings=gsettings,
        seed_cases=seeds,
        seeds_sha256="abc",
        n_dev=N_DEV,
        dev_span_min_rows=DEV_ROWS,
    )
    assert result.seed_cases_missing == ("GONE",)
    resumed = gold.open_gold_session(
        gold.session_path(gsettings), gold.GoldFlags(), settings=gsettings
    )
    assert {i: c.row for i, c in resumed.seed_cases.items()} == {"LE030": 4, "LE039": 6}
    assert resumed.seed_cases["LE030"].code_version == "ca79641"
    with pytest.raises(gold.SessionMismatch, match="--seeds"):
        gold.open_gold_session(
            gold.session_path(gsettings), gold.GoldFlags(seeds=path), settings=gsettings
        )


@pytest.mark.parametrize(
    ("row", "match"),
    [
        (["LE1", "gone", "false", "", "false", "c", "#1"], "status"),
        (["LE1", "listed", "yes", "", "false", "c", "#1"], "relisted"),
        (["LE1", "listed", "false", "", "false", "", "#1"], "code_version"),
        (["LE1", "listed", "true", "", "false", "c", "#1"], "takes no relisted"),
        (["LE1", "unmatched", "false", "X@2020", "false", "c", "#1"], "takes no relisted"),
    ],
)
def test_read_seed_file_refuses_a_bad_row(tmp_path: Path, row: list[str], match: str) -> None:
    with pytest.raises(gold.GoldRefused, match=match):
        gold.read_seed_file(_seed_file(tmp_path, [row]))


def test_read_seed_file_refuses_a_repeat_and_a_missing_column(tmp_path: Path) -> None:
    row = ["LE1", "listed", "false", "", "false", "c", "#1"]
    with pytest.raises(gold.GoldRefused, match="repeats"):
        gold.read_seed_file(_seed_file(tmp_path, [row, row]))
    bom = tmp_path / "bom.csv"
    bom.write_bytes(
        b"\xef\xbb\xbf"
        + ",".join(gold.SEED_FILE_COLUMNS).encode()
        + b"\nLE1,listed,false,,false,c,#1\n"
    )
    assert [c.row for c in gold.read_seed_file(bom).seeds] == [6]
    path = tmp_path / "short.csv"
    path.write_text("listing_end_id,status\nLE1,listed\n", encoding="utf-8")
    with pytest.raises(gold.GoldRefused, match="lacks column"):
        gold.read_seed_file(path)


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
        gold.open_gold_session(built.session.session_file, gold.GoldFlags(), settings=gsettings)


def test_frame_hash_mismatch_is_refused(gsettings: Settings, tmp_path: Path) -> None:
    frame = _write_frame(tmp_path, _frame_rows())
    wrong = gold.FrameExport(frame.dataset_id, frame.file, "0" * 64)
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
    path = built.session.session_file
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
        session, lid, label="bankruptcy", relied_on="8-K item 1.03", basis="text", seconds_spent=700
    )
    (line,) = _working(session)
    assert line["listing_end_id"] == lid and line["gold_label"] == "bankruptcy"
    assert (line["seconds_spent"], line["idle"]) == (gold.SECONDS_CAP, True)
    labeled_at = datetime.fromisoformat(line["labeled_at"])
    assert labeled_at.tzinfo is not None and labeled_at.utcoffset() == timedelta(0)


def test_second_answer_refused_until_undo(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    lid = session.cases[0].listing_end_id
    gold.record_label(
        session, lid, label="bankruptcy", relied_on="notice", basis="text", seconds_spent=5
    )
    with pytest.raises(gold.AlreadyAnswered):
        gold.record_label(
            session, lid, label="going_private", relied_on="notice", basis="text", seconds_spent=5
        )
    with pytest.raises(gold.AlreadyAnswered):
        gold.record_skip(session, lid)
    gold.record_undo(session, lid)
    assert gold.next_case(session) == 0
    gold.record_label(
        session, lid, label="going_private", relied_on="notice", basis="text", seconds_spent=5
    )
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
        gold.lock_gold(session, _connect(gsettings), text_floor=0)


def test_label_checks(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    lid = session.cases[0].listing_end_id
    with pytest.raises(gold.GoldRefused, match="not one of the options"):
        gold.record_label(
            session, lid, label="other", relied_on="notice", basis="text", seconds_spent=1
        )
    with pytest.raises(gold.GoldRefused, match="accession or URL"):
        gold.record_label(
            session, lid, label="bankruptcy", relied_on="outside", basis="text", seconds_spent=1
        )
    assert not session.working_path.exists()


def test_skip_restart_skip_ends_unlabelled(gsettings: Settings, tmp_path: Path) -> None:
    built = _build(gsettings, tmp_path)
    session = built.session
    first = session.cases[0].listing_end_id
    gold.record_skip(session, first)
    for case in session.cases[1:]:
        gold.record_label(
            session,
            case.listing_end_id,
            label="bankruptcy",
            relied_on="notice",
            basis="text",
            seconds_spent=1,
        )
    resumed = gold.open_gold_session(session.session_file, gold.GoldFlags(), settings=gsettings)
    assert gold.next_case(resumed) == 0
    gold.record_skip(resumed, first)
    assert _working(resumed)[-1]["unlabelled"] is True
    assert gold.next_case(resumed) is None
    result = gold.lock_gold(resumed, _connect(gsettings), text_floor=0)
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
        basis="text",
        seconds_spent=1,
    )
    resumed = gold.open_gold_session(session.session_file, gold.GoldFlags(), settings=gsettings)
    assert gold.next_case(resumed) == 2
    for case in resumed.cases[2:]:
        gold.record_label(
            resumed,
            case.listing_end_id,
            label="bankruptcy",
            relied_on="notice",
            basis="text",
            seconds_spent=1,
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
        basis="text",
        seconds_spent=1,
    )
    with pytest.raises(gold.SessionPartial):
        gold.lock_gold(session, _connect(gsettings), text_floor=0)


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
        gold.lock_gold(session, _connect(gsettings), text_floor=0)


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
        gold.lock_gold(session, _connect(gsettings), text_floor=0)


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
            gold.record_label(
                session, lid, label="bankruptcy", relied_on="notice", basis="text", seconds_spent=1
            )
            gold.record_undo(session, lid)
            gold.record_label(
                session,
                lid,
                label=OUTSIDE_LABEL,
                relied_on="outside",
                basis="text",
                passage_ref="0000000000-17-000001",
                text_states="unresolved",
                seconds_spent=1,
            )
        else:
            gold.record_label(
                session, lid, label="bankruptcy", relied_on="notice", basis="text", seconds_spent=1
            )

    result = gold.lock_gold(session, _connect(gsettings), text_floor=0)
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
    # #1351: the seed's pre-fix rule answer is in the export, bound by its hash
    assert (by_id["LE039"]["pre_fix_status"], by_id["LE039"]["pre_fix_row"]) == ("listed", 6)
    assert by_id["LE039"]["pre_fix_code_version"] == "f2b5261"
    assert by_id[plain]["pre_fix_status"] is None and by_id[plain]["pre_fix_row"] is None
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
    assert gold.lock_gold(session, _connect(gsettings), text_floor=0).sha256 == result.sha256


def test_store_locked_at_the_lock_is_retryable(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    _label_all(session)
    before = session.working_path.read_bytes()

    @contextmanager
    def busy() -> Iterator[duckdb.DuckDBPyConnection]:
        raise StoreLockedError("locked by another process")
        yield  # pragma: no cover

    result = gold.lock_gold(session, busy, text_floor=0)
    assert result.state == "busy" and "store busy" in (result.message or "")
    assert session.working_path.read_bytes() == before
    assert not session.lock_path.exists()
    assert gold.lock_gold(session, _connect(gsettings), text_floor=0).state == "locked"


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
    resumed = gold.open_gold_session(session.session_file, gold.GoldFlags(), settings=gsettings)
    gold.case_view(resumed, 0)
    _label_all(resumed)
    assert calls == []
    gold.lock_gold(resumed, _connect(gsettings), text_floor=0)
    assert len(calls) == 1  # the caller's own write connection, for the registration


def test_lock_a_session_over_a_hundred_cases(gsettings: Settings, tmp_path: Path) -> None:
    """The export keeps the frame's column types and the gold columns' fixed ones:
    a column null in the first hundred rows and set later still locks."""
    rows = _frame_rows(total=260, dev_rows=130)
    rows[-1]["rule_successor_id"] = "0000001259@2017-09-01"
    frame = _write_frame(tmp_path, rows)
    session = gold.build_gold_session(
        frame,
        SEED,
        230,
        _exclusion(tmp_path, []),
        settings=gsettings,
        n_dev=110,
        dev_span_min_rows=130,
    ).session
    ids = sorted(c.listing_end_id for c in session.cases)
    for case in session.cases:
        lid = case.listing_end_id
        if lid == ids[-1]:
            gold.record_label(
                session,
                lid,
                label="bankruptcy",
                relied_on="outside",
                basis="text",
                passage_ref="https://www.sec.gov/x",
                seconds_spent=12,
            )
        else:
            gold.record_label(
                session, lid, label="bankruptcy", relied_on="notice", basis="text", seconds_spent=30
            )
    result = gold.lock_gold(session, _connect(gsettings), text_floor=0)
    export = pl.read_parquet(datafiles.gold_path(gsettings, result.sha256))
    assert export.height == len(session.cases)
    assert export["passage_ref"].drop_nulls().to_list() == ["https://www.sec.gov/x"]
    assert export.schema["seconds_spent"] == pl.Float64
    assert export.schema["labeled_at"] == pl.Datetime("us", "UTC")
    assert (
        export.schema["form25_accepted_at"]
        == pl.read_parquet(frame.file).schema["form25_accepted_at"]
    )


# --- the basis (#1492) ------------------------------------------------------------


@pytest.mark.parametrize(
    ("label", "relied_on", "basis", "match"),
    [
        ("bankruptcy", "notice", None, "needs a basis"),
        ("unresolved", "notice", "text", "has no basis"),
        ("bankruptcy", "notice", "rule", "is not one of"),
        ("bankruptcy", "notice", "provision", "needs relied_on `form 25`"),
        ("bankruptcy", "8-K item 1.03", "provision", "needs relied_on `form 25`"),
        ("bankruptcy", "8-K item 1.03", "exchange_rule", "needs relied_on `notice`"),
        ("bankruptcy", "form 25", "exchange_rule", "needs relied_on `notice`"),
        ("bankruptcy", "form 25", "text", "a `text` basis needs"),
    ],
)
def test_record_label_refuses_a_missing_or_contradicting_basis(
    gsettings: Settings,
    tmp_path: Path,
    label: str,
    relied_on: str,
    basis: str | None,
    match: str,
) -> None:
    session = _build(gsettings, tmp_path).session
    lid = session.cases[0].listing_end_id
    with pytest.raises(gold.GoldRefused, match=match):
        gold.record_label(
            session, lid, label=label, relied_on=relied_on, basis=basis, seconds_spent=1
        )
    assert not session.working_path.exists()


def test_record_label_accepts_form_25_and_each_agreeing_basis(
    gsettings: Settings, tmp_path: Path
) -> None:
    session = _build(gsettings, tmp_path).session
    ids = [c.listing_end_id for c in session.cases]
    answers = [
        ("instrument_retirement", "form 25", "provision", None),
        ("bankruptcy", "notice", "exchange_rule", None),
        ("bankruptcy", "8-K item 1.03", "text", None),
        ("bankruptcy", "outside", "text", "0000000000-17-000001"),
        ("unresolved", "form 25", None, None),
    ]
    for lid, (label, relied_on, basis, passage) in zip(ids, answers, strict=False):
        gold.record_label(
            session,
            lid,
            label=label,
            relied_on=relied_on,
            basis=basis,
            passage_ref=passage,
            seconds_spent=1,
        )
    assert [(ln["relied_on"], ln["basis"]) for ln in _working(session)] == [
        (r, b) for _, r, b, _ in answers
    ]


def _pilot_ids(session: gold.GoldSession) -> list[str]:
    return [c.listing_end_id for c in session.cases if c.split == "pilot" and c.in_random_draw]


def _label_by_basis(session: gold.GoldSession, plan: dict[str, str]) -> None:
    """Answer every case: `plan` maps a listing end to `provision`, `exchange_rule`,
    `unresolved` or `unlabelled`; every other case is a `text` label."""
    for case in session.cases:
        lid = case.listing_end_id
        kind = plan.get(lid, "text")
        if kind == "unlabelled":
            continue
        if kind == "unresolved":
            gold.record_label(session, lid, label="unresolved", relied_on="notice", seconds_spent=1)
            continue
        relied_on = {"provision": "form 25", "exchange_rule": "notice", "text": "notice"}[kind]
        gold.record_label(
            session, lid, label="bankruptcy", relied_on=relied_on, basis=kind, seconds_spent=1
        )
    for lid, kind in plan.items():
        if kind == "unlabelled":
            gold.record_skip(session, lid)
            gold.record_skip(session, lid)


def test_basis_counts_counts_the_random_pilot_draw_per_round(
    gsettings: Settings, tmp_path: Path
) -> None:
    # LE030 is a seed outside the draw (its CIK excluded), so it counts nowhere.
    built = _build(gsettings, tmp_path, excluded=[("LE030", "")], seed_cases=("LE030",))
    path = built.session.session_file
    doc = json.loads(path.read_text(encoding="utf-8"))
    pilot = _pilot_ids(built.session)
    # Mark two random pilot cases as an extension round (what T167b writes).
    for case in doc["cases"]:
        if case["listing_end_id"] in pilot[-2:]:
            case["draw_round"] = 1
    path.write_text(json.dumps(doc), encoding="utf-8")
    session = gold.open_gold_session(path, gold.GoldFlags(), settings=gsettings)
    assert sorted(c.draw_round for c in session.cases if c.listing_end_id in pilot) == [
        0,
        0,
        0,
        0,
        1,
        1,
    ]
    plan = {
        pilot[0]: "provision",
        pilot[1]: "exchange_rule",
        pilot[2]: "unresolved",
        pilot[4]: "provision",
        "LE030": "provision",
    }
    _label_by_basis(session, plan)
    before = session.working_path.read_bytes()
    counts = gold.basis_counts(session)
    assert session.working_path.read_bytes() == before
    assert dict(counts.counts) == {
        "text": 2,
        "exchange_rule": 1,
        "provision": 2,
        "unresolved": 1,
        "unlabelled": 0,
    }
    assert (counts.n_pilot, counts.scorable_pilot_text, counts.scorable_pilot) == (6, 2, 5)
    assert set(counts.by_round) == {0, 1}
    assert dict(counts.by_round[0].counts) == {
        "text": 1,
        "exchange_rule": 1,
        "provision": 1,
        "unresolved": 1,
        "unlabelled": 0,
    }
    assert (counts.by_round[1].n_pilot, counts.by_round[1].scorable_pilot_text) == (2, 1)
    assert counts.by_round[1].counts["provision"] == 1


def test_basis_counts_ignores_rule_provision(gsettings: Settings, tmp_path: Path) -> None:
    """Nothing reads the frame's provision: a sentinel changes no count."""
    rows = _frame_rows()
    for row in rows:
        docs = json.loads(row["documents"])
        docs["rule_provision"] = docs["rule_provision_raw"] = "12d2-2(a)(1)"
        row["documents"] = json.dumps(docs, sort_keys=True)
    session = _build(gsettings, tmp_path, rows=rows).session
    _label_all(session)
    counts = gold.basis_counts(session)
    assert counts.counts["text"] == len(_pilot_ids(session))
    assert counts.counts["provision"] == 0


def test_lock_refuses_a_labelled_line_without_basis_naming_the_case(
    gsettings: Settings, tmp_path: Path
) -> None:
    session = _build(gsettings, tmp_path).session
    _label_all(session)
    lid = session.cases[1].listing_end_id
    datafiles.append_jsonl(
        session.working_path,
        [
            {"listing_end_id": lid, "undo": True},
            {
                "listing_end_id": lid,
                "gold_label": "bankruptcy",
                "relied_on": "notice",
                "confidence": "high",
            },
        ],
    )
    with pytest.raises(gold.GoldRefused, match=f"{lid}: a `bankruptcy` label needs a basis"):
        gold.lock_gold(session, _connect(gsettings), text_floor=0)
    assert not session.lock_path.exists()


def _gold_datasets(settings: Settings) -> int:
    conn = duckdb.connect(settings.store.path, read_only=True)
    try:
        row = conn.execute(
            "SELECT count(*) FROM research_datasets WHERE name = ?", [gold.DATASET_NAME]
        ).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def test_lock_under_the_floor_refuses_and_writes_nothing(
    gsettings: Settings, tmp_path: Path
) -> None:
    session = _build(gsettings, tmp_path).session
    pilot = _pilot_ids(session)
    _label_by_basis(session, {pilot[0]: "provision", pilot[1]: "unresolved"})
    text = len(pilot) - 2
    with pytest.raises(gold.BelowFloor, match=f"is {text}, under the floor {text + 1}"):
        gold.lock_gold(session, _connect(gsettings), text_floor=text + 1)
    assert not session.lock_path.exists()
    assert _gold_datasets(gsettings) == 0
    gold_dir = datafiles.gold_working_path(gsettings).parent
    assert sorted(p.name for p in gold_dir.iterdir()) == ["session.json", "working.jsonl"]
    result = gold.lock_gold(session, _connect(gsettings), text_floor=text)
    assert result.state == "locked"
    assert _gold_datasets(gsettings) == 1


def test_the_export_carries_basis_draw_round_and_the_protocol_version(
    gsettings: Settings, tmp_path: Path
) -> None:
    session = _build(gsettings, tmp_path).session
    pilot = _pilot_ids(session)
    plan = {pilot[0]: "provision", pilot[1]: "exchange_rule", pilot[2]: "unresolved"}
    plan[pilot[3]] = "unlabelled"
    _label_by_basis(session, plan)
    result = gold.lock_gold(session, _connect(gsettings), text_floor=2)
    assert (result.scorable_pilot, result.scorable_pilot_text) == (4, 2)
    assert dict(result.basis_counts.counts) == {
        "text": 2,
        "exchange_rule": 1,
        "provision": 1,
        "unresolved": 1,
        "unlabelled": 1,
    }
    assert set(result.basis_counts.by_round) == {0}
    export_path = datafiles.gold_path(gsettings, result.sha256)
    export = pl.read_parquet(export_path)
    assert export.schema["basis"] == pl.String and export.schema["draw_round"] == pl.Int64
    by_id = {r["listing_end_id"]: r for r in export.to_dicts()}
    assert by_id[pilot[0]]["basis"] == "provision"
    assert by_id[pilot[1]]["basis"] == "exchange_rule"
    assert by_id[pilot[2]]["basis"] is None  # unresolved
    assert by_id[pilot[3]]["basis"] is None and by_id[pilot[3]]["unlabelled"] is True
    assert by_id[pilot[4]]["basis"] == "text"
    assert set(export["draw_round"].to_list()) == {0}
    assert pl.read_parquet_metadata(export_path)["protocol_version"] == "1"
    lock = json.loads(session.lock_path.read_text(encoding="utf-8"))
    assert lock["scorable_pilot_text"] == 2
    assert lock["basis_counts"]["by_round"]["0"]["provision"] == 1
    again = gold.lock_gold(session, _connect(gsettings), text_floor=99)
    assert (again.sha256, again.scorable_pilot_text) == (result.sha256, 2)


def test_suggested_n_follows_the_spec_formula(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    pilot = _pilot_ids(session)
    _label_by_basis(session, {pilot[0]: "provision", pilot[1]: "provision"})
    counts = gold.basis_counts(session)
    # n_dev + ceil((floor + 5) * n_pilot / text) = 3 + ceil(15 * 6 / 4) = 3 + 23
    assert gold.suggested_n(session, counts, 10) == N_DEV + 23
    none = gold.BasisCounts(6, dict.fromkeys(gold.BASIS_COUNT_KEYS, 0), {})
    assert gold.suggested_n(session, none, 10) is None


# --- the extended draw (#1492, T167b) ---------------------------------------------


def _random_ids(session: gold.GoldSession) -> set[str]:
    return {c.listing_end_id for c in session.cases if c.in_random_draw}


def _fresh(gsettings: Settings, tmp_path: Path, name: str, n: int) -> gold.GoldSession:
    """The same frame, seed and exclusion drawn with `n` in a separate research store."""
    other = tmp_path / name
    other.mkdir()
    research = gsettings.research.model_copy(update={"data_dir": str(other / "research")})
    settings = gsettings.model_copy(update={"research": research})
    return gold.build_gold_session(
        _write_frame(other, _frame_rows()),
        SEED,
        n,
        _exclusion(other, [("LE030", "")]),
        settings=settings,
        n_dev=N_DEV,
        dev_span_min_rows=DEV_ROWS,
    ).session


def _built_and_labelled(
    gsettings: Settings, tmp_path: Path, seed_cases: tuple[str, ...] = ()
) -> gold.GoldSession:
    session = _build(gsettings, tmp_path, excluded=[("LE030", "")], seed_cases=seed_cases).session
    _label_all(session)
    return session


def test_extend_continues_the_permutation_and_leaves_every_case_unchanged(
    gsettings: Settings, tmp_path: Path
) -> None:
    session = _built_and_labelled(gsettings, tmp_path)
    before_doc = json.loads(session.session_file.read_text(encoding="utf-8"))
    before_lines = session.working_path.read_bytes()
    result = gold.extend_gold_session(session, N + 3)
    after = result.session
    doc = json.loads(after.session_file.read_text(encoding="utf-8"))
    assert doc["n"] == after.n == N + 3
    assert doc["cases"][: len(before_doc["cases"])] == before_doc["cases"]
    assert session.working_path.read_bytes() == before_lines
    assert (len(result.added), result.flipped, result.draw_round) == (3, (), 1)
    assert [c["listing_end_id"] for c in doc["cases"][-3:]] == list(result.added)
    for case in after.cases[-3:]:
        assert (case.split, case.in_random_draw, case.seed_case, case.draw_round) == (
            "pilot",
            True,
            False,
            1,
        )
        assert (
            after.pilot_period[0]
            <= after.rows[case.listing_end_id]["form25_accepted_at"].date()
            <= after.pilot_period[1]
        )
    pilot = [c.listing_end_id for c in after.cases if c.split == "pilot" and c.in_random_draw]
    ciks = [_cik10(after.rows[lid]["cik"]) for lid in pilot]
    assert len(set(ciks)) == len(ciks)
    assert _cik10(1030) not in ciks  # the exclusion still applies
    # exactly what a first draw of n + 3 would have drawn
    assert _random_ids(after) == _random_ids(_fresh(gsettings, tmp_path, "fresh", N + 3))
    # the added cases are open and labelled like any other; the count sees round 1
    assert gold.next_case(after) == len(before_doc["cases"])
    _label_all(after)
    counts = gold.basis_counts(after)
    assert (counts.by_round[0].n_pilot, counts.by_round[1].n_pilot) == (N - N_DEV, 3)
    assert counts.by_round[1].scorable_pilot_text == 3
    assert counts.scorable_pilot_text == N - N_DEV + 3
    again = gold.extend_gold_session(after, N + 4)
    assert again.draw_round == 2 and len(again.added) == 1


def _cik10(value: object) -> str:
    return f"{int(str(value)):010d}"


def test_extend_flips_a_reached_seed_and_keeps_its_label(
    gsettings: Settings, tmp_path: Path
) -> None:
    (reached,) = _random_ids(_fresh(gsettings, tmp_path, "plus1", N + 1)) - _random_ids(
        _fresh(gsettings, tmp_path, "base", N)
    )
    session = _built_and_labelled(gsettings, tmp_path, seed_cases=(reached,))
    seed = next(c for c in session.cases if c.listing_end_id == reached)
    assert (seed.seed_case, seed.in_random_draw) == (True, False)
    label = next(ln for ln in _working(session) if ln["listing_end_id"] == reached)
    result = gold.extend_gold_session(session, N + 1)
    assert (result.added, result.flipped) == ((), (reached,))
    after = result.session
    ids = [c.listing_end_id for c in after.cases]
    assert ids.count(reached) == 1 and len(ids) == len(session.cases)
    flipped = next(c for c in after.cases if c.listing_end_id == reached)
    assert (flipped.seed_case, flipped.in_random_draw, flipped.draw_round) == (True, True, 0)
    assert gold.next_case(after) is None  # its label stands
    assert gold._final_by_case(after)[reached] == label


def test_extend_refuses(gsettings: Settings, tmp_path: Path) -> None:
    session = _build(gsettings, tmp_path).session
    gold.record_label(
        session,
        session.cases[0].listing_end_id,
        label="bankruptcy",
        relied_on="notice",
        basis="text",
        seconds_spent=1,
    )
    with pytest.raises(gold.GoldRefused, match="no final answer"):
        gold.extend_gold_session(session, N + 1)
    _label_all(session)
    with pytest.raises(gold.GoldRefused, match="must exceed the session's n"):
        gold.extend_gold_session(session, N)
    planted = _plant(gsettings, "departure-reason/1.jsonl")
    with pytest.raises(gold.InferenceFilesPresent):
        gold.extend_gold_session(session, N + 1)
    planted.unlink()
    exclude = Path(json.loads(session.session_file.read_text())["exclusion"]["path"])
    original = exclude.read_bytes()
    exclude.write_text("accession,cik\nLE005,\n", encoding="utf-8")
    with pytest.raises(gold.GoldRefused, match="cannot reproduce"):
        gold.extend_gold_session(session, N + 1)
    exclude.write_bytes(original)
    gold.lock_gold(session, _connect(gsettings), text_floor=0)
    with pytest.raises(gold.SessionLocked):
        gold.extend_gold_session(session, N + 1)
    assert json.loads(session.session_file.read_text())["n"] == N


def test_extend_refuses_a_session_whose_draw_it_cannot_reproduce(
    gsettings: Settings, tmp_path: Path
) -> None:
    session = _built_and_labelled(gsettings, tmp_path)
    path = session.session_file
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["seed"] = SEED + 1  # the cases on file are not this seed's draw
    path.write_text(json.dumps(doc), encoding="utf-8")
    edited = gold.open_gold_session(path, gold.GoldFlags(), settings=gsettings)
    with pytest.raises(gold.GoldRefused, match="cannot reproduce"):
        gold.extend_gold_session(edited, N + 1)
