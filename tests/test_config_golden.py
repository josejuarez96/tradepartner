"""Golden tests that pin `tradepartner.config` while it is split into modules (plan
config-split, T310, #2512).

These goldens are R1 scaffolding: they prove a verbatim move changed nothing a caller
can see (the defaults, the schema, the frozen keys and hashes, the env names, the
importable names, and the `.env` loading). A PR that changes config on purpose reruns

    CONFIG_GOLDEN_UPDATE=1 uv run pytest tests/test_config_golden.py

and commits the rewritten files in `tests/fixtures/config_golden/` in the same PR, so the
golden diff shows the change. The plan deletes this file once T315 lands (owner, 2026-10-28).
No test reads the real `.env`; every `Settings` here is built with `_env_file=None` or from
a synthetic file in `tmp_path`.
"""

from __future__ import annotations

import ast
import json
import os
import re
import types
import typing
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from tradepartner import config
from tradepartner.backtest import hypothesis
from tradepartner.config import FAMILIES, Settings
from tradepartner.store import registry

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "config_golden"
REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"
#: What the checkout root is replaced by, so any worktree gives the same dump.
ROOT_PLACEHOLDER = "<ROOT>"
#: The stand-in for every secret in the synthetic `.env`; one per name, so a value that
#: lands on the wrong field is seen.
_DUMMY_PREFIX = "dummy-secret-"

_UPDATING = os.environ.get("CONFIG_GOLDEN_UPDATE") == "1"


def _golden_hint(name: str) -> str:
    return (
        f"golden {name} differs. These goldens are R1 scaffolding (plan config-split, T310): "
        "if this PR changes config on purpose, rewrite them in this PR with "
        "`CONFIG_GOLDEN_UPDATE=1 uv run pytest tests/test_config_golden.py` and commit "
        "tests/fixtures/config_golden/; if it should not, the change is a bug."
    )


def _check_golden(name: str, actual: Any) -> None:
    """Compare `actual` with `fixtures/config_golden/<name>.json`, or rewrite it."""
    path = GOLDEN_DIR / f"{name}.json"
    if _UPDATING:
        GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return
    assert path.exists(), f"missing golden {path.name}. " + _golden_hint(name)
    expected = json.loads(path.read_text(encoding="utf-8"))
    # Round-trip `actual` so tuples compare as lists; dict order never matters.
    assert json.loads(json.dumps(actual)) == expected, _golden_hint(name)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every variable `Settings` reads (found from its fields, as `test_config.py`
    does), so no shell leaks into a golden."""
    fields = {name.upper() for name in Settings.model_fields}
    nested = tuple(f"{name}__" for name in fields)
    for key in list(os.environ):
        upper = key.upper()
        if upper in fields or upper.startswith(nested) or upper == "TRADEPARTNER_ENV_FILE":
            monkeypatch.delenv(key)


def _scrub_root(value: Any) -> Any:
    """`value` with the checkout root replaced by `ROOT_PLACEHOLDER` in every string."""
    root = str(Path(config.__file__).resolve().parents[2])
    if isinstance(value, str):
        return value.replace(root, ROOT_PLACEHOLDER)
    if isinstance(value, list):
        return [_scrub_root(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_root(v) for k, v in value.items()}
    return value


def _default_dump() -> dict[str, Any]:
    dumped: dict[str, Any] = Settings(_env_file=None).model_dump(mode="json")
    return _scrub_root(dumped)  # type: ignore[no-any-return]


def test_settings_default_dump_golden() -> None:
    _check_golden("settings_dump", _default_dump())


def test_settings_json_schema_golden() -> None:
    _check_golden("settings_schema", Settings.model_json_schema())


def test_frozen_keys_golden() -> None:
    # A list, so the order is part of the golden.
    _check_golden("frozen_keys", list(hypothesis.frozen_keys()))


def test_family_params_sha256_golden() -> None:
    settings = Settings(_env_file=None)
    hashes = {
        family: registry.params_sha256(hypothesis.frozen_params_of(settings, family=family))
        for family in sorted(FAMILIES)
    }
    assert hashes, "FAMILIES is empty"
    _check_golden("family_params_sha256", hashes)


def _model_of(annotation: Any) -> type[BaseModel] | None:
    """The model class an annotation names, looking through `X | None`."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        for arg in typing.get_args(annotation):
            if arg is not type(None) and (model := _model_of(arg)) is not None:
                return model
    return None


def _env_names(model: type[BaseModel], prefix: str = "") -> list[str]:
    """Every env-var name under `model`: each field, and `FIELD__SUB` for a nested model
    (a `dict[str, Model]` field gets a `<KEY>` segment, as `ALPACA_PAPER_BOOKS__<KEY>__...`)."""
    names: list[str] = []
    for field, info in model.model_fields.items():
        name = f"{prefix}{field.upper()}"
        names.append(name)
        annotation = info.annotation
        nested = _model_of(annotation)
        if nested is not None:
            names.extend(_env_names(nested, f"{name}__"))
        elif typing.get_origin(annotation) is dict:
            value_model = _model_of(typing.get_args(annotation)[1])
            if value_model is not None:
                names.extend(_env_names(value_model, f"{name}__<KEY>__"))
    return names


