"""The Book page's one write, "Stop this book now" (plan T277, #2268; ADR 0018
section 2 and its 2026-10-26 amendment, PR #2228).

`kill(settings, book, reason)` runs the desk's own command, `tradepartner paper
kill --book=<book> --reason=<text>`, as a child of the same interpreter
(`sys.executable -m tradepartner.cli`), in list form with no shell, once, and
never retries it. Nothing here opens the store, imports the switch or the
override writer, or touches a broker: the child is the only writer, so the
page's read-only connection can never break its write, and the kill path is
byte for byte the one the owner uses at the desk.

**Checks before anything runs** (each a `KillRefused`, the child never
started): the book against the token grammar (`journal.check_book_id`), the
reason typed, not blank and at most `ui.max_reason_chars`, and the store the
page reads being the live store (`store_refusal`).

**The same store.** The live store is the one the project-root `.env` (plus the
defaults) names, read by `live_store_path` with `STORE__PATH` and
`TRADEPARTNER_ENV_FILE` left out of the environment for that one read, so a
dashboard launched at a research copy or a backup cannot pass for it. The kill
is offered only when the page's file is that file (`os.path.samefile`) and no
backup manifest (`<file name>.manifest.json`, `store.backup`'s marker) sits
beside it. The child gets the page's store as an absolute `STORE__PATH` in a
copied environment without `TRADEPARTNER_ENV_FILE` (so it reads the
project-root `.env`), and the project root as its working directory.

**The result is the exit code alone**: 0 is engaged; every other code (2
usage, 3 write failed after the store wait, 4 refused, 5 locked, 6 no window,
anything else) is NOT engaged for this book. The command's own text comes back
scrubbed, to show, and is never read for the outcome: its "NOT engaged yet:
store busy ... waiting" line can precede a success.

**The wait** is `config.kill_wait_seconds` of the live config (the one the child
reads, so its bound matches the child's own store wait), which exceeds `paper kill`'s
worst-case store wait by a margin (config refuses less). Past it the kill is NOT
confirmed: the child is neither terminated nor retried; a daemon thread drains
and reaps it, and the next render's read of the journal shows the truth.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic_settings import BaseSettings, EnvSettingsSource, PydanticBaseSettingsSource

from tradepartner import config
from tradepartner.config import Settings, kill_wait_seconds, secret_values
from tradepartner.store.journal import check_book_id

#: The anchor `config._default_env_file` uses: the child's cwd and the live `.env`'s folder.
PROJECT_ROOT: Path = Path(config.__file__).resolve().parents[2]
ENGAGED_EXIT = 0
#: The environment the live read leaves out, and the child's copy drops or sets.
_STORE_PATH_VAR = "STORE__PATH"
_ENV_FILE_VAR = "TRADEPARTNER_ENV_FILE"
_CONTROL = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|[\x00-\x08\x0b-\x1f\x7f]")


class KillStatus(StrEnum):
    """What one Stop click came to."""

    ENGAGED = "engaged"
    NOT_ENGAGED = "not_engaged"
    NOT_CONFIRMED = "not_confirmed"


@dataclass(frozen=True)
class KillResult:
    """`status` from the exit code alone (None when the wait ran out); `output`
    is the command's scrubbed text, to show beneath, never read for the outcome."""

    status: KillStatus
    exit_code: int | None
    output: str


class KillRefused(ValueError):
    """Refused before the command started; nothing ran."""


class _Child(Protocol):
    @property
    def returncode(self) -> int | None: ...

    def communicate(
        self, input: str | None = None, timeout: float | None = None
    ) -> tuple[str, str]: ...


def desk_command(book: str) -> str:
    """The command to run at the desk instead, for the page to print."""
    return f'tradepartner paper kill --book={book} --reason="<why>"'


class _EnvWithoutStorePath(EnvSettingsSource):
    """The environment, less `STORE__PATH` (and a whole-section `STORE`)."""

    def _load_env_vars(self) -> dict[str, str | None]:
        skip = {_STORE_PATH_VAR.lower(), "store"}
        return {k: v for k, v in super()._load_env_vars().items() if k.lower() not in skip}


