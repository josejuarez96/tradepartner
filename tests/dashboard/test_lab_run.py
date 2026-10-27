"""The Lab's one Run slot (plan T298, #2318; amendment 2026-10-27 #2304): a slot file
created with O_EXCL, a record of the child, a daemon waiter, status and release."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tradepartner.dashboard import lab_run
from tradepartner.dashboard.lab_run import Busy, Taken

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=UTC)


@pytest.fixture
def slot(tmp_path: Path) -> Path:
    return tmp_path / "t.duckdb.lab-run.json"


def _take(slot: Path, **kw: object) -> Taken | Busy:
    args: dict[str, object] = {"in_flight": (), "now": NOW, "grace_seconds": 30}
    args.update(kw)
    return lab_run.take(slot, **args)  # type: ignore[arg-type]


def _age(path: Path, seconds: float) -> None:
    t = NOW.timestamp() - seconds
    os.utime(path, (t, t))


class FakeChild:
    def __init__(self, pid: int, code: int = 0) -> None:
        self.pid = pid
        self.code = code
        self.gate = threading.Event()

    def wait(self) -> int:
        self.gate.wait(5)
        return self.code


def _record(slot: Path, pid: int, log: Path, start: str = "S1") -> None:
    lab_run.record(
        slot,
        FakeChild(pid),
        kind="sweep",
        slug="b10",
        baseline=7,
        started_at=NOW,
        log_path=log,
        start_time_of=lambda _p: start,
    )


def test_take_creates_slot_with_o_excl_and_second_take_is_busy(slot: Path) -> None:
    assert isinstance(_take(slot), Taken)
    assert slot.exists()
    again = _take(slot)
    assert isinstance(again, Busy)  # a double click
    assert not isinstance(again, Taken)


def test_take_busy_names_each_in_flight_and_creates_nothing(slot: Path) -> None:
    res = _take(slot, in_flight=("open sweep_runs row 12", "unfinished trial 40"))
    assert isinstance(res, Busy)
    assert any("open sweep_runs row 12" in r for r in res.reasons)
    assert any("unfinished trial 40" in r for r in res.reasons)
    assert not slot.exists()


def test_empty_slot_inside_grace_is_busy_and_never_taken_over(slot: Path) -> None:
    assert isinstance(_take(slot), Taken)
    _age(slot, 10)
    # a second tab inside the grace
    res = _take(slot)
    assert isinstance(res, Busy)
    assert slot.exists() and lab_run._read(slot) is None
    assert not list(slot.parent.glob("*.stale*"))


def test_empty_slot_past_grace_is_stale_and_taken_over(slot: Path) -> None:
    assert isinstance(_take(slot), Taken)
    _age(slot, 31)
    assert isinstance(_take(slot), Taken)


def test_record_holds_pid_start_kind_slug_baseline_started_log(slot: Path, tmp_path: Path) -> None:
    log = tmp_path / "run.log"
    assert isinstance(_take(slot), Taken)
    _record(slot, 4242, log)
    data = json.loads(slot.read_text())
    assert data["pid"] == 4242
    assert data["pid_start"] == "S1"
    assert data["kind"] == "sweep"
    assert data["slug"] == "b10"
    assert data["baseline"] == 7
    assert data["started_at"] == NOW.isoformat()
    assert data["log_path"] == str(log)
    assert (slot.stat().st_mode & 0o777) == 0o600


def test_default_start_time_uses_ps_list_form(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def fake_run(cmd: object, **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append((cmd, kw))
        return subprocess.CompletedProcess([], 0, stdout="Mon Oct 27 15:00:00 2026\n")

    monkeypatch.setattr(lab_run.subprocess, "run", fake_run)
    assert lab_run.process_start_time(99) == "Mon Oct 27 15:00:00 2026"
    cmd, kw = seen[0]  # type: ignore[misc]
    assert cmd == ["ps", "-o", "lstart=", "-p", "99"]
    assert not kw.get("shell")


def test_live_pid_with_same_start_is_busy(slot: Path, tmp_path: Path) -> None:
    assert isinstance(_take(slot), Taken)
    _record(slot, os.getpid(), tmp_path / "l")
    res = lab_run.take(slot, in_flight=(), now=NOW, grace_seconds=30, start_time_of=lambda _p: "S1")
    assert isinstance(res, Busy)


def test_pid_alive_with_other_start_time_is_gone_and_taken_over(slot: Path, tmp_path: Path) -> None:
    assert isinstance(_take(slot), Taken)
    _record(slot, os.getpid(), tmp_path / "l", start="OLD")
    res = lab_run.take(
        slot, in_flight=(), now=NOW, grace_seconds=30, start_time_of=lambda _p: "NEW"
    )
    assert isinstance(res, Taken)
    assert not list(slot.parent.glob("*.stale*"))  # renamed aside, then dropped


def test_dead_pid_is_stale(slot: Path, tmp_path: Path) -> None:
    assert isinstance(_take(slot), Taken)
    _record(slot, 2**22 + 5, tmp_path / "l")
    res = lab_run.take(slot, in_flight=(), now=NOW, grace_seconds=30, start_time_of=lambda _p: None)
    assert isinstance(res, Taken)


def test_two_threads_racing_for_a_stale_slot_one_wins(slot: Path, tmp_path: Path) -> None:
    for _ in range(20):
        slot.unlink(missing_ok=True)
        assert isinstance(_take(slot), Taken)
        _record(slot, 2**22 + 5, tmp_path / "l")
        out: list[object] = []
        barrier = threading.Barrier(2)

        def go(barrier: threading.Barrier = barrier, out: list[object] = out) -> None:
            barrier.wait()
            out.append(
                lab_run.take(
                    slot,
                    in_flight=(),
                    now=NOW,
                    grace_seconds=30,
                    start_time_of=lambda _p: None,
                )
            )

        ts = [threading.Thread(target=go) for _ in range(2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert sorted(type(o).__name__ for o in out) == ["Busy", "Taken"]
        assert slot.exists()


def test_waiter_writes_exit_code_via_temp_and_replace(
    slot: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert isinstance(_take(slot), Taken)
    child = FakeChild(1234, code=2)
    lab_run.record(
        slot,
        child,
        kind="sweep",
        slug="b10",
        baseline=3,
        started_at=NOW,
        log_path=tmp_path / "l",
        start_time_of=lambda _p: "S",
    )
    replaced: list[tuple[str, str]] = []
    real = os.replace

    def spy(a: object, b: object) -> None:
        replaced.append((str(a), str(b)))
        real(a, b)  # type: ignore[arg-type]

    monkeypatch.setattr(lab_run.os, "replace", spy)
    t = lab_run.watch(slot, child)
    assert t.daemon
    child.gate.set()
    t.join(5)
    assert json.loads(slot.read_text())["exit_code"] == 2
    assert replaced and replaced[-1][1] == str(slot) and replaced[-1][0] != str(slot)


def test_release_renames_to_last_and_baseline_and_exit_read_back(
    slot: Path, tmp_path: Path
) -> None:
    assert isinstance(_take(slot), Taken)
    child = FakeChild(1234, code=1)
    lab_run.record(
        slot,
        child,
        kind="backtest",
        slug="h",
        baseline=9,
        started_at=NOW,
        log_path=tmp_path / "l",
        start_time_of=lambda _p: "S",
    )
    child.gate.set()
    lab_run.watch(slot, child).join(5)
    assert lab_run.release(slot, start_time_of=lambda _p: None) is True
    assert not slot.exists()
    last = lab_run.read_last(slot)
    assert last is not None
    assert (last.baseline, last.exit_code, last.slug, last.kind) == (9, 1, "h", "backtest")
    assert lab_run.last_path(slot).name.endswith(".lab-run.last.json")


def test_release_refuses_a_live_child(slot: Path, tmp_path: Path) -> None:
    assert isinstance(_take(slot), Taken)
    _record(slot, os.getpid(), tmp_path / "l")
    assert lab_run.release(slot, start_time_of=lambda _p: "S1") is False
    assert slot.exists()


def test_next_take_clears_last(slot: Path, tmp_path: Path) -> None:
    assert isinstance(_take(slot), Taken)
    _record(slot, 2**22 + 5, tmp_path / "l")
    assert lab_run.release(slot, start_time_of=lambda _p: None)
    assert lab_run.read_last(slot) is not None
    assert isinstance(_take(slot), Taken)
    assert lab_run.read_last(slot) is None


def test_status_idle_starting_running_ended(slot: Path, tmp_path: Path) -> None:
    def st() -> lab_run.SlotStatus:
        return lab_run.status(
            slot,
            scrub=lambda s: s,
            now=NOW,
            grace_seconds=30,
            start_time_of=lambda _p: "S1",
            tail_bytes=4000,
        )

    assert st().state == "idle"
    assert isinstance(_take(slot), Taken)
    assert st().state == "starting"
    log = tmp_path / "l"
    log.write_text("hello\nworld\n")
    _record(slot, os.getpid(), log)
    s = st()
    assert s.state == "running" and s.record is not None and s.record.slug == "b10"
    assert s.log_tail == "hello\nworld\n"
    # exit code recorded: ended
    d = json.loads(slot.read_text())
    d["exit_code"] = 0
    slot.write_text(json.dumps(d))
    assert st().state == "ended"
    # dead pid
    d.pop("exit_code")
    d["pid"] = 2**22 + 5
    slot.write_text(json.dumps(d))
    assert (
        lab_run.status(
            slot,
            scrub=lambda s: s,
            now=NOW,
            grace_seconds=30,
            start_time_of=lambda _p: None,
            tail_bytes=4000,
        ).state
        == "ended"
    )


def test_status_tail_goes_through_scrub_and_is_bounded(slot: Path, tmp_path: Path) -> None:
    log = tmp_path / "l"
    log.write_text("x" * 100 + "\nab SECRET cd\n" + "y" * 20 + "\n")
    assert isinstance(_take(slot), Taken)
    _record(slot, os.getpid(), log)
    s = lab_run.status(
        slot,
        scrub=lambda t: t.replace("SECRET", "***"),
        now=NOW,
        grace_seconds=30,
        start_time_of=lambda _p: "S1",
        tail_bytes=60,
    )
    assert "SECRET" not in s.log_tail and "***" in s.log_tail
    assert len(s.log_tail) <= 60


def test_status_refuses_a_symlinked_log(slot: Path, tmp_path: Path) -> None:
    target = tmp_path / "secret.txt"
    target.write_text("topsecret")
    link = tmp_path / "l"
    link.symlink_to(target)
    assert isinstance(_take(slot), Taken)
    _record(slot, os.getpid(), link)
    s = lab_run.status(
        slot,
        scrub=lambda t: t,
        now=NOW,
        grace_seconds=30,
        start_time_of=lambda _p: "S1",
        tail_bytes=4000,
    )
    assert "topsecret" not in s.log_tail


def test_status_takes_no_connection_and_module_imports_neither_child_nor_store() -> None:
    tree = ast.parse(Path(lab_run.__file__).read_text())
    mods: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom):
            mods.append(n.module or "")
            mods += [f"{n.module}.{a.name}" for a in n.names]
    assert not [m for m in mods if "lab_child" in m or ".store" in m or m.startswith("duckdb")]


def test_abandon_removes_only_its_own_reservation(slot: Path) -> None:
    first = _take(slot)
    assert isinstance(first, Taken)
    assert lab_run.abandon(slot, first) is True
    assert not slot.exists()
    # a slow spawn fails after another tab took the slot over: nothing of B's is deleted
    a = _take(slot)
    assert isinstance(a, Taken)
    _age(slot, 31)
    b = _take(slot)
    assert isinstance(b, Taken)
    assert lab_run.abandon(slot, a) is False
    assert slot.exists()
    assert lab_run.abandon(slot, b) is True


def test_release_refuses_a_starting_reservation(slot: Path) -> None:
    assert isinstance(_take(slot), Taken)
    assert lab_run.release(slot, start_time_of=lambda _p: None) is False
    assert slot.exists()


def test_waiter_cannot_overwrite_a_newer_run_after_release(slot: Path, tmp_path: Path) -> None:
    old = FakeChild(2**22 + 5, code=0)
    assert isinstance(_take(slot), Taken)
    lab_run.record(
        slot,
        old,
        kind="sweep",
        slug="old",
        baseline=1,
        started_at=NOW,
        log_path=tmp_path / "l",
        start_time_of=lambda _p: None,
    )
    assert lab_run.release(slot, start_time_of=lambda _p: None)
    new = _take(slot)
    assert isinstance(new, Taken)
    lab_run._store_exit_code(slot, old.pid, 0)  # the old waiter, late
    assert lab_run._read(slot) is None  # the new reservation is intact
    assert json.loads(slot.read_text())["token"] == new.token


def test_state_changes_hold_an_exclusive_lock(slot: Path) -> None:
    import fcntl

    lock = slot.with_name(slot.name + ".lock")
    assert isinstance(_take(slot), Taken)
    fd = os.open(lock, os.O_WRONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    done = threading.Event()

    def other() -> None:
        lab_run.release(slot, start_time_of=lambda _p: None)
        done.set()

    t = threading.Thread(target=other, daemon=True)
    t.start()
    assert not done.wait(0.3)  # blocked behind our lock
    os.close(fd)
    assert done.wait(5)
    t.join(5)


def test_ps_nonzero_exit_with_error_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(cmd: object, **kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 2, stdout="", stderr="ps: boom")

    monkeypatch.setattr(lab_run.subprocess, "run", fake)
    with pytest.raises(lab_run.PsUnavailable):
        lab_run.process_start_time(1)

    def missing(cmd: object, **kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 1, stdout="", stderr="")

    monkeypatch.setattr(lab_run.subprocess, "run", missing)
    assert lab_run.process_start_time(1) is None


def test_status_does_not_hang_on_a_named_pipe(slot: Path, tmp_path: Path) -> None:
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert isinstance(_take(slot), Taken)
    _record(slot, os.getpid(), fifo)
    result: list[str] = []
    t = threading.Thread(
        target=lambda: result.append(
            lab_run.status(
                slot,
                scrub=lambda x: x,
                now=NOW,
                grace_seconds=30,
                start_time_of=lambda _p: "S1",
                tail_bytes=100,
            ).log_tail
        ),
        daemon=True,
    )
    t.start()
    t.join(5)
    assert result == [""]


def test_real_subprocess_roundtrip(slot: Path, tmp_path: Path) -> None:
    assert isinstance(_take(slot), Taken)
    log = tmp_path / "l"
    p = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.2); raise SystemExit(3)"]
    )
    lab_run.record(slot, p, kind="sweep", slug="s", baseline=None, started_at=NOW, log_path=log)
    lab_run.watch(slot, p).join(10)
    assert json.loads(slot.read_text())["exit_code"] == 3
    assert lab_run.release(slot)
    last = lab_run.read_last(slot)
    assert last is not None and last.baseline is None and last.exit_code == 3


def test_record_refuses_naive_started_at(slot: Path, tmp_path: Path) -> None:
    assert isinstance(_take(slot), Taken)
    with pytest.raises(ValueError, match="tz-aware"):
        lab_run.record(
            slot,
            FakeChild(1),
            kind="s",
            slug="s",
            baseline=None,
            started_at=datetime(2026, 10, 27, 15, 0),  # noqa: DTZ001
            log_path=tmp_path / "l",
            start_time_of=lambda _p: "S",
        )


def test_waiter_after_release_writes_exit_code_to_last(slot: Path, tmp_path: Path) -> None:
    assert isinstance(_take(slot), Taken)
    child = FakeChild(2**22 + 5, code=2)
    lab_run.record(
        slot,
        child,
        kind="sweep",
        slug="b10",
        baseline=7,
        started_at=NOW,
        log_path=tmp_path / "l",
        start_time_of=lambda _p: None,
    )
    assert lab_run.release(slot, start_time_of=lambda _p: None)
    t = lab_run.watch(slot, child)
    child.gate.set()
    t.join(5)
    last = lab_run.read_last(slot)
    assert last is not None and (last.baseline, last.exit_code) == (7, 2)
    assert not slot.exists()


def test_tail_cut_inside_a_secret_never_shows_part_of_it(slot: Path, tmp_path: Path) -> None:
    log = tmp_path / "l"
    log.write_text("first line\nkey=ABCDEFGHIJKL end\nlast line\n")
    assert isinstance(_take(slot), Taken)
    _record(slot, os.getpid(), log)
    # cut lands inside "ABCDEFGHIJKL"
    cut = len("last line\n") + len(" end\n") + 6
    s = lab_run.status(
        slot,
        scrub=lambda t: t.replace("ABCDEFGHIJKL", "[x]"),
        now=NOW,
        grace_seconds=30,
        start_time_of=lambda _p: "S1",
        tail_bytes=cut,
    )
    assert "GHIJKL" not in s.log_tail and "last line" in s.log_tail


def test_status_ignores_a_non_regular_log(slot: Path, tmp_path: Path) -> None:
    assert isinstance(_take(slot), Taken)
    _record(slot, os.getpid(), tmp_path)  # a directory
    s = lab_run.status(
        slot,
        scrub=lambda t: t,
        now=NOW,
        grace_seconds=30,
        start_time_of=lambda _p: "S1",
        tail_bytes=4000,
    )
    assert s.log_tail == ""


def test_record_refuses_when_another_tab_took_the_slot_over(slot: Path, tmp_path: Path) -> None:
    first = _take(slot)
    assert isinstance(first, Taken)
    _age(slot, 31)
    assert isinstance(_take(slot), Taken)  # tab B, past the grace
    with pytest.raises(lab_run.SlotLost):
        lab_run.record(
            slot,
            FakeChild(5),
            kind="s",
            slug="s",
            baseline=None,
            started_at=NOW,
            log_path=tmp_path / "l",
            start_time_of=lambda _p: "S",
            taken=first,
        )
    assert lab_run._read(slot) is None  # tab B's file is untouched


def _ps_down(_pid: int) -> str | None:
    raise lab_run.PsUnavailable("down")


def test_ps_failure_is_unknown_not_gone(slot: Path, tmp_path: Path) -> None:
    assert isinstance(_take(slot), Taken)
    _record(slot, os.getpid(), tmp_path / "l")
    res = lab_run.take(slot, in_flight=(), now=NOW, grace_seconds=30, start_time_of=_ps_down)
    assert isinstance(res, Busy)
    assert lab_run.release(slot, start_time_of=_ps_down) is False
    st = lab_run.status(
        slot, scrub=lambda t: t, now=NOW, grace_seconds=30, start_time_of=_ps_down, tail_bytes=4000
    )
    assert st.state == "running"


def test_ps_failure_at_record_falls_back_to_signal_zero(slot: Path, tmp_path: Path) -> None:
    assert isinstance(_take(slot), Taken)
    rec = lab_run.record(
        slot,
        FakeChild(os.getpid()),
        kind="s",
        slug="s",
        baseline=None,
        started_at=NOW,
        log_path=tmp_path / "l",
        start_time_of=_ps_down,
    )
    assert rec.pid_start is None
    res = lab_run.take(
        slot, in_flight=(), now=NOW, grace_seconds=30, start_time_of=lambda _p: "whatever"
    )
    assert isinstance(res, Busy)


def test_ps_error_raises_ps_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise subprocess.TimeoutExpired("ps", 10)

    monkeypatch.setattr(lab_run.subprocess, "run", boom)
    with pytest.raises(lab_run.PsUnavailable):
        lab_run.process_start_time(1)