def test_env_var_names_golden() -> None:
    names = _env_names(Settings)
    assert len(names) == len(set(names))
    _check_golden("env_var_names", sorted(names))


def _defined_names(source: str) -> list[str]:
    """Every top-level name a module defines: classes, functions, assigned names. Imports
    are not pinned."""
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                names.update(n.id for n in ast.walk(target) if isinstance(n, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return sorted(names)


def test_importable_names_golden() -> None:
    """Every name `config.py` defined when the golden was written stays an attribute of
    `tradepartner.config`, private ones included. Once names move to sibling modules the
    AST of `config.py` no longer defines them, so the test checks the golden list against
    the module's attributes, not the AST."""
    if _UPDATING:
        source = Path(config.__file__).read_text(encoding="utf-8")
        _check_golden("importable_names", _defined_names(source))
        return
    path = GOLDEN_DIR / "importable_names.json"
    assert path.exists(), "missing golden importable_names.json. " + _golden_hint("importable")
    expected: list[str] = json.loads(path.read_text(encoding="utf-8"))
    assert expected, "the importable-name golden is empty"
    missing = [name for name in expected if not hasattr(config, name)]
    assert not missing, f"names gone from tradepartner.config: {missing}. " + _golden_hint(
        "importable_names"
    )


_EXAMPLE_LINE = re.compile(r"^#\s*([A-Z][A-Z0-9_]*)=(.*)$")


def _example_names() -> list[str]:
    """Every variable `.env.example` lists (commented, as the file ships)."""
    names = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        match = _EXAMPLE_LINE.match(line)
        if match:
            names.append(match.group(1))
    return names


def _default_for(name: str, dumped: dict[str, Any]) -> str | None:
    """The `.env` text for a nested `SECTION__FIELD` name at its current default, or None
    when the default cannot be written (unset, or no such key)."""
    node: Any = dumped
    for part in name.lower().split("__"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if node is None:
        return None
    if isinstance(node, bool):
        return "true" if node else "false"
    if isinstance(node, str | int | float):
        return str(node)
    return json.dumps(node)


def _synthetic_env(tmp_path: Path, dumped: dict[str, Any]) -> tuple[Path, dict[str, str]]:
    """A `.env` setting every name `.env.example` lists: a nested config key to its current
    default (a key whose default is unset is left out), anything else (a secret, a per-book
    pair, a name `Settings` ignores) to a distinct dummy. Returns the file and each dummy."""
    lines: list[str] = []
    dummies: dict[str, str] = {}
    for i, name in enumerate(dict.fromkeys(_example_names())):
        if "__" in name and "_BOOKS__" not in name:
            value = _default_for(name, dumped)
            if value is None:  # an unset default (null) cannot be written as text
                continue
        else:
            value = dummies[name] = f"{_DUMMY_PREFIX}{i}"
        lines.append(f"{name}={value}")
    path = tmp_path / "synthetic.env"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path, dummies


def _without_secrets(dumped: dict[str, Any]) -> dict[str, Any]:
    secret_fields = {
        name
        for name, info in Settings.model_fields.items()
        if info.annotation in (SecretStr | None, SecretStr)
        or name in ("alpaca_paper_books", "alert_smtp_host")
    }
    return {k: v for k, v in dumped.items() if k not in secret_fields}


def test_synthetic_env_from_example_loads_to_the_default_dump(tmp_path: Path) -> None:
    golden_path = GOLDEN_DIR / "settings_dump.json"
    if _UPDATING:  # written by test_settings_default_dump_golden
        pytest.skip("rewriting goldens")
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    env_file, dummies = _synthetic_env(tmp_path, golden)
    assert len(_example_names()) >= 20, ".env.example lists far fewer names than expected"
    try:
        settings = Settings(_env_file=env_file)
    except Exception as exc:  # the message must not carry a secret; it names the file
        pytest.fail(f"the synthetic .env from .env.example no longer loads: {type(exc).__name__}")
    loaded = _scrub_root(settings.model_dump(mode="json"))
    assert _without_secrets(loaded) == _without_secrets(golden), _golden_hint("settings_dump")
    # The dummies landed on their own fields (secrets aside from the dump comparison).
    assert settings.alpaca_api_key is not None
    assert settings.alpaca_api_key.get_secret_value() == dummies["ALPACA_API_KEY"]
    assert settings.fred_api_key is not None
    assert settings.fred_api_key.get_secret_value() == dummies["FRED_API_KEY"]
    assert set(settings.alpaca_paper_books) == {"daily", "sandbox"}
