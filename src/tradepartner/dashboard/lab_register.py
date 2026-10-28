"""Register a merged sweep through the desk command, then wait for its text.

The caller passes a file *name*, never a path. The Lab's file helper resolves it
inside docs/sweeps and checks that it is merged again at the click. Registration
has its own pipes; it never opens or truncates the separate Run log.
"""

from __future__ import annotations

import subprocess
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from tradepartner.config import Settings
from tradepartner.dashboard import book_actions, lab_child, lab_files
from tradepartner.dashboard.lab_files import FileKind, GitState

RegisterStatus = Literal["created", "unchanged", "refused", "store_busy", "failed", "not_confirmed"]


class RegisterRefused(ValueError):
    """A precondition failed before a child started."""


@dataclass(frozen=True)
class RegisterResult:
    """The command's scrubbed output and its no-new-row outcome."""

    status: RegisterStatus
    output: str
    exit_code: int | None
    wait_seconds: int


def _drain(child: subprocess.Popen[str]) -> None:
    """Reap a child that outlived the bounded page callback."""
    with suppress(OSError):
        child.communicate()


def register(settings: Settings, docs_root: Path, kind: FileKind, name: str) -> RegisterResult:
    """Run `sweep register -- <server path>` once, or refuse before spawning.

    The bound is the store's writer wait plus the UI margin. When it expires,
    the command may still finish; the page must make a fresh registry read to
    learn what happened and must never retry this click.
    """
    if kind is not FileKind.SWEEP:
        raise RegisterRefused("A hypothesis file is registered by sweep promote at the desk.")
    docs_root = docs_root.resolve()
    try:
        path = lab_files.resolve_file(docs_root, FileKind.SWEEP, name)
    except lab_files.LabFileRefused as exc:
        raise RegisterRefused(str(exc)) from exc
    try:
        live = book_actions.live_settings()
    except (ValueError, OSError) as exc:
        raise RegisterRefused("The live store's config could not be read, so nothing ran.") from exc
    refusal = lab_child.store_refusal(settings, live)
    if refusal is not None:
        raise RegisterRefused(refusal)
    if lab_files.git_state(path, docs_root.parent, settings) is not GitState.MERGED:
        raise RegisterRefused(f"{name} is not merged, so it cannot be registered here.")
    argv = lab_child.register_argv(path)
    if not lab_child._entry_point().is_file():
        raise RegisterRefused("The tradepartner command is not installed beside this interpreter.")
    wait_seconds = settings.store.lock_retry_seconds + settings.ui.kill_wait_margin_seconds
    child = subprocess.Popen(
        argv,
        env=lab_child._child_env(settings),
        cwd=book_actions.PROJECT_ROOT,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = child.communicate(timeout=wait_seconds)
    except subprocess.TimeoutExpired:
        threading.Thread(
            target=_drain, args=(child,), daemon=True, name="lab-register-waiter"
        ).start()
        return RegisterResult(
            "not_confirmed", "Registration is not confirmed yet.", None, wait_seconds
        )
    output = lab_child.scrub("\n".join(part for part in (stdout, stderr) if part), settings, live)
    code = child.returncode
    if code == 0:
        status: RegisterStatus = (
            "unchanged" if "unchanged, nothing written" in output else "created"
        )
    elif code == 2:
        status = "refused"
    elif code == 1 and output.lstrip().startswith("store busy:"):
        status = "store_busy"
    else:
        status = "failed"
    return RegisterResult(status, output, code, wait_seconds)
