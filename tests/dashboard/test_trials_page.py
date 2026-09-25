"""Tests for the trial-registry view (Phase 3 T44, spec req 17).

Driven headless through `streamlit.testing.v1.AppTest` on the shell, as
`test_backtest_page.py` does, over a temp store seeded only through
`store.registry`. `load_registry_view` is also exercised directly: it is
the page's only reader and needs no Streamlit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from streamlit.testing.v1 import AppTest

from tradepartner.config import Settings
from tradepartner.dashboard import trials_page
from tradepartner.store import registry, schema
from tradepartner.store.db import open_for_write

_APP_PATH = str(
    Path(__file__).resolve().parents[2] / "src" / "tradepartner" / "dashboard" / "app.py"
)
_PAGE = "Trial registry"


def _hypothesis(conn: duckdb.DuckDBPyConnection, settings: Settings, slug: str) -> int:
    return registry.register_hypothesis(
        conn,
        slug=slug,
        family="momentum",
        title=f"{slug} title",
        doc_path=f"docs/hypotheses/{slug}.md",
        doc_sha256="0" * 64,
        params={"costs.per_side_bps": 15.0, "strategy.top_fraction": 0.1},
        in_sample_start=date(2017, 1, 31),
        holdout_start=date(2023, 1, 1),
        holdout_end=date(2025, 12, 31),
        registered_by="owner",
        settings=settings,
    ).hypothesis_id


def _open(
    conn: duckdb.DuckDBPyConnection, settings: Settings, hypothesis_id: int, **kwargs: Any
) -> registry.TrialHandle:
    values: dict[str, Any] = {
        "kind": "in_sample",
        "start_session": date(2020, 1, 31),
        "end_session": date(2020, 3, 31),
        "data_cutoff": datetime(2020, 3, 31, 20, tzinfo=UTC),
        "synthetic": False,
        "run_by": "owner",
    }
    values.update(kwargs)
    return registry.open_trial(conn, hypothesis_id=hypothesis_id, settings=settings, **values)


def _ok(conn: duckdb.DuckDBPyConnection, handle: registry.TrialHandle) -> None:
    registry.write_result(
        conn,
        handle,
        registry.ResultStatistics(
            n_trials=1,
            sharpe_variance=None,
            sr_star=0.0,
            psr_zero=0.9,
            dsr=0.9,
            sharpe_variance_excess=None,
            sr_star_excess=0.0,
            psr_zero_excess=0.6,
            dsr_excess=0.6,
            dsr_basis="psr",
            red_flag=False,
            gap_max_count_share=0.03,
            gap_max_size_share=0.004,
        ),
    )


class Seeded:
    def __init__(self) -> None:
        self.ok = 0
        self.failed = 0
        self.refused = 0
        self.unfinished = 0
        self.synthetic = 0
        self.other = 0


def _seed(store_path: Path, tmp_path: Path) -> Seeded:
    """Two hypotheses; on h1 an ok, a failed, a refused, an unfinished and a
    synthetic trial; on h2 one ok trial; and two owner decisions."""
    store_settings = Settings(_env_file=None, store={"path": str(store_path)})
    # A different "real store" path, so synthetic trials are allowed here.
    seed_settings = Settings(_env_file=None, store={"path": str(tmp_path / "real.duckdb")})
    seeded = Seeded()
    with open_for_write(store_settings) as conn:
        schema.init_schema(conn)
        h1 = _hypothesis(conn, seed_settings, "h1-momentum-12-1")
        h2 = _hypothesis(conn, seed_settings, "h2-momentum-top-20")
        ok = _open(conn, seed_settings, h1)
        _ok(conn, ok)
        seeded.ok = ok.trial_id
        failed = _open(conn, seed_settings, h1)
        registry.close_trial(conn, failed, "failed", "provider raised: no bars for SPY")
        seeded.failed = failed.trial_id
        refused = _open(conn, seed_settings, h1, kind="holdout", start_session=date(2023, 1, 31))
        registry.close_trial(conn, refused, "refused_gap", "gap 7.1% over the 5% threshold")
        seeded.refused = refused.trial_id
        seeded.unfinished = _open(conn, seed_settings, h1).trial_id
        synthetic = _open(conn, seed_settings, h1, synthetic=True)
        _ok(conn, synthetic)
        seeded.synthetic = synthetic.trial_id
        other = _open(conn, seed_settings, h2)
        _ok(conn, other)
        seeded.other = other.trial_id
        registry.record_decision(
            conn,
            kind="gap_signoff",
            reason="free-data gap accepted for H1",
            values={"gap_count_share": 0.031},
            hypothesis_id=h1,
        )
        registry.record_decision(
            conn,
            kind="gap_override",
            reason="override for the h2 exploratory run",
            values={"gap_count_share": 0.061},
            trial_id=other.trial_id,
        )
    return seeded


@pytest.fixture
def seeded_store(tmp_path: Path) -> tuple[Path, Seeded]:
    store_path = tmp_path / "store.duckdb"
    return store_path, _seed(store_path, tmp_path)


def _read(store_path: Path, **kwargs: Any) -> trials_page.RegistryView:
    conn = duckdb.connect(str(store_path), read_only=True)
    try:
        return trials_page.load_registry_view(conn, **kwargs)
    finally:
        conn.close()


# --- load_registry_view (pure reader) -----------------------------------------


def test_trials_newest_first_synthetic_hidden(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, s = seeded_store
    view = _read(store_path)
    assert [t.trial_id for t in view.trials] == [s.other, s.unfinished, s.refused, s.failed, s.ok]


def test_synthetic_shown_when_asked(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, s = seeded_store
    view = _read(store_path, include_synthetic=True)
    assert s.synthetic in [t.trial_id for t in view.trials]
    assert [t.trial_id for t in view.trials] == sorted(
        (t.trial_id for t in view.trials), reverse=True
    )


def test_hypothesis_filter(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, s = seeded_store
    view = _read(store_path, slug="h2-momentum-top-20")
    assert [t.trial_id for t in view.trials] == [s.other]
    assert view.slugs == ("h1-momentum-12-1", "h2-momentum-top-20")


def test_failed_refused_and_unfinished_keep_their_message(
    seeded_store: tuple[Path, Seeded],
) -> None:
    store_path, s = seeded_store
    by_id = {t.trial_id: t for t in _read(store_path).trials}
    assert (by_id[s.failed].status, by_id[s.failed].message) == (
        "failed",
        "provider raised: no bars for SPY",
    )
    assert by_id[s.refused].status == "refused_gap"
    assert by_id[s.unfinished].status == registry.UNFINISHED
    assert by_id[s.unfinished].finished_at is None


def test_decisions_newest_first_and_filtered(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, s = seeded_store
    view = _read(store_path)
    assert [d.kind for d in view.decisions] == ["gap_override", "gap_signoff"]
    override = view.decisions[0]
    assert (override.trial_id, override.slug) == (s.other, "h2-momentum-top-20")
    assert override.values_json == '{"gap_count_share":0.061}'
    # A decision tied to a trial is found through that trial's hypothesis.
    h1 = _read(store_path, slug="h1-momentum-12-1")
    assert [d.reason for d in h1.decisions] == ["free-data gap accepted for H1"]


def test_status_counts(seeded_store: tuple[Path, Seeded]) -> None:
    store_path, _ = seeded_store
    counts = _read(store_path).status_counts
    assert counts == {"ok": 2, "failed": 1, "refused_gap": 1, "unfinished": 1}


# --- headless render ------------------------------------------------------------


def _app(monkeypatch: pytest.MonkeyPatch, store_path: Path) -> AppTest:
    monkeypatch.setenv("STORE__PATH", str(store_path))
    monkeypatch.setenv("TRADEPARTNER_ENV_FILE", str(store_path.parent / "does-not-exist.env"))
    at = AppTest.from_file(_APP_PATH)
    at.run()
    at.sidebar.radio[0].set_value(_PAGE).run()
    return at


def _text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown]
    parts += [c.value for c in at.caption]
    parts += [h.value for h in at.subheader]
    parts += [e.value for kind in (at.info, at.warning, at.error, at.success) for e in kind]
    return "\n".join(str(p) for p in parts)


def _trial_ids(at: AppTest) -> list[int]:
    return [int(i) for i in at.dataframe[0].value["trial"].to_list()]


def test_page_is_in_the_shell_navigation(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    at = _app(monkeypatch, seeded_store[0])
    assert not at.exception
    assert at.sidebar.radio[0].options == ["Data health", "Backtest", _PAGE]


def test_render_lists_trials_and_decisions(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, s = seeded_store
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert _trial_ids(at) == [s.other, s.unfinished, s.refused, s.failed, s.ok]
    trials = at.dataframe[0].value
    assert "provider raised: no bars for SPY" in trials["message"].to_list()
    decisions = at.dataframe[1].value
    assert decisions["reason"].to_list() == [
        "override for the h2 exploratory run",
        "free-data gap accepted for H1",
    ]
    text = _text(at)
    assert "1 unfinished" in text and "1 failed" in text and "1 refused_gap" in text


def test_render_synthetic_toggle(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, s = seeded_store
    at = _app(monkeypatch, store_path)
    [toggle] = at.checkbox
    assert toggle.value is False
    assert s.synthetic not in _trial_ids(at)
    toggle.check().run()
    assert s.synthetic in _trial_ids(at)


def test_render_hypothesis_filter(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    store_path, s = seeded_store
    at = _app(monkeypatch, store_path)
    [picker] = at.selectbox
    assert picker.options == ["All hypotheses", "h1-momentum-12-1", "h2-momentum-top-20"]
    picker.set_value("h2-momentum-top-20").run()
    assert _trial_ids(at) == [s.other]
    assert at.dataframe[1].value["reason"].to_list() == ["override for the h2 exploratory run"]


def test_render_reads_only_through_the_shells_connection(
    monkeypatch: pytest.MonkeyPatch, seeded_store: tuple[Path, Seeded]
) -> None:
    opened: list[object] = []
    real_connect = duckdb.connect

    def _counting_connect(*args: Any, **kwargs: Any) -> duckdb.DuckDBPyConnection:
        opened.append(kwargs.get("read_only"))
        return real_connect(*args, **kwargs)

    at = _app(monkeypatch, seeded_store[0])
    monkeypatch.setattr(duckdb, "connect", _counting_connect)
    at.checkbox[0].check().run()
    assert not at.exception
    assert opened == [True]


def test_render_empty_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = tmp_path / "empty.duckdb"
    with open_for_write(Settings(_env_file=None, store={"path": str(store_path)})) as conn:
        schema.init_schema(conn)
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert "no trials" in _text(at).lower()
    assert not at.dataframe


def test_render_registry_not_initialised(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store_path = tmp_path / "v2.duckdb"
    conn = duckdb.connect(str(store_path))
    try:
        conn.execute("CREATE TABLE schema_version (version INTEGER, applied_at TIMESTAMPTZ)")
        conn.execute("INSERT INTO schema_version VALUES (2, now())")
    finally:
        conn.close()
    at = _app(monkeypatch, store_path)
    assert not at.exception
    assert "registry not initialised" in _text(at).lower()
    assert not at.dataframe
