"""The paper run lock (Phase 4 plan T59; per book since ADR 0017 B.3, plan T155):
one exclusive `fcntl.flock` holder at a time on `<store.path>.paper.<book>.lock`,
refused at once, visible from another process, released on exception; another
book's lock is another file."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from tradepartner.config import Settings
from tradepartner.execution.lock import LockHeld, is_held, lock_path, run_lock

_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def holder(settings: Settings) -> Iterator[subprocess.Popen[str]]:
    """Another process holding the run lock until its stdin closes."""
    script = textwrap.dedent(
        f"""
        import sys
        from tradepartner.config import Settings
        from tradepartner.execution.lock import run_lock

        settings = Settings(_env_file=None, store={{"path": {settings.store.path!r}}})
        with run_lock(settings):
            print("held", flush=True)
            sys.stdin.read()
        """
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        cwd=_ROOT,
    )
    assert proc.stdout is not None
    assert proc.stdout.readline().strip() == "held"
    try:
        yield proc
    finally:
        assert proc.stdin is not None
        proc.stdin.close()
        proc.wait(timeout=30)


def test_the_lock_file_sits_beside_the_store(settings: Settings) -> None:
    """Per book (ADR 0017 B.3): `paper.book_id`'s (`main`) by default."""
    assert lock_path(settings) == Path(f"{settings.store.path}.paper.main.lock")
    assert lock_path(settings, "main") == lock_path(settings)
    assert lock_path(settings, "b") == Path(f"{settings.store.path}.paper.b.lock")


def test_the_default_book_is_paper_book_id(settings: Settings) -> None:
    other = Settings(_env_file=None, store={"path": settings.store.path}, paper={"book_id": "fx"})
    assert lock_path(other) == Path(f"{settings.store.path}.paper.fx.lock")


@pytest.mark.parametrize("book_id", ["", "../x", "a/b", "a.b", "a b"])
def test_a_book_outside_the_token_grammar_is_refused(settings: Settings, book_id: str) -> None:
    """The token can never name a path outside the store's directory."""
    with pytest.raises(ValueError, match="book_id"):
        lock_path(settings, book_id)
    with pytest.raises(ValueError, match="book_id"), run_lock(settings, book_id):
        pass


def test_two_books_hold_two_locks_at_once(settings: Settings) -> None:
    """Spec acceptance (ADR 0017): a second book's lock is taken while `main`'s is
    held, and each book's second holder is still refused."""
    with run_lock(settings, "main"), run_lock(settings, "b"):
        assert is_held(settings, "main")
        assert is_held(settings, "b")
        assert not is_held(settings, "c")
        with pytest.raises(LockHeld), run_lock(settings, "main"):
            pass
        with pytest.raises(LockHeld), run_lock(settings, "b"):
            pass
    assert not is_held(settings, "main")
    assert not is_held(settings, "b")


def test_another_process_holding_main_leaves_b_free(
    settings: Settings, holder: subprocess.Popen[str]
) -> None:
    assert is_held(settings, "main")
    assert not is_held(settings, "b")
    with run_lock(settings, "b"):
        pass


def test_a_second_holder_in_the_same_process_is_refused_at_once(settings: Settings) -> None:
    with run_lock(settings):
        started = time.monotonic()
        with pytest.raises(LockHeld), run_lock(settings):
            pass
        assert time.monotonic() - started < settings.store.lock_retry_seconds
    with run_lock(settings):
        pass


def test_another_process_holding_it_refuses_this_one_and_names_its_pid(
    settings: Settings, holder: subprocess.Popen[str]
) -> None:
    with pytest.raises(LockHeld, match=f"pid {holder.pid}"), run_lock(settings):
        pass


def test_is_held_sees_another_process(settings: Settings, holder: subprocess.Popen[str]) -> None:
    assert is_held(settings)


def test_is_held_is_false_when_free_and_does_not_take_the_lock(settings: Settings) -> None:
    with run_lock(settings):
        pass
    assert not is_held(settings)
    with run_lock(settings):
        assert is_held(settings)


def test_is_held_before_any_run_creates_no_file(settings: Settings) -> None:
    assert not is_held(settings)
    assert not lock_path(settings).exists()


def test_the_lock_is_released_on_exception(settings: Settings) -> None:
    with pytest.raises(RuntimeError, match="boom"), run_lock(settings):
        raise RuntimeError("boom")
    assert not is_held(settings)
    with run_lock(settings):
        pass


def test_the_lock_is_released_when_the_holder_dies(settings: Settings) -> None:
    script = textwrap.dedent(
        f"""
        import os
        from tradepartner.config import Settings
        from tradepartner.execution.lock import run_lock

        settings = Settings(_env_file=None, store={{"path": {settings.store.path!r}}})
        with run_lock(settings):
            os._exit(3)
        """
    )
    done = subprocess.run([sys.executable, "-c", script], cwd=_ROOT, check=False)
    assert done.returncode == 3
    assert not is_held(settings)


def test_the_holder_records_its_pid(settings: Settings) -> None:
    with run_lock(settings):
        assert f"pid {os.getpid()}" in lock_path(settings).read_text(encoding="utf-8")
