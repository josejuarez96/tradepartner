"""The research-labeling commands (plan T124; research-labeling spec C12 and req 18,
the amendment of 2026-10-08 #1330): `corpus fetch departure-reason`, `research frame
build departure-reason`, `research gold`, `research label` and `research review`.

Every test runs on a fresh fixture store at `store.path` (a temp file), with the
research store in a temp directory, both spend ceilings set and a key-shaped
`typesafe_api_key`, so a leak in any output is detectable. The model client is the
scripted double (`tests/research/fake_model_client.py`) through `make_app`'s
`model_client` edge, EDGAR is a recorded transport, and the review page is never
started: the launcher is a fake that records the argv and, where a test needs it,
answers the session through the same `gold` and `review` functions the page calls,
opening only what the page opens. Nothing is sent and nothing is spent.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl
import pytest
from click.testing import Result
from corpus.test_departure_fetch import KLX, Router, _routes
from research.fake_model_client import Answer, ScriptedModelClient, Step
from research.labeling.test_job import MERGER, UNRESOLVED, _dev, _experiment, _pilot, _row
from typer.testing import CliRunner

from tradepartner import cli
from tradepartner.cli_record import scrub_text
from tradepartner.config import Settings
from tradepartner.research import RunHandle, datafiles
from tradepartner.research.labeling import gold, job, review
from tradepartner.store import research, schema
from tradepartner.store.db import StoreLockedError, open_for_write, open_read_only

MODEL = "jev-1.13.0"
TRANSFER = "exchange_transfer"
NOW = datetime(2026, 10, 9, 12, tzinfo=UTC)
USER_AGENT = "TradePartner test-agent test@example.com"
#: Key-shaped, so the `cli_record` scrub would flag it in any output.
TYPESAFE_KEY = "tsk_live_" + "Q7x" * 12
OPTION_NAMES = (
    "merger_or_acquisition",
    "going_private",
    "instrument_retirement",
    "redomicile_or_reorganisation",
    "bankruptcy",
    "compliance_delisting",
    "voluntary_withdrawal",
    "exchange_transfer",
)
#: A frame batch: listing end -> (rule status, scripted answer). B1, B2 agree (row 5,
#: merger), B3 disagrees, B4 is a model `unresolved`.
BATCH: dict[str, tuple[str, Step]] = {
    "B1": ("delisted", Answer(MERGER)),
    "B2": ("delisted", Answer(MERGER)),
    "B3": ("delisted", Answer(TRANSFER)),
    "B4": ("delisted", Answer(UNRESOLVED)),
}
GOLD_FRAME_ROWS = 340
GOLD_N = 35

Launcher = Callable[[list[str]], int]


# --- the world --------------------------------------------------------------------


@pytest.fixture
def s(tmp_path: Path) -> Settings:
    """A fixture store, the research store under `tmp_path`, both ceilings set and a
    key present, the pace unthrottled; the three labeling registrations."""
    base = Settings(
        _env_file=None,
        sec_edgar_user_agent=USER_AGENT,
        typesafe_api_key=TYPESAFE_KEY,
        store={"path": str(tmp_path / "store.duckdb")},
        edgar={"cache_dir": str(tmp_path / "edgar")},
        research={
            "data_dir": str(tmp_path / "research"),
            "spend_ceiling_usd_month": 40.0,
            "spend_ceiling_usd_total": 40.0,
        },
    )
    labeling = base.research.labeling.model_copy(update={"requests_per_second": 1000.0})
    edgar = base.edgar.model_copy(update={"requests_per_second": 1e6})
    out = base.model_copy(
        update={"research": base.research.model_copy(update={"labeling": labeling}), "edgar": edgar}
    )
    with open_for_write(out) as conn:
        schema.init_schema(conn)
        for parsed in (
            _experiment(
                "departure-reason-batches",
                kind="benchmark",
                dataset="departure-reason-frame",
                splits=("full",),
                metric="yield",
                direction="greater",
                threshold=None,
            ),
            _experiment(
                "departure-reason-pilot",
                kind="benchmark",
                dataset="departure-reason-gold",
                splits=("dev", "pilot"),
                metric="class_accuracy",
                direction="greater",
                threshold=0.80,
            ),
            _experiment(
                "departure-reason-drift",
                kind="robustness",
                dataset="departure-reason-gold",
                splits=("dev",),
                metric="flip_rate",
                direction="less",
                threshold=0.10,
            ),
        ):
            research.register_experiment(conn, parsed, "owner")
    return out


def _parquet(rows: Sequence[Mapping[str, Any]]) -> tuple[bytes, str]:
    buffer = io.BytesIO()
    pl.DataFrame(list(rows), infer_schema_length=None).write_parquet(buffer)
    data = buffer.getvalue()
    return data, hashlib.sha256(data).hexdigest()


def _frame(s: Settings, rows: Sequence[Mapping[str, Any]]) -> int:
    """A frame export at its content address, registered; its dataset id."""
    data, sha = _parquet(rows)
    path = datafiles.frame_path(s, sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    days = [r["form25_accepted_at"].date() for r in rows]
    with open_for_write(s) as conn:
        return research.register_dataset(
            conn,
            name="departure-reason-frame",
            version=sha[:12],
            path=str(path),
            sha256=sha,
            event_start=min(days),
            event_end=max(days),
            n_rows=len(rows),
            event_column="form25_accepted_at",
        ).dataset_id


def _gold(s: Settings, dev: Sequence[Mapping[str, Any]], pilot: Sequence[Mapping[str, Any]]) -> int:
    """A locked gold export at its content address, `pilot` sealed; its dataset id."""
    rows = [*dev, *pilot]
    data, sha = _parquet(rows)
    path = datafiles.gold_path(s, sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    splits = datafiles.gold_splits_path(s, sha)
    splits.write_text(json.dumps({"splits": ["dev"] * len(dev) + ["pilot"] * len(pilot)}))
    dev_days = [r["form25_accepted_at"].date() for r in dev]
    pilot_days = [r["form25_accepted_at"].date() for r in pilot]
    pilot_span = (min(pilot_days), max(pilot_days))
    with open_for_write(s) as conn:
        return research.register_dataset(
            conn,
            name="departure-reason-gold",
            version=sha[:12],
            path=str(path),
            sha256=sha,
            event_start=min(dev_days),
            event_end=max(pilot_days),
            n_rows=len(rows),
            event_column="form25_accepted_at",
            split_path=str(splits),
            split_sha256=hashlib.sha256(splits.read_bytes()).hexdigest(),
            split_spans={"dev": (min(dev_days), max(dev_days)), "pilot": pilot_span},
            sealed_splits=("pilot",),
            sealed_periods=(pilot_span,),
            locked=True,
            seed=11,
        ).dataset_id


def _no_launch(argv: list[str]) -> int:
    raise AssertionError(f"no page should start here: {argv}")


def _no_client(_settings: Settings, _handle: RunHandle) -> ScriptedModelClient:
    raise AssertionError("no model client should be built here")


def _cli(
    s: Settings,
    *args: str,
    launcher: Launcher = _no_launch,
    client: job.ClientFactory = _no_client,
    edgar_client: Any = None,
) -> Result:
    app = cli.make_app(
        settings=lambda: s,
        clock=lambda: NOW,
        launcher=launcher,
        model_client=client,
        edgar_client=edgar_client,
    )
    result = CliRunner().invoke(app, list(args))
    _, hits = scrub_text(result.output, secrets=[TYPESAFE_KEY, USER_AGENT])
    assert hits == 0, result.output
    assert TYPESAFE_KEY not in result.output
    return result


def _count(s: Settings, sql: str, *params: Any) -> int:
    with duckdb.connect(s.store.path, read_only=True) as conn:
        row = conn.execute(sql, list(params)).fetchone()
    assert row is not None
    return int(row[0])


def _datasets(s: Settings, name: str) -> int:
    return _count(s, "SELECT count(*) FROM research_datasets WHERE name = ?", name)


def _store_digest(s: Settings) -> str:
    """Every research registry table's rows, hashed: "nothing written to the store"."""
    with duckdb.connect(s.store.path, read_only=True) as conn:
        parts = [
            repr(conn.execute(f"SELECT * FROM {t} ORDER BY ALL").fetchall())
            for t in ("research_datasets", "research_runs", "research_results")
        ]
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _page_args(argv: list[str]) -> tuple[dict[str, str], list[str]]:
    """The Streamlit flags before `--` and the page's own arguments after it, asserted
    as `tradepartner dashboard`'s test does."""
    assert argv[1:4] == ["-m", "streamlit", "run"]
    page = Path(argv[4])
    assert page == cli.REVIEW_PAGE and page.is_file()
    assert page.name == "review_page.py" and page.parent.name == "labeling"
    split = argv.index("--")
    flags = dict(zip(argv[5:split:2], argv[6:split:2], strict=True))
    return flags, argv[split + 1 :]