class _LiveSettings(Settings):
    """`Settings` whose environment source never sees `STORE__PATH`."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            _EnvWithoutStorePath(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


def live_settings() -> Settings:
    """The settings the child reads: the project-root `.env` plus the defaults and
    the environment, with `STORE__PATH` and `TRADEPARTNER_ENV_FILE` ignored (the
    `.env` path is passed explicitly; nothing in `os.environ` changes)."""
    source: dict[str, Any] = {"_env_file": PROJECT_ROOT / ".env"}
    return _LiveSettings(**source)


def live_store_path(live: Settings | None = None) -> Path:
    """The live store (`live_settings`' `store.path`), resolved under `PROJECT_ROOT`."""
    path = Path((live or live_settings()).store.path)
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def store_refusal(settings: Settings, live: Settings | None = None) -> str | None:
    """None when the page reads the live store and no backup manifest sits
    beside it; otherwise one plain sentence saying why, with the desk command."""
    page = Path(settings.store.path).resolve()
    try:
        same = os.path.samefile(page, live_store_path(live))
    except OSError:
        same = False
    except ValueError:  # the live config itself does not load
        return (
            "The live store's config could not be read, so this page cannot stop a book. "
            f"At the desk, run `{desk_command('<book>')}`."
        )
    if not same:
        why = "This page is reading a store that is not the live store"
    elif page.with_name(f"{page.name}.manifest.json").exists():
        why = "This page is reading a store with a backup manifest beside it"
    else:
        return None
    return f"{why}, so it cannot stop a book. At the desk, run `{desk_command('<book>')}`."


def reason_refusal(reason: str | None, settings: Settings) -> str | None:
    """Why `reason` cannot be sent (empty, blank, or over `ui.max_reason_chars`), or None."""
    if reason is None or not reason.strip():
        return "Type the reason first: an empty or blank reason stops nothing."
    cap = settings.ui.max_reason_chars
    if len(reason) > cap:
        return f"The reason is {len(reason)} characters; the limit is {cap}."
    return None


def _start(argv: list[str], *, env: dict[str, str], cwd: Path) -> _Child:
    """Start the child (a seam for tests): list form, no shell, text pipes."""
    return subprocess.Popen(
        argv,
        env=env,
        cwd=cwd,
        shell=False,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _scrub(text: str, *settings: Settings) -> str:
    values = {v for s in settings for v in secret_values(s)}
    for value in sorted(values, key=len, reverse=True):
        text = text.replace(value, "[redacted]")
    return _CONTROL.sub(" ", text).strip()


def _drain(child: Any) -> None:
    """Read a late child's pipes to its end and reap it; its outcome is the journal's."""
    threading.Thread(target=child.communicate, daemon=True).start()


def kill(settings: Settings, book: str, reason: str) -> KillResult:
    """Run `paper kill --book=<book> --reason=<reason>` once (module docstring).
    `KillRefused` when a check fails, before anything starts."""
    try:
        check_book_id(book)
    except ValueError as exc:
        raise KillRefused(f"{book!r} is not a book name") from exc
    refusal = reason_refusal(reason, settings)
    if refusal is not None:
        raise KillRefused(refusal)
    try:
        live = live_settings()
    except ValueError as exc:
        raise KillRefused(store_refusal(settings, None) or "live config unreadable") from exc
    refusal = store_refusal(settings, live)
    if refusal is not None:
        raise KillRefused(refusal)
    dropped = (_STORE_PATH_VAR, _ENV_FILE_VAR, "STORE")
    env = {k: v for k, v in os.environ.items() if k.upper() not in dropped}
    env[_STORE_PATH_VAR] = str(Path(settings.store.path).resolve())
    argv = [
        sys.executable,
        "-m",
        "tradepartner.cli",
        "paper",
        "kill",
        f"--book={book}",
        f"--reason={reason}",
    ]
    child = _start(argv, env=env, cwd=PROJECT_ROOT)
    try:
        # The child's own wait is the live config's, so the bound is too.
        stdout, stderr = child.communicate(timeout=kill_wait_seconds(live))
    except subprocess.TimeoutExpired as exc:
        _drain(child)
        partial = "\n".join(
            p.decode(errors="replace") if isinstance(p, bytes) else p
            for p in (exc.stdout, exc.stderr)
            if p
        )
        return KillResult(KillStatus.NOT_CONFIRMED, None, _scrub(partial, settings, live))
    code = child.returncode
    status = KillStatus.ENGAGED if code == ENGAGED_EXIT else KillStatus.NOT_ENGAGED
    output = "\n".join(p for p in (stdout, stderr) if p)
    return KillResult(status, code, _scrub(output, settings, live))
