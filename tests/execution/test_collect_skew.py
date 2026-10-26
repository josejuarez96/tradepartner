"""#2186: a broker clock BEHIND ours must not hide a run's fills.

The fill read's lower bound used to be anchored on our own `pending` stamp, while
the broker filters fills on its own `transaction_time`; a fast Mac clock then
read past the fills. The bound now also takes the broker's `submitted_at`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from execution.test_run_trade import F_0, Env, at
from execution.test_run_trade import env as env
from execution.test_run_trade import window as window
from tradepartner.store.journal import PaperWindowRow

MAY_2 = date(2019, 5, 2)


def _behind(env: Env, monkeypatch: pytest.MonkeyPatch, seconds: int) -> None:
    """Make the fake broker's clock run `seconds` behind ours."""
    fake = env.fake
    real_now = fake._now
    monkeypatch.setattr(fake, "_now", lambda: real_now() - timedelta(seconds=seconds))


@pytest.mark.parametrize("behind_seconds", [30, 90, 600])
def test_the_broker_clock_behind_ours(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch, behind_seconds: int
) -> None:
    """The run still journals all three buy fills at any broker lag."""
    _behind(env, monkeypatch, behind_seconds)
    out = env.run(at(F_0))
    assert out.status == "ok"
    assert env.count("fills") == 3


def test_a_fast_mac_clock_still_journals_the_fills_and_the_next_session_runs_clean(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Our clock 90 s ahead: the run journals the broker's real fills, and the next
    session (clock fixed) runs clean instead of halting on `fills_lagging`."""
    fake = env.fake
    real_now = fake._now
    _behind(env, monkeypatch, 90)
    first = env.run(at(F_0))
    assert first.status == "ok"
    assert env.count("fills") == 3
    monkeypatch.setattr(fake, "_now", real_now)
    second = env.run(at(MAY_2))
    assert second.status == "ok"
    assert env.query("SELECT count(*) FROM fills WHERE source = 'broker_feed'") == [(3,)]


def test_a_fast_mac_clock_is_noted_and_the_fills_are_stamped_with_our_collection_time(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Our clock 90 s ahead (limit 60 s): a warning note names the skew, the run does not
    halt, and each fill's `known_at` is our collection time, not back-dated."""
    _behind(env, monkeypatch, 90)
    env.run(at(F_0))
    assert env.count("fills") == 3
    result = env.result(env.latest_run())
    assert "clock skew" in str(result), result
    rows = env.query("SELECT known_at, ingested_at, filled_at FROM fills")
    assert len(rows) == 3
    for known_at, ingested_at, filled_at in rows:
        assert known_at == ingested_at
        assert known_at - filled_at >= timedelta(seconds=80)  # ours, not the broker's time


def test_a_bogus_past_submitted_at_cannot_reach_back_beyond_the_window_start(
    env: Env, window: PaperWindowRow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The read's `since` is never earlier than the window's start minus the overlap."""
    seen: list[object] = []
    fake = env.fake
    real_fills = fake.fills

    def spy(since: object) -> object:
        seen.append(since)
        return real_fills(since)  # type: ignore[arg-type]

    monkeypatch.setattr(fake, "fills", spy)
    _behind(env, monkeypatch, 10 * 86400)  # submitted_at ten days before our stamps
    env.run(at(F_0))
    floor = window.started_at - timedelta(seconds=env.settings.paper.fill_read_overlap_seconds)
    assert seen and all(s is None or s >= floor for s in seen), seen