def _assert_localhost(flags: Mapping[str, str]) -> None:
    assert flags == {"--server.address": "localhost", "--browser.gatherUsageStats": "false"}


def _assert_no_content(output: str, ids: Sequence[str]) -> None:
    """Nothing of a session's content: no option name, no case id, no probability."""
    for name in OPTION_NAMES:
        assert name not in output, output
    for lid in ids:
        assert lid not in output, output
    assert "probabilit" not in output


# --- the parser -------------------------------------------------------------------


@pytest.mark.parametrize("command", ["probe", "spend", "record-fixtures"])
def test_the_dropped_research_commands_do_not_exist(s: Settings, command: str) -> None:
    result = _cli(s, "research", command)
    assert result.exit_code == 2
    assert "No such command" in result.output


@pytest.mark.parametrize(
    "args",
    [
        ("research", "gold", "--out", "x"),
        ("research", "review", "--run", "1", "--out", "x"),
        ("research", "label", "s", "--dataset", "1", "--split", "dev", "--out", "x"),
        ("research", "label", "s", "--dataset", "1", "--split", "dev", "--synthetic"),
    ],
)
def test_no_out_or_synthetic_flag(s: Settings, args: tuple[str, ...]) -> None:
    result = _cli(s, *args)
    assert result.exit_code == 2
    assert "No such option" in result.output


def test_every_command_and_option_exists(s: Settings) -> None:
    for args, options in (
        (("corpus", "fetch", "departure-reason"), ("--since", "--until", "--cik", "--limit")),
        (("research", "frame", "build", "departure-reason"), ("--corpus", "--as-of", "--register")),
        (("research", "gold"), ("--frame", "--seed", "--n", "--exclude", "--lock")),
        (
            ("research", "label"),
            (
                "--dataset",
                "--split",
                "--dry-run",
                "--configurations",
                "--accepted-from",
                "--accepted-to",
                "--limit",
                "--model",
            ),
        ),
        (("research", "review"), ("--run", "--finish")),
    ):
        result = _cli(s, *args, "--help")
        assert result.exit_code == 0, result.output
        for option in options:
            assert option in result.output, (args, option)


# --- corpus fetch and frame build -------------------------------------------------


def test_corpus_fetch_then_frame_build_registers_the_frame(s: Settings, tmp_path: Path) -> None:
    router = Router(_routes())
    fetched = _cli(
        s,
        "corpus",
        "fetch",
        "departure-reason",
        "--since",
        "2026-07-01",
        "--until",
        "2026-09-30",
        edgar_client=router.client(),
    )
    assert fetched.exit_code == 0, fetched.output
    corpus = Path(s.edgar.cache_dir).resolve() / "corpus" / "departure-reason" / "corpus.jsonl"
    assert corpus.is_file()
    assert "index_rows_seen 5, kept 1" in fetched.output
    assert "identity_holds True" in fetched.output

    printed = _cli(
        s,
        "research",
        "frame",
        "build",
        "departure-reason",
        "--corpus",
        str(corpus),
        "--as-of",
        "2026-10-09T00:00:00+00:00",
    )
    assert printed.exit_code == 0, printed.output
    assert "register it with: tradepartner dataset register --name departure-reason-frame" in (
        printed.output.replace("\n", " ")
    )
    assert _datasets(s, "departure-reason-frame") == 0

    registered = _cli(
        s,
        "research",
        "frame",
        "build",
        "departure-reason",
        "--corpus",
        str(corpus),
        "--as-of",
        "2026-10-09T00:00:00+00:00",
        "--register",
    )
    assert registered.exit_code == 0, registered.output
    assert "1 rows" in registered.output
    assert _datasets(s, "departure-reason-frame") == 1
    with duckdb.connect(s.store.path, read_only=True) as conn:
        row = conn.execute(
            "SELECT event_column, event_start FROM research_datasets "
            "WHERE name = 'departure-reason-frame'"
        ).fetchone()
    assert row == ("form25_accepted_at", date(2026, 9, 24))
    assert KLX not in registered.output.replace(str(corpus), "")


def test_frame_build_refuses_an_as_of_without_its_offset(s: Settings, tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text("")
    result = _cli(
        s,
        "research",
        "frame",
        "build",
        "departure-reason",
        "--corpus",
        str(corpus),
        "--as-of",
        "2026-10-09T00:00:00",
    )
    assert result.exit_code == 2
    assert "no UTC offset" in result.output


def test_corpus_fetch_needs_the_edgar_user_agent(s: Settings) -> None:
    bare = s.model_copy(update={"sec_edgar_user_agent": None})
    result = _cli(bare, "corpus", "fetch", "departure-reason", "--until", "2026-09-30")
    assert result.exit_code == 2
    assert "SEC_EDGAR_USER_AGENT" in result.output


# --- research label ---------------------------------------------------------------


def _factory(drift: ScriptedModelClient, batch: ScriptedModelClient) -> job.ClientFactory:
    return lambda _s, handle: drift if handle.slug == job.DRIFT_SLUG else batch


def _baseline(s: Settings) -> tuple[int, int]:
    """A gold dataset and the frozen configuration's `dev` run over it, through the
    CLI: `(gold id, baseline run id)`."""
    gold_id = _gold(s, _dev(1), _pilot(1))
    result = _cli(
        s,
        "research",
        "label",
        "departure-reason-pilot",
        "--dataset",
        str(gold_id),
        "--split",
        "dev",
        "--model",
        MODEL,
        client=lambda _s, _h: ScriptedModelClient([Answer(MERGER)]),
    )
    assert result.exit_code == 0, result.output
    assert "run 1: ok" in result.output
    return gold_id, 1


def _batch(s: Settings) -> int:
    """The four-row frame batch, labelled through the CLI after the drift probe and
    left unfinished for review; its run id."""
    gold_id, baseline = _baseline(s)
    start = datetime(2019, 1, 2, 15, tzinfo=UTC)
    rows = [
        {**_row(lid, start + timedelta(days=i), eightk=False), "rule_status": status}
        for i, (lid, (status, _)) in enumerate(BATCH.items())
    ]
    frame_id = _frame(s, rows)
    batch_client = ScriptedModelClient([step for _, step in BATCH.values()])
    result = _cli(
        s,
        "research",
        "label",
        "departure-reason-batches",
        "--dataset",
        str(frame_id),
        "--split",
        "full",
        "--model",
        MODEL,
        "--drift-gold",
        str(gold_id),
        "--drift-baseline-run",
        str(baseline),
        client=_factory(ScriptedModelClient([Answer(MERGER)]), batch_client),
    )
    assert result.exit_code == 0, result.output
    run_id = int(result.output.split("run ")[1].split(":")[0])
    assert f"run {run_id}: unfinished" in result.output
    assert "shortlist: 4 items to review, 0 deferred" in result.output
    _assert_no_content(result.output, list(BATCH))
    assert len(batch_client.requests) == len(BATCH)
    return run_id


def test_label_scores_a_gold_split_on_a_non_synthetic_run(s: Settings) -> None:
    _baseline(s)
    assert _count(s, "SELECT count(*) FROM research_runs WHERE NOT synthetic") == 1
    assert _count(s, "SELECT count(*) FROM research_runs WHERE synthetic") == 0
    assert datafiles.inference_path(s, 1).is_file()


def test_label_runs_a_frame_batch_after_the_drift_probe(s: Settings) -> None:
    run_id = _batch(s)
    assert _count(s, "SELECT count(*) FROM research_results WHERE run_id = ?", run_id) == 0
    assert datafiles.shortlist_path(s, run_id).is_file()


def test_label_dry_run_opens_no_run_and_calls_nothing(s: Settings) -> None:
    rows = [
        _row(lid, datetime(2019, 1, 2, 15, tzinfo=UTC) + timedelta(days=i), eightk=True)
        for i, lid in enumerate(BATCH)
    ]
    frame_id = _frame(s, rows)
    before = _store_digest(s)
    result = _cli(
        s,
        "research",
        "label",
        "departure-reason-batches",
        "--dataset",
        str(frame_id),
        "--split",
        "full",
        "--dry-run",
    )
    assert result.exit_code == 0, result.output
    assert "rows: 4, first calls: 4" in result.output
    assert "estimate: $" in result.output
    assert "headroom: total $40.000000, month $40.000000" in result.output
    assert "dry run: no run opened, no call made" in result.output
    assert _store_digest(s) == before
    assert not (Path(s.research.data_dir) / "inferences").exists()


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ((), "--model is required"),
        (("--model", "jev-latest"), "alias"),
        (("--model", MODEL, "--drift-gold", "1"), "go with a batch split"),
        (("--model", MODEL, "--spend-holdout"), "needs a non-blank --holdout-reason"),
        (("--model", MODEL, "--holdout-reason", "x"), "goes with --spend-holdout"),
    ],
)
def test_label_refuses_before_anything_opens(
    s: Settings, extra: tuple[str, ...], message: str
) -> None:
    gold_id = _gold(s, _dev(1), _pilot(1))
    result = _cli(
        s,
        "research",
        "label",
        "departure-reason-pilot",
        "--dataset",
        str(gold_id),
        "--split",
        "dev",
        *extra,
    )
    assert result.exit_code == 2, result.output
    assert message in result.output
    assert _count(s, "SELECT count(*) FROM research_runs") == 0


def test_label_a_batch_without_the_drift_flags_is_refused(s: Settings) -> None:
    frame_id = _frame(s, [_row("B1", datetime(2019, 1, 2, 15, tzinfo=UTC), eightk=False)])
    result = _cli(
        s,
        "research",
        "label",
        "departure-reason-batches",
        "--dataset",
        str(frame_id),
        "--split",
        "full",
        "--model",
        MODEL,
    )
    assert result.exit_code == 2
    assert "--drift-gold and --drift-baseline-run" in result.output
    assert _count(s, "SELECT count(*) FROM research_runs") == 0


def test_label_the_sealed_pilot_without_the_holdout_flags_is_a_refusal(s: Settings) -> None:
    gold_id = _gold(s, _dev(1), _pilot(1))
    result = _cli(
        s,
        "research",
        "label",
        "departure-reason-pilot",
        "--dataset",
        str(gold_id),
        "--split",
        "pilot",
        "--model",
        MODEL,
        client=lambda _s, _h: ScriptedModelClient([Answer(MERGER)]),
    )
    assert result.exit_code == 2, result.output
    assert "refused_holdout" in result.output


def test_label_a_failed_run_exits_one_and_keeps_its_row(s: Settings) -> None:
    gold_id = _gold(s, _dev(1), _pilot(1))
    result = _cli(
        s,
        "research",
        "label",
        "departure-reason-pilot",
        "--dataset",
        str(gold_id),
        "--split",
        "dev",
        "--model",
        MODEL,
        client=lambda _s, _h: ScriptedModelClient([Answer(MERGER, model="jev-1.14.0")]),
    )
    assert result.exit_code == 1, result.output
    assert "run 1: failed" in result.output and "model id mismatch" in result.output
    assert _count(s, "SELECT count(*) FROM research_results WHERE outcome = 'failed'") == 1


def test_label_an_unexpected_error_keeps_the_failed_close_and_scrubs(s: Settings) -> None:
    gold_id = _gold(s, _dev(1), _pilot(1))

    def explode(_s: Settings, _h: RunHandle) -> ScriptedModelClient:
        raise RuntimeError(f"boom with {TYPESAFE_KEY}")

    result = _cli(
        s,
        "research",
        "label",
        "departure-reason-pilot",
        "--dataset",
        str(gold_id),
        "--split",
        "dev",
        "--model",
        MODEL,
        client=explode,
    )
    assert result.exit_code == 1
    assert "failed: RuntimeError" in result.output
    # The job closed its run `failed`; the command committed that close.
    assert _count(s, "SELECT count(*) FROM research_results WHERE outcome = 'failed'") == 1


# --- research review --------------------------------------------------------------


def _decide_all(s: Settings, run_id: int, version: str, *, stop_after: int | None = None) -> None:
    """What the page does: rebuild the session on a read-only connection (which fails
    if the command still holds its write connection) and decide items."""
    session = review.build_review_session(
        run_id, settings=s, connect=lambda: open_read_only(s), code_version=version
    )
    for n, item in enumerate(session.items):
        if stop_after is not None and n >= stop_after:
            break
        review.record_decision(
            session, item, decision="a", reason="filing states it", relied_on="notice"
        )


def _review_page(
    s: Settings,
    run_id: int,
    seen: list[list[str]],
    *,
    stop_after: int | None = None,
    then: Callable[[], int] = lambda: 0,
) -> Launcher:
    def launch(argv: list[str]) -> int:
        seen.append(argv)
        flags, page = _page_args(argv)
        _assert_localhost(flags)
        assert page[:2] == ["--session", str(datafiles.review_path(s, run_id))]
        assert page[2] == "--code-version"
        _decide_all(s, run_id, page[3], stop_after=stop_after)
        return then()

    return launch


def test_review_launches_the_page_then_finishes_a_complete_session(s: Settings) -> None:
    run_id = _batch(s)
    seen: list[list[str]] = []
    result = _cli(
        s, "research", "review", "--run", str(run_id), launcher=_review_page(s, run_id, seen)
    )
    assert result.exit_code == 0, result.output
    assert len(seen) == 1
    flags, page = _page_args(seen[0])
    _assert_localhost(flags)
    assert page[0:2] == ["--session", str(datafiles.review_path(s, run_id))]
    commit, dirty = cli.registry.code_version()
    assert page[2:] == ["--code-version", f"{commit}+dirty" if dirty else commit]
    sha = hashlib.sha256(datafiles.review_path(s, run_id).read_bytes()).hexdigest()
    assert f"review finished: run {run_id} ok, n_reviewed 4" in result.output
    assert sha in result.output
    assert _count(s, "SELECT count(*) FROM research_results WHERE run_id = ?", run_id) == 1
    assert _datasets(s, "departure-reason-reviews") == 1
    _assert_no_content(result.output, list(BATCH))
    lines = datafiles.read_jsonl(datafiles.review_path(s, run_id))
    assert {line["code_version"] for line in lines} == {page[3]}


@pytest.mark.parametrize("ending", ["ctrl_c", "nonzero"])
def test_review_finishes_after_ctrl_c_or_a_nonzero_status(s: Settings, ending: str) -> None:
    run_id = _batch(s)

    def then() -> int:
        if ending == "ctrl_c":
            raise KeyboardInterrupt
        return 3

    result = _cli(
        s,
        "research",
        "review",
        "--run",
        str(run_id),
        launcher=_review_page(s, run_id, [], then=then),
    )
    assert result.exit_code == 0, result.output
    assert f"review finished: run {run_id} ok, n_reviewed 4" in result.output


def test_review_partial_prints_the_open_count_and_stays_unfinished(s: Settings) -> None:
    run_id = _batch(s)
    result = _cli(
        s,
        "research",
        "review",
        "--run",
        str(run_id),
        launcher=_review_page(s, run_id, [], stop_after=1),
    )
    assert result.exit_code == 0, result.output
    assert "3 of 4 items open; left unfinished" in result.output
    assert _count(s, "SELECT count(*) FROM research_results WHERE run_id = ?", run_id) == 0
    assert not review.build_review_session(
        run_id, settings=s, connect=lambda: open_read_only(s), code_version="x"
    ).finishing_path.exists()


def test_review_store_busy_exits_nonzero_and_writes_nothing(
    s: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _batch(s)
    path = datafiles.review_path(s, run_id)
    snapshot: dict[str, Any] = {}

    def then() -> int:
        snapshot["review"] = path.read_bytes()
        snapshot["store"] = _store_digest(s)
        monkeypatch.setattr(cli, "open_for_write", _busy)
        return 0

    result = _cli(
        s,
        "research",
        "review",
        "--run",
        str(run_id),
        launcher=_review_page(s, run_id, [], then=then),
    )
    assert result.exit_code == 1, result.output
    assert "store busy" in result.output
    assert path.read_bytes() == snapshot["review"]
    assert _store_digest(s) == snapshot["store"]

    monkeypatch.undo()
    recovered = _cli(s, "research", "review", "--run", str(run_id), "--finish")
    assert recovered.exit_code == 0, recovered.output
    assert f"review finished: run {run_id} ok, n_reviewed 4" in recovered.output
    assert path.read_bytes() == snapshot["review"]


def test_review_finish_on_a_partial_session_is_refused(s: Settings) -> None:
    run_id = _batch(s)
    _decide_all(s, run_id, "abc", stop_after=2)
    result = _cli(s, "research", "review", "--run", str(run_id), "--finish")
    assert result.exit_code == 2
    assert "2 of 4 items open" in result.output
    assert _count(s, "SELECT count(*) FROM research_results WHERE run_id = ?", run_id) == 0


def test_review_refuses_a_synthetic_run_on_the_real_store(s: Settings, tmp_path: Path) -> None:
    """The registry's file-identity check: a synthetic run never attaches on the store
    the CLI is configured for."""
    gold_id, baseline = _baseline(s)
    frame_id = _frame(s, [_row("S1", datetime(2019, 1, 2, 15, tzinfo=UTC), eightk=False)])
    elsewhere = s.model_copy(
        update={"store": s.store.model_copy(update={"path": str(tmp_path / "other.duckdb")})}
    )
    with open_for_write(s) as conn:
        synthetic = job.run_batch(
            conn,
            elsewhere,
            _factory(
                ScriptedModelClient([Answer(MERGER)]), ScriptedModelClient([Answer(TRANSFER)])
            ),
            "departure-reason-batches",
            frame_id,
            "full",
            model=MODEL,
            drift=job.DriftProbe(dataset_id=gold_id, baseline_run_id=baseline),
            synthetic=True,
            clock=lambda: NOW,
            sleep=lambda _s: None,
        )
    assert synthetic.outcome == "unfinished", synthetic
    result = _cli(s, "research", "review", "--run", str(synthetic.run_id))
    assert result.exit_code == 2
    assert "synthetic" in result.output


# --- research gold ----------------------------------------------------------------


def _gold_frame(s: Settings) -> int:
    """340 listing ends, one CIK each: the first 300 in 2016 (the dev span), the rest
    in 2017; registered at the content address."""
    rows = []
    for i in range(GOLD_FRAME_ROWS):
        lid = f"G{i:03d}"
        cik = f"{2000 + i:010d}"
        accepted = (
            datetime(2016, 1, 4, 15, tzinfo=UTC) + timedelta(hours=i)
            if i < 300
            else datetime(2017, 1, 4, 15, tzinfo=UTC) + timedelta(days=i - 300)
        )
        documents = json.loads(_row(lid, accepted, eightk=True)["documents"])
        documents["cik"] = cik
        rows.append(
            {
                **_row(lid, accepted, eightk=True),
                "cik": cik,
                "documents": json.dumps(documents, sort_keys=True),
            }
        )
    return _frame(s, rows)


def _exclusion(tmp_path: Path) -> Path:
    path = tmp_path / "sample.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["accession", "cik"])
        writer.writerow(["G000", "2000"])
    return path


def _label_cases(s: Settings, *, stop_after: int | None = None) -> None:
    """What the page does in gold mode: resume the session (no store connection) and
    answer cases in order."""
    session = gold.open_gold_session(gold.session_path(s), gold.GoldFlags(), settings=s)
    n = 0
    while (i := gold.next_case(session)) is not None:
        if stop_after is not None and n >= stop_after:
            break
        gold.record_label(
            session,
            session.cases[i].listing_end_id,
            label=MERGER,
            relied_on="notice",
            seconds_spent=42.0,
        )
        n += 1


def _gold_page(
    s: Settings,
    seen: list[list[str]],
    *,
    stop_after: int | None = None,
    then: Callable[[], int] = lambda: 0,
) -> Launcher:
    def launch(argv: list[str]) -> int:
        seen.append(argv)
        flags, page = _page_args(argv)
        _assert_localhost(flags)
        assert page == ["--session", str(gold.session_path(s))]
        _label_cases(s, stop_after=stop_after)
        return then()

    return launch


def _gold_cli(s: Settings, tmp_path: Path, launcher: Launcher, *extra: str) -> Result:
    frame_id = _gold_frame(s)
    return _cli(
        s,
        "research",
        "gold",
        "--frame",
        str(frame_id),
        "--seed",
        "7",
        "--n",
        str(GOLD_N),
        "--exclude",
        str(_exclusion(tmp_path)),
        *extra,
        launcher=launcher,
    )


def _case_ids(s: Settings) -> list[str]:
    doc = json.loads(gold.session_path(s).read_text())
    return [c["listing_end_id"] for c in doc["cases"]]


def test_gold_builds_launches_and_locks_a_complete_session(s: Settings, tmp_path: Path) -> None:
    seen: list[list[str]] = []
    result = _gold_cli(s, tmp_path, _gold_page(s, seen))
    assert result.exit_code == 0, result.output
    assert len(seen) == 1
    assert "exclusion: 1 accessions matched, 1 issuer CIKs, 1 rows removed" in result.output
    assert f"gold session: {GOLD_N} cases (30 dev, 5 pilot), {GOLD_N} open" in result.output
    lock = json.loads((gold.session_path(s).with_name("lock.json")).read_text())
    assert (
        f"gold session locked: dataset {lock['dataset_id']}, scorable pilot count 5, "
        f"sha256 {lock['sha256']}" in result.output
    )
    assert _datasets(s, "departure-reason-gold") == 1
    _assert_no_content(result.output, _case_ids(s))


def test_gold_with_no_flags_resumes_and_locks(s: Settings, tmp_path: Path) -> None:
    partial = _gold_cli(s, tmp_path, _gold_page(s, [], stop_after=3))
    assert partial.exit_code == 0, partial.output
    assert f"{GOLD_N - 3} of {GOLD_N} cases open; left unlocked" in partial.output
    assert not gold.session_path(s).with_name("lock.json").exists()
    assert _datasets(s, "departure-reason-gold") == 0

    seen: list[list[str]] = []
    resumed = _cli(s, "research", "gold", launcher=_gold_page(s, seen))
    assert resumed.exit_code == 0, resumed.output
    assert f"{GOLD_N - 3} open" in resumed.output
    assert "gold session locked" in resumed.output
    assert _datasets(s, "departure-reason-gold") == 1
    _assert_no_content(partial.output + resumed.output, _case_ids(s))


def test_gold_with_flags_that_differ_from_the_session_refuses(s: Settings, tmp_path: Path) -> None:
    _gold_cli(s, tmp_path, _gold_page(s, [], stop_after=0))
    result = _cli(s, "research", "gold", "--seed", "8")
    assert result.exit_code == 2
    assert "flags differ from the gold session" in result.output


@pytest.mark.parametrize("ending", ["ctrl_c", "nonzero"])
def test_gold_locks_after_ctrl_c_or_a_nonzero_status(
    s: Settings, tmp_path: Path, ending: str
) -> None:
    def then() -> int:
        if ending == "ctrl_c":
            raise KeyboardInterrupt
        return 1

    result = _gold_cli(s, tmp_path, _gold_page(s, [], then=then))
    assert result.exit_code == 0, result.output
    assert "gold session locked" in result.output


def test_gold_store_busy_exits_nonzero_and_writes_nothing(
    s: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot: dict[str, Any] = {}

    def then() -> int:
        snapshot["working"] = datafiles.gold_working_path(s).read_bytes()
        snapshot["store"] = _store_digest(s)
        monkeypatch.setattr(cli, "open_for_write", _busy)
        return 0

    result = _gold_cli(s, tmp_path, _gold_page(s, [], then=then))
    assert result.exit_code == 1, result.output
    assert "store busy" in result.output
    assert datafiles.gold_working_path(s).read_bytes() == snapshot["working"]
    assert _store_digest(s) == snapshot["store"]
    assert not gold.session_path(s).with_name("lock.json").exists()

    monkeypatch.undo()
    recovered = _cli(s, "research", "gold", "--lock")
    assert recovered.exit_code == 0, recovered.output
    assert "gold session locked" in recovered.output
    assert _datasets(s, "departure-reason-gold") == 1


def test_gold_lock_refuses_a_partial_session_and_flags(s: Settings, tmp_path: Path) -> None:
    _gold_cli(s, tmp_path, _gold_page(s, [], stop_after=2))
    partial = _cli(s, "research", "gold", "--lock")
    assert partial.exit_code == 2
    assert f"{GOLD_N - 2} of {GOLD_N} cases open" in partial.output
    with_flags = _cli(s, "research", "gold", "--lock", "--seed", "7")
    assert with_flags.exit_code == 2
    assert "--lock runs alone" in with_flags.output
    assert _datasets(s, "departure-reason-gold") == 0


def test_gold_refuses_while_an_inference_file_exists(s: Settings, tmp_path: Path) -> None:
    planted = Path(s.research.data_dir) / "inferences" / "departure-reason" / "9.jsonl"
    planted.parent.mkdir(parents=True)
    planted.write_text("{}\n")
    result = _gold_cli(s, tmp_path, _no_launch)
    assert result.exit_code == 2
    assert "gold is made before any model output" in result.output
    assert not gold.session_path(s).exists()


def test_gold_a_new_session_needs_its_flags(s: Settings) -> None:
    result = _cli(s, "research", "gold", "--seed", "7")
    assert result.exit_code == 2
    assert "needs --frame, --seed and --exclude" in result.output


@contextmanager
def _busy(_settings: Settings) -> Iterator[duckdb.DuckDBPyConnection]:
    raise StoreLockedError("held by another process")
    yield  # pragma: no cover
